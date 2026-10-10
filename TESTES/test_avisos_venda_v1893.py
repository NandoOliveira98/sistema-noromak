"""V18.93 — avisos de venda (popup com som e notificação com o site fechado).

O que não pode quebrar:
- a assinatura VAPID (ES256, feita em Python puro) tem de ser válida, senão o Google/Apple
  recusam todo push;
- no dia da publicação, a fila que já existe NÃO vira uma enxurrada de avisos;
- venda vista de novo não avisa de novo; carrinho do ML é uma venda só;
- o aparelho com push a caminho não ganha outro (sem pilha de notificações), e renovar a
  inscrição ao abrir a tela não pode pular a venda que ainda vai ser entregue;
- só quem tem o módulo Admin recebe; aparelho que o serviço de push dá como sumido sai.
"""

import base64
import hashlib
import json
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock

from base import RAIZ, S, USUARIO_ADMIN, TesteNoromak

APP = RAIZ / "APP"
FCM = "https://fcm.googleapis.com/fcm/send/abc123"


def _b64d(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def verificar_es256(publica_b64: str, token: str) -> bool:
    """Verificação ECDSA P-256 independente da assinatura (u1·G + u2·Q)."""
    pub = _b64d(publica_b64)
    q = (int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big"))
    cabeca, corpo, assinatura = token.split(".")
    sig = _b64d(assinatura)
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    n = S._P256_N
    e = int.from_bytes(hashlib.sha256(f"{cabeca}.{corpo}".encode()).digest(), "big")
    w = pow(s, -1, n)
    x = S._p256_somar(S._p256_multiplicar(e * w % n), S._p256_multiplicar(r * w % n, q))
    return x is not None and x[0] % n == r


def agora_iso(horas: float = 0) -> str:
    return (datetime.now(S.BR_TZ) - timedelta(hours=horas)).isoformat()


def venda(venda_id: str, horas: float = 0, valor: float = 100.0, titulo: str = "Pastilha") -> dict:
    return {"venda_id": venda_id, "venda_em": agora_iso(horas), "valor": valor, "titulo": titulo, "qtd": 1, "itens": 1}


class AssinaturaVapid(unittest.TestCase):

    def test_vetor_oficial_rfc6979(self):
        d = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
        x, y = S._p256_multiplicar(d)
        self.assertEqual(x, 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6)
        self.assertEqual(y, 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299)
        r, s = S.p256_assinar(d, b"sample")
        self.assertEqual(r, 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716)
        self.assertEqual(s, 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8)


class Avisos(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._AVISOS_JWT_CACHE.clear()
        S.AVISOS_VENDA_EVENT.clear()
        self.enviados: list[str] = []
        self.resposta_push = 201
        patcher = mock.patch.object(S, "_avisos_push_enviar", side_effect=self._push_falso)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _push_falso(self, endpoint):
        self.enviados.append(endpoint)
        return self.resposta_push, ""

    def inscrever(self, endpoint=FCM, **extra):
        return self.cliente.pedir("POST", "/api/avisos-venda/inscrever",
                                  {"inscricao": {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}},
                                   "aparelho": "Chrome · Android", **extra})

    def entregar(self, endpoint=FCM):
        return self.cliente.pedir("POST", "/api/avisos-venda/entregar", {"endpoint": endpoint})

    def base_pronta(self):
        """Simula o 1º ciclo depois da publicação: o que já existe entra calado."""
        S.avisos_venda_registrar("ml", [venda("ANTIGA-1")])
        S.avisos_venda_registrar("shopee", [venda("SN-ANTIGA")])

    # ----------------------------------------------------------------- VAPID
    def test_jwt_vapid_assinado_e_valido(self):
        token = S._avisos_vapid_jwt("https://fcm.googleapis.com")
        claims = json.loads(_b64d(token.split(".")[1]))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertGreater(claims["exp"], datetime.now().timestamp())
        self.assertLessEqual(claims["exp"] - datetime.now().timestamp(), 24 * 3600, "o push service recusa exp > 24 h")
        self.assertTrue(verificar_es256(S.avisos_vapid_publica(), token), "assinatura ES256 inválida")

    def test_chave_nunca_muda(self):
        _, a = self.cliente.pedir("GET", "/api/avisos-venda/chave")
        _, b = self.cliente.pedir("GET", "/api/avisos-venda/chave")
        self.assertEqual(a["chave"], b["chave"])
        self.assertEqual(len(_b64d(a["chave"])), 65)

    # ----------------------------------------------------------------- vigia
    def test_primeira_leitura_nao_avisa_a_fila_inteira(self):
        self.assertEqual(S.avisos_venda_registrar("ml", [venda("1"), venda("2")]), 0)
        self.assertFalse(S.AVISOS_VENDA_EVENT.is_set())
        self.assertEqual(S.avisos_venda_registrar("ml", [venda("1"), venda("2"), venda("3")]), 1)
        self.assertTrue(S.AVISOS_VENDA_EVENT.is_set())
        # A Shopee tem a própria 1ª leitura.
        self.assertEqual(S.avisos_venda_registrar("shopee", [venda("SN1")]), 0)

    def test_venda_repetida_ou_antiga_nao_avisa(self):
        self.base_pronta()
        self.assertEqual(S.avisos_venda_registrar("ml", [venda("10")]), 1)
        self.assertEqual(S.avisos_venda_registrar("ml", [venda("10")]), 0, "mesma venda avisou duas vezes")
        self.assertEqual(S.avisos_venda_registrar("ml", [venda("11", horas=72)]), 0, "venda de 3 dias atrás avisou")

    def test_carrinho_do_ml_e_uma_venda(self):
        orders = [
            {"id": 1, "pack_id": 900, "status": "paid", "date_closed": agora_iso(), "total_amount": 50.0,
             "order_items": [{"item": {"title": "Filtro de óleo"}, "quantity": 2, "unit_price": 25.0}]},
            {"id": 2, "pack_id": 900, "status": "paid", "date_closed": agora_iso(), "total_amount": 30.0,
             "order_items": [{"item": {"title": "Vela"}, "quantity": 1, "unit_price": 30.0}]},
            {"id": 3, "status": "paid", "date_closed": agora_iso(), "total_amount": 1234.5,
             "order_items": [{"item": {"title": "Kit embreagem"}, "quantity": 1, "unit_price": 1234.5}]},
            {"id": 4, "status": "cancelled", "date_closed": agora_iso(), "total_amount": 9.0, "order_items": []},
        ]
        vendas = {v["venda_id"]: v for v in S.avisos_vendas_do_ml(orders)}
        self.assertEqual(set(vendas), {"pack:900", "3"})
        self.assertEqual(vendas["pack:900"]["valor"], 80.0)
        self.assertEqual(vendas["pack:900"]["qtd"], 3)
        self.assertEqual(vendas["pack:900"]["itens"], 2)

    def test_shopee_valor_com_desconto(self):
        pedidos = [{"order_sn": "SN9", "create_time": int(datetime.now().timestamp()), "item_list": [
            {"item_name": "Lâmpada H4", "model_quantity_purchased": 2, "model_discounted_price": 19.9, "model_original_price": 25},
        ]}]
        v = S.avisos_vendas_da_shopee(pedidos)[0]
        self.assertEqual((v["venda_id"], round(v["valor"], 2), v["qtd"], v["titulo"]), ("SN9", 39.8, 2, "Lâmpada H4"))

    def test_vigia_do_ml_registra_sem_chamada_a_mais(self):
        S.avisos_venda_registrar("ml", [venda("BASE")])
        order = {"id": 777, "status": "paid", "date_closed": agora_iso(), "total_amount": 59.9,
                 "order_items": [{"item": {"title": "Palheta"}, "quantity": 1, "unit_price": 59.9}]}
        chamadas = []

        def api_get(caminho):
            chamadas.append(caminho)
            return {"results": [order]}
        with mock.patch.object(S, "ml_access_token", return_value=("tok", "1")), \
                mock.patch.object(S, "ml_api_get", side_effect=api_get), \
                mock.patch.object(S, "_ml_completed_order_ids", return_value={"777"}), \
                mock.patch.object(S, "_ml_linked_order_ids", return_value=set()), \
                mock.patch.object(S, "_ml_existing_order_ids", return_value=set()), \
                mock.patch.object(S, "_ml_full_ignorados", return_value=set()):
            S.ml_new_order_watch_once()
        self.assertEqual(len(chamadas), 1, "o aviso não pode fazer chamada a mais ao ML")
        self.assertEqual(S.avisos_venda_recentes(0)["vendas"][0]["venda_id"], "777")

    # ----------------------------------------------------------------- envio e entrega
    def test_fluxo_completo_um_push_por_vez(self):
        self.base_pronta()
        status, corpo = self.inscrever()
        self.assertEqual(status, 200, corpo)
        S.avisos_venda_registrar("ml", [venda("20", valor=1234.5, titulo="Kit embreagem")])
        self.assertEqual(S.avisos_venda_enviar_rodada()["enviados"], 1)
        S.avisos_venda_registrar("shopee", [venda("SN20", valor=59.9)])
        self.assertEqual(S.avisos_venda_enviar_rodada()["enviados"], 0, "push novo com o anterior ainda a caminho")
        # A tela foi aberta no meio (renova a inscrição): não pode pular as vendas a caminho.
        self.inscrever(renovar=True)
        status, corpo = self.entregar()
        self.assertEqual(status, 200, corpo)
        self.assertEqual([v["titulo"] for v in corpo["vendas"]], ["Venda ML · R$ 1.234,50", "Venda Shopee · R$ 59,90"])
        self.assertEqual(corpo["vendas"][0]["corpo"], "Kit embreagem (1 un.)")
        self.assertEqual(self.entregar()[1]["vendas"], [], "a mesma venda foi entregue duas vezes")
        self.assertEqual(S.avisos_venda_enviar_rodada()["enviados"], 0)
        self.assertEqual(len(self.enviados), 1)

    def test_aparelho_novo_nao_recebe_venda_antiga(self):
        self.base_pronta()
        S.avisos_venda_registrar("ml", [venda("30")])
        self.inscrever()
        self.assertEqual(S.avisos_venda_enviar_rodada()["enviados"], 0)
        self.assertEqual(self.entregar()[1]["vendas"], [])

    def test_endereco_de_push_fora_da_lista_e_recusado(self):
        for ruim in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://googleapis.com.exemplo.net/x"):
            status, _ = self.inscrever(ruim)
            self.assertEqual(status, 400, ruim)

    def test_aparelho_sumido_sai_da_lista(self):
        self.base_pronta()
        self.inscrever()
        self.resposta_push = 410
        S.avisos_venda_registrar("ml", [venda("40")])
        S.avisos_venda_enviar_rodada()
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM aviso_inscricao_v1893").fetchone()[0], 0)

    def test_sem_modulo_admin_nao_recebe(self):
        self.base_pronta()
        self.inscrever()
        with S.DB_LOCK:
            S.DB.execute("UPDATE app_users SET can_admin=0 WHERE username=?", (USUARIO_ADMIN,))
            S.DB.commit()
        S.avisos_venda_registrar("ml", [venda("50")])
        self.assertEqual(S.avisos_venda_enviar_rodada()["enviados"], 0)
        self.assertEqual(self.entregar()[1]["vendas"], [])

    def test_entregar_sem_inscricao_nao_mostra_nada(self):
        self.base_pronta()
        S.avisos_venda_registrar("ml", [venda("60")])
        status, corpo = self.entregar("https://fcm.googleapis.com/fcm/send/outro")
        self.assertEqual((status, corpo["vendas"]), (200, []))

    def test_teste_chega_so_neste_aparelho(self):
        self.inscrever()
        status, corpo = self.cliente.pedir("POST", "/api/avisos-venda/testar", {"endpoint": FCM})
        self.assertEqual(status, 200, corpo)
        vendas = self.entregar()[1]["vendas"]
        self.assertEqual([v["marketplace"] for v in vendas], ["teste"])
        self.assertEqual(self.entregar()[1]["vendas"], [])
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM aviso_venda_v1893").fetchone()[0], 0,
                             "o teste não pode virar venda")

    def test_tela_aberta_sem_push(self):
        self.base_pronta()
        _, chave = self.cliente.pedir("GET", "/api/avisos-venda/chave")
        S.avisos_venda_registrar("ml", [venda("70")])
        status, corpo = self.cliente.pedir("GET", f"/api/avisos-venda/recentes?desde={chave['ultimo_id']}")
        self.assertEqual(status, 200, corpo)
        self.assertEqual([v["venda_id"] for v in corpo["vendas"]], ["70"])

    def test_desligar(self):
        self.inscrever()
        status, _ = self.cliente.pedir("POST", "/api/avisos-venda/desinscrever", {"endpoint": FCM})
        self.assertEqual(status, 200)
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM aviso_inscricao_v1893").fetchone()[0], 0)


class Telas(unittest.TestCase):

    def test_worker_no_painel_de_saude(self):
        self.assertIn("avisos_venda_loop", S.SAUDE_WORKERS_INFO)

    def test_service_worker_e_menu(self):
        sw = (APP / "sw.js").read_text(encoding="utf-8")
        self.assertIn('addEventListener("push"', sw)
        self.assertIn('addEventListener("notificationclick"', sw)
        self.assertIn("/api/avisos-venda/entregar", sw)
        # O mesmo endereço do service worker nos dois lugares: outro endereço reinstala o worker.
        url_pwa = re.search(r"sw\.js\?v=[\d.]+", (APP / "pwa-install.js").read_text(encoding="utf-8")).group(0)
        self.assertIn(url_pwa, (APP / "nk-avisos.js").read_text(encoding="utf-8"))
        menu = (APP / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn("nk-avisos.js", menu)
        self.assertIn('data-nk-avisos="1" data-nk-modulo="admin"', menu)
        # A Conferência não ganha popup com som (atrapalha a bipagem).
        conf = (APP / "conferencia.html").read_text(encoding="utf-8")
        self.assertNotIn("nk-avisos.js", conf)
        self.assertNotIn("nk-menu.js", conf)

    def test_central_de_integracoes_v1893(self):
        """Pedido do dono (10/10): sem os botões de verificar etiquetas e nomes pela NF-e; log minimizável."""
        admin = (APP / "admin.html").read_text(encoding="utf-8")
        self.assertNotIn('id="btnMlVerificarEtiquetas"', admin)
        self.assertNotIn('id="btnMlAtualizarNomesNfe"', admin)
        self.assertIn('id="btnMlLogMinimizar"', admin)
        self.assertIn('<div id="mlLogCorpo">', admin)


if __name__ == "__main__":
    unittest.main()
