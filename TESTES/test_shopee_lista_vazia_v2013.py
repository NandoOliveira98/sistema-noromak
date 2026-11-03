"""V20.13 — lista de envio da Shopee vazia não é "já organizado" (caso real 03/11).

Desde 03/11 07:41 a `get_shipment_list` volta vazia (sem erro) mesmo com pedidos
READY_TO_SHIP e NF-e válida (26103128P67KJW, 2610312J8G1YHA...: pacote LOGISTICS_READY).
O vigia tratava todo pendente sem etiqueta fora da lista como "já organizado", pulava o
ship_order e ficava pedindo rastreio para sempre; e só olhava os 40 mais recentes, então os
de 31/10 nem entravam. Agora a situação real do pacote (get_order_detail) decide.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from base import S, TesteNoromak, gravar_como_integracao, linha


def _detalhe(sn, status="READY_TO_SHIP", logistica="LOGISTICS_READY", pacote=None):
    return {"order_sn": sn, "order_status": status,
            "package_list": [{"package_number": pacote or f"OFG{sn}", "logistics_status": logistica}]}


class ListaDeEnvioVazia(TesteNoromak):
    def setUp(self):
        super().setUp()
        hoje = time.strftime("%Y-%m-%d")
        gravar_como_integracao([linha(f"SHP{i:03d}", "SKU", uid=f"U{i}", Marketplace="SHOPEE",
                                      **{"Data do Pedido": hoje}) for i in range(60)])

    def _candidatos(self, detalhes):
        with mock.patch.object(S, "shopee_order_details", side_effect=lambda sns, campos="": detalhes(sns)):
            return S.shopee_label_candidatos([])

    def test_pacote_nao_organizado_vai_pelo_ship_order(self):
        lista, ja = self._candidatos(lambda sns: [_detalhe(sn) for sn in sns])
        self.assertEqual(len(lista), 60, "pendentes além dos 40 mais recentes ficaram de fora")
        self.assertEqual(ja, set(), "pacote LOGISTICS_READY tratado como já organizado")
        self.assertEqual(lista[0]["package_number"], "OFG" + lista[0]["order_sn"])

    def test_pacote_ja_organizado_vai_direto_ao_rastreio(self):
        lista, ja = self._candidatos(lambda sns: [_detalhe(sn, logistica="LOGISTICS_REQUEST_CREATED") for sn in sns])
        self.assertEqual(len(ja), 60)

    def test_reenviar_tambem_vai_pelo_ship_order(self):
        lista, ja = self._candidatos(lambda sns: [_detalhe(sn, status="RETRY_SHIP") for sn in sns])
        self.assertEqual(ja, set())

    def test_sem_detalhe_da_shopee_fica_como_antes(self):
        def falha(sns):
            raise RuntimeError("Falha de rede na Shopee API")
        lista, ja = self._candidatos(falha)
        self.assertEqual(len(ja), 60)
        self.assertEqual(len(lista), 60)

    def test_pedido_que_esta_na_lista_nao_e_consultado(self):
        consultados = []

        def detalhes(sns, campos=""):
            self.assertEqual(campos, "package_list")
            consultados.extend(sns)
            return [_detalhe(sn) for sn in sns]
        with mock.patch.object(S, "shopee_order_details", side_effect=detalhes):
            lista, ja = S.shopee_label_candidatos([{"order_sn": "SHP000", "package_number": "P0"}])
        self.assertNotIn("SHP000", consultados)
        self.assertEqual(sum(1 for x in lista if x["order_sn"] == "SHP000"), 1)

    def test_ship_order_e_chamado_e_falta_de_nfe_aparece(self):
        eventos, posts = [], []

        def post(path, body=None):
            posts.append(path)
            if path.endswith("get_shipping_document_parameter"):
                return {"response": {"result_list": []}}
            if path.endswith("ship_order"):
                raise RuntimeError("Shopee API: logistics.lack_of_invoice_data Please upload the invoice")
            raise AssertionError(path)
        with mock.patch.object(S, "shopee_order_details", side_effect=lambda sns, campos="": [_detalhe(sn) for sn in sns]), \
                mock.patch.object(S, "shopee_api_post", side_effect=post), \
                mock.patch.object(S, "shopee_api_get", side_effect=lambda p, q=None: {"response": {"dropoff": {}}}), \
                mock.patch.object(S, "shopee_log_event", side_effect=lambda *a, **k: eventos.append(a)):
            lista, ja = S.shopee_label_candidatos([])
            S.shopee_sync_shipping_documents(lista[:1], ja_organizados=ja)
        self.assertIn("/api/v2/logistics/ship_order", posts)
        self.assertTrue(any("aguardando NF-e" in str(e) for e in eventos))


if __name__ == "__main__":
    unittest.main()
