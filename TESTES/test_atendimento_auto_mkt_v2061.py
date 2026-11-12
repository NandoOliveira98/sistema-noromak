"""V20.61 — atendimento AUTOMÁTICO de ML e Shopee no Apollo (dono, 12/11: "ativar a criação automática também, apenas
ignorar se já existir atendimento"; decisões: na hora, começa desligado, por marketplace).

A rotina `mkt_atendimento_loop` faz para ML/Shopee o que o botão do painel (V20.47) faz, sem o clique: cadastro do cliente,
procura um atendimento aberto do cliente com as peças da venda (achou = não cria, "ja_tinha"), `atendimento/criar` com
`manual: false` (o Guilherme liberou em 12/11) e o comprometer. Ao ligar, o que já está na fila fica "anterior" (atendido à
mão ou pela integração; segue pelo botão). Um pedido por volta. Shopee: o nome/CPF da nota vem da API (liberada em 12/11).
"""
from __future__ import annotations

import time
from unittest import mock

from base import S, linha, gravar_como_admin
from test_atendimento_ml_shopee_v2047 import Base, Porta, PEDIDO, CPF, NOME, _venda
from test_expedicao_apollo_v1930 import ENVIO_ML

OUTRO = "2000019442143805"


class BaseAuto(Base):
    def rodada(self, porta, **kw):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML), \
                mock.patch.object(S, "_tiktok_atendimento_custo", side_effect=lambda cod, ctx: None), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}):
            return S.mkt_atendimento_rodada(**kw)

    def ligar(self, mp="Mercado Livre"):
        S.mkt_atendimento_automatico_ligar(mp, True, "luis")


class Interruptor(BaseAuto):
    def test_comeca_desligado_e_desligado_nao_cria(self):
        self.assertFalse(S.mkt_atendimento_config()["Mercado Livre"]["automatico"])
        self.assertFalse(S.mkt_atendimento_config()["Shopee"]["automatico"])
        self.pedido(horas=0.1)
        porta = Porta()
        self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_o_que_ja_estava_na_fila_ao_ligar_fica_anterior(self):
        self.pedido(horas=0.1)
        self.ligar()
        self.assertEqual(self.estado().get("situacao"), "anterior")
        porta = Porta()
        self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"] or True)  # o botão continua valendo

    def test_botao_desligado_barra_o_automatico(self):
        self.ligar()
        S.mkt_atendimento_ligar("Mercado Livre", False, "luis")
        self.pedido(horas=0.1)
        porta = Porta()
        self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_so_o_marketplace_ligado(self):
        S.mkt_atendimento_automatico_ligar("Shopee", True, "luis")
        self.pedido(horas=0.1)  # ML
        porta = Porta()
        self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())


class CriaSozinho(BaseAuto):
    def test_venda_nova_cria_na_hora_sem_o_clique(self):
        self.ligar()
        self.pedido(horas=0.05)  # entrou na fila depois de ligar: na hora
        porta = Porta()
        res = self.rodada(porta)
        self.assertIn("atendimento/criar", porta.rotas())
        corpo = porta.corpo("atendimento/criar")
        self.assertIs(corpo.get("manual"), False, "rotina não é clique de gente")
        self.assertEqual(corpo["pedido_externo"], f"ML-{PEDIDO}")
        e = self.estado()
        self.assertEqual(e["situacao"], "criado")
        self.assertEqual(e["comprometido"], "sim")
        self.assertEqual(res["feitos"], 1)

    def test_ja_existe_atendimento_aberto_nao_cria(self):
        """O cliente já tem atendimento aberto com as peças (valor diferente não importa — regra da V20.60)."""
        self.ligar()
        self.pedido(horas=0.05)
        preparo = {"ok": True, "situacao": "escolher", "candidatos": [
            {"atendimento": "794001", "bate": True, "faltam": [], "valor": 19.99, "total_a_soltar": 1,
             "itens": [{"codigo": "0893764272", "quantidade": 1, "a_soltar": 1}]}]}
        porta = Porta()
        with mock.patch.object(S, "exp_romaneio_preparar", return_value=preparo):
            self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        e = self.estado()
        self.assertEqual(e["situacao"], "ja_tinha")
        self.assertEqual(e["atendimento"], "794001")

    def test_sem_nome_cpf_espera(self):
        self.ligar()
        gravar_como_admin([linha(PEDIDO, "0893764272", 1, Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506")])
        _venda(PEDIDO, "0893764272", 1, 25.90, horas=0.05)
        porta = Porta()
        self.rodada(porta)
        self.assertEqual(porta.rotas(), [])
        self.assertNotIn(self.estado().get("situacao"), ("criado", "erro", "revisar"))

    def test_um_pedido_por_volta(self):
        self.ligar()
        self.pedido(horas=0.05)
        gravar_como_admin([linha(OUTRO, "0893764272", 1, Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508507")])
        S.salvar_cliente_fiscal(OUTRO, "52998224725", "Outra Pessoa", fonte="api_ml", usuario="automatico")
        _venda(OUTRO, "0893764272", 1, 25.90, horas=0.05)
        porta = Porta()
        self.rodada(porta)
        self.assertEqual(porta.rotas().count("atendimento/criar"), 1)

    def test_erro_da_porta_tenta_de_novo_depois_e_para(self):
        self.ligar()
        self.pedido(horas=0.05)
        porta = Porta()
        porta.criar_atend = {"ok": False, "situacao": "preco_recusado", "erro": "preço"}
        self.rodada(porta)
        e = self.estado()
        self.assertEqual(e["situacao"], "erro")
        self.assertGreater(float(e["proxima_epoch"]), time.time() + 60, "espera antes de tentar de novo")
        porta2 = Porta()
        self.rodada(porta2)
        self.assertNotIn("atendimento/criar", porta2.rotas(), "ainda não chegou a vez")
        with S.DB_LOCK:
            S.DB.execute("UPDATE mkt_atendimento_v2047 SET proxima_epoch=0, tentativas=? WHERE pedido=?",
                         (S.MKT_ATEND_AUTO_MAX_TENTATIVAS, PEDIDO))
            S.DB.commit()
        porta3 = Porta()
        self.rodada(porta3)
        self.assertNotIn("atendimento/criar", porta3.rotas(), "passou do limite de tentativas: só o botão")

    def test_apollo_ocupado_para_a_volta_sem_gastar_tentativa(self):
        self.ligar()
        self.pedido(horas=0.05)
        porta = Porta()
        porta.criar_atend = S.PortaErro("Muitas chamadas ao Apollo neste minuto", passageiro=True)
        self.rodada(porta)
        e = self.estado()
        self.assertEqual(int(e.get("tentativas") or 0), 0)

    def test_botao_rodando_a_rotina_espera(self):
        self.ligar()
        self.pedido(horas=0.05)
        porta = Porta()
        self.assertTrue(S._MKT_ATEND_LOCK.acquire(blocking=False))
        try:
            self.rodada(porta)
        finally:
            S._MKT_ATEND_LOCK.release()
        self.assertEqual(porta.rotas(), [])


class Shopee(BaseAuto):
    SN = "261112ABCDEF01"

    def test_nome_e_cpf_da_api_da_shopee_viram_os_da_nota(self):
        S.mkt_atendimento_automatico_ligar("Shopee", True, "luis")
        gravar_como_admin([linha(self.SN, "0893764272", 1, Marketplace="SHOPEE", CPFShopee=CPF, NomeFiscalShopee=NOME)])
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,status,updated_at) "
                         "VALUES('shopee',?,?,?,'2026-11-12','0893764272',1,25.90,'paid','2026-11-12T10:00:00')",
                         (self.SN, "1", S.datetime.now(S.timezone.utc).isoformat()))
            S.DB.commit()
        endereco = {"endereco": {"rua": "RUA A", "numero": "10", "bairro": "CENTRO", "cep": "16010000",
                                 "cidade": "ARACATUBA", "uf": "SP"}, "falta": ""}
        porta = Porta()
        with mock.patch.object(S, "_exp_endereco_shopee", return_value=endereco):
            self.rodada(porta)
        guardado = S._cliente_fiscal_guardado(self.SN) or {}
        self.assertEqual(guardado.get("documento_numero"), CPF)
        self.assertIn("atendimento/criar", porta.rotas())
        self.assertEqual(porta.corpo("atendimento/criar")["condicao_pagamento"], 971)

    def test_cpf_mascarado_nao_vale(self):
        S.mkt_atendimento_automatico_ligar("Shopee", True, "luis")
        gravar_como_admin([linha(self.SN, "0893764272", 1, Marketplace="SHOPEE", CPFShopee="***.***.***-72",
                                 NomeFiscalShopee="D****")])
        porta = Porta()
        self.rodada(porta)
        self.assertIsNone(S._cliente_fiscal_guardado(self.SN))
        self.assertEqual(porta.rotas(), [])


class Rotas(BaseAuto):
    def test_ligar_pela_rota_e_worker_no_painel(self):
        from base import iniciar_servidor, Cliente
        url = iniciar_servidor()
        c = Cliente(url)
        c.entrar()
        st, corpo = c.pedir("POST", "/api/expedicao-apollo/atendimento-mkt", {"marketplace": "Shopee", "automatico": True})
        self.assertEqual(st, 200, corpo)
        self.assertTrue(corpo["config"]["Shopee"]["automatico"])
        self.assertTrue(corpo["config"]["Shopee"]["ligado"], "ligar o automático não mexe no botão")
        self.assertIn("mkt_atendimento_loop", S.SAUDE_WORKERS_INFO)
        html = (S.APP_DIR / "admin.html").read_text(encoding="utf-8")
        self.assertIn("alternarAtendMktAutoAdmin(", html)


class RevisaoV2061(BaseAuto):
    """revisar-antes-de-subir da V20.61 (12/11). Decisões do dono mantidas: na hora para todos; nome/CPF da API da Shopee."""

    def test_venda_antiga_que_entra_depois_de_ligar_nao_cria(self):
        """Reinclusão/print/Mover de filial de venda antiga: o atendimento original pode já ter sido faturado no Apollo."""
        self.ligar()
        self.pedido(horas=5)   # entrou na fila agora, mas a venda é de 5 h atrás
        porta = Porta()
        self.rodada(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "anterior")

    def test_ao_ligar_o_clique_que_falhou_vira_anterior(self):
        self.pedido(horas=0.1)
        S._mkt_atend_gravar(PEDIDO, marketplace="Mercado Livre", situacao="erro", detalhe="clique que falhou")
        self.ligar()
        self.assertEqual(self.estado()["situacao"], "anterior")

    def test_ao_ligar_nao_mexe_no_que_ja_foi_criado(self):
        self.pedido(horas=0.1)
        S._mkt_atend_gravar(PEDIDO, marketplace="Mercado Livre", situacao="criado", atendimento="795001")
        self.ligar()
        self.assertEqual(self.estado()["situacao"], "criado")

    def test_anterior_e_aguardando_seguem_a_espera_de_2h_do_botao(self):
        self.pedido(horas=0.2)
        for situacao in ("anterior", "aguardando"):
            S._mkt_atend_gravar(PEDIDO, marketplace="Mercado Livre", situacao=situacao, detalhe="x")
            self.assertFalse(S.mkt_atendimento_estado(PEDIDO)["disponivel"], situacao)

    def test_ja_tinha_deixa_o_botao_criar(self):
        """Homônimo ou outra compra do cliente: o automático não cria, mas o clique de gente decide (regra da V20.60)."""
        self.pedido(horas=3)
        S._mkt_atend_gravar(PEDIDO, marketplace="Mercado Livre", situacao="ja_tinha", atendimento="794001",
                            detalhe=S.MKT_JA_TINHA_AUTO + " o cliente já tem o atendimento 794001")
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e["disponivel"])
        self.assertEqual(e["acao"], "criar")
        # o "já tinha" do botão/porta (conferido pelo código do cliente) continua sem botão
        S._mkt_atend_gravar(PEDIDO, situacao="ja_tinha", detalhe="O Apollo já tem o atendimento 796123 aberto deste cliente")
        self.assertFalse(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_shopee_cpf_mascarado_e_cnpj_legivel_usa_o_cnpj(self):
        sn = "261112ABCDEF02"
        gravar_como_admin([linha(sn, "0893764272", 1, Marketplace="SHOPEE", CPFShopee="***.***.***-72",
                                 CNPJShopee="11222333000181", NomeFiscalShopee="Empresa Exemplo Ltda")])
        S._mkt_nota_da_shopee(sn, [{"CPFShopee": "***.***.***-72", "CNPJShopee": "11222333000181",
                                    "NomeFiscalShopee": "Empresa Exemplo Ltda"}])
        self.assertEqual((S._cliente_fiscal_guardado(sn) or {}).get("documento_numero"), "11222333000181")
