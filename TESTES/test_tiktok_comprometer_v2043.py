"""V20.43 — peças do atendimento do TikTok comprometidas (porta atendimento/comprometer do Guilherme, 09/11).

O `atendimento/criar` só abre o atendimento; as peças ficavam descomprometidas e o Apollo não reservava. Agora o
Noromak chama o `comprometer {revenda, operador, contato}` logo depois de criar e, se não comprometeu tudo, tenta de
novo SÓ o comprometer nas voltas seguintes (até 5 vezes), nunca o criar.
"""

from unittest import mock

from base import S
from test_tiktok_atendimento_v2033 import Base, Porta, _sc


def _comp(itens=None, ok=True, situacao="comprometido", erro=None):
    r = {"ok": ok, "situacao": situacao, "itens": itens if itens is not None else []}
    if erro:
        r["erro"] = erro
    return r


class Comprometer(Base):

    def vez_chegou(self, pedido):
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_atendimento_v2033 SET comp_proxima=0 WHERE pedido=?", (pedido,))
            S.DB.commit()

    def test_compromete_logo_depois_de_criar(self):
        self.receber(_sc("590001"))
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertEqual(porta.rotas()[-2:], ["atendimento/criar", "atendimento/comprometer"])
        self.assertEqual(porta.corpo("atendimento/comprometer"), {"revenda": 3, "operador": "noromak", "contato": 793500})
        e = self.estado("590001")
        self.assertEqual((e["situacao"], e["comprometido"]), ("criado", "sim"))
        self.assertIn("590001", S._anuncio_pedidos_com_atendimento())
        porta2 = Porta(buscar="encontrado")
        self.rodar(porta2)
        self.assertEqual(porta2.rotas(), [], "comprometido: não chama mais nada")

    def test_ja_comprometido_vale_como_sim(self):
        self.receber(_sc("590002"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(situacao="ja_comprometido")
        self.rodar(porta)
        self.assertEqual(self.estado("590002")["comprometido"], "sim")

    def test_peca_sem_saldo_fica_parcial_e_so_o_comprometer_e_repetido(self):
        self.receber(_sc("590003"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp([{"codigo": "0893600022", "comprometido": False, "motivo": "sem saldo"}])
        self.rodar(porta)
        e = self.estado("590003")
        self.assertEqual(e["comprometido"], "parcial")
        self.assertIn("0893600022: sem saldo", e["comp_detalhe"])
        self.assertNotIn("590003", S._anuncio_pedidos_com_atendimento(), "sem comprometer o estoque desconta")
        porta2 = Porta(buscar="encontrado")
        self.rodar(porta2)
        self.assertEqual(porta2.rotas(), [], "espera a vez (1 h)")
        self.vez_chegou("590003")
        porta3 = Porta(buscar="encontrado")
        self.rodar(porta3)
        self.assertEqual(porta3.rotas(), ["atendimento/comprometer"], "nunca cria de novo")
        self.assertEqual(self.estado("590003")["comprometido"], "sim")

    def test_recusa_para_depois_de_5_tentativas(self):
        self.receber(_sc("590004"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(ok=False, situacao="recusado", erro="não apareceu entre os abertos")
        self.rodar(porta)
        for _ in range(6):
            self.vez_chegou("590004")
            p = Porta(buscar="encontrado")
            p.comprometer = porta.comprometer
            self.rodar(p)
        e = self.estado("590004")
        self.assertEqual(e["comprometido"], "nao")
        self.assertEqual(e["comp_tentativas"], S.TIKTOK_COMP_MAX)
        self.assertIn("botão do painel", e["comp_detalhe"])

    def test_resposta_perdida_fica_incerto_e_repete(self):
        self.receber(_sc("590005"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = S.PortaErro("sem resposta", incerto=True)
        r = self.rodar(porta)
        self.assertEqual(self.estado("590005")["situacao"], "criado", "falha no comprometer não desfaz o atendimento")
        self.assertEqual(self.estado("590005")["comprometido"], "incerto")
        self.vez_chegou("590005")
        porta2 = Porta(buscar="encontrado")
        self.rodar(porta2)
        self.assertEqual(porta2.rotas(), ["atendimento/comprometer"])
        self.assertEqual(self.estado("590005")["comprometido"], "sim")

    def test_resposta_sem_itens_nem_prova_nao_e_sim(self):
        self.receber(_sc("590006"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(itens=[], situacao="comprometido")
        self.rodar(porta)
        self.assertNotEqual(self.estado("590006")["comprometido"], "sim")

    def test_com_romaneio_solto_nao_tenta_comprometer(self):
        self.receber(_sc("590007"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(ok=False, situacao="recusado", erro="x")
        self.rodar(porta)
        self.vez_chegou("590007")
        with mock.patch.object(S, "_tiktok_atendimento_ja_atendido", return_value=True):
            p = Porta(buscar="encontrado")
            self.rodar(p)
        self.assertNotIn("atendimento/comprometer", p.rotas())

    def test_um_pedido_por_volta_contando_o_comprometer(self):
        self.receber(_sc("590008"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(ok=False, situacao="recusado", erro="x")
        self.rodar(porta)
        self.vez_chegou("590008")
        self.receber(_sc("590008"), _sc("590009"))
        p = Porta(buscar="encontrado")
        with mock.patch.object(S, "_exp_porta", side_effect=p), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "_tiktok_atendimento_custo", return_value=None), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}):
            S.tiktok_atendimento_rodada()  # sem forcar: uma vaga
        self.assertEqual(sum(1 for r in p.rotas() if r in ("atendimento/criar", "atendimento/comprometer")), 2,
                         "o pedido novo cria e compromete; o retry espera a próxima volta")
        self.assertEqual(self.estado("590008")["comprometido"], "nao")

    def test_botao_so_compromete_quando_ja_criado(self):
        self.receber(_sc("590010"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(ok=False, situacao="recusado", erro="x")
        self.rodar(porta)
        p = Porta(buscar="encontrado")
        with mock.patch.object(S, "_exp_porta", side_effect=p), mock.patch.object(S.threading, "Thread") as t:
            t.side_effect = lambda target, **k: mock.Mock(start=target)
            r = S.tiktok_atendimento_manual_iniciar("590010", "luis")
        self.assertTrue(r["ok"])
        self.assertEqual(p.rotas(), ["atendimento/comprometer"])
        self.assertEqual(self.estado("590010")["comprometido"], "sim")
        with self.assertRaises(ValueError):  # tudo feito
            S.tiktok_atendimento_manual_iniciar("590010", "luis")

    def test_painel_recebe_a_situacao(self):
        self.receber(_sc("590011"))
        self.rodar(Porta(buscar="encontrado"))
        at = S.tiktok_atendimento_do_pedido("590011")
        self.assertEqual(at["comprometido"], "sim")
        linha = next(p for p in S.tiktok_atendimento_payload()["pedidos"] if p["pedido"] == "590011")
        self.assertEqual(linha["comprometido"], "sim")


if __name__ == "__main__":
    import unittest
    unittest.main()
