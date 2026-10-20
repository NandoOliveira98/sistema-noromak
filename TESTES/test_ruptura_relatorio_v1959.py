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


if __name__ == "__main__":
    unittest.main()
