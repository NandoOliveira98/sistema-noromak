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
        # V18.77: comissão e frete do anúncio vêm do ML. Por padrão o ML "não responde"
        # (vale o Precificador); os testes de tarifa trocam por self.tarifa_ml(...).
        self._orig_api = S.ml_api_get
        S._ML_TARIFA_CACHE.clear()

        def sem_ml(caminho, headers=None):
            raise RuntimeError("ML simulado: sem resposta")
        S.ml_api_get = sem_ml
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
        S.ml_api_get = self._orig_api
        S._ML_TARIFA_CACHE.clear()
        super().tearDown()

    def tarifa_ml(self, comissao_pct=12, frete=15.0):
        def fake(caminho, headers=None):
            if caminho.startswith("/items/"):
                return {"price": 100.0, "listing_type_id": "gold_special", "category_id": "MLB1234", "shipping": {}}
            if caminho.startswith("/sites/MLB/listing_prices"):
                return [{"sale_fee_amount": 0, "sale_fee_details": {"percentage_fee": comissao_pct, "fixed_fee": 0}}]
            if "/shipping_options/free" in caminho:
                return {"coverage": {"all_country": {"list_cost": frete}}}
            raise AssertionError(caminho)
        S.ml_api_get = fake
        S._ML_TARIFA_CACHE.clear()

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
        # vende 20/dia e tem 50 ao vivo: dura 2,5 dias (< 7)
        S._promo_ruptura_por_anuncio = lambda: {"MLB1": {"grupo": "critico", "cobertura_dias": 3.0, "ritmo_dia": 20.0}}
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


class DadoAoVivoManda(Base):
    """V18.74: MLB1050410219 foi reativado (10 un.) depois da checagem da Ruptura das
    08:14 (pausado, 0 un.). A conferência das 09:16 leu o anúncio ao vivo, mas a trava
    usou o dado velho da Ruptura e mostrou "Sem estoque"."""

    def test_estoque_ao_vivo_vence_a_ruptura_antiga(self):
        original = S._promo_ruptura_por_anuncio
        S._promo_ruptura_por_anuncio = lambda: {"MLB1": {"grupo": "zerado", "cobertura_dias": 0.0, "ritmo_dia": 0.5}}
        try:
            self.itens["MLB1"]["estoque"] = 10          # ao vivo: tem estoque
            self.marcar()
            self.modo("automatico")
            self.rodar()
            self.assertEqual(len(self.posts), 1, self.item()["motivo"])
        finally:
            S._promo_ruptura_por_anuncio = original

    def test_estoque_ao_vivo_usa_o_ritmo_da_ruptura(self):
        original = S._promo_ruptura_por_anuncio
        # vende 2/dia e tem 6 ao vivo: dura 3 dias (< 7) -> não renova
        S._promo_ruptura_por_anuncio = lambda: {"MLB1": {"grupo": "ok", "cobertura_dias": 40.0, "ritmo_dia": 2.0}}
        try:
            self.itens["MLB1"]["estoque"] = 6
            self.marcar()
            self.modo("automatico")
            self.rodar()
            self.assertEqual(self.item()["situacao"], "estoque")
        finally:
            S._promo_ruptura_por_anuncio = original

    def test_a_conferencia_atualiza_o_historico_de_estoque(self):
        self.marcar()
        self.rodar()
        with S.DB_LOCK:
            r = S.DB.execute("SELECT estoque_ultimo FROM ml_estoque_dia_v1872 WHERE item_id='MLB1'").fetchone()
        self.assertEqual(r["estoque_ultimo"], 50)

    def test_preco_do_anuncio_e_o_da_conferencia(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,preco) VALUES('MLB1',67.5)")
            S.DB.commit()
        self.marcar()
        self.rodar()
        self.assertEqual(self.item()["preco_anuncio"], 100.0)

    def test_antes_de_conferir_a_data_e_do_monitor_e_vem_marcada(self):
        S.save_storage(S.PROMO_DATAS_KEY, None, S.json_dumps({"rows": [{"mlb": "1", "date": "2026-10-23"}]}), False,
                       actor="t", actor_role="admin", source_screen="t")
        self.marcar()
        i = self.item()
        self.assertEqual((i["situacao"], i["fim_origem"]), ("nao_checado", "monitor"))
        # depois de conferir, sem promoção ativa no ML, a data do monitor não aparece mais
        self.marcar()
        S.promo_auto_config_salvar({"modo_renovacao": "simulacao"}, "t")
        S.promo_auto_config_salvar({"estoque_min_dias": 0}, "t")
        self.itens["MLB1"]["status"] = "paused"
        self.rodar()
        i = self.item()
        self.assertEqual((i["fim"], i["fim_origem"]), ("", "ml"))

    def test_marcar_pela_rota_ja_confere(self):
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar",
                                           {"mlb": "MLB1", "ativo": True, "preco": "90"})
        self.assertEqual(status, 200, corpo)
        import time
        for _ in range(50):
            if self.item()["situacao"] != "nao_checado":
                break
            time.sleep(0.1)
        self.assertEqual(self.item()["situacao"], "pronta")


class RemoverAnuncio(Base):
    """V18.76: anúncio apagado no ML sai da renovação e da lista de vez (inclusive do monitor
    do Precificador). Incluir de novo pelo número desfaz a remoção."""

    def _monitor(self, *mlbs):
        S.save_storage(S.PROMO_DATAS_KEY, None, S.json_dumps(
            {"rows": [{"mlb": m.replace("MLB", ""), "date": "2026-10-20"} for m in mlbs]}), False,
            actor="t", actor_role="admin", source_screen="t")

    def mlbs(self):
        return {i["mlb"] for i in S.promo_renovacao_payload(hoje=HOJE)["itens"]}

    def test_remover_tira_da_lista_mesmo_estando_no_monitor(self):
        self._monitor("MLB1", "MLB7")
        self.marcar()
        r = S.promo_renovacao_remover(["MLB1", "mlb7"], "admin_teste")
        self.assertEqual(r["removidos"], 2)
        self.assertEqual(self.mlbs() & {"MLB1", "MLB7"}, set())
        self.assertEqual(S.promo_renovacao_payload(hoje=HOJE)["removidos"], 2)

    def test_removido_nao_e_conferido_nem_renovado(self):
        self.marcar()
        self.modo("automatico")
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        self.rodar()
        self.assertEqual(self.posts, [])
        r = S.promo_renovacao_rodada(hoje=HOJE, pausa=0, so=["MLB1"], ator="admin_teste")
        self.assertEqual(r["resultados"], [], "anúncio removido não pode ser renovado pelo clique")
        self.assertEqual(self.posts, [])

    def test_incluir_de_novo_desfaz_a_remocao(self):
        self._monitor("MLB1")
        self.marcar()
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        self.marcar()
        self.assertIn("MLB1", self.mlbs())
        self.assertEqual(S.promo_renovacao_payload(hoje=HOJE)["removidos"], 0)

    def test_historico_registra_a_remocao(self):
        self.marcar()
        S.promo_renovacao_remover(["MLB1"], "admin_teste")
        log = S.promo_renovacao_payload(hoje=HOJE)["historico"]
        self.assertEqual((log[0]["mlb"], log[0]["acao"], log[0]["por"]), ("MLB1", "removida", "admin_teste"))

    def test_rota_remover_so_admin_e_valida(self):
        self.marcar()
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/remover", {"mlbs": []})
        self.assertEqual(status, 400, corpo)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/remover", {"mlbs": ["MLB1"]})
        self.assertEqual((status, corpo["removidos"]), (200, 1), corpo)
        self.assertNotIn("MLB1", self.mlbs())


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


class TarifaDoML(Base):
    """V18.77: a renovação usa a comissão e o frete DO ANÚNCIO no ML (teste real de 08/10
    bateu com o cobrado). O Precificador tem 12% e taxa 0 para tudo; o anúncio pode pagar
    frete e outra comissão, e a margem que parecia boa não é."""

    def test_frete_do_ml_derruba_a_margem_e_nao_renova(self):
        # R$ 90 pelo Precificador: 90 - 10,80 - 10,80 - 50 = 18,40 -> 20,4% (renovaria)
        # com o frete de R$ 15 do ML: 3,40 -> 3,8% (abaixo da mínima de 10%)
        self.tarifa_ml(frete=15.0)
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.posts, [])
        i = self.item()
        self.assertEqual(i["situacao"], "revisar")
        self.assertIn("Margem 3,8%", i["motivo"])
        self.assertAlmostEqual(i["margem"], 0.0378, places=3)

    def test_comissao_da_categoria_vem_do_ml(self):
        # comissão 16% e frete 0: 90 - 14,40 - 10,80 - 50 = 14,80 -> 16,4%
        self.tarifa_ml(comissao_pct=16, frete=0.0)
        self.marcar()
        self.rodar()
        self.assertAlmostEqual(self.item()["margem"], 0.1644, places=3)

    def test_ml_sem_resposta_usa_o_precificador_e_avisa(self):
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(len(self.posts), 1)          # 20,4% pelo Precificador
        log = S.promo_renovacao_payload(hoje=HOJE)["historico"]
        self.assertTrue(any("Precificador" in (h["motivo"] or "") for h in log if h["acao"] == "renovada"), log)

    def test_trava_do_minimo_ao_criar_usa_o_frete_do_ml(self):
        # mínimo pelo Precificador: 50 / 0,76 = 65,79; com frete 15: 65 / 0,76 = 85,53
        self.tarifa_ml(frete=15.0)
        with self.assertRaises(ValueError) as c:
            S.ml_promocao_aplicar("MLB1", 80.0, "2026-10-06T00:00:00", "2026-11-05T00:00:00", "t", sku="SKU1")
        self.assertIn("85,53", str(c.exception))
        self.assertEqual(self.posts, [])


class ContaNaLista(Base):
    """V18.77: a gaveta de preço da tela calcula margem/lucro no navegador com a mesma
    conta do servidor — o payload traz custo e taxas de cada anúncio."""

    def test_item_traz_custo_taxas_minimo_e_ideal(self):
        self.marcar()
        c = self.item()["conta"]
        self.assertEqual(c["sku"], "SKU1")
        self.assertEqual((c["custo"], c["comissao"], c["imposto"], c["taxa_fixa"]), (50.0, 0.12, 0.12, 0.0))
        self.assertAlmostEqual(c["minimo"], 65.79)          # 50 / (1 - 0,24)
        self.assertAlmostEqual(c["ideal"], 81.97)           # 50 / (1 - 0,24 - 0,15)
        self.assertAlmostEqual(c["margem_min"], 0.10)

    def test_nao_marcado_tambem_traz_a_conta(self):
        S.promo_renovacao_marcar("MLB2", False, "admin_teste")
        self.assertEqual(self.item("MLB2")["conta"]["sku"], "SKU1")

    def _produtos(self):
        raw, _, _ = S.get_storage("precificador_online_v1")
        return {p["sku"]: p for p in S.json.loads(raw)["products"]}

    def _precificador_real(self):
        S._precificador_produtos_ml = self._orig[3]
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, S.json_dumps({"products": [
            {"_id": "p1", "sku": "SKU1", "produto": "Peça", "custo": 50.0, "imposto": 0.12, "curva": "A",
             "marketplaces": {"ml": {"taxaFixa": 6.0, "comissao": 0.12, "frete": 1.0, "margem": 0.15},
                              "shopee": {"frete": 0, "margem": 0.2}}},
            {"_id": "p2", "sku": "OUTRO", "produto": "Outra", "custo": 10.0, "imposto": 0.12,
             "marketplaces": {"ml": {"taxaFixa": 0, "comissao": 0.12, "frete": 0, "margem": 0.2}}}]}),
            False, actor="teste", actor_role="admin")

    def test_editar_custo_e_taxas_grava_no_precificador_so_daquele_produto(self):
        # V18.77: a gaveta edita custo e taxas e o Precificador passa a usar os mesmos valores
        self._precificador_real()
        r = S.precificador_produto_ml_atualizar("SKU1", {"custo": 60, "taxa_fixa": 10, "comissao": 0.14,
                                                         "imposto": 0.10, "margem": 0.2}, "admin_teste")
        self.assertTrue(r["ok"], r)
        p = self._produtos()
        self.assertEqual(p["SKU1"]["custo"], 60.0)
        self.assertEqual(p["SKU1"]["imposto"], 0.10)
        # "Taxa fixa / frete" é a soma: o frete fica, a taxa fixa absorve a diferença
        self.assertEqual(p["SKU1"]["marketplaces"]["ml"], {"taxaFixa": 9.0, "comissao": 0.14, "frete": 1.0, "margem": 0.2})
        self.assertEqual(p["SKU1"]["marketplaces"]["shopee"], {"frete": 0, "margem": 0.2})   # intocado
        self.assertEqual(p["OUTRO"]["custo"], 10.0)
        c = S._promo_produtos()["SKU1"]
        self.assertEqual((c["custo"], c["taxa_fixa"], c["comissao"]), (60.0, 10.0, 0.14))

    def test_custo_do_apollo_grava_filial_e_custos_por_filial_como_o_precificador(self):
        self._precificador_real()
        self.marcar()
        self.assertEqual(self.item()["conta"]["filial_custo"], 1)          # padrão: Araçatuba
        filiais = [{"revenda": 1, "casa": "Araçatuba", "custo_medio": 55.5, "tem_cadastro": True, "erro": None},
                   {"revenda": 3, "casa": "Bauru", "custo_medio": 61.0, "tem_cadastro": True, "lixo": "x"},
                   {"revenda": "abc"}]
        r = S.precificador_produto_ml_atualizar("SKU1", {"custo": 61.0, "filial_custo": 3, "custos_filiais": filiais}, "a")
        self.assertTrue(r["ok"], r)
        p = self._produtos()["SKU1"]
        self.assertEqual((p["custo"], p["filialCusto"]), (61.0, 3))
        self.assertEqual(p["custosFiliais"], [
            {"revenda": 1, "casa": "Araçatuba", "custo_medio": 55.5, "tem_cadastro": True, "erro": None},
            {"revenda": 3, "casa": "Bauru", "custo_medio": 61.0, "tem_cadastro": True, "erro": None}])
        self.assertEqual(self.item()["conta"]["filial_custo"], 3)

    def test_editar_recusa_valor_impossivel_e_sku_fora_do_precificador(self):
        self._precificador_real()
        self.assertFalse(S.precificador_produto_ml_atualizar("SKU1", {"custo": 0}, "a")["ok"])
        self.assertFalse(S.precificador_produto_ml_atualizar("SKU1", {"comissao": 0.6, "imposto": 0.5}, "a")["ok"])
        self.assertFalse(S.precificador_produto_ml_atualizar("NAOEXISTE", {"custo": 5}, "a")["ok"])
        self.assertEqual(self._produtos()["SKU1"]["custo"], 50.0)

    def test_conta_diz_o_que_pode_ser_editado(self):
        self._precificador_real()
        self.marcar()
        c = self.item()["conta"]
        self.assertTrue(c["editavel"])
        self.assertTrue(c["custo_editavel"])

    def _ml_tarifas(self, falha_frete=False):
        chamadas = []

        def fake(caminho, headers=None):
            chamadas.append(caminho)
            if caminho.startswith("/items/MLB1"):
                return {"id": "MLB1", "price": 100.0, "listing_type_id": "gold_special", "category_id": "MLB263725",
                        "shipping": {"logistic_type": "cross_docking", "free_shipping": False}}
            if caminho.startswith("/sites/MLB/listing_prices"):
                self.assertIn("listing_type_id=gold_special", caminho)
                self.assertIn("category_id=MLB263725", caminho)
                return [{"sale_fee_amount": 3.37, "sale_fee_details": {"percentage_fee": 13, "fixed_fee": 0}}]
            if "/shipping_options/free" in caminho:
                if falha_frete:
                    raise RuntimeError("HTTP 403")
                return {"coverage": {"all_country": {"list_cost": 6.95, "currency_id": "BRL"}}}
            raise AssertionError(caminho)
        S.ml_api_get = fake
        return chamadas

    def test_tarifa_e_frete_do_anuncio_vem_do_ml(self):
        # V18.77: teste real de 08/10 — listing_prices e shipping_options/free batem com o cobrado
        orig = S.ml_api_get
        try:
            chamadas = self._ml_tarifas()
            S._ML_TARIFA_CACHE.clear()
            r = S.ml_tarifa_frete_anuncio("MLB1", 25.9)
            self.assertEqual((r["comissao"], r["taxa_fixa"], r["frete"], r["tarifa"]), (0.13, 0.0, 6.95, 3.37))
            self.assertEqual((r["tipo"], r["categoria"], r["preco"]), ("gold_special", "MLB263725", 25.9))
            n = len(chamadas)
            S.ml_tarifa_frete_anuncio("MLB1", 25.9)          # cache: não chama o ML de novo
            self.assertEqual(len(chamadas), n)
        finally:
            S.ml_api_get = orig

    def test_frete_que_nao_vem_fica_nulo_e_nao_zero(self):
        orig = S.ml_api_get
        try:
            self._ml_tarifas(falha_frete=True)
            S._ML_TARIFA_CACHE.clear()
            r = S.ml_tarifa_frete_anuncio("MLB1", 25.9)
            self.assertEqual(r["comissao"], 0.13)
            self.assertIsNone(r["frete"])
        finally:
            S.ml_api_get = orig

    def test_sem_custo_no_precificador_a_conta_vem_nula(self):
        S._ml_vinculos_mlb_sku = lambda: {}
        S.promo_renovacao_marcar("MLB9", True, "admin_teste", preco=90.0)
        self.assertIsNone(self.item("MLB9")["conta"])


if __name__ == "__main__":
    unittest.main()
