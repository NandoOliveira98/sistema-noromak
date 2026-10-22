"""V19.75 — desconto próprio com o prazo vencido conta como encerrado.

MLB3705527286 (22/10): às 09:42 o ML ainda devolvia o PRICE_DISCOUNT como "started" com fim
em 21/10 23:59:59; a linha ficou "Ativa — renova quando acabar" e nunca renovou.
"""

import unittest
from datetime import date, datetime

from base import S, TesteNoromak

PROD = {"sku": "SKU1", "produto": "Peça", "curva": "A", "custo": 30.0, "imposto": 0.12,
        "comissao": 0.13, "taxa_fixa": 0.0, "margem_alvo": 0.15}
CFG = {"estoque_min_dias": 0, "duracao_dias": 30, "margem_min_promo": 0.10}
DET = {"status": "active", "preco": 98.0, "estoque": 50}
AGORA = datetime(2026, 10, 22, 9, 42, tzinfo=S.BR_TZ)


def promo(status, fim):
    return [{"type": S.ML_PROMO_TIPO, "status": status, "price": 72.9, "finish_date": fim}]


class PromoVencida(TesteNoromak):
    def avaliar(self, promos):
        return S._promo_renovacao_avaliar("MLB1", 72.9, promos, DET, PROD, None, CFG,
                                          date(2026, 10, 22), agora=AGORA)

    def test_started_com_fim_no_passado_renova(self):
        out = self.avaliar(promo("started", "2026-10-21T23:59:59"))
        self.assertNotEqual(out["situacao"], "ativa")
        self.assertEqual(out["situacao"], "pronta", out)

    def test_started_dentro_do_prazo_continua_ativa(self):
        out = self.avaliar(promo("started", "2026-10-25T23:59:59"))
        self.assertEqual(out["situacao"], "ativa")

    def test_sem_data_continua_ativa(self):
        out = self.avaliar(promo("started", ""))
        self.assertEqual(out["situacao"], "ativa")


if __name__ == "__main__":
    unittest.main()
