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
from unittest import mock

from base import S, TesteNoromak, gravar_como_admin, pedidos


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

    # --- Transição: pedido que já estava na fila com a linha do kit antes da V20.22 ---

    def sync_antes_da_v2022(self):
        with mock.patch.object(S, "_infer_quantity_kit_from_sku", return_value=None):
            S.shopee_manual_sync()

    def test_linha_antiga_do_kit_com_bip_nao_volta_e_o_bip_passa(self):
        pedido = "261028SR23HUPK"
        self.itens[pedido] = [item("kit10-10396473")]
        self.sync_antes_da_v2022()
        linhas = pedidos()
        for r in linhas:
            if r.get("Pedido") == pedido:
                r["Lido"] = 1     # o kit (1 de 1) já tinha sido bipado
        gravar_como_admin(linhas)
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        rows = [r for r in pedidos() if r.get("Pedido") == pedido]
        self.assertEqual([(r["SKU"], r["Quantidade"]) for r in rows], [("10396473", "10")],
                         "REGRESSÃO: a linha antiga do kit voltou junto com os componentes")
        self.assertEqual(int(rows[0].get("Lido") or 0), 10, "REGRESSÃO: o bip do kit se perdeu na troca")

    def test_linha_antiga_do_kit_meio_bipada_passa_o_proporcional(self):
        pedido = "261028AAAAAAAA"
        self.itens[pedido] = [item("kit2-0893764272", qtd=3)]
        self.sync_antes_da_v2022()
        linhas = pedidos()
        for r in linhas:
            if r.get("Pedido") == pedido:
                r["Lido"] = 1     # 1 dos 3 kits
        gravar_como_admin(linhas)
        S.shopee_manual_sync()
        rows = [r for r in pedidos() if r.get("Pedido") == pedido]
        self.assertEqual([(r["SKU"], r["Quantidade"], int(r.get("Lido") or 0)) for r in rows],
                         [("0893764272", "6", 2)])


    def _admin_edita_linha_antiga(self, pedido, **campos):
        linhas = pedidos()
        for r in linhas:
            if r.get("Pedido") == pedido:
                r.update(campos)
        gravar_como_admin(linhas)

    def test_linha_antiga_com_quantidade_do_admin_fica_e_nao_infla_o_bip(self):
        """O ADMIN contornou o bug pondo Quantidade 10 na linha do kit e 4 já foram bipados."""
        pedido = "261028BBBBBBBB"
        self.itens[pedido] = [item("kit10-10396473")]
        self.sync_antes_da_v2022()
        self._admin_edita_linha_antiga(pedido, Quantidade="10")
        self._admin_edita_linha_antiga(pedido, Lido=4)
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        rows = [r for r in pedidos() if r.get("Pedido") == pedido]
        self.assertEqual([(r["SKU"], r["Quantidade"], int(r.get("Lido") or 0)) for r in rows],
                         [("kit10-10396473", "10", 4)],
                         "REGRESSÃO: linha corrigida pelo ADMIN sumiu ou o bip ficou maior que o real")

    def test_linha_antiga_com_sku_trocado_pelo_admin_nao_fica_em_dobro(self):
        pedido = "261028CCCCCCCC"
        self.itens[pedido] = [item("kit10-10396473")]
        self.sync_antes_da_v2022()
        self._admin_edita_linha_antiga(pedido, SKU="10396473", Quantidade="10")
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        self.assertEqual(self.skus(pedido), [("10396473", "10")],
                         "REGRESSÃO: SKU corrigido pelo ADMIN + componentes = 20 unidades")


class MagaluKitTransicao(TesteNoromak):
    """V20.22 — mesma troca na Magalu (e no TikTok, que usa o mesmo merge)."""

    def setUp(self):
        super().setUp()
        self.api = []
        p = mock.patch.object(S, "magalu_pedidos_abertos", side_effect=lambda: list(self.api))
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.object(S, "_magalu_baixar_etiqueta", return_value="^XA^XZ")
        e.start()
        self.addCleanup(e.stop)
        S.magalu_importacao_definir(True, "admin_teste")
        self.api = [{"code": "1578270107775283", "id": "O1", "status": "approved", "channel": {"id": "C"},
                     "purchased_at": "2026-11-04T12:00:00+00:00", "customer": {"name": "Cliente"},
                     "deliveries": [{"code": "E1", "id": "D1", "status": "approved",
                                     "items": [{"info": {"sku": "KIT2-0893764272", "name": "Kit", "id": "i1"},
                                                "quantity": 1}],
                                     "shipping": {"handling_time": {"limit_date": "2026-11-06T00:00:00Z"}}}]}]

    def linhas(self):
        return sorted((r["SKU"], r["Quantidade"]) for r in pedidos() if r.get("Pedido") == "1578270107775283")

    def importar_antes_da_v2022(self):
        with mock.patch.object(S, "_infer_quantity_kit_from_sku", return_value=None):
            S.magalu_importar_pedidos()

    def test_linha_antiga_do_kit_nao_fica_junto_dos_componentes(self):
        self.importar_antes_da_v2022()
        self.assertEqual(self.linhas(), [("KIT2-0893764272", "1")])
        S.magalu_importar_pedidos()
        S.magalu_importar_pedidos()
        self.assertEqual(self.linhas(), [("0893764272", "2")],
                         "REGRESSÃO: kit em dobro na Magalu (linha antiga + componentes)")

    def test_linha_do_kit_excluida_pelo_admin_nao_volta_como_componentes(self):
        self.importar_antes_da_v2022()
        uid = next(r["__uidLinha"] for r in pedidos() if r.get("Pedido") == "1578270107775283")
        status, corpo = self.cliente.pedir("POST", "/api/order-line-delete", {
            "pedido": "1578270107775283", "uid": uid, "sku": "KIT2-0893764272", "marketplace": "MAGALU"})
        self.assertEqual(status, 200, corpo)
        S.magalu_importar_pedidos()
        self.assertEqual(self.linhas(), [], "REGRESSÃO: linha excluída pelo ADMIN voltou como componentes")

