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


class RevisaoAntesDeSubir(unittest.TestCase):
    """Achados da revisão de 28/10: trava por pedido, valores pelo servidor, romaneio já solto
    com valor diferente e atendimento sem valor."""

    def setUp(self):
        reiniciar_banco()

    def _reservado(self):
        return S._faturamento_correcao_ler(S.normalize(PEDIDO)) is not None

    def _executar(self, valor_apollo, **kw):
        porta = _porta(valor_apollo)
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            return S.faturamento_executar(PEDIDO, kw.pop("usuario", "luis"), **kw), porta

    def test_segunda_chamada_durante_a_correcao_e_recusada_sem_ir_ao_apollo(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        self.assertTrue(r1["trava"])
        r2, porta = self._executar(23.31, usuario="ana")
        self.assertFalse(r2["ok"])
        self.assertIn("andamento", r2["error"])
        self.assertEqual(porta.chamadas, [])                 # nem conferiu: não pesa no Apollo

    def test_reserva_nao_vence_sozinha_e_sai_pelo_liberar(self):
        # correção pela metade (peça descomprometida) não pode voltar à fila sozinha
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        with S.DB_LOCK:
            S.DB.execute("UPDATE faturamento_correcao_v1999 SET epoch=epoch-86400")
            S.DB.commit()
        r2, porta = self._executar(25.9)
        self.assertFalse(r2["ok"])
        self.assertEqual(porta.chamadas, [])
        itens = S.faturamento_payload()["itens"]
        self.assertEqual([i["situacao"] for i in itens], ["em_correcao"])
        self.assertTrue(S.faturamento_correcao_liberar(PEDIDO, "luis")["ok"])
        self.assertFalse(self._reservado())
        r3, _ = self._executar(25.9)
        self.assertEqual(r3["etapa"], "condicao", r3)

    def test_com_a_trava_depois_de_corrigido_segue(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        r2, porta = self._executar(25.9, trava=r1["trava"])
        self.assertEqual(r2["etapa"], "condicao", r2)
        self.assertIn("romaneio/soltar", porta.rotas())
        self.assertFalse(self._reservado())

    def test_com_a_trava_e_ainda_errado_nao_pede_outra_correcao(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        r2, porta = self._executar(23.31, trava=r1["trava"])
        self.assertFalse(r2["ok"])
        self.assertNotIn("trava", r2)
        self.assertNotIn("romaneio/soltar", porta.rotas())

    def test_liberar_desfaz_a_trava_sem_ir_ao_apollo(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        r2, porta = self._executar(23.31, trava=r1["trava"], liberar=True)
        self.assertFalse(r2["ok"])
        self.assertEqual(porta.chamadas, [])
        self.assertFalse(self._reservado())

    def test_extensao_le_os_valores_do_servidor_so_com_a_trava(self):
        _pedido(escrow=CUPOM_SHOPEE)
        r1, _ = self._executar(23.31)
        c = S.faturamento_correcao(PEDIDO, r1["trava"])
        self.assertEqual((c["ok"], c["atendimento"], c["valor_certo"], c["valor_atual"]), (True, "790955", 25.9, 23.31))
        self.assertFalse(S.faturamento_correcao(PEDIDO, "outra")["ok"])
        self.assertFalse(S.faturamento_correcao("OUTRO", r1["trava"])["ok"])

    def test_romaneio_ja_solto_com_valor_diferente_nao_vai_ao_caixa(self):
        _pedido(escrow=CUPOM_SHOPEE)
        solto = {"ok": True, "itens": [{"codigo": "0893764272", "requisitada": 1.0, "a_soltar": 0.0}], "total_a_soltar": 0.0}
        porta = PortaFalsa(previa=solto, abertos=[{"atendimento": 790955, "cliente": "THAYNARA CRISTINA  SILVA",
                                                   "valor": 23.31, "aberto_em": "2026-10-28T07:00:00"}])
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis", condicao_ok=True)
        self.assertFalse(r["ok"])
        self.assertNotIn("caixa/enviar", porta.rotas())

    def test_romaneio_ja_solto_sem_valor_nao_vai_ao_caixa(self):
        _pedido(escrow=CUPOM_SHOPEE)
        solto = {"ok": True, "itens": [{"codigo": "0893764272", "requisitada": 1.0, "a_soltar": 0.0}], "total_a_soltar": 0.0}
        porta = PortaFalsa(previa=solto, abertos=[{"atendimento": 790955, "cliente": "THAYNARA CRISTINA  SILVA",
                                                   "aberto_em": "2026-10-28T07:00:00"}])
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis", condicao_ok=True)
        self.assertFalse(r["ok"])
        self.assertNotIn("caixa/enviar", porta.rotas())

    def test_atendimento_sem_valor_fica_em_atencao(self):
        _pedido(escrow=CUPOM_SHOPEE)
        porta = PortaFalsa(abertos=[{"atendimento": 790955, "cliente": "THAYNARA CRISTINA  SILVA",
                                     "aberto_em": "2026-10-28T07:00:00"}])
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_conferir(PEDIDO, "Shopee")
        self.assertEqual(r["situacao"], "atencao")


class Tela(unittest.TestCase):
    def test_lote_pede_a_correcao_a_extensao_e_confere_de_novo(self):
        html = (RAIZ / "APP" / "faturamento.html").read_text(encoding="utf-8")
        self.assertIn('r.etapa==="preco"', html)
        self.assertIn('"preco-corrigir-lote"', html)
        self.assertIn('s==="corrigir_valor"', html)
        self.assertIn("trava:r.trava", html)                 # a tela devolve a senha da correção
        self.assertIn("PARAR", html)                         # correção pela metade para o lote

    def test_rota_da_extensao_so_admin(self):
        from base import Cliente, iniciar_servidor
        reiniciar_banco()
        url = iniciar_servidor()
        status, _ = Cliente(url).pedir("GET", "/api/faturamento/correcao?pedido=X&trava=Y")
        self.assertEqual(status, 401)
        admin = Cliente(url)
        admin.entrar()
        status, d = admin.pedir("GET", "/api/faturamento/correcao?pedido=X&trava=Y")
        self.assertEqual((status, d["ok"]), (200, False))
        status, d = admin.pedir("POST", "/api/faturamento/correcao/liberar", {"pedido": "X"})
        self.assertEqual((status, d["ok"]), (404, False))


if __name__ == "__main__":
    unittest.main()
