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
                 "componentes": [{"sku": "0899900101", "quantidade": 1}, {"sku": "0893014105", "quantidade": 1}]}]
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

    def gravar_fila_de_producao(self) -> None:
        """A fila como a regra antiga deixou o carrinho (lida em produção em 26/10)."""
        from base import gravar_como_integracao, linha
        base = dict(Marketplace="MERCADO LIVRE", PackIdMercadoLivre=CARRINHO, OrigemCadastro="API Mercado Livre")
        gravar_como_integracao([
            linha(CARRINHO, "0899900101", 1, uid="MLAPI_ff8c45dd0b79c55515bf", VendaMercadoLivre=AVULSA,
                  MercadoLivreLinhaKey=f"{AVULSA}|MLB7715889578|None|0", Kit="SIM", SkuKitOrigem=SKU_KIT, **base),
            linha(CARRINHO, "0893014105", 1, uid="MLAPI_8e1995b355d1201380fe", VendaMercadoLivre=KIT,
                  MercadoLivreLinhaKey=f"{KIT}|MLB7859805954|None|1", Kit="SIM", SkuKitOrigem=SKU_KIT, **base),
            linha(CARRINHO, SKU_KIT, 1, uid="MLAPI_82f376361dc110e3e53e", VendaMercadoLivre=KIT,
                  MercadoLivreLinhaKey=f"{KIT}|MLB7859805954|None|0", Kit="VERIFICAR", **base),
        ], origem="API Mercado Livre")

    def test_fila_ja_embaralhada_se_acerta_na_releitura(self):
        self.gravar_fila_de_producao()
        self.cadastrar_kit()
        for venda_id in (KIT, AVULSA, KIT):
            S.ml_import_order(venda_id, source="teste")
            self.assertLessEqual(self.pecas().get("0899900101", 0), 2, "pano duplicado na releitura")
        self.assertEqual(self.pecas(), {"0899900101": 2, "0893014105": 1})

    def test_linhas_marcadas_com_a_venda_errada_nao_duplicam(self):
        # Regra antiga: importar B remarcava todas as linhas do carrinho como B.
        from base import gravar_como_integracao, linha
        base = dict(Marketplace="MERCADO LIVRE", PackIdMercadoLivre=CARRINHO, OrigemCadastro="API Mercado Livre",
                    VendaMercadoLivre=KIT)
        self.cadastrar_kit()
        gravar_como_integracao([
            linha(CARRINHO, "0899900101", 1, uid="U-A", MercadoLivreLinhaKey=f"{AVULSA}|MLB7715889578|None|0", **base),
            linha(CARRINHO, "0893014105", 1, uid="U-B1", MercadoLivreLinhaKey=f"{KIT}|MLB7859805954|None|1", **base),
            linha(CARRINHO, "0899900101", 1, uid="U-B0", MercadoLivreLinhaKey=f"{KIT}|MLB7859805954|None|0", **base),
        ], origem="API Mercado Livre")
        S.ml_import_order(AVULSA, source="teste")
        self.assertEqual(self.pecas(), {"0899900101": 2, "0893014105": 1})

    def test_peca_excluida_pelo_admin_nao_volta(self):
        # O tombstone de linha ML é pelo UID: o UID da peça não pode mudar entre versões.
        self.cadastrar_kit()
        S.ml_import_order(KIT, source="teste")
        uid = {r["SKU"]: r["__uidLinha"] for r in pedidos()}["0893014105"]
        self.assertEqual(uid, "MLAPI_8e1995b355d1201380fe", "UID da peça mudou: tombstones antigos deixam de valer")
        atual, _, _ = S.get_storage("conf_pedidos")
        linhas = [r for r in json.loads(atual) if r["SKU"] != "0893014105"]
        S.save_storage("conf_pedidos", atual, json.dumps(linhas), True, actor="admin_teste", actor_role="admin",
                       source_screen="Admin", change_method="Excluir linha", client_ip="127.0.0.1")
        S.ml_import_order(KIT, source="teste")
        self.assertNotIn("0893014105", self.pecas(), "peça excluída pelo ADMIN voltou")

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
