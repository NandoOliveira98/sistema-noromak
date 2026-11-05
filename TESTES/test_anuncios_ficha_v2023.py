# V20.23: ficha do produto (plataforma de anuncios, fase 1) - origem de cada campo e versao.
import unittest
from unittest import mock

from base import S, TesteNoromak, linha


class FichaTeste(TesteNoromak):
    def test_salvar_e_ler(self):
        S.ficha_salvar("0893117732", {"marca": "Würth", "peso_g": 450}, "luis", None)
        f = S.ficha_ler("0893117732")
        self.assertEqual(f["campos"]["marca"], "Würth")
        self.assertEqual(f["origem"]["marca"], "admin")
        self.assertEqual(f["versao"], 1)

    def test_importacao_nao_passa_por_cima_do_admin(self):
        self.test_salvar_e_ler()
        alterados = S.ficha_importar("0893117732", {"marca": "WURTH", "ean": "4048962065124"}, "anuncio_ml")
        self.assertEqual(alterados, ["ean"])
        f = S.ficha_ler("0893117732")
        self.assertEqual(f["campos"]["marca"], "Würth")
        self.assertEqual(f["origem"]["marca"], "admin")
        self.assertEqual(f["origem"]["ean"], "anuncio_ml")

    def test_importacao_respeita_ordem(self):
        S.ficha_importar("A1", {"peso_g": 500}, "anuncio_ml")
        S.ficha_importar("A1", {"peso_g": 450}, "alephee")
        f = S.ficha_ler("A1")
        self.assertEqual((f["campos"]["peso_g"], f["origem"]["peso_g"]), (450, "alephee"))
        S.ficha_importar("A1", {"peso_g": 999}, "apollo")
        self.assertEqual(S.ficha_ler("A1")["campos"]["peso_g"], 450)

    def test_conflito_de_versao(self):
        S.ficha_salvar("B1", {"nome": "x"}, "luis", None)
        S.ficha_salvar("B1", {"nome": "y"}, "luis", 1)
        with self.assertRaises(S.FichaConflito) as ctx:
            S.ficha_salvar("B1", {"nome": "z"}, "luis", 1)
        self.assertEqual(ctx.exception.atual["versao"], 2)

    def test_valor_vazio_nao_vira_zero(self):
        self.assertEqual(S.ficha_importar("C1", {"peso_g": None, "ean": ""}, "anuncio_ml"), [])

    def test_codigo_normalizado(self):
        S.ficha_salvar(" 0893117732 ", {"nome": "n"}, "luis", None)
        self.assertEqual(S.ficha_ler("0893117732")["campos"]["nome"], "n")

    def test_importacao_cria_ficha_e_sobe_versao(self):
        S.ficha_importar("D1", {"nome": "n"}, "apollo")
        self.assertEqual(S.ficha_ler("D1")["versao"], 1)
        S.ficha_importar("D1", {"marca": "m"}, "apollo")
        self.assertEqual(S.ficha_ler("D1")["versao"], 2)
        S.ficha_importar("D1", {"marca": "outra"}, "apollo")  # mesma autoridade: nao muda
        self.assertEqual(S.ficha_ler("D1")["versao"], 2)

    def test_salvar_sem_versao_em_ficha_existente_da_conflito(self):
        S.ficha_salvar("E1", {"nome": "x"}, "luis", None)
        with self.assertRaises(S.FichaConflito):
            S.ficha_salvar("E1", {"nome": "y"}, "outro", None)
        self.assertEqual(S.ficha_ler("E1")["campos"]["nome"], "x")

    def test_medida_zero_ou_invalida_nao_entra(self):
        r = S.ficha_importar("F1", {"peso_g": 0, "altura_cm": -3, "largura_cm": "abc", "comprimento_cm": 12}, "apollo")
        self.assertEqual(r, ["comprimento_cm"])


class EstoqueKitTeste(TesteNoromak):
    def test_kit_de_3(self):
        self.assertEqual(S.kit_da_para_montar([{"sku": "A", "quantidade": 3}], {"A": 24}, {}), 8)

    def test_peca_que_da_menos_manda(self):
        comps = [{"sku": "A", "quantidade": 2}, {"sku": "B", "quantidade": 1}]
        self.assertEqual(S.kit_da_para_montar(comps, {"A": 10, "B": 3}, {}), 3)

    def test_desconta_pendente(self):
        self.assertEqual(S.kit_da_para_montar([{"sku": "A", "quantidade": 3}], {"A": 24}, {"A": 6}), 6)

    def test_sem_leitura_e_nulo(self):
        comps = [{"sku": "A", "quantidade": 3}]
        self.assertIsNone(S.kit_da_para_montar(comps, {"A": None}, {}))
        self.assertIsNone(S.kit_da_para_montar(comps, {}, {}))

    def test_nunca_negativo(self):
        self.assertEqual(S.kit_da_para_montar([{"sku": "A", "quantidade": 1}], {"A": 2}, {"A": 5}), 0)

    def test_pendentes_por_filial(self):
        self.salvar_admin([linha("P1", "KIT2-X", 1, Casa="bauru"), linha("P2", "X", 1)])
        self.assertEqual(S.pendentes_da_filial("bauru"), {S._exp_norm_codigo("X"): 2})
        self.assertEqual(S.pendentes_da_filial("aracatuba"), {S._exp_norm_codigo("X"): 1})

    def test_apollo_null_nao_vira_zero(self):
        resp = {"ok": True, "estoques": [{"revenda": 1, "disponivel": None, "respondeu": False}]}
        with mock.patch.object(S, "crm_stock_lookup", return_value=resp):
            self.assertEqual(S.apollo_disponivel_filial(["A"], "aracatuba"), {"A": None})

    def test_apollo_le_a_revenda_da_filial_e_limita_10(self):
        resp = {"ok": True, "estoques": [{"revenda": 1, "disponivel": 9, "respondeu": True},
                                         {"revenda": 3, "disponivel": 4, "respondeu": True}]}
        with mock.patch.object(S, "crm_stock_lookup", return_value=resp) as m:
            r = S.apollo_disponivel_filial([f"C{i}" for i in range(12)], "bauru")
        self.assertEqual(m.call_count, 10)
        self.assertEqual(r["C0"], 4.0)
        self.assertIsNone(r["C11"])

    def test_apollo_erro_ou_limite_vira_nulo(self):
        with mock.patch.object(S, "crm_stock_lookup", side_effect=S.CrmLimiteAtingido(1)):
            self.assertEqual(S.apollo_disponivel_filial(["A"], "bauru"), {"A": None})


    def test_campo_apagado_pelo_admin_nao_volta_pela_importacao(self):
        # V20.23: apagar e decisao do Admin: a origem fica `admin` sem valor e o puxar nao preenche de novo
        S.ficha_importar("A2", {"marca": "WURTH", "ean": "789"}, "anuncio_ml")
        v = S.ficha_ler("A2")["versao"]
        S.ficha_salvar("A2", {"marca": ""}, "luis", v)
        f = S.ficha_ler("A2")
        self.assertNotIn("marca", f["campos"])
        self.assertEqual(f["origem"]["marca"], "admin")
        self.assertEqual(S.ficha_importar("A2", {"marca": "WURTH", "ean": "789"}, "anuncio_ml"), [])
        self.assertEqual(S.ficha_importar("A2", {"marca": "WURTH"}, "alephee"), [])
        self.assertNotIn("marca", S.ficha_ler("A2")["campos"])
        res = S.anuncio_ficha_payload("A2")
        self.assertEqual((res["ficha"]["campos"].get("marca"), res["ficha"]["origem"]["marca"]), (None, "admin"))
        # o Admin digitando de novo vale
        S.ficha_salvar("A2", {"marca": "Würth"}, "luis", S.ficha_ler("A2")["versao"])
        f = S.ficha_ler("A2")
        self.assertEqual((f["campos"]["marca"], f["origem"]["marca"]), ("Würth", "admin"))

    def test_apollo_recebe_o_codigo_com_pontos(self):
        # V20.23: o Apollo e consultado com o codigo do cadastro, nao com a chave sem pontuacao
        S.kit_salvar("KITPT", "", [{"sku": "6007.003.315.00.8", "quantidade": 2}], "luis", None)
        self.salvar_admin([linha("P1", "6007.003.315.00.8", 1)])
        resp = {"ok": True, "estoques": [{"revenda": 1, "disponivel": 9, "respondeu": True},
                                         {"revenda": 3, "disponivel": 4, "respondeu": True}]}
        with mock.patch.object(S, "crm_stock_lookup", return_value=resp) as m:
            res = S.anuncio_ficha_payload("KITPT", ler_apollo=True)
        self.assertEqual({c.args[0] for c in m.call_args_list}, {"6007.003.315.00.8"})
        chave = S._exp_norm_codigo("6007.003.315.00.8")
        self.assertEqual(res["estoque"]["aracatuba"]["disponivel"], {chave: 9.0})
        self.assertEqual(res["estoque"]["aracatuba"]["pendente"], {chave: 1})
        self.assertEqual(res["estoque"]["aracatuba"]["da_para_montar"], 4)   # (9 - 1) // 2
        self.assertEqual(res["estoque"]["bauru"]["da_para_montar"], 2)       # 4 // 2
        S.ficha_salvar("6007.003.315.00.8", {"marca": "Würth"}, "luis", None)  # a peca so tem ficha depois disto
        with mock.patch.object(S, "crm_stock_lookup", return_value=resp) as m:
            res = S.anuncio_ficha_payload("6007.003.315.00.8", ler_apollo=True)
        self.assertEqual({c.args[0] for c in m.call_args_list}, {"6007.003.315.00.8"})
        self.assertEqual(res["estoque"]["aracatuba"]["da_para_montar"], 8)

if __name__ == "__main__":
    unittest.main()
