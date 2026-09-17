"""Invariantes da Conferência: bip persistente e conclusão canônica.

V17.70 — o bip confirmado pelo operador é monotônico.
V17.72 / V17.80 — pedido concluído nunca volta para a fila.
"""

import unittest

from base import (
    S,
    TesteNoromak,
    gravar_como_integracao,
    linha,
    pedidos,
)


class BipPersistente(TesteNoromak):
    """V17.70 — nenhum snapshot antigo pode desfazer um bip já confirmado."""

    def linha_lida(self, quantidade_lida: int) -> list[dict]:
        return [linha("PED-1", "SKU-A", quantidade=3, uid="PED-1-L1",
                      Lido=quantidade_lida)]

    def test_quantidade_bipada_nao_diminui_com_snapshot_antigo(self):
        gravar_como_integracao(self.linha_lida(0))
        # O operador bipa 2 das 3 unidades.
        self.salvar_admin(self.linha_lida(2))

        # Um polling atrasado devolve a cópia antiga, com Lido=0.
        gravar_como_integracao(self.linha_lida(0))

        atual = pedidos()[0]
        self.assertEqual(
            atual.get("Lido"), 2,
            "REGRESSÃO: sincronização atrasada zerou o bip já confirmado")

    def test_quantidade_bipada_pode_aumentar(self):
        gravar_como_integracao(self.linha_lida(0))
        self.salvar_admin(self.linha_lida(1))
        self.salvar_admin(self.linha_lida(3))
        self.assertEqual(pedidos()[0].get("Lido"), 3,
                         "o bip não pôde avançar para a quantidade total")

    def test_metadados_de_conferencia_nao_somem(self):
        gravar_como_integracao(self.linha_lida(0))
        self.salvar_admin([linha("PED-1", "SKU-A", quantidade=3, uid="PED-1-L1",
                                 Lido=3, Conferido=True,
                                 ConferidoPor="operador_teste")])

        gravar_como_integracao(self.linha_lida(0))

        atual = pedidos()[0]
        self.assertTrue(atual.get("Conferido"),
                        "a marca de conferido foi apagada por um snapshot antigo")
        self.assertEqual(atual.get("ConferidoPor"), "operador_teste")


class ConclusaoCanonica(TesteNoromak):
    """V17.72 — completed_orders é barreira de reentrada, não enfeite visual."""

    def concluir(self, pedido: str) -> dict:
        status, corpo = self.cliente.pedir("POST", "/api/order-complete", {"pedido": pedido})
        self.assertEqual(status, 200, f"conclusão falhou: {corpo}")
        return corpo

    def test_pedido_concluido_sai_da_fila(self):
        self.salvar_admin([linha("PED-1", "SKU-A"), linha("PED-2", "SKU-B")])
        self.concluir("PED-1")
        self.assertPedidoAusente("PED-1", "pedido concluído continuou na fila")
        self.assertPedidoPresente("PED-2", "a conclusão afetou outro pedido")

    def test_pedido_concluido_nao_volta_pela_sincronizacao(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.concluir("PED-1")
        gravar_como_integracao([linha("PED-1", "SKU-A")])
        self.assertPedidoAusente(
            "PED-1", "REGRESSÃO: pedido concluído voltou para a fila pela sincronização")

    def test_kit_concluido_nao_volta_por_nenhum_componente(self):
        kit = [
            linha("PED-KIT", "COMP-1", uid="PED-KIT-L1"),
            linha("PED-KIT", "COMP-2", uid="PED-KIT-L2"),
        ]
        self.salvar_admin(kit)
        self.concluir("PED-KIT")
        self.assertPedidoAusente("PED-KIT", "KIT concluído sobrou na fila")

        gravar_como_integracao(kit)
        self.assertPedidoAusente(
            "PED-KIT", "REGRESSÃO: componente de KIT concluído voltou pela sincronização")

    def test_pedido_manual_concluido_nao_volta_pelo_cofre(self):
        """O cofre manual e o override __ENTITY__ não podem furar a conclusão."""
        status, corpo = self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [linha("PED-MANUAL", "SKU-M")], "source": "PAINEL_ADMIN",
        })
        self.assertEqual(status, 200, f"cadastro manual falhou: {corpo}")
        self.assertPedidoPresente("PED-MANUAL", "o pedido manual não entrou")

        self.concluir("PED-MANUAL")
        self.assertPedidoAusente(
            "PED-MANUAL", "REGRESSÃO: pedido manual concluído voltou pelo cofre/override")

        gravar_como_integracao([linha("PED-OUTRO", "SKU-Z")])
        self.assertPedidoAusente(
            "PED-MANUAL", "pedido manual concluído voltou após uma sincronização")

    def test_admin_pode_reimportar_pedido_concluido(self):
        """A conclusão é barreira para automação, não para o ADMIN."""
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.concluir("PED-1")
        self.assertPedidoAusente("PED-1", "pedido concluído continuou na fila")

        status, corpo = self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [linha("PED-1", "SKU-A")], "source": "PAINEL_ADMIN",
        })
        self.assertEqual(status, 200, f"reimportação do ADMIN falhou: {corpo}")
        self.assertPedidoPresente(
            "PED-1", "o ADMIN reimportou explicitamente e o pedido não voltou")

    def test_conclusao_fica_registrada_no_historico_permanente(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.concluir("PED-1")
        total = S.DB.execute(
            "SELECT COUNT(*) FROM order_completion_history WHERE order_key=?",
            ("PED-1",),
        ).fetchone()[0]
        self.assertEqual(total, 1, "a conclusão não entrou no histórico permanente")

    def test_pedido_concluido_resiste_a_varias_sincronizacoes(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.concluir("PED-1")
        for rodada in range(5):
            gravar_como_integracao([linha("PED-1", "SKU-A")])
            self.assertPedidoAusente(
                "PED-1", f"pedido concluído voltou na rodada {rodada + 1}")


if __name__ == "__main__":
    unittest.main()
