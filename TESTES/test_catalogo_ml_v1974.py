"""V19.74 — Monitor de catálogo do ML (decisões do dono, 22/10).

Só lê o ML: situação na vitrine do catálogo (price_to_win), concorrentes e se o preço para
ganhar ainda dá a margem mínima da promoção. Aviso (popup/push) só na VIRADA de ganhando
para perdendo e só quando o preço para ganhar dá margem. 1ª leitura não avisa.
"""
import unittest

from base import Cliente, S, TesteNoromak

PROD = {"custo": 50.0, "comissao": 0.12, "imposto": 0.12, "taxa_fixa": 6.0, "margem_alvo": 0.2, "produto": "Peça"}


def ptw(status, preco=120.0, ganhar=100.0):
    return {"item_id": "MLB1", "current_price": preco, "price_to_win": ganhar, "status": status,
            "catalog_product_id": "MLB999", "winner": {"item_id": "MLB7", "price": ganhar - 0.01},
            "reason": ["price"], "boosts": []}


class Avaliar(unittest.TestCase):
    CFG = {"margem_min_promo": 10}

    def test_perdendo_com_margem(self):
        a = S.catalogo_ml_avaliar(ptw("competing"), PROD, PROD, self.CFG)
        self.assertEqual(a["situacao"], "perdendo")
        # 100 − 12 − 12 − 6 − 50 = 20 → 20%
        self.assertAlmostEqual(a["margem_ganhar"], 0.20)
        self.assertTrue(a["da_margem"])

    def test_perdendo_sem_margem(self):
        a = S.catalogo_ml_avaliar(ptw("competing", ganhar=80.0), PROD, PROD, self.CFG)
        self.assertFalse(a["da_margem"])

    def test_sem_custo_fica_nulo_nunca_zero(self):
        a = S.catalogo_ml_avaliar(ptw("competing"), None, None, self.CFG)
        self.assertIsNone(a["margem_ganhar"])
        self.assertIsNone(a["da_margem"])

    def test_situacoes(self):
        self.assertEqual(S.catalogo_ml_avaliar(ptw("winning"), PROD, PROD, self.CFG)["situacao"], "ganhando")
        self.assertEqual(S.catalogo_ml_avaliar(ptw("sharing_first_place"), PROD, PROD, self.CFG)["situacao"], "compartilhando")
        self.assertEqual(S.catalogo_ml_avaliar(ptw("listed"), PROD, PROD, self.CFG)["situacao"], "nao_elegivel")


class Rodada(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.salvos = {n: getattr(S, n) for n in ("ml_api_get", "ml_itens_detalhe", "_promo_produtos",
                                                   "_ml_vinculos_mlb_sku", "_promo_prod_com_ml", "catalogo_ml_anuncios",
                                                   "ml_casas_conectadas")}
        S.ml_casas_conectadas = lambda: ["aracatuba"]
        self.status = {"MLB1": "winning"}
        self.ganhar = 100.0
        self.preco = 120.0
        self.concorrentes_resp = {"results": [{"item_id": "MLB7", "seller_id": 55, "price": 99.99,
                                               "shipping": {"free_shipping": True, "logistic_type": "fulfillment"},
                                               "listing_type_id": "gold_pro"}]}
        self.chamadas = []

        def api(path, *a, **k):
            self.chamadas.append(path)
            if "/price_to_win" in path:
                mlb = path.split("/")[2]
                r = ptw(self.status[mlb], preco=self.preco, ganhar=self.ganhar)
                r["item_id"] = mlb
                return r
            if path.startswith("/products/"):
                return self.concorrentes_resp
            raise AssertionError(path)
        S.ml_api_get = api
        S.catalogo_ml_anuncios = lambda: list(self.status)
        S.ml_itens_detalhe = lambda ids: {m: {"titulo": "Peça", "preco": 120.0, "sku_ml": "ABC", "status": "active",
                                              "produto_catalogo": "MLB999"} for m in ids}
        S._promo_produtos = lambda: {"ABC": dict(PROD)}
        S._ml_vinculos_mlb_sku = lambda: {}
        S._promo_prod_com_ml = lambda prod, mlb, preco, det=None: (prod, "")

    def tearDown(self):
        for n, f in self.salvos.items():
            setattr(S, n, f)
        super().tearDown()

    def avisos(self):
        with S.DB_LOCK:
            return S.DB.execute("SELECT * FROM aviso_venda_v1893 WHERE marketplace='catalogo' AND silencioso=0").fetchall()

    def test_primeira_leitura_nao_avisa(self):
        self.status["MLB1"] = "competing"
        S.catalogo_ml_rodada(pausa=0)
        self.assertEqual(self.avisos(), [])
        p = S.catalogo_ml_payload()
        self.assertEqual(p["itens"][0]["situacao"], "perdendo")
        self.assertEqual(p["itens"][0]["concorrentes"], 1)

    def test_virada_com_margem_avisa_uma_vez(self):
        S.catalogo_ml_rodada(pausa=0)
        self.status["MLB1"] = "competing"
        S.catalogo_ml_rodada(pausa=0)
        S.catalogo_ml_rodada(pausa=0)  # continua perdendo: não repete
        av = self.avisos()
        self.assertEqual(len(av), 1)
        self.assertEqual(av[0]["casa"], "aracatuba")
        f = S._avisos_formatar(av[0])
        self.assertIn("catálogo", f["titulo"].lower())
        self.assertTrue(f["url"].endswith("/catalogo-ml"))

    def test_virada_sem_margem_nao_avisa(self):
        S.catalogo_ml_rodada(pausa=0)
        self.status["MLB1"] = "competing"
        self.ganhar = 80.0
        S.catalogo_ml_rodada(pausa=0)
        self.assertEqual(self.avisos(), [])
        self.assertEqual(S.catalogo_ml_payload()["resumo"]["perdendo_sem_margem"], 1)

    def test_nada_e_gravado_no_ml(self):
        S.catalogo_ml_rodada(pausa=0)
        self.assertTrue(self.chamadas)  # só ml_api_get (GET); nenhum POST/PUT existe na rodada

    def test_anuncio_que_saiu_do_catalogo_sai_da_lista(self):
        S.catalogo_ml_rodada(pausa=0)
        self.status = {}
        S.catalogo_ml_rodada(pausa=0)
        self.assertEqual(S.catalogo_ml_payload()["itens"], [])

    def test_erro_no_ml_nao_apaga_a_leitura_anterior(self):
        S.catalogo_ml_rodada(pausa=0)

        def falha(path, *a, **k):
            raise RuntimeError("timeout")
        S.ml_api_get = falha
        S.catalogo_ml_rodada(pausa=0)
        it = S.catalogo_ml_payload()["itens"][0]
        self.assertEqual(it["situacao"], "ganhando")
        self.assertIn("timeout", it["erro"])

    def test_rotas(self):
        S.catalogo_ml_rodada(pausa=0)
        st, d = self.cliente.pedir("GET", "/api/catalogo-ml?conta=aracatuba")
        self.assertEqual(st, 200)
        self.assertEqual(d["itens"][0]["item_id"], "MLB1")
        st, d = self.cliente.pedir("GET", "/api/catalogo-ml/item?conta=aracatuba&item=MLB1")
        self.assertEqual(st, 200)
        self.assertEqual(d["concorrentes"][0]["item_id"], "MLB7")
        self.assertTrue(d["historico"])
        st, _ = Cliente(self.url_base).pedir("GET", "/api/catalogo-ml")
        self.assertIn(st, (401, 403))

    # --- revisão de 22/10 (achados médios) ---

    def test_resposta_estranha_dos_concorrentes_nao_zera(self):
        S.catalogo_ml_rodada(pausa=0)
        self.concorrentes_resp = {}            # 200 sem "results"
        S.catalogo_ml_rodada(pausa=0)
        self.assertEqual(S.catalogo_ml_payload()["itens"][0]["concorrentes"], 1)
        self.assertEqual(len(S.catalogo_ml_item("aracatuba", "MLB1")["concorrentes"]), 1)

    def test_preco_para_ganhar_abaixo_da_faixa_do_frete_nao_afirma_margem(self):
        # 85 → 75 cruza a faixa de R$ 79 do frete grátis: o frete usado é o do preço atual
        S.catalogo_ml_rodada(pausa=0)
        self.status["MLB1"] = "competing"
        self.preco, self.ganhar = 85.0, 75.0
        S._promo_produtos = lambda: {"ABC": dict(PROD, custo=30.0)}
        S.catalogo_ml_rodada(pausa=0)
        self.assertEqual(self.avisos(), [])
        it = S.catalogo_ml_payload()["itens"][0]
        self.assertIn("79", it["taxas_origem"])
        self.assertTrue(it["frete_incerto"])


class ListaDeAnuncios(TesteNoromak):
    """A lista de catálogo só é usada para apagar quando vier COMPLETA."""

    def setUp(self):
        super().setUp()
        self._get, self._row = S.ml_api_get, S.ml_integration_row
        S.ml_integration_row = lambda: {"seller_id": "123"}

    def tearDown(self):
        S.ml_api_get, S.ml_integration_row = self._get, self._row
        super().tearDown()

    def test_usa_scan_e_junta_as_paginas(self):
        paginas = {"": {"results": ["MLB1", "MLB2"], "scroll_id": "A", "paging": {"total": 3}},
                   "A": {"results": ["MLB3"], "scroll_id": "B", "paging": {"total": 3}},
                   "B": {"results": [], "scroll_id": "B", "paging": {"total": 3}}}
        vistos = []

        def api(path, *a, **k):
            vistos.append(path)
            self.assertIn("search_type=scan", path)
            self.assertIn("catalog_listing=true", path)
            sid = path.split("scroll_id=")[1] if "scroll_id=" in path else ""
            return paginas[sid]
        S.ml_api_get = api
        self.assertEqual(S.catalogo_ml_anuncios(), ["MLB1", "MLB2", "MLB3"])

    def test_lista_incompleta_falha_e_nao_apaga_o_monitor(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO catalogo_ml_v1974(conta,item_id,situacao) VALUES('aracatuba','MLB9','ganhando')")
            S.DB.commit()
        S.ml_api_get = lambda path, *a, **k: {"results": ["MLB1"], "scroll_id": "", "paging": {"total": 5}}
        with self.assertRaises(RuntimeError):
            S.catalogo_ml_anuncios()
        salvo = S.ml_casas_conectadas
        S.ml_casas_conectadas = lambda: ["aracatuba"]
        try:
            S.catalogo_ml_rodada(pausa=0)
        finally:
            S.ml_casas_conectadas = salvo
        self.assertEqual([i["item_id"] for i in S.catalogo_ml_payload()["itens"]], ["MLB9"])

    def test_resposta_que_nao_e_lista_falha(self):
        S.ml_api_get = lambda path, *a, **k: "erro"
        with self.assertRaises(RuntimeError):
            S.catalogo_ml_anuncios()


if __name__ == "__main__":
    unittest.main()
