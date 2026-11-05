"""V20.24 - Anuncios: correcoes da revisao antes de subir.
Ficha que nao some quando o anuncio vem sem SKU do ML e HTTP 408 = incerto. (A ordem do kit esta em
test_anuncios_kit_fila_v2023.py; o 408 de ponta a ponta no Publicar, em test_anuncios_publicar_v2024.py.)"""

import urllib.error
from unittest import mock

from base import S, TesteNoromak


def item(sku="0893117732", titulo="Produto"):
    return {"titulo": titulo, "preco": 10.5, "sku_ml": sku, "status": "active", "sub_status": [], "estoque": 3,
            "categoria": "MLB1", "tipo": "gold_special", "user_product": "", "produto_catalogo": "",
            "logistica": "", "atributos": [], "seller_id": "111"}


def linha(mlb):
    with S.DB_LOCK:
        r = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='ml' AND anuncio_id=?", (mlb,)).fetchone()
    return dict(r) if r else None


class RevisaoAntesDeSubirTeste(TesteNoromak):

    # B2: anuncio sem SKU no ML nao apaga a ficha ja ligada (ex.: publicado pelo Noromak)
    def test_sku_vazio_no_ml_nao_apaga_a_ficha(self):
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]):
            for sku in ("0893117732", ""):
                with mock.patch.object(S, "ml_todos_os_anuncios", return_value=["MLB1"]), \
                        mock.patch.object(S, "ml_itens_detalhe", return_value={"MLB1": item(sku=sku, titulo="T" + sku)}):
                    r = S.anuncios_ml_puxar()
        l = linha("MLB1")
        self.assertEqual((l["ficha_codigo"], l["sku_anuncio"]), ("0893117732", "0893117732"))
        self.assertEqual(l["titulo"], "T")  # o resto atualiza
        self.assertEqual(r["contas"]["aracatuba"]["sem_ficha"], 0)

    # B3: HTTP 408 no POST /items = incerto (como 5xx); 429 continua recusa
    def test_408_vira_sem_resposta(self):
        for exc in (RuntimeError("Mercado Livre respondeu HTTP 408: timeout"),
                    urllib.error.HTTPError("u", 408, "x", {}, None)):
            with mock.patch.object(S, "_ml_promo_http", side_effect=exc):
                with self.assertRaises(S.AnuncioMLSemResposta):
                    S._anuncio_ml_chamar("POST", "/items", {})
        with mock.patch.object(S, "_ml_promo_http", side_effect=RuntimeError("Mercado Livre respondeu HTTP 429: x")):
            with self.assertRaises(S.AnuncioMLRecusou):
                S._anuncio_ml_chamar("POST", "/items", {})


if __name__ == "__main__":
    import unittest
    unittest.main()
