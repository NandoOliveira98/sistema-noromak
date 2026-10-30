"""V20.07 — gravar a fila sem mudar nada não sobe a versão.

Medido em produção em 30/10/2026, já com a V20.06: a fila subia ~10 versões por minuto e 6 de 8
sem mudança nenhuma no conteúdo. Cada versão nova faz toda tela aberta baixar a fila inteira e os
vigias do ML refazerem a leitura.
"""

import json
import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha


def versao() -> int:
    return S.get_storage("conf_pedidos")[1]


def bruto() -> str:
    with S.DB_LOCK:
        return S.DB.execute("SELECT value FROM storage WHERE key='conf_pedidos'").fetchone()[0]


class SemMudanca(TesteNoromak):
    def test_mesma_fila_nao_sobe_a_versao(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        v = versao()
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        self.assertEqual(versao(), v)

    def test_mudanca_real_sobe_a_versao(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        v = versao()
        gravar_como_integracao([linha("P1", "SKU1", uid="U1", NF="123")])
        self.assertEqual(versao(), v + 1)
        self.assertEqual(S._rows_from_storage_value(S.get_storage("conf_pedidos")[0])[0]["NF"], "123")

    def test_linha_nova_sobe_a_versao(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        v = versao()
        gravar_como_integracao([linha("P1", "SKU1", uid="U1"), linha("P2", "SKU2", uid="U2")])
        self.assertEqual(versao(), v + 1)

    def test_resposta_devolve_a_versao_do_banco(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        atual, v, em = S.get_storage("conf_pedidos")
        _, devolvida, quando = S.save_storage("conf_pedidos", atual, atual, False, actor="x", actor_role="sistema")
        self.assertEqual((devolvida, quando), (v, em))

    def test_ordem_das_chaves_nao_conta(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        atual, v, _ = S.get_storage("conf_pedidos")
        linhas = json.loads(atual)
        S.save_storage("conf_pedidos", atual, json.dumps([dict(reversed(list(x.items()))) for x in linhas]), False,
                       actor="x", actor_role="sistema")
        self.assertEqual(versao(), v)

    def test_transacao_fica_fechada(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        atual, _, _ = S.get_storage("conf_pedidos")
        S.save_storage("conf_pedidos", atual, atual, False, actor="x", actor_role="sistema")
        self.assertFalse(S.DB.in_transaction)
        gravar_como_integracao([linha("P1", "SKU1", uid="U1", NF="9")])  # BEGIN IMMEDIATE seguinte

    def test_force_continua_gravando(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        atual, v, _ = S.get_storage("conf_pedidos")
        S.save_storage("conf_pedidos", atual, atual, True, actor="x", actor_role="sistema")
        self.assertEqual(versao(), v + 1)

    def test_outras_chaves_nao_mudam_de_regra(self):
        S.save_storage("conf_logs", None, "[]", True, actor="x", actor_role="sistema")
        v = S.get_storage("conf_logs")[1]
        S.save_storage("conf_logs", "[]", "[]", False, actor="x", actor_role="sistema")
        self.assertEqual(S.get_storage("conf_logs")[1], v + 1)

    def test_override_do_admin_fica_gravado_mesmo_sem_mudanca_na_fila(self):
        """Decisão do ADMIN entra na mesma transação: não pode se perder com o pulo da gravação."""
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        atual, _, _ = S.get_storage("conf_pedidos")
        linhas = json.loads(atual)
        linhas[0]["SKU"] = "SKU9"
        S.save_storage("conf_pedidos", atual, json.dumps(linhas), False, actor="adm", actor_role="admin")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM admin_manual_overrides WHERE storage_key='conf_pedidos'").fetchone()[0]
        self.assertGreater(n, 0)
        self.assertEqual(S._rows_from_storage_value(S.get_storage("conf_pedidos")[0])[0]["SKU"], "SKU9")

    def test_override_de_tela_antiga_grava_mesmo_com_a_fila_igual(self):
        """Tela com NF antiga, banco já com a nova: o ADMIN grava a nova. O 1º save marca a linha
        (__adminOverride); o 2º, igual, passa pelo ramo sem gravação e o delta continua lá."""
        gravar_como_integracao([linha("P1", "SKU1", uid="U1", NF="1")])
        antes, _, _ = S.get_storage("conf_pedidos")
        gravar_como_integracao([linha("P1", "SKU1", uid="U1", NF="2")])
        atual, v, _ = S.get_storage("conf_pedidos")
        S.save_storage("conf_pedidos", antes, atual, False, actor="adm", actor_role="admin")
        marcado, v, _ = S.get_storage("conf_pedidos")
        S.save_storage("conf_pedidos", antes, marcado, False, actor="adm", actor_role="admin")
        self.assertEqual(versao(), v)
        self.assertFalse(S.DB.in_transaction)
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM admin_manual_overrides WHERE storage_key='conf_pedidos' "
                             "AND field_name='NF'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_troca_de_sku_no_lapis_entra_no_historico_com_o_usuario(self):
        gravar_como_integracao([linha("P1", "SKU1", uid="U1")])
        status, r = self.cliente.pedir("POST", "/api/order-sku", {"uid": "U1", "sku": "SKU2", "pedido": "P1", "oldSku": "SKU1"})
        self.assertEqual(status, 200, r)
        with S.DB_LOCK:
            h = S.DB.execute("SELECT old_sku,new_sku,username FROM sku_change_history WHERE new_sku='SKU2'").fetchall()
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["old_sku"], h[0]["new_sku"]), ("SKU1", "SKU2"))
        self.assertNotIn(h[0]["username"], ("", "sistema", "desconhecido"))


if __name__ == "__main__":
    unittest.main()
