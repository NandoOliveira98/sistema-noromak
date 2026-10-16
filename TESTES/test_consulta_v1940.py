"""V19.40 — perfil Consulta (somente leitura). Decisões do dono, 16/10:

- vê tudo (pedidos, Conferência, Financeiro, custos, Saúde...) menos Usuários e Segurança;
- não altera nada: o servidor recusa toda gravação (403 SOMENTE_LEITURA), mesmo de botão
  que a tela esqueceu de esconder;
- sempre todas as filiais;
- Conferência só para ver a fila (sem bipar, travar, concluir); sem Estação Zebra.
"""

import json

from base import SENHA_ADMIN, Cliente, S, TesteNoromak, gravar_como_integracao, linha, pedidos


class PerfilConsulta(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha("P1", "SKU-1")])
        with S.DB_LOCK:
            S.upsert_app_user("vera", "Vera", "consulta", True, SENHA_ADMIN, "teste")
            S.DB.commit()
        self.consulta = Cliente(self.url_base)
        self.usuario = self.consulta.entrar("vera", SENHA_ADMIN, area="admin")

    def fila(self) -> list:
        return pedidos()

    def assertSomenteLeitura(self, status: int, corpo: dict) -> None:
        self.assertEqual(status, 403, corpo)
        self.assertEqual(corpo.get("code"), "SOMENTE_LEITURA", corpo)

    def test_sessao_da_consulta(self):
        self.assertEqual(self.usuario["app_role"], "consulta")
        self.assertTrue(self.usuario["somente_leitura"])
        self.assertEqual(self.usuario["casa"], "todas", "Consulta vê sempre todas as filiais")
        p = self.usuario["permissions"]
        self.assertTrue(p["admin"] and p["conferencia"] and p["precificador"])
        self.assertFalse(p["estacao"], "Consulta não entra na Estação Zebra")
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='vera'").fetchone()
        self.assertFalse(S.is_supervisor_user(row), "Consulta não é Supervisor")

    def test_le_a_fila_e_as_telas_de_gestao(self):
        status, corpo = self.consulta.pedir("GET", "/api/storage?key=conf_pedidos")
        self.assertEqual(status, 200, corpo)
        status, _ = self.consulta.pedir("GET", "/api/financeiro/resumo")
        self.assertNotIn(status, (401, 403))

    def test_nao_grava_a_fila(self):
        antes = self.fila()
        status, corpo = self.consulta.pedir("POST", "/api/storage", {
            "key": "conf_pedidos", "newValue": "[]", "force": True})
        self.assertSomenteLeitura(status, corpo)
        self.assertEqual(self.fila(), antes, "REGRESSÃO: Consulta apagou a fila")

    def test_nao_bipa_nem_conclui_nem_trava(self):
        for rota in ("/api/order-progress", "/api/order-complete", "/api/order-lock",
                     "/api/order-line-delete", "/api/admin-order-upsert", "/api/print/queue/enqueue"):
            status, corpo = self.consulta.pedir("POST", rota, {"pedido": "P1", "orderId": "P1"})
            self.assertSomenteLeitura(status, corpo)
        self.assertEqual(len(self.fila()), 1)

    def test_nao_apaga_nem_conecta_integracao(self):
        status, corpo = self.consulta.pedir("DELETE", "/api/storage?key=conf_pedidos")
        self.assertSomenteLeitura(status, corpo)
        status, corpo = self.consulta.pedir("GET", "/api/integracoes/mercadolivre/connect")
        self.assertSomenteLeitura(status, corpo)

    def test_nao_ve_usuarios_nem_seguranca(self):
        self.assertEqual(self.consulta.pedir("GET", "/api/roles")[0], 403)
        self.assertEqual(self.consulta.pedir("GET", "/api/storage?key=conf_usuarios")[0], 403)
        self.assertEqual(self.consulta.pedir("GET", "/api/security/status")[0], 403)

    def test_consultas_por_post_continuam_valendo(self):
        status, corpo = self.consulta.pedir("POST", "/api/full/kits", {"skus": ["SKU-1"]})
        self.assertEqual(status, 200, corpo)
        status, _ = self.consulta.pedir("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)

    def test_admin_cria_consulta_pela_tela(self):
        status, corpo = self.cliente.pedir("POST", "/api/roles", {
            "username": "rui", "display_name": "Rui", "role": "consulta", "password": SENHA_ADMIN,
            "permissions": {"estacao": True}})
        self.assertEqual(status, 200, corpo)
        _, lista = self.cliente.pedir("GET", "/api/roles")
        rui = next(u for u in lista["users"] if u["username"] == "rui")
        self.assertEqual(rui["role"], "consulta")
        self.assertEqual(rui["display_profile"], "Consulta")
        self.assertEqual(rui["filial"], "todas")
        self.assertFalse(rui["can_estacao"], "Consulta nunca tem a Estação")

    def test_perfil_sobrevive_ao_salvar_a_lista_de_usuarios(self):
        usuarios = S.app_users_payload()
        vera = next(u for u in usuarios if u["Login"] == "vera")
        self.assertEqual(vera["Perfil"], "Consulta")
        S.apply_app_users_payload(json.dumps(usuarios), "teste")
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='vera'").fetchone()
        self.assertEqual(S.role_efetivo(row), "consulta", "salvar a lista não pode virar Operador")
