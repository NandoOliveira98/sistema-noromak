"""V20.60 — o automático do TikTok não cria quando o cliente já tem atendimento das mesmas peças (dono, 12/11).

"Se esse cliente já tiver atendimento criado do mesmo item, aí a nossa integração não cria." Antes de criar, o
atendimento automático do TikTok procura no Apollo os atendimentos abertos do cliente (o mesmo levantamento do painel,
`exp_romaneio_preparar`): um com TODAS as peças do pedido (o valor não importa) e não ligado a outro pedido = não cria
(situação `ja_tinha`, sai da rotina). Romaneio já solto no Apollo também conta. Sem resposta do Apollo = espera.
"""
from unittest import mock

from base import S
from test_tiktok_atendimento_v2033 import Base, Porta, _sc


def _preparo(*candidatos, **extra):
    d = {"ok": True, "situacao": "escolher", "candidatos": list(candidatos)}
    d.update(extra)
    return d


def _cand(numero, bate=True, faltam=None, valor=10.0, qtd=1, codigo="0893600022", a_soltar=1):
    return {"atendimento": numero, "bate": bate, "faltam": faltam or [], "valor": valor, "total_a_soltar": a_soltar,
            "itens": [{"codigo": codigo, "quantidade": qtd, "a_soltar": a_soltar}]}


class JaTemAtendimento(Base):

    def setUp(self):
        super().setUp()
        self._sem_aberto.stop()   # aqui a procura é a de verdade (sobre o exp_romaneio_preparar simulado)

    def _rodar(self, preparo, porta=None):
        porta = porta or Porta(buscar="encontrado")
        with mock.patch.object(S, "exp_romaneio_preparar", return_value=preparo) as prep:
            self.rodar(porta)
        return porta, prep

    def test_mesmas_pecas_nao_cria(self):
        self.receber(_sc("581001"))
        porta, _ = self._rodar(_preparo(_cand("795000", valor=999.0)))   # valor diferente: ainda é o mesmo item
        self.assertNotIn("atendimento/criar", porta.rotas())
        e = self.estado("581001")
        self.assertEqual((e["situacao"], e["atendimento"]), ("ja_tinha", "795000"))
        porta2, _ = self._rodar(_preparo())
        self.assertNotIn("atendimento/criar", porta2.rotas(), "ja_tinha sai da rotina")

    def test_romaneio_ja_solto_conta(self):
        self.receber(_sc("581002"))
        porta, _ = self._rodar(_preparo(_cand("795001", a_soltar=0), romaneio_fora="795001"))
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581002")["situacao"], "ja_tinha")

    def test_faltando_peca_cria(self):
        self.receber(_sc("581003"))
        porta, _ = self._rodar(_preparo(_cand("795002", faltam=["0893600022"])))
        self.assertIn("atendimento/criar", porta.rotas())

    def test_atendimento_de_outro_pedido_nao_conta(self):
        self.receber(_sc("581004"))
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO mkt_atendimento_v2047(pedido, atendimento, situacao) VALUES('OUTRO','795003','criado')")
            S.DB.commit()
        porta, _ = self._rodar(_preparo(_cand("795003")))
        self.assertIn("atendimento/criar", porta.rotas(), "o atendimento é de outra venda do mesmo cliente")

    def test_sem_atendimento_cria(self):
        self.receber(_sc("581005"))
        porta, prep = self._rodar(_preparo())
        self.assertTrue(prep.called)
        self.assertIn("atendimento/criar", porta.rotas())

    def test_apollo_sem_resposta_espera(self):
        self.receber(_sc("581006"))
        porta, _ = self._rodar({"ok": False, "situacao": "erro", "aviso": "Muitas chamadas", "apollo_ocupado": True})
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertNotEqual(self.estado("581006").get("situacao"), "ja_tinha")

    def test_incerto_vai_direto_a_porta(self):
        """O criar já saiu e não voltou: o aberto pode ser o NOSSO; a chave da porta devolve ja_existia."""
        self.receber(_sc("581007"))
        S._tiktok_atendimento_registrar("581007", "incerto", "sem resposta")
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0, criar_enviado_em='2026-11-12' "
                         "WHERE pedido='581007'")
            S.DB.commit()
        porta, prep = self._rodar(_preparo(_cand("795004")))
        self.assertFalse(prep.called)
        self.assertIn("atendimento/criar", porta.rotas())


class Revisao(JaTemAtendimento):
    """revisar-antes-de-subir da V20.60 (12/11)."""

    def _incerto(self, pedido):
        """1º criar sem resposta (o Apollo pode ter criado com a chave TIKTOK-<pedido>)."""
        porta = Porta(buscar="encontrado", criar_atend=S.PortaErro("sem resposta", incerto=True))
        self._rodar(_preparo(), porta)
        self.assertEqual(self.estado(pedido)["situacao"], "incerto")
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0 WHERE pedido=?", (pedido,))
            S.DB.commit()

    def test_incerto_depois_erro_ainda_vai_direto_a_porta(self):
        """ALTA: o atendimento que o próprio Noromak criou não vira ja_tinha depois de um erro no meio."""
        self.receber(_sc("581101"))
        self._incerto("581101")
        erro = S.PortaErro("Muitas chamadas", passageiro=True)
        erro.status = 429
        self._rodar(_preparo(), Porta(buscar="encontrado", criar_atend=erro))
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0 WHERE pedido='581101'")
            S.DB.commit()
        porta, prep = self._rodar(_preparo(_cand("795100")))
        self.assertFalse(prep.called, "o criar já saiu: quem decide é a chave da porta")
        self.assertIn("atendimento/criar", porta.rotas())

    def test_recusa_clara_da_porta_volta_a_procurar(self):
        self.receber(_sc("581102"))
        recusa = {"ok": False, "situacao": "sem_estoque", "erro": "sem saldo"}
        self._rodar(_preparo(), Porta(buscar="encontrado", criar_atend=recusa))
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0 WHERE pedido='581102'")
            S.DB.commit()
        porta, prep = self._rodar(_preparo(_cand("795101")))
        self.assertTrue(prep.called, "recusa clara = nada criado com a nossa chave: procura de novo")
        self.assertEqual(self.estado("581102")["situacao"], "ja_tinha")

    def _proximo(self, pedido):
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0 WHERE pedido=?", (pedido,))
            S.DB.commit()

    def test_429_no_primeiro_criar_volta_a_procurar(self):
        """2ª revisão (MÉDIA): 429 = nada criado; a marca não fica e a próxima volta procura de novo."""
        self.receber(_sc("581106"))
        erro = S.PortaErro("Muitas chamadas", passageiro=True)
        erro.status = 429
        self._rodar(_preparo(), Porta(buscar="encontrado", criar_atend=erro))
        self._proximo("581106")
        _, prep = self._rodar(_preparo(_cand("795105")))
        self.assertTrue(prep.called)
        self.assertEqual(self.estado("581106")["situacao"], "ja_tinha")

    def test_valor_divergente_nao_apaga_a_marca(self):
        """2ª revisão: valor_divergente = o Apollo GRAVOU; a próxima vai direto à porta (a chave decide)."""
        self.receber(_sc("581107"))
        self._rodar(_preparo(), Porta(buscar="encontrado",
                                      criar_atend={"ok": False, "situacao": "valor_divergente", "erro": "outro valor"}))
        self._proximo("581107")
        _, prep = self._rodar(_preparo(_cand("795106")))
        self.assertFalse(prep.called)

    def test_romaneio_fora_de_outro_pedido_nao_conta(self):
        """ALTA: romaneio já solto de OUTRO pedido nosso do mesmo cliente."""
        self.receber(_sc("581103"))
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO tiktok_atendimento_v2033(pedido, atendimento, situacao) VALUES('OUTRO2','795102','criado')")
            S.DB.commit()
        porta, _ = self._rodar(_preparo(_cand("795102", a_soltar=0), romaneio_fora="795102"))
        self.assertIn("atendimento/criar", porta.rotas())

    def test_quantidade_menor_nao_conta(self):
        """MÉDIA: o atendimento aberto tem 1 unidade e o pedido 3: não é o mesmo item."""
        self.receber(_sc("581104", itens=(("SK1", "0893600022", 3, "69.75"),)))
        porta, _ = self._rodar(_preparo(_cand("795103", qtd=1)))
        self.assertIn("atendimento/criar", porta.rotas())

    def test_botao_cria_mesmo_com_ja_tinha(self):
        """MÉDIA: ja_tinha tem saída — o clique de gente no painel cria (o operador conferiu)."""
        self.receber(_sc("581105"))
        self._rodar(_preparo(_cand("795104")))
        self.assertEqual(self.estado("581105")["situacao"], "ja_tinha")
        porta = Porta(buscar="encontrado")
        with mock.patch.object(S, "exp_romaneio_preparar", return_value=_preparo(_cand("795104"))) as prep, \
                mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "_tiktok_atendimento_custo", side_effect=lambda cod, ctx: None), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}), \
                mock.patch.object(S.threading, "Thread") as t:
            t.side_effect = lambda target, **k: mock.Mock(start=target)  # roda na hora (sem thread)
            S.tiktok_atendimento_manual_iniciar("581105", "luis")
        self.assertFalse(prep.called)
        self.assertIn("atendimento/criar", porta.rotas())


if __name__ == "__main__":
    import unittest
    unittest.main()
