"""V17.85 — botão "Cliente" da Central de Pedidos do Dia.

A rota /api/cliente-fiscal entrega CPF/CNPJ e nome de "Dados para a NF-e" de
um pedido, para a extensão do Chrome atualizar o cadastro no Linx DMS. O nome
TEM que ser o da nota: nunca apelido da conta, nunca quem recebe. A rota é
somente leitura.
"""

import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha


class ClienteFiscal(TesteNoromak):

    def setUp(self):
        super().setUp()
        S.CLIENTE_FISCAL_CACHE.clear()
        self._ml_api_get_original = S.ml_api_get
        self.chamadas_ml = []

    def tearDown(self):
        S.ml_api_get = self._ml_api_get_original
        super().tearDown()

    def consultar(self, pedido):
        return self.cliente.pedir("GET", f"/api/cliente-fiscal?pedido={pedido}")

    def fingir_ml(self, resposta):
        def falso(caminho, headers=None):
            self.chamadas_ml.append(caminho)
            if isinstance(resposta, Exception):
                raise resposta
            return resposta
        S.ml_api_get = falso

    def test_usa_o_que_o_pedido_ja_guarda(self):
        gravar_como_integracao([linha(
            "2000018464816980", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            Cliente="Naor P Junior", CPFMercadoLivre="33121733885",
            NomeFiscalMercadoLivre="Naor Prato Junior",
        )], origem="API Mercado Livre")
        self.fingir_ml(AssertionError("não devia chamar o ML"))
        status, corpo = self.consultar("2000018464816980")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["documento"], {"tipo": "CPF", "numero": "33121733885"})
        self.assertEqual(corpo["nome_fiscal"], "Naor Prato Junior")
        self.assertEqual(corpo["fonte"], "pedido")
        self.assertEqual(self.chamadas_ml, [], "chamou a API do ML sem precisar")

    def test_pedido_antigo_do_ml_rele_a_nota_na_api(self):
        """Pedidos importados antes da V17.84 não guardaram o CPF."""
        gravar_como_integracao([linha(
            "2000018464816980", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            Cliente="Naor P Junior", VendaMercadoLivre="2000018464816980",
        )], origem="API Mercado Livre")
        self.fingir_ml({
            "id": 2000018464816980, "site_id": "MLB",
            "buyer": {"nickname": "NAOR.JUNIOR", "billing_info": {
                "name": "Naor", "last_name": "Prato Junior",
                "identification": {"type": "CPF", "number": "331.217.338-85"}}},
        })
        status, corpo = self.consultar("2000018464816980")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["documento"], {"tipo": "CPF", "numero": "33121733885"})
        self.assertIn("Naor", corpo["nome_fiscal"])
        self.assertNotIn("NAOR.JUNIOR", corpo["nome_fiscal"], "usou o apelido da conta")
        self.assertEqual(corpo["fonte"], "mercado_livre")

    def test_nunca_usa_o_nome_de_quem_recebe(self):
        gravar_como_integracao([linha(
            "2000000000000001", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            Cliente="Quem Recebe", VendaMercadoLivre="2000000000000001",
        )], origem="API Mercado Livre")
        self.fingir_ml({"id": 1, "buyer": {"nickname": "APELIDO"}})
        status, corpo = self.consultar("2000000000000001")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["nome_fiscal"], "",
                         "sem Dados para NF-e o nome tem que vir vazio, não o do pedido")
        self.assertTrue(corpo["aviso"])

    def test_falha_do_ml_nao_derruba_a_rota(self):
        gravar_como_integracao([linha(
            "2000000000000002", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            VendaMercadoLivre="2000000000000002",
        )], origem="API Mercado Livre")
        self.fingir_ml(RuntimeError("token expirado"))
        status, corpo = self.consultar("2000000000000002")
        self.assertEqual(status, 200, corpo)
        self.assertIn("Mercado Livre", corpo["aviso"])

    def test_shopee_sem_cpf_avisa(self):
        gravar_como_integracao([linha("SHP-1", "SKU-A", uid="L1", Marketplace="SHOPEE")])
        self.fingir_ml(AssertionError("Shopee não consulta o ML"))
        status, corpo = self.consultar("SHP-1")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["documento"]["numero"], "")
        self.assertIn("Shopee", corpo["aviso"])

    def test_acha_pedido_ja_conferido(self):
        gravar_como_integracao([linha(
            "PED-C", "SKU-A", uid="L1", CPFShopee="46414894168", NomeFiscalShopee="Fulano de Tal",
        )])
        self.cliente.pedir("POST", "/api/order-complete", {"pedido": "PED-C"})
        status, corpo = self.consultar("PED-C")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["documento"]["numero"], "46414894168")

    def test_pedido_inexistente(self):
        status, corpo = self.consultar("NAO-EXISTE")
        self.assertEqual(status, 404)
        self.assertFalse(corpo["ok"])

    def test_exige_admin(self):
        from base import Cliente
        status, _ = Cliente(self.url_base).pedir("GET", "/api/cliente-fiscal?pedido=X")
        self.assertIn(status, (401, 403))

    def test_consulta_nao_grava_nada(self):
        gravar_como_integracao([linha(
            "2000000000000003", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            VendaMercadoLivre="2000000000000003",
        )], origem="API Mercado Livre")
        self.fingir_ml({"id": 3, "buyer": {"billing_info": {
            "name": "Fulano", "identification": {"type": "CPF", "number": "46414894168"}}}})
        _, versao_antes, _ = S.get_storage("conf_pedidos")
        self.consultar("2000000000000003")
        _, versao_depois, _ = S.get_storage("conf_pedidos")
        self.assertEqual(versao_antes, versao_depois, "a consulta gravou no banco")


if __name__ == "__main__":
    unittest.main()
