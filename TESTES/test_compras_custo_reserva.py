"""V18.07 — Demanda de Compra: peça sem estoque ficava sem custo.

O custo médio do Apollo vai a 0 quando o estoque zera. A peça sumia do valor do
pedido e do complemento até o mínimo justamente quando mais precisava ser
comprada (caso real: 245 peças zeradas, 34 delas vendendo). Agora vale, nesta
ordem: custo atual do Apollo > custo digitado > último custo > 0 já visto ou
custo gravado na venda do ML (o mais recente dos dois).
"""

import json

from base import S, TesteNoromak


class CustoReservaDemanda(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.precificador({"0893556005": 21.4, "816.6087-0": 0, "816.3195-0": 0})
        self.cliente.pedir("POST", "/api/compras/fornecedores", {"nome": "TESTE", "codigos": "0893556005,816.6087-0,816.3195-0"})

    def precificador(self, custos):
        atual, _, _ = S.get_storage("precificador_online_v1")
        produtos = [{"sku": c, "produto": f"PECA {c}", "custo": v, "curva": "A"} for c, v in custos.items()]
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")

    def itens(self):
        status, d = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=TESTE&dias=60")
        self.assertEqual(status, 200, d)
        return {x["codigo"]: x for x in d["itens"]}, d

    def test_custo_atual_do_apollo_vale_e_vira_reserva(self):
        itens, d = self.itens()
        self.assertEqual((itens["0893556005"]["custo"], itens["0893556005"]["custo_origem"]), (21.4, "apollo"))
        # O estoque zerou: o Precificador passa a mostrar 0, mas o último custo continua valendo.
        self.precificador({"0893556005": 0, "816.6087-0": 0, "816.3195-0": 0})
        itens, _ = self.itens()
        self.assertEqual((itens["0893556005"]["custo"], itens["0893556005"]["custo_origem"]), (21.4, "ultimo"),
                         "peça sem estoque perdeu o custo")
        self.assertTrue(itens["0893556005"]["custo_data"])

    def test_custo_da_venda_do_ml_quando_nunca_teve_custo_aqui(self):
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO ml_profitability_snapshot(order_id,sku,unit_cost,cost_source,captured_at) VALUES(?,?,?,?,?)",
                ("2000001", "152000-816.6087-0", 409.05, "Apollo", "2026-09-01T19:42:19+00:00"))
            S.DB.commit()
        itens, d = self.itens()
        self.assertEqual((itens["816.6087-0"]["custo"], itens["816.6087-0"]["custo_origem"]), (409.05, "venda"))
        self.assertIsNone(itens["816.3195-0"]["custo"], "inventou custo para peça sem nenhum histórico")
        self.assertEqual(d["sem_custo"], 1)

    def test_custo_digitado_vale_so_enquanto_o_apollo_esta_sem_custo(self):
        status, corpo = self.cliente.pedir("POST", "/api/compras/custo", {"codigo": "816.3195-0", "custo": "152,52"})
        self.assertEqual(status, 200, corpo)
        itens, _ = self.itens()
        self.assertEqual((itens["816.3195-0"]["custo"], itens["816.3195-0"]["custo_origem"]), (152.52, "manual"))
        # O Apollo voltou a ter custo: o custo real manda sobre o digitado.
        self.precificador({"0893556005": 21.4, "816.6087-0": 0, "816.3195-0": 160.0})
        itens, _ = self.itens()
        self.assertEqual((itens["816.3195-0"]["custo"], itens["816.3195-0"]["custo_origem"]), (160.0, "apollo"))

    def test_apagar_custo_digitado(self):
        self.cliente.pedir("POST", "/api/compras/custo", {"codigo": "816.3195-0", "custo": 99})
        self.cliente.pedir("POST", "/api/compras/custo", {"codigo": "816.3195-0", "custo": ""})
        itens, _ = self.itens()
        self.assertIsNone(itens["816.3195-0"]["custo"])

    def test_nunca_guarda_custo_zero(self):
        S.custos_registrar_auto([("816.3195-0", 0), ("816.3195-0", None), ("", 10)], "teste")
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM custos_conhecidos_v1807").fetchone()[0], 0)

    def test_custo_invalido_da_erro_e_nao_apaga(self):
        self.cliente.pedir("POST", "/api/compras/custo", {"codigo": "816.3195-0", "custo": "1.234,56"})
        status, _ = self.cliente.pedir("POST", "/api/compras/custo", {"codigo": "816.3195-0", "custo": "abc"})
        self.assertEqual(status, 400)
        itens, _ = self.itens()
        self.assertEqual(itens["816.3195-0"]["custo"], 1234.56)
