"""V19.24 — Magalu, fase 0: páginas públicas de termos/privacidade e conexão OAuth.

O cadastro do app no ID Magalu (CLI `idm client create`) exige três endereços públicos:
termos de uso, política de privacidade e o callback do OAuth. Sem eles no ar, não dá nem
para criar as credenciais. Aqui só conecta: nada lê pedido nem grava na fila.
"""

import threading
import time
import urllib.error
import urllib.request
from unittest import mock

from base import S, TesteNoromak


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # devolve o 302 em vez de seguir
        return None


def _get_cru(url: str) -> tuple[int, str, str]:
    abridor = urllib.request.build_opener(_SemRedirecionar)
    try:
        with abridor.open(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Location") or ""
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read().decode("utf-8", "replace"), erro.headers.get("Location") or ""


class MagaluFase0(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("MAGALU_CLIENT_ID", "CLI-ID"), ("MAGALU_CLIENT_SECRET", "SEGREDO")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def _conectar(self, access="AT1", refresh="RT1", expira=7200) -> None:
        url = S.magalu_connect_url()
        state = url.split("state=")[1].split("&")[0]
        resp = {"access_token": access, "refresh_token": refresh, "expires_in": expira, "scope": "open:order-order-seller:read"}
        with mock.patch.object(S, "_magalu_token_http", return_value=resp):
            S.magalu_exchange_code("CODE", state)

    def test_termos_e_privacidade_abrem_sem_login(self):
        for rota in ("/termos", "/privacidade"):
            st, corpo, _ = _get_cru(self.url_base + rota)
            self.assertEqual(st, 200, rota)
            self.assertIn("Noromak", corpo, rota)
            self.assertNotIn("web-auth.js", corpo, f"{rota} precisa ser pública: a Magalu abre sem login")

    def test_url_de_consentimento(self):
        url = S.magalu_connect_url()
        self.assertTrue(url.startswith("https://id.magalu.com/login?"), url)
        for parte in ("client_id=CLI-ID", "response_type=code", "choose_tenants=true", "state="):
            self.assertIn(parte, url)
        self.assertIn("magalu%2Fcallback", url)

    def test_sem_credenciais_nao_conecta(self):
        with mock.patch.object(S, "MAGALU_CLIENT_ID", ""):
            with self.assertRaises(RuntimeError):
                S.magalu_connect_url()

    def test_state_invalido_ou_reusado_recusado(self):
        with self.assertRaises(RuntimeError):
            S.magalu_exchange_code("CODE", "inventado")
        url = S.magalu_connect_url()
        state = url.split("state=")[1].split("&")[0]
        resp = {"access_token": "A", "refresh_token": "R", "expires_in": 7200}
        with mock.patch.object(S, "_magalu_token_http", return_value=resp):
            S.magalu_exchange_code("CODE", state)
            with self.assertRaises(RuntimeError, msg="state é de uso único"):
                S.magalu_exchange_code("CODE", state)

    def test_conectar_grava_token_e_status_nao_vaza(self):
        self._conectar()
        st = S.magalu_status_payload()
        self.assertTrue(st["connected"])
        status, corpo = self.cliente.pedir("GET", "/api/integracoes/magalu/status")
        self.assertEqual(status, 200)
        texto = str(corpo)
        self.assertNotIn("AT1", texto)
        self.assertNotIn("RT1", texto)

    def test_renovacao_serializada_e_refresh_trocado(self):
        self._conectar(expira=10)  # já vence dentro da margem
        chamadas = []

        def renovar(dados):
            chamadas.append(dict(dados))
            time.sleep(0.2)
            return {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200}

        with mock.patch.object(S, "_magalu_token_http", side_effect=renovar):
            ts = [threading.Thread(target=S.magalu_access_token) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(len(chamadas), 1, "REGRESSÃO: duas renovações gastaram o mesmo refresh_token")
            self.assertEqual(chamadas[0]["refresh_token"], "RT1")
            self.assertEqual(chamadas[0]["grant_type"], "refresh_token")
            self.assertEqual(S.magalu_access_token(), "AT2")
        row = S.magalu_integration_row()
        self.assertEqual(row["refresh_token"], "RT2")

    def test_callback_publico_com_erro_volta_ao_admin(self):
        st, _, loc = _get_cru(self.url_base + "/api/integracoes/magalu/callback?code=X&state=inventado")
        self.assertEqual(st, 302)
        self.assertIn("magalu=erro", loc)
        self.assertFalse(S.magalu_status_payload()["connected"])

    def test_status_exige_login(self):
        st, _, _ = _get_cru(self.url_base + "/api/integracoes/magalu/status")
        self.assertIn(st, (401, 403))

    def test_limpeza_inclui_log_da_magalu(self):
        S.magalu_log_event("teste", "", "OK")
        with S.DB_LOCK:
            S.DB.execute("UPDATE magalu_event_log SET created_at='2000-01-01T00:00:00'")
            S.DB.commit()
        self.assertEqual(S.limpar_logs_antigos().get("magalu_event_log"), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
