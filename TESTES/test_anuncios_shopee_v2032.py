"""V20.32 - criar anuncio na Shopee (Araçatuba) pela plataforma de anuncios.

Desenho: docs/superpowers/specs/2026-11-06-anuncios-shopee-design.md. A Shopee e simulada (_shopee_chamar)."""

import json
from unittest import mock

from base import S, TesteNoromak

from test_agente_anuncios_v2031 import foto_na_ficha


class ShopeeFalsa:
    """Responde como a Shopee (formato do SDK gerado da doc)."""

    def __init__(self):
        self.chamadas = []
        self.add_item = {"response": {"item_id": 987654321}}
        self.add_item_erro = None
        self.upload_erro = None
        self.canais = [
            {"logistics_channel_id": 90001, "logistics_channel_name": "Shopee Xpress", "enabled": True,
             "fee_type": "SIZE_INPUT", "weight_limit": {"item_max_weight": 30, "item_min_weight": 0}},
            {"logistics_channel_id": 90002, "logistics_channel_name": "Desligado", "enabled": False, "fee_type": "SIZE_INPUT"},
            {"logistics_channel_id": 90003, "logistics_channel_name": "Tamanho", "enabled": True, "fee_type": "SIZE_SELECTION"}]
        self.marca_obrigatoria = False

    def __call__(self, method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False):
        self.chamadas.append((method, path, params, payload))
        if path.endswith("get_item_limit"):
            return {"response": {"item_name_length_limit": {"min_limit": 10, "max_limit": 100},
                                 "item_description_length_limit": {"min_limit": 20, "max_limit": 3000},
                                 "item_image_count_limit": {"min_limit": 1, "max_limit": 9}}}
        if path.endswith("get_category"):
            return {"response": {"category_list": [
                {"category_id": 100, "parent_category_id": 0, "display_category_name": "Automotivo", "has_children": True},
                {"category_id": 200, "parent_category_id": 100, "display_category_name": "Limpeza", "has_children": False}]}}
        if path.endswith("category_recommend"):
            return {"response": {"category_id": [200, 100]}}
        if path.endswith("get_attribute_tree"):
            return {"response": {"list": [{"category_id": 200, "attribute_tree": [
                {"attribute_id": 11, "name": "Volume", "mandatory": True,
                 "attribute_info": {"input_type": 3, "attribute_unit_list": ["ml", "L"]}},
                {"attribute_id": 12, "name": "Tipo", "mandatory": True, "attribute_info": {"input_type": 1},
                 "attribute_value_list": [{"value_id": 501, "name": "Espuma"}, {"value_id": 502, "name": "Líquido"}]},
                {"attribute_id": 13, "name": "Cor", "mandatory": False, "attribute_info": {"input_type": 3}}]}]}}
        if path.endswith("get_brand_list"):
            if params.get("offset") == 0:
                return {"response": {"brand_list": [{"brand_id": 7001, "original_brand_name": "TUNAP"}], "has_next_page": True,
                                     "next_offset": 100, "is_mandatory": self.marca_obrigatoria}}
            return {"response": {"brand_list": [{"brand_id": 7002, "original_brand_name": "Vonixx"}], "has_next_page": False}}
        if path.endswith("get_channel_list"):
            return {"response": {"logistics_channel_list": self.canais}}
        if path.endswith("upload_image"):
            if self.upload_erro:
                raise self.upload_erro
            return {"response": {"image_info": {"image_id": f"img{len(self.chamadas)}"}}}
        if path.endswith("add_item"):
            if self.add_item_erro:
                raise self.add_item_erro
            return self.add_item
        raise AssertionError(f"chamada inesperada {path}")


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._SHOPEE_LEITURA_CACHE.clear()
        self.shopee = ShopeeFalsa()
        for alvo, valor in (("_shopee_chamar", self.shopee), ("shopee_refresh_access_token", lambda *_a, **_k: {}),
                            ("anuncio_foto_bytes", lambda _fid: (b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg"))):
            p = mock.patch.object(S, alvo, new=valor)
            p.start()
            self.addCleanup(p.stop)
        S.ficha_salvar("TUNAP109", {"nome": "Limpa Interior Tunap 109 Espuma 500ml", "marca": "Tunap", "ncm": "3402.90.39",
                                    "peso_g": 600, "altura_cm": 30, "largura_cm": 15, "comprimento_cm": 14.2}, "admin", None)
        foto_na_ficha("TUNAP109")
        with S.DB_LOCK:
            self.foto = S.DB.execute("SELECT id FROM anuncio_foto_v2024 WHERE ficha_codigo='TUNAP109'").fetchone()[0]

    def rascunho(self, **extra):
        dados = {"codigo": "TUNAP109", "titulo": "Limpa Interior Automotivo Tunap 109 Espuma Spray 500ml",
                 "categoria": "200", "categoria_nome": "Limpeza", "descricao": "Espuma que limpa o interior do carro.",
                 "preco": "89,90", "quantidade": "2", "fotos": [self.foto],
                 "atributos": {"11": {"valor": "500", "unidade": "ml"}, "12": {"valor_id": "501"}}}
        dados.update(extra)
        return S.shopee_rascunho_salvar(dados, "admin", None)

    def apollo(self, dpm=24):
        return mock.patch.object(S, "anuncio_ficha_payload",
                                 return_value={"estoque": {"aracatuba": {"da_para_montar": dpm}}})


class Leituras(Base):

    def test_limites_categoria_e_sugestao_com_caminho(self):
        self.assertEqual(S.shopee_item_limites("200")["titulo_max"], 100)
        sug = S.shopee_categoria_sugerir("Limpa Interior Tunap")
        self.assertEqual(sug[0], {"categoria": "200", "nome": "Limpeza", "caminho": "Automotivo > Limpeza", "folha": True})
        self.assertFalse(sug[1]["folha"])

    def test_atributos_marcas_e_canais(self):
        at = {a["id"]: a for a in S.shopee_categoria_atributos("200")["atributos"]}
        self.assertTrue(at["11"]["obrigatorio"])
        self.assertEqual(at["11"]["unidades"], ["ml", "L"])
        self.assertEqual(at["12"]["tipo"], "lista")
        self.assertEqual([v["id"] for v in at["12"]["valores"]], ["501", "502"])
        marcas = S.shopee_marcas("200")
        self.assertEqual([m["id"] for m in marcas["marcas"]], ["7001", "7002"], "pagina todas")
        self.assertEqual(S.shopee_marca_da_ficha("Tunap", marcas), {"id": "7001", "nome": "TUNAP"})
        usaveis = S.shopee_canais_que_aceitam(S.shopee_canais_envio(), 0.6, {"height": 30, "width": 15, "length": 15})
        self.assertEqual([c["id"] for c in usaveis], ["90001"], "desligado e o de tamanho a escolher ficam fora")
        self.assertEqual(S.shopee_canais_que_aceitam(S.shopee_canais_envio(), 31, {}), [], "acima do peso")

    def test_leitura_guardada_24h(self):
        S.shopee_categoria_atributos("200")
        S.shopee_categoria_atributos("200")
        self.assertEqual(sum(1 for c in self.shopee.chamadas if c[1].endswith("get_attribute_tree")), 1)


class Rascunho(Base):

    def test_salvar_e_versao(self):
        r = self.rascunho()
        self.assertEqual((r["situacao"], r["preco"], r["quantidade"]), ("rascunho", 89.9, 2))
        r2 = S.shopee_rascunho_salvar({"id": r["id"], "preco": "99"}, "admin", r["versao"])
        self.assertEqual(r2["versao"], r["versao"] + 1)
        with self.assertRaises(S.FichaConflito):
            S.shopee_rascunho_salvar({"id": r["id"], "preco": "100"}, "admin", r["versao"])

    def test_foto_de_outro_produto_recusada(self):
        S.ficha_salvar("OUTRO1", {"nome": "Outro"}, "admin", None)
        foto_na_ficha("OUTRO1", "o.jpg")
        with S.DB_LOCK:
            outra = S.DB.execute("SELECT id FROM anuncio_foto_v2024 WHERE ficha_codigo='OUTRO1'").fetchone()[0]
        with self.assertRaises(ValueError):
            self.rascunho(fotos=[outra])

    def test_categoria_nova_limpa_os_atributos(self):
        r = self.rascunho()
        r2 = S.shopee_rascunho_salvar({"id": r["id"], "categoria": "300"}, "admin", r["versao"])
        self.assertEqual(r2["atributos"], {})


class Conferir(Base):

    def test_tudo_certo_vira_validado(self):
        r = self.rascunho()
        res = S.shopee_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"], res["erros"])
        self.assertEqual(res["situacao"], "validado")

    def test_erros_locais(self):
        r = self.rascunho(titulo="X" * 101, descricao="Curta [CONFIRMAR]", atributos={"12": {"valor_id": "501"}})
        erros = " | ".join(S.shopee_rascunho_conferir(r["id"], "admin")["erros"])
        self.assertIn("até 100", erros)
        self.assertIn("[CONFIRMAR]", erros)
        self.assertIn("Volume", erros)

    def test_peso_e_medidas_lidos_da_ficha_na_hora(self):
        S.ficha_salvar("TUNAP109", {"peso_g": ""}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = self.rascunho()
        self.assertIn("peso", " ".join(S.shopee_rascunho_conferir(r["id"], "admin")["erros"]))
        S.ficha_salvar("TUNAP109", {"peso_g": 600}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = S.shopee_rascunho_ler(r["id"])
        self.assertTrue(S.shopee_rascunho_conferir(r["id"], "admin")["ok"], "a ficha corrigida vale sem mexer no rascunho")

    def test_marca_obrigatoria_fora_da_lista(self):
        self.shopee.marca_obrigatoria = True
        S.ficha_salvar("TUNAP109", {"marca": "Desconhecida"}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = self.rascunho()
        self.assertIn("marca", " ".join(S.shopee_rascunho_conferir(r["id"], "admin")["erros"]))

    def test_corpo_do_add_item(self):
        r = self.rascunho()
        erros, ctx = S._shopee_conferir_local(S.shopee_rascunho_ler(r["id"]))
        self.assertEqual(erros, [])
        corpo = S.shopee_item_corpo(S.shopee_rascunho_ler(r["id"]), S.ficha_ler("TUNAP109"), ["imgA"], ctx)
        self.assertEqual(corpo["weight"], 0.6)
        self.assertEqual(corpo["dimension"], {"package_length": 15, "package_width": 15, "package_height": 30})
        self.assertEqual(corpo["brand"], {"brand_id": 7001, "original_brand_name": "TUNAP"})
        self.assertEqual(corpo["logistic_info"], [{"logistic_id": 90001, "enabled": True}])
        self.assertEqual(corpo["tax_info"], {"ncm": "34029039", "origin": "0"})
        self.assertEqual(corpo["gtin_code"], "00")
        self.assertIn({"attribute_id": 11, "attribute_value_list": [{"value_id": 0, "original_value_name": "500",
                                                                     "value_unit": "ml"}]}, corpo["attribute_list"])
        self.assertIn({"attribute_id": 12, "attribute_value_list": [{"value_id": 501}]}, corpo["attribute_list"])
        self.assertEqual((corpo["item_sku"], corpo["seller_stock"], corpo["condition"]), ("TUNAP109", [{"stock": 2}], "NEW"))

    def test_sem_marca_e_nao_obrigatoria_vai_no_brand(self):
        S.ficha_salvar("TUNAP109", {"marca": ""}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = self.rascunho()
        _erros, ctx = S._shopee_conferir_local(S.shopee_rascunho_ler(r["id"]))
        self.assertEqual(ctx["marca"], {"id": "0", "nome": "No Brand"})


class Publicar(Base):

    def pronto(self, **extra):
        r = self.rascunho(**extra)
        self.assertTrue(S.shopee_rascunho_conferir(r["id"], "admin")["ok"])
        return r["id"]

    def test_publica_e_entra_na_lista_em_manual(self):
        rid = self.pronto()
        with self.apollo():
            res = S.shopee_publicar(rid, "admin")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["item_id"], "987654321")
        with S.DB_LOCK:
            linha = dict(S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='shopee'").fetchone())
        self.assertEqual((linha["conta"], linha["anuncio_id"], linha["controle"], linha["estoque_modo"]),
                         ("aracatuba", "987654321", "noromak", "manual"))
        with self.assertRaises(ValueError):
            S.shopee_publicar(rid, "admin")

    def test_recusa_vira_erro_e_sem_resposta_vira_incerto(self):
        rid = self.pronto()
        self.shopee.add_item_erro = S.ShopeeRecusou("error_param: tax_info.ncm is required", {"error": "error_param"})
        with self.apollo():
            res = S.shopee_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "erro")
        self.assertIn("tax_info", res["erros"][0])
        S.shopee_rascunho_conferir(rid, "admin")
        self.shopee.add_item_erro = S.ShopeeSemResposta("timeout")
        with self.apollo():
            res = S.shopee_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "incerto")
        with self.assertRaises(ValueError):
            S.shopee_publicar(rid, "admin")  # nunca repete sozinho
        lib = S.shopee_incerto_liberar(rid, "555", "admin")
        self.assertEqual(lib["situacao"], "publicado")

    def test_falha_antes_de_criar_volta_a_validado(self):
        rid = self.pronto()
        with self.apollo(None):
            self.assertEqual(S.shopee_publicar(rid, "admin")["situacao"], "validado")
        with self.apollo(1):
            res = S.shopee_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertIn("acima do que dá para montar", res["erros"][0])
        self.shopee.upload_erro = S.ShopeeSemResposta("caiu")
        with self.apollo():
            res = S.shopee_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertFalse(any(c[1].endswith("add_item") for c in self.shopee.chamadas))

    def test_liberar_sem_numero_volta_a_validado(self):
        rid = self.pronto()
        self.shopee.add_item_erro = S.ShopeeSemResposta("timeout")
        with self.apollo():
            S.shopee_publicar(rid, "admin")
        self.assertEqual(S.shopee_incerto_liberar(rid, "", "admin")["situacao"], "validado")


class Rotas(Base):

    def test_rotas_do_admin(self):
        st, c = self.cliente.pedir("GET", "/api/anuncios/shopee/categorias?titulo=Limpa%20Interior")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["categorias"][0]["categoria"], "200")
        st, c = self.cliente.pedir("GET", "/api/anuncios/shopee/atributos?categoria=200&codigo=TUNAP109")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["marca_da_ficha"], {"id": "7001", "nome": "TUNAP"})
        st, c = self.cliente.pedir("POST", "/api/anuncios/shopee/rascunho", {"codigo": "TUNAP109", "titulo": "Limpa Interior X"})
        self.assertEqual(st, 200, c)
        rid, v = c["rascunho"]["id"], c["rascunho"]["versao"]
        st, c = self.cliente.pedir("POST", "/api/anuncios/shopee/rascunho", {"id": rid, "titulo": "Outro", "versao": v + 5})
        self.assertEqual(st, 409, c)
        st, c = self.cliente.pedir("GET", "/api/anuncios/shopee/rascunhos?codigo=TUNAP109")
        self.assertEqual([r["id"] for r in c["rascunhos"]], [rid])

    def test_supervisor_nao_mexe(self):
        from base import SENHA_ADMIN, Cliente
        with S.DB_LOCK:
            S.upsert_app_user("sup_sh", "Sup", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_sh", SENHA_ADMIN, area="admin")
        st, _ = sup.pedir("POST", "/api/anuncios/shopee/rascunho", {"codigo": "TUNAP109", "titulo": "X"})
        self.assertEqual(st, 403)
        st, _ = sup.pedir("GET", "/api/anuncios/shopee/categorias?titulo=X")
        self.assertEqual(st, 403)


class Preco(Base):

    def test_preco_ideal_cai_na_propria_faixa(self):
        with mock.patch.object(S, "anuncio_custo_para_preco",
                               return_value={"custo": 53.10, "custo_origem": "x", "margem_alvo": 0.20, "imposto": 0.12}):
            c = S.shopee_conta_preco("TUNAP109", "139,90")
        self.assertEqual(c["comissao_pct"], 0.175)
        self.assertEqual(c["taxa_fixa"], 20.0)
        self.assertAlmostEqual(c["lucro"], 139.90 - 139.90 * 0.175 - 20 - 139.90 * 0.12 - 53.10, places=2)
        ideal = c["preco_ideal"]
        pct, fixa = S.shopee_taxas_por_preco(ideal)
        margem = (ideal - ideal * pct - fixa - ideal * 0.12 - 53.10) / ideal
        self.assertAlmostEqual(margem, 0.20, places=2)

    def test_sem_custo_sem_lucro(self):
        with mock.patch.object(S, "anuncio_custo_para_preco",
                               return_value={"custo": None, "custo_origem": "x", "margem_alvo": 0.20, "imposto": 0.12}):
            c = S.shopee_conta_preco("TUNAP109", "100")
        self.assertIsNone(c["lucro"])
        self.assertIsNone(c["preco_ideal"])


if __name__ == "__main__":
    import unittest
    unittest.main()
