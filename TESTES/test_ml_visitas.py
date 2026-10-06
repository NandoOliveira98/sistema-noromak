"""V18.68 — coleta diária das visitas dos anúncios do ML (base da conversão).

O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import unittest
from datetime import date

from base import S, TesteNoromak

HOJE = date(2026, 10, 6)


def resposta(item_id, dias):
    return {"item_id": item_id, "unit": "day", "last": 30,
            "results": [{"date": f"{d}T00:00:00Z", "total": v} for d, v in dias.items()]}


class VisitasML(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_todos_os_anuncios, S.ml_integration_row)
        S.ml_integration_row = lambda: {"access_token": "TESTE", "seller_id": "1"}
        S.ml_todos_os_anuncios = lambda forcar=False: ["MLB1", "MLB2", "MLB3"]
        self.chamadas = []
        self.respostas = {
            "MLB1": resposta("MLB1", {"2026-10-04": 10, "2026-10-05": 7}),
            "MLB2": resposta("MLB2", {"2026-10-05": 3}),
            "MLB3": resposta("MLB3", {"2026-10-05": 0}),
        }

        self.detalhes = []

        def falso(rota, **_):
            if rota.startswith("/items?ids="):
                self.detalhes.append(rota)
                ids = rota.split("ids=")[1].split("&")[0].split(",")
                return [{"code": 200, "body": {"id": m, "title": "Peça " + m, "price": 100.0, "status": "active",
                                               "available_quantity": 5, "category_id": "MLBCAT",
                                               "attributes": [{"id": "SELLER_SKU", "value_name": "SKU-" + m}]}}
                        for m in ids]
            if rota.startswith("/categories/"):
                self.detalhes.append(rota)
                return {"id": "MLBCAT", "name": "Pastilhas de Freio"}
            self.chamadas.append(rota)
            item = rota.split("/")[2]
            r = self.respostas[item]
            if isinstance(r, Exception):
                raise r
            return r
        S.ml_api_get = falso

    def tearDown(self):
        S.ml_api_get, S.ml_todos_os_anuncios, S.ml_integration_row = self._originais
        super().tearDown()

    def rodar(self):
        return S.ml_visitas_rodada(hoje=HOJE, forcar_horario=True, pausa=0)

    def visitas(self):
        with S.DB_LOCK:
            return {(r["item_id"], r["dia"]): r["visitas"] for r in S.DB.execute(
                "SELECT item_id, dia, visitas FROM ml_visitas_dia_v1868").fetchall()}

    def test_grava_as_visitas_por_anuncio_e_por_dia(self):
        r = self.rodar()
        self.assertEqual(r["buscados"], 3)
        v = self.visitas()
        self.assertEqual(v[("MLB1", "2026-10-04")], 10)
        self.assertEqual(v[("MLB1", "2026-10-05")], 7)
        self.assertEqual(v[("MLB3", "2026-10-05")], 0, "zero de verdade também é gravado")
        self.assertIn("last=30&unit=day&ending=2026-10-06", self.chamadas[0])

    def test_segunda_rodada_no_mesmo_dia_nao_busca_de_novo(self):
        self.rodar()
        self.chamadas.clear()
        r = self.rodar()
        self.assertEqual(self.chamadas, [], "anúncio já buscado hoje foi buscado de novo")
        self.assertEqual(r["ja_buscados_hoje"], 3)

    def test_dia_seguinte_regrava_a_janela_e_guarda_o_historico(self):
        self.rodar()
        # no dia seguinte o ML corrige o dia 05 e o dia 04 já saiu da resposta
        self.respostas["MLB1"] = resposta("MLB1", {"2026-10-05": 9, "2026-10-06": 4})
        S.ml_visitas_rodada(hoje=date(2026, 10, 7), forcar_horario=True, pausa=0)
        v = self.visitas()
        self.assertEqual(v[("MLB1", "2026-10-05")], 9, "o dia corrigido pelo ML não foi regravado")
        self.assertEqual(v[("MLB1", "2026-10-04")], 10, "o histórico fora da janela foi apagado")

    def test_erro_fica_pendente_e_e_retomado(self):
        self.respostas["MLB2"] = RuntimeError("Falha de conexão com Mercado Livre: timeout")
        r = self.rodar()
        self.assertEqual((r["buscados"], r["erros"]), (2, 1))
        self.respostas["MLB2"] = resposta("MLB2", {"2026-10-05": 3})
        self.chamadas.clear()
        r = self.rodar()
        self.assertEqual(len(self.chamadas), 1, "a retomada deveria buscar só o que falhou")
        self.assertEqual(self.visitas()[("MLB2", "2026-10-05")], 3)

    def test_anuncio_inexistente_nao_trava_a_rodada(self):
        self.respostas["MLB1"] = RuntimeError('Mercado Livre HTTP 404: {"error":"not_found"}')
        r = self.rodar()
        self.assertEqual((r["buscados"], r["nao_encontrados"], r["erros"]), (2, 1, 0))
        self.chamadas.clear()
        self.rodar()
        self.assertEqual(self.chamadas, [], "anúncio 404 deveria esperar o dia seguinte")

    def test_sem_permissao_para_a_rodada_sem_martelar_o_ml(self):
        S.ml_todos_os_anuncios = lambda forcar=False: [f"MLB{i}" for i in range(1, 21)]
        erro = RuntimeError("Mercado Livre HTTP 403: PA_UNAUTHORIZED_RESULT_FROM_POLICIES")
        self.respostas = {f"MLB{i}": erro for i in range(1, 21)}
        r = self.rodar()
        self.assertTrue(r["interrompido"])
        self.assertEqual(len(self.chamadas), S.ML_VISITAS_ERROS_SEGUIDOS)

    def test_ml_desconectado_nao_chama_a_api(self):
        S.ml_integration_row = lambda: None
        r = self.rodar()
        self.assertIn("pulado", r)
        self.assertEqual(self.chamadas, [])

    def test_status_separa_anuncio_sumido_de_erro(self):
        self.respostas["MLB1"] = RuntimeError('Mercado Livre HTTP 404: {"error":"not_found"}')
        S.ml_visitas_rodada(forcar_horario=True, pausa=0)   # hoje de verdade, como o status
        st = S.ml_visitas_status()
        self.assertEqual((st["com_erro"], st["nao_encontrados_hoje"]), (0, 1),
                         "anúncio que o ML não acha não é falha da coleta")

    def test_status_so_para_admin(self):
        self.rodar()
        status, corpo = self.cliente.pedir("GET", "/api/ml-visitas/status")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["linhas"], 4)
        self.assertEqual(corpo["historico_desde"], "2026-10-04")

    def test_guarda_o_retrato_do_anuncio_uma_vez_por_dia(self):
        self.rodar()
        with S.DB_LOCK:
            r = dict(S.DB.execute("SELECT * FROM ml_visitas_item_v1868 WHERE item_id='MLB1'").fetchone())
            cat = S.DB.execute("SELECT nome FROM ml_categoria_v1868 WHERE categoria_id='MLBCAT'").fetchone()
        self.assertEqual((r["titulo"], r["sku"], r["preco"], r["estoque"], r["categoria_id"]),
                         ("Peça MLB1", "SKU-MLB1", 100.0, 5, "MLBCAT"))
        self.assertEqual(cat["nome"], "Pastilhas de Freio")
        self.detalhes.clear()
        self.rodar()
        self.assertEqual(self.detalhes, [], "o retrato do anúncio foi buscado de novo no mesmo dia")


class Conversao(TesteNoromak):
    """Conversão = vendas ÷ visitas, com referência da categoria (ou da conta)."""

    HOJE = date(2026, 10, 6)          # período de 30 dias: 06/09 a 05/10

    def visita(self, item, dia, n):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_dia_v1868(item_id,dia,visitas,updated_at) VALUES(?,?,?,'')",
                         (item, dia, n))
            S.DB.commit()

    def venda(self, item, dia, pedidos, valor=100.0):
        with S.DB_LOCK:
            for k in range(pedidos):
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,"
                             "qty,amount,status,updated_at) VALUES('ml',?,?,?,?,?,?,?,1,?,'paid','')",
                             (f"{item}-{dia}-{k}", "1", dia, dia, "S", item, "Título " + item, valor))
            S.DB.commit()

    def categoria(self, item, cat):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,categoria_id,titulo) VALUES(?,?,?)",
                         (item, cat, "Anúncio " + item))
            S.DB.commit()

    def payload(self):
        dados = S.conversao_payload(30, hoje=self.HOJE)
        return dados, {i["mlb"]: i for i in dados["itens"]}

    def test_conversao_e_vendas_dividido_por_visitas_no_periodo(self):
        self.visita("MLB1", "2026-10-01", 200)
        self.visita("MLB1", "2026-08-20", 999)      # fora do período: não entra
        self.venda("MLB1", "2026-10-01", 4)
        self.venda("MLB1", "2026-10-06", 9)         # hoje: ainda não fechou, não entra
        _, i = self.payload()
        self.assertEqual((i["MLB1"]["visitas"], i["MLB1"]["pedidos"]), (200, 4))
        self.assertAlmostEqual(i["MLB1"]["conversao"], 0.02)

    def test_anuncio_sem_visita_coletada_nao_vira_conversao_infinita(self):
        self.venda("MLB9", "2026-10-01", 3)
        dados, i = self.payload()
        self.assertNotIn("MLB9", i, "anúncio sem visita coletada entrou na conta")
        self.assertEqual(dados["resumo"]["vendeu_sem_visita_coletada"], 1)

    def test_referencia_da_categoria_quando_tem_amostra(self):
        # categoria A converte ~5%; categoria B (pouca amostra) usa a referência da conta
        for k in range(1, 6):
            m = f"MLBA{k}"
            self.categoria(m, "A")
            self.visita(m, "2026-10-01", 100)
            self.venda(m, "2026-10-01", 5)
        self.categoria("MLBB1", "B")
        self.visita("MLBB1", "2026-10-01", 100)
        self.venda("MLBB1", "2026-10-01", 1)
        _, i = self.payload()
        self.assertEqual(i["MLBA1"]["ref_origem"], "categoria")
        self.assertAlmostEqual(i["MLBA1"]["conversao_ref"], 0.05)
        self.assertEqual(i["MLBB1"]["ref_origem"], "conta")

    def test_classifica_vitrine_joia_campeao_e_pouca_amostra(self):
        dados = {  # item: (visitas, pedidos)
            "MLBV": (1000, 2),      # muito visto, 0,2%  -> vitrine
            "MLBC": (1000, 40),     # muito visto, 4%    -> campeão
            "MLBM1": (400, 8), "MLBM2": (400, 8), "MLBM3": (300, 6),   # meio, 2%
            "MLBJ": (100, 6),       # pouco visto, 6% e 6 vendas -> joia
            "MLBP": (10, 2),        # 10 visitas -> ruído
        }
        for m, (v, p) in dados.items():
            self.visita(m, "2026-10-01", v)
            self.venda(m, "2026-10-01", p)
        r, i = self.payload()
        self.assertEqual(i["MLBV"]["grupo"], "vitrine")
        self.assertEqual(i["MLBC"]["grupo"], "campeao")
        self.assertEqual(i["MLBJ"]["grupo"], "joia")
        self.assertEqual(i["MLBP"]["grupo"], "pouca_amostra")
        # potencial = visitas × (ref − conv) × ticket; ref da conta = 2%
        self.assertAlmostEqual(i["MLBV"]["potencial"], round(1000 * (0.02 - 0.002) * 100, 2))
        self.assertIsNone(i["MLBC"]["potencial"])

    def test_conversao_caindo_contra_o_periodo_anterior(self):
        self.visita("MLB1", "2026-08-20", 200)
        self.venda("MLB1", "2026-08-20", 10)        # antes: 5%
        self.visita("MLB1", "2026-10-01", 200)
        self.venda("MLB1", "2026-10-01", 2)         # agora: 1%
        r, i = self.payload()
        self.assertTrue(i["MLB1"]["caindo"])
        self.assertEqual(r["resumo"]["caindo"], 1)

    def test_rota_e_somente_admin_e_responde(self):
        status, corpo = self.cliente.pedir("GET", "/api/conversao?dias=30")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["periodo"]["dias"], 30)


if __name__ == "__main__":
    unittest.main()
