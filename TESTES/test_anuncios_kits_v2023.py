"""V20.23 - Anuncios fase 1: kits lidos e gravados no mesmo Cadastro de Kits (conf_kits)."""

import json

from base import S, TesteNoromak, linha, pedidos


class KitsTeste(TesteNoromak):

    def cadastro_direto(self, kits):
        atual, _, _ = S.get_storage("conf_kits")
        S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                       source_screen="teste", change_method="teste", client_ip="127.0.0.1")

    def lista(self):
        bruto, _, _ = S.get_storage("conf_kits")
        return json.loads(bruto or "[]")

    def test_salvar_kit_novo_e_ler(self):
        S.kit_salvar("KIT32052153", "Texamatic 3L", [{"sku": "81794-32052153", "quantidade": 3}], "luis", None)
        k = S.kit_composicao("KIT32052153")
        self.assertEqual(k["componentes"], [{"sku": "81794-32052153", "quantidade": 3}])
        self.assertEqual(k["fonte"], "cadastro")
        self.assertEqual(S.ficha_ler("KIT32052153")["tipo"], "kit")

    def test_mudar_quantidade_de_1_para_3(self):
        self.cadastro_direto([
            {"sku_kit": "KITA", "nome": "Kit A", "ativo": True, "extra": "fica",
             "componentes": [{"sku": "P1", "quantidade": 1, "obs": "x"}]},
            {"sku_kit": "KITB", "nome": "Kit B", "ativo": True, "campo_novo": 7,
             "componentes": [{"sku": "P2", "quantidade": 2}]},
        ])
        S.kit_salvar("KITA", "Kit A", [{"sku": "P1", "quantidade": 3}], "luis", None)
        self.assertEqual(S.kit_composicao("KITA")["componentes"], [{"sku": "P1", "quantidade": 3}])
        por = {k["sku_kit"]: k for k in self.lista()}
        self.assertEqual(len(por), 2)
        self.assertEqual(por["KITA"]["extra"], "fica")
        self.assertEqual(por["KITB"], {"sku_kit": "KITB", "nome": "Kit B", "ativo": True, "campo_novo": 7,
                                       "componentes": [{"sku": "P2", "quantidade": 2}]})

    def test_kits_da_peca(self):
        S.kit_salvar("KITX", "X", [{"sku": "81794-32052153", "quantidade": 3}], "luis", None)
        S.kit_salvar("KITY", "Y", [{"sku": "81794-32052153", "quantidade": 4}, {"sku": "Z", "quantidade": 1}], "luis", None)
        S.kit_salvar("KITW", "W", [{"sku": "OUTRA", "quantidade": 1}], "luis", None)
        achados = {k["sku_kit"]: k["quantidade_da_peca"] for k in S.kits_da_peca("81794-32052153")}
        self.assertEqual(achados, {"KITX": 3, "KITY": 4})

    def test_prefixo_vira_cadastro(self):
        self.assertEqual(S.kit_composicao("KIT2-ABC")["fonte"], "prefixo")
        S.kit_salvar("KIT2-ABC", "Kit ABC", [{"sku": "ABC", "quantidade": 2}], "luis", None)
        self.assertEqual(S.kit_composicao("KIT2-ABC")["fonte"], "cadastro")

    def test_validacao(self):
        for comps in ([{"sku": "P", "quantidade": 0}], [{"sku": "KITV", "quantidade": 1}], [],
                      [{"sku": "P", "quantidade": 1000}], [{"sku": "P", "quantidade": 1.5}], [{"sku": "", "quantidade": 1}]):
            with self.assertRaises(ValueError):
                S.kit_salvar("KITV", "V", comps, "luis", None)
        with self.assertRaises(ValueError):
            S.kit_salvar("", "V", [{"sku": "P", "quantidade": 1}], "luis", None)

    def test_conflito(self):
        S.kit_salvar("KITC", "C", [{"sku": "P", "quantidade": 1}], "luis", None)
        _, versao, _ = S.get_storage("conf_kits")
        with self.assertRaises(S.FichaConflito):
            S.kit_salvar("KITC", "C", [{"sku": "P", "quantidade": 2}], "luis", versao - 1)
        S.kit_salvar("KITC", "C", [{"sku": "P", "quantidade": 2}], "luis", versao)
        self.assertEqual(S.kit_composicao("KITC")["componentes"][0]["quantidade"], 2)

    def test_ficha_existente_so_troca_tipo(self):
        S.ficha_salvar("KITF", {"nome": "Meu kit"}, "luis", None)
        antes = S.ficha_ler("KITF")
        S.kit_salvar("KITF", "Meu kit", [{"sku": "P", "quantidade": 1}], "luis", None)
        depois = S.ficha_ler("KITF")
        self.assertEqual(depois["tipo"], "kit")
        self.assertEqual(depois["campos"], antes["campos"])
        self.assertEqual(depois["versao"], antes["versao"])

    def test_bip_da_fila_nao_diminui(self):
        S.kit_salvar("KITQ", "Q", [{"sku": "P1", "quantidade": 1}], "luis", None)
        self.salvar_admin([linha("PED-1", "P1", 1, Lido=1), linha("PED-1", "P2", 2, Lido=2)])
        antes = sorted((r["SKU"], r["Lido"]) for r in pedidos())
        S.kit_salvar("KITQ", "Q", [{"sku": "P1", "quantidade": 3}], "luis", None)
        self.assertEqual(sorted((r["SKU"], r["Lido"]) for r in pedidos()), antes)


if __name__ == "__main__":
    import unittest
    unittest.main()
