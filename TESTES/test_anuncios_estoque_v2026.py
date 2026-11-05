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
                    mock.patch.object(S, "_ml_estoque_http", side_effect=self.ml.up_http, create=True),
                    mock.patch.object(S, "ml_access_token", return_value=("tok", "1")),
                    mock.patch.object(S, "ANUNCIO_ESTOQUE_RELEITURA_ESPERAS", (0, 0), create=True),
                    mock.patch.object(S, "crm_stock_lookup", side_effect=stock)]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)
        for nome in ("_ANUNCIO_APOLLO_CHAMADAS", "_ANUNCIO_ESTOQUE_ERRO"):
            if hasattr(S, nome):
                getattr(S, nome).clear()


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


if __name__ == "__main__":
    import unittest
    unittest.main()
