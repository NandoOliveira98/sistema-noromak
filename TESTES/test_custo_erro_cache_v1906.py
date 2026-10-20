"""V19.06 — erro do custo no Apollo fica guardado: repetir o código não chama o Apollo de novo."""
import io, json, unittest, urllib.error
from base import S, TesteNoromak


class CustoErroCache(TesteNoromak):
    def setUp(self):
        super().setUp()
        self.chamadas = 0
        self._orig = S.urllib.request.urlopen
        self._chave = S.CHAVE_CRM
        S.CHAVE_CRM = "x"
        S.CRM_COST_ERRO_CACHE.clear()
        S.CRM_COST_CACHE.clear()
        S.CRM_RATE_CALLS.clear()

        def falso(req, timeout=None):
            self.chamadas += 1
            raise urllib.error.HTTPError(req.full_url, self.codigo_http, "x", {}, io.BytesIO(json.dumps(self.corpo).encode()))
        S.urllib.request.urlopen = falso

    def tearDown(self):
        S.urllib.request.urlopen = self._orig
        S.CHAVE_CRM = self._chave
        S.CRM_COST_ERRO_CACHE.clear()
        S.CRM_RATE_CALLS.clear()
        super().tearDown()

    def test_codigo_inexistente_vai_ao_apollo_uma_vez(self):
        self.codigo_http, self.corpo = 404, {"ok": False, "erro": "Peça não encontrada"}
        for _ in range(50):
            with self.assertRaises(RuntimeError):
                S.crm_cost_lookup("KIT", "op")
        self.assertEqual(self.chamadas, 1)

    def test_limite_guardado_pouco_tempo(self):
        self.codigo_http, self.corpo = 429, {}
        with self.assertRaises(RuntimeError):
            S.crm_cost_lookup("A/B", "op")
        # V19.62: quem segura o limite é o balde local (cheio pelo 429), não o cache de erro
        self.assertNotIn("A/B", S.CRM_COST_ERRO_CACHE)
        with self.assertRaises(RuntimeError):
            S.crm_cost_lookup("A/B", "op")
        self.assertEqual(self.chamadas, 1)

    def test_outro_codigo_continua_consultando(self):
        self.codigo_http, self.corpo = 404, {"ok": False, "erro": "x"}
        for c in ("A", "B"):
            with self.assertRaises(RuntimeError):
                S.crm_cost_lookup(c, "op")
        self.assertEqual(self.chamadas, 2)


if __name__ == "__main__":
    unittest.main()
