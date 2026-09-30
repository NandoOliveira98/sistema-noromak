"""V18.47 — invariantes da tela Oportunidades de Kit.

A tela só serve se os números forem honestos:

  * carrinho do ML (vários orders com o mesmo pack_id) é UM pedido;
  * par comprado junto conta uma vez por pedido, e a % é sobre os pedidos de cada produto;
  * par que já existe no Cadastro de Kits aparece marcado, não como oportunidade nova;
  * kit vendido é um produto só — os componentes dele não viram "comprados juntos";
  * "kit de quantidade" sugere a quantidade que o cliente mais leva;
  * ticket baixo diz quantas unidades passam do limite do ML.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from base import S, TesteNoromak

HOJE = datetime.now(S.BR_TZ).date().isoformat()


def _vender(marketplace: str, order_id: str, sku: str, qty: int, valor: float,
            pack_id: str = "", dia: str = HOJE) -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,"
            "sku,anuncio,title,qty,amount,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (marketplace, order_id, sku, pack_id, dia + "T10:00:00-03:00", dia, sku, "",
             f"Produto {sku}", qty, valor, "paid", S.now_iso()),
        )
        S.DB.commit()


def _gravar(chave: str, valor) -> None:
    atual, _, _ = S.get_storage(chave)
    S.save_storage(chave, atual, json.dumps(valor), False,
                   actor="admin_teste", actor_role="admin", source_screen="Teste",
                   change_method="Teste automatizado", client_ip="127.0.0.1")


class TesteKitsAnalise(TesteNoromak):

    def cenario(self, min_pedidos: int = 2) -> dict:
        _gravar("precificador_online_v1", {"products": [
            {"sku": "AAA", "produto": "Filtro", "custo": 10.0},
            {"sku": "BBB", "produto": "Vela", "custo": 5.0},
            {"sku": "CCC", "produto": "Oleo", "custo": 20.0},
        ]})
        _gravar("conf_kits", [{"sku_kit": "KIT-AC", "nome": "Kit filtro + oleo",
                               "componentes": [{"sku": "AAA", "quantidade": 1}, {"sku": "CCC", "quantidade": 1}]}])
        # Carrinho ML: dois orders, mesmo pack -> um pedido com AAA + BBB.
        _vender("ml", "O1", "AAA", 1, 40.0, pack_id="P1")
        _vender("ml", "O2", "BBB", 2, 20.0, pack_id="P1")
        # Shopee: AAA + BBB no mesmo pedido.
        _vender("shopee", "S1", "AAA", 1, 40.0)
        _vender("shopee", "S1", "BBB", 1, 10.0)
        # AAA + CCC juntos (já é o KIT-AC) em dois pedidos.
        _vender("ml", "O3", "AAA", 1, 40.0)
        _vender("ml", "O3", "CCC", 1, 60.0)
        _vender("ml", "O4", "AAA", 1, 40.0)
        _vender("ml", "O4", "CCC", 1, 60.0)
        # BBB sozinho, em 2 e 3 unidades.
        _vender("ml", "O5", "BBB", 2, 20.0)
        _vender("ml", "O6", "BBB", 3, 30.0)
        # Kit vendido: é um produto só.
        _vender("ml", "O7", "KIT-AC", 1, 95.0)
        return S.kits_analise_payload(30, "", min_pedidos, 79.0)

    def test_carrinho_do_ml_e_um_pedido_so(self) -> None:
        r = self.cenario()["resumo"]
        self.assertEqual(r["pedidos"], 7, "O1 e O2 têm o mesmo pack_id: é um carrinho, um pedido")
        self.assertEqual(r["pedidos_multiproduto"], 4)

    def test_par_comprado_junto(self) -> None:
        d = self.cenario()
        par = next(p for p in d["pares"] if (p["a"], p["b"]) == ("AAA", "BBB"))
        self.assertEqual(par["pedidos_juntos"], 2)
        self.assertEqual(par["ml"], 1)
        self.assertEqual(par["shopee"], 1)
        self.assertEqual(par["pedidos_a"], 4)
        self.assertEqual(par["conf_a_b"], 50.0, "2 dos 4 pedidos com AAA levaram BBB")
        self.assertEqual(par["preco_kit"], 50.0)
        self.assertEqual(par["custo_kit"], 15.0)
        self.assertEqual(par["ja_existe"], "")

    def test_par_que_ja_e_kit_vem_marcado(self) -> None:
        par = next(p for p in self.cenario()["pares"] if (p["a"], p["b"]) == ("AAA", "CCC"))
        self.assertEqual(par["ja_existe"], "KIT-AC")

    def test_kit_vendido_nao_vira_componentes(self) -> None:
        d = self.cenario()
        self.assertFalse(any("KIT-AC" in (p["a"], p["b"]) for p in d["pares"]),
                         "kit vendido sozinho não forma par com nada")
        self.assertFalse(any(q["produto"] == "KIT-AC" for q in d["ticket_baixo"] + d["quantidade"]))

    def test_kit_de_quantidade(self) -> None:
        q = next(x for x in self.cenario()["quantidade"] if x["produto"] == "BBB")
        self.assertEqual(q["pedidos"], 4)
        self.assertEqual(q["pedidos_multi_un"], 3, "P1 (2 un), O5 (2 un) e O6 (3 un)")
        self.assertEqual(q["qtd_sugerida"], 2, "2 unidades é o que o cliente mais leva")
        self.assertEqual(q["dist_qtd"], {"1": 1, "2": 2, "3": 1, "4+": 0})

    def test_ticket_baixo(self) -> None:
        b = next(x for x in self.cenario()["ticket_baixo"] if x["produto"] == "BBB")
        self.assertEqual(b["preco_unit"], 10.0)
        self.assertEqual(b["unidades_para_limite"], 8, "8 x R$10 passa de R$79")

    def test_minimo_de_pedidos_esconde_par_raro(self) -> None:
        self.assertEqual(self.cenario(min_pedidos=3)["pares"], [])


if __name__ == "__main__":
    unittest.main()
