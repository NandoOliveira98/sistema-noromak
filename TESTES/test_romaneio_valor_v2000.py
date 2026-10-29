"""V20.00 — o painel do romaneio mostra o valor da venda (caso real 29/10, pedido 2000019214827468).

A extensão Linx Cliente (romaneio-api.js) desenha "Pedido R$ … / Atendimento R$ … ≠" com o
`valor_pedido` da resposta, mas a rota nunca mandava: o romaneio era solto sem ver a diferença
e a correção do valor (Faturamento, V19.99) só vale antes de soltar.
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import S, reiniciar_banco
from test_faturamento_v1943 import PEDIDO, _pedido
from test_expedicao_apollo_v1930 import PortaFalsa


class ValorNoPainel(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def _preparar(self):
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            return S.exp_romaneio_preparar(PEDIDO)

    def test_manda_o_valor_da_venda(self):
        _pedido(valor=30.0)
        r = self._preparar()
        self.assertEqual(r["valor_pedido"], 30.0)
        self.assertTrue(r["candidatos"])

    def test_venda_nao_sincronizada_fica_nula_nunca_zero(self):
        _pedido(valor=None)
        self.assertIsNone(self._preparar()["valor_pedido"])


if __name__ == "__main__":
    unittest.main()
