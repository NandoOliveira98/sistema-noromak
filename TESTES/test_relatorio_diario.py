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


class RelatorioFrescor(unittest.TestCase):
    """V18.22: em 22/09/2026 o e-mail das 00:01 anunciou R$ 3.775,37 enquanto o
    dia fechou em R$ 6.814,47 — a sincronização do ML tinha parado às 19h e o
    relatório publicou o número parcial sem nenhum aviso."""

    DIA = "2026-09-22"

    def setUp(self):
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM vendas_v1799")
            S.DB.commit()

    def _venda(self, marketplace, order_id, valor):
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (marketplace, order_id, order_id, self.DIA + "T10:00:00-03:00", self.DIA,
                 "SKU1", 1, valor, S.now_iso()),
            )
            S.DB.commit()

    def test_a_sincronizacao_velha_gera_aviso(self):
        self._venda("ml", "P1", 100.0)
        # última sincronização ANTES do fim do dia = dado possivelmente parcial
        S._vendas_meta("ultima_sincronizacao_ml", self.DIA + "T19:00:00-03:00")
        S._vendas_meta("ultimo_erro_ml", "")
        aviso = S.relatorio_diario_frescor(self.DIA)
        self.assertIn("Mercado Livre", aviso)
        self.assertIn("sem sincronizar", aviso)

    def test_b_erro_na_sincronizacao_gera_aviso(self):
        self._venda("ml", "P2", 50.0)
        S._vendas_meta("ultima_sincronizacao_ml", "2026-09-23T09:00:00-03:00")
        S._vendas_meta("ultimo_erro_ml", "token expirado")
        self.assertIn("falhou", S.relatorio_diario_frescor(self.DIA))

    def test_c_sincronizacao_em_dia_nao_avisa(self):
        self._venda("ml", "P3", 70.0)
        S._vendas_meta("ultimo_erro_ml", "")
        S._vendas_meta("ultima_sincronizacao_ml", "2026-09-23T08:00:00-03:00")
        self.assertEqual(S.relatorio_diario_frescor(self.DIA), "")

    def test_d_aviso_aparece_no_email(self):
        dados = S.relatorio_diario_dados(self.DIA)
        dados["aviso"] = "Mercado Livre: sem sincronizar desde o fim do dia"
        html = S.relatorio_diario_html(dados)
        self.assertIn("pode estar incompleto", html)
        self.assertIn("sem sincronizar", html)
        self.assertIn("ATENCAO", S.relatorio_diario_texto(dados))
        # sem aviso, nada de banner
        dados["aviso"] = ""
        self.assertNotIn("pode estar incompleto", S.relatorio_diario_html(dados))
