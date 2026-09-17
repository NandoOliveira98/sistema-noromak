"""Invariantes da regra absoluta: ADMIN > Noromak > Integrações.

Cada teste aqui representa um problema que JÁ aconteceu em produção e custou uma
versão para ser corrigido (V17.64 a V17.81). O objetivo é que nenhum deles possa
voltar sem a suíte acusar.
"""

import unittest

from base import (
    TesteNoromak,
    gravar_como_integracao,
    linha,
    numeros_de_pedido,
    pedidos,
)


class ExclusaoSoberanaDoAdmin(TesteNoromak):
    """V17.66 / V17.81 — o que o ADMIN exclui não volta."""

    def test_pedido_excluido_nao_volta_pela_sincronizacao(self):
        self.salvar_admin([linha("PED-1", "SKU-A"), linha("PED-2", "SKU-B")])
        self.excluir_admin("PED-1")
        self.assertPedidoAusente("PED-1", "pedido não saiu na exclusão ADMIN")

        # A integração devolve a lista completa, como se nada tivesse acontecido.
        gravar_como_integracao([linha("PED-1", "SKU-A"), linha("PED-2", "SKU-B")])

        self.assertPedidoAusente(
            "PED-1", "REGRESSÃO: a sincronização ressuscitou um pedido excluído pelo ADMIN")
        self.assertPedidoPresente(
            "PED-2", "a exclusão de PED-1 não podia afetar PED-2")

    def test_exclusao_cria_tombstone_ativo(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.excluir_admin("PED-1")

        registros = [t for t in self.tombstones() if t.get("Pedido") == "PED-1"]
        self.assertTrue(registros, "nenhum tombstone foi criado para o pedido excluído")
        self.assertTrue(registros[0].get("Ativo"), "o tombstone nasceu inativo")
        self.assertEqual(registros[0].get("Origem"), "ADMIN")

    def test_kit_inteiro_sai_com_todos_os_componentes(self):
        kit = [
            linha("PED-KIT", "COMP-1", uid="PED-KIT-L1"),
            linha("PED-KIT", "COMP-2", uid="PED-KIT-L2"),
            linha("PED-KIT", "COMP-3", uid="PED-KIT-L3"),
        ]
        self.salvar_admin([*kit, linha("PED-OUTRO", "SKU-Z")])
        resposta = self.excluir_admin("PED-KIT")

        self.assertEqual(resposta.get("removed_rows"), 3, "nem todo componente do KIT saiu")
        self.assertPedidoAusente("PED-KIT", "componente de KIT sobrou após exclusão")

        # A integração tenta recriar o KIT inteiro.
        gravar_como_integracao([*kit, linha("PED-OUTRO", "SKU-Z")])
        self.assertPedidoAusente(
            "PED-KIT", "REGRESSÃO: componente de KIT voltou pela sincronização")

    def test_exclusao_sobrevive_a_varias_rodadas_de_sincronizacao(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.excluir_admin("PED-1")
        for rodada in range(5):
            gravar_como_integracao([linha("PED-1", "SKU-A")])
            self.assertPedidoAusente(
                "PED-1", f"pedido voltou na rodada {rodada + 1} de sincronização")

    def test_resposta_so_confirma_sucesso_se_o_pedido_sumiu(self):
        """V17.81 — o servidor relê a base depois do COMMIT antes de confirmar."""
        self.salvar_admin([linha("PED-1", "SKU-A")])
        resposta = self.excluir_admin("PED-1")
        self.assertTrue(resposta.get("verified"), "o servidor confirmou sem reler a base")
        self.assertTrue(resposta.get("order_tombstone"))


class ReinclusaoExplicitaDoAdmin(TesteNoromak):
    """V17.78 — só o próprio ADMIN pode desfazer uma exclusão do ADMIN."""

    def test_reinclusao_pelo_admin_derruba_o_tombstone(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.excluir_admin("PED-1")
        self.assertPedidoAusente("PED-1", "pedido não saiu")

        status, corpo = self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [linha("PED-1", "SKU-A")],
            "source": "PAINEL_ADMIN",
        })
        self.assertEqual(status, 200, f"reinclusão do ADMIN falhou: {corpo}")
        self.assertPedidoPresente(
            "PED-1", "o ADMIN reincluiu o pedido e ele não voltou")

        ativos = [t for t in self.tombstones()
                  if t.get("Pedido") == "PED-1" and t.get("Ativo")]
        self.assertFalse(ativos, "o tombstone continuou ativo após reinclusão do ADMIN")

    def test_pedido_reincluido_resiste_a_nova_sincronizacao(self):
        self.salvar_admin([linha("PED-1", "SKU-A")])
        self.excluir_admin("PED-1")
        self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [linha("PED-1", "SKU-A")], "source": "PAINEL_ADMIN",
        })
        gravar_como_integracao([linha("PED-9", "SKU-Z")])
        self.assertPedidoPresente(
            "PED-1", "a sincronização apagou um pedido reincluído pelo ADMIN")


class CamposProtegidosPeloAdmin(TesteNoromak):
    """V17.48 / V17.51 — integração atualiza outros dados, nunca o campo do ADMIN."""

    def test_sku_alterado_pelo_admin_nao_e_sobrescrito(self):
        uid = "PED-1-LINHA-UNICA"

        # Sequência real: o pedido CHEGA pela integração com o SKU errado...
        gravar_como_integracao([linha("PED-1", "SKU-ERRADO-DA-API", uid=uid)])
        # ...o ADMIN corrige o SKU na tela de Administração...
        self.salvar_admin([linha("PED-1", "SKU-CORRIGIDO-PELO-ADMIN", uid=uid)])
        # ...e a Shopee insiste no SKU antigo na próxima sincronização.
        gravar_como_integracao([linha("PED-1", "SKU-ERRADO-DA-API", uid=uid)])

        atual = [p for p in pedidos() if p.get("Pedido") == "PED-1"]
        self.assertEqual(len(atual), 1, "a linha duplicou em vez de ser reconciliada")
        self.assertEqual(
            atual[0].get("SKU"), "SKU-CORRIGIDO-PELO-ADMIN",
            "REGRESSÃO: a sincronização sobrescreveu o SKU definido pelo ADMIN")

    def test_pedido_manual_nao_some_na_sincronizacao(self):
        status, corpo = self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [linha("PED-MANUAL", "SKU-M")],
            "source": "PAINEL_ADMIN",
        })
        self.assertEqual(status, 200, f"cadastro manual falhou: {corpo}")

        # A integração devolve a lista SEM o pedido manual — ele não existe na API.
        gravar_como_integracao([linha("PED-API", "SKU-API")])

        self.assertPedidoPresente(
            "PED-MANUAL", "REGRESSÃO: pedido manual do ADMIN sumiu na sincronização")
        self.assertPedidoPresente("PED-API", "o pedido da API não entrou")

    def test_sincronizacao_nao_apaga_pedidos_ausentes_na_resposta(self):
        """V17.44 — conf_pedidos é cumulativo para quem não tem permissão de exclusão."""
        self.salvar_admin([linha("PED-1", "SKU-A"), linha("PED-2", "SKU-B")])
        gravar_como_integracao([linha("PED-2", "SKU-B")])
        self.assertEqual(
            numeros_de_pedido(), {"PED-1", "PED-2"},
            "a sincronização apagou um pedido que apenas não veio nesta rodada")


if __name__ == "__main__":
    unittest.main()
