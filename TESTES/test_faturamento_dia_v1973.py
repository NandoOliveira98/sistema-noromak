"""V19.73 — Faturamento filtra pelo prazo de despacho (pedido do dono, 22/10).

"As vendas para faturar hoje", não as que caíram hoje: cada item traz o prazo de despacho
(ML: DataLimiteDespachoMercadoLivre; Magalu: PrazoEnvioMagalu). A tela abre em hoje e hoje
inclui os atrasados; Shopee (sem prazo na fila) aparece em qualquer data.
"""
from __future__ import annotations

import unittest
from unittest import mock

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

    def test_prazo_da_magalu_em_utc_vira_dia_de_brasilia(self):
        # 23/10 02:59 UTC = 22/10 23:59 em Brasília: o pedido sai no dia 22.
        gravar_como_admin([linha("1500000000000009", "A9", Marketplace="MAGALU",
                                 PrazoEnvioMagalu="2026-10-23T02:59:59Z")])
        self.assertEqual(S.faturamento_payload()["itens"][0]["prazo"], "2026-10-22")

    def test_fila_lida_uma_vez_por_tela(self):
        gravar_como_admin([linha("2000000000000001", "A1", Marketplace="MERCADO LIVRE")])
        real = S.get_storage
        chamadas = []
        with mock.patch.object(S, "get_storage", side_effect=lambda *a, **k: chamadas.append(a) or real(*a, **k)):
            S.faturamento_payload()
        self.assertEqual(len([a for a in chamadas if a and a[0] == "conf_pedidos"]), 1)

    def test_tela_filtra_pelo_prazo_e_hoje_inclui_atrasados(self):
        html = (S.APP_DIR / "faturamento.html").read_text(encoding="utf-8")
        self.assertIn('id="filtroDia"', html)
        self.assertIn("!i.prazo||(h?i.prazo<=f:i.prazo===f)", html)


if __name__ == "__main__":
    unittest.main()
