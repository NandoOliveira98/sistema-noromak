"""V20.47 — criar cliente e atendimento de ML/Shopee pelo painel (dono, 10/11).

Quando o cadastro automático passa 2 h sem achar o cliente no Apollo, o painel do romaneio (extensão Linx
Cliente) mostra o botão "Criar cliente e atendimento no Apollo": a mesma rotina do TikTok (V20.33/V20.43) —
cria o cliente, cria o atendimento pela porta `atendimento/criar` (chave ML-/SHOPEE-<pedido>, que nunca cria
dois) com as peças e o preço da venda e compromete as peças. Antes de criar, procura no Apollo um atendimento
aberto do cliente com as peças do pedido (a integração pode ter trazido a venda): achou = não cria outro.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
import unittest
from unittest import mock

from base import S, linha, gravar_como_admin, reiniciar_banco
from test_expedicao_apollo_v1930 import ENVIO_ML

PEDIDO = "2000019442143804"
CPF = "90972392572"
NOME = "Delvanio Fernandes Pereira"


class Porta:
    """Porta falsa: cliente, atendimentos abertos (para a busca do já existente), criar e comprometer."""

    def __init__(self, buscar="nao_cadastrado", abertos=None, previa_itens=None):
        self.chamadas: list[tuple[str, dict]] = []
        self.buscar = buscar
        self.abertos = abertos if abertos is not None else []
        self.previa_itens = previa_itens or []
        self.criar_atend = None
        self.comprometer = None

    def __call__(self, rota, corpo, escrita=False):
        self.chamadas.append((rota, dict(corpo)))
        if rota == "cliente/buscar":
            if self.buscar == "encontrado":
                return {"ok": True, "situacao": "encontrado", "cliente": 83070, "nome": NOME.upper()}
            return {"ok": True, "situacao": "nao_cadastrado"}
        if rota == "cliente/criar":
            return {"ok": True, "situacao": "criado", "cliente": 99001}
        if rota == "cliente/atualizar":
            return {"ok": True, "situacao": "atualizado"}
        if rota == "cliente/credito":
            return {"ok": True, "situacao": "ja_liberado"}
        if rota == "cliente/situacao":
            return {"ok": True, "cliente": corpo["cliente"], "nome": NOME.upper(), "veredito": "consultar",
                    "situacao_credito": {"codigo": "0"}, "cadastro": {"codigo": "0"}, "motivos": []}
        if rota == "atendimentos/abertos":
            return {"ok": True, "total": len(self.abertos), "atendimentos": self.abertos}
        if rota == "romaneio/previa":
            return {"ok": True, "itens": self.previa_itens,
                    "total_a_soltar": sum(float(i.get("a_soltar") or 0) for i in self.previa_itens)}
        if rota == "atendimento/criar":
            if isinstance(self.criar_atend, Exception):
                raise self.criar_atend
            if self.criar_atend is not None:
                return self.criar_atend
            return {"ok": True, "situacao": "criado", "atendimento": 795001, "revenda": corpo["revenda"],
                    "pedido_externo": corpo["pedido_externo"], "valor": corpo["valor_esperado"],
                    "itens": [dict(i, item=k + 1) for k, i in enumerate(corpo["itens"])]}
        if rota == "atendimento/comprometer":
            if isinstance(self.comprometer, Exception):
                raise self.comprometer
            if self.comprometer is not None:
                return self.comprometer
            return {"ok": True, "situacao": "comprometido", "atendimento": corpo["contato"],
                    "itens": [{"codigo": "0893764272", "comprometido": True}]}
        raise AssertionError(f"rota inesperada {rota}")

    def rotas(self):
        return [r for r, _ in self.chamadas]

    def corpo(self, rota):
        return next(c for r, c in self.chamadas if r == rota)


def _venda(pedido, sku, qtd, valor, line="1", horas=3.0):
    venda_em = datetime.fromtimestamp(time.time() - horas * 3600, timezone.utc).isoformat()
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,status,updated_at) "
                     "VALUES('ml',?,?,?,'2026-11-10',?,?,?,'paid','2026-11-10T10:00:00')",
                     (pedido, line, venda_em, sku, qtd, valor))
        S.DB.commit()


class Base(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        S.cadastro_auto_ligar(True, "admin")

    def pedido(self, marketplace="MERCADO LIVRE", sku="0893764272", qtd=1, valor=25.90, horas=3, **extra):
        gravar_como_admin([linha(PEDIDO, sku, qtd, Marketplace=marketplace, ShipmentIdMercadoLivre="48263508506", **extra)])
        S.salvar_cliente_fiscal(PEDIDO, CPF, NOME, fonte="api_ml", usuario="automatico")
        inicio = time.time() - horas * 3600
        S._cadastro_auto_registrar(PEDIDO, inicio, time.time() - 600, 5, "aguardando",
                                   "Cliente ainda não está no Apollo.", automatico=True)
        _venda(PEDIDO, sku, qtd, valor, horas=horas)

    def criar(self, porta, custos=None, **kw):
        custos = custos or {}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=ENVIO_ML), \
                mock.patch.object(S, "_tiktok_atendimento_custo", side_effect=lambda cod, ctx: custos.get(cod)), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}):
            return S.mkt_atendimento_iniciar(PEDIDO, "luis", em_fundo=False, **kw)

    def estado(self):
        return S.mkt_atendimento_do_pedido(PEDIDO) or {}


class QuandoAparece(Base):
    def test_antes_de_2h_nao_aparece(self):
        self.pedido(horas=1)
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertFalse(e["disponivel"])
        with self.assertRaises(ValueError):
            self.criar(Porta())

    def test_depois_de_2h_aparece(self):
        self.pedido(horas=3)
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_cliente_criado_pelo_noromak_aparece_na_hora(self):
        # V20.49 (dono, 10/11, pedido 2000015964547311): o painel CRIOU o cliente (ele não existia no Apollo, então a
        # integração ainda não trouxe a venda) — o botão aparece na hora, só sem atendimento aberto com as peças.
        self.pedido(horas=0.2)
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "pelo botão", "83070", cliente_criado=True)
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e["disponivel"])
        self.assertEqual(e["acao"], "criar")
        self.assertTrue(e["requer_sem_atendimento"])

    def test_cliente_que_ja_existia_espera_2h_da_venda(self):
        # o cliente já estava no Apollo (compra anterior): a venda nova cai pela integração — espera 2 h da venda.
        self.pedido(horas=0.2)
        with S.DB_LOCK:
            S.DB.execute("UPDATE vendas_v1799 SET sale_at=? WHERE order_id=?",
                         (datetime.fromtimestamp(time.time() - 600, timezone.utc).isoformat(), PEDIDO))
            S.DB.commit()
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "pelo botão", "83070")
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertFalse(e["disponivel"], "REGRESSÃO: botão na hora para toda venda com cliente antigo")
        self.assertIn("aparece às", e["motivo"])
        with S.DB_LOCK:
            S.DB.execute("UPDATE vendas_v1799 SET sale_at=? WHERE order_id=?",
                         (datetime.fromtimestamp(time.time() - 3 * 3600, timezone.utc).isoformat(), PEDIDO))
            S.DB.commit()
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e["disponivel"])
        self.assertTrue(e["requer_sem_atendimento"])

    def test_marca_continua_depois_de_um_clique_sem_sucesso(self):
        self.pedido(horas=0.2)
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "pelo botão", "83070", cliente_criado=True)
        S._mkt_atend_gravar(PEDIDO, situacao="revisar", detalhe="A venda ainda não foi copiada")
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e["disponivel"])
        self.assertTrue(e["requer_sem_atendimento"], "REGRESSÃO: a marca sumiu e o botão voltaria com o atendimento na tela")

    def test_romaneio_ja_solto_no_apollo_nao_cria(self):
        self.pedido(horas=3)
        S.salvar_cliente_fiscal(PEDIDO, CPF, NOME, linx_cliente="83070", fonte="porta", usuario="t")
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "pelo botão", "83070")
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 0.0}])
        self.criar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "ja_tinha")

    def test_cliente_ja_cadastrado_com_atendimento_da_integracao_nao_cria(self):
        self.pedido(horas=0.2)
        S._cadastro_auto_registrar(PEDIDO, time.time(), time.time(), 0, "feito", "pelo botão", "83070", cliente_criado=True)
        S.salvar_cliente_fiscal(PEDIDO, CPF, NOME, linx_cliente="83070", fonte="porta", usuario="t")
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        self.criar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "ja_tinha")

    def test_magalu_e_tiktok_ficam_de_fora(self):
        self.pedido(marketplace="MAGALU", horas=3)
        self.assertFalse(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_pedido_fora_da_fila_nao_aparece(self):
        self.assertFalse(S.mkt_atendimento_estado("999")["disponivel"])


class Criar(Base):
    def test_cria_cliente_atendimento_e_compromete(self):
        self.pedido()
        porta = Porta()
        r = self.criar(porta)
        self.assertTrue(r["ok"], r)
        self.assertEqual([x for x in porta.rotas() if x in ("cliente/criar", "atendimento/criar", "atendimento/comprometer")],
                         ["cliente/criar", "atendimento/criar", "atendimento/comprometer"])
        corpo = porta.corpo("atendimento/criar")
        self.assertEqual((corpo["origem"], corpo["pedido_externo"], corpo["revenda"], corpo["cliente"]),
                         ("MERCADOLIVRE", f"ML-{PEDIDO}", 1, 99001))
        self.assertEqual(corpo["itens"], [{"codigo": "0893764272", "quantidade": 1, "unitario": 25.9}])
        self.assertEqual(corpo["valor_esperado"], 25.9)
        # V20.51 (Guilherme, 11/11): ML/Shopee só abrem com `manual: true` (clique de gente no painel).
        self.assertIs(corpo.get("manual"), True)
        e = self.estado()
        self.assertEqual((e["situacao"], e["atendimento"], e["comprometido"]), ("criado", "795001", "sim"))
        self.assertEqual(S.cadastro_auto_do_pedido(PEDIDO)["situacao"], "feito")
        self.assertIn(S.normalize(PEDIDO), S._anuncio_pedidos_com_atendimento())

    def test_shopee_vai_com_o_endereco_da_pagina_e_condicao_971(self):
        self.pedido(marketplace="SHOPEE")
        porta = Porta()
        with mock.patch.object(S, "shopee_order_details", return_value=[]):
            r = self.criar(porta, endereco_texto="Rua das Flores, 120, Apto 3, Bauru, São Paulo, 17012-000", bairro="Centro")
        self.assertTrue(r["ok"], r)
        corpo = porta.corpo("atendimento/criar")
        self.assertEqual((corpo["origem"], corpo["pedido_externo"], corpo["condicao_pagamento"]),
                         ("SHOPEE", f"SHOPEE-{PEDIDO}", 971))
        self.assertIn("endereco", porta.corpo("cliente/criar"))

    def test_kit_divide_o_preco_pelo_custo(self):
        self.pedido(sku="0893600022/0893764272", valor=40.00)
        porta = Porta()
        porta.comprometer = {"ok": True, "situacao": "ja_comprometido", "itens": []}
        r = self.criar(porta, custos={"0893600022": 30.0, "0893764272": 10.0})
        self.assertTrue(r["ok"], r)
        itens = porta.corpo("atendimento/criar")["itens"]
        self.assertEqual(sorted((i["codigo"], i["unitario"]) for i in itens), [("0893600022", 30.0), ("0893764272", 10.0)])

    def test_atendimento_da_integracao_ja_existe_nao_cria_outro(self):
        self.pedido()
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        r = self.criar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertNotIn("atendimento/comprometer", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "ja_tinha")
        self.assertIn("790001", self.estado()["detalhe"])
        self.assertFalse(r["ok"])

    def test_fila_diferente_da_venda_vai_para_revisar(self):
        self.pedido()
        _venda(PEDIDO, "0893600022", 1, 10.0, line="2")   # a venda tem uma peça que a fila não tem
        porta = Porta()
        r = self.criar(porta)
        self.assertEqual(self.estado()["situacao"], "revisar")
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertFalse(r["ok"])

    def test_sem_a_venda_copiada_vai_para_revisar(self):
        self.pedido()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM vendas_v1799")
            S.DB.commit()
        porta = Porta()
        self.criar(porta)
        self.assertEqual(self.estado()["situacao"], "revisar")
        self.assertEqual(porta.chamadas, [])

    def test_comprometer_que_falhou_o_botao_so_compromete(self):
        self.pedido()
        porta = Porta()
        porta.comprometer = {"ok": True, "situacao": "comprometido",
                             "itens": [{"codigo": "0893764272", "comprometido": False, "motivo": "sem saldo"}]}
        self.criar(porta)
        self.assertEqual(self.estado()["comprometido"], "parcial")
        porta2 = Porta(buscar="encontrado")
        self.criar(porta2)
        self.assertEqual(porta2.rotas(), ["atendimento/comprometer"])
        self.assertEqual(self.estado()["comprometido"], "sim")

    def test_comprometido_nao_faz_mais_nada(self):
        self.pedido()
        self.criar(Porta())
        with self.assertRaises(ValueError):
            self.criar(Porta())

    def test_resposta_perdida_fica_incerto_e_o_botao_tenta_de_novo(self):
        self.pedido()
        porta = Porta()
        porta.criar_atend = S.PortaErro("sem resposta", incerto=True)
        self.criar(porta)
        self.assertEqual(self.estado()["situacao"], "incerto")
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"])
        porta2 = Porta(buscar="encontrado")
        r = self.criar(porta2)
        self.assertTrue(r["ok"], r)
        self.assertEqual(porta2.corpo("atendimento/criar")["pedido_externo"], f"ML-{PEDIDO}")

    def test_romaneio_ja_solto_nao_cria(self):
        self.pedido()
        with mock.patch.object(S, "_tiktok_atendimento_ja_atendido", return_value=True):
            self.assertFalse(S.mkt_atendimento_estado(PEDIDO)["disponivel"])
            with self.assertRaises(ValueError):
                self.criar(Porta())

    def test_painel_recebe_o_estado(self):
        self.pedido()
        with mock.patch.object(S, "_exp_porta", side_effect=Porta()):
            d = S.exp_romaneio_preparar(PEDIDO)
        self.assertTrue(d["atendimento_mkt"]["disponivel"])


class RevisaoBotao(Base):
    """revisar-antes-de-subir do botão (10/11, "corrigir todos")."""

    def test_acompanhamento_da_extensao_nao_vai_ao_apollo(self):
        self.pedido()
        porta = Porta()
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            d = S.mkt_atendimento_painel(PEDIDO)
        self.assertEqual(porta.chamadas, [])
        self.assertTrue(d["atendimento_mkt"]["disponivel"])

    def test_depois_do_incerto_vai_direto_ao_criar_e_compromete(self):
        self.pedido()
        porta = Porta()
        porta.criar_atend = S.PortaErro("sem resposta", incerto=True)
        self.criar(porta)
        # o Apollo criou: o 2º clique acha o nosso atendimento aberto — não pode virar "ja_tinha"
        porta2 = Porta(buscar="encontrado",
                       abertos=[{"atendimento": 795001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                       previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        porta2.criar_atend = {"ok": True, "situacao": "ja_existia", "atendimento": 795001, "valor": 25.9, "itens": []}
        r = self.criar(porta2)
        self.assertTrue(r["ok"], r)
        self.assertIn("atendimento/criar", porta2.rotas())
        e = self.estado()
        self.assertEqual((e["situacao"], e["comprometido"]), ("ja_existia", "sim"))

    def test_atendimento_de_outro_valor_nao_conta_como_ja_existente(self):
        # homônimo / outra compra: as peças batem mas o valor não é o da venda
        self.pedido()
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 99.0, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        r = self.criar(porta)
        self.assertTrue(r["ok"], r)
        self.assertIn("atendimento/criar", porta.rotas())

    def test_atendimento_ja_ligado_a_outro_pedido_nao_conta(self):
        self.pedido()
        S._mkt_atend_gravar("2000019999999999", situacao="criado", atendimento="790001")
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        r = self.criar(porta)
        self.assertTrue(r["ok"], r)
        self.assertIn("atendimento/criar", porta.rotas())

    def test_atendimento_com_romaneio_ja_solto_conta_como_ja_existente(self):
        # V20.49: atendimento com as peças e o romaneio já solto no Apollo (fora do painel) = a venda já tem
        # atendimento — não cria outro (o lado seguro: duplicar compromete o estoque em dobro).
        self.pedido()
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 25.9, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 0.0}])
        self.criar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "ja_tinha")

    def test_admin_muda_a_fila_durante_o_cadastro(self):
        self.pedido()
        porta = Porta()
        original = S.exp_cadastro_atualizar

        def cadastro_e_admin(*a, **k):
            r = original(*a, **k)
            gravar_como_admin([linha(PEDIDO, "0893764272", 2, Marketplace="MERCADO LIVRE",
                                     ShipmentIdMercadoLivre="48263508506")])
            return r

        with mock.patch.object(S, "exp_cadastro_atualizar", side_effect=cadastro_e_admin):
            r = self.criar(porta)
        self.assertFalse(r["ok"])
        self.assertEqual(self.estado()["situacao"], "revisar")
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_repetir_nao_regrava_o_cadastro(self):
        self.pedido()
        porta = Porta()
        porta.criar_atend = {"ok": False, "situacao": "sem_estoque", "erro": "Peça sem saldo"}
        self.criar(porta)
        self.assertEqual(self.estado()["situacao"], "erro")
        porta2 = Porta(buscar="encontrado")
        r = self.criar(porta2)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("cliente/atualizar", porta2.rotas())
        self.assertNotIn("cliente/criar", porta2.rotas())

    def test_thread_que_nao_sobe_nao_deixa_criando(self):
        self.pedido()
        with mock.patch.object(S.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            with self.assertRaises(RuntimeError):
                S.mkt_atendimento_iniciar(PEDIDO, "luis")
        self.assertNotEqual(self.estado().get("situacao"), "criando")
        self.assertFalse(S._MKT_ATEND_LOCK.locked())
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_comprometer_depois_que_o_admin_mudou_o_pedido(self):
        self.pedido()
        porta = Porta()
        porta.comprometer = {"ok": True, "situacao": "comprometido",
                             "itens": [{"codigo": "0893764272", "comprometido": False, "motivo": "sem saldo"}]}
        self.criar(porta)
        gravar_como_admin([linha(PEDIDO, "0893764272", 3, Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48263508506")])
        porta2 = Porta(buscar="encontrado")
        self.criar(porta2)
        self.assertNotIn("atendimento/comprometer", porta2.rotas())
        self.assertNotEqual(self.estado()["comprometido"], "sim")

    def test_kit_que_perderia_centavo_vai_para_revisar(self):
        # 2 kits de duas peças sem peça de 1 unidade: total ímpar não divide sem sobra
        self.pedido(sku="0893600022/0893764272", qtd=2, valor=40.01)
        porta = Porta()
        r = self.criar(porta, custos={"0893600022": 30.0, "0893764272": 10.0})
        self.assertFalse(r["ok"])
        self.assertEqual(self.estado()["situacao"], "revisar")
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_ml_avisa_a_condicao_antes_do_caixa(self):
        self.pedido()
        self.criar(Porta())
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e.get("condicao_aviso"))
        self.assertIn("condição", e["condicao_aviso"].lower())


class Revisao2Botao(Base):
    """2ª revisão do botão (10/11, "corrigir todos")."""

    def test_shopee_atendimento_da_integracao_com_cupom_e_reconhecido(self):
        self.pedido(marketplace="SHOPEE", valor=99.90)
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO shopee_escrow_v1810(order_sn, order_income_json) VALUES(?, ?)",
                         (PEDIDO, '{"voucher_from_seller": 5.0, "voucher_from_shopee": 2.5, "coins": 0}'))
            S.DB.commit()
        porta = Porta(buscar="encontrado",
                      abertos=[{"atendimento": 790001, "cliente": NOME.upper(), "valor": 92.40, "aberto_em": "2026-11-10"}],
                      previa_itens=[{"codigo": "0893764272", "descricao": "LIMPA AR", "requisitada": 1.0, "a_soltar": 1.0}])
        with mock.patch.object(S, "shopee_order_details", return_value=[]):
            self.criar(porta, endereco_texto="Rua das Flores, 120, Bauru, São Paulo, 17012-000")
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado()["situacao"], "ja_tinha")

    def test_kit_compromete_mesmo_com_o_custo_mudado(self):
        self.pedido(sku="0893600022/0893764272", valor=40.00)
        porta = Porta()
        porta.comprometer = {"ok": True, "situacao": "comprometido",
                             "itens": [{"codigo": "0893764272", "comprometido": False, "motivo": "sem saldo"}]}
        self.criar(porta, custos={"0893600022": 30.0, "0893764272": 10.0})
        porta2 = Porta(buscar="encontrado")
        self.criar(porta2, custos={"0893600022": 20.0, "0893764272": 20.0})   # custo de Araçatuba mudou
        self.assertIn("atendimento/comprometer", porta2.rotas())
        self.assertEqual(self.estado()["comprometido"], "sim")

    def test_faturamento_nao_manda_ml_do_botao_ao_caixa_sem_a_condicao(self):
        self.pedido()
        self.criar(Porta())
        res = S._mkt_faturamento_barreira(PEDIDO, {"situacao": "pronto", "motivo": ""})
        self.assertEqual(res["situacao"], "atencao")
        self.assertIn("condição", res["motivo"].lower())
        # Shopee (condição acertada pela extensão) e pedido sem o botão passam
        self.assertEqual(S._mkt_faturamento_barreira("999", {"situacao": "pronto", "motivo": ""})["situacao"], "pronto")

    def test_criado_ja_marca_comprometendo(self):
        self.pedido()
        vistos = []
        original = S.mkt_atendimento_comprometer

        def olhar(p):
            vistos.append((S.mkt_atendimento_do_pedido(p) or {}).get("comprometido"))
            return original(p)

        with mock.patch.object(S, "mkt_atendimento_comprometer", side_effect=olhar):
            self.criar(Porta())
        self.assertEqual(vistos, ["comprometendo"])

    def test_leitura_do_estado_respeita_a_filial(self):
        from base import Cliente, iniciar_servidor, SENHA_ADMIN
        self.pedido()   # pedido de Araçatuba (linha sem Casa)
        url = iniciar_servidor()
        with S.DB_LOCK:
            S.upsert_app_user("supbauru", "Sup Bauru", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"admin": True, "conferencia": True})
            S.DB.execute("UPDATE app_users SET casa='bauru' WHERE username='supbauru'")
            S.DB.commit()
        c = Cliente(url)
        c.entrar("supbauru", SENHA_ADMIN)
        status, _ = c.pedir("GET", f"/api/expedicao-apollo/criar-atendimento?pedido={PEDIDO}")
        self.assertEqual(status, 403)
        admin = Cliente(url)
        admin.entrar()
        status, d = admin.pedir("GET", f"/api/expedicao-apollo/criar-atendimento?pedido={PEDIDO}")
        self.assertEqual(status, 200)
        self.assertTrue(d["atendimento_mkt"]["disponivel"])


class CepDeOutraCidade(Base):
    """Pedido 2000019436248196 (10/11): o ML manda o distrito (Campos Lindos) e o Apollo recusa:
    "O CEP é de CRISTALINA, não de CAMPOS LINDOS. Nada foi enviado." Tenta de novo com a cidade do CEP."""

    ENVIO = {"receiver_address": {
        "street_name": "Rua 5", "street_number": "120", "comment": "",
        "neighborhood": {"name": "Centro"}, "zip_code": "73850-000",
        "city": {"name": "Campos Lindos"}, "state": {"id": "BR-GO", "name": "Goiás"}}}

    class PortaCep(Porta):
        def __call__(self, rota, corpo, escrita=False):
            end = corpo.get("endereco") or {}
            if rota in ("cliente/atualizar", "cliente/criar") and S.normalize(end.get("cidade")) == "CAMPOSLINDOS":
                self.chamadas.append((rota, dict(corpo, endereco=dict(end))))
                return {"ok": False, "situacao": "endereco_recusado",
                        "erro": "O CEP é de CRISTALINA, não de CAMPOS LINDOS. Nada foi enviado."}
            return super().__call__(rota, dict(corpo, endereco=dict(end)) if end else corpo, escrita)

    def cadastrar(self, porta):
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=self.ENVIO):
            return S.exp_cadastro_atualizar(PEDIDO, "luis")

    def test_atualizar_tenta_de_novo_com_a_cidade_do_cep(self):
        self.pedido()
        porta = self.PortaCep(buscar="encontrado")
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        cidades = [c["endereco"]["cidade"] for rota, c in porta.chamadas if rota == "cliente/atualizar"]
        self.assertEqual([S.normalize(c) for c in cidades], ["CAMPOSLINDOS", "CRISTALINA"])
        self.assertTrue(any("CRISTALINA" in a for a in r["avisos"]), r["avisos"])

    def test_criar_tambem(self):
        self.pedido()
        porta = self.PortaCep()
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        criar = [c["endereco"]["cidade"] for rota, c in porta.chamadas if rota == "cliente/criar"]
        self.assertEqual([S.normalize(c) for c in criar], ["CAMPOSLINDOS", "CRISTALINA"])
        self.assertEqual(S.normalize(porta.corpo("cliente/atualizar")["endereco"]["cidade"]), "CRISTALINA")

    def test_cidade_do_erro_vai_sem_acento(self):
        self.pedido()

        class PortaAcento(Porta):
            def __call__(self, rota, corpo, escrita=False):
                end = corpo.get("endereco") or {}
                if rota == "cliente/atualizar" and S.normalize(end.get("cidade")) == "CAMPOSLINDOS":
                    self.chamadas.append((rota, dict(corpo, endereco=dict(end))))
                    return {"ok": False, "erro": "O CEP é de São João d'Aliança, não de CAMPOS LINDOS."}
                return super().__call__(rota, dict(corpo, endereco=dict(end)) if end else corpo, escrita)

        porta = PortaAcento(buscar="encontrado")
        r = self.cadastrar(porta)
        self.assertTrue(r["ok"], r)
        self.assertEqual(porta.corpo("cliente/atualizar")["endereco"]["cidade"], "CAMPOS LINDOS")
        ultimo = [c for rr, c in porta.chamadas if rr == "cliente/atualizar"][-1]
        self.assertEqual(ultimo["endereco"]["cidade"], "SAO JOAO D'ALIANCA")

    def test_outro_erro_nao_repete(self):
        self.pedido()

        class PortaRecusa(Porta):
            def __call__(self, rota, corpo, escrita=False):
                if rota == "cliente/atualizar":
                    self.chamadas.append((rota, dict(corpo)))
                    return {"ok": False, "situacao": "recusado", "erro": "Rua inválida."}
                return super().__call__(rota, corpo, escrita)

        porta = PortaRecusa(buscar="encontrado")
        r = self.cadastrar(porta)
        self.assertFalse(r["ok"])
        self.assertEqual(porta.rotas().count("cliente/atualizar"), 1)


class RespostasNovasDaPorta(Base):
    """V20.52 (contrato do Guilherme, 10/11, tarefa #567): a porta procura do lado dela um atendimento aberto do mesmo
    cliente com todas as peças e devolve `ja_aberto_no_apollo` com o número — nada criado, como o nosso "ja_tinha"."""

    def test_ja_aberto_no_apollo_vira_ja_tinha_com_o_numero(self):
        self.pedido()
        porta = Porta()
        porta.criar_atend = {"ok": False, "situacao": "ja_aberto_no_apollo", "atendimento": 796123,
                             "erro": "Já existe o atendimento 796123 aberto deste cliente com todas as peças."}
        r = self.criar(porta)
        self.assertFalse(r["ok"])
        e = self.estado()
        self.assertEqual((e["situacao"], e["atendimento"]), ("ja_tinha", "796123"))
        self.assertIn("796123", e["detalhe"])
        self.assertNotIn("atendimento/comprometer", porta.rotas())
        self.assertFalse(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_sem_estoque_e_erro_com_o_motivo_da_porta(self):
        self.pedido()
        porta = Porta()
        porta.criar_atend = {"ok": False, "situacao": "sem_estoque",
                             "erro": "Sem saldo na revenda 1: 0893764272 (pedido 1, disponível 0)."}
        self.criar(porta)
        e = self.estado()
        self.assertEqual(e["situacao"], "erro")
        self.assertIn("Sem saldo", e["detalhe"])


class Interruptor(Base):
    """V20.52 (dono, 11/11: "essa porta de ligação tem que ser minha"): liga/desliga por marketplace no Noromak
    (Admin › Integrações, só o perfil Admin). Padrão = ligado (como já funcionava)."""

    def test_padrao_ligado_nos_dois(self):
        c = S.mkt_atendimento_config()
        self.assertTrue(c["Mercado Livre"]["ligado"])
        self.assertTrue(c["Shopee"]["ligado"])

    def test_desligado_o_botao_some_e_nada_vai_a_porta(self):
        self.pedido()
        S.mkt_atendimento_ligar("Mercado Livre", False, "admin")
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertFalse(e["disponivel"])
        self.assertIn("desligado", e["motivo"])
        porta = Porta()
        with self.assertRaises(ValueError):
            self.criar(porta)
        self.assertEqual(porta.rotas(), [])

    def test_desligar_um_nao_desliga_o_outro(self):
        S.mkt_atendimento_ligar("Shopee", False, "admin")
        self.pedido()
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"])
        c = S.mkt_atendimento_config()
        self.assertTrue(c["Mercado Livre"]["ligado"])
        self.assertFalse(c["Shopee"]["ligado"])

    def test_desligado_ainda_compromete_o_que_ja_foi_criado(self):
        self.pedido()
        S._mkt_atend_gravar(PEDIDO, marketplace="Mercado Livre", situacao="criado", atendimento="795001",
                            comprometido="erro", cliente="99001")
        S.mkt_atendimento_ligar("Mercado Livre", False, "admin")
        e = S.mkt_atendimento_estado(PEDIDO)
        self.assertTrue(e["disponivel"])
        self.assertEqual(e["acao"], "comprometer")

    def test_religar_volta_o_botao(self):
        self.pedido()
        S.mkt_atendimento_ligar("Mercado Livre", False, "admin")
        S.mkt_atendimento_ligar("Mercado Livre", True, "admin")
        self.assertTrue(S.mkt_atendimento_estado(PEDIDO)["disponivel"])

    def test_marketplace_desconhecido_recusado(self):
        with self.assertRaises(ValueError):
            S.mkt_atendimento_ligar("Magalu", False, "admin")


class InterruptorRotas(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        from base import iniciar_servidor
        self.url = iniciar_servidor()

    def test_admin_liga_e_desliga(self):
        from base import Cliente
        admin = Cliente(self.url)
        admin.entrar()
        status, d = admin.pedir("GET", "/api/expedicao-apollo/atendimento-mkt")
        self.assertEqual((status, d["config"]["Shopee"]["ligado"]), (200, True))
        status, d = admin.pedir("POST", "/api/expedicao-apollo/atendimento-mkt", {"marketplace": "Shopee", "ligado": False})
        self.assertEqual(status, 200, d)
        self.assertFalse(d["config"]["Shopee"]["ligado"])
        from base import USUARIO_ADMIN
        self.assertEqual(d["config"]["Shopee"]["por"], USUARIO_ADMIN)
        status, _ = admin.pedir("POST", "/api/expedicao-apollo/atendimento-mkt", {"marketplace": "Magalu", "ligado": False})
        self.assertEqual(status, 400)

    def test_sem_login_nao_muda(self):
        from base import Cliente
        status, _ = Cliente(self.url).pedir("POST", "/api/expedicao-apollo/atendimento-mkt",
                                            {"marketplace": "Shopee", "ligado": False})
        self.assertEqual(status, 401)
        self.assertTrue(S.mkt_atendimento_config()["Shopee"]["ligado"])


if __name__ == "__main__":
    unittest.main()
