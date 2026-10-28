"""V19.99 — Fila de faturamento: corrigir o valor do atendimento no Apollo (decisões do dono, 28/10).

A integração Shopee → Apollo grava o que o comprador pagou (preço − cupom da loja − cupom da Shopee
− moedas); o certo é o preço da unidade × quantidade (a venda). No ML o kit sai com centavos a mais.
A fila só manda corrigir quando a diferença TEM EXPLICAÇÃO — Shopee: cupons + moedas do escrow
(± R$ 0,05); ML: até R$ 0,10 —; fora disso continua barrando. Quem corrige é a extensão Linx Cliente
(Linx web); o servidor confere de novo antes de soltar o romaneio. Teste real com o dono 28/10:
261028RSB9AC4M, atendimento 790955, 23,31 → 25,90.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock

from base import S, linha, gravar_como_admin, reiniciar_banco
from test_expedicao_apollo_v1930 import PortaFalsa

PEDIDO = "261028RSB9AC4M"
RAIZ = Path(__file__).resolve().parent.parent


def _pedido(marketplace="SHOPEE", valor=25.9, escrow=None):
    gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace=marketplace)])
    S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Thaynara Cristina Silva", linx_cliente="83020",
                            fonte="teste", usuario="teste")
    mp = "shopee" if marketplace == "SHOPEE" else "ml"
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,amount,updated_at) "
                     "VALUES(?,?,'1','2026-10-28','2026-10-28',?, '2026-10-28')", (mp, PEDIDO, valor))
        if escrow is not None:
            S.DB.execute("INSERT INTO shopee_escrow_v1810(order_sn,order_income_json) VALUES(?,?)",
                         (PEDIDO, json.dumps(escrow)))
        S.DB.commit()
    S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "teste", "83020")


def _porta(valor):
    return PortaFalsa(abertos=[{"atendimento": 790955, "cliente": "THAYNARA CRISTINA  SILVA", "valor": valor,
                                "aberto_em": "2026-10-28T07:00:00", "dias_aberto": 0}])


CUPOM_SHOPEE = {"voucher_from_seller": 0, "voucher_from_shopee": 2.59, "coins": 0}


class Classificar(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def _conferir(self, valor_apollo, mp="Shopee"):
        with mock.patch.object(S, "_exp_porta", side_effect=_porta(valor_apollo)):
            return S.faturamento_conferir(PEDIDO, mp)

    def test_shopee_cupom_explica_a_diferenca(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r = self._conferir(23.31)
        self.assertEqual((r["situacao"], r["atendimento"]), ("corrigir_valor", "790955"), r)
        self.assertIn("23,31", r["motivo"])
        self.assertIn("25,90", r["motivo"])

    def test_shopee_cupom_da_loja_e_moedas_tambem_explicam(self):
        _pedido(valor=52.8, escrow={"voucher_from_seller": 1.59, "voucher_from_shopee": 0, "coins": 0.01})
        self.assertEqual(self._conferir(51.2)["situacao"], "corrigir_valor")

    def test_shopee_diferenca_sem_explicacao_continua_barrando(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r = self._conferir(20.0)
        self.assertEqual(r["situacao"], "atencao")
        self.assertIn("Valor diferente", r["motivo"])

    def test_shopee_sem_escrow_nao_adivinha(self):
        _pedido(escrow=None)
        self.assertEqual(self._conferir(23.31)["situacao"], "atencao")

    def test_ml_centavos_do_kit(self):
        _pedido(marketplace="MERCADO LIVRE", valor=132.9)
        self.assertEqual(self._conferir(132.93, "Mercado Livre")["situacao"], "corrigir_valor")

    def test_ml_diferenca_grande_continua_barrando(self):
        _pedido(marketplace="MERCADO LIVRE", valor=132.9)
        self.assertEqual(self._conferir(130.0, "Mercado Livre")["situacao"], "atencao")

    def test_valor_igual_continua_pronto(self):
        _pedido(escrow=CUPOM_SHOPEE)
        self.assertEqual(self._conferir(25.9)["situacao"], "pronto")


class Executar(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_para_na_etapa_preco_sem_soltar_o_romaneio(self):
        _pedido(escrow=CUPOM_SHOPEE)
        porta = _porta(23.31)
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis")
        self.assertEqual((r["ok"], r["etapa"], r["atendimento"]), (True, "preco", "790955"), r)
        self.assertEqual((r["valor_certo"], r["valor_atual"]), (25.9, 23.31))
        self.assertNotIn("romaneio/soltar", porta.rotas())
        self.assertNotIn("caixa/enviar", porta.rotas())

    def test_depois_de_corrigido_segue_o_fluxo_normal(self):
        _pedido(escrow=CUPOM_SHOPEE)
        porta = _porta(25.9)                                  # a extensão já corrigiu no Linx
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis")
        self.assertEqual(r["etapa"], "condicao", r)
        self.assertIn("romaneio/soltar", porta.rotas())

    def test_payload_conta_como_pronto_para_o_lote(self):
        _pedido(escrow=CUPOM_SHOPEE)
        with mock.patch.object(S, "_exp_porta", side_effect=_porta(23.31)):
            S.faturamento_conferir(PEDIDO, "Shopee")
        itens = S.faturamento_payload()["itens"]
        self.assertEqual([(i["pedido"], i["situacao"]) for i in itens], [(PEDIDO, "corrigir_valor")])


class Tela(unittest.TestCase):
    def test_lote_pede_a_correcao_a_extensao_e_confere_de_novo(self):
        html = (RAIZ / "APP" / "faturamento.html").read_text(encoding="utf-8")
        self.assertIn('r.etapa==="preco"', html)
        self.assertIn('"preco-corrigir-lote"', html)
        self.assertIn('s==="corrigir_valor"', html)


if __name__ == "__main__":
    unittest.main()
