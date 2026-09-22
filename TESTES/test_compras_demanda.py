"""V18.00 — Demanda de Compra (versão provisória).

Vendas vêm do ML e da Shopee (vendas_v1799); custo e curva vêm da lista do
Precificador, cujo SKU é o código Apollo. O anúncio pode ter outro formato
(157507-0893556005, kit2-X, A/B) e precisa virar o código certo — senão a peça
aparece como "não vendeu" e o sistema deixa de sugerir compra.
"""

import json
from datetime import datetime, timedelta

from base import S, TesteNoromak


def hoje(delta=0):
    return (datetime.now(S.BR_TZ).date() - timedelta(days=delta)).isoformat()


class DemandaCompra(TesteNoromak):

    def setUp(self):
        super().setUp()
        produtos = [
            {"sku": "0893556005", "produto": "LIMPA RADIADOR 500 ML", "custo": 21.4, "curva": "A"},
            {"sku": "0892332041", "produto": "LIMPA PARA BRISA 100 ML", "custo": 9.8, "curva": "B"},
            {"sku": "0893600022", "produto": "FLANELA MICROFIBRA", "custo": 5.0, "curva": "C"},
            {"sku": "4045", "produto": "HS334 VEDADOR ANAEROBICO", "custo": 16.32, "curva": "A"},
            {"sku": "920.1045-0", "produto": "COMPRESSOR SCHULZ CSI", "custo": 1289.0, "curva": "B"},
        ]
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")
        linhas = [
            ("ml", "1", "157507-0893556005", 3, hoje(1)),      # prefixo-código
            ("shopee", "S1", "0893556005", 2, hoje(2)),        # código puro
            ("ml", "2", "kit2-0892332041", 4, hoje(3)),        # kit de 2 -> 8 unidades
            ("ml", "3", "0893600022/0892332041", 1, hoje(3)),  # anúncio com duas peças
            ("ml", "4", "0893556005", 50, hoje(90)),           # fora do período de 60 dias
            ("ml", "5", "4045", 7, hoje(5)),                   # outro fornecedor
            ("ml", "6", "ANUNCIO-SEM-CADASTRO", 1, hoje(5)),
        ]
        with S.DB_LOCK:
            S.DB.executemany(
                "INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                [(m, o, sku, d + "T10:00:00-03:00", d, sku, q, 10.0, S.now_iso()) for m, o, sku, q, d in linhas],
            )
            S.DB.commit()

    def salvar(self, **corpo):
        return self.cliente.pedir("POST", "/api/compras/fornecedores", corpo)

    def test_fornecedor_exige_regra(self):
        status, corpo = self.salvar(nome="wurth", pedido_minimo=3000)
        self.assertEqual(status, 400, corpo)

    def test_demanda_soma_ml_shopee_kits_e_combinados(self):
        status, corpo = self.salvar(nome="wurth", pedido_minimo=3000, prazo=7, cobertura=30, prefixos="089")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["fornecedor"]["nome"], "WURTH")
        status, d = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=WURTH&dias=60")
        self.assertEqual(status, 200, d)
        por = {x["codigo"]: x for x in d["itens"]}
        self.assertEqual(set(por), {"0893556005", "0892332041", "0893600022"}, "entrou peça de outro fornecedor")
        self.assertEqual((por["0893556005"]["vendido_ml"], por["0893556005"]["vendido_shopee"]), (3, 2),
                         "venda de 90 dias atrás entrou, ou o formato prefixo-código não casou")
        self.assertEqual(por["0892332041"]["vendido"], 9, "kit2 deve contar 2 unidades por venda (+1 do combinado)")
        self.assertEqual(por["0893600022"]["vendido"], 1)
        self.assertEqual(por["0893556005"]["custo"], 21.4)
        self.assertEqual(d["anuncios_sem_codigo"], 1)

    def test_regras_por_palavra_e_lista_de_codigos(self):
        self.salvar(nome="SCHULZ", palavras="schulz", pedido_minimo=5000)
        self.salvar(nome="HS BOND", codigos="4045\n4018", pedido_minimo=1500)
        _, schulz = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=SCHULZ")
        _, hs = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=HS%20BOND")
        self.assertEqual([x["codigo"] for x in schulz["itens"]], ["920.1045-0"])
        self.assertEqual([(x["codigo"], x["vendido"]) for x in hs["itens"]], [("4045", 7)])

    def test_excluir_fornecedor(self):
        self.salvar(nome="WURTH", prefixos="089")
        status, corpo = self.salvar(nome="WURTH", excluir=True)
        self.assertEqual(status, 200)
        self.assertEqual(corpo["fornecedores"], [])

    def test_kit_rotulo_nao_explode_quantidade(self):
        # V18.19: "KIT" seguido de CÓDIGOS de produto (KIT10291406 - 99090203,
        # kit27200154) é rótulo de kit, NÃO o formato "KITn-X". A quantidade não
        # pode ser lida como milhões. Kit homogêneo real (kit10-X) continua valendo.
        produtos = [
            {"sku": "99090203", "produto": "CHAVE S 050", "custo": 12.0, "curva": "A"},
            {"sku": "10396473", "produto": "COMPONENTE DO KIT", "custo": 5.0, "curva": "B"},
            {"sku": "27200154", "produto": "OUTRA PECA", "custo": 8.0, "curva": "C"},
        ]
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")
        linhas = [
            ("ml", "V1", "99090203", 6, hoje(1)),                 # venda direta real
            ("ml", "V2", "KIT10291406 - 99090203", 4, hoje(1)),   # rótulo: NAO vira 10.291.406 un
            ("ml", "V3", "kit27200154", 3, hoje(1)),              # rótulo sem separador
            ("ml", "V4", "kit10-10396473", 2, hoje(1)),           # kit homogeneo legitimo: 10x
        ]
        with S.DB_LOCK:
            S.DB.executemany(
                "INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                [(m, o, sku, d + "T10:00:00-03:00", d, sku, q, 10.0, S.now_iso()) for m, o, sku, q, d in linhas],
            )
            S.DB.commit()
        self.salvar(nome="VOLARE", pedido_minimo=2000, prazo=7, cobertura=30, codigos="99090203\n10396473\n27200154")
        status, d = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=VOLARE&dias=30")
        self.assertEqual(status, 200, d)
        por = {x["codigo"]: x for x in d["itens"]}
        # a venda direta (6) + os 4 do rótulo que casam pelo código depois do traço = 10; NUNCA milhões
        self.assertEqual(por["99090203"]["vendido"], 10, "KIT-rótulo explodiu a quantidade do 99090203")
        self.assertLess(por["99090203"]["vendido"], 1000)
        # kit homogêneo legítimo continua contando 10 unidades por venda (2 vendas -> 20)
        self.assertEqual(por["10396473"]["vendido"], 20, "kit10-X deveria contar 10 unidades por venda")
        # kit27200154 (rótulo sem separador) não pode virar 2,7 milhões do 27200154
        self.assertEqual(por["27200154"]["vendido"], 0)

    def test_infer_kit_prefixo_exige_separador(self):
        # Unidade pura da regra: só "KITn<sep>base" com n pequeno é kit inferido.
        self.assertIsNone(S._infer_quantity_kit_from_sku("kit27200154"))     # sem separador
        self.assertIsNone(S._infer_quantity_kit_from_sku("KIT10291406 - 99090203"))  # n gigante
        self.assertIsNone(S._infer_quantity_kit_from_sku("KIT2X"))           # sem separador
        k = S._infer_quantity_kit_from_sku("kit2-0893764272")
        self.assertEqual((k["componentes"][0]["sku"], k["componentes"][0]["quantidade"]), ("0893764272", 2))
        k = S._infer_quantity_kit_from_sku("KIT10-10396473")
        self.assertEqual(k["componentes"][0]["quantidade"], 10)
