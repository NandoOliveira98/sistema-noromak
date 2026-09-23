"""V18.23 — promoções do Mercado Livre com a conta do Precificador.

O botão sobe preço de verdade num marketplace real. A trava do preço mínimo
precisa morar no SERVIDOR: a tela avisa, mas quem recusa é o backend. Estes
testes garantem que nenhum caminho aplica promoção no prejuízo, e que a conta
é a mesma do Precificador (preço − comissão − taxa − imposto − custo).
"""

import json
import unittest

from base import S, TesteNoromak, reiniciar_banco


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


class RotasRespondem(TesteNoromak):
    """V18.23: a rota GET foi parar dentro do do_POST e a tela recebia
    "Arquivo não encontrado". Um teste de rota pega isso na hora."""

    def test_a_get_promocoes_nao_cai_no_servidor_de_arquivos(self):
        status, corpo = self.cliente.pedir("GET", "/api/precificador/promocoes")
        # Sem token do ML o conteúdo falha, mas TEM de ser JSON desta rota —
        # nunca 404 de arquivo.
        self.assertNotEqual(status, 404, "a rota GET não está registrada no do_GET")
        self.assertIsInstance(corpo, dict)
        self.assertIn("ok", corpo)

    def test_b_post_aplicar_esta_registrada(self):
        status, corpo = self.cliente.pedir(
            "POST", "/api/precificador/promocoes/aplicar", {"mlb": "MLB1", "preco": 10})
        self.assertNotEqual(status, 404, "a rota POST não está registrada no do_POST")
        self.assertIsInstance(corpo, dict)
        self.assertIn("ok", corpo)


class SemMercadoLivreConectado(unittest.TestCase):
    """V18.25: sem ML conectado a listagem levantava KeyError('seller_id') e a
    tela mostrava "Não foi possível carregar: 'seller_id'". Tem de ser um erro
    que explique o que fazer."""

    def test_a_mensagem_clara(self):
        with self.assertRaises(RuntimeError) as c:
            S.ml_itens_do_vendedor(0, 10)
        msg = str(c.exception)
        self.assertIn("não está conectado", msg)
        self.assertNotIn("seller_id", msg)


class DataVoltaParaAPlanilha(unittest.TestCase):
    """V18.26: há DUAS fontes de data — a API do ML e a base da planilha que o
    monitor de promoções usa. Se aplicar a promoção não atualizar a planilha, o
    anúncio continua aparecendo como VENCIDO lá, e alguém corrige na mão."""

    def setUp(self):
        base = {"rows": [
            {"mlb": "1059999471", "date": "2026-09-18", "manualZero": False, "priceOk": False},
            {"mlb": "1050401594", "date": "2026-08-30", "manualZero": True, "priceOk": False},
        ], "deleted": [], "updatedAt": ""}
        atual, _, _ = S.get_storage(S.PROMO_DATAS_KEY)
        S.save_storage(S.PROMO_DATAS_KEY, atual, json.dumps(base), False,
                       actor="teste", actor_role="admin")

    def _linhas(self):
        raw, _, _ = S.get_storage(S.PROMO_DATAS_KEY)
        return {str(x.get("mlb")): x for x in json.loads(raw or "{}").get("rows", [])}

    def test_a_atualiza_a_data_do_anuncio(self):
        # aceita com e sem o prefixo MLB, e com data ISO completa
        self.assertTrue(S.promo_atualizar_data_planilha("MLB1059999471", "2026-10-23T00:00:00", "teste"))
        self.assertEqual(self._linhas()["1059999471"]["date"], "2026-10-23")

    def test_b_nao_mexe_nos_outros(self):
        S.promo_atualizar_data_planilha("1059999471", "2026-10-23", "teste")
        outro = self._linhas()["1050401594"]
        self.assertEqual(outro["date"], "2026-08-30")
        self.assertTrue(outro["manualZero"], "a marcação de estoque zerado foi perdida")

    def test_c_anuncio_novo_entra_na_base(self):
        self.assertTrue(S.promo_atualizar_data_planilha("MLB777888999", "2026-11-05", "teste"))
        self.assertEqual(self._linhas()["777888999"]["date"], "2026-11-05")

    def test_d_data_invalida_nao_grava(self):
        for ruim in ("", "amanhã", "05/11/2026", None):
            self.assertFalse(S.promo_atualizar_data_planilha("MLB1059999471", ruim, "teste"))
        self.assertEqual(self._linhas()["1059999471"]["date"], "2026-09-18")
