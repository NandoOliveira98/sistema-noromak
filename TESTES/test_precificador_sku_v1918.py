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


class TestTelaSimplesV1919(unittest.TestCase):
    """V19.19 — tela simples: título do produto aberto, resultado no topo, taxas recolhidas,
    uma linha de situação, backup no menu ⋯. Os ids que o código usa continuam existindo."""
    def test_ids_continuam_unicos(self):
        for i in ("sku", "produto", "curva", "custo", "autosave-status", "cost-api-status",
                  "cost-last-change-note", "export-btn", "import-btn", "delete-btn", "comparison-body"):
            self.assertEqual(HTML.count('id="%s"' % i), 1, i)

    def test_resultado_antes_das_taxas(self):
        self.assertLess(HTML.index('class="panel prec-compare-panel"'), HTML.index('class="taxas-v1919"'))
        self.assertIn("function tituloProdutoV1919()", HTML)
        self.assertIn('id="retry-v1919"', HTML)


class TestCadastrarComponenteV1920(unittest.TestCase):
    """V19.20 — componente do kit sem cadastro: botão cadastra com descrição/custo do Apollo
    (ou perguntando) e já vincula; gravação que falha desfaz o cadastro."""
    def test_atalho_e_regras(self):
        self.assertIn("kit-cadastrar-v1920", HTML)
        self.assertIn("async function cadastrarComponenteV1920(index, botao)", HTML)
        corpo = HTML[HTML.index("async function cadastrarComponenteV1920"):HTML.index("function selectKitProduct(index, productId)")]
        self.assertIn("findExactProduct(sku)", corpo)          # já existe: só vincula
        self.assertIn("state.products.shift();", corpo)        # gravação falhou: desfaz
        self.assertIn("selectKitProduct(index, produto._id)", corpo)
