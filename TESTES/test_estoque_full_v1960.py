"""V19.60 — "No Full" da Demanda Full = estoque no ARMAZÉM do ML, não o available_quantity.

MLB6143159674 (20/10): available_quantity 23 (tudo no depósito do vendedor) e 0 no Full
(/user-products/{id}/stock, meli_facility). A remessa mostrava "No Full 23" e sugeria 2.
"""
import unittest
from datetime import date, timedelta

from base import S, TesteNoromak

HOJE = date(2026, 10, 20)
ONTEM = (HOJE - timedelta(days=1)).isoformat()

ESTOQUE_UP = {"id": "MLBU1", "locations": [
    {"type": "seller_warehouse", "quantity": 23},
    {"type": "meli_facility", "availability_type": "in_stock", "quantity": 0}]}


class EstoqueNoArmazem(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._get = S.ml_api_get
        self._prec = S._compras_produtos_precificador
        S._compras_produtos_precificador = lambda: {"4045": {}}

    def tearDown(self):
        S.ml_api_get = self._get
        S._compras_produtos_precificador = self._prec
        super().tearDown()

    def test_le_so_o_armazem_do_ml(self):
        S.ml_api_get = lambda path, *a, **k: ESTOQUE_UP
        info = {"MLB1": {"estoque": 23, "logistica": "fulfillment", "user_product": "MLBU1"}}
        S.ml_estoque_full_completar(info)
        self.assertEqual(info["MLB1"]["estoque_full"], 0)

    def test_ml_sem_resposta_fica_desconhecido(self):
        def falha(path, *a, **k):
            raise RuntimeError("timeout")
        S.ml_api_get = falha
        info = {"MLB1": {"estoque": 23, "logistica": "fulfillment", "user_product": "MLBU1"}}
        S.ml_estoque_full_completar(info)
        self.assertIsNone(info["MLB1"]["estoque_full"])

    def test_anuncio_fora_do_full_nao_consulta(self):
        chamadas = []
        S.ml_api_get = lambda path, *a, **k: chamadas.append(path) or ESTOQUE_UP
        info = {"MLB1": {"estoque": 5, "logistica": "drop_off", "user_product": "MLBU1"}}
        S.ml_estoque_full_completar(info)
        self.assertEqual(chamadas, [])
        self.assertNotIn("estoque_full", info["MLB1"])

    def _registrar(self, full):
        S._ml_estoque_registrar({"MLB6143159674": {"estoque": 23, "status": "active", "logistica": "fulfillment",
                                                    "estoque_full": full}}, ONTEM)
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,sku,titulo) VALUES('MLB6143159674','154893-4045','Hs 334')")
            S.DB.commit()

    def test_remessa_usa_o_estoque_do_full(self):
        self._registrar(0)
        a = S.full_remessa_candidatos(HOJE)["anuncios"]
        self.assertEqual([x["no_full"] for x in a if x["anuncio"] == "MLB6143159674"], [0])

    def test_remessa_sem_numero_do_full_fica_em_branco(self):
        self._registrar(None)
        a = S.full_remessa_candidatos(HOJE)["anuncios"]
        self.assertEqual([x["no_full"] for x in a if x["anuncio"] == "MLB6143159674"], [None])

    def test_ruptura_continua_com_o_estoque_vendavel(self):
        self._registrar(0)
        with S.DB_LOCK:
            r = S.DB.execute("SELECT estoque_ultimo, sem_estoque FROM ml_estoque_dia_v1872 WHERE item_id='MLB6143159674'").fetchone()
        self.assertEqual((r["estoque_ultimo"], r["sem_estoque"]), (23, 0))


if __name__ == "__main__":
    unittest.main()
