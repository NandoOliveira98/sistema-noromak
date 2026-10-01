"""V18.52 — venda cancelada (ou Full) no Mercado Livre sai MESMO da fila.

Em produção (29/09/2026) 5 vendas canceladas continuavam em Pedidos pendentes
como "paid": o watcher removia a linha, mas gravava sem permissão de exclusão e
`preserve_pending_records_v1714` devolvia a linha no mesmo `save_storage`. O log
dizia "REMOVIDO DOS PENDENTES" a cada ~25 s, 15 mil vezes por dia, e nada saía.
"""

import unittest

from base import S, TesteNoromak, gravar_como_admin, gravar_como_integracao, linha, pedidos


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE",
                 VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre", **extra)


class CancelamentoMercadoLivre(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment)
        S.ml_access_token = lambda *a, **k: ("token", "SELLER")

    def tearDown(self) -> None:
        S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment = self._originais
        super().tearDown()

    def importar(self, venda: str, *, status: str = "paid", shipment: dict | None = None) -> dict:
        S.ml_api_get = lambda *a, **k: {
            "id": venda, "status": status, "seller": {"id": "SELLER"},
            "buyer": {"billing_info": {"id": "B1"}},
        }
        S._ml_resolve_forward_shipment = lambda *a, **k: ("SHIP-" + venda, shipment)
        return S.ml_import_order(venda, source="teste")

    def vendas(self) -> set[str]:
        return {str(r.get("VendaMercadoLivre") or "") for r in pedidos()}

    def test_venda_cancelada_sai_da_fila(self):
        gravar_como_integracao([linha_ml("V1", "SKU-A"), linha_ml("V1", "SKU-B"),
                                linha_ml("V2", "SKU-C")], origem="API Mercado Livre")
        resultado = self.importar("V1", status="cancelled")
        self.assertEqual(resultado.get("removed_pending_rows"), 2)
        self.assertNotIn("V1", self.vendas(),
                         "REGRESSÃO: venda cancelada continuou em Pedidos pendentes")
        self.assertIn("V2", self.vendas(), "o cancelamento levou junto outra venda")

    def test_segunda_passada_nao_remove_de_novo(self):
        gravar_como_integracao([linha_ml("V1", "SKU-A")], origem="API Mercado Livre")
        self.importar("V1", status="cancelled")
        self.assertEqual(self.importar("V1", status="cancelled").get("removed_pending_rows"), 0,
                         "a linha voltou e o watcher removeu de novo (loop)")

    def test_venda_full_sai_da_fila(self):
        gravar_como_integracao([linha_ml("V3", "SKU-D"), linha_ml("V2", "SKU-C")],
                               origem="API Mercado Livre")
        self.importar("V3", shipment={"logistic_type": "fulfillment"})
        self.assertNotIn("V3", self.vendas(), "REGRESSÃO: venda Full continuou na fila")
        self.assertIn("V2", self.vendas())

    def test_pedido_incluido_pelo_admin_continua(self):
        # ADMIN > integração: linha manual protegida não é apagada pelo cancelamento.
        gravar_como_admin([linha_ml("V4", "SKU-E", ManualAdminProtegido="SIM")])
        self.importar("V4", status="cancelled")
        self.assertIn("V4", self.vendas(),
                      "o cancelamento automático apagou um pedido incluído pelo ADMIN")


if __name__ == "__main__":
    unittest.main()
