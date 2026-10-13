"""V19.16 — bip leve (13/10): a cada bip a Conferência mandava a fila INTEIRA (~700 KB ida e
volta, XHR síncrono) e o computador de 4 GB da expedição congelava. Agora o bip manda só a
linha (Lido) para /api/order-progress, com as mesmas regras: bip nunca diminui, não passa da
quantidade, pedido concluído não volta, filial respeitada."""
import json
import unittest

from base import S, TesteNoromak


def fila():
    return json.loads(S.get_storage("conf_pedidos")[0])


class BipLeve(TesteNoromak):
    def setUp(self):
        super().setUp()
        linhas = [
            {"__uidLinha": "L1", "Pedido": "P100", "SKU": "A", "Código de Barras": "111", "Quantidade": 3, "Lido": 0},
            {"__uidLinha": "L2", "Pedido": "P100", "SKU": "B", "Código de Barras": "222", "Quantidade": 1, "Lido": 0},
            {"Pedido": "P200", "SKU": "C", "Código de Barras": "333", "Quantidade": 2, "Lido": 0},
        ]
        S.save_storage("conf_pedidos", None, json.dumps(linhas), False, actor="admin", actor_role="admin")

    def bip(self, pedido, **linha):
        return self.cliente.pedir("POST", "/api/order-progress", {"pedido": pedido, "linhas": [linha]})

    def lido(self, pedido, sku):
        return next(r["Lido"] for r in fila() if r["Pedido"] == pedido and r["SKU"] == sku)

    def test_grava_so_a_linha(self):
        st, c = self.bip("P100", uid="L1", sku="A", ean="111", lido=2)
        self.assertEqual(st, 200, c)
        self.assertEqual((self.lido("P100", "A"), self.lido("P100", "B")), (2, 0))
        self.assertNotIn("value", c)                       # resposta leve, sem a fila

    def test_sem_uid_pelo_sku_e_ean(self):
        self.assertEqual(self.bip("P200", sku="C", ean="333", lido=1)[0], 200)
        self.assertEqual(self.lido("P200", "C"), 1)

    def test_nunca_diminui_nem_passa_da_quantidade(self):
        self.bip("P100", uid="L1", sku="A", ean="111", lido=2)
        self.bip("P100", uid="L1", sku="A", ean="111", lido=1)   # bip velho chegando depois
        self.assertEqual(self.lido("P100", "A"), 2)
        self.bip("P100", uid="L1", sku="A", ean="111", lido=9)
        self.assertEqual(self.lido("P100", "A"), 3)

    def test_linha_inexistente_pede_o_caminho_antigo(self):
        st, c = self.bip("P100", uid="NAO", sku="Z", ean="999", lido=1)
        self.assertEqual(st, 409, c)

    def test_pedido_concluido_nao_volta(self):
        self.bip("P100", uid="L1", sku="A", ean="111", lido=3)
        self.bip("P100", uid="L2", sku="B", ean="222", lido=1)
        st, c = self.cliente.pedir("POST", "/api/order-complete", {"pedido": "P100"})
        self.assertEqual(st, 200, c)
        st, c = self.bip("P100", uid="L1", sku="A", ean="111", lido=3)
        self.assertEqual(st, 409, c)
        self.assertFalse(any(r["Pedido"] == "P100" for r in fila()))

    def test_versao_sobe_para_as_outras_telas(self):
        antes = S.get_storage("conf_pedidos")[1]
        self.bip("P100", uid="L1", sku="A", ean="111", lido=1)
        self.assertGreater(S.get_storage("conf_pedidos")[1], antes)

    def test_tela_usa_o_bip_leve(self):
        from base import RAIZ
        html = (RAIZ / "APP" / "conferencia.html").read_text(encoding="utf-8")
        js = (RAIZ / "APP" / "shared-storage.js").read_text(encoding="utf-8")
        self.assertIn("/api/order-progress", js)
        self.assertIn("salvarBipLeve", html)


if __name__ == "__main__":
    unittest.main()
