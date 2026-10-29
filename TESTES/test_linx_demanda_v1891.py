"""V18.91 — Demanda de Compra pela Linx Smart API (demanda de todos os canais e compra em aberto).

A Linx é simulada: nenhum teste aqui chama a API de verdade. O formato real nunca foi visto
(produção sem credenciais em 09/10): os testes prendem a leitura tolerante e, principalmente,
que dado que não veio NUNCA vira zero por cima da venda do e-commerce.
"""

import json
import unittest
from datetime import date, datetime

from base import S


def meses_fechados(n):
    hoje = datetime.now(S.BR_TZ).date().replace(day=1)
    ano, mes, out = hoje.year, hoje.month, []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        out.append((ano, mes))
    return out


class LeituraTolerante(unittest.TestCase):

    def test_mes_em_varios_formatos_e_total_pela_soma_dos_canais(self):
        resp = {"Dados": [
            {"Ano": 2026, "Mes": 9, "DemandaTotal": 10, "DemandaBalcao": 6, "DemandaOficina": 3, "DemandaTelemark": 1},
            {"MesAno": "08/2026", "DemandaBalcao": 3, "DemandaOficina": 2},
            {"periodo": "2026-07", "demandatotal": "4,5"},
            {"Competencia": "202606", "DemandaTotal": 7},
            {"Qualquer": "coisa"},
        ]}
        meses, ilegiveis = S._linx_demanda_meses(resp)
        self.assertEqual(meses["2026-09"]["total"], 10)
        self.assertEqual(meses["2026-08"]["total"], 5)
        self.assertEqual(meses["2026-07"]["total"], 4.5)
        self.assertEqual(meses["2026-06"]["total"], 7)
        self.assertEqual(ilegiveis, 1)

    def test_compra_em_aberto_e_o_que_falta_baixar(self):
        abertas = S._linx_compras_abertas([
            {"CodigoPedido": 77, "QuantidadePedida": 10, "QuantidadeBaixada": 4, "DataProvavelRecebimento": "2026-10-20T00:00:00"},
            {"CodigoPedido": 78, "QuantidadePedida": 5}])
        self.assertEqual([(a["pedido"], a["pedida"], a["baixada"], a["previsao"]) for a in abertas],
                         [("77", 10.0, 4.0, "2026-10-20"), ("78", 5.0, 0.0, "")])


# V20.00: a demanda passou a vir da porta do NoromakWeb e a rodada, a tela e a compra em aberto
# estão em test_linx_demanda_porta_v2000.py. Aqui fica a leitura tolerante, que continua em uso.


if __name__ == "__main__":
    unittest.main()
