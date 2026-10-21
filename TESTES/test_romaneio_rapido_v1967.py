"""V19.67 — painel do romaneio ficava 4–7 s em "Carregando…" (21/10).

As consultas à porta saíam uma depois da outra (~1 s cada). Agora a situação do cliente
e a lista de atendimentos saem juntas (nome do Apollo guardado por código) e as prévias
também. O resultado e o número de consultas não mudam.
"""
import time
import unittest
from unittest import mock

from base import S, reiniciar_banco
from test_expedicao_apollo_v1930 import PEDIDO, PortaFalsa, _pedido_com_cliente

ATRASO = 0.3


class PortaLenta(PortaFalsa):
    def __call__(self, rota, corpo, escrita=False):
        time.sleep(ATRASO)
        return super().__call__(rota, corpo, escrita)


def _tres_atendimentos():
    return [{"atendimento": 787600 + i, "cliente": "THAYNARA CRISTINA  SILVA", "vendedor": {"nome": "LUIS"},
             "valor": 25.9, "aberto_em": "2026-10-14T20:11:21"} for i in range(3)]


class RomaneioRapido(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        _pedido_com_cliente()

    def _preparar(self, porta):
        t = time.monotonic()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        return d, time.monotonic() - t

    def test_previas_saem_juntas(self):
        porta = PortaLenta(abertos=_tres_atendimentos())
        d, seg = self._preparar(porta)
        self.assertEqual(len(d["candidatos"]), 3)
        self.assertEqual([c["atendimento"] for c in d["candidatos"]], ["787600", "787601", "787602"])
        self.assertEqual(porta.rotas().count("romaneio/previa"), 3)
        # situação + atendimentos + 3 prévias juntas = 3 esperas, não 5
        self.assertLess(seg, 4 * ATRASO)

    def test_segunda_abertura_pede_situacao_e_atendimentos_juntos(self):
        self._preparar(PortaFalsa())
        porta = PortaLenta()
        d, seg = self._preparar(porta)
        self.assertEqual(d["situacao"], "pronto")
        self.assertEqual(sorted(porta.rotas()), ["atendimentos/abertos", "cliente/situacao", "romaneio/previa"])
        self.assertLess(seg, 2.7 * ATRASO)

    def test_nome_mudou_no_apollo_refaz_a_lista_com_o_novo(self):
        S._EXP_NOME_APOLLO["83020"] = "NOME ANTIGO"
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["situacao"], "pronto")
        nomes = [c["cliente_nome"] for r, c in porta.chamadas if r == "atendimentos/abertos"]
        self.assertEqual(sorted(nomes), ["NOME ANTIGO", "THAYNARA CRISTINA  SILVA"])
        self.assertEqual(S._EXP_NOME_APOLLO["83020"], "THAYNARA CRISTINA  SILVA")


if __name__ == "__main__":
    unittest.main()
