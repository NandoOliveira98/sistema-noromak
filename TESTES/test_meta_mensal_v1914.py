"""V19.14 — meta mensal de faturamento no Dashboard (todas as vendas do mês corrente)."""
import unittest
from datetime import date

from base import S, TesteNoromak

HOJE = date(2026, 10, 13)


class Meta(TesteNoromak):
    def venda(self, oid, dia, valor, tabela="vendas_v1799", conta=None):
        with S.DB_LOCK:
            if conta:
                S.DB.execute(f"INSERT INTO {tabela}(conta,marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES(?,'ml',?,'1',?,?,'A','M','t',1,?,'paid','')", (conta, oid, dia, dia, valor))
            else:
                S.DB.execute(f"INSERT INTO {tabela}(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES('ml',?,'1',?,?,'A','M','t',1,?,'paid','')", (oid, dia, dia, valor))
            S.DB.commit()

    def payload(self):
        with S.DB_LOCK:
            return S.meta_mensal_payload(HOJE)

    def test_sem_meta(self):
        p = self.payload()
        self.assertIsNone(p["meta"])
        self.assertIsNone(p["percentual"])

    def test_soma_todas_as_contas_do_mes(self):
        self.venda("a", "2026-10-01", 1000)
        self.venda("b", "2026-10-13", 500, "vendas_conta_v1898", "bauru")
        self.venda("c", "2026-09-30", 9999)                     # mês anterior fica fora
        S.meta_mensal_salvar("2026-10", "300.000", "admin")    # digitado à brasileira
        p = self.payload()
        self.assertEqual((p["meta"], p["faturado"]), (300000.0, 1500.0))
        self.assertEqual(p["percentual"], 0.5)
        self.assertEqual(p["dias_restantes"], 19)               # 13 a 31, hoje conta
        self.assertEqual(p["ritmo_necessario"], round(298500 / 19, 2))

    def test_mes_sem_meta_repete_a_anterior(self):
        S.meta_mensal_salvar("2026-09", 250000, "admin")
        p = self.payload()
        self.assertEqual(p["meta"], 250000.0)
        self.assertFalse(p["meta_do_mes"])
        S.meta_mensal_salvar("2026-10", "310000,50", "admin")
        self.assertEqual(self.payload()["meta"], 310000.5)

    def test_valor_invalido(self):
        for v in ("", "0", "abc", -5):
            with self.assertRaises(ValueError):
                S.meta_mensal_salvar("2026-10", v, "admin")
        with self.assertRaises(ValueError):
            S.meta_mensal_salvar("10/2026", 1000, "admin")

    def test_rota_e_tela(self):
        st, c = self.cliente.pedir("POST", "/api/dashboard/meta", {"mes": "2026-10", "valor": "300000"})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("GET", "/api/dashboard/meta")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["meta"], 300000.0)
        from base import RAIZ
        html = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="metaV1914"', html)
        self.assertNotIn("prompt(", html)   # V19.13: janela padrão no lugar do prompt()


if __name__ == "__main__":
    unittest.main()
