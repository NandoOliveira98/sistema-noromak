"""V20.11 — correção do valor pelo painel do pedido (dono, 02/11, Shopee 2610314H4FMAE7: 105,69 × 98,29).

A correção só existia na tela Faturamento, e o painel deixava soltar o romaneio com o valor errado;
romaneio solto com valor diferente fica fora da correção. Agora o servidor recusa o romaneio do painel
com valor diferente: a diferença explicada corrige primeiro; a sem explicação pede "conferi".
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import S, reiniciar_banco
from test_faturamento_valor_v1999 import CUPOM_SHOPEE, PEDIDO, _pedido, _porta


class PainelValor(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def porta(self, valor):
        p = _porta(valor)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(S, "_exp_porta", side_effect=p).start()
        return p

    def soltar(self, **kw):
        return S.exp_romaneio_soltar_painel(PEDIDO, "790955", "SHOPEE", "luis", **kw)

    def test_leitura_traz_a_explicacao_da_diferenca(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        d = S.exp_romaneio_painel(PEDIDO)
        self.assertIn("cupom da Shopee", d["candidatos"][0]["valor_explicacao"])
        self.assertIsNone(d["correcao_pendente"])

    def test_valor_igual_solta(self):
        _pedido(escrow=CUPOM_SHOPEE)
        p = self.porta(25.9)
        self.assertTrue(self.soltar()["ok"])
        self.assertIn("romaneio/soltar", p.rotas())

    def test_diferenca_explicada_nao_solta(self):
        _pedido(escrow=CUPOM_SHOPEE)
        p = self.porta(23.31)
        r = self.soltar()
        self.assertEqual((r["ok"], r["situacao"]), (False, "valor_diferente"))
        self.assertTrue(r["explicada"])
        self.assertNotIn("romaneio/soltar", p.rotas())

    def test_diferenca_sem_explicacao_so_solta_com_conferido(self):
        _pedido(escrow=None)
        p = self.porta(23.31)
        r = self.soltar()
        self.assertEqual(r["situacao"], "valor_diferente")
        self.assertFalse(r["explicada"])
        self.assertNotIn("romaneio/soltar", p.rotas())
        self.assertTrue(self.soltar(valor_conferido=True)["ok"])
        self.assertIn("romaneio/soltar", p.rotas())

    def test_diferenca_explicada_nao_solta_nem_com_conferido(self):
        # Revisão de 02/11: Linx fechado virava "Conferi" e o romaneio saía com o valor errado.
        _pedido(escrow=CUPOM_SHOPEE)
        p = self.porta(23.31)
        r = self.soltar(valor_conferido=True)
        self.assertEqual(r["situacao"], "valor_diferente")
        self.assertNotIn("romaneio/soltar", p.rotas())

    def previa(self, valor, **kw):
        volta = {"ok": True, "pode": True, "atendimento": "790955", "cliente": "83020", "valor": valor, "impedimentos": [], "falhas": []}
        with mock.patch.object(S, "exp_caixa_previa", return_value=volta) as m:
            r = S.exp_caixa_previa_painel(PEDIDO, "790955", "83020", "luis", **kw)
        return r, m

    def test_caixa_com_valor_diferente_pede_conferencia(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r, _ = self.previa(23.31)
        self.assertFalse(r["pode"])
        self.assertTrue(r["valor_diferente"])
        self.assertIn("23,31", r["impedimentos"][0])
        self.assertTrue(self.previa(23.31, valor_conferido=True)[0]["pode"])

    def test_caixa_com_valor_certo_passa(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.assertTrue(self.previa(25.9)[0]["pode"])

    def test_caixa_recusa_com_correcao_aberta_sem_ir_ao_apollo(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        S.exp_valor_reservar(PEDIDO, "790955", "luis")
        r, m = self.previa(25.9, valor_conferido=True)
        self.assertEqual(r["situacao"], "em_correcao")
        m.assert_not_called()
        with mock.patch.object(S, "exp_caixa_enviar") as e:
            r = S.exp_caixa_enviar_painel(PEDIDO, "790955", "83020", 25.9, "luis", valor_conferido=True)
        self.assertEqual(r["situacao"], "em_correcao")
        e.assert_not_called()

    def test_envio_ao_caixa_confere_o_valor_visto(self):
        _pedido(escrow=CUPOM_SHOPEE)
        with mock.patch.object(S, "exp_caixa_enviar", return_value={"ok": True}) as e:
            r = S.exp_caixa_enviar_painel(PEDIDO, "790955", "83020", 23.31, "luis")
            self.assertEqual(r["situacao"], "valor_diferente")
            e.assert_not_called()
            self.assertTrue(S.exp_caixa_enviar_painel(PEDIDO, "790955", "83020", 23.31, "luis", valor_conferido=True)["ok"])
            self.assertTrue(S.exp_caixa_enviar_painel(PEDIDO, "790955", "83020", 25.9, "luis")["ok"])

    def test_liberar_solta_a_reserva_de_pedido_fora_da_fila(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        S.exp_valor_reservar(PEDIDO, "790955", "luis")
        S.save_storage("conf_pedidos", [], "admin", force=True)
        self.assertTrue(S.faturamento_correcao_liberar(PEDIDO, "luis")["ok"])
        self.assertIsNone(S._faturamento_correcao_ler(S.normalize(PEDIDO)))

    def test_venda_sem_valor_nao_trava(self):
        _pedido(escrow=None)
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM vendas_v1799")
            S.DB.commit()
        self.porta(23.31)
        self.assertTrue(self.soltar()["ok"])

    def test_reserva_entrega_a_senha_e_a_extensao_le_o_valor_do_servidor(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        r = S.exp_valor_reservar(PEDIDO, "790955", "luis")
        self.assertTrue(r["ok"], r)
        c = S.faturamento_correcao(PEDIDO, r["trava"])
        self.assertEqual((c["atendimento"], c["valor_certo"], c["valor_atual"]), ("790955", 25.9, 23.31))

    def test_sem_explicacao_nao_reserva(self):
        _pedido(escrow=None)
        self.porta(23.31)
        self.assertFalse(S.exp_valor_reservar(PEDIDO, "790955", "luis")["ok"])

    def test_outra_reserva_bloqueia_correcao_e_romaneio(self):
        _pedido(escrow=CUPOM_SHOPEE)
        p = self.porta(23.31)
        self.assertTrue(S.exp_valor_reservar(PEDIDO, "790955", "luis")["ok"])
        self.assertFalse(S.exp_valor_reservar(PEDIDO, "790955", "ana")["ok"])
        r = self.soltar(valor_conferido=True)
        self.assertEqual(r["situacao"], "em_correcao")
        self.assertNotIn("romaneio/soltar", p.rotas())
        self.assertEqual(S.exp_romaneio_painel(PEDIDO)["correcao_pendente"]["usuario"], "luis")

    def test_concluir_exige_a_senha_e_libera(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        r = S.exp_valor_reservar(PEDIDO, "790955", "luis")
        self.assertFalse(S.faturamento_correcao_concluir(PEDIDO, "errada", "luis")["ok"])
        self.assertIsNotNone(S.exp_romaneio_painel(PEDIDO)["correcao_pendente"])
        self.assertTrue(S.faturamento_correcao_concluir(PEDIDO, r["trava"], "luis")["ok"])
        self.assertIsNone(S.exp_romaneio_painel(PEDIDO)["correcao_pendente"])

    def test_lote_respeita_a_reserva_do_painel(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.porta(23.31)
        S.exp_valor_reservar(PEDIDO, "790955", "luis")
        self.assertEqual(S.faturamento_executar(PEDIDO, "ana")["etapa"], "preco")


if __name__ == "__main__":
    unittest.main()
