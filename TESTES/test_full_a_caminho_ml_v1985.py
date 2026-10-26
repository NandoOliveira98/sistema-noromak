"""V19.85 — "a caminho" da remessa Full também lido do ML (/inventories/{id}/stock/fulfillment,
status "transfer"). Caso real 26/10: o envio da NF 57.238 saiu sem "Marcar agendada" e a sugestão
repetiu os mesmos itens. A caminho = o MAIOR entre o agendado no sistema e o do ML, nunca a soma."""
import unittest
from datetime import date, timedelta

from base import S, TesteNoromak, RAIZ

HOJE = date(2026, 10, 26)


def ml_falso(inventarios, estoque, chamadas=None):
    """inventarios: MLB -> inventory_id; estoque: inventory_id -> corpo (ou Exception)."""
    def get(path, *a, **k):
        if chamadas is not None:
            chamadas.append(path)
        if path.startswith("/items?ids="):
            ids = path.split("ids=")[1].split("&")[0].split(",")
            return [{"code": 200, "body": {"id": m, "inventory_id": inventarios.get(m)}} for m in ids]
        if path.startswith("/inventories/"):
            inv = path.split("/")[2]
            r = estoque.get(inv)
            if isinstance(r, Exception):
                raise r
            return r
        raise AssertionError(path)
    return get


def fulfillment(transfer=None, outros=()):
    det = [{"status": s, "quantity": q} for s, q in outros]
    if transfer is not None:
        det.append({"status": "transfer", "quantity": transfer})
    return {"inventory_id": "X", "total": 0, "available_quantity": 0, "not_available_detail": det}


class LeituraML(TesteNoromak):
    def setUp(self):
        super().setUp()
        self._get, self._pausa = S.ml_api_get, S.FULL_ML_PAUSA
        S.FULL_ML_PAUSA = 0
        S.FULL_ML_INVENTARIO.clear()

    def tearDown(self):
        S.ml_api_get, S.FULL_ML_PAUSA = self._get, self._pausa
        S.FULL_ML_INVENTARIO.clear()
        super().tearDown()

    def test_transfer_e_o_a_caminho(self):
        S.ml_api_get = ml_falso({"MLB1": "INV1", "MLB2": "INV2"},
                                {"INV1": fulfillment(48, [("notSupported", 3)]), "INV2": fulfillment(None)})
        self.assertEqual(S.full_ml_a_caminho(["MLB1", "MLB2"]), {"MLB1": 48, "MLB2": 0})

    def test_ml_sem_resposta_fica_desconhecido_nunca_zero(self):
        S.ml_api_get = ml_falso({"MLB1": "INV1", "MLB2": None},
                                {"INV1": RuntimeError("Mercado Livre HTTP 500")})
        self.assertEqual(S.full_ml_a_caminho(["MLB1", "MLB2"]), {"MLB1": None, "MLB2": None})

    def test_ml_desconectado_nao_quebra(self):
        def falha(*a, **k):
            raise RuntimeError("Mercado Livre não conectado")
        S.ml_api_get = falha
        self.assertEqual(S.full_ml_a_caminho(["MLB1"]), {"MLB1": None})

    def test_inventario_guardado_nao_e_pedido_de_novo(self):
        chamadas = []
        S.ml_api_get = ml_falso({"MLB1": "INV1"}, {"INV1": fulfillment(5)}, chamadas)
        S.full_ml_a_caminho(["MLB1"])
        S.full_ml_a_caminho(["MLB1"])
        self.assertEqual(sum(1 for c in chamadas if c.startswith("/items?ids=")), 1)

    def test_erros_seguidos_param_a_leitura(self):
        chamadas = []
        invs = {f"MLB{i}": f"INV{i}" for i in range(10)}
        S.ml_api_get = ml_falso(invs, {f"INV{i}": RuntimeError("HTTP 429") for i in range(10)}, chamadas)
        r = S.full_ml_a_caminho(list(invs))
        self.assertTrue(all(v is None for v in r.values()))
        self.assertLessEqual(sum(1 for c in chamadas if c.startswith("/inventories/")), 5)


class RemessaComML(TesteNoromak):
    def anuncio(self, mlb, sku, no_full, venda):
        d = (HOJE - timedelta(days=1)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_estoque_dia_v1872(item_id,dia,checagens,estoque_ultimo,estoque_full_ultimo,logistica,updated_at) "
                         "VALUES(?,?,1,?,?,'fulfillment','')", (mlb, d, no_full, no_full))
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,sku,titulo) VALUES(?,?,?)", (mlb, sku, "T " + mlb))
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml',?,'1',?,?,?,?,'t',?,10,'paid','')", (f"o{mlb}", d, d, sku, mlb, venda))
            S.DB.commit()

    def setUp(self):
        super().setUp()
        self._orig, self._ml = S._compras_produtos_precificador, S.full_ml_a_caminho
        S._compras_produtos_precificador = lambda: {"A": {}, "B": {}, "C": {}}
        self.ml = {}
        S.full_ml_a_caminho = lambda mlbs: {m: self.ml.get(m) for m in mlbs}

    def tearDown(self):
        S._compras_produtos_precificador, S.full_ml_a_caminho = self._orig, self._ml
        super().tearDown()

    def itens(self):
        return {i["anuncio"]: i for i in S.full_remessa_atual()["itens"]}

    def test_envio_sem_marcar_agendada_e_descontado_pelo_ml(self):
        self.anuncio("MLB1", "A", no_full=0, venda=84)
        self.ml = {"MLB1": 48}
        S.full_remessa_rodada(HOJE, estoque={"A": 500})
        i = self.itens()["MLB1"]
        self.assertEqual((i["a_caminho"], i["a_caminho_ml"], i["a_caminho_sis"]), (48, 48, 0))
        self.assertEqual(i["qtd_sugerida"], 36)

    def test_maior_entre_sistema_e_ml_nunca_soma(self):
        self.anuncio("MLB1", "A", no_full=0, venda=200)
        self.anuncio("MLB2", "B", no_full=0, venda=200)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 500, "B": 500})
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=20, usuario="admin")
        S.full_remessa_ajustar(r["remessa_id"], "MLB2", qtd=20, usuario="admin")
        S.full_remessa_agendar(r["remessa_id"], HOJE.isoformat(), "admin")
        self.ml = {"MLB1": 50, "MLB2": 5}   # MLB1: ML registrou mais; MLB2: ML ainda não registrou tudo
        S.full_remessa_rodada(HOJE, estoque={"A": 500, "B": 500})
        it = self.itens()
        self.assertEqual((it["MLB1"]["a_caminho"], it["MLB1"]["a_caminho_sis"], it["MLB1"]["a_caminho_ml"]), (50, 20, 50))
        self.assertEqual((it["MLB2"]["a_caminho"], it["MLB2"]["a_caminho_sis"], it["MLB2"]["a_caminho_ml"]), (20, 20, 5))
        self.assertEqual(it["MLB1"]["qtd_sugerida"], 150)
        self.assertEqual(it["MLB2"]["qtd_sugerida"], 180)

    def test_ml_sem_resposta_fica_com_o_agendado(self):
        self.anuncio("MLB1", "A", no_full=0, venda=100)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 500})
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=30, usuario="admin")
        S.full_remessa_agendar(r["remessa_id"], HOJE.isoformat(), "admin")
        self.ml = {"MLB1": None}
        S.full_remessa_rodada(HOJE, estoque={"A": 500})
        i = self.itens()["MLB1"]
        self.assertEqual((i["a_caminho"], i["a_caminho_ml"], i["qtd_sugerida"]), (30, None, 70))

    def test_a_caminho_cobre_tudo_vai_para_fora_com_motivo(self):
        self.anuncio("MLB1", "A", no_full=0, venda=6)
        self.ml = {"MLB1": 6}
        S.full_remessa_rodada(HOJE, estoque={"A": 500})
        self.assertNotIn("MLB1", self.itens())
        fora = {f["anuncio"]: f for f in S.full_remessa_atual()["fora"]}
        self.assertIn("a caminho", fora["MLB1"]["motivo"])

    def test_tela_mostra_a_coluna(self):
        html = (RAIZ / "APP" / "analise-full.html").read_text(encoding="utf-8")
        self.assertIn("A caminho", html)
        self.assertIn("a_caminho_ml", html)


if __name__ == "__main__":
    unittest.main()
