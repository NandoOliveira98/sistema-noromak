"""V19.01 — Auditor de cobranças do Mercado Livre.

Decisões do dono (11/10): o sistema encontra na fatura do ML o que parece cobrado indevidamente
(estorno que não veio, frete de devolução cobrado em reclamação ganha/coberta, tarifa ou frete
acima do esperado, ajuste de peso) e acompanha cada caso até o fim. A contestação é feita pela
equipe no ML — o auditor só lê. O que não pode quebrar:
- a rodada nunca desfaz a situação marcada por uma pessoa, e caso ignorado nunca volta;
- o mesmo lançamento da fatura nunca vira dois casos;
- a detecção não chama o ML (só lê o banco);
- o frete do carrinho é comparado por envio, não por pedido.
"""

from datetime import date, timedelta
from unittest import mock

from base import RAIZ, Cliente, S, TesteNoromak

HOJE = date(2026, 10, 11)
SENHA = "Senha-de-teste-1901"


def dia(n: int) -> str:
    """Data ISO n dias antes de HOJE."""
    return (HOJE - timedelta(days=n)).isoformat()


class Base(TesteNoromak):

    _seq = 0

    def lanc(self, order_id: str, sub: str, valor: float, quando: str, *, shipment: str = "",
             periodo: str = "", credito: bool = False) -> str:
        Base._seq += 1
        did = f"D{Base._seq}"
        periodo = periodo or (quando[:7] + "-01")
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO ml_billing_period_detail(period_key,document_type,detail_id,order_id,shipment_id,"
                "detail_amount,detail_type,detail_sub_type,transaction_detail,is_credit,detail_date) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (periodo, "BILL", did, order_id, shipment, valor, "BONUS" if credito else "CHARGE", sub,
                 "Lançamento " + sub, 1 if credito else 0, quando))
            S.DB.commit()
        return did

    def venda(self, order_id: str, sale_fee: float | None, quando: str, shipment: str = "", ativa: bool = True) -> None:
        with S.DB_LOCK:
            if ativa:
                S.DB.execute(
                    "INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,qty,amount,status,updated_at) "
                    "VALUES('ml',?,?,?,?,1,100,'paid',?)", (order_id, "L1", quando + "T10:00:00", quando, quando))
            S.DB.execute(
                "INSERT INTO ml_venda_custo_v1830(order_id,line_key,sku,sale_fee,shipment_id,sale_day,captured_at) "
                "VALUES(?,?,?,?,?,?,?)", (order_id, "L1", "SKU", sale_fee, shipment, quando, quando))
            S.DB.commit()

    def cancelado(self, order_id: str, quando: str) -> None:
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_pedido_status_v1901(order_id,status,cancelado_em,consultado_em) VALUES(?,?,?,?)",
                         (order_id, "cancelled", quando + "T12:00:00-03:00", quando))
            S.DB.commit()

    def frete(self, shipment: str, bruto: float, custo: float = 10.0) -> None:
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_frete_envio_v1830(shipment_id,cost,bruto,captured_at,captured_epoch,erro) "
                         "VALUES(?,?,?,'',0,'')", (shipment, custo, bruto))
            S.DB.commit()

    def reclamacao(self, claim: str, order_id: str, *, fechada: str = "", beneficiado: str = "", cobertura=None,
                   frete_dev=None, total=None, reembolsado=None, devolucao: int = 0) -> None:
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO ml_reclamacao_v1888(claim_id,order_id,status,fechada_em,beneficiado,cobertura,"
                "frete_devolucao,pedido_total,reembolsado,tem_devolucao,aberta_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (claim, order_id, "closed" if fechada else "opened", fechada, beneficiado, cobertura,
                 frete_dev, total, reembolsado, devolucao, dia(60)))
            S.DB.commit()

    def rodar(self, hoje: date = HOJE) -> dict:
        casos = S.auditoria_detectar(hoje)
        return S.auditoria_gravar(casos, hoje)

    def caso(self, caso_id: str) -> dict | None:
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM ml_cobranca_caso_v1901 WHERE caso_id=?", (caso_id,)).fetchone()
        return dict(r) if r else None

    def todos(self) -> list[dict]:
        with S.DB_LOCK:
            return [dict(r) for r in S.DB.execute("SELECT * FROM ml_cobranca_caso_v1901 ORDER BY caso_id")]


class EstornoQueNaoVeio(Base):

    def test_cancelado_com_tarifa_e_frete_sem_credito_vira_caso(self):
        self.venda("O1", 18.40, dia(40), "S1", ativa=False)
        self.lanc("O1", "CVVML", 18.40, dia(40))
        self.lanc("O1", "CXDE", 22.90, dia(40), shipment="S1")
        self.cancelado("O1", dia(39))
        self.rodar()
        c = self.caso("estorno:O1")
        self.assertIsNotNone(c, "estorno que não veio não virou caso")
        self.assertEqual((c["situacao"], c["cobrado"], c["esperado"], c["diferenca"]), ("a_contestar", 41.3, 0.0, 41.3))
        self.assertEqual(len(S.json.loads(c["lancamentos_json"])), 2)

    def test_cancelado_com_credito_nao_vira_caso(self):
        self.lanc("O2", "CVVML", 18.40, dia(40))
        self.lanc("O2", "BVVML", 18.40, dia(38), credito=True)
        self.cancelado("O2", dia(39))
        self.rodar()
        self.assertIsNone(self.caso("estorno:O2"))

    def test_carencia_de_30_dias(self):
        self.lanc("O3", "CVVML", 10.0, dia(29))
        self.cancelado("O3", dia(29))
        self.rodar()
        self.assertEqual(self.caso("estorno:O3")["situacao"], "aguardando")
        self.rodar(HOJE + timedelta(days=2))
        self.assertEqual(self.caso("estorno:O3")["situacao"], "a_contestar")

    def test_credito_dentro_da_carencia_vira_recuperado(self):
        self.lanc("O4", "CVVML", 10.0, dia(10))
        self.cancelado("O4", dia(10))
        self.rodar()
        self.assertEqual(self.caso("estorno:O4")["situacao"], "aguardando")
        self.lanc("O4", "BVVML", 10.0, dia(1), credito=True)
        self.rodar()
        c = self.caso("estorno:O4")
        self.assertEqual((c["situacao"], c["recuperado"]), ("recuperado", 10.0))

    def test_reembolso_total_espera_so_a_tarifa(self):
        self.venda("O5", 12.60, dia(50), "S5")
        self.lanc("O5", "CVVML", 12.60, dia(50))
        self.lanc("O5", "CXDE", 20.0, dia(50), shipment="S5")
        self.reclamacao("C5", "O5", fechada=dia(40), total=100.0, reembolsado=100.0, devolucao=1)
        self.rodar()
        c = self.caso("estorno:O5")
        self.assertIsNotNone(c)
        self.assertEqual((c["cobrado"], c["diferenca"]), (12.6, 12.6), "o frete da ida não deveria ser esperado de volta")

    def test_reembolso_parcial_fica_fora(self):
        self.venda("O6", 12.60, dia(50))
        self.lanc("O6", "CVVML", 12.60, dia(50))
        self.reclamacao("C6", "O6", fechada=dia(40), total=100.0, reembolsado=30.0)
        self.rodar()
        self.assertIsNone(self.caso("estorno:O6"))


class FreteDeDevolucao(Base):

    def test_vendedor_ganhou_vira_caso(self):
        self.reclamacao("C1", "P1", fechada=dia(40), beneficiado="respondent", frete_dev=27.35)
        self.rodar()
        c = self.caso("devolucao:P1")
        self.assertEqual((c["situacao"], c["cobrado"], c["claim_id"]), ("a_contestar", 27.35, "C1"))

    def test_cobertura_do_ml_vira_caso(self):
        self.reclamacao("C2", "P2", fechada=dia(40), beneficiado="complainant", cobertura=1, frete_dev=33.10)
        self.rodar()
        self.assertIsNotNone(self.caso("devolucao:P2"))

    def test_comprador_ganhou_sem_cobertura_nao_vira_caso(self):
        self.reclamacao("C3", "P3", fechada=dia(40), beneficiado="complainant", cobertura=0, frete_dev=20.0)
        self.rodar()
        self.assertIsNone(self.caso("devolucao:P3"))

    def test_reclamacao_aberta_nao_vira_caso(self):
        self.reclamacao("C4", "P4", beneficiado="respondent", frete_dev=20.0)
        self.rodar()
        self.assertIsNone(self.caso("devolucao:P4"))


class AcimaDoEsperado(Base):

    def test_tarifa_acima_do_minimo_vira_caso(self):
        self.venda("T1", 38.20, dia(20))
        self.lanc("T1", "CVVML", 40.70, dia(20))
        self.rodar()
        c = self.caso("tarifa_acima:T1")
        self.assertEqual((c["situacao"], c["cobrado"], c["esperado"], c["diferenca"]), ("a_contestar", 40.7, 38.2, 2.5))

    def test_diferenca_pequena_so_soma_no_cartao(self):
        self.venda("T2", 38.20, dia(20))
        self.lanc("T2", "CVVML", 39.70, dia(20))
        r = self.rodar()
        self.assertIsNone(self.caso("tarifa_acima:T2"))
        mes = dia(20)[:7]
        self.assertEqual(r["pequenas"][mes], {"qtd": 1, "total": 1.5})

    def test_frete_comparado_por_envio_no_carrinho(self):
        for oid in ("K1", "K2", "K3"):
            self.venda(oid, 5.0, dia(15), "SK")
            self.lanc(oid, "CVVML", 5.0, dia(15))
        # O mesmo lançamento de frete do carrinho aparece em cada pedido (PK inclui o order_id).
        for oid in ("K1", "K2", "K3"):
            with S.DB_LOCK:
                S.DB.execute(
                    "INSERT INTO ml_billing_period_detail(period_key,document_type,detail_id,order_id,shipment_id,"
                    "detail_amount,detail_type,detail_sub_type,is_credit,detail_date) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (dia(15)[:7] + "-01", "BILL", "FRETE-SK", oid, "SK", 64.90, "CHARGE", "CXDE", 0, dia(15)))
                S.DB.commit()
        self.frete("SK", 52.40)
        self.rodar()
        c = self.caso("frete_acima:SK")
        self.assertIsNotNone(c)
        self.assertEqual((c["cobrado"], c["esperado"], c["diferenca"]), (64.9, 52.4, 12.5),
                         "o frete do carrinho foi somado uma vez por pedido")

    def test_frete_igual_ao_valor_cheio_nao_vira_caso(self):
        self.venda("F1", 5.0, dia(15), "SF1")
        self.lanc("F1", "CXDE", 52.40, dia(15), shipment="SF1")
        self.frete("SF1", 52.40)
        self.rodar()
        self.assertIsNone(self.caso("frete_acima:SF1"))

    def test_lancamento_nunca_em_dois_casos(self):
        """Frete de pedido cancelado é caso de estorno, nunca também de frete acima do esperado."""
        self.venda("X1", 10.0, dia(40), "SX1", ativa=False)
        self.lanc("X1", "CVVML", 10.0, dia(40))
        self.lanc("X1", "CXDE", 60.0, dia(40), shipment="SX1")
        self.frete("SX1", 50.0)
        self.cancelado("X1", dia(39))
        self.rodar()
        self.assertIsNotNone(self.caso("estorno:X1"))
        self.assertIsNone(self.caso("frete_acima:SX1"))
        self.assertIsNone(self.caso("tarifa_acima:X1"))


class AjusteDePeso(Base):

    def test_desligado_sem_o_codigo(self):
        self.venda("W1", 5.0, dia(15))
        self.lanc("W1", "CXXPESO", 15.0, dia(15))
        self.rodar()
        self.assertFalse([c for c in self.todos() if c["tipo"] == "peso"])

    def test_ligado_vira_caso_por_lancamento(self):
        self.venda("W2", 5.0, dia(15))
        did = self.lanc("W2", "CXXPESO", 15.0, dia(15))
        with mock.patch.object(S, "ML_FATURA_AJUSTE_PESO", {"CXXPESO"}):
            self.rodar()
        c = self.caso("peso:" + did)
        self.assertEqual((c["situacao"], c["cobrado"], c["esperado"]), ("a_contestar", 15.0, 0.0))


class SituacaoHumana(Base):

    def preparar(self) -> None:
        self.lanc("H1", "CVVML", 20.0, dia(40))
        self.cancelado("H1", dia(39))
        self.rodar()

    def test_rodada_nao_desfaz_contestado_nem_negado(self):
        self.preparar()
        self.assertTrue(S.auditoria_caso_acao("estorno:H1", "contestar", "luis", protocolo="ML-1")["ok"])
        self.rodar()
        c = self.caso("estorno:H1")
        self.assertEqual((c["situacao"], c["protocolo"], c["contestado_por"]), ("contestado", "ML-1", "luis"))
        self.assertTrue(S.auditoria_caso_acao("estorno:H1", "negar", "luis")["ok"])
        self.rodar()
        self.assertEqual(self.caso("estorno:H1")["situacao"], "negado")

    def test_ignorado_nunca_volta(self):
        self.preparar()
        r = S.auditoria_caso_acao("estorno:H1", "ignorar", "luis")
        self.assertFalse(r["ok"], "ignorar sem motivo foi aceito")
        self.assertTrue(S.auditoria_caso_acao("estorno:H1", "ignorar", "luis", motivo="cobrança correta")["ok"])
        self.rodar()
        self.rodar()
        c = self.caso("estorno:H1")
        self.assertEqual((c["situacao"], c["motivo_ignorado"]), ("ignorado", "cobrança correta"))
        self.assertEqual(len(self.todos()), 1)
        self.assertTrue(S.auditoria_caso_acao("estorno:H1", "reabrir", "luis")["ok"])
        self.assertEqual(self.caso("estorno:H1")["situacao"], "a_contestar")

    def test_recuperado_total_parcial_e_negado_que_vira_recuperado(self):
        self.preparar()
        S.auditoria_caso_acao("estorno:H1", "contestar", "luis", protocolo="ML-2")
        self.lanc("H1", "BVVML", 5.0, dia(2), credito=True)
        self.rodar()
        c = self.caso("estorno:H1")
        self.assertEqual((c["situacao"], c["recuperado"]), ("recuperado_parcial", 5.0))
        S.auditoria_caso_acao("estorno:H1", "contestar", "luis", protocolo="ML-3")
        S.auditoria_caso_acao("estorno:H1", "negar", "luis")
        self.lanc("H1", "BVVML", 15.0, dia(1), credito=True)
        self.rodar()
        c = self.caso("estorno:H1")
        self.assertEqual((c["situacao"], c["recuperado"]), ("recuperado", 20.0))

    def test_acao_registra_no_audit_log(self):
        self.preparar()
        S.auditoria_caso_acao("estorno:H1", "contestar", "luis", protocolo="ML-9")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'ml_cobranca_%'").fetchone()[0]
        self.assertGreaterEqual(n, 1)


class SemChamarOML(Base):

    def test_deteccao_nao_chama_o_ml(self):
        self.lanc("N1", "CVVML", 20.0, dia(40))
        self.cancelado("N1", dia(39))
        with mock.patch.object(S, "ml_api_get", side_effect=AssertionError("chamou o ML")), \
             mock.patch.object(S, "_ml_profitability_api_get", side_effect=AssertionError("chamou o ML")):
            self.rodar()
        self.assertIsNotNone(self.caso("estorno:N1"))

    def test_consulta_status_so_dos_que_sumiram_das_vendas(self):
        self.venda("V1", 5.0, dia(20))                      # venda ativa: não consulta
        self.venda("V2", 5.0, dia(20), ativa=False)         # sumiu das vendas: consulta
        self.lanc("V1", "CVVML", 5.0, dia(20))
        self.lanc("V2", "CVVML", 5.0, dia(20))
        chamados = []

        def falso(caminho, **kw):
            chamados.append(caminho)
            return {"id": 2, "status": "cancelled", "cancel_detail": {"date": dia(19) + "T10:00:00.000-04:00"}}
        with mock.patch.object(S, "ml_api_get", side_effect=falso):
            r = S.auditoria_consultar_status(HOJE, limite=50, pausa=0)
            S.auditoria_consultar_status(HOJE, limite=50, pausa=0)  # 2ª vez: já consultado
        self.assertEqual(chamados, ["/orders/V2"])
        self.assertEqual(r["consultados"], 1)
        with S.DB_LOCK:
            st = S.DB.execute("SELECT status FROM ml_pedido_status_v1901 WHERE order_id='V2'").fetchone()[0]
        self.assertEqual(st, "cancelled")

    def test_valor_cheio_do_frete_guardado_da_mesma_resposta(self):
        self.assertEqual(S._ml_shipping_gross_parse({"gross_amount": 128.89, "senders": [{"cost": 22.9}]}), 128.89)
        self.assertIsNone(S._ml_shipping_gross_parse({"senders": [{"cost": 22.9}]}))


class TelaERotas(Base):

    def preparar(self) -> None:
        self.lanc("R1", "CVVML", 20.0, dia(40))
        self.cancelado("R1", dia(39))
        self.rodar()

    def test_payload_da_tela(self):
        self.preparar()
        status, corpo = self.cliente.pedir("GET", "/api/auditoria-ml")
        self.assertEqual(status, 200, corpo)
        caso = corpo["casos"][0]
        self.assertEqual(caso["caso_id"], "estorno:R1")
        self.assertIn("Solicito a devolução de R$ 20,00", caso["texto"])
        self.assertEqual(corpo["resumo"]["a_contestar"]["qtd"], 1)
        self.assertFalse(corpo["peso_ligado"])

    def test_acao_pela_rota(self):
        self.preparar()
        status, corpo = self.cliente.pedir("POST", "/api/auditoria-ml/caso",
                                           {"caso_id": "estorno:R1", "acao": "contestar", "protocolo": "ML-77"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(self.caso("estorno:R1")["contestado_por"], S.os.environ["EXP_ADMIN_USUARIO"])
        status, corpo = self.cliente.pedir("POST", "/api/auditoria-ml/caso", {"caso_id": "estorno:R1", "acao": "xpto"})
        self.assertEqual(status, 400, corpo)

    def test_prazo_marca_fora_do_prazo(self):
        self.preparar()
        status, _ = self.cliente.pedir("POST", "/api/auditoria-ml/config", {"prazo_contestacao_dias": 30})
        self.assertEqual(status, 200)
        _, corpo = self.cliente.pedir("GET", "/api/auditoria-ml")
        self.assertTrue(corpo["casos"][0]["fora_prazo"])

    def test_permissoes(self):
        with S.DB_LOCK:
            S.upsert_app_user("op_1901", "Operador", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.upsert_app_user("sup_1901", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        op = Cliente(self.url_base)
        op.entrar("op_1901", SENHA, area="conferencia")
        for metodo, rota in (("GET", "/api/auditoria-ml"), ("POST", "/api/auditoria-ml/caso"),
                             ("POST", "/api/auditoria-ml/rodar"), ("GET", "/api/auditoria-ml/codigos")):
            status, _ = op.pedir(metodo, rota, {} if metodo == "POST" else None)
            self.assertEqual(status, 403, f"operador acessou {rota}")
        sup = Cliente(self.url_base)
        sup.entrar("sup_1901", SENHA, area="admin")
        self.assertEqual(sup.pedir("GET", "/api/auditoria-ml")[0], 200, "Supervisor deveria ver a tela")
        self.assertEqual(sup.pedir("GET", "/api/auditoria-ml/codigos")[0], 403, "códigos são só do perfil Admin")

    def test_codigos_da_fatura(self):
        self.lanc("Q1", "CVVML", 20.0, dia(40))
        self.lanc("Q2", "CXXPESO", 9.0, dia(40))
        status, corpo = self.cliente.pedir("GET", "/api/auditoria-ml/codigos")
        self.assertEqual(status, 200, corpo)
        por = {c["sub_type"]: c for c in corpo["codigos"]}
        self.assertEqual(por["CVVML"]["classe"], "tarifa")
        self.assertEqual(por["CXXPESO"]["classe"], "outros")
        self.assertEqual(por["CXXPESO"]["total"], 9.0)

    def test_tela_e_menu(self):
        import urllib.request
        req = urllib.request.Request(self.url_base + "/auditoria-ml")
        req.add_header("Cookie", self.cliente.cookie)
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("Cobranças do ML", r.read().decode("utf-8"))
        menu = (RAIZ / "APP" / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('href: "/auditoria-ml"', menu)
        self.assertIn("ml_auditoria_loop", S.SAUDE_WORKERS_INFO)


class LinhaDoEmail(Base):

    def test_uma_vez_por_caso(self):
        self.lanc("E1", "CVVML", 20.0, dia(40))
        self.cancelado("E1", dia(39))
        self.rodar()
        linha = S.auditoria_resumo_email(marcar=True)
        self.assertIn("1 caso(s) novo(s) a contestar", linha)
        self.assertIn("R$ 20,00", linha)
        self.assertEqual(S.auditoria_resumo_email(marcar=True), "", "o mesmo caso foi avisado duas vezes")

    def test_relatorio_leva_a_linha(self):
        dados = S.relatorio_diario_dados(dia(1))
        dados["cobrancas"] = "Cobranças do ML: 2 caso(s) novo(s) a contestar (R$ 10,00)"
        self.assertIn("Cobranças do ML", S.relatorio_diario_texto(dados))
        self.assertIn("Cobranças do ML", S.relatorio_diario_html(dados))



class IconeDasTelas(TesteNoromak):
    """V19.02: as telas novas declaravam ícone vazio (data:,) e a aba ficava sem o símbolo do Noromak."""

    def test_toda_tela_tem_o_icone_do_noromak(self):
        for pagina in sorted((RAIZ / "APP").glob("*.html")):
            html = pagina.read_text(encoding="utf-8")
            self.assertIn('rel="icon" href="/favicon.ico', html, f"{pagina.name} sem o ícone do Noromak na aba")
            self.assertNotIn('href="data:,"', html, f"{pagina.name} com ícone vazio")

if __name__ == "__main__":
    import unittest
    unittest.main()
