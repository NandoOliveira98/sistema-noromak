"""V17.99 — Dashboard de vendas (Mercado Livre e Shopee).

As vendas ficam numa tabela local (vendas_v1799). O marketplace continua sendo a
fonte: a janela recente é regravada a cada sincronização, então venda cancelada
some. Nenhuma chamada sai da máquina — as APIs são substituídas por falsos.
"""

import unittest
from datetime import datetime

from base import S, TesteNoromak


def venda(order_id, sku, qty, valor, data, status="paid", pack=""):
    return {"order_id": order_id, "pack_id": pack, "sku": sku, "product": f"Produto {sku}",
            "date": f"{data}T10:00:00-03:00", "qty": qty, "sale_amount_api": valor,
            "line_fee_api": 1.0, "status": status,
            "order_summary": {"order_items": [{"item": {"id": f"MLB{sku}", "seller_sku": sku}}]}}


def ts(data, hora=10):
    return int(datetime.strptime(f"{data} {hora}", "%Y-%m-%d %H").replace(tzinfo=S.BR_TZ).timestamp())


def pedido_shopee(sn, status, data, itens):
    return {"order_sn": sn, "order_status": status, "create_time": ts(data), "item_list": [
        {"item_id": 111, "model_id": i, "item_sku": sku, "model_sku": "", "item_name": f"Shopee {sku}",
         "model_name": "", "model_quantity_purchased": q, "model_discounted_price": preco, "model_original_price": preco * 1.2}
        for i, (sku, q, preco) in enumerate(itens)]}


class DashboardVendas(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S._ml_profitability_recent_sales, S.shopee_api_get, S.shopee_order_details)
        self.ml = {}
        self.shopee = []

        def ml_falso(days=30, limit=2000, date_from="", date_to="", status_filter="valid"):
            # Igual ao ML real: /orders/search recusa order.status=partially_refunded.
            if status_filter == "partially_refunded":
                raise RuntimeError("Mercado Livre HTTP 400: Invalid filters: [partially_refunded]")
            todas = [v for lista in self.ml.values() for v in lista]
            if status_filter == "all":
                return todas
            return [v for v in todas if v["status"] == "paid"]

        def lista_falsa(path, params=None, retry_auth=True):
            assert path == "/api/v2/order/get_order_list", path
            ini, fim = params["time_from"], params["time_to"]
            return {"response": {"more": False, "order_list": [
                {"order_sn": o["order_sn"], "order_status": o["order_status"]}
                for o in self.shopee if ini <= o["create_time"] <= fim]}}

        def detalhe_falso(sns):
            return [o for o in self.shopee if o["order_sn"] in sns]

        S._ml_profitability_recent_sales = ml_falso
        S.shopee_api_get = lista_falsa
        S.shopee_order_details = detalhe_falso

    def tearDown(self):
        S._ml_profitability_recent_sales, S.shopee_api_get, S.shopee_order_details = self._orig
        super().tearDown()

    def linhas(self, marketplace=None):
        with S.DB_LOCK:
            sql = "SELECT marketplace, order_id, sku, qty, amount, sale_day FROM vendas_v1799"
            if marketplace:
                return S.DB.execute(sql + " WHERE marketplace=? ORDER BY order_id", (marketplace,)).fetchall()
            return S.DB.execute(sql + " ORDER BY marketplace, order_id").fetchall()

    def test_ml_regrava_a_janela_e_remove_cancelada(self):
        self.ml = {"valid": [venda("1", "4045", 2, 60.0, "2026-09-10"), venda("2", "0893556005", 1, 45.5, "2026-09-11")]}
        S.vendas_ml_sync_window("2026-09-10", "2026-09-12")
        self.assertEqual(len(self.linhas("ml")), 2)
        self.ml = {"valid": [venda("1", "4045", 2, 60.0, "2026-09-10")]}
        S.vendas_ml_sync_window("2026-09-10", "2026-09-12")
        self.assertEqual([r["order_id"] for r in self.linhas("ml")], ["1"], "venda cancelada continuou no dashboard")

    def test_ml_nao_usa_filtro_que_o_ml_recusa_e_ignora_cancelada(self):
        self.ml = {"x": [venda("1", "4045", 1, 30.0, "2026-09-10"),
                         venda("2", "4045", 1, 25.0, "2026-09-10", status="partially_refunded"),
                         venda("3", "4045", 9, 270.0, "2026-09-10", status="cancelled")]}
        S.vendas_ml_sync_window("2026-09-10", "2026-09-10")
        self.assertEqual([r["order_id"] for r in self.linhas("ml")], ["1", "2"])

    def test_venda_fora_da_janela_nao_e_apagada(self):
        self.ml = {"valid": [venda("1", "4045", 1, 30.0, "2026-08-01")]}
        S.vendas_ml_sync_window("2026-08-01", "2026-08-01")
        self.ml = {"valid": []}
        S.vendas_ml_sync_window("2026-09-01", "2026-09-05")
        self.assertEqual(len(self.linhas()), 1)

    def test_shopee_valor_e_preco_com_desconto_vezes_quantidade(self):
        self.shopee = [
            pedido_shopee("SH1", "COMPLETED", "2026-09-10", [("4045", 2, 35.0), ("4018", 1, 20.0)]),
            pedido_shopee("SH2", "CANCELLED", "2026-09-10", [("4045", 5, 35.0)]),
            pedido_shopee("SH3", "UNPAID", "2026-09-11", [("4045", 1, 35.0)]),
        ]
        S.vendas_shopee_sync_window("2026-09-10", "2026-09-12")
        linhas = self.linhas("shopee")
        self.assertEqual([(r["order_id"], r["sku"], r["amount"]) for r in linhas],
                         [("SH1", "4045", 70.0), ("SH1", "4018", 20.0)])
        self.assertEqual(linhas[0]["sale_day"], "2026-09-10")

    def test_shopee_cancelada_depois_some(self):
        self.shopee = [pedido_shopee("SH1", "READY_TO_SHIP", "2026-09-10", [("4045", 1, 35.0)])]
        S.vendas_shopee_sync_window("2026-09-10", "2026-09-10")
        self.shopee[0]["order_status"] = "CANCELLED"
        S.vendas_shopee_sync_window("2026-09-10", "2026-09-10")
        self.assertEqual(self.linhas("shopee"), [])

    def test_shopee_nao_aceita_janela_maior_que_15_dias(self):
        with self.assertRaises(ValueError):
            S.vendas_shopee_sync_window("2026-09-01", "2026-09-20")
        from datetime import date
        janelas = S._vendas_sync_intervalo("shopee", date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual([(j["de"], j["ate"]) for j in janelas],
                         [("2026-08-01", "2026-08-15"), ("2026-08-16", "2026-08-30"), ("2026-08-31", "2026-08-31")])

    def test_resumo_soma_os_dois_marketplaces(self):
        self.ml = {"valid": [
            venda("1", "4045", 2, 60.0, "2026-09-10"),
            venda("2", "4045", 1, 30.0, "2026-09-12"),
            venda("3", "0893556005", 1, 100.0, "2026-09-12"),
            venda("9", "4045", 5, 150.0, "2026-09-07"),  # período anterior
        ], "partially_refunded": [venda("4", "4018", 1, 20.0, "2026-09-11", status="partially_refunded")]}
        S.vendas_ml_sync_window("2026-09-07", "2026-09-12")
        self.shopee = [pedido_shopee("SH1", "COMPLETED", "2026-09-11", [("4045", 3, 30.0)])]
        S.vendas_shopee_sync_window("2026-09-07", "2026-09-12")

        status, corpo = self.cliente.pedir("GET", "/api/dashboard/vendas?data_inicio=2026-09-10&data_fim=2026-09-12")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["resumo"]["faturamento"], 300.0)
        self.assertEqual(corpo["resumo"]["pedidos"], 5)
        self.assertEqual(corpo["resumo"]["unidades"], 8)
        self.assertEqual(corpo["resumo"]["ticket_medio"], 60.0)
        canais = {c["marketplace"]: c for c in corpo["por_marketplace"]}
        self.assertEqual(canais["ml"]["faturamento"], 210.0)
        self.assertEqual(canais["shopee"]["faturamento"], 90.0)
        self.assertEqual([d["dia"] for d in corpo["por_dia"]], ["2026-09-10", "2026-09-11", "2026-09-12"])
        self.assertEqual(corpo["por_dia"][1]["faturamento"], 110.0)
        self.assertEqual(corpo["por_dia"][1]["por_marketplace"]["shopee"]["faturamento"], 90.0)
        top = corpo["top_unidades"][0]
        self.assertEqual((top["sku"], top["unidades"]), ("4045", 6))
        self.assertEqual(top["por_marketplace"], {"ml": 3, "shopee": 3})
        self.assertEqual(corpo["periodo_anterior"], {"inicio": "2026-09-07", "fim": "2026-09-09"})
        self.assertEqual(corpo["anterior"]["faturamento"], 150.0)
        # Venda com reembolso parcial conta (valor já líquido do reembolso).
        self.assertEqual(corpo["por_dia"][1]["por_marketplace"]["ml"]["faturamento"], 20.0)

        status, so_shopee = self.cliente.pedir("GET", "/api/dashboard/vendas?data_inicio=2026-09-10&data_fim=2026-09-12&marketplace=shopee")
        self.assertEqual(so_shopee["resumo"]["faturamento"], 90.0)
        self.assertEqual(so_shopee["anterior"]["faturamento"], 0.0)

    def test_data_invalida(self):
        status, _ = self.cliente.pedir("GET", "/api/dashboard/vendas?data_inicio=10/09/2026&data_fim=2026-09-12")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
