"""V19.30 — botão do romaneio/caixa (e cadastro, desligado) pelas portas da Expedição.

Cada regra aqui saiu de um teste real com o Apollo em 15/10/2026:
  - o caixa sem o código do cliente dá Erro 2619 (UF vazia) — o cliente vai sempre;
  - o romaneio leva a observação digitada (vazia não sai);
  - romaneio e caixa nunca saem duas vezes pelo Noromak;
  - resposta perdida no caixa ("incerto") trava o próximo envio até alguém conferir;
  - o cadastro só roda com EXP_CADASTRO_API=1 e manda e-mail + telefone + celular juntos.
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from base import S, Cliente, iniciar_servidor, linha, gravar_como_admin, reiniciar_banco

PEDIDO = "2000018986686836"


class PortaFalsa:
    """Imita as respostas reais da porta (formatos copiados dos testes de 15/10)."""

    def __init__(self, abertos=None, previa=None, caixa_previa=None, enviar=None, soltar=None):
        self.chamadas: list[tuple[str, dict]] = []
        self.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83020, "nome": "THAYNARA CRISTINA  SILVA"}
        self.abertos = abertos if abertos is not None else [
            {"atendimento": 787601, "cliente": "THAYNARA CRISTINA  SILVA", "vendedor": {"usuario": 210, "nome": "LUIS"},
             "valor": 25.9, "aberto_em": "2026-10-14T20:11:21", "dias_aberto": 1}]
        self.previa = previa or {"ok": True, "itens": [{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0,
                                                        "a_soltar": 1.0}], "total_a_soltar": 1.0}
        self.caixa_previa = caixa_previa or {"ok": True, "valor": 25.9, "itens": [], "impedimentos": [], "pode_enviar": True,
                                             "validacoes": {"validacoes": {"passou": True}, "ipi": {"passou": True}}}
        self.enviar = enviar or {"ok": True, "situacao": "enviado", "mensagem_do_erp": "Itens enviados para frente de caixa."}
        self.soltar = soltar or {"ok": True, "situacao": "solto", "soltos": [{"codigo": "0893764272", "quantidade": 1.0}],
                                 "mensagens": ["Romaneio 124250 do contato 787601 gerado com sucesso!"]}

    def __call__(self, rota, corpo, escrita=False):
        self.chamadas.append((rota, dict(corpo)))
        if rota == "cliente/situacao":
            return {"ok": True, "cliente": corpo["cliente"], "nome": "THAYNARA CRISTINA  SILVA", "veredito": "consultar",
                    "situacao_credito": {"codigo": "0"}, "cadastro": {"codigo": "0"}, "motivos": []}
        if rota == "atendimentos/abertos":
            return {"ok": True, "total": len(self.abertos), "atendimentos": self.abertos}
        if rota == "romaneio/previa":
            return self.previa
        if rota == "romaneio/soltar":
            return self.soltar
        if rota == "caixa/previa":
            return self.caixa_previa
        if rota == "caixa/enviar":
            if isinstance(self.enviar, Exception):
                raise self.enviar
            return self.enviar
        if rota == "cliente/atualizar":
            return {"ok": True, "situacao": "atualizado", "campos": list(corpo)}
        if rota == "cliente/credito":
            return {"ok": True, "situacao": "ja_liberado"}
        if rota == "cliente/buscar":
            return self.buscar
        if rota == "cliente/criar":
            return {"ok": True, "situacao": "criado", "cliente": 99001}
        raise AssertionError(f"rota inesperada {rota}")

    def rotas(self):
        return [r for r, _ in self.chamadas]

    def corpo(self, rota):
        return next(c for r, c in reversed(self.chamadas) if r == rota)


def _pedido_com_cliente(codigo="83020", documento="06278073994", nome="Thaynara Cristina Silva"):
    gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE")])
    S.salvar_cliente_fiscal(PEDIDO, documento, nome, linx_cliente=codigo, fonte="teste", usuario="teste")


class RomaneioCaixaPelaPorta(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_cliente_conhecido_acha_o_atendimento_pelo_nome_do_apollo(self):
        _pedido_com_cliente()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["situacao"], "pronto")
        self.assertEqual(d["escolhido"], "787601")
        # A busca é pelo nome COMO ESTÁ NO APOLLO (não o da NF-e).
        self.assertEqual(porta.corpo("atendimentos/abertos")["cliente_nome"], "THAYNARA CRISTINA  SILVA")
        self.assertEqual(d["candidatos"][0]["bate"], 1)

    def test_sem_codigo_pede_o_numero_do_atendimento(self):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE")])
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
            self.assertEqual(d["situacao"], "informar_atendimento")
            self.assertEqual(porta.chamadas, [])
            d = S.exp_romaneio_preparar(PEDIDO, atendimento="787601")
        self.assertEqual(porta.corpo("atendimentos/abertos")["atendimento"], 787601)
        self.assertEqual(d["escolhido"], "787601")

    def test_v1931_sem_codigo_busca_pelo_nome_da_nfe(self):
        # Caso real 15/10: pedido 2000018991339082, NF-e "Lindomar Souza Dos Santos", sem código.
        _pedido_com_cliente(codigo="", nome="Lindomar Souza Dos Santos")
        porta = PortaFalsa()
        porta.buscar = {"ok": False, "erro": "busca fora"}  # V19.32: o nome é a reserva do CPF
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(porta.corpo("atendimentos/abertos")["cliente_nome"], "LINDOMAR SOUZA DOS SANTOS")
        self.assertNotIn("cliente/situacao", porta.rotas())
        self.assertEqual(d["escolhido"], "787601")  # peças batem

    def test_v1931_nome_completo_nao_acha_tenta_primeiro_e_ultimo(self):
        _pedido_com_cliente(codigo="", nome="Márcio Costa de Araujo")
        porta = PortaFalsa()
        porta.buscar = {"ok": False, "erro": "busca fora"}
        original = porta.__call__

        def so_no_curto(rota, corpo, escrita=False):
            if rota == "atendimentos/abertos" and corpo["cliente_nome"] != "MARCIO ARAUJO":
                porta.chamadas.append((rota, dict(corpo)))
                return {"ok": True, "atendimentos": []}
            return original(rota, corpo, escrita)

        with mock.patch.object(S, "_exp_porta", side_effect=so_no_curto):
            d = S.exp_romaneio_preparar(PEDIDO)
        nomes = [c["cliente_nome"] for r, c in porta.chamadas if r == "atendimentos/abertos"]
        self.assertEqual(nomes, ["MARCIO COSTA DE ARAUJO", "MARCIO ARAUJO"])
        self.assertEqual(d["busca_por_nome"], "MARCIO ARAUJO")
        self.assertEqual(d["escolhido"], "787601")

    def test_v1931_achado_pelo_nome_sem_peca_batendo_nao_escolhe(self):
        _pedido_com_cliente(codigo="", nome="Lindomar Souza Dos Santos")
        outra = {"ok": True, "itens": [{"codigo": "9999", "a_soltar": 1.0}], "total_a_soltar": 1.0}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(previa=outra)):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["escolhido"], "")

    def test_v1937_kit_com_peca_faltando_no_atendimento_nao_e_escolhido(self):
        # Caso real 15/10, Shopee 261015P6NPQQHV: kit 0893600022/0893764272, atendimento só com o 0893764272.
        gravar_como_admin([linha(PEDIDO, "0893600022/0893764272", Marketplace="SHOPEE")])
        S.salvar_cliente_fiscal(PEDIDO, "10638907640", "Victor Ribeiro Goncalves Pereira", linx_cliente="72870",
                                fonte="teste", usuario="teste")
        previa = {"ok": True, "itens": [{"codigo": "0893764272", "a_soltar": 1.0}], "total_a_soltar": 1.0}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(previa=previa)):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["candidatos"][0]["faltam"], ["0893600022"])
        self.assertEqual(d["escolhido"], "")

    def test_v1937_sku_do_ml_com_prefixo_nao_acusa_falta(self):
        gravar_como_admin([linha(PEDIDO, "157156-6008.005.005.00.R", Marketplace="MERCADO LIVRE")])
        S.salvar_cliente_fiscal(PEDIDO, "06278073994", "Fulano de Tal", linx_cliente="83020", fonte="teste", usuario="t")
        previa = {"ok": True, "itens": [{"codigo": "6008.005.005.00.R", "a_soltar": 1.0}], "total_a_soltar": 1.0}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(previa=previa)):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["candidatos"][0]["faltam"], [])
        self.assertEqual(d["escolhido"], "787601")

    def test_v1938_romaneio_ja_solto_no_apollo_libera_o_caixa(self):
        # Caso real 15/10, Shopee 261015M126M9WD: atendimento 787596 com a peça certa e nada a soltar.
        _pedido_com_cliente()
        ja_solto = {"ok": True, "itens": [{"codigo": "0893764272", "requisitada": 1.0, "a_soltar": 0.0}], "total_a_soltar": 0.0}
        porta = PortaFalsa(previa=ja_solto)
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
            self.assertEqual(d["situacao"], "romaneio_fora")
            self.assertEqual(d["romaneio_fora"], "787601")
            p = S.exp_caixa_previa(PEDIDO, d["romaneio_fora"], "", "luis")
        self.assertTrue(p["pode"])
        self.assertEqual(porta.corpo("caixa/previa")["atendimento"], 787601)

    def test_dois_atendimentos_quem_escolhe_e_a_pessoa(self):
        _pedido_com_cliente()
        dois = [{"atendimento": 1, "valor": 10}, {"atendimento": 2, "valor": 20}]
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(abertos=dois)):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["situacao"], "escolher")
        self.assertEqual(d["escolhido"], "")

    def test_pecas_que_nao_batem_nao_sao_escolhidas_sozinhas(self):
        _pedido_com_cliente()
        outra = {"ok": True, "itens": [{"codigo": "9999", "a_soltar": 1.0}], "total_a_soltar": 1.0}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(previa=outra)):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["escolhido"], "")

    def test_observacao_e_obrigatoria_e_romaneio_sai_uma_vez(self):
        _pedido_com_cliente()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            self.assertFalse(S.exp_romaneio_soltar(PEDIDO, "787601", "  ", "luis")["ok"])
            self.assertNotIn("romaneio/soltar", porta.rotas())
            r = S.exp_romaneio_soltar(PEDIDO, "787601", "mercado livre 20/10", "luis")
            self.assertTrue(r["ok"])
            self.assertEqual(porta.corpo("romaneio/soltar")["observacao"], "MERCADO LIVRE 20/10")
            self.assertEqual(porta.corpo("romaneio/soltar")["operador"], "luis")
            de_novo = S.exp_romaneio_soltar(PEDIDO, "787601", "MERCADO LIVRE", "luis")
        self.assertFalse(de_novo["ok"])
        self.assertEqual(porta.rotas().count("romaneio/soltar"), 1)

    def test_caixa_manda_sempre_o_cliente(self):
        _pedido_com_cliente()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.exp_romaneio_soltar(PEDIDO, "787601", "MERCADO LIVRE", "luis")
            p = S.exp_caixa_previa(PEDIDO, "", "", "luis")
        self.assertTrue(p["pode"])
        corpo = porta.corpo("caixa/previa")
        self.assertEqual(corpo["cliente"], 83020)
        self.assertEqual(corpo["atendimento"], 787601)  # do romaneio solto
        self.assertIs(corpo["validar"], True)

    def test_sem_codigo_do_cliente_o_caixa_nao_chama_o_apollo(self):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE")])
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.exp_caixa_previa(PEDIDO, "787601", "", "luis")
        self.assertFalse(r["ok"])
        self.assertIn("2619", r["error"])
        self.assertEqual(porta.chamadas, [])

    def test_ipi_recusado_nao_libera_o_envio(self):
        _pedido_com_cliente()
        ruim = {"ok": True, "valor": 25.9, "pode_enviar": True, "impedimentos": [],
                "validacoes": {"validacoes": {"passou": True}, "ipi": {"passou": False, "texto": "não devolveu verdadeiro"}}}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(caixa_previa=ruim)):
            p = S.exp_caixa_previa(PEDIDO, "787601", "", "luis")
        self.assertFalse(p["pode"])
        self.assertTrue(any("ipi" in f for f in p["falhas"]))

    def test_envio_leva_o_valor_visto_e_nao_repete(self):
        _pedido_com_cliente()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.exp_romaneio_soltar(PEDIDO, "787601", "MERCADO LIVRE", "luis")
            r = S.exp_caixa_enviar(PEDIDO, "", "", 25.9, "luis")
            self.assertTrue(r["ok"])
            self.assertEqual(porta.corpo("caixa/enviar")["valor_visto"], 25.9)
            self.assertEqual(porta.corpo("caixa/enviar")["cliente"], 83020)
            de_novo = S.exp_caixa_enviar(PEDIDO, "", "", 25.9, "luis")
        self.assertFalse(de_novo["ok"])
        self.assertEqual(porta.rotas().count("caixa/enviar"), 1)

    def test_resposta_perdida_trava_ate_conferir(self):
        _pedido_com_cliente()
        porta = PortaFalsa(enviar=S.PortaErro("sem resposta", incerto=True))
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.exp_romaneio_soltar(PEDIDO, "787601", "MERCADO LIVRE", "luis")
            r = S.exp_caixa_enviar(PEDIDO, "", "", 25.9, "luis")
            self.assertEqual(r["situacao"], "incerto")
            travado = S.exp_caixa_enviar(PEDIDO, "", "", 25.9, "luis")
            self.assertEqual(travado["situacao"], "travado")
            self.assertEqual(porta.rotas().count("caixa/enviar"), 1)
            porta.enviar = {"ok": True, "situacao": "enviado"}
            ok = S.exp_caixa_enviar(PEDIDO, "", "", 25.9, "luis", conferido=True)
        self.assertTrue(ok["ok"])

    def test_condicao_de_pagamento_faltando_vira_instrucao(self):
        _pedido_com_cliente()
        recusa = {"ok": False, "situacao": "recusado",
                  "erro": "HTTP 412: Necessário informar a(s) forma(s) de pagamento no botão Condições de Pagamento!"}
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa(enviar=recusa)):
            r = S.exp_caixa_enviar(PEDIDO, "787601", "", 25.9, "luis")
        self.assertFalse(r["ok"])
        self.assertIn("condição de pagamento", r["error"])


class CadastroPelaPorta(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_desligado_nao_chama_a_porta(self):
        _pedido_com_cliente()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", False):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        self.assertEqual(r["situacao"], "desligado")
        self.assertEqual(porta.chamadas, [])

    def test_ligado_manda_os_tres_contatos_e_o_nome_limpo(self):
        _pedido_com_cliente(nome="Thaynara  Cristína Silva")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        self.assertTrue(r["ok"])
        corpo = porta.corpo("cliente/atualizar")
        for campo in ("email", "telefone", "celular", "ddd_telefone", "ddd_celular"):
            self.assertIn(campo, corpo)
        self.assertEqual(corpo["nome"], "THAYNARA CRISTINA SILVA")
        self.assertEqual(porta.corpo("cliente/credito")["situacao_credito"], "1")


class PortaNomeAbreviado(PortaFalsa):
    def __init__(self, nome_apollo, **kw):
        super().__init__(**kw)
        self.nome_apollo = nome_apollo
        self.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83020, "nome": nome_apollo}

    def __call__(self, rota, corpo, escrita=False):
        r = super().__call__(rota, corpo, escrita)
        return {**r, "nome": self.nome_apollo} if rota == "cliente/situacao" else r


class NomeAbreviadoV2009(unittest.TestCase):
    """V20.09: nome da NF-e abreviado ("NAYARA C", venda 2000019255236640). Revisão de 02/11."""

    def setUp(self):
        reiniciar_banco()

    def cadastrar(self, porta, **kw):
        _pedido_com_cliente(nome=kw.pop("nome", "NAYARA C"), **kw)
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True):
            return S.exp_cadastro_atualizar(PEDIDO, "luis")

    def test_nao_encurta_o_nome_completo_do_apollo(self):
        porta = PortaNomeAbreviado("NAYARA CRISTINA SOUZA")
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("nome", porta.corpo("cliente/atualizar"))
        self.assertIn("email", porta.corpo("cliente/atualizar"))
        self.assertTrue(any("NAYARA CRISTINA SOUZA" in a for a in r["avisos"]), r["avisos"])

    def test_nao_encurta_tambem_sem_o_codigo_guardado(self):
        porta = PortaNomeAbreviado("NAYARA CRISTINA SOUZA")
        self.cadastrar(porta, codigo="")
        self.assertNotIn("nome", porta.corpo("cliente/atualizar"))

    def test_nome_de_outra_pessoa_no_apollo_segue_a_nfe(self):
        porta = PortaNomeAbreviado("PREMIUM FLEX")
        self.cadastrar(porta)
        self.assertEqual(porta.corpo("cliente/atualizar")["nome"], "NAYARA C")

    def test_nome_completo_nao_gasta_chamada_de_busca(self):
        porta = PortaFalsa()
        self.cadastrar(porta, nome="Thaynara Cristina Silva")
        self.assertNotIn("cliente/buscar", porta.rotas())

    def test_nome_abreviado_no_apollo_nao_escolhe_o_atendimento_sozinho(self):
        _pedido_com_cliente(nome="NAYARA C")
        porta = PortaNomeAbreviado("NAYARA C")
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["escolhido"], "")
        self.assertEqual(d["situacao"], "escolher")
        self.assertTrue(d["nome_abreviado"])

    def test_nome_completo_no_apollo_continua_escolhendo(self):
        _pedido_com_cliente(nome="NAYARA C")
        porta = PortaNomeAbreviado("NAYARA CRISTINA SOUZA")
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            self.assertEqual(S.exp_romaneio_preparar(PEDIDO)["escolhido"], "787601")

    def test_faturamento_manda_o_nome_abreviado_para_a_atencao(self):
        base = {"nome_abreviado": True, "cadastro": {}, "credito": {"veredito": "consultar"}}
        cand = {"atendimento": "787601", "valor": 25.9, "bate": 1, "faltam": []}
        with mock.patch.object(S, "_exp_estado_operacoes", return_value={"romaneio": None, "caixa": None}):
            r = S.faturamento_classificar(PEDIDO, {**base, "candidatos": [{**cand, "total_a_soltar": 1}]}, 25.9)
            self.assertEqual(r["situacao"], "atencao")
            self.assertIn("abreviado", r["motivo"])
            r = S.faturamento_classificar(PEDIDO, {**base, "romaneio_fora": "787601",
                                                   "candidatos": [{**cand, "total_a_soltar": 0}]}, 25.9)
            self.assertEqual(r["situacao"], "atencao")


ENVIO_ML = {"receiver_address": {
    "street_name": "Avenida Maranhão", "street_number": "915", "comment": "Casa 2",
    "neighborhood": {"name": "Zona 01"}, "zip_code": "86870-250",
    "city": {"name": "Ivaiporã"}, "state": {"id": "BR-PR", "name": "Paraná"}}}


def _pedido_ml_com_envio(codigo="", documento="06278073994", nome="Thaynara Cristina Silva"):
    gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506")])
    S.salvar_cliente_fiscal(PEDIDO, documento, nome, linx_cliente=codigo, fonte="teste", usuario="teste")


class CadastroV1932(unittest.TestCase):
    """Contrato real do Guilherme (15/10): buscar → criar → atualizar com nome e endereço."""

    def setUp(self):
        reiniciar_banco()

    def _rodar(self, porta, envio=ENVIO_ML):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=envio) as ml:
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        return r, ml

    def test_endereco_vem_dos_dados_do_envio_sem_acento(self):
        _pedido_ml_com_envio(codigo="83020")
        porta = PortaFalsa()
        r, ml = self._rodar(porta)
        self.assertTrue(r["ok"], r)
        ml.assert_called_once_with("/shipments/48263508506")
        self.assertEqual(porta.corpo("cliente/atualizar")["endereco"], {
            "rua": "AVENIDA MARANHAO", "numero": "915", "complemento": "CASA 2", "bairro": "ZONA 01",
            "cep": "86870250", "cidade": "IVAIPORA", "uf": "PR"})

    def test_celular_padrao_e_o_que_a_criacao_aceita(self):
        _pedido_ml_com_envio(codigo="83020")
        porta = PortaFalsa()
        self._rodar(porta)
        corpo = porta.corpo("cliente/atualizar")
        self.assertGreaterEqual(corpo["celular"], 900000000)
        self.assertEqual(corpo["email"], "lojavirtual@noromak.com.br")

    def test_sem_codigo_busca_pelo_cpf_e_nao_cria_quem_existe(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        r, _ = self._rodar(porta)
        self.assertEqual(r["cliente"], "83020")
        self.assertEqual(porta.corpo("cliente/buscar")["documento"], "06278073994")
        self.assertNotIn("cliente/criar", porta.rotas())
        self.assertEqual(S._exp_cliente_do_pedido(PEDIDO)["codigo"], "83020")  # guardado

    def test_nao_cadastrado_cria_com_endereco_e_depois_atualiza(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        r, _ = self._rodar(porta)
        self.assertEqual(r["situacao"], "criado")
        self.assertEqual(r["cliente"], "99001")
        criar = porta.corpo("cliente/criar")
        self.assertEqual(criar["nome"], "THAYNARA CRISTINA SILVA")
        self.assertEqual(criar["endereco"]["cep"], "86870250")
        self.assertNotIn("telefone", criar)  # fixo é opcional na criação
        self.assertEqual(porta.corpo("cliente/atualizar")["cliente"], 99001)

    def test_nao_cadastrado_sem_endereco_inteiro_nao_cria(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        sem_bairro = {"receiver_address": dict(ENVIO_ML["receiver_address"], neighborhood={})}
        r, _ = self._rodar(porta, envio=sem_bairro)
        self.assertEqual(r["situacao"], "sem_endereco")
        self.assertIn("bairro", r["error"])
        self.assertNotIn("cliente/criar", porta.rotas())

    def test_cliente_existente_sem_endereco_inteiro_atualiza_o_resto_e_avisa(self):
        _pedido_ml_com_envio(codigo="83020")
        porta = PortaFalsa()
        r, _ = self._rodar(porta, envio={"receiver_address": {"street_name": "Rua X"}})
        self.assertTrue(r["ok"])
        self.assertNotIn("endereco", porta.corpo("cliente/atualizar"))
        self.assertTrue(any("Endereço não enviado" in a for a in r["avisos"]))

    def test_mais_de_um_cliente_devolve_a_lista_e_nao_escolhe(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "mais_de_um", "clientes": [{"cliente": 1}, {"cliente": 2}]}
        r, _ = self._rodar(porta)
        self.assertEqual(r["situacao"], "mais_de_um")
        self.assertEqual(len(r["lista"]), 2)
        self.assertNotIn("cliente/atualizar", porta.rotas())

    def test_busca_que_falha_nunca_vira_nao_cadastrado(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        porta.buscar = {"ok": False, "erro": "Apollo fora"}
        r, _ = self._rodar(porta)
        self.assertFalse(r["ok"])
        self.assertNotIn("cliente/criar", porta.rotas())

    def test_v1933_rotulo_lido_como_nome_nao_grava(self):
        # Caso real 15/10, pedido 2000018994284906 (CNPJ): a extensão leu "Tipo de contribuinte".
        _pedido_ml_com_envio(codigo="83020", documento="59398838000128", nome="Tipo de contribuinte")
        porta = PortaFalsa()
        r, _ = self._rodar(porta)
        self.assertEqual(r["situacao"], "nome_invalido")
        self.assertEqual(porta.chamadas, [])

    def test_v1933_ie_vai_na_criacao_do_cnpj(self):
        _pedido_ml_com_envio(documento="59398838000128", nome="Gabriel Amorim Hespindola Soares")
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis", ie="8005099728")
        self.assertTrue(r["ok"], r)
        self.assertEqual(porta.corpo("cliente/criar")["inscricao_estadual"], "8005099728")
        self.assertEqual(porta.corpo("cliente/criar")["categoria"], 3)

    def test_v1933_ie_de_cliente_existente_vira_aviso(self):
        _pedido_ml_com_envio(codigo="83020", documento="59398838000128", nome="Gabriel Amorim Hespindola Soares")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis", ie="8005099728")
        self.assertTrue(r["ok"], r)
        # V19.44: a porta agora grava I.E. e categoria também no atualizar.
        corpo = porta.corpo("cliente/atualizar")
        self.assertEqual((corpo["categoria"], corpo["inscricao_estadual"]), (3, "8005099728"))

    def _v1944(self, documento, usuario, ie=""):
        _pedido_ml_com_envio(codigo="83020", documento=documento, nome="Gabriel Amorim Hespindola Soares")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True),                 mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML):
            S.exp_cadastro_atualizar(PEDIDO, usuario, ie=ie)
        return porta.corpo("cliente/atualizar")

    def test_v1944_cnpj_sem_ie_pelo_botao_vai_isento(self):
        corpo = self._v1944("59398838000128", "luis")
        self.assertEqual((corpo["categoria"], corpo["inscricao_estadual"]), (2, "ISENTO"))

    def test_v1944_cpf_categoria_1_sem_ie(self):
        corpo = self._v1944("06278073994", "luis")
        self.assertEqual(corpo["categoria"], 1)
        self.assertNotIn("inscricao_estadual", corpo)

    def test_v1944_automatico_nao_mexe_na_ie_do_cnpj(self):
        corpo = self._v1944("59398838000128", "automatico")
        self.assertNotIn("categoria", corpo)
        self.assertNotIn("inscricao_estadual", corpo)

    def _shopee(self, recipient):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="SHOPEE")])
        S.salvar_cliente_fiscal(PEDIDO, "48500911808", "Leonardo Marques Coimbra Brandão", linx_cliente="83066",
                                fonte="teste", usuario="teste")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "shopee_order_details", return_value=[{"order_sn": PEDIDO, "recipient_address": recipient}]):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        return r, porta

    def test_v1935_shopee_endereco_sem_acento_e_bairro_centro(self):
        # Caso real 15/10, pedido 261015PK6V2UGV: a Shopee não mostra o bairro.
        r, porta = self._shopee({"full_address": "R Jesuíno Antônio, 342, Casa, Osasco, São Paulo, 06045080",
                                 "city": "Osasco", "state": "São Paulo", "zipcode": "06045080"})
        self.assertTrue(r["ok"], r)
        corpo = porta.corpo("cliente/atualizar")
        self.assertEqual(corpo["nome"], "LEONARDO MARQUES COIMBRA BRANDAO")
        self.assertEqual(corpo["endereco"], {"rua": "R JESUINO ANTONIO", "numero": "342", "complemento": "CASA",
                                             "bairro": "CENTRO", "cep": "06045080", "cidade": "OSASCO", "uf": "SP"})
        self.assertTrue(any("CENTRO" in a for a in r["avisos"]))

    def test_v1935_shopee_bairro_da_api_vale_quando_vem(self):
        r, porta = self._shopee({"full_address": "Avenida Amazonas, 95, Apartamento 202, Timóteo, Minas Gerais, 35180628",
                                 "district": "Centro Norte", "city": "Timóteo", "state": "Minas Gerais", "zipcode": "35180-628"})
        end = porta.corpo("cliente/atualizar")["endereco"]
        self.assertEqual((end["bairro"], end["cidade"], end["uf"], end["complemento"]),
                         ("CENTRO NORTE", "TIMOTEO", "MG", "APARTAMENTO 202"))

    def test_v1935_shopee_mascarado_nao_vai(self):
        r, porta = self._shopee({"full_address": "****", "city": "Osasco", "state": "São Paulo", "zipcode": "06045080"})
        self.assertNotIn("endereco", porta.corpo("cliente/atualizar"))
        self.assertTrue(any("Endereço não enviado" in a for a in r["avisos"]))

    def test_v1936_shopee_api_mascarada_usa_o_endereco_da_pagina(self):
        # Caso real 15/10 (261015PK6V2UGV): a API devolveu o endereço mascarado.
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="SHOPEE")])
        S.salvar_cliente_fiscal(PEDIDO, "48500911808", "Leonardo Marques Coimbra Brandão", linx_cliente="83066",
                                fonte="teste", usuario="teste")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "shopee_order_details", return_value=[{"recipient_address": {"full_address": "****"}}]):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis",
                                         endereco_texto="R Jesuíno Antônio, 342, Casa, Osasco, São Paulo, 06045080")
        self.assertTrue(r["ok"], r)
        self.assertEqual(porta.corpo("cliente/atualizar")["endereco"], {
            "rua": "R JESUINO ANTONIO", "numero": "342", "complemento": "CASA", "bairro": "CENTRO",
            "cep": "06045080", "cidade": "OSASCO", "uf": "SP"})

    def test_v1936_texto_da_pagina(self):
        e = S.exp_endereco_do_texto("Avenida Amazonas, 95, Apartamento 202, Timóteo, Minas Gerais, 35180628")["endereco"]
        self.assertEqual((e["rua"], e["numero"], e["complemento"], e["cidade"], e["uf"], e["bairro"]),
                         ("AVENIDA AMAZONAS", "95", "APARTAMENTO 202", "TIMOTEO", "MG", "CENTRO"))
        self.assertIsNone(S.exp_endereco_do_texto("Rua X, ******89")["endereco"])

    def test_v1939_credito_que_nao_gravou_vira_aviso_no_topo(self):
        # Caso real 15/10: clientes criados pela API voltaram "nao_gravou" e o caixa pediu a função 257.
        _pedido_ml_com_envio(codigo="83068")
        porta = PortaFalsa()
        original = porta.__call__

        def credito_nao_grava(rota, corpo, escrita=False):
            if rota == "cliente/credito":
                porta.chamadas.append((rota, dict(corpo)))
                return {"ok": True, "situacao": "nao_gravou"}
            return original(rota, corpo, escrita)

        r, _ = self._rodar(credito_nao_grava)
        self.assertEqual(r["credito"], "nao_gravou")
        self.assertTrue(r["avisos"][0].startswith("CRÉDITO NÃO LIBERADO"))
        self.assertIn("257", r["avisos"][0])

    def test_romaneio_sem_codigo_acha_pelo_cpf(self):
        _pedido_ml_com_envio()
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["cliente"]["codigo"], "83020")
        self.assertIn("cliente/situacao", porta.rotas())  # nome do Apollo, não o da NF-e
        self.assertEqual(d["escolhido"], "787601")


class _PortaHttp(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.chave = self.headers.get("X-Chave-Expedicao")
        corpo = b'{"detail":"Not Found"}'
        # Tamanho e fim de conexão explícitos: sem eles a leitura às vezes falhava com a
        # máquina carregada e o teste caía como "resposta não voltou" (publicação de 16/10).
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


class PortaDeVerdade(unittest.TestCase):
    def test_404_vira_porta_nao_liberada_e_a_chave_vai_no_cabecalho(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _PortaHttp)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(S, "EXP_PORTA_BASE", f"http://127.0.0.1:{srv.server_address[1]}/api/v1/expedicao"), \
                    mock.patch.object(S, "CHAVE_CRM", "chave-teste"):
                with self.assertRaises(S.PortaErro) as cm:
                    S._exp_porta("romaneio/soltar", {"revenda": 1}, escrita=True)
            self.assertIn("não está liberada", str(cm.exception))
            self.assertFalse(cm.exception.incerto)
            self.assertEqual(srv.chave, "chave-teste")
        finally:
            srv.shutdown()

    def test_base_sai_da_url_do_custo(self):
        self.assertTrue(S.EXP_PORTA_BASE.endswith("/api/v1/expedicao"))


class Rotas(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        self.url = iniciar_servidor()

    def test_sem_login_401_e_admin_usa(self):
        anonimo = Cliente(self.url)
        status, _ = anonimo.pedir("GET", f"/api/expedicao-apollo/romaneio?pedido={PEDIDO}")
        self.assertEqual(status, 401)
        _pedido_com_cliente()
        admin = Cliente(self.url)
        admin.entrar()
        with mock.patch.object(S, "_exp_porta", side_effect=PortaFalsa()):
            status, d = admin.pedir("GET", f"/api/expedicao-apollo/romaneio?pedido={PEDIDO}")
            self.assertEqual(status, 200, d)
            self.assertEqual(d["escolhido"], "787601")
            status, d = admin.pedir("POST", "/api/expedicao-apollo/romaneio/soltar",
                                    {"pedido": PEDIDO, "atendimento": "787601", "observacao": "SHOPEE"})
            self.assertEqual(status, 200, d)
            with mock.patch.object(S, "EXP_CADASTRO_API", False):
                status, d = admin.pedir("POST", "/api/expedicao-apollo/cadastro", {"pedido": PEDIDO})
        self.assertEqual(status, 409)
        self.assertEqual(d["situacao"], "desligado")

    def test_tela_usa_a_rota_nova(self):
        html = (S.APP_DIR / "admin.html").read_text(encoding="utf-8")
        self.assertIn("pdExpRomaneioV1930(this)", html)
        self.assertNotIn("pdRomaneioLinxV1860(this)", html)


class NomeUnicoShopeeV2010(unittest.TestCase):
    """V20.10: a Shopee traz o nome inteiro numa palavra só (pedido 2610314H4FMAE7, "Jeoacaz")."""

    def setUp(self):
        reiniciar_banco()

    def pedido(self, nome="Jeoacaz", marketplace="SHOPEE", documento="04756305563", codigo="83020"):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace=marketplace)])
        return S.salvar_cliente_fiscal(PEDIDO, documento, nome, linx_cliente=codigo, fonte="teste", usuario="teste")

    def cadastrar(self, porta, **kw):
        self.assertTrue(self.pedido(**kw)["ok"])
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True):
            return S.exp_cadastro_atualizar(PEDIDO, "luis")

    def test_shopee_com_cpf_aceita_uma_palavra(self):
        self.assertTrue(self.pedido()["ok"])
        self.assertTrue(self.pedido(nome="Ana")["ok"])

    def test_mercado_livre_continua_exigindo_duas(self):
        self.assertFalse(self.pedido(marketplace="MERCADO LIVRE")["ok"])

    def test_empresa_continua_exigindo_duas(self):
        self.assertFalse(self.pedido(documento="59398838000110")["ok"])

    def test_rotulo_e_mascara_continuam_recusados(self):
        for nome in ("Observações", "Jeo*", "Jo", "123456"):
            self.assertFalse(self.pedido(nome=nome)["ok"], nome)

    def test_nao_encurta_o_nome_completo_do_apollo(self):
        porta = PortaNomeAbreviado("JEOACAZ SOUZA LIMA")
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("nome", porta.corpo("cliente/atualizar"))
        self.assertTrue(any("JEOACAZ SOUZA LIMA" in a for a in r["avisos"]), r["avisos"])

    def test_nome_de_outra_pessoa_no_apollo_segue_a_shopee(self):
        porta = PortaNomeAbreviado("PREMIUM FLEX")
        self.cadastrar(porta)
        self.assertEqual(porta.corpo("cliente/atualizar")["nome"], "JEOACAZ")

    def test_sem_saber_o_nome_do_apollo_nao_grava_por_cima(self):
        porta = PortaNomeAbreviado("")
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("nome", porta.corpo("cliente/atualizar"))

    def test_cliente_novo_e_criado_com_a_palavra(self):
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        end = {"endereco": {"rua": "RUA CANADA"}, "falta": ""}
        self.pedido(codigo="")
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True),                 mock.patch.object(S, "exp_endereco_entrega", return_value=end):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        self.assertEqual(porta.corpo("cliente/criar")["nome"], "JEOACAZ", r)

    def test_uma_palavra_nao_escolhe_o_atendimento_sozinho(self):
        self.pedido()
        porta = PortaNomeAbreviado("JEOACAZ")
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertEqual(d["escolhido"], "")
        self.assertTrue(d["nome_abreviado"])

    def test_empresa_de_uma_palavra_no_apollo_nao_trava(self):
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE")])
        S.salvar_cliente_fiscal(PEDIDO, "59398838000110", "Acme Pecas Ltda", linx_cliente="83020", fonte="teste", usuario="teste")
        porta = PortaNomeAbreviado("ACME")
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            self.assertFalse(S.exp_romaneio_preparar(PEDIDO)["nome_abreviado"])


if __name__ == "__main__":
    unittest.main()


class CodigoComHifen(unittest.TestCase):
    """V19.46: Schulz usa código com hífen ("816.3279-0"). O SKU do anúncio "124656-816.3279-0"
    era quebrado em todo hífen e nunca batia: "Peça do atendimento diferente do pedido"
    (pedido real 2000019013311172, 16/10)."""

    def setUp(self):
        reiniciar_banco()

    def test_codigo_com_hifen_bate(self):
        gravar_como_admin([linha(PEDIDO, "124656-816.3279-0", Marketplace="MERCADO LIVRE")])
        S.salvar_cliente_fiscal(PEDIDO, "03201119113", "Anderson Jose da Silva", linx_cliente="83148",
                                fonte="teste", usuario="teste")
        porta = PortaFalsa(previa={"ok": True, "itens": [{"codigo": "816.3279-0", "descricao": "CATRACA",
                                                          "requisitada": 1.0, "a_soltar": 1.0}], "total_a_soltar": 1.0})
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.exp_romaneio_preparar(PEDIDO)
        c = d["candidatos"][0]
        self.assertTrue(c["bate"], c)
        self.assertEqual(c["faltam"], [])
        self.assertEqual(d["escolhido"], "787601")


class CodigoComBarra(unittest.TestCase):
    """V19.48: "PSH367/80" é UM produto com barra no código, não um kit "A/B". O atendimento
    tinha "PSH367/80" e a tela dizia "Falta no atendimento: PSH367, 80" (pedido real
    2000019012332882, 16/10). O kit de verdade (peças separadas) continua exigindo cada peça."""

    def setUp(self):
        reiniciar_banco()

    def _preparar(self, sku, itens):
        gravar_como_admin([linha(PEDIDO, sku, Marketplace="MERCADO LIVRE")])
        S.salvar_cliente_fiscal(PEDIDO, "03201119113", "Armando Ciampolini Neto", linx_cliente="83138",
                                fonte="teste", usuario="teste")
        porta = PortaFalsa(previa={"ok": True, "itens": [{"codigo": c, "descricao": "X", "requisitada": 1.0, "a_soltar": 1.0}
                                                         for c in itens], "total_a_soltar": float(len(itens))})
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            return S.exp_romaneio_preparar(PEDIDO)["candidatos"][0]

    def test_codigo_com_barra_e_um_produto(self):
        c = self._preparar("PSH367/80", ["PSH367/80"])
        self.assertTrue(c["bate"], c)
        self.assertEqual(c["faltam"], [])

    def test_kit_de_verdade_continua_exigindo_cada_peca(self):
        c = self._preparar("0893600022/0893764272", ["0893764272"])
        self.assertEqual(c["faltam"], ["0893600022"])
