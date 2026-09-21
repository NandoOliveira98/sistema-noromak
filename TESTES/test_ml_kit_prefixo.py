"""V18.03 — um kit KIT2-SKU não pode inflar a venda do produto SIMPLES.

Bug real (21/09/2026): venda do Mercado Livre do produto 152285-0893014105
(1 unidade) foi importada com Quantidade 2. Causa: existia o kit
"KIT2-0893014105" (2 x 0893014105) no Cadastro de Kits, e o indexador do
catálogo (_load_kit_catalog) gravava o kit TAMBÉM sob o SKU do componente
"0893014105" (porque _kit_sku_aliases removia o prefixo KIT ao indexar). Assim
o pedido do produto simples casava com o kit e a quantidade dobrava.

O prefixo KIT só pode ser removido na BUSCA (para um pedido KIT2-X achar um
cadastro gravado como X), nunca na INDEXAÇÃO (senão o kit vaza para o X puro).
"""

import json
import unittest

from base import S


def setUpModule():
    S.init_db()


def _seed_kits(kits):
    atual, _, _ = S.get_storage("conf_kits")
    S.save_storage(
        "conf_kits", atual, json.dumps(kits), False,
        actor="admin", actor_role="admin",
        source_screen="Teste", change_method="Teste", client_ip="127.0.0.1",
    )


class KitPrefixoNaoInflaProdutoSimples(unittest.TestCase):
    def setUp(self):
        _seed_kits([{
            "sku_kit": "KIT2-0893014105", "nome": "KIT2-0893014105", "ativo": True,
            "componentes": [{"sku": "0893014105", "quantidade": 2}],
        }])

    def test_produto_simples_nao_vira_kit(self):
        catalog = S._load_kit_catalog()
        item = {"item": {"seller_sku": "152285-0893014105", "title": "Hidrata Couro 500g"}, "quantity": 1}
        linhas = S._expand_ml_order_item(item, catalog)
        self.assertEqual(len(linhas), 1)
        self.assertFalse(
            linhas[0]["is_kit"],
            "REGRESSAO: o produto simples 0893014105 virou kit por causa do KIT2-0893014105",
        )
        self.assertEqual(linhas[0]["quantity"], 1, "REGRESSAO: a quantidade inflou de 1 para 2")
        self.assertEqual(S.normalize(linhas[0]["sku"]), S.normalize("0893014105"))

    def test_o_kit_de_verdade_continua_kit(self):
        catalog = S._load_kit_catalog()
        item = {"item": {"seller_sku": "KIT2-0893014105", "title": "Kit 2 Hidrata"}, "quantity": 1}
        linhas = S._expand_ml_order_item(item, catalog)
        self.assertTrue(linhas[0]["is_kit"], "o kit de verdade tem que continuar sendo kit")
        self.assertEqual(linhas[0]["quantity"], 2, "1 kit x 2 componentes = 2")
        self.assertEqual(S.normalize(linhas[0]["sku"]), S.normalize("0893014105"))


class LimpaResiduoDeKitPrefixo(unittest.TestCase):
    """V18.04 — o reparo de boot limpa o selo de kit que sobrou nas vendas de
    produto simples, sem tocar nos kits de verdade."""

    def _linha_residuo(self):
        return {
            "Pedido": "2000018534495590", "Marketplace": "MERCADO LIVRE", "SKU": "0893014105",
            "Quantidade": "2", "Kit": "SIM", "Nome do Kit": "KIT2-0893014105",
            "Quantidade de Kits": "1", "Quantidade por Kit": "2",
            "SkuKitOrigem": "KIT2-0893014105", "SkuOrigemImportacao": "152285-0893014105",
        }

    def _linha_kit_real(self):
        return {
            "Pedido": "9", "Marketplace": "MERCADO LIVRE", "SKU": "KS001", "Quantidade": "6",
            "Kit": "SIM", "Nome do Kit": "KIT3-KS001", "Quantidade de Kits": "2",
            "Quantidade por Kit": "3", "SkuKitOrigem": "KIT3-KS001", "SkuOrigemImportacao": "KIT3-KS001",
        }

    def test_limpa_o_residuo_e_corrige_a_quantidade(self):
        rows = [self._linha_residuo()]
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows(rows), 1)
        r = rows[0]
        self.assertEqual(r["Quantidade"], "1", "a quantidade tem que voltar para a vendida (1)")
        self.assertEqual(r["Kit"], "")
        self.assertEqual(r["Quantidade por Kit"], "")
        self.assertEqual(r["SkuKitOrigem"], "")

    def test_nao_toca_no_kit_de_verdade(self):
        rows = [self._linha_kit_real()]
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows(rows), 0)
        self.assertEqual(rows[0]["Kit"], "SIM")
        self.assertEqual(rows[0]["Quantidade"], "6")

    def test_kit_real_com_prefixo_numerico_do_ml_nao_e_residuo(self):
        r = self._linha_kit_real()
        r["SkuOrigemImportacao"] = "152285-KIT3-KS001"
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows([r]), 0)

    def test_idempotente(self):
        rows = [self._linha_residuo()]
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows(rows), 1)
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows(rows), 0)


if __name__ == "__main__":
    unittest.main()
