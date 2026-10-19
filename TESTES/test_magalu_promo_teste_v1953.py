"""V19.53 — Magalu: teste de LEITURA para a futura promoção automática.

A documentação da API Promocional não mostra os corpos. Antes de construir, o botão lê de
verdade (promoções, SKUs de uma promoção, catálogo e preço) e guarda as respostas. Nada pode
ser criado nem alterado na Magalu: só GET. A Magalu é simulada aqui.
"""

from unittest import mock

from base import S, TesteNoromak


class MagaluPromoTeste(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self.chamadas = []

    def _api(self, respostas, falhar=()):
        def fake(path, params=None):
            self.chamadas.append(path)
            for pedaco in falhar:
                if pedaco in path:
                    raise RuntimeError("Magalu HTTP 403: sem permissão")
            for prefixo, resp in respostas:
                if path.startswith(prefixo):
                    return resp
            return {"results": []}
        return _Patches(fake)

    def test_so_le_e_guarda_as_respostas(self):
        respostas = [
            ("/seller/v1/promotions/P1/skus", {"results": [{"sku": "ABC", "status": "approved", "price": 90}]}),
            ("/seller/v1/promotions/P1", {"id": "P1", "type": {"id": "absolute_discount"}}),
            ("/seller/v1/promotions", {"results": [{"id": "P1", "type": {"id": "absolute_discount"}}]}),
            ("/seller/v1/portfolios/skus", {"results": [{"sku": "ABC"}, {"sku": "D/E"}]}),
            ("/seller/v1/portfolios/prices/", {"price": 100, "list_price": 120}),
        ]
        with self._api(respostas):
            r = S.magalu_promo_teste()
        self.assertTrue(all(e["ok"] for e in r["etapas"]), r)
        self.assertEqual(r["tipos_de_promocao"], ["absolute_discount"])
        self.assertIn("/seller/v1/portfolios/prices/D%2FE", self.chamadas, "SKU com barra vai codificado na URL")
        with S.DB_LOCK:
            tipos = {x["tipo"] for x in S.DB.execute("SELECT tipo FROM magalu_amostra_v1926").fetchall()}
        self.assertTrue({"promo_promocoes", "promo_promocao_skus", "promo_catalogo_skus", "promo_preco"} <= tipos)

    def test_uma_chamada_que_falha_nao_para_as_outras(self):
        respostas = [("/seller/v1/portfolios/skus", {"results": [{"sku": "ABC"}]}),
                     ("/seller/v1/portfolios/prices/", {"price": 100})]
        with self._api(respostas, falhar=("/seller/v1/promotions",)):
            r = S.magalu_promo_teste()
        por = {e["etapa"]: e for e in r["etapas"]}
        self.assertFalse(por["promocoes"]["ok"])
        self.assertIn("403", por["promocoes"]["erro"])
        self.assertTrue(por["catalogo_skus"]["ok"] and por["preco"]["ok"])

    def test_nao_usa_nenhuma_chamada_que_grava(self):
        """Só existe GET aqui: as funções de POST/PUT da Magalu nunca são chamadas."""
        gravou = []
        with self._api([("/seller/v1/promotions", {"results": [{"id": "P1"}]})]), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=lambda *a, **k: gravou.append(a)):
            S.magalu_promo_teste()
        self.assertEqual(gravou, [], "o teste não pode abrir conexão própria (POST/PUT)")

    def test_pede_as_promocoes_em_andamento_e_lista_vazia_conta_zero(self):
        """V19.54: sem filtro a Magalu só devolve as que começam nos próximos 30 dias."""
        params = {}
        def fake(path, p=None):
            if path == "/seller/v1/promotions":
                params.update(p or {})
                return {"results": [], "meta": {"page": {"count": 0}}}
            return {"results": []}
        with _Patches(fake):
            r = S.magalu_promo_teste()
        self.assertIn("start_date_gte", params)
        self.assertLess(params["start_date_gte"], S.datetime.now(S.timezone.utc).isoformat())
        self.assertEqual({e["etapa"]: e["itens"] for e in r["etapas"]}["promocoes"], 0)

    def test_le_primeiro_a_promocao_de_preco_do_seller(self):
        lidas = []
        promos = {"results": [{"id": "C1", "type": {"id": "percentage_discount"}, "origin": "channel"},
                              {"id": "S1", "type": {"id": "absolute_discount"}, "origin": "self_service"}]}
        def fake(path, p=None):
            lidas.append(path)
            return promos if path == "/seller/v1/promotions" else {"results": []}
        with _Patches(fake):
            S.magalu_promo_teste()
        detalhes = [x for x in lidas if x.startswith("/seller/v1/promotions/") and not x.endswith("/skus")]
        self.assertEqual(detalhes[0], "/seller/v1/promotions/S1")
        self.assertIn("/seller/v1/promotions/C1/skus", lidas)

    def test_rota_so_do_perfil_admin(self):
        respostas = [("/seller/v1/promotions", {"results": []})]
        with self._api(respostas):
            status, corpo = self.cliente.pedir("GET", "/api/integracoes/magalu/teste-promocoes")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["ok"])


class _Patches:
    """magalu_api_get falso + token presente (sem conexão real)."""

    def __init__(self, fake):
        self._p = [mock.patch.object(S, "magalu_api_get", side_effect=fake),
                   mock.patch.object(S, "magalu_access_token", return_value="t")]

    def __enter__(self):
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()
        return False
