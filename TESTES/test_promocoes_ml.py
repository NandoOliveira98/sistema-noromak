"""V18.23 — promoções do Mercado Livre com a conta do Precificador.

O botão sobe preço de verdade num marketplace real. A trava do preço mínimo
precisa morar no SERVIDOR: a tela avisa, mas quem recusa é o backend. Estes
testes garantem que nenhum caminho aplica promoção no prejuízo, e que a conta
é a mesma do Precificador (preço − comissão − taxa − imposto − custo).
"""

import json
import unittest

from base import S, reiniciar_banco


def setUpModule():
    reiniciar_banco()


PROD = {
    "sku": "TESTE1", "produto": "Produto de teste", "curva": "A",
    "custo": 34.10, "imposto": 0.12, "comissao": 0.12, "taxa_fixa": 6.75, "margem_alvo": 0.15,
}


class ContaDaPromocao(unittest.TestCase):
    def test_a_conta_bate_com_o_precificador(self):
        c = S.promo_conta(PROD, 66.97)
        self.assertAlmostEqual(c["comissao"], 8.04, places=2)
        self.assertAlmostEqual(c["imposto"], 8.04, places=2)
        # o lucro sai das parcelas SEM arredondar (arredonda só no fim)
        esperado = 66.97 - 66.97 * 0.12 - 6.75 - 66.97 * 0.12 - 34.10
        self.assertAlmostEqual(c["lucro"], round(esperado, 2), places=2)
        self.assertAlmostEqual(c["margem"], 0.15, places=2)

    def test_b_minimo_zera_o_lucro(self):
        minimo = S.promo_preco_minimo(PROD)
        self.assertAlmostEqual(S.promo_conta(PROD, minimo)["lucro"], 0.0, places=2)

    def test_c_ideal_entrega_a_margem_alvo(self):
        ideal = S.promo_preco_ideal(PROD)
        self.assertAlmostEqual(S.promo_conta(PROD, ideal)["margem"], PROD["margem_alvo"], places=3)

    def test_d_sem_custo_nao_calcula(self):
        sem = dict(PROD, custo=0.0)
        self.assertIsNone(S.promo_preco_minimo(sem))
        self.assertIsNone(S.promo_preco_ideal(sem))


class TravaDoPrecoMinimo(unittest.TestCase):
    """A barreira não pode depender da tela."""

    def setUp(self):
        produtos = [{
            "sku": "TESTE1", "produto": "Produto de teste", "custo": 34.10, "imposto": 0.12, "curva": "A",
            "marketplaces": {"ml": {"taxaFixa": 6.75, "comissao": 0.12, "frete": 0, "margem": 0.15}},
        }]
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")
        vinc, _, _ = S.get_storage("ml_sku_mlb_links_v1")
        S.save_storage("ml_sku_mlb_links_v1", vinc,
                       json.dumps({"TESTE1": [{"mlb": "MLB999000111", "status": "Vinculado"}]}),
                       False, actor="teste", actor_role="admin")

    def test_a_preco_no_prejuizo_e_recusado(self):
        # mínimo fica ~R$ 53,75; qualquer coisa abaixo tem de ser barrada ANTES
        # de qualquer chamada ao Mercado Livre.
        with self.assertRaises(ValueError) as c:
            S.ml_promocao_aplicar("MLB999000111", 49.90, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")
        msg = str(c.exception)
        self.assertIn("abaixo do preço mínimo", msg)
        self.assertIn("prejuízo", msg)

    def test_b_sem_custo_no_precificador_recusa(self):
        with self.assertRaises(ValueError) as c:
            S.ml_promocao_aplicar("MLB000000000", 100.0, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")
        self.assertIn("Precificador", str(c.exception))

    def test_c_preco_invalido_recusa(self):
        for ruim in ("", "abc", 0, -5):
            with self.assertRaises(ValueError):
                S.ml_promocao_aplicar("MLB999000111", ruim, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")


if __name__ == "__main__":
    unittest.main()
