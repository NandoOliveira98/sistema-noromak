"""V19.03 — Remessa Full automática. Apollo e ML simulados."""
import unittest
from base import S, TesteNoromak


class NaoAceito(TesteNoromak):
    def test_marcar_e_liberar(self):
        S.full_nao_aceito_marcar("0893600022", "inflamável", "admin")
        self.assertIn("0893600022", S.full_nao_aceito_lista())
        self.assertEqual(S.full_nao_aceito_lista()["0893600022"]["motivo"], "inflamável")
        self.assertTrue(S.full_nao_aceito_liberar("0893600022", "admin"))
        self.assertNotIn("0893600022", S.full_nao_aceito_lista())

    def test_codigo_normalizado(self):
        S.full_nao_aceito_marcar("  abc-1 ", "", "admin")
        self.assertIn("ABC-1", S.full_nao_aceito_lista())


from datetime import date, timedelta

HOJE = date(2026, 10, 11)


class Calculo(TesteNoromak):
    def anuncio(self, mlb, sku, no_full, venda, dia=HOJE):
        d = (dia - timedelta(days=1)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_estoque_dia_v1872(item_id,dia,checagens,estoque_ultimo,logistica,updated_at) "
                         "VALUES(?,?,1,?,'fulfillment','')", (mlb, d, no_full))
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,sku,titulo) VALUES(?,?,?)", (mlb, sku, "T " + mlb))
            if venda:
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES('ml',?,'1',?,?,?,?,'t',?,10,'paid','')", (f"o{mlb}", d, d, sku, mlb, venda))
            S.DB.commit()

    def setUp(self):
        super().setUp()
        self.conhecidos = {"A": {}, "B": {}, "C": {}}
        self._orig = S._compras_produtos_precificador
        S._compras_produtos_precificador = lambda: self.conhecidos

    def tearDown(self):
        S._compras_produtos_precificador = self._orig
        super().tearDown()

    def calc(self, estoque):
        return S.full_remessa_calcular(S.full_remessa_candidatos(HOJE), estoque)

    def test_necessidade_30_dias_menos_o_full(self):
        self.anuncio("MLB1", "A", no_full=10, venda=60)
        r = self.calc({"A": 500})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 50)
        self.assertEqual(r["unidades"], 50)

    def test_limitado_ao_apollo_e_mais_urgente_primeiro(self):
        self.anuncio("MLB1", "A", no_full=50, venda=60)   # 25 dias
        self.anuncio("MLB2", "A", no_full=0, venda=30)    # urgente
        r = {i["anuncio"]: i for i in self.calc({"A": 35})["itens"]}
        self.assertEqual(r["MLB2"]["qtd_sugerida"], 30)
        self.assertEqual(r["MLB1"]["qtd_sugerida"], 5)

    def test_apollo_sem_resposta_nao_vira_zero_nem_estoque(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        i = self.calc({"A": None})["itens"][0]
        self.assertIsNone(i["estoque_apollo"])
        self.assertEqual(i["qtd_sugerida"], 0)
        self.assertIn("Apollo", i["motivo"])

    def test_kit_conta_1_e_consome_componentes(self):
        self.anuncio("MLB9", "KIT2-A", no_full=0, venda=30)
        r = self.calc({"A": 20})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 10)   # 20 peças / 2 por kit
        self.assertEqual(r["unidades"], 10)

    def test_nao_aceito_sai(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        S.full_nao_aceito_marcar("A", "recusado", "admin")
        r = self.calc({"A": 100})
        self.assertEqual(r["itens"], [])
        self.assertTrue(any("não aceito" in f["motivo"] for f in r["fora"]))

    def test_anuncio_sem_codigo_fica_fora_com_motivo(self):
        self.anuncio("MLB1", "ZZZ", no_full=0, venda=30)
        r = self.calc({})
        self.assertEqual(r["itens"], [])
        self.assertEqual(r["fora"][0]["anuncio"], "MLB1")

    def test_novo_sugerido_ate_10_sem_confirmar(self):
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = self.calc({"C": 100})
        self.assertEqual(r["novos"][0]["qtd_sugerida"], 10)
        self.assertEqual(r["novos"][0]["confirmado"], 0)
        self.assertEqual(r["unidades"], 0)

if __name__ == "__main__":
    unittest.main()
