"""V19.78 — duas abas do Precificador não podem ficar num pingue-pongue (22/10).

Medido em produção: com outra aba do Precificador aberta, a tela ficava 100% ocupada (31 s de
trabalho a cada 30 s, "Página sem resposta"). O promoLoad() regravava ml_promocoes_datas_v1 no
localStorage com um updatedAt novo a cada carga; isso dispara o evento "storage" na outra aba, cujo
listener chama promoLoad() de novo, que regrava... para sempre. Agora só grava quando a lista mudou.
"""
import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "APP" / "precificador.html").read_text(encoding="utf-8")


def _corpo(funcao: str) -> str:
    ini = HTML.index(f"function {funcao}(")
    prox = re.search(r"\n      function \w+\(", HTML[ini + 10:])
    return HTML[ini: ini + 10 + (prox.start() if prox else 4000)]


class AbasDoPrecificador(unittest.TestCase):

    def test_promoload_so_grava_no_navegador_quando_a_lista_mudou(self):
        corpo = _corpo("promoLoad")
        self.assertIn("promoIgual", corpo)
        grava = corpo.index("localStorage.setItem(PROMO_STORAGE_KEY")
        self.assertIn("if(!promoIgual)", corpo[:grava])

    def test_quem_ouve_outra_aba_nao_grava_o_banco(self):
        # o listener de "storage" das promoções só recarrega e redesenha; nunca chama promoSave
        ini = HTML.index("if(e && e.key===PROMO_STORAGE_KEY)")
        trecho = HTML[ini: ini + 400]
        self.assertNotIn("promoSave", trecho)

    def test_applyremotestate_nao_grava(self):
        corpo = _corpo("applyRemoteState")
        self.assertNotIn("localStorage.setItem", corpo)
        self.assertNotIn("persist(", corpo)


if __name__ == "__main__":
    unittest.main()
