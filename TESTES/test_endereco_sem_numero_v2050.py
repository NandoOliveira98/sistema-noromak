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


class RevisaoV2050(unittest.TestCase):
    """revisar-antes-de-subir (11/11): número com espaço antes da letra e "sem número" escrito de outro jeito."""

    def test_numero_com_espaco_antes_da_letra_e_numero(self):
        # TikTok aceita "45 B" (_tiktok_api_endereco); o texto da página não reconhecia e virava complemento.
        e = S.exp_endereco_do_texto("Rua X, 45 B, Casa, Osasco, SP, 06045080")["endereco"]
        self.assertEqual((e["rua"], e["numero"], e["complemento"]), ("RUA X", "45 B", "CASA"))

    def test_sem_numero_escrito_de_outro_jeito_vira_sn(self):
        for n in ("S N", "S-N", "S.N.", "S/Nº", "s/n°", "SEM NUMERO", "Sem número", "SEM Nº", "0"):
            with self.subTest(n=n):
                self.assertEqual(S._exp_numero_ou_sn(n), "SN")
                e = S.exp_endereco_do_texto(f"Br 101 km 26, {n}, São José do Norte, RS, 96225000")["endereco"]
                self.assertEqual((e["rua"], e.get("complemento", "")), ("BR 101 KM 26", ""))
                self.assertEqual(S._exp_numero_ou_sn(e["numero"]), "SN")

    def test_numero_de_verdade_nao_vira_sn(self):
        for n in ("12A", "45 B", "1500", "SN 12", "KM 26", "S/N 40"):
            with self.subTest(n=n):
                self.assertEqual(S._exp_numero_ou_sn(n), n)


class EnvioDoPedido(unittest.TestCase):
    """Caso real 11/11 (ML 2000019449560734, envio 48479376661): "Cliente novo precisa do endereço inteiro: pedido
    sem número de envio do ML". O endereço só olhava a 1ª linha do pedido; sem o envio nela, o cadastro parava."""

    def setUp(self):
        reiniciar_banco()

    def test_envio_de_outra_linha_do_pedido(self):
        from base import gravar_como_admin, linha
        gravar_como_admin([linha(PEDIDO, "0893764272", Marketplace="MERCADO LIVRE"),
                           linha(PEDIDO, "0893480130", Marketplace="MERCADO LIVRE", ShipmentIdMercadoLivre="48479376661")])
        with mock.patch.object(S, "ml_api_get", return_value=_envio("480")) as ml:
            r = S.exp_endereco_entrega(PEDIDO)
        self.assertEqual(r["endereco"]["numero"], "480", r)
        ml.assert_called_once_with("/shipments/48479376661")

    def test_sem_envio_na_fila_le_a_venda_no_ml(self):
        from base import gravar_como_admin, linha
        gravar_como_admin([linha(PEDIDO, "0893480130", Marketplace="MERCADO LIVRE")])
        chamadas = []

        def ml_get(caminho, *a, **k):
            chamadas.append(caminho)
            if caminho.startswith("/orders/"):
                return {"id": int(PEDIDO), "shipping": {"id": 48479376661}}
            return _envio("480")

        with mock.patch.object(S, "ml_api_get", side_effect=ml_get):
            r = S.exp_endereco_entrega(PEDIDO)
        self.assertEqual(r["endereco"]["rua"], "BR 101 KM 26", r)
        self.assertEqual(chamadas, [f"/orders/{PEDIDO}", "/shipments/48479376661"])

    def test_venda_sem_envio_no_ml_continua_avisando(self):
        from base import gravar_como_admin, linha
        gravar_como_admin([linha(PEDIDO, "0893480130", Marketplace="MERCADO LIVRE")])
        with mock.patch.object(S, "ml_api_get", return_value={"id": int(PEDIDO), "shipping": {}}):
            r = S.exp_endereco_entrega(PEDIDO)
        self.assertIsNone(r["endereco"])
        self.assertIn("sem número de envio", r["falta"])


class TipoDeRuaRecusado(unittest.TestCase):
    """Caso real 11/11 (ML 2000015965127073, "Condomínio Algodoal 8B"): o Apollo lê a 1ª palavra como tipo de rua e
    recusou "o tipo de rua "Condomínio" não está cadastrado no Apollo". Decisão do dono: repetir uma vez com "RUA" na frente."""

    RECUSA = 'o tipo de rua "Condomínio" não está cadastrado no Apollo.'

    def _porta(self, recusas):
        chamadas = []

        def porta(rota, corpo, escrita=False):
            chamadas.append(dict(corpo["endereco"]))
            if recusas:
                return {"ok": False, "erro": recusas.pop(0)}
            return {"ok": True, "situacao": "atualizado"}
        return porta, chamadas

    def test_repete_uma_vez_com_rua_e_avisa(self):
        porta, chamadas = self._porta([self.RECUSA])
        avisos = []
        corpo = {"endereco": {"rua": "CONDOMINIO ALGODOAL 8B", "numero": "", "bairro": "PARQUE VERDE", "cep": "67208142",
                              "cidade": "MARITUBA", "uf": "PA"}}
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S._exp_porta_endereco("cliente/atualizar", corpo, avisos)
        self.assertTrue(r["ok"], r)
        self.assertEqual([c["rua"] for c in chamadas], ["CONDOMINIO ALGODOAL 8B", "RUA CONDOMINIO ALGODOAL 8B"])
        self.assertTrue(any("RUA CONDOMINIO" in a for a in avisos), avisos)

    def test_recusa_tambem_com_rua_nao_repete_de_novo(self):
        porta, chamadas = self._porta([self.RECUSA, 'o tipo de rua "Rua" não está cadastrado no Apollo.'])
        corpo = {"endereco": {"rua": "CONDOMINIO X", "numero": "1", "bairro": "B", "cep": "67208142", "cidade": "C", "uf": "PA"}}
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S._exp_porta_endereco("cliente/atualizar", corpo, [])
        self.assertIn("tipo de rua", r["erro"])
        self.assertEqual(len(chamadas), 2)

    def test_cidade_e_tipo_de_rua_na_mesma_vez(self):
        porta, chamadas = self._porta(["O CEP é de MARITUBA, não de BENEVIDES. Nada foi enviado.", self.RECUSA])
        corpo = {"endereco": {"rua": "CONDOMINIO X", "numero": "1", "bairro": "B", "cep": "67208142", "cidade": "BENEVIDES",
                              "uf": "PA"}}
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S._exp_porta_endereco("cliente/atualizar", corpo, [])
        self.assertTrue(r["ok"], r)
        self.assertEqual((chamadas[-1]["rua"], chamadas[-1]["cidade"]), ("RUA CONDOMINIO X", "MARITUBA"))


if __name__ == "__main__":
    unittest.main()
