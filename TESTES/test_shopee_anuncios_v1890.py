"""V18.90 — Conversão e Ruptura também na Shopee.

A Shopee é simulada: nenhum teste aqui chama a API de verdade.
"""

import unittest
from datetime import date, timedelta

from base import S, TesteNoromak

HOJE = date(2026, 10, 9)


class ShopeeFalsa:
    def __init__(self):
        self.itens = {   # item_id -> (status Shopee, nome, sku, preço, estoque, views)
            "101": ("NORMAL", "Lanterna Volare", "6563-1040", 309.9, 12, 1000),
            "102": ("NORMAL", "Manopla Câmbio", "2324-6010", 78.9, 0, 400),
            "103": ("UNLIST", "Sensor ABS", "7711-0099", 233.5, 5, 50),
        }
        self.chamadas = []

    def __call__(self, caminho, params=None, retry_auth=True):
        params = params or {}
        self.chamadas.append((caminho, dict(params)))
        if caminho == "/api/v2/product/get_item_list":
            ids = [i for i, v in self.itens.items() if v[0] == params["item_status"]]
            return {"response": {"item": [{"item_id": int(i), "item_status": self.itens[i][0]} for i in ids],
                                 "has_next_page": False}}
        ids = [x for x in str(params.get("item_id_list") or "").split(",") if x in self.itens]
        if caminho == "/api/v2/product/get_item_base_info":
            return {"response": {"item_list": [
                {"item_id": int(i), "item_name": self.itens[i][1], "item_sku": self.itens[i][2], "item_status": self.itens[i][0],
                 "category_id": 555, "price_info": [{"current_price": self.itens[i][3]}],
                 "stock_info_v2": {"summary_info": {"total_available_stock": self.itens[i][4]}}} for i in ids]}}
        if caminho == "/api/v2/product/get_item_extra_info":
            return {"response": {"item_list": [{"item_id": int(i), "views": self.itens[i][5]} for i in ids]}}
        raise AssertionError("rota não simulada: " + caminho)


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S.shopee_api_get, S.shopee_integration_row)
        self.shopee = ShopeeFalsa()
        S.shopee_api_get = self.shopee
        S.shopee_integration_row = lambda: {"access_token": "t", "shop_id": "77", "refresh_token": "r"}
        S._SHOPEE_ANUNCIOS_ESTADO.update(estoque_em=0.0, ultimo={})

    def tearDown(self):
        S.shopee_api_get, S.shopee_integration_row = self._orig
        super().tearDown()

    def rodar(self, dia=HOJE, completa=True):
        return S.shopee_anuncios_rodada(hoje=dia, completa=completa)

    def venda(self, item, dia, qtd=1, valor=100.0, pedido=None, marketplace="shopee"):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,"
                         "qty,amount,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'paid','')",
                         (marketplace, pedido or f"{item}-{dia}-{qtd}", "1", dia, dia, "S", item, "T " + item, qtd, valor))
            S.DB.commit()

    def visitas(self, item):
        with S.DB_LOCK:
            return {r["dia"]: r["visitas"] for r in S.DB.execute(
                "SELECT dia, visitas FROM shopee_visitas_dia_v1890 WHERE item_id=?", (item,)).fetchall()}


class Coleta(Base):

    def test_retrato_grava_anuncios_com_status_do_vocabulario_do_ml(self):
        r = self.rodar()
        self.assertTrue(r["ok"], r)
        with S.DB_LOCK:
            itens = {x["item_id"]: dict(x) for x in S.DB.execute("SELECT * FROM shopee_item_v1890").fetchall()}
        self.assertEqual(set(itens), {"101", "102", "103"})
        self.assertEqual((itens["101"]["status"], itens["103"]["status"]), ("active", "paused"))
        self.assertEqual((itens["101"]["titulo"], itens["101"]["preco"], itens["102"]["estoque"]), ("Lanterna Volare", 309.9, 0))

    def test_visita_do_dia_e_a_diferenca_entre_retratos(self):
        self.rodar(HOJE - timedelta(days=1))
        self.assertEqual(self.visitas("101"), {}, "primeiro retrato não tem com o que comparar")
        self.shopee.itens["101"] = self.shopee.itens["101"][:5] + (1060,)
        self.rodar(HOJE)
        self.assertEqual(self.visitas("101"), {(HOJE - timedelta(days=1)).isoformat(): 60})

    def test_total_que_cai_nao_vira_visita_negativa(self):
        self.rodar(HOJE - timedelta(days=1))
        self.shopee.itens["101"] = self.shopee.itens["101"][:5] + (900,)
        r = self.rodar(HOJE)
        self.assertEqual(r["total_caiu"], 1)
        self.assertEqual(self.visitas("101"), {})
        with S.DB_LOCK:
            flag = S.DB.execute("SELECT views_nao_acumulado FROM shopee_item_v1890 WHERE item_id='101'").fetchone()[0]
        self.assertEqual(flag, 1)

    def test_estoque_entra_na_tabela_da_shopee_e_nao_na_do_ml(self):
        self.rodar()
        with S.DB_LOCK:
            sp = S.DB.execute("SELECT COUNT(*) FROM shopee_estoque_dia_v1890").fetchone()[0]
            ml = S.DB.execute("SELECT COUNT(*) FROM ml_estoque_dia_v1872").fetchone()[0]
        self.assertEqual((sp, ml), (3, 0))

    def test_fora_do_retrato_so_confere_o_estoque_de_quem_vende(self):
        self.venda("102", (HOJE - timedelta(days=3)).isoformat())
        self.rodar(completa=False)
        bases = [c for c in self.shopee.chamadas if c[0].endswith("get_item_base_info")]
        self.assertEqual([c[1]["item_id_list"] for c in bases], ["102"])
        self.assertFalse(any(c[0].endswith("get_item_extra_info") for c in self.shopee.chamadas))

    def test_shopee_desconectada_nao_chama_nada(self):
        S.shopee_integration_row = lambda: None
        r = self.rodar()
        self.assertIn("pulado", r)
        self.assertEqual(self.shopee.chamadas, [])

    def test_tabela_de_estoque_desconhecida_e_recusada(self):
        with self.assertRaises(ValueError):
            S._ml_estoque_registrar({"1": {"estoque": 1}}, "2026-10-09", tabela="usuarios")


class Telas(Base):

    def test_ruptura_da_shopee_acha_o_zerado(self):
        for k in range(1, 20):
            self.venda("102", (HOJE - timedelta(days=k)).isoformat(), valor=78.9)
        self.rodar(HOJE - timedelta(days=1))
        d = S.ruptura_payload(30, hoje=HOJE, marketplace="shopee")
        i = {x["mlb"]: x for x in d["itens"]}["102"]
        self.assertEqual(i["grupo"], "zerado")
        self.assertTrue(i["link"].startswith("https://shopee.com.br/product/77/102"))
        self.assertEqual(d["marketplace_nome"], "Shopee")

    def test_ruptura_do_ml_nao_mistura_a_shopee(self):
        for k in range(1, 20):
            self.venda("102", (HOJE - timedelta(days=k)).isoformat())
        self.rodar(HOJE - timedelta(days=1))
        self.assertEqual(S.ruptura_payload(30, hoje=HOJE)["itens"], [])

    def test_conversao_da_shopee_usa_as_visitas_calculadas(self):
        self.rodar(HOJE - timedelta(days=2))
        self.shopee.itens["101"] = self.shopee.itens["101"][:5] + (1100,)
        self.rodar(HOJE - timedelta(days=1))
        self.venda("101", (HOJE - timedelta(days=2)).isoformat(), pedido="P1")
        self.venda("101", (HOJE - timedelta(days=2)).isoformat(), pedido="P2")
        d = S.conversao_payload(30, hoje=HOJE, marketplace="shopee")
        i = {x["mlb"]: x for x in d["itens"]}["101"]
        self.assertEqual((i["visitas"], i["pedidos"]), (100, 2))
        self.assertAlmostEqual(i["conversao"], 0.02)
        self.assertFalse(d["tem_ads"])

    def test_rotas_aceitam_o_marketplace_e_recusam_o_desconhecido(self):
        for rota in ("/api/ruptura", "/api/conversao"):
            status, corpo = self.cliente.pedir("GET", rota + "?dias=30&marketplace=shopee")
            self.assertEqual(status, 200, corpo)
            self.assertEqual(corpo["marketplace"], "shopee")
            status, corpo = self.cliente.pedir("GET", rota + "?dias=30&marketplace=amazon")
            self.assertEqual(status, 400, corpo)

    def test_worker_novo_esta_no_painel_de_saude(self):
        self.assertIn("shopee_anuncios_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
