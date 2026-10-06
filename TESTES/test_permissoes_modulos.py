"""V18.68 — desmarcar um módulo de um usuário não pode "voltar sozinho".

Em produção (06/10/2026): o dono desmarcava Estação ou Precificador do Eduardo
(Supervisor), salvava, e a caixa voltava marcada. O POST /api/roles trocava as
permissões de todo Supervisor por "tudo liberado". E a cada reinício do servidor,
todo usuário com perfil Admin voltava a ter todos os módulos.
"""

import unittest

from base import S, TesteNoromak

SENHA = "Senha-de-teste-1868"


class ModulosDoUsuario(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        with S.DB_LOCK:
            S.upsert_app_user("eduardo", "Eduardo", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True, "estacao": True, "precificador": True})
            S.upsert_app_user("admin_x", "Admin X", "admin", True, SENHA, "teste")
            S.DB.commit()

    def salvar(self, username: str, perfil: str, **permissoes) -> None:
        status, corpo = self.cliente.pedir("POST", "/api/roles", {
            "username": username, "display_name": username.title(), "role": perfil,
            "active": True, "permissions": permissoes,
        })
        self.assertEqual(status, 200, corpo)

    def usuario(self, username: str) -> dict:
        status, corpo = self.cliente.pedir("GET", "/api/roles")
        self.assertEqual(status, 200, corpo)
        return next(u for u in corpo["users"] if u["username"] == username)

    def test_supervisor_perde_o_modulo_desmarcado(self):
        self.salvar("eduardo", "supervisor", conferencia=True, admin=True, estacao=False, precificador=False)
        u = self.usuario("eduardo")
        self.assertEqual((u["can_estacao"], u["can_precificador"]), (0, 0),
                         "o módulo desmarcado do Supervisor voltou a ficar liberado")
        self.assertEqual(u["display_profile"], "Supervisor", "o Supervisor deixou de ser Supervisor")

    def test_supervisor_ganha_o_modulo_marcado(self):
        self.salvar("eduardo", "supervisor", conferencia=True, admin=True, estacao=False, precificador=False)
        self.salvar("eduardo", "supervisor", conferencia=True, admin=True, estacao=True, precificador=False)
        self.assertEqual(self.usuario("eduardo")["can_estacao"], 1)

    def test_supervisor_sempre_mantem_o_admin(self):
        """Supervisor É o operador com acesso ao Admin: sem ele, viraria Operador."""
        self.salvar("eduardo", "supervisor", conferencia=True, admin=False, estacao=True, precificador=True)
        u = self.usuario("eduardo")
        self.assertEqual(u["can_admin"], 1)
        self.assertEqual(u["display_profile"], "Supervisor")

    def test_supervisor_sem_lista_de_permissoes_continua_com_tudo(self):
        """Compatibilidade: quem manda só o perfil (sem permissões) recebe o padrão de antes."""
        status, corpo = self.cliente.pedir("POST", "/api/roles", {
            "username": "novo_sup", "display_name": "Novo", "role": "supervisor", "active": True, "password": SENHA})
        self.assertEqual(status, 200, corpo)
        u = self.usuario("novo_sup")
        self.assertEqual((u["can_conferencia"], u["can_admin"], u["can_estacao"], u["can_precificador"]), (1, 1, 1, 1))

    def test_admin_perde_o_modulo_e_continua_sem_depois_de_reiniciar(self):
        self.salvar("admin_x", "admin", conferencia=True, admin=True, estacao=False, precificador=True)
        self.assertEqual(self.usuario("admin_x")["can_estacao"], 0)
        S.ensure_app_user_permissions_schema()   # o que roda no boot do servidor
        self.assertEqual(self.usuario("admin_x")["can_estacao"], 0,
                         "o reinício do servidor religou o módulo desmarcado do Admin")

    def test_proprietario_continua_com_tudo(self):
        dono = S.ADMIN_USERNAME
        status, _ = self.cliente.pedir("POST", "/api/roles", {
            "username": dono, "display_name": "Dono", "role": "admin", "active": True,
            "permissions": {"conferencia": True, "admin": True, "estacao": False, "precificador": False}})
        u = self.usuario(dono)
        if S.is_owner_username(dono):
            self.assertEqual((u["can_estacao"], u["can_precificador"]), (1, 1),
                             "a conta do proprietário perdeu módulo")


if __name__ == "__main__":
    unittest.main()
