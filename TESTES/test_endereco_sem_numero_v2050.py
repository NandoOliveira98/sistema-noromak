"""V20.50 — endereço sem número vai ao Apollo com "SN".

Caso real 11/11 (ML 2000019472156870, Eva Josiane Xavier Vaz): o ML mandou "Br 101 km 26" com número "SN".
O Noromak mandava o número vazio; como o Apollo não tem campo de número (vai DENTRO do Endereço, contrato
do Guilherme), o cliente ficou com "BR 101 KM 26" e o crédito pelo Linx web voltou HTTP 412: "Após o endereço
deve conter uma vírgula seguida de um espaço para informar o número".
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import S, reiniciar_banco
from test_expedicao_apollo_v1930 import PEDIDO, PortaFalsa, _pedido_ml_com_envio


def _envio(numero):
    return {"receiver_address": {
        "street_name": "Br 101 km 26", "street_number": numero, "comment": "",
        "neighborhood": {"name": "Interior"}, "zip_code": "96225-000",
        "city": {"name": "São José do Norte"}, "state": {"id": "BR-RS", "name": "Rio Grande do Sul"}}}


class EnderecoSemNumero(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def _rodar(self, envio):
        _pedido_ml_com_envio(codigo="85147")
        porta = PortaFalsa()
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", return_value=envio):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis")
        self.assertTrue(r["ok"], r)
        return porta.corpo("cliente/atualizar")["endereco"]

    def test_ml_com_sn_vai_sn(self):
        end = self._rodar(_envio("SN"))
        self.assertEqual((end["rua"], end["numero"]), ("BR 101 KM 26", "SN"))

    def test_ml_sem_numero_ou_s_barra_n_ou_zero_vai_sn(self):
        for numero in ("", "S/N", "s/n", "0", None):
            with self.subTest(numero=numero):
                reiniciar_banco()
                self.assertEqual(self._rodar(_envio(numero))["numero"], "SN")

    def test_numero_de_verdade_nao_muda(self):
        self.assertEqual(self._rodar(_envio("915"))["numero"], "915")

    def test_texto_da_pagina_com_sn(self):
        porta = PortaFalsa()
        _pedido_ml_com_envio(codigo="85147")
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True):
            r = S.exp_cadastro_atualizar(PEDIDO, "luis", endereco_texto="Br 101 km 26, SN, São José do Norte, RS, 96225000")
        self.assertTrue(r["ok"], r)
        self.assertEqual(porta.corpo("cliente/atualizar")["endereco"]["numero"], "SN")


if __name__ == "__main__":
    unittest.main()
