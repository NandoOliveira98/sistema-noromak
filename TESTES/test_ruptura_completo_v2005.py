"""V20.05 — Ruptura: o Excel de 6 abas e a folha A4 feitos à mão em 30/10 viram botões da tela.

Regra que nasceu do incidente de 30/10: nada aqui consulta o Apollo. O relatório usa só o
estoque guardado (Demanda de Compra + remessa Full) e o que não tem leitura fica em branco.
"""

import io
import json
import re
import unittest
import urllib.request
import zipfile
from datetime import date

from base import S
from test_ruptura import Base, detalhe

HOJE = date(2026, 10, 20)


def guardar_apollo(codigo: str, disponivel, em: str = "2026-10-19T10:00:00") -> None:
    with S.DB_LOCK:
        S._compras_geral_schema()
        S.DB.execute("INSERT INTO compras_estoque_v1912(codigo,consultado_em,resultado_json) VALUES(?,?,?)",
                     (codigo, em, json.dumps({"stock_known": disponivel is not None, "available": disponivel})))
        S.DB.commit()


def remessa(anuncio: str, codigo: str, disponivel, a_caminho: int = 0, em: str = "2026-10-19T09:00:00") -> None:
    with S.DB_LOCK:
        cur = S.DB.execute("INSERT INTO full_remessa_v1903(situacao,montada_em) VALUES('montando',?)", (em,))
        S.DB.execute("INSERT INTO full_remessa_item_v1903(remessa_id,anuncio,codigo,estoque_apollo,a_caminho) "
                     "VALUES(?,?,?,?,?)", (cur.lastrowid, anuncio, codigo, disponivel, a_caminho))
        S.DB.commit()


class Cenario(Base):
    def setUp(self):
        super().setUp()
        self._apollo = (S.crm_stock_lookup,)
        def proibido(*a, **k):
            raise AssertionError("o relatório não pode consultar o Apollo")
        S.crm_stock_lookup = proibido

    def tearDown(self):
        S.crm_stock_lookup, = self._apollo
        super().tearDown()

    def anuncio(self, item, sku, estoque, logistica="drop_off", qtd=2):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,titulo,sku) VALUES(?,?,?)",
                         (item, "Peça " + item, sku))
            S.DB.commit()
        for k in range(30):
            self.venda(item, f"2026-09-{k + 1:02d}", qtd=qtd, valor=100.0 * qtd)
        self.checar(item, "2026-10-19", detalhe(estoque, logistica=logistica))

    def item(self, r, mlb):
        return next(i for i in r["itens"] if i["mlb"] == mlb)


class Acoes(Cenario):
    def test_zerado_sem_peca_e_comprar(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "Comprar")

    def test_zerado_com_peca_e_ajustar(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 5)
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertEqual((i["acao"], i["apollo"], i["apollo_lido"]), ("Ajustar anúncio", 5, "19/10"))

    def test_zerado_no_full_com_peca_e_enviar_ao_full(self):
        self.anuncio("MLB1", "COD1", 0, logistica="fulfillment")
        guardar_apollo("COD1", 5)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "Enviar ao Full")

    def test_sem_leitura_nunca_vira_zero(self):
        self.anuncio("MLB1", "COD1", 0)
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertIsNone(i["apollo"])
        self.assertEqual(i["acao"], "Sem leitura")

    def test_apollo_sem_resposta_guardado_nao_vira_zero(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", None)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "Sem leitura")

    def test_a_caminho_troca_comprar(self):
        self.anuncio("MLB1", "COD1", 0)
        remessa("MLB1", "COD1", 0, a_caminho=12)
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertEqual((i["acao"], i["a_caminho"]), ("A caminho", 12))

    def test_leitura_mais_nova_vence(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0, em="2026-10-18T10:00:00")
        remessa("MLB1", "COD1", 9, em="2026-10-19T09:00:00")
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["apollo"], 9)

    def test_kit_e_a_peca_que_tem_menos(self):
        self.anuncio("MLB1", "AAA111/BBB222", 0)
        guardar_apollo("AAA111", 7)
        guardar_apollo("BBB222", 2)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["apollo"], 2)

    def test_kit_com_peca_sem_leitura_fica_sem_leitura(self):
        self.anuncio("MLB1", "AAA111/BBB222", 0)
        guardar_apollo("AAA111", 7)
        self.assertIsNone(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["apollo"])


class Resumo(Cenario):
    def test_grupos_somam_os_zerados(self):
        self.anuncio("MLB1", "COD1", 0)
        self.anuncio("MLB2", "COD2", 0)
        self.anuncio("MLB3", "COD3", 0)
        guardar_apollo("COD1", 0)
        guardar_apollo("COD2", 4)
        r = S.ruptura_completo_payload(HOJE)["resumo"]
        self.assertEqual((r["comprar"]["anuncios"], r["ajustar"]["anuncios"], r["sem_leitura"]["anuncios"]), (1, 1, 1))
        self.assertEqual(r["zerados"], 3)
        self.assertAlmostEqual(r["comprar"]["dia"] + r["ajustar"]["dia"] + r["sem_leitura"]["dia"], r["perda_dia"], 1)

    def test_faturamento_compara_os_mesmos_dias(self):
        self.anuncio("MLB1", "COD1", 5)
        self.venda("MLB1", "2026-10-05", qtd=1, valor=300.0)
        self.venda("MLB1", "2026-09-28", qtd=1, valor=900.0)  # depois do dia 19: fora da comparação
        r = S.ruptura_completo_payload(HOJE)
        canal = r["canais"][0]
        self.assertEqual(canal["canal"], "Mercado Livre")
        self.assertEqual(canal["atual"], 300.0)
        self.assertEqual(canal["anterior"], 3800.0)  # 19 dias de setembro a R$ 200

    def test_nada_a_informar_nao_quebra(self):
        r = S.ruptura_completo_payload(HOJE)
        self.assertEqual(r["resumo"]["zerados"], 0)
        S.ruptura_relatorio_xlsx(hoje=HOJE)
        self.assertIn("Ruptura de outubro 2026", S.ruptura_a4_html(HOJE))


class Excel(Cenario):
    def preparar(self):
        self.anuncio("MLB1", "COD1", 0)
        self.anuncio("MLB2", "COD2", 1)
        guardar_apollo("COD1", 0)
        guardar_apollo("COD2", 8)

    def test_seis_abas_na_ordem_e_as_antigas_depois(self):
        self.preparar()
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        nomes = re.findall(r'<sheet name="([^"]+)"', z.read("xl/workbook.xml").decode())
        self.assertEqual(nomes, ["Resumo", "Ações", "Perto de acabar", "Zerados", "Maiores perdas", "Marcas",
                                 "Semanal", "Mensal", "Anúncios"])

    def test_cabecalho_fixo_e_filtro(self):
        self.preparar()
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        acoes = z.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn('ySplit="4"', acoes)
        self.assertIn('<autoFilter ref="A4:', acoes)
        self.assertIn("Comprar", acoes)
        self.assertIn("MLB1", acoes)
        self.assertLess(acoes.index("<sheetViews>"), acoes.index("<cols>"))
        self.assertLess(acoes.index("</sheetData>"), acoes.index("<autoFilter"))

    def test_sem_leitura_fica_em_branco_na_planilha(self):
        self.anuncio("MLB1", "COD1", 0)
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        acoes = z.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("Sem leitura", acoes)
        self.assertNotIn("<v>0</v>", acoes)


class FolhaA4(Cenario):
    def test_folha_traz_os_blocos(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0)
        h = S.ruptura_a4_html(HOJE)
        for t in ("@page{size:A4 portrait", "Faturamento médio perdido por dia", "Comprar primeiro",
                  "Ajustar anúncio", "Próximos passos", "Limites dos números", "Peça MLB1"):
            self.assertIn(t, h)

    def test_titulo_do_anuncio_nao_vira_html(self):
        self.anuncio("MLB1", "COD1", 0)
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_visitas_item_v1868 SET titulo='<script>x</script>' WHERE item_id='MLB1'")
            S.DB.commit()
        self.assertNotIn("<script>x", S.ruptura_a4_html(HOJE))

    def test_rota_e_permissao(self):
        req = urllib.request.Request(self.cliente.url_base + "/api/ruptura/a4", headers={"Cookie": self.cliente.cookie})
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type"))

    def test_tela_tem_o_botao(self):
        html = (S.APP_DIR / "ruptura.html").read_text(encoding="utf-8")
        self.assertIn('id="relA4"', html)
        self.assertIn("/expedicao/api/ruptura/a4", html)


if __name__ == "__main__":
    unittest.main()
