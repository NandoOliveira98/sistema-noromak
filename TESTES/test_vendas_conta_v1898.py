"""V18.98 — vendas por conta do Mercado Livre no Dashboard (Todas / Araçatuba / Bauru).

Decisão do dono (10/10): filtro pela conta que vendeu, e por enquanto SÓ no Dashboard — as
outras telas que leem vendas_v1799 (Financeiro, Demanda de Compra, relatório...) continuam
só com Araçatuba. O que não pode quebrar: a sincronização de uma conta nunca apaga as vendas
da outra, e a venda de Bauru não entra no custo de venda (Financeiro) de Araçatuba.
"""

from datetime import datetime, timedelta
from unittest import mock

from base import S, TesteNoromak

HOJE = datetime.now(S.BR_TZ).date()


def venda(order_id: str, valor: float, dia=HOJE) -> dict:
    return {"order_id": order_id, "date": f"{dia.isoformat()}T10:00:00-03:00", "sku": "SKU-" + order_id, "qty": 1,
            "sale_amount_api": valor, "status": "paid", "pack_id": "", "product": "Produto " + order_id,
            "line_fee_api": 1.0, "shipment_id": "S" + order_id, "order_summary": {}}


class VendasPorConta(TesteNoromak):

    def sincronizar(self, casa: str, vendas: list[dict]) -> None:
        with mock.patch.object(S, "_ml_profitability_recent_sales", return_value=vendas), S.ml_na_casa(casa):
            S.vendas_ml_sync_window(HOJE.isoformat(), HOJE.isoformat())

    def test_cada_conta_na_sua_tabela_e_uma_nao_apaga_a_outra(self):
        self.sincronizar("aracatuba", [venda("A1", 100.0)])
        self.sincronizar("bauru", [venda("B1", 40.0)])
        self.sincronizar("aracatuba", [venda("A1", 100.0)])  # janela completa de Araçatuba de novo
        with S.DB_LOCK:
            ara = [r[0] for r in S.DB.execute("SELECT order_id FROM vendas_v1799")]
            bau = [r[0] for r in S.DB.execute("SELECT order_id FROM vendas_conta_v1898 WHERE conta='bauru'")]
            custo = [r[0] for r in S.DB.execute("SELECT order_id FROM ml_venda_custo_v1830")]
        self.assertEqual(ara, ["A1"], "venda de Bauru entrou nas telas que são só de Araçatuba")
        self.assertEqual(bau, ["B1"], "a sincronização de Araçatuba apagou as vendas de Bauru")
        self.assertEqual(custo, ["A1"], "a venda de Bauru entrou no custo de venda do Financeiro")

    def test_dashboard_filtra_pela_conta(self):
        self.sincronizar("aracatuba", [venda("A1", 100.0), venda("A2", 50.0)])
        self.sincronizar("bauru", [venda("B1", 40.0)])
        dia = HOJE.isoformat()
        with S.DB_LOCK:
            todas = S.vendas_dashboard_payload(dia, dia, "", "todas")
            ara = S.vendas_dashboard_payload(dia, dia, "", "aracatuba")
            bau = S.vendas_dashboard_payload(dia, dia, "", "bauru")
            ml_bau = S.vendas_dashboard_payload(dia, dia, "shopee", "bauru")
        self.assertEqual((todas["resumo"]["faturamento"], todas["resumo"]["pedidos"]), (190.0, 3))
        self.assertEqual(ara["resumo"]["faturamento"], 150.0)
        self.assertEqual(bau["resumo"]["faturamento"], 40.0)
        self.assertEqual(ml_bau["resumo"]["faturamento"], 0.0, "Shopee é de Araçatuba")
        self.assertEqual({c["conta"]: c["faturamento"] for c in todas["por_conta"]}, {"aracatuba": 150.0, "bauru": 40.0})

    def test_rota_aceita_a_conta(self):
        self.sincronizar("bauru", [venda("B1", 40.0)])
        dia = HOJE.isoformat()
        status, corpo = self.cliente.pedir("GET", f"/api/dashboard/vendas?data_inicio={dia}&data_fim={dia}&conta=bauru")
        self.assertEqual(status, 200, corpo)
        self.assertEqual((corpo["conta"], corpo["resumo"]["faturamento"]), ("bauru", 40.0))
        status, corpo = self.cliente.pedir("GET", f"/api/dashboard/vendas?data_inicio={dia}&data_fim={dia}&conta=xpto")
        self.assertEqual(corpo["conta"], "todas")

    def test_marcas_de_sincronizacao_por_conta(self):
        with S.ml_na_casa("bauru"):
            self.assertEqual(S._vendas_chave("ml"), "ml@bauru")
            self.assertEqual(S._vendas_chave("shopee"), "shopee")
        self.assertEqual(S._vendas_chave("ml"), "ml")

    def test_tela_tem_o_filtro(self):
        from base import RAIZ
        admin = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('data-v1799-conta="bauru"', admin)
        self.assertIn("&conta=${encodeURIComponent(v1799.conta)}", admin)
