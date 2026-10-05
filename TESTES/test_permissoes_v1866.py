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


if __name__ == "__main__":
    unittest.main()
