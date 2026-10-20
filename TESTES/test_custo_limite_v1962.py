"""V19.62 — consulta de custo não gasta o limite do Apollo com recusa nem devolve 502 no limite.

Relatório do NoromakWeb (09–16/10): 79% das chamadas ao /custo foram recusadas (422 código
vazio/> 30 caracteres e 429), e a nossa rota repassava tudo como 502.
"""
import io, json, unittest, urllib.error
from base import S, TesteNoromak


class CustoLimite(TesteNoromak):
    def setUp(self):
        super().setUp()
        self.chamadas = 0
        self._orig = S.urllib.request.urlopen
        self._chave = S.CHAVE_CRM
        S.CHAVE_CRM = "x"
        S.CRM_COST_ERRO_CACHE.clear()
        S.CRM_COST_CACHE.clear()
        S.CRM_RATE_CALLS.clear()
        self.codigo_http, self.corpo = 422, {}

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

    def test_codigo_longo_nao_chama_o_apollo(self):
        with self.assertRaises(ValueError):
            S.crm_cost_lookup("X" * 31, "op")
        self.assertEqual(self.chamadas, 0)
        self.assertEqual(S.CRM_RATE_CALLS, [])

    def test_recusa_422_vira_400_e_fica_guardada(self):
        for _ in range(5):
            with self.assertRaises(ValueError):
                S.crm_cost_lookup("A B C", "op")
        self.assertEqual(self.chamadas, 1)

    def test_balde_cheio_nao_chama_o_apollo(self):
        S.CRM_RATE_CALLS[:] = [S.time.monotonic()] * S.CRM_RATE_LIMIT_PER_MINUTE
        with self.assertRaises(S.CrmLimiteAtingido) as ctx:
            S.crm_cost_lookup("0893600022", "op")
        self.assertEqual(self.chamadas, 0)
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_429_do_apollo_enche_o_balde(self):
        self.codigo_http = 429
        with self.assertRaises(S.CrmLimiteAtingido):
            S.crm_cost_lookup("0893600022", "op")
        with self.assertRaises(S.CrmLimiteAtingido):
            S.crm_cost_lookup("0893764272", "op")
        self.assertEqual(self.chamadas, 1)

    def test_rota_devolve_429_com_espera(self):
        S.CRM_RATE_CALLS[:] = [S.time.monotonic()] * S.CRM_RATE_LIMIT_PER_MINUTE
        S.urllib.request.urlopen = self._orig  # o cliente de teste usa o mesmo urlopen
        status, data = self.cliente.pedir("GET", "/api/precificador/custo?codigo=0893600022")
        self.assertEqual(status, 429)
        self.assertGreater(data.get("retry_after") or 0, 0)


if __name__ == "__main__":
    unittest.main()
