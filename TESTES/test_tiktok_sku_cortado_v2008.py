"""V20.08 — SKU do vendedor cortado pelo TikTok em 50 caracteres.

Caso real de 30/10/2026: 4 kits chegam com exatamente 50 caracteres
("KIT-2011069/2009004/2011058/2008003/2011068/201104"), o último código pela metade. Com o corte
em cima de um código real, ou com a peça seguinte perdida, o estoque de um kit incompleto iria
para o TikTok. Enquanto ninguém confirmar o código, nada é enviado.
"""

from pathlib import Path
from unittest import mock

from base import S, TesteNoromak
from test_tiktok_extensao_v1981 import _apollo, _produto, _sku

CORTADO = "KIT-2011069/2009004/2011058/2008003/2011068/201104"
COMPLETO = "KIT-2011069/2009004/2011058/2008003/2011068/2011047"
PECAS = ("2011069", "2009004", "2011058", "2008003", "2011068")


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._TIKTOK_APOLLO_CHAMADAS.clear()
        S.tiktok_produtos_da_extensao([_produto(skus=[_sku("S1", CORTADO, 3)])], completo=True)

    def _item(self):
        return S.tiktok_estoque_itens()[0]

    def _ler(self, codigo, disp=9):
        with mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(disp)):
            S.tiktok_apollo_ler(codigo)


class Trava(Base):

    def test_sku_de_50_caracteres_nao_envia_mesmo_com_tudo_lido(self):
        self.assertEqual(len(CORTADO), S.TIKTOK_SKU_LIMITE)
        for codigo in PECAS + ("201104",):   # o trecho cortado é também um código real
            self._ler(codigo)
        item = self._item()
        self.assertEqual(item["situacao"], "sku_cortado")
        self.assertIsNone(item["desejado"])
        self.assertIn("50", item["motivo"])

    def test_nao_entra_no_envio(self):
        with mock.patch.object(S, "tiktok_api") as api:
            S._tiktok_config_gravar("modo_estoque", "lote", "t")
            r = S.tiktok_estoque_enviar("t")
        api.assert_not_called()
        self.assertEqual(r["enviados"], 0)

    def test_sku_curto_segue_normal(self):
        S.tiktok_produtos_da_extensao([_produto(skus=[_sku("S2", "KIT-2011069/2009004", 3)])], completo=True)
        self._ler("2011069")
        self._ler("2009004")
        self.assertEqual(self._item()["situacao"], "enviar")

    def test_vinculo_a_mao_libera(self):
        S.tiktok_vinculo_definir("S1", COMPLETO, "dono")
        for codigo in PECAS + ("2011047",):
            self._ler(codigo)
        item = self._item()
        self.assertEqual(item["situacao"], "enviar")
        self.assertEqual(item["desejado"], 9)

    def test_rodizio_nao_le_o_apollo_do_sku_cortado(self):
        with mock.patch.object(S, "crm_stock_lookup") as leitura:
            self.assertIsNone(S.tiktok_apollo_ler_proximo())
        leitura.assert_not_called()

    def test_rodizio_le_depois_do_vinculo(self):
        S.tiktok_vinculo_definir("S1", "2011047", "dono")
        with mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(1)) as leitura:
            S.tiktok_apollo_ler_proximo()
        leitura.assert_called_once()


class Sugestao(Base):

    def _sugestoes(self, conhecidos):
        with mock.patch.object(S, "_separacao_codigos_conhecidos", return_value=set(conhecidos)):
            return S.tiktok_estoque_payload()["itens"][0]["sugestoes"]

    def test_um_codigo_comeca_com_o_trecho(self):
        self.assertEqual(self._sugestoes({"2011047", "2011069", "999"}), [COMPLETO])

    def test_varios_codigos_viram_varias_opcoes(self):
        self.assertEqual(self._sugestoes({"2011047", "2011048", "201104"}),
                         [CORTADO, COMPLETO, COMPLETO[:-1] + "8"])

    def test_nada_no_cadastro_nao_sugere(self):
        self.assertEqual(self._sugestoes({"999"}), [])

    def test_sugestao_nao_grava_nada(self):
        self._sugestoes({"2011047"})
        self.assertEqual(self._item()["vinculo"], "")
        self.assertEqual(self._item()["situacao"], "sku_cortado")

    def test_cadastro_com_erro_nao_derruba_a_tela(self):
        with mock.patch.object(S, "_separacao_codigos_conhecidos", side_effect=RuntimeError("x")):
            self.assertEqual(S.tiktok_estoque_payload()["itens"][0]["sugestoes"], [])

    def test_sem_sku_cortado_nao_le_o_precificador(self):
        S.tiktok_produtos_da_extensao([_produto(skus=[_sku("S2", "2011004", 3)])], completo=True)
        with mock.patch.object(S, "_separacao_codigos_conhecidos") as leitura:
            S.tiktok_estoque_payload()
        leitura.assert_not_called()


class Tela(TesteNoromak):

    def test_tela_mostra_a_situacao_na_aba_sem_vinculo(self):
        html = (Path(S.APP_DIR) / "tiktok-estoque.html").read_text(encoding="utf-8")
        self.assertIn("sku_cortado", html)
        self.assertIn("sugestoes", html)

    def test_selo_do_menu_nao_empurra_o_icone(self):
        css = (Path(S.APP_DIR) / "nk-ui.css").read_text(encoding="utf-8")
        for mode in ("min-width:1001px", "min-width:761px"):
            self.assertIn(mode, css)
        bloco = css[css.index("Selo do menu"):]
        self.assertIn(".nk-perguntas-n", bloco)
        self.assertIn(".nk-tarefas-n", bloco)
        self.assertIn("position:absolute", bloco)
        self.assertIn("margin:0!important", bloco)
