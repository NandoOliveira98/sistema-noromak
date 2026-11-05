"""V20.27 - Anuncios: estoque no deposito do vendedor (ML multiorigem).

Caso real (05/11 20:28): a conta de Aracatuba usa o estoque multiorigem do ML. O GET do estoque do user-product
volta sem `selling_address`, com UM local `seller_warehouse`, e o V20.26 recusava ("ajuste no ML").
"""

import copy

from base import S
from test_anuncios_estoque_v2026 import EstoqueBase, FalsoMLEstoque, canal, erro_ml, inserir

# Resposta REAL guardada em anuncio_amostra_v2024 (tipo estoque_up_ler), 05/11.
REAL = {"id": "MLBU1397136147", "user_id": 224625106,
        "locations": [{"type": "seller_warehouse", "store_id": "79979052", "quantity": 7,
                       "network_node_id": "BRP2246251061", "country_id": "BR", "status": "active"}],
        "last_updated": "2026-11-02T22:07:53Z", "available_sites": ["MLB"], "product_release_date": None,
        "stock_mode": "countable"}
UP = "MLBU1397136147"


class FalsoMLMultiorigem(FalsoMLEstoque):
    """O ML falso do V20.26 com o estoque multiorigem: GET devolve o corpo real; PUT em
    /stock/type/seller_warehouse muda a quantidade do deposito pelo store_id."""

    def __init__(self):
        super().__init__()
        self.corpo = {}        # up -> corpo do GET
        self.versao = {}       # up -> x-version
        self.ignora_put = set()  # up cujo PUT responde 200 sem mudar

    def multiorigem(self, mlb, corpo, versao=274):
        up = corpo["id"]
        self.corpo[up] = copy.deepcopy(corpo)
        self.versao[up] = versao
        qtd = sum(int(l.get("quantity") or 0) for l in corpo["locations"] if l["type"] != "meli_facility")
        self.item(mlb, qtd, up=up)

    def up_http(self, metodo, caminho, payload=None, headers=None):
        up = caminho.split("/user-products/", 1)[1].split("/", 1)[0]
        if up not in self.corpo:
            return super().up_http(metodo, caminho, payload, headers)
        self.chamadas.append((metodo, caminho, payload, S.ml_casa_atual(), dict(headers or {})))
        if metodo == "GET":
            return copy.deepcopy(self.corpo[up]), {"x-version": str(self.versao[up])}
        assert caminho == f"/user-products/{up}/stock/type/seller_warehouse", caminho
        if self.conflito_up > 0:
            self.conflito_up -= 1
            self.versao[up] += 1
            raise erro_ml(409, "version conflict")
        assert str((headers or {}).get("x-version")) == str(self.versao[up]), headers
        if up not in self.ignora_put:
            for pedido in payload["locations"]:
                for loc in self.corpo[up]["locations"]:
                    if loc["type"] == "seller_warehouse" and loc["store_id"] == pedido["store_id"]:
                        loc["quantity"] = pedido["quantity"]
            self.versao[up] += 1
            for it in self.itens.values():
                if it["user_product_id"] == up:
                    it["available_quantity"] = sum(int(l["quantity"]) for l in self.corpo[up]["locations"]
                                                   if l["type"] != "meli_facility")
        return {"id": up, "locations": self.corpo[up]["locations"]}, {}


class DepositoTeste(EstoqueBase):

    def setUp(self):
        super().setUp()
        self.ml = FalsoMLMultiorigem()
        # os patches do EstoqueBase ja estao ativos (S.* sao mocks): aponta para o ML multiorigem
        S.ml_api_get.side_effect = self.ml.get
        S._ml_promo_http.side_effect = self.ml.promo
        S._ml_estoque_http.side_effect = self.ml.up_http

    def puts_up(self):
        return [c for c in self.ml.escritas() if "/user-products/" in c[1]]

    def test_put_no_deposito_com_corpo_e_versao(self):
        inserir()
        self.ml.multiorigem("MLB1", REAL)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["metodo"], "user_product")
        self.assertEqual(r["depois"], 12)
        puts = self.puts_up()
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][1], f"/user-products/{UP}/stock/type/seller_warehouse")
        self.assertEqual(puts[0][2], {"locations": [{"store_id": "79979052", "network_node_id": "BRP2246251061",
                                                     "quantity": 12}]})
        self.assertEqual(puts[0][4], {"x-version": "274"})
        self.assertFalse([c for c in self.ml.escritas() if c[1].startswith("/items/")])  # nada de PUT /items
        self.assertEqual(canal()["estoque_enviado"], 12)
        self.assertEqual(canal()["estoque"], 12)
        self.assertEqual(S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["resultado"], "ok")
        with S.DB_LOCK:
            tipos = {r[0] for r in S.DB.execute("SELECT tipo FROM anuncio_amostra_v2024").fetchall()}
        self.assertIn("estoque_put_seller_warehouse", tipos)
        # releu o estoque do deposito depois do PUT
        gets = [c for c in self.ml.chamadas if c[0] == "GET" and "/user-products/" in c[1]]
        self.assertGreaterEqual(len(gets), 2)

    def test_zero_vai_no_deposito(self):
        inserir()
        self.ml.multiorigem("MLB1", REAL)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 0, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.puts_up()[0][2]["locations"][0]["quantity"], 0)

    def test_releitura_nao_confere_erro(self):
        inserir()
        self.ml.multiorigem("MLB1", REAL)
        self.ml.ignora_put.add(UP)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(r["resultado"], "erro")
        self.assertIn("não mudou", r["erro"])
        self.assertIsNone(canal()["estoque_enviado"])
        self.assertEqual(S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["resultado"], "erro")

    def test_409_rele_e_tenta_uma_vez(self):
        inserir()
        self.ml.multiorigem("MLB1", REAL)
        self.ml.conflito_up = 1
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 9, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        puts = self.puts_up()
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0][4], {"x-version": "274"})
        self.assertEqual(puts[1][4], {"x-version": "275"})  # releu a versao
        inserir(anuncio_id="MLB2")
        outro = dict(REAL, id="MLBU2")
        self.ml.multiorigem("MLB2", outro)
        self.ml.conflito_up = 2  # duas vezes seguidas: desiste
        r = S.ml_estoque_gravar("aracatuba", "MLB2", 9, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(self.ml.itens["MLB2"]["available_quantity"], 7)

    def test_dois_depositos_recusa_listando(self):
        inserir()
        dois = copy.deepcopy(REAL)
        dois["locations"].append({"type": "seller_warehouse", "store_id": "80000001", "quantity": 3,
                                  "network_node_id": "BRP9", "country_id": "BR", "status": "active"})
        self.ml.multiorigem("MLB1", dois)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertIn("79979052", r["erro"])
        self.assertIn("80000001", r["erro"])
        self.assertIn("7", r["erro"])
        self.assertEqual(self.ml.escritas(), [])

    def test_deposito_inativo_recusa(self):
        inserir()
        inativo = copy.deepcopy(REAL)
        inativo["locations"][0]["status"] = "paused"
        self.ml.multiorigem("MLB1", inativo)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertIn("79979052", r["erro"])
        self.assertEqual(self.ml.escritas(), [])

    def test_deposito_e_full_grava_so_o_deposito(self):
        """meli_facility (Full) nao conta como deposito; o PUT so leva o do vendedor."""
        inserir()
        misto = copy.deepcopy(REAL)
        misto["locations"].append({"type": "meli_facility", "quantity": 4})
        self.ml.multiorigem("MLB1", misto)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(self.puts_up()[0][2]["locations"]), 1)

    def test_endereco_de_venda_e_deposito_juntos_recusa(self):
        inserir()
        junto = copy.deepcopy(REAL)
        junto["locations"].append({"type": "selling_address", "quantity": 2})
        self.ml.multiorigem("MLB1", junto)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertIn("endereço de venda", r["erro"])
        self.assertEqual(self.ml.escritas(), [])

    def test_full_continua_recusado(self):
        inserir()
        self.ml.multiorigem("MLB1", REAL)
        self.ml.itens["MLB1"]["logistic_type"] = "fulfillment"
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 12, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(self.ml.escritas(), [])

    def test_selling_address_sem_mudanca(self):
        """O caminho do V20.26 (selling_address) continua igual."""
        inserir()
        self.ml.item("MLB1", 3, up="UP1")
        self.ml.up["UP1"] = {"locations": [{"type": "selling_address", "quantity": 3}], "versao": 4}
        self.ml.nao_muda.add("MLB1")
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 9, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.puts_up()[0][1], "/user-products/UP1/stock/type/selling_address")

    def test_manual_pela_funcao_da_tela(self):
        inserir(estoque=7)
        self.ml.multiorigem("MLB1", REAL)
        r = S.anuncio_estoque_manual("aracatuba", "MLB1", 15, "luis")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.puts_up()[0][1], f"/user-products/{UP}/stock/type/seller_warehouse")
        self.assertEqual(canal()["estoque_enviado"], 15)

    def test_automatico_usa_o_deposito(self):
        inserir(ficha="P1", modo="automatico", estoque=7)
        self.ml.multiorigem("MLB1", REAL)
        self.apollo["P1"] = {1: 10, 3: 10}
        S.anuncios_apollo_ler("P1", "aracatuba")
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        r = S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(r["enviados"], 1, r)
        self.assertEqual(self.puts_up()[0][1], f"/user-products/{UP}/stock/type/seller_warehouse")
        self.assertEqual(self.puts_up()[0][2]["locations"][0]["quantity"], 10)
        self.assertEqual(canal()["estoque_enviado"], 10)

    def test_bauru_usa_a_casa_bauru(self):
        inserir(conta="bauru", anuncio_id="MLB9")
        self.ml.multiorigem("MLB9", dict(REAL, id="MLBU9"))
        r = S.ml_estoque_gravar("bauru", "MLB9", 2, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual({c[3] for c in self.ml.chamadas}, {"bauru"})


if __name__ == "__main__":
    import unittest
    unittest.main()
