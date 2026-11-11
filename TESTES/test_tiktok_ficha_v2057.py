"""V20.57 — Bauru igual a Araçatuba na tela de Anúncios (dono, 11/11: "Araçatuba está um padrão e Bauru outro").

- Os anúncios do TikTok criam a ficha do produto como o "Puxar anúncios do ML" (nome = título do anúncio, origem
  anuncio_tiktok, abaixo do ML e do Admin; kit pelo mesmo critério): antes ficavam "sem nome · sem ficha".
- O painel "Estoque automático" aparece com qualquer casa (em Bauru: Bauru e TikTok), mesmo sem anúncio em Automático.
"""
import re
from pathlib import Path

from base import S, TesteNoromak

HTML = (Path(__file__).resolve().parent.parent / "APP" / "anuncios.html").read_text(encoding="utf-8")


def _sku(sku_id, seller_sku, titulo="Desengripante Wurth 300ml"):
    S._tiktok_sku_gravar({sku_id: {"product_id": "P" + sku_id, "titulo": titulo, "status": "ACTIVATE",
                                   "seller_sku": seller_sku, "warehouse_id": "W", "armazens": 1, "qtd": 3}},
                         apagar_ausentes=False)


class FichaDoTiktok(TesteNoromak):
    def _ficha(self, cod):
        return S.ficha_ler(cod) if hasattr(S, "ficha_ler") else S._ficha_ler_sem_lock(cod)

    def test_cria_a_ficha_com_o_titulo(self):
        _sku("1738000000000000001", "2005001")
        f = self._ficha("2005001")
        self.assertIsNotNone(f, "a ficha nasce como no Puxar do ML")
        self.assertEqual(f["campos"].get("nome"), "Desengripante Wurth 300ml")
        self.assertEqual(f["origem"].get("nome"), "anuncio_tiktok")

    def test_nao_passa_por_cima_do_admin_nem_do_ml(self):
        S.ficha_importar("2005002", {"nome": "Nome do ML"}, "anuncio_ml")
        _sku("1738000000000000002", "2005002", titulo="Outro nome")
        self.assertEqual(self._ficha("2005002")["campos"]["nome"], "Nome do ML")

    def test_fichas_que_ja_estavam_espelhadas_ganham_a_ficha(self):
        """As 90 de produção: a linha já está na plataforma e a ficha não existe."""
        _sku("1738000000000000003", "2005003")
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM anuncio_ficha_v2023 WHERE codigo='2005003'")
            S.DB.commit()
        with S.DB_LOCK:
            S._tiktok_anuncios_espelhar()
            S.DB.commit()
        self.assertIsNotNone(self._ficha("2005003"))

    def test_nao_le_o_precificador_com_a_trava_do_banco(self):
        """Revisão (ALTO): o Precificador pega outra trava e relê o banco — lido sob o DB_LOCK podia travar tudo."""
        from unittest import mock
        with mock.patch.object(S, "_separacao_codigos_conhecidos", return_value=set()) as conhecidos, \
                mock.patch.object(S, "precificador_dados", return_value={}) as prec:
            _sku("1738000000000000005", "2005005/2005006")
        conhecidos.assert_not_called()
        prec.assert_not_called()
        self.assertIsNotNone(self._ficha("2005005/2005006"))

    def test_sku_cortado_sem_vinculo_nao_cria_ficha(self):
        cortado = ("2005001/" * 7)[:S.TIKTOK_SKU_LIMITE]
        _sku("1738000000000000006", cortado)
        self.assertIsNone(self._ficha(cortado), "o fim pode ter perdido uma peça: espera o vínculo à mão")

    def test_falha_na_ficha_nao_deixa_ficha_pela_metade(self):
        from unittest import mock
        with mock.patch.object(S, "ficha_importar", side_effect=RuntimeError("falhou")):
            _sku("1738000000000000007", "2005007/2005008")
        self.assertIsNone(self._ficha("2005007/2005008"), "nada de kit vazio sem nome")
        _sku("1738000000000000007", "2005007/2005008")
        self.assertEqual(self._ficha("2005007/2005008")["campos"].get("nome"), "Desengripante Wurth 300ml")

    def test_sem_sku_nao_cria_nada(self):
        _sku("1738000000000000004", "")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM anuncio_ficha_v2023 WHERE codigo=''").fetchone()[0]
        self.assertEqual(n, 0)


class PainelEstoqueSempre(TesteNoromak):
    def test_painel_nao_some_sem_automatico(self):
        m = re.search(r"function desenharPainelEstoque\(\) \{(.*?)\n\}", HTML, re.S)
        corpo = m.group(1)
        self.assertNotIn("d.resumo.automatico || d.resumo.manual", corpo)


if __name__ == "__main__":
    import unittest
    unittest.main()
