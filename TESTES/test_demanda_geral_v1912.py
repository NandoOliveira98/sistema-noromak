"""V19.12 — Demanda de Compra geral (13/10): o 504 da tela vinha do estoque lido na hora
pelo navegador. Agora o servidor gera em segundo plano, guarda, separa por marca, e manda
o e-mail de segunda (só com a chave ligada). Apollo e SMTP simulados."""

import io
import json
import unittest
import zipfile
from datetime import datetime, timedelta

from base import S, TesteNoromak, RAIZ


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S.save_storage("precificador_online_v1", None, json.dumps({"products": [
            {"sku": "0893600022", "produto": "Arruela Wurth", "custo": 2.5, "curva": "A"},
            {"sku": "816.0009-0", "produto": "Compressor Schulz", "custo": 100.0, "curva": "A"},
            {"sku": "P10031", "produto": "Paralama traseiro", "custo": 77.0, "curva": "B"},
            {"sku": "9999", "produto": "Parada sem venda", "custo": 1.0}]}), False, actor="t", actor_role="admin")
        S.compras_salvar_fornecedor("WURTH", {"prefixos": ["0893"], "pedido_minimo": 0, "prazo": 7, "cobertura": 30}, "t")
        S.compras_salvar_fornecedor("SCHULZ", {"prefixos": ["816."], "pedido_minimo": 0, "prazo": 7, "cobertura": 30}, "t")
        hoje = datetime.now(S.BR_TZ).date().isoformat()
        with S.DB_LOCK:
            for i, (sku, q) in enumerate([("0893600022", 30), ("816.0009-0", 6), ("P10031", 7)]):
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES('ml',?,'1',?,?,?,'MLB1','t',?,10,'paid','')", (f"o{i}", hoje, hoje, sku, q))
            S.DB.commit()
        self.consultas = []
        self.apollo = {"0893600022": 5, "816.0009-0": 0, "P10031": 1}
        self._orig = (S.crm_stock_lookup, S.enviar_email_smtp)

        def falso(cod, op="", wait_seconds=0.0):
            self.consultas.append(cod)
            v = self.apollo.get(cod)
            if v is None:
                raise RuntimeError("Apollo fora")
            return {"estoques": [{"revenda": 1, "disponivel": v}, {"revenda": 2, "disponivel": 0}], "casas_sem_resposta": []}
        S.crm_stock_lookup = falso
        self.emails = []
        S.enviar_email_smtp = lambda assunto, texto, html, dest, anexos=None: self.emails.append((assunto, html, dest, anexos)) or dest

    def tearDown(self):
        S.crm_stock_lookup, S.enviar_email_smtp = self._orig
        super().tearDown()


class Geracao(Base):
    def test_consulta_so_o_que_vendeu_e_guarda(self):
        r = S.compras_geral_rodada(intervalo_s=0)
        self.assertEqual(sorted(self.consultas), ["0893600022", "816.0009-0", "P10031"])   # 9999 não vendeu
        self.assertTrue(r["completa"])
        self.assertEqual(S.compras_geral_info()["ultima"]["codigos"], 3)
        item = {x["codigo"]: x for x in S.compras_demanda_payload("WURTH", 30)["itens"]}["0893600022"]
        self.assertEqual(item["estoque"]["available"], 5)       # Disponível guardado, sem nova consulta
        self.assertEqual(len(self.consultas), 3)

    def test_apollo_sem_resposta_nao_vira_zero(self):
        del self.apollo["P10031"]
        r = S.compras_geral_rodada(intervalo_s=0)
        self.assertEqual(r["sem_resposta"], 1)
        e = S.compras_estoque_guardado(["P10031"])["P10031"]
        self.assertFalse(e["stock_known"])
        self.assertIsNone(e["available"])

    def test_so_uma_marca(self):
        S.compras_geral_rodada("SCHULZ", intervalo_s=0)
        self.assertEqual(self.consultas, ["816.0009-0"])
        self.assertEqual(S.compras_geral_info()["ultima"], {})   # só marca não conta como geral
        # V19.44: a demanda da marca fica com data própria.
        m = S.compras_geral_info()["ultimas_marcas"]["SCHULZ"]
        self.assertEqual(m["codigos"], 1)
        self.assertTrue(m["em"])

    def test_andamento(self):
        vistos = []
        orig = S.crm_stock_lookup
        S.crm_stock_lookup = lambda cod, op="", w=0.0: vistos.append(dict(S.compras_andamento())) or orig(cod, op, w)
        S.compras_geral_rodada(intervalo_s=0)
        self.assertEqual([(v["feito"], v["total"], v["rodando"]) for v in vistos], [(0, 3, True), (1, 3, True), (2, 3, True)])
        self.assertFalse(S.compras_andamento()["rodando"])


class Marcas(Base):
    def test_sem_marca_e_marca_manual(self):
        self.assertEqual([x["codigo"] for x in S.compras_sem_marca_payload()["itens"]], ["P10031"])
        S.compras_marca_manual_salvar("P10031", "SCHULZ", "admin")
        self.assertEqual(S.compras_sem_marca_payload()["itens"], [])
        self.assertIn("P10031", [x["codigo"] for x in S.compras_demanda_payload("SCHULZ", 30)["itens"]])

    def test_manual_manda_sobre_a_regra(self):
        S.compras_marca_manual_salvar("0893600022", "SCHULZ", "admin")
        self.assertNotIn("0893600022", [x["codigo"] for x in S.compras_demanda_payload("WURTH", 30)["itens"]])
        self.assertIn("0893600022", [x["codigo"] for x in S.compras_demanda_payload("SCHULZ", 30)["itens"]])
        S.compras_marca_manual_salvar("0893600022", "", "admin")   # vazio volta às regras
        self.assertIn("0893600022", [x["codigo"] for x in S.compras_demanda_payload("WURTH", 30)["itens"]])

    def test_fornecedor_inexistente_recusa(self):
        with self.assertRaises(ValueError):
            S.compras_marca_manual_salvar("P10031", "NAO EXISTE", "admin")


class Email(Base):
    SEG_01 = datetime(2026, 10, 12, 1, 5, tzinfo=S.BR_TZ)
    SEG_07 = datetime(2026, 10, 12, 7, 5, tzinfo=S.BR_TZ)

    def test_calculo_e_planilha(self):
        S.compras_geral_rodada(intervalo_s=0)
        rel = S.compras_geral_relatorio()
        schulz = next(m for m in rel["marcas"] if m["fornecedor"] == "SCHULZ")
        # 6 em 30 dias = 0,2/dia × 37 dias = 7,4 -> 8, estoque 0
        self.assertEqual(schulz["itens"][0]["comprar"], 8)
        self.assertEqual(schulz["total"], 800.0)
        self.assertEqual([x["codigo"] for x in rel["sem_marca"]], ["P10031"])
        _, _, html, xlsx = S.compras_geral_email(rel)
        self.assertIn("SCHULZ", html)
        nomes = zipfile.ZipFile(io.BytesIO(xlsx)).read("xl/workbook.xml").decode()
        for aba in ("Resumo", "SCHULZ", "WURTH", "Sem marca"):
            self.assertIn(f'name="{aba}"', nomes)

    def test_segunda_gera_e_email_so_ligado(self):
        orig = S.relatorio_diario_config
        S.relatorio_diario_config = lambda: {**orig(), "destinatarios": ["dono@x"]}
        try:
            self.assertEqual(S.compras_geral_passo(self.SEG_01 - timedelta(days=1)), "")   # domingo
            self.assertEqual(S.compras_geral_passo(self.SEG_01), "gerou")
            self.assertEqual(S.compras_geral_passo(self.SEG_01 + timedelta(hours=1)), "")    # já gerou na semana
            self.assertEqual(S.compras_geral_passo(self.SEG_07), "")                         # e-mail desligado
            self.assertEqual(self.emails, [])
            S._compras_estado_gravar("email_ligado", "1")
            self.assertEqual(S.compras_geral_passo(self.SEG_07), "email")
            self.assertEqual(self.emails[0][2], ["dono@x"])
            self.assertEqual(S.compras_geral_passo(self.SEG_07 + timedelta(hours=2)), "")   # 1x por semana
        finally:
            S.relatorio_diario_config = orig

    def test_worker_no_painel_de_saude(self):
        self.assertIn("compras_geral_loop", S.SAUDE_WORKERS_INFO)


class Rotas(Base):
    def test_operador_403_e_admin_liga_email(self):
        from base import Cliente
        with S.DB_LOCK:
            S.upsert_app_user("op_1912", "Operador", "operador", True, "Senha-de-teste-1912", "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.DB.commit()
        op = Cliente(self.url_base)
        op.entrar("op_1912", "Senha-de-teste-1912", area="conferencia")
        self.assertEqual(op.pedir("GET", "/api/compras/geral")[0], 403)
        self.assertEqual(op.pedir("POST", "/api/compras/marca", {"codigo": "P10031", "fornecedor": "SCHULZ"})[0], 403)
        st, c = self.cliente.pedir("POST", "/api/compras/geral/email", {"ligado": True})
        self.assertEqual(st, 200, c)
        self.assertTrue(c["email_ligado"])
        st, c = self.cliente.pedir("POST", "/api/compras/marca", {"codigo": "P10031", "fornecedor": "SCHULZ"})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("GET", "/api/compras/sem-marca")
        self.assertEqual((st, c["itens"]), (200, []))

    def test_tela_usa_o_guardado(self):
        html = (RAIZ / "APP" / "compras.html").read_text(encoding="utf-8")
        self.assertIn("/api/compras/geral/gerar", html)
        self.assertIn("/api/compras/sem-marca", html)
        self.assertIn("/api/compras/marca", html)
        self.assertIn('id="geralBarra"', html)
        self.assertNotIn('"/api/full/apollo-stock"', html)   # a tela não consulta mais o Apollo na hora


if __name__ == "__main__":
    unittest.main()
