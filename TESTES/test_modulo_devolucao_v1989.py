"""V19.89 — módulo Devolução (decisões do dono, 27/10).

A tela "Receber devolução" deixa de abrir para todo operador com Conferência: passa a ter módulo
próprio, ligado à mão na tela de Usuários (hoje Felipe e Matheus). O que não pode quebrar:
- só o Receber depende do módulo; a tela do admin (tratar no Apollo) continua no módulo Admin;
- ao subir, o perfil Admin já nasce com o módulo e os demais começam sem;
- salvar usuário por um caminho que não manda o módulo novo (tela antiga, conf_usuarios, extensão)
  NÃO desliga a Devolução de ninguém;
- Consulta segue a regra dele (vê tudo, não grava); Compras não tem.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from base import S, SENHA_ADMIN, Cliente, TesteNoromak, linha


def concluir(pedido: str, linhas: list[dict]) -> None:
    with S.DB_LOCK:
        for tabela in ("completed_orders", "order_completion_history"):
            S.DB.execute(f"INSERT OR REPLACE INTO {tabela}(order_key,order_id,completed_at,completed_by,payload) "
                         "VALUES(?,?,?,?,?)", (S.normalize(pedido), pedido, S.now_iso(), "admin", json.dumps(linhas)))
        S.DB.commit()


class ModuloDevolucao(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._DEVOLUCAO_EAN_CACHE.clear()
        concluir("P100", [linha("P100", "AAA", 2, Cliente="José da Silva")])

    def usuario(self, login: str, role: str = "operador", permissions: dict | None = None) -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), role, True, SENHA_ADMIN, "teste", permissions=permissions)
            S.DB.commit()
        c = Cliente(self.url_base)
        c.entrar(login, SENHA_ADMIN, area="portal")
        return c

    def modulo(self, login: str) -> bool:
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username=?", (login,)).fetchone()
        return S.user_permissions_from_row(row)["devolucao"]

    def pagina(self, c: Cliente, rota: str) -> int:
        req = urllib.request.Request(c.url_base + rota, headers={"Cookie": c.cookie})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    REGISTRO = {"chave": "P100", "itens": [{"sku": "AAA", "qtd": 1, "estado": "bom"}]}

    def test_operador_sem_o_modulo_nao_recebe_devolucao(self):
        c = self.usuario("felipe")                     # Conferência ligada, Devolução não
        self.assertFalse(self.modulo("felipe"))
        self.assertEqual(c.pedir("GET", "/api/devolucoes/buscar?q=AAA")[0], 403)
        self.assertEqual(c.pedir("GET", "/api/devolucoes/pedido?chave=P100")[0], 403)
        self.assertEqual(c.pedir("POST", "/api/devolucoes/registrar", self.REGISTRO)[0], 403)
        self.assertNotEqual(self.pagina(c, "/devolucoes"), 200)

    def test_operador_com_o_modulo_recebe_e_nao_ve_a_tela_do_admin(self):
        c = self.usuario("matheus", permissions={"conferencia": True, "devolucao": True})
        st, corpo = c.pedir("GET", "/api/devolucoes/buscar?q=AAA")
        self.assertEqual((st, [p["pedido"] for p in corpo["pedidos"]]), (200, ["P100"]))
        st, corpo = c.pedir("POST", "/api/devolucoes/registrar", self.REGISTRO)
        self.assertEqual(st, 200, corpo)
        self.assertEqual(self.pagina(c, "/devolucoes"), 200)
        self.assertEqual(c.pedir("GET", "/api/devolucoes")[0], 403)
        self.assertNotEqual(self.pagina(c, "/devolucoes-admin"), 200)

    def test_so_o_modulo_devolucao_libera_mesmo_sem_conferencia(self):
        c = self.usuario("so_devol", permissions={"conferencia": False, "devolucao": True})
        self.assertEqual(c.pedir("POST", "/api/devolucoes/registrar", self.REGISTRO)[0], 200)

    def test_supervisor_sem_o_modulo_trata_mas_nao_recebe(self):
        c = self.usuario("sup", permissions={"conferencia": True, "admin": True})
        self.assertEqual(c.pedir("GET", "/api/devolucoes")[0], 200)
        self.assertEqual(c.pedir("POST", "/api/devolucoes/registrar", self.REGISTRO)[0], 403)

    def test_admin_nasce_com_o_modulo(self):
        self.assertTrue(self.modulo(S.ADMIN_USERNAME))
        self.usuario("novo_admin", role="admin")
        self.assertTrue(self.modulo("novo_admin"))
        self.usuario("novo_op")
        self.assertFalse(self.modulo("novo_op"))

    def test_migracao_admin_ligado_operador_desligado(self):
        self.usuario("felipe", permissions={"conferencia": True})
        with S.DB_LOCK:
            S.DB.execute("ALTER TABLE app_users DROP COLUMN can_devolucao")
            S.DB.commit()
        S.ensure_app_user_permissions_schema()
        self.assertTrue(self.modulo(S.ADMIN_USERNAME))
        self.assertFalse(self.modulo("felipe"))
        # Depois de criada, a migração não roda de novo: o Admin desligado continua desligado.
        with S.DB_LOCK:
            S.DB.execute("UPDATE app_users SET can_devolucao=0 WHERE username=?", (S.ADMIN_USERNAME,))
            S.DB.commit()
        S.ensure_app_user_permissions_schema()
        self.assertFalse(self.modulo(S.ADMIN_USERNAME))

    def test_api_roles_liga_desliga_e_nao_apaga_quando_nao_vem(self):
        self.usuario("felipe")
        base = {"username": "felipe", "display_name": "Felipe", "role": "operador", "active": True}
        st, corpo = self.cliente.pedir("POST", "/api/roles", {**base, "permissions": {
            "conferencia": True, "admin": False, "estacao": True, "precificador": False, "devolucao": True}})
        self.assertEqual(st, 200, corpo)
        self.assertTrue(self.modulo("felipe"))
        # Tela antiga / outro caminho sem o campo novo: mantém.
        st, corpo = self.cliente.pedir("POST", "/api/roles", {**base, "permissions": {
            "conferencia": True, "admin": False, "estacao": False, "precificador": False}})
        self.assertEqual(st, 200, corpo)
        self.assertTrue(self.modulo("felipe"))
        st, corpo = self.cliente.pedir("POST", "/api/roles", {**base, "permissions": {
            "conferencia": True, "devolucao": False}})
        self.assertEqual(st, 200, corpo)
        self.assertFalse(self.modulo("felipe"))
        # GET /api/roles mostra a caixa.
        st, corpo = self.cliente.pedir("GET", "/api/roles")
        felipe = next(u for u in corpo["users"] if u["username"] == "felipe")
        self.assertIn("can_devolucao", felipe)

    def test_conf_usuarios_sem_o_campo_nao_desliga(self):
        self.usuario("matheus", permissions={"conferencia": True, "devolucao": True})
        lista = json.loads(S.json_dumps(S.app_users_payload()))
        for u in lista:   # payload como a tela antiga monta: só os 4 módulos de antes
            u.pop("AcessoDevolucao", None)
            u.get("Acessos", {}).pop("devolucao", None)
        with S.DB_LOCK:
            S.apply_app_users_payload(json.dumps(lista), S.ADMIN_USERNAME)
        self.assertTrue(self.modulo("matheus"))
        matheus = next(u for u in S.app_users_payload() if u["Login"] == "matheus")
        self.assertTrue(matheus["AcessoDevolucao"])
        self.assertTrue(matheus["Acessos"]["devolucao"])

    def test_sessao_traz_o_modulo(self):
        c = self.usuario("matheus", permissions={"conferencia": True, "devolucao": True})
        st, corpo = c.pedir("GET", "/api/auth/session")
        self.assertEqual(st, 200, corpo)
        self.assertTrue(corpo["user"]["permissions"]["devolucao"])

    def test_perfis_especiais(self):
        self.assertTrue(S.CONSULTA_PERMISSOES["devolucao"])
        self.assertFalse(S.COMPRAS_PERMISSOES["devolucao"])
        c = self.usuario("marcus", role="consulta")
        self.assertEqual(c.pedir("GET", "/api/devolucoes/buscar?q=AAA")[0], 200)
        self.assertEqual(c.pedir("POST", "/api/devolucoes/registrar", self.REGISTRO)[0], 403)

    def test_telas(self):
        perm = (S.APP_DIR / "user-permissions.js").read_text(encoding="utf-8")
        self.assertIn('"AcessoDevolucao"', perm)
        admin = (S.APP_DIR / "admin.html").read_text(encoding="utf-8")
        self.assertIn("AcessoDevolucao:!!u.can_devolucao", admin)
        self.assertIn("devolucao:u.AcessoDevolucao===true", admin)
        menu = (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('href: "/devolucoes", modulo: "devolucao"', menu)
        auth = (S.APP_DIR / "web-auth.js").read_text(encoding="utf-8")
        self.assertIn('permission(user,"devolucao")', auth)


if __name__ == "__main__":
    import unittest
    unittest.main()
