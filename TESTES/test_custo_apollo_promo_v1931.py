"""V19.31 — Promoções automáticas: a linha usava o custo do Precificador e a gaveta o do Apollo.

Caso real (15/10, MLB2699786590, SKU RS000032): a gaveta trouxe R$ 48,30 do Apollo
(Araçatuba) e mostrou lucro de R$ 17,15; a linha, com o R$ 59,12 guardado no Precificador,
mostrava R$ 6,33. Decisão do dono: vale o custo do Apollo. Custo 0 do Apollo (estoque
zerado) e leitura velha continuam caindo no Precificador.
"""

import json
from datetime import datetime, timedelta, timezone

from base import S, TesteNoromak


class CustoApolloNaPromocao(TesteNoromak):

    def setUp(self):
        super().setUp()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM custo_apollo_v1931")
            S.DB.commit()
        self.precificador([
            {"sku": "RS000032", "produto": "RESERVATORIO", "custo": 59.12, "imposto": 0.12,
             "marketplaces": {"ml": {"comissao": 0.12, "taxaFixa": 21.65, "frete": 0}}},
            {"sku": "PECA-B", "produto": "PECA B", "custo": 10, "imposto": 0.12, "filialCusto": 3,
             "marketplaces": {"ml": {"comissao": 0.12, "taxaFixa": 0, "frete": 0}}},
            {"sku": "KIT-X", "produto": "KIT X", "isKit": True, "custo": 999, "imposto": 0.12,
             "kitItems": [{"sku": "RS000032", "quantidade": 2}],
             "marketplaces": {"ml": {"comissao": 0.12, "taxaFixa": 0, "frete": 0}}},
        ])

    def precificador(self, produtos):
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")

    def test_caso_real_linha_igual_a_gaveta(self):
        S.custo_apollo_registrar("RS000032", [
            {"revenda": 1, "casa": "Araçatuba", "custo_medio": 48.30, "tem_cadastro": True, "erro": None},
            {"revenda": 2, "casa": "Ribeirão", "custo_medio": 0, "tem_cadastro": True, "erro": None}], 48.30)
        prod = S._promo_produtos()["RS000032"]
        self.assertAlmostEqual(prod["custo"], 48.30)
        self.assertEqual(prod["custo_origem"], "apollo")
        self.assertAlmostEqual(prod["custo_precificador"], 59.12)
        self.assertAlmostEqual(S.promo_conta(prod, 114.60)["lucro"], 17.15, places=2)

    def test_sem_apollo_fica_o_precificador(self):
        prod = S._promo_produtos()["RS000032"]
        self.assertAlmostEqual(prod["custo"], 59.12)
        self.assertAlmostEqual(S.promo_conta(prod, 114.60)["lucro"], 6.33, places=2)

    def test_custo_zero_do_apollo_nao_vale(self):
        S.custo_apollo_registrar("RS000032", [{"revenda": 1, "casa": "Araçatuba", "custo_medio": 0,
                                               "tem_cadastro": True, "erro": None}], 0)
        self.assertAlmostEqual(S._promo_produtos()["RS000032"]["custo"], 59.12)

    def test_leitura_velha_nao_vale(self):
        S.custo_apollo_registrar("RS000032", [{"revenda": 1, "casa": "Araçatuba", "custo_medio": 48.30,
                                               "tem_cadastro": True, "erro": None}], 48.30)
        velho = (datetime.now(timezone.utc) - timedelta(days=S.CUSTO_APOLLO_PROMO_DIAS + 1)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("UPDATE custo_apollo_v1931 SET data=?", (velho,))
            S.DB.commit()
        self.assertAlmostEqual(S._promo_produtos()["RS000032"]["custo"], 59.12)

    def test_filial_do_produto_e_reserva(self):
        # filialCusto=3 (Bauru) no Precificador: usa Bauru
        S.custo_apollo_registrar("PECA-B", [
            {"revenda": 1, "casa": "Araçatuba", "custo_medio": 7, "tem_cadastro": True, "erro": None},
            {"revenda": 3, "casa": "Bauru", "custo_medio": 8, "tem_cadastro": True, "erro": None}], 7)
        self.assertAlmostEqual(S._promo_produtos()["PECA-B"]["custo"], 8)
        # Bauru sem cadastro: 1ª filial com custo, como na gaveta
        S.custo_apollo_registrar("PECA-B", [
            {"revenda": 1, "casa": "Araçatuba", "custo_medio": 7, "tem_cadastro": True, "erro": None},
            {"revenda": 3, "casa": "Bauru", "custo_medio": None, "tem_cadastro": False, "erro": None}], 7)
        self.assertAlmostEqual(S._promo_produtos()["PECA-B"]["custo"], 7)

    def test_kit_soma_componentes_com_o_apollo(self):
        S.custo_apollo_registrar("RS000032", [{"revenda": 1, "casa": "Araçatuba", "custo_medio": 48.30,
                                               "tem_cadastro": True, "erro": None}], 48.30)
        self.assertAlmostEqual(S._promo_produtos()["KIT-X"]["custo"], 96.60)

    def test_precificador_nao_e_alterado(self):
        S.custo_apollo_registrar("RS000032", [{"revenda": 1, "casa": "Araçatuba", "custo_medio": 48.30,
                                               "tem_cadastro": True, "erro": None}], 48.30)
        S._promo_produtos()
        self.assertAlmostEqual(S._precificador_produtos_ml()["RS000032"]["custo"], 59.12)
