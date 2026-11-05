"""V20.24 - Anuncios fase 2, tarefa 3: leituras do ML para criar (categoria, atributos, tarifa) - so GET."""

import json
from unittest import mock

from base import S, TesteNoromak, Cliente

SENHA = "SenhaTeste#2024"

ATRIBUTOS = [
    {"id": "BRAND", "name": "Marca", "tags": {"required": True}, "value_type": "string", "value_max_length": 255},
    {"id": "MODEL", "name": "Modelo", "tags": {"catalog_required": True}, "value_type": "string"},
    {"id": "COLOR", "name": "Cor", "tags": {"conditional_required": True}, "value_type": "list",
     "values": [{"id": "1", "name": "Preto"}, {"id": "2", "name": "Azul"}]},
    {"id": "OCULTO", "name": "Oculto", "tags": {"hidden": True, "required": True}},
    {"id": "SOLEITURA", "name": "Leitura", "tags": {"read_only": True}},
    {"id": "FIXO", "name": "Fixo", "tags": {"fixed": True}},
    {"id": "GTIN", "name": "GTIN", "tags": {}},
    {"id": "EMPTY_GTIN_REASON", "name": "Motivo", "tags": {}},
    {"id": "SIMPLES", "name": "Simples"},
]


class FalsoML:
    def __init__(self, atributos=None, erro=None):
        self.chamadas = []
        self.atributos = ATRIBUTOS if atributos is None else atributos
        self.erro = erro

    def __call__(self, caminho, **kw):
        self.chamadas.append(caminho)
        if self.erro:
            raise RuntimeError(self.erro)
        if "domain_discovery" in caminho:
            return [{"domain_id": "MLB-X", "domain_name": "Pecas", "category_id": "MLB123", "category_name": "Filtros"},
                    {"domain_id": "MLB-Y", "domain_name": "Outro", "category_id": "MLB456", "category_name": "Oleos"}]
        if caminho.endswith("/attributes"):
            return self.atributos
        if caminho.startswith("/categories/"):
            return {"id": "MLB123", "name": "Filtros", "path_from_root": [{"name": "Acessorios"}, {"name": "Filtros"}]}
        if "listing_prices" in caminho:
            return [{"sale_fee_amount": 17.5, "sale_fee_details": {"percentage_fee": 14, "fixed_fee": 0}}]
        raise AssertionError(caminho)


class CriarMLTeste(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._ANUNCIO_ATRIBUTOS_CACHE.clear()
        S._ML_TARIFA_CACHE.clear()

    def test_sugerir_categorias(self):
        falso = FalsoML()
        with mock.patch.object(S, "ml_api_get", falso):
            r = S.ml_categoria_sugerir("Filtro de oleo", "aracatuba")
        self.assertEqual(r[0], {"categoria": "MLB123", "categoria_nome": "Filtros", "dominio": "MLB-X", "dominio_nome": "Pecas"})
        self.assertEqual(len(r), 2)
        self.assertIn("domain_discovery/search?limit=5&q=Filtro", falso.chamadas[0])

    def test_titulo_vazio_400(self):
        with self.assertRaises(ValueError):
            S.ml_categoria_sugerir("  ", "aracatuba")
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]):
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/categorias?titulo=&conta=aracatuba")
        self.assertEqual(st, 400, c)

    def test_atributos_obrigatorios_e_fora_os_ocultos(self):
        with mock.patch.object(S, "ml_api_get", FalsoML()):
            r = S.ml_categoria_atributos("MLB123", "aracatuba")
        ids = {a["id"]: a for a in r["atributos"]}
        self.assertNotIn("OCULTO", ids)
        self.assertNotIn("SOLEITURA", ids)
        self.assertNotIn("FIXO", ids)
        self.assertTrue(ids["BRAND"]["obrigatorio"])
        self.assertTrue(ids["MODEL"]["obrigatorio"])
        self.assertFalse(ids["COLOR"]["obrigatorio"])
        self.assertTrue(ids["COLOR"]["condicional"])
        self.assertEqual(ids["COLOR"]["valores"][0], {"id": "1", "nome": "Preto"})
        self.assertEqual(ids["BRAND"]["max"], 255)
        self.assertFalse(ids["SIMPLES"]["obrigatorio"])
        self.assertEqual(r["caminho"], ["Acessorios", "Filtros"])
        with self.assertRaises(ValueError):
            S.ml_categoria_atributos("../x", "aracatuba")

    def test_cache_de_atributos(self):
        falso = FalsoML()
        with mock.patch.object(S, "ml_api_get", falso):
            S.ml_categoria_atributos("MLB123", "aracatuba")
            n = len(falso.chamadas)
            S.ml_categoria_atributos("MLB123", "aracatuba")
        self.assertEqual(len(falso.chamadas), n)

    def test_atributos_da_ficha_peso_medidas_sku(self):
        ficha = {"codigo": "P1", "tipo": "peca", "campos": {
            "marca": "Wurth", "modelo": "M1", "ean": "7890000000017", "peso_g": 250, "altura_cm": 10,
            "largura_cm": 5.5, "comprimento_cm": "20"}}
        r = S.anuncio_atributos_da_ficha(ficha, ATRIBUTOS)
        self.assertEqual(r["SELLER_SKU"], "P1")
        self.assertEqual(r["SELLER_PACKAGE_WEIGHT"], "250 g")
        self.assertEqual(r["SELLER_PACKAGE_HEIGHT"], "10 cm")
        self.assertEqual(r["SELLER_PACKAGE_WIDTH"], "5.5 cm")
        self.assertEqual(r["SELLER_PACKAGE_LENGTH"], "20 cm")
        self.assertEqual((r["BRAND"], r["MODEL"], r["GTIN"]), ("Wurth", "M1", "7890000000017"))
        self.assertNotIn("EMPTY_GTIN_REASON", r)

    def test_kit_sem_gtin_usa_empty_gtin_reason(self):
        # V20.24 (revisao): os valores reais do ML sao frases; escolhe a que fala de kit (o nome exato da lista)
        defs = [dict(a) for a in ATRIBUTOS]
        for a in defs:
            if a["id"] == "EMPTY_GTIN_REASON":
                a["valores"] = [{"id": "17055160", "nome": "O produto não tem código cadastrado"},
                                {"id": "17055158", "nome": "O produto é um Kit ou pack"}]
        kit = {"codigo": "K1", "tipo": "kit", "campos": {"marca": "X"}}
        self.assertEqual(S.anuncio_atributos_da_ficha(kit, defs)["EMPTY_GTIN_REASON"], "O produto é um Kit ou pack")
        corpo = S.anuncio_item_corpo({"titulo": "a", "atributos": {"EMPTY_GTIN_REASON": "O produto é um Kit ou pack"}},
                                     [], defs)
        self.assertEqual(corpo["attributes"], [{"id": "EMPTY_GTIN_REASON", "value_id": "17055158"}])
        # sem valor de kit na lista (ou sem lista): fica vazio para o Admin escolher, nunca texto livre "Kit"
        for a in defs:
            if a["id"] == "EMPTY_GTIN_REASON":
                a["valores"] = [{"id": "17055160", "nome": "O produto não tem código cadastrado"}]
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, defs))
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, ATRIBUTOS))
        corpo = S.anuncio_item_corpo({"titulo": "a", "atributos": {"EMPTY_GTIN_REASON": "Kit"}}, [], defs)
        self.assertEqual(corpo["attributes"], [])
        sem = [a for a in ATRIBUTOS if a["id"] != "EMPTY_GTIN_REASON"]
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, sem))
        kit["campos"]["ean"] = "7890000000017"
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, defs))

    def test_custo_de_aracatuba_para_o_preco(self):  # V20.24 (revisao): regra V19.77/V19.79
        produtos = {"products": [
            {"_id": "source-7", "sku": "PLAN1", "custo": 80},  # so da planilha antiga: sem custo
            {"_id": "p2", "sku": "AP1", "custo": 39.0, "filialCusto": "3",
             "custosFiliais": [{"revenda": "1", "custo_medio": 12.5}, {"revenda": "3", "custo_medio": 39.0}]},
        ]}
        with mock.patch.object(S, "precificador_dados", return_value=produtos):
            plan = S.anuncio_custo_para_preco("PLAN1")
            ap = S.anuncio_custo_para_preco("AP1")
            kit = S.anuncio_custo_para_preco("KIT2-AP1")
            kit_sem = S.anuncio_custo_para_preco("KIT2-PLAN1")
        self.assertIsNone(plan["custo"])
        self.assertIn("sem custo de Araçatuba", plan["custo_origem"])
        self.assertEqual(ap["custo"], 12.5)
        self.assertIn("Araçatuba", ap["custo_origem"])
        self.assertEqual(kit["custo"], 25.0)
        self.assertIn("peças", kit["custo_origem"])
        self.assertIsNone(kit_sem["custo"])

    def test_ml_promo_http_timeout(self):  # V20.24 (revisao): padrao igual; o Publicar passa 90 s
        vistos = []

        class Resp:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            vistos.append(timeout)
            return Resp()
        with mock.patch.object(S.urllib.request, "urlopen", urlopen),                 mock.patch.object(S, "ml_access_token", return_value=("t", "1")):
            S._ml_promo_http("GET", "/x")
            S._ml_promo_http("POST", "/items", {"a": 1}, timeout=90)
        self.assertEqual(vistos, [S.ML_HTTP_TIMEOUT, 90])

    def test_tarifa_nova_sem_frete(self):
        falso = FalsoML()
        with mock.patch.object(S, "ml_api_get", falso):
            r = S.ml_tarifa_nova(100, "gold_special", "MLB123", "aracatuba")
        self.assertEqual((r["comissao"], r["taxa_fixa"], r["tarifa"]), (0.14, 0.0, 17.5))
        self.assertIsNone(r["frete"])
        self.assertIn("depois de publicar", r["frete_texto"])
        self.assertEqual(len(falso.chamadas), 1)
        with self.assertRaises(ValueError):
            S.ml_tarifa_nova(0, "gold_special", "MLB123", "aracatuba")

    def test_amostra_guardada_uma_vez(self):
        with mock.patch.object(S, "ml_api_get", FalsoML()):
            S.ml_categoria_sugerir("a", "aracatuba")
        S._anuncio_amostra("domain_discovery", ["outra"])
        with S.DB_LOCK:
            rows = S.DB.execute("SELECT resposta FROM anuncio_amostra_v2024 WHERE tipo='domain_discovery'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("MLB123", rows[0]["resposta"])
        S._anuncio_amostra("grande", "x" * 10000)
        with S.DB_LOCK:
            r = S.DB.execute("SELECT length(resposta) n FROM anuncio_amostra_v2024 WHERE tipo='grande'").fetchone()
        self.assertLessEqual(r["n"], 6000)

    def test_conta_desconhecida_400(self):
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]):
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/atributos?categoria=MLB123&conta=outra")
        self.assertEqual(st, 400, c)

    def test_erro_do_ml_502(self):
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]), \
                mock.patch.object(S, "ml_api_get", FalsoML(erro="Mercado Livre HTTP 400: ruim")):
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/categorias?titulo=abc&conta=aracatuba")
        self.assertEqual(st, 502, c)
        self.assertIn("ruim", c["error"])

    def test_rotas_admin_ok(self):
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]), \
                mock.patch.object(S, "ml_api_get", FalsoML()):
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/categorias?titulo=filtro&conta=aracatuba")
            self.assertEqual((st, c["categorias"][0]["categoria"]), (200, "MLB123"))
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/atributos?categoria=MLB123&conta=aracatuba")
            self.assertEqual(st, 200, c)
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/tarifa?preco=100&tipo=gold_special&categoria=MLB123&conta=aracatuba")
            self.assertEqual((st, c["tarifa"]), (200, 17.5))

    def test_supervisor_403(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_2024c", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2024c", SENHA, area="admin")
        for rota in ("/api/anuncios/ml/categorias?titulo=a", "/api/anuncios/ml/atributos?categoria=MLB1",
                     "/api/anuncios/ml/tarifa?preco=1&tipo=x&categoria=MLB1"):
            self.assertEqual(sup.pedir("GET", rota)[0], 403, rota)


class RascunhoTeste(TesteNoromak):
    """V20.24 - tarefa 4: rascunho do anuncio e corpo do item."""

    def setUp(self):
        super().setUp()
        S._ANUNCIO_ATRIBUTOS_CACHE.clear()
        S.ficha_salvar("P1", {"nome": "Filtro"}, "t", None)
        S.ficha_salvar("P2", {"nome": "Outro"}, "t", None)
        self.ml = FalsoML()
        self._ps = [mock.patch.object(S, "ml_api_get", self.ml),
                    mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"])]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)

    def foto(self, cod="P1"):
        with S.DB_LOCK:
            cur = S.DB.execute("INSERT INTO anuncio_foto_v2024(ficha_codigo,arquivo,tipo,ordem) VALUES(?,?,?,0)",
                               (cod, "x.jpg", "jpg"))
            S.DB.commit()
            return cur.lastrowid

    def novo(self, **extra):
        d = {"codigo": "p1", "conta": "aracatuba", "titulo": "Filtro de oleo"}
        d.update(extra)
        return S.anuncio_rascunho_salvar(d, "t", None)

    def test_criar_e_ler_rascunho(self):
        r = self.novo(preco="49,90", quantidade=3, frete_gratis=True, garantia="90 dias")
        self.assertEqual((r["ficha_codigo"], r["situacao"], r["versao"], r["preco"], r["quantidade"]),
                         ("P1", "rascunho", 1, 49.9, 3))
        self.assertIs(r["frete_gratis"], True)
        self.assertEqual(r["tipo_anuncio"], "gold_special")
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["titulo"], "Filtro de oleo")
        self.assertEqual([x["id"] for x in S.anuncio_rascunhos("P1")], [r["id"]])
        self.assertEqual(S.anuncio_rascunhos("P2"), [])
        with self.assertRaises(ValueError):
            S.anuncio_rascunho_salvar({"codigo": "NAOEXISTE", "conta": "aracatuba"}, "t", None)

    def test_ordem_titulo_categoria_atributos(self):
        with self.assertRaises(ValueError):
            self.novo(titulo="", categoria="MLB123")
        with self.assertRaises(ValueError):
            self.novo(atributos={"BRAND": "X"})
        r = self.novo(categoria="MLB123", atributos={"BRAND": "Wurth", "VAZIO": "  "})
        self.assertEqual(r["atributos"], {"BRAND": "Wurth"})
        with self.assertRaises(ValueError):  # apagar o titulo com categoria
            S.anuncio_rascunho_salvar({"id": r["id"], "titulo": ""}, "t", r["versao"])

    def test_trocar_categoria_guarda_o_digitado(self):
        r = self.novo(categoria="MLB123", atributos={"BRAND": "Wurth", "COLOR": "Preto"})
        self.ml.atributos = [a for a in ATRIBUTOS if a["id"] != "COLOR"]
        r2 = S.anuncio_rascunho_salvar({"id": r["id"], "categoria": "MLB999"}, "t", r["versao"])
        self.assertEqual(r2["categoria"], "MLB999")
        self.assertEqual(r2["atributos"]["BRAND"], "Wurth")
        self.assertNotIn("COLOR", r2["atributos"])
        self.assertEqual(r2["atributos"]["_guardados"], {"MLB123": {"COLOR": "Preto"}})
        self.ml.atributos = ATRIBUTOS
        r3 = S.anuncio_rascunho_salvar({"id": r["id"], "categoria": "MLB123"}, "t", r2["versao"])
        self.assertEqual(r3["atributos"], {"BRAND": "Wurth", "COLOR": "Preto"})

    def test_categoria_sem_resposta_do_ml_recusa(self):
        r = self.novo(categoria="MLB123", atributos={"BRAND": "Wurth"})
        self.ml.erro = "Mercado Livre HTTP 500"
        with self.assertRaises(ValueError):
            S.anuncio_rascunho_salvar({"id": r["id"], "categoria": "MLB777"}, "t", r["versao"])
        atual = S.anuncio_rascunho_ler(r["id"])
        self.assertEqual((atual["categoria"], atual["versao"]), ("MLB123", r["versao"]))

    def test_publicado_nao_edita(self):
        r = self.novo()
        for sit in ("publicado", "publicando", "incerto"):
            with S.DB_LOCK:
                S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao=? WHERE id=?", (sit, r["id"]))
                S.DB.commit()
            with self.assertRaises(ValueError):
                S.anuncio_rascunho_salvar({"id": r["id"], "titulo": "Novo"}, "t", r["versao"])
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao='erro' WHERE id=?", (r["id"],))
            S.DB.commit()
        self.assertEqual(S.anuncio_rascunho_salvar({"id": r["id"], "titulo": "Novo"}, "t", r["versao"])["titulo"], "Novo")

    def test_conflito_409(self):
        r = self.novo()
        S.anuncio_rascunho_salvar({"id": r["id"], "preco": 10}, "t", r["versao"])
        with self.assertRaises(S.FichaConflito) as cm:
            S.anuncio_rascunho_salvar({"id": r["id"], "preco": 11}, "t", r["versao"])
        self.assertEqual(cm.exception.atual["preco"], 10.0)
        st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho", {"id": r["id"], "preco": 12, "versao": r["versao"]})
        self.assertEqual((st, c["error"]), (409, "CONFLITO"), c)
        self.assertEqual(c["atual"]["preco"], 10.0)
        st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho", {"id": r["id"], "preco": 12, "versao": 2})
        self.assertEqual((st, c["rascunho"]["preco"]), (200, 12.0), c)
        st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho",
                                   {"codigo": "P1", "conta": "outra", "titulo": "x"})
        self.assertEqual(st, 400, c)
        st, c = self.cliente.pedir("GET", "/api/anuncios/rascunhos?codigo=P1")
        self.assertEqual((st, len(c["rascunhos"])), (200, 1), c)

    def test_foto_de_outra_ficha_recusada(self):
        minha, alheia = self.foto("P1"), self.foto("P2")
        with self.assertRaises(ValueError):
            self.novo(fotos=[minha, alheia])
        with self.assertRaises(ValueError):
            self.novo(fotos=[999999])
        r = self.novo(fotos=[minha, minha])
        self.assertEqual(r["fotos"], [minha])

    def test_mudar_volta_para_rascunho(self):
        r = self.novo()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao='validado' WHERE id=?", (r["id"],))
            S.DB.commit()
        igual = S.anuncio_rascunho_salvar({"id": r["id"], "titulo": "Filtro de oleo"}, "t", r["versao"])
        self.assertEqual((igual["situacao"], igual["versao"]), ("validado", r["versao"]))
        r2 = S.anuncio_rascunho_salvar({"id": r["id"], "preco": 5}, "t", r["versao"])
        self.assertEqual((r2["situacao"], r2["versao"]), ("rascunho", r["versao"] + 1))

    def test_corpo_do_item(self):
        r = self.novo(categoria="MLB123", preco=49.9, quantidade=2, frete_gratis=True, garantia="90 dias",
                      tipo_anuncio="gold_pro",
                      atributos={"BRAND": "Wurth", "COLOR": "preto", "EMPTY_GTIN_REASON": "O produto é um kit ou pack", "MODEL": ""})
        defs = [{"id": "COLOR", "valores": [{"id": "1", "nome": "Preto"}]},
                {"id": "EMPTY_GTIN_REASON", "valores": [{"id": "17055158", "nome": "O produto é um kit ou pack"},
                                                       {"id": "17055160", "nome": "O produto não tem código cadastrado"}]}]
        c = S.anuncio_item_corpo(r, ["MLB_P1", "MLB_P2"], defs)
        self.assertEqual((c["title"], c["category_id"], c["price"], c["currency_id"], c["available_quantity"]),
                         ("Filtro de oleo", "MLB123", 49.9, "BRL", 2))
        self.assertEqual((c["buying_mode"], c["listing_type_id"], c["condition"]), ("buy_it_now", "gold_pro", "new"))
        self.assertEqual(c["pictures"], [{"id": "MLB_P1"}, {"id": "MLB_P2"}])
        self.assertEqual(c["shipping"], {"mode": "me2", "free_shipping": True})
        self.assertEqual(sorted(c["attributes"], key=lambda a: a["id"]),
                         [{"id": "BRAND", "value_name": "Wurth"}, {"id": "COLOR", "value_id": "1"},
                          {"id": "EMPTY_GTIN_REASON", "value_id": "17055158"}])
        self.assertEqual([t["id"] for t in c["sale_terms"]], ["WARRANTY_TYPE", "WARRANTY_TIME"])
        self.assertEqual(c["sale_terms"][1]["value_name"], "90 dias")
        # V20.24 (revisao): garantia so no formato "<n> dias|meses|anos"; outro texto nao vai
        for texto, esperado in (("6 Meses", "6 meses"), ("1 ano", "1 anos"), (" 30  dias ", "30 dias"),
                                ("3 mês", "3 meses")):
            ct = S.anuncio_item_corpo({"titulo": "a", "garantia": texto}, [])
            self.assertEqual(ct["sale_terms"][1]["value_name"], esperado, texto)
        for texto in ("garantia de fabrica", "90", "dias", "6 meses de fabrica"):
            self.assertEqual(S.anuncio_item_corpo({"titulo": "a", "garantia": texto}, [])["sale_terms"], [], texto)
        sem = S.anuncio_item_corpo({"titulo": "a", "atributos": {"X": "y", "_guardados": {}}}, [])
        self.assertEqual((sem["sale_terms"], sem["attributes"]), ([], [{"id": "X", "value_name": "y"}]))
        self.assertIs(sem["shipping"]["free_shipping"], False)
