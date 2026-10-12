"""V19.03 — Remessa Full automática. Apollo e ML simulados."""
import unittest
from base import S, TesteNoromak


class NaoAceito(TesteNoromak):
    def test_marcar_e_liberar(self):
        S.full_nao_aceito_marcar("0893600022", "inflamável", "admin")
        self.assertIn("0893600022", S.full_nao_aceito_lista())
        self.assertEqual(S.full_nao_aceito_lista()["0893600022"]["motivo"], "inflamável")
        self.assertTrue(S.full_nao_aceito_liberar("0893600022", "admin"))
        self.assertNotIn("0893600022", S.full_nao_aceito_lista())

    def test_codigo_normalizado(self):
        S.full_nao_aceito_marcar("  abc-1 ", "", "admin")
        self.assertIn("ABC-1", S.full_nao_aceito_lista())


if __name__ == "__main__":
    unittest.main()
