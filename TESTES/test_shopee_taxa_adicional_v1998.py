"""V19.98 — Taxa de serviço adicional da Shopee (3,5%) no Precificador e nas Promoções automáticas.

Prints do detalhe de renda da Shopee (dono, 28/10): além de 20% (comissão 18,6% + transação 1,4%)
e R$ 4,50 por unidade, a Shopee cobra uma "Taxa de serviço adicional" de 3,5% sobre o preço (já sem
o cupom da loja). 5 de 6 pedidos pagaram 3,5%; a Water Off (24,90) pagou 5% — decisão do dono:
3,5% fixo para todos. As três cópias da tabela (Precificador, servidor e gaveta das Promoções
automáticas) precisam ficar iguais.
"""

import re
import unittest
from pathlib import Path

from base import S

RAIZ = Path(__file__).resolve().parent.parent

# (base das taxas = preço − cupom da loja, unidades, total "Taxas e Encargos" do print)
PEDIDOS_REAIS = [
    (25.90, 1, 10.59),   # Higienizador 0893764272
    (31.91, 1, 12.01),   # Descarbonizante 0893100310
    (19.00, 1, 8.97),    # Válvula CAM1005
    (51.21, 1, 16.54),   # kit2-0893764272, 52,80 − cupom NORO3 1,59
    (73.91, 2, 26.37),   # 2 × 38,90 − cupom NORO5 3,89 (faixa pelo preço unitário)
]


class TaxaAdicionalShopee(unittest.TestCase):

    def test_pedidos_reais_batem_com_a_tabela(self):
        for base, qtd, total in PEDIDOS_REAIS:
            comissao, fixa = S.shopee_taxas_por_preco(base / qtd)
            # cada linha do print é arredondada à parte: até 2 centavos de diferença
            self.assertAlmostEqual(base * comissao + fixa * qtd, total, delta=0.02, msg=(base, qtd))

    def test_adicional_em_todas_as_faixas(self):
        self.assertEqual(S.SHOPEE_TAXA_ADICIONAL, 0.035)
        self.assertEqual(S.shopee_taxas_por_preco(79.99), (0.235, 4.5))
        self.assertEqual(S.shopee_taxas_por_preco(80.0), (0.175, 16.0))
        self.assertEqual(S.shopee_taxas_por_preco(150.0), (0.175, 20.0))
        self.assertEqual(S.shopee_taxas_por_preco(250.0), (0.175, 26.0))

    def test_tres_copias_da_tabela_iguais(self):
        servidor = [(a, b, round(c, 4), d) for a, b, c, d in S.SHOPEE_FAIXAS_TAXA]
        prec = (RAIZ / "APP" / "precificador.html").read_text(encoding="utf-8")
        adicional = float(re.search(r"var SHOPEE_TAXA_ADICIONAL = ([\d.]+);", prec).group(1))
        faixas_prec = [(float(a), float("inf") if b == "Infinity" else float(b), round(float(c) + adicional, 4), float(d))
                       for a, b, c, d in re.findall(
                           r"\{ min: ([\d.]+), max: (\w+|[\d.]+), commission: ([\d.]+) \+ SHOPEE_TAXA_ADICIONAL, fixed: ([\d.]+)",
                           prec)]
        self.assertEqual(faixas_prec, servidor)
        promo = (RAIZ / "APP" / "promocoes-auto.html").read_text(encoding="utf-8")
        linha = re.search(r"const FAIXAS=\[(.*?)\];\n", promo).group(1)
        faixas_promo = [(float(a), float("inf") if b == "Infinity" else float(b), round(float(c), 4), float(d))
                        for a, b, c, d in re.findall(r"\[([\d.]+),(\w+|[\d.]+),([\d.]+),([\d.]+)\]", linha)]
        self.assertEqual(faixas_promo, servidor)


if __name__ == "__main__":
    unittest.main()
