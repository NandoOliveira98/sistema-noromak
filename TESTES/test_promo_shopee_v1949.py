"""V19.49 — Promoções automáticas da Shopee (renovação do desconto próprio pela API v2.discount).

Decisões do dono (16/10): marcação por anúncio; uma promoção por dia de vencimento que começa
quando a anterior termina; regras do ML, modo próprio (começa em Simulação, Automático com
OWNER); nunca mexe em promoção que não criou nem cria por cima de uma agendada; variação a
variação; recusa por campanha é tentada de novo. A Shopee é simulada: nada chama a API real.
"""

import unittest
from datetime import datetime

from base import S, TesteNoromak

AGORA = int(datetime(2026, 10, 16, 12, 0, tzinfo=S.BR_TZ).timestamp())
DIA = 86400
# custo 40, imposto 12%; taxa Shopee pela faixa (R$ 80–99,99: 14% + R$ 16, tabela de 16/10)
PROD = {"sku": "SKU1", "produto": "Peça", "curva": "A", "custo": 40.0, "imposto": 0.12,
        "comissao": 0.12, "taxa_fixa": 0.0, "margem_alvo": 0.15}


class ShopeeFalsa:
    def __init__(self):
        # item_id -> {status, nome, sku, estoque, modelos {model_id: (sku, preço normal, estoque)}}
        self.itens = {
            "101": {"status": "NORMAL", "nome": "Peça simples", "sku": "SKU1", "estoque": 40,
                    "modelos": {"0": ("SKU1", 120.0, 40)}},
            "202": {"status": "NORMAL", "nome": "Peça com variação", "sku": "", "estoque": 30,
                    "modelos": {"11": ("SKU1", 120.0, 10), "12": ("SEMCUSTO", 120.0, 20)}},
        }
        self.descontos = {}   # discount_id -> {nome, inicio, fim, status, itens {item: {model: preço}}}
        self.gets, self.posts = [], []
        self.recusar = ""     # texto de erro do add_discount_item para todos os itens
        self.proximo_id = 900

    def promo(self, did, inicio, fim, status, itens, nome="Manual"):
        self.descontos[str(did)] = {"nome": nome, "inicio": inicio, "fim": fim, "status": status, "itens": itens}

    def get(self, caminho, params=None, retry_auth=True):
        params = params or {}
        self.gets.append((caminho, dict(params)))
        if caminho == "/api/v2/discount/get_discount_list":
            lista = [{"discount_id": int(d), "discount_name": v["nome"], "start_time": v["inicio"],
                      "end_time": v["fim"], "status": v["status"]}
                     for d, v in self.descontos.items() if v["status"] == params["discount_status"]]
            return {"response": {"discount_list": lista, "more": False}}
        if caminho == "/api/v2/discount/get_discount":
            v = self.descontos[str(params["discount_id"])]
            itens = []
            for item, mods in v["itens"].items():
                if list(mods) == ["0"]:
                    itens.append({"item_id": int(item), "item_promotion_price": mods["0"], "model_list": []})
                else:
                    itens.append({"item_id": int(item), "model_list": [
                        {"model_id": int(m), "model_promotion_price": p} for m, p in mods.items()]})
            return {"response": {"discount_id": int(params["discount_id"]), "item_list": itens, "more": False}}
        if caminho == "/api/v2/product/get_item_base_info":
            out = []
            for i in str(params["item_id_list"]).split(","):
                it = self.itens.get(i)
                if not it:
                    continue
                sem_var = list(it["modelos"]) == ["0"]
                out.append({"item_id": int(i), "item_name": it["nome"], "item_sku": it["sku"],
                            "item_status": it["status"], "has_model": not sem_var,
                            "price_info": [{"original_price": it["modelos"]["0"][1], "current_price": 99.0}] if sem_var else [],
                            "stock_info_v2": {"summary_info": {"total_available_stock": it["estoque"]}}})
            return {"response": {"item_list": out}}
        if caminho == "/api/v2/product/get_model_list":
            it = self.itens[str(params["item_id"])]
            return {"response": {"model": [
                {"model_id": int(m), "model_sku": v[0], "model_name": "Var " + m,
                 "price_info": [{"original_price": v[1], "current_price": v[1]}],
                 "stock_info_v2": {"summary_info": {"total_available_stock": v[2]}}}
                for m, v in it["modelos"].items()]}}
        raise AssertionError("rota não simulada: " + caminho)

    def post(self, caminho, payload=None, retry_auth=True):
        self.posts.append((caminho, payload))
        if caminho == "/api/v2/discount/add_discount":
            self.proximo_id += 1
            self.promo(self.proximo_id, payload["start_time"], payload["end_time"], "upcoming", {},
                       payload["discount_name"])
            return {"response": {"discount_id": self.proximo_id}}
        if caminho == "/api/v2/discount/add_discount_item":
            if self.recusar:
                return {"response": {"count": 0, "error_list": [
                    {"item_id": it["item_id"], "fail_message": self.recusar} for it in payload["item_list"]]}}
            d = self.descontos[str(payload["discount_id"])]
            for it in payload["item_list"]:
                if "item_promotion_price" in it:
                    d["itens"][str(it["item_id"])] = {"0": it["item_promotion_price"]}
                else:
                    d["itens"][str(it["item_id"])] = {str(m["model_id"]): m["model_promotion_price"]
                                                     for m in it["model_list"]}
            return {"response": {"count": len(payload["item_list"]), "error_list": []}}
        raise AssertionError("POST não simulado: " + caminho)


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S.shopee_api_get, S.shopee_api_post, S.shopee_integration_row, S._precificador_produtos_ml)
        self.sh = ShopeeFalsa()
        S.shopee_api_get = self.sh.get
        S.shopee_api_post = self.sh.post
        S.shopee_integration_row = lambda: {"access_token": "t", "shop_id": "77", "refresh_token": "r"}
        S._precificador_produtos_ml = lambda: {"SKU1": dict(PROD)}

    def tearDown(self):
        import threading
        for t in threading.enumerate():
            if t.name.startswith("shopee-promo"):
                t.join(10)
        S.shopee_api_get, S.shopee_api_post, S.shopee_integration_row, S._precificador_produtos_ml = self._orig
        super().tearDown()

    def marcar(self, item="101"):
        S.shopee_promo_marcar(item, True, "admin_teste")

    def modo(self, modo):
        S.shopee_promo_config_salvar({"modo_renovacao": modo}, "admin_teste")

    def rodar(self, agora=AGORA, **kw):
        return S.shopee_promo_rodada(agora_ts=agora, **kw)

    def item(self, item="101"):
        return {i["item_id"]: i for i in S.shopee_promo_payload(agora_ts=AGORA)["itens"]}[item]

    def criacoes(self):
        return [p for p in self.sh.posts if p[0].endswith("add_discount")]


class Renovacao(Base):

    def test_taxa_pela_faixa_do_precificador(self):
        # V19.98: comissão da faixa + 3,5% de taxa de serviço adicional
        self.assertEqual(S.shopee_taxas_por_preco(79.99), (0.235, 4.5))
        self.assertEqual(S.shopee_taxas_por_preco(80.0), (0.175, 16.0))
        self.assertEqual(S.shopee_taxas_por_preco(150.0), (0.175, 20.0))
        self.assertEqual(S.shopee_taxas_por_preco(250.0), (0.175, 26.0))
        self.assertEqual(S.shopee_taxas_por_preco(600.0), (0.175, 26.0))
        c = S.shopee_promo_conta(PROD, 85.0)
        # 85 − 14,875 − 16 − 10,20 − 40 = 3,92 (4,6%: abaixo da mínima de 10%)
        self.assertEqual(c["lucro"], 3.92)

    def test_vencendo_no_automatico_cria_a_proxima_comecando_no_fim_da_atual(self):
        fim = AGORA + 2 * DIA
        self.sh.promo(500, AGORA - 20 * DIA, fim, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 1, r)
        criar = self.criacoes()[0][1]
        self.assertEqual((criar["start_time"], criar["end_time"]), (fim, fim + 30 * DIA))
        self.assertEqual(criar["discount_name"], "Noromak auto 18/10")
        itens = [p for p in self.sh.posts if p[0].endswith("add_discount_item")][0][1]["item_list"]
        self.assertEqual(itens, [{"item_id": 101, "purchase_limit": 0, "item_promotion_price": 105.0}])
        self.assertEqual(self.item()["situacao"], "agendada")

    def test_longe_do_fim_so_espera(self):
        self.sh.promo(500, AGORA - DIA, AGORA + 20 * DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        i = self.item()
        self.assertEqual(i["situacao"], "ativa")
        self.assertEqual(i["modelos"][0]["preco"], 105.0)   # o preço vigente fica guardado

    def test_simulacao_e_lote_so_leem_a_shopee(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        for modo in ("simulacao", "lote"):
            self.modo(modo)
            r = self.rodar()
            self.assertEqual(self.sh.posts, [], modo)
            self.assertEqual(r["renovaria"], 1)
            self.assertEqual(self.item()["situacao"], "pronta")

    def test_clique_no_lote_renova(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("lote")
        r = self.rodar(so=["101"], ator="admin_teste")
        self.assertEqual(r["renovadas"], 1, r)

    def test_nao_renova_duas_vezes_no_mesmo_dia(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        # a Shopee "ainda não mostra" a nova
        self.sh.descontos = {k: v for k, v in self.sh.descontos.items() if k == "500"}
        self.rodar(agora=AGORA + 600)
        self.assertEqual(len(self.criacoes()), 1)

    def test_mesmo_dia_de_vencimento_vira_uma_promocao_so(self):
        fim = AGORA + DIA
        self.sh.itens["303"] = dict(self.sh.itens["101"], nome="Outra")
        self.sh.promo(500, AGORA - 20 * DIA, fim, "ongoing", {"101": {"0": 105.0}, "303": {"0": 110.0}})
        self.marcar("101")
        self.marcar("303")
        self.modo("automatico")
        self.rodar()
        self.assertEqual(len(self.criacoes()), 1)
        nova = [d for k, d in self.sh.descontos.items() if k != "500"][0]
        self.assertEqual(set(nova["itens"]), {"101", "303"})

    def test_sem_promocao_comeca_daqui_a_mais_de_uma_hora(self):
        self.marcar()
        S.shopee_promo_precos("101", {"0": 105.0}, "admin_teste")
        self.modo("automatico")
        self.rodar()
        criar = self.criacoes()[0][1]
        self.assertGreaterEqual(criar["start_time"], AGORA + 3600)

    def test_promocao_agendada_nao_cria_por_cima(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.sh.promo(501, AGORA + DIA, AGORA + 10 * DIA, "upcoming", {"101": {"0": 100.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        i = self.item()
        self.assertEqual(i["situacao"], "agendada")
        self.assertIn("não cria outra por cima", i["motivo"])

    def test_nunca_altera_nem_encerra_promocao_manual(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        for caminho, payload in self.sh.posts:
            self.assertNotIn(caminho.rsplit("/", 1)[-1], ("end_discount", "delete_discount", "update_discount",
                                                          "update_discount_item", "delete_discount_item"))
            if caminho.endswith("add_discount_item"):
                self.assertNotEqual(payload["discount_id"], 500)

    def test_nao_marcado_nao_renova(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.sh.posts, [])

    def test_margem_abaixo_da_minima_nao_renova(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 85.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        self.assertEqual(self.item()["situacao"], "revisar")

    def test_sem_estoque_nao_renova(self):
        self.sh.itens["101"]["estoque"] = 0
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        self.assertEqual(self.item()["situacao"], "estoque")

    def test_anuncio_pausado_nao_renova(self):
        self.sh.itens["101"]["status"] = "UNLIST"
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        self.assertIn("pausado", self.item()["motivo"])

    def test_limite_do_dia_vale_no_automatico(self):
        S.shopee_promo_config_salvar({"limite_dia": 1}, "admin_teste")
        self.sh.itens["303"] = dict(self.sh.itens["101"], nome="Outra")
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}, "303": {"0": 110.0}})
        self.marcar("101")
        self.marcar("303")
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual((r["renovadas"], r["renovaria"]), (1, 1), r)


class Gaveta(Base):

    def test_conta_da_gaveta_com_o_produto_real_26330445(self):
        """V19.50: custo R$ 20,96, imposto 12%, frete 0, anúncio R$ 53,90 (16/10). Sem marcar."""
        prods = {"26330445": {"sku": "26330445", "produto": "Acrílico", "curva": "A", "custo": 20.96, "imposto": 0.12,
                              "comissao": 0.12, "taxa_fixa": 0.0, "margem_alvo": 0.2}}
        c = S._shopee_promo_calculo({"sku": "26330445", "preco_original": 53.90, "preco": None}, "", prods,
                                    {"26330445": 0.0}, S.shopee_promo_config())
        # V19.98: com o adicional de 3,5% (era 37,45 / 43,90 e lucro 11,19 / 7,53)
        # mínimo = (20,96 + 4,50) ÷ 0,645; 10% = (20,96 + 4,50) ÷ 0,545
        self.assertEqual((c["preco_minimo"], c["preco_margem_min"], c["sugestao"]), (39.48, 46.72, 48.51))
        self.assertEqual((c["lucro_anuncio"], c["lucro_sugestao"]), (9.31, 5.83))

    def test_sem_custo_nao_vira_zero(self):
        self.assertIsNone(S._shopee_promo_calculo({"sku": "NAOEXISTE", "preco_original": 50.0}, "", {}, {},
                                                  S.shopee_promo_config()))

    def test_lista_traz_a_conta_sem_marcar(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO shopee_item_v1890(item_id,titulo,sku,status,preco,estoque) VALUES('101','Peça','SKU1','active',120,5)")
            S.DB.commit()
        S.shopee_promo_buscar_novos("admin_teste")
        i = self.item("101")
        self.assertFalse(i["marcado"])
        self.assertEqual(i["calculos"]["0"]["custo"], 40.0)
        self.assertEqual(i["calculos"]["0"]["sugestao"], 108.0)

    def test_marcado_sem_conferencia_aparece_como_nao_conferido(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO shopee_item_v1890(item_id,titulo,sku,status) VALUES('101','Peça','SKU1','active')")
            S.DB.commit()
        S.shopee_promo_buscar_novos("admin_teste")
        orig = S.shopee_promo_rodada
        S.shopee_promo_rodada = lambda **k: {}
        try:
            self.marcar()
        finally:
            S.shopee_promo_rodada = orig
        self.assertEqual(self.item()["situacao"], "nao_checado")


class Variacoes(Base):

    def test_cada_variacao_com_a_sua_trava(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"202": {"11": 105.0, "12": 105.0}})
        self.marcar("202")
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 1, r)
        itens = [p for p in self.sh.posts if p[0].endswith("add_discount_item")][0][1]["item_list"]
        # a variação sem custo fica de fora; a outra renova com o seu preço
        self.assertEqual(itens[0]["model_list"], [{"model_id": 11, "model_promotion_price": 105.0}])
        i = self.item("202")
        self.assertIn("de fora", i["aviso"])
        sem_custo = {m["model_id"]: m for m in i["modelos"]}["12"]
        self.assertEqual(sem_custo["situacao"], "revisar")
        self.assertIsNone(sem_custo["margem"])   # nunca custo zero

    def test_preco_por_variacao_editado_na_gaveta(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"202": {"11": 105.0}})
        self.marcar("202")
        self.rodar()
        S.shopee_promo_precos("202", {"11": 108.0}, "admin_teste")
        self.modo("automatico")
        self.rodar(agora=AGORA + 60)
        itens = [p for p in self.sh.posts if p[0].endswith("add_discount_item")][0][1]["item_list"]
        self.assertEqual(itens[0]["model_list"], [{"model_id": 11, "model_promotion_price": 108.0}])

    def test_preco_alterado_em_outro_computador_da_409(self):
        S.shopee_promo_precos("202", {"11": 108.0}, "admin_teste")
        with self.assertRaises(S.PromoConflito):
            S.shopee_promo_precos("202", {"11": 110.0}, "admin_teste", precos_antes={"11": 100.0})


class Campanha(Base):

    def test_recusa_por_campanha_tenta_de_novo(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.sh.recusar = "item is in campaign"
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual((r["renovadas"], r["erros"]), (0, 1), r)
        self.assertIn("campanha da Shopee", self.item()["motivo"])
        # a campanha acabou: a próxima conferência renova (reaproveita a promoção já criada)
        self.sh.recusar = ""
        r = self.rodar(agora=AGORA + 3600)
        self.assertEqual(r["renovadas"], 1, r)
        self.assertEqual(len(self.criacoes()), 1)


class Lista(Base):

    def test_buscar_novos_entra_sem_marcar_e_removido_nao_volta(self):
        with S.DB_LOCK:
            for iid in ("101", "202", "909"):
                S.DB.execute("INSERT INTO shopee_item_v1890(item_id,titulo,sku,status) VALUES(?,?,?,?)",
                             (iid, "Anúncio " + iid, "SKU1", "active"))
            S.DB.commit()
        S.shopee_promo_remover(["909"], "admin_teste")
        r = S.shopee_promo_buscar_novos("admin_teste")
        self.assertEqual(r["incluidos"], 2)
        ids = {i["item_id"]: i for i in S.shopee_promo_payload(agora_ts=AGORA)["itens"]}
        self.assertEqual(set(ids), {"101", "202"})
        self.assertFalse(ids["101"]["marcado"])
        # incluir de novo (marcar) desfaz a remoção
        self.marcar("909")
        self.assertIn("909", {i["item_id"] for i in S.shopee_promo_payload(agora_ts=AGORA)["itens"]})

    def test_simulacao_guarda_a_primeira_resposta(self):
        self.sh.promo(500, AGORA - 20 * DIA, AGORA + DIA, "ongoing", {"101": {"0": 105.0}})
        self.marcar()
        self.rodar()
        amostras = S.shopee_promo_amostra()["amostras"]
        self.assertIn("get_discount_list", amostras)
        self.assertIn("get_discount", amostras)

    def test_modo_da_shopee_nao_mexe_no_do_ml(self):
        self.modo("lote")
        self.assertEqual(S.promo_auto_config(S.CASA_PRINCIPAL)["modo_renovacao"], "simulacao")
        self.assertEqual(S.shopee_promo_config()["modo_renovacao"], "lote")

    def test_rotas_e_automatico_pede_owner(self):
        status, corpo = self.cliente.pedir("GET", "/api/promocoes-auto/shopee")
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/shopee/config", {"modo_renovacao": "lote"})
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/shopee/config", {"modo_renovacao": "automatico"})
        self.assertIn(status, (403, 428), corpo)
        self.assertEqual(S.shopee_promo_config()["modo_renovacao"], "lote")


if __name__ == "__main__":
    unittest.main()
