"""V18.72 — Ruptura: venda perdida por falta de estoque nos anúncios do ML.

O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import unittest
from datetime import date, timedelta

from base import S, TesteNoromak

HOJE = date(2026, 10, 6)          # período de 30 dias: 06/09 a 05/10


def detalhe(estoque, status="active", sub=None, logistica="drop_off"):
    return {"titulo": "Peça", "preco": 50.0, "sku_ml": "SKU", "status": status,
            "sub_status": sub or [], "estoque": estoque, "categoria": "", "logistica": logistica}


class Base(TesteNoromak):

    def venda(self, item, dia, qtd=1, valor=100.0, pedido=None):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,"
                         "qty,amount,status,updated_at) VALUES('ml',?,?,?,?,?,?,?,?,?,'paid','')",
                         (pedido or f"{item}-{dia}-{qtd}-{valor}", "1", dia, dia, "S", item, "Título " + item, qtd, valor))
            S.DB.commit()

    def checar(self, item, dia, *estados):
        for k, d in enumerate(estados):
            S._ml_estoque_registrar({item: d}, dia, quando=f"{dia}T{10 + k:02d}:00:00")

    def dia(self, item, dia):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM ml_estoque_dia_v1872 WHERE item_id=? AND dia=?", (item, dia)).fetchone()
        return dict(r) if r else None


class RegistroDeEstoque(Base):

    def test_conta_as_checagens_sem_estoque_do_dia(self):
        self.checar("MLB1", "2026-10-05", detalhe(3), detalhe(0), detalhe(0), detalhe(2))
        r = self.dia("MLB1", "2026-10-05")
        self.assertEqual((r["checagens"], r["sem_estoque"]), (4, 2))
        self.assertEqual((r["estoque_min"], r["estoque_ultimo"], r["ultimo_sem_estoque"]), (0, 2, 0))

    def test_pausado_por_falta_de_estoque_conta_como_ruptura(self):
        self.checar("MLB1", "2026-10-05", detalhe(None, "paused", ["out_of_stock"]))
        self.assertEqual(self.dia("MLB1", "2026-10-05")["sem_estoque"], 1)

    def test_pausado_a_mao_com_estoque_nao_e_ruptura(self):
        self.checar("MLB1", "2026-10-05", detalhe(4, "paused"))
        r = self.dia("MLB1", "2026-10-05")
        self.assertEqual((r["checagens"], r["sem_estoque"], r["status_ultimo"]), (1, 0, "paused"))

    def test_estoque_desconhecido_nao_vira_zero(self):
        self.checar("MLB1", "2026-10-05", detalhe(None))
        self.assertIsNone(self.dia("MLB1", "2026-10-05"), "checagem sem estoque informado foi contada")


class RodadaDeEstoque(Base):

    def setUp(self):
        super().setUp()
        self._originais = (S.ml_itens_detalhe, S.ml_integration_row)
        S.ml_integration_row = lambda: {"access_token": "TESTE", "seller_id": "1"}
        self.pedidos = []

        def falso(mlbs):
            self.pedidos.append(list(mlbs))
            return {m: detalhe(0 if m == "MLBZ" else 7) for m in mlbs}
        S.ml_itens_detalhe = falso

    def tearDown(self):
        S.ml_itens_detalhe, S.ml_integration_row = self._originais
        super().tearDown()

    def test_checa_so_os_anuncios_que_venderam_nos_ultimos_60_dias(self):
        self.venda("MLBA", "2026-10-01")
        self.venda("MLBZ", "2026-08-20")
        self.venda("MLBVELHO", "2026-06-01")         # fora dos 60 dias
        r = S.ml_estoque_rodada(hoje=HOJE, pausa=0)
        self.assertEqual(sorted(sum(self.pedidos, [])), ["MLBA", "MLBZ"])
        self.assertEqual(r["checados"], 2)
        self.assertEqual(self.dia("MLBZ", "2026-10-06")["ultimo_sem_estoque"], 1)

    def test_ml_desconectado_nao_chama_a_api(self):
        S.ml_integration_row = lambda: None
        self.venda("MLBA", "2026-10-01")
        r = S.ml_estoque_rodada(hoje=HOJE, pausa=0)
        self.assertIn("pulado", r)
        self.assertEqual(self.pedidos, [])

    def test_a_rodada_diaria_das_visitas_tambem_guarda_o_estoque(self):
        # o retrato diário cobre todos os anúncios, inclusive os que nunca venderam
        S._ml_visitas_detalhes(["MLBZ", "MLBB"], "2026-10-05", None, 0)
        self.assertEqual(self.dia("MLBZ", "2026-10-05")["sem_estoque"], 1)
        self.assertEqual(self.dia("MLBB", "2026-10-05")["estoque_ultimo"], 7)


class Ruptura(Base):

    def payload(self):
        dados = S.ruptura_payload(30, hoje=HOJE)
        return dados, {i["mlb"]: i for i in dados["itens"]}

    def test_zerado_agora_mostra_quanto_perde_por_dia(self):
        # 20 un. em 60 dias de base, todos com estoque -> 1/3 un. por dia, ticket R$ 100
        for k in range(20):
            self.venda("MLB1", "2026-09-%02d" % (1 + k))
        self.checar("MLB1", "2026-10-06", detalhe(0))
        d, i = self.payload()
        self.assertEqual(i["MLB1"]["grupo"], "zerado")
        self.assertAlmostEqual(i["MLB1"]["ritmo_dia"], 20 / 60, places=4)
        self.assertAlmostEqual(i["MLB1"]["perda_dia"], round(20 / 60 * 100, 2))
        self.assertAlmostEqual(d["resumo"]["perda_dia"], i["MLB1"]["perda_dia"])

    def test_ritmo_ignora_os_dias_sem_estoque(self):
        # 10 un. em 60 dias, mas 30 desses dias sem estoque -> 10/30 por dia
        for k in range(10):
            self.venda("MLB1", "2026-08-%02d" % (10 + k))
        for k in range(30):
            self.checar("MLB1", "2026-09-%02d" % (6 + k) if k < 25 else "2026-10-%02d" % (k - 24), detalhe(0))
        self.checar("MLB1", "2026-10-06", detalhe(5))
        _, i = self.payload()
        self.assertAlmostEqual(i["MLB1"]["ritmo_dia"], 10 / 30, places=4)
        self.assertAlmostEqual(i["MLB1"]["dias_sem_estoque"], 30.0)
        # perdido no período = ritmo × dias sem estoque × ticket (estimativa)
        self.assertAlmostEqual(i["MLB1"]["perdido"], round(10 / 30 * 30 * 100, 2))

    def test_dia_meio_sem_estoque_conta_meio_dia(self):
        for k in range(6):
            self.venda("MLB1", "2026-09-%02d" % (1 + k))
        self.checar("MLB1", "2026-10-01", detalhe(2), detalhe(0))
        self.checar("MLB1", "2026-10-06", detalhe(9))
        _, i = self.payload()
        self.assertAlmostEqual(i["MLB1"]["dias_sem_estoque"], 0.5)

    def test_vai_zerar_em_poucos_dias(self):
        for item in ("MLB1", "MLB2", "MLB3"):          # 1 un. por dia nos 60 dias da base
            for k in range(60):
                self.venda(item, (date(2026, 8, 7) + timedelta(days=k)).isoformat())
        self.checar("MLB1", "2026-10-06", detalhe(5))     # 5 dias
        self.checar("MLB2", "2026-10-06", detalhe(12))    # 12 dias
        self.checar("MLB3", "2026-10-06", detalhe(40))    # 40 dias
        _, i = self.payload()
        self.assertEqual(i["MLB1"]["grupo"], "critico")
        self.assertAlmostEqual(i["MLB1"]["cobertura_dias"], 5.0, places=1)
        self.assertEqual(i["MLB2"]["grupo"], "atencao")
        self.assertEqual(i["MLB3"]["grupo"], "ok")

    def test_pausado_a_mao_que_vende_aparece_separado(self):
        for k in range(5):
            self.venda("MLB1", "2026-09-%02d" % (1 + k))
        self.checar("MLB1", "2026-10-06", detalhe(8, "paused"))
        _, i = self.payload()
        self.assertEqual(i["MLB1"]["grupo"], "pausado")
        self.assertIsNone(i["MLB1"]["perda_dia"], "pausa manual não é falta de estoque")

    def test_anuncio_sem_venda_e_encerrado_ficam_fora(self):
        self.checar("MLBSEM", "2026-10-06", detalhe(0))
        for k in range(5):
            self.venda("MLBFIM", "2026-09-%02d" % (1 + k))
        self.checar("MLBFIM", "2026-10-06", detalhe(0, "closed"))
        _, i = self.payload()
        self.assertNotIn("MLBSEM", i)
        self.assertNotIn("MLBFIM", i)

    def test_sem_checagem_de_estoque_nao_inventa_situacao(self):
        for k in range(5):
            self.venda("MLB1", "2026-09-%02d" % (1 + k))
        d, i = self.payload()
        self.assertNotIn("MLB1", i)
        self.assertEqual(d["resumo"]["vendeu_sem_estoque_coletado"], 1)

    def test_full_vem_marcado(self):
        for k in range(5):
            self.venda("MLB1", "2026-09-%02d" % (1 + k))
        self.checar("MLB1", "2026-10-06", detalhe(0, logistica="fulfillment"))
        _, i = self.payload()
        self.assertTrue(i["MLB1"]["full"])

    def test_rota_e_somente_admin_e_responde(self):
        status, corpo = self.cliente.pedir("GET", "/api/ruptura?dias=30")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["periodo"]["dias"], 30)


if __name__ == "__main__":
    unittest.main()
