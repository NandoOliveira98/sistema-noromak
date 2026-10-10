"""V18.94 — conta do Mercado Livre por casa (Araçatuba e Bauru).

O que não pode quebrar:
- Araçatuba (casa principal) continua exatamente onde estava (ml_integration id=1):
  conectar Bauru nunca toca nela;
- a mesma conta do ML nunca fica nas duas casas (pedido entraria duas vezes);
- o aviso (webhook) vai para a casa dona do user_id;
- pedido importado pela conta de Bauru entra marcado como Bauru, e cada casa só revê
  (etiqueta, reconciliação) os pedidos dela, com a conta dela;
- cada casa renova o próprio token (refresh_token do ML é de uso único).
"""

import json
import time
import unittest
from unittest import mock

from base import RAIZ, S, TesteNoromak, gravar_como_integracao, linha, pedidos


def token(seller: str, acesso: str, refresh: str = "R") -> dict:
    return {"user_id": seller, "access_token": acesso, "refresh_token": refresh, "token_type": "Bearer",
            "scope": "offline_access read write", "expires_in": 21600}


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE", VendaMercadoLivre=venda,
                 ShipmentIdMercadoLivre="SHIP-" + venda, OrigemCadastro="API Mercado Livre", **extra)


class CasasMercadoLivre(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(S, "ml_credentials_configured", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def conectar(self, casa: str, seller: str, acesso: str) -> None:
        with S.ml_na_casa(casa):
            S.ml_store_token(token(seller, acesso), nickname=f"conta-{casa}")

    def test_bauru_nao_toca_na_conta_de_aracatuba(self):
        self.conectar("aracatuba", "111", "TOK-ARA")
        self.conectar("bauru", "222", "TOK-BAU")
        self.assertEqual(S.ml_integration_row()["access_token"], "TOK-ARA", "sem casa = Araçatuba, como sempre")
        with S.ml_na_casa("bauru"):
            self.assertEqual(S.ml_integration_row()["seller_id"], "222")
            self.assertEqual(S.ml_access_token(), ("TOK-BAU", "222"))
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT access_token FROM ml_integration WHERE id=1").fetchone()[0], "TOK-ARA")
        self.assertEqual(S.ml_casas_conectadas(), ["aracatuba", "bauru"])
        self.assertEqual(S.ml_casa_atual(), "aracatuba", "o contexto vazou para fora do with")

    def test_mesma_conta_nas_duas_casas_e_recusada(self):
        self.conectar("aracatuba", "111", "TOK-ARA")
        with S.ml_na_casa("bauru"), self.assertRaisesRegex(RuntimeError, "já está conectada em Araçatuba"):
            S.ml_store_token(token("111", "OUTRO"))
        with S.ml_na_casa("bauru"):
            self.assertIsNone(S.ml_integration_row())

    def test_casa_desconhecida(self):
        with self.assertRaises(ValueError):
            with S.ml_na_casa("ribeirao"):
                pass

    def test_cada_casa_renova_o_proprio_token(self):
        self.assertIs(S._ml_token_lock(), S.ML_TOKEN_LOCK)
        with S.ml_na_casa("bauru"):
            self.assertIsNot(S._ml_token_lock(), S.ML_TOKEN_LOCK)
        self.conectar("aracatuba", "111", "TOK-ARA")
        self.conectar("bauru", "222", "TOK-BAU")
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_integration_casa_v1894 SET expires_at_epoch=? WHERE casa='bauru'", (int(time.time()) - 10,))
            S.DB.commit()
        pedidos_refresh = []

        def http(metodo, url, data=None, **k):
            pedidos_refresh.append(dict(data or {}))
            return token("222", "TOK-BAU-NOVO", "R2")
        with mock.patch.object(S, "ml_http_json", side_effect=http), mock.patch.object(S, "ml_credentials_configured", return_value=True):
            with S.ml_na_casa("bauru"):
                self.assertEqual(S.ml_access_token()[0], "TOK-BAU-NOVO")
        self.assertEqual(len(pedidos_refresh), 1)
        self.assertEqual(S.ml_integration_row()["access_token"], "TOK-ARA", "renovar Bauru mexeu em Araçatuba")

    def test_oauth_guarda_a_casa_que_conectou(self):
        with mock.patch.object(S, "ml_credentials_configured", return_value=True):
            url = S.ml_oauth_authorization_url("bauru")
        state = url.split("state=")[1].split("&")[0]
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT casa FROM ml_oauth_state WHERE state=?", (state,)).fetchone()[0], "bauru")

        def http(metodo, url, data=None, token=None, **k):
            if url.endswith("/oauth/token"):
                return {"user_id": "222", "access_token": "TOK-BAU", "refresh_token": "R", "scope": "offline_access", "expires_in": 21600}
            return {"nickname": "NOROMAK BAURU"}
        with mock.patch.object(S, "ml_http_json", side_effect=http), mock.patch.object(S, "ml_credentials_configured", return_value=True):
            status = S.ml_exchange_authorization_code("CODE", state)
        self.assertEqual(status["casa"], "bauru")
        self.assertIsNone(S.ml_integration_row(), "a conexão de Bauru foi gravada em Araçatuba")
        with S.ml_na_casa("bauru"):
            self.assertEqual(S.ml_integration_row()["nickname"], "NOROMAK BAURU")

    def test_aviso_do_ml_vai_para_a_casa_do_seller(self):
        self.conectar("aracatuba", "111", "TOK-ARA")
        self.conectar("bauru", "222", "TOK-BAU")
        casas = []
        with mock.patch.object(S, "ml_import_order", side_effect=lambda *a, **k: casas.append(S.ml_casa_atual()) or {}):
            S.ml_process_notification({"topic": "orders_v2", "resource": "/orders/9", "user_id": "222"})
            S.ml_process_notification({"topic": "orders_v2", "resource": "/orders/8", "user_id": "111"})
            S.ml_process_notification({"topic": "orders_v2", "resource": "/orders/7", "user_id": "999"})
        self.assertEqual(casas, ["bauru", "aracatuba"], "seller de fora não pode ser importado")

    def test_pedido_de_bauru_entra_marcado(self):
        self.conectar("bauru", "222", "TOK-BAU")
        order = {"id": "5001", "status": "paid", "seller": {"id": "222"}, "buyer": {"billing_info": {"id": "B1"}},
                 "order_items": [{"item": {"id": "MLB1", "title": "Filtro", "seller_sku": "SKU-1"}, "quantity": 1, "unit_price": 10}]}
        with mock.patch.object(S, "ml_api_get", return_value=order), \
                mock.patch.object(S, "_ml_resolve_forward_shipment", return_value=("SHIP-5001", {"status": "ready_to_ship", "logistic_type": "cross_docking"})), \
                mock.patch.object(S, "_ml_fetch_shipping_zpl", return_value=""):
            with S.ml_na_casa("bauru"):
                S.ml_import_order("5001", source="teste")
        linhas = [r for r in pedidos() if str(r.get("VendaMercadoLivre")) == "5001"]
        self.assertTrue(linhas, "o pedido não entrou")
        self.assertEqual({r.get("Casa") for r in linhas}, {"bauru"})

    def test_cada_casa_so_reve_os_pedidos_dela(self):
        gravar_como_integracao([linha_ml("A1", "S1"), linha_ml("B1", "S2", Casa="bauru")], origem="API Mercado Livre")
        self.assertEqual(S._ml_all_pending_label_candidates(), [("A1", "SHIP-A1")], "linha sem casa é de Araçatuba")
        with S.ml_na_casa("bauru"):
            self.assertEqual(S._ml_all_pending_label_candidates(), [("B1", "SHIP-B1")])
            self.assertNotIn("a1", {x.lower() for x in S._ml_manual_pending_numbers()})

    def test_log_marca_a_casa(self):
        with S.ml_na_casa("bauru"):
            S.ml_log_event("new_order_watcher", "/orders/search", "", "", "OK", "")
        with S.DB_LOCK:
            topico = S.DB.execute("SELECT topic FROM ml_event_log ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(topico, "[Bauru] new_order_watcher")

    def test_status_por_casa_na_tela(self):
        self.conectar("bauru", "222", "TOK-BAU")
        status, corpo = self.cliente.pedir("GET", "/api/integracoes/mercadolivre/status?casa=bauru")
        self.assertEqual(status, 200, corpo)
        self.assertEqual((corpo["mercadolivre"]["casa"], corpo["mercadolivre"]["seller_id"]), ("bauru", "222"))
        status, corpo = self.cliente.pedir("GET", "/api/integracoes/mercadolivre/status")
        self.assertEqual(corpo["mercadolivre"]["casa"], "aracatuba")
        self.assertEqual(self.cliente.pedir("GET", "/api/integracoes/mercadolivre/status?casa=xyz")[0], 400)

    def test_cartao_de_bauru_na_central(self):
        admin = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="mlBauruCard"', admin)
        self.assertIn("mercadolivre/connect?casa=bauru", admin)


if __name__ == "__main__":
    unittest.main()
