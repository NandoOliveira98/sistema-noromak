"""V17.99 — Dashboard de vendas.

As vendas do ML ficam numa tabela local (vendas_ml_v1799). O ML continua sendo a
fonte: a janela recente é regravada a cada sincronização, então venda cancelada
some. Nenhuma chamada sai da máquina — a busca no ML é substituída por um falso.
"""

import unittest

from base import S, TesteNoromak


def venda(order_id, sku, qty, valor, data, status="paid", pack=""):
    return {"order_id": order_id, "pack_id": pack, "sku": sku, "product": f"Produto {sku}",
            "date": f"{data}T10:00:00-03:00", "qty": qty, "sale_amount_api": valor,
            "line_fee_api": 1.0, "status": status,
            "order_summary": {"order_items": [{"item": {"id": f"MLB{sku}", "seller_sku": sku}}]}}


class DashboardVendas(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = S._ml_profitability_recent_sales
        self.respostas = {}

        def falso(days=30, limit=2000, date_from="", date_to="", status_filter="valid"):
            return list(self.respostas.get(status_filter, []))
        S._ml_profitability_recent_sales = falso

    def tearDown(self):
        S._ml_profitability_recent_sales = self._orig
        super().tearDown()

    def linhas(self):
        with S.DB_LOCK:
            return S.DB.execute("SELECT order_id, sku, qty, amount, sale_day FROM vendas_ml_v1799 ORDER BY order_id").fetchall()

    def test_sincronizacao_regrava_a_janela_e_remove_cancelada(self):
        self.respostas = {"valid": [venda("1", "4045", 2, 60.0, "2026-09-10"), venda("2", "0893556005", 1, 45.5, "2026-09-11")]}
        S.vendas_ml_sync_window("2026-09-10", "2026-09-12")
        self.assertEqual(len(self.linhas()), 2)
        # O pedido 2 foi cancelado: a próxima busca não o traz mais.
        self.respostas = {"valid": [venda("1", "4045", 2, 60.0, "2026-09-10")]}
        S.vendas_ml_sync_window("2026-09-10", "2026-09-12")
        self.assertEqual([r["order_id"] for r in self.linhas()], ["1"], "venda cancelada continuou no dashboard")

    def test_venda_fora_da_janela_nao_e_apagada(self):
        self.respostas = {"valid": [venda("1", "4045", 1, 30.0, "2026-08-01")]}
        S.vendas_ml_sync_window("2026-08-01", "2026-08-01")
        self.respostas = {"valid": []}
        S.vendas_ml_sync_window("2026-09-01", "2026-09-05")
        self.assertEqual(len(self.linhas()), 1)

    def test_resumo_por_dia_e_mais_vendidos(self):
        self.respostas = {"valid": [
            venda("1", "4045", 2, 60.0, "2026-09-10"),
            venda("2", "4045", 1, 30.0, "2026-09-12"),
            venda("3", "0893556005", 1, 100.0, "2026-09-12"),
            venda("9", "4045", 5, 150.0, "2026-09-07"),  # período anterior
        ], "partially_refunded": [venda("4", "4018", 1, 20.0, "2026-09-11", status="partially_refunded")]}
        S.vendas_ml_sync_window("2026-09-07", "2026-09-12")
        status, corpo = self.cliente.pedir("GET", "/api/dashboard/vendas?data_inicio=2026-09-10&data_fim=2026-09-12")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["resumo"]["faturamento"], 210.0)
        self.assertEqual(corpo["resumo"]["pedidos"], 4)
        self.assertEqual(corpo["resumo"]["unidades"], 5)
        self.assertEqual(corpo["resumo"]["ticket_medio"], 52.5)
        self.assertEqual([d["dia"] for d in corpo["por_dia"]], ["2026-09-10", "2026-09-11", "2026-09-12"])
        self.assertEqual(corpo["por_dia"][1]["faturamento"], 20.0)
        self.assertEqual(corpo["top_unidades"][0]["sku"], "4045")
        self.assertEqual(corpo["top_unidades"][0]["unidades"], 3)
        self.assertEqual(corpo["top_faturamento"][0]["sku"], "0893556005")
        # Período anterior de mesmo tamanho: 07 a 09/09.
        self.assertEqual(corpo["periodo_anterior"], {"inicio": "2026-09-07", "fim": "2026-09-09"})
        self.assertEqual(corpo["anterior"]["faturamento"], 150.0)

    def test_data_invalida(self):
        status, _ = self.cliente.pedir("GET", "/api/dashboard/vendas?data_inicio=10/09/2026&data_fim=2026-09-12")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
