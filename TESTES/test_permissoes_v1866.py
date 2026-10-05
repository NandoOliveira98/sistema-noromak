"""V18.66 — permissões decididas pelo dono em 05/10/2026 (quadro de permissões).

Item 4: apagar uma base inteira (DELETE /api/storage) é só do Admin e com a
confirmação do proprietário (senha + 2FA), como já é para alterar usuários.
Antes, qualquer Supervisor apagava produtos, kits ou etiquetas de uma vez.
"""

import hashlib
import json
import time
import unittest

from base import S, Cliente, TesteNoromak

SENHA_TESTE = "Senha-de-teste-1866"


class PermissoesV1866(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        with S.DB_LOCK:
            # Supervisor = operador com acesso ao Painel Admin.
            S.upsert_app_user("supervisor_x", "Supervisor X", "operador", True, SENHA_TESTE, "teste",
                              permissions={"conferencia": True, "admin": True})
            # Admin que não é o proprietário do sistema.
            S.upsert_app_user("admin_x", "Admin X", "admin", True, SENHA_TESTE, "teste")
            S.DB.commit()
        S.save_storage("conf_produtos", None, json.dumps([{"SKU": "A1", "Código de Barras": "789"}]), True)

    def entrar_como(self, usuario: str) -> Cliente:
        cliente = Cliente(self.url_base)
        cliente.entrar(usuario, SENHA_TESTE, area="admin")
        return cliente

    def confirmar_proprietario(self, cliente: Cliente) -> None:
        """Equivale a o OWNER ter digitado senha + código na janela de confirmação."""
        token = cliente.cookie.split("=", 1)[1]
        S.CRITICAL_REAUTH[hashlib.sha256(token.encode("utf-8")).hexdigest()] = time.time() + 300

    def base_existe(self, chave: str) -> bool:
        valor, _, _ = S.get_storage(chave)
        return valor is not None and json.loads(valor) != []

    # ---- item 4 -----------------------------------------------------------

    def test_supervisor_nao_apaga_base_inteira(self):
        status, _ = self.entrar_como("supervisor_x").pedir("DELETE", "/api/storage?key=conf_produtos")
        self.assertEqual(status, 403)
        self.assertTrue(self.base_existe("conf_produtos"), "o Supervisor apagou a base de produtos")

    def test_admin_que_nao_e_proprietario_nao_apaga(self):
        cliente = self.entrar_como("admin_x")
        self.confirmar_proprietario(cliente)  # nem com a janela "confirmada" na sessão dele
        status, _ = cliente.pedir("DELETE", "/api/storage?key=conf_produtos")
        self.assertEqual(status, 403)
        self.assertTrue(self.base_existe("conf_produtos"))

    def test_proprietario_sem_confirmacao_recebe_pedido_de_senha(self):
        status, corpo = self.cliente.pedir("DELETE", "/api/storage?key=conf_produtos")
        self.assertEqual(status, 428)
        self.assertEqual(corpo.get("code"), "OWNER_REAUTH_REQUIRED")
        self.assertTrue(self.base_existe("conf_produtos"))

    def test_proprietario_confirmado_apaga_e_fica_na_auditoria(self):
        self.confirmar_proprietario(self.cliente)
        status, corpo = self.cliente.pedir("DELETE", "/api/storage?key=conf_produtos")
        self.assertEqual(status, 200, corpo)
        self.assertFalse(self.base_existe("conf_produtos"))
        with S.DB_LOCK:
            linha = S.DB.execute(
                "SELECT 1 FROM audit_log WHERE action='storage.delete' AND detail LIKE '%conf_produtos%'"
            ).fetchone()
        self.assertIsNotNone(linha, "a exclusão não ficou registrada na auditoria")

    def test_tentativa_negada_fica_na_auditoria(self):
        self.entrar_como("supervisor_x").pedir("DELETE", "/api/storage?key=conf_produtos")
        with S.DB_LOCK:
            linha = S.DB.execute(
                "SELECT 1 FROM audit_log WHERE action LIKE 'security.denied%' AND detail LIKE '%conf_produtos%'"
            ).fetchone()
        self.assertIsNotNone(linha, "a tentativa negada não ficou registrada")


PEDIDO_KIT = "2609263A9T6TVY"


class ExcluirLinhaPelaConferenciaV1866(TesteNoromak):
    """Item 1: excluir produto de um pedido é do Admin e do Supervisor.

    O operador só remove a linha ARTIFICIAL de um kit da Shopee cujos componentes já
    estão no pedido — é o que a limpeza automática da Conferência grava sozinha.
    Antes, o operador apagava qualquer linha e ela virava bloqueio permanente; num
    caso real (26092752FDYNQD) apagou primeiro o produto de verdade.
    """

    def setUp(self) -> None:
        super().setUp()
        from base import gravar_como_integracao, linha
        with S.DB_LOCK:
            S.upsert_app_user("operador_x", "Operador X", "operador", True, SENHA_TESTE, "teste",
                              permissions={"conferencia": True})
            S.upsert_app_user("supervisor_x", "Supervisor X", "operador", True, SENHA_TESTE, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        gravar_como_integracao([
            linha(PEDIDO_KIT, "0893600022", uid="KITDB_C0", Kit="SIM",
                  SkuOrigemImportacao="0893600022/0893764272"),
            linha(PEDIDO_KIT, "0893764272", uid="KITDB_C1", Kit="SIM",
                  SkuOrigemImportacao="0893600022/0893764272"),
            linha(PEDIDO_KIT, "0893600022/0893764272", uid="SHOPEEAPI_ART",
                  SkuOrigemImportacao="0893600022/0893764272"),
            linha("2609280000PROD", "PROD-REAL", uid="LINHA_REAL"),
        ])

    def entrar_como(self, usuario: str, area: str) -> Cliente:
        cliente = Cliente(self.url_base)
        cliente.entrar(usuario, SENHA_TESTE, area=area)
        return cliente

    def gravar_sem(self, cliente: Cliente, uid: str, *, permitir_remocoes: bool = True) -> tuple[int, dict]:
        from base import pedidos
        atual, _, _ = S.get_storage("conf_pedidos")
        novas = [r for r in pedidos() if r.get("__uidLinha") != uid]
        return cliente.pedir("POST", "/api/storage", {
            "key": "conf_pedidos", "oldValue": atual, "newValue": json.dumps(novas),
            "allowPendingDeletes": permitir_remocoes,
        })

    def uids(self) -> set:
        from base import pedidos
        return {r.get("__uidLinha") for r in pedidos()}

    def test_operador_nao_remove_produto_de_verdade(self):
        status, corpo = self.gravar_sem(self.entrar_como("operador_x", "conferencia"), "LINHA_REAL")
        self.assertEqual(status, 403, corpo)
        self.assertIn("supervisor", str(corpo.get("error", "")).lower())
        self.assertIn("LINHA_REAL", self.uids())

    def test_operador_nao_remove_componente_do_kit(self):
        status, _ = self.gravar_sem(self.entrar_como("operador_x", "conferencia"), "KITDB_C0")
        self.assertEqual(status, 403)
        self.assertIn("KITDB_C0", self.uids())

    def test_operador_remove_linha_artificial_do_kit(self):
        status, corpo = self.gravar_sem(self.entrar_como("operador_x", "conferencia"), "SHOPEEAPI_ART")
        self.assertEqual(status, 200, corpo)
        self.assertNotIn("SHOPEEAPI_ART", self.uids())
        self.assertTrue({"KITDB_C0", "KITDB_C1"} <= self.uids())

    def test_operador_continua_gravando_bipagem(self):
        from base import pedidos
        cliente = self.entrar_como("operador_x", "conferencia")
        atual, _, _ = S.get_storage("conf_pedidos")
        linhas = pedidos()
        for r in linhas:
            if r.get("__uidLinha") == "LINHA_REAL":
                r["Lido"] = 1
        status, corpo = cliente.pedir("POST", "/api/storage", {
            "key": "conf_pedidos", "oldValue": atual, "newValue": json.dumps(linhas),
        })
        self.assertEqual(status, 200, corpo)

    def test_operador_nao_grava_lista_de_exclusoes_shopee(self):
        status, _ = self.entrar_como("operador_x", "conferencia").pedir("POST", "/api/storage", {
            "key": "conf_shopee_exclusoes_v1", "oldValue": None,
            "newValue": json.dumps([{"Pedido": "2609280000PROD", "SKU": "PROD-REAL", "Origem": "MANUAL"}]),
        })
        self.assertEqual(status, 403)

    def test_limpeza_do_operador_nao_apaga_pedido_que_chegou_depois(self):
        from base import gravar_como_integracao, linha, pedidos
        cliente = self.entrar_como("operador_x", "conferencia")
        lido, _, _ = S.get_storage("conf_pedidos")           # a tela leu isto
        sem_kit = [r for r in json.loads(lido) if r.get("__uidLinha") != "SHOPEEAPI_ART"]
        gravar_como_integracao(pedidos() + [linha("2609300000NOVO", "NOVO-1", uid="LINHA_NOVA")])
        status, corpo = cliente.pedir("POST", "/api/storage", {
            "key": "conf_pedidos", "oldValue": lido, "newValue": json.dumps(sem_kit),
            "allowPendingDeletes": True,
        })
        self.assertEqual(status, 200, corpo)
        self.assertIn("LINHA_NOVA", self.uids(), "a gravação da tela antiga apagou o pedido novo")
        self.assertNotIn("SHOPEEAPI_ART", self.uids())

    def test_supervisor_remove_produto(self):
        status, corpo = self.gravar_sem(self.entrar_como("supervisor_x", "admin"), "LINHA_REAL")
        self.assertEqual(status, 200, corpo)
        self.assertNotIn("LINHA_REAL", self.uids())


if __name__ == "__main__":
    unittest.main()
