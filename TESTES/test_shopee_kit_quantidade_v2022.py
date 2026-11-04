"""V20.22 — kit "kitN-X" da Shopee subia com a quantidade errada na Conferência.

Casos reais (04/11/2026, dono: "no romaneio sai uma quantidade e sobe outra"):

1. 261104EF1BKBAP — anúncio 0893764272, variação "2 UN Carro novo" (model_sku
   kit2-0893764272, cadastrado como 2 × 0893764272). O servidor abria o kit certo
   (2 un.), mas o reparo de boot da V18.04 (_kits_residuo_prefixo_reparar_rows), feito
   para o ML, achava que era "resíduo": o SkuOrigemImportacao da linha do componente
   (V18.71) é o SKU do componente, sem "KIT". A cada reinício (publicação) a quantidade
   voltava para 1 e o selo de kit sumia. O pedido foi concluído 4 min depois da
   publicação das 16:05, com 1 unidade.

2. 261028SR23HUPK — kit10-10396473 sem Cadastro de Kits subia como 1 × "kit10-10396473".
   O ML já lia o prefixo (10 × 10396473, _infer_quantity_kit_from_sku); a Shopee não.
"""

import json

from base import S, TesteNoromak, pedidos


def item(sku, qtd=1, item_id=1, model_id=10, item_sku=None):
    return {"model_sku": sku, "item_sku": item_sku or sku, "item_id": item_id, "model_id": model_id,
            "line_item_id": "", "model_quantity_purchased": qtd, "item_name": "Produto"}


class ShopeeKitQuantidade(TesteNoromak):

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

    def cadastrar_kit(self, sku_kit, componentes):
        atual, _, _ = S.get_storage("conf_kits")
        kits = [k for k in json.loads(atual or "[]") if k.get("sku_kit") != sku_kit]
        kits.append({"sku_kit": sku_kit, "nome": sku_kit, "ativo": True,
                     "componentes": [{"sku": s, "quantidade": q} for s, q in componentes]})
        S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                       source_screen="teste", change_method="teste", client_ip="127.0.0.1")

    def skus(self, pedido):
        return sorted((r["SKU"], r["Quantidade"]) for r in pedidos() if r.get("Pedido") == pedido)

    def test_kit_cadastrado_nao_volta_para_1_no_reinicio(self):
        pedido = "261104EF1BKBAP"
        self.cadastrar_kit("kit2-0893764272", [("0893764272", 2)])
        self.itens[pedido] = [item("kit2-0893764272", item_sku="0893764272")]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(pedido), [("0893764272", "2")])
        S.reparar_kits_residuo_prefixo_v1803()   # o que roda a cada publicação
        self.assertEqual(self.skus(pedido), [("0893764272", "2")],
                         "REGRESSÃO: o reparo de boot do ML voltou o kit da Shopee para 1 unidade")
        r = [x for x in pedidos() if x.get("Pedido") == pedido][0]
        self.assertEqual((r["Kit"], r["SkuKitOrigem"], r["Quantidade por Kit"]), ("SIM", "kit2-0893764272", "2"))

    def test_linha_de_kit_aberto_pelo_cadastro_nao_e_residuo(self):
        r = {"Pedido": "1", "SKU": "0893764272", "Quantidade": "2", "Kit": "SIM",
             "Quantidade de Kits": "1", "Quantidade por Kit": "2", "SkuKitOrigem": "kit2-0893764272",
             "SkuOrigemImportacao": "0893764272", "__uidLinha": "KITDB_1_KIT2-0893764272_0893764272_0"}
        self.assertEqual(S._kits_residuo_prefixo_reparar_rows([r]), 0)
        self.assertEqual(r["Quantidade"], "2")

    def test_kit_sem_cadastro_pelo_prefixo_como_no_ml(self):
        pedido = "261028SR23HUPK"
        self.cadastrar_kit("kit5-27200154", [("27200154", 5)])
        self.itens[pedido] = [item("kit5-27200154", qtd=2, item_id=1),
                              item("kit10-10396473", item_id=2, model_id=20)]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(pedido), [("10396473", "10"), ("27200154", "10")],
                         "REGRESSÃO: kit10-10396473 sem cadastro subiu como 1 unidade do SKU do kit")
        S.reparar_kits_residuo_prefixo_v1803()
        self.assertEqual(self.skus(pedido), [("10396473", "10"), ("27200154", "10")])

    def test_sku_comum_nao_vira_kit_pelo_prefixo(self):
        pedido = "261104AAAAAAAA"
        self.itens[pedido] = [item("kit27200154", qtd=1)]   # "KIT" como rótulo (V18.19)
        S.shopee_manual_sync()
        self.assertEqual(self.skus(pedido), [("kit27200154", "1")])
