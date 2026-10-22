"""V19.77 — custo do produto no Financeiro (decisões do dono, 22/10).

6007.003.315.00.8: vendido em 07/10 e 16/10 com custo unitário de R$ 13,02 em Araçatuba; o estoque
zerou, o Precificador ficou com 0 e o Financeiro puxou R$ 39,41 — o último custo lido no Apollo de
BAURU (custos_conhecidos_v1807 mistura as filiais). Regras:
  * custo é sempre o de ARAÇATUBA (revenda 1), nunca o de outra filial;
  * o custo de Araçatuba é CONGELADO na venda quando ela entra (até 1 dia depois);
  * venda antiga sem custo congelado = melhor estimativa, marcada "estimado"; o custo digitado vence;
  * SKU com barra que é um código inteiro ("PSH367/80") não vira duas peças;
  * código fora do Precificador com custo do Apollo de Araçatuba guardado tem custo;
  * o escrow da Shopee é buscado sozinho (sem o botão), senão a linha fica "parcial".
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from base import S, TesteNoromak

HOJE = date.today()


def _vender(order_id: str, sku: str, qty: int, valor: float, dia: str, marketplace: str = "ml") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,"
            "sku,anuncio,title,qty,amount,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (marketplace, order_id, sku, "", dia + "T10:00:00-03:00", dia, sku, "", f"Produto {sku}", qty, valor,
             "paid", S.now_iso()))
        S.DB.commit()


def _precificador(produtos: list[dict]) -> None:
    atual, _, _ = S.get_storage("precificador_online_v1")
    S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                   actor="admin_teste", actor_role="admin", source_screen="Teste",
                   change_method="Teste automatizado", client_ip="127.0.0.1")


def _filiais(ara: float, bauru: float) -> list[dict]:
    return [{"revenda": 1, "casa": "Araçatuba", "custo_medio": ara}, {"revenda": 2, "casa": "Ribeirão", "custo_medio": 0},
            {"revenda": 3, "casa": "Bauru", "custo_medio": bauru}]


def _apollo(codigo: str, custos: dict[str, float]) -> None:
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO custo_apollo_v1931(codigo,custos_json,data) VALUES(?,?,?)",
                     (codigo, json.dumps(custos), S.now_iso()))
        S.DB.commit()


def _conhecido(codigo: str, auto: float | None = None, manual: float | None = None) -> None:
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO custos_conhecidos_v1807(codigo,custo_auto,origem_auto,data_auto,custo_manual,"
                     "data_manual,manual_por) VALUES(?,?,?,?,?,?,?)",
                     (codigo, auto, "Apollo" if auto else "", S.now_iso() if auto else "", manual,
                      S.now_iso() if manual else "", "admin" if manual else ""))
        S.DB.commit()


def _sku(payload: dict, sku: str) -> dict:
    return next(s for s in payload["por_sku"] if s["sku"] == sku.upper())


def _periodo() -> dict:
    return S.financeiro_payload((HOJE - timedelta(days=40)).isoformat(), HOJE.isoformat())


class CustoDoFinanceiro(TesteNoromak):

    def test_nunca_usa_o_custo_de_bauru(self):
        # o caso real: Precificador 0, Araçatuba zerado, último custo visto = o de Bauru
        _precificador([{"sku": "6007.003.315.00.8", "custo": 0, "filialCusto": 1, "custosFiliais": _filiais(0, 39.41)}])
        _apollo("6007.003.315.00.8", {"3": 39.41})
        _conhecido("6007.003.315.00.8", auto=39.41)
        _vender("V1", "6007.003.315.00.8", 2, 86.40, (HOJE - timedelta(days=10)).isoformat())
        s = _sku(_periodo(), "6007.003.315.00.8")
        self.assertTrue(s["sem_custo"])          # sem custo de Araçatuba: pendência, nunca 39,41

    def test_ultimo_custo_de_aracatuba_guardado(self):
        _precificador([{"sku": "6007.003.315.00.8", "custo": 0, "filialCusto": 1, "custosFiliais": _filiais(0, 39.41)}])
        _apollo("6007.003.315.00.8", {"1": 13.02, "3": 39.41})
        _vender("V1", "6007.003.315.00.8", 2, 86.40, (HOJE - timedelta(days=10)).isoformat())
        s = _sku(_periodo(), "6007.003.315.00.8")
        self.assertAlmostEqual(s["cmv"], 26.04)
        self.assertEqual(s["custo_origem"], "estimado")

    def test_precificador_com_custo_de_bauru_usa_o_de_aracatuba(self):
        _precificador([{"sku": "X1", "custo": 39.0, "filialCusto": 3, "custosFiliais": _filiais(13.0, 39.0)}])
        _vender("V1", "X1", 1, 50.0, HOJE.isoformat())
        self.assertAlmostEqual(_sku(_periodo(), "X1")["cmv"], 13.0)

    def test_custo_congelado_na_venda_nao_muda_depois(self):
        _precificador([{"sku": "X1", "custo": 13.02, "filialCusto": 1, "custosFiliais": _filiais(13.02, 39.41)}])
        _vender("V1", "X1", 1, 43.20, HOJE.isoformat())
        n = S.financeiro_custo_congelar()
        self.assertEqual(n, 1)
        # estoque zerou: Precificador vai a 0; depois volta com outro custo médio
        _precificador([{"sku": "X1", "custo": 0, "filialCusto": 1, "custosFiliais": _filiais(0, 39.41)}])
        s = _sku(_periodo(), "X1")
        self.assertAlmostEqual(s["cmv"], 13.02)
        self.assertEqual(s["custo_origem"], "venda")
        _precificador([{"sku": "X1", "custo": 20.0, "filialCusto": 1, "custosFiliais": _filiais(20.0, 39.41)}])
        self.assertAlmostEqual(_sku(_periodo(), "X1")["cmv"], 13.02)
        self.assertEqual(S.financeiro_custo_congelar(), 0)   # não congela de novo

    def test_venda_antiga_nao_e_congelada_e_o_digitado_vence(self):
        _precificador([{"sku": "X1", "custo": 20.0, "filialCusto": 1, "custosFiliais": _filiais(20.0, 0)}])
        _vender("V1", "X1", 1, 43.20, (HOJE - timedelta(days=10)).isoformat())
        self.assertEqual(S.financeiro_custo_congelar(), 0)
        s = _sku(_periodo(), "X1")
        self.assertEqual((s["cmv"], s["custo_origem"]), (20.0, "estimado"))
        _conhecido("X1", manual=13.02)
        s = _sku(_periodo(), "X1")
        self.assertEqual((s["cmv"], s["custo_origem"]), (13.02, "manual"))

    def test_sem_custo_nao_congela(self):
        _precificador([{"sku": "X1", "custo": 0, "filialCusto": 1, "custosFiliais": _filiais(0, 0)}])
        _vender("V1", "X1", 1, 43.20, HOJE.isoformat())
        self.assertEqual(S.financeiro_custo_congelar(), 0)
        _precificador([{"sku": "X1", "custo": 13.0, "filialCusto": 1, "custosFiliais": _filiais(13.0, 0)}])
        self.assertEqual(S.financeiro_custo_congelar(), 1)   # tenta de novo na rodada seguinte

    def test_sku_com_barra_que_e_um_codigo_inteiro(self):
        _precificador([{"sku": "PSH367/80", "custo": 60.0, "filialCusto": 1, "custosFiliais": _filiais(60.0, 0)}])
        _vender("V1", "PSH367/80", 1, 151.30, HOJE.isoformat())
        self.assertAlmostEqual(_sku(_periodo(), "PSH367/80")["cmv"], 60.0)

    def test_kit_a_barra_b_continua_um_de_cada(self):
        _precificador([{"sku": "A1", "custo": 10.0}, {"sku": "B1", "custo": 5.0}])
        _vender("V1", "A1/B1", 1, 40.0, HOJE.isoformat())
        self.assertAlmostEqual(_sku(_periodo(), "A1/B1")["cmv"], 15.0)

    def test_codigo_fora_do_precificador_com_custo_do_apollo_de_aracatuba(self):
        _precificador([{"sku": "OUTRO", "custo": 1.0}])
        _apollo("27200154", {"1": 8.5})
        _vender("V1", "27200154", 2, 92.30, HOJE.isoformat())
        self.assertAlmostEqual(_sku(_periodo(), "27200154")["cmv"], 17.0)

    def test_escrow_da_shopee_buscado_sozinho(self):
        _precificador([{"sku": "X1", "custo": 10.0}])
        _vender("SP1", "X1", 1, 30.0, (HOJE - timedelta(days=2)).isoformat(), marketplace="shopee")
        _vender("SP2", "X1", 1, 30.0, (HOJE - timedelta(days=60)).isoformat(), marketplace="shopee")
        buscados = []
        salvo = S.shopee_escrow_lookup
        S.shopee_escrow_lookup = lambda sn, permitir_api=True: buscados.append(sn) or None
        try:
            S.financeiro_shopee_escrow_pendentes()
        finally:
            S.shopee_escrow_lookup = salvo
        self.assertEqual(buscados, ["SP1"])       # só os últimos 30 dias


if __name__ == "__main__":
    import unittest
    unittest.main()
