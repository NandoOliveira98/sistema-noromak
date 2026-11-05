"""V20.23 — mudar um kit na tela Anúncios com pedido desse kit na fila da Conferência.

O kit_salvar grava o MESMO Cadastro de Kits que a sincronização da Shopee usa para abrir o kit
em componentes (_shopee_expand_kit_lines, uid KITDB_<pedido>_<kit>_<sku>_<i>). O teste passa pelo
caminho real da sincronização (shopee_manual_sync com a API da Shopee simulada), sem simular o merge.
"""

import json

from base import S, TesteNoromak, pedidos

PEDIDO = "261105KITQ0001"


def item(sku, qtd=1, item_id=1, model_id=10):
    return {"model_sku": sku, "item_sku": sku, "item_id": item_id, "model_id": model_id,
            "line_item_id": "", "model_quantity_purchased": qtd, "item_name": "Produto"}


class KitNaFila(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.itens = {}
        self.originais = (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
                          S.shopee_log_event)
        S.shopee_status_payload = lambda: {"connected": True, "shop_id": "1"}
        S.shopee_current_shipments = lambda: [{"order_sn": p, "package_number": "P-" + p} for p in self.itens]
        S.shopee_order_details = lambda sns: [
            {"order_sn": p, "order_status": "READY_TO_SHIP", "item_list": self.itens[p],
             "recipient_address": {"name": "Cliente Teste"}} for p in sns]
        S.shopee_log_event = lambda *a, **k: None

    def tearDown(self):
        (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
         S.shopee_log_event) = self.originais
        super().tearDown()

    def versao_kits(self):
        return S.get_storage("conf_kits")[1]

    def salvar_kit(self, comps):
        return S.kit_salvar("KITQ", "Kit Q", [{"sku": s, "quantidade": q} for s, q in comps], "admin_teste",
                            self.versao_kits())

    def linhas(self):
        return sorted(((r["SKU"], str(r["Quantidade"]), int(r.get("Lido") or 0), r.get("__uidLinha"))
                       for r in pedidos() if r.get("Pedido") == PEDIDO), key=lambda x: x[0])

    def bipar(self, sku):
        """Bip pela gravação comum da Conferência (operador), não pelo ADMIN."""
        atual, _, _ = S.get_storage("conf_pedidos")
        rows = json.loads(atual or "[]")
        for r in rows:
            if r.get("Pedido") == PEDIDO and r.get("SKU") == sku:
                r["Lido"] = int(r.get("Lido") or 0) + 1
        S.save_storage("conf_pedidos", atual, json.dumps(rows), False, actor="operador_teste",
                       actor_role="operador", source_screen="Conferência", change_method="bip",
                       client_ip="127.0.0.1")

    def preparar(self):
        self.salvar_kit([("A", 1), ("B", 1), ("C", 1)])
        self.itens[PEDIDO] = [item("KITQ")]
        S.shopee_manual_sync()
        self.assertEqual([x[:3] for x in self.linhas()], [("A", "1", 0), ("B", "1", 0), ("C", "1", 0)])
        self.bipar("B")
        self.assertEqual([x[:3] for x in self.linhas()], [("A", "1", 0), ("B", "1", 1), ("C", "1", 0)])

    def test_tirar_peca_com_pedido_na_fila_e_recusado_e_a_fila_fica(self):
        self.preparar()
        antes = self.linhas()
        with self.assertRaises(ValueError) as erro:
            self.salvar_kit([("B", 1), ("C", 1)])
        self.assertIn("pedido na fila de conferência", str(erro.exception))
        comp = S.kit_composicao("KITQ")
        self.assertEqual([(c["sku"], c["quantidade"]) for c in comp["componentes"]], [("A", 1), ("B", 1), ("C", 1)])
        S.shopee_manual_sync()
        self.assertEqual(self.linhas(), antes)

    def test_mudar_quantidade_com_pedido_na_fila_pode(self):
        self.preparar()
        self.salvar_kit([("A", 3), ("B", 1), ("C", 1)])
        S.shopee_manual_sync()
        linhas = self.linhas()
        self.assertEqual([x[:3] for x in linhas], [("A", "3", 0), ("B", "1", 1), ("C", "1", 0)])
        self.assertEqual(len({x[3] for x in linhas}), 3, "uid repetido na fila")

    def test_mudar_composicao_sem_pedido_na_fila_pode(self):
        self.salvar_kit([("A", 1), ("B", 1), ("C", 1)])
        self.salvar_kit([("B", 1), ("C", 1)])
        comp = S.kit_composicao("KITQ")
        self.assertEqual([(c["sku"], c["quantidade"]) for c in comp["componentes"]], [("B", 1), ("C", 1)])

    def test_pedido_de_outro_kit_nao_trava(self):
        S.kit_salvar("KITZ", "Kit Z", [{"sku": "A", "quantidade": 1}, {"sku": "D", "quantidade": 1}],
                     "admin_teste", self.versao_kits())
        self.salvar_kit([("A", 1), ("B", 1), ("C", 1)])
        self.itens[PEDIDO] = [item("KITZ")]
        S.shopee_manual_sync()
        self.salvar_kit([("B", 1), ("C", 1)])  # nenhum pedido do KITQ na fila
        comp = S.kit_composicao("KITQ")
        self.assertEqual([c["sku"] for c in comp["componentes"]], ["B", "C"])


if __name__ == "__main__":
    import unittest
    unittest.main()
