"""V20.06 — Financeiro: faturamento, pedidos e ticket dos últimos 12 meses.

Pedido do dono em 30/10. A rota do resumo passa de 60 s em produção (504), por isso os mini
gráficos têm rota própria, só com uma soma das vendas: nada de custo, Precificador ou Apollo.
"""

import unittest
from datetime import date

from base import S, TesteNoromak, RAIZ

HOJE = date(2026, 10, 30)


def vender(mp: str, pedido: str, dia: str, valor: float, linha: str = "1") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,"
            "status,updated_at) VALUES(?,?,?,?,?,'S','A','T',1,?,'paid','')",
            (mp, pedido, linha, dia, dia, valor))
        S.DB.commit()


def mes(r, chave):
    return next(m for m in r["meses"] if m["mes"] == chave)


class Evolucao(TesteNoromak):
    def test_doze_meses_em_ordem_terminando_no_atual(self):
        r = S.financeiro_evolucao_payload(hoje=HOJE)
        self.assertEqual([m["mes"] for m in r["meses"]][0], "2025-11")
        self.assertEqual([m["mes"] for m in r["meses"]][-1], "2026-10")
        self.assertEqual(len(r["meses"]), 12)

    def test_virada_de_ano(self):
        r = S.financeiro_evolucao_payload(hoje=date(2027, 1, 1))
        self.assertEqual((r["meses"][0]["mes"], r["meses"][-1]["mes"]), ("2026-02", "2027-01"))

    def test_soma_e_ticket(self):
        vender("ml", "P1", "2026-09-10", 100.0)
        vender("ml", "P1", "2026-09-10", 50.0, linha="2")
        vender("shopee", "P2", "2026-09-11", 150.0)
        m = mes(S.financeiro_evolucao_payload(hoje=HOJE), "2026-09")
        self.assertEqual((m["faturamento"], m["pedidos"], m["ticket"]), (300.0, 2, 150.0))

    def test_mesmo_numero_de_pedido_em_canais_diferentes_conta_dois(self):
        vender("ml", "P1", "2026-09-10", 100.0)
        vender("shopee", "P1", "2026-09-10", 100.0)
        self.assertEqual(mes(S.financeiro_evolucao_payload(hoje=HOJE), "2026-09")["pedidos"], 2)

    def test_filtro_de_canal(self):
        vender("ml", "P1", "2026-09-10", 100.0)
        vender("shopee", "P2", "2026-09-11", 40.0)
        self.assertEqual(mes(S.financeiro_evolucao_payload("shopee", HOJE), "2026-09")["faturamento"], 40.0)

    def test_mes_sem_venda_fica_em_branco_nunca_zero(self):
        m = mes(S.financeiro_evolucao_payload(hoje=HOJE), "2026-03")
        self.assertEqual((m["faturamento"], m["pedidos"], m["ticket"]), (None, None, None))

    def test_mes_atual_e_parcial_e_fora_da_janela_nao_entra(self):
        vender("ml", "P1", "2025-10-31", 999.0)
        r = S.financeiro_evolucao_payload(hoje=HOJE)
        self.assertTrue(r["meses"][-1]["parcial"])
        self.assertFalse(r["meses"][-2]["parcial"])
        self.assertIsNone(r["meses"][0]["faturamento"])

    def test_mesmo_total_do_resumo(self):
        vender("ml", "P1", "2026-09-10", 100.0)
        vender("shopee", "P2", "2026-09-12", 33.33)
        resumo = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        m = mes(S.financeiro_evolucao_payload(hoje=HOJE), "2026-09")
        self.assertEqual((m["faturamento"], m["pedidos"]), (resumo["faturamento"], resumo["pedidos"]))

    def test_nao_le_precificador_nem_apollo(self):
        def proibido(*a, **k):
            raise AssertionError("a rota dos mini gráficos tem de ser leve")
        guardados = S._compras_produtos_precificador, S.crm_stock_lookup, S.get_storage
        S._compras_produtos_precificador = S.crm_stock_lookup = S.get_storage = proibido
        try:
            S.financeiro_evolucao_payload(hoje=HOJE)
        finally:
            S._compras_produtos_precificador, S.crm_stock_lookup, S.get_storage = guardados

    def test_rota(self):
        status, r = self.cliente.pedir("GET", "/api/financeiro/evolucao?marketplace=ml")
        self.assertEqual(status, 200)
        self.assertEqual(len(r["meses"]), 12)

    def test_rota_exige_login(self):
        from base import Cliente
        status, _ = Cliente(self.url_base).pedir("GET", "/api/financeiro/evolucao")
        self.assertEqual(status, 401)

    def test_falha_na_rota_volta_json(self):
        original = S.financeiro_evolucao_payload
        def quebra(*a, **k):
            raise RuntimeError("banco ocupado")
        S.financeiro_evolucao_payload = quebra
        try:
            status, r = self.cliente.pedir("GET", "/api/financeiro/evolucao")
        finally:
            S.financeiro_evolucao_payload = original
        self.assertEqual((status, r["ok"]), (500, False))

    def test_tela_nao_depende_do_resumo(self):
        h = (RAIZ / "APP" / "financeiro.html").read_text(encoding="utf-8")
        self.assertIn("/api/financeiro/evolucao", h)
        self.assertIn('id="evoFat"', h)
        self.assertIn('$("marketplace").addEventListener("change", carregarEvolucao)', h)
        self.assertIn('$("atualizar").addEventListener("click", carregarEvolucao)', h)
        self.assertIn('if (!r) { voltar(); return; }', h)
        self.assertIn('erro de uma resposta antiga', h)


if __name__ == "__main__":
    unittest.main()
