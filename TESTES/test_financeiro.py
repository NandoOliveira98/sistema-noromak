"""V18.30 — invariantes do Financeiro (faturamento, gastos e lucro).

Cada teste aqui trava uma regra de dinheiro. Se um deles cair, o dashboard está
mentindo sobre o lucro — que é o único motivo da tela existir:

  * imposto é 12% do valor da venda, sempre;
  * lucro bruto = faturamento − custo do produto;
  * lucro líquido desconta taxa, frete, Ads e imposto;
  * venda sem custo conhecido NÃO vira lucro (fica como pendência);
  * a tarifa é do PEDIDO: um pedido com duas linhas não paga duas tarifas;
  * pedido sem fatura sincronizada fica pendente em vez de virar taxa zero;
  * Ads é custo do período e não pode ser contado duas vezes (API + fatura);
  * Shopee: taxa = comissão + serviço + transação, frete = frete líquido do escrow.
"""

from __future__ import annotations

import json
import unittest

from base import S, TesteNoromak


def _vender(marketplace: str, order_id: str, sku: str, qty: int, valor: float,
            dia: str = "2026-09-10", line_key: str = "") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,"
            "sku,anuncio,title,qty,amount,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (marketplace, order_id, line_key or sku, "", dia + "T10:00:00-03:00", dia, sku, "",
             f"Produto {sku}", qty, valor, "paid", S.now_iso()),
        )
        S.DB.commit()


def _lancamento_fatura(order_id: str, valor: float, *, sub_type: str = "", transacao: str = "",
                       sale_fee_net: float | None = None, credito: bool = False,
                       detail_id: str = "", period_key: str = "2026-09-01",
                       detail_type: str = "") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO ml_billing_period_detail(period_key,document_type,detail_id,order_id,pack_id,"
            "shipment_id,detail_amount,detail_type,detail_sub_type,transaction_detail,debited_from_operation,"
            "sale_fee_net,sale_fee_gross,sale_fee_rebate,sale_fee_discount,is_credit,raw_json,synced_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (period_key, "CREDIT_NOTE" if credito else "BILL", detail_id or f"{order_id}-{sub_type}-{valor}",
             order_id, "", "", valor, (detail_type or ("BONUS" if credito else "CHARGE")), sub_type,
             transacao, "YES", sale_fee_net, None, None, None, 1 if credito else 0, "{}", S.now_iso()),
        )
        S.DB.commit()


def _escrow(order_sn: str, comissao: float, servico: float, transacao: float, frete: float,
            escrow_amount: float = 0.0, preco: float = 0.0, ams: float = 0.0) -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO shopee_escrow_v1810(order_sn,selling_price,escrow_amount,commission_fee,"
            "service_fee,transaction_fee,shipping_net,ams_commission,order_income_json,fetched_at,fetched_epoch,erro) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'')",
            (order_sn, preco, escrow_amount, comissao, servico, transacao, frete, ams, "{}",
             S.now_iso(), 9_999_999_999),
        )
        S.DB.commit()


def _ads(dia: str, custo: float, sku: str = "") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO ml_ads_dia_v1830(dia,cost,clicks,prints,units,total_amount,campanhas,updated_at) "
            "VALUES(?,?,0,0,0,0,1,?)", (dia, custo, S.now_iso()))
        if sku:
            S.DB.execute(
                "INSERT OR REPLACE INTO ml_ads_item_dia_v1830(dia,ad_group_id,campaign_id,item_id,sku,cost,"
                "clicks,prints,units,total_amount,updated_at) VALUES(?,?,?,?,?,?,0,0,0,0,?)",
                (dia, f"g-{sku}", "c1", f"MLB{sku}", sku, custo, S.now_iso()))
        S.DB.commit()


def _custos_precificador(itens: dict[str, float]) -> None:
    """Lista do Precificador: é ela que dá o custo (código Apollo -> custo)."""
    dados = {"products": [{"sku": codigo, "produto": f"Produto {codigo}", "custo": custo, "curva": "A"}
                          for codigo, custo in itens.items()]}
    atual, _, _ = S.get_storage("precificador_online_v1")
    S.save_storage("precificador_online_v1", atual, json.dumps(dados), False,
                   actor="admin_teste", actor_role="admin", source_screen="Teste",
                   change_method="Teste automatizado", client_ip="127.0.0.1")


class TesteFinanceiroCalculo(TesteNoromak):
    """A conta em si, com uma venda ML de duas linhas e todos os custos conhecidos."""

    def cenario(self) -> dict:
        _custos_precificador({"AAA": 30.0, "BBB": 20.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        _vender("ml", "ML1", "BBB", 1, 100.0)
        _lancamento_fatura("ML1", 22.0, sub_type="CV", transacao="Cobranca por venda", sale_fee_net=22.0)
        _lancamento_fatura("ML1", 18.0, sub_type="CXD", transacao="Mercado Envios")
        _ads("2026-09-10", 10.0)
        return S.financeiro_payload("2026-09-01", "2026-09-30")

    def test_faturamento_e_cmv(self) -> None:
        t = self.cenario()["totais"]
        self.assertEqual(t["faturamento"], 200.0)
        self.assertEqual(t["cmv"], 50.0, "CMV tem que ser a soma dos custos do Apollo das duas linhas")
        self.assertEqual(t["unidades"], 2)
        self.assertEqual(t["pedidos"], 1, "duas linhas do MESMO pedido não podem virar dois pedidos")

    def test_imposto_e_sempre_12_por_cento_da_venda(self) -> None:
        d = self.cenario()
        self.assertEqual(d["imposto_percent"], 12.0)
        self.assertEqual(d["totais"]["imposto"], 24.0)

    def test_tarifa_do_pedido_nao_e_cobrada_por_linha(self) -> None:
        t = self.cenario()["totais"]
        self.assertEqual(t["taxas"], 22.0, "a tarifa é do pedido: ratear entre as linhas não pode dobrar o valor")
        self.assertEqual(t["frete"], 18.0, "o frete da fatura também é do pedido")

    def test_lucro_bruto_e_liquido(self) -> None:
        t = self.cenario()["totais"]
        self.assertEqual(t["lucro_bruto"], 150.0, "bruto = faturamento - custo do produto")
        # 150 - 22 (taxa) - 18 (frete) - 10 (ads) - 24 (imposto)
        self.assertEqual(t["lucro_liquido"], 76.0)
        self.assertEqual(t["margem_bruta"], 75.0)
        self.assertEqual(t["margem_liquida"], 38.0)

    def test_soma_dos_dias_bate_com_o_total(self) -> None:
        d = self.cenario()
        soma = round(sum(x["lucro_liquido"] for x in d["por_dia"]), 2)
        self.assertEqual(soma, d["totais"]["lucro_liquido"], "o dia a dia tem que fechar com o total")

    def test_periodo_nao_traz_venda_de_fora(self) -> None:
        self.cenario()
        _vender("ml", "ML9", "AAA", 1, 500.0, dia="2026-08-15")
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["faturamento"], 200.0, "venda de agosto não pode entrar no período de setembro")


class TesteFinanceiroPendencias(TesteNoromak):
    """O que o sistema não sabe tem que aparecer como pendência — nunca como lucro."""

    def test_venda_sem_custo_nao_vira_lucro(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        _vender("ml", "ML2", "SEM-CADASTRO", 1, 100.0)
        _lancamento_fatura("ML1", 10.0, sub_type="CV", sale_fee_net=10.0)
        _lancamento_fatura("ML2", 10.0, sub_type="CV", sale_fee_net=10.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["cmv"], 30.0, "o custo desconhecido não pode ser estimado nem zerado")
        self.assertEqual(d["cobertura"]["faturamento_sem_custo"], 100.0)
        linha = next(x for x in d["por_sku"] if x["sku"] == "SEM-CADASTRO")
        self.assertTrue(linha["sem_custo"])
        pedido = next(x for x in d["pedidos"] if x["order_id"] == "ML2")
        self.assertIsNone(pedido["lucro_liquido"], "sem custo não existe lucro do pedido")

    def test_pedido_fora_da_fatura_fica_pendente(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["taxas"], 0.0)
        self.assertEqual(d["cobertura"]["faturamento_sem_taxa"], 100.0,
                         "taxa não conciliada é pendência; não pode virar taxa zero silenciosa")
        self.assertEqual(d["cobertura"]["pedidos_ml_conciliados"], 0)
        pedido = next(x for x in d["pedidos"] if x["order_id"] == "ML1")
        self.assertFalse(pedido["conciliado"])
        self.assertIsNone(pedido["taxa"])


class TesteFinanceiroAds(TesteNoromak):
    """Ads é custo do período, vem da API de Publicidade e entra uma vez só."""

    def test_ads_entra_no_lucro_do_periodo(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        _lancamento_fatura("ML1", 10.0, sub_type="CV", sale_fee_net=10.0)
        _ads("2026-09-10", 7.0, sku="AAA")
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["ads"], 7.0)
        # 100 - 30 (custo) - 10 (taxa) - 7 (ads) - 12 (imposto)
        self.assertEqual(d["totais"]["lucro_liquido"], 41.0)
        self.assertEqual(next(x for x in d["por_sku"] if x["sku"] == "AAA")["ads"], 7.0)

    def test_publicidade_na_fatura_nao_dobra_o_ads(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        _lancamento_fatura("ML1", 10.0, sub_type="CV", sale_fee_net=10.0)
        # Mesmo gasto de Ads, agora também lançado na fatura dentro do pedido.
        _lancamento_fatura("ML1", 7.0, sub_type="PUB", transacao="Publicidade Product Ads", detail_id="pub-1")
        _ads("2026-09-10", 7.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["ads"], 7.0)
        self.assertEqual(d["totais"]["outros"], 0.0,
                         "publicidade da fatura tem que sair de 'outros': o Ads já veio da API")
        self.assertEqual(d["totais"]["lucro_liquido"], 41.0)

    def test_ads_da_shopee_nao_existe_e_nao_e_inventado(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("shopee", "SP1", "AAA", 1, 100.0)
        _escrow("SP1", 5.0, 2.0, 1.0, 7.0)
        _ads("2026-09-10", 7.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")
        self.assertEqual(d["totais"]["ads"], 0.0, "o gasto de Ads do ML não pode ser jogado na Shopee")

    def test_status_do_ads_diz_quando_nao_esta_conectado(self) -> None:
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertFalse(d["ads"]["conectado"], "sem anunciante configurado, a tela precisa avisar")


class TesteFinanceiroShopee(TesteNoromak):
    """Shopee: a autoridade é o escrow (get_escrow_detail)."""

    def test_taxa_e_frete_vem_do_escrow(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("shopee", "SP1", "AAA", 1, 100.0)
        _escrow("SP1", 5.0, 2.0, 1.0, 7.0, escrow_amount=85.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")
        t = d["totais"]
        self.assertEqual(t["taxas"], 8.0, "comissão + serviço + transação")
        self.assertEqual(t["frete"], 7.0)
        # 100 - 30 - 8 - 7 - 12
        self.assertEqual(t["lucro_liquido"], 43.0)

    def test_pedido_sem_escrow_fica_pendente(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("shopee", "SP2", "AAA", 1, 100.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")
        self.assertEqual(d["cobertura"]["pedidos_shopee_conciliados"], 0)
        self.assertEqual(d["cobertura"]["faturamento_sem_taxa"], 100.0)

    def test_os_dois_marketplaces_somam_no_total(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 1, 100.0)
        _vender("shopee", "SP1", "AAA", 1, 100.0)
        _lancamento_fatura("ML1", 10.0, sub_type="CV", sale_fee_net=10.0)
        _escrow("SP1", 5.0, 2.0, 1.0, 7.0)
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["faturamento"], 200.0)
        self.assertEqual(d["totais"]["taxas"], 18.0)
        self.assertEqual(d["por_marketplace"]["ml"]["faturamento"], 100.0)
        self.assertEqual(d["por_marketplace"]["shopee"]["faturamento"], 100.0)


def _custo_da_venda(order_id: str, line_key: str, sku: str, sale_fee: float | None,
                    shipment_id: str = "", dia: str = "2026-09-10") -> None:
    """O que o ML já mostra no ato da venda (tarifa da linha + envio)."""
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO ml_venda_custo_v1830(order_id,line_key,sku,sale_fee,shipment_id,sale_day,captured_at) "
            "VALUES(?,?,?,?,?,?,?)", (order_id, line_key, sku, sale_fee, shipment_id, dia, S.now_iso()))
        S.DB.commit()


def _frete_envio(shipment_id: str, custo: float) -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO ml_frete_envio_v1830(shipment_id,cost,captured_at,captured_epoch,erro) "
            "VALUES(?,?,?,?,'')", (shipment_id, custo, S.now_iso(), 9_999_999_999))
        S.DB.commit()


class TesteFinanceiroCustoNoAtoDaVenda(TesteNoromak):
    """V18.31 — a tarifa e o frete aparecem NO ATO DA VENDA; a fatura confere depois.

    O caso é o que o ML mostra no detalhe do pedido:
        Preço do produto        R$ 156,50
        Tarifa de venda (12%)  -R$  18,78
        Mercado Envios          -R$  22,25
    """

    def cenario_venda(self) -> None:
        _custos_precificador({"GP-ALAV": 60.0})
        _vender("ml", "ML7700", "GP-ALAV", 1, 156.50)
        _custo_da_venda("ML7700", "GP-ALAV", "GP-ALAV", 18.78, shipment_id="E900")
        _frete_envio("E900", 22.25)

    def test_tarifa_da_venda_e_usada_antes_da_fatura_existir(self) -> None:
        self.cenario_venda()
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 18.78, "a tarifa do ato da venda tem que valer enquanto a fatura não sai")
        self.assertEqual(t["frete"], 22.25, "o frete do Mercado Envios por conta do vendedor também")
        self.assertEqual(t["imposto"], 18.78)          # 12% de 156,50
        # 156,50 - 60,00 (custo) - 18,78 (tarifa) - 22,25 (frete) - 18,78 (imposto)
        self.assertEqual(t["lucro_liquido"], 36.69)

    def test_fonte_aparece_como_da_venda(self) -> None:
        self.cenario_venda()
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        p = next(x for x in d["pedidos"] if x["order_id"] == "ML7700")
        self.assertTrue(p["conciliado"])
        self.assertFalse(p["na_fatura"], "ainda não está na fatura: a tela precisa dizer isso")
        self.assertEqual(p["taxa_venda"], 18.78)
        self.assertIsNone(p["taxa_fatura"])
        self.assertEqual(d["cobertura"]["pedidos_ml_taxa_da_venda"], 1)
        self.assertEqual(d["cobertura"]["faturamento_sem_taxa"], 0.0)

    def test_fatura_prevalece_e_a_diferenca_fica_visivel(self) -> None:
        self.cenario_venda()
        # A fatura cobrou 19,10 (0,32 a mais do que o mostrado na venda).
        _lancamento_fatura("ML7700", 19.10, sub_type="CV", transacao="Cobranca por venda", sale_fee_net=19.10)
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["taxas"], 19.10, "quando existe, vale o que foi COBRADO")
        p = next(x for x in d["pedidos"] if x["order_id"] == "ML7700")
        self.assertTrue(p["na_fatura"])
        self.assertEqual(p["taxa_venda"], 18.78)
        self.assertEqual(p["taxa_fatura"], 19.10)
        self.assertEqual(p["divergencia_taxa"], 0.32)
        self.assertEqual(d["cobertura"]["divergencias"], 1)
        self.assertEqual(d["cobertura"]["divergencia_total"], 0.32)

    def test_as_duas_fontes_nunca_somam(self) -> None:
        self.cenario_venda()
        _lancamento_fatura("ML7700", 18.78, sub_type="CV", sale_fee_net=18.78)
        _lancamento_fatura("ML7700", 22.25, sub_type="CXD", transacao="Mercado Envios", detail_id="frete-1")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 18.78, "fatura e venda são a MESMA cobrança; somar dobraria a taxa")
        self.assertEqual(t["frete"], 22.25)
        self.assertEqual(t["lucro_liquido"], 36.69)

    def test_frete_de_envio_com_dois_pedidos_nao_conta_duas_vezes(self) -> None:
        _custos_precificador({"AAA": 10.0})
        _vender("ml", "ML01", "AAA", 1, 100.0)
        _vender("ml", "ML02", "AAA", 1, 100.0)
        _custo_da_venda("ML01", "AAA", "AAA", 5.0, shipment_id="E555")
        _custo_da_venda("ML02", "AAA", "AAA", 5.0, shipment_id="E555")
        _frete_envio("E555", 20.0)
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["frete"], 20.0, "o envio é um só: 10,00 para cada pedido, não 20,00 para cada")

    def test_sale_fee_da_order_e_por_unidade(self) -> None:
        # O ML manda sale_fee POR UNIDADE; a tarifa da linha é sale_fee x quantidade.
        self.assertEqual(S._ml_item_fee({"sale_fee": 9.39, "quantity": 2}), 18.78)
        self.assertEqual(S._ml_item_fee({"sale_fee": 18.78, "quantity": 1}), 18.78)
        self.assertIsNone(S._ml_item_fee({"quantity": 1}), "sem sale_fee não se inventa tarifa")

    def test_frete_do_vendedor_le_senders_cost(self) -> None:
        payload = {"gross_amount": 41.9, "receiver": {"cost": 19.65},
                   "senders": [{"user_id": "224625106", "cost": 22.25}]}
        self.assertEqual(S._ml_shipping_cost_parse(payload, "224625106"), 22.25,
                         "o custo do vendedor é senders[].cost, nunca o gross_amount")


class TesteFaturaMLReal(TesteNoromak):
    """V18.34 — os sub_type que a fatura do MLB usa de verdade.

    Levantados na conta 224625106 em 28/09/2026. A regra antiga procurava CV/BV
    e textos como "cobrança por venda"; a fatura diz "Custo por vender no
    Mercado Livre" com sub_type CVVML. O efeito em produção foi a tarifa inteira
    (R$ 11,4 mil no mês) cair em "outros" e a margem aparecer inflada.
    """

    def cenario(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML100", "AAA", 1, 200.0)

    def test_custo_por_vender_e_tarifa(self) -> None:
        self.cenario()
        _lancamento_fatura("ML100", 24.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="a")
        _lancamento_fatura("ML100", 3.5, sub_type="CVVPRC", transacao="Custo por cobrar no Mercado Pago", detail_id="b")
        _lancamento_fatura("ML100", 0.5, sub_type="CVVFNU", transacao="Taxa de recebimento", detail_id="c")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 28.0, "CVVML + CVVPRC + CVVFNU formam a tarifa de venda")
        self.assertEqual(t["outros"], 0.0, "tarifa não pode ficar escondida em 'outros'")

    def test_cancelamento_abate_a_tarifa(self) -> None:
        self.cenario()
        _lancamento_fatura("ML100", 24.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="a")
        _lancamento_fatura("ML100", 24.0, sub_type="BVVML", transacao="Cancelamento do Custo por vender",
                           credito=True, detail_id="b")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 0.0, "venda cancelada devolve a tarifa")

    def test_envio_extra_e_frete(self) -> None:
        self.cenario()
        _lancamento_fatura("ML100", 20.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="a")
        _lancamento_fatura("ML100", 12.0, sub_type="CXDE", transacao="Tarifa de envio extra ou intermunicipal", detail_id="b")
        _lancamento_fatura("ML100", 7.0, sub_type="CFFE", transacao="Tarifa de envio extra ou intermunicipal", detail_id="c")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["frete"], 19.0)
        self.assertEqual(t["taxas"], 20.0)

    def test_taxa_de_parcelamento_nao_e_custo(self) -> None:
        # O comprador paga o acréscimo; o ML mostra os dois somados em R$ 0,00.
        self.cenario()
        _lancamento_fatura("ML100", 20.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="a")
        _lancamento_fatura("ML100", 15.0, sub_type="CFONPN", transacao="Taxa de parcelamento (equivalente ao acrescimo)", detail_id="b")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["outros"], 0.0, "parcelamento se anula com o acréscimo do comprador")
        self.assertEqual(t["taxas"], 20.0)

    def test_armazenagem_full_e_custo(self) -> None:
        self.cenario()
        _lancamento_fatura("ML100", 20.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="a")
        _lancamento_fatura("ML100", 1.5, sub_type="CFWA", transacao="Tarifa pelo servico de armazenamento Full", detail_id="b")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["outros"], 1.5, "armazenagem Full é custo real, fica em outros")


class TesteFaturaDuplicada(TesteNoromak):
    """V18.36 — o Billing devolve os MESMOS lançamentos em BILL e em CREDIT_NOTE.

    Em produção isso zerou a tarifa de venda do mês: cada cobrança ganhava uma
    cópia marcada como crédito (pelo document_type que nós pedimos) e uma
    anulava a outra. R$ 132 mil de faturamento ML apareceram com R$ 59,42 de
    taxa. Crédito agora é o que o lançamento diz, e a leitura deduplica.
    """

    def _duplicado(self, order_id: str, valor: float, sub: str, detail_id: str) -> None:
        """Grava o mesmo detail_id nos dois documentos, como a API devolve."""
        for doc in ("BILL", "CREDIT_NOTE"):
            with S.DB_LOCK:
                S.DB.execute(
                    "INSERT OR REPLACE INTO ml_billing_period_detail(period_key,document_type,detail_id,order_id,"
                    "pack_id,shipment_id,detail_amount,detail_type,detail_sub_type,transaction_detail,"
                    "debited_from_operation,sale_fee_net,sale_fee_gross,sale_fee_rebate,sale_fee_discount,"
                    "is_credit,raw_json,synced_at) VALUES(?,?,?,?,'','',?,?,?,?,?,?,NULL,NULL,NULL,?,'{}',?)",
                    ("2026-09-01", doc, detail_id, order_id, valor, "CHARGE", sub,
                     "Custo por vender no Mercado Livre", "INAPPLICABLE", None,
                     1 if doc == "CREDIT_NOTE" else 0, S.now_iso()))
                S.DB.commit()

    def test_copia_em_credit_note_nao_zera_a_tarifa(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML400", "AAA", 1, 200.0)
        self._duplicado("ML400", 24.0, "CVVML", "dup-1")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 24.0, "o mesmo detail_id nos dois documentos é UM lançamento só")

    def test_estorno_de_verdade_continua_abatendo(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML401", "AAA", 1, 200.0)
        self._duplicado("ML401", 24.0, "CVVML", "dup-2")
        # Estorno real: outro detail_id, detail_type BONUS.
        _lancamento_fatura("ML401", 24.0, sub_type="BVVML", detail_type="BONUS",
                           transacao="Cancelamento do Custo por vender", credito=True, detail_id="estorno-1")
        t = S.financeiro_payload("2026-09-01", "2026-09-30")["totais"]
        self.assertEqual(t["taxas"], 0.0, "cancelamento com detail_id próprio continua devolvendo a tarifa")

    def test_venda_real_do_cilindro_auxiliar(self) -> None:
        """Pedido 2000018677905102, 28/09/2026 — o caso que o dono conferiu.

        Mercado Turbo: 255,40 − 118,56 − 30,65 (imposto) − 30,65 (tarifa)
                       − 24,45 (frete) = 51,09 (20,00%).
        """
        _custos_precificador({"816.3234-0": 118.57})
        _vender("ml", "2000018677905102", "816.3234-0", 1, 255.40, dia="2026-09-28")
        _custo_da_venda("2000018677905102", "816.3234-0", "816.3234-0", 30.65,
                        shipment_id="48116989093", dia="2026-09-28")
        _frete_envio("48116989093", 24.45)
        # A fatura repete cada lançamento em BILL e em CREDIT_NOTE.
        self._duplicado("2000018677905102", 30.39, "CVVML", "72187967734")
        self._duplicado("2000018677905102", 0.26, "CVVPRC", "72187967727")
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        t = d["totais"]
        self.assertEqual(t["taxas"], 30.65, "30,39 (CVVML) + 0,26 (CVVPRC), sem as cópias")
        self.assertEqual(t["frete"], 24.45)
        self.assertEqual(t["imposto"], 30.65)
        self.assertEqual(t["lucro_liquido"], 51.08)   # 1 centavo do custo: 118,57 x 118,56
        self.assertEqual(t["margem_liquida"], 20.0)

    def test_credito_vem_do_lancamento_e_nao_do_documento(self) -> None:
        self.assertEqual(S._ml_billing_credito_do_lancamento("CHARGE", "CVVML"), 0)
        self.assertEqual(S._ml_billing_credito_do_lancamento("BONUS", "BVVML"), 1)
        self.assertEqual(S._ml_billing_credito_do_lancamento("", "BXDE"), 1)
        self.assertEqual(S._ml_billing_credito_do_lancamento("CHARGE", "CXDE"), 0)


class TesteAdsDaFatura(TesteNoromak):
    """V18.34 — PADS: o gasto com Ads está na fatura, pedido a pedido."""

    def cenario(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML200", "AAA", 1, 200.0)
        _lancamento_fatura("ML200", 20.0, sub_type="CVVML", transacao="Custo por vender no Mercado Livre", detail_id="cv")
        _lancamento_fatura("ML200", 9.0, sub_type="PADS",
                           transacao="Tarifa por campanha de publicidade de Product Ads", detail_id="ads")

    def test_pads_vira_ads_quando_a_api_nao_responde(self) -> None:
        self.cenario()
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["ads"]["fonte"], "fatura")
        self.assertEqual(d["totais"]["ads"], 9.0)
        self.assertEqual(d["totais"]["outros"], 0.0, "publicidade não pode ficar dentro de 'outros'")
        # 200 - 40 (custo) - 20 (tarifa) - 9 (ads) - 24 (imposto)
        self.assertEqual(d["totais"]["lucro_liquido"], 107.0)

    def test_ads_da_fatura_chega_no_produto_e_no_pedido(self) -> None:
        self.cenario()
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(next(x for x in d["por_sku"] if x["sku"] == "AAA")["ads"], 9.0)
        self.assertEqual(next(x for x in d["pedidos"] if x["order_id"] == "ML200")["ads"], 9.0)

    def test_api_tem_prioridade_e_nao_soma_com_a_fatura(self) -> None:
        self.cenario()
        _ads("2026-09-10", 5.0)          # API de Publicidade respondeu
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["ads"]["fonte"], "api")
        self.assertEqual(d["totais"]["ads"], 5.0, "uma fonte OU a outra; somar contaria o Ads duas vezes")


class TesteParcialNaoEZero(TesteNoromak):
    """V18.34 — R$ 0,00 só quando é zero de verdade; senão, pendência visível."""

    def test_frete_desconhecido_aparece_como_pendencia(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML300", "AAA", 1, 100.0)
        _custo_da_venda("ML300", "AAA", "AAA", 12.0)   # tarifa sim, envio nenhum
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["totais"]["frete"], 0.0)
        self.assertEqual(d["cobertura"]["faturamento_sem_frete"], 100.0,
                         "sem frete conciliado o faturamento tem que entrar na pendência")
        linha = next(x for x in d["por_sku"] if x["sku"] == "AAA")
        self.assertEqual(linha["sem_frete"], 100.0)
        self.assertEqual(linha["sem_taxa"], 0.0)

    def test_frete_zero_de_verdade_nao_vira_pendencia(self) -> None:
        _custos_precificador({"AAA": 40.0})
        _vender("ml", "ML301", "AAA", 1, 100.0)
        _custo_da_venda("ML301", "AAA", "AAA", 12.0, shipment_id="E301")
        _frete_envio("E301", 0.0)      # o ML respondeu: o vendedor não pagou envio
        d = S.financeiro_payload("2026-09-01", "2026-09-30")
        self.assertEqual(d["cobertura"]["faturamento_sem_frete"], 0.0,
                         "envio consultado e sem custo é zero de verdade")


class TesteShopeeEscrowAutoridade(TesteNoromak):
    """V18.35 — na Shopee, o escrow_amount é a autoridade.

    Caso real do pedido 2609152YHBQ424 (28/09/2026):
        preço do produto           R$ 25,90
        comissão                   R$  4,66
        taxa de serviço            R$  5,79
        comissão de afiliados AMS  R$  2,58
        escrow (o que entra)       R$ 12,87
    Sem o AMS a conta fechava em R$ 15,45 de renda — R$ 2,58 a mais do que a
    Shopee paga de verdade. No mês inteiro eram 3,5% do preço somindo.
    """

    def cenario(self) -> None:
        _custos_precificador({"AAA": 8.0})
        _vender("shopee", "SP900", "AAA", 1, 25.90)
        _escrow("SP900", comissao=4.66, servico=5.79, transacao=0.0, frete=0.0,
                escrow_amount=12.87, preco=25.90, ams=2.58)

    def test_afiliados_entram_como_ads(self) -> None:
        self.cenario()
        d = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")
        self.assertEqual(d["totais"]["ads"], 2.58, "a comissão de afiliados da Shopee é custo de marketing")
        self.assertEqual(d["totais"]["taxas"], 10.45)
        self.assertEqual(d["ads"]["shopee_afiliados"], 2.58)

    def test_custo_total_bate_com_o_escrow(self) -> None:
        self.cenario()
        t = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")["totais"]
        retido = round(25.90 - 12.87, 2)
        somado = round(t["taxas"] + t["frete"] + t["ads"] + t["outros"], 2)
        self.assertEqual(somado, retido, "o que descontamos tem que ser o que a Shopee reteve")
        # 25,90 - 8,00 (custo) - 13,03 (retido) - 3,11 (imposto 12%)
        self.assertEqual(t["lucro_liquido"], 1.76)

    def test_retencao_nao_detalhada_vira_outros(self) -> None:
        # Escrow menor do que as taxas explicam: a diferença não pode virar lucro.
        _custos_precificador({"AAA": 8.0})
        _vender("shopee", "SP901", "AAA", 1, 100.0)
        _escrow("SP901", comissao=14.0, servico=6.0, transacao=0.0, frete=0.0,
                escrow_amount=70.0, preco=100.0, ams=0.0)
        t = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")["totais"]
        self.assertEqual(t["outros"], 10.0, "os R$ 10 que a Shopee reteve sem detalhar são custo")

    def test_sem_preco_no_escrow_nao_inventa_outros(self) -> None:
        _custos_precificador({"AAA": 8.0})
        _vender("shopee", "SP902", "AAA", 1, 100.0)
        _escrow("SP902", comissao=14.0, servico=6.0, transacao=0.0, frete=0.0)  # escrow/preço zerados
        t = S.financeiro_payload("2026-09-01", "2026-09-30", "shopee")["totais"]
        self.assertEqual(t["outros"], 0.0, "sem escrow confiável não se inventa resíduo")
        self.assertEqual(t["taxas"], 20.0)


class TesteRentabilidadeRemovida(TesteNoromak):
    """V18.33 — a Rentabilidade ML saiu; o Financeiro ficou no lugar dela.

    O que NÃO pode quebrar junto: o endereço antigo, os custos da gaveta do
    Dashboard e o custo de reserva da Demanda de Compra.
    """

    def test_endereco_antigo_abre_o_financeiro(self) -> None:
        import urllib.request
        req = urllib.request.Request(self.url_base + "/rentabilidade-ml")
        req.add_header("Cookie", self.cliente.cookie)
        with urllib.request.urlopen(req, timeout=20) as r:
            corpo = r.read().decode("utf-8", "replace")
        self.assertEqual(r.status, 200)
        self.assertIn("Noromak · Financeiro", corpo, "o link antigo tem que abrir a tela nova")

    def test_api_antiga_nao_existe_mais(self) -> None:
        status, _ = self.cliente.pedir("GET", "/api/rentabilidade-ml?date_from=2026-09-01&date_to=2026-09-30")
        self.assertEqual(status, 404, "a rota da tela removida não pode continuar respondendo")

    def test_gaveta_do_dashboard_usa_o_custo_do_ato_da_venda(self) -> None:
        _vender("ml", "ML555", "AAA", 1, 100.0)
        _custo_da_venda("ML555", "AAA", "AAA", 12.34, shipment_id="E555")
        _frete_envio("E555", 7.66)
        d = S.dashboard_produto_custos("AAA", dias=400)
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["ml"]["tarifa"], 12.34)
        self.assertEqual(d["ml"]["envios"], 7.66)
        self.assertEqual(d["ml"]["com_dado"], 1)

    def test_gaveta_ainda_le_o_historico_antigo(self) -> None:
        # Pedido antigo, que só existe no snapshot da Rentabilidade removida.
        _vender("ml", "ML444", "AAA", 1, 80.0)
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT OR REPLACE INTO ml_profitability_snapshot(order_id,sku,unit_cost,cost_source,sale_amount,"
                "ml_fee,seller_shipping_cost,other_ml_cost,captured_at,raw_summary) VALUES(?,?,?,?,?,?,?,?,?,'')",
                ("ML444", "AAA", 30.0, "Apollo", 80.0, 9.5, 4.5, 0.0, S.now_iso()))
            S.DB.commit()
        d = S.dashboard_produto_custos("AAA", dias=400)
        self.assertEqual(d["ml"]["tarifa"], 9.5, "o histórico gravado não pode sumir com a remoção da tela")
        self.assertEqual(d["ml"]["envios"], 4.5)


class TesteFinanceiroRota(TesteNoromak):
    """A rota HTTP: só ADMIN, e devolve o mesmo cálculo."""

    def test_resumo_exige_admin(self) -> None:
        from base import Cliente
        anonimo = Cliente(self.url_base)
        status, _ = anonimo.pedir("GET", "/api/financeiro/resumo?data_inicio=2026-09-01&data_fim=2026-09-30")
        self.assertIn(status, (401, 403), "dado financeiro não pode sair sem sessão ADMIN")

    def test_resumo_responde_o_periodo(self) -> None:
        _custos_precificador({"AAA": 30.0})
        _vender("ml", "ML1", "AAA", 2, 200.0)
        _lancamento_fatura("ML1", 20.0, sub_type="CV", sale_fee_net=20.0)
        status, corpo = self.cliente.pedir("GET", "/api/financeiro/resumo?data_inicio=2026-09-01&data_fim=2026-09-30")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo.get("ok"))
        self.assertEqual(corpo["totais"]["faturamento"], 200.0)
        self.assertEqual(corpo["totais"]["cmv"], 60.0)
        self.assertEqual(corpo["totais"]["imposto"], 24.0)

    def test_data_invalida_e_recusada(self) -> None:
        status, corpo = self.cliente.pedir("GET", "/api/financeiro/resumo?data_inicio=01/09/2026&data_fim=2026-09-30")
        self.assertEqual(status, 400, corpo)


if __name__ == "__main__":
    unittest.main()
