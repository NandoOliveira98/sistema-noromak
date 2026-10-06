"""V18.71 — KIT da Shopee chega à Conferência como os produtos do kit.

Bug de 06/10/2026 (pedido 261006VAG06G07): a Shopee mandou uma linha só, com o
SKU do anúncio do kit "0893600022/0893764272". O servidor não abria kit da
Shopee (só do ML); o Admin abria só na TELA (V16.46), e a Conferência mostrava a
linha colada — que não dá para bipar. Funcionava "às vezes" porque qualquer
salvamento no Admin gravava a lista já aberta.

Agora a sincronização abre o kit pelo Cadastro de Kits, como no ML, com o mesmo
__uidLinha que o Admin já usa (KITDB_…), para casar com o que já foi gravado.
Roda a sincronização real; só a resposta da Shopee é simulada.
"""

import json

from base import S, TesteNoromak, gravar_como_admin, linha, pedidos

PEDIDO = "261006VAG06G07"
KIT = "0893600022/0893764272"
A, B = "0893600022", "0893764272"


def item(sku, qtd=1, item_id=1, model_id=10, line_item_id=""):
    return {"model_sku": sku, "item_sku": sku, "item_id": item_id, "model_id": model_id,
            "line_item_id": line_item_id, "model_quantity_purchased": qtd,
            "item_name": "Higienizador + Cheirinho"}


class ShopeeKit(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.itens = {PEDIDO: [item(KIT)]}
        self.originais = (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
                          S.shopee_log_event)
        S.shopee_status_payload = lambda: {"connected": True, "shop_id": "1"}
        S.shopee_current_shipments = lambda: [{"order_sn": p, "package_number": "P-" + p} for p in self.itens]
        S.shopee_order_details = lambda sns: [
            {"order_sn": p, "order_status": "READY_TO_SHIP", "item_list": self.itens[p],
             "recipient_address": {"name": "Everton Meira"}} for p in sns]
        S.shopee_log_event = lambda *a, **k: None
        self.cadastrar_kit(KIT, [(A, 1), (B, 1)])

    def tearDown(self):
        (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
         S.shopee_log_event) = self.originais
        super().tearDown()

    def cadastrar_kit(self, sku_kit, componentes):
        atual, _, _ = S.get_storage("conf_kits")
        kits = [k for k in json.loads(atual or "[]") if k.get("sku_kit") != sku_kit]
        kits.append({"sku_kit": sku_kit, "nome": "Kit Higienizador + Cheirinho", "ativo": True,
                     "componentes": [{"sku": s, "quantidade": q} for s, q in componentes]})
        S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                       source_screen="teste", change_method="teste", client_ip="127.0.0.1")

    def linhas(self, pedido=PEDIDO):
        return [r for r in pedidos() if r.get("Pedido") == pedido]

    def skus(self, pedido=PEDIDO):
        return sorted((r["SKU"], r["Quantidade"]) for r in self.linhas(pedido))

    def test_kit_so_com_a_linha_colada_vira_os_componentes(self):
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1"), (B, "1")], "REGRESSÃO: Conferência recebeu o SKU colado do kit")
        for r in self.linhas():
            self.assertEqual((r["Kit"], r["SkuKitOrigem"], r["Quantidade de Kits"]), ("SIM", KIT, "1"))
            self.assertEqual(r["OrigemCadastro"], "API_SHOPEE")
            self.assertTrue(r["__uidLinha"].startswith(f"KITDB_{PEDIDO}_{KIT}_"), r["__uidLinha"])

    def test_sincronizar_de_novo_nao_duplica_e_nao_perde_bip(self):
        S.shopee_manual_sync()
        linhas = pedidos()
        for r in linhas:
            if r.get("SKU") == A:
                r["Lido"] = 1
        gravar_como_admin(linhas)
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1"), (B, "1")])
        lido = {r["SKU"]: int(r.get("Lido") or 0) for r in self.linhas()}
        self.assertEqual(lido[A], 1, "REGRESSÃO: bip do componente perdido na nova sincronização")

    def test_pedido_pendente_antigo_com_a_linha_colada_e_consertado(self):
        """O que já está na fila desde antes da correção (linha colada gravada)."""
        antiga = S._shopee_build_pending_lines(
            {"order_sn": PEDIDO, "item_list": [item(KIT)], "recipient_address": {"name": "Everton Meira"}}, ["P1"], [])
        self.assertEqual(antiga[0]["SKU"], KIT)
        from base import gravar_como_integracao
        gravar_como_integracao(antiga)
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1"), (B, "1")], "a linha colada antiga ficou na fila")

    def test_componentes_salvos_pelo_admin_casam_com_os_do_servidor(self):
        """O Admin, ao salvar, grava o kit aberto com KITDB_<pedido>_<kit>_<sku>_<i>."""
        S.shopee_manual_sync()
        linhas = pedidos()
        uids = {r["SKU"]: r["__uidLinha"] for r in linhas}
        self.assertEqual(uids, {A: f"KITDB_{PEDIDO}_{KIT}_{A}_0", B: f"KITDB_{PEDIDO}_{KIT}_{B}_1"})

    def test_quantidade_de_kits_multiplica_os_componentes(self):
        self.cadastrar_kit(KIT, [(A, 1), (B, 2)])
        self.itens[PEDIDO] = [item(KIT, qtd=3)]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "3"), (B, "6")])
        self.assertEqual({r["Quantidade de Kits"] for r in self.linhas()}, {"3"})

    def test_kit_nao_cadastrado_fica_como_veio(self):
        self.itens[PEDIDO] = [item("SEMCADASTRO-1/SEMCADASTRO-2")]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [("SEMCADASTRO-1/SEMCADASTRO-2", "1")])

    def test_produto_simples_nao_vira_kit(self):
        self.itens[PEDIDO] = [item(A)]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1")])
        self.assertEqual(self.linhas()[0].get("Kit"), "")

    def test_shopee_ja_mandou_os_componentes_nao_duplica(self):
        self.itens[PEDIDO] = [item(KIT, item_id=1), item(A, item_id=2, model_id=20), item(B, item_id=3, model_id=30)]
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1"), (B, "1")])

    def test_componente_excluido_pelo_admin_nao_volta(self):
        S.shopee_manual_sync()
        uid_b = next(r["__uidLinha"] for r in self.linhas() if r["SKU"] == B)
        status, corpo = self.cliente.pedir("POST", "/api/order-line-delete", {
            "pedido": PEDIDO, "uid": uid_b, "uidLinha": uid_b, "sku": B, "marketplace": "SHOPEE"})
        self.assertEqual(status, 200, corpo)
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [(A, "1")], "REGRESSÃO: componente excluído pelo ADMIN voltou")

    def test_kit_inteiro_excluido_pelo_admin_nao_volta(self):
        S.shopee_manual_sync()
        self.excluir_admin(PEDIDO)
        S.shopee_manual_sync()
        self.assertPedidoAusente(PEDIDO, "REGRESSÃO: pedido excluído pelo ADMIN voltou pela Shopee")

    def test_linha_do_kit_excluida_pelo_admin_antes_da_correcao_nao_vira_componentes(self):
        """Exclusão manual gravada com o SKU do kit (linha colada) continua valendo:
        abrir o kit em componentes não pode desfazer a decisão do ADMIN."""
        from base import gravar_como_integracao
        antiga = S._shopee_build_pending_lines(
            {"order_sn": PEDIDO, "item_list": [item(KIT)], "recipient_address": {"name": "Everton Meira"}}, ["P1"], [])
        self.itens["OUTRO000000001"] = [item(A, item_id=9, model_id=90)]
        gravar_como_integracao(antiga)
        uid = antiga[0]["__uidLinha"]
        status, corpo = self.cliente.pedir("POST", "/api/order-line-delete", {
            "pedido": PEDIDO, "uid": uid, "sku": KIT, "marketplace": "SHOPEE"})
        self.assertEqual(status, 200, corpo)
        S.shopee_manual_sync()
        self.assertEqual(self.skus(), [], "REGRESSÃO: kit excluído pelo ADMIN voltou como componentes")
