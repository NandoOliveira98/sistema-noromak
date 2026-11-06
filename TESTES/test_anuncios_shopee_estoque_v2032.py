"""V20.32 - estoque Manual / Automatico dos anuncios da Shopee (Araçatuba): o mesmo disponivel do ML (Apollo de
Araçatuba menos o que esta a baixar); interruptor proprio da conta 'shopee'; grava por update_stock e rele."""

from unittest import mock

from base import S, linha

from test_anuncios_estoque_v2026 import EstoqueBase, fila, inserir


class ShopeeEstoqueFalsa:
    def __init__(self):
        self.estoque = {}
        self.variacoes = set()
        self.nao_muda = set()
        self.chamadas = []
        self.recusa = None

    def __call__(self, method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False):
        self.chamadas.append((method, path, params, payload))
        if path.endswith("get_item_base_info"):
            iid = str(params["item_id_list"])
            with S.DB_LOCK:  # o SKU do item na Shopee = o codigo da ficha ligada (revisao: o envio confere)
                f = S.DB.execute("SELECT ficha_codigo FROM anuncio_canal_v2023 WHERE canal='shopee' AND anuncio_id=?",
                                 (iid,)).fetchone()
            return {"response": {"item_list": [{"item_id": int(iid), "item_status": "NORMAL",
                                                "item_sku": f[0] if f else "",
                                                "has_model": iid in self.variacoes,
                                                "stock_info_v2": {"seller_stock": [{"stock": self.estoque.get(iid, 0)}]}}]}}
        if path.endswith("update_stock"):
            if self.recusa:
                raise self.recusa
            iid = str(payload["item_id"])
            if iid not in self.nao_muda:
                self.estoque[iid] = payload["stock_list"][0]["seller_stock"][0]["stock"]
            return {"response": {"success_list": [{"model_id": 0, "stock": self.estoque.get(iid)}], "failure_list": []}}
        raise AssertionError(path)

    def escritas(self):
        return [c for c in self.chamadas if c[1].endswith("update_stock")]


def inserir_shopee(item="111", ficha="P1", modo="manual", estoque=None, controle="noromak"):
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,"
                     "titulo,estoque,estoque_modo) VALUES('shopee','aracatuba',?,?,?,?,?,?,?)",
                     (item, ficha, ficha, controle, "T " + item, estoque, modo))
        S.DB.commit()


def linha_shopee(item="111"):
    with S.DB_LOCK:
        return dict(S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='shopee' AND anuncio_id=?",
                                 (item,)).fetchone())


class ShopeeEstoque(EstoqueBase):

    def setUp(self):
        super().setUp()
        self.sh = ShopeeEstoqueFalsa()
        p = mock.patch.object(S, "_shopee_chamar", new=self.sh)
        p.start()
        self.addCleanup(p.stop)

    def preparar(self, item="111", ficha="P1", disp=10, atual=3):
        inserir_shopee(item, ficha, modo="automatico", estoque=atual)
        self.sh.estoque[item] = atual
        self.apollo[ficha] = {1: disp}
        S.anuncios_apollo_ler(ficha, "aracatuba")

    def test_manual_grava_e_rele(self):
        inserir_shopee()
        self.sh.estoque["111"] = 5
        r = S.anuncio_estoque_manual("shopee", "111", 8, "luis")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.sh.escritas()[0][3], {"item_id": 111, "stock_list": [{"model_id": 0, "seller_stock": [{"stock": 8}]}]})
        self.assertEqual((linha_shopee()["estoque"], linha_shopee()["estoque_enviado"]), (8, 8))
        self.assertEqual(S.anuncio_estoque_log("shopee", "aracatuba", "111")[0]["resultado"], "ok")

    def test_aceitou_sem_mudar_e_erro(self):
        inserir_shopee()
        self.sh.nao_muda.add("111")
        r = S.anuncio_estoque_manual("shopee", "111", 8, "luis")
        self.assertFalse(r["ok"])
        self.assertIn("mostra", r["erro"])

    def test_variacoes_nao_grava(self):
        inserir_shopee()
        self.sh.variacoes.add("111")
        r = S.anuncio_estoque_manual("shopee", "111", 8, "luis")
        self.assertFalse(r["ok"])
        self.assertEqual(self.sh.escritas(), [])

    def test_so_noromak(self):
        inserir_shopee(controle="alephee")
        with self.assertRaises(ValueError):
            S.anuncio_estoque_manual("shopee", "111", 8, "luis")

    def test_simulacao_da_shopee_e_separada_do_ml(self):
        self.preparar()
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")  # o ML ligado nao liga a Shopee
        r = S.anuncios_estoque_rodada("shopee", ator="sistema")
        self.assertEqual((r["simulados"], r["enviados"]), (1, 0))
        self.assertEqual(self.sh.escritas(), [])

    def test_automatico_mesmo_numero_do_ml_e_venda_desconta(self):
        self.preparar(disp=10, atual=3)
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3)
        S.anuncio_estoque_config_definir("shopee", "automatico", "luis")
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        fila([linha("PED1", "P1", 2, Marketplace="SHOPEE")])
        r = S.anuncios_estoque_rodada("shopee", ator="sistema")
        self.assertEqual(r["enviados"], 1, r)
        self.assertEqual(self.sh.estoque["111"], 8, "Apollo 10 menos 2 a baixar")
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 8, "o mesmo disponível nos dois canais")

    def test_rodizio_le_o_codigo_da_shopee(self):
        inserir_shopee("111", "PSH", modo="automatico")
        self.apollo["PSH"] = {1: 4}
        self.assertIn("PSH", S._anuncios_codigos_automatico("aracatuba"))
        S.anuncio_estoque_config_definir("shopee", "automatico", "luis")
        self.sh.estoque["111"] = 0
        estado = {}
        afetadas = S.anuncios_estoque_volta(estado)
        self.assertIn("shopee", afetadas)
        self.assertEqual(self.sh.estoque["111"], 4)

    def test_payload_da_conta_shopee(self):
        self.preparar()
        p = S.anuncios_estoque_payload("shopee", "111")
        self.assertEqual(p["conta"], "shopee")
        self.assertEqual(p["anuncio"]["anuncio_id"], "111")
        self.assertTrue(p["anuncio"]["pode_controlar"])
        self.assertEqual(p["itens"][0]["calculado"], 10)


if __name__ == "__main__":
    import unittest
    unittest.main()
