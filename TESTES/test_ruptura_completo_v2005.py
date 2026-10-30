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

from base import S, SENHA_ADMIN, Cliente
from test_ruptura import Base, detalhe

HOJE = date(2026, 10, 20)


def guardar_apollo(codigo: str, disponivel, em: str = "2026-10-19T10:00:00") -> None:
    with S.DB_LOCK:
        S._compras_geral_schema()
        S.DB.execute("INSERT INTO compras_estoque_v1912(codigo,consultado_em,resultado_json) VALUES(?,?,?)",
                     (codigo, em, json.dumps({"stock_known": disponivel is not None, "available": disponivel})))
        S.DB.commit()


def remessa(anuncio: str, codigo: str, situacao: str = "agendada", qtd: int = 12, a_caminho_ml=None,
            estoque_apollo=None, em: str = "2026-10-19T09:00:00") -> None:
    with S.DB_LOCK:
        cur = S.DB.execute("INSERT INTO full_remessa_v1903(situacao,montada_em) VALUES(?,?)", (situacao, em))
        S.DB.execute("INSERT INTO full_remessa_item_v1903(remessa_id,anuncio,codigo,estoque_apollo,qtd_sugerida,a_caminho_ml) "
                     "VALUES(?,?,?,?,?,?)", (cur.lastrowid, anuncio, codigo, estoque_apollo, qtd, a_caminho_ml))
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
        guardar_apollo("COD1", 0)
        remessa("MLB1", "COD1", qtd=12)
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertEqual((i["acao"], i["a_caminho"]), ("A caminho", 12))

    def test_remessa_recebida_nao_e_a_caminho(self):
        """Antes o item saía de Comprar para sempre, sem nada vindo."""
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0)
        remessa("MLB1", "COD1", situacao="recebida", qtd=48)
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertEqual((i["acao"], i["a_caminho"]), ("Comprar", None))

    def test_a_caminho_do_ml_na_remessa_aberta(self):
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0)
        remessa("MLB1", "COD1", situacao="pronta", qtd=5, a_caminho_ml=30)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["a_caminho"], 30)

    def test_full_zerado_com_a_caminho_nao_pede_envio(self):
        self.anuncio("MLB1", "COD1", 0, logistica="fulfillment")
        guardar_apollo("COD1", 3)
        remessa("MLB1", "COD1", qtd=48)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "A caminho")

    def test_critico_no_full_com_a_caminho_nao_pede_envio(self):
        self.anuncio("MLB1", "COD1", 1, logistica="fulfillment")
        guardar_apollo("COD1", 100)
        remessa("MLB1", "COD1", qtd=40)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "A caminho")

    def test_a_caminho_que_cobre_a_demanda_vem_do_fora(self):
        """Caso da NF 57.238: 48 em transfer cobrem os 30 dias, o item vai para fora da remessa."""
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 0)
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO full_remessa_v1903(situacao,montada_em,fora_json) VALUES('pronta','x',?)",
                         (json.dumps([{"anuncio": "MLB1", "a_caminho": 48, "motivo": "Full + a caminho cobrem 30 dias"}]),))
            S.DB.commit()
        i = self.item(S.ruptura_completo_payload(HOJE), "MLB1")
        self.assertEqual((i["acao"], i["a_caminho"]), ("A caminho", 48))

    def test_o_calculo_guarda_o_a_caminho_no_fora(self):
        cand = {"anuncios": [{"anuncio": "MLB1", "codigo": "C", "venda_30d": 30, "no_full": 0, "a_caminho": 48,
                              "componentes": [{"codigo": "C", "qtd": 1}]}]}
        r = S.full_remessa_calcular(cand, {"C": 5})
        self.assertEqual(r["fora"][0]["a_caminho"], 48)

    def test_saldo_da_remessa_nao_vale_como_estoque(self):
        """A remessa grava o saldo depois dos anúncios anteriores: 2 em vez dos 10 reais."""
        self.anuncio("MLB1", "COD1", 0)
        guardar_apollo("COD1", 10, em="2026-10-18T10:00:00")
        remessa("MLB1", "COD1", situacao="montando", estoque_apollo=2, em="2026-10-19T09:00:00")
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["apollo"], 10)

    def test_so_a_remessa_sem_demanda_fica_sem_leitura(self):
        self.anuncio("MLB1", "COD1", 0)
        remessa("MLB1", "COD1", situacao="montando", estoque_apollo=5)
        self.assertEqual(self.item(S.ruptura_completo_payload(HOJE), "MLB1")["acao"], "Sem leitura")

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


class UmPorVez(Cenario):
    def test_cliques_juntos_fazem_uma_conta_so(self):
        import threading, time
        self.anuncio("MLB1", "COD1", 0)
        original = S._ruptura_completo_calcular
        contas = []
        def lento(hoje):
            contas.append(1)
            time.sleep(0.2)
            return original(hoje)
        S._ruptura_completo_calcular = lento
        try:
            ts = [threading.Thread(target=S.ruptura_completo_payload) for _ in range(4)]
            [x.start() for x in ts]
            [x.join() for x in ts]
        finally:
            S._ruptura_completo_calcular = original
        self.assertEqual(len(contas), 1)

    def test_falha_no_cadastro_avisa_e_nao_fica_guardada(self):
        self.anuncio("MLB1", "COD1", 0)
        original = S._compras_produtos_precificador
        def quebra():
            raise RuntimeError("ilegível")
        S._compras_produtos_precificador = quebra
        try:
            self.assertIn("Cadastro não lido", S.ruptura_a4_html())
        finally:
            S._compras_produtos_precificador = original
        self.assertEqual(S._RUPTURA_COMPLETO_CACHE, {})


class FiltroDoExcel(Cenario):
    def test_nome_interno_do_filtro(self):
        self.anuncio("MLB1", "COD1", 0)
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('_xlnm._FilterDatabase" localSheetId="1"', wb)
        self.assertLess(wb.index("</sheets>"), wb.index("<definedNames>"))

    def test_lista_vazia_sem_filtro(self):
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        self.assertNotIn("<autoFilter", z.read("xl/worksheets/sheet2.xml").decode())


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

    def test_compras_nao_ve_o_faturamento(self):
        self.anuncio("MLB1", "COD1", 0)
        status, r = self.cliente.pedir("POST", "/api/roles", {
            "username": "carla", "display_name": "Carla", "role": "compras", "active": True, "password": SENHA_ADMIN})
        self.assertEqual(status, 200, r)
        compras = Cliente(self.url_base)
        compras.entrar("carla", SENHA_ADMIN, area="portal")
        req = urllib.request.Request(self.url_base + "/api/ruptura/a4", headers={"Cookie": compras.cookie})
        with urllib.request.urlopen(req, timeout=30) as resp:
            h = resp.read().decode("utf-8")
        for proibido in ("Faturamento por canal", "Ticket médio", "do faturado", "Faturamento 01"):
            self.assertNotIn(proibido, h)
        self.assertIn("Comprar", h)
        self.assertIn("Faturamento por canal", S.ruptura_a4_html(HOJE))

    def test_excel_do_compras_sem_percentual_do_faturado(self):
        self.anuncio("MLB1", "COD1", 0)
        marcas = lambda **k: zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE, **k))).read("xl/worksheets/sheet6.xml").decode("utf-8")
        self.assertIn("% do faturado", marcas())
        self.assertNotIn("% do faturado", marcas(sem_faturamento=True))

    def test_texto_diz_a_fonte_certa(self):
        self.assertNotIn("remessa Full), sem consulta", S.ruptura_a4_html(HOJE))

    def test_tela_tem_o_botao(self):
        html = (S.APP_DIR / "ruptura.html").read_text(encoding="utf-8")
        self.assertIn('id="relA4"', html)
        self.assertIn("/expedicao/api/ruptura/a4", html)


if __name__ == "__main__":
    unittest.main()
