"""V18.97 — venda cujo envio já saiu não entra (nem fica) na fila.

Produção 10/10: ao conectar a conta de Bauru, o vigia de pedidos novos trouxe as vendas pagas
de setembro/outubro que já tinham sido entregues; o dono teve de excluir uma a uma (Bauru não
tinha nada pendente de envio). Mesma regra do Full: fora da fila e na memória do vigia.
"""

from unittest import mock

from base import S, TesteNoromak, gravar_como_admin, gravar_como_integracao, linha, pedidos


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"MLAPI_{venda}_{sku}", Marketplace="MERCADO LIVRE", VendaMercadoLivre=venda,
                 ShipmentIdMercadoLivre="SHIP-" + venda, OrigemCadastro="API Mercado Livre",
                 **{"Etiqueta API Mercado Livre": "AGUARDANDO"}, **extra)


class VendaDespachada(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._ml_full_ignorados_cache_limpar()
        for alvo, valor in (("ml_access_token", ("tok", "SELLER")), ("ml_credentials_configured", True)):
            p = mock.patch.object(S, alvo, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def importar(self, venda: str, status_envio: str) -> dict:
        order = {"id": venda, "status": "paid", "seller": {"id": "SELLER"}, "buyer": {"billing_info": {"id": "B1"}},
                 "order_items": [{"item": {"id": "MLB1", "title": "Filtro", "seller_sku": "SKU-1"}, "quantity": 1, "unit_price": 10}]}
        envio = {"id": "SHIP-" + venda, "status": status_envio, "logistic_type": "xd_drop_off"}
        with mock.patch.object(S, "ml_api_get", return_value=order), \
                mock.patch.object(S, "_ml_resolve_forward_shipment", return_value=("SHIP-" + venda, envio)), \
                mock.patch.object(S, "_ml_fetch_shipping_zpl", return_value=""):
            return S.ml_import_order(venda, source="teste")

    def vendas(self) -> set[str]:
        return {str(r.get("VendaMercadoLivre") or "") for r in pedidos()}

    def test_venda_entregue_nao_entra(self):
        for st in ("delivered", "shipped", "not_delivered"):
            r = self.importar("V-" + st, st)
            self.assertEqual((r.get("ignored"), r.get("reason")), (True, "DESPACHADO"), st)
        self.assertFalse(self.vendas() & {"V-delivered", "V-shipped", "V-not_delivered"},
                         "REGRESSÃO: venda já entregue entrou na fila")
        self.assertIn("V-delivered", S._ml_full_ignorados(), "o vigia voltaria a importar a cada 20 s")

    def test_venda_pronta_para_envio_continua_entrando(self):
        self.importar("V-PRONTA", "ready_to_ship")
        self.assertIn("V-PRONTA", self.vendas())

    def test_linha_da_integracao_sai_e_a_do_admin_fica(self):
        gravar_como_integracao([linha_ml("V1", "SKU-1")], origem="API Mercado Livre")
        gravar_como_admin([*pedidos(), linha_ml("V2", "SKU-2", ManualAdminProtegido="SIM")])
        self.importar("V1", "delivered")
        self.importar("V2", "delivered")
        self.assertNotIn("V1", self.vendas())
        self.assertIn("V2", self.vendas(), "o pedido incluído pelo ADMIN não pode sumir")

    def test_vigia_de_etiquetas_tira_a_venda_ja_entregue(self):
        gravar_como_integracao([linha_ml("V3", "SKU-3"), linha_ml("V4", "SKU-4")], origem="API Mercado Livre")
        importados = []
        original = S.ml_import_order

        def api_get(caminho, **k):
            return {"status": "delivered" if "SHIP-V3" in caminho else "pending"}

        def importar(order_id, source=""):
            importados.append(order_id)
            order = {"id": order_id, "status": "paid", "seller": {"id": "SELLER"}, "buyer": {"billing_info": {"id": "B1"}},
                     "order_items": [{"item": {"id": "MLB1", "title": "F", "seller_sku": "SKU-3"}, "quantity": 1, "unit_price": 10}]}
            with mock.patch.object(S, "ml_api_get", return_value=order),                     mock.patch.object(S, "_ml_resolve_forward_shipment",
                                      return_value=("SHIP-" + order_id, {"status": "delivered"})):
                return original(order_id, source=source)
        with mock.patch.object(S, "ml_api_get", side_effect=api_get), \
                mock.patch.object(S, "ml_import_order", side_effect=importar):
            S.ml_pending_label_watch_once()
        self.assertEqual(importados, ["V3"])
        self.assertNotIn("V3", self.vendas())
        self.assertIn("V4", self.vendas())
