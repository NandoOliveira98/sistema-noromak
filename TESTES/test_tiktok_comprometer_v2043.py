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


class RevisaoAntesDeSubir(Base):
    """Achados do revisar-antes-de-subir da V20.43 (09/11)."""

    vez_chegou = Comprometer.vez_chegou

    def deixar_parcial(self, pedido):
        self.receber(_sc(pedido))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp(ok=False, situacao="recusado", erro="x")
        self.rodar(porta)
        self.vez_chegou(pedido)

    def botao(self, pedido, porta):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S.threading, "Thread") as t:
            t.side_effect = lambda target, **k: mock.Mock(start=target)
            return S.tiktok_atendimento_manual_iniciar(pedido, "luis")

    def test_revenda_e_sempre_a_de_bauru_mesmo_fora_da_fila(self):
        self.deixar_parcial("591001")
        with mock.patch.object(S, "_exp_revenda", return_value=1):
            p = Porta(buscar="encontrado")
            self.rodar(p)
        self.assertEqual(p.corpo("atendimento/comprometer")["revenda"], 3)

    def test_botao_recusa_pedido_fora_da_fila_ou_com_romaneio(self):
        self.deixar_parcial("591002")
        with mock.patch.object(S, "_tiktok_atendimento_ja_atendido", return_value=True):
            with self.assertRaises(ValueError):
                self.botao("591002", Porta(buscar="encontrado"))
        self.excluir_admin("591002", "TIKTOK")
        p = Porta(buscar="encontrado")
        with self.assertRaises(ValueError):
            self.botao("591002", p)
        self.assertEqual(p.rotas(), [])

    def test_venda_cancelada_nao_e_comprometida(self):
        self.deixar_parcial("591003")
        with S.DB_LOCK:  # o TikTok confirmou o cancelamento (pedido ficou na fila com peça bipada)
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET status='CANCELLED' WHERE pedido='591003'")
            S.DB.commit()
        p = Porta(buscar="encontrado")
        self.rodar(p)
        self.assertNotIn("atendimento/comprometer", p.rotas())
        with self.assertRaises(ValueError):
            self.botao("591003", Porta(buscar="encontrado"))

    def test_limite_por_minuto_e_ocupado_nao_gastam_tentativa(self):
        self.receber(_sc("591004"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = S.PortaErro("Muitas chamadas", passageiro=True)
        self.rodar(porta)
        for _ in range(8):
            self.vez_chegou("591004")
            p = Porta(buscar="encontrado")
            p.comprometer = porta.comprometer
            self.rodar(p)
        e = self.estado("591004")
        self.assertEqual(e["comp_tentativas"], 0)
        ocupado = S.PortaErro("Este cliente/atendimento já está sendo mexido agora.")
        ocupado.status = 409
        self.vez_chegou("591004")
        p = Porta(buscar="encontrado")
        p.comprometer = ocupado
        self.rodar(p)
        self.assertEqual(self.estado("591004")["comp_tentativas"], 1, "2ª revisão: 10 seguidos = 1 tentativa (sem laço infinito)")

    def test_romaneio_solto_enquanto_esperava_a_porta_nao_compromete(self):
        self.deixar_parcial("591012")
        p = Porta(buscar="encontrado")
        with mock.patch.object(S, "_tiktok_comp_pode", side_effect=["", "o pedido já tem romaneio/caixa"]):
            self.rodar(p)
        self.assertNotIn("atendimento/comprometer", p.rotas())
        self.assertIn("romaneio", self.estado("591012")["comp_detalhe"])

    def test_sem_nada_a_comprometer_nao_le_a_fila(self):
        with mock.patch.object(S, "get_storage", wraps=S.get_storage) as gs:
            self.assertEqual(S._tiktok_comp_candidatos(), [])
        self.assertFalse(any(c.args and c.args[0] == "conf_pedidos" for c in gs.call_args_list))

    def test_comprometer_repete_mesmo_com_o_automatico_desligado(self):
        self.deixar_parcial("591005")
        S._tiktok_config_gravar("atendimento_auto", "0", "teste")
        p = Porta(buscar="encontrado")
        self.rodar(p)
        self.assertEqual(p.rotas(), ["atendimento/comprometer"], "só mexe em atendimento que já existe")
        self.assertEqual(self.estado("591005")["comprometido"], "sim")
        self.receber(_sc("591006"))
        p2 = Porta(buscar="encontrado")
        self.rodar(p2)
        self.assertNotIn("atendimento/criar", p2.rotas(), "desligado não cria")

    def test_comprometendo_que_sobrou_do_reinicio_volta_a_ser_tentado(self):
        self.deixar_parcial("591007")
        S._tiktok_comp_registrar("591007", "comprometendo", "Comprometendo pelo painel (luis)…")
        S.ensure_tiktok_atendimento_schema()  # boot
        e = self.estado("591007")
        self.assertNotEqual(e["comprometido"], "comprometendo")
        p = Porta(buscar="encontrado")
        self.rodar(p)
        self.assertEqual(self.estado("591007")["comprometido"], "sim")

    def test_sem_atendimento_nao_deixa_comprometendo_preso(self):
        self.receber(_sc("591008"))
        S._tiktok_atendimento_registrar("591008", "criado", "x")  # sem número do atendimento
        S._tiktok_comp_registrar("591008", "comprometendo", "…")
        S.tiktok_atendimento_comprometer("591008")
        self.assertNotEqual(self.estado("591008")["comprometido"], "comprometendo")

    def test_formato_S_do_linx_vale_como_comprometido(self):
        self.receber(_sc("591009"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp([{"codigo": "0893600022", "comprometido": "S"}])
        self.rodar(porta)
        self.assertEqual(self.estado("591009")["comprometido"], "sim")

    def test_formato_desconhecido_nunca_e_sim(self):
        self.receber(_sc("591010"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = _comp([{"codigo": "0893600022", "comprometido": "N"}])
        self.rodar(porta)
        self.assertEqual(self.estado("591010")["comprometido"], "parcial")

    def test_amostra_mascarada(self):
        self.receber(_sc("591011"))
        porta = Porta(buscar="encontrado")
        porta.comprometer = dict(_comp(situacao="ja_comprometido"), cliente_nome="FULANO DE TAL", cpf="12345678909")
        self.rodar(porta)
        with S.DB_LOCK:
            r = S.DB.execute("SELECT resposta FROM tiktok_amostra_v1963 WHERE tipo='porta atendimento/comprometer'").fetchone()
        self.assertIsNotNone(r)
        self.assertNotIn("12345678909", r["resposta"])
        self.assertNotIn("FULANO", r["resposta"])


if __name__ == "__main__":
    import unittest
    unittest.main()
