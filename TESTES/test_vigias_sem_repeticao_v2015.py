"""V20.15 — a Expedição voltou a pesar na VPS (03/11/2026: 62% de média desde o boot, picos de 85%).

Medido por thread em produção: o vigia de etiquetas e o de pedidos novos do ML somavam ~22%.

- Dois pedidos de Bauru já postados na agência (envio ready_to_ship / dropped_off) ficaram
  na fila: o vigia de etiquetas reimportava cada um a cada ciclo, o ML recusava a etiqueta
  (NOT_PRINTABLE_STATUS) e o log ganhou 127 "NOME FISCAL ATUALIZADO" de cada em 3 h.
- O vigia de pedidos novos (20 s) relia o envio e baixava a etiqueta de cada pedido recente
  que já estava na fila esperando NF-e (~70 em 03/11) — o mesmo trabalho que o vigia de
  etiquetas já faz em rodízio de 15 s.
"""

import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE",
                 VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre", **extra)


class VigiaDePedidosNovosNaoRefazEtiqueta(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment,
                           S.ml_import_order, S._ml_fetch_shipping_zpl)
        S.ml_access_token = lambda *a, **k: ("token", "SELLER")
        S._ML_LOG_ULTIMO.clear()
        self.chamadas: list[tuple[str, str]] = []

        def api(path, *a, **k):
            if path.startswith("/orders/search"):
                return {"results": [{"id": oid, "date_created": S.now_iso(), "status": "paid"}
                                    for oid in ("2000000000001", "2000000000002")]}
            return {}
        S.ml_api_get = api

        def resolver(order_id, order=None, *a, **k):
            self.chamadas.append(("envio", str(order_id)))
            return "", None
        S._ml_resolve_forward_shipment = resolver

        def zpl(shipment_id):
            self.chamadas.append(("zpl", str(shipment_id)))
            return ""
        S._ml_fetch_shipping_zpl = zpl

        def importar(order_id, **kw):
            self.chamadas.append(("import", str(order_id)))
            return {}
        S.ml_import_order = importar

    def tearDown(self) -> None:
        (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment,
         S.ml_import_order, S._ml_fetch_shipping_zpl) = self._originais
        super().tearDown()

    def test_pedido_com_envio_na_fila_fica_com_o_vigia_de_etiquetas(self):
        gravar_como_integracao([
            # já veio da API (venda + envio), esperando NF-e: o vigia de etiquetas acompanha
            linha_ml("2000000000001", "SKU-A", ShipmentIdMercadoLivre="4400000000001"),
            # veio sem envio (extensão/manual): continua com o vigia de pedidos novos
            linha("2000000000002", "SKU-B", uid="m2", Marketplace="MERCADO LIVRE",
                  VendaMercadoLivre="2000000000002"),
        ], origem="API Mercado Livre")
        self.assertIn(("2000000000001", "4400000000001"), S._ml_all_pending_label_candidates())
        S.ml_new_order_watch_once()
        tocados = {oid for _, oid in self.chamadas}
        self.assertNotIn("2000000000001", tocados,
                         "REGRESSÃO: o vigia de 20 s voltou a reler envio/etiqueta de pedido que o vigia "
                         "de etiquetas já acompanha")
        self.assertIn(("envio", "2000000000002"), self.chamadas,
                      "o pedido sem envio na fila ficou sem ninguém procurando o envio")

    def test_pedido_novo_continua_sendo_importado(self):
        S.ml_new_order_watch_once()
        self.assertIn(("import", "2000000000001"), self.chamadas)
        self.assertIn(("import", "2000000000002"), self.chamadas)


class VigiaDeEtiquetasNaoInsisteEmPacotePostado(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_import_order, S._ml_fetch_shipping_zpl)
        S._ML_LOG_ULTIMO.clear()
        self.importacoes: list[str] = []
        self.substatus = "dropped_off"

        def api(path, *a, **k):
            if path.startswith("/shipments/"):
                return {"id": "4400000000001", "status": "ready_to_ship", "substatus": self.substatus}
            return {}
        S.ml_api_get = api
        S._ml_fetch_shipping_zpl = lambda sid: ""

        def importar(order_id, **kw):
            self.importacoes.append(str(order_id))
            return {"fiscal_name_updated": True, "fiscal_name": "Fulano"}
        S.ml_import_order = importar
        gravar_como_integracao([
            linha_ml("2000000000001", "SKU-A", ShipmentIdMercadoLivre="4400000000001"),
        ], origem="API Mercado Livre")

    def tearDown(self) -> None:
        S.ml_api_get, S.ml_import_order, S._ml_fetch_shipping_zpl = self._originais
        super().tearDown()

    def test_postado_na_agencia_nao_e_reimportado(self):
        for substatus in ("dropped_off", "picked_up", "in_hub"):
            self.substatus = substatus
            S.ml_pending_label_watch_once()
            S.ml_pending_label_watch_once()
        self.assertEqual(self.importacoes, [],
                         "REGRESSÃO: pacote já postado foi reimportado a cada ciclo (a etiqueta não imprime mais)")

    def test_aviso_de_postado_vai_uma_vez_para_o_log(self):
        for _ in range(3):
            S.ml_pending_label_watch_once()
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM ml_event_log WHERE topic='shipment_watcher' "
                             "AND result='JÁ POSTADO / ETIQUETA NÃO IMPRIME'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_pronto_para_imprimir_continua_reimportando(self):
        self.substatus = "ready_to_print"
        S.ml_pending_label_watch_once()
        self.assertEqual(self.importacoes, ["2000000000001"])


if __name__ == "__main__":
    unittest.main()
