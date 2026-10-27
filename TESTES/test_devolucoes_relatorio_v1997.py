"""V19.97 — relatório de devoluções no formato da planilha do dono (27/10):
NF | NFD | NOME | SKU E QUANTIDADE | VALOR | MOTIVO.

- NF = nota de venda (ML / Shopee, guardada em devolucao_nf_v1995);
- NFD = nota de devolução = o documento do Apollo digitado ao tratar;
- VALOR = preço de venda × quantidade que voltou (reclamação do ML > vendas copiadas > venda lida no ML);
  sem preço nenhum, o valor da NF;
- MOTIVO = motivo da devolução: o admin escolhe; sem escolha, vem da reclamação do ML.
Exportação por LOTE (dono, 27/10): entram só as devoluções TRATADAS que nunca foram exportadas, de qualquer
mês; cada uma fica marcada com o lote; o histórico baixa o mesmo lote de novo (sem desfazer).
"""
from __future__ import annotations

import io
import json
import zipfile
from unittest import mock

from base import S, TesteNoromak, linha


def concluir(pedido: str, linhas: list[dict]) -> None:
    with S.DB_LOCK:
        for tabela in ("completed_orders", "order_completion_history"):
            S.DB.execute(f"INSERT OR REPLACE INTO {tabela}(order_key,order_id,completed_at,completed_by,payload) "
                         "VALUES(?,?,?,?,?)", (S.normalize(pedido), pedido, S.now_iso(), "admin", json.dumps(linhas)))
        S.DB.commit()


class RelatorioDevolucoes(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._DEVOLUCAO_EAN_CACHE.clear()
        S._DEVOLUCAO_NF_FALTA.clear()
        concluir("P100", [linha("P100", "AAA", 2, Cliente="LUCI ANGELA", Marketplace="MERCADO LIVRE",
                                VendaMercadoLivre="2000100"),
                          linha("P100", "BBB/CCC", 1, Cliente="LUCI ANGELA", Marketplace="MERCADO LIVRE",
                                VendaMercadoLivre="2000100")])
        S._devolucao_indexar_tudo(maximo=5)

    def nova(self, itens, chave="P100", quando="2026-11-05T13:00:00-03:00") -> int:
        dev_id = S.devolucao_registrar({"chave": chave, "itens": itens}, "joao", "todas")["id"]
        with S.DB_LOCK:
            S.DB.execute("UPDATE devolucao_v1988 SET recebido_em=? WHERE id=?", (quando, dev_id))
            S.DB.commit()
        return dev_id

    def ler(self, dev_id):
        with S.DB_LOCK:
            return S._devolucao_ler(dev_id)

    def reclamacao(self, itens=None, motivo="PDD9939", nome="repentant_buyer"):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_reclamacao_v1888(claim_id,order_id,motivo_id,tem_devolucao,itens_json) "
                         "VALUES('C1','2000100',?,1,?)", (motivo, json.dumps(itens or [])))
            S.DB.execute("INSERT OR REPLACE INTO ml_reclamacao_motivo_v1888(motivo_id,nome,detalhe,buscado_em) "
                         "VALUES(?,?,'x','x')", (motivo, nome))
            S.DB.commit()

    def nf(self, order_key="P100", numero="71137"):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO devolucao_nf_v1995(order_key,nf_json,lido_em) VALUES(?,?,'x')",
                         (order_key, json.dumps({"numero": numero, "serie": "1", "chave": "", "valor": 115.6})))
            S.DB.commit()

    RUIM = {"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "avariado"}

    def test_motivo_do_ml_e_o_do_admin_vence(self):
        self.reclamacao(nome="repentant_buyer")
        dev = self.nova([self.RUIM])
        self.assertEqual(S.devolucao_motivo(self.ler(dev))[1:], ("Comprador se arrependeu", "ml"))
        S.devolucao_salvar_valores(dev, {"motivo_dev": "falha_entrega"}, self.ler(dev)["versao"], "admin")
        self.assertEqual(S.devolucao_motivo(self.ler(dev)), ("falha_entrega", "Falha na entrega", "admin"))
        with self.assertRaises(ValueError):
            S.devolucao_salvar_valores(dev, {"motivo_dev": "xyz"}, self.ler(dev)["versao"], "admin")

    def test_motivos_do_ml_conhecidos(self):
        for nome, esperado in (("uncompats_item_with_vehicle_acc", "Incompatível com o veículo"),
                               ("broken_item", "Produto chegou quebrado ou danificado"),
                               ("missing_parts", "Pacote chegou com itens faltando")):
            self.assertEqual(S._devolucao_motivo_do_ml(nome)[1], esperado, nome)
        self.assertEqual(S._devolucao_motivo_do_ml("algo_novo")[0], "outro")

    def test_valor_pelo_preco_da_reclamacao_e_kit_dividido(self):
        self.reclamacao(itens=[{"sku": "AAA", "qtd": 2, "preco": 57.8}, {"sku": "BBB/CCC", "qtd": 1, "preco": 20.0}])
        dev = self.nova([self.RUIM, {"sku": "BBB", "qtd": 1, "estado": "bom"}])
        v = S.devolucao_valor_venda(self.ler(dev))
        self.assertEqual((v["por_item"]["AAA"], v["por_item"]["BBB"], v["total"], v["origem"]), (57.8, 10.0, 67.8, "venda"))

    def test_valor_pelas_vendas_copiadas_e_sem_preco_usa_a_nf(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,title,"
                         "qty,amount,status,updated_at) VALUES('ml','2000100','L1','','2026-10-01T10:00','2026-10-01','AAA',"
                         "'','t',2,100,'paid','x')")
            S.DB.commit()
        dev = self.nova([self.RUIM])
        self.assertEqual(S.devolucao_valor_venda(self.ler(dev))["total"], 50.0)
        concluir("P300", [linha("P300", "ZZZ", 1, Marketplace="MERCADO LIVRE")])
        S._devolucao_indexar_tudo(maximo=5)
        self.nf("P300", "70903")
        dev2 = self.nova([{"sku": "ZZZ", "qtd": 1, "estado": "bom"}], chave="P300")
        v = S.devolucao_valor_venda(self.ler(dev2))
        self.assertEqual((v["total"], v["origem"]), (115.6, "nf"))

    def test_linhas_no_formato_da_planilha_e_xlsx(self):
        self.reclamacao(itens=[{"sku": "AAA", "qtd": 2, "preco": 57.8}], nome="broken_item")
        self.nf()
        dev = self.nova([{"sku": "AAA", "qtd": 2, "estado": "ruim", "motivo": "avariado"}])
        S.devolucao_tratar(dev, "72265", self.ler(dev)["versao"], "admin")
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("HTTP 404")):
            cab, linhas = S.devolucoes_relatorio_linhas([dev])
        self.assertEqual(cab, ["NF", "NFD", "NOME", "SKU E QUANTIDADE", "VALOR", "MOTIVO"])
        self.assertEqual(linhas, [["71137", "72265", "LUCI ANGELA", "AAA / 2 UNIDADES", 115.6,
                                   "Produto chegou quebrado ou danificado"]])
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("HTTP 404")):
            lote = S.devolucao_exportar("admin", "todas")
            conteudo = S.devolucoes_relatorio_xlsx(lote["id"], "todas")
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            folha = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("LUCI ANGELA", folha)
        self.assertIn("AAA / 2 UNIDADES", folha)

    def tratar(self, dev, nfd):
        S.devolucao_salvar_valores(dev, {"frete": 0}, self.ler(dev)["versao"], "admin")
        S.devolucao_tratar(dev, nfd, self.ler(dev)["versao"], "admin")

    def test_lote_so_com_tratadas_nunca_exportadas(self):
        a = self.nova([self.RUIM])
        b = self.nova([self.RUIM], quando="2026-10-02T10:00:00-03:00")   # outro mês: entra igual
        self.nova([{"sku": "BBB", "qtd": 1, "estado": "bom"}])           # a tratar: fica de fora
        self.tratar(a, "72265")
        self.tratar(b, "72266")
        lote = S.devolucao_exportar("admin", "todas")
        self.assertEqual(sorted(lote["devolucoes"]), sorted([a, b]))
        self.assertEqual(self.ler(a)["exportacao_id"], lote["id"])
        with self.assertRaises(ValueError):          # clicar de novo não duplica
            S.devolucao_exportar("admin", "todas")
        c = self.nova([{"sku": "CCC", "qtd": 1, "estado": "bom"}])
        self.tratar(c, "72267")
        lote2 = S.devolucao_exportar("admin", "todas")
        self.assertEqual(lote2["devolucoes"], [c])
        self.assertEqual(lote2["id"], lote["id"] + 1)
        hist = S.devolucoes_exportacoes("todas")
        self.assertEqual([h["id"] for h in hist], [lote2["id"], lote["id"]])
        self.assertEqual(hist[1]["devolucoes"], 2)
        # baixar de novo: as mesmas devoluções, sem lote novo
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("HTTP 404")):
            self.assertTrue(S.devolucoes_relatorio_xlsx(lote["id"], "todas")[:2] == b"PK")
        self.assertEqual(len(S.devolucoes_exportacoes("todas")), 2)
        self.assertEqual(sorted(S.devolucoes_lote_ids(lote["id"], "todas")), sorted([a, b]))

    def test_lote_por_filial(self):
        concluir("P200", [linha("P200", "AAA", 1, Casa="bauru", Marketplace="MERCADO LIVRE")])
        S._devolucao_indexar_tudo(maximo=5)
        a = self.nova([self.RUIM])
        b = self.nova([self.RUIM], chave="P200")
        self.tratar(a, "1")
        self.tratar(b, "2")
        lote = S.devolucao_exportar("admin", "bauru")
        self.assertEqual(lote["devolucoes"], [b])
        with self.assertRaises(PermissionError):
            S.devolucoes_lote_ids(lote["id"], "aracatuba")
        self.assertEqual(S.devolucao_exportar("admin", "todas")["devolucoes"], [a])

    def test_rota_do_relatorio_so_do_admin(self):
        dev = self.nova([self.RUIM])
        st, corpo = self.cliente.pedir("POST", "/api/devolucoes/exportar", {})
        self.assertEqual((st, corpo.get("code")), (409, "NADA_A_EXPORTAR"))
        self.tratar(dev, "72265")
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("HTTP 404")):
            st, corpo = self.cliente.pedir("POST", "/api/devolucoes/exportar", {})
        self.assertEqual(st, 200, corpo)
        lote = corpo["lote"]
        st, corpo = self.cliente.pedir("GET", "/api/devolucoes/exportacoes")
        self.assertEqual((st, corpo["exportacoes"][0]["id"]), (200, lote))
        import urllib.request
        req = urllib.request.Request(self.url_base + f"/api/devolucoes/relatorio.xlsx?lote={lote}",
                                     headers={"Cookie": self.cliente.cookie})
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("HTTP 404")):
            with urllib.request.urlopen(req, timeout=30) as r:
                self.assertEqual(r.status, 200)
                self.assertIn("spreadsheetml", r.headers.get("Content-Type"))
                self.assertTrue(r.read()[:2] == b"PK" or r.headers.get("Content-Encoding") == "gzip")


if __name__ == "__main__":
    import unittest
    unittest.main()
