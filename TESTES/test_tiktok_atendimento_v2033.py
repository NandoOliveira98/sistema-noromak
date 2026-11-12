"""V20.33 — atendimento do TikTok criado no Apollo (porta atendimento/criar), como a integração do ML.

Decisões do dono (06/11): "a mesma rotina do ML": cadastrar o cliente e em seguida criar o atendimento,
sozinho; kit = preço dividido entre as peças pelo custo; pedido de Bauru usa a revenda 3 em todo o painel.
A porta não cria dois atendimentos para o mesmo `pedido_externo` (responde "ja_existia").
"""

import time
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
        if rota == "atendimento/comprometer":  # V20.43
            comp = getattr(self, "comprometer", None)
            if isinstance(comp, Exception):
                raise comp
            if comp is not None:
                return comp
            return {"ok": True, "situacao": "comprometido", "atendimento": corpo["contato"],
                    "itens": [{"codigo": "0893600022", "comprometido": True}]}
        raise AssertionError(f"rota inesperada {rota}")

    def corpo(self, rota):
        return next(c for r, c in self.chamadas if r == rota)

    def rotas(self):
        return [r for r, _ in self.chamadas]


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._tiktok_config_gravar("atendimento_auto", "1", "teste")
        # V20.60: a procura de atendimento aberto do cliente lê o Apollo (painel); aqui = nenhum aberto.
        # test_tiktok_ja_tem_atendimento_v2060 desliga este atalho e testa a procura.
        self._sem_aberto = mock.patch.object(S, "_atend_aberto_mesmas_pecas", return_value=("", "", False))
        self._sem_aberto.start()
        self.addCleanup(self._sem_aberto.stop)

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

    def test_v2055_cadastro_segurado_para_o_painel_nao_e_erro(self):
        """revisar-antes-de-subir (11/11): leitura segurada pela vez do painel (V20.54) = Apollo ocupado, 2 min."""
        self.receber(_sc("580099"))

        class Segura(Porta):
            def __call__(self, rota, corpo, escrita=False):
                if rota == "cliente/buscar":
                    raise S.PortaErro("Apollo reservado para o painel neste minuto.", passageiro=True)
                return super().__call__(rota, corpo, escrita)
        t0 = time.time()
        self.rodar(Segura())
        e = self.estado("580099")
        self.assertEqual(e["situacao"], "aguardando", e)
        with S.DB_LOCK:
            prox = S.DB.execute("SELECT proxima_epoch FROM tiktok_atendimento_v2033 WHERE pedido='580099'").fetchone()[0]
        self.assertLess(prox - t0, 300, "Apollo ocupado tenta de novo em 2 min, não em 10")

    def test_v2055_apollo_ocupado_para_a_volta(self):
        """2ª revisão: com o Apollo ocupado a rodada não passa pelos outros pedidos (cada um seguraria a porta)."""
        self.receber(_sc("580097"), _sc("580098"))

        class Segura(Porta):
            def __call__(self, rota, corpo, escrita=False):
                if rota == "cliente/buscar":
                    self.chamadas.append((rota, dict(corpo)))
                    raise S.PortaErro("Apollo reservado para o painel neste minuto.", passageiro=True)
                return super().__call__(rota, corpo, escrita)
        porta = Segura()
        self.rodar(porta)
        self.assertEqual(porta.rotas().count("cliente/buscar"), 1, porta.rotas())

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
        self.assertEqual(e["situacao"], "revisar")   # revisão 06/11: alguém libera o crédito
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


class RevisaoAntesDeSubir(Base):
    """revisar-antes-de-subir (06/11, "corrigir todos")."""

    def fila_do(self, pedido):
        bruto, _, _ = S.get_storage("conf_pedidos")
        return [r for r in S._rows_from_storage_value(bruto) if isinstance(r, dict) and r.get("Pedido") == pedido]

    def test_pedido_com_romaneio_ja_solto_nao_ganha_outro_atendimento(self):
        self.receber(_sc("581001"))
        S._linx_operacao_registrar("581001", "romaneio", "ok", {"observacao": "TIKTOK"}, "790001", "luis")
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581001")["situacao"], "anterior")

    def test_ao_ligar_os_pedidos_que_ja_estao_na_fila_ficam_de_fora(self):
        S._tiktok_config_gravar("atendimento_auto", "0", "teste")
        self.receber(_sc("581002"))
        S.tiktok_atendimento_definir(True, "dono")
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581002")["situacao"], "anterior")
        self.receber(_sc("581003"))                       # chegou depois de ligar: entra
        self.rodar(porta)
        self.assertIn("atendimento/criar", porta.rotas())

    def test_quantidade_mudada_pelo_admin_nao_vai_a_do_tiktok(self):
        self.receber(_sc("581004", itens=(("SK1", "0893600022", 2, "50.00"),)))
        linhas = self.fila_do("581004")
        linhas[0]["Quantidade"] = "1"
        gravar_como_admin(linhas)
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        e = self.estado("581004")
        self.assertEqual(e["situacao"], "revisar")
        self.assertIn("fila", e["detalhe"])

    def test_sku_cortado_sem_vinculo_nao_cria(self):
        sku = ("0893600022/" * 5)[:50]
        self.receber(_sc("581005", itens=(("SK1", sku, 1, "69.75"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertIn("cortado", self.estado("581005")["detalhe"])

    def test_vinculo_a_mao_vence_o_sku_do_tiktok(self):
        S.DB.execute("INSERT INTO tiktok_vinculo_v1964(sku_id,codigos,por,em) VALUES('SK9','1001/1002','dono','x')")
        S.DB.commit()
        self.receber(_sc("581006", itens=(("SK9", "QUALQUERCOISA", 1, "100.00"),)))
        porta = Porta(buscar="encontrado")
        self.rodar(porta, custos={"1001": 30.0, "1002": 10.0})
        itens = porta.corpo("atendimento/criar")["itens"]
        self.assertEqual([i["codigo"] for i in itens], ["1001", "1002"])

    def test_pedido_cancelado_que_ficou_na_fila_nao_ganha_atendimento(self):
        self.receber(_sc("581007"))
        S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET status='CANCELLED', saiu_epoch=1 WHERE pedido='581007'")
        S.DB.commit()
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_valor_da_venda_e_o_do_tiktok_de_agora(self):
        self.receber(_sc("581008", itens=(("SK1", "0893600022", 2, "50.00"),)))
        self.rodar(Porta(buscar="encontrado"))
        self.assertEqual(S._exp_valor_pedido("581008"), 100.0)
        # o comprador cancelou uma unidade depois: a venda passa a 50 e o atendimento (100) fica diferente
        o = _sc("581008", itens=(("SK1", "0893600022", 2, "50.00"),))
        o["order_status_module"][1]["sku_display_status"] = 140
        self.receber(o)
        self.assertEqual(S._exp_valor_pedido("581008"), 50.0)

    def test_centavos_da_divisao_nao_viram_diferenca(self):
        self.receber(_sc("581009", itens=(("SK1", "0893600022", 3, "33.3333"),), origem="100.00"))
        self.rodar(Porta(buscar="encontrado"))
        self.assertEqual(S._exp_valor_pedido("581009"), 99.99)   # o que foi ao Apollo

    def test_desconto_negativo_ou_cancelamento_parcial_com_desconto_revisa(self):
        self.receber(_sc("581010", desconto="-5.00"))
        o = _sc("581011", itens=(("SK1", "0893600022", 1, "50.00"), ("SK2", "0893600023", 1, "50.00")), desconto="10.00")
        o["order_status_module"][1]["sku_display_status"] = 140
        self.receber(o)
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581010")["situacao"], "revisar")
        self.assertEqual(self.estado("581011")["situacao"], "revisar")

    def test_um_pedido_por_volta(self):
        self.receber(_sc("581012"), _sc("581013"))
        porta = Porta(buscar="encontrado")
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True):
            S.tiktok_atendimento_rodada()
        self.assertEqual(porta.rotas().count("atendimento/criar"), 1)

    def test_pedido_que_saiu_da_fila_sem_atendimento_fica_avisado(self):
        self.receber(_sc("581014", cpf=""))
        S._tiktok_config_gravar("atendimento_ligado_em", "2000-01-01", "teste")
        S._tiktok_marcar_fora_da_fila(set())
        self.assertEqual(self.estado("581014")["situacao"], "fora_da_fila")

    def test_sem_data_de_ligar_nao_marca_e_quem_volta_a_fila_volta_a_ser_candidato(self):
        self.receber(_sc("581015"))
        self.assertEqual(S._tiktok_marcar_fora_da_fila(set()), 0)
        S._tiktok_config_gravar("atendimento_ligado_em", "2000-01-01", "teste")
        S._tiktok_marcar_fora_da_fila(set())                     # "saiu" (leitura antiga)
        self.assertEqual(self.estado("581015")["situacao"], "fora_da_fila")
        self.assertIn("581015", S._tiktok_atendimento_candidatos())   # está na fila: volta a valer

    def test_sku_trocado_pelo_admin_revisa(self):
        self.receber(_sc("581016", itens=(("SK1", "0893600022", 1, "69.75"),)))
        linhas = self.fila_do("581016")
        linhas[0]["SKU"] = "0893600099"
        linhas[0]["SkuManualOverride"] = "SIM"
        gravar_como_admin(linhas)
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581016")["situacao"], "revisar")

    def test_romaneio_com_erro_tambem_prova_atendimento_a_mao(self):
        self.receber(_sc("581017"))
        S._linx_operacao_registrar("581017", "romaneio", "erro", "sem estoque", "790020", "luis")
        porta = Porta(buscar="encontrado")
        self.rodar(porta)
        self.assertNotIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581017")["situacao"], "anterior")

    def test_pedido_que_mudou_depois_do_atendimento_barra_o_caixa(self):
        self.receber(_sc("581018"))
        self.rodar(Porta(buscar="encontrado"))
        self.assertEqual(S._exp_caixa_valor_diferente("581018", 69.75), "")
        S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET status='CANCELLED', saiu_epoch=1 WHERE pedido='581018'")
        S.DB.commit()
        self.assertIn("mudou no TikTok", S._exp_caixa_valor_diferente("581018", 69.75))

    def test_cadastro_pelo_botao_nao_espera_a_porta_ocupada(self):
        self.receber(_sc("581019"))
        S.EXP_PORTA_LOCK.acquire()
        try:
            with mock.patch.object(S, "EXP_CLIQUE_ESPERA_S", 0.1), mock.patch.object(S, "EXP_CADASTRO_API", True):
                r = S.exp_cadastro_atualizar("581019", "luis")
        finally:
            S.EXP_PORTA_LOCK.release()
        self.assertEqual(r["situacao"], "ocupado")


class RevendaPelaConta(Base):

    def test_revenda_segue_a_conta_e_nao_a_filial_que_confere(self):
        gravar_como_admin([
            linha("SHP1", "0893600022", Marketplace="SHOPEE", Casa="bauru"),               # movido para Bauru conferir
            linha("MLA1", "0893600022", Marketplace="MERCADO LIVRE", Casa="bauru", ContaMercadoLivre="aracatuba"),
            linha("MLB1", "0893600022", Marketplace="MERCADO LIVRE", Casa="aracatuba", ContaMercadoLivre="bauru"),
            linha("TTK1", "0893600022", Marketplace="TIKTOK", Casa="aracatuba"),
        ])
        self.assertEqual(S._exp_revenda("SHP1"), 1)
        self.assertEqual(S._exp_revenda("MLA1"), 1)
        self.assertEqual(S._exp_revenda("MLB1"), 3)
        self.assertEqual(S._exp_revenda("TTK1"), 3)

    def test_caixa_usa_a_revenda_do_romaneio(self):
        gravar_como_admin([linha("MLB2", "0893600022", Marketplace="MERCADO LIVRE", ContaMercadoLivre="bauru")])
        S._linx_operacao_registrar("MLB2", "romaneio", "ok", {"observacao": "X", "revenda": 1}, "790010", "luis")
        corpo, _ = S._exp_caixa_corpo("MLB2", "", "83070")
        self.assertEqual(corpo["revenda"], 1)

    def test_clique_nao_espera_a_porta_ocupada_e_nao_solta_duas_vezes(self):
        gravar_como_admin([linha("MLA2", "0893600022", Marketplace="MERCADO LIVRE")])
        S.EXP_PORTA_LOCK.acquire()
        try:
            with mock.patch.object(S, "EXP_CLIQUE_ESPERA_S", 0.1):
                r = S.exp_romaneio_soltar("MLA2", "790011", "MERCADO LIVRE", "luis")
        finally:
            S.EXP_PORTA_LOCK.release()
        self.assertFalse(r["ok"])
        self.assertIn("ocupad", r["error"])
        chamadas = []

        def porta(rota, corpo, escrita=False):
            chamadas.append(rota)
            return {"ok": True, "situacao": "solto"}
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            S.exp_romaneio_soltar("MLA2", "790011", "MERCADO LIVRE", "luis")
            r2 = S.exp_romaneio_soltar("MLA2", "790011", "MERCADO LIVRE", "luis")
        self.assertEqual(len(chamadas), 1)
        self.assertFalse(r2["ok"])


class EstoqueComAtendimento(Base):

    def test_estoque_do_tiktok_desconta_sempre_e_anuncios_veem_o_atendimento(self):
        self.receber(_sc("582001", itens=(("SK1", "0893600022", 2, "50.00"),)))
        self.rodar(Porta(buscar="encontrado"))
        S.anuncio_estoque_desconta_definir("bauru", True, "dono")
        # 2ª revisão: o TikTok não tem a trava de queda dos anúncios; desconta tudo (erra para menos)
        self.assertEqual(S._tiktok_pendentes_bauru().get("0893600022"), 2)
        self.assertIn("582001", S._anuncio_pedidos_com_atendimento())


class BotaoDoPainel(Base):
    """V20.37 (07/11, pedido 587022850875164046): com o automático desligado, o painel cria o atendimento de UM pedido."""

    def criar(self, pedido, porta, custos=None):
        custos = custos or {}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "_tiktok_atendimento_custo", side_effect=lambda cod, ctx: custos.get(cod)), \
                mock.patch.object(S, "_financeiro_custos_aracatuba", return_value={}), \
                mock.patch.object(S.threading, "Thread") as t:
            t.side_effect = lambda target, **k: mock.Mock(start=target)  # roda na hora (sem thread)
            return S.tiktok_atendimento_manual_iniciar(pedido, "luis")

    def test_cria_com_o_automatico_desligado(self):
        S._tiktok_config_gravar("atendimento_auto", "0", "teste")
        self.receber(_sc("581001"))
        porta = Porta()
        r = self.criar("581001", porta)
        self.assertTrue(r["ok"])
        self.assertIn("atendimento/criar", porta.rotas())
        self.assertEqual(self.estado("581001")["situacao"], "criado")
        with self.assertRaises(ValueError):  # já criado: não cria de novo
            self.criar("581001", Porta())

    def test_pedido_que_estava_na_fila_ao_ligar_tambem_cria_pelo_botao(self):
        self.receber(_sc("581002"))
        S._tiktok_atendimento_registrar("581002", "anterior", "Já estava na fila ao ligar: atendimento à mão.")
        porta = Porta()
        self.criar("581002", porta)
        self.assertEqual(self.estado("581002")["situacao"], "criado")

    def test_kit_sem_cadastro_fica_com_o_motivo(self):
        self.receber(_sc("581003", itens=(("SK9", "KIT-2050171-2011058", 1, "69.75"),)))
        porta = Porta()
        self.criar("581003", porta)
        e = self.estado("581003")
        self.assertEqual(e["situacao"], "revisar")
        self.assertNotIn("atendimento/criar", porta.rotas())

    def test_pedido_que_nao_e_do_tiktok(self):
        with self.assertRaises(ValueError):
            self.criar("NAOEXISTE", Porta())


class PrecoRelido(Base):
    """V20.38 (07/11): pedido "a enviar" lido antes da V20.33 (CPF sim, preço não) volta a ter o detalhe aberto."""

    def test_sem_preco_guardado_a_extensao_reabre(self):
        self.receber(_sc("582001"))
        self.assertIn("582001", S.tiktok_sc_estado()["com_detalhe"], "com CPF e preço: não reabre")
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET venda_json='', detalhe_em='2026-11-06T17:44:10.184+00:00' "
                         "WHERE pedido='582001'")  # lido antes da V20.33 (ainda não guardava o preço)
            S.DB.commit()
        self.assertNotIn("582001", S.tiktok_sc_estado()["com_detalhe"], "sem o preço: reabre o detalhe")
        with S.DB_LOCK:  # lido depois da V20.33 sem preço: reabrir não resolve (não volta a cada 2 min)
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET detalhe_em='2026-11-07T10:00:00+00:00' WHERE pedido='582001'")
            S.DB.commit()
        self.assertIn("582001", S.tiktok_sc_estado()["com_detalhe"])
        with S.DB_LOCK:  # já enviado: não reabre (não precisa de atendimento)
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET detalhe_em='2026-11-06T17:44:10.184+00:00' WHERE pedido='582001'")
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET status='IN_TRANSIT' WHERE pedido='582001'")
            S.DB.commit()
        self.assertIn("582001", S.tiktok_sc_estado()["com_detalhe"])


class SubsidioDoTikTok(Base):
    """V20.39 (1º pedido real, 07/11): unidade já sem os dois descontos (119,90 − 45,10 da loja − 5,05 do TikTok = 69,75).
    A venda leva só o desconto da loja: 74,80."""

    def test_unidade_ja_sem_os_dois_descontos(self):
        det = _sc("583001", itens=(("SK1", "0893600022", 1, "69.75"),), desconto="45.1", origem="119.9")
        det["price_module"]["platform_discount_total"] = {"price_val": "5.05"}
        self.assertEqual(S._tiktok_sc_venda(det)["itens"][0]["valor"], 74.8)
        self.receber(det)
        porta = Porta()
        self.rodar(porta)
        corpo = porta.corpo("atendimento/criar")
        self.assertEqual((corpo["valor_esperado"], corpo["itens"][0]["unitario"]), (74.8, 74.8))

    def test_valor_que_nao_bate_com_nenhuma_conta_continua_revisar(self):
        det = _sc("583002", itens=(("SK1", "0893600022", 1, "60.00"),), desconto="45.1", origem="119.9")
        det["price_module"]["platform_discount_total"] = {"price_val": "5.05"}
        self.assertIn("erro", S._tiktok_sc_venda(det))


class ErroDeRegraVelha(Base):
    """V20.39: o pedido real ficou com o erro da regra da V20.38 guardado; o detalhe é reaberto uma vez."""

    def test_erro_antigo_reabre_e_o_novo_nao(self):
        self.receber(_sc("584001"))
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_sc_pedido_v2030 SET venda_json=? WHERE pedido='584001'",
                         ('{"erro":"preço das unidades (69.75) não confere com o valor do pedido (119.90)"}',))
            S.DB.commit()
        self.assertNotIn("584001", S.tiktok_sc_estado()["com_detalhe"], "erro de regra velha: reabre")
        det = _sc("584001", itens=(("SK1", "0893600022", 1, "60.00"),), desconto="45.1", origem="119.9")
        self.receber(det)  # relido; ainda não bate, mas agora com a regra nova
        self.assertIn('"regra":', S._tiktok_sc_guardado("584001")["venda"] and
                      S.DB.execute("SELECT venda_json FROM tiktok_sc_pedido_v2030 WHERE pedido='584001'").fetchone()[0])
        self.assertIn("584001", S.tiktok_sc_estado()["com_detalhe"], "erro da regra atual: não reabre a cada 2 min")
