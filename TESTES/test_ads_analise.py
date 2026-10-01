"""V18.54 — invariantes da Análise de ADS.

A tela só serve se os números forem honestos:

  * o .xlsx do ML é lido só com a biblioteca padrão, e uma planilha errada é recusada com
    uma mensagem clara (nunca vira relatório vazio);
  * '-' do ML (métrica sem valor) é zero, e linha sem MLB não entra;
  * importar o mesmo período de novo SUBSTITUI o relatório (não duplica o Ads);
  * o lucro por anúncio usa o cálculo do Financeiro (custo, tarifa, frete, imposto) e o
    Ads vem só do relatório — o PADS da fatura não entra, senão contaria duas vezes;
  * venda sem custo deixa a margem nula, nunca estimada.
"""

from __future__ import annotations

import base64
import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from base import USUARIO_ADMIN, Cliente, S, TesteNoromak
from test_financeiro import _custo_da_venda, _custos_precificador, _frete_envio, _lancamento_fatura

CABECALHO = ["Desde", "Até", "Campanha", "Título do anúncio patrocinado", "Código do anúncio", "Status",
             "Impressões", "Cliques", "CPC \n(Custo por clique)", "CTR\n(Click Through Rate)",
             "CVR\n(Conversion rate)", "Receita\n(Moeda local)", "Investimento\n(Moeda local)",
             "ACOS\n (Investimento / Receitas)", "ROAS\n(Receitas / Investimento)", "Vendas diretas",
             "Vendas indiretas", "Vendas por publicidade\n(Diretas + Indiretas)",
             "Receita por vendas diretas\n(Moeda Local)", "Receita por vendas indiretas"]


def _linha(mlb: str, investimento: str, receita: str, *, titulo: str = "Produto", campanha: str = "Campanha",
           cliques: str = "10.0", vendas: str = "0.0", desde: str = "31-ago-2026", ate: str = "30-set-2026") -> list[str]:
    return [desde, ate, campanha, titulo, mlb, "Ativo", "1000.0", cliques, "-", "-", "-", receita, investimento,
            "-", "-", vendas, "0.0", vendas, receita, "0.0"]


def _xlsx(linhas: list[list[str]], *, com_ajuda: bool = True) -> bytes:
    """Monta um .xlsx no formato do relatório do ML (sharedStrings, 3 abas, cabeçalho na linha 2)."""
    textos: list[str] = []

    def idx(t: str) -> int:
        if t not in textos:
            textos.append(t)
        return textos.index(t)

    def folha(rows: list[list[str]]) -> str:
        out = []
        for n, row in enumerate(rows, start=1):
            cels = "".join(f'<c r="{chr(65 + i)}{n}" t="s"><v>{idx(v)}</v></c>' for i, v in enumerate(row) if v != "")
            out.append(f'<row r="{n}">{cels}</row>')
        return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                + "".join(out) + "</sheetData></worksheet>")

    abas = []
    if com_ajuda:
        abas.append(("Ajuda", folha([["", "Confira em detalhes o desempenho dos seus anúncios patrocinados"]])))
    abas.append(("Relatório Anúncios patrocinados",
                 folha([["Relatório de publicidade por anúncios patrocinados"], CABECALHO] + linhas)))
    sst = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           + "".join(f"<si><t>{escape(t)}</t></si>" for t in textos) + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, _) in enumerate(abas, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(abas) + 1))
                   + "</Relationships>")
        for i, (_n, xml) in enumerate(abas, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        z.writestr("xl/sharedStrings.xml", sst)
    return buf.getvalue()


def _vender(order_id: str, sku: str, anuncio: str, qty: int, valor: float, dia: str = "2026-09-10") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,"
            "sku,anuncio,title,qty,amount,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ml", order_id, sku, "", dia + "T10:00:00-03:00", dia, sku, anuncio, f"Produto {sku}", qty, valor,
             "paid", S.now_iso()))
        S.DB.commit()


class TesteLeituraDoRelatorio(TesteNoromak):

    def test_le_periodo_e_metricas(self) -> None:
        r = S.ads_relatorio_ler_xlsx(_xlsx([
            _linha("MLB111", "12.5", "300.0", cliques="20.0", vendas="2.0"),
            _linha("MLB222", "0.46", "-"),
            _linha("", "9.0", "9.0"),             # linha sem anúncio não entra
        ]))
        self.assertEqual((r["periodo_inicio"], r["periodo_fim"]), ("2026-08-31", "2026-09-30"))
        self.assertEqual([x["mlb"] for x in r["linhas"]], ["MLB111", "MLB222"])
        a, b = r["linhas"]
        self.assertEqual((a["investimento"], a["receita"], a["cliques"], a["vendas_diretas"]), (12.5, 300.0, 20, 2))
        self.assertEqual(b["receita"], 0.0, "'-' do ML é métrica sem valor: zero, não erro")

    def test_planilha_que_nao_e_o_relatorio_e_recusada(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>')
        with self.assertRaisesRegex(ValueError, "Anúncios patrocinados"):
            S.ads_relatorio_ler_xlsx(buf.getvalue())
        with self.assertRaisesRegex(ValueError, ".xlsx"):
            S.ads_relatorio_ler_xlsx(b"isto nao e um zip")

    def test_mesmo_periodo_substitui_e_nao_duplica_o_ads(self) -> None:
        S.ads_relatorio_importar(_xlsx([_linha("MLB111", "10.0", "100.0")]), "a.xlsx", "admin")
        r = S.ads_relatorio_importar(_xlsx([_linha("MLB111", "15.0", "150.0")]), "b.xlsx", "admin")
        self.assertTrue(r["substituiu"])
        lista = S.ads_relatorios_lista()
        self.assertEqual(len(lista), 1, "o mesmo período importado duas vezes é UM relatório")
        self.assertEqual(lista[0]["investimento"], 15.0)
        S.ads_relatorio_importar(_xlsx([_linha("MLB111", "5.0", "0.0", desde="01-out-2026", ate="31-out-2026")]))
        self.assertEqual(len(S.ads_relatorios_lista()), 2, "período diferente é outro relatório")


class TesteLucroPorAnuncio(TesteNoromak):

    def test_lucro_usa_o_financeiro_e_o_ads_so_do_relatorio(self) -> None:
        _custos_precificador({"ZK05": 45.62})
        _vender("2000015151309807", "ZK05", "MLB4242949155", 2, 157.80)
        _custo_da_venda("2000015151309807", "ZK05", "ZK05", 18.94, shipment_id="E-AGUA")
        _frete_envio("E-AGUA", 22.90)
        # PADS da fatura: o mesmo gasto do relatório. Não pode entrar de novo.
        _lancamento_fatura("2000015151309807", 3.79, sub_type="PADS", transacao="Product Ads", detail_id="pads")
        S.ads_relatorio_importar(_xlsx([_linha("MLB4242949155", "3.79", "0.0")]))
        d = S.ads_analise_payload()
        f = d["financeiro"]["MLB4242949155"]
        self.assertEqual(f["faturamento"], 157.80)
        self.assertEqual(f["cmv"], 91.24)
        self.assertEqual(f["frete"], 22.90)
        # 157,80 - 91,24 - 18,94 (tarifa) - 22,90 (frete) - 18,94 (imposto 12%) — sem Ads
        self.assertEqual(f["margem"], 5.78)
        self.assertEqual(d["linhas"][0][6], 3.79, "o Ads da tela é o do relatório")

    def test_venda_sem_custo_deixa_a_margem_nula(self) -> None:
        _vender("ML9", "SEMCUSTO", "MLB999", 1, 100.0)
        _custo_da_venda("ML9", "SEMCUSTO", "SEMCUSTO", 12.0, shipment_id="E9")
        _frete_envio("E9", 10.0)
        S.ads_relatorio_importar(_xlsx([_linha("MLB999", "5.0", "100.0", vendas="1.0")]))
        f = S.ads_analise_payload()["financeiro"]["MLB999"]
        self.assertIsNone(f["margem"], "sem custo do produto não existe margem — nada é estimado")
        self.assertEqual(f["sem_custo"], 100.0)

    def test_anuncio_fora_do_relatorio_nao_entra(self) -> None:
        _custos_precificador({"AAA": 10.0})
        _vender("ML1", "AAA", "MLB1", 1, 50.0)
        S.ads_relatorio_importar(_xlsx([_linha("MLB2", "1.0", "0.0")]))
        self.assertEqual(S.ads_analise_payload()["financeiro"], {})


class TesteRotasAnaliseAds(TesteNoromak):

    def test_importa_pela_api_e_le_de_volta(self) -> None:
        corpo = {"arquivo": "report.xlsx",
                 "conteudo_base64": base64.b64encode(_xlsx([_linha("MLB111", "10.0", "100.0")])).decode()}
        status, r = self.cliente.pedir("POST", "/api/ads-analise/importar", corpo)
        self.assertEqual(status, 200, r)
        self.assertEqual((r["linhas"], r["investimento"]), (1, 10.0))
        status, d = self.cliente.pedir("GET", "/api/ads-analise")
        self.assertEqual(status, 200, d)
        self.assertEqual(d["relatorio"]["id"], r["id"])
        self.assertEqual(d["relatorio"]["importado_por"], USUARIO_ADMIN, "quem importou fica registrado")

    def test_arquivo_errado_responde_400(self) -> None:
        corpo = {"arquivo": "x.xlsx", "conteudo_base64": base64.b64encode(b"nao e xlsx").decode()}
        status, r = self.cliente.pedir("POST", "/api/ads-analise/importar", corpo)
        self.assertEqual(status, 400)
        self.assertIn(".xlsx", r["error"])

    def test_sem_login_nao_importa_nem_le(self) -> None:
        anonimo = Cliente(self.url_base)
        status, _ = anonimo.pedir("GET", "/api/ads-analise")
        self.assertEqual(status, 401)
        status, _ = anonimo.pedir("POST", "/api/ads-analise/importar", {"conteudo_base64": ""})
        self.assertEqual(status, 401)

    def test_sem_relatorio_responde_vazio(self) -> None:
        status, d = self.cliente.pedir("GET", "/api/ads-analise")
        self.assertEqual(status, 200)
        self.assertIsNone(d["relatorio"])


if __name__ == "__main__":
    unittest.main()
