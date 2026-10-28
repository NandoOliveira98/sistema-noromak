"""V19.69 — Quadro de tarefas (/tarefas). Decisões do dono, 21/10:

- 4 colunas fixas (A fazer, Em andamento, Aguardando, Concluído); Iniciar/Concluir medem o tempo;
- Plano agrupa cartões; cartão tem área, prioridade, prazo, responsável, filial (só filtro), checklist
  e comentários com histórico;
- Admin/Supervisor criam e editam; o responsável move, marca checklist e comenta; Consulta só vê;
  excluir (lixeira restaurável) só o perfil Admin;
- concluído há mais de 14 dias vai para Arquivados (reabrir volta);
- o quadro nasce com o plano "Aumentar faturamento — out/26";
- tarefas atrasadas entram no e-mail das 08:00.
"""

from datetime import datetime, timedelta

from base import SENHA_ADMIN, Cliente, S, TesteNoromak


class QuadroTarefas(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        with S.DB_LOCK:
            S.upsert_app_user("sup", "Supervisor", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.upsert_app_user("ana", "Ana", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True})
            S.upsert_app_user("beto", "Beto", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True})
            S.upsert_app_user("vera", "Vera", "consulta", True, SENHA_ADMIN, "teste")
            S.DB.commit()

    def como(self, usuario: str) -> Cliente:
        c = Cliente(self.url_base)
        c.entrar(usuario, SENHA_ADMIN, area="conferencia")
        return c

    def criar(self, cliente=None, **campos) -> dict:
        corpo = {"titulo": "Repor Water Off", "area": "Compras", "prioridade": "alta"}
        corpo.update(campos)
        status, r = (cliente or self.cliente).pedir("POST", "/api/tarefas/cartao", corpo)
        self.assertEqual(status, 200, r)
        return r["cartao"]

    def cartoes(self, cliente=None, extra: str = "") -> list:
        status, r = (cliente or self.cliente).pedir("GET", "/api/tarefas" + extra)
        self.assertEqual(status, 200, r)
        return r["cartoes"]

    # ---- carga inicial -------------------------------------------------------------------
    def test_nasce_com_o_plano_de_faturamento_sem_responsavel(self):
        status, r = self.cliente.pedir("GET", "/api/tarefas")
        self.assertEqual(status, 200, r)
        nomes = [p["nome"] for p in r["planos"]]
        self.assertIn("Aumentar faturamento — out/26", nomes)
        self.assertGreaterEqual(len(r["cartoes"]), 20)
        self.assertTrue(all(c["coluna"] == "a_fazer" and not c["responsavel"] for c in r["cartoes"]))

    def test_carga_inicial_nao_volta_no_reinicio_nem_ressuscita_excluido(self):
        alvo = self.cartoes()[0]
        status, r = self.cliente.pedir("POST", "/api/tarefas/excluir", {"id": alvo["id"]})
        self.assertEqual(status, 200, r)
        antes = len(self.cartoes())
        S.ensure_tarefas_schema()
        S.ensure_tarefas_schema()
        depois = self.cartoes()
        self.assertEqual(len(depois), antes, "o reinício duplicou ou recriou cartões da carga inicial")
        self.assertNotIn(alvo["id"], [c["id"] for c in depois])

    # ---- permissões ----------------------------------------------------------------------
    def test_operador_ve_o_quadro_mas_nao_cria(self):
        ana = self.como("ana")
        self.assertTrue(self.cartoes(ana))
        status, r = ana.pedir("POST", "/api/tarefas/cartao", {"titulo": "x"})
        self.assertEqual(status, 403, r)

    def test_supervisor_cria_e_edita_mas_nao_exclui(self):
        sup = self.como("sup")
        c = self.criar(sup, responsavel="ana")
        status, r = sup.pedir("POST", "/api/tarefas/cartao", {"id": c["id"], "versao": c["versao"], "titulo": "Novo título"})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["cartao"]["titulo"], "Novo título")
        status, r = sup.pedir("POST", "/api/tarefas/excluir", {"id": c["id"]})
        self.assertEqual(status, 403, r)

    def test_consulta_so_ve(self):
        vera = self.como("vera")
        self.assertTrue(self.cartoes(vera))
        c = self.criar(responsavel="vera")
        for rota, corpo in (("/api/tarefas/cartao", {"titulo": "x"}), ("/api/tarefas/mover", {"id": c["id"], "coluna": "andamento"}),
                            ("/api/tarefas/comentar", {"id": c["id"], "texto": "oi"})):
            status, r = vera.pedir("POST", rota, corpo)
            self.assertEqual(status, 403, (rota, r))

    def test_sem_login_nao_ve(self):
        status, _ = Cliente(self.url_base).pedir("GET", "/api/tarefas")
        self.assertEqual(status, 401)

    def test_tela_abre_para_qualquer_usuario_logado(self):
        import urllib.request
        ana = self.como("ana")
        req = urllib.request.Request(self.url_base + "/tarefas", headers={"Cookie": ana.cookie})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("Quadro de tarefas", resp.read().decode("utf-8"))

    # ---- execução pelo responsável -------------------------------------------------------
    def test_responsavel_inicia_e_conclui_e_o_tempo_fica_registrado(self):
        c = self.criar(responsavel="ana")
        ana = self.como("ana")
        status, r = ana.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "andamento"})
        self.assertEqual(status, 200, r)
        self.assertTrue(r["cartao"]["iniciado_em"])
        status, r = ana.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "concluido"})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["cartao"]["concluido_por"], "ana")
        self.assertTrue(r["cartao"]["concluido_em"])

    def test_quem_nao_e_responsavel_nao_move(self):
        c = self.criar(responsavel="ana")
        status, r = self.como("beto").pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "andamento"})
        self.assertEqual(status, 403, r)

    def test_coluna_invalida_recusada(self):
        c = self.criar(responsavel="ana")
        status, r = self.cliente.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "lixo"})
        self.assertEqual(status, 400, r)

    def test_checklist_marcado_pelo_responsavel_e_preservado_na_edicao(self):
        c = self.criar(responsavel="ana", checklist=[{"texto": "Cotar"}, {"texto": "Comprar"}])
        item = c["checklist"][0]["id"]
        status, r = self.como("ana").pedir("POST", "/api/tarefas/checklist", {"id": c["id"], "item": item, "feito": True})
        self.assertEqual(status, 200, r)
        atual = r["cartao"]
        lista = atual["checklist"] + [{"texto": "Receber"}]
        status, r = self.cliente.pedir("POST", "/api/tarefas/cartao", {"id": c["id"], "versao": atual["versao"], "checklist": lista})
        self.assertEqual(status, 200, r)
        feitos = {i["texto"]: i["feito"] for i in r["cartao"]["checklist"]}
        self.assertEqual(feitos, {"Cotar": True, "Comprar": False, "Receber": False})

    def test_comentario_e_historico(self):
        c = self.criar(responsavel="ana")
        ana = self.como("ana")
        ana.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "andamento"})
        status, r = ana.pedir("POST", "/api/tarefas/comentar", {"id": c["id"], "texto": "Fornecedor respondeu"})
        self.assertEqual(status, 200, r)
        status, r = ana.pedir("GET", f"/api/tarefas/cartao?id={c['id']}")
        self.assertEqual(status, 200, r)
        tipos = [(e["tipo"], e["usuario"]) for e in r["eventos"]]
        self.assertIn(("criado", S.ADMIN_USERNAME), tipos)
        self.assertIn(("movido", "ana"), tipos)
        self.assertIn(("comentario", "ana"), tipos)

    # ---- concorrência --------------------------------------------------------------------
    def test_edicao_com_tela_desatualizada_recusada(self):
        c = self.criar()
        self.cliente.pedir("POST", "/api/tarefas/cartao", {"id": c["id"], "versao": c["versao"], "titulo": "Outro PC"})
        status, r = self.cliente.pedir("POST", "/api/tarefas/cartao", {"id": c["id"], "versao": c["versao"], "titulo": "Este PC"})
        self.assertEqual(status, 409, r)
        self.assertEqual(r["cartao"]["titulo"], "Outro PC", "a edição velha passou por cima da nova")

    # ---- arquivo e lixeira ---------------------------------------------------------------
    def test_concluido_ha_mais_de_14_dias_vai_para_arquivados_e_reabre(self):
        c = self.criar(responsavel="ana")
        self.cliente.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "concluido"})
        velho = (datetime.now(S.BR_TZ) - timedelta(days=15)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("UPDATE tarefa_cartao_v1969 SET concluido_em=? WHERE id=?", (velho, c["id"]))
            S.DB.commit()
        self.assertNotIn(c["id"], [x["id"] for x in self.cartoes()])
        self.assertIn(c["id"], [x["id"] for x in self.cartoes(extra="?arquivados=1")])
        status, r = self.cliente.pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "a_fazer"})
        self.assertEqual(status, 200, r)
        self.assertFalse(r["cartao"]["concluido_em"])
        self.assertIn(c["id"], [x["id"] for x in self.cartoes()])

    def test_excluir_vai_para_lixeira_e_restaura(self):
        c = self.criar()
        status, r = self.cliente.pedir("POST", "/api/tarefas/excluir", {"id": c["id"]})
        self.assertEqual(status, 200, r)
        self.assertNotIn(c["id"], [x["id"] for x in self.cartoes()])
        self.assertIn(c["id"], [x["id"] for x in self.cartoes(extra="?lixeira=1")])
        status, r = self.cliente.pedir("POST", "/api/tarefas/restaurar", {"id": c["id"]})
        self.assertEqual(status, 200, r)
        self.assertIn(c["id"], [x["id"] for x in self.cartoes()])

    def test_cartao_excluido_nao_e_movido(self):
        c = self.criar(responsavel="ana")
        self.cliente.pedir("POST", "/api/tarefas/excluir", {"id": c["id"]})
        status, r = self.como("ana").pedir("POST", "/api/tarefas/mover", {"id": c["id"], "coluna": "andamento"})
        self.assertEqual(status, 404, r)

    # ---- contador e e-mail ---------------------------------------------------------------
    def test_resumo_do_usuario_conta_abertas_e_atrasadas(self):
        ontem = (datetime.now(S.BR_TZ).date() - timedelta(days=1)).isoformat()
        self.criar(responsavel="ana", prazo=ontem)
        self.criar(responsavel="ana")
        c3 = self.criar(responsavel="ana", prazo=ontem)
        self.cliente.pedir("POST", "/api/tarefas/mover", {"id": c3["id"], "coluna": "concluido"})
        self.criar(responsavel="beto", prazo=ontem)
        status, r = self.como("ana").pedir("GET", "/api/tarefas/resumo")
        self.assertEqual(status, 200, r)
        self.assertEqual((r["abertas"], r["atrasadas"]), (2, 1))

    def test_linha_das_atrasadas_no_email(self):
        # A carga inicial (plano faturamento-out26) tem cartões com prazo em outubro/2026: a partir
        # de 28/10 eles já estão atrasados. O teste parte só dos cartões que ele mesmo cria.
        with S.DB_LOCK:
            S.DB.execute("UPDATE tarefa_cartao_v1969 SET excluido_em='teste' WHERE excluido_em=''")
            S.DB.commit()
        self.assertEqual(S.tarefas_resumo_email(), "", "sem atraso não há linha")
        ontem = (datetime.now(S.BR_TZ).date() - timedelta(days=1)).isoformat()
        self.criar(responsavel="ana", prazo=ontem)
        self.criar(prazo=ontem)
        linha = S.tarefas_resumo_email()
        self.assertIn("2 atrasada", linha)
        self.assertIn("Ana", linha)
        self.assertIn("sem responsável", linha)

    def test_responsavel_precisa_existir(self):
        status, r = self.cliente.pedir("POST", "/api/tarefas/cartao", {"titulo": "x", "responsavel": "fantasma"})
        self.assertEqual(status, 400, r)


if __name__ == "__main__":
    import unittest
    unittest.main()
