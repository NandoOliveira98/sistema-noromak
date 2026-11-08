"""V20.41 — estoque Manual / Automático dos anúncios do TikTok (Bauru) na plataforma de anúncios (dono, 07/11: "plataforma,
por anúncio"). Só com a fonte "api": os anúncios ficam com controle 'noromak' e a conta de estoque 'tiktok' (Apollo de Bauru
menos o que está a baixar em Bauru); com a extensão o controle é 'tiktok' e nada é enviado pela plataforma."""

import time
from unittest import mock

from base import S, linha

from test_anuncios_estoque_v2026 import EstoqueBase, fila


class TikTokFalso:
    def __init__(self):
        self.estoque = {}       # sku_id -> quantidade
        self.locais = {}        # sku_id -> lista de depósitos
        self.seller_sku = {}    # sku_id -> SKU do vendedor
        self.nao_muda = set()
        self.status = "ACTIVATE"
        self.escritas = []

    def __call__(self, metodo, path, params=None, body=None, loja=True):
        if metodo == "GET" and path.startswith("/product/202309/products/"):
            skus = [{"id": sid, "seller_sku": self.seller_sku.get(sid, ""),
                     "inventory": [{"warehouse_id": w, "quantity": self.estoque.get(sid, 0)}
                                   for w in self.locais.get(sid, ["W1"])]} for sid in self.estoque]
            return {"code": 0, "data": {"id": path.rsplit("/", 1)[-1], "status": self.status, "skus": skus}}
        if path.endswith("/inventory/update"):
            self.escritas.append(body)
            for sk in body["skus"]:
                if sk["id"] not in self.nao_muda:
                    self.estoque[sk["id"]] = sk["inventory"][0]["quantity"]
            return {"code": 0, "data": {"errors": []}}
        raise AssertionError(path)


class TikTokEstoque(EstoqueBase):

    def setUp(self):
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        agora = int(time.time())
        S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                    "access_token_expire_in": agora + 86400,
                                                    "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
            S.DB.commit()
        self.tt = TikTokFalso()
        for p in (mock.patch.object(S, "tiktok_api", new=self.tt),
                  mock.patch.object(S, "TIKTOK_ESTOQUE_RELEITURA_ESPERAS", (0, 0))):
            p.start()
            self.addCleanup(p.stop)

    def variacao(self, sid="V1", sku="P1", qtd=3, api=True):
        self.tt.estoque[sid] = qtd
        self.tt.seller_sku[sid] = sku
        S._tiktok_sku_gravar({sid: {"product_id": "PR1", "titulo": "T", "status": "ACTIVATE", "seller_sku": sku,
                                    "warehouse_id": "W1", "armazens": 1, "qtd": qtd}}, apagar_ausentes=False)
        if api and S.tiktok_fonte() != "api":
            S.tiktok_fonte_definir("api", "dono")

    def hub(self, sid="V1"):
        with S.DB_LOCK:
            return dict(S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='tiktok' AND anuncio_id=?",
                                     (sid,)).fetchone())

    def test_controle_segue_a_fonte(self):
        self.variacao(api=False)
        self.assertEqual(self.hub()["controle"], "tiktok")
        with self.assertRaises(ValueError):
            S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")
        S.tiktok_fonte_definir("api", "dono")
        self.assertEqual(self.hub()["controle"], "noromak")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='automatico' WHERE canal='tiktok'")
            S.DB.commit()
        S.tiktok_fonte_definir("extensao", "dono")
        self.assertEqual((self.hub()["controle"], self.hub()["estoque_modo"]), ("tiktok", "manual"),
                         "voltou para a extensão: o Automático nunca fica ligado")

    def test_manual_grava_e_rele(self):
        self.variacao(qtd=5)
        r = S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.tt.escritas[0], {"skus": [{"id": "V1", "inventory": [{"warehouse_id": "W1", "quantity": 8}]}]})
        self.assertEqual((self.hub()["estoque"], self.hub()["estoque_enviado"]), (8, 8))

    def test_aceitou_sem_mudar_e_erro(self):
        self.variacao()
        self.tt.nao_muda.add("V1")
        r = S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")
        self.assertFalse(r["ok"])
        self.assertIn("mostra", r["erro"])

    def test_dois_depositos_ou_sku_alheio_nao_grava(self):
        self.variacao()
        self.tt.locais["V1"] = ["W1", "W2"]
        self.assertFalse(S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")["ok"])
        self.tt.locais["V1"] = ["W1"]
        self.tt.seller_sku["V1"] = "OUTRO"
        self.assertFalse(S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")["ok"])
        self.assertEqual(self.tt.escritas, [])

    def test_automatico_apollo_de_bauru_menos_fila_de_bauru(self):
        self.variacao(qtd=3)
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='automatico' WHERE canal='tiktok'")
            S.DB.commit()
        self.apollo["P1"] = {3: 10}
        S.anuncios_apollo_ler("P1", "bauru")
        S.anuncio_estoque_config_definir("tiktok", "automatico", "luis")
        fila([linha("TT9", "P1", 2, Marketplace="TIKTOK", Casa="bauru")])
        r = S.anuncios_estoque_rodada("tiktok", ator="sistema")
        self.assertEqual(r["enviados"], 1, r)
        self.assertEqual(self.tt.estoque["V1"], 8, "Apollo de Bauru 10 menos 2 a baixar em Bauru")

    def test_simulacao_da_conta_tiktok_nao_grava(self):
        self.variacao()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='automatico' WHERE canal='tiktok'")
            S.DB.commit()
        self.apollo["P1"] = {3: 10}
        S.anuncios_apollo_ler("P1", "bauru")
        r = S.anuncios_estoque_rodada("tiktok", ator="sistema")
        self.assertEqual((r["simulados"], r["enviados"]), (1, 0))
        self.assertEqual(self.tt.escritas, [])

    def test_rodizio_le_o_codigo_do_tiktok_e_worker_nao_envia_mais(self):
        self.variacao()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='automatico' WHERE canal='tiktok'")
            S.DB.commit()
        self.assertIn("P1", S._anuncios_codigos_automatico("bauru"))
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/enviar", {})
        self.assertEqual(st, 409, corpo)

    def test_sku_cortado_sem_vinculo_nao_recebe_estoque(self):
        # revisar-antes-de-subir (V20.41): a regra da V20.08 também na plataforma
        cortado = "KIT2-" + "1234567/" * 5 + "201104"[:50 - len("KIT2-" + "1234567/" * 5)]
        cortado = cortado[:50]
        self.assertEqual(len(cortado), 50)
        self.variacao(sku=cortado)
        with self.assertRaises(ValueError) as ctx:
            S.anuncio_estoque_manual("tiktok", "V1", 8, "luis")
        self.assertIn("cortado", str(ctx.exception))
        S.tiktok_vinculo_definir("V1", "1234567/2011044", "dono")
        self.assertEqual(S._anuncio_estoque_elegivel(self.hub()), "", "com o vínculo à mão vale")

    def test_envio_pela_api_espera_a_trava_da_troca_de_fonte(self):
        self.variacao()
        ocupada = mock.Mock()
        ocupada.acquire.return_value = False
        with mock.patch.object(S, "TIKTOK_ESTOQUE_LOCK", ocupada):
            r = S._tiktok_estoque_gravar_api("V1", 8, "P1")
        self.assertFalse(r["ok"])
        self.assertEqual(self.tt.escritas, [])

    def test_tela(self):
        texto = (S.APP_DIR / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn('tiktok: ["TikTok", "no TikTok"', texto)
        self.assertIn('contas.push("tiktok")', texto)


if __name__ == "__main__":
    import unittest
    unittest.main()
