"""V18.52 — login único com o NoromakWeb (contrato de 23/09/2026).

O portão do NoromakWeb (nginx) manda dois cabeçalhos em toda chamada:
`X-NoromakWeb-Usuario` (quem está logado lá) e `X-NoromakWeb-Segredo` (senha
combinada, guardada no .env dos dois lados). Sem o segredo certo, o cabeçalho de
usuário não vale nada — qualquer processo da VPS alcança a porta 8100.
"""

import json
import unittest
import urllib.error
import urllib.request

from base import S, TesteNoromak

SEGREDO = "s" * 48


class LoginUnicoNoromakWeb(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._segredo_original = S.NOROMAKWEB_SSO_SECRET
        S.NOROMAKWEB_SSO_SECRET = SEGREDO
        with S.DB_LOCK:
            S.DB.execute(
                """INSERT INTO app_users(username,display_name,password_hash,password_salt,
                   password_iterations,role,active,can_conferencia,can_admin,updated_at)
                   VALUES('operador_x','Operador X','h','s',1,'operador',1,1,0,'2026-09-29')""")
            S.DB.execute(
                """INSERT INTO app_users(username,display_name,password_hash,password_salt,
                   password_iterations,role,active,updated_at)
                   VALUES('inativo_x','Inativo','h','s',1,'operador',0,'2026-09-29')""")
            S.DB.commit()

    def tearDown(self) -> None:
        S.NOROMAKWEB_SSO_SECRET = self._segredo_original
        super().tearDown()

    def pedir(self, metodo: str, rota: str, cabecalhos: dict, corpo: dict | None = None) -> tuple[int, dict]:
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        for nome, valor in cabecalhos.items():
            req.add_header(nome, valor)
        try:
            with urllib.request.urlopen(req, timeout=30) as resposta:
                return resposta.status, json.loads(resposta.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as erro:
            return erro.code, json.loads(erro.read().decode("utf-8") or "{}")

    def sessao(self, usuario: str, segredo: str = SEGREDO) -> dict:
        _, corpo = self.pedir("GET", "/api/me", {
            "X-NoromakWeb-Usuario": usuario, "X-NoromakWeb-Segredo": segredo,
        })
        return corpo

    def test_usuario_do_noromakweb_entra_sem_segundo_login(self):
        corpo = self.sessao("operador_x")
        self.assertTrue(corpo.get("authenticated"), corpo)
        self.assertEqual(corpo["user"]["username"], "operador_x")

    def test_nome_de_usuario_nao_diferencia_maiusculas(self):
        self.assertTrue(self.sessao("Operador_X").get("authenticated"))

    def test_segredo_errado_nao_entra(self):
        self.assertFalse(self.sessao("operador_x", "errado" * 8).get("authenticated"),
                         "FALHA DE SEGURANÇA: cabeçalho sem o segredo certo autenticou")

    def test_sem_segredo_nao_entra(self):
        _, corpo = self.pedir("GET", "/api/me", {"X-NoromakWeb-Usuario": "admin_teste"})
        self.assertFalse(corpo.get("authenticated"),
                         "FALHA DE SEGURANÇA: só o nome de usuário bastou para entrar")

    def test_desligado_quando_segredo_nao_configurado(self):
        S.NOROMAKWEB_SSO_SECRET = ""
        self.assertFalse(self.sessao("operador_x", "").get("authenticated"))

    def test_segredo_curto_demais_mantem_desligado(self):
        S.NOROMAKWEB_SSO_SECRET = "curto"
        self.assertFalse(self.sessao("operador_x", "curto").get("authenticated"))

    def test_usuario_que_nao_existe_na_expedicao_nao_entra(self):
        self.assertFalse(self.sessao("fulano_do_crm").get("authenticated"))

    def test_usuario_inativo_nao_entra(self):
        self.assertFalse(self.sessao("inativo_x").get("authenticated"))

    def test_permissoes_continuam_sendo_da_expedicao(self):
        status, _ = self.pedir("POST", "/api/order-sku", {
            "X-NoromakWeb-Usuario": "operador_x", "X-NoromakWeb-Segredo": SEGREDO,
        }, {"pedido": "X", "sku": "Y"})
        self.assertEqual(status, 403, "operador entrou pelo NoromakWeb e ganhou acesso de Admin")

    def test_login_antigo_continua_funcionando(self):
        status, corpo = self.cliente.pedir("GET", "/api/me")
        self.assertTrue(corpo.get("authenticated"), "o login próprio da Expedição parou")


if __name__ == "__main__":
    unittest.main()
