"""V19.70 — perfil Compras. Decisões do dono, 21/10:

- o pessoal de compras vê SÓ o Quadro de tarefas, a Ruptura de estoque e a Demanda de Compra;
- no Quadro executa os próprios cartões (não cria); na Demanda de Compra pode digitar
  (custo, a caminho, fornecedores); a Ruptura é só ver;
- o Quadro de tarefas fica em destaque: 1º item do menu.
"""

import re
import urllib.error
import urllib.request

from base import RAIZ, SENHA_ADMIN, Cliente, S, TesteNoromak


class PerfilCompras(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        status, r = self.cliente.pedir("POST", "/api/roles", {
            "username": "carla", "display_name": "Carla Compras", "role": "compras",
            "active": True, "password": SENHA_ADMIN})
        self.assertEqual(status, 200, r)
        self.compras = Cliente(self.url_base)
        self.usuario = self.compras.entrar("carla", SENHA_ADMIN, area="portal")

    def pagina(self, rota: str) -> int:
        req = urllib.request.Request(self.url_base + rota, headers={"Cookie": self.compras.cookie})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_sessao_do_perfil(self):
        self.assertEqual(self.usuario["app_role"], "compras")
        p = self.usuario["permissions"]
        self.assertTrue(p.get("compras"))
        self.assertFalse(p["admin"] or p["conferencia"] or p["estacao"] or p["precificador"])
        self.assertFalse(self.usuario["somente_leitura"], "Compras não é o perfil Consulta")
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='carla'").fetchone()
        self.assertFalse(S.is_supervisor_user(row))
        self.assertEqual(row["role"], "operador", "o CHECK da tabela só aceita admin/operador")

    def test_abre_so_as_telas_de_compras(self):
        for rota in ("/tarefas", "/ruptura", "/compras"):
            self.assertEqual(self.pagina(rota), 200, rota)
        for rota in ("/admin", "/financeiro", "/precificador", "/conferencia.html", "/promocoes-auto", "/analise-full"):
            self.assertIn(self.pagina(rota), (302, 403), rota)

    def test_le_ruptura_e_demanda(self):
        for rota in ("/api/ruptura", "/api/ruptura/relatorio", "/api/compras/fornecedores", "/api/compras/demanda"):
            status, r = self.compras.pedir("GET", rota)
            self.assertNotIn(status, (401, 403), (rota, r))

    def test_grava_na_demanda_de_compra(self):
        status, r = self.compras.pedir("POST", "/api/compras/custo", {"codigo": "0893600022", "custo": 12.5})
        self.assertNotIn(status, (401, 403), r)

    def test_nao_ve_o_resto_do_sistema(self):
        for rota in ("/api/financeiro/resumo", "/api/storage?key=conf_pedidos", "/api/posvenda",
                     "/api/compras/linx/amostra", "/api/roles", "/api/saude"):
            status, r = self.compras.pedir("GET", rota)
            self.assertIn(status, (401, 403), (rota, r))
        status, r = self.compras.pedir("POST", "/api/order-complete", {"pedido": "P1"})
        self.assertIn(status, (401, 403), r)

    def test_no_quadro_executa_mas_nao_cria(self):
        status, r = self.compras.pedir("POST", "/api/tarefas/cartao", {"titulo": "x"})
        self.assertEqual(status, 403, r)
        status, r = self.cliente.pedir("POST", "/api/tarefas/cartao", {"titulo": "Cotar Water Off", "responsavel": "carla"})
        self.assertEqual(status, 200, r)
        status, r = self.compras.pedir("POST", "/api/tarefas/mover", {"id": r["cartao"]["id"], "coluna": "andamento"})
        self.assertEqual(status, 200, r)

    def test_perfil_sobrevive_a_lista_de_usuarios_e_a_troca_de_senha(self):
        status, r = self.cliente.pedir("GET", "/api/roles")
        carla = next(u for u in r["users"] if u["username"] == "carla")
        self.assertEqual((carla["role"], carla["display_profile"]), ("compras", "Compras"))
        # O Admin salva a lista (ou troca a senha) mandando o perfil que a tela mostra.
        status, r = self.cliente.pedir("POST", "/api/roles", {"username": "carla", "display_name": "Carla Compras",
                                                              "role": "compras", "active": True, "password": "Outra-Senha-123"})
        self.assertEqual(status, 200, r)
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='carla'").fetchone()
        self.assertEqual(S.role_efetivo(row), "compras")
        self.assertEqual(S.user_permissions_from_row(row).get("compras"), True)

    def test_virar_operador_tira_o_perfil(self):
        self.cliente.pedir("POST", "/api/roles", {"username": "carla", "display_name": "Carla", "role": "operador", "active": True})
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='carla'").fetchone()
        self.assertEqual(S.role_efetivo(row), "operador")
        self.assertFalse(S.user_permissions_from_row(row).get("compras"))

    def test_consulta_continua_consulta(self):
        self.cliente.pedir("POST", "/api/roles", {"username": "vera", "display_name": "Vera", "role": "consulta",
                                                  "active": True, "password": SENHA_ADMIN})
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='vera'").fetchone()
        self.assertEqual(S.role_efetivo(row), "consulta")

    def test_menu_quadro_em_destaque_e_telas_de_compras(self):
        js = (RAIZ / "APP" / "nk-menu.js").read_text(encoding="utf-8")
        primeiro = re.search(r'\{ id: "([a-z-]+)"', js[js.index("const MENU"):]).group(1)
        self.assertEqual(primeiro, "tarefas", "o Quadro de tarefas tem que ser o 1º item do menu")
        for item in ("ruptura", "compras"):
            linha = next(l for l in js.splitlines() if f'id: "{item}"' in l)
            self.assertIn('alt: "compras"', linha, f"{item} sem acesso do perfil Compras no menu")

    def test_tela_de_usuarios_oferece_o_perfil(self):
        admin = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('<option value="Compras">', admin)
        self.assertNotIn('return p.includes("admin") ? "Admin" : (p.includes("supervisor") ? "Supervisor" : "Operador"); }', admin,
                         "a troca de senha rebaixava Consulta/Compras para Operador")


if __name__ == "__main__":
    import unittest
    unittest.main()
