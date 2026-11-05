"""V20.26 - Anuncios fase 3: controle de estoque dos anuncios do ML (Manual / Automatico)."""

import json
import time
import urllib.error
from unittest import mock

from base import S, TesteNoromak, Cliente, linha

SENHA = "SenhaTeste#2026"


def inserir(conta="aracatuba", anuncio_id="MLB1", ficha="P1", controle="noromak", logistica="", removido="",
            estoque=None, modo="manual", up=""):
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
            "logistica,removido_em,estoque,estoque_modo,user_product_id) VALUES('ml',?,?,?,?,?,?,?,?,?,?,?)",
            (conta, anuncio_id, ficha, ficha, controle, "T " + anuncio_id, logistica, removido, estoque, modo, up))
        S.DB.commit()


def canal(anuncio_id="MLB1", conta="aracatuba"):
    with S.DB_LOCK:
        r = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='ml' AND conta=? AND anuncio_id=?",
                         (conta, anuncio_id)).fetchone()
    return dict(r) if r else None


def erro_ml(status, corpo=""):
    e = RuntimeError(f"Mercado Livre respondeu HTTP {status}: {corpo}")
    e.ml_status, e.ml_corpo = status, corpo
    return e


class FalsoMLEstoque:
    """ML falso: GET /items (ml_api_get), PUT /items (_ml_promo_http) e o estoque do user-product
    (_ml_estoque_http). `nao_muda` = o PUT responde 200 sem mudar nada (conta com varios depositos)."""

    def __init__(self):
        self.itens = {}       # mlb -> dict(available_quantity, user_product_id, logistic_type)
        self.up = {}          # up -> {"locations": [...], "versao": int}
        self.chamadas = []
        self.nao_muda = set()
        self.conflito_up = 0  # quantos PUT do user-product respondem 409 antes de aceitar
        self.falha_get = False

    def item(self, mlb, qtd, up="", logistica="drop_off"):
        self.itens[mlb] = {"available_quantity": qtd, "user_product_id": up or None, "logistic_type": logistica}

    def get(self, caminho, **kw):
        self.chamadas.append(("GET", caminho, None, S.ml_casa_atual()))
        if self.falha_get:
            raise RuntimeError("Mercado Livre HTTP 500: fora")
        mlb = caminho.split("/items/", 1)[1].split("?", 1)[0]
        it = self.itens[mlb]
        return {"id": mlb, "available_quantity": it["available_quantity"], "user_product_id": it["user_product_id"],
                "shipping": {"logistic_type": it["logistic_type"]}, "status": "active"}

    def promo(self, metodo, caminho, payload=None, timeout=None):
        self.chamadas.append((metodo, caminho, payload, S.ml_casa_atual()))
        assert metodo == "PUT" and caminho.startswith("/items/"), caminho
        mlb = caminho.split("/items/", 1)[1]
        if mlb not in self.nao_muda:
            self.itens[mlb]["available_quantity"] = payload["available_quantity"]
        return {"id": mlb, "available_quantity": self.itens[mlb]["available_quantity"]}

    def up_http(self, metodo, caminho, payload=None, headers=None):
        self.chamadas.append((metodo, caminho, payload, S.ml_casa_atual(), dict(headers or {})))
        up = caminho.split("/user-products/", 1)[1].split("/", 1)[0]
        d = self.up[up]
        if metodo == "GET":
            return {"locations": d["locations"]}, {"x-version": str(d["versao"])}
        if self.conflito_up > 0:
            self.conflito_up -= 1
            d["versao"] += 1
            raise erro_ml(409, "version conflict")
        assert str((headers or {}).get("x-version")) == str(d["versao"]), headers
        for loc in d["locations"]:
            if loc["type"] == "selling_address":
                loc["quantity"] = payload["quantity"]
        d["versao"] += 1
        for mlb, it in self.itens.items():
            if it["user_product_id"] == up:
                it["available_quantity"] = sum(l["quantity"] for l in d["locations"])
        return {"ok": True}, {}

    def escritas(self):
        return [c for c in self.chamadas if c[0] == "PUT"]


class EstoqueBase(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.ml = FalsoMLEstoque()
        self.apollo = {}   # codigo -> {revenda: disponivel | None}
        self.apollo_chamadas = []

        def stock(codigo, operador="", wait_seconds=0):
            self.apollo_chamadas.append(codigo)
            d = self.apollo.get(codigo)
            if isinstance(d, Exception):
                raise d
            d = d or {}
            return {"ok": True, "estoques": [{"revenda": r, "disponivel": v, "respondeu": v is not None}
                                             for r, v in d.items()]}

        self._ps = [mock.patch.object(S, "ml_api_get", side_effect=self.ml.get),
                    mock.patch.object(S, "_ml_promo_http", side_effect=self.ml.promo),
                    mock.patch.object(S, "_ml_estoque_http", side_effect=self.ml.up_http),
                    mock.patch.object(S, "ml_access_token", return_value=("tok", "1")),
                    mock.patch.object(S, "ANUNCIO_ESTOQUE_RELEITURA_ESPERAS", (0, 0)),
                    mock.patch.object(S, "crm_stock_lookup", side_effect=stock)]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)
        S._ANUNCIO_APOLLO_CHAMADAS.clear()
        if hasattr(S, "_ANUNCIO_ESTOQUE_ERRO"):  # Task 4
            S._ANUNCIO_ESTOQUE_ERRO.clear()


class ModoERegistroTeste(EstoqueBase):

    def test_modo_padrao_manual(self):
        inserir()
        self.assertEqual(canal()["estoque_modo"], "manual")

    def test_automatico_so_no_noromak(self):
        inserir()
        r = S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "automatico", "luis")
        self.assertEqual(r["modo"], "automatico")
        self.assertEqual(canal()["estoque_modo"], "automatico")
        inserir(anuncio_id="MLB2", controle="alephee")
        inserir(anuncio_id="MLB3", controle="full", logistica="fulfillment")
        inserir(anuncio_id="MLB4", removido="2026-11-05")
        inserir(anuncio_id="MLB5", logistica="fulfillment")
        for mlb in ("MLB2", "MLB3", "MLB4", "MLB5", "MLB404"):
            with self.assertRaises(ValueError, msg=mlb):
                S.anuncio_estoque_modo_definir("ml", "aracatuba", mlb, "automatico", "luis")
        with self.assertRaises(ValueError):
            S.anuncio_estoque_modo_definir("ml", "aracatuba", "MLB1", "qualquer", "luis")

    def test_trocar_controle_volta_para_manual(self):
        inserir(modo="automatico")
        S.anuncio_controle_definir("ml", "aracatuba", "MLB1", "alephee", "luis", False)
        S.anuncio_controle_definir("ml", "aracatuba", "MLB1", "noromak", "luis", True)
        self.assertEqual(canal()["estoque_modo"], "manual")

    def test_config_padrao_simulacao(self):
        self.assertEqual(S.anuncio_estoque_config("aracatuba")["modo"], "simulacao")
        self.assertEqual(S.anuncio_estoque_config("bauru")["modo"], "simulacao")
        S.anuncio_estoque_config_definir("bauru", "lote", "luis")
        self.assertEqual(S.anuncio_estoque_config("bauru")["modo"], "lote")
        self.assertEqual(S.anuncio_estoque_config("aracatuba")["modo"], "simulacao")
        with self.assertRaises(ValueError):
            S.anuncio_estoque_config_definir("bauru", "ligado", "luis")

    def test_log_grava_e_lista(self):
        S._anuncio_estoque_registrar("ml", "aracatuba", "MLB1", "manual", 3, 5, "teste", "ok", "d", "luis")
        S._anuncio_estoque_registrar("ml", "aracatuba", "MLB2", "manual", 1, 0, "teste", "erro", "d", "luis")
        log = S.anuncio_estoque_log("ml", "aracatuba", "MLB1")
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["de"], log[0]["para"], log[0]["resultado"]), (3, 5, "ok"))
        self.assertEqual([l["anuncio_id"] for l in S.anuncio_estoque_log("ml", "aracatuba")], ["MLB2", "MLB1"])

    def test_log_guarda_so_os_ultimos(self):
        with mock.patch.object(S, "ANUNCIO_ESTOQUE_LOG_MAX", 3):
            for i in range(6):
                S._anuncio_estoque_registrar("ml", "aracatuba", "MLB1", "manual", i, i, "", "ok", "", "t")
        log = S.anuncio_estoque_log("ml", "aracatuba", "MLB1")
        self.assertEqual([l["de"] for l in log], [5, 4, 3])


class GravarMLTeste(EstoqueBase):

    def test_item_simples_put_e_releitura(self):
        inserir(estoque=3)
        self.ml.item("MLB1", 3)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 7, ator="luis", modo="manual", motivo="digitado")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["antes"], r["depois"], r["metodo"]), (3, 7, "items"))
        self.assertEqual(self.ml.escritas()[0][1:3], ("/items/MLB1", {"available_quantity": 7}))
        c = canal()
        self.assertEqual((c["estoque_enviado"], c["estoque"]), (7, 7))
        self.assertTrue(c["estoque_enviado_em"])
        log = S.anuncio_estoque_log("ml", "aracatuba", "MLB1")
        self.assertEqual((log[0]["de"], log[0]["para"], log[0]["resultado"], log[0]["ator"]), (3, 7, "ok", "luis"))

    def test_zero_digitado_pelo_admin_vai(self):
        inserir()
        self.ml.item("MLB1", 4)
        self.assertTrue(S.ml_estoque_gravar("aracatuba", "MLB1", 0, ator="luis", modo="manual", motivo="")["ok"])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 0)

    def test_quantidade_invalida_nao_chama(self):
        inserir()
        self.ml.item("MLB1", 4)
        for q in (-1, 1.5, "abc", "", None, True, 100000):
            with self.assertRaises(ValueError, msg=repr(q)):
                S.ml_estoque_gravar("aracatuba", "MLB1", q, ator="luis", modo="manual", motivo="")
        self.assertEqual(self.ml.chamadas, [])

    def test_ja_estava_nao_envia(self):
        inserir()
        self.ml.item("MLB1", 5)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 5, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"])
        self.assertEqual(self.ml.escritas(), [])
        self.assertEqual(canal()["estoque_enviado"], 5)

    def test_200_sem_mudar_e_erro(self):
        """RF3: o PUT responde 200 e o ML continua com o numero antigo: nunca marca ok."""
        inserir(estoque=3)
        self.ml.item("MLB1", 3)
        self.ml.nao_muda.add("MLB1")
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 9, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertIn("não mudou", r["erro"])
        self.assertIsNone(canal()["estoque_enviado"])
        self.assertEqual(S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["resultado"], "erro")

    def test_200_sem_mudar_tenta_user_product(self):
        """RF3: com user-product de um local selling_address, o 200 sem efeito tenta o caminho do estoque."""
        inserir()
        self.ml.item("MLB1", 3, up="UP1")
        self.ml.up["UP1"] = {"locations": [{"type": "selling_address", "quantity": 3}], "versao": 4}
        self.ml.nao_muda.add("MLB1")
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 9, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["metodo"], "items+user_product")
        put_up = [c for c in self.ml.escritas() if "/user-products/" in c[1]]
        self.assertEqual(put_up[0][1], "/user-products/UP1/stock/type/selling_address")
        self.assertEqual(put_up[0][4], {"x-version": "4"})

    def test_user_product_com_deposito_vai_pelo_stock(self):
        inserir()
        self.ml.item("MLB1", 3, up="UP1")
        self.ml.up["UP1"] = {"locations": [{"type": "selling_address", "quantity": 3},
                                           {"type": "seller_warehouse", "quantity": 0}], "versao": 2}
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 6, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["metodo"], "user_product")
        self.assertFalse([c for c in self.ml.escritas() if c[1].startswith("/items/")])

    def test_deposito_sem_selling_address_erro_claro(self):
        inserir()
        self.ml.item("MLB1", 3, up="UP1")
        self.ml.up["UP1"] = {"locations": [{"type": "seller_warehouse", "quantity": 3}], "versao": 2}
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 6, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertIn("depósito do vendedor", r["erro"])
        self.assertEqual(self.ml.escritas(), [])

    def test_409_rele_e_tenta_uma_vez(self):
        inserir()
        self.ml.item("MLB1", 3, up="UP1")
        self.ml.up["UP1"] = {"locations": [{"type": "selling_address", "quantity": 3},
                                           {"type": "store", "quantity": 0}], "versao": 1}
        self.ml.conflito_up = 1
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 6, ator="luis", modo="manual", motivo="")
        self.assertTrue(r["ok"], r)
        self.ml.item("MLB2", 3, up="UP2")
        inserir(anuncio_id="MLB2")
        self.ml.up["UP2"] = {"locations": [{"type": "selling_address", "quantity": 3},
                                           {"type": "store", "quantity": 0}], "versao": 1}
        self.ml.conflito_up = 2  # duas vezes seguidas: desiste
        r = S.ml_estoque_gravar("aracatuba", "MLB2", 6, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(self.ml.itens["MLB2"]["available_quantity"], 3)

    def test_full_recusado_sem_chamada(self):
        inserir(controle="full", logistica="fulfillment")
        inserir(anuncio_id="MLB2", logistica="fulfillment")
        inserir(anuncio_id="MLB3", controle="alephee")
        inserir(anuncio_id="MLB4", removido="2026-11-05")
        for mlb in ("MLB1", "MLB2", "MLB3", "MLB4"):
            with self.assertRaises(ValueError, msg=mlb):
                S.ml_estoque_gravar("aracatuba", mlb, 5, ator="luis", modo="manual", motivo="")
        self.assertEqual(self.ml.chamadas, [])

    def test_full_lido_ao_vivo_nao_envia(self):
        inserir()
        self.ml.item("MLB1", 3, logistica="fulfillment")
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 5, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(self.ml.escritas(), [])
        self.assertEqual(canal()["logistica"], "fulfillment")

    def test_conta_bauru_usa_a_casa_bauru(self):
        inserir(conta="bauru", anuncio_id="MLB9")
        self.ml.item("MLB9", 1)
        self.assertTrue(S.ml_estoque_gravar("bauru", "MLB9", 2, ator="luis", modo="manual", motivo="")["ok"])
        self.assertTrue(self.ml.chamadas)
        self.assertEqual({c[3] for c in self.ml.chamadas}, {"bauru"})

    def test_ml_sem_resposta_nada_enviado(self):
        inserir()
        self.ml.item("MLB1", 3)
        self.ml.falha_get = True
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 5, ator="luis", modo="manual", motivo="")
        self.assertFalse(r["ok"])
        self.assertEqual(self.ml.escritas(), [])

    def test_conferir_desiste(self):
        inserir()
        self.ml.item("MLB1", 3)
        r = S.ml_estoque_gravar("aracatuba", "MLB1", 5, ator="sistema", modo="automatico", motivo="",
                                conferir=lambda: (None, "virou Manual"))
        self.assertEqual(r["resultado"], "ignorado")
        self.assertEqual(self.ml.chamadas, [])

    def test_amostras(self):
        inserir()
        self.ml.item("MLB1", 3)
        S.ml_estoque_gravar("aracatuba", "MLB1", 5, ator="luis", modo="manual", motivo="")
        with S.DB_LOCK:
            tipos = {r[0] for r in S.DB.execute("SELECT tipo FROM anuncio_amostra_v2024").fetchall()}
        self.assertIn("estoque_put_item", tipos)


def fila(linhas):
    atual, _, _ = S.get_storage("conf_pedidos")
    S.save_storage("conf_pedidos", atual, json.dumps(linhas), True, actor="teste", actor_role="admin")


def kits(lista):
    atual, _, _ = S.get_storage("conf_kits")
    S.save_storage("conf_kits", atual, json.dumps(lista), False, actor="admin_teste", actor_role="admin",
                   allow_completed_reimport=True)


class CalculoTeste(EstoqueBase):

    def ler(self, codigo, ara=None, bau=None, filial="aracatuba"):
        self.apollo[codigo] = {1: ara, 3: bau}
        S.anuncios_apollo_ler(codigo, filial)

    def item(self, mlb="MLB1", conta="aracatuba"):
        return next(i for i in S.anuncios_estoque_calcular(conta) if i["anuncio_id"] == mlb)

    def test_peca(self):
        inserir(ficha="P1", modo="automatico", estoque=2)
        self.ler("P1", ara=10)
        it = self.item()
        self.assertEqual((it["calculado"], it["motivo"], it["muda"]), (10, "", True))
        self.assertEqual(it["pecas"][0]["disponivel"], 10.0)

    def test_so_automatico_noromak_nao_full(self):
        inserir(anuncio_id="MLB1", modo="manual")
        inserir(anuncio_id="MLB2", modo="automatico", controle="alephee")
        inserir(anuncio_id="MLB3", modo="automatico", logistica="fulfillment")
        inserir(anuncio_id="MLB4", modo="automatico", removido="x")
        inserir(anuncio_id="MLB5", modo="automatico")
        self.assertEqual([i["anuncio_id"] for i in S.anuncios_estoque_calcular("aracatuba")], ["MLB5"])

    def test_pendente_desconta(self):
        inserir(ficha="6007.003.315.00.8", modo="automatico")
        self.ler("6007.003.315.00.8", ara=10)
        fila([linha("PED1", "6007003315008", 3), linha("PED2", "6007.003.315.00.8", 2, Casa="bauru")])
        it = self.item()
        self.assertEqual(it["calculado"], 7)  # so a fila de Aracatuba desconta
        self.assertEqual(it["pecas"][0]["a_baixar"], 3)
        self.assertEqual(self.apollo_chamadas, ["6007.003.315.00.8"])  # o codigo como no cadastro

    def test_kit_pela_peca_que_da_menos(self):
        kits([{"id": "KIT_a", "sku_kit": "KITAB", "nome": "Kit", "ativo": True,
               "componentes": [{"sku": "A1", "quantidade": 2}, {"sku": "B1", "quantidade": 1}]}])
        inserir(ficha="KITAB", modo="automatico")
        self.ler("A1", ara=9)
        self.ler("B1", ara=10)
        fila([linha("PED1", "B1", 8)])
        self.assertEqual(self.item()["calculado"], 2)  # A1: 9//2=4; B1: (10-8)=2

    def test_kit_com_peca_sem_leitura_nao_envia(self):
        """RF5: uma peca sem leitura do Apollo = kit sem numero (nunca zero, nunca so pelas outras)."""
        kits([{"id": "KIT_a", "sku_kit": "KITAB", "nome": "Kit", "ativo": True,
               "componentes": [{"sku": "A1", "quantidade": 1}, {"sku": "B1", "quantidade": 1}]}])
        inserir(ficha="KITAB", modo="automatico")
        self.ler("A1", ara=9)
        it = self.item()
        self.assertIsNone(it["calculado"])
        self.assertIn("B1", it["motivo"])
        self.ler("B1", ara=None)  # Apollo respondeu sem a filial
        it = self.item()
        self.assertIsNone(it["calculado"])
        self.assertIn("não respondeu", it["motivo"])

    def test_kit_a_barra_b_e_kitn(self):
        inserir(anuncio_id="MLB1", ficha="AA1/BB1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="KIT3-AA1", modo="automatico")
        self.ler("AA1", ara=7)
        self.ler("BB1", ara=4)
        self.assertEqual(self.item("MLB1")["calculado"], 4)
        self.assertEqual(self.item("MLB2")["calculado"], 2)

    def test_kit_inativo_nao_envia(self):
        kits([{"id": "KIT_a", "sku_kit": "KITAB", "nome": "Kit", "ativo": False,
               "componentes": [{"sku": "A1", "quantidade": 1}, {"sku": "B1", "quantidade": 1}]}])
        inserir(ficha="KITAB", modo="automatico")
        self.ler("A1", ara=9)
        self.ler("B1", ara=9)
        self.assertIsNone(self.item()["calculado"])

    def test_apollo_sem_resposta(self):
        """RF2: Apollo sem resposta (erro ou limite) = nao envia, nunca zero."""
        inserir(ficha="P1", modo="automatico")
        self.apollo["P1"] = RuntimeError("Apollo fora")
        S.anuncios_apollo_ler("P1", "aracatuba")
        it = self.item()
        self.assertIsNone(it["calculado"])
        self.assertIn("não respondeu", it["motivo"])

    def test_leitura_antiga_nao_vale(self):
        inserir(ficha="P1", modo="automatico")
        self.ler("P1", ara=5)
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch-?", (S.ANUNCIO_APOLLO_VALIDADE_S + 5,))
            S.DB.commit()
        self.assertIsNone(self.item()["calculado"])

    def test_fila_nao_lida(self):
        inserir(ficha="P1", modo="automatico")
        self.ler("P1", ara=5)
        with mock.patch.object(S, "pendentes_por_filial", return_value={"aracatuba": None}):
            it = self.item()
        self.assertIsNone(it["calculado"])
        self.assertIn("fila", it["motivo"])

    def test_bauru_le_a_revenda_3(self):
        inserir(conta="bauru", anuncio_id="MLB9", ficha="P1", modo="automatico")
        self.ler("P1", ara=50, bau=4, filial="bauru")
        self.assertEqual(self.item("MLB9", "bauru")["calculado"], 4)

    def test_rodizio_3_por_minuto(self):
        for i in range(5):
            inserir(anuncio_id=f"MLB{i}", ficha=f"P{i}", modo="automatico")
            self.apollo[f"P{i}"] = {1: 5}
        lidos = [S.anuncios_apollo_ler_proximo("aracatuba") for _ in range(5)]
        self.assertEqual(lidos[3:], [None, None])
        self.assertEqual(len(set(lidos[:3])), 3)  # nunca lido primeiro: tres codigos diferentes
        self.assertEqual(len(self.apollo_chamadas), 3)

    def test_rodizio_prioridade(self):
        for cod in ("PA", "PB", "PC"):
            inserir(anuncio_id="MLB" + cod, ficha=cod, modo="automatico")
            self.apollo[cod] = {1: 50}
        self.apollo["PB"] = {1: 2}
        for cod in ("PA", "PB", "PC"):
            S.anuncios_apollo_ler(cod, "aracatuba")
        with S.DB_LOCK:  # PA lido ha 1 h (mais antigo); PB baixo lido ha 20 min; PC agora
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=? WHERE codigo='PA'", (int(time.time()) - 3600,))
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=? WHERE codigo='PB'", (int(time.time()) - 1200,))
            S.DB.commit()
        inserir(anuncio_id="MLBPN", ficha="PN", modo="automatico")  # nunca lido
        self.apollo["PN"] = {1: 9}
        self.assertEqual(S.anuncios_apollo_ler_proximo("aracatuba"), "PN")
        self.assertEqual(S.anuncios_apollo_ler_proximo("aracatuba"), "PB")
        self.assertEqual(S.anuncios_apollo_ler_proximo("aracatuba"), "PA")

    def test_rodizio_so_anuncios_em_automatico(self):
        inserir(ficha="P1", modo="manual")
        self.assertIsNone(S.anuncios_apollo_ler_proximo("aracatuba"))
        self.assertEqual(self.apollo_chamadas, [])

    def test_limite_do_apollo_nao_grava(self):
        inserir(ficha="P1", modo="automatico")
        self.apollo["P1"] = S.CrmLimiteAtingido(10)
        self.assertIsNone(S.anuncios_apollo_ler_proximo("aracatuba"))
        with S.DB_LOCK:
            self.assertIsNone(S.DB.execute("SELECT 1 FROM anuncio_apollo_v2026").fetchone())


if __name__ == "__main__":
    import unittest
    unittest.main()
