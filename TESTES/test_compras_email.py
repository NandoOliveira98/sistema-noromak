"""V18.14 — "Enviar ao comprador" da Demanda de Compra por e-mail.

O dono pediu (22/09/2026) um botão que manda o pedido de compra montado na tela
para o comprador (um e-mail para tudo). Reaproveita o SMTP do relatório diário.
Este teste valida a config do comprador, o formato do e-mail e os erros claros
(sem comprador / sem SMTP) — sem tocar em rede.
"""

import os
import unittest

from base import S, reiniciar_banco


def setUpModule():
    reiniciar_banco()


class ComprasEmail(unittest.TestCase):
    PEDIDO = {
        "fornecedor": "WURTH", "minimo": 1000, "periodo_dias": 60, "total": 182.60, "atingiu_minimo": False,
        "itens": [
            {"codigo": "0893764272", "descricao": "Higienizador Limpa Ar", "qtd": 10, "custo": 8.26, "subtotal": 82.60},
            {"codigo": "X2", "descricao": "Outro item", "qtd": 5, "custo": 20.0, "subtotal": 100.0},
        ],
    }

    def test_a_comprador_config(self):
        S.compras_comprador_email("comprador@noromak.com.br")
        self.assertEqual(S.compras_comprador_email(), "comprador@noromak.com.br")

    def test_b_formato_html_e_texto(self):
        html = S.compras_pedido_email_html(self.PEDIDO)
        self.assertIn("WURTH", html)
        self.assertIn("Higienizador Limpa Ar", html)
        self.assertIn("faltam", html)  # abaixo do mínimo
        txt = S.compras_pedido_email_texto(self.PEDIDO)
        self.assertIn("0893764272", txt)

    def test_c_sem_comprador_da_erro(self):
        S.compras_comprador_email("")  # limpa
        with self.assertRaises(RuntimeError) as c:
            S.compras_enviar_comprador(self.PEDIDO)
        self.assertIn("comprador", str(c.exception).lower())

    def test_d_sem_smtp_da_erro(self):
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_SENHA"):
            os.environ.pop(k, None)
        S.compras_comprador_email("comprador@noromak.com.br")
        with self.assertRaises(RuntimeError) as c:
            S.compras_enviar_comprador(self.PEDIDO)
        self.assertIn("SMTP", str(c.exception))

    def test_e_limite_transferencia(self):
        # V18.17: mínimo global de transferência, guardado e lido, nunca negativo.
        self.assertEqual(S.compras_limite_transferencia("150,50"), 150.5)
        self.assertEqual(S.compras_limite_transferencia(), 150.5)
        self.assertEqual(S.compras_limite_transferencia("-10"), 0.0)
        self.assertEqual(S.compras_limite_transferencia("abc"), 0.0)


if __name__ == "__main__":
    unittest.main()
