"""V19.18 — no Precificador usavam o campo SKU do produto aberto como busca e trocavam o SKU
do produto salvo. O SKU de produto salvo fica travado; digitar nele vai para a busca de cima;
trocar exige o botão ✎ com confirmação."""
import pathlib, unittest

HTML = (pathlib.Path(__file__).resolve().parent.parent / "APP" / "precificador.html").read_text(encoding="utf-8")


class TestSkuTravado(unittest.TestCase):
    def test_trava_aplicada_ao_abrir_e_ao_salvar(self):
        self.assertIn("function skuTravaV1918()", HTML)
        self.assertIn('setValue("sku", product.sku);\n        setValue("produto", product.produto);\n        skuTravaV1918();', HTML)
        self.assertEqual(HTML.count("state.dirty = false;\n        skuTravaV1918();"), 2)

    def test_digitar_vai_para_a_busca_e_alterar_pede_confirmacao(self):
        self.assertIn("el.search.focus();", HTML)
        self.assertIn('id="sku-alterar-v1918"', HTML)
        self.assertIn("nkConfirmar(texto)", HTML)


if __name__ == "__main__":
    unittest.main()
