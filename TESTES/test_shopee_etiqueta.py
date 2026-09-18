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
