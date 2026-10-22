"""V19.73 — Faturamento filtra pelo prazo de despacho (pedido do dono, 22/10).

"As vendas para faturar hoje", não as que caíram hoje: cada item traz o prazo de despacho
(ML: DataLimiteDespachoMercadoLivre; Magalu: PrazoEnvioMagalu). A tela abre em hoje e hoje
inclui os atrasados; Shopee (sem prazo na fila) aparece em qualquer data.
"""
from __future__ import annotations

import unittest

from base import S, gravar_como_admin, linha, reiniciar_banco


class PrazoDeDespacho(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_cada_item_traz_o_prazo(self):
        gravar_como_admin([
            linha("2000000000000001", "A1", Marketplace="MERCADO LIVRE", DataLimiteDespachoMercadoLivre="2026-10-22"),
            linha("1500000000000002", "A2", Marketplace="MAGALU", PrazoEnvioMagalu="2026-10-23T18:00:00Z"),
            linha("261022ABCDEF", "A3", Marketplace="SHOPEE")])
        prazos = {i["pedido"]: i["prazo"] for i in S.faturamento_payload()["itens"]}
        self.assertEqual(prazos, {"2000000000000001": "2026-10-22", "1500000000000002": "2026-10-23",
                                  "261022ABCDEF": ""})

    def test_tela_filtra_pelo_prazo_e_hoje_inclui_atrasados(self):
        html = (S.APP_DIR / "faturamento.html").read_text(encoding="utf-8")
        self.assertIn('id="filtroDia"', html)
        self.assertIn("!i.prazo||(h?i.prazo<=f:i.prazo===f)", html)


if __name__ == "__main__":
    unittest.main()
