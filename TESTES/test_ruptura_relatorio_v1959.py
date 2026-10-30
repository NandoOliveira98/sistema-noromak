"""V19.59 — Ruptura: relatório semanal e mensal da venda perdida por estoque zerado."""

import unittest
from datetime import date

from base import S
from test_ruptura import Base, detalhe

HOJE = date(2026, 10, 20)   # terça: semana atual começou 19/10; última fechada 12/10–18/10


class RelatorioRuptura(Base):

    def preparar(self):
        # 60 un. em 60 dias com estoque o tempo todo, menos 2 dias zerado na semana 12–18/10
        for k in range(30):
            self.venda("MLB1", f"2026-09-{k + 1:02d}", qtd=2, valor=200.0)
        self.checar("MLB1", "2026-10-13", detalhe(0))
        self.checar("MLB1", "2026-10-14", detalhe(0))
        self.checar("MLB1", "2026-10-15", detalhe(5))

    def periodo(self, lista, inicio):
        return next(p for p in lista if p["inicio"] == inicio)

    def test_semana_fechada_mostra_o_perdido(self):
        self.preparar()
        r = S.ruptura_relatorio_payload(hoje=HOJE)
        s = self.periodo(r["semanas"], "2026-10-12")
        self.assertEqual((s["fim"], s["parcial"]), ("2026-10-18", False))
        # ritmo = 60 un / (60 - 2) dias; 2 dias sem estoque; ticket R$ 100
        self.assertAlmostEqual(s["unidades_perdidas"], 2 * 60 / 58, places=1)
        self.assertAlmostEqual(s["perdido"], 2 * 60 / 58 * 100, delta=1)
        self.assertEqual(s["anuncios"], 1)
        self.assertEqual(s["top"][0]["mlb"], "MLB1")

    def test_semana_e_mes_em_andamento_sao_parciais(self):
        self.preparar()
        r = S.ruptura_relatorio_payload(hoje=HOJE)
        self.assertTrue(r["semanas"][0]["parcial"])
        self.assertEqual(r["semanas"][0]["inicio"], "2026-10-19")
        m = r["meses"][0]
        self.assertEqual((m["inicio"], m["fim"], m["parcial"]), ("2026-10-01", "2026-10-19", True))
        self.assertGreater(m["perdido"], 0)
        self.assertEqual(self.periodo(r["meses"], "2026-09-01")["perdido"], 0)

    def test_periodo_antes_da_coleta_fica_marcado(self):
        self.preparar()
        r = S.ruptura_relatorio_payload(hoje=HOJE)
        set_ = self.periodo(r["meses"], "2026-09-01")
        self.assertFalse(set_["coleta_completa"])

    def test_encerrado_fica_fora(self):
        self.preparar()
        self.checar("MLB1", "2026-10-19", detalhe(0, "closed"))
        r = S.ruptura_relatorio_payload(hoje=HOJE)
        self.assertEqual(self.periodo(r["semanas"], "2026-10-12")["perdido"], 0)

    def test_rota(self):
        self.preparar()
        st, d = self.cliente.pedir("GET", "/api/ruptura/relatorio?marketplace=ml")
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])


    def test_v1961_completo_traz_todos_os_anuncios(self):
        self.preparar()
        st, d = self.cliente.pedir("GET", "/api/ruptura/relatorio?marketplace=ml&completo=1")
        self.assertEqual(st, 200)
        s = next(p for p in d["semanas"] if p["perdido"] > 0)
        self.assertEqual([i["mlb"] for i in s["itens"]], ["MLB1"])
        st, d = self.cliente.pedir("GET", "/api/ruptura/relatorio?marketplace=ml")
        self.assertNotIn("itens", d["semanas"][0])

    def test_excel(self):
        import io, zipfile
        self.preparar()
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE)))
        wb = z.read("xl/workbook.xml").decode()
        for aba in ("Semanal", "Mensal", "Anúncios"):
            self.assertIn(aba, wb)
        self.assertIn("MLB1", z.read("xl/worksheets/sheet3.xml").decode())
        import urllib.request
        req = urllib.request.Request(self.cliente.url_base + "/api/ruptura/relatorio.xlsx?marketplace=ml",
                                     headers={"Cookie": self.cliente.cookie})
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("spreadsheetml", resp.headers.get("Content-Type"))
            self.assertTrue(resp.read()[:2] == b"PK")

    def test_v2003_excel_do_periodo_traz_os_anuncios_na_primeira_aba(self):
        import io, zipfile
        self.preparar()
        self.venda("MLB2", "2026-09-10", qtd=2, valor=200.0)
        self.checar("MLB2", "2026-09-20", detalhe(0))    # só em setembro: não entra em outubro
        z = zipfile.ZipFile(io.BytesIO(S.ruptura_relatorio_xlsx(hoje=HOJE, tipo="meses", inicio="2026-10-01")))
        self.assertEqual([n for n in z.namelist() if n.startswith("xl/worksheets/")], ["xl/worksheets/sheet1.xml"])
        aba = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("MLB1", aba)
        self.assertNotIn("MLB2", aba)
        for coluna in ("SKU", "Dias sem estoque", "Unidades perdidas", "Perdido (R$)", "Link"):
            self.assertIn(coluna, aba)

    def test_v2003_excel_do_periodo_inexistente_e_recusado(self):
        self.preparar()
        with self.assertRaises(ValueError):
            S.ruptura_relatorio_xlsx(hoje=HOJE, tipo="meses", inicio="2020-01-01")
        with self.assertRaises(ValueError):
            S.ruptura_relatorio_xlsx(hoje=HOJE, tipo="anos", inicio="2026-10-01")

    def test_v2003_rota_do_excel_do_periodo(self):
        import urllib.error, urllib.request
        self.preparar()
        base = self.cliente.url_base + "/api/ruptura/relatorio.xlsx?marketplace=ml&tipo=meses&inicio="
        mes = (S.datetime.now(S.BR_TZ).date() - S.timedelta(days=1)).replace(day=1).isoformat()
        req = urllib.request.Request(base + mes, headers={"Cookie": self.cliente.cookie})
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.assertTrue(resp.read()[:2] == b"PK")
            self.assertIn(f"ruptura_ml_meses_{mes}", resp.headers.get("Content-Disposition"))
        req = urllib.request.Request(base + "2020-01-01", headers={"Cookie": self.cliente.cookie})
        with self.assertRaises(urllib.error.HTTPError) as erro:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(erro.exception.code, 400)

    def test_v2003_gaveta_baixa_o_periodo_aberto(self):
        html = (S.APP_DIR / "ruptura.html").read_text(encoding="utf-8")
        # marketplace e tipo guardados ao abrir: trocar com a gaveta aberta não muda o arquivo
        self.assertIn("const mp=mkt(), modo=relModo;", html)
        self.assertIn("marketplace='+mp+'&tipo='+modo+'&inicio='+p.inicio", html)

    def test_v2003_erro_no_excel_da_gaveta_avisa_sem_sair_da_tela(self):
        html = (S.APP_DIR / "ruptura.html").read_text(encoding="utf-8")
        trecho = html[html.index("$('perXlsx').onclick"):html.index("async function carregarRel")]
        self.assertNotIn("location.href", trecho)
        self.assertIn("nkToast", trecho)

    def test_v2003_sku_das_vendas_quando_o_retrato_nao_tem(self):
        self.preparar()
        m = S.ruptura_relatorio_payload(hoje=HOJE, completo=True)["meses"][0]
        self.assertEqual(m["itens"][0]["sku"], "S")
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_visitas_item_v1868(item_id,titulo,sku) VALUES('MLB1','T','DO-RETRATO')")
            S.DB.commit()
        m = S.ruptura_relatorio_payload(hoje=HOJE, completo=True)["meses"][0]
        self.assertEqual(m["itens"][0]["sku"], "DO-RETRATO")


if __name__ == "__main__":
    unittest.main()
