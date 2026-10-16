"""V19.41 — Cadastro automático do cliente no Apollo (decisões do dono, 16/10).

A regra mais importante: NUNCA cria cliente. O cliente da venda demora um tempo incerto
para cair no Apollo; criar na hora duplicaria o cadastro. Não achou → tenta de 10 em 10
minutos por 1 hora (contada da chegada do nome/CPF da NF-e) e depois fica "não encontrado".
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from base import S, Cliente, iniciar_servidor, linha, gravar_como_admin, reiniciar_banco
from test_expedicao_apollo_v1930 import PortaFalsa, ENVIO_ML

PEDIDO = "2000019000000001"


def _pedido(documento="06278073994", nome="Fulano de Tal da Silva", marketplace="MERCADO LIVRE"):
    gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace=marketplace, ShipmentIdMercadoLivre="48263508506")])
    S.salvar_cliente_fiscal(PEDIDO, documento, nome, fonte="extensao", usuario="teste")


class CadastroAuto(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def _rodar(self, porta, agora=None):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            return S.cadastro_auto_rodada(agora=agora)

    def test_desligado_nao_faz_nada(self):
        _pedido()
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_cliente_que_existe_e_atualizado_uma_vez(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        self.assertEqual(self._rodar(porta)["feito"], 1)
        self.assertIn("cliente/atualizar", porta.rotas())
        self.assertIn("cliente/credito", porta.rotas())
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito")
        porta.chamadas.clear()
        self._rodar(porta, agora=time.time() + 3 * 3600)      # feito não repete
        self.assertEqual(porta.chamadas, [])

    def test_nunca_cria_e_desiste_depois_de_1h(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        t0 = time.time()
        self.assertEqual(self._rodar(porta, agora=t0)["aguardando"], 1)
        self._rodar(porta, agora=t0 + 120)                   # menos de 10 min: não tenta
        self.assertEqual(porta.rotas().count("cliente/buscar"), 1)
        self._rodar(porta, agora=t0 + 610)                   # 10 min depois: tenta de novo
        self.assertEqual(porta.rotas().count("cliente/buscar"), 2)
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["tentativas"], 2)
        self._rodar(porta, agora=t0 + 3700)                  # passou 1 h: desiste
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "nao_encontrado")
        self.assertNotIn("cliente/criar", porta.rotas())
        n = len(porta.chamadas)
        self._rodar(porta, agora=t0 + 9000)
        self.assertEqual(len(porta.chamadas), n)

    def test_cliente_cai_no_apollo_durante_a_hora(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        t0 = time.time()
        self._rodar(porta, agora=t0)
        porta.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83100}
        self._rodar(porta, agora=t0 + 1300)
        estado = S.cadastro_auto_do_pedido(PEDIDO)
        self.assertEqual((estado["situacao"], estado["cliente"]), ("feito", "83100"))
        self.assertNotIn("cliente/criar", porta.rotas())

    def test_pedido_de_antes_de_ligar_fica_de_fora(self):
        _pedido()
        time.sleep(0.01)
        S.cadastro_auto_ligar(True, "admin")
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_mais_de_um_cliente_nao_mexe(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "mais_de_um", "clientes": [{"cliente": 1}, {"cliente": 2}]}
        self._rodar(porta)
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "mais_de_um")
        self.assertNotIn("cliente/atualizar", porta.rotas())

    def test_shopee_entra_e_magalu_fica_de_fora(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido(marketplace="SHOPEE")
        porta = PortaFalsa()
        with mock.patch.object(S, "shopee_order_details", return_value=[]):
            self.assertEqual(self._rodar(porta)["feito"], 1)
        reiniciar_banco()
        S.cadastro_auto_ligar(True, "admin")
        _pedido(marketplace="MAGALU")
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_desligar_para_a_rotina(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        S.cadastro_auto_ligar(False, "admin")
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_painel_e_lista_mostram_a_situacao(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        self._rodar(porta)
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["cadastro_auto"]["situacao"], "aguardando")
        self.assertEqual(S.lista_cliente_fiscal()["clientes"][PEDIDO]["cadastro_auto"]["situacao"], "aguardando")


class Rotas(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        self.url = iniciar_servidor()

    def test_admin_liga_e_desliga(self):
        admin = Cliente(self.url)
        admin.entrar()
        status, d = admin.pedir("GET", "/api/expedicao-apollo/cadastro-auto")
        self.assertEqual((status, d["ligado"]), (200, False))
        status, d = admin.pedir("POST", "/api/expedicao-apollo/cadastro-auto", {"ligado": True})
        self.assertEqual((status, d["ligado"]), (200, True))
        self.assertTrue(d["ligado_em"])
        status, d = admin.pedir("POST", "/api/expedicao-apollo/cadastro-auto", {"ligado": False})
        self.assertFalse(d["ligado"])

    def test_sem_login_nao_liga(self):
        status, _ = Cliente(self.url).pedir("POST", "/api/expedicao-apollo/cadastro-auto", {"ligado": True})
        self.assertEqual(status, 401)
        self.assertFalse(S.cadastro_auto_config()["ligado"])

    def test_worker_no_painel_de_saude(self):
        self.assertIn("cadastro_auto_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
