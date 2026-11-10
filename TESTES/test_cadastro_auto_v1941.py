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

    def test_nunca_cria_e_espaca_as_tentativas_ate_3_dias(self):
        # V20.47: 1 h era pouco (a maioria dos clientes cai no Apollo depois). Continua
        # tentando enquanto o pedido está na fila: 10 min na 1ª hora, 30 min até 6 h,
        # depois de hora em hora; desiste com 3 dias.
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
        self._rodar(porta, agora=t0 + 3700)                  # passou 1 h: NÃO desiste
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "aguardando")
        self.assertIn("Próxima tentativa", S.cadastro_auto_do_pedido(PEDIDO)["detalhe"])
        self._rodar(porta, agora=t0 + 3700 + 1200)           # depois de 1 h: 30 min entre tentativas
        self.assertEqual(porta.rotas().count("cliente/buscar"), 3)
        self._rodar(porta, agora=t0 + 3700 + 1810)
        self.assertEqual(porta.rotas().count("cliente/buscar"), 4)
        self._rodar(porta, agora=t0 + 7 * 3600)              # depois de 6 h: de hora em hora
        self._rodar(porta, agora=t0 + 7 * 3600 + 2400)
        self.assertEqual(porta.rotas().count("cliente/buscar"), 5)
        self._rodar(porta, agora=t0 + 3 * 86400 + 60)        # 3 dias: desiste
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "nao_encontrado")
        self.assertNotIn("cliente/criar", porta.rotas())
        n = len(porta.chamadas)
        self._rodar(porta, agora=t0 + 3 * 86400 + 9000)
        self.assertEqual(len(porta.chamadas), n)

    def test_tentativa_sem_cliente_nao_le_o_envio_no_ml(self):
        # V20.47: a tentativa que não acha o cliente custa só a busca pelo CPF.
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML) as ml:
            S.cadastro_auto_rodada(agora=time.time())
        self.assertEqual(porta.rotas(), ["cliente/buscar"])
        ml.assert_not_called()

    def test_achou_le_o_envio_e_busca_uma_vez_so(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83100}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML) as ml:
            self.assertEqual(S.cadastro_auto_rodada(agora=time.time())["feito"], 1)
        self.assertEqual(porta.rotas().count("cliente/buscar"), 1)
        self.assertTrue(ml.called)
        self.assertIn("endereco", next(c for r, c in porta.chamadas if r == "cliente/atualizar"))

    def test_nao_encontrado_antigo_volta_a_ser_tentado(self):
        # Os que a regra de 1 h já tinha dado como "não encontrado" voltam enquanto estão na fila.
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        t0 = time.time()
        S._cadastro_auto_registrar(PEDIDO, t0, t0, 7, "nao_encontrado", "Cliente ainda não está no Apollo.")
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83100}
        self.assertEqual(self._rodar(porta, agora=t0 + 4 * 3600)["feito"], 1)
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito")

    def test_faturamento_mostra_a_proxima_tentativa(self):
        S.cadastro_auto_ligar(True, "admin")
        _pedido()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        self._rodar(porta)
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            r = S.faturamento_conferir(PEDIDO, "Mercado Livre")
        self.assertEqual(r["situacao"], "aguardando_cadastro")
        self.assertIn("Próxima tentativa", r["motivo"])

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
            self.assertEqual(self._rodar(porta)["feito"], 1)   # V20.47: conta como feito (sem endereço)
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


class RevisaoV2047(unittest.TestCase):
    """revisar-antes-de-subir da V20.47 (10/11, "corrigir todos")."""

    def setUp(self):
        reiniciar_banco()
        S.cadastro_auto_ligar(True, "admin")

    def _rodar(self, porta, agora=None, ml=ENVIO_ML):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ml):
            return S.cadastro_auto_rodada(agora=agora)

    def test_feito_pelo_botao_no_meio_da_rodada_nao_e_refeito_nem_desfeito(self):
        # A rodada escolhe os candidatos no começo; o botão marca "feito" enquanto ela espera.
        _pedido()
        t0 = time.time()
        velho = S._cadastro_auto_candidatos(S.cadastro_auto_config()["ligado_em"], t0)
        S._cadastro_auto_registrar(PEDIDO, t0, t0, 0, "feito", "pelo botão (luis)", "83100")
        porta = PortaFalsa()
        with mock.patch.object(S, "_cadastro_auto_candidatos", return_value=velho):
            self._rodar(porta, agora=t0 + 30)
        self.assertEqual(porta.chamadas, [])
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito")

    def test_registro_da_rotina_nunca_troca_o_feito(self):
        _pedido()
        t0 = time.time()
        S._cadastro_auto_registrar(PEDIDO, t0, t0, 0, "feito", "pelo botão (luis)", "83100")
        S._cadastro_auto_registrar(PEDIDO, t0, t0 + 5, 3, "aguardando", "erro da porta", automatico=True)
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito")

    def test_pedido_novo_passa_na_frente_dos_antigos(self):
        t0 = time.time()
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506"),
                           linha("2000019000000002", "0893764272", Marketplace="MERCADO LIVRE",
                                 ShipmentIdMercadoLivre="48263508507")])
        S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal da Silva", fonte="extensao", usuario="t")
        S.salvar_cliente_fiscal("2000019000000002", "11144477735", "Beltrano Souza", fonte="extensao", usuario="t")
        # o 1º (mais antigo no banco) já está há 7 h tentando; o 2º acabou de chegar
        S._cadastro_auto_registrar(PEDIDO, t0 - 7 * 3600, t0 - 2 * 3600, 9, "aguardando", "x", automatico=True)
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        with mock.patch.object(S, "CADASTRO_AUTO_POR_RODADA", 1):
            self._rodar(porta, agora=t0)
        self.assertEqual(porta.corpo("cliente/buscar")["documento"], "11144477735")

    def test_nao_encontrado_com_cliente_ja_guardado_nao_e_reaberto(self):
        # Resolvido à mão (extensão antiga / Apollo): o código ficou guardado sem "feito".
        _pedido()
        t0 = time.time()
        S._cadastro_auto_registrar(PEDIDO, t0, t0, 7, "nao_encontrado", "Cliente ainda não está no Apollo.")
        S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal da Silva", linx_cliente="83100",
                                fonte="extensao", usuario="luis")
        porta = PortaFalsa()
        self._rodar(porta, agora=t0 + 4 * 3600)
        self.assertEqual(porta.chamadas, [])
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "nao_encontrado")

    def test_apollo_no_limite_para_a_volta_e_nao_gasta_tentativa(self):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506"),
                           linha("2000019000000002", "0893764272", Marketplace="MERCADO LIVRE",
                                 ShipmentIdMercadoLivre="48263508507")])
        S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal da Silva", fonte="extensao", usuario="t")
        S.salvar_cliente_fiscal("2000019000000002", "11144477735", "Beltrano Souza", fonte="extensao", usuario="t")
        chamadas = []

        def porta(rota, corpo, escrita=False):
            chamadas.append(rota)
            raise S.PortaErro("Muitas chamadas ao Apollo", passageiro=True)

        self._rodar(porta)
        self.assertEqual(chamadas, ["cliente/buscar"])          # parou no 1º limite
        self.assertIsNone(S.cadastro_auto_do_pedido(PEDIDO))
        self.assertIsNone(S.cadastro_auto_do_pedido("2000019000000002"))

    def test_sem_endereco_fica_feito_sem_endereco_e_o_faturamento_segue(self):
        # Shopee: a API mascara o endereço; o painel da extensão continua oferecendo o cadastro
        # (ela só pula com "feito") e o Faturamento não fica parado.
        _pedido(marketplace="SHOPEE")
        porta = PortaFalsa()
        with mock.patch.object(S, "shopee_order_details", return_value=[]):
            self.assertEqual(self._rodar(porta)["feito"], 1)
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito_sem_endereco")
        self.assertNotIn("endereco", porta.corpo("cliente/atualizar"))
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            r = S.faturamento_conferir(PEDIDO, "Shopee")
        self.assertNotEqual(r["situacao"], "aguardando_cadastro")
        porta.chamadas.clear()
        self._rodar(porta, agora=time.time() + 3 * 3600)        # não é tentado de novo
        self.assertEqual(porta.chamadas, [])

    def test_faturamento_nao_diz_cliente_fora_do_apollo_quando_o_erro_foi_da_porta(self):
        _pedido()
        t0 = time.time()
        S._cadastro_auto_registrar(PEDIDO, t0, t0, 1, "aguardando", "Resposta inesperada da busca do cliente.",
                                   automatico=True)
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            r = S.faturamento_conferir(PEDIDO, "Mercado Livre")
        self.assertNotIn("cair no Apollo", r["motivo"])
        self.assertIn("Resposta inesperada", r["motivo"])


class BotaoMarcaFeito(unittest.TestCase):
    """V19.42: cadastro pelo botão fica registrado — o painel pula a etapa em qualquer PC."""

    def setUp(self):
        reiniciar_banco()

    def test_botao_registra_feito_e_a_rotina_nao_refaz(self):
        _pedido()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            self.assertTrue(S.exp_cadastro_atualizar(PEDIDO, "luis")["ok"])
            estado = S.cadastro_auto_do_pedido(PEDIDO)
            self.assertEqual(estado["situacao"], "feito")
            self.assertIn("botão", estado["detalhe"])
            d = S.exp_romaneio_preparar(PEDIDO)
            self.assertEqual(d["cadastro_auto"]["situacao"], "feito")
            S.cadastro_auto_ligar(True, "admin")
            S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal da Silva", fonte="extensao", usuario="t")
            porta.chamadas.clear()
            S.cadastro_auto_rodada(agora=time.time() + 700)
        self.assertEqual(porta.chamadas, [])


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


class NomeCpfDaApiDoMl(unittest.TestCase):
    """V19.47: o ML voltou a liberar billing-info (16/10) e a importação já grava
    NomeFiscalMercadoLivre/CPFMercadoLivre na linha. O automático só olhava o que a
    extensão gravou em cliente_fiscal: pedido 2000015556694429 ficou parado > 1 h."""

    def setUp(self):
        reiniciar_banco()

    def _linha_api(self, **extra):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506",
                                 NomeFiscalMercadoLivre="Fernando Henrique da Silva Souza",
                                 CPFMercadoLivre="12569176648", **extra)])

    def test_cpf_da_api_dispara_o_cadastro_sem_extensao(self):
        S.cadastro_auto_ligar(True, "admin")
        time.sleep(0.01)
        self._linha_api()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            self.assertEqual(S.cadastro_auto_rodada()["feito"], 1)
        self.assertEqual(porta.corpo("cliente/buscar")["documento"], "12569176648")
        self.assertEqual(S._cliente_fiscal_guardado(PEDIDO)["documento_numero"], "12569176648")

    def test_nao_sobrescreve_o_que_a_extensao_gravou(self):
        self._linha_api()
        S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal da Silva", fonte="extensao", usuario="t")
        S.cliente_fiscal_da_api_ml()
        self.assertEqual(S._cliente_fiscal_guardado(PEDIDO)["documento_numero"], "06278073994")
