"""V18.88 — Pós-venda do Mercado Livre: reclamações, devoluções e perdas, perguntas e reputação.

O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from base import S, TesteNoromak

VENDEDOR = "99"


def iso(delta_horas: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_horas)).astimezone(S.BR_TZ).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")


def reclamacao(cid, status="opened", pedido="2000001", acao=None, prazo=None, resolucao=None, devolucao=False,
               entregue=True, aberta=None, motivo="PDD9939"):
    acoes = [{"action": acao, "mandatory": True, "due_date": prazo}] if acao else []
    return {"id": cid, "resource_id": int(pedido), "resource": "order", "status": status, "type": "mediations",
            "stage": "claim", "reason_id": motivo, "fulfilled": entregue,
            "players": [{"role": "complainant", "type": "buyer", "user_id": 1, "available_actions": []},
                        {"role": "respondent", "type": "seller", "user_id": int(VENDEDOR), "available_actions": acoes}],
            "resolution": resolucao, "date_created": aberta or iso(-48), "last_updated": iso(-1),
            "related_entities": ["return"] if devolucao else []}


def pedido(total=100.0, reembolsado=0.0, sku="ABC", qtd=1):
    return {"id": 2000001, "total_amount": total,
            "payments": [{"transaction_amount": total, "transaction_amount_refunded": reembolsado}],
            "order_items": [{"item": {"id": "MLB1", "title": "Peça", "seller_sku": sku}, "quantity": qtd, "unit_price": total / qtd}]}


class MLFalso:
    """Responde como o ML às rotas que o Pós-venda usa e guarda as chamadas."""

    def __init__(self):
        self.reclamacoes: list[dict] = []
        self.pedidos: dict[str, dict] = {}
        self.devolucoes: dict[str, dict] = {}
        self.custos: dict[str, float] = {}
        self.perguntas: list[dict] = []
        self.pergunta_unica: dict[str, dict] = {}
        self.erro_busca = ""
        self.chamadas: list[str] = []

    def __call__(self, caminho, headers=None):
        self.chamadas.append(caminho)
        url = urlparse(caminho)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if p == "/post-purchase/v1/claims/search":
            if self.erro_busca:
                raise RuntimeError(self.erro_busca)
            assert q["players.role"] == "respondent" and q["players.user_id"] == VENDEDOR, q
            # V19.92: como o ML real (27/10, em produção): range sozinho não conta como filtro.
            if "status" not in q and "stage" not in q:
                raise RuntimeError("Mercado Livre HTTP 400: {'code': 400, 'error': 'bad_request_error', "
                                   "'message': 'atLeastOneFilterProvided: at least one filter parameter must be provided'}")
            lista = [c for c in self.reclamacoes if q.get("status") in (None, c["status"])]
            return {"paging": {"total": len(lista), "offset": 0, "limit": 100}, "data": lista}
        if p.endswith("/detail"):
            return {"due_date": None, "action_responsible": "seller", "title": "Responda", "problem": "Chegou quebrado"}
        if p.endswith("/affects-reputation"):
            return {"affects_reputation": "affected"}
        if p.startswith("/post-purchase/v1/claims/reasons/"):
            return {"id": p.rsplit("/", 1)[1], "name": "repentant_buyer", "detail": "Não quero mais"}
        if p.startswith("/post-purchase/v2/claims/") and p.endswith("/returns"):
            cid = p.split("/")[4]
            if cid not in self.devolucoes:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return self.devolucoes[cid]
        if p.endswith("/charges/return-cost"):
            cid = p.split("/")[4]
            if cid not in self.custos:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return {"currency_id": "BRL", "amount": self.custos[cid]}
        if p.startswith("/orders/"):
            return self.pedidos[p.rsplit("/", 1)[1]]
        if p == "/questions/search":
            assert q["seller_id"] == VENDEDOR and q["status"] == "UNANSWERED"
            return {"total": len(self.perguntas), "questions": self.perguntas}
        if p.startswith("/questions/"):
            qid = p.rsplit("/", 1)[1]
            if qid not in self.pergunta_unica:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return self.pergunta_unica[qid]
        if p.startswith("/users/"):
            return {"seller_reputation": {"level_id": "5_green", "power_seller_status": "gold", "metrics": {
                "sales": {"period": "60 days", "completed": 900},
                "claims": {"rate": 0.011, "value": 10},
                "delayed_handling_time": {"rate": 0.02, "value": 18},
                "cancellations": {"rate": 0.001, "value": 1}}}}
        raise AssertionError("rota não simulada: " + caminho)


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.ml = MLFalso()
        self._orig = (S.ml_api_get, S.ml_integration_row, S._ml_promo_http, S.ml_status_payload)
        S.ml_status_payload = lambda: {"configured": True}
        S.ml_api_get = self.ml
        S.ml_integration_row = lambda: {"access_token": "tok", "seller_id": VENDEDOR}
        S._POSVENDA_ESTADO.update(ultimo={}, perguntas={})

    def tearDown(self):
        S.ml_api_get, S.ml_integration_row, S._ml_promo_http, S.ml_status_payload = self._orig
        super().tearDown()

    def linha(self, cid):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM ml_reclamacao_v1888 WHERE claim_id=?", (str(cid),)).fetchone()
        return dict(r) if r else None

    def rodar(self):
        return S.posvenda_reclamacoes_rodada(pausa=0)


class ColetaDeReclamacoes(Base):

    def test_aberta_guarda_acao_prazo_e_detalhe(self):
        prazo = iso(10)
        self.ml.reclamacoes = [reclamacao(1, acao="send_message_to_complainant", prazo=prazo)]
        self.ml.pedidos["2000001"] = pedido(150.0)
        r = self.rodar()
        self.assertTrue(r["ok"], r)
        l = self.linha(1)
        self.assertEqual((l["status"], l["acao"], l["prazo"], l["order_id"]), ("opened", "send_message_to_complainant", prazo, "2000001"))
        self.assertEqual((l["problema"], l["afeta_reputacao"], l["pedido_total"]), ("Chegou quebrado", "affected", 150.0))
        self.assertEqual(l["precisa_detalhe"], 0)

    def test_primeira_carga_busca_historico_e_depois_so_o_que_mudou(self):
        self.ml.reclamacoes = [reclamacao(1)]
        self.ml.pedidos["2000001"] = pedido()
        self.rodar()
        primeira = [c for c in self.ml.chamadas if "claims/search" in c]
        self.assertTrue(any("date_created%3Aafter" in c for c in primeira), primeira)
        self.ml.chamadas.clear()
        self.rodar()
        segunda = [c for c in self.ml.chamadas if "claims/search" in c]
        self.assertTrue(any("last_updated%3Aafter" in c for c in segunda), segunda)
        self.assertFalse(any("/orders/" in c for c in self.ml.chamadas), "releu o pedido de reclamação que não mudou")

    def test_fechada_com_devolucao_guarda_frete_e_situacao_do_produto(self):
        self.ml.reclamacoes = [reclamacao(2, status="closed", devolucao=True,
                                          resolucao={"reason": "payment_refunded", "benefited": ["complainant"],
                                                     "closed_by": "mediator", "applied_coverage": False})]
        self.ml.pedidos["2000001"] = pedido(100.0, reembolsado=100.0)
        self.ml.devolucoes["2"] = {"id": 7, "status": "delivered", "subtype": "return_total", "status_money": "refunded",
                                   "shipments": [{"type": "return", "destination": {"name": "seller_address"}}],
                                   "related_entities": []}
        self.ml.custos["2"] = 23.5
        self.rodar()
        l = self.linha(2)
        self.assertEqual((l["devolucao_status"], l["devolucao_destino"], l["frete_devolucao"], l["reembolsado"]),
                         ("delivered", "seller_address", 23.5, 100.0))
        self.assertEqual(l["resolucao"], "payment_refunded")

    def test_sem_permissao_nao_quebra_e_fica_visivel(self):
        self.ml.erro_busca = "Mercado Livre HTTP 403: {'message': 'PolicyAgent'}"
        r = self.rodar()
        self.assertFalse(r["ok"])
        self.assertTrue(r["sem_permissao"])
        item = S._saude_posvenda()[0]
        self.assertEqual(item["nivel"], "atencao")
        self.assertIn("permissão", item["detalhe"])

    def test_pedido_que_falha_nao_vira_zero(self):
        self.ml.reclamacoes = [reclamacao(3, status="closed",
                                          resolucao={"reason": "payment_refunded", "benefited": ["complainant"],
                                                     "closed_by": "mediator", "applied_coverage": False})]
        # sem pedido simulado: /orders/ levanta KeyError -> erro guardado na linha
        self.rodar()
        l = self.linha(3)
        self.assertIsNone(l["reembolsado"])
        self.assertIn("pedido", l["erro"])
        v = S._posvenda_valores(l, lambda sku: 10.0)
        self.assertIsNone(v["venda_desfeita"])
        self.assertIsNone(v["perda"], "perda calculada sem saber o reembolso")


def fechada(**extra):
    base = {"status": "closed", "beneficiado": "complainant", "cobertura": 0, "entregue": 1, "tem_devolucao": 0,
            "pedido_total": 100.0, "reembolsado": 100.0, "frete_devolucao": None, "devolucao_status": "",
            "devolucao_subtipo": "", "produto_condicao": "",
            "itens_json": json.dumps([{"sku": "ABC", "qtd": 2, "preco": 50.0}])}
    base.update(extra)
    return base


CUSTO = lambda sku: 20.0  # noqa: E731


class ContaDaPerda(unittest.TestCase):

    def test_produto_voltou_bom_perde_so_o_frete_da_devolucao(self):
        v = S._posvenda_valores(fechada(tem_devolucao=1, devolucao_status="delivered", frete_devolucao=23.5), CUSTO)
        self.assertEqual((v["produto"], v["produto_perdido"], v["perda"], v["venda_desfeita"]), ("voltou", 0.0, 23.5, 100.0))

    def test_reembolso_sem_devolucao_perde_o_custo_do_produto(self):
        v = S._posvenda_valores(fechada(), CUSTO)
        self.assertEqual((v["produto"], v["produto_perdido"], v["perda"]), ("nao_voltou", 40.0, 40.0))

    def test_devolucao_inservivel_perde_custo_mais_frete(self):
        v = S._posvenda_valores(fechada(tem_devolucao=1, devolucao_status="delivered", frete_devolucao=15.0,
                                        produto_condicao="unsaleable"), CUSTO)
        self.assertEqual((v["produto"], v["perda"]), ("inservivel", 55.0))

    def test_reembolso_parcial_comprador_fica_com_o_produto(self):
        v = S._posvenda_valores(fechada(reembolsado=30.0), CUSTO)
        self.assertEqual((v["produto"], v["reembolso_parcial"], v["perda"]), ("ficou_parcial", 30.0, 30.0))

    def test_cobertura_do_ml_nao_e_perda(self):
        v = S._posvenda_valores(fechada(cobertura=1), CUSTO)
        self.assertEqual((v["venda_desfeita"], v["perda"]), (0.0, 0.0))

    def test_vendedor_ganhou_nao_e_perda(self):
        v = S._posvenda_valores(fechada(beneficiado="respondent", reembolsado=0.0), CUSTO)
        self.assertEqual(v["perda"], 0.0)

    def test_frete_da_devolucao_desconhecido_deixa_incompleta(self):
        v = S._posvenda_valores(fechada(tem_devolucao=1, devolucao_status="delivered"), CUSTO)
        self.assertIsNone(v["perda"])
        self.assertIn("frete da devolução", v["pendente"])

    def test_sem_custo_do_produto_deixa_incompleta(self):
        v = S._posvenda_valores(fechada(), lambda sku: None)
        self.assertIsNone(v["perda"])
        self.assertIn("custo do produto", v["pendente"])

    def test_aberta_ainda_nao_tem_perda(self):
        v = S._posvenda_valores(fechada(status="opened"), CUSTO)
        self.assertEqual(v["produto"], "em_aberto")
        self.assertIsNone(v["perda"])


class Perguntas(Base):

    def pergunta(self, qid, status="UNANSWERED", resposta=None):
        return {"id": qid, "item_id": "MLB1", "text": "Serve no Gol 2010?", "status": status,
                "date_created": iso(-5), "from": {"id": 5},
                "answer": {"text": resposta, "date_created": iso(-1)} if resposta else None}

    def status(self, qid):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM ml_pergunta_v1888 WHERE pergunta_id=?", (str(qid),)).fetchone()
        return dict(r) if r else None

    def test_grava_as_sem_resposta_e_rele_a_que_saiu_da_lista(self):
        self.ml.perguntas = [self.pergunta(10), self.pergunta(11)]
        S.posvenda_perguntas_rodada()
        self.assertEqual(self.status(10)["status"], "UNANSWERED")
        self.ml.perguntas = [self.pergunta(11)]
        self.ml.pergunta_unica["10"] = self.pergunta(10, "ANSWERED", "Serve sim")
        S.posvenda_perguntas_rodada()
        l = self.status(10)
        self.assertEqual((l["status"], l["resposta"], l["respondida_por"]), ("ANSWERED", "Serve sim", "Mercado Livre"))

    def test_responder_manda_ao_ml_e_registra_quem(self):
        self.ml.perguntas = [self.pergunta(20)]
        S.posvenda_perguntas_rodada()
        enviado = {}

        def falso(metodo, caminho, corpo=None):
            enviado.update(metodo=metodo, caminho=caminho, corpo=corpo)
            return {"id": 20, "status": "ANSWERED", "answer": {"text": corpo["text"], "date_created": iso()}}

        S._ml_promo_http = falso
        S.posvenda_responder("20", "Serve sim, ano 2008 a 2012.", "luis")
        self.assertEqual((enviado["metodo"], enviado["caminho"]), ("POST", "/answers"))
        self.assertEqual(enviado["corpo"], {"question_id": 20, "text": "Serve sim, ano 2008 a 2012."})
        l = self.status(20)
        self.assertEqual((l["status"], l["respondida_por"]), ("ANSWERED", "luis"))
        with S.DB_LOCK:
            a = S.DB.execute("SELECT * FROM audit_log WHERE action='ml_pergunta_responder'").fetchone()
        self.assertEqual(a["username"], "luis")
        with self.assertRaises(ValueError):
            S.posvenda_responder("20", "de novo", "luis")

    def test_resposta_vazia_ou_longa_e_recusada_sem_chamar_o_ml(self):
        self.ml.perguntas = [self.pergunta(30)]
        S.posvenda_perguntas_rodada()
        S._ml_promo_http = lambda *a, **k: self.fail("chamou o ML")
        for texto in ("", "   ", "x" * (S.POSVENDA_RESPOSTA_MAX + 1)):
            with self.assertRaises(ValueError):
                S.posvenda_responder("30", texto, "luis")


class TelaEAlerta(Base):

    def test_prazo_em_menos_de_24h_e_critico_no_painel_de_saude(self):
        self.ml.reclamacoes = [reclamacao(1, acao="send_message_to_complainant", prazo=iso(5))]
        self.ml.pedidos["2000001"] = pedido()
        self.rodar()
        item = S._saude_posvenda()[0]
        self.assertEqual((item["id"], item["nivel"]), ("posvenda_prazo", "critico"))
        self.assertEqual(item["acao"], "")   # V19.76: a tela saiu

    def test_prazo_longe_e_so_atencao(self):
        self.ml.reclamacoes = [reclamacao(1, acao="send_message_to_complainant", prazo=iso(60))]
        self.ml.pedidos["2000001"] = pedido()
        self.rodar()
        self.assertEqual(S._saude_posvenda()[0]["nivel"], "atencao")

    def test_relatorio_soma_as_perdas_do_periodo(self):
        r = {"reason": "payment_refunded", "benefited": ["complainant"], "closed_by": "mediator", "applied_coverage": False}
        self.ml.reclamacoes = [reclamacao(5, status="closed", resolucao=r, devolucao=True, aberta=iso(-72))]
        self.ml.pedidos["2000001"] = pedido(100.0, reembolsado=100.0)
        self.ml.devolucoes["5"] = {"id": 8, "status": "delivered", "subtype": "return_total", "shipments": [],
                                   "related_entities": []}
        self.ml.custos["5"] = 30.0
        self.rodar()
        d = S.posvenda_payload(30)
        rel = d["relatorio"]
        self.assertEqual((rel["reclamacoes"], rel["devolucoes"], rel["frete_devolucao"], rel["perda"], rel["venda_desfeita"]),
                         (1, 1, 30.0, 30.0, 100.0))
        self.assertEqual(rel["por_motivo"][0]["rotulo"], "Não quero mais")

    def test_rota_responde_para_admin(self):
        status, corpo = self.cliente.pedir("GET", "/api/posvenda?dias=30")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["ok"])
        status, corpo = self.cliente.pedir("POST", "/api/posvenda/responder", {"pergunta_id": "1", "texto": "oi"})
        self.assertEqual(status, 400, corpo)

    def test_worker_novo_esta_no_painel_de_saude(self):
        self.assertIn("posvenda_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
