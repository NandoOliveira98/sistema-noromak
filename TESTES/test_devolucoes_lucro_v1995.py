"""V19.95 — devoluções no lucro (decisões do dono, 27/10; desenho em
docs/superpowers/specs/2026-10-27-devolucoes-lucro-design.md).

- perda = custo (Araçatuba) das peças com destino "perda" + frete da devolução; o valor da venda não entra
  (a venda cancelada já sai do faturamento);
- destino da peça vem do estado (bom = estoque, ruim = perda) e o admin troca (garantia, desconto);
- frete do ML pela reclamação; o digitado vence; sem frete ou sem custo = incompleta, nunca zero;
- custo fica congelado quando a devolução é tratada;
- conta no mês em que a peça chegou; "a confirmar" até ser tratada; só a partir de 01/11/2026;
- o Financeiro desconta a linha Devoluções do lucro (só Araçatuba, como o resto do Financeiro).
"""
from __future__ import annotations

import json
from unittest import mock

from base import S, TesteNoromak, linha


def concluir(pedido: str, linhas: list[dict]) -> None:
    with S.DB_LOCK:
        for tabela in ("completed_orders", "order_completion_history"):
            S.DB.execute(f"INSERT OR REPLACE INTO {tabela}(order_key,order_id,completed_at,completed_by,payload) "
                         "VALUES(?,?,?,?,?)", (S.normalize(pedido), pedido, S.now_iso(), "admin", json.dumps(linhas)))
        S.DB.commit()


CUSTOS = {"AAA": 10.0, "BBB": 4.0}


def custo_falso(sku, ctx, congelar=False):
    v = CUSTOS.get(S.normalize(sku))
    return (v, "apollo") if v is not None else (None, "")


class DevolucoesNoLucro(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._DEVOLUCAO_EAN_CACHE.clear()
        CUSTOS.clear()
        CUSTOS.update({"AAA": 10.0, "BBB": 4.0})
        concluir("P100", [linha("P100", "AAA", 2, Cliente="José", Marketplace="MERCADO LIVRE",
                                VendaMercadoLivre="2000100"),
                          linha("P100", "BBB", 1, Cliente="José", Marketplace="MERCADO LIVRE",
                                VendaMercadoLivre="2000100")])
        self.p1 = mock.patch.object(S, "_financeiro_custo_aracatuba", side_effect=custo_falso)
        self.p1.start()

    def tearDown(self) -> None:
        self.p1.stop()
        super().tearDown()

    def nova(self, itens, chave="P100", quando="2026-11-05T13:00:00-03:00") -> int:
        dev_id = S.devolucao_registrar({"chave": chave, "itens": itens}, "joao", "todas")["id"]
        with S.DB_LOCK:
            S.DB.execute("UPDATE devolucao_v1988 SET recebido_em=? WHERE id=?", (quando, dev_id))
            S.DB.commit()
        return dev_id

    def ler(self, dev_id):
        with S.DB_LOCK:
            return S._devolucao_ler(dev_id)

    def valores(self, dev_id):
        return S.devolucao_valores(self.ler(dev_id))

    RUIM = {"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "avariado"}
    BOM = {"sku": "BBB", "qtd": 1, "estado": "bom"}

    def test_perda_e_peca_ruim_mais_frete_e_sem_frete_fica_incompleta(self):
        dev = self.nova([self.RUIM, self.BOM])
        v = self.valores(dev)
        self.assertEqual((v["perda_pecas"], v["frete"], v["perda"], v["incompleta"]), (10.0, None, None, True))
        self.assertEqual([i["destino"] for i in self.ler(dev)["itens"]], ["perda", "estoque"])
        d = self.ler(dev)
        S.devolucao_salvar_valores(dev, {"frete": 5}, d["versao"], "admin")
        v = self.valores(dev)
        self.assertEqual((v["perda_pecas"], v["frete"], v["frete_origem"], v["perda"], v["incompleta"]),
                         (10.0, 5.0, "digitado", 15.0, False))

    def test_frete_zero_digitado_e_zero_de_verdade(self):
        dev = self.nova([self.BOM])
        S.devolucao_salvar_valores(dev, {"frete": 0}, self.ler(dev)["versao"], "admin")
        v = self.valores(dev)
        self.assertEqual((v["perda"], v["incompleta"]), (0.0, False))

    def test_frete_do_ml_pela_reclamacao_e_o_digitado_vence(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_reclamacao_v1888(claim_id,order_id,frete_devolucao,tem_devolucao) "
                         "VALUES('C1','2000100',23.5,1)")
            S.DB.commit()
        S._devolucao_indexar_tudo(maximo=5)
        dev = self.nova([self.RUIM])
        v = self.valores(dev)
        self.assertEqual((v["frete"], v["frete_origem"], v["perda"]), (23.5, "ml", 33.5))
        S.devolucao_salvar_valores(dev, {"frete": 12}, self.ler(dev)["versao"], "admin")
        self.assertEqual(self.valores(dev)["frete"], 12.0)

    def test_destino_garantia_nao_e_perda_e_vai_para_o_historico(self):
        dev = self.nova([self.RUIM])
        d = self.ler(dev)
        S.devolucao_salvar_valores(dev, {"frete": 0, "itens": [{"id": d["itens"][0]["id"], "destino": "garantia"}]},
                                   d["versao"], "admin")
        d = self.ler(dev)
        self.assertEqual(d["itens"][0]["destino"], "garantia")
        self.assertEqual(self.valores(dev)["perda"], 0.0)
        self.assertIn("valores", [e["acao"] for e in d["eventos"]])
        with self.assertRaises(ValueError):
            S.devolucao_salvar_valores(dev, {"itens": [{"id": d["itens"][0]["id"], "destino": "lixo"}]},
                                       d["versao"], "admin")

    def test_sem_custo_fica_incompleta_e_o_admin_digita(self):
        CUSTOS.pop("AAA")
        dev = self.nova([self.RUIM])
        S.devolucao_salvar_valores(dev, {"frete": 0}, self.ler(dev)["versao"], "admin")
        v = self.valores(dev)
        self.assertEqual((v["perda"], v["incompleta"]), (None, True))
        d = self.ler(dev)
        S.devolucao_salvar_valores(dev, {"itens": [{"id": d["itens"][0]["id"], "custo": 7.5}]}, d["versao"], "admin")
        v = self.valores(dev)
        self.assertEqual((v["perda"], v["incompleta"], v["itens"][0]["custo_origem"]), (7.5, False, "manual"))

    def test_custo_congelado_ao_tratar(self):
        dev = self.nova([self.RUIM])
        d = self.ler(dev)
        S.devolucao_salvar_valores(dev, {"frete": 0}, d["versao"], "admin")
        S.devolucao_tratar(dev, "NF 1", self.ler(dev)["versao"], "admin")
        CUSTOS["AAA"] = 99.0
        self.assertEqual(self.valores(dev)["perda"], 10.0)

    def test_resumo_do_mes_compara_com_o_anterior_e_ranking(self):
        S._devolucao_vender = None
        with S.DB_LOCK:
            for i, (dia, sku, q) in enumerate([("2026-11-02", "AAA", 8), ("2026-11-03", "BBB", 2),
                                               ("2026-10-10", "AAA", 5)]):
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,"
                             "anuncio,title,qty,amount,status,updated_at) VALUES('ml',?,?,'',?,?,?,'','t',?,10,'paid','x')",
                             (f"V{i}", f"V{i}", dia + "T10:00:00", dia, sku, q))
            S.DB.commit()
        a = self.nova([self.RUIM])
        S.devolucao_salvar_valores(a, {"frete": 5}, self.ler(a)["versao"], "admin")
        self.nova([self.BOM], quando="2026-10-20T10:00:00-03:00")
        r = S.devolucoes_resumo("2026-11", "todas")
        m = r["mes"]
        self.assertEqual((m["devolucoes"], m["pecas"], m["perda"], m["a_tratar"], m["vendidas"]), (1, 1, 15.0, 1, 10))
        self.assertEqual(m["taxa"], 10.0)            # 1 devolvida ÷ 10 vendidas
        self.assertEqual(r["anterior"]["devolucoes"], 1)
        self.assertEqual(r["anterior"]["periodo"], "2026-10")
        top = r["ranking"][0]
        self.assertEqual((top["sku"], top["devolvidas"], top["vendidas"], top["perda"]), ("AAA", 1, 8, 10.0))

    def test_financeiro_desconta_devolucoes_do_mes_da_chegada(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,"
                         "title,qty,amount,status,updated_at) VALUES('ml','V9','V9','','2026-11-05T10:00:00','2026-11-05',"
                         "'AAA','','t',1,100,'paid','x')")
            S.DB.commit()
        sem = S.financeiro_payload("2026-11-01", "2026-11-30")["totais"]["lucro_liquido"]
        a = self.nova([self.RUIM])
        S.devolucao_salvar_valores(a, {"frete": 5}, self.ler(a)["versao"], "admin")
        self.nova([self.RUIM], quando="2026-10-20T10:00:00-03:00")              # outubro: fora
        d = S.financeiro_payload("2026-11-01", "2026-11-30")
        t = d["totais"]
        self.assertEqual(t["devolucoes"], 15.0)
        self.assertAlmostEqual(t["lucro_liquido"], sem - 15.0, places=2)
        self.assertAlmostEqual(sum(x["lucro_liquido"] for x in d["por_dia"]), t["lucro_liquido"], places=2)
        self.assertEqual(d["cobertura"]["devolucoes_a_confirmar"], 15.0)
        self.assertEqual(d["por_marketplace"]["ml"]["devolucoes"], 15.0)
        # antes de 01/11 nada entra, mesmo com devolução no período
        self.assertEqual(S.financeiro_payload("2026-10-01", "2026-10-31")["totais"]["devolucoes"], 0.0)

    def test_financeiro_sem_bauru_e_incompleta_visivel(self):
        concluir("P200", [linha("P200", "AAA", 1, Casa="bauru", Marketplace="MERCADO LIVRE")])
        self.nova([self.RUIM], chave="P200")                                    # Bauru: fora do Financeiro
        self.nova([self.RUIM])                                                  # sem frete: incompleta
        d = S.financeiro_payload("2026-11-01", "2026-11-30")
        self.assertEqual(d["totais"]["devolucoes"], 10.0)
        self.assertEqual(d["cobertura"]["devolucoes_incompletas"], 1)

    def test_rotas_so_do_admin(self):
        dev = self.nova([self.RUIM])
        st, corpo = self.cliente.pedir("GET", "/api/devolucoes/resumo?mes=2026-11")
        self.assertEqual(st, 200, corpo)
        st, corpo = self.cliente.pedir("POST", "/api/devolucoes/valores",
                                       {"id": dev, "versao": self.ler(dev)["versao"], "frete": 3})
        self.assertEqual(st, 200, corpo)
        st, corpo = self.cliente.pedir("POST", "/api/devolucoes/valores", {"id": dev, "versao": 1, "frete": 3})
        self.assertEqual(st, 409, corpo)
        st, lista = self.cliente.pedir("GET", "/api/devolucoes?mes=2026-11")
        self.assertEqual(lista["devolucoes"][0]["valores"]["perda"], 13.0)
        with S.DB_LOCK:
            S.upsert_app_user("op", "Op", "operador", True, "senha-teste-123", "teste",
                              permissions={"conferencia": True, "devolucao": True})
            S.DB.commit()
        from base import Cliente
        op = Cliente(self.url_base)
        op.entrar("op", "senha-teste-123", area="portal")
        self.assertEqual(op.pedir("GET", "/api/devolucoes/resumo?mes=2026-11")[0], 403)
        self.assertEqual(op.pedir("POST", "/api/devolucoes/valores", {"id": dev, "versao": 9, "frete": 1})[0], 403)


    # --- NF de venda (dono, 27/10): /users/{seller}/invoices/orders/{order} — número, série, chave, data, valor.
    # Conferido em produção: Full série 2 (ML emite) e Coleta série 1. Lida uma vez e guardada. ---

    def nota_ml(self, chamadas, respostas):
        def api(caminho, *a, **k):
            chamadas.append((S.ml_casa_atual(), caminho))
            r = respostas.get(caminho)
            if r is None:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return r
        return mock.patch.object(S, "ml_api_get", side_effect=api)

    def test_nf_de_venda_lida_uma_vez_e_guardada(self):
        dev = self.nova([self.RUIM])
        S._devolucao_indexar_tudo(maximo=5)
        chamadas = []
        resp = {"/users/99/invoices/orders/2000100": {
            "id": 7229947959, "status": "authorized", "invoice_number": 71680, "invoice_series": "1",
            "issued_date": "2026-10-15T10:53:43.219Z", "amount": 89.9,
            "attributes": {"invoice_key": "35261014346930000106550010000716801007872323"}}}
        with self.nota_ml(chamadas, resp),                 mock.patch.object(S, "ml_integration_row", return_value={"seller_id": "99", "access_token": "t"}):
            r = S.devolucao_nf(dev)
            self.assertEqual((r["nf"]["numero"], r["nf"]["serie"], r["nf"]["chave"][:6], r["nf"]["valor"]),
                             ("71680", "1", "352610", 89.9))
            n = len(chamadas)
            self.assertEqual(S.devolucao_nf(dev)["nf"]["numero"], "71680")
        self.assertEqual(len(chamadas), n)          # guardada: não lê o ML de novo

    def test_nf_sem_nota_e_shopee(self):
        dev = self.nova([self.RUIM])
        S._devolucao_indexar_tudo(maximo=5)
        with self.nota_ml([], {}),                 mock.patch.object(S, "ml_integration_row", return_value={"seller_id": "99", "access_token": "t"}):
            r = S.devolucao_nf(dev)
        self.assertIsNone(r["nf"])
        self.assertTrue(r["motivo"])
        concluir("SH1", [linha("SH1", "AAA", 1, Marketplace="SHOPEE")])
        dev2 = self.nova([self.RUIM], chave="SH1")
        r = S.devolucao_nf(dev2)
        self.assertIsNone(r["nf"])
        self.assertIn("Shopee", r["motivo"])
        st, corpo = self.cliente.pedir("GET", f"/api/devolucoes/nf?id={dev2}")
        self.assertEqual(st, 200, corpo)

if __name__ == "__main__":
    import unittest
    unittest.main()
