"""V18.10 — custos REAIS de venda por marketplace na gaveta do Dashboard.

O dono pediu (21/09/2026) que a gaveta mostre os custos reais separados por
marketplace: Mercado Livre (tarifa de venda + Mercado Envios, de
ml_profitability_snapshot) e Shopee (comissão + taxa de serviço + frete e a
renda líquida do escrow, de get_escrow_detail). O escrow é cacheado em
shopee_escrow_v1810; aqui ele é pré-semeado para o teste não tocar em rede.

Valida a agregação por SKU no período: soma correta de tarifa/envios do ML e
uso do escrow_amount como renda líquida da Shopee.
"""

import time
import unittest

from base import S, reiniciar_banco


def setUpModule():
    reiniciar_banco()


class CustosReaisPorMarketplace(unittest.TestCase):
    SKU = "0893764272"

    def _semear(self):
        hoje = S.datetime.now(S.BR_TZ).date().isoformat()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM vendas_v1799")
            S.DB.execute("DELETE FROM ml_profitability_snapshot")
            S.DB.execute("DELETE FROM shopee_escrow_v1810")
            for oid, mp, qty, amt in [("MLA", "ml", 1, 25.90), ("MLB", "ml", 1, 25.90), ("SPA", "shopee", 1, 25.90)]:
                S.DB.execute(
                    "INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mp, oid, oid + "-L", "", hoje + "T10:00:00", hoje, self.SKU, "", "Higienizador", qty, amt, "", S.now_iso()),
                )
            for oid in ("MLA", "MLB"):
                S.DB.execute(
                    "INSERT INTO ml_profitability_snapshot(order_id,sku,unit_cost,cost_source,sale_amount,ml_fee,seller_shipping_cost,other_ml_cost,captured_at,raw_summary)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (oid, self.SKU, 14.80, "apollo", 25.90, 3.11, 7.15, 0, S.now_iso(), ""),
                )
            # escrow Shopee já em cache e fresco: renda líquida 15.81 (o print do dono)
            S.DB.execute(
                "INSERT INTO shopee_escrow_v1810(order_sn,selling_price,escrow_amount,commission_fee,service_fee,transaction_fee,shipping_net,order_income_json,fetched_at,fetched_epoch,erro)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,'')",
                ("SPA", 25.90, 15.81, 4.66, 5.43, 0.0, 0.0, "{}", S.now_iso(), int(time.time())),
            )
            S.DB.commit()

    def test_agrega_ml_e_shopee_reais(self):
        self._semear()
        r = S.dashboard_produto_custos(self.SKU, 30)
        self.assertTrue(r["ok"], r)
        ml, sp = r["ml"], r["shopee"]

        # Mercado Livre: 2 pedidos, tarifa e envios somados (do snapshot).
        self.assertEqual(ml["pedidos"], 2)
        self.assertEqual(ml["unidades"], 2)
        self.assertAlmostEqual(ml["vendas"], 51.80, places=2)
        self.assertAlmostEqual(ml["tarifa"], 6.22, places=2)
        self.assertAlmostEqual(ml["envios"], 14.30, places=2)
        self.assertAlmostEqual(ml["renda"], 31.28, places=2)  # 51.80 - 6.22 - 14.30
        self.assertEqual(ml["com_dado"], 2)

        # Shopee: renda líquida vem do escrow (autoridade).
        self.assertEqual(sp["pedidos"], 1)
        self.assertAlmostEqual(sp["vendas"], 25.90, places=2)
        self.assertAlmostEqual(sp["renda"], 15.81, places=2)
        self.assertAlmostEqual(sp["comissao"], 4.66, places=2)
        self.assertEqual(sp["com_dado"], 1)

    def test_sku_sem_venda_no_periodo(self):
        self._semear()
        r = S.dashboard_produto_custos("NAO-EXISTE", 30)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["ml"])
        self.assertIsNone(r["shopee"])

    def test_escrow_parse_usa_escrow_amount_como_renda(self):
        parsed = S._shopee_escrow_parse({
            "order_selling_price": 25.90, "escrow_amount": 15.81,
            "commission_fee": 4.66, "service_fee": 5.43, "seller_transaction_fee": 0.52,
            "actual_shipping_fee": 14.48, "shopee_shipping_rebate": 14.48, "buyer_paid_shipping_fee": 0.0,
        })
        self.assertAlmostEqual(parsed["escrow_amount"], 15.81, places=2)
        self.assertAlmostEqual(parsed["shipping_net"], 0.0, places=2)  # 14.48 - 14.48


if __name__ == "__main__":
    unittest.main()
