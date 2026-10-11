"""V18.89 — Convites de campanha do ML nas Promoções automáticas.

O ML é simulado: nenhum teste aqui chama a API de verdade.
Produto de teste: custo 50, comissão 12%, imposto 12%, sem taxa fixa ->
preço mínimo R$ 65,79; margem mínima da promoção (10%) a partir de ~R$ 75,76.
"""

import unittest
from urllib.parse import parse_qs, urlparse

from base import S, TesteNoromak

PROD = {"sku": "SKU1", "produto": "Peça", "curva": "A", "custo": 50.0, "imposto": 0.12,
        "comissao": 0.12, "taxa_fixa": 0.0, "margem_alvo": 0.15}


def campanha(pid, tipo, status="started", prazo="2099-12-31T23:59:00Z", nome="Campanha"):
    return {"id": pid, "type": tipo, "status": status, "start_date": "2026-10-01T00:00:00Z",
            "finish_date": "2099-12-31T23:59:00Z", "deadline_date": prazo, "name": nome}


def candidato(mlb, **extra):
    base = {"id": mlb, "status": "candidate", "original_price": 100.0}
    base.update(extra)
    return base


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S._ml_promo_http, S.ml_itens_detalhe, S._precificador_produtos_ml, S._ml_vinculos_mlb_sku,
                      S.ml_integration_row, S.ml_api_get, S._promo_ruptura_por_anuncio)
        # V18.99: conectada só a conta principal (Bauru não conectada nestes cenários)
        S.ml_integration_row = lambda: ({"access_token": "TESTE", "seller_id": "1"}
                                        if S.ml_casa_atual() == S.CASA_PRINCIPAL else None)
        S._precificador_produtos_ml = lambda: {"SKU1": dict(PROD)}
        S._ml_vinculos_mlb_sku = lambda: {"MLB1": "SKU1", "MLB2": "SKU1", "MLB3": "SKU1"}
        S._promo_ruptura_por_anuncio = lambda: {}
        S._ML_TARIFA_CACHE.clear()

        def sem_ml(caminho, headers=None):   # tarifa do anúncio: vale a do Precificador
            raise RuntimeError("ML simulado: sem resposta")
        S.ml_api_get = sem_ml
        self.det = {m: {"titulo": "Peça " + m, "preco": 100.0, "status": "active", "estoque": 50, "sku_ml": "SKU1"}
                    for m in ("MLB1", "MLB2", "MLB3")}
        S.ml_itens_detalhe = lambda mlbs: {m: dict(self.det[m]) for m in mlbs if m in self.det}
        self.campanhas = []
        self.itens = {}
        self.posts = []
        self.recusar = ""

        def http(metodo, caminho, payload=None):
            u = urlparse(caminho)
            if metodo == "GET" and u.path.startswith("/seller-promotions/users/"):
                return {"results": self.campanhas, "paging": {"total": len(self.campanhas)}}
            if metodo == "GET" and u.path.endswith("/items"):
                pid = u.path.split("/")[3]
                q = parse_qs(u.query)
                assert q["promotion_type"][0] in S.PROMO_CONVITE_TIPOS
                return {"results": self.itens.get(pid, []), "paging": {}}
            if metodo == "POST" and u.path.startswith("/seller-promotions/items/"):
                if self.recusar:
                    raise RuntimeError(self.recusar)
                self.posts.append((u.path.rsplit("/", 1)[1], payload))
                return {"offer_id": "OFFER-1", "price": payload.get("deal_price") or 76.0, "original_price": 100.0}
            raise AssertionError(f"rota não simulada: {metodo} {caminho}")
        S._ml_promo_http = http

    def tearDown(self):
        (S._ml_promo_http, S.ml_itens_detalhe, S._precificador_produtos_ml, S._ml_vinculos_mlb_sku,
         S.ml_integration_row, S.ml_api_get, S._promo_ruptura_por_anuncio) = self._orig
        S._ML_TARIFA_CACHE.clear()
        super().tearDown()

    def modo(self, modo):
        S.promo_auto_config_salvar({"modo_convites": modo}, "admin_teste")

    def rodar(self):
        return S.promo_convites_rodada(pausa=0)

    def convite(self, pid, mlb):
        return {(i["promotion_id"], i["mlb"]): i for i in S.promo_convites_payload()["itens"]}[(pid, mlb)]


class Avaliacao(Base):

    def test_cofinanciada_conta_a_parte_que_o_ml_paga(self):
        # comprador paga 76, mas o vendedor recebe 84 (só banca 16%; o ML paga 8%)
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=76.0, seller_percentage=16, meli_percentage=8,
                                       offer_id="CANDIDATE-MLB1-1")]
        self.rodar()
        c = self.convite("P-1", "MLB1")
        self.assertEqual((c["situacao"], c["receita"]), ("pronto", 84.0))
        self.assertAlmostEqual(c["margem"], (84 * 0.76 - 50) / 84, places=3)

    def test_margem_abaixo_da_minima_vai_para_revisar(self):
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=62.0, seller_percentage=30, meli_percentage=8, offer_id="C-1")]
        self.rodar()
        c = self.convite("P-1", "MLB1")
        self.assertEqual(c["situacao"], "revisar")
        self.assertIn("Margem", c["motivo"])

    def test_abaixo_do_preco_minimo_nunca_fica_pronto(self):
        self.campanhas = [campanha("P-1", "PRICE_MATCHING")]
        self.itens["P-1"] = [candidato("MLB1", price=55.0, seller_percentage=40, offer_id="C-1")]
        self.rodar()
        self.assertIn("prejuízo", self.convite("P-1", "MLB1")["motivo"])

    def test_deal_usa_o_sugerido_quando_da_margem(self):
        self.campanhas = [campanha("P-2", "DEAL")]
        self.itens["P-2"] = [candidato("MLB1", price=0, suggested_discounted_price=80.0,
                                       min_discounted_price=70.0, max_discounted_price=90.0)]
        self.rodar()
        c = self.convite("P-2", "MLB1")
        self.assertEqual((c["situacao"], c["preco_escolhido"]), ("pronto", 80.0))

    def test_deal_cai_para_o_maior_preco_quando_o_sugerido_fura_a_margem(self):
        self.campanhas = [campanha("P-2", "DEAL")]
        self.itens["P-2"] = [candidato("MLB1", price=0, suggested_discounted_price=74.0,
                                       min_discounted_price=70.0, max_discounted_price=90.0)]
        self.rodar()
        c = self.convite("P-2", "MLB1")
        self.assertEqual((c["situacao"], c["preco_escolhido"]), ("pronto", 90.0))
        self.assertIn("maior que o ML aceita", c["motivo"])

    def test_deal_sem_margem_em_nenhum_preco_nao_aceita(self):
        self.campanhas = [campanha("P-2", "DEAL")]
        self.itens["P-2"] = [candidato("MLB1", price=0, suggested_discounted_price=70.0,
                                       min_discounted_price=66.0, max_discounted_price=72.0)]
        self.rodar()
        self.assertEqual(self.convite("P-2", "MLB1")["situacao"], "revisar")

    def test_sem_estoque_nao_entra(self):
        self.det["MLB1"]["estoque"] = 0
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=76.0, seller_percentage=16, offer_id="C-1")]
        self.rodar()
        self.assertEqual(self.convite("P-1", "MLB1")["situacao"], "estoque")

    def test_sem_custo_nao_entra(self):
        S._ml_vinculos_mlb_sku = lambda: {}
        self.det["MLB1"]["sku_ml"] = "DESCONHECIDO"
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=76.0, seller_percentage=16, offer_id="C-1")]
        self.rodar()
        c = self.convite("P-1", "MLB1")
        self.assertEqual(c["situacao"], "revisar")
        self.assertIn("Sem custo", c["motivo"])

    def test_tipo_fora_da_lista_e_campanha_encerrada_ficam_de_fora(self):
        self.campanhas = [campanha("P-L", "LIGHTNING"), campanha("P-F", "SMART", status="finished")]
        self.itens = {"P-L": [candidato("MLB1")], "P-F": [candidato("MLB1")]}
        self.rodar()
        self.assertEqual(S.promo_convites_payload()["itens"], [])

    def test_anuncio_removido_pelo_admin_nao_aparece(self):
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=76.0, seller_percentage=16, offer_id="C-1")]
        self.rodar()
        self.assertEqual(S.promo_convites_payload()["itens"], [])

    def test_convite_que_saiu_do_ml_sai_da_lista(self):
        self.campanhas = [campanha("P-1", "SMART")]
        self.itens["P-1"] = [candidato("MLB1", price=76.0, seller_percentage=16, offer_id="C-1"),
                             candidato("MLB2", price=76.0, seller_percentage=16, offer_id="C-2")]
        self.rodar()
        self.itens["P-1"] = self.itens["P-1"][:1]
        self.rodar()
        self.assertEqual([i["mlb"] for i in S.promo_convites_payload()["itens"]], ["MLB1"])


class Aceitar(Base):

    def preparar(self):
        self.campanhas = [campanha("P-1", "SMART"), campanha("P-2", "DEAL"), campanha("P-3", "MARKETPLACE_CAMPAIGN")]
        self.itens = {
            "P-1": [candidato("MLB1", price=76.0, seller_percentage=16, meli_percentage=8, offer_id="CANDIDATE-MLB1-1")],
            "P-2": [candidato("MLB2", price=0, suggested_discounted_price=80.0, min_discounted_price=70.0,
                              max_discounted_price=90.0)],
            "P-3": [candidato("MLB3", price=80.0, seller_percentage=15, meli_percentage=5),
                    candidato("MLB1", price=60.0, seller_percentage=30, meli_percentage=10)],
        }

    def test_simulacao_nunca_chama_o_ml(self):
        self.preparar()
        self.modo("simulacao")
        self.rodar()
        self.assertEqual(self.posts, [])

    def test_automatico_aceita_so_o_que_passa_e_com_o_corpo_certo_de_cada_tipo(self):
        self.preparar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["aceitos_agora"], 3)
        corpos = {mlb + "|" + p["promotion_id"]: p for mlb, p in self.posts}
        self.assertEqual(corpos["MLB1|P-1"], {"promotion_id": "P-1", "promotion_type": "SMART", "offer_id": "CANDIDATE-MLB1-1"})
        self.assertEqual(corpos["MLB2|P-2"], {"promotion_id": "P-2", "promotion_type": "DEAL", "deal_price": 80.0})
        self.assertEqual(corpos["MLB3|P-3"], {"promotion_id": "P-3", "promotion_type": "MARKETPLACE_CAMPAIGN"})
        self.assertNotIn("MLB1|P-3", corpos, "aceitou convite com margem abaixo da mínima")
        self.assertEqual(self.convite("P-1", "MLB1")["situacao"], "aceito")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM promo_auto_log_v1873 WHERE tipo='convite' AND acao='aceito'").fetchone()[0]
        self.assertEqual(n, 3)

    def test_automatico_respeita_o_limite_do_dia(self):
        self.preparar()
        S.promo_auto_config_salvar({"modo_convites": "automatico", "limite_dia": 1}, "admin_teste")
        self.rodar()
        self.assertEqual(len(self.posts), 1)

    def test_aceitar_em_lote_reconfere_e_recusa_o_que_nao_passa(self):
        self.preparar()
        self.rodar()
        r = S.promo_convites_aceitar_lote([{"promotion_id": "P-1", "mlb": "MLB1"},
                                           {"promotion_id": "P-3", "mlb": "MLB1"}], "luis")
        self.assertEqual(r["aceitos"], 1)
        falhou = [x for x in r["resultados"] if not x["ok"]][0]
        self.assertIn("Margem", falhou["erro"])
        self.assertEqual(len(self.posts), 1)

    def test_aceitar_reconfere_o_estoque_ao_vivo(self):
        self.preparar()
        self.rodar()
        self.det["MLB1"]["estoque"] = 0          # zerou depois da última rodada
        with self.assertRaises(ValueError):
            S.promo_convite_aceitar("P-1", "MLB1", "luis")
        self.assertEqual(self.posts, [])

    def test_recusa_do_ml_fica_registrada(self):
        self.preparar()
        self.rodar()
        self.recusar = "Mercado Livre respondeu HTTP 400: ERROR_CREDIBILITY_DISCOUNTED_PRICE"
        with self.assertRaises(RuntimeError):
            S.promo_convite_aceitar("P-2", "MLB2", "luis")
        c = self.convite("P-2", "MLB2")
        self.assertEqual(c["situacao"], "erro")
        self.assertIn("CREDIBILITY", c["motivo"])

    def test_ligar_o_automatico_dos_convites_exige_o_proprietario(self):
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/config", {"modo_convites": "automatico"})
        self.assertNotEqual(status, 200, corpo)
        self.assertEqual(S.promo_auto_config()["modo_convites"], "simulacao")
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/config", {"modo_convites": "lote"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(S.promo_auto_config()["modo_convites"], "lote")

    def test_rota_de_convites_responde(self):
        status, corpo = self.cliente.pedir("GET", "/api/promocoes-auto/convites")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["modo"], "simulacao")

    def test_worker_novo_esta_no_painel_de_saude(self):
        self.assertIn("promo_convites_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
