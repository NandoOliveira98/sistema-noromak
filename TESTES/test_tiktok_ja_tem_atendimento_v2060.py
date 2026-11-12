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


def _cand(numero, bate=True, faltam=None, valor=10.0):
    return {"atendimento": numero, "bate": bate, "faltam": faltam or [], "valor": valor, "total_a_soltar": 1}


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
        porta, _ = self._rodar(_preparo(romaneio_fora="795001"))
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
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0 WHERE pedido='581007'")
            S.DB.commit()
        porta, prep = self._rodar(_preparo(_cand("795004")))
        self.assertFalse(prep.called)
        self.assertIn("atendimento/criar", porta.rotas())


if __name__ == "__main__":
    import unittest
    unittest.main()
