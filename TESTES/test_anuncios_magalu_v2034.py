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

    def __call__(self, method, path, params=None, payload=None, *, timeout=35):
        self.chamadas.append((method, path, params, payload))
        if path == "/seller/v1/portfolios/category":
            return {"results": [{"id": CAT, "name": "Limpeza automotiva", "path": ["Automotivo", "Limpeza automotiva"],
                                 "is_leaf": True}]}
        if path.endswith("/attributes"):
            return {"results": [{"name": "Volume", "required": "required", "values": ["500 ml", "1 L"]},
                                {"name": "Cor", "required": "optional"}]}
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
                return {"sku": sku, "status": "PUBLISHED", "title": self.skus[sku]["title"]}
            if sku in self.skus:
                self.aparece_depois -= 1
            raise S.MagaluRecusou("Magalu HTTP 404: not found", {"message": "not found"}, 404)
        if path.startswith("/v0/traces/"):
            return {"results": self.trace}
        if path.startswith("/seller/v1/portfolios/prices/"):
            if self.preco_erro:
                raise self.preco_erro
            self.precos[path.rsplit("/", 1)[1]] = payload
            return {"trace_id": "tr-p"}
        if path.startswith("/seller/v1/portfolios/stocks/"):
            sku = path.rsplit("/", 1)[1]
            if method == "GET":
                if sku not in self.skus:
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
        self.mg = MagaluFalsa()
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
                                   "caminho": "Automotivo > Limpeza automotiva", "folha": True})
        d = S.magalu_categoria_atributos(CAT)
        self.assertEqual([(a["nome"], a["obrigatorio"]) for a in d["atributos"]], [("Volume", True), ("Cor", False)])
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

        def criar_e_cair(*a, **k):
            self.mg.skus["TUNAP109"] = {"title": "x"}
            raise S.MagaluSemResposta("timeout")
        orig = self.mg.__call__

        def chamar(method, path, params=None, payload=None, *, timeout=35):
            if method == "POST" and path == "/seller/v1/portfolios/skus":
                return criar_e_cair()
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

    def test_publicando_no_boot_vira_aguardando(self):
        r = self.conferido()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_magalu_v2034 SET situacao='publicando' WHERE id=?", (r["id"],))
            S.DB.commit()
        S.ensure_anuncios_schema()
        novo = S.magalu_rascunho_ler(r["id"])
        self.assertEqual((novo["situacao"], novo["sku"], novo["etapa"]), ("aguardando", "TUNAP109", "sku"))


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

    def test_aceitou_sem_mostrar_e_erro(self):
        self.inserir_magalu()
        self.mg.estoque["P1"] = 5
        self.mg.nao_muda.add("P1")
        r = S.anuncio_estoque_manual("magalu", "P1", 8, "luis")
        self.assertFalse(r["ok"])
        self.assertIn("mostra", r["erro"])

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
