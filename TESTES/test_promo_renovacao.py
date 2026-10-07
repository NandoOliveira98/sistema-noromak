"""V18.73 — Renovação automática do desconto próprio (PRICE_DISCOUNT) de 30 dias.

O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import unittest
from datetime import date

from base import S, TesteNoromak

HOJE = date(2026, 10, 6)

# custo 50, comissão 12%, imposto 12%, sem taxa fixa, margem do produto 15%
PROD = {"sku": "SKU1", "produto": "Peça", "curva": "A", "custo": 50.0, "imposto": 0.12,
        "comissao": 0.12, "taxa_fixa": 0.0, "margem_alvo": 0.15}


def promo_propria(status="started", preco=90.0, fim="2026-10-20T00:00:00", piso=60.0, teto=99.0):
    return {"type": "PRICE_DISCOUNT", "status": status, "price": preco if status != "candidate" else None,
            "original_price": 100.0, "start_date": "2026-09-20T00:00:00", "finish_date": fim if status != "candidate" else None,
            "min_discounted_price": piso, "max_discounted_price": teto}


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S.ml_promocoes_do_item, S.ml_itens_detalhe, S._ml_promo_http, S._precificador_produtos_ml,
                      S._ml_vinculos_mlb_sku, S.ml_integration_row, S.promo_atualizar_data_planilha)
        S.ml_integration_row = lambda: {"access_token": "TESTE", "seller_id": "1"}
        S._precificador_produtos_ml = lambda: {"SKU1": dict(PROD)}
        S._ml_vinculos_mlb_sku = lambda: {"MLB1": "SKU1", "MLB2": "SKU1"}
        S.promo_atualizar_data_planilha = lambda *a, **k: True
        self.promos = {"MLB1": [promo_propria("candidate")], "MLB2": [promo_propria("candidate")]}
        self.itens = {"MLB1": {"titulo": "Peça 1", "preco": 100.0, "status": "active", "estoque": 50, "sku_ml": "SKU1"},
                      "MLB2": {"titulo": "Peça 2", "preco": 100.0, "status": "active", "estoque": 50, "sku_ml": "SKU1"}}
        self.posts = []
        S.ml_promocoes_do_item = lambda mlb: list(self.promos.get(mlb, []))
        S.ml_itens_detalhe = lambda mlbs: {m: dict(self.itens[m]) for m in mlbs if m in self.itens}

        def http(metodo, caminho, payload=None):
            self.posts.append((metodo, caminho, payload))
            mlb = caminho.split("/items/")[1].split("?")[0]
            self.promos[mlb] = [promo_propria("started", payload["deal_price"], payload["finish_date"])]
            return {"ok": True}
        S._ml_promo_http = http

    def tearDown(self):
        (S.ml_promocoes_do_item, S.ml_itens_detalhe, S._ml_promo_http, S._precificador_produtos_ml,
         S._ml_vinculos_mlb_sku, S.ml_integration_row, S.promo_atualizar_data_planilha) = self._orig
        super().tearDown()

    def marcar(self, mlb="MLB1", preco=90.0):
        S.promo_renovacao_marcar(mlb, True, "admin_teste", preco=preco)

    def modo(self, modo):
        S.promo_auto_config_salvar({"modo_renovacao": modo}, "admin_teste")

    def rodar(self, hoje=HOJE):
        return S.promo_renovacao_rodada(hoje=hoje, pausa=0)

    def item(self, mlb="MLB1"):
        return {i["mlb"]: i for i in S.promo_renovacao_payload(hoje=HOJE)["itens"]}[mlb]


class Renovacao(Base):

    def test_anuncio_nao_marcado_nao_e_renovado(self):
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.posts, [])

    def test_vencida_no_automatico_renova_30_dias_com_o_mesmo_preco(self):
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 1)
        metodo, caminho, payload = self.posts[0]
        self.assertEqual((metodo, payload["deal_price"], payload["promotion_type"]), ("POST", 90.0, "PRICE_DISCOUNT"))
        self.assertEqual((payload["start_date"], payload["finish_date"]),
                         ("2026-10-06T00:00:00", "2026-11-05T00:00:00"))
        self.assertEqual(self.item()["situacao"], "ativa")

    def test_nao_renova_duas_vezes_no_mesmo_dia(self):
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.promos["MLB1"] = [promo_propria("candidate")]      # o ML ainda não mostra a nova
        self.rodar()
        self.assertEqual(len(self.posts), 1)

    def test_promocao_ativa_guarda_o_preco_e_espera_vencer(self):
        self.marcar(preco=None)
        self.promos["MLB1"] = [promo_propria("started", 88.0, "2026-10-20T00:00:00")]
        self.modo("automatico")
        self.rodar()
        i = self.item()
        self.assertEqual((i["situacao"], i["preco"], i["fim"][:10]), ("ativa", 88.0, "2026-10-20"))
        self.assertEqual(self.posts, [])
        # venceu: renova com o preço que estava valendo
        self.promos["MLB1"] = [promo_propria("candidate")]
        self.rodar(hoje=date(2026, 10, 20))
        self.assertEqual(self.posts[0][2]["deal_price"], 88.0)

    def test_simulacao_nao_chama_o_ml(self):
        self.marcar()
        self.modo("simulacao")
        r = self.rodar()
        self.assertEqual(self.posts, [])
        self.assertEqual(r["renovaria"], 1)
        self.assertEqual(self.item()["situacao"], "pronta")

    def test_lote_espera_o_clique_e_renova_pela_rota(self):
        self.marcar()
        self.modo("lote")
        self.rodar()
        self.assertEqual(self.posts, [])
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/renovar", {"mlbs": ["MLB1"]})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["resultados"][0]["ok"], True)
        self.assertEqual(len(self.posts), 1)

    def test_margem_abaixo_da_do_produto_vai_para_revisar(self):
        # R$ 70: lucro = 70 - 8,40 - 8,40 - 50 = 3,20 -> 4,6% (< 15% do produto)
        self.marcar(preco=70.0)
        self.modo("automatico")
        self.rodar()
        i = self.item()
        self.assertEqual(i["situacao"], "revisar")
        self.assertIn("margem", i["motivo"].lower())
        self.assertEqual(self.posts, [])

    def test_sem_custo_nao_renova(self):
        S._precificador_produtos_ml = lambda: {}
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.item()["situacao"], "revisar")
        self.assertEqual(self.posts, [])

    def test_fora_do_piso_do_ml_nao_renova(self):
        self.promos["MLB1"] = [promo_propria("candidate", piso=95.0)]
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertIn("mercado livre", self.item()["motivo"].lower())
        self.assertEqual(self.posts, [])

    def test_preco_do_anuncio_abaixo_da_promocao_nao_renova(self):
        self.itens["MLB1"]["preco"] = 85.0
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.item()["situacao"], "revisar")
        self.assertEqual(self.posts, [])

    def test_em_campanha_do_ml_espera_e_avisa_se_a_margem_caiu(self):
        # campanha a R$ 65: margem 0,4% (< 15%) -> só aviso, não sai da campanha
        self.promos["MLB1"] = [promo_propria("candidate"),
                               {"type": "SMART", "status": "started", "price": 65.0, "name": "Campanha do ML",
                                "finish_date": "2026-10-31T00:00:00"}]
        self.marcar()
        self.modo("automatico")
        self.rodar()
        i = self.item()
        self.assertEqual(i["situacao"], "em_campanha")
        self.assertTrue(i["aviso"])
        self.assertEqual(self.posts, [])

    def test_estoque_acabando_nao_renova(self):
        original = S._promo_ruptura_por_anuncio
        S._promo_ruptura_por_anuncio = lambda: {"MLB1": {"grupo": "critico", "cobertura_dias": 3.0}}
        try:
            self.marcar()
            self.modo("automatico")
            self.rodar()
            self.assertEqual(self.item()["situacao"], "estoque")
            self.assertEqual(self.posts, [])
        finally:
            S._promo_ruptura_por_anuncio = original

    def test_limite_por_dia(self):
        self.marcar("MLB1")
        self.marcar("MLB2")
        S.promo_auto_config_salvar({"modo_renovacao": "automatico", "limite_dia": 1}, "admin_teste")
        r = self.rodar()
        self.assertEqual((r["renovadas"], len(self.posts)), (1, 1))

    def test_historico_registra_quem_e_por_que(self):
        self.marcar()
        self.modo("automatico")
        self.rodar()
        log = S.promo_renovacao_payload(hoje=HOJE)["historico"]
        self.assertEqual(log[0]["acao"], "renovada")
        self.assertEqual(log[0]["por"], "sistema")


class SkuKitEMargem(Base):
    """Correções 1 e 2: sem vínculo usa o SKU do anúncio; kit tem custo pelos
    componentes; a régua é a margem mínima da PROMOÇÃO (não a margem-alvo)."""

    def test_sem_vinculo_usa_o_sku_do_anuncio(self):
        S._ml_vinculos_mlb_sku = lambda: {}
        self.itens["MLB1"]["sku_ml"] = "1304-SKU1"
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 1, self.item()["motivo"])

    def test_vinculo_manda_mesmo_se_o_anuncio_diz_outra_coisa(self):
        S._ml_vinculos_mlb_sku = lambda: {"MLB1": "SEM-CUSTO"}
        self.itens["MLB1"]["sku_ml"] = "SKU1"
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.item()["situacao"], "revisar")
        self.assertEqual(self.posts, [])

    def _precificador(self, produtos):
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, S.json_dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")

    def test_kit_do_precificador_tem_custo_pelos_componentes_atuais(self):
        S._precificador_produtos_ml = self._orig[3]          # lê o Precificador de verdade
        ml = {"taxaFixa": 0, "comissao": 0.12, "frete": 0, "margem": 0.2}
        self._precificador([
            {"sku": "COMP", "produto": "Peça", "custo": 20.0, "imposto": 0.12, "marketplaces": {"ml": ml}},
            # custo guardado velho (10); os componentes hoje custam 2 x 20 = 40
            {"sku": "KIT2", "produto": "Kit 2 peças", "custo": 10.0, "imposto": 0.12, "isKit": True,
             "kitItems": [{"sku": "COMP", "quantidade": 2, "custoUnitario": 5.0}], "marketplaces": {"ml": ml}},
        ])
        prod = S._promo_produtos()["KIT2"]
        self.assertAlmostEqual(prod["custo"], 40.0)
        S._ml_vinculos_mlb_sku = lambda: {"MLB1": "KIT2"}
        self.marcar()
        self.modo("automatico")
        self.rodar()   # R$ 90: 90 - 10,80 - 10,80 - 40 = 28,40 -> 31,6%
        self.assertEqual(len(self.posts), 1, self.item()["motivo"])

    def test_kit_do_cadastro_de_kits_soma_os_componentes(self):
        S._precificador_produtos_ml = self._orig[3]
        ml = {"taxaFixa": 0, "comissao": 0.12, "frete": 0, "margem": 0.2}
        self._precificador([{"sku": "COMP", "produto": "Peça", "custo": 20.0, "imposto": 0.12,
                             "marketplaces": {"ml": ml}}])
        atual, _, _ = S.get_storage("conf_kits")
        S.save_storage("conf_kits", atual, S.json_dumps([{"sku_kit": "KITC", "ativo": True,
                       "componentes": [{"sku": "COMP", "quantidade": 3}]}]), False,
                       actor="teste", actor_role="admin")
        self.assertAlmostEqual(S._promo_produtos()["KITC"]["custo"], 60.0)

    def test_kit_com_componente_sem_custo_fica_sem_custo(self):
        S._precificador_produtos_ml = self._orig[3]
        ml = {"taxaFixa": 0, "comissao": 0.12, "frete": 0, "margem": 0.2}
        self._precificador([
            {"sku": "KIT2", "produto": "Kit", "custo": 0, "imposto": 0.12, "isKit": True,
             "kitItems": [{"sku": "NAOEXISTE", "quantidade": 2}], "marketplaces": {"ml": ml}}])
        self.assertNotIn("KIT2", S._promo_produtos())

    def test_margem_minima_e_a_da_promocao_e_nao_a_alvo(self):
        # R$ 77: 77 - 9,24 - 9,24 - 50 = 8,52 -> 11,1%: abaixo da alvo (15%), acima da mínima (10%)
        self.marcar(preco=77.0)
        self.modo("automatico")
        self.rodar()
        self.assertEqual(len(self.posts), 1, self.item()["motivo"])
        S.promo_auto_config_salvar({"margem_min_promo": 12}, "admin_teste")
        self.promos["MLB1"] = [promo_propria("candidate")]
        with S.DB_LOCK:
            S.DB.execute("UPDATE promo_renovacao_v1873 SET renovado_dia=''")
            S.DB.commit()
        self.rodar()
        self.assertIn("12%", self.item()["motivo"])
        self.assertEqual(len(self.posts), 1)


class SemDiaSemDesconto(Base):
    """Melhoria 3: o worker acorda logo depois do vencimento e confere só aquele anúncio."""

    def _ativa(self, mlb, fim):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO promo_renovacao_v1873(mlb,marcado,preco,situacao,fim) "
                         "VALUES(?,1,90,'ativa',?)", (mlb, fim))
            S.DB.commit()

    def test_acorda_um_minuto_depois_do_vencimento(self):
        agora = S.datetime(2026, 10, 19, 23, 50, tzinfo=S.BR_TZ)
        self._ativa("MLB1", "2026-10-20T00:00:00")
        self._ativa("MLB2", "2026-11-20T00:00:00")
        espera, vencendo = S._promo_proxima_espera(agora)
        self.assertAlmostEqual(espera, 11 * 60, delta=1)
        self.assertEqual(vencendo, [])

    def test_depois_do_vencimento_confere_so_quem_venceu(self):
        agora = S.datetime(2026, 10, 20, 0, 2, tzinfo=S.BR_TZ)
        self._ativa("MLB1", "2026-10-20T00:00:00")
        self._ativa("MLB2", "2026-11-20T00:00:00")
        espera, vencendo = S._promo_proxima_espera(agora)
        self.assertEqual(vencendo, ["MLB1"])
        self.assertLessEqual(espera, 180)

    def test_rodada_parcial_so_chama_o_ml_para_os_vencidos(self):
        chamados = []
        original = S.ml_promocoes_do_item
        S.ml_promocoes_do_item = lambda mlb: (chamados.append(mlb), original(mlb))[1]
        self.marcar("MLB1")
        self.marcar("MLB2")
        S.promo_renovacao_rodada(hoje=HOJE, pausa=0, apenas=["MLB1"])
        self.assertEqual(chamados, ["MLB1"])

    def test_sem_nada_vencendo_espera_a_hora_cheia(self):
        espera, vencendo = S._promo_proxima_espera(S.datetime(2026, 10, 7, 10, 0, tzinfo=S.BR_TZ))
        self.assertEqual((espera, vencendo), (S.PROMO_RENOVACAO_INTERVALO, []))


class Rotas(Base):

    def test_ligar_o_automatico_exige_senha_e_codigo_do_owner(self):
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/config", {"modo_renovacao": "automatico"})
        self.assertEqual(status, 428, corpo)
        self.assertEqual(S.promo_auto_config()["modo_renovacao"], "simulacao")

    def test_voltar_para_simulacao_nao_exige_codigo(self):
        self.modo("automatico")
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/config", {"modo_renovacao": "simulacao"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(S.promo_auto_config()["modo_renovacao"], "simulacao")

    def test_marcar_e_listar(self):
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar", {"mlb": "mlb1", "ativo": True})
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("GET", "/api/promocoes-auto/renovacao")
        self.assertEqual(status, 200, corpo)
        self.assertEqual([i["mlb"] for i in corpo["itens"] if i["marcado"]], ["MLB1"])
        self.assertEqual(corpo["config"]["modo_renovacao"], "simulacao")

    def test_monitor_do_precificador_entra_na_lista_sem_marcar(self):
        S.save_storage(S.PROMO_DATAS_KEY, None, S.json_dumps(
            {"rows": [{"mlb": "123", "date": "2026-10-08", "manualZero": False, "priceOk": False}]}), False,
            actor="t", actor_role="admin", source_screen="t")
        i = {x["mlb"]: x for x in S.promo_renovacao_payload(hoje=HOJE)["itens"]}
        self.assertFalse(i["MLB123"]["marcado"])
        self.assertEqual(i["MLB123"]["fim"][:10], "2026-10-08")


if __name__ == "__main__":
    unittest.main()
