"""V17.89 — etiquetas Shopee pararam de ser geradas desde 10/09/2026.

O worker de etiquetas chama ship_order com package_number. Para pedido UNSPLIT
a Shopee recusa e o sistema deveria refazer SEM o pacote — mas o detector do
erro não reconhecia a mensagem real: a Shopee escreve "pacakge" (com erro de
digitação) no código e usa apóstrofo tipográfico em "don’t". Resultado: 1.064
erros só em 18/09 e nenhuma etiqueta Shopee subiu sozinha.
"""

import unittest

from base import S

# Texto exato gravado em shopee_event_log em 18/09/2026.
MENSAGEM_REAL = (
    "Shopee API: logistics.ship_order_not_need_pacakge_number "
    "Please don’t request with package_number for this unsplit order."
)


class DetectorPacoteDesnecessario(unittest.TestCase):

    def test_reconhece_a_mensagem_real_da_shopee(self):
        self.assertTrue(S._shopee_package_not_needed_error(MENSAGEM_REAL),
                        "REGRESSÃO: o fallback sem package_number não é acionado e a etiqueta nunca sai")

    def test_reconhece_a_grafia_correta_tambem(self):
        self.assertTrue(S._shopee_package_not_needed_error(
            "logistics.ship_order_not_need_package_number"))

    def test_reconhece_so_pelo_texto_com_apostrofo_simples(self):
        self.assertTrue(S._shopee_package_not_needed_error(
            "Please don't request with package_number for this unsplit order."))

    def test_nao_confunde_com_outros_erros(self):
        for outro in ("logistics.package_can_not_print", "error_param: package_number is invalid",
                      "already arranged", ""):
            self.assertFalse(S._shopee_package_not_needed_error(outro), outro)


if __name__ == "__main__":
    unittest.main()
