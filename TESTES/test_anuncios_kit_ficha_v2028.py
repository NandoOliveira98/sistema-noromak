"""V20.28 - Anuncios: seletor "Este produto e: Peca / Kit" e composicao do kit na ficha.

Caso real (dono, 05/11): o SKU "0893600022-0893764272" (duas pecas do Apollo) era lido como prefixo de anuncio +
codigo, virava ficha de peca e o "+ Novo kit" com ele era recusado."""

import json
from unittest import mock

from base import S, SENHA_ADMIN, TesteNoromak, Cliente, linha

SENHA = "SenhaTeste#2028"
DUPLO = "0893600022-0893764272"
COMPS = [{"sku": "0893600022", "quantidade": 1}, {"sku": "0893764272", "quantidade": 2}]


def inserir(anuncio_id="MLB1", ficha=DUPLO, controle="noromak", modo="manual", conta="aracatuba"):
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
            "estoque_modo) VALUES('ml',?,?,?,?,?,?,?)",
            (conta, anuncio_id, ficha, ficha, controle, "T " + anuncio_id, modo))
        S.DB.commit()


def modo(anuncio_id="MLB1", conta="aracatuba"):
    with S.DB_LOCK:
        return S.DB.execute("SELECT estoque_modo FROM anuncio_canal_v2023 WHERE canal='ml' AND conta=? AND anuncio_id=?",
                            (conta, anuncio_id)).fetchone()[0]


def cadastro():
    bruto, _, _ = S.get_storage("conf_kits")
    return {S.normalize(k["sku_kit"]): k for k in json.loads(bruto or "[]")}


def fila(linhas):
    atual, _, _ = S.get_storage("conf_pedidos")
    S.save_storage("conf_pedidos", atual, json.dumps(linhas), True, actor="teste", actor_role="admin")


def puxada_cria_peca(codigo=DUPLO):
    """Como a puxada do ML: ficha criada pelo ficha_importar (origem anuncio_ml), tipo peca."""
    S.ficha_importar(codigo, {"nome": "Kit duas pecas", "marca": "Wurth"}, "anuncio_ml")


class KitFichaTeste(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.apollo = {}

        def stock(codigo, operador="", wait_seconds=0):
            d = self.apollo.get(codigo) or {}
            return {"ok": True, "estoques": [{"revenda": r, "disponivel": v, "respondeu": v is not None}
                                             for r, v in d.items()]}
        p = mock.patch.object(S, "crm_stock_lookup", side_effect=stock)
        p.start()
        self.addCleanup(p.stop)
        S._ANUNCIO_APOLLO_CHAMADAS.clear()

    def test_heuristica_do_hifen(self):
        self.assertTrue(S.anuncio_sku_parece_kit(DUPLO))
        self.assertTrue(S.anuncio_sku_parece_kit("6007.003.315.00.8-0893764272"))
        for sku in ("151490-0893764272", "157507-0893556005", "81794-32052153", "KIT2-0893600022",
                    "0893600022/0893764272", "0893600022", "WAP331-ABCDEFG", "0893600022-0893764272-1", ""):
            self.assertFalse(S.anuncio_sku_parece_kit(sku), sku)

    def test_peca_da_puxada_vira_kit_e_estoque_usa_a_composicao(self):
        puxada_cria_peca()
        self.assertEqual(S.ficha_ler(DUPLO)["tipo"], "peca")
        d = S.anuncio_ficha_payload(DUPLO)
        self.assertTrue(d["kit_suspeito"])
        lista = {f["codigo"]: f for f in S.anuncios_lista_payload()["fichas"]}
        self.assertTrue(lista[S.normalize(DUPLO)]["kit_suspeito"])
        # o "+ Novo kit" de antes continua recusado (sem a escolha explicita)
        with self.assertRaises(S.KitCodigoDeProduto):
            S.kit_salvar(DUPLO, "", COMPS, "luis", None)
        res = S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis", COMPS, "Kit duas pecas")
        self.assertEqual(res["tipo"], "kit")
        row = cadastro()[S.normalize(DUPLO)]
        self.assertTrue(row["ativo"])
        self.assertEqual([(c["sku"], c["quantidade"]) for c in row["componentes"]],
                         [("0893600022", 1), ("0893764272", 2)])
        f = S.ficha_ler(DUPLO)
        self.assertEqual((f["tipo"], f["tipo_confirmado_por"]), ("kit", "luis"))
        self.assertEqual(f["campos"]["marca"], "Wurth")  # campos da ficha ficam
        d = S.anuncio_ficha_payload(DUPLO)
        self.assertEqual(d["tipo"], "kit")
        self.assertFalse(d["kit_suspeito"])
        self.assertEqual(len(d["composicao"]["componentes"]), 2)
        # estoque do anuncio pela composicao: 0893600022 da 7, 0893764272 da 9//2 = 4
        inserir()
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        self.apollo = {"0893600022": {1: 7}, "0893764272": {1: 9}}
        S.anuncios_apollo_ler("0893600022", "aracatuba")
        S.anuncios_apollo_ler("0893764272", "aracatuba")
        it = next(i for i in S.anuncios_estoque_calcular("aracatuba") if i["anuncio_id"] == "MLB1")
        self.assertEqual(it["calculado"], 4, it)

    def test_produto_real_so_com_confirmacao(self):
        atual, _, _ = S.get_storage("conf_produtos")
        S.save_storage("conf_produtos", atual, json.dumps([{"SKU": DUPLO, "Descrição": "Peca"}]), False,
                       actor="admin_teste", actor_role="admin", source_screen="teste", change_method="teste",
                       client_ip="127.0.0.1")
        with self.assertRaises(S.KitCodigoDeProduto) as cm:
            S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis", COMPS)
        self.assertIn("TODA venda", str(cm.exception))
        self.assertNotIn(S.normalize(DUPLO), cadastro())
        S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis", COMPS, confirmar_produto=True)
        self.assertTrue(cadastro()[S.normalize(DUPLO)]["ativo"])

    def test_peca_criada_pelo_admin_pede_confirmacao(self):
        S.ficha_salvar("PECA28", {"nome": "Minha peca"}, "luis", None)
        with self.assertRaises(S.KitCodigoDeProduto):
            S.anuncio_ficha_tipo_definir("PECA28", "kit", "luis", [{"sku": "P1", "quantidade": 2}])
        S.anuncio_ficha_tipo_definir("PECA28", "kit", "luis", [{"sku": "P1", "quantidade": 2}], confirmar_produto=True)
        self.assertEqual(S.anuncio_ficha_payload("PECA28")["tipo"], "kit")

    def test_voltar_para_peca_desativa_e_fila_recusa(self):
        puxada_cria_peca()
        S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis", COMPS)
        fila([linha("PED1", "0893600022", 1, uid=f"KITDB_PED1_{S.normalize(DUPLO)}_0893600022_0",
                    SkuKitOrigem=DUPLO)])
        with self.assertRaises(ValueError) as cm:
            S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")
        self.assertIn("fila de conferência", str(cm.exception))
        self.assertTrue(cadastro()[S.normalize(DUPLO)]["ativo"])
        self.assertEqual(S.ficha_ler(DUPLO)["tipo"], "kit")
        fila([])
        S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")
        row = cadastro()[S.normalize(DUPLO)]
        self.assertIs(row["ativo"], False)  # desativado, nunca apagado
        self.assertEqual(len(row["componentes"]), 2)
        d = S.anuncio_ficha_payload(DUPLO)
        self.assertEqual(d["tipo"], "peca")
        self.assertIsNone(d["composicao"])
        self.assertEqual(len(d["kit_inativo"]["componentes"]), 2)
        self.assertFalse(d["kit_suspeito"])
        # a puxada nao desfaz a escolha do Admin
        S._ficha_garantir_kit(DUPLO, "importacao:anuncio_ml")
        self.assertEqual(S.ficha_ler(DUPLO)["tipo"], "peca")
        # Kit de novo religa o kit com as pecas que ja tinha
        S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis")
        self.assertTrue(cadastro()[S.normalize(DUPLO)]["ativo"])
        self.assertEqual(S.anuncio_ficha_payload(DUPLO)["tipo"], "kit")

    def test_kit_sem_pecas_recusado(self):
        puxada_cria_peca()
        with self.assertRaises(ValueError) as cm:
            S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis")
        self.assertIn("Composição do kit", str(cm.exception))

    def test_automatico_volta_a_manual_quando_o_tipo_muda(self):
        puxada_cria_peca()
        S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")  # confirma peca: o Automatico pode ligar
        inserir(modo="manual")
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        self.assertEqual(modo(), "automatico")
        res = S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis", COMPS)
        self.assertEqual(res["voltaram_manual"], ["MLB1"])
        self.assertEqual(modo(), "manual")
        log = S.anuncio_estoque_log("ml", "aracatuba", "MLB1")
        self.assertIn("peca -> kit", log[0]["detalhe"])
        # confirmar o mesmo tipo nao mexe no modo
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        S.anuncio_ficha_tipo_definir(DUPLO, "kit", "luis")
        self.assertEqual(modo(), "automatico")
        S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")
        self.assertEqual(modo(), "manual")

    def test_sku_duplo_trava_o_automatico_ate_confirmar(self):
        puxada_cria_peca()
        inserir()
        with self.assertRaises(ValueError) as cm:
            S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        self.assertIn("dois códigos", str(cm.exception))
        self.assertEqual(modo(), "manual")
        # anuncio que ja estava em Automatico (antes da V20.28) nao envia ate confirmar
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='automatico' WHERE anuncio_id='MLB1'")
            S.DB.commit()
        it = next(i for i in S.anuncios_estoque_calcular("aracatuba") if i["anuncio_id"] == "MLB1")
        self.assertIsNone(it["calculado"])
        self.assertIn("Peça ou Kit", it["motivo"])
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='manual' WHERE anuncio_id='MLB1'")
            S.DB.commit()
        self.assertTrue(S.anuncio_ficha_payload(DUPLO)["anuncios"][0]["kit_suspeito"])
        S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        self.assertEqual(modo(), "automatico")
        self.assertFalse(S.anuncio_ficha_payload(DUPLO)["anuncios"][0]["kit_suspeito"])
        # prefixo de anuncio comum nao trava
        inserir(anuncio_id="MLB2", ficha="151490-0893764272")
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB2", "automatico", "luis")

    def test_peca_confirmada_com_traco_usa_o_codigo_inteiro(self):
        """Peca confirmada = 1 x o codigo inteiro no Apollo e no pendente (nunca so "0893764272")."""
        puxada_cria_peca()
        inserir()
        fila([linha("PED1", DUPLO, 3), linha("PED2", "0893764272", 5)])
        # sem confirmar: selo + Automatico recusado (como hoje)
        self.assertTrue(S.anuncio_ficha_payload(DUPLO)["kit_suspeito"])
        with self.assertRaises(ValueError):
            S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        S.anuncio_ficha_tipo_definir(DUPLO, "peca", "luis")
        S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        chamados = []
        stock_antigo = S.crm_stock_lookup.side_effect

        def stock(codigo, operador="", wait_seconds=0):
            chamados.append(codigo)
            return stock_antigo(codigo, operador, wait_seconds)
        S.crm_stock_lookup.side_effect = stock
        self.assertEqual(S._anuncios_codigos_automatico("aracatuba"), {S.normalize(DUPLO): S.normalize(DUPLO)})
        self.apollo = {S.normalize(DUPLO): {1: 10}, "0893764272": {1: 50}}
        S.anuncios_apollo_ler(S.normalize(DUPLO), "aracatuba")
        self.assertEqual(chamados, [S.normalize(DUPLO)])
        it = next(i for i in S.anuncios_estoque_calcular("aracatuba") if i["anuncio_id"] == "MLB1")
        self.assertEqual([(p["codigo"], p["quantidade"]) for p in it["pecas"]], [(S.normalize(DUPLO), 1)])
        self.assertEqual(it["pecas"][0]["a_baixar"], 3)  # so a linha do SKU inteiro; a de 0893764272 nao
        self.assertEqual(it["calculado"], 7)
        pend = S.pendentes_por_filial(("aracatuba",))["aracatuba"]
        self.assertEqual(pend.get(S._exp_norm_codigo(DUPLO)), 3)
        self.assertEqual(pend.get("0893764272"), 5)

    def test_busca_de_produtos(self):
        S.ficha_importar("0893600022", {"nome": "Desengripante 300ml"}, "anuncio_ml")
        r = S.anuncio_produtos_buscar("desengrip")
        self.assertEqual([i["codigo"] for i in r["itens"]], ["0893600022"])
        self.assertFalse(r["exato"])
        self.assertTrue(S.anuncio_produtos_buscar("0893600022")["exato"])
        self.assertFalse(S.anuncio_produtos_buscar("NAOEXISTE99")["exato"])


class KitFichaRotasTeste(TesteNoromak):

    def usuario(self, nome, consulta=False):
        with S.DB_LOCK:
            S.upsert_app_user(nome, nome, "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            if consulta:
                S.DB.execute("UPDATE app_users SET somente_leitura=1 WHERE username=?", (nome,))
            S.DB.commit()
        c = Cliente(self.url_base)
        c.entrar(nome, SENHA, area="admin")
        return c

    def test_admin_e_codigo_de_produto(self):
        puxada_cria_peca()
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha/tipo", {"codigo": DUPLO, "tipo": "kit", "componentes": COMPS})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["tipo"], "kit")
        atual, _, _ = S.get_storage("conf_produtos")
        S.save_storage("conf_produtos", atual, json.dumps([{"SKU": "REAL28", "Descrição": "Peca"}]), False,
                       actor="admin_teste", actor_role="admin", source_screen="teste", change_method="teste",
                       client_ip="127.0.0.1")
        corpo = {"codigo": "REAL28", "tipo": "kit", "componentes": [{"sku": "P1", "quantidade": 2}]}
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha/tipo", corpo)
        self.assertEqual((st, c.get("codigo_erro")), (400, "CODIGO_DE_PRODUTO"), c)
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha/tipo", {**corpo, "confirmar_produto": True})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("GET", "/api/anuncios/produtos?busca=REAL")
        self.assertEqual(st, 200, c)
        self.assertIn("REAL28", [i["codigo"] for i in c["itens"]])

    def test_supervisor_e_consulta_nao_gravam(self):
        puxada_cria_peca()
        sup = self.usuario("sup_2028")
        self.assertEqual(sup.pedir("GET", "/api/anuncios/produtos?busca=089")[0], 200)
        st, _ = sup.pedir("POST", "/api/anuncios/ficha/tipo", {"codigo": DUPLO, "tipo": "peca"})
        self.assertEqual(st, 403)
        con = self.usuario("con_2028", consulta=True)
        st, _ = con.pedir("POST", "/api/anuncios/ficha/tipo", {"codigo": DUPLO, "tipo": "peca"})
        self.assertEqual(st, 403)
        self.assertEqual(S.ficha_ler(DUPLO)["tipo_confirmado_por"], "")


if __name__ == "__main__":
    import unittest
    unittest.main()
