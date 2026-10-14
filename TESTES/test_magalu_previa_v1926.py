"""V19.26 — Magalu, fase 1a: ver os pedidos que entrariam na fila, SEM gravar.

Antes de ligar a importação (1b), o dono confere a prévia com o Portal do Seller e a 1ª
resposta real fica guardada para conferir os nomes dos campos (a doc só tem exemplo).
"""

import json
from unittest import mock

from base import S, TesteNoromak, gravar_como_admin, linha, pedidos


def _gravar(chave: str, valor) -> None:
    atual, _, _ = S.get_storage(chave)
    S.save_storage(chave, atual, json.dumps(valor), False, actor="admin_teste", actor_role="admin",
                   source_screen="Teste", change_method="Teste automatizado", client_ip="127.0.0.1")


def _item(sku, qtd, nome="Peça"):
    return {"info": {"sku": sku, "name": nome, "id": "i-" + sku}, "quantity": qtd,
            "unit_price": {"value": 1990, "normalizer": 100}}


def _pedido(code, entregas, status="approved"):
    return {"code": code, "id": "id-" + code, "status": status, "purchased_at": "2026-10-14T12:00:00+00:00",
            "customer": {"name": "Cliente " + code},
            "deliveries": [{"code": f"{code}-{i + 1}", "id": f"d{i}", "status": st, "items": itens,
                            "shipping": {"handling_time": {"limit_date": "2026-10-16T00:00:00Z"}}}
                           for i, (st, itens) in enumerate(entregas)]}


class MagaluPrevia(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self.chamadas = []

    def _api(self, paginas):
        def fake(path, params=None):
            self.chamadas.append((path, dict(params or {})))
            off = int((params or {}).get("_offset", 0))
            return paginas.get(off, {"meta": {"page": {"count": 0}}, "results": []})
        return mock.patch.object(S, "magalu_api_get", side_effect=fake)

    def test_previa_mostra_o_que_entraria_e_nao_grava(self):
        _gravar("conf_kits", [{"sku_kit": "KIT-AB", "nome": "Kit A+B",
                               "componentes": [{"sku": "AAA", "quantidade": 1}, {"sku": "BBB", "quantidade": 2}]}])
        gravar_como_admin([linha("1111111111111111", "ZZZ", Marketplace="MAGALU")])
        antes = json.dumps(pedidos(), sort_keys=True)
        resp = {0: {"meta": {"page": {"count": 3, "limit": 100}}, "results": [
            _pedido("1111111111111111", [("approved", [_item("ZZZ", 1)])]),
            _pedido("2222222222222222", [("invoiced", [_item("KIT-AB", 2), _item("CCC", 1)]),
                                         ("shipped", [_item("DDD", 1)])]),
            _pedido("3333333333333333", [("delivered", [_item("EEE", 1)])]),
        ]}}
        with self._api(resp):
            r = S.magalu_pedidos_previa()
        self.assertEqual(json.dumps(pedidos(), sort_keys=True), antes, "a prévia NÃO pode gravar na fila")
        por = {p["pedido"]: p for p in r["pedidos"]}
        self.assertEqual(set(por), {"1111111111111111", "2222222222222222"}, "entrega enviada/entregue não entra")
        self.assertTrue(por["1111111111111111"]["ja_na_fila"])
        self.assertFalse(por["2222222222222222"]["ja_na_fila"])
        linhas = {(l["sku"], l["quantidade"], l["kit"]) for l in por["2222222222222222"]["linhas"]}
        self.assertEqual(linhas, {("AAA", "2", "KIT-AB"), ("BBB", "4", "KIT-AB"), ("CCC", "1", "")})
        self.assertNotIn("DDD", {l["sku"] for l in por["2222222222222222"]["linhas"]})
        self.assertEqual(self.chamadas[0][0], "/seller/v1/orders")
        self.assertEqual(self.chamadas[0][1].get("status"), "approved")

    def test_pagina_ate_acabar(self):
        pag = lambda codes: {"meta": {"page": {"count": len(codes), "limit": 2}},
                             "results": [_pedido(c, [("approved", [_item("X", 1)])]) for c in codes]}
        with self._api({0: pag(["A1", "A2"]), 2: pag(["A3"])}), mock.patch.object(S, "MAGALU_PEDIDOS_POR_PAGINA", 2):
            r = S.magalu_pedidos_previa()
        self.assertEqual([p["pedido"] for p in r["pedidos"]], ["A1", "A2", "A3"])

    def test_primeira_resposta_real_fica_guardada(self):
        with self._api({0: {"meta": {}, "results": [_pedido("A1", [("approved", [_item("X", 1)])])]}}):
            S.magalu_pedidos_previa()
        st, corpo = self.cliente.pedir("GET", "/api/integracoes/magalu/amostra")
        self.assertEqual(st, 200)
        self.assertEqual(corpo["amostras"][0]["tipo"], "orders")
        self.assertIn("A1", corpo["amostras"][0]["resposta"])

    def test_rota_da_previa(self):
        with self._api({0: {"meta": {}, "results": []}}):
            st, corpo = self.cliente.pedir("GET", "/api/integracoes/magalu/previa")
        self.assertEqual(st, 200, corpo)
        self.assertEqual(corpo["pedidos"], [])

    def test_magalu_api_get_renova_no_401(self):
        tokens = iter(["T1", "T2"])
        with mock.patch.object(S, "magalu_access_token", side_effect=lambda force=False: next(tokens)), \
                mock.patch.object(S, "_magalu_http_get", side_effect=[S.MagaluHttp401(), {"ok": 1}]) as get:
            self.assertEqual(S.magalu_api_get("/seller/v1/orders"), {"ok": 1})
        self.assertEqual([c.args[1] for c in get.call_args_list], ["T1", "T2"])


if __name__ == "__main__":
    import unittest
    unittest.main()
