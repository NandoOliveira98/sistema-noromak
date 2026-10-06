"""V18.60 — Linx Smart API, etapa 1: "cliente já cadastrado no Linx?".

A consulta é SOMENTE LEITURA no Linx: só token e ConsultaClientes. Os testes
sobem uma Linx falsa em 127.0.0.1 — nenhuma chamada sai para a Linx de verdade.
O que importa aqui:
- nada é chamado sem .env, e a senha nunca aparece em status/erro;
- só as rotas de leitura são usadas;
- o código vindo da consulta NÃO substitui o que a extensão gravou (linx_cliente);
- CPF de outra pessoa devolvido pela Linx não vira "encontrado";
- mais de um código para o mesmo CPF não é escolhido sozinho;
- CPF corrigido depois volta a ser consultado.
"""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from base import S, TesteNoromak, gravar_como_integracao, linha

CPF = "33121733885"
SENHA = "senha-secreta-linx"


class LinxFalsa:
    def __init__(self):
        self.chamadas = []
        self.clientes = {}          # cpf -> lista de registros devolvidos
        self.token_atual = "tok-1"
        self.tokens_emitidos = 0
        self.falhar_consulta = 0    # status HTTP a devolver na consulta (0 = normal)
        self.agenda = []            # registros da Agenda de contatos
        self.listas = {}            # contato -> ItensRomaneio pendentes
        self.confirma_status = 200  # 208 = Linx perguntando algo
        self.nome_depois_aar = None
        self.simplificado_ignora_bloqueio = False   # V18.70: Linx sem a correção
        self.simplificado_apaga = ()                 # campos que o Simplificado zera
        falsa = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_POST(self):
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(tamanho).decode("utf-8")
                falsa.chamadas.append({"path": self.path, "headers": dict(self.headers), "corpo": corpo})
                if self.path == "/api-seguranca/token":
                    falsa.tokens_emitidos += 1
                    falsa.token_atual = f"tok-{falsa.tokens_emitidos}"
                    return self.responder(200, {"access_token": falsa.token_atual, "token_type": "bearer",
                                                "expires_in": 3600})
                if self.path == "/api-veiculos-crm/Geral/ConsultaClientes/ConsultaClientes":
                    if self.headers.get("Authorization") != f"Bearer {falsa.token_atual}":
                        return self.responder(401, {"message": "token invalido"})
                    if falsa.falhar_consulta:
                        return self.responder(falsa.falhar_consulta, {"MensagemDeErro": "falha simulada"})
                    dados = json.loads(corpo)
                    return self.responder(200, falsa.clientes.get(dados.get("CgcOuCpf"), []))
                pecas = "/api-pecas-balcao-premium/Pecas/ManutencaoRomaneio/PecRomaneio/"
                if self.path == pecas + "GeraListaRomaneio":
                    contato = json.loads(corpo)["Contato"]
                    return self.responder(200, {"ItensRomaneio": falsa.listas.get(contato, []), "Observacao": "",
                                                "ImprimirRomaneioPDF": False, "RomaneioAutomatico": False})
                if self.path == pecas + "AtualizaListaRomaneio/AtualizaQuantidades":
                    return self.responder(200, json.loads(corpo)["ListaRomaneio"])
                if self.path == pecas + "ConfirmaRomaneio":
                    if falsa.confirma_status == 208:
                        return self.responder(208, {"TextoDeConfirmacao": "Item sem estoque. Gerar providencia?"})
                    contato = json.loads(corpo)["IdentificacaoAtendimento"]["Contato"]
                    falsa.listas[contato] = []
                    return self.responder(200, ["Romaneio 123 gerado"])
                if self.path == "/api-veiculos-crm/Geral/ManutencaoClienteSimplificado/AtualizarCadastroClienteSimplificado":
                    dados = json.loads(corpo)
                    for reg in falsa.clientes.get(dados["CPFCNPJ"], []):
                        if not falsa.simplificado_ignora_bloqueio:
                            reg["BloqueioCredito"] = dados["BloqueioCredito"]
                        for campo in falsa.simplificado_apaga:
                            reg[campo] = ""
                    return self.responder(200, None)
                if self.path == "/api-veiculos-crm/Geral/ManutencaoClienteSimplificado/AtualizarCadastroClienteAAR":
                    dados = json.loads(corpo)
                    for reg in falsa.clientes.get(dados["CPFCNPJ"], []):
                        reg["Nome"] = falsa.nome_depois_aar or dados["Nome"]
                    return self.responder(200, None)
                self.responder(404, {})

            def do_GET(self):
                falsa.chamadas.append({"path": self.path, "headers": dict(self.headers), "corpo": ""})
                if self.path.startswith("/api-pecas-balcao-premium/VendaExternaPecas/Contatos/Agenda/"):
                    return self.responder(200, falsa.agenda)
                self.responder(404, {})

            def responder(self, status, dados):
                bruto = b"" if dados is None else json.dumps(dados).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def gravacoes(self):
        return [c["path"] for c in self.chamadas
                if c["path"].endswith(("AtualizaQuantidades", "ConfirmaRomaneio", "AtualizarCadastroClienteAAR",
                                       "AtualizarCadastroClienteSimplificado"))]

    def consultas(self):
        return [c for c in self.chamadas if "ConsultaClientes" in c["path"]]

    def parar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


class _BaseLinx(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.linx = LinxFalsa()
        self.original = {k: getattr(S, k) for k in
                         ("LINX_API_BASE", "LINX_API_CHAVE", "LINX_API_USUARIO", "LINX_API_SENHA",
                          "LINX_ESCRITA_ATIVA")}
        S.LINX_API_BASE = self.linx.url
        S.LINX_API_CHAVE = "chave-assinatura"
        S.LINX_API_USUARIO = "integracao"
        S.LINX_API_SENHA = SENHA
        S.LINX_TOKEN.update(access_token="", expira=0.0)
        S.LINX_ESTADO.update(ultimo_ok="", ultimo_erro="", ultimo_erro_em="")

    def tearDown(self):
        for k, v in self.original.items():
            setattr(S, k, v)
        S.LINX_TOKEN.update(access_token="", expira=0.0)
        self.linx.parar()
        super().tearDown()

    def pedido_com_cpf(self, pedido="2000018464816980", cpf=CPF, nome="Naor Prato Junior", linx_cliente=""):
        gravar_como_integracao([linha(pedido, "SKU-A", uid=f"{pedido}-L1", Marketplace="MERCADO LIVRE")],
                               origem="API Mercado Livre")
        r = S.salvar_cliente_fiscal(pedido, cpf, nome, linx_cliente=linx_cliente, fonte="teste")
        self.assertTrue(r["ok"], r)

    def lista(self):
        status, corpo = self.cliente.pedir("GET", "/api/cliente-fiscal/lista")
        self.assertEqual(status, 200, corpo)
        return corpo["clientes"]


class LinxSmartApi(_BaseLinx):

    def test_sem_env_nao_chama_a_linx(self):
        S.LINX_API_CHAVE = ""
        S.LINX_API_SENHA = ""
        self.pedido_com_cpf()
        resumo = S.linx_clientes_sincronizar()
        self.assertFalse(resumo["ok"])
        self.assertEqual(self.linx.chamadas, [])
        status, corpo = self.cliente.pedir("GET", "/api/linx/status")
        self.assertEqual(status, 200, corpo)
        self.assertFalse(corpo["configurado"])
        self.assertEqual(corpo["faltando"], ["LINX_API_CHAVE", "LINX_API_SENHA"])

    def test_cliente_encontrado_aparece_na_central(self):
        self.pedido_com_cpf()
        self.linx.clientes[CPF] = [{"Cliente": 81558, "Nome": "NAOR PRATO JUNIOR", "Cpf": int(CPF), "Cgc": ""}]
        resumo = S.linx_clientes_sincronizar()
        self.assertEqual(resumo["encontrados"], 1, resumo)
        api = self.lista()["2000018464816980"]["linx_api"]
        self.assertEqual(api["status"], "encontrado")
        self.assertEqual(api["cliente"], "81558")
        # Só leitura: token + ConsultaClientes, e nenhuma outra rota.
        self.assertEqual({c["path"] for c in self.linx.chamadas},
                         {"/api-seguranca/token", "/api-veiculos-crm/Geral/ConsultaClientes/ConsultaClientes"})
        consulta = self.linx.consultas()[0]
        self.assertEqual(consulta["headers"].get("Ocp-Apim-Subscription-Key"), "chave-assinatura")
        corpo = json.loads(consulta["corpo"])
        self.assertEqual(corpo["CgcOuCpf"], CPF)
        self.assertTrue(corpo["PessoaFisica"])
        # Já encontrado: não consulta de novo.
        S.linx_clientes_sincronizar()
        self.assertEqual(len(self.linx.consultas()), 1)

    def test_token_e_reaproveitado_e_renovado_no_401(self):
        self.pedido_com_cpf("P1", CPF)
        self.pedido_com_cpf("P2", "52998224725")
        S.linx_clientes_sincronizar()
        self.assertEqual(self.linx.tokens_emitidos, 1, "pediu token novo a cada consulta")
        # A Linx invalida o token: o servidor pede outro e repete UMA vez.
        self.linx.token_atual = "outro"
        self.linx.clientes[CPF] = [{"Cliente": 7, "Nome": "X", "Cpf": int(CPF)}]
        r = S.linx_consultar_cliente(CPF)
        self.assertEqual(r["status"], "encontrado")
        self.assertEqual(self.linx.tokens_emitidos, 2)

    def test_nao_cadastrado_vira_fila_e_e_reconsultado_depois(self):
        self.pedido_com_cpf()
        S.linx_clientes_sincronizar()
        status, corpo = self.cliente.pedir("GET", "/api/linx/clientes/sem-cadastro")
        self.assertEqual(status, 200, corpo)
        self.assertEqual([p["pedido"] for p in corpo["pedidos"]], ["2000018464816980"])
        self.assertEqual(corpo["pedidos"][0]["documento"]["numero"], CPF)
        # Logo em seguida não consulta de novo...
        S.linx_clientes_sincronizar()
        self.assertEqual(len(self.linx.consultas()), 1)
        # ...mas depois de 6 h sim: alguém pode ter cadastrado.
        with S.DB_LOCK:
            S.DB.execute("UPDATE cliente_fiscal SET linx_api_consultado_epoch=?",
                         (int(time.time()) - S.LINX_RECONSULTA_SEM_CADASTRO - 1,))
            S.DB.commit()
        self.linx.clientes[CPF] = [{"Cliente": 99, "Nome": "NAOR", "Cpf": int(CPF)}]
        S.linx_clientes_sincronizar()
        self.assertEqual(self.lista()["2000018464816980"]["linx_api"]["cliente"], "99")
        _, corpo = self.cliente.pedir("GET", "/api/linx/clientes/sem-cadastro")
        self.assertEqual(corpo["pedidos"], [])

    def test_cpf_de_outra_pessoa_nao_vira_encontrado(self):
        self.pedido_com_cpf()
        self.linx.clientes[CPF] = [{"Cliente": 5, "Nome": "OUTRA PESSOA", "Cpf": 52998224725}]
        S.linx_clientes_sincronizar()
        self.assertEqual(self.lista()["2000018464816980"]["linx_api"]["status"], "nao_cadastrado")

    def test_varios_codigos_nao_escolhe_sozinho(self):
        self.pedido_com_cpf()
        self.linx.clientes[CPF] = [
            {"Cliente": 10, "Nome": "NAOR A", "Cpf": int(CPF)},
            {"Cliente": 11, "Nome": "NAOR B", "Cpf": int(CPF)},
        ]
        S.linx_clientes_sincronizar()
        api = self.lista()["2000018464816980"]["linx_api"]
        self.assertEqual(api["status"], "varios")
        self.assertEqual(api["cliente"], "")

    def test_cpf_com_zero_a_esquerda(self):
        """A Linx devolve Cpf como número: o zero da frente some."""
        cpf = "01234567890"
        self.pedido_com_cpf(cpf=cpf)
        self.linx.clientes[cpf] = [{"Cliente": 12, "Nome": "ZE", "Cpf": 1234567890}]
        S.linx_clientes_sincronizar()
        self.assertEqual(self.lista()["2000018464816980"]["linx_api"]["status"], "encontrado")

    def test_nao_mexe_no_que_a_extensao_gravou(self):
        self.pedido_com_cpf(linx_cliente="81558")
        S.linx_clientes_sincronizar()
        self.assertEqual(self.linx.chamadas, [], "consultou um cliente que a extensão já gravou no Linx")
        self.assertEqual(self.lista()["2000018464816980"]["linx_cliente"], "81558")

    def test_cpf_corrigido_volta_a_ser_consultado(self):
        self.pedido_com_cpf()
        self.linx.clientes[CPF] = [{"Cliente": 1, "Nome": "A", "Cpf": int(CPF)}]
        S.linx_clientes_sincronizar()
        novo = "52998224725"
        S.salvar_cliente_fiscal("2000018464816980", novo, "Naor Prato Junior", fonte="teste")
        self.assertEqual(self.lista()["2000018464816980"]["linx_api"]["status"], "",
                         "mostrou o resultado do CPF antigo")
        S.linx_clientes_sincronizar()
        self.assertEqual(json.loads(self.linx.consultas()[-1]["corpo"])["CgcOuCpf"], novo)

    def test_linx_fora_do_ar_para_o_lote_e_nao_vaza_senha(self):
        self.pedido_com_cpf("P1", CPF)
        self.pedido_com_cpf("P2", "52998224725")
        self.linx.falhar_consulta = 500
        resumo = S.linx_clientes_sincronizar()
        self.assertFalse(resumo["ok"])
        self.assertEqual(len(self.linx.consultas()), 1, "insistiu com a Linx fora do ar")
        status, corpo = self.cliente.pedir("GET", "/api/linx/status")
        self.assertIn("falha simulada", corpo["ultimo_erro"])
        self.assertNotIn(SENHA, json.dumps(corpo))
        self.assertNotIn(SENHA, json.dumps(self.lista()))

    def test_rota_de_teste_e_so_do_admin(self):
        anonimo = type(self.cliente)(self.url_base)
        # Sem corpo: o servidor recusa sem ler o corpo e fecha a conexão; com corpo,
        # o Windows às vezes aborta o envio (WinError 10053) e o teste ficava instável.
        status, _ = anonimo.pedir("POST", "/api/linx/testar")
        self.assertIn(status, (401, 403))
        self.assertEqual(self.linx.chamadas, [])
        self.linx.clientes[CPF] = [{"Cliente": 3, "Nome": "N", "Cpf": int(CPF)}]
        status, corpo = self.cliente.pedir("POST", "/api/linx/testar", {"documento": CPF})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["consulta"]["cliente"], "3")


class LinxRomaneio(_BaseLinx):
    """V18.60 — romaneio do atendimento que a integração do ML já criou."""

    PEDIDO = "2000018464816980"

    def setUp(self):
        super().setUp()
        self.linx.clientes[CPF] = [{"Cliente": 81558, "Nome": "NAOR PRATO JUNIOR", "Cpf": int(CPF),
                                    "SituacaoCredito": "N", "BloqueioCredito": "N"}]
        self.linx.agenda = [
            {"Contato": 5001, "Cliente": 81558, "DataContato": "2026-10-02T09:00:00", "DescricaoSituacaoContato": "Aberto"},
            {"Contato": 5000, "Cliente": 81558, "DataContato": "2026-09-20T09:00:00", "DescricaoSituacaoContato": "Fechado"},
            {"Contato": 7777, "Cliente": 99999, "DataContato": "2026-10-02T09:00:00"},
        ]
        self.linx.listas[5001] = [{"ItemEstoquePub": "0893117732", "DescricaoItemEstoque": "FILTRO",
                                   "QuantidadeRequisitada": 2, "QuantidadeParaImprimir": 2, "CodigoItemEstoque": 158529}]

    def criar_pedido(self, marketplace="MERCADO LIVRE", sku="157507-0893117732", cadastrado=True):
        gravar_como_integracao([linha(self.PEDIDO, sku, uid="L1", Marketplace=marketplace,
                                      **{"Data do Pedido": "2026-09-21T10:00:00-03:00"})], origem="API Mercado Livre")
        # cadastrado = a extensão/robô já gravou cadastro e crédito no Linx.
        self.assertTrue(S.salvar_cliente_fiscal(self.PEDIDO, CPF, "Naor Prato Junior", fonte="teste",
                                                linx_cliente="81558" if cadastrado else "")["ok"])

    def preparar(self):
        status, corpo = self.cliente.pedir("GET", f"/api/linx/romaneio?pedido={self.PEDIDO}")
        self.assertEqual(status, 200, corpo)
        return corpo

    def confirmar(self, **extra):
        corpo = {"pedido": self.PEDIDO, "contato": 5001, "observacao": "MERCADO LIVRE 21/09", **extra}
        return self.cliente.pedir("POST", "/api/linx/romaneio", corpo)

    def test_prepara_acha_o_contato_e_monta_a_observacao(self):
        self.criar_pedido()
        r = self.preparar()
        self.assertEqual(r["situacao"], "pronto", r)
        self.assertEqual(r["escolhido"], "5001")
        self.assertEqual(r["observacao"], "MERCADO LIVRE 21/09", "observação usa a data do PEDIDO")
        self.assertEqual([c["contato"] for c in r["candidatos"]], ["5001"], "atendimento sem item não é candidato")
        self.assertEqual(r["candidatos"][0]["itens_do_pedido"], 1)
        self.assertEqual(r["cliente"]["credito"], {"situacao": "N", "bloqueio": "N"})
        self.assertEqual(self.linx.gravacoes(), [], "preparar gravou no Linx")

    def test_dois_atendimentos_com_itens_so_escolhe_se_um_bate_com_o_pedido(self):
        self.criar_pedido()
        self.linx.listas[5000] = [{"ItemEstoquePub": "999", "DescricaoItemEstoque": "OUTRA", "QuantidadeParaImprimir": 1}]
        r = self.preparar()
        self.assertEqual(r["escolhido"], "5001")
        self.linx.listas[5000] = [{"ItemEstoquePub": "0893117732", "DescricaoItemEstoque": "FILTRO", "QuantidadeParaImprimir": 1}]
        r = self.preparar()
        self.assertEqual(r["situacao"], "escolher")
        self.assertEqual(r["escolhido"], "", "escolheu sozinho entre dois atendimentos iguais")

    def test_escrita_desligada_nao_grava_nada(self):
        S.LINX_ESCRITA_ATIVA = False
        self.criar_pedido()
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertIn("LINX_ESCRITA_ATIVA", corpo["error"])
        status, corpo = self.cliente.pedir("POST", "/api/linx/cadastro", {"pedido": self.PEDIDO})
        self.assertEqual(status, 409, corpo)
        self.assertEqual(self.linx.chamadas, [])

    def test_imprime_com_observacao_e_nao_repete(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido()
        status, corpo = self.confirmar(observacao="mercado livre 21/09")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["situacao"], "impresso")
        confirma = [c for c in self.linx.chamadas if c["path"].endswith("ConfirmaRomaneio")]
        self.assertEqual(len(confirma), 1)
        enviado = json.loads(confirma[0]["corpo"])
        self.assertEqual(enviado["ListaRomaneio"]["Observacao"], "MERCADO LIVRE 21/09")
        self.assertEqual(enviado["IdentificacaoAtendimento"]["Contato"], 5001)
        atualiza = [c for c in self.linx.chamadas if c["path"].endswith("AtualizaQuantidades")]
        self.assertEqual(json.loads(atualiza[0]["corpo"])["ListaRomaneio"]["Observacao"], "MERCADO LIVRE 21/09")
        # Segundo clique: recusado sem tocar no Linx.
        antes = len(self.linx.chamadas)
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertIn("já foi impresso", corpo["error"])
        self.assertEqual(len(self.linx.chamadas), antes)
        self.assertEqual([o["situacao"] for o in S.linx_operacoes_do_pedido(self.PEDIDO)], ["ok"])

    def test_atendimento_sem_itens_nao_confirma(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido()
        self.linx.listas[5001] = []
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertEqual(self.linx.gravacoes(), [])

    def test_pergunta_da_linx_nao_e_respondida(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido()
        self.linx.confirma_status = 208
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertEqual(corpo["situacao"], "pergunta")
        self.assertIn("Gerar providencia", corpo["error"])
        self.assertEqual(len([c for c in self.linx.chamadas if c["path"].endswith("ConfirmaRomaneio")]), 1)

    def test_shopee_exige_condicao_conferida(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(marketplace="SHOPEE")
        status, corpo = self.confirmar(observacao="SHOPEE 21/09")
        self.assertEqual(status, 409, corpo)
        self.assertIn("971", corpo["error"])
        self.assertEqual(self.linx.gravacoes(), [])
        status, corpo = self.confirmar(observacao="SHOPEE 21/09", condicao_conferida=True)
        self.assertEqual(status, 200, corpo)

    def test_condicao_trocada_pela_extensao_libera_a_shopee(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(marketplace="SHOPEE")
        status, corpo = self.cliente.pedir("POST", "/api/linx/operacao",
                                           {"pedido": self.PEDIDO, "etapa": "condicao", "situacao": "ok", "contato": 5001})
        self.assertEqual(status, 200, corpo)
        status, corpo = self.confirmar(observacao="SHOPEE 21/09")
        self.assertEqual(status, 200, corpo)

    def test_romaneio_so_depois_do_cadastro(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(cadastrado=False)
        self.assertEqual(self.preparar()["cadastro"], {"feito": False, "pela_api": False})
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertIn("antes do romaneio", corpo["error"])
        self.assertEqual(self.linx.gravacoes(), [])

    def test_cadastro_pela_api_exige_credito_conferido(self):
        """A API não libera crédito: depois dela, alguém confere no Linx."""
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(cadastrado=False)
        status, corpo = self.cliente.pedir("POST", "/api/linx/cadastro", {"pedido": self.PEDIDO})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(self.preparar()["cadastro"], {"feito": True, "pela_api": True})
        status, corpo = self.confirmar()
        self.assertEqual(status, 409, corpo)
        self.assertIn("crédito", corpo["error"])
        self.assertFalse([c for c in self.linx.chamadas if c["path"].endswith("ConfirmaRomaneio")])
        status, corpo = self.confirmar(credito_conferido=True)
        self.assertEqual(status, 200, corpo)

    def test_cadastro_aar_aplica_a_regra_e_confere(self):
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(cadastrado=False)
        status, corpo = self.cliente.pedir("POST", "/api/linx/cadastro", {"pedido": self.PEDIDO})
        self.assertEqual(status, 200, corpo)
        aar = [c for c in self.linx.chamadas if c["path"].endswith("AtualizarCadastroClienteAAR")]
        enviado = json.loads(aar[0]["corpo"])
        self.assertEqual(enviado["Nome"], "NAOR PRATO JUNIOR")
        self.assertEqual(enviado["Email"], "lojavirtual@noromak.com.br")
        self.assertEqual((enviado["DDDTelefone"], enviado["Telefone"]), (11, 111111111))
        self.assertEqual(enviado["Cliente"], 81558)
        self.assertEqual(self.lista()[self.PEDIDO]["linx_cliente"], "81558")

    def test_cadastro_que_nao_pegou_nao_e_dado_como_feito(self):
        """Neste gateway 200 não é confirmação: relê o cliente e confere."""
        S.LINX_ESCRITA_ATIVA = True
        self.criar_pedido(cadastrado=False)
        self.linx.nome_depois_aar = "NOME ANTIGO"
        status, corpo = self.cliente.pedir("POST", "/api/linx/cadastro", {"pedido": self.PEDIDO})
        self.assertEqual(status, 409, corpo)
        self.assertIn("NOME ANTIGO", corpo["error"])
        self.assertEqual(self.lista()[self.PEDIDO]["linx_cliente"], "")

    def test_contatos_achados_pela_extensao_substituem_a_agenda(self):
        """A Agenda pode não listar o atendimento da integração; a extensão acha pelo Linx local."""
        self.criar_pedido()
        self.linx.agenda = []
        self.linx.listas[6100] = [{"ItemEstoquePub": "0893117732", "DescricaoItemEstoque": "FILTRO", "QuantidadeParaImprimir": 1}]
        status, r = self.cliente.pedir("GET", f"/api/linx/romaneio?pedido={self.PEDIDO}&contatos=6100,abc,6100")
        self.assertEqual(status, 200, r)
        self.assertEqual(r["fonte_contatos"], "extensao")
        self.assertEqual(r["escolhido"], "6100")
        self.assertFalse([c for c in self.linx.chamadas if "Agenda" in c["path"]])


class LinxRoboEExtensao(_BaseLinx):
    """V18.60 — o robô do Apollo desktop faz cadastro + crédito; a extensão faz
    condição de pagamento e caixa. Aqui fica a fila e o registro do que foi feito."""

    PEDIDO = "2000018464816980"

    def setUp(self):
        super().setUp()
        gravar_como_integracao([linha(self.PEDIDO, "0893117732", uid="L1", Marketplace="MERCADO LIVRE")],
                               origem="API Mercado Livre")
        self.assertTrue(S.salvar_cliente_fiscal(self.PEDIDO, CPF, "Naor Prato Junior", fonte="teste")["ok"])

    def robo(self, metodo, rota, corpo=None, token=None):
        import urllib.error
        import urllib.request
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + (token if token is not None else S.get_or_create_linx_robo_token()))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as erro:
            return erro.code, json.loads(erro.read().decode("utf-8") or "{}")

    def enfileirar(self):
        status, corpo = self.cliente.pedir("POST", "/api/linx/robo/fila", {"pedido": self.PEDIDO})
        self.assertEqual(status, 200, corpo)
        return corpo

    def test_robo_sem_token_nao_ve_a_fila(self):
        self.enfileirar()
        status, _ = self.robo("GET", "/api/linx/robo/proximo", token="errado")
        self.assertEqual(status, 401)
        status, _ = self.cliente.pedir("GET", "/api/linx/robo/proximo")  # sessão de admin não basta
        self.assertEqual(status, 401)

    def test_fila_completa_libera_o_romaneio(self):
        self.enfileirar()
        self.assertTrue(self.enfileirar()["ja_na_fila"], "enfileirou duas vezes")
        status, corpo = self.robo("GET", "/api/linx/robo/proximo")
        self.assertEqual(status, 200, corpo)
        item = corpo["item"]
        self.assertEqual((item["cpf"], item["nome"]), (CPF, "NAOR PRATO JUNIOR"))
        status, vazio = self.robo("GET", "/api/linx/robo/proximo")
        self.assertIsNone(vazio["item"], "entregou o mesmo cadastro duas vezes")
        status, corpo = self.robo("POST", "/api/linx/robo/resultado", {"id": item["id"], "ok": True, "cliente": "81558"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(S._linx_cadastro_situacao(self.PEDIDO), {"feito": True, "pela_api": False},
                         "cadastro do robô inclui crédito: não pede conferência")
        self.assertEqual(self.lista()[self.PEDIDO]["linx_cliente"], "81558")

    def test_erro_do_robo_volta_para_a_fila_ate_3_vezes(self):
        self.enfileirar()
        for tentativa in range(1, 4):
            _, corpo = self.robo("GET", "/api/linx/robo/proximo")
            self.assertIsNotNone(corpo["item"], f"tentativa {tentativa}")
            _, r = self.robo("POST", "/api/linx/robo/resultado", {"id": corpo["item"]["id"], "ok": False, "mensagem": "PAROU: x"})
        self.assertEqual(r["situacao"], "erro")
        _, corpo = self.robo("GET", "/api/linx/robo/proximo")
        self.assertIsNone(corpo["item"])
        self.assertFalse(S._linx_cadastro_situacao(self.PEDIDO)["feito"])

    def test_item_esquecido_pelo_robo_volta_depois_de_15_min(self):
        self.enfileirar()
        _, corpo = self.robo("GET", "/api/linx/robo/proximo")
        with S.DB_LOCK:
            S.DB.execute("UPDATE linx_robo_fila_v1860 SET pego_epoch=?", (int(time.time()) - S.LINX_ROBO_TRAVA_SEGUNDOS - 1,))
            S.DB.commit()
        _, de_novo = self.robo("GET", "/api/linx/robo/proximo")
        self.assertEqual(de_novo["item"]["id"], corpo["item"]["id"])

    def test_robo_so_cadastra_cpf(self):
        S.salvar_cliente_fiscal(self.PEDIDO, "11222333000181", "Empresa Teste Ltda", fonte="teste")
        status, corpo = self.cliente.pedir("POST", "/api/linx/robo/fila", {"pedido": self.PEDIDO})
        self.assertEqual(status, 409, corpo)
        self.assertIn("CPF", corpo["error"])

    def test_registro_da_extensao_condicao_e_caixa(self):
        status, corpo = self.cliente.pedir("POST", "/api/linx/operacao",
                                           {"pedido": self.PEDIDO, "etapa": "condicao", "situacao": "ok", "contato": 5001})
        self.assertEqual(status, 200, corpo)
        status, corpo = self.cliente.pedir("POST", "/api/linx/operacao",
                                           {"pedido": self.PEDIDO, "etapa": "romaneio", "situacao": "ok"})
        self.assertEqual(status, 409, "a tela não pode registrar romaneio: só o servidor imprime")
        self.assertEqual([o["etapa"] for o in S.linx_operacoes_do_pedido(self.PEDIDO)], ["condicao"])



class LinxTesteCredito(_BaseLinx):
    """V18.70 — chamado da Linx: BloqueioCredito no AtualizarCadastroClienteSimplificado.

    O Simplificado regrava o cadastro inteiro: o corpo vem do cadastro atual, só o
    crédito muda, e depois o cliente é relido e comparado campo a campo."""

    def setUp(self):
        super().setUp()
        self.linx.clientes[CPF] = [{
            "Cliente": 81558, "Nome": "NAOR PRATO JUNIOR", "Cpf": int(CPF), "CepEntrega": 16010000,
            "MunicipioEntrega": "ARACATUBA", "UfEntrega": "SP", "LogradouroEntrega": "RUA TESTE 10",
            "BairroEntrega": "CENTRO", "DDDTelefone": 11, "Telefone": 111111111, "DDDCelular": 11,
            "Celular": 111111111, "EmailCasa": "lojavirtual@noromak.com.br", "Categoria": 1,
            "SituacaoCredito": "1", "BloqueioCredito": "2",
        }]

    def pedir(self, **corpo):
        return self.cliente.pedir("POST", "/api/linx/teste-credito", {"documento": CPF, **corpo})

    def test_simulacao_nao_grava_e_reenvia_o_cadastro_atual(self):
        S.LINX_ESCRITA_ATIVA = True
        status, corpo = self.pedir(bloqueio="1")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["simulacao"])
        self.assertEqual((corpo["cliente"], corpo["bloqueio_antes"]), ("81558", "2"))
        enviado = corpo["corpo"]
        self.assertEqual(enviado["BloqueioCredito"], "1")
        self.assertEqual((enviado["Endereco"], enviado["Cidade"], enviado["CEP"]), ("RUA TESTE 10", "ARACATUBA", 16010000))
        self.assertEqual((enviado["Cliente"], enviado["CPFCNPJ"]), (81558, CPF))
        self.assertTrue(enviado["BPVCategoria"])
        self.assertEqual(self.linx.gravacoes(), [])

    def test_escrita_desligada_nao_grava(self):
        S.LINX_ESCRITA_ATIVA = False
        status, corpo = self.pedir(bloqueio="1", gravar=True)
        self.assertEqual(status, 409, corpo)
        self.assertIn("LINX_ESCRITA_ATIVA", corpo["error"])
        self.assertEqual(self.linx.gravacoes(), [])

    def test_correcao_funcionou(self):
        S.LINX_ESCRITA_ATIVA = True
        status, corpo = self.pedir(bloqueio="1", gravar=True)
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["gravou_bloqueio"])
        self.assertEqual((corpo["bloqueio_antes"], corpo["bloqueio_depois"]), ("2", "1"))
        self.assertEqual(corpo["outros_campos_alterados"], [])

    def test_linx_aceita_mas_nao_grava_o_credito(self):
        """200 não é confirmação: sem a correção, o teste diz que falhou."""
        S.LINX_ESCRITA_ATIVA = True
        self.linx.simplificado_ignora_bloqueio = True
        status, corpo = self.pedir(bloqueio="1", gravar=True)
        self.assertEqual(status, 200, corpo)
        self.assertFalse(corpo["gravou_bloqueio"])
        self.assertEqual(corpo["bloqueio_depois"], "2")

    def test_campo_apagado_pela_linx_aparece(self):
        S.LINX_ESCRITA_ATIVA = True
        self.linx.simplificado_apaga = ("BairroEntrega",)
        _, corpo = self.pedir(bloqueio="1", gravar=True)
        self.assertEqual(corpo["outros_campos_alterados"],
                         [{"campo": "BairroEntrega", "antes": "CENTRO", "depois": ""}])

    def test_valor_invalido_e_mais_de_um_cliente_sao_recusados(self):
        S.LINX_ESCRITA_ATIVA = True
        status, corpo = self.pedir(bloqueio="9", gravar=True)
        self.assertEqual(status, 409, corpo)
        self.linx.clientes[CPF].append({**self.linx.clientes[CPF][0], "Cliente": 90000})
        status, corpo = self.pedir(bloqueio="1", gravar=True)
        self.assertEqual(status, 409, corpo)
        self.assertIn("exatamente um", corpo["error"])
        self.assertEqual(self.linx.gravacoes(), [])

    def test_so_admin(self):
        anonimo = type(self.cliente)(self.url_base)
        status, _ = anonimo.pedir("POST", "/api/linx/teste-credito")
        self.assertIn(status, (401, 403))
        self.assertEqual(self.linx.chamadas, [])

if __name__ == "__main__":
    unittest.main()
