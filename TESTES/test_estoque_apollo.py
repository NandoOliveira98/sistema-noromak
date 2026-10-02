"""V17.94 — estoque disponível do Apollo para a Demanda Full.

Contrato da rota /api/v1/expedicao/estoque (INTEGRACAO_ESTOQUE_APOLLO.md):
- `disponivel: null` / `tem_cadastro: null` é casa que NÃO respondeu. Nunca é zero.
- `ok: false` é peça inexistente (ou ERP recusou): mostrar o erro.
- 20 chamadas/min somadas com /custo: o que passa do limite volta "pending"
  para o navegador tentar de novo, em vez de estourar 429 no Apollo.

O Apollo é um servidor falso local: nenhuma chamada sai da máquina.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from base import S, TesteNoromak

RESPOSTAS = {
    "4045": {
        "ok": True, "codigo": "4045", "descricao": "HS334 VEDADOR", "unidade": "PC",
        "disponivel_total": 34, "casas_sem_resposta": [], "revenda_principal": 1,
        "estoques": [
            {"revenda": 1, "disponivel": 34.0, "localizacao": "ZZ.9.14.A.99", "tem_cadastro": True, "erro": None},
            {"revenda": 2, "disponivel": 0.0, "localizacao": "ZZ.3.1.B.2", "tem_cadastro": True, "erro": None},
            {"revenda": 3, "disponivel": 0.0, "localizacao": "GV.1.4.B.2", "tem_cadastro": False, "erro": None},
        ],
    },
    "PARCIAL": {
        "ok": True, "codigo": "PARCIAL", "descricao": "", "unidade": "PC",
        "disponivel_total": 5, "casas_sem_resposta": [3], "revenda_principal": 1,
        "estoques": [
            {"revenda": 1, "disponivel": 5.0, "localizacao": "", "tem_cadastro": True, "erro": None},
            {"revenda": 2, "disponivel": 0.0, "localizacao": "", "tem_cadastro": False, "erro": None},
            {"revenda": 3, "disponivel": None, "localizacao": "", "tem_cadastro": None, "erro": "timeout"},
        ],
    },
    "NAOEXISTE": {"ok": False, "erro": "Peça NAOEXISTE não encontrada em nenhuma revenda."},
}


class ApolloFalso(BaseHTTPRequestHandler):
    chamadas: list[str] = []

    def do_GET(self):
        url = urlparse(self.path)
        codigo = parse_qs(url.query).get("codigo", [""])[0]
        ApolloFalso.chamadas.append(codigo)
        if url.path != "/api/v1/expedicao/estoque" or self.headers.get("X-Chave-Expedicao") != "chave-teste":
            self.send_response(404); self.end_headers(); return
        corpo = json.dumps(RESPOSTAS.get(codigo, RESPOSTAS["NAOEXISTE"])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass


class EstoqueApollo(TesteNoromak):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.apollo = ThreadingHTTPServer(("127.0.0.1", 0), ApolloFalso)
        threading.Thread(target=cls.apollo.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.apollo.shutdown()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self._orig = (S.CHAVE_CRM, S.CRM_STOCK_API_URL, S.CRM_RATE_LIMIT_PER_MINUTE)
        S.CHAVE_CRM = "chave-teste"
        S.CRM_STOCK_API_URL = f"http://127.0.0.1:{self.apollo.server_address[1]}/api/v1/expedicao/estoque"
        S.CRM_STOCK_CACHE.clear()
        S.CRM_RATE_CALLS.clear()
        ApolloFalso.chamadas = []

    def tearDown(self):
        S.CHAVE_CRM, S.CRM_STOCK_API_URL, S.CRM_RATE_LIMIT_PER_MINUTE = self._orig
        S.CRM_RATE_CALLS.clear()
        super().tearDown()

    def consultar(self, itens, **extra):
        status, corpo = self.cliente.pedir("POST", "/api/full/apollo-stock", {"items": itens, "filial": 0, **extra})
        self.assertEqual(status, 200, corpo)
        return {r["sku"]: r for r in corpo["results"]}

    def test_url_padrao_e_a_rota_de_estoque_nao_a_de_custo(self):
        self.assertTrue(S._crm_stock_url_padrao().endswith("/api/v1/expedicao/estoque"))

    def test_possivel_enviar_limitado_ao_disponivel(self):
        r = self.consultar([{"sku": "4045", "need": 50}])["4045"]
        self.assertTrue(r["stock_known"])
        self.assertEqual(r["available"], 34)
        self.assertEqual(r["can_send"], 34)
        self.assertEqual(r["shortage"], 16)
        self.assertFalse(r["sufficient"])
        r = self.consultar([{"sku": "4045", "need": 12}])["4045"]
        self.assertTrue(r["sufficient"])
        self.assertEqual(r["can_send"], 12)

    def test_casa_sem_resposta_nao_vira_zero(self):
        r = self.consultar([{"sku": "PARCIAL", "need": 10}])["PARCIAL"]
        self.assertTrue(r["stock_known"])
        self.assertTrue(r["partial"], "total com casa sem resposta é piso, não exato")
        self.assertEqual(r["missing_branches"], [3])
        bauru = next(b for b in r["branches"] if b["revenda"] == 3)
        self.assertIsNone(bauru["disponivel"], "casa que não respondeu virou número")
        # Consultando só a casa que não respondeu: desconhecido, nunca zero.
        status, corpo = self.cliente.pedir("POST", "/api/full/apollo-stock",
                                           {"items": [{"sku": "PARCIAL", "need": 10}], "filial": 3})
        r = corpo["results"][0]
        self.assertFalse(r["stock_known"])
        self.assertIsNone(r["available"])

    def test_peca_inexistente_mostra_o_erro_do_apollo(self):
        r = self.consultar([{"sku": "NAOEXISTE", "need": 3}])["NAOEXISTE"]
        self.assertFalse(r["ok"])
        self.assertIn("não encontrada", r["error"])

    def test_cache_nao_repete_chamada(self):
        self.consultar([{"sku": "4045", "need": 1}])
        self.consultar([{"sku": "4045", "need": 1}])
        self.assertEqual(ApolloFalso.chamadas, ["4045"])

    def test_limite_por_minuto_devolve_pending_sem_chamar_o_apollo(self):
        S.CRM_RATE_LIMIT_PER_MINUTE = 1
        res = self.consultar([{"sku": "4045", "need": 1}, {"sku": "PARCIAL", "need": 1}], nao_esperar=True)
        self.assertTrue(res["4045"]["ok"])
        self.assertTrue(res["PARCIAL"]["pending"])
        self.assertGreater(res["PARCIAL"]["retry_after"], 0)
        self.assertEqual(ApolloFalso.chamadas, ["4045"], "passou do limite e chamou o Apollo mesmo assim")


class KitsDemandaFull(TesteNoromak):
    """V17.96: kit2-X vende 2 unidades de X — a Demanda Full precisa saber disso."""

    def gravar_kits(self, kits):
        atual, _, _ = S.get_storage("conf_kits")
        S.save_storage(
            "conf_kits", atual, json.dumps(kits), False,
            actor="admin_teste", actor_role="admin", source_screen="Admin / Kits",
            change_method="Teste automatizado", client_ip="127.0.0.1",
        )

    def kits(self, skus):
        status, corpo = self.cliente.pedir("POST", "/api/full/kits", {"skus": skus})
        self.assertEqual(status, 200, corpo)
        return corpo["kits"]

    def test_prefixo_kit2_vira_duas_unidades(self):
        kits = self.kits(["kit2-0892332041", "157507-0893556005"])
        self.assertEqual(kits["kit2-0892332041"]["componentes"], [{"sku": "0892332041", "quantidade": 2}])
        self.assertNotIn("157507-0893556005", kits, "SKU comum virou kit")

    def test_cadastro_de_kits_tem_prioridade(self):
        self.gravar_kits([{"sku_kit": "KIT-LIMPEZA", "nome": "Kit limpeza", "ativo": True, "componentes": [
            {"sku": "150922-0893764272", "quantidade": 1}, {"sku": "157507-0893556005", "quantidade": 2}]}])
        kits = self.kits(["KIT-LIMPEZA"])
        self.assertEqual(len(kits["KIT-LIMPEZA"]["componentes"]), 2)
        self.assertEqual(kits["KIT-LIMPEZA"]["fonte"], "cadastro")

    def test_sku_comum_nao_herda_composicao_do_kit_com_prefixo(self):
        self.gravar_kits([{"sku_kit": "KIT3-0893270202", "ativo": True,
                           "componentes": [{"sku": "0893270202", "quantidade": 3}]}])
        kits = self.kits(["0893270202", "KIT3-0893270202"])
        self.assertNotIn("0893270202", kits)
        self.assertEqual(kits["KIT3-0893270202"]["componentes"][0]["quantidade"], 3)


if __name__ == "__main__":
    unittest.main()


class AnunciosDemandaFull(TesteNoromak):
    """V18.61: o relatório Full lista os anúncios do produto ("1079290698 | 3615301139").
    A tela mostra o MLB de cada um e qual mais vendeu nos últimos 30 dias, para
    saber qual anúncio usar na remessa."""

    def gravar_venda(self, order_id, anuncio, qty, dias_atras, marketplace="ml"):
        dia = (S.datetime.now(S.BR_TZ) - S.timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (marketplace, order_id, anuncio, "", dia + "T10:00:00", dia, "X", anuncio, "Titulo " + anuncio, qty, 10.0, "paid", dia),
            )
            S.DB.commit()

    def anuncios(self, lista):
        status, corpo = self.cliente.pedir("POST", "/api/full/anuncios", {"anuncios": lista})
        self.assertEqual(status, 200, corpo)
        return corpo["anuncios"]

    def test_soma_vendas_30_dias_por_mlb_aceitando_numero_sem_prefixo(self):
        self.gravar_venda("1", "MLB1079290698", 2, 1)
        self.gravar_venda("2", "MLB1079290698", 3, 10)
        self.gravar_venda("3", "MLB3615301139", 1, 2)
        self.gravar_venda("4", "MLB1079290698", 50, 45)  # fora da janela
        res = self.anuncios(["1079290698", "MLB3615301139", "MLB999"])
        self.assertEqual(res["MLB1079290698"]["unidades"], 5)
        self.assertEqual(res["MLB1079290698"]["pedidos"], 2)
        self.assertEqual(res["MLB3615301139"]["unidades"], 1)
        self.assertEqual(res["MLB999"]["unidades"], 0, "anúncio sem venda precisa voltar zero, não sumir")

    def test_venda_shopee_nao_conta(self):
        self.gravar_venda("5", "MLB1079290698", 7, 1, marketplace="shopee")
        self.assertEqual(self.anuncios(["MLB1079290698"])["MLB1079290698"]["unidades"], 0)
