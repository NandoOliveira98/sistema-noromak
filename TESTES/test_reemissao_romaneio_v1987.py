"""V19.87 — Reemissão do romaneio pela Conferência.

Caso real (27/10): 247 de 247 romaneios soltos pela porta voltaram "Impressão enviada para o
gerenciador de impressão e impressora 1", mas às vezes a folha não sai no estoque. A reemissão
é feita pela extensão NOROMAK_REEMISSAO_ROMANEIO no Linx web (o servidor não alcança o Apollo);
o servidor só diz, para o pedido aberto na Conferência, qual romaneio foi solto e em qual
atendimento. O que não pode quebrar:
- o número do romaneio sai da mensagem do Apollo guardada no `romaneio/soltar`;
- erro depois de um ok não apaga o ok; só erro = "não solto" (com a última situação);
- o operador da Conferência lê (não é rota de gestão), mas não o pedido de outra filial.
"""

from base import S, SENHA_ADMIN, Cliente, TesteNoromak, gravar_como_integracao, linha

PEDIDO = "2000019188694346"
MENSAGEM = ("Romaneio 125245 do contato 790497 gerado com sucesso! \r\n"
            "Impressão enviada para o gerenciador de impressão e impressora 1.")


def linha_ml(venda: str, sku: str, casa: str = "") -> dict:
    campos = dict(Marketplace="MERCADO LIVRE", VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre")
    if casa:
        campos.update(Casa=casa, ContaMercadoLivre=casa)
    return linha(venda, sku, uid=f"MLAPI_{venda}_{sku}", **campos)


class ReemissaoRomaneio(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha_ml(PEDIDO, "26330445"), linha_ml("2000099999999999", "X1", casa="bauru")],
                               origem="API Mercado Livre")
        self.operador = self.novo_usuario("joao", "aracatuba")

    def novo_usuario(self, login: str, filial: str) -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), "operador", True, SENHA_ADMIN, "teste")
            S.DB.execute("UPDATE app_users SET casa=? WHERE username=?", (filial, login))
            S.DB.commit()
        cliente = Cliente(self.url_base)
        cliente.entrar(login, SENHA_ADMIN, area="conferencia")
        return cliente

    def soltar(self, situacao: str = "ok", pedido: str = PEDIDO) -> None:
        detalhe = ({"observacao": "MERCADO LIVRE 10:30", "soltos": [{"codigo": "26330445", "quantidade": 1.0}],
                    "mensagens": [MENSAGEM], "via": "porta"} if situacao == "ok" else "Apollo não respondeu")
        S._linx_operacao_registrar(pedido, "romaneio", situacao, detalhe, "790497", "luis")

    def test_romaneio_solto_traz_numero_atendimento_e_observacao(self):
        self.soltar()
        status, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["solto"])
        self.assertEqual(corpo["romaneio"], "125245")
        self.assertEqual(corpo["atendimento"], "790497")
        self.assertEqual(corpo["observacao"], "MERCADO LIVRE 10:30")
        self.assertEqual(corpo["revenda"], 1)
        self.assertEqual(corpo["pecas"], [{"codigo": "26330445", "quantidade": 1.0}])
        self.assertTrue(corpo["em"])

    def test_sem_romaneio_e_so_erro_nao_e_solto(self):
        status, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertEqual(status, 200, corpo)
        self.assertFalse(corpo["solto"])
        self.soltar("erro")
        _, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertFalse(corpo["solto"])
        self.assertEqual(corpo["ultima_situacao"], "erro")

    def test_erro_depois_do_ok_nao_apaga_o_romaneio(self):
        self.soltar()
        self.soltar("erro")
        _, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertTrue(corpo["solto"])
        self.assertEqual(corpo["romaneio"], "125245")

    def test_mensagem_sem_numero_fica_sem_romaneio(self):
        S._linx_operacao_registrar(PEDIDO, "romaneio", "ok", {"mensagens": ["ok"], "via": "porta"}, "790497", "luis")
        _, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertTrue(corpo["solto"])
        self.assertEqual(corpo["romaneio"], "")
        self.assertEqual(corpo["atendimento"], "790497")

    def test_pedido_de_outra_filial_e_recusado(self):
        self.soltar(pedido="2000099999999999")
        status, corpo = self.operador.pedir("GET", "/api/romaneio-pedido?pedido=2000099999999999")
        self.assertEqual(status, 403, corpo)
        self.assertEqual(corpo.get("code"), "OUTRA_FILIAL")

    # --- Revisão de 27/10 ---------------------------------------------------------------

    def test_resposta_nao_traz_o_login_de_quem_soltou(self):
        self.soltar()
        _, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertNotIn("por", corpo)

    def test_pedido_fora_da_fila_nao_e_lido_por_quem_ve_uma_filial(self):
        # Pedido concluído (fora da fila) de Araçatuba: a trava de filial não sabe de quem é.
        S._linx_operacao_registrar("2000088888888888", "romaneio", "ok",
                                   {"mensagens": [MENSAGEM], "via": "porta"}, "790497", "luis")
        bauru = self.novo_usuario("lucas", "bauru")
        status, corpo = bauru.pedir("GET", "/api/romaneio-pedido?pedido=2000088888888888")
        self.assertEqual(status, 404, corpo)
        self.assertNotIn("atendimento", corpo)
        # Quem vê todas as filiais (Admin) continua lendo.
        status, corpo = self.cliente.pedir("GET", "/api/romaneio-pedido?pedido=2000088888888888")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["romaneio"], "125245")

    def test_dois_romaneios_no_mesmo_soltar_aparecem_os_dois(self):
        S._linx_operacao_registrar(PEDIDO, "romaneio", "ok", {"mensagens": [
            "Romaneio 125245 do contato 790497 gerado com sucesso!",
            "Romaneio 125246 do contato 790497 gerado com sucesso! Romaneio 125246 impresso."], "via": "porta"},
            "790497", "luis")
        _, corpo = self.operador.pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertEqual(corpo["romaneios"], ["125245", "125246"])

    def test_tela_conferencia_protege_o_leitor_consulta_e_pedido_trocado(self):
        from pathlib import Path
        html = (Path(S.__file__).parent / "APP" / "conferencia.html").read_text(encoding="utf-8")
        ini = html.index("function reimprimirRomaneioV1987")
        corpo = html[ini:html.index("function abrirPedido(uid)")]
        # O campo de código volta a ter foco logo depois de mandar (o botão fica desativado).
        envio = corpo.index('tipo: "noromak-reemissao-romaneio"')
        self.assertIn("focarCampoCodigoAposAcao(input)", corpo[envio:envio + 400])
        # A resposta de outro pedido não cobre o aviso do pedido aberto.
        self.assertIn("romaneioPedidoPedidoV1987 !== pedidoDoClique", corpo)
        carregar = html[html.index("async function carregarRomaneioPedidoV1987"):ini]
        # Consulta (somente leitura) não ganha o botão; romaneio sem número também não.
        self.assertIn("dataset.somenteLeitura", carregar)
        self.assertIn("romaneios", carregar)

    def test_sem_login_e_sem_pedido(self):
        status, _ = Cliente(self.url_base).pedir("GET", f"/api/romaneio-pedido?pedido={PEDIDO}")
        self.assertEqual(status, 401)
        status, _ = self.operador.pedir("GET", "/api/romaneio-pedido")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
