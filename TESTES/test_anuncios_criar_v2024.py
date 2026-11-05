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
        kit = {"codigo": "K1", "tipo": "kit", "campos": {"marca": "X"}}
        self.assertEqual(S.anuncio_atributos_da_ficha(kit, ATRIBUTOS)["EMPTY_GTIN_REASON"], "Kit")
        sem = [a for a in ATRIBUTOS if a["id"] != "EMPTY_GTIN_REASON"]
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, sem))
        kit["campos"]["ean"] = "7890000000017"
        self.assertNotIn("EMPTY_GTIN_REASON", S.anuncio_atributos_da_ficha(kit, ATRIBUTOS))

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
