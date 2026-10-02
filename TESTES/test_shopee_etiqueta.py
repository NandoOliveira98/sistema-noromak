"""V17.89 — etiquetas Shopee pararam de ser geradas desde 10/09/2026.

O worker de etiquetas chama ship_order com package_number. Para pedido UNSPLIT
a Shopee recusa e o sistema deveria refazer SEM o pacote — mas o detector do
erro não reconhecia a mensagem real: a Shopee escreve "pacakge" (com erro de
digitação) no código e usa apóstrofo tipográfico em "don’t". Resultado: 1.064
erros só em 18/09 e nenhuma etiqueta Shopee subiu sozinha.
"""

import unittest

from base import S

# Texto exato gravado em shopee_event_log em 18/09/2026.
MENSAGEM_REAL = (
    "Shopee API: logistics.ship_order_not_need_pacakge_number "
    "Please don’t request with package_number for this unsplit order."
)


class DetectorPacoteDesnecessario(unittest.TestCase):

    def test_reconhece_a_mensagem_real_da_shopee(self):
        self.assertTrue(S._shopee_package_not_needed_error(MENSAGEM_REAL),
                        "REGRESSÃO: o fallback sem package_number não é acionado e a etiqueta nunca sai")

    def test_reconhece_a_grafia_correta_tambem(self):
        self.assertTrue(S._shopee_package_not_needed_error(
            "logistics.ship_order_not_need_package_number"))

    def test_reconhece_so_pelo_texto_com_apostrofo_simples(self):
        self.assertTrue(S._shopee_package_not_needed_error(
            "Please don't request with package_number for this unsplit order."))

    def test_nao_confunde_com_outros_erros(self):
        for outro in ("logistics.package_can_not_print", "error_param: package_number is invalid",
                      "already arranged", ""):
            self.assertFalse(S._shopee_package_not_needed_error(outro), outro)



class CacheDoOrganizarEnvio(unittest.TestCase):
    """V17.90 — "Organizar envio" só fica em cache quando o rastreio sai.

    Em 18/09/2026 a Shopee recusou o ship_order porque a NF-e ainda não tinha sido
    enviada, o sistema registrou "já organizado" e guardou isso por 2 h: a
    etiqueta só sairia 2 h depois de a NF-e chegar.
    """

    def test_esquecer_libera_nova_tentativa(self):
        S._shopee_mark_ship_order_cached("260918BHYEGN3E", "OFG1", unsplit=True)
        self.assertEqual(S._shopee_ship_order_cached("260918BHYEGN3E", "OFG1"), (True, True))
        S._shopee_forget_ship_order("260918BHYEGN3E", "OFG1")
        self.assertEqual(S._shopee_ship_order_cached("260918BHYEGN3E", "OFG1"), (False, False))

    def test_esquecer_nao_mexe_em_outro_pedido(self):
        S._shopee_mark_ship_order_cached("OUTRO", "P2")
        S._shopee_forget_ship_order("260918BHYEGN3E", "OFG1")
        self.assertEqual(S._shopee_ship_order_cached("OUTRO", "P2")[0], True)
        S._shopee_forget_ship_order("OUTRO", "P2")


if __name__ == "__main__":
    unittest.main()


class EtiquetaDePedidoJaOrganizado(__import__("base").TesteNoromak):
    """V17.91 — pedido organizado sai de get_shipment_list e a etiqueta nunca
    era buscada. Agora ele entra pela lista de pendentes sem etiqueta."""

    def setUp(self):
        super().setUp()
        import time as _t
        from base import gravar_como_integracao, linha
        gravar_como_integracao([linha(
            "260918BHYEGN3E", "0893573150", uid="S1", Marketplace="SHOPEE",
            PacoteShopee="OFG243397266133082", **{"Data do Pedido": _t.strftime("%Y-%m-%d")})])
        self.originais = (S.shopee_api_get, S.shopee_api_post, S.shopee_api_post_bytes, S.shopee_log_event)
        self.chamadas = []
        self.eventos = []
        S.shopee_log_event = lambda *a, **k: self.eventos.append(a)

    def tearDown(self):
        S.shopee_api_get, S.shopee_api_post, S.shopee_api_post_bytes, S.shopee_log_event = self.originais
        super().tearDown()

    def fingir_shopee(self, arquivo=b"^XA^FO40,40^FDSHOPEE^FS^XZ", tipo="text/plain"):
        def get(path, params=None):
            self.chamadas.append(("GET", path, dict(params or {})))
            if path.endswith("get_tracking_number"):
                return {"response": {"tracking_number": "BR2609183391"}}
            if path.endswith("get_shipping_parameter"):
                raise AssertionError("pedido já organizado não pode voltar a organizar envio")
            return {"response": {}}

        def post(path, body=None):
            self.chamadas.append(("POST", path, body))
            if path.endswith("get_shipping_document_parameter"):
                return {"response": {"result_list": [{"order_sn": "260918BHYEGN3E",
                        "package_number": "OFG243397266133082", "suggest_shipping_document_type": "THERMAL_AIR_WAYBILL"}]}}
            if path.endswith("ship_order"):
                raise AssertionError("pedido já organizado não pode voltar a organizar envio")
            if path.endswith("create_shipping_document"):
                return {"response": {"result_list": [{"order_sn": "260918BHYEGN3E"}]}}
            if path.endswith("get_shipping_document_result"):
                return {"response": {"result_list": [{"order_sn": "260918BHYEGN3E", "status": "READY"}]}}
            return {"response": {}}

        S.shopee_api_get = get
        S.shopee_api_post = post
        S.shopee_api_post_bytes = lambda path, body=None: (arquivo, tipo)

    def test_lista_de_pendentes_sem_etiqueta(self):
        self.assertEqual(S.shopee_pedidos_sem_etiqueta(),
                         [{"order_sn": "260918BHYEGN3E", "package_number": "OFG243397266133082"}])

    def test_pedido_organizado_chega_a_conferencia(self):
        self.fingir_shopee()
        organizados = S.shopee_pedidos_sem_etiqueta()
        r = S.shopee_sync_shipping_documents(organizados, ja_organizados={x["order_sn"] for x in organizados})
        self.assertEqual((r["tracking_ok"], r["baixados"], r["vinculados"]), (1, 1, 1), r)
        self.assertEqual(S.shopee_pedidos_sem_etiqueta(), [], "a etiqueta não ficou vinculada ao pedido")

    def test_etiqueta_em_pdf_e_baixada_mas_nao_vinculada(self):
        """Sem ZPL a Zebra não imprime: o sistema avisa em vez de vincular lixo."""
        self.fingir_shopee(arquivo=b"%PDF-1.4 ...", tipo="application/pdf")
        organizados = S.shopee_pedidos_sem_etiqueta()
        r = S.shopee_sync_shipping_documents(organizados, ja_organizados={x["order_sn"] for x in organizados})
        self.assertEqual((r["baixados"], r["vinculados"], r["nao_zpl"]), (1, 0, 1), r)

    def test_falta_de_nfe_fica_pendente_e_nao_vira_ja_organizado(self):
        def post(path, body=None):
            if path.endswith("get_shipping_document_parameter"):
                return {"response": {"result_list": [{"order_sn": "260918BHYEGN3E", "package_number": "P1"}]}}
            if path.endswith("ship_order"):
                if body.get("package_number"):
                    raise RuntimeError("Shopee API: logistics.ship_order_not_need_pacakge_number Please don’t request with package_number")
                raise RuntimeError("Shopee API: logistics.lack_of_invoice_data Please upload the invoice or verify the details of the document already submitted")
            raise AssertionError("não devia passar do ship_order: " + path)
        S.shopee_api_post = post
        S.shopee_api_get = lambda path, params=None: {"response": {"dropoff": {}}, "info_needed": {}}
        S._shopee_forget_ship_order("260918BHYEGN3E", "P1")
        r = S.shopee_sync_shipping_documents([{"order_sn": "260918BHYEGN3E", "package_number": "P1"}])
        self.assertEqual(r["ship_order_ok"], 0, "falta de NF-e foi contada como envio organizado")
        self.assertEqual(S._shopee_ship_order_cached("260918BHYEGN3E", "P1"), (False, False))
        self.assertTrue(any("aguardando NF-e" in str(e) for e in self.eventos))


class SincronizacaoNaoApagaPedidoOrganizado(__import__("base").TesteNoromak):
    """V17.92 — a importação de pedidos Shopee passa a rodar sozinha (worker).
    Garante que isso não tira da fila o pedido já organizado."""

    def setUp(self):
        super().setUp()
        from base import gravar_como_integracao, linha
        gravar_como_integracao([
            linha(p, "SKU-" + p, uid="U-" + p, Marketplace="SHOPEE", OrigemCadastro="API_SHOPEE")
            for p in ("ORGANIZADO01", "CANCELADO001", "AORGANIZAR01")
        ])
        self.originais = (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
                          S._shopee_build_pending_lines, S.shopee_log_event)
        S.shopee_status_payload = lambda: {"connected": True, "shop_id": "1"}
        S.shopee_current_shipments = lambda: [{"order_sn": "AORGANIZAR01", "package_number": "P1"}]
        status = {"ORGANIZADO01": "PROCESSED", "CANCELADO001": "CANCELLED", "AORGANIZAR01": "READY_TO_SHIP"}
        S.shopee_order_details = lambda sns: [{"order_sn": x, "order_status": status[x]} for x in sns]
        S._shopee_build_pending_lines = lambda order, pkgs, produtos: [
            linha(order["order_sn"], "SKU-" + order["order_sn"], uid="U-" + order["order_sn"],
                  Marketplace="SHOPEE", OrigemCadastro="API_SHOPEE")]
        S.shopee_log_event = lambda *a, **k: None

    def tearDown(self):
        (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
         S._shopee_build_pending_lines, S.shopee_log_event) = self.originais
        super().tearDown()

    def test_pedido_organizado_nao_sai_da_fila(self):
        """Rodando a cada minuto, a sincronização não pode tirar da fila o
        pedido organizado (que saiu de get_shipment_list) antes da conferência.
        Quem garante é preserve_pending_records na gravação da integração."""
        from base import numeros_de_pedido
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        fila = numeros_de_pedido()
        self.assertIn("ORGANIZADO01", fila, "REGRESSÃO: pedido organizado sumiu da fila antes da conferência")
        self.assertIn("AORGANIZAR01", fila)

    def test_worker_automatico_chama_a_sincronizacao(self):
        import threading
        chamadas = []
        original = S.shopee_manual_sync
        parar = threading.Event()
        S.shopee_manual_sync = lambda **k: (chamadas.append(k), parar.set())
        esperar = parar.wait
        try:
            parar.wait = lambda t=None: esperar(0.01) if not chamadas else True
            S.shopee_orders_worker_loop(parar)
        finally:
            S.shopee_manual_sync = original
        self.assertEqual(chamadas, [{"acordar_etiquetas": False}])


class EtiquetaShopeeSemEspera(__import__("base").TesteNoromak):
    """V18.62 — etiquetas Shopee demoravam 7 a 10 minutos (ou mais) para cair.

    O worker andava uma etapa por ciclo de 3 min (organizar → rastreio → gerar →
    baixar), a importação de pedido novo não o acordava, a fila de pendentes sem
    etiqueta cortava em 40 sem olhar a data e cada pedido fazia uma chamada só de
    diagnóstico.
    """

    def setUp(self):
        super().setUp()
        self.originais = (S.shopee_api_get, S.shopee_api_post, S.shopee_api_post_bytes,
                          S.shopee_log_event, S.SHOPEE_DOC_RESULT_RETRY_WAIT)
        S.shopee_log_event = lambda *a, **k: None
        S.SHOPEE_DOC_RESULT_RETRY_WAIT = 0
        self.chamadas = []

    def tearDown(self):
        (S.shopee_api_get, S.shopee_api_post, S.shopee_api_post_bytes,
         S.shopee_log_event, S.SHOPEE_DOC_RESULT_RETRY_WAIT) = self.originais
        super().tearDown()

    def fingir_shopee(self, status_result):
        """status_result: lista de status devolvidos em sequência por get_shipping_document_result."""
        fila = list(status_result)

        def get(path, params=None):
            self.chamadas.append(path)
            if path.endswith("get_tracking_number"):
                return {"response": {"tracking_number": "BR1"}}
            return {"response": {}}

        def post(path, body=None):
            self.chamadas.append(path)
            if path.endswith("get_shipping_document_parameter"):
                return {"response": {"result_list": [{"order_sn": "NOVO01", "suggest_shipping_document_type": "THERMAL_AIR_WAYBILL"}]}}
            if path.endswith("create_shipping_document"):
                return {"response": {"result_list": [{"order_sn": "NOVO01"}]}}
            if path.endswith("get_shipping_document_result"):
                st = fila.pop(0) if fila else "PROCESSING"
                return {"response": {"result_list": [{"order_sn": "NOVO01", "status": st}]}}
            return {"response": {}}

        S.shopee_api_get = get
        S.shopee_api_post = post
        S.shopee_api_post_bytes = lambda path, body=None: (b"^XA^FDX^FS^XZ", "text/plain")

    def test_etiqueta_em_processamento_e_consultada_de_novo_no_mesmo_ciclo(self):
        self.fingir_shopee(["PROCESSING", "READY"])
        r = S.shopee_sync_shipping_documents([{"order_sn": "NOVO01"}], ja_organizados={"NOVO01"})
        self.assertEqual(r["vinculados"], 1, "REGRESSÃO: etiqueta pronta segundos depois ficou para o próximo ciclo (3 min)")

    def test_etiqueta_pronta_nao_faz_chamada_de_diagnostico(self):
        self.fingir_shopee(["READY"])
        S.shopee_sync_shipping_documents([{"order_sn": "NOVO01"}], ja_organizados={"NOVO01"})
        self.assertNotIn("/api/v2/logistics/get_shipping_document_data_info", self.chamadas)

    def test_pedido_novo_nao_fica_fora_da_fila_de_pendentes(self):
        import time as _t
        from base import gravar_como_integracao, linha
        ontem = _t.strftime("%Y-%m-%d", _t.localtime(_t.time() - 86400))
        hoje = _t.strftime("%Y-%m-%d")
        linhas = [linha(f"TRAVADO{i:03d}", "SKU", uid=f"T{i}", Marketplace="SHOPEE",
                        **{"Data do Pedido": ontem}) for i in range(45)]
        linhas.append(linha("NOVOHOJE01", "SKU", uid="N1", Marketplace="SHOPEE", **{"Data do Pedido": hoje}))
        gravar_como_integracao(linhas)
        fila = [x["order_sn"] for x in S.shopee_pedidos_sem_etiqueta()]
        self.assertEqual(fila[0], "NOVOHOJE01", "REGRESSÃO: pedido novo ficou atrás de 40 travados e nunca ganha etiqueta")

    def test_worker_volta_rapido_so_enquanto_ha_pedido_no_meio_do_fluxo(self):
        normal, rapido = S.SHOPEE_LABEL_WORKER_INTERVAL, S.SHOPEE_LABEL_FOLLOWUP_INTERVAL
        self.assertLess(rapido, normal)
        espera, n = S._shopee_label_proxima_espera({"aguardando": 1}, 0)
        self.assertEqual((espera, n), (rapido, 1))
        espera, n = S._shopee_label_proxima_espera({"aguardando": 0}, 3)
        self.assertEqual((espera, n), (normal, 0))
        # Pedido que nunca anda (ex.: sem NF-e) não pode prender o worker no ritmo rápido.
        espera, n = S._shopee_label_proxima_espera({"aguardando": 1}, S.SHOPEE_LABEL_FOLLOWUP_MAX)
        self.assertEqual(espera, normal)
        # Progresso real (etiqueta vinculada) zera o limite.
        espera, n = S._shopee_label_proxima_espera({"aguardando": 1, "vinculados": 1}, S.SHOPEE_LABEL_FOLLOWUP_MAX)
        self.assertEqual((espera, n), (rapido, 1))


class ImportacaoDePedidoNovoAcordaEtiquetas(__import__("base").TesteNoromak):
    """V18.62 — o worker de pedidos (a cada minuto) não acordava o de etiquetas:
    o pedido novo esperava até 3 min só para começar."""

    def setUp(self):
        super().setUp()
        from base import linha
        self.originais = (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
                          S._shopee_build_pending_lines, S.shopee_log_event)
        S.shopee_status_payload = lambda: {"connected": True, "shop_id": "1"}
        S.shopee_current_shipments = lambda: [{"order_sn": "NOVOPEDIDO01", "package_number": "P1"}]
        S.shopee_order_details = lambda sns: [{"order_sn": x, "order_status": "READY_TO_SHIP"} for x in sns]
        S._shopee_build_pending_lines = lambda order, pkgs, produtos: [
            linha(order["order_sn"], "SKU-1", uid="U-" + order["order_sn"],
                  Marketplace="SHOPEE", OrigemCadastro="API_SHOPEE")]
        S.shopee_log_event = lambda *a, **k: None
        S.SHOPEE_LABEL_WAKE_EVENT.clear()

    def tearDown(self):
        (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
         S._shopee_build_pending_lines, S.shopee_log_event) = self.originais
        S.SHOPEE_LABEL_WAKE_EVENT.clear()
        super().tearDown()

    def test_pedido_novo_acorda_e_repeticao_nao(self):
        S.shopee_manual_sync(acordar_etiquetas=False)
        self.assertTrue(S.SHOPEE_LABEL_WAKE_EVENT.is_set(), "REGRESSÃO: pedido novo não acordou o worker de etiquetas")
        S.SHOPEE_LABEL_WAKE_EVENT.clear()
        S.shopee_manual_sync(acordar_etiquetas=False)
        self.assertFalse(S.SHOPEE_LABEL_WAKE_EVENT.is_set(),
                         "a sincronização de cada minuto não pode acordar o worker sem pedido novo (V17.92)")
