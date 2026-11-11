"""V20.52 — aviso no Chat Seguro ao tratar devolução com peça ruim (dono, 11/11).

Porta `mensagens/enviar` do NoromakWeb (contrato do Guilherme, seção 7): o Noromak manda o login de quem recebe
(`alejandro`, peças) e o texto; a entrega é deles. Regras daqui:
  - só devolução com alguma peça RUIM (qtd > 0) avisa; só boa não avisa;
  - um aviso por devolução (reabrir e tratar de novo não manda outra vez, se o primeiro chegou);
  - falha do Chat Seguro nunca desfaz o tratar e fica registrada.
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import S, TesteNoromak, linha
from test_devolucoes_v1988 import concluir


class Porta:
    def __init__(self, resposta=None, erro=None):
        self.chamadas = []
        self.resposta = resposta or {"ok": True, "enviadas": 1,
                                     "resultados": [{"para": "alejandro", "ok": True, "situacao": "enviada"}]}
        self.erro = erro

    def __call__(self, rota, corpo, escrita=False):
        self.chamadas.append((rota, dict(corpo)))
        if self.erro:
            raise self.erro
        return self.resposta


class AvisoDevolucao(TesteNoromak):
    def setUp(self) -> None:
        super().setUp()
        S._DEVOLUCAO_EAN_CACHE.clear()
        concluir("P100", [linha("P100", "AAA", 2, Cliente="José da Silva", Descrição="Parafuso"),
                          linha("P100", "BBB", 1, Cliente="José da Silva", Marketplace="SHOPEE")])

    def registrar(self, itens):
        return S.devolucao_registrar({"chave": "P100", "itens": itens}, "joao", "todas")

    def tratar(self, dev_id, porta, documento="NFD 77"):
        d = [x for x in S.devolucoes_lista({}, "todas")["devolucoes"] if x["id"] == dev_id][0]
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "CHAVE_CRM", "chave-teste"):
            return S.devolucao_tratar(dev_id, documento, d["versao"], "chefe", aviso_em_fundo=False)

    def test_peca_ruim_avisa_o_alejandro(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "avariado"},
                            {"sku": "BBB", "qtd": 1, "estado": "bom"}])
        porta = Porta()
        self.assertTrue(self.tratar(r["id"], porta)["ok"])
        self.assertEqual(len(porta.chamadas), 1)
        rota, corpo = porta.chamadas[0]
        self.assertEqual(rota, "mensagens/enviar")
        self.assertEqual(corpo["para"], ["alejandro"])
        self.assertEqual(corpo["operador"], "chefe")
        self.assertIn("P100", corpo["titulo"])
        self.assertIn("AAA", corpo["texto"])
        self.assertIn("Avariado", corpo["texto"])
        self.assertIn("NFD 77", corpo["texto"])
        self.assertNotIn("BBB", corpo["texto"])          # peça boa não entra
        self.assertNotIn("&", corpo["link"])               # a porta recusa link com &
        self.assertTrue(corpo["link"].startswith("http"))
        aviso = S.devolucao_aviso_do(r["id"])
        self.assertEqual(aviso["situacao"], "enviada")

    def test_so_peca_boa_nao_avisa(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"}])
        porta = Porta()
        self.tratar(r["id"], porta)
        self.assertEqual(porta.chamadas, [])
        self.assertIsNone(S.devolucao_aviso_do(r["id"]))

    def test_falha_do_chat_nao_desfaz_o_tratar(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "usado"}])
        porta = Porta(erro=S.PortaErro("porta fora", passageiro=True))
        with mock.patch.object(S, "DEVOLUCAO_AVISO_REPETIR_S", 0):
            self.assertTrue(self.tratar(r["id"], porta)["ok"])
        d = S.devolucoes_lista({"situacao": "tratada"}, "todas")["devolucoes"][0]
        self.assertEqual(d["documento_apollo"], "NFD 77")
        self.assertEqual(S.devolucao_aviso_do(r["id"])["situacao"], "erro")
        self.assertEqual(len(porta.chamadas), 2)          # passageiro: tenta mais uma vez

    def test_destinatario_sem_endereco_fica_registrado(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "usado"}])
        porta = Porta({"ok": False, "enviadas": 0, "resultados": [
            {"para": "alejandro", "ok": False, "situacao": "sem_endereco", "erro": "não cadastrou o e-mail"}]})
        self.tratar(r["id"], porta)
        aviso = S.devolucao_aviso_do(r["id"])
        self.assertEqual(aviso["situacao"], "sem_endereco")
        self.assertEqual(len(porta.chamadas), 1)          # sem_endereco não se repete

    def test_reabrir_e_tratar_de_novo_nao_manda_outra_vez(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "avariado"}])
        porta = Porta()
        self.tratar(r["id"], porta)
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        S.devolucao_reabrir(r["id"], d["versao"], "chefe")
        self.tratar(r["id"], porta, documento="NFD 78")
        self.assertEqual(len(porta.chamadas), 1)


if __name__ == "__main__":
    unittest.main()
