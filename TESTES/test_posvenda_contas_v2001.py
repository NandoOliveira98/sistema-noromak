"""V20.01 — Pós-venda volta como tela, com Araçatuba e Bauru (decisões do dono, 29/10).

O que não pode quebrar:
- pergunta e reclamação guardam a conta de onde vieram e cada conta é lida com o PRÓPRIO token;
- a resposta sai pela conta dona da pergunta (nunca pela principal por engano);
- Bauru sem permissão (403) aparece como tal e não apaga nada de Araçatuba;
- saudação e assinatura de cada conta entram uma vez só e o limite do ML vale para o texto final;
- duas pessoas na mesma pergunta: só uma resposta chega ao ML;
- pergunta nova avisa (popup/push) uma vez; a 1ª leitura de cada conta e pergunta antiga não avisam;
- selo do menu: sem resposta e passadas de 1 h;
- a tela mostra o anúncio, as respostas anteriores do mesmo anúncio e as devoluções a caminho,
  dizendo se a peça já chegou na tela de Devoluções;
- respostas prontas: módulo Admin edita, Consulta e operador não.

O ML é simulado: nenhum teste chama a API de verdade.
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from base import S, SENHA_ADMIN, Cliente, TesteNoromak

VENDEDOR = {"aracatuba": "99", "bauru": "77"}


def iso(delta_horas: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_horas)).astimezone(S.BR_TZ).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")


def pergunta(qid, item="MLB1", horas=-0.5, status="UNANSWERED", resposta=None, texto="Serve no Gol 2010?"):
    return {"id": qid, "item_id": item, "text": texto, "status": status, "date_created": iso(horas), "from": {"id": 5},
            "answer": {"text": resposta, "date_created": iso(-0.1)} if resposta else None}


def reclamacao(cid, conta, pedido="2000001", devolucao=False):
    return {"id": cid, "resource_id": int(pedido), "resource": "order", "status": "opened", "type": "mediations",
            "stage": "claim", "reason_id": "PDD1", "fulfilled": True,
            "players": [{"role": "respondent", "type": "seller", "user_id": int(VENDEDOR[conta]),
                         "available_actions": [{"action": "send_message_to_complainant", "mandatory": True,
                                                "due_date": iso(10)}]}],
            "resolution": None, "date_created": iso(-48), "last_updated": iso(-1),
            "related_entities": ["return"] if devolucao else []}


class MLDuasContas:
    """ML falso com uma resposta por conta. Guarda (conta, caminho) de cada chamada."""

    def __init__(self):
        self.perguntas = {"aracatuba": [], "bauru": []}
        self.reclamacoes = {"aracatuba": [], "bauru": []}
        self.erro = {"aracatuba": "", "bauru": ""}
        self.devolucoes: dict[str, dict] = {}
        self.chamadas: list[tuple[str, str]] = []

    def __call__(self, caminho, headers=None):
        conta = S.ml_casa_atual()
        self.chamadas.append((conta, caminho))
        url = urlparse(caminho)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if self.erro[conta] and (p.startswith("/questions") or p.startswith("/post-purchase")):
            raise RuntimeError(self.erro[conta])
        if p == "/questions/search":
            assert q["seller_id"] == VENDEDOR[conta], (conta, q)
            return {"total": len(self.perguntas[conta]), "questions": self.perguntas[conta]}
        if p.startswith("/questions/"):
            raise RuntimeError("Mercado Livre HTTP 404: not found")
        if p == "/post-purchase/v1/claims/search":
            assert q["players.user_id"] == VENDEDOR[conta], (conta, q)
            lista = [c for c in self.reclamacoes[conta] if q.get("status") in (None, c["status"])]
            return {"paging": {"total": len(lista)}, "data": lista}
        if p.endswith("/detail"):
            return {"action_responsible": "seller", "title": "Responda", "problem": "Chegou quebrado"}
        if p.endswith("/affects-reputation"):
            return {"affects_reputation": "affected"}
        if p.startswith("/post-purchase/v1/claims/reasons/"):
            return {"name": "x", "detail": "Chegou com defeito"}
        if p.startswith("/post-purchase/v2/claims/") and p.endswith("/returns"):
            cid = p.split("/")[4]
            if cid not in self.devolucoes:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return self.devolucoes[cid]
        if p.endswith("/charges/return-cost"):
            raise RuntimeError("Mercado Livre HTTP 404: not found")
        if p.startswith("/orders/"):
            return {"id": int(p.rsplit("/", 1)[1]), "total_amount": 80.0, "payments": [],
                    "order_items": [{"item": {"id": "MLB1", "title": "Peça", "seller_sku": "ABC"},
                                     "quantity": 1, "unit_price": 80.0}]}
        if p.startswith("/users/"):
            nivel = "5_green" if conta == "aracatuba" else "4_light_green"
            return {"seller_reputation": {"level_id": nivel, "metrics": {"sales": {"completed": 10, "period": "60 days"}}}}
        raise AssertionError("rota não simulada: " + caminho)


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.ml = MLDuasContas()
        self._orig = (S.ml_api_get, S.ml_integration_row, S._ml_promo_http, S.ml_status_payload, S.ml_casas_conectadas)
        S.ml_api_get = self.ml
        S.ml_integration_row = lambda: {"access_token": "tok", "seller_id": VENDEDOR[S.ml_casa_atual()]}
        S.ml_casas_conectadas = lambda: ["aracatuba", "bauru"]
        S.ml_status_payload = lambda: {"configured": True}
        S._POSVENDA_ESTADO.update(ultimo={}, perguntas={}, contas={})
        S._POSVENDA_RESPONDENDO.clear()
        with S.DB_LOCK:
            for t in ("ml_pergunta_v1888", "ml_reclamacao_v1888", "aviso_venda_v1893", "posvenda_resposta_pronta_v2001",
                      "posvenda_config_v2001", "ml_reputacao_conta_v2001"):
                S.DB.execute(f"DELETE FROM {t}")
            S.DB.execute("DELETE FROM aviso_config_v1893 WHERE chave LIKE 'base_pergunta%'")
            S.DB.commit()

    def tearDown(self):
        (S.ml_api_get, S.ml_integration_row, S._ml_promo_http, S.ml_status_payload, S.ml_casas_conectadas) = self._orig
        super().tearDown()

    def ler(self, tabela, chave, valor):
        with S.DB_LOCK:
            r = S.DB.execute(f"SELECT * FROM {tabela} WHERE {chave}=?", (str(valor),)).fetchone()
        return dict(r) if r else None

    def rodada(self):
        return S.posvenda_rodada_contas(reclamacoes=True, pausa=0)

    def responder_com(self, enviados):
        def falso(metodo, caminho, corpo=None):
            enviados.append({"conta": S.ml_casa_atual(), "caminho": caminho, "corpo": corpo})
            return {"id": corpo["question_id"], "status": "ANSWERED", "answer": {"text": corpo["text"], "date_created": iso()}}
        S._ml_promo_http = falso


class PerguntasPorConta(Base):

    def test_cada_conta_e_lida_com_o_proprio_vendedor_e_marcada(self):
        self.ml.perguntas["aracatuba"] = [pergunta(10)]
        self.ml.perguntas["bauru"] = [pergunta(20, item="MLB9")]
        self.rodada()
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 10)["conta"], "aracatuba")
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 20)["conta"], "bauru")
        buscas = {c for c, p in self.ml.chamadas if p.startswith("/questions/search")}
        self.assertEqual(buscas, {"aracatuba", "bauru"})

    def test_pergunta_que_saiu_da_lista_e_relida_na_conta_dela(self):
        self.ml.perguntas["bauru"] = [pergunta(21)]
        self.rodada()
        self.ml.perguntas["bauru"] = []
        self.ml.chamadas.clear()
        self.rodada()
        relidas = [c for c, p in self.ml.chamadas if p.startswith("/questions/21")]
        self.assertEqual(relidas, ["bauru"], "releu a pergunta de Bauru com o token de outra conta")
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 21)["status"], "DELETED")

    def test_resposta_sai_pela_conta_dona_da_pergunta(self):
        self.ml.perguntas["bauru"] = [pergunta(22)]
        self.rodada()
        enviados = []
        self.responder_com(enviados)
        S.posvenda_responder("22", "Serve sim.", "luis")
        self.assertEqual([e["conta"] for e in enviados], ["bauru"])
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 22)["status"], "ANSWERED")

    def test_bauru_sem_permissao_aparece_e_nao_apaga_aracatuba(self):
        self.ml.perguntas["aracatuba"] = [pergunta(30)]
        self.ml.erro["bauru"] = "Mercado Livre HTTP 403: {'message': 'PolicyAgent'}"
        self.rodada()
        d = S.posvenda_payload(30)
        self.assertEqual([p["pergunta_id"] for p in d["perguntas"]], ["30"])
        contas = {c["conta"]: c for c in d["contas"]}
        self.assertTrue(contas["bauru"]["perguntas"]["sem_permissao"])
        self.assertTrue(contas["bauru"]["reclamacoes"]["sem_permissao"])
        self.assertTrue(contas["aracatuba"]["perguntas"]["ok"])


class SaudacaoEAssinatura(Base):

    def setUp(self):
        super().setUp()
        self.ml.perguntas["bauru"] = [pergunta(40), pergunta(41)]
        self.rodada()
        S.posvenda_config_salvar({"saudacao@bauru": "Olá!", "assinatura@bauru": "Equipe Noromak Bauru"}, "luis")

    def test_entram_uma_vez_so(self):
        enviados = []
        self.responder_com(enviados)
        S.posvenda_responder("40", "Serve sim.", "luis")
        S.posvenda_responder("41", "Olá! Serve sim. Equipe Noromak Bauru", "luis")
        self.assertEqual(enviados[0]["corpo"]["text"], "Olá! Serve sim. Equipe Noromak Bauru")
        self.assertEqual(enviados[1]["corpo"]["text"], "Olá! Serve sim. Equipe Noromak Bauru", "duplicou a saudação")

    def test_limite_vale_para_o_texto_final(self):
        S._ml_promo_http = lambda *a, **k: self.fail("chamou o ML")
        texto = "x" * (S.POSVENDA_RESPOSTA_MAX - 5)
        with self.assertRaisesRegex(ValueError, "caracteres"):
            S.posvenda_responder("40", texto, "luis")

    def test_saudacao_de_uma_conta_nao_vai_na_outra(self):
        self.ml.perguntas["aracatuba"] = [pergunta(42)]
        self.rodada()
        enviados = []
        self.responder_com(enviados)
        S.posvenda_responder("42", "Serve sim.", "luis")
        self.assertEqual(enviados[0]["corpo"]["text"], "Serve sim.")


class DuasPessoas(Base):

    def test_so_uma_resposta_chega_ao_ml(self):
        self.ml.perguntas["aracatuba"] = [pergunta(50)]
        self.rodada()
        entrou, solta = threading.Event(), threading.Event()
        enviados = []

        def lento(metodo, caminho, corpo=None):
            enviados.append(corpo)
            entrou.set()
            solta.wait(5)
            return {"answer": {"text": corpo["text"]}}

        S._ml_promo_http = lento
        t = threading.Thread(target=lambda: S.posvenda_responder("50", "Primeira", "ana"))
        t.start()
        self.assertTrue(entrou.wait(5))
        with self.assertRaisesRegex(ValueError, "respondendo"):
            S.posvenda_responder("50", "Segunda", "bia")
        solta.set()
        t.join(5)
        self.assertEqual(len(enviados), 1)
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 50)["respondida_por"], "ana")

    def test_erro_do_ml_libera_a_pergunta(self):
        self.ml.perguntas["aracatuba"] = [pergunta(51)]
        self.rodada()

        def falha(*a, **k):
            raise RuntimeError("Mercado Livre HTTP 500")

        S._ml_promo_http = falha
        with self.assertRaises(RuntimeError):
            S.posvenda_responder("51", "Oi", "ana")
        enviados = []
        self.responder_com(enviados)
        S.posvenda_responder("51", "Oi", "ana")
        self.assertEqual(len(enviados), 1)


class Avisos(Base):

    def avisos(self):
        with S.DB_LOCK:
            return [dict(r) for r in S.DB.execute("SELECT * FROM aviso_venda_v1893 WHERE marketplace='pergunta' "
                                                  "AND silencioso=0 ORDER BY id")]

    def test_primeira_leitura_nao_avisa_e_a_nova_avisa_uma_vez(self):
        self.ml.perguntas["bauru"] = [pergunta(60)]
        self.rodada()
        self.assertEqual(self.avisos(), [], "1ª leitura da conta avisou a fila inteira")
        self.ml.perguntas["bauru"] = [pergunta(60), pergunta(61, texto="Tem pronta entrega?")]
        self.rodada()
        self.rodada()
        av = self.avisos()
        self.assertEqual(len(av), 1)
        self.assertEqual(av[0]["casa"], "bauru")
        f = S._avisos_formatar(av[0])
        self.assertIn("Bauru", f["titulo"])
        self.assertIn("pronta entrega", f["corpo"])
        self.assertTrue(f["url"].endswith("/posvenda"))

    def test_pergunta_antiga_entra_calada(self):
        self.rodada()   # 1ª leitura das duas contas (vazia)
        self.ml.perguntas["aracatuba"] = [pergunta(62, horas=-72)]
        self.rodada()
        self.assertEqual(self.avisos(), [])


class ResumoDoMenu(Base):

    def test_conta_sem_resposta_e_atrasadas(self):
        self.ml.perguntas["aracatuba"] = [pergunta(70, horas=-0.2), pergunta(71, horas=-3)]
        self.ml.perguntas["bauru"] = [pergunta(72, horas=-0.1)]
        self.rodada()
        st, d = self.cliente.pedir("GET", "/api/posvenda/resumo")
        self.assertEqual(st, 200, d)
        self.assertEqual((d["sem_resposta"], d["atrasadas"]), (3, 1))
        self.assertEqual(d["por_conta"], {"aracatuba": 2, "bauru": 1})


class Tela(Base):

    def test_pergunta_traz_anuncio_e_respostas_anteriores(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,titulo,sku,preco,estoque,status) "
                         "VALUES('MLB1','Filtro de óleo','ABC',39.9,12,'active')")
            S.DB.commit()
        self.ml.perguntas["aracatuba"] = [pergunta(80)]
        self.rodada()
        self.ml.perguntas["aracatuba"] = []
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_pergunta_v1888 SET status='ANSWERED', resposta='Serve sim', respondida_em=? "
                         "WHERE pergunta_id='80'", (iso(),))
            S.DB.commit()
        self.ml.perguntas["aracatuba"] = [pergunta(81)]
        self.rodada()
        d = S.posvenda_payload(30, com_relatorio=False)
        p = d["perguntas"][0]
        self.assertEqual(p["pergunta_id"], "81")
        self.assertEqual((p["anuncio"]["titulo"], p["anuncio"]["preco"], p["anuncio"]["estoque"]), ("Filtro de óleo", 39.9, 12))
        self.assertEqual([h["resposta"] for h in p["anteriores"]], ["Serve sim"])
        self.assertNotIn("relatorio", d)

    def test_devolucao_a_caminho_diz_se_ja_chegou(self):
        self.ml.reclamacoes["bauru"] = [reclamacao(900, "bauru", pedido="2000005", devolucao=True),
                                        reclamacao(901, "bauru", pedido="2000006", devolucao=True)]
        for cid in ("900", "901"):
            self.ml.devolucoes[cid] = {"id": 1, "status": "shipped", "subtype": "return_total",
                                       "shipments": [{"type": "return", "shipment_id": 555, "destination": {"name": "seller_address"}}],
                                       "related_entities": []}
        self.rodada()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO devolucao_v1988(filial,marketplace,pedido,order_key,recebido_em,situacao) "
                         "VALUES('bauru','MERCADO LIVRE','2000005','2000005',?,'a_tratar')", (S.now_iso(),))
            S.DB.commit()
        d = S.posvenda_payload(30, com_relatorio=False)
        caminho = {x["order_id"]: x for x in d["devolucoes"]}
        self.assertEqual(set(caminho), {"2000005", "2000006"})
        self.assertTrue(caminho["2000005"]["recebida"])
        self.assertFalse(caminho["2000006"]["recebida"])
        self.assertEqual(caminho["2000006"]["conta"], "bauru")
        self.assertEqual({a["claim_id"]: a["conta"] for a in d["abertas"]}, {"900": "bauru", "901": "bauru"})

    def test_reclamacao_de_bauru_e_detalhada_na_conta_dela(self):
        self.ml.reclamacoes["bauru"] = [reclamacao(910, "bauru")]
        self.ml.reclamacoes["aracatuba"] = [reclamacao(911, "aracatuba", pedido="2000009")]
        self.rodada()
        self.assertEqual(self.ler("ml_reclamacao_v1888", "claim_id", 910)["conta"], "bauru")
        detalhe = {c for c, p in self.ml.chamadas if "/claims/910/" in p}
        self.assertEqual(detalhe, {"bauru"})
        # 1ª carga de Bauru busca o histórico, mesmo com Araçatuba já carregada
        buscas_bauru = [p for c, p in self.ml.chamadas if c == "bauru" and "claims/search" in p]
        self.assertTrue(any("date_created%3Aafter" in p for p in buscas_bauru), buscas_bauru)

    def test_reputacao_por_conta(self):
        self.rodada()
        d = S.posvenda_payload(30, com_relatorio=False)
        self.assertEqual(d["reputacao_por_conta"]["aracatuba"][0]["nivel"], "5_green")
        self.assertEqual(d["reputacao_por_conta"]["bauru"][0]["nivel"], "4_light_green")

    def test_prazo_critico_diz_a_conta_e_abre_a_tela(self):
        self.ml.reclamacoes["bauru"] = [reclamacao(920, "bauru")]
        self.rodada()
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_reclamacao_v1888 SET prazo=? WHERE claim_id='920'", (iso(3),))
            S.DB.commit()
        item = S._saude_posvenda()[0]
        self.assertEqual(item["nivel"], "critico")
        self.assertIn("Bauru", item["detalhe"])
        self.assertEqual(item["acao"], "/posvenda")

    def test_pagina_e_menu(self):
        req = urllib.request.Request(self.cliente.url_base + "/posvenda", headers={"Cookie": self.cliente.cookie})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8")
        self.assertIn("Pós-venda", html)
        menu = (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('id: "posvenda"', menu)


class RespostasProntas(Base):

    def usuario(self, login, role="operador", permissions=None, somente_leitura=0):
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), role, True, SENHA_ADMIN, "teste", permissions=permissions)
            if somente_leitura:
                S.DB.execute("UPDATE app_users SET somente_leitura=? WHERE username=?", (somente_leitura, login))
            S.DB.commit()
        c = Cliente(self.url_base)
        c.entrar(login, SENHA_ADMIN, area="portal")
        return c

    def test_admin_cria_edita_e_apaga(self):
        st, d = self.cliente.pedir("POST", "/api/posvenda/respostas", {"titulo": "Original", "texto": "Sim, é original."})
        self.assertEqual(st, 200, d)
        rid = d["resposta"]["id"]
        st, d = self.cliente.pedir("POST", "/api/posvenda/respostas", {"id": rid, "titulo": "Original", "texto": "É original Wurth."})
        self.assertEqual(st, 200, d)
        st, d = self.cliente.pedir("GET", "/api/posvenda")
        self.assertEqual([r["texto"] for r in d["respostas_prontas"]], ["É original Wurth."])
        st, d = self.cliente.pedir("POST", "/api/posvenda/respostas", {"id": rid, "excluir": True})
        self.assertEqual(st, 200, d)
        st, d = self.cliente.pedir("GET", "/api/posvenda")
        self.assertEqual(d["respostas_prontas"], [])

    def test_texto_vazio_recusado(self):
        st, d = self.cliente.pedir("POST", "/api/posvenda/respostas", {"titulo": "x", "texto": "  "})
        self.assertEqual(st, 400, d)

    def test_operador_sem_modulo_admin_nao_ve_nem_edita(self):
        c = self.usuario("operador1")
        self.assertEqual(c.pedir("GET", "/api/posvenda")[0], 403)
        self.assertEqual(c.pedir("GET", "/api/posvenda/resumo")[0], 403)
        self.assertEqual(c.pedir("POST", "/api/posvenda/respostas", {"titulo": "a", "texto": "b"})[0], 403)

    def test_consulta_ve_mas_nao_responde_nem_edita(self):
        c = self.usuario("consulta1", somente_leitura=1)
        self.assertEqual(c.pedir("GET", "/api/posvenda")[0], 200)
        self.assertEqual(c.pedir("POST", "/api/posvenda/respostas", {"titulo": "a", "texto": "b"})[0], 403)
        self.assertEqual(c.pedir("POST", "/api/posvenda/responder", {"pergunta_id": "1", "texto": "b"})[0], 403)
        self.assertEqual(c.pedir("POST", "/api/posvenda/config", {"saudacao@bauru": "Oi"})[0], 403)

    def test_config_so_chaves_conhecidas(self):
        st, d = self.cliente.pedir("POST", "/api/posvenda/config", {"saudacao@ribeirao": "Oi"})
        self.assertEqual(st, 400, d)
        st, d = self.cliente.pedir("POST", "/api/posvenda/config", {"saudacao@bauru": "Oi", "assinatura@aracatuba": "Noromak"})
        self.assertEqual(st, 200, d)
        st, d = self.cliente.pedir("GET", "/api/posvenda")
        self.assertEqual(d["config"]["saudacao@bauru"], "Oi")


class RevisaoAntesDeSubir(Base):
    """Os 4 pontos médios da revisão de 29/10."""

    def test_resposta_apagada_pelo_ml_volta_para_a_fila(self):
        # respondida pelo Noromak há mais tempo que a proteção e o ML diz "sem resposta" de novo (moderação)
        self.ml.perguntas["aracatuba"] = [pergunta(300)]
        self.rodada()
        enviados = []
        self.responder_com(enviados)
        S.posvenda_responder("300", "Serve sim.", "luis")
        self.rodada()   # busca atrasada do ML ainda traz a pergunta: continua respondida
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 300)["status"], "ANSWERED")
        velho = (datetime.now(timezone.utc) - timedelta(minutes=S.POSVENDA_PROTEGE_RESPOSTA_MIN + 5)).astimezone().isoformat()
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_pergunta_v1888 SET respondida_em=? WHERE pergunta_id='300'", (velho,))
            S.DB.commit()
        self.rodada()
        self.assertEqual(self.ler("ml_pergunta_v1888", "pergunta_id", 300)["status"], "UNANSWERED",
                         "resposta apagada pelo ML deixou a pergunta presa como respondida")

    def test_perguntas_das_duas_contas_antes_das_reclamacoes(self):
        self.ml.reclamacoes["aracatuba"] = [reclamacao(400, "aracatuba")]
        self.rodada()
        ordem = [(c, "perg" if p.startswith("/questions/search") else "recl")
                 for c, p in self.ml.chamadas if p.startswith(("/questions/search", "/post-purchase/v1/claims/search"))]
        primeira_recl = next(i for i, x in enumerate(ordem) if x[1] == "recl")
        self.assertEqual({c for c, t in ordem[:primeira_recl]}, {"aracatuba", "bauru"},
                         f"as perguntas de Bauru esperaram as reclamações de Araçatuba: {ordem}")

    def test_reclamacoes_rodando_numa_conta_nao_pulam_a_outra(self):
        self.ml.reclamacoes["bauru"] = [reclamacao(410, "bauru")]
        trava = S._posvenda_trava_conta("aracatuba")
        trava.acquire()   # a rotina automática está detalhando Araçatuba
        try:
            self.rodada()
        finally:
            trava.release()
        self.assertIsNotNone(self.ler("ml_reclamacao_v1888", "claim_id", 410), "Bauru foi pulada porque Araçatuba rodava")

    def test_buscar_agora_um_por_vez(self):
        entrou, solta = threading.Event(), threading.Event()
        orig = S.posvenda_rodada_contas

        def lenta(*a, **k):
            entrou.set()
            solta.wait(5)

        S.posvenda_rodada_contas = lenta
        try:
            st, d = self.cliente.pedir("POST", "/api/posvenda/sincronizar", {})
            self.assertEqual(st, 200, d)
            self.assertTrue(entrou.wait(5))
            st, d = self.cliente.pedir("POST", "/api/posvenda/sincronizar", {})
            self.assertEqual(st, 200, d)
            self.assertTrue(d["ja_estava"], d)
        finally:
            solta.set()
            S.posvenda_rodada_contas = orig

    def test_conta_desconectada_nao_conta_no_selo_nem_na_saude(self):
        self.ml.perguntas["bauru"] = [pergunta(420, horas=-5)]
        self.ml.reclamacoes["bauru"] = [reclamacao(421, "bauru")]
        self.rodada()
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_reclamacao_v1888 SET prazo=? WHERE claim_id='421'", (iso(-10),))
            S.DB.commit()
        S.ml_casas_conectadas = lambda: ["aracatuba"]   # Bauru caiu
        S._POSVENDA_ESTADO["contas"].pop("bauru", None)
        r = S.posvenda_resumo()
        self.assertEqual((r["sem_resposta"], r["atrasadas"]), (0, 0))
        self.assertNotEqual(S._saude_posvenda()[0]["nivel"], "critico")


class Migracao(Base):

    def test_reputacao_antiga_vira_de_aracatuba(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_reputacao_dia_v1888(dia,nivel,atualizado_em) VALUES('2026-10-20','3_yellow','x')")
            S.DB.commit()
        S.ensure_posvenda_contas_schema()
        r = self.ler("ml_reputacao_conta_v2001", "dia", "2026-10-20")
        self.assertEqual((r["conta"], r["nivel"]), ("aracatuba", "3_yellow"))


if __name__ == "__main__":
    import unittest
    unittest.main()
