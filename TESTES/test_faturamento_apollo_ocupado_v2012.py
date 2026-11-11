"""V20.12 — Apollo cheio não é "atenção" (caso real 03/11, Shopee 2610312SXXXGJE).

A porta respondeu 429 ("Muitas chamadas ao Apollo neste minuto") e o pedido foi para
"Precisa de atenção", só conferido de novo 10 min depois. Decisões do dono (03/11): limite
por minuto ou Apollo sem resposta = "aguardando_apollo" (aba Aguardando), conferido de novo
em 2 min, e a rodada para de ir ao Apollo na hora. 404/401 (porta/chave) continuam atenção.
"""
from __future__ import annotations

import io
import time
import unittest
import urllib.error
from unittest import mock

from base import S, gravar_como_admin, linha, reiniciar_banco
from test_expedicao_apollo_v1930 import PortaFalsa

PEDIDOS = ["2000019000000081", "2000019000000082"]


def _pedido(pedido):
    gravar_como_admin([linha(p, "0893764272", Marketplace="MERCADO LIVRE") for p in PEDIDOS])
    S.salvar_cliente_fiscal(pedido, "06278073994", "Thaynara Cristina Silva", linx_cliente="83020",
                            fonte="teste", usuario="teste")
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,amount,updated_at) "
                     "VALUES('ml',?,'1','2026-10-16','2026-10-16',25.9,'2026-10-16')", (pedido,))
        S.DB.commit()
    S._cadastro_auto_registrar(pedido, time.time(), time.time(), 0, "feito", "teste", "83020")


def _http(codigo):
    def abrir(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, codigo, "x", {}, io.BytesIO(b"{}"))
    return abrir


class ApolloOcupado(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        for p in PEDIDOS:
            _pedido(p)

    def _conferir_http(self, codigo):
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=_http(codigo)):
            return S.faturamento_conferir(PEDIDOS[0], "Mercado Livre")

    def test_429_vira_aguardando_apollo_e_nao_atencao(self):
        r = self._conferir_http(429)
        self.assertEqual(r["situacao"], "aguardando_apollo")
        self.assertIn("2 min", r["motivo"])

    def test_sem_resposta_tambem_aguarda(self):
        def caiu(req, timeout=None):
            raise TimeoutError("timed out")
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=caiu):
            r = S.faturamento_conferir(PEDIDOS[0], "Mercado Livre")
        self.assertEqual(r["situacao"], "aguardando_apollo")

    def test_porta_nao_liberada_continua_atencao(self):
        self.assertEqual(self._conferir_http(404)["situacao"], "atencao")
        self.assertEqual(self._conferir_http(401)["situacao"], "atencao")

    def test_rodada_confere_de_novo_em_2_min(self):
        t0 = time.time()
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=_http(429)):
            S.faturamento_rodada(agora=t0)
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.faturamento_rodada(agora=t0 + 60)          # ainda dentro dos 2 min
            situacoes = {i["pedido"]: i["situacao"] for i in S.faturamento_payload()["itens"]}
            self.assertEqual(situacoes[PEDIDOS[0]], "aguardando_apollo")   # não voltou ao Apollo
            self.assertEqual(situacoes[PEDIDOS[1]], "pronto")              # o que ficou de fora foi agora
            S.faturamento_rodada(agora=t0 + 130)
        itens = {i["pedido"]: i["situacao"] for i in S.faturamento_payload()["itens"]}
        self.assertEqual(itens[PEDIDOS[0]], "pronto")

    def test_rodada_para_no_primeiro_limite(self):
        abertos = []

        def abrir(req, timeout=None):
            abertos.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b"{}"))
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=abrir):
            S.faturamento_rodada(agora=time.time())
        n = len(abertos)
        situacoes = {i["pedido"]: i["situacao"] for i in S.faturamento_payload()["itens"]}
        # O primeiro bateu no limite; o segundo nem foi ao Apollo (continua "não conferido").
        self.assertEqual(sorted(situacoes.values()), ["aguardando_apollo", "nao_conferido"])
        self.assertLessEqual(n, 3)

    # Revisão 03/11: porta fora do ar por horas deixava o pedido em Aguardando para sempre.
    def _rodada_429(self, agora):
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"),                 mock.patch.object(S.urllib.request, "urlopen", side_effect=_http(429)):
            return S.faturamento_conferir(PEDIDOS[0], "Mercado Livre", agora)

    def test_30_min_sem_resposta_vira_atencao(self):
        t0 = time.time()
        self.assertEqual(self._rodada_429(t0)["situacao"], "aguardando_apollo")
        self.assertEqual(self._rodada_429(t0 + 1200)["situacao"], "aguardando_apollo")
        r = self._rodada_429(t0 + 1800)
        self.assertEqual(r["situacao"], "atencao")
        self.assertIn("30 min", r["motivo"])
        # continua em atenção nas próximas falhas (não volta a "aguardando")
        self.assertEqual(self._rodada_429(t0 + 2500)["situacao"], "atencao")

    def test_resposta_do_apollo_zera_a_contagem(self):
        t0 = time.time()
        self._rodada_429(t0)
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            self.assertEqual(S.faturamento_conferir(PEDIDOS[0], "Mercado Livre", t0 + 1000)["situacao"], "pronto")
        self.assertEqual(self._rodada_429(t0 + 1900)["situacao"], "aguardando_apollo")

    def test_v2055_rotina_segurada_para_o_painel_nao_vira_atencao(self):
        """revisar-antes-de-subir (11/11): a leitura segurada pela vez do painel (V20.54) não é Apollo fora do ar."""
        t0 = time.time()
        S.exp_porta_uso_zerar()
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), mock.patch.object(S, "EXP_PORTA_ROTINA_LEITURAS", 0), \
                S.exp_porta_rotina():
            self.assertEqual(S.faturamento_conferir(PEDIDOS[0], "Mercado Livre", t0)["situacao"], "aguardando_apollo")
            r = S.faturamento_conferir(PEDIDOS[0], "Mercado Livre", t0 + 2400)
        self.assertEqual(r["situacao"], "aguardando_apollo", r)
        self.assertNotIn("sem resposta há", r["motivo"])

    def test_tela_poe_na_aba_aguardando(self):
        html = open(S.APP_DIR / "faturamento.html", encoding="utf-8").read()
        self.assertTrue('s==="aguardando_apollo"' in html, "aba Aguardando sem o aguardando_apollo")
        self.assertTrue('r.situacao==="aguardando_apollo"' in html, "lote não tenta de novo")


if __name__ == "__main__":
    unittest.main()
