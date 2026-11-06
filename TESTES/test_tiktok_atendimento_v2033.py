"""V20.33 — atendimento do TikTok criado no Apollo (porta atendimento/criar), como a integração do ML.

Decisões do dono (06/11): "a mesma rotina do ML": cadastrar o cliente e em seguida criar o atendimento,
sozinho; kit = preço dividido entre as peças pelo custo; pedido de Bauru usa a revenda 3 em todo o painel.
A porta não cria dois atendimentos para o mesmo `pedido_externo` (responde "ja_existia").
"""

from unittest import mock

from base import S, TesteNoromak, linha, gravar_como_admin

CPF = "27247410809"
NOME = "DOUGLAS DOS SANTOS JESUS"


def _endereco():
    return [{"key": "name", "value": "Douglas Santos"}, {"key": "street_name", "value": "Avenida Guarapiranga"},
            {"key": "street_number", "value": "2500"}, {"key": "address_detail", "value": "AP 74A"},
            {"key": "neighborhood", "value": "Guarapiranga"}, {"key": "geo_l2", "value": "São Paulo"},
            {"key": "geo_l1", "value": "SP"}, {"key": "zipcode", "value": "04911005"}]


def _sc(numero, itens=(("SK1", "0893600022", 1, "69.75"),), desconto="0", origem=None, status=111, cpf=CPF):
    """Detalhe do Seller Center com preço. itens = (sku_id, seller_sku, quantidade, preço da unidade)."""
    linhas_status, skus, n, soma = [], [], 0, 0.0
    for sku_id, sku, qtd, preco in itens:
        ids = []
        for _ in range(qtd):
            n += 1
            ids.append(f"{numero}L{n}")
            linhas_status.append({"order_line_id": f"{numero}L{n}", "sku_display_status": status})
        soma += float(preco) * qtd
        skus.append({"sku_id": sku_id, "order_line_ids": ids, "seller_sku_name": sku, "product_name": "Produto",
                     "quantity": qtd, "sku_unit_price": {"price_val": preco}})
    return {"main_order_id": numero,
            "trade_order_module": {"create_time": "1794000000", "latest_rts_time": "1794100000"},
            "order_status_module": linhas_status, "sku_module": skus,
            "fulfill_unit_id_mapper": [{"package_id": f"PK{numero}", "order_line_id": []}],
            "price_module": {"main_order_origin_sale_price": origem if origem is not None else f"{soma:.2f}",
                             "seller_discount_total": desconto},
            "buyer_info_module": {"buyer_nickname": "apelido", "cpf": cpf, "cpf_name": NOME,
                                  "shipping_address": {"items": _endereco()}}}


def _kits(lista):
    atual, _, _ = S.get_storage("conf_kits")
    S.save_storage("conf_kits", atual, S.json_dumps(lista), False, actor="admin_teste", actor_role="admin",
                   source_screen="teste", change_method="teste", client_ip="127.0.0.1")


class Porta:
    def __init__(self, buscar="nao_cadastrado", criar_atend=None):
        self.chamadas = []
        self.buscar = buscar
        self.criar_atend = criar_atend

    def __call__(self, rota, corpo, escrita=False):
        self.chamadas.append((rota, dict(corpo)))
        if rota == "cliente/buscar":
            if self.buscar == "encontrado":
                return {"ok": True, "situacao": "encontrado", "cliente": 83070, "nome": NOME}
            return {"ok": True, "situacao": "nao_cadastrado"}
        if rota == "cliente/criar":
            return {"ok": True, "situacao": "criado", "cliente": 99001}
        if rota == "cliente/atualizar":
            return {"ok": True, "situacao": "atualizado"}
        if rota == "cliente/credito":
            return {"ok": True, "situacao": "ja_liberado"}
        if rota == "atendimento/criar":
            if isinstance(self.criar_atend, Exception):
                raise self.criar_atend
            if self.criar_atend is not None:
                return self.criar_atend
            return {"ok": True, "situacao": "criado", "atendimento": 793500, "revenda": corpo["revenda"],
                    "pedido_externo": corpo["pedido_externo"], "valor": corpo["valor_esperado"],
                    "itens": [dict(i, item=k + 1) for k, i in enumerate(corpo["itens"])]}
        raise AssertionError(f"rota inesperada {rota}")

    def corpo(self, rota):
        return next(c for r, c in self.chamadas if r == rota)

    def rotas(self):
        return [r for r, _ in self.chamadas]


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._tiktok_config_gravar("atendimento_auto", "1", "teste")

    def receber(self, *detalhes):
        lista = [{k: v for k, v in o.items() if k not in ("price_module",)} for o in detalhes]
        return S.tiktok_pedidos_da_extensao(lista, list(detalhes), completo=True, ator="dono")

    def rodar(self, porta, custos=None):
        custos = custos or {}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "_tiktok_atendimento_custo", side_effect=lambda cod, ctx: custos.get(cod)), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}):
            return S.tiktok_atendimento_rodada(forcar=True)

    def estado(self, pedido):
        return S.tiktok_atendimento_do_pedido(pedido) or {}


class Rotina(Base):

    def test_cadastra_e_cria_o_atendimento_em_bauru(self):
        self.receber(_sc("580001"))
        porta = Porta()
        self.rodar(porta)
        self.assertEqual(porta.rotas()[:2], ["cliente/buscar", "cliente/criar"])
        criar = porta.corpo("cliente/criar")
        self.assertEqual(criar["revenda"], 3)
        self.assertEqual(criar["endereco"]["bairro"], "GUARAPIRANGA")
        self.assertEqual(criar["endereco"]["complemento"], "AP 74A")
        corpo = porta.corpo("atendimento/criar")
        self.assertEqual((corpo["revenda"], corpo["origem"], corpo["pedido_externo"], corpo["cliente"]),
                         (3, "TIKTOK", "TIKTOK-580001", 99001))
        self.assertEqual(corpo["condicao_pagamento"], 973)
        self.assertEqual(corpo["itens"], [{"codigo": "0893600022", "quantidade": 1, "unitario": 69.75}])
        self.assertEqual(corpo["valor_esperado"], 69.75)
        e = self.estado("580001")
        self.assertEqual((e["situacao"], e["atendimento"]), ("criado", "793500"))
        # cadastro marcado como feito: o painel pula a etapa
        self.assertEqual(S.cadastro_auto_do_pedido("580001")["situacao"], "feito")
        # uma vez só
        porta2 = Porta()
        self.rodar(porta2)
        self.assertNotIn("atendimento/criar", porta2.rotas())

    def test_desligado_nao_chama_o_apollo(self):
        S._tiktok_config_gravar("atendimento_auto", "0", "teste")
        self.receber(_sc("580002"))
        porta = Porta()
        self.rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_comeca_desligado(self):
        S.DB.execute("DELETE FROM tiktok_config_v1964 WHERE chave='atendimento_auto'")
        S.DB.commit()
        self.assertFalse(S.tiktok_atendimento_ligado())

    def test_desconto_da_loja_entra_no_preco(self):
        # 2 × 89,90 = 179,80 − 50,00 da loja = 129,80 (exemplo real do mapa, 06/11)
        self.receber(_sc("580003", itens=(("SK1", "0893600022", 2, "89.90"),), desconto="50.00"))
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        corpo = porta.corpo("atendimento/criar")
        self.assertEqual(corpo["itens"], [{"codigo": "0893600022", "quantidade": 2, "unitario": 64.9}])
        self.assertEqual(corpo["valor_esperado"], 129.8)
        self.assertEqual(S._exp_valor_pedido("580003"), 129.8)

    def test_preco_que_nao_confere_nao_cria(self):
        self.receber(_sc("580004", itens=(("SK1", "0893600022", 1, "69.75"),), origem="80.00"))
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("580004")["situacao"], "revisar")

    def test_kit_dividido_pelo_custo(self):
        _kits([{"id": "KIT_1", "sku_kit": "KITCERA", "ativo": True,
                                                        "componentes": [{"sku": "1001", "quantidade": 1},
                                                                        {"sku": "1002", "quantidade": 2}]}])
        self.receber(_sc("580005", itens=(("SK1", "KITCERA", 1, "100.00"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta, custos={"1001": 30.0, "1002": 10.0})
        itens = porta.corpo("atendimento/criar")["itens"]
        self.assertEqual(itens, [{"codigo": "1001", "quantidade": 1, "unitario": 60.0},
                                 {"codigo": "1002", "quantidade": 2, "unitario": 20.0}])
        self.assertEqual(round(sum(i["quantidade"] * i["unitario"] for i in itens), 2), 100.0)

    def test_kit_com_centavo_que_sobra(self):
        _kits([{"id": "KIT_1", "sku_kit": "KITTRES", "ativo": True,
                                                        "componentes": [{"sku": "1001", "quantidade": 1},
                                                                        {"sku": "1002", "quantidade": 1},
                                                                        {"sku": "1003", "quantidade": 1}]}])
        self.receber(_sc("580006", itens=(("SK1", "KITTRES", 1, "100.00"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta, custos={"1001": 10.0, "1002": 10.0, "1003": 10.0})
        itens = porta.corpo("atendimento/criar")["itens"]
        self.assertEqual(round(sum(i["quantidade"] * i["unitario"] for i in itens), 2), 100.0)

    def test_kit_sem_custo_de_uma_peca_nao_cria(self):
        _kits([{"id": "KIT_1", "sku_kit": "KITCERA", "ativo": True,
                                                        "componentes": [{"sku": "1001", "quantidade": 1},
                                                                        {"sku": "1002", "quantidade": 1}]}])
        self.receber(_sc("580007", itens=(("SK1", "KITCERA", 1, "100.00"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta, custos={"1001": 30.0})
        self.assertNotIn("atendimento/criar", porta.rotas())
        e = self.estado("580007")
        self.assertEqual(e["situacao"], "revisar")
        self.assertIn("1002", e["detalhe"])

    def test_sku_com_cara_de_dois_codigos_sem_cadastro_nao_cria(self):
        # Caso real 06/11: "KIT-2050171-2011058" — sem o Cadastro de Kits a leitura perderia uma peça.
        self.receber(_sc("580008", itens=(("SK1", "KIT-2050171-2011058", 1, "69.75"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("580008")["situacao"], "revisar")

    def test_sem_credito_tenta_de_novo_depois(self):
        self.receber(_sc("580009"))
        porta = Porta(buscar="encontrado", criar_atend={"ok": False, "situacao": "sem_credito", "erro": "Cliente sem crédito"})
        self.rodar(porta)
        e = self.estado("580009")
        self.assertEqual(e["situacao"], "erro")
        self.assertIn("crédito", e["detalhe"])
        self.assertGreater(float(e["proxima_epoch"]), 0)

    def test_resposta_perdida_fica_incerto_e_repete_com_a_mesma_chave(self):
        self.receber(_sc("580010"))
        porta = Porta(buscar="encontrado", criar_atend=S.PortaErro("sem resposta", incerto=True))
        self.rodar(porta)
        self.assertEqual(self.estado("580010")["situacao"], "incerto")
        self.assertGreater(float(self.estado("580010")["proxima_epoch"]), 0)   # espera antes de repetir
        S.DB.execute("UPDATE tiktok_atendimento_v2033 SET proxima_epoch=0")      # o tempo passou
        S.DB.commit()
        porta2 = Porta(buscar="encontrado", criar_atend={"ok": True, "situacao": "ja_existia", "atendimento": 793501,
                                                          "valor": 69.75, "itens": []})
        self.rodar(porta2)
        self.assertEqual(porta2.corpo("atendimento/criar")["pedido_externo"], "TIKTOK-580010")
        self.assertEqual(self.estado("580010")["atendimento"], "793501")

    def test_valor_relido_diferente_avisa(self):
        self.receber(_sc("580011"))
        porta = Porta(buscar="encontrado", criar_atend={"ok": True, "situacao": "criado", "atendimento": 793502,
                                                         "valor": 70.10, "itens": []})
        self.rodar(porta)
        e = self.estado("580011")
        self.assertEqual(e["atendimento"], "793502")
        self.assertIn("70,10", e["detalhe"])

    def test_sem_cpf_da_nota_espera(self):
        self.receber(_sc("580012", cpf=""))
        porta = Porta()
        self.rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_pedido_de_outra_origem_nao_entra(self):
        gravar_como_admin([linha("PEDML1", "0893600022", Marketplace="MERCADO LIVRE")])
        self.assertEqual(S._tiktok_atendimento_candidatos(), [])


class Revenda(Base):

    def test_pedido_de_bauru_usa_a_revenda_3(self):
        gravar_como_admin([linha("PEDB1", "0893600022", Marketplace="MERCADO LIVRE", Casa="bauru"),
                           linha("PEDA1", "0893600022", Marketplace="MERCADO LIVRE")])
        self.assertEqual(S._exp_revenda("PEDB1"), 3)
        self.assertEqual(S._exp_revenda("PEDA1"), 1)
        S.salvar_cliente_fiscal("PEDB1", CPF, NOME, linx_cliente="83070", fonte="teste", usuario="teste")
        chamadas = []

        def porta(rota, corpo, escrita=False):
            chamadas.append((rota, dict(corpo)))
            if rota == "cliente/situacao":
                return {"ok": True, "nome": NOME, "situacao_credito": {"codigo": "0"}}
            if rota == "atendimentos/abertos":
                return {"ok": True, "atendimentos": []}
            raise AssertionError(rota)
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.exp_romaneio_preparar("PEDB1")
        self.assertTrue(chamadas)
        self.assertTrue(all(c["revenda"] == 3 for _, c in chamadas))
