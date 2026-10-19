"""V19.55 — Promoções automáticas da Magalu: a mesma régua da Shopee, com as taxas da Magalu do
Precificador e o formato REAL lido em produção em 18/10 (promoção, SKUs da promoção, preço de/por).

A Magalu é simulada: nada chama a API de verdade.
"""

import json
from datetime import datetime, timezone
from unittest import mock

from base import S, TesteNoromak

AGORA = int(datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc).timestamp())
DIA = 86400
# custo 20, imposto 12%; Magalu do Precificador: comissão 15%, taxa fixa R$ 5, frete 0
PROD = {"sku": "JFO0211", "produto": "Filtro", "curva": "A", "custo": 20.0, "imposto": 0.12,
        "comissao": 0.12, "taxa_fixa": 0.0, "margem_alvo": 0.2}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class MagaluFalsa:
    def __init__(self):
        self.precos = {"JFO0211": 5000, "SEMTAXA": 5000}       # centavos (list_price = price)
        self.promos = {}                                      # id -> {promo, skus{sku: centavos}}
        self.enviados = []
        self.catalogo = [{"sku": "JFO0211", "title": "Filtro de óleo", "status": "ACTIVE"},
                         {"sku": "SEMTAXA", "title": "Sem taxa", "status": "ACTIVE"},
                         {"sku": "BLOQ", "title": "Bloqueado", "status": "BLOCKED"}]
        self.recusar = ""

    def promo(self, pid, ini, fim, skus, origem="self_service", nome="Manual"):
        self.promos[pid] = {"promo": {"id": pid, "name": nome, "type": {"id": "absolute_discount"}, "origin": origem,
                                      "status": "active", "validity": {"starts_at": _iso(ini), "ends_at": _iso(fim)}},
                            "skus": dict(skus)}

    def get(self, path, params=None):
        if path == "/seller/v1/promotions":
            return {"results": [v["promo"] for v in self.promos.values()], "meta": {"page": {"count": len(self.promos)}}}
        if path.startswith("/seller/v1/promotions/") and path.endswith("/skus"):
            pid = path.split("/")[4]
            return {"results": [{"sku": k, "status": "active", "price": {"original": {"value": self.precos.get(k, 0), "normalizer": 100},
                                                                         "promotional": {"value": v, "normalizer": 100}}}
                                for k, v in self.promos[pid]["skus"].items()]}
        if path.startswith("/seller/v1/portfolios/prices/"):
            sku = path.rsplit("/", 1)[-1]
            v = self.precos[sku]
            return {"results": [{"list_price": v, "price": v, "normalizer": 100, "channel": {"id": "CANAL"}}]}
        if path == "/seller/v1/portfolios/skus":
            return {"results": self.catalogo if not (params or {}).get("_offset") else []}
        raise AssertionError("GET não simulado: " + path)

    def enviar(self, metodo, caminho, corpo=None):
        self.enviados.append((metodo, caminho, corpo))
        if metodo == "POST" and caminho == "/seller/v1/promotions":
            pid = f"N{len(self.promos) + 1}"
            ini, fim = (S._magalu_ts(corpo["validity"][k]) for k in ("starts_at", "ends_at"))
            self.promo(pid, ini, fim, {}, nome=corpo["name"])
            return {"id": pid}
        if metodo == "PUT":
            if self.recusar:
                raise RuntimeError("Magalu HTTP 422: " + self.recusar)
            pid, sku = caminho.split("/")[4], caminho.split("/")[6]
            self.promos[pid]["skus"][sku] = corpo["price"]["promotional"]["value"]
            return {}
        if metodo == "POST" and caminho.endswith("/apply"):
            return {"status_http": 202}
        raise AssertionError("envio não simulado: " + caminho)


def _gravar_precificador(produtos):
    atual, _, _ = S.get_storage("precificador_online_v1")
    S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False, actor="admin_teste",
                   actor_role="admin", source_screen="Teste", change_method="Teste automatizado", client_ip="127.0.0.1")


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.mg = MagaluFalsa()
        self._p = [mock.patch.object(S, "magalu_api_get", side_effect=self.mg.get),
                   mock.patch.object(S, "_magalu_http_enviar", side_effect=self.mg.enviar),
                   mock.patch.object(S, "magalu_access_token", return_value="t"),
                   mock.patch.object(S, "_precificador_produtos_ml", return_value={
                       "JFO0211": dict(PROD), "SEMTAXA": dict(PROD, sku="SEMTAXA")})]
        for p in self._p:
            p.start()
        _gravar_precificador([
            {"sku": "JFO0211", "custo": 20, "imposto": 0.12,
             "marketplaces": {"magalu": {"comissao": 0.15, "taxaFixa": 5, "frete": 0, "margem": 0.2}}},
            {"sku": "SEMTAXA", "custo": 20, "imposto": 0.12,
             "marketplaces": {"magalu": {"comissao": 0, "taxaFixa": 0, "frete": 0, "margem": 0.2}}}])
        S.magalu_promo_buscar_novos("admin_teste")

    def tearDown(self):
        import threading
        for t in threading.enumerate():
            if t.name.startswith("magalu-promo"):
                t.join(10)
        for p in self._p:
            p.stop()
        super().tearDown()

    def marcar(self, sku="JFO0211", preco=None):
        S.magalu_promo_marcar(sku, True, "admin_teste")
        if preco:
            S.magalu_promo_precos(sku, {"0": preco}, "admin_teste")

    def modo(self, m):
        S.magalu_promo_config_salvar({"modo_renovacao": m}, "admin_teste")

    def rodar(self, agora=AGORA, **kw):
        return S.magalu_promo_rodada(agora_ts=agora, **kw)

    def item(self, sku="JFO0211"):
        return {i["item_id"]: i for i in S.magalu_promo_payload(agora_ts=AGORA)["itens"]}[sku]

    def criacoes(self):
        return [e for e in self.mg.enviados if e[0] == "POST" and e[1] == "/seller/v1/promotions"]


class Renovacao(Base):

    def test_conta_com_as_taxas_da_magalu(self):
        tx = S._magalu_taxas_precificador()["JFO0211"]
        c = S.magalu_promo_conta(PROD, 45.0, tx)
        # 45 − 6,75 − 5 − 5,40 − 20 = 7,85
        self.assertEqual(c["lucro"], 7.85)

    def test_comissao_zerada_conta_como_sem_taxa(self):
        self.assertNotIn("SEMTAXA", S._magalu_taxas_precificador())
        self.marcar("SEMTAXA", 45.0)
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        i = self.item("SEMTAXA")
        self.assertEqual(i["situacao"], "revisar")
        self.assertIn("taxas da Magalu", i["motivo"])

    def test_vencendo_cria_a_proxima_no_fim_da_atual_com_o_mesmo_preco(self):
        fim = AGORA + DIA
        self.mg.promo("P1", AGORA - 10 * DIA, fim, {"JFO0211": 4500})
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 1, r)
        corpo = self.criacoes()[0][2]
        self.assertEqual(corpo["type"], {"id": "absolute_discount"})
        self.assertEqual(corpo["scope"], "seller_choice")
        self.assertEqual(S._magalu_ts(corpo["validity"]["starts_at"]), fim)
        self.assertEqual(S._magalu_ts(corpo["validity"]["ends_at"]), fim + 30 * DIA)
        put = [e for e in self.mg.enviados if e[0] == "PUT"][0]
        self.assertEqual(put[1], "/seller/v1/promotions/N2/skus/JFO0211")
        self.assertEqual(put[2]["price"]["promotional"]["value"], 4500)
        self.assertTrue(any(e[1].endswith("/apply") for e in self.mg.enviados))
        self.assertEqual(self.item()["situacao"], "agendada")

    def test_simulacao_e_lote_so_leem(self):
        self.mg.promo("P1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 4500})
        self.marcar()
        for m in ("simulacao", "lote"):
            self.modo(m)
            r = self.rodar()
            self.assertEqual(self.mg.enviados, [], m)
            self.assertEqual(r["renovaria"], 1)

    def test_clique_no_lote_cria(self):
        self.mg.promo("P1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 4500})
        self.marcar()
        self.modo("lote")
        r = self.rodar(so=["JFO0211"], ator="admin_teste")
        self.assertEqual(r["renovadas"], 1, r)

    def test_campanha_da_magalu_segura_a_data_mas_nao_da_o_preco(self):
        """Feirão (origin channel): espera a campanha acabar (como o ML) e o preço dela não vira o nosso."""
        self.mg.promo("C1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 2600}, origem="channel", nome="Feirão")
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        i = self.item()
        self.assertEqual(i["situacao"], "em_campanha")
        self.assertIn("Feirão", i["motivo"])
        self.assertIsNone(i["modelos"][0]["preco"], "o preço da campanha não pode virar o preço da renovação")

    def test_desconto_acima_de_60_por_cento_nao_vai(self):
        self.marcar(preco=15.0)   # 70% de desconto sobre R$ 50 (e abaixo do mínimo)
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        self.assertEqual(self.item()["situacao"], "revisar")

    def test_anuncio_bloqueado_nao_renova(self):
        self.mg.precos["BLOQ"] = 5000
        self.marcar("BLOQ", 45.0)
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.criacoes(), [])
        self.assertIn("pausado", self.item("BLOQ")["motivo"])

    def test_nao_cria_por_cima_de_agendada_nem_mexe_na_manual(self):
        self.mg.promo("P1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 4500})
        self.mg.promo("P2", AGORA + DIA, AGORA + 10 * DIA, {"JFO0211": 4400})
        self.marcar()
        self.modo("automatico")
        self.rodar()
        self.assertEqual(self.mg.enviados, [])
        self.assertEqual(self.item()["situacao"], "agendada")

    def test_recusa_da_magalu_fica_para_revisar_e_tenta_de_novo(self):
        self.mg.promo("P1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 4500})
        self.mg.recusar = "sku em outra promoção"
        self.marcar()
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual((r["renovadas"], r["erros"]), (0, 1))
        self.mg.recusar = ""
        r = self.rodar(agora=AGORA + 3600)
        self.assertEqual(r["renovadas"], 1, r)
        self.assertEqual(len(self.criacoes()), 1, "reaproveita a promoção já criada com o mesmo início")


class Lista(Base):

    def test_buscar_novos_traz_o_catalogo_sem_marcar(self):
        ids = {i["item_id"]: i for i in S.magalu_promo_payload(agora_ts=AGORA)["itens"]}
        self.assertEqual(set(ids), {"JFO0211", "SEMTAXA", "BLOQ"})
        self.assertFalse(ids["JFO0211"]["marcado"])
        S.magalu_promo_remover(["BLOQ"], "admin_teste")
        S.magalu_promo_buscar_novos("admin_teste")
        self.assertNotIn("BLOQ", {i["item_id"] for i in S.magalu_promo_payload(agora_ts=AGORA)["itens"]})

    def test_gaveta_tem_a_conta_com_taxa_fixa(self):
        self.mg.promo("P1", AGORA - 10 * DIA, AGORA + DIA, {"JFO0211": 4500})
        self.marcar()
        self.rodar()
        c = self.item()["calculos"]["0"]
        self.assertEqual((c["comissao"], c["taxa_fixa"], c["custo"]), (0.15, 5.0, 20.0))
        self.assertEqual(c["preco_minimo"], 34.25)   # (20 + 5) / (1 − 0,15 − 0,12)

    def test_modo_da_magalu_separado(self):
        self.modo("lote")
        self.assertEqual(S.shopee_promo_config()["modo_renovacao"], "simulacao")
        self.assertEqual(S.promo_auto_config(S.CASA_PRINCIPAL)["modo_renovacao"], "simulacao")
        self.assertEqual(S.magalu_promo_config()["modo_renovacao"], "lote")

    def test_rotas_e_automatico_pede_owner(self):
        status, corpo = self.cliente.pedir("GET", "/api/promocoes-auto/magalu")
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/magalu/config", {"modo_renovacao": "automatico"})
        self.assertIn(status, (403, 428), corpo)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/magalu/marcar", {"item_id": "JFO0211", "ativo": True})
        self.assertEqual(status, 200, corpo)
