"""V19.04 — Campanhas do ML lidas por anúncio.

A lista do vendedor (/seller-promotions/users) não traz o "Preço competitivo" (PRICE_MATCHING
por anúncio) nem a oferta relâmpago. Por anúncio (/seller-promotions/items/{MLB}) vem tudo.
O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import unittest
from urllib.parse import urlparse

from base import S
from test_promo_convites_v1889 import Base

# resposta real de 12/10 (MLB1853256760), adaptada para o MLB1 de teste
REAL = [
    {"id": "P-MLB17937078", "type": "SMART", "ref_id": "CANDIDATE-MLB1-77082912898", "status": "candidate",
     "price": 85.0, "meli_percentage": 3, "seller_percentage": 12, "original_price": 100.0,
     "name": "Impulsione suas vendas"},
    {"id": "P-MLB18099366", "type": "PRICE_MATCHING", "ref_id": "CANDIDATE-MLB1-77440137604",
     "status": "candidate", "price": 85.0, "meli_percentage": 2.4, "seller_percentage": 12.6,
     "original_price": 100.0, "name": "Saia na frente da concorrência"},
    {"id": "LGH-MLB1000", "type": "LIGHTNING", "ref_id": "CANDIDATE-MLB1-77525897379", "status": "candidate",
     "price": 85.0, "original_price": 100.0, "min_discounted_price": 20.0, "max_discounted_price": 90.0,
     "suggested_discounted_price": 85.0, "stock": {"min": 5, "max": 27}},
    {"type": "PRICE_DISCOUNT", "status": "started", "price": 95.0, "original_price": 100.0,
     "start_date": "2026-09-24T00:00:00", "finish_date": "2026-10-23T23:59:59", "name": ""},
    {"id": "P-MLB17909028", "type": "DEAL", "status": "candidate", "price": 0, "original_price": 100.0,
     "start_date": "2026-10-19T00:00:00-03:00", "finish_date": "2099-11-12T00:00:00-03:00",
     "name": "11.11 da Black", "min_discounted_price": 20.0, "max_discounted_price": 90.0,
     "suggested_discounted_price": 85.0},
    {"id": "C-MLB5742416", "type": "SELLER_COUPON_CAMPAIGN", "sub_type": "FIXED_PERCENTAGE",
     "fixed_percentage": 6, "status": "candidate", "price": 0, "original_price": 100.0},
]


class CampanhasPorAnuncio(Base):

    def setUp(self):
        super().setUp()
        self.por_anuncio = {}
        self.falhar = set()
        self.lidos = []
        anterior = S._ml_promo_http

        def http(metodo, caminho, payload=None):
            u = urlparse(caminho)
            if metodo == "GET" and u.path.startswith("/seller-promotions/items/"):
                mlb = u.path.rsplit("/", 1)[1]
                self.lidos.append((S.ml_casa_atual(), mlb))
                if mlb in self.falhar:
                    raise RuntimeError("Mercado Livre respondeu HTTP 500: erro")
                return [dict(x) for x in self.por_anuncio.get(mlb, [])]
            return anterior(metodo, caminho, payload)
        S._ml_promo_http = http

    def na_lista(self, mlb, conta="aracatuba"):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO promo_renovacao_v1873(mlb, conta) VALUES(?,?)", (mlb, conta))
            S.DB.commit()

    def pares(self, conta=None):
        with S.DB_LOCK:
            q = "SELECT promotion_id, mlb FROM ml_convite_v1889" + (" WHERE conta=?" if conta else "")
            return {(r[0], r[1]) for r in S.DB.execute(q, (conta,) if conta else ()).fetchall()}

    def test_tipos_gravados_e_ignorados(self):
        self.na_lista("MLB1")
        self.por_anuncio["MLB1"] = REAL
        self.rodar()
        self.assertEqual(self.pares(), {("P-MLB17937078", "MLB1"), ("P-MLB18099366", "MLB1"),
                                        ("LGH-MLB1000", "MLB1"), ("P-MLB17909028", "MLB1")})
        c = self.convite("P-MLB18099366", "MLB1")
        self.assertEqual(c["tipo"], "PRICE_MATCHING")
        with S.DB_LOCK:
            oid = S.DB.execute("SELECT offer_id FROM ml_convite_v1889 WHERE promotion_id='P-MLB18099366'").fetchone()[0]
        self.assertEqual(oid, "CANDIDATE-MLB1-77440137604")
        # sem campanha na lista do vendedor: a linha aparece na tela mesmo assim
        self.assertEqual(self.convite("P-MLB17937078", "MLB1")["situacao"], "pronto")

    def test_relampago_nunca_pronto_nem_aceito(self):
        self.na_lista("MLB1")
        self.por_anuncio["MLB1"] = REAL
        self.modo("automatico")
        self.rodar()
        c = self.convite("LGH-MLB1000", "MLB1")
        self.assertEqual(c["situacao"], "so_ver")
        self.assertIn("relâmpago", c["motivo"])
        self.assertIsNotNone(c["margem"])
        self.assertNotIn("LGH-MLB1000", [p["promotion_id"] for _, p in self.posts])
        with self.assertRaises(ValueError):
            S.promo_convite_aceitar("LGH-MLB1000", "MLB1", "admin_teste")

    def test_deal_por_anuncio_mantem_a_regra(self):
        self.na_lista("MLB1")
        self.por_anuncio["MLB1"] = REAL
        self.rodar()
        c = self.convite("P-MLB17909028", "MLB1")
        self.assertEqual((c["situacao"], c["preco_escolhido"]), ("pronto", 85.0))

    def test_limpeza_so_do_anuncio_lido(self):
        self.na_lista("MLB1")
        self.na_lista("MLB2")
        self.por_anuncio = {"MLB1": REAL[:2], "MLB2": REAL[:1]}
        self.rodar()
        self.por_anuncio = {"MLB1": REAL[:1], "MLB2": REAL[:1]}
        self.falhar = {"MLB2"}
        with S.DB_LOCK:   # convite de MLB3 (fora da lista) visto antes por outro caminho fica intocado
            S.DB.execute("INSERT INTO ml_campanha_v1889(conta,promotion_id,tipo,status) "
                         "VALUES('aracatuba','P-X','SMART','started')")
            S.DB.commit()
        self.campanhas = [{"id": "P-X", "type": "SMART", "status": "started"}]
        self.itens["P-X"] = [{"id": "MLB3", "status": "candidate", "original_price": 100.0, "price": 85.0}]
        self.por_anuncio["MLB2"] = []
        self.rodar()
        self.assertEqual(self.pares(), {("P-MLB17937078", "MLB1"), ("P-MLB17937078", "MLB2"), ("P-X", "MLB3")},
                         "MLB1 perdeu o PRICE_MATCHING; MLB2 deu erro e não pode perder nada")

    def test_removido_nao_entra(self):
        self.na_lista("MLB1")
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        self.por_anuncio["MLB1"] = REAL
        self.rodar()
        self.assertEqual(self.pares(), set())
        self.assertNotIn(("aracatuba", "MLB1"), self.lidos)

    def test_cada_conta_le_os_seus_anuncios(self):
        S.ml_integration_row = lambda: {"access_token": "TOK-" + S.ml_casa_atual(),
                                        "seller_id": "1" if S.ml_casa_atual() == S.CASA_PRINCIPAL else "2"}
        orig = S.ml_casas_conectadas
        S.ml_casas_conectadas = lambda: ["aracatuba", "bauru"]
        self.na_lista("MLB1")
        self.na_lista("MLB3", "bauru")
        self.por_anuncio = {"MLB1": REAL[:1], "MLB3": REAL[1:2]}
        try:
            self.rodar()
        finally:
            S.ml_casas_conectadas = orig
        self.assertEqual(sorted(self.lidos), [("aracatuba", "MLB1"), ("bauru", "MLB3")])
        self.assertEqual(self.pares("bauru"), {("P-MLB18099366", "MLB3")})
        self.assertEqual(self.pares("aracatuba"), {("P-MLB17937078", "MLB1")})


class PausaEInterrupcao(CampanhasPorAnuncio):

    def test_buscar_convites_sem_stop_event_tambem_pausa(self):
        self.na_lista("MLB1")
        self.na_lista("MLB2")
        self.campanhas = [{"id": "P-X", "type": "SMART", "status": "started"}]
        chamadas = []
        orig = S.time.sleep
        S.time.sleep = lambda s: chamadas.append(s)
        try:
            S.promo_convites_rodada(pausa=0.3)
        finally:
            S.time.sleep = orig
        self.assertGreaterEqual(chamadas.count(0.3), 3, "1 campanha + 2 anúncios sem pausa")

    def test_rodada_interrompida_nao_limpa(self):
        import threading
        self.na_lista("MLB1")
        self.por_anuncio["MLB1"] = REAL[:2]
        self.rodar()
        antes = self.pares()
        self.por_anuncio["MLB1"] = []
        parar = threading.Event()
        parar.set()
        S._promo_convites_rodada_conta(parar, pausa=0)
        self.assertEqual(self.pares(), antes)
        with S.DB_LOCK:
            st = {r[0] for r in S.DB.execute("SELECT status FROM ml_campanha_v1889").fetchall()}
        self.assertNotIn("finished", st)


class ComparacaoNaLinha(Base):
    """V19.04 (tarefa 2): cada linha da renovação traz as campanhas do anúncio e a mais lucrativa."""

    def setUp(self):
        super().setUp()
        with S.DB_LOCK:   # promoção atual a R$ 65: lucro 0,76×65 − 50 = −0,60
            S.DB.execute("INSERT OR REPLACE INTO promo_renovacao_v1873(mlb, conta, marcado, preco, situacao) "
                         "VALUES('MLB1','aracatuba',1,65.0,'ok')")
            S.DB.commit()

    def convite_db(self, pid, tipo, receita, situacao="pronto", mlb="MLB1", conta="aracatuba",
                   prazo="2099-12-31T23:59:00Z", status="started"):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_campanha_v1889(conta,promotion_id,tipo,status,nome,prazo) "
                         "VALUES(?,?,?,?,?,?)", (conta, pid, tipo, status, "Camp " + pid, prazo))
            S.DB.execute("INSERT OR REPLACE INTO ml_convite_v1889(promotion_id,mlb,tipo,status_item,preco_original,"
                         "preco,situacao,motivo,receita,conta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (pid, mlb, tipo, "candidate", 100.0, receita, situacao, "m", receita, conta))
            S.DB.commit()

    def linha(self, mlb="MLB1"):
        p = S.promo_renovacao_payload()
        return {i["mlb"]: i for i in p["itens"]}[mlb], p

    def test_melhor_pelo_lucro_e_ganho(self):
        self.convite_db("P-A", "SMART", 85.0)
        self.convite_db("P-B", "PRICE_MATCHING", 90.0)
        i, p = self.linha()
        self.assertAlmostEqual(i["lucro_promo"], -0.6, places=2)
        self.assertEqual([c["promotion_id"] for c in i["campanhas"]], ["P-B", "P-A"])
        self.assertEqual(i["campanhas"][0]["nome_tipo"], "Preço competitivo")
        self.assertEqual(i["campanhas"][1]["nome_tipo"], "Desconto inteligente")
        self.assertAlmostEqual(i["campanhas"][0]["lucro"], 18.4, places=2)
        self.assertTrue(i["campanhas"][0]["pode_aceitar"])
        self.assertEqual(i["campanha_melhor"]["promotion_id"], "P-B")
        self.assertAlmostEqual(i["campanha_melhor"]["ganho"], 19.0, places=2)
        self.assertEqual(p["resumo"]["campanha_mais_lucro"], 1)

    def test_abaixo_da_minima_nunca_melhor(self):
        self.convite_db("P-A", "SMART", 70.0)   # lucro 3,20 > −0,60, mas margem 4,6%
        i, p = self.linha()
        self.assertEqual(len(i["campanhas"]), 1)
        self.assertIsNone(i["campanha_melhor"])
        self.assertEqual(p["resumo"]["campanha_mais_lucro"], 0)

    def test_relampago_pode_ser_melhor_mas_nao_aceita(self):
        self.convite_db("P-A", "SMART", 85.0)
        self.convite_db("LGH-1", "LIGHTNING", 95.0, situacao="so_ver")
        i, _ = self.linha()
        self.assertEqual(i["campanha_melhor"]["promotion_id"], "LGH-1")
        self.assertFalse(i["campanha_melhor"]["pode_aceitar"])
        self.assertEqual(i["campanha_melhor"]["nome_tipo"], "Oferta relâmpago")

    def test_sem_custo_lucros_nulos(self):
        S._precificador_produtos_ml = lambda: {}
        self.convite_db("P-A", "SMART", 85.0)
        i, p = self.linha()
        self.assertIsNone(i["lucro_promo"])
        self.assertIsNone(i["campanhas"][0]["lucro"])
        self.assertIsNone(i["campanha_melhor"])
        self.assertEqual(p["resumo"]["campanha_mais_lucro"], 0)

    def test_outra_conta_expirado_e_removido_fora(self):
        self.convite_db("P-A", "SMART", 85.0, conta="bauru")
        self.convite_db("P-B", "SMART", 85.0, prazo="2000-01-01T00:00:00Z")
        self.convite_db("P-C", "SMART", 85.0, situacao="expirado")
        self.convite_db("P-D", "SMART", 85.0, status="finished")
        i, _ = self.linha()
        self.assertEqual(i["campanhas"], [])
        self.convite_db("P-E", "SMART", 85.0)
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        p = S.promo_renovacao_payload()
        self.assertNotIn("MLB1", [x["mlb"] for x in p["itens"]])

    def test_sem_promocao_compara_com_preco_do_anuncio(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE promo_renovacao_v1873 SET preco=NULL, preco_anuncio=100.0, marcado=0")
            S.DB.commit()
        self.convite_db("P-A", "SMART", 85.0)
        i, _ = self.linha()
        self.assertAlmostEqual(i["lucro_promo"], 26.0, places=2)
        self.assertIsNone(i["campanha_melhor"])

    def test_mesma_base_com_cache_quente_num_preco_so(self):
        # cache quente só no preço da promoção (65): comissão 20%, frete 5 — vale também para a campanha (85)
        orig = S.ml_tarifa_frete_anuncio
        def tarifa(mlb, preco=None, **kw):
            if round(float(preco), 2) == 65.0:
                return {"comissao": 0.20, "taxa_fixa": 0.0, "frete": 5.0}
            raise RuntimeError("fora do cache")
        S.ml_tarifa_frete_anuncio = tarifa
        try:
            self.convite_db("P-A", "SMART", 85.0)
            i, _ = self.linha()
        finally:
            S.ml_tarifa_frete_anuncio = orig
        self.assertAlmostEqual(i["lucro_promo"], 65 * 0.68 - 5 - 50, places=2)
        self.assertAlmostEqual(i["campanhas"][0]["lucro"], 85 * 0.68 - 5 - 50, places=2)

    def test_aceita_continua_depois_do_prazo(self):
        self.convite_db("P-A", "SMART", 85.0, situacao="aceito", prazo="2000-01-01T00:00:00Z")
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_convite_v1889 SET status_item='started'")
            S.DB.commit()
        i, _ = self.linha()
        self.assertEqual([c["promotion_id"] for c in i["campanhas"]], ["P-A"])


class TelaCampanhaNaLinha(unittest.TestCase):
    # V19.04: coluna nova na lista; a lista separada de convites saiu, o modo ficou nas Regras.
    def test_tela(self):
        from base import RAIZ
        h = (RAIZ / "APP" / "promocoes-auto.html").read_text(encoding="utf-8")
        self.assertIn('<th class="l">Campanha do ML</th>', h)
        self.assertIn("Campanha do ML dá mais lucro", h)
        self.assertNotIn('id="convTab"', h)
        for i in ('id="convModos"', 'id="convBuscar"', 'id="convModoDesc"'):
            self.assertIn(i, h)
        self.assertLess(h.index('id="regrasBox"'), h.index('id="convModos"'))
        self.assertIn("e.stopPropagation()", h)


if __name__ == "__main__":
    unittest.main()
