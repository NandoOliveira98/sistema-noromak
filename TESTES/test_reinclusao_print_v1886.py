"""V18.86 — pedido reincluído pelo "Ler print" do Admin não pode sumir.

Produção, 09/10: o ADMIN excluiu o pedido ML 2000018881405252 inteiro (quantidade errada)
e tentou incluir de novo pelo "Ler print". A tela dizia "salvo", mas o pedido não
aparecia: a exclusão grava um tombstone do NÚMERO do pedido, e o print gravava pela
rota comum (/api/storage), que — de propósito — não desfaz tombstone (um snapshot
antigo de outra tela não pode ressuscitar o que o ADMIN excluiu). Só a extensão usava
/api/admin-order-upsert, a reinclusão explícita do ADMIN (V17.78).
"""

import re
import unittest

from base import RAIZ, TesteNoromak, gravar_como_integracao, linha, pedidos

PEDIDO = "2000018881405252"


def _linha_print(uid: str, quantidade: int = 10) -> dict:
    """Linha no formato que salvarPedidoImportadoPorPrint monta."""
    return linha(PEDIDO, "27200153", quantidade, uid=uid,
                 Marketplace="MERCADO LIVRE", OrigemCadastro="MANUAL_ADMIN",
                 ManualAdminProtegido="SIM", ManualAdminOrigem="PAINEL_ADMIN")


class ReinclusaoPeloPrint(TesteNoromak):

    def _excluir_pedido_da_api(self) -> None:
        gravar_como_integracao([linha(PEDIDO, "27200153", 1, uid="MLAPI_x",
                                      Marketplace="MERCADO LIVRE",
                                      VendaMercadoLivre=PEDIDO)], origem="API Mercado Livre")
        self.excluir_admin(PEDIDO, "MERCADO LIVRE")
        self.assertPedidoAusente(PEDIDO, "pedido não saiu na exclusão ADMIN")

    def test_rota_comum_nao_desfaz_a_exclusao(self):
        """Por que o bug acontecia: a rota comum respeita o tombstone (e deve)."""
        self._excluir_pedido_da_api()
        self.salvar_admin([*pedidos(), _linha_print("PED-PRINT-1")])
        self.assertPedidoAusente(
            PEDIDO, "a rota comum passou a desfazer exclusão do ADMIN — snapshot antigo "
                    "de outra tela ressuscitaria pedido excluído")

    def test_reinclusao_explicita_volta_e_resiste_a_sincronizacao(self):
        self._excluir_pedido_da_api()
        status, corpo = self.cliente.pedir("POST", "/api/admin-order-upsert", {
            "rows": [_linha_print("PED-PRINT-1")], "source": "PAINEL_ADMIN_PRINT",
        })
        self.assertEqual(status, 200, f"reinclusão pelo print falhou: {corpo}")
        atuais = [p for p in pedidos() if p.get("Pedido") == PEDIDO]
        self.assertEqual(len(atuais), 1, "o pedido reincluído não voltou (ou duplicou)")
        self.assertEqual(str(atuais[0].get("Quantidade")), "10",
                         "a quantidade corrigida pelo ADMIN não ficou")
        self.assertFalse([t for t in self.tombstones()
                          if t.get("Pedido") == PEDIDO and t.get("Ativo")],
                         "o tombstone continuou ativo após a reinclusão")

        # O vigia do ML grava de novo a lista sem a linha manual.
        gravar_como_integracao([linha("2000000000000001", "OUTRO", uid="MLAPI_y",
                                      Marketplace="MERCADO LIVRE")], origem="API Mercado Livre")
        self.assertPedidoPresente(PEDIDO, "a sincronização apagou o pedido reincluído")

    def test_tela_grava_o_print_pela_reinclusao_do_admin(self):
        html = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        inicio = html.index("function salvarPedidoImportadoPorPrint(")
        corpo = html[inicio:html.index("\nfunction ", inicio + 10)]
        self.assertTrue(
            re.search(r"upsertPedidosAdmin\([^)]*PAINEL_ADMIN_PRINT", corpo),
            "o Salvar do print voltou a gravar pela rota comum: pedido excluído e "
            "incluído de novo some da fila")


if __name__ == "__main__":
    unittest.main()
