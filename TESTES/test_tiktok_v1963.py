"""V19.63 — TikTok Shop (loja de Bauru), fase 0: só a conexão OAuth.

Aqui só conecta e lê a loja autorizada (shop_cipher) — nada mexe em estoque nem na fila.
A assinatura das chamadas (HMAC-SHA256 com o app_secret) é a parte que mais quebra
integração: o teste prende o algoritmo da documentação.
"""

import hashlib
import hmac
import io
import json
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

from base import S, TesteNoromak


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get_cru(url: str) -> tuple[int, str, str]:
    abridor = urllib.request.build_opener(_SemRedirecionar)
    try:
        with abridor.open(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Location") or ""
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read().decode("utf-8", "replace"), erro.headers.get("Location") or ""


def _resp_token(access="AT1", refresh="RT1", dura=7 * 86400):
    agora = int(time.time())
    return {"code": 0, "message": "success", "data": {
        "access_token": access, "access_token_expire_in": agora + dura,
        "refresh_token": refresh, "refresh_token_expire_in": agora + 365 * 86400,
        "open_id": "OPEN1", "seller_name": "Noromak Bauru", "seller_base_region": "BR",
        "granted_scopes": ["seller.product.basic"]}}


LOJAS = {"code": 0, "data": {"shops": [
    {"id": "7000", "name": "Noromak Bauru", "region": "BR", "seller_type": "LOCAL", "cipher": "CIPHER-BAURU", "code": "BRX"}]}}


class TiktokFase0(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "APPKEY"), ("TIKTOK_APP_SECRET", "SEGREDO"),
                            ("TIKTOK_SERVICE_ID", "SERV1")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def _conectar(self, **kw) -> None:
        url = S.tiktok_connect_url()
        state = url.split("state=")[1].split("&")[0]
        with mock.patch.object(S, "_tiktok_token_http", return_value=_resp_token(**kw)), \
                mock.patch.object(S, "_tiktok_http", return_value=LOJAS):
            S.tiktok_exchange_code("CODE", state)

    def test_url_de_autorizacao(self):
        url = S.tiktok_connect_url()
        self.assertTrue(url.startswith("https://services.tiktokshop.com/open/authorize?"), url)
        self.assertIn("service_id=SERV1", url)
        self.assertIn("state=", url)

    def test_sem_credenciais_nao_conecta(self):
        with mock.patch.object(S, "TIKTOK_APP_SECRET", ""):
            with self.assertRaises(RuntimeError):
                S.tiktok_connect_url()

    def test_assinatura_da_documentacao(self):
        params = {"app_key": "APPKEY", "timestamp": "1700000000", "shop_cipher": "C",
                  "sign": "ignorar", "access_token": "ignorar", "page_size": "10"}
        corpo = '{"a":1}'
        base = "/product/202309/products/1/inventory/update" + "app_key" + "APPKEY" + "page_size" + "10" \
            + "shop_cipher" + "C" + "timestamp" + "1700000000" + corpo
        esperado = hmac.new(b"SEGREDO", ("SEGREDO" + base + "SEGREDO").encode(), hashlib.sha256).hexdigest()
        self.assertEqual(S.tiktok_assinar("/product/202309/products/1/inventory/update", params, corpo), esperado)

    def test_conectar_grava_token_e_loja_e_status_nao_vaza(self):
        self._conectar()
        row = S.tiktok_integration_row()
        self.assertEqual(row["shop_cipher"], "CIPHER-BAURU")
        self.assertEqual(row["casa"], "bauru")
        st = S.tiktok_status_payload()
        self.assertTrue(st["connected"])
        self.assertEqual(st["shop_name"], "Noromak Bauru")
        status, corpo = self.cliente.pedir("GET", "/api/integracoes/tiktok/status")
        self.assertEqual(status, 200)
        texto = json.dumps(corpo)
        for segredo in ("AT1", "RT1", "CIPHER-BAURU", "SEGREDO"):
            self.assertNotIn(segredo, texto)

    def test_state_de_uso_unico(self):
        with self.assertRaises(RuntimeError):
            S.tiktok_exchange_code("CODE", "inventado")
        url = S.tiktok_connect_url()
        state = url.split("state=")[1].split("&")[0]
        with mock.patch.object(S, "_tiktok_token_http", return_value=_resp_token()), \
                mock.patch.object(S, "_tiktok_http", return_value=LOJAS):
            S.tiktok_exchange_code("CODE", state)
            with self.assertRaises(RuntimeError):
                S.tiktok_exchange_code("CODE", state)

    def test_erro_do_tiktok_nao_grava(self):
        url = S.tiktok_connect_url()
        state = url.split("state=")[1].split("&")[0]
        with mock.patch.object(S, "_tiktok_token_http", return_value={"code": 36004004, "message": "auth code expired"}):
            with self.assertRaises(RuntimeError):
                S.tiktok_exchange_code("CODE", state)
        self.assertFalse(S.tiktok_status_payload()["connected"])

    def test_renovacao_serializada(self):
        self._conectar(dura=10)
        chamadas = []

        def renovar(caminho, params):
            chamadas.append((caminho, dict(params)))
            time.sleep(0.2)
            return _resp_token("AT2", "RT2")

        with mock.patch.object(S, "_tiktok_token_http", side_effect=renovar):
            ts = [threading.Thread(target=S.tiktok_access_token) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(len(chamadas), 1, "REGRESSÃO: duas renovações gastaram o mesmo refresh_token")
        self.assertEqual(chamadas[0][0], "/api/v2/token/refresh")
        self.assertEqual(chamadas[0][1]["refresh_token"], "RT1")
        self.assertEqual(chamadas[0][1]["grant_type"], "refresh_token")
        self.assertEqual(S.tiktok_integration_row()["refresh_token"], "RT2")

    def test_chamada_assinada_leva_token_no_cabecalho_e_cipher(self):
        self._conectar()
        enviados = []

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def abrir(req, timeout=0):
            enviados.append(req)
            return Resp(json.dumps({"code": 0, "data": {"warehouses": []}}).encode())

        with mock.patch.object(S.urllib.request, "urlopen", side_effect=abrir):
            S.tiktok_api("GET", "/logistics/202309/warehouses")
        req = enviados[0]
        self.assertTrue(req.full_url.startswith("https://open-api.tiktokglobalshop.com/logistics/202309/warehouses?"))
        self.assertIn("shop_cipher=CIPHER-BAURU", req.full_url)
        self.assertIn("sign=", req.full_url)
        self.assertNotIn("access_token=", req.full_url, "token vai no cabeçalho, nunca na URL")
        self.assertEqual(req.get_header("X-tts-access-token"), "AT1")

    def test_callback_com_erro_volta_ao_admin(self):
        st, _, loc = _get_cru(self.url_base + "/api/integracoes/tiktok/callback?code=X&state=inventado")
        self.assertEqual(st, 302)
        self.assertIn("tiktok=erro", loc)

    def test_status_exige_login_e_consulta_nao_conecta(self):
        st, _, _ = _get_cru(self.url_base + "/api/integracoes/tiktok/status")
        self.assertIn(st, (401, 403))
        self.assertIn("/api/integracoes/tiktok/connect", S.CONSULTA_GET_BLOQUEADOS)

    def test_limpeza_inclui_log_do_tiktok(self):
        S.tiktok_log_event("teste", "", "OK")
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_event_log SET created_at='2000-01-01T00:00:00'")
            S.DB.commit()
        self.assertEqual(S.limpar_logs_antigos().get("tiktok_event_log"), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
