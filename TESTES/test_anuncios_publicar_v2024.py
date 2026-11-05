"""V20.24 - Anuncios fase 2, tarefa 5: conferir no ML (validate) e Publicar (a unica escrita no ML)."""

import json
import socket
import threading
import urllib.error
from unittest import mock

from base import S, TesteNoromak, Cliente

SENHA = "SenhaTeste#2024"

ATRIBUTOS = [
    {"id": "BRAND", "name": "Marca", "tags": {"required": True}, "value_type": "string"},
    {"id": "COLOR", "name": "Cor", "tags": {}, "value_type": "list",
     "values": [{"id": "52049", "name": "Preto"}]},
]


def erro_http(status, corpo):
    return RuntimeError(f"Mercado Livre respondeu HTTP {status}: {json.dumps(corpo)}")


class FalsoML:
    """_ml_promo_http falso: grava as chamadas e responde por caminho."""

    def __init__(self):
        self.chamadas = []
        self.timeouts = []
        self.validate = None      # None = 204; Exception = levanta; lista = uma resposta por chamada
        self.itens = []           # respostas/excecoes do POST /items, em ordem
        self.descricao = None     # Exception = levanta

    def __call__(self, metodo, caminho, payload=None, timeout=None):
        self.chamadas.append((metodo, caminho, payload, S.ml_casa_atual()))
        self.timeouts.append((caminho, timeout))
        if caminho == "/items/validate":
            v = self.validate.pop(0) if isinstance(self.validate, list) and self.validate else self.validate
            if isinstance(v, Exception):
                raise v
            return {}
        if caminho == "/items":
            r = self.itens.pop(0) if self.itens else {"id": "MLB5550001", "status": "active", "price": 49.9,
                                                       "available_quantity": 2, "title": payload.get("title")}
            if isinstance(r, Exception):
                raise r
            return r
        if caminho.endswith("/description"):
            if isinstance(self.descricao, Exception):
                raise self.descricao
            return {"plain_text": payload["plain_text"]}
        raise AssertionError(caminho)

    def posts_items(self):
        return [c for c in self.chamadas if c[1] == "/items"]


def apollo(disp):
    return {"ok": True, "estoques": [{"revenda": 1, "disponivel": disp, "respondeu": disp is not None}]}


class PublicarTeste(TesteNoromak):

    def setUp(self):
        super().setUp()
        S._ANUNCIO_ATRIBUTOS_CACHE.clear()
        S.ficha_salvar("P1", {"nome": "Filtro"}, "t", None)
        self.ml = FalsoML()
        self.fotos_subidas = []
        self.apollo = apollo(10)

        def get_falso(caminho, **kw):
            if caminho.endswith("/attributes"):
                return ATRIBUTOS
            return {"id": "MLB123", "name": "Filtros", "path_from_root": []}

        def foto_falsa(dados, tipo):
            self.fotos_subidas.append((dados, tipo, S.ml_casa_atual()))
            return f"PIC{len(self.fotos_subidas)}"

        self.foto_falsa = foto_falsa
        self._ps = [mock.patch.object(S, "ml_api_get", get_falso),
                    mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]),
                    mock.patch.object(S, "_ml_promo_http", self.ml),
                    mock.patch.object(S, "_ml_foto_upload", side_effect=lambda d, t: self.foto_falsa(d, t)),
                    mock.patch.object(S, "ml_access_token", return_value=("tok", "1")),
                    mock.patch.object(S, "crm_stock_lookup", side_effect=lambda *a, **k: self.apollo)]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)

    def foto(self):
        f = S.anuncio_foto_adicionar("P1", _png(), "t")
        return f["id"]

    def rascunho(self, **extra):
        d = {"codigo": "P1", "conta": "aracatuba", "titulo": "Filtro de oleo"}
        r = S.anuncio_rascunho_salvar(d, "t", None)
        campos = {"id": r["id"], "categoria": "MLB123", "preco": "49,90", "quantidade": 2,
                  "atributos": {"BRAND": "Wurth", "COLOR": "Preto"}, "fotos": [self.foto()],
                  "descricao": "Filtro bom"}
        campos.update(extra)
        return S.anuncio_rascunho_salvar(campos, "t", r["versao"])

    def validado(self, **extra):
        r = self.rascunho(**extra)
        v = S.anuncio_validar(r["id"], "t")
        self.assertEqual(v["situacao"], "validado", v)
        return v["rascunho"]

    def canal(self, mlb):
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='ml' AND anuncio_id=?", (mlb,)).fetchone()
        return dict(row) if row else None

    # ---- validar ----
    def test_validar_ok(self):
        r = self.rascunho()
        v = S.anuncio_validar(r["id"], "t")
        self.assertTrue(v["ok"])
        self.assertEqual(v["rascunho"]["situacao"], "validado")
        metodo, caminho, corpo, casa = self.ml.chamadas[0]
        self.assertEqual((metodo, caminho, casa), ("POST", "/items/validate", "aracatuba"))
        self.assertEqual(corpo["pictures"], [])  # a foto so sobe no Publicar
        self.assertIn({"id": "COLOR", "value_id": "52049"}, corpo["attributes"])
        self.assertEqual(self.fotos_subidas, [])
        self.assertEqual(self.ml.posts_items(), [])

    def test_validar_erro_mostra_causas(self):
        r = self.rascunho()
        self.ml.validate = erro_http(400, {"message": "Validation error", "cause": [
            {"type": "error", "code": "item.pictures.missing", "references": ["item.pictures"], "message": "Sem foto"},
            {"type": "warning", "code": "item.x", "message": "So aviso"},
            {"type": "error", "code": "item.attribute.invalid", "references": ["item.attributes"],
             "message": "Atributo MODEL obrigatorio"}]})
        v = S.anuncio_validar(r["id"], "t")
        self.assertFalse(v["ok"])
        self.assertEqual(v["situacao"], "rascunho")
        self.assertEqual(v["erros"], ["Atributo MODEL obrigatorio (item.attribute.invalid)"])
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["erros"], v["erros"])
        # so causas de foto = conferido (a foto e conferida localmente)
        self.ml.validate = erro_http(400, {"cause": [{"type": "error", "code": "item.pictures.missing",
                                                      "message": "Sem foto"}]})
        self.assertEqual(S.anuncio_validar(r["id"], "t")["situacao"], "validado")
        # edicao de um `erro` revalida
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao='erro' WHERE id=?", (r["id"],))
            S.DB.commit()
        self.ml.validate = None
        self.assertEqual(S.anuncio_validar(r["id"], "t")["situacao"], "validado")

    def test_validar_sem_foto_ou_obrigatorio_local(self):
        r = self.rascunho(fotos=[], atributos={"COLOR": "Preto"})
        v = S.anuncio_validar(r["id"], "t")
        self.assertEqual(v["situacao"], "rascunho")
        self.assertTrue(any("foto" in e for e in v["erros"]), v["erros"])
        self.assertTrue(any("Marca" in e for e in v["erros"]), v["erros"])
        self.assertEqual(self.ml.chamadas, [])  # nem chamou o ML
        with self.assertRaises(S.FichaConflito):  # rascunho editado durante a conferencia
            r2 = self.rascunho()
            orig = S._anuncio_conferir_local

            def editar_no_meio(rr):
                S.anuncio_rascunho_salvar({"id": rr["id"], "preco": "50"}, "x", rr["versao"])
                return orig(rr)
            with mock.patch.object(S, "_anuncio_conferir_local", editar_no_meio):
                S.anuncio_validar(r2["id"], "t")

    # ---- publicar ----
    def test_publicar_ok_cria_linha_noromak(self):
        r = self.validado()
        p = S.anuncio_publicar(r["id"], "luis")
        self.assertTrue(p["ok"], p)
        self.assertEqual((p["situacao"], p["mlb"]), ("publicado", "MLB5550001"))
        self.assertEqual([c[2] for c in self.fotos_subidas], ["aracatuba"])
        _, _, corpo, casa = self.ml.posts_items()[0]
        self.assertEqual(casa, "aracatuba")
        self.assertEqual(corpo["pictures"], [{"id": "PIC1"}])
        self.assertEqual((corpo["title"], corpo["available_quantity"], corpo["price"]), ("Filtro de oleo", 2, 49.9))
        desc = [c for c in self.ml.chamadas if c[1] == "/items/MLB5550001/description"]
        self.assertEqual(desc[0][2], {"plain_text": "Filtro bom"})
        lido = S.anuncio_rascunho_ler(r["id"])
        self.assertEqual((lido["situacao"], lido["mlb"]), ("publicado", "MLB5550001"))
        c = self.canal("MLB5550001")
        self.assertEqual((c["conta"], c["ficha_codigo"], c["controle"], c["sku_anuncio"], c["estoque"], c["status"]),
                         ("aracatuba", "P1", "noromak", "P1", 2, "active"))
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM audit_log WHERE action='anuncios.publicar'").fetchone()[0]
        self.assertEqual(n, 1)
        with self.assertRaises(ValueError):  # publicado nao e mais editado
            S.anuncio_rascunho_salvar({"id": r["id"], "preco": "1"}, "t", lido["versao"])

    def test_publicar_duas_vezes_um_anuncio(self):  # RF1
        r = self.validado()
        S.anuncio_publicar(r["id"], "t")
        with self.assertRaises(ValueError):
            S.anuncio_publicar(r["id"], "t")
        self.assertEqual(len(self.ml.posts_items()), 1)
        # rota: lock ocupado = 409; publicacao repetida = 400; nunca um 2o POST /items
        r2 = self.validado()
        S.ANUNCIOS_PUBLICAR_LOCK.acquire()
        try:
            st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho/publicar", {"id": r2["id"]})
        finally:
            S.ANUNCIOS_PUBLICAR_LOCK.release()
        self.assertEqual((st, c["error"]), (409, "Outra publicação em andamento."))
        self.assertEqual(S.anuncio_rascunho_ler(r2["id"])["situacao"], "validado")
        st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho/publicar", {"id": r2["id"]})
        self.assertEqual((st, c["situacao"]), (200, "publicado"))
        st, _ = self.cliente.pedir("POST", "/api/anuncios/rascunho/publicar", {"id": r2["id"]})
        self.assertEqual(st, 400)
        self.assertEqual(len(self.ml.posts_items()), 2)
        # duas threads ao mesmo tempo na funcao (sem o lock da rota): a maquina de situacao segura
        r3 = self.validado()
        self.ml.itens = [{"id": "MLB5550003"}, {"id": "MLB5550004"}]
        porta = threading.Event()
        orig = S.anuncio_ficha_payload

        def lento(*a, **k):
            porta.wait(2)
            return orig(*a, **k)
        res = []
        with mock.patch.object(S, "anuncio_ficha_payload", lento):
            def rodar():
                try:
                    res.append(S.anuncio_publicar(r3["id"], "t")["situacao"])
                except ValueError as exc:
                    res.append(str(exc))
            ts = [threading.Thread(target=rodar) for _ in range(2)]
            for t in ts:
                t.start()
            porta.set()
            for t in ts:
                t.join(5)
        self.assertEqual(sorted(res), sorted(["publicado", "Este rascunho já está sendo publicado."]))
        self.assertEqual(len(self.ml.posts_items()), 3)

    def test_rede_caiu_vira_incerto_e_nao_repete(self):  # RF2
        for falha in (urllib.error.URLError("rede caiu"), socket.timeout("timed out"), TimeoutError("x"),
                      ConnectionResetError("reset"), erro_http(502, {"message": "bad gateway"}),
                      urllib.error.HTTPError("u", 503, "x", {}, None)):
            r = self.validado()
            antes = len(self.ml.posts_items())
            self.ml.itens = [falha]
            p = S.anuncio_publicar(r["id"], "t")
            self.assertEqual(p["situacao"], "incerto", falha)
            self.assertEqual(len(self.ml.posts_items()), antes + 1, falha)  # nao repete
            self.assertEqual(S.anuncio_rascunho_ler(r["id"])["situacao"], "incerto")
            with self.assertRaises(ValueError):  # nao publica de novo sem liberar
                S.anuncio_publicar(r["id"], "t")
            with self.assertRaises(ValueError):  # nem valida
                S.anuncio_validar(r["id"], "t")
        # V20.24 (revisao): 408 (o ML desistiu de esperar) tambem e incerto; 429 = nao processado, recusa
        for falha, sit in ((erro_http(408, {"message": "Request Timeout"}), "incerto"),
                           (urllib.error.HTTPError("u", 408, "x", {}, None), "incerto"),
                           (erro_http(429, {"message": "too many"}), "erro")):
            r = self.validado()
            self.ml.itens = [falha]
            self.assertEqual(S.anuncio_publicar(r["id"], "t")["situacao"], sit, falha)
        # resposta 2xx sem id = incerto
        r = self.validado()
        self.ml.itens = [{"status": "active"}]
        self.assertEqual(S.anuncio_publicar(r["id"], "t")["situacao"], "incerto")
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM anuncio_canal_v2023").fetchone()[0], 0)

    def test_publicando_no_boot_vira_incerto(self):
        r = self.validado()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao='publicando' WHERE id=?", (r["id"],))
            S.DB.commit()
        S.ensure_anuncios_schema()
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["situacao"], "incerto")

    def test_falha_inesperada_nunca_fica_publicando(self):
        r = self.validado()
        with mock.patch.object(S, "anuncio_item_corpo", side_effect=KeyError("x")):
            with self.assertRaises(KeyError):
                S.anuncio_publicar(r["id"], "t")
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["situacao"], "validado")
        self.assertEqual(self.ml.posts_items(), [])

    def test_liberar_incerto_com_e_sem_mlb(self):
        r = self.validado()
        self.ml.itens = [urllib.error.URLError("x")]
        S.anuncio_publicar(r["id"], "t")
        with self.assertRaises(ValueError):
            S.anuncio_incerto_liberar(r["id"], "abc", "t")
        lib = S.anuncio_incerto_liberar(r["id"], "", "t")
        self.assertEqual(lib["situacao"], "validado")
        with self.assertRaises(ValueError):  # so incerto libera
            S.anuncio_incerto_liberar(r["id"], "", "t")
        self.ml.itens = [urllib.error.URLError("x")]
        S.anuncio_publicar(r["id"], "t")
        st, c = self.cliente.pedir("POST", "/api/anuncios/rascunho/liberar", {"id": r["id"], "mlb": "mlb 7770001"[4:]})
        self.assertEqual((st, c["situacao"], c["mlb"]), (200, "publicado", "MLB7770001"))
        linha = self.canal("MLB7770001")
        self.assertEqual((linha["controle"], linha["ficha_codigo"], linha["estoque"]), ("noromak", "P1", 2))
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM audit_log WHERE action='anuncios.liberar'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_apollo_sem_resposta_nao_publica(self):  # RF3
        r = self.validado()
        self.apollo = apollo(None)
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "validado")
        self.assertIn("Apollo não respondeu", p["erros"][0])
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["erros"], p["erros"])
        self.assertEqual((self.ml.posts_items(), self.fotos_subidas), ([], []))
        with mock.patch.object(S, "crm_stock_lookup", side_effect=S.CrmLimiteAtingido(1)):
            self.assertEqual(S.anuncio_publicar(r["id"], "t")["situacao"], "validado")
        self.apollo = apollo(0)
        p = S.anuncio_publicar(r["id"], "t")
        self.assertIn("Sem estoque", p["erros"][0])
        self.assertEqual(self.ml.posts_items(), [])

    def test_quantidade_acima_do_disponivel_recusada(self):
        r = self.validado(quantidade=5)
        self.apollo = apollo(3)
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "validado")
        self.assertIn("acima do que dá para montar", p["erros"][0])
        self.assertEqual(self.ml.posts_items(), [])
        self.apollo = apollo(5)
        self.assertEqual(S.anuncio_publicar(r["id"], "t")["situacao"], "publicado")

    def test_foto_falhou_nada_criado(self):
        r = self.validado()

        def falha(d, t):
            raise RuntimeError("Mercado Livre recusou a foto (HTTP 400): ruim")
        self.foto_falsa = falha
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "validado")
        self.assertIn("foto 1", p["erros"][0])
        self.assertEqual(self.ml.posts_items(), [])

    def test_title_recusado_tenta_family_name_uma_vez(self):
        r = self.validado()
        recusa = erro_http(400, {"message": "body.invalid_fields", "cause": [
            {"type": "error", "code": "body.invalid_fields", "message": "The fields [title] are invalid, use family_name"}]})
        self.ml.itens = [recusa, recusa]
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "erro")
        posts = self.ml.posts_items()
        self.assertEqual(len(posts), 2)
        self.assertNotIn("family_name", posts[0][2])
        self.assertNotIn("title", posts[1][2])
        self.assertEqual(posts[1][2]["family_name"], "Filtro de oleo")
        # recusa de outro tipo: nao repete
        r2 = self.validado()
        self.ml.itens = [erro_http(400, {"message": "x", "cause": [{"type": "error", "code": "item.price.invalid",
                                                                   "message": "Preco invalido"}]})]
        p = S.anuncio_publicar(r2["id"], "t")
        self.assertEqual((p["situacao"], p["erros"]), ("erro", ["Preco invalido (item.price.invalid)"]))
        self.assertEqual(len(self.ml.posts_items()), 3)
        # family_name aceito na 2a
        r3 = self.validado()
        self.ml.itens = [recusa, {"id": "MLB5550009"}]
        self.assertEqual(S.anuncio_publicar(r3["id"], "t")["situacao"], "publicado")
        # `erro` volta a publicar so depois de validar de novo
        with self.assertRaises(ValueError):
            S.anuncio_publicar(r2["id"], "t")
        self.assertEqual(S.anuncio_validar(r2["id"], "t")["situacao"], "validado")

    def test_descricao_falhou_continua_publicado(self):
        r = self.validado()
        self.ml.descricao = urllib.error.URLError("caiu")
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "publicado")
        self.assertTrue(p["avisos"] and "descrição" in p["avisos"][0])
        self.assertIsNotNone(self.canal("MLB5550001"))

    def test_amostras_gravadas(self):
        r = self.validado()
        S.anuncio_publicar(r["id"], "t")
        with S.DB_LOCK:
            tipos = {x[0] for x in S.DB.execute("SELECT tipo FROM anuncio_amostra_v2024")}
        self.assertTrue({"validate", "item_criar", "descricao"} <= tipos, tipos)

    def test_supervisor_403(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_2024p", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        r = self.validado()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2024p", SENHA, area="admin")
        for rota in ("/api/anuncios/rascunho/validar", "/api/anuncios/rascunho/publicar",
                     "/api/anuncios/rascunho/liberar"):
            self.assertEqual(sup.pedir("POST", rota, {"id": r["id"]})[0], 403, rota)
        self.assertEqual(self.ml.posts_items(), [])

    # ---- revisao final (V20.24) ----
    UP = {"message": "body.invalid_fields", "cause": [
        {"type": "error", "code": "body.invalid_fields",
         "message": "The fields [title] are invalid for user products, use family_name"}]}

    def test_validar_up_tenta_family_name_e_lembra(self):  # item 1
        r = self.rascunho()
        self.ml.validate = [erro_http(400, self.UP), None]
        v = S.anuncio_validar(r["id"], "t")
        self.assertEqual(v["situacao"], "validado", v)
        vals = [c for c in self.ml.chamadas if c[1] == "/items/validate"]
        self.assertEqual(len(vals), 2)
        self.assertIn("title", vals[0][2])
        self.assertNotIn("title", vals[1][2])
        self.assertEqual(vals[1][2]["family_name"], "Filtro de oleo")
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["usa_family_name"], 1)
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "publicado", p)
        posts = self.ml.posts_items()
        self.assertEqual(len(posts), 1)
        self.assertNotIn("title", posts[0][2])
        self.assertEqual(posts[0][2]["family_name"], "Filtro de oleo")
        # conferido com title: lembra 0
        r2 = self.rascunho()
        self.ml.validate = None
        S.anuncio_validar(r2["id"], "t")
        self.assertEqual(S.anuncio_rascunho_ler(r2["id"])["usa_family_name"], 0)

    def test_validar_up_falha_duas_vezes_mostra_os_dois(self):  # itens 1 e 5
        r = self.rascunho()
        outro = erro_http(400, {"cause": [{"type": "error", "code": "item.attribute.missing",
                                           "message": "Falta MODEL"}]})
        self.ml.validate = [erro_http(400, self.UP), outro]
        v = S.anuncio_validar(r["id"], "t")
        self.assertEqual(v["situacao"], "rascunho")
        self.assertEqual(len([c for c in self.ml.chamadas if c[1] == "/items/validate"]), 2)
        self.assertTrue(any("family_name" in e for e in v["erros"]), v["erros"])
        self.assertTrue(any("Falta MODEL" in e for e in v["erros"]), v["erros"])

    def test_recusa_de_titulo_comum_nao_repete(self):  # item 5
        r = self.rascunho()
        self.ml.validate = [erro_http(400, {"cause": [{"type": "error", "code": "item.title.length",
                                                       "references": ["item.title"], "message": "Title too long"}]})]
        v = S.anuncio_validar(r["id"], "t")
        self.assertEqual(v["situacao"], "rascunho")
        self.assertEqual(len([c for c in self.ml.chamadas if c[1] == "/items/validate"]), 1)
        r2 = self.validado()
        self.ml.itens = [erro_http(400, {"cause": [{"type": "error", "code": "item.title.length",
                                                    "references": ["item.title"], "message": "Title too long"}]})]
        p = S.anuncio_publicar(r2["id"], "t")
        self.assertEqual(p["situacao"], "erro")
        self.assertEqual(len(self.ml.posts_items()), 1)

    def test_publicar_family_name_falha_mostra_os_dois(self):  # item 5
        r = self.validado()
        outro = erro_http(400, {"cause": [{"type": "error", "code": "item.price.invalid", "message": "Preco invalido"}]})
        self.ml.itens = [erro_http(400, self.UP), outro]
        p = S.anuncio_publicar(r["id"], "t")
        self.assertEqual(p["situacao"], "erro")
        self.assertTrue(any("family_name" in e for e in p["erros"]), p["erros"])
        self.assertTrue(any("Preco invalido" in e for e in p["erros"]), p["erros"])

    def test_timeout_90s_no_post_items_e_descricao(self):  # item 4
        r = self.validado()
        S.anuncio_publicar(r["id"], "t")
        t = dict(self.ml.timeouts)
        self.assertEqual(t["/items"], 90)
        self.assertEqual(t["/items/MLB5550001/description"], 90)

    def test_publicar_le_so_a_filial_do_rascunho(self):  # item 8
        r = self.validado()
        lidas = []
        orig = S.apollo_disponivel_filial

        def ler(codigos, filial):
            lidas.append(filial)
            return orig(codigos, filial)
        with mock.patch.object(S, "apollo_disponivel_filial", ler):
            self.assertEqual(S.anuncio_publicar(r["id"], "t")["situacao"], "publicado")
        self.assertEqual(lidas, ["aracatuba"])

    def test_liberar_mlb_de_outro_produto_recusado(self):  # item 7
        S.ficha_salvar("P9", {"nome": "Outro"}, "t", None)
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,controle) "
                         "VALUES('ml','aracatuba','MLB8880001','P9','noromak')")
            S.DB.execute("INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,controle) "
                         "VALUES('ml','aracatuba','MLB8880002','P1','alephee')")
            S.DB.commit()
        r = self.validado()
        self.ml.itens = [urllib.error.URLError("x")]
        S.anuncio_publicar(r["id"], "t")
        for mlb in ("MLB8880001", "MLB8880002"):
            with self.assertRaises(ValueError):
                S.anuncio_incerto_liberar(r["id"], mlb, "t")
        self.assertEqual(S.anuncio_rascunho_ler(r["id"])["situacao"], "incerto")
        with S.DB_LOCK:
            row = S.DB.execute("SELECT ficha_codigo, controle FROM anuncio_canal_v2023 WHERE anuncio_id='MLB8880001'").fetchone()
        self.assertEqual((row[0], row[1]), ("P9", "noromak"))
        self.assertEqual(S.anuncio_incerto_liberar(r["id"], "MLB8880003", "t")["situacao"], "publicado")


class FotoUploadTeste(TesteNoromak):
    """_ml_foto_upload de verdade (urlopen falso): multipart com o campo `file`, 401 renova uma vez."""

    def test_multipart_e_401(self):
        pedidos = []

        class Resp:
            def __init__(self, texto):
                self.texto = texto

            def read(self):
                return self.texto.encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            pedidos.append(req)
            if len(pedidos) == 1:
                raise urllib.error.HTTPError(req.full_url, 401, "x", {}, None)
            return Resp('{"id": "123-MLA456"}')
        tokens = iter([("t1", "1"), ("t2", "1")])
        with mock.patch.object(S.urllib.request, "urlopen", urlopen), \
                mock.patch.object(S, "ml_access_token", side_effect=lambda **k: next(tokens)):
            fid = S._ml_foto_upload(b"DADOS", "image/png")
        self.assertEqual(fid, "123-MLA456")
        self.assertEqual(len(pedidos), 2)
        req = pedidos[1]
        self.assertTrue(req.full_url.endswith("/pictures/items/upload"))
        self.assertEqual(req.get_header("Authorization"), "Bearer t2")
        self.assertIn("multipart/form-data; boundary=", req.get_header("Content-type"))
        self.assertIn(b'name="file"; filename="foto.png"', req.data)
        self.assertIn(b"DADOS", req.data)

    def test_recusa_vira_runtimeerror(self):
        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "x", {}, None)
        with mock.patch.object(S.urllib.request, "urlopen", urlopen), \
                mock.patch.object(S, "ml_access_token", return_value=("t", "1")):
            with self.assertRaises(RuntimeError):
                S._ml_foto_upload(b"x", "image/jpeg")


def _png(l=600, a=600):
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", l, a, 8, 0, 0, 0, 0))
            + chunk(b"IEND", b""))
