"""V19.00 — achados 3 e 7 da verificação de 11/10.

3. O GET /api/storage devolvia QUALQUER chave a qualquer usuário logado. Um operador da
   Conferência lia o token do agente Zebra (print_agent_token_v1: com ele, lia o ZPL das
   etiquetas com endereço de cliente e sumia com trabalhos da fila), o token do robô Linx
   (fila de CPFs), os custos do Precificador e as senhas em texto de quem pediu acesso
   (conf_solicitacoes_usuarios). O ?list=1 listava todas as chaves.

7. Usuário excluído voltava a cada reinício: a migração de usuários rodava em TODO boot e
   recriava quem faltava a partir de MIGRACAO_INICIAL/BANCO/CONF_USUARIOS_*.json (que está no
   Git, com "felipe"/"matheus" e senha "123") e da tabela user_roles, que a exclusão não
   limpava.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from base import (SENHA_ADMIN, USUARIO_ADMIN, Cliente, S, TesteNoromak, gravar_como_integracao, linha,
                  reiniciar_banco)


class LeituraDoStorage(TesteNoromak):
    """Achado 3: cada perfil lê só o que a tela dele usa; segredo não sai por aqui."""

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha("P1", "SKU-1")])
        S.get_or_create_print_agent_token()
        S.save_storage("conf_solicitacoes_usuarios", None,
                       json.dumps([{"id": "SOL-1", "Login": "novo", "Senha": "segredo-do-novo"}]), True,
                       actor="teste", actor_role="sistema")
        S.save_storage("precificador_online_v1", None, json.dumps({"products": [{"sku": "X", "custo": 10}]}), True,
                       actor="teste", actor_role="sistema")

    def usuario(self, login: str, **modulos: bool) -> Cliente:
        perms = {"conferencia": True, "admin": False, "estacao": False, "precificador": False, **modulos}
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), "operador", True, SENHA_ADMIN, "teste", permissions=perms)
            S.DB.commit()
        cliente = Cliente(self.url_base)
        cliente.entrar(login, SENHA_ADMIN, area="acesso")  # portal: não exige módulo
        return cliente

    def ler(self, cliente: Cliente, chave: str) -> int:
        status, _ = cliente.pedir("GET", f"/api/storage?key={chave}")
        return status

    def test_operador_nao_le_o_token_do_agente_zebra_nem_do_robo(self):
        operador = self.usuario("ana")
        for chave in (S.PRINT_AGENT_TOKEN_STORAGE_KEY, S.LINX_ROBO_TOKEN_STORAGE_KEY):
            self.assertEqual(self.ler(operador, chave), 403, f"REGRESSÃO: operador leu {chave}")
        status, corpo = operador.pedir("GET", f"/api/storage?keys=conf_pedidos,{S.PRINT_AGENT_TOKEN_STORAGE_KEY}")
        self.assertEqual(status, 403, "a leitura de várias chaves de uma vez também tem de barrar o token")
        self.assertNotIn(S.get_or_create_print_agent_token(), json.dumps(corpo))

    def test_nem_o_admin_le_segredo_pela_rota_comum(self):
        self.assertEqual(self.ler(self.cliente, S.PRINT_AGENT_TOKEN_STORAGE_KEY), 403)
        self.assertEqual(self.ler(self.cliente, S.LINX_ROBO_TOKEN_STORAGE_KEY), 403)

    def test_ninguem_grava_segredo_pela_rota_comum(self):
        antes = S.get_or_create_print_agent_token()
        status, _ = self.cliente.pedir("POST", "/api/storage", {
            "key": S.PRINT_AGENT_TOKEN_STORAGE_KEY, "newValue": "token-escolhido", "force": True})
        self.assertEqual(status, 403, "trocar o token do agente é com a rota própria (OWNER)")
        self.assertEqual(S.get_or_create_print_agent_token(), antes)

    def test_operador_nao_le_senha_de_quem_pediu_acesso(self):
        operador = self.usuario("ana")
        self.assertEqual(self.ler(operador, "conf_solicitacoes_usuarios"), 403)

    def test_operador_so_da_conferencia_nao_le_custos(self):
        operador = self.usuario("ana")
        self.assertEqual(self.ler(operador, "precificador_online_v1"), 403)
        precificador = self.usuario("bia", conferencia=False, precificador=True)
        self.assertEqual(self.ler(precificador, "precificador_online_v1"), 200)
        self.assertEqual(self.ler(precificador, "conf_pedidos"), 403,
                         "quem só tem o Precificador não precisa da fila de pedidos")

    def test_operador_le_tudo_o_que_a_conferencia_usa(self):
        operador = self.usuario("ana")
        for chave in ("conf_pedidos", "conf_pedidos_rascunhos", "conf_produtos", "conf_kits", "conf_etiquetas",
                      "conf_fotos", "conf_historico_conferencias_v2", "conf_logs", "conf_banco_atualizado_em",
                      "conf_usuarios", "conf_coleta_dia_v1", "conf_nomes_conferencia_v1",
                      "conf_shopee_exclusoes_v1"):
            self.assertEqual(self.ler(operador, chave), 200, f"a Conferência quebra sem {chave}")
        status, corpo = operador.pedir("GET", "/api/storage?versions=conf_pedidos,conf_produtos,conf_kits")
        self.assertEqual(status, 200, "o polling de versões não pode quebrar")

    def test_estacao_le_o_que_a_tela_usa(self):
        estacao = self.usuario("caio", conferencia=False, estacao=True)
        for chave in ("conf_pedidos", "conf_etiquetas", "conf_banco_atualizado_em"):
            self.assertEqual(self.ler(estacao, chave), 200, f"a Estação quebra sem {chave}")

    def test_supervisor_e_admin_leem_as_chaves_de_gestao(self):
        supervisor = self.usuario("dani", admin=True)
        for cliente in (supervisor, self.cliente):
            self.assertEqual(self.ler(cliente, "precificador_online_v1"), 200)
            self.assertEqual(self.ler(cliente, "conf_solicitacoes_usuarios"), 200)

    def test_lista_de_chaves_so_para_o_perfil_admin(self):
        operador = self.usuario("ana")
        status, _ = operador.pedir("GET", "/api/storage?list=1")
        self.assertEqual(status, 403)
        status, corpo = self.cliente.pedir("GET", "/api/storage?list=1")
        self.assertEqual(status, 200)
        self.assertNotIn(S.PRINT_AGENT_TOKEN_STORAGE_KEY, corpo.get("keys", []))


class UsuarioExcluidoNaoVolta(TesteNoromak):
    """Achado 7: a migração de usuários é só para a instalação nova."""

    def setUp(self) -> None:
        self.pasta = Path(tempfile.mkdtemp(prefix="migracao_v1900_"))
        (self.pasta / "BANCO").mkdir()
        usuarios = [{"Nome": "Felipe", "Login": "felipe", "Senha": "123", "Perfil": "Operador"},
                    {"Nome": "Matheus", "Login": "matheus", "Senha": "123", "Perfil": "Operador"}]
        (self.pasta / "BANCO" / "CONF_USUARIOS_teste.json").write_text(
            json.dumps({"value": json.dumps(usuarios)}), encoding="utf-8")
        self.patch = mock.patch.object(S, "MIGRATION_DIR", self.pasta)
        self.patch.start()
        super().setUp()  # instalação nova COM o arquivo de migração

    def tearDown(self) -> None:
        self.patch.stop()
        reiniciar_banco()
        shutil.rmtree(self.pasta, ignore_errors=True)

    def usuarios(self) -> set[str]:
        with S.DB_LOCK:
            return {r[0] for r in S.DB.execute("SELECT username FROM app_users").fetchall()}

    def test_instalacao_nova_traz_os_usuarios_do_arquivo(self):
        self.assertTrue({"felipe", "matheus", USUARIO_ADMIN} <= self.usuarios())

    def test_senha_fraca_do_arquivo_nao_vale(self):
        status, _ = Cliente(self.url_base).pedir("POST", "/api/auth/login",
                                                 {"username": "felipe", "password": "123", "area": "conferencia"})
        self.assertNotEqual(status, 200, "REGRESSÃO: felipe entrou com a senha '123' do arquivo de migração")

    def test_usuario_excluido_nao_volta_no_reinicio(self):
        status, corpo = self.cliente.pedir("DELETE", "/api/roles?username=felipe")
        self.assertEqual(status, 200, corpo)
        self.assertNotIn("felipe", self.usuarios())
        S.init_db()  # o que o boot faz a cada publicação/reinício
        self.assertNotIn("felipe", self.usuarios(), "REGRESSÃO: o usuário excluído voltou depois do reinício")
        with S.DB_LOCK:
            sobra = S.DB.execute("SELECT COUNT(*) FROM user_roles WHERE username='felipe'").fetchone()[0]
        self.assertEqual(sobra, 0, "a exclusão tem de limpar o perfil antigo (user_roles)")

    def test_excluido_pela_lista_de_usuarios_tambem_nao_volta(self):
        lista = [u for u in S.app_users_payload() if u["Login"] != "matheus"]
        with S.DB_LOCK:
            S.apply_app_users_payload(json.dumps(lista), USUARIO_ADMIN)
        S.init_db()
        self.assertNotIn("matheus", self.usuarios(), "REGRESSÃO: o usuário excluído voltou depois do reinício")

    def test_reinicio_nao_mexe_em_quem_existe(self):
        antes = self.usuarios()
        S.init_db()
        self.assertEqual(self.usuarios(), antes)
