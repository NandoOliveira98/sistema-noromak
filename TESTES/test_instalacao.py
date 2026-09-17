"""Instalação nova e persistência básica.

O teste de instalação existe porque a V17.81 não subia em um banco vazio:
`ensure_app_user_permissions_schema()` deixava uma transação implícita aberta e o
`BEGIN IMMEDIATE` de `migrate_legacy_if_empty()` derrubava o `init_db()`. Em
produção nunca apareceu porque o banco já existia — só quebraria numa recuperação
de desastre, que é o pior momento possível.
"""

import json
import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha, pedidos, reiniciar_banco


class InstalacaoNova(unittest.TestCase):

    def test_init_db_funciona_em_banco_vazio(self):
        reiniciar_banco()  # apaga o arquivo e roda init_db() do zero
        tabelas = {
            row[0] for row in
            S.DB.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for obrigatoria in ("storage", "completed_orders", "admin_manual_overrides",
                            "admin_manual_orders_v1752", "app_users", "audit_log"):
            self.assertIn(obrigatoria, tabelas,
                          f"tabela {obrigatoria} não foi criada na instalação nova")

    def test_init_db_nao_deixa_transacao_aberta(self):
        reiniciar_banco()
        self.assertFalse(
            S.DB.in_transaction,
            "init_db() terminou com transação implícita aberta — "
            "a próxima gravação falharia com 'transaction within a transaction'")

    def test_init_db_e_idempotente(self):
        reiniciar_banco()
        S.init_db()
        S.init_db()
        self.assertFalse(S.DB.in_transaction)


class PersistenciaEntreLeituras(TesteNoromak):
    """O servidor é canônico: o que foi gravado precisa voltar igual."""

    def test_gravacao_do_admin_sobrevive_a_nova_leitura(self):
        self.salvar_admin([linha("PED-1", "SKU-A", quantidade=7)])
        atual = pedidos()
        self.assertEqual(len(atual), 1)
        self.assertEqual(atual[0].get("Quantidade"), 7)

    def test_versao_do_storage_avanca_a_cada_gravacao(self):
        _, v0, _ = S.get_storage("conf_pedidos")
        self.salvar_admin([linha("PED-1", "SKU-A")])
        _, v1, _ = S.get_storage("conf_pedidos")
        gravar_como_integracao([linha("PED-1", "SKU-A"), linha("PED-2", "SKU-B")])
        _, v2, _ = S.get_storage("conf_pedidos")
        self.assertGreater(v1, v0, "a versão não avançou após gravação do ADMIN")
        self.assertGreater(v2, v1, "a versão não avançou após a sincronização")

    def test_polling_de_versoes_responde_as_chaves_pedidas(self):
        status, corpo = self.cliente.pedir(
            "GET", "/api/storage?versions=conf_pedidos,conf_produtos")
        self.assertEqual(status, 200)
        self.assertIn("conf_pedidos", corpo.get("versions", {}))
        self.assertIn("conf_produtos", corpo.get("versions", {}))


class Autenticacao(TesteNoromak):

    def test_rota_protegida_recusa_sem_sessao(self):
        from base import Cliente
        anonimo = Cliente(self.url_base)
        status, _ = anonimo.pedir("POST", "/api/order-kit-delete", {"pedido": "PED-1"})
        self.assertIn(status, (401, 403),
                      "rota administrativa respondeu sem sessão autenticada")

    def test_senha_errada_nao_entra(self):
        from base import Cliente, USUARIO_ADMIN
        cliente = Cliente(self.url_base)
        status, _ = cliente.pedir("POST", "/api/auth/login", {
            "username": USUARIO_ADMIN, "password": "senha-errada", "area": "admin"})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
