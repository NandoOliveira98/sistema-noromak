"""V20.41 - criar anuncio no TikTok Shop (Bauru) pela plataforma de anuncios e "Trazer dados do anuncio" do TikTok.

Molde: a Shopee (V20.32). O TikTok e simulado (tiktok_api nas leituras, _tiktok_chamar nas escritas); construido pela
documentacao da Open API 202309, sem resposta real."""

import json
import time
from unittest import mock

from base import S, TesteNoromak, linha

from test_agente_anuncios_v2031 import foto_na_ficha
from test_anuncios_puxar_dados_v2025 import png
from test_anuncios_estoque_v2026 import fila

REAL_CHAMAR = S._tiktok_chamar  # o de verdade (o teste Chamada simula so a rede)


class TikTokFalso:
    """Responde como a Open API 202309 (formato da documentacao)."""

    def __init__(self):
        self.chamadas = []
        self.criar = {"code": 0, "data": {"product_id": "1729000000001", "skus": [{"id": "1729000000099",
                                                                                 "seller_sku": "TUNAP109"}]}}
        self.criar_erro = None
        self.upload_erro = None
        self.check = {"code": 0, "data": {"check_result": "PASS", "fail_reasons": []}}
        self.check_erro = None
        self.depositos = [{"id": "W1", "name": "Bauru", "type": "SALES_WAREHOUSE", "effect_status": "ENABLED"},
                          {"id": "W9", "name": "Devolução", "type": "RETURN_WAREHOUSE", "effect_status": "ENABLED"}]
        self.produto = {"id": "1729000000001", "title": "Limpa Interior Tunap 109 &amp; Espuma",
                        "description": "<p>Linha 1 &lt;ok&gt;</p><ul><li>Item A</li></ul>",
                        "brand": {"id": "7001", "name": "TUNAP"},
                        "category_chains": [{"id": "100", "is_leaf": False}, {"id": "200", "is_leaf": True}],
                        "main_images": [{"uri": "a", "urls": ["https://p16-oec-va.ibyteimg.com/foto1.jpeg"]},
                                        {"uri": "b", "urls": ["https://evil.example.com/foto2.jpeg"]}],
                        "skus": [{"id": "1729000000099", "seller_sku": "TUNAP109",
                                  "inventory": [{"warehouse_id": "W1", "quantity": 2}]}]}

    # tiktok_api(metodo, path, params=None, body=None, loja=True)
    def api(self, metodo, path, params=None, body=None, loja=True):
        self.chamadas.append((metodo, path, params, body))
        if path == "/product/202309/categories":
            return {"code": 0, "data": {"categories": [
                {"id": "100", "parent_id": "0", "local_name": "Automotivo", "is_leaf": False},
                {"id": "200", "parent_id": "100", "local_name": "Limpeza", "is_leaf": True}]}}
        if path == "/product/202309/categories/recommend":
            return {"code": 0, "data": {"leaf_category_id": "200", "categories": [{"id": "100", "name": "Automotivo"}]}}
        if path == "/product/202309/categories/200/attributes":
            return {"code": 0, "data": {"attributes": [
                {"id": "11", "name": "Volume", "type": "PRODUCT_PROPERTY", "is_requried": True, "is_customizable": True},
                {"id": "12", "name": "Tipo", "type": "PRODUCT_PROPERTY", "is_required": True,
                 "values": [{"id": "501", "name": "Espuma"}, {"id": "502", "name": "Líquido"}]},
                {"id": "13", "name": "Cor", "type": "SALES_PROPERTY", "is_requried": True}]}}
        if path == "/product/202309/categories/200/rules":
            return {"code": 0, "data": {"package_dimension": {"is_required": True}, "size_chart": {"is_required": False}}}
        if path == "/product/202309/brands":
            if not (params or {}).get("page_token"):
                return {"code": 0, "data": {"brands": [{"id": "7001", "name": "TUNAP"}], "next_page_token": "p2"}}
            return {"code": 0, "data": {"brands": [{"id": "7002", "name": "Vonixx"}], "next_page_token": ""}}
        if path == "/logistics/202309/warehouses":
            return {"code": 0, "data": {"warehouses": self.depositos}}
        if path.startswith("/product/202309/products/") and metodo == "GET":
            return {"code": 0, "data": self.produto}
        raise AssertionError(f"leitura inesperada {metodo} {path}")

    # _tiktok_chamar(metodo, path, params=None, body=None, *, multipart=None, timeout=35, amostra="")
    def chamar(self, metodo, path, params=None, body=None, *, multipart=None, timeout=35, amostra=""):
        self.chamadas.append((metodo, path, params, body if multipart is None else multipart[0]))
        if path == "/product/202309/images/upload":
            if self.upload_erro:
                raise self.upload_erro
            assert multipart[1][0] == "data" and multipart[0] == {"use_case": "MAIN_IMAGE"}
            return {"code": 0, "data": {"uri": f"tos-uri-{len(self.chamadas)}"}}
        if path == "/product/202309/products/listing_check":
            if self.check_erro:
                raise self.check_erro
            return self.check
        if path == "/product/202309/products":
            if self.criar_erro:
                raise self.criar_erro
            return self.criar
        raise AssertionError(f"escrita inesperada {path}")

    def criou(self):
        return [c for c in self.chamadas if c[1] == "/product/202309/products"]


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._TIKTOK_LEITURA_CACHE.clear()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        agora = int(time.time())
        S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                    "access_token_expire_in": agora + 86400,
                                                    "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
            S.DB.commit()
        self.tt = TikTokFalso()
        for alvo, valor in (("tiktok_api", self.tt.api), ("_tiktok_chamar", self.tt.chamar),
                            ("anuncio_foto_bytes", lambda _fid: (b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg"))):
            p = mock.patch.object(S, alvo, new=valor)
            p.start()
            self.addCleanup(p.stop)
        S.tiktok_fonte_definir("api", "dono")
        S.ficha_salvar("TUNAP109", {"nome": "Limpa Interior Tunap 109 Espuma 500ml", "marca": "Tunap",
                                    "peso_g": 600, "altura_cm": 30, "largura_cm": 15, "comprimento_cm": 14.2},
                       "admin", None)
        foto_na_ficha("TUNAP109")
        with S.DB_LOCK:
            self.foto = S.DB.execute("SELECT id FROM anuncio_foto_v2024 WHERE ficha_codigo='TUNAP109'").fetchone()[0]

    def rascunho(self, **extra):
        dados = {"codigo": "TUNAP109", "titulo": "Limpa Interior Automotivo Tunap 109 Espuma Spray 500ml",
                 "categoria": "200", "categoria_nome": "Limpeza", "descricao": "Espuma que limpa.\n\nUse <pouco> & bem.",
                 "preco": "89,90", "quantidade": "2", "fotos": [self.foto],
                 "atributos": {"11": {"valor": "500 ml"}, "12": {"valor_id": "501"}}}
        dados.update(extra)
        return S.tiktok_rascunho_salvar(dados, "admin", None)

    def pronto(self, **extra):
        r = self.rascunho(**extra)
        res = S.tiktok_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"], res)
        return r["id"]

    def apollo(self, dpm=24):
        return mock.patch.object(S, "anuncio_ficha_payload", return_value={"estoque": {"bauru": {"da_para_montar": dpm}}})

    def hub(self):
        with S.DB_LOCK:
            return [dict(x) for x in S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='tiktok'")]


class Leituras(Base):

    def test_categoria_sugerida_atributos_regras_marcas_deposito(self):
        sug = S.tiktok_categoria_sugerir("Limpa Interior Tunap")
        self.assertEqual(sug[0], {"categoria": "200", "nome": "Limpeza", "caminho": "Automotivo > Limpeza", "folha": True})
        self.assertEqual(len(sug), 1, "a categoria que tem subcategorias não é sugerida")
        at = {a["id"]: a for a in S.tiktok_categoria_atributos("200")["atributos"]}
        self.assertEqual(sorted(at), ["11", "12"], "atributo de venda (variação) fica fora")
        self.assertTrue(at["11"]["obrigatorio"] and at["11"]["livre"], "is_requried (grafia da doc) vale")
        self.assertTrue(at["12"]["obrigatorio"] and not at["12"]["livre"])
        self.assertTrue(S.tiktok_categoria_regras("200")["medidas_obrigatorias"])
        marcas = S.tiktok_marcas("200")
        self.assertEqual([m["id"] for m in marcas["marcas"]], ["7001", "7002"], "pagina todas")
        self.assertEqual(S.tiktok_marca_da_ficha("Tunap", marcas), {"id": "7001", "nome": "TUNAP"})
        self.assertEqual(S.tiktok_deposito_venda()["id"], "W1", "o de devolução não conta")
        S.tiktok_categoria_atributos("200")
        self.assertEqual(sum(1 for c in self.tt.chamadas if c[1].endswith("/200/attributes")), 1, "cache de 24 h")

    def test_dois_depositos_de_venda_recusa_listando(self):
        self.tt.depositos.append({"id": "W2", "name": "Outro", "type": "SALES_WAREHOUSE", "effect_status": "ENABLED"})
        with self.assertRaises(RuntimeError) as ctx:
            S.tiktok_deposito_venda()
        self.assertIn("W2", str(ctx.exception))

    def test_descricao_html_escapada_e_volta(self):
        htm = S._tiktok_descricao_html("A <b> & c\nlinha 2\n\nPar 2")
        self.assertEqual(htm, "<p>A &lt;b&gt; &amp; c<br>linha 2</p><p>Par 2</p>")
        self.assertEqual(S._tiktok_html_texto(htm), "A <b> & c\nlinha 2\nPar 2")


class Rascunho(Base):

    def test_salvar_e_versao(self):
        r = self.rascunho()
        self.assertEqual((r["situacao"], r["preco"], r["quantidade"]), ("rascunho", 89.9, 2))
        r2 = S.tiktok_rascunho_salvar({"id": r["id"], "preco": "99"}, "admin", r["versao"])
        self.assertEqual(r2["versao"], r["versao"] + 1)
        with self.assertRaises(S.FichaConflito):
            S.tiktok_rascunho_salvar({"id": r["id"], "preco": "100"}, "admin", r["versao"])

    def test_versao_na_rota_409(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/tiktok/rascunho", {"codigo": "TUNAP109", "titulo": "Limpa X"})
        self.assertEqual(st, 200, c)
        rid, v = c["rascunho"]["id"], c["rascunho"]["versao"]
        st, c = self.cliente.pedir("POST", "/api/anuncios/tiktok/rascunho", {"id": rid, "titulo": "Outro", "versao": v + 3})
        self.assertEqual((st, c.get("error")), (409, "CONFLITO"))
        st, c = self.cliente.pedir("GET", "/api/anuncios/tiktok/rascunhos?codigo=TUNAP109")
        self.assertEqual(([r["id"] for r in c["rascunhos"]], c["fonte"]), ([rid], "api"))

    def test_categoria_nova_limpa_atributos_e_marca(self):
        r = self.rascunho(marca={"id": "7001", "nome": "TUNAP"})
        r2 = S.tiktok_rascunho_salvar({"id": r["id"], "categoria": "300"}, "admin", r["versao"])
        self.assertEqual((r2["atributos"], r2["marca"]), ({}, {}))


class Conferir(Base):

    def test_tudo_certo_vira_validado_e_listing_check_sem_fotos(self):
        r = self.rascunho()
        res = S.tiktok_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["conferido_tiktok"])
        check = [c for c in self.tt.chamadas if c[1].endswith("listing_check")][0][3]
        self.assertNotIn("main_images", check, "as fotos só sobem no Publicar")
        self.assertEqual(check["skus"][0]["inventory"], [{"warehouse_id": "W1", "quantity": 2}])
        self.assertEqual(check["brand_id"], "7001", "a marca da ficha achada na lista")
        self.assertFalse([c for c in self.tt.chamadas if c[1].endswith("images/upload")])

    def test_listing_check_recusa_vira_erro_e_motivo_de_foto_e_ignorado(self):
        self.tt.check = {"code": 0, "data": {"check_result": "FAILED", "fail_reasons": [
            {"reasons": ["main_images is required", "Title contains prohibited word"]}]}}
        r = self.rascunho()
        res = S.tiktok_rascunho_conferir(r["id"], "admin")
        self.assertEqual(res["situacao"], "rascunho")
        self.assertEqual(res["erros"], ["O TikTok recusaria: Title contains prohibited word"])

    def test_listing_check_fora_do_ar_fica_a_conferencia_local(self):
        self.tt.check_erro = S.TikTokRecusou("TikTok: 404 not found", {"code": 40400})
        r = self.rascunho()
        res = S.tiktok_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"])
        self.assertIn("só a conferência do Noromak", " ".join(res["avisos"]))

    def test_erros_locais_e_peso_da_ficha(self):
        S.ficha_salvar("TUNAP109", {"peso_g": ""}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = self.rascunho(atributos={"12": {"valor": "Espuma"}}, descricao="Texto [CONFIRMAR]")
        erros = " | ".join(S.tiktok_rascunho_conferir(r["id"], "admin")["erros"])
        for trecho in ("Volume", "Tipo: escolha um valor da lista", "[CONFIRMAR]", "peso"):
            self.assertIn(trecho, erros)
        self.assertFalse([c for c in self.tt.chamadas if c[1].endswith("listing_check")], "com erro local não chama")

    def test_corpo_do_criar(self):
        r = S.tiktok_rascunho_ler(self.rascunho()["id"])
        erros, ctx = S._tiktok_conferir_local(r)
        self.assertEqual(erros, [])
        corpo = S.tiktok_produto_corpo(r, S.ficha_ler("TUNAP109"), ["u1"], ctx)
        self.assertEqual(corpo["skus"], [{"seller_sku": "TUNAP109", "price": {"amount": "89.90", "currency": "BRL"},
                                          "inventory": [{"warehouse_id": "W1", "quantity": 2}]}])
        self.assertEqual(corpo["package_weight"], {"value": "0.6", "unit": "KILOGRAM"})
        self.assertEqual(corpo["package_dimensions"], {"length": "15", "width": "15", "height": "30", "unit": "CENTIMETER"})
        self.assertEqual(corpo["main_images"], [{"uri": "u1"}])
        self.assertEqual(corpo["description"], "<p>Espuma que limpa.</p><p>Use &lt;pouco&gt; &amp; bem.</p>")
        self.assertIn({"id": "11", "values": [{"name": "500 ml"}]}, corpo["product_attributes"])
        self.assertIn({"id": "12", "values": [{"id": "501"}]}, corpo["product_attributes"])


class Publicar(Base):

    def test_publica_entra_na_lista_e_no_tiktok_sku(self):
        rid = self.pronto()
        with self.apollo():
            res = S.tiktok_publicar(rid, "admin")
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["product_id"], res["sku_id"]), ("1729000000001", "1729000000099"))
        corpo = self.tt.criou()[0][3]
        self.assertTrue(corpo["main_images"][0]["uri"].startswith("tos-uri-"))
        h = self.hub()
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["conta"], h[0]["anuncio_id"], h[0]["user_product_id"], h[0]["controle"],
                          h[0]["estoque_modo"], h[0]["ficha_codigo"]),
                         ("bauru", "1729000000099", "1729000000001", "noromak", "manual", "TUNAP109"))
        with S.DB_LOCK:
            sk = dict(S.DB.execute("SELECT * FROM tiktok_sku_v1964 WHERE sku_id='1729000000099'").fetchone())
        self.assertEqual((sk["product_id"], sk["seller_sku"], sk["warehouse_id"], sk["qtd_tiktok"]),
                         ("1729000000001", "TUNAP109", "W1", 2))
        with self.assertRaises(ValueError):
            S.tiktok_publicar(rid, "admin")

    def test_recusa_vira_erro_sem_nada_criado(self):
        rid = self.pronto()
        self.tt.criar_erro = S.TikTokRecusou("TikTok: 12052048 category_id invalid", {"code": 12052048,
                                                                                    "message": "category invalid"},
                                             status=200)
        with self.apollo():
            res = S.tiktok_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "erro")
        self.assertIn("category", res["erros"][0])
        self.assertEqual(self.hub(), [])

    def test_sem_resposta_vira_incerto_e_nunca_repete_liberar_confere_o_sku(self):
        rid = self.pronto()
        self.tt.criar_erro = S.TikTokSemResposta("timeout")
        with self.apollo():
            res = S.tiktok_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "incerto")
        with self.apollo(), self.assertRaises(ValueError):
            S.tiktok_publicar(rid, "admin")
        self.assertEqual(len(self.tt.criou()), 1, "nunca repete sozinho")
        self.tt.produto["skus"][0]["seller_sku"] = "OUTRO"
        with self.assertRaises(ValueError) as ctx:
            S.tiktok_incerto_liberar(rid, "1729000000001", "admin")
        self.assertIn("não é deste produto", str(ctx.exception))
        self.assertEqual(S.tiktok_rascunho_ler(rid)["situacao"], "incerto")
        self.tt.produto["skus"][0]["seller_sku"] = "TUNAP109"
        lib = S.tiktok_incerto_liberar(rid, "1729000000001", "admin")
        self.assertEqual((lib["situacao"], lib["sku_id"]), ("publicado", "1729000000099"))
        self.assertEqual(self.hub()[0]["anuncio_id"], "1729000000099")

    def test_erro_interno_do_tiktok_no_criar_e_incerto(self):
        rid = self.pronto()
        self.tt.criar_erro = S.TikTokRecusou("TikTok: 36009004 internal error", {"code": 36009004,
                                                                               "message": "Internal error"}, status=200)
        with self.apollo():
            self.assertEqual(S.tiktok_publicar(rid, "admin")["situacao"], "incerto")

    def test_liberar_sem_numero_volta_a_validado(self):
        rid = self.pronto()
        self.tt.criar_erro = S.TikTokSemResposta("timeout")
        with self.apollo():
            S.tiktok_publicar(rid, "admin")
        self.assertEqual(S.tiktok_incerto_liberar(rid, "", "admin")["situacao"], "validado")

    def test_ja_anunciado_nao_publica_outro(self):
        rid = self.pronto()
        S._tiktok_sku_gravar({"V1": {"product_id": "PR1", "titulo": "T", "status": "ACTIVATE", "seller_sku": "TUNAP109",
                                     "warehouse_id": "W1", "armazens": 1, "qtd": 3}}, apagar_ausentes=False)
        with self.apollo(), self.assertRaises(ValueError) as ctx:
            S.tiktok_publicar(rid, "admin")
        self.assertIn("já tem anúncio no TikTok", str(ctx.exception))
        self.assertEqual(self.tt.criou(), [])

    def test_fonte_extensao_recusa_409(self):
        rid = self.pronto()
        S.tiktok_fonte_definir("extensao", "dono")
        with self.assertRaises(S.TikTokFonteNaoApi):
            S.tiktok_publicar(rid, "admin")
        st, c = self.cliente.pedir("POST", "/api/anuncios/tiktok/publicar", {"id": rid})
        self.assertEqual((st, c.get("codigo_erro")), (409, "FONTE_EXTENSAO"), c)
        self.assertEqual(S.tiktok_rascunho_ler(rid)["situacao"], "validado")
        self.assertTrue(S.ANUNCIOS_PUBLICAR_LOCK.acquire(blocking=False), "a trava foi solta")
        S.ANUNCIOS_PUBLICAR_LOCK.release()

    def test_sem_leitura_do_apollo_ou_acima_dele_nao_publica(self):
        rid = self.pronto()
        with self.apollo(None):
            self.assertEqual(S.tiktok_publicar(rid, "admin")["situacao"], "validado")
        with self.apollo(1):
            res = S.tiktok_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertIn("Apollo de Bauru (1)", res["erros"][0])
        self.tt.upload_erro = S.TikTokSemResposta("caiu")
        with self.apollo():
            self.assertEqual(S.tiktok_publicar(rid, "admin")["situacao"], "validado")
        self.assertEqual(self.tt.criou(), [], "nada foi criado")

    def test_estoque_inicial_e_o_apollo_de_bauru_menos_a_fila_de_bauru(self):
        apollo = {"TUNAP109": {1: 50, 3: 5}}  # Araçatuba nunca conta
        p = mock.patch.object(S, "crm_stock_lookup", side_effect=lambda codigo, operador="", wait_seconds=0: {
            "ok": True, "estoques": [{"revenda": r, "disponivel": v, "respondeu": True}
                                     for r, v in apollo.get(codigo, {}).items()]})
        p.start()
        self.addCleanup(p.stop)
        S._ANUNCIO_APOLLO_CHAMADAS.clear()
        fila([linha("TT9", "TUNAP109", 2, Marketplace="TIKTOK", Casa="bauru"),
              linha("ML1", "TUNAP109", 7, Marketplace="MERCADO LIVRE")])  # Araçatuba não desconta de Bauru
        rid = self.pronto(quantidade="4")
        res = S.tiktok_publicar(rid, "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertIn("(3)", res["erros"][0], "5 no Apollo de Bauru menos 2 a baixar em Bauru")
        r = S.tiktok_rascunho_ler(rid)
        S.tiktok_rascunho_salvar({"id": rid, "quantidade": "3"}, "admin", r["versao"])
        S.tiktok_rascunho_conferir(rid, "admin")
        self.assertTrue(S.tiktok_publicar(rid, "admin")["ok"])

    def test_publicar_pela_rota_em_segundo_plano(self):
        rid = self.pronto()
        with self.apollo():
            st, c = self.cliente.pedir("POST", "/api/anuncios/tiktok/publicar", {"id": rid})
            self.assertEqual(st, 200, c)
            fim = time.time() + 10
            while time.time() < fim and S.tiktok_rascunho_ler(rid)["situacao"] == "publicando":
                time.sleep(0.05)
        self.assertEqual(S.tiktok_rascunho_ler(rid)["situacao"], "publicado")


class Chamada(Base):

    def test_multipart_nao_assina_o_corpo_e_separa_recusa_de_sem_resposta(self):
        vistos = []

        def rede(metodo, url, token, corpo, tipo, timeout):
            vistos.append((url, corpo, tipo))
            return 200, json.dumps({"code": 0, "data": {"uri": "u"}})
        with mock.patch.object(S, "_tiktok_http_bruto", side_effect=rede):
            REAL_CHAMAR("POST", "/product/202309/images/upload",
                             multipart=({"use_case": "MAIN_IMAGE"}, ("data", "f.jpg", b"JPG", "image/jpeg")))
        url, corpo, tipo = vistos[0]
        q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
        params = {k: v for k, v in q.items() if k != "sign"}
        self.assertEqual(q["sign"], S.tiktok_assinar("/product/202309/images/upload", params, ""))
        self.assertTrue(tipo.startswith("multipart/form-data") and b'name="data"' in corpo)
        with mock.patch.object(S, "_tiktok_http_bruto", return_value=(502, "bad gateway")):
            with self.assertRaises(S.TikTokSemResposta):
                REAL_CHAMAR("POST", "/product/202309/products", body={})
        with mock.patch.object(S, "_tiktok_http_bruto", return_value=(200, json.dumps({"code": 12019, "message": "x"}))):
            with self.assertRaises(S.TikTokRecusou):
                REAL_CHAMAR("POST", "/product/202309/products", body={})


class TrazerDados(Base):

    def setUp(self):
        super().setUp()
        self.baixados = []

        def baixar(url):
            self.baixados.append(url)
            return png(800, 800)
        p = mock.patch.object(S, "_tiktok_foto_baixar", side_effect=baixar)
        p.start()
        self.addCleanup(p.stop)
        S._tiktok_sku_gravar({"1729000000099": {"product_id": "1729000000001", "titulo": "T", "status": "ACTIVATE",
                                                "seller_sku": "TUNAP109", "warehouse_id": "W1", "armazens": 1, "qtd": 2}},
                             apagar_ausentes=False)

    def test_traz_titulo_descricao_marca_e_fotos_so_https_do_tiktok_sem_tocar_no_admin(self):
        S.ficha_salvar("TUNAP109", {"descricao": ""}, "admin", S.ficha_ler("TUNAP109")["versao"])  # apagado pelo Admin
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM anuncio_foto_v2024 WHERE ficha_codigo='TUNAP109'")
            S.DB.commit()
        r = S.anuncio_trazer_dados("tiktok", "bauru", "1729000000099", "admin")
        f = S.ficha_ler("TUNAP109")
        self.assertEqual(f["campos"]["nome"], "Limpa Interior Tunap 109 Espuma 500ml", "título do Admin fica")
        self.assertEqual(f["campos"]["marca"], "Tunap", "marca do Admin fica")
        self.assertNotIn("descricao", f["campos"], "apagado pelo Admin continua apagado")
        self.assertEqual(r["trazidos"]["campos"], [])
        self.assertEqual(self.baixados, ["https://p16-oec-va.ibyteimg.com/foto1.jpeg"], "host de fora nunca é baixado")
        fotos = S.anuncio_fotos("TUNAP109")
        self.assertEqual([x["origem"] for x in fotos], ["anuncio_tiktok"])
        self.assertEqual(self.hub()[0]["categoria"], "200")

    def test_ficha_sem_dados_recebe_do_tiktok_e_nao_duplica_fotos(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_ficha_v2023 SET campos_json='{}', origem_json='{}' WHERE codigo='TUNAP109'")
            S.DB.commit()
        r = S.anuncio_trazer_dados("tiktok", "bauru", "1729000000099", "admin")
        f = S.ficha_ler("TUNAP109")
        self.assertEqual(f["campos"]["nome"], "Limpa Interior Tunap 109 & Espuma")
        self.assertEqual(f["campos"]["descricao"], "Linha 1 <ok>\n- Item A")
        self.assertEqual((f["campos"]["marca"], f["origem"]["marca"]), ("TUNAP", "anuncio_tiktok"))
        self.assertTrue(r["trazidos"]["fotos_ja_tinha"], "a ficha já tinha foto: nada baixado")
        self.assertEqual(self.baixados, [])

    def test_url_da_foto(self):
        self.assertTrue(S._tiktok_foto_url_ok("https://p16-oec-va.ibyteimg.com/x.jpeg"))
        self.assertTrue(S._tiktok_foto_url_ok("https://p19.tiktokcdn-us.com/x"))
        for ruim in ("http://p16.ibyteimg.com/x", "https://ibyteimg.com.evil.io/x", "https://evil.example.com/x"):
            self.assertFalse(S._tiktok_foto_url_ok(ruim), ruim)

    def test_fonte_extensao_e_removido(self):
        S.anuncio_remover("tiktok", "bauru", "1729000000099", "admin")
        with self.assertRaises(ValueError):
            S.anuncio_trazer_dados("tiktok", "bauru", "1729000000099", "admin")
        S.tiktok_fonte_definir("extensao", "dono")
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "tiktok", "conta": "bauru", "anuncio_id": "1729000000099"})
        self.assertEqual(st, 400, c)


class RevisaoAntesDeSubir(Base):
    """revisar-antes-de-subir da V20.41 (07/11)."""

    def test_publicar_confere_a_fonte_de_novo(self):
        rid = self.pronto()
        r = S.tiktok_publicar_reservar(rid, "admin")
        S._tiktok_config_gravar("fonte", "extensao", "dono")  # virou extensão depois do clique
        with self.apollo():
            res = S.tiktok_publicar_executar(r, "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertEqual(self.tt.criou(), [], "nada criado com a fonte extensão")

    def test_listing_check_falhou_sem_motivo_nao_e_conferido(self):
        self.tt.check = {"code": 0, "data": {"check_result": "FAILED", "fail_reasons": [{"codigo": 1}]}}
        r = self.rascunho()
        res = S.tiktok_rascunho_conferir(r["id"], "admin")
        self.assertFalse(res["conferido_tiktok"])
        self.assertIn("sem um motivo legível", " ".join(res["avisos"]))

    def test_marca_alem_da_lista_cortada_e_buscada_pelo_nome(self):
        original = self.tt.api

        def api(metodo, path, params=None, body=None, loja=True):
            if path == "/product/202309/brands":
                if (params or {}).get("brand_name"):
                    return {"code": 0, "data": {"brands": [{"id": "7009", "name": "TUNAP"}]}}
                return {"code": 0, "data": {"brands": [{"id": "1", "name": "Outra"}], "next_page_token": "mais"}}
            return original(metodo, path, params, body, loja)
        with mock.patch.object(S, "tiktok_api", new=api), mock.patch.object(S, "TIKTOK_MARCAS_PAGINAS", 2):
            S._TIKTOK_LEITURA_CACHE.clear()
            self.assertTrue(S.tiktok_marcas("200")["incompleta"])
            r = S.tiktok_rascunho_ler(self.rascunho()["id"])
            erros, ctx = S._tiktok_conferir_local(r)
        self.assertEqual(ctx["marca"], {"id": "7009", "nome": "TUNAP"})


class Tela(TesteNoromak):

    def test_painel_e_botao(self):
        texto = (S.APP_DIR / "anuncios.html").read_text(encoding="utf-8")
        for trecho in ('id="secTiktok"', "Criar anúncio no TikTok", "/api/anuncios/tiktok/publicar",
                       'a.canal === "tiktok" && a.controle === "noromak") b.push', 'CASA === "bauru"'):
            self.assertIn(trecho, texto)


if __name__ == "__main__":
    import unittest
    unittest.main()
