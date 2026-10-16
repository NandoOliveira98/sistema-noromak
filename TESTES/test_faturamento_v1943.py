"""V19.43 — Fila de faturamento em lote (decisões do dono, 16/10).

PRONTO só com: cadastro feito, crédito liberado, UM atendimento, peças batendo e valor do
atendimento IGUAL ao da venda. Na hora de executar o pedido é conferido de novo.
Shopee/Magalu param para a troca da condição de pagamento (extensão, Linx web).
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from base import S, Cliente, iniciar_servidor, linha, gravar_como_admin, reiniciar_banco
from test_expedicao_apollo_v1930 import PortaFalsa

PEDIDO = "2000019000000077"


def _pedido(marketplace="MERCADO LIVRE", valor=25.9, cadastro=True):
    gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace=marketplace)])
    S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Thaynara Cristina Silva", linx_cliente="83020",
                            fonte="teste", usuario="teste")
    if valor is not None:
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,amount,updated_at) "
                         "VALUES('ml',?,'1','2026-10-16','2026-10-16',?, '2026-10-16')", (PEDIDO, valor))
            S.DB.commit()
    if cadastro:
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "teste", "83020")


class Fila(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def _conferir(self, porta, mp="Mercado Livre"):
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            return S.faturamento_conferir(PEDIDO, mp)

    def test_pronto_com_tudo_batendo(self):
        _pedido()
        r = self._conferir(PortaFalsa())
        self.assertEqual((r["situacao"], r["atendimento"]), ("pronto", "787601"))

    def test_sem_cadastro_aguarda_e_nao_vai_ao_apollo(self):
        _pedido(cadastro=False)
        porta = PortaFalsa()
        self.assertEqual(self._conferir(porta)["situacao"], "aguardando_cadastro")
        self.assertEqual(porta.chamadas, [])

    def test_valor_diferente_fica_em_atencao(self):
        _pedido(valor=30.0)
        r = self._conferir(PortaFalsa())
        self.assertEqual(r["situacao"], "atencao")
        self.assertIn("Valor diferente", r["motivo"])

    def test_valor_desconhecido_nunca_e_pronto(self):
        _pedido(valor=None)
        self.assertEqual(self._conferir(PortaFalsa())["situacao"], "atencao")

    def test_dois_atendimentos_fica_em_atencao(self):
        _pedido()
        a = {"atendimento": 1, "cliente": "THAYNARA CRISTINA  SILVA", "valor": 25.9}
        r = self._conferir(PortaFalsa(abertos=[a, {**a, "atendimento": 2}]))
        self.assertEqual(r["situacao"], "atencao")

    def test_payload_lista_os_pendentes(self):
        _pedido()
        self._conferir(PortaFalsa())
        itens = S.faturamento_payload()["itens"]
        self.assertEqual([(i["pedido"], i["situacao"]) for i in itens], [(PEDIDO, "pronto")])

    def test_rodada_respeita_o_intervalo(self):
        _pedido()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            t0 = time.time()
            S.faturamento_rodada(agora=t0)
            n = len(porta.chamadas)
            S.faturamento_rodada(agora=t0 + 60)
            self.assertEqual(len(porta.chamadas), n)
            S.faturamento_rodada(agora=t0 + 700)
            self.assertGreater(len(porta.chamadas), n)


class Executar(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_ml_solta_romaneio_com_observacao_e_envia_ao_caixa(self):
        _pedido()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis")
        self.assertEqual((r["ok"], r["etapa"]), (True, "feito"), r)
        self.assertEqual(porta.corpo("romaneio/soltar")["observacao"], "MERCADO LIVRE")
        self.assertIn("caixa/enviar", porta.rotas())

    def test_reconfere_na_hora_e_recusa_se_mudou(self):
        _pedido()
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            S.faturamento_conferir(PEDIDO, "Mercado Livre")
        porta = PortaFalsa(abertos=[])                       # atendimento sumiu no Apollo
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis")
        self.assertFalse(r["ok"])
        self.assertNotIn("romaneio/soltar", porta.rotas())
        self.assertNotIn("caixa/enviar", porta.rotas())

    def test_shopee_para_na_condicao(self):
        _pedido(marketplace="SHOPEE")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.faturamento_executar(PEDIDO, "luis")
        self.assertEqual(r["etapa"], "condicao")
        self.assertIn("romaneio/soltar", porta.rotas())
        self.assertNotIn("caixa/enviar", porta.rotas())


class Rotas(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        self.url = iniciar_servidor()

    def test_sem_login_401(self):
        status, _ = Cliente(self.url).pedir("GET", "/api/faturamento")
        self.assertEqual(status, 401)

    def test_admin_le_a_fila(self):
        admin = Cliente(self.url)
        admin.entrar()
        status, d = admin.pedir("GET", "/api/faturamento")
        self.assertEqual((status, d["ok"]), (200, True))

    def test_worker_no_painel_de_saude(self):
        self.assertIn("faturamento_loop", S.SAUDE_WORKERS_INFO)

    def test_menu_tem_faturamento_e_relatorios_sairam(self):
        menu = open(S.APP_DIR / "nk-menu.js", encoding="utf-8").read()
        self.assertIn('id: "faturamento"', menu)
        admin = open(S.APP_DIR / "admin.html", encoding="utf-8").read()
        self.assertNotIn("abrirSeletorRelatorioShopee", admin)
        self.assertNotIn("abrirSeletorRelatorioMercadoLivre", admin)


if __name__ == "__main__":
    unittest.main()


class Vez(unittest.TestCase):
    """V19.48: a rodada sempre começava pelo topo da fila; com 4 por rodada e 10 min de
    intervalo, os do fim nunca chegavam (261016S2NU71S0: cadastro feito 13:08, tela parada
    em "Aguardando cadastro"). Agora vai primeiro quem teve o cadastro feito e o mais antigo."""

    def setUp(self):
        reiniciar_banco()

    def test_cadastro_recem_feito_passa_na_frente(self):
        pedidos = [f"20000190000000{i:02d}" for i in range(8)]
        gravar_como_admin([linha(p, "0893764272", Marketplace="MERCADO LIVRE") for p in pedidos])
        agora = time.time()
        for p in pedidos[:7]:
            S._cadastro_auto_registrar(p, agora, agora, 0, "feito", "t", "83020")
        conferidos = []
        with S.DB_LOCK:
            for p in pedidos:
                S.DB.execute("INSERT OR REPLACE INTO faturamento_v1943(order_key,pedido,situacao,conferido_epoch) VALUES(?,?,?,?)",
                             (S.normalize(p), p, "atencao", agora - 700))
            S.DB.execute("UPDATE faturamento_v1943 SET situacao='aguardando_cadastro', conferido_epoch=? WHERE pedido=?",
                         (agora - 5000, pedidos[6]))
            S.DB.commit()
        with mock.patch.object(S, "faturamento_conferir", side_effect=lambda p, mp, a=None: conferidos.append(p)):
            S.faturamento_rodada(agora=agora)
        self.assertEqual(conferidos[0], pedidos[6])
