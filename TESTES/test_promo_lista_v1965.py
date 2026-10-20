"""V19.65 — lista das Promoções automáticas: um cálculo por vez.

Em produção (20/10, 14:20–14:42): 80 chamadas a /api/promocoes-auto/renovacao com média de
53 s (504 no nginx), o processo em 900 MB e a Conferência travada atrás. Várias abas pediam
a lista inteira ao mesmo tempo e cada uma recalculava tudo.
"""

import threading
import time

from base import S
from test_promo_renovacao import HOJE, Base


class ListaUmCalculo(Base):

    def setUp(self):
        super().setUp()
        self.marcar("MLB1")
        self.calculos = 0
        original = S._promo_renovacao_payload_calcular

        def contado(*a, **k):
            self.calculos += 1
            time.sleep(0.3)
            return original(*a, **k)
        S._promo_renovacao_payload_calcular = contado
        self.addCleanup(setattr, S, "_promo_renovacao_payload_calcular", original)

    def test_varias_abas_ao_mesmo_tempo_calculam_uma_vez(self):
        res = []
        ts = [threading.Thread(target=lambda: res.append(S.promo_renovacao_payload(hoje=HOJE))) for _ in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(self.calculos, 1)
        self.assertEqual(len({r["versao"] for r in res}), 1)
        self.assertIn("MLB1", {i["mlb"] for i in res[0]["itens"]})

    def test_mudanca_na_lista_recalcula(self):
        S.promo_renovacao_payload(hoje=HOJE)
        self.marcar("MLB2")
        p = S.promo_renovacao_payload(hoje=HOJE)
        self.assertEqual(self.calculos, 2)
        self.assertIn("MLB2", {i["mlb"] for i in p["itens"] if i.get("marcado")})

    def test_quem_chega_depois_recebe_calculo_novo(self):
        S.promo_renovacao_payload(hoje=HOJE)
        S.promo_renovacao_payload(hoje=HOJE)
        self.assertEqual(self.calculos, 2)

    def test_cada_aba_recebe_a_sua_copia(self):
        res = []
        ts = [threading.Thread(target=lambda: res.append(S.promo_renovacao_payload(hoje=HOJE))) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        res[0]["itens"].clear()
        self.assertTrue(res[1]["itens"])

    def test_erro_no_calculo_chega_a_quem_esperava(self):
        S._promo_renovacao_payload_calcular = lambda *a, **k: (time.sleep(0.3), 1 / 0)
        erros = []

        def pedir():
            try:
                S.promo_renovacao_payload(hoje=HOJE)
            except ZeroDivisionError:
                erros.append(1)
        ts = [threading.Thread(target=pedir) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(erros), 3)


class PrecificadorUmaVezPorVersao(Base):
    """O JSON do Precificador é decodificado uma vez por versão, e a mudança aparece na hora."""

    def gravar(self, produtos):
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, S.json_dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")

    def test_decodifica_uma_vez_e_ve_a_mudanca(self):
        self.gravar([{"sku": "A1"}])
        a = S.precificador_dados()
        self.assertIs(S.precificador_dados(), a)
        self.gravar([{"sku": "B2"}])
        self.assertEqual([p["sku"] for p in S.precificador_dados()["products"]], ["B2"])
