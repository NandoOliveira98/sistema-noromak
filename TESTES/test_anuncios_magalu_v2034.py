"""V20.34 - criar anuncio e controlar estoque na Magalu (Araçatuba) pela plataforma de anuncios.

Desenho: docs/superpowers/specs/2026-11-06-anuncios-magalu-design.md. A Magalu e simulada (_magalu_chamar) no
formato da especificacao OpenAPI oficial (202 + trace_id; preco em centavos; estoque AVAILABLE)."""

import json
from unittest import mock

from base import S, TesteNoromak, linha

from test_anuncios_estoque_v2026 import EstoqueBase, fila, inserir

CAT = "11111111-2222-3333-4444-555555555555"
FOTO = "https://http2.mlstatic.com/D_NQ_NP_123-MLA1-O.jpg"


class MagaluFalsa:
    """Responde como a Magalu (spec products.openapi.yaml)."""

    def __init__(self):
        self.chamadas = []
        self.skus = {}          # sku -> corpo criado
        self.estoque = {}       # sku -> quantidade (AVAILABLE)
        self.precos = {}
        self.criar_erro = None
        self.preco_erro = None
        self.estoque_erro = None
        self.aparece_depois = 0  # quantas leituras do SKU ainda dao 404 depois de criado (assincrono)
        self.nao_cria = False    # 202 mas o SKU nunca aparece
        self.trace = []
        self.nao_muda = set()
        self.branches = {}
        self.filhas = {}         # categoria -> subcategorias (hierarchy?parent_id=)
        self.attrs_extras = 0    # atributos a mais (paginacao)
        self.estoque_404 = False  # SKU novo sem estoque: o GET do estoque da 404

    def __call__(self, method, path, params=None, payload=None, *, timeout=35):
        self.chamadas.append((method, path, params, payload))
        if path == "/seller/v1/portfolios/categories":  # revisao: plural, como na spec (categories.openapi.yaml)
            return {"results": [{"id": CAT, "name": "Limpeza automotiva", "path": "/automotivo/limpeza-automotiva",
                                 "parent_id": None, "created_at": "2024-01-01T00:00:00Z", "created_by": "x"}]}
        if path == "/seller/v1/portfolios/categories/hierarchy":
            return {"results": [{"id": f, "name": "Filha", "parent_id": params["parent_id"]}
                                for f in self.filhas.get(params["parent_id"], [])]}
        if path.endswith("/attributes"):
            assert path.startswith("/seller/v1/portfolios/categories/"), path
            base = [{"name": "Volume", "display_name": "Volume", "required": "required", "variation": True,
                     "choices": ["500 ml", "1 L"], "active": True},
                    {"name": "Cor", "required": "optional", "choices": None, "variation": True},
                    {"name": "Antigo", "required": "required", "active": False, "variation": True},
                    {"name": "Dimensões do Produto", "required": "optional", "variation": False}]
            extra = [{"name": f"Extra {i}", "required": "optional", "variation": False} for i in range(self.attrs_extras)]
            todos = base + extra
            off, lim = int((params or {}).get("_offset") or 0), int((params or {}).get("_limit") or 50)
            return {"results": todos[off:off + lim]}
        if path.endswith("/datasheet"):
            return {"results": [{"name": "Fabricante", "required": "required"}, {"name": "Modelo", "required": "recommended"}]}
        if path == "/seller/v1/portfolios/skus" and method == "POST":
            if self.criar_erro:
                raise self.criar_erro
            if not self.nao_cria:
                self.skus[payload["sku"]] = payload
            return {"trace_id": "tr-1"}
        if path.startswith("/seller/v1/portfolios/skus/"):
            sku = path.rsplit("/", 1)[1]
            if sku in self.skus and self.aparece_depois <= 0:
                saida = {"sku": sku, "status": "PUBLISHED", "title": self.skus[sku]["title"]}
                if "extra_data" in self.skus[sku]:
                    saida["extra_data"] = self.skus[sku]["extra_data"]
                return saida
            if sku in self.skus:
                self.aparece_depois -= 1
            raise S.MagaluRecusou("Magalu HTTP 404: not found", {"message": "not found"}, 404)
        if path.startswith("/v0/traces/"):
            return {"results": self.trace}
        if path.startswith("/seller/v1/portfolios/prices/"):
            sku = path.rsplit("/", 1)[1]
            if method == "GET":
                if sku not in self.precos:
                    return {"results": []}
                return {"results": [dict(self.precos[sku], normalizer=100)]}
            if self.preco_erro:
                raise self.preco_erro
            self.precos[sku] = payload
            return {"trace_id": "tr-p"}
        if path.startswith("/seller/v1/portfolios/stocks/"):
            sku = path.rsplit("/", 1)[1]
            if method == "GET":
                if sku not in self.skus or (self.estoque_404 and sku not in self.estoque):
                    raise S.MagaluRecusou("Magalu HTTP 404", {}, 404)
                if sku not in self.estoque:
                    return {"results": []}
                linha_e = {"channel": {"id": S.MAGALU_CANAL_ID}, "quantity": self.estoque[sku], "type": "AVAILABLE"}
                saida = [dict(linha_e, branch={"id": b}) for b in self.branches.get(sku, [])] or [linha_e]
                return {"results": saida + [{"channel": {"id": S.MAGALU_CANAL_ID}, "quantity": 9, "type": "RESERVED"}]}
            if self.estoque_erro:
                raise self.estoque_erro
            if sku not in self.nao_muda:
                self.estoque[sku] = payload["quantity"]
            return {"trace_id": "tr-e"}
        raise AssertionError(f"chamada inesperada {method} {path}")

    def escritas(self, trecho):
        return [c for c in self.chamadas if c[0] in ("POST", "PATCH") and trecho in c[1]]


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._MAGALU_LEITURA_CACHE.clear()
        S._MAGALU_ACOMPANHAR_VISTO.clear()
        S._MAGALU_FOTO_OK.clear()
        self.mg = MagaluFalsa()
        p = mock.patch.object(S, "anuncio_ficha_payload",  # o Apollo do estoque inicial (relido no acompanhamento)
                              return_value={"estoque": {"aracatuba": {"da_para_montar": 24}}})
        p.start()
        self.addCleanup(p.stop)
        for alvo, valor in (("_magalu_chamar", self.mg),
                            ("_anuncio_foto_ml_baixar", lambda _u: b"\xff\xd8\xff\xe0" + b"0" * 64)):
            p = mock.patch.object(S, alvo, new=valor)
            p.start()
            self.addCleanup(p.stop)
        S.ficha_salvar("TUNAP109", {"nome": "Limpa Interior Tunap 109 Espuma 500ml", "marca": "Tunap", "ncm": "3402.90.39",
                                    "ean": "7891234567895", "peso_g": 600, "altura_cm": 30, "largura_cm": 15,
                                    "comprimento_cm": 14.2}, "admin", None)

    def rascunho(self, **extra):
        dados = {"codigo": "TUNAP109", "titulo": "Limpa Interior Automotivo Tunap 109 Espuma Spray 500ml",
                 "categoria": CAT, "categoria_nome": "Limpeza automotiva", "marca": "Tunap",
                 "descricao": "Espuma que limpa o interior do carro.", "preco": "99,90", "quantidade": "2",
                 "fotos": [FOTO], "atributos": {"Volume": "500 ml"}, "ficha_tecnica": {"Fabricante": "Tunap"}}
        dados.update(extra)
        return S.magalu_rascunho_salvar(dados, "admin", None)

    def apollo(self, dpm=24):
        return mock.patch.object(S, "anuncio_ficha_payload",
                                 return_value={"estoque": {"aracatuba": {"da_para_montar": dpm}}})

    def conferido(self, **extra):
        r = self.rascunho(**extra)
        res = S.magalu_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"], res)
        return res["rascunho"]


class Leituras(Base):

    def test_categoria_e_atributos(self):
        cats = S.magalu_categorias_buscar("limpeza")
        self.assertEqual(cats[0], {"categoria": CAT, "nome": "Limpeza automotiva",
                                   "caminho": "automotivo > limpeza automotiva", "folha": None})
        with self.assertRaises(ValueError):
            S.magalu_categorias_buscar("li")  # a spec pede 3 letras
        d = S.magalu_categoria_atributos(CAT)
        self.assertEqual([(a["nome"], a["obrigatorio"]) for a in d["atributos"]], [("Volume", True), ("Cor", False)],
                         "atributo inativo fica fora")
        self.assertEqual(d["atributos"][0]["valores"], ["500 ml", "1 L"])
        self.assertTrue(d["ficha_tecnica"][1]["recomendado"])
        S.magalu_categoria_atributos(CAT)
        self.assertEqual(sum(1 for c in self.mg.chamadas if c[1].endswith("/attributes")), 1, "guardado 24 h")
        with self.assertRaises(ValueError):
            S.magalu_categoria_atributos("123")

    def test_conta_do_preco_com_comissao_digitada(self):
        with mock.patch.object(S, "anuncio_custo_para_preco",
                               return_value={"custo": 40.0, "custo_origem": "Apollo", "margem_alvo": 0.2, "imposto": 0.12}), \
                mock.patch.object(S, "_magalu_taxas_precificador", return_value={}):
            sem = S.magalu_conta_preco("TUNAP109", "100")
            self.assertIsNone(sem["lucro"], "sem comissão = sem lucro (nunca zero)")
            self.assertIn("comissão", sem["sem_taxa"])
            c = S.magalu_conta_preco("TUNAP109", "100", comissao="16", taxa_fixa="5")
        self.assertEqual((c["comissao"], c["lucro"], c["margem"]), (16.0, 27.0, 0.27))
        self.assertEqual(c["preco_ideal"], round(45 / (1 - 0.16 - 0.12 - 0.2) + 0.0049, 2))

    def test_fotos_vem_do_anuncio_do_ml(self):
        inserir(anuncio_id="MLB9", ficha="TUNAP109")
        with mock.patch.object(S, "ml_itens_detalhe", return_value={"MLB9": {"fotos": [
                "http://http2.mlstatic.com/a-O.jpg", "https://evil.example.com/x.jpg", "https://http2.mlstatic.com/a-O.jpg"]}}):
            fotos = S.magalu_fotos_do_ml("TUNAP109")
        self.assertEqual(fotos, [{"url": "https://http2.mlstatic.com/a-O.jpg", "mlb": "MLB9"}])


class Rascunho(Base):

    def test_salvar_versao_e_categoria_nova_zera_atributos(self):
        r = self.rascunho()
        self.assertEqual((r["situacao"], r["preco"], r["quantidade"], r["atributos"]), ("rascunho", 99.9, 2, {"Volume": "500 ml"}))
        r2 = S.magalu_rascunho_salvar({"id": r["id"], "categoria": "aaaaaaaa-2222-3333-4444-555555555555"}, "admin", r["versao"])
        self.assertEqual((r2["atributos"], r2["ficha_tecnica"]), ({}, {}))
        with self.assertRaises(S.FichaConflito):
            S.magalu_rascunho_salvar({"id": r["id"], "preco": "100"}, "admin", r["versao"])

    def test_foto_so_do_mlstatic(self):
        with self.assertRaises(ValueError):
            self.rascunho(fotos=["https://noromakweb.com.br/expedicao/api/anuncios/foto?id=1"])
        with self.assertRaises(ValueError):
            self.rascunho(fotos=["http://http2.mlstatic.com/a.jpg"])

    def test_conferir_aponta_o_que_falta(self):
        r = self.rascunho(marca="", atributos={}, ficha_tecnica={}, preco_de="50")
        res = S.magalu_rascunho_conferir(r["id"], "admin")
        texto = " ".join(res["erros"])
        self.assertFalse(res["ok"])
        for trecho in ("marca", "Volume", "Fabricante", "preço “de”"):
            self.assertIn(trecho, texto)
        self.assertEqual(res["rascunho"]["situacao"], "rascunho")

    def test_foto_que_nao_e_jpeg_barra(self):
        r = self.rascunho()
        with mock.patch.object(S, "_anuncio_foto_ml_baixar", return_value=b"\x89PNG" + b"0" * 64):
            res = S.magalu_rascunho_conferir(r["id"], "admin")
        self.assertIn("JPEG", " ".join(res["erros"]))

    def test_corpo_do_sku_segue_a_spec(self):
        r = self.conferido()
        _erros, ctx = S._magalu_conferir_local(r)
        c = S.magalu_sku_corpo(r, S.ficha_ler("TUNAP109"), ctx)
        self.assertEqual(c["sku"], "TUNAP109")
        self.assertEqual(c["channels"], [{"id": S.MAGALU_CANAL_ID}])
        self.assertEqual(c["identifiers"], [{"type": "ean", "value": "7891234567895"}])
        self.assertTrue(c["has_ean"])
        self.assertEqual([d["name"] for d in c["dimensions"]], ["product", "package"])
        self.assertEqual(c["dimensions"][1]["weight"], {"value": 600, "unit": "g"})
        self.assertEqual(c["images"], [{"reference": FOTO, "type": "image/jpeg"}])
        self.assertEqual((c["ncm"], c["origin"], c["category"]), ("34029039", "national", {"id": CAT}))
        self.assertEqual(c["attributes"], [{"name": "Volume", "value": "500 ml"}])
        p = S.magalu_preco_corpo(r)
        self.assertEqual((p["price"], p["list_price"], p["currency"], p["normalizer"]), (9990, 9990, "BRL", 100))


class Publicar(Base):

    def test_publica_aguarda_o_sku_e_manda_preco_e_estoque(self):
        r = self.conferido()
        self.mg.aparece_depois = 1
        with self.apollo():
            res = S.magalu_publicar(r["id"], "admin")
        self.assertEqual((res["situacao"], res["rascunho"]["trace_id"]), ("aguardando", "tr-1"))
        self.assertEqual(self.mg.escritas("/prices/"), [], "202 sozinho não publica nem manda preço")
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "aguardando", "o SKU ainda não apareceu")
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "publicado", a)
        self.assertEqual(self.mg.precos["TUNAP109"]["currency"], "BRL")
        self.assertEqual(self.mg.estoque["TUNAP109"], 2)
        with S.DB_LOCK:
            ln = dict(S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='magalu'").fetchone())
        self.assertEqual((ln["anuncio_id"], ln["conta"], ln["controle"], ln["estoque_modo"]),
                         ("TUNAP109", "aracatuba", "noromak", "manual"))

    def test_sku_que_ja_existe_na_magalu_nao_e_sobrescrito(self):
        r = self.conferido()
        self.mg.skus["TUNAP109"] = {"title": "de outra ferramenta"}
        with self.apollo():
            res = S.magalu_publicar(r["id"], "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertIn("Já existe", res["erros"][0])
        self.assertEqual(self.mg.escritas("/skus"), [])

    def test_quantidade_acima_do_apollo_nao_envia(self):
        r = self.conferido()
        with self.apollo(dpm=1):
            res = S.magalu_publicar(r["id"], "admin")
        self.assertEqual(res["situacao"], "validado")
        self.assertEqual(self.mg.escritas("/skus"), [])

    def test_recusa_422_e_erro_e_nada_criado(self):
        r = self.conferido()
        self.mg.criar_erro = S.MagaluRecusou("Magalu HTTP 422: title: too long",
                                             {"detail": [{"loc": ["body", "title"], "msg": "too long"}]}, 422)
        with self.apollo():
            res = S.magalu_publicar(r["id"], "admin")
        self.assertEqual(res["situacao"], "erro")
        self.assertIn("too long", res["erros"][0])

    def test_sem_resposta_aguarda_e_confere_pela_magalu(self):
        r = self.conferido()

        def criar_e_cair(payload):
            self.mg.skus["TUNAP109"] = payload
            raise S.MagaluSemResposta("timeout")
        orig = self.mg.__call__

        def chamar(method, path, params=None, payload=None, *, timeout=35):
            if method == "POST" and path == "/seller/v1/portfolios/skus":
                return criar_e_cair(payload)
            return orig(method, path, params, payload, timeout=timeout)
        with self.apollo(), mock.patch.object(S, "_magalu_chamar", new=chamar):
            res = S.magalu_publicar(r["id"], "admin")
            self.assertEqual(res["situacao"], "aguardando")
            a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "publicado")

    def test_sku_que_nunca_aparece_vira_incerto_e_trace_com_erro_vira_erro(self):
        r = self.conferido()
        self.mg.nao_cria = True
        with self.apollo():
            S.magalu_publicar(r["id"], "admin")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET enviado_em='2026-01-01T00:00:00+00:00' WHERE id=?", (r["id"],))
            S.DB.commit()
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "incerto")
        res = S.magalu_incerto_liberar(r["id"], "nao_criado", "admin")
        self.assertEqual(res["rascunho"]["situacao"], "validado")
        self.mg.trace = [{"severity": "error", "message": "Categoria inválida"}]
        with self.apollo():
            S.magalu_publicar(r["id"], "admin")
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "erro")
        self.assertIn("Categoria inválida", a["erros"][0])

    def test_preco_recusado_fica_aguardando_e_tenta_de_novo(self):
        r = self.conferido()
        self.mg.preco_erro = S.MagaluRecusou("Magalu HTTP 400: preço", {}, 400)
        with self.apollo():
            S.magalu_publicar(r["id"], "admin")
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "aguardando")
        self.assertIn("preço", a["erros"][0])
        self.mg.preco_erro = None
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "publicado")

    def test_um_anuncio_por_produto(self):
        r = self.conferido()
        with self.apollo():
            S.magalu_publicar(r["id"], "admin")
        S.magalu_acompanhar(r["id"], forcar=True)
        r2 = self.conferido()
        with self.assertRaises(ValueError):
            S.magalu_publicar_reservar(r2["id"], "admin")

    def test_publicando_no_boot_sem_envio_volta_a_validado(self):
        """Revisao: sem a marca "enviado" o POST nunca saiu: nada a acompanhar (um SKU alheio nunca e tomado)."""
        r = self.conferido()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET situacao='publicando' WHERE id=?", (r["id"],))
            S.DB.commit()
        S.ensure_anuncios_schema()
        novo = S.magalu_rascunho_ler(r["id"])
        self.assertEqual(novo["situacao"], "validado")
        self.assertIn("reiniciou", novo["erros"][0])

    def test_publicando_no_boot_depois_do_envio_vira_aguardando(self):
        r = self.conferido()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET situacao='publicando', enviado_em=? WHERE id=?",
                         (S.now_iso(), r["id"]))
            S.DB.commit()
        S.ensure_anuncios_schema()
        novo = S.magalu_rascunho_ler(r["id"])
        self.assertEqual((novo["situacao"], novo["sku"], novo["etapa"]), ("aguardando", "TUNAP109", "sku"))


class Revisao(Base):
    """revisar-antes-de-subir da V20.34 (06/11, "corrija tudo")."""

    def publicar(self, r):
        with self.apollo():
            return S.magalu_publicar(r["id"], "admin")

    def envio_antigo(self, r):
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET enviado_em='2026-01-01T00:00:00+00:00' WHERE id=?",
                         (r["id"],))
            S.DB.commit()

    def test_sku_de_outra_ferramenta_nunca_e_tomado_no_acompanhamento(self):
        r = self.conferido()
        self.mg.nao_cria = True
        self.publicar(r)
        self.mg.skus["TUNAP109"] = {"title": "Produto de outra ferramenta"}  # aparece um SKU alheio com o codigo
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "erro")
        self.assertIn("não é o que o Noromak enviou", a["erros"][0])
        self.assertEqual((self.mg.escritas("/prices/"), self.mg.escritas("/stocks/")), ([], []))
        with S.DB_LOCK:
            self.assertIsNone(S.DB.execute("SELECT 1 FROM anuncio_canal_v2023 WHERE canal='magalu'").fetchone())
        with self.assertRaises(ValueError):  # erro de envio nao volta a aguardando pelo "Conferir de novo"
            S.magalu_incerto_liberar(r["id"], "conferir", "admin")

    def test_recusa_do_envio_nao_pode_conferir_de_novo(self):
        r = self.conferido()
        self.mg.criar_erro = S.MagaluRecusou("Magalu HTTP 422: x", {}, 422)
        self.publicar(r)
        with self.assertRaises(ValueError):
            S.magalu_incerto_liberar(r["id"], "conferir", "admin")

    def test_preco_recusado_para_depois_de_5_tentativas(self):
        r = self.conferido()
        self.mg.preco_erro = S.MagaluRecusou("Magalu HTTP 403: sem permissão", {}, 403)
        self.publicar(r)
        for _ in range(S.MAGALU_ETAPA_TENTATIVAS - 1):
            self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "aguardando")
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "erro")
        self.assertIn("o preço não foi aceito", a["erros"][0])
        self.mg.preco_erro = None
        with mock.patch.object(S, "magalu_acompanhar_iniciar", side_effect=lambda rid, ator: S.magalu_rascunho_ler(rid)):
            res = S.magalu_incerto_liberar(r["id"], "conferir", "admin")  # o SKU e nosso: pode tentar de novo
        self.assertEqual(res["rascunho"]["situacao"], "aguardando")
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "publicado")

    def test_estoque_inicial_e_o_do_apollo_agora(self):
        r = self.conferido(quantidade="20")
        self.publicar(r)
        with mock.patch.object(S, "anuncio_ficha_payload",
                               return_value={"estoque": {"aracatuba": {"da_para_montar": 3}}}):
            a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "publicado", a)
        self.assertEqual(self.mg.estoque["TUNAP109"], 3, "nunca o pedido de antes, sempre o que dá para montar agora")
        self.assertIn("Estoque inicial 3", " ".join(a["erros"]))
        with S.DB_LOCK:
            ln = S.DB.execute("SELECT estoque FROM anuncio_canal_v2023 WHERE canal='magalu'").fetchone()
        self.assertEqual(ln[0], 3, "o estoque da linha é o lido na Magalu")

    def test_estoque_inicial_sem_apollo_nao_envia(self):
        r = self.conferido()
        self.publicar(r)
        with mock.patch.object(S, "anuncio_ficha_payload", side_effect=RuntimeError("Apollo fora")):
            a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "aguardando")
        self.assertEqual(self.mg.escritas("/stocks/"), [])

    def test_estoque_inicial_usa_patch_quando_ja_tem_linha(self):
        r = self.conferido()
        self.publicar(r)
        self.mg.estoque["TUNAP109"] = 0  # a Magalu criou a linha junto com o SKU
        S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual([c[0] for c in self.mg.escritas("/stocks/")], ["PATCH"])

    def test_nao_publica_antes_de_a_magalu_mostrar_preco_e_estoque(self):
        r = self.conferido()
        self.publicar(r)
        self.mg.nao_muda.add("TUNAP109")  # o estoque aceito (202) ainda nao aparece
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "aguardando")
        self.assertIn("esperando a Magalu", a["erros"][0])
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET etapa_em='2026-01-01T00:00:00+00:00' WHERE id=?",
                         (r["id"],))
            S.DB.commit()
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(a["situacao"], "publicado")
        self.assertIn("ainda não mostra o estoque", " ".join(a["erros"]))

    def test_leitura_falhando_tambem_conta_o_prazo(self):
        r = self.conferido()
        self.mg.nao_cria = True
        self.publicar(r)
        self.envio_antigo(r)
        with mock.patch.object(S, "_magalu_sku_ler", side_effect=S.MagaluSemResposta("fora do ar")):
            self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "incerto")

    def test_sku_nosso_que_apareceu_tarde_e_reconhecido_no_publicar(self):
        r = self.conferido()
        self.mg.nao_cria = True
        self.publicar(r)
        self.envio_antigo(r)
        S.magalu_acompanhar(r["id"], forcar=True)
        S.magalu_incerto_liberar(r["id"], "nao_criado", "admin")
        self.mg.skus["TUNAP109"] = {"title": r["titulo"]}  # o nosso apareceu depois
        res = self.publicar(r)
        self.assertEqual(res["situacao"], "aguardando")
        self.assertEqual(len(self.mg.escritas("/portfolios/skus")), 1, "não cria de novo")
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "publicado")

    def test_categoria_com_subcategorias_e_sku_com_barra_barram(self):
        self.mg.filhas[CAT] = ["99999999-2222-3333-4444-555555555555"]
        r = self.rascunho()
        res = S.magalu_rascunho_conferir(r["id"], "admin")
        self.assertIn("subcategorias", " ".join(res["erros"]))
        S.ficha_salvar("A1/B2", {"nome": "Kit", "marca": "X", "peso_g": 1, "altura_cm": 1, "largura_cm": 1,
                                 "comprimento_cm": 1}, "admin", None)
        r2 = self.rascunho(codigo="A1/B2")
        self.assertIn("caractere", " ".join(S.magalu_rascunho_conferir(r2["id"], "admin")["erros"]))

    def test_nome_de_atributo_longo_e_valor_fora_da_lista(self):
        r = self.rascunho(atributos={"Volume": "750 ml", "Nome de atributo bem comprido": "x"})
        texto = " ".join(S.magalu_rascunho_conferir(r["id"], "admin")["erros"])
        self.assertIn("Volume: escolha um valor da lista", texto)
        self.assertIn("até 20 caracteres", texto)

    def test_fotos_conferidas_em_paralelo_com_prazo(self):
        import threading
        trava = threading.Event()
        with mock.patch.object(S, "_anuncio_foto_ml_baixar", side_effect=lambda u: trava.wait(5) or b"\xff\xd8\xff0"):
            erros, avisos = S._magalu_fotos_conferir([FOTO, FOTO.replace("123", "456")], 0.2)
        trava.set()
        self.assertEqual(erros, [])
        self.assertIn("não terminaram", avisos[0])

    def test_volta_do_worker_nao_espera_a_magalu(self):
        import time as _t
        r = self.conferido()
        self.publicar(r)
        with mock.patch.object(S, "MAGALU_ACOMPANHAR_ESPERA_S", 0.2), \
                mock.patch.object(S, "_magalu_sku_ler", side_effect=lambda sku: _t.sleep(2)):
            t0 = _t.monotonic()
            S.magalu_acompanhar_pendentes_fundo()
            self.assertLess(_t.monotonic() - t0, 1.5, "a volta do estoque do ML não fica presa na Magalu")
            _t.sleep(2.2)

    def test_rota_acompanhar_responde_na_hora(self):
        r = self.conferido()
        self.publicar(r)
        with mock.patch.object(S, "_magalu_acompanhar_seguro"):
            st, d = self.cliente.pedir("POST", "/api/anuncios/magalu/acompanhar", {"id": r["id"]})
        self.assertEqual((st, d["rascunho"]["situacao"]), (200, "aguardando"))

    def test_sku_e_nosso_pela_marca_do_envio_mesmo_com_o_titulo_mudado(self):
        r = self.conferido()
        self.mg.aparece_depois = 1
        self.publicar(r)
        corpo = self.mg.skus["TUNAP109"]
        self.assertEqual(corpo["extra_data"][0]["name"], "noromak_envio")
        self.assertTrue(corpo["extra_data"][0]["value"].startswith(f"{r['id']}:"))
        corpo["title"] = "Título que a Magalu mudou"  # moderacao/catalogo
        S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "publicado")

    def test_mesmo_titulo_sem_a_marca_nao_e_nosso(self):
        r = self.conferido()
        self.mg.criar_erro = None
        self.mg.trace = [{"severity": "error", "message": "recusado"}]
        self.mg.nao_cria = True
        self.publicar(r)
        self.assertEqual(S.magalu_acompanhar(r["id"], forcar=True)["situacao"], "erro")
        self.assertIsNone(S.magalu_rascunho_ler(r["id"])["enviado_em"], "trace recusou: nada criado, o envio não vale")
        r2 = self.conferido()
        # outra ferramenta criou com o mesmo codigo e o mesmo titulo (com extra_data de outra pessoa)
        self.mg.skus["TUNAP109"] = {"title": r2["titulo"], "extra_data": [{"name": "IdProduct", "value": "80527"}]}
        res = self.publicar(r2)
        self.assertEqual(res["situacao"], "validado")
        self.assertIn("Já existe", res["erros"][0])

    def test_estoque_inicial_cria_a_linha_quando_o_get_da_404(self):
        r = self.conferido()
        self.mg.estoque_404 = True
        self.publicar(r)
        a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual([c[0] for c in self.mg.escritas("/stocks/")], ["POST"])
        self.assertEqual(a["situacao"], "publicado")

    def test_apollo_sem_leitura_nao_gasta_tentativa(self):
        r = self.conferido()
        self.publicar(r)
        with mock.patch.object(S, "anuncio_ficha_payload", side_effect=RuntimeError("Apollo fora")):
            for _ in range(S.MAGALU_ETAPA_TENTATIVAS + 2):
                a = S.magalu_acompanhar(r["id"], forcar=True)
        self.assertEqual((a["situacao"], a["tentativas"]), ("aguardando", 0))

    def test_atributos_paginados_e_os_que_nao_sao_de_variacao_vao_para_a_ficha(self):
        self.mg.attrs_extras = 120
        d = S.magalu_categoria_atributos(CAT)
        self.assertEqual([a["nome"] for a in d["atributos"]], ["Volume", "Cor"])
        nomes = [a["nome"] for a in d["ficha_tecnica"]]
        self.assertIn("Dimensões do Produto", nomes)
        self.assertIn("Extra 119", nomes, "a página 2 também vem")

    def test_reservado_depois_do_envio_antigo_volta_a_validado_no_boot(self):
        r = self.conferido()
        with S.DB_LOCK:  # envio de uma tentativa ANTERIOR (ex.: "Não foi criado")
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET enviado_em='2026-01-01T00:00:00+00:00' WHERE id=?",
                         (r["id"],))
            S.DB.commit()
        S.magalu_publicar_reservar(r["id"], "admin")
        S.ensure_anuncios_schema()
        self.assertEqual(S.magalu_rascunho_ler(r["id"])["situacao"], "validado")

    def test_intervalo_nao_apaga_a_trava_de_quem_ainda_aguarda(self):
        r = self.conferido()
        self.mg.aparece_depois = 5
        self.publicar(r)
        S.magalu_acompanhar(r["id"], forcar=True)
        trava = S._magalu_acompanhar_trava(r["id"])
        S.magalu_acompanhar(r["id"])  # cai no intervalo de 20 s
        self.assertIs(S._magalu_acompanhar_trava(r["id"]), trava)
        self.assertIn(r["id"], S._MAGALU_ACOMPANHAR_VISTO)


    def test_republicar_limpa_a_remocao_do_admin(self):
        r = self.conferido()
        with S.DB_LOCK:
            S._magalu_canal_noromak("TUNAP109", r, "admin", "PUBLISHED", 2)
            S.DB.execute("UPDATE anuncio_canal_v2023 SET removido_em='2026-01-01', estoque_modo='automatico' "
                         "WHERE canal='magalu'")
            S._magalu_canal_noromak("TUNAP109", r, "admin", "PUBLISHED", 5)
            ln = dict(S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='magalu'").fetchone())
            S.DB.commit()
        self.assertEqual((ln["removido_em"], ln["estoque_modo"], ln["estoque"]), ("", "manual", 5))

    def test_token_recusado_ja_renovado_nao_renova_de_novo(self):
        import time as _t
        chamadas = []
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM magalu_integration")
            S.DB.execute("INSERT INTO magalu_integration(id,access_token,refresh_token,token_expires_at) "
                         "VALUES(1,'novo','r1',?)", (int(_t.time()) + 3600,))
            S.DB.commit()
        with mock.patch.object(S, "_magalu_token_http", side_effect=lambda d: chamadas.append(d) or {}):
            self.assertEqual(S.magalu_access_token(force=True, recusado="velho"), "novo")
        self.assertEqual(chamadas, [], "outro já renovou: aproveita")


class MagaluEstoque(EstoqueBase):

    def setUp(self):
        super().setUp()
        self.mg = MagaluFalsa()
        for alvo, valor in (("_magalu_chamar", self.mg), ("MAGALU_ESTOQUE_RELEITURA_ESPERAS", (0, 0))):
            p = mock.patch.object(S, alvo, new=valor)
            p.start()
            self.addCleanup(p.stop)

    def inserir_magalu(self, sku="P1", modo="manual", estoque=None, controle="noromak"):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,"
                         "titulo,estoque,estoque_modo) VALUES('magalu','aracatuba',?,?,?,?,?,?,?)",
                         (sku, sku, sku, controle, "T " + sku, estoque, modo))
            S.DB.commit()
        self.mg.skus[sku] = {"title": "T"}

    def test_manual_grava_e_rele(self):
        self.inserir_magalu()
        self.mg.estoque["P1"] = 5
        r = S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["antes"], 5, "RESERVED não entra no número")
        esc = self.mg.escritas("/stocks/")
        self.assertEqual((esc[0][0], esc[0][3]), ("PATCH", {"channel": {"id": S.MAGALU_CANAL_ID}, "quantity": 8, "type": "AVAILABLE"}))
        self.assertEqual(S.anuncio_estoque_log("magalu", "aracatuba", "P1")[0]["resultado"], "ok")

    def test_dois_centros_de_distribuicao_nao_grava(self):
        self.inserir_magalu()
        self.mg.estoque["P1"] = 5
        self.mg.branches["P1"] = ["cd-a", "cd-b"]
        r = S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        self.assertFalse(r["ok"])
        self.assertEqual(self.mg.escritas("/stocks/"), [])

    def test_aceitou_sem_mostrar_espera_e_na_3a_vez_e_erro(self):
        self.inserir_magalu()
        self.mg.estoque["P1"] = 5
        self.mg.nao_muda.add("P1")
        S._MAGALU_ESTOQUE_SEM_MOSTRAR.clear()
        r = S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        self.assertEqual((r["ok"], r["resultado"]), (False, "ignorado"), "aceito e ainda não mostrado: espera")
        S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        r = S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        self.assertEqual(r["resultado"], "erro", "sem laço calado")
        self.assertIn("3 vezes", r["erro"])

    def test_automatico_mesmo_disponivel_dos_outros_canais(self):
        self.inserir_magalu(modo="automatico", estoque=3)
        self.mg.estoque["P1"] = 3
        self.apollo["P1"] = {1: 10}
        S.anuncios_apollo_ler("P1", "aracatuba")
        self.assertIn("P1", S._anuncios_codigos_automatico("aracatuba"))
        S.anuncio_estoque_config_definir("magalu", "simulacao", "luis")
        r = S.anuncios_estoque_rodada("magalu", ator="sistema")
        self.assertEqual((r["simulados"], r["enviados"]), (1, 0), "a Magalu tem o próprio interruptor")
        S.anuncio_estoque_config_definir("magalu", "automatico", "luis")
        fila([linha("PED1", "P1", 2, Marketplace="MAGALU")])
        r = S.anuncios_estoque_rodada("magalu", ator="sistema")
        self.assertEqual(r["enviados"], 1, r)
        self.assertEqual(self.mg.estoque["P1"], 8, "Apollo 10 menos 2 a baixar")

    def test_volta_do_worker_inclui_a_magalu(self):
        self.inserir_magalu(modo="automatico")
        self.mg.estoque["P1"] = 0
        self.apollo["P1"] = {1: 4}
        S.anuncio_estoque_config_definir("magalu", "automatico", "luis")
        afetadas = S.anuncios_estoque_volta({})
        self.assertIn("magalu", afetadas)
        self.assertEqual(self.mg.estoque["P1"], 4)

    def test_payload_da_conta_magalu(self):
        self.inserir_magalu(modo="automatico")
        self.apollo["P1"] = {1: 6}
        S.anuncios_apollo_ler("P1", "aracatuba")
        p = S.anuncios_estoque_payload("magalu", "P1")
        self.assertEqual((p["conta"], p["anuncio"]["anuncio_id"], p["itens"][0]["calculado"]), ("magalu", "P1", 6))


class Rotas(Base):

    def test_rotas_e_permissoes(self):
        r = self.rascunho()
        st, d = self.cliente.pedir("GET", f"/api/anuncios/magalu/rascunhos?codigo=TUNAP109")
        self.assertEqual((st, d["rascunhos"][0]["id"]), (200, r["id"]))
        st, d = self.cliente.pedir("POST", "/api/anuncios/magalu/conferir", {"id": r["id"]})
        self.assertEqual((st, d["situacao"]), (200, "validado"), d)
        st, d = self.cliente.pedir("GET", "/api/anuncios/magalu/categorias?nome=limpeza")
        self.assertEqual((st, d["categorias"][0]["categoria"]), (200, CAT))


if __name__ == "__main__":
    import unittest
    unittest.main()
