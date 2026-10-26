"""V19.85 — kit dentro de carrinho do ML duplicava linha na fila (caso real 26/10/2026).

Carrinho 2000015699983821: venda 2000019165135936 = pano 0899900101 avulso; venda
2000019165142788 = kit "KIT-0893014105/0899900101" (hidratante + pano). Certo: 2 panos e
1 hidratante. A fila ficou com o pano avulso e o pano do kit FUNDIDOS numa linha só (a
importação achava a linha por Pedido + SKU, e no carrinho o Pedido é o número do carrinho),
o hidratante, e a linha do kit fechado "VERIFICAR" (importada antes do kit ser reconhecido),
que a troca pelas peças tirava mas o `preserve_pending_records_v1714` devolvia no save.
"""

import json
import unittest

from base import S, TesteNoromak, pedidos

CARRINHO = "2000015699983821"
AVULSA = "2000019165135936"
KIT = "2000019165142788"
SKU_KIT = "KIT-0893014105/0899900101"


def venda(order_id: str, sku: str, titulo: str, mlb: str) -> dict:
    return {"id": order_id, "status": "paid", "pack_id": CARRINHO, "seller": {"id": "SELLER"},
            "buyer": {"billing_info": {"id": "B1"}},
            "order_items": [{"item": {"id": mlb, "title": titulo, "seller_sku": sku}, "quantity": 1}]}


VENDAS = {
    AVULSA: venda(AVULSA, "151001-0899900101", "Pano Microfibra Azul", "MLB7715889578"),
    KIT: venda(KIT, SKU_KIT, "Kit Microfibra Hidrata Couro", "MLB7859805954"),
}


class KitEmCarrinhoML(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment,
                           S._ml_fiscal_and_label_state)
        S.ml_access_token = lambda *a, **k: ("token", "SELLER")
        S.ml_api_get = lambda path, *a, **k: VENDAS[path.rsplit("/", 1)[-1]]
        S._ml_resolve_forward_shipment = lambda *a, **k: ("48345861229", {"logistic_type": "cross_docking"})
        S._ml_fiscal_and_label_state = lambda **k: {}

    def tearDown(self) -> None:
        (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment,
         S._ml_fiscal_and_label_state) = self._originais
        super().tearDown()

    def cadastrar_kit(self) -> None:
        atual, _, _ = S.get_storage("conf_kits")
        kits = [{"sku_kit": SKU_KIT, "nome": SKU_KIT, "ativo": True,
                 "componentes": [{"sku": "0893014105", "quantidade": 1}, {"sku": "0899900101", "quantidade": 1}]}]
        S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                       source_screen="Teste", change_method="Teste automatizado", client_ip="127.0.0.1")

    def pecas(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for r in pedidos():
            if str(r.get("Pedido")) == CARRINHO:
                total[r["SKU"]] = total.get(r["SKU"], 0) + int(r.get("Quantidade") or 0)
        return total

    def test_caso_real_kit_reconhecido_depois(self):
        # 11:42 o kit veio fechado (sem cadastro); depois o kit foi reconhecido e as vendas relidas.
        S.ml_import_order(KIT, source="API Mercado Livre / recuperação por shipment")
        self.cadastrar_kit()
        S.ml_import_order(KIT, source="API Mercado Livre / watcher etiqueta")
        S.ml_import_order(AVULSA, source="API Mercado Livre / entrada automática")
        self.assertEqual(self.pecas(), {"0899900101": 2, "0893014105": 1},
                         "REGRESSÃO 26/10: carrinho com kit ficou com linha a mais / peça a menos")

    def test_avulsa_antes_do_kit_nao_funde(self):
        self.cadastrar_kit()
        S.ml_import_order(AVULSA, source="teste")
        S.ml_import_order(KIT, source="teste")
        S.ml_import_order(AVULSA, source="teste")   # releitura não junta nem duplica
        S.ml_import_order(KIT, source="teste")
        self.assertEqual(self.pecas(), {"0899900101": 2, "0893014105": 1})
        vendas = sorted(r["VendaMercadoLivre"] for r in pedidos() if r["SKU"] == "0899900101")
        self.assertEqual(vendas, sorted([AVULSA, KIT]))

    def test_pedido_lancado_a_mao_continua_sendo_reconhecido(self):
        # O vínculo de linha de print/extensão (sem venda) com a venda da API continua.
        from base import gravar_como_admin, linha
        gravar_como_admin([linha(CARRINHO, "0899900101", 1, Marketplace="MERCADO LIVRE")])
        S.ml_import_order(AVULSA, source="teste")
        linhas = [r for r in pedidos() if str(r.get("Pedido")) == CARRINHO]
        self.assertEqual([(r["SKU"], r.get("VendaMercadoLivre")) for r in linhas], [("0899900101", AVULSA)])

    def test_linha_do_kit_fechado_ja_bipada_nao_some(self):
        # Bip é monotônico: a troca automática não apaga linha com leitura.
        S.ml_import_order(KIT, source="teste")
        atual, _, _ = S.get_storage("conf_pedidos")
        linhas = json.loads(atual)
        for r in linhas:
            if r["SKU"] == SKU_KIT:
                r["Lido"] = 1
        S.save_storage("conf_pedidos", atual, json.dumps(linhas), False, actor="op", actor_role="operador",
                       source_screen="Conferência", change_method="Bip", client_ip="127.0.0.1")
        self.cadastrar_kit()
        S.ml_import_order(KIT, source="teste")
        self.assertIn(SKU_KIT, self.pecas())


if __name__ == "__main__":
    unittest.main()
