"""V20.56 — TikTok junto dos outros canais na tela de Anúncios (dono, 11/11).

"Tem que estar tudo junto onde já tem o Mercado Livre, Shopee e Magalu" e "trazer os anúncios do TikTok, vamos controlar
Bauru por lá". Os painéis de criar anúncio (Shopee, Magalu, TikTok) não somem mais pelo seletor de casa, e a lista ganha o
botão "Puxar anúncios do TikTok", que usa as rotas que já existiam (fonte → API e leitura do catálogo em 2º plano).
"""
import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "APP" / "anuncios.html").read_text(encoding="utf-8")


def _funcao(nome):
    m = re.search(r"function " + nome + r"\(\) \{(.*?)\n\}", HTML, re.S)
    assert m, nome
    return m.group(1)


class PaineisJuntos(unittest.TestCase):
    def test_paineis_de_criar_nao_dependem_da_casa(self):
        for nome in ("desenharShopee", "desenharMagalu", "desenharTiktok"):
            corpo = _funcao(nome)
            self.assertNotIn("CASA", corpo, f"{nome} não pode sumir pelo seletor de casa")
            self.assertIn("if (!F)", corpo)


class PuxarTiktok(unittest.TestCase):
    def test_botao_ao_lado_do_ml(self):
        acoes = _funcao("acoesLista")
        self.assertIn("Puxar anúncios do TikTok", acoes)
        self.assertIn("puxarTiktok()", acoes)

    def test_usa_as_rotas_que_ja_existem(self):
        m = re.search(r"async function puxarTiktok\(\) \{(.*?)\n\}", HTML, re.S)
        self.assertTrue(m)
        corpo = m.group(1)
        self.assertIn("/api/integracoes/tiktok/status", corpo)
        self.assertIn("/api/integracoes/tiktok/fonte", corpo)
        self.assertIn("/api/tiktok-estoque/atualizar", corpo)
        # trocar a fonte é uma decisão: sempre pergunta antes
        self.assertLess(corpo.index("nkConfirmar"), corpo.index("/api/integracoes/tiktok/fonte"))

    def test_revisao_confirmacao_e_andamento(self):
        corpo = re.search(r"async function puxarTiktok\(\) \{(.*?)\n\}", HTML, re.S).group(1)
        # etiqueta automática ligada = recusa antes de trocar a fonte; atendimento ligado = avisado
        self.assertLess(corpo.index("st.etiqueta_ligada"), corpo.index("/api/integracoes/tiktok/fonte"))
        self.assertIn("st.atendimento_ligado", corpo)
        self.assertIn("PEDIDOS", corpo)
        self.assertNotIn("60000", corpo, "sem esperar às cegas")
        acomp = re.search(r"async function acompanharTiktok\(\) \{(.*?)\n\}", HTML, re.S).group(1)
        self.assertIn("/api/tiktok-estoque/catalogo", acomp)
        self.assertIn('CASA === "aracatuba"', acomp)
        self.assertIn("TTC.rodando", _funcao("acoesLista"))

    def test_segunda_revisao(self):
        acomp = re.search(r"async function acompanharTiktok\(\) \{(.*?)\n\}", HTML, re.S).group(1)
        self.assertIn("ids_diferentes", acomp)
        self.assertIn("substituir: true", acomp)
        self.assertLess(acomp.index("nkConfirmar"), acomp.index("substituir: true"), "substituir só com confirmação")
        self.assertNotIn("?? 0", acomp, "sem número não vira 0")
        self.assertIn("TTC.falhas", acomp)
        self.assertIn("15 * 60000", acomp)


if __name__ == "__main__":
    unittest.main()
