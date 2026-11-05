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


if __name__ == "__main__":
    unittest.main()
