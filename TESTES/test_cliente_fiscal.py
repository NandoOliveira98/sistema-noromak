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


class ClienteFiscalGuardado(TesteNoromak):
    """V17.86 — o que a extensão lê na página da venda fica numa tabela própria."""

    def setUp(self):
        super().setUp()
        S.CLIENTE_FISCAL_CACHE.clear()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM cliente_fiscal")
            S.DB.commit()
        self._ml_api_get_original = S.ml_api_get
        S.ml_api_get = lambda *a, **k: {"id": 1, "buyer": {"nickname": "APELIDO"}}
        gravar_como_integracao([linha(
            "2000018514706508", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            Cliente="Carlos Eduardo L.Costa", VendaMercadoLivre="2000018514706508",
        )], origem="API Mercado Livre")

    def tearDown(self):
        S.ml_api_get = self._ml_api_get_original
        super().tearDown()

    def guardar(self, **corpo):
        base = {"pedido": "2000018514706508", "documento": "322.443.738-35",
                "nome": "Carlos Eduardo Lima Costa"}
        base.update(corpo)
        return self.cliente.pedir("POST", "/api/cliente-fiscal", base)

    def test_guarda_e_a_consulta_passa_a_usar(self):
        status, corpo = self.guardar()
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal?pedido=2000018514706508")
        self.assertEqual(corpo["documento"], {"tipo": "CPF", "numero": "32244373835"})
        self.assertEqual(corpo["nome_fiscal"], "Carlos Eduardo Lima Costa")
        self.assertEqual(corpo["fonte"], "noromak")

    def test_aparece_na_lista_da_central(self):
        self.guardar(linx_cliente="81523")
        _, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal/lista")
        item = corpo["clientes"]["2000018514706508"]
        self.assertEqual(item["nome_fiscal"], "Carlos Eduardo Lima Costa")
        self.assertEqual(item["linx_cliente"], "81523")
        self.assertTrue(item["linx_atualizado_em"])

    def test_nova_leitura_nao_apaga_o_codigo_do_linx(self):
        self.guardar(linx_cliente="81523")
        self.guardar()  # releitura da página, sem código do Linx
        _, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal/lista")
        self.assertEqual(corpo["clientes"]["2000018514706508"]["linx_cliente"], "81523")

    def test_sobrevive_a_sincronizacao_do_ml(self):
        """A sincronização reescreve NomeFiscal/CPF da linha com vazio. A tabela fica."""
        self.guardar()
        gravar_como_integracao([linha(
            "2000018514706508", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
            Cliente="Carlos Eduardo L.Costa", VendaMercadoLivre="2000018514706508",
            NomeFiscalMercadoLivre="", CPFMercadoLivre="",
        )], origem="API Mercado Livre")
        _, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal?pedido=2000018514706508")
        self.assertEqual(corpo["nome_fiscal"], "Carlos Eduardo Lima Costa")

    def test_recusa_texto_de_tela_como_nome(self):
        """O erro real de 18/09/2026: a leitura pegou "Observações"."""
        status, corpo = self.guardar(nome="Observações")
        self.assertEqual(status, 400, corpo)

    def test_recusa_cpf_invalido(self):
        status, _ = self.guardar(documento="123")
        self.assertEqual(status, 400)

    def test_recusa_pedido_inexistente(self):
        status, _ = self.guardar(pedido="NAO-EXISTE")
        self.assertEqual(status, 400)

    def test_so_mexe_nos_pedidos_para_preencher_a_conferencia(self):
        """V17.87: a 1ª leitura preenche Nome Conferência; as seguintes não mexem."""
        import json
        valor_antes, _, _ = S.get_storage("conf_pedidos")
        self.guardar()
        valor_1, versao_1, _ = S.get_storage("conf_pedidos")
        antes = {k: v for k, v in json.loads(valor_antes)[0].items()}
        depois = {k: v for k, v in json.loads(valor_1)[0].items()}
        mudou = {k for k in set(antes) | set(depois) if antes.get(k) != depois.get(k)}
        self.assertEqual(mudou, set(S.CAMPOS_NOME_CONFERENCIA) | {"NomeConferenciaFonte"},
                         "guardar o cliente mexeu em campo que não é o nome de conferência")
        self.guardar()
        _, versao_2, _ = S.get_storage("conf_pedidos")
        self.assertEqual(versao_1, versao_2, "a segunda leitura gravou de novo em conf_pedidos")

    def test_exige_admin(self):
        from base import Cliente
        status, _ = Cliente(self.url_base).pedir("POST", "/api/cliente-fiscal", {"pedido": "X"})
        self.assertIn(status, (401, 403))
        status, _ = Cliente(self.url_base).pedir("GET", "/api/cliente-fiscal/lista")
        self.assertIn(status, (401, 403))


class ShopeeUsuarioNaoEhNomeFiscal(TesteNoromak):
    """V17.88 — a API da Shopee mascara o nome; o sistema punha o nome de
    usuário (ex.: janiosergio578) como nome fiscal, e ele iria para o Linx."""

    def test_usuario_gravado_como_nome_fiscal_e_ignorado(self):
        row = {"Pedido": "260918BHYEGN3E", "UsuarioShopee": "janiosergio578",
               "NomeFiscalShopee": "janiosergio578", "CPFShopee": "08677683470"}
        self.assertEqual(S.nome_fiscal_do_pedido(row), "")

    def test_nome_real_da_shopee_continua_valendo(self):
        row = {"Pedido": "X", "UsuarioShopee": "janiosergio578",
               "NomeFiscalShopee": "Janio Sergio Araujo Da Nobrega"}
        self.assertEqual(S.nome_fiscal_do_pedido(row), "Janio Sergio Araujo Da Nobrega")

    def test_cliente_fiscal_da_shopee_traz_cpf_e_nao_traz_usuario(self):
        gravar_como_integracao([linha(
            "260918BHYEGN3E", "0893573150", uid="S1", Marketplace="SHOPEE",
            Cliente="janiosergio578", UsuarioShopee="janiosergio578",
            NomeFiscalShopee="janiosergio578", CPFShopee="08677683470",
        )])
        status, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal?pedido=260918BHYEGN3E")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["documento"], {"tipo": "CPF", "numero": "08677683470"})
        self.assertEqual(corpo["nome_fiscal"], "", "o nome de usuário foi entregue como nome fiscal")
        self.assertTrue(corpo["aviso"])


class NomeConferenciaPelaNFe(TesteNoromak):
    """V17.87 — o nome de Dados para a NF-e preenche o campo Conferência."""

    def setUp(self):
        super().setUp()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM cliente_fiscal")
            S.DB.commit()

    def nomes_conferencia(self, pedido):
        valor, _, _ = S.get_storage("conf_pedidos")
        import json
        return [r.get("Nome Conferência") for r in json.loads(valor or "[]")
                if isinstance(r, dict) and S.normalize(S.order_number(r)) == S.normalize(pedido)]

    def guardar(self, pedido, nome="Carlos Eduardo Lima Costa"):
        return self.cliente.pedir("POST", "/api/cliente-fiscal",
                                  {"pedido": pedido, "documento": "32244373835", "nome": nome})

    def test_preenche_conferencia_vazia_em_todas_as_linhas(self):
        gravar_como_integracao([
            linha("PED-KIT", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE"),
            linha("PED-KIT", "SKU-B", uid="L2", Marketplace="MERCADO LIVRE"),
        ], origem="API Mercado Livre")
        status, corpo = self.guardar("PED-KIT")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["conferencia_preenchida"], 2)
        self.assertEqual(self.nomes_conferencia("PED-KIT"), ["Carlos Eduardo Lima Costa"] * 2)

    def test_nunca_troca_nome_ja_digitado(self):
        gravar_como_integracao([linha("PED-1", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE")],
                               origem="API Mercado Livre")
        self.cliente.pedir("POST", "/api/order-conference-name", {"pedido": "PED-1", "nome": "Nome da Operadora"})
        _, corpo = self.guardar("PED-1")
        self.assertEqual(corpo["conferencia_preenchida"], 0)
        self.assertEqual(self.nomes_conferencia("PED-1"), ["Nome da Operadora"],
                         "o preenchimento automático passou por cima de um nome humano")

    def test_sincronizacao_nao_apaga_o_nome_preenchido(self):
        gravar_como_integracao([linha("PED-2", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE")],
                               origem="API Mercado Livre")
        self.guardar("PED-2")
        gravar_como_integracao([linha("PED-2", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE",
                                      Cliente="Nome da conta")], origem="API Mercado Livre")
        self.assertEqual(self.nomes_conferencia("PED-2"), ["Carlos Eduardo Lima Costa"])

    def test_nome_preenchido_pode_ser_corrigido_a_mao(self):
        """Sem override ADMIN: a correção humana posterior vale."""
        gravar_como_integracao([linha("PED-3", "SKU-A", uid="L1", Marketplace="MERCADO LIVRE")],
                               origem="API Mercado Livre")
        self.guardar("PED-3")
        status, _ = self.cliente.pedir("POST", "/api/order-conference-name", {"pedido": "PED-3", "nome": "Outro Nome"})
        self.assertEqual(status, 200)
        self.assertEqual(self.nomes_conferencia("PED-3"), ["Outro Nome"])


if __name__ == "__main__":
    unittest.main()
