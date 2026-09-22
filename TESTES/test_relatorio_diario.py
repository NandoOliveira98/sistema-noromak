"""V18.13 — agente de relatório diário de vendas por e-mail.

O dono pediu (22/09/2026) um resumo diário às 00:01 com tudo que vendeu, por
e-mail, SEM IA (custo zero). Os números vêm de vendas_v1799; o e-mail sai por
smtplib (biblioteca padrão). Este teste valida a montagem dos dados do dia e que,
sem SMTP configurado, o envio falha com mensagem clara (não manda nada torto).
"""

import os
import unittest

from base import S, reiniciar_banco


def setUpModule():
    reiniciar_banco()


class RelatorioDiario(unittest.TestCase):
    def _semear(self, dia):
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM vendas_v1799")
            linhas = [
                ("A", "ml", "SKU1", "Produto Um", 2, 50.0),
                ("B", "ml", "SKU2", "Produto Dois", 1, 30.0),
                ("C", "shopee", "SKU1", "Produto Um", 3, 75.0),
            ]
            for oid, mp, sku, titulo, qty, amt in linhas:
                S.DB.execute(
                    "INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mp, oid, oid + "-L", "", dia + "T10:00:00", dia, sku, "", titulo, qty, amt, "", S.now_iso()),
                )
            S.DB.commit()

    def test_dados_e_formato_do_dia(self):
        dia = "2026-09-20"
        self._semear(dia)
        d = S.relatorio_diario_dados(dia)
        self.assertEqual(d["dia"], dia)
        self.assertAlmostEqual(d["faturamento"], 155.0, places=2)  # 50 + 30 + 75
        self.assertEqual(d["pedidos"], 3)
        self.assertEqual(d["unidades"], 6)  # 2 + 1 + 3

        mp = {x["marketplace"]: x for x in d["por_marketplace"]}
        self.assertAlmostEqual(mp["ml"]["faturamento"], 80.0, places=2)
        self.assertAlmostEqual(mp["shopee"]["faturamento"], 75.0, places=2)

        # Produtos agrupados por marketplace+sku: ml/SKU1, ml/SKU2, shopee/SKU1.
        self.assertEqual(len(d["produtos"]), 3)

        html = S.relatorio_diario_html(d)
        self.assertIn("Vendas de 20/09/2026", html)
        self.assertIn("Produto Um", html)
        self.assertIn("Produto Dois", S.relatorio_diario_texto(d))

    def test_dia_sem_vendas(self):
        self._semear("2026-09-20")
        d = S.relatorio_diario_dados("2026-09-19")  # nada nesse dia
        self.assertEqual(d["faturamento"], 0.0)
        self.assertEqual(d["produtos"], [])
        # não pode quebrar ao formatar um dia vazio
        self.assertIn("Nenhuma venda", S.relatorio_diario_html(d))

    def test_envio_sem_config_da_erro_claro(self):
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_SENHA", "RELATORIO_EMAILS"):
            os.environ.pop(k, None)
        with self.assertRaises(RuntimeError) as ctx:
            S.relatorio_diario_enviar_email("assunto", "texto", "<b>html</b>")
        # Mensagem clara apontando o .env (falta SMTP ou RELATORIO_EMAILS).
        self.assertIn(".env", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
