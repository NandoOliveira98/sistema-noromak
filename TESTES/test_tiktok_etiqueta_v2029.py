"""V20.29 — TikTok Shop (Bauru): etiqueta automática (organizar envio + PDF), cancelamento.

Decisões do dono (06/11): o servidor organiza o envio sozinho e a Conferência de Bauru imprime a
etiqueta em PDF; a nota sai pelo Linx e o Noromak só espera (AGUARDANDO NF); a etiqueta
automática começa DESLIGADA (é a única ação que grava no TikTok); cancelado sai da fila com a
regra do ML (a linha do ADMIN fica, concluído não volta).
"""

import json
import time
from pathlib import Path
from unittest import mock

from base import S, TesteNoromak

DOC_URL = "https://p16-oec.tiktokcdn.com/etiqueta.pdf"


def _conectar():
    agora = int(time.time())
    S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                "access_token_expire_in": agora + 86400,
                                                "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
    with S.DB_LOCK:
        S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
        S.DB.commit()


def _pedido(numero, status="AWAITING_SHIPMENT", itens=(("SK1", "0893600022"),), **extra):
    p = {"id": numero, "status": status, "create_time": 1760000000, "rts_sla_time": 1760090000,
         "recipient_address": {"name": "Cliente Teste"}, "packages": [{"id": f"PK{numero}"}],
         "line_items": [{"id": f"{numero}-{i}", "sku_id": s, "seller_sku": c, "product_name": "Desengripante",
                         "display_status": status} for i, (s, c) in enumerate(itens)]}
    p.update(extra)
    return p


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        _conectar()
        S._TIKTOK_ETIQUETA_TENTATIVA.clear()
        S._TIKTOK_ETIQUETA_ESTADO.clear()
        S._TIKTOK_ETIQUETA_ORGANIZADO.clear()
        self.enviados = []
        self.erro_ship = None
        self.doc_url = DOC_URL

    def _api(self, pedidos=None):
        teste = self

        def falso(metodo, path, params=None, body=None, loja=True):
            teste.enviados.append((metodo, path, params, body))
            if path == "/order/202309/orders/search":
                st = (body or {}).get("order_status")
                return {"code": 0, "data": {"orders": [o for o in (pedidos or []) if o.get("status") == st]}}
            if path.endswith("/ship"):
                if teste.erro_ship:
                    raise teste.erro_ship
                return {"code": 0, "data": {}}
            if path.endswith("/shipping_documents"):
                return {"code": 0, "data": {"doc_url": teste.doc_url}}
            return {"code": 0, "data": {}}
        return mock.patch.object(S, "tiktok_api", side_effect=falso)

    def _chamadas(self, fim):
        return [e for e in self.enviados if e[1].endswith(fim)]

    def _fila(self):
        bruto, _, _ = S.get_storage("conf_pedidos")
        return S._rows_from_storage_value(bruto)

    def _linha(self, pedido):
        return next(l for l in self._fila() if l.get("Pedido") == pedido)

    def _etiquetas_tiktok(self):
        bruto, _, _ = S.get_storage("conf_etiquetas")
        bank = json.loads(bruto) if bruto else {}
        return [r for r in (bank.get("registros") or {}).values() if r.get("marketplace") == "tiktok"]

    def _importar(self, pedidos):
        with self._api(pedidos):
            return S.tiktok_importar_pedidos()


class Etiqueta(Base):

    def test_comeca_desligada_e_desligada_nao_chama_o_ship(self):
        self.assertFalse(S.tiktok_etiqueta_ligada())
        with self._api([_pedido("TT1")]):
            S.tiktok_rodada()
        self.assertFalse(self._chamadas("/ship"))
        self.assertEqual(self._linha("TT1").get("Etiqueta API TikTok", ""), "")

    def test_ligada_organiza_e_vincula(self):
        S.tiktok_etiqueta_definir(True, "dono")
        with self._api([_pedido("TT1")]):
            S.tiktok_rodada()
        ship = self._chamadas("/ship")
        self.assertEqual(len(ship), 1)
        self.assertEqual(ship[0][1], "/fulfillment/202309/packages/PKTT1/ship")
        regs = self._etiquetas_tiktok()
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["envio"], "TIKTOK_PDF:PKTT1")
        self.assertEqual(regs[0]["pedido"], "TT1")
        linha = self._linha("TT1")
        self.assertEqual(linha["Etiqueta API TikTok"], "VINCULADA")
        self.assertEqual(linha["PacoteTikTok"], "PKTT1")

    def test_ja_vinculado_nao_organiza_de_novo(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 1)
        self.assertEqual(len(self._etiquetas_tiktok()), 1)

    def test_aguardando_collection_nao_chama_ship_so_documento(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1", status="AWAITING_COLLECTION")])
        self.assertFalse(self._chamadas("/ship"))
        self.assertEqual(len(self._chamadas("/shipping_documents")), 1)
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "VINCULADA")

    def test_sem_nota_mostra_aguardando_nf_e_nao_repete_antes_de_600s(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self.erro_ship = RuntimeError("TikTok /ship: 21011001 invoice not uploaded")
        self._importar([_pedido("TT1")])
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "AGUARDANDO NF")
        self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 1, "sem NF não pode virar uma chamada a cada volta")
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "AGUARDANDO NF")
        depois = time.time() + 601
        with mock.patch.object(S.time, "time", return_value=depois):
            self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 2)

    def test_outro_erro_marca_erro_e_nao_trava_a_importacao(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self.erro_ship = RuntimeError("TikTok HTTP 500: falhou")
        r = self._importar([_pedido("TT1")])
        self.assertEqual(r["etiquetas"]["erros"], 1)
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "ERRO")

    def test_edicao_do_admin_fica_e_so_campos_operacionais_mudam(self):
        self._importar([_pedido("TT1")])
        linhas = self._fila()
        for l in linhas:
            if l.get("Pedido") == "TT1":
                l["Nome Conferência"] = "NOME DO ADMIN"
        self.salvar_admin(linhas)
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        linha = self._linha("TT1")
        self.assertEqual(linha.get("Nome Conferência"), "NOME DO ADMIN")
        self.assertEqual(linha["Etiqueta API TikTok"], "VINCULADA")


def _get(cliente, rota):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(cliente.url_base + rota)
    req.add_header("Cookie", cliente.cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


class Pdf(Base):

    def setUp(self) -> None:
        super().setUp()
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        self.enviados.clear()

    def _usuario(self, login, filial):
        from base import SENHA_ADMIN, Cliente
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), "operador", True, SENHA_ADMIN, "teste")
            S.DB.execute("UPDATE app_users SET casa=? WHERE username=?", (filial, login))
            S.DB.commit()
        cliente = Cliente(self.url_base)
        cliente.entrar(login, SENHA_ADMIN, area="conferencia")
        return cliente

    def test_baixa_o_link_novo_a_cada_impressao(self):
        with self._api([_pedido("TT1")]), \
                mock.patch.object(S, "_tiktok_baixar_pdf", return_value=b"%PDF-1.4 teste") as baixar:
            self.assertTrue(S.tiktok_etiqueta_pdf("TT1", "bauru").startswith(b"%PDF"))
            S.tiktok_etiqueta_pdf("TT1", "bauru")
        self.assertEqual(len(self._chamadas("/shipping_documents")), 2, "o link do TikTok vence: pedir de novo a cada impressão")
        self.assertEqual(baixar.call_args[0][0], DOC_URL)

    def test_link_fora_do_tiktok_recusado_sem_baixar(self):
        for url in ("https://evil.example.com/x.pdf", "http://p16-oec.tiktokcdn.com/x.pdf"):
            self.doc_url = url
            with self._api([_pedido("TT1")]), mock.patch.object(S, "_tiktok_baixar_pdf") as baixar:
                with self.assertRaises(RuntimeError):
                    S.tiktok_etiqueta_pdf("TT1", "bauru")
            baixar.assert_not_called()

    def test_nao_pdf_recusado(self):
        with self._api([_pedido("TT1")]), mock.patch.object(S, "_tiktok_baixar_pdf", return_value=b"<html>"):
            with self.assertRaises(RuntimeError):
                S.tiktok_etiqueta_pdf("TT1", "bauru")

    def test_outra_filial_403(self):
        with self.assertRaises(PermissionError):
            S.tiktok_etiqueta_pdf("TT1", "aracatuba")
        aracatuba = self._usuario("ana", "aracatuba")
        status, _, corpo = _get(aracatuba, "/api/etiqueta-tiktok-pdf?pedido=TT1")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(corpo).get("code"), "OUTRA_FILIAL")

    def test_bauru_baixa_pela_rota(self):
        bauru = self._usuario("lucas", "bauru")
        with self._api([_pedido("TT1")]), mock.patch.object(S, "_tiktok_baixar_pdf", return_value=b"%PDF-1.4 x"):
            status, tipo, corpo = _get(bauru, "/api/etiqueta-tiktok-pdf?pedido=TT1")
        self.assertEqual(status, 200)
        self.assertEqual(tipo, "application/pdf")
        self.assertTrue(corpo.startswith(b"%PDF"))

    def test_fora_da_fila_404(self):
        with self.assertRaises(ValueError):
            S.tiktok_etiqueta_pdf("NAOEXISTE", "bauru")
        status, _, _ = _get(self.cliente, "/api/etiqueta-tiktok-pdf?pedido=NAOEXISTE")
        self.assertEqual(status, 404)


class TelaConferencia(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self.html = (Path(S.__file__).resolve().parent / "APP" / "conferencia.html").read_text(encoding="utf-8")

    def _funcao(self, nome):
        inicio = self.html.index(f"function {nome}(")
        return self.html[inicio:self.html.index("\n}\n", inicio)]

    def test_conferencia_imprime_tiktok_pelo_pdf(self):
        self.assertIn('includes("tiktok")) return "tiktok"', self._funcao("normalizarMarketplace"))
        self.assertIn("/expedicao/api/etiqueta-tiktok-pdf", self.html)
        fallback = self._funcao("imprimirDadosComFallback")
        self.assertIn('=== "tiktok"', fallback)
        self.assertLess(fallback.index('=== "tiktok"'), fallback.index('=== "bauru"'),
                        "o TikTok imprime em PDF em qualquer filial, antes da regra do ML de Bauru")
        self.assertIn('marketplace === "tiktok"', self._funcao("prepararDadosImpressaoMarketplace"))
        self.assertIn('"TikTok Shop"', self._funcao("nomeMarketplace"))
        self.assertNotIn("nkToast", self.html, "a Conferência fica com alert() (trava o leitor de propósito)")


class Cancelamento(Base):

    def _pedidos(self):
        return {l.get("Pedido") for l in self._fila()}

    def test_cancelado_sai_da_fila(self):
        self._importar([_pedido("TT1"), _pedido("TT2")])
        self._importar([_pedido("TT2")])
        self.assertNotIn("TT1", self._pedidos())
        self.assertIn("TT2", self._pedidos())

    def test_linha_protegida_pelo_admin_fica(self):
        self._importar([_pedido("TT1")])
        linhas = self._fila()
        for l in linhas:
            if l.get("Pedido") == "TT1":
                l["ManualAdminProtegido"] = "SIM"  # regra do _admin_manual_pending_protected (V17.51/V18.52)
        self.salvar_admin(linhas)
        self._importar([])
        self.assertIn("TT1", self._pedidos(), "a linha protegida pelo ADMIN não pode sair pela integração")

    def test_concluido_nao_volta(self):
        self._importar([_pedido("TT1")])
        status, corpo = self.cliente.pedir("POST", "/api/order-complete", {"pedido": "TT1"})
        self.assertEqual(status, 200, corpo)
        self._importar([_pedido("TT1")])
        self.assertNotIn("TT1", self._pedidos())

    def test_unidade_cancelada_baixa_a_quantidade(self):
        p = _pedido("TT1", itens=(("SK1", "0893600022"), ("SK1", "0893600022")))
        p["line_items"][1]["display_status"] = "CANCELLED"
        self._importar([p])
        self.assertEqual(self._linha("TT1")["Quantidade"], "1")

    def test_cancelado_depois_de_vinculado_nao_imprime(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        self._importar([])
        with self.assertRaises(ValueError):
            S.tiktok_etiqueta_pdf("TT1", "bauru")

    def test_pedido_com_cancelamento_pedido_nao_organiza_envio(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1", cancel_reason="Buyer requested")])
        self.assertFalse(self._chamadas("/ship"))
        self.assertFalse(self._chamadas("/shipping_documents"))
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "AGUARDANDO")


class Rotas(Base):

    def test_ligar_etiqueta_so_perfil_admin(self):
        from base import SENHA_ADMIN, Cliente
        with S.DB_LOCK:
            S.upsert_app_user("supervisor_t", "Supervisor T", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("supervisor_t", SENHA_ADMIN, area="admin")
        st, _ = sup.pedir("POST", "/api/integracoes/tiktok/etiqueta", {"ligada": True})
        self.assertEqual(st, 403)
        self.assertFalse(S.tiktok_etiqueta_ligada())
        st, corpo = self.cliente.pedir("POST", "/api/integracoes/tiktok/etiqueta", {"ligada": True})
        self.assertEqual(st, 200, corpo)
        self.assertTrue(S.tiktok_etiqueta_ligada())
        self.assertTrue(corpo["etiqueta"])

    def test_status_mostra_a_chave(self):
        self.assertFalse(S.tiktok_status_payload()["etiqueta_ligada"])
        S.tiktok_etiqueta_definir(True, "dono")
        self.assertTrue(S.tiktok_status_payload()["etiqueta_ligada"])

    def test_tela_tem_o_botao(self):
        html = (Path(S.__file__).resolve().parent / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertTrue("/expedicao/api/integracoes/tiktok/etiqueta" in html, "falta a chamada da rota")
        self.assertTrue('id="btnTiktokEtiqueta"' in html, "falta o botão")


class RevisaoFinal(Base):
    """Achados da revisão final (06/11)."""

    def test_nao_organiza_envio_de_pedido_excluido_pelo_admin_nem_concluido(self):
        self._importar([_pedido("TT1"), _pedido("TT2")])
        self.excluir_admin("TT1", marketplace="TIKTOK")
        st, corpo = self.cliente.pedir("POST", "/api/order-complete", {"pedido": "TT2"})
        self.assertEqual(st, 200, corpo)
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1"), _pedido("TT2"), _pedido("TT3")])
        ship = [e[1] for e in self._chamadas("/ship")]
        self.assertEqual(ship, ["/fulfillment/202309/packages/PKTT3/ship"],
                         "o ADMIN excluiu/concluiu: nada pode ser gravado no TikTok por esses pedidos")

    def test_envio_ja_organizado_nao_e_organizado_de_novo(self):
        S.tiktok_etiqueta_definir(True, "dono")
        teste = self
        falhar_doc = {"sim": True}

        def falso(metodo, path, params=None, body=None, loja=True):
            teste.enviados.append((metodo, path, params, body))
            if path == "/order/202309/orders/search":
                st = (body or {}).get("order_status")
                return {"code": 0, "data": {"orders": [o for o in [_pedido("TT1")] if o.get("status") == st]}}
            if path.endswith("/shipping_documents") and falhar_doc["sim"]:
                raise RuntimeError("TikTok: invoice not uploaded")
            if path.endswith("/shipping_documents"):
                return {"code": 0, "data": {"doc_url": DOC_URL}}
            return {"code": 0, "data": {}}
        with mock.patch.object(S, "tiktok_api", side_effect=falso):
            S.tiktok_importar_pedidos()
            falhar_doc["sim"] = False
            with mock.patch.object(S.time, "time", return_value=time.time() + 601):
                S.tiktok_importar_pedidos()
        self.assertEqual(len(self._chamadas("/ship")), 1, "envio já organizado: só falta o documento")
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "VINCULADA")

    def test_amostra_uma_por_rota_sem_o_id(self):
        with mock.patch.object(S, "_tiktok_http", return_value={"code": 0, "data": {}}):
            S.tiktok_api("POST", "/fulfillment/202309/packages/PK1/ship", body={})
            S.tiktok_api("POST", "/fulfillment/202309/packages/PK2/ship", body={})
            S.tiktok_api("GET", "/finance/202501/orders/57712345/statement_transactions")
        with S.DB_LOCK:
            tipos = [r[0] for r in S.DB.execute("SELECT tipo FROM tiktok_amostra_v1963").fetchall()]
        self.assertIn("POST /fulfillment/202309/packages/{id}/ship", tipos)
        self.assertEqual(len([t for t in tipos if "/ship" in t]), 1)
        self.assertIn("GET /finance/202501/orders/{id}/statement_transactions", tipos)


KIT = "0893600022/0893764272"


class RevisaoAntesDeSubir(Base):
    """Achados da revisar-antes-de-subir (06/11)."""

    def _cadastrar_kit(self):
        atual, _, _ = S.get_storage("conf_kits")
        kits = [{"sku_kit": KIT, "nome": "Kit teste", "ativo": True,
                 "componentes": [{"sku": "0893600022", "quantidade": 1}, {"sku": "0893764272", "quantidade": 1}]}]
        S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                       source_screen="teste", change_method="teste", client_ip="127.0.0.1")

    def test_barreiras_do_admin_lidas_sob_a_trava(self):
        S.tiktok_etiqueta_definir(True, "dono")
        vistos = []
        original = S.filter_completed_orders_v1772

        def espiao(valor):
            vistos.append(S.DB_LOCK._lock._is_owned())
            return original(valor)
        with mock.patch.object(S, "filter_completed_orders_v1772", side_effect=espiao):
            self._importar([_pedido("TT1")])
        self.assertTrue(vistos and all(vistos), "a barreira do ADMIN leu o banco sem o DB_LOCK")

    def test_falha_ao_ler_as_barreiras_nao_organiza_e_nao_derruba_a_importacao(self):
        S.tiktok_etiqueta_definir(True, "dono")
        original = S.filter_completed_orders_v1772
        chamadas = []

        def falha_na_barreira(valor):  # a 1ª chamada é a barreira da etiqueta; as outras, a gravação
            chamadas.append(1)
            if len(chamadas) == 1:
                raise RuntimeError("banco ocupado")
            return original(valor)
        with mock.patch.object(S, "filter_completed_orders_v1772", side_effect=falha_na_barreira):
            r = self._importar([_pedido("TT1")])
        self.assertFalse(self._chamadas("/ship"), "sem saber o que o ADMIN tirou, não grava no TikTok")
        self.assertIn("TT1", {l.get("Pedido") for l in self._fila()}, "a importação continua")
        self.assertEqual(r["etiquetas"]["erros"], 1)

    def test_kit_excluido_linha_a_linha_nao_organiza_envio(self):
        self._cadastrar_kit()
        self._importar([_pedido("TT1", itens=(("SK9", KIT),))])
        linhas = [l for l in self._fila() if l.get("Pedido") == "TT1"]
        self.assertEqual(len(linhas), 2, "o kit tem de abrir em duas peças")
        for l in linhas:
            st, corpo = self.cliente.pedir("POST", "/api/order-line-delete", {
                "pedido": "TT1", "uid": l["__uidLinha"], "uidLinha": l["__uidLinha"], "sku": l["SKU"],
                "marketplace": "TIKTOK"})
            self.assertEqual(st, 200, corpo)
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1", itens=(("SK9", KIT),))])
        self.assertFalse(self._chamadas("/ship"), "o ADMIN excluiu as linhas do kit: nada no TikTok")

    def test_sincronizar_roda_em_segundo_plano_e_recusa_quando_ja_roda(self):
        with S.TIKTOK_SYNC_LOCK:
            st, corpo = self.cliente.pedir("POST", "/api/integracoes/tiktok/sincronizar", {})
        self.assertEqual(st, 409, corpo)
        with self._api([_pedido("TT1")]):
            st, corpo = self.cliente.pedir("POST", "/api/integracoes/tiktok/sincronizar", {})
            self.assertEqual(st, 200, corpo)
            self.assertTrue(corpo.get("iniciado"))
            for _ in range(50):
                if any(l.get("Pedido") == "TT1" for l in self._fila()):
                    break
                time.sleep(0.1)
        self.assertIn("TT1", {l.get("Pedido") for l in self._fila()})
        html = (Path(S.__file__).resolve().parent / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertTrue("Sincronização iniciada" in html, "a tela tem de dizer que começou em segundo plano")

    def test_amostra_guarda_tambem_a_primeira_resposta_boa(self):
        respostas = iter([{"code": 21011001, "message": "invoice not uploaded"},
                          {"code": 0, "data": {"doc_url": DOC_URL}}])
        with mock.patch.object(S, "_tiktok_http", side_effect=lambda *a, **k: next(respostas)):
            with self.assertRaises(RuntimeError):
                S.tiktok_api("GET", "/fulfillment/202309/packages/PK1/shipping_documents")
            S.tiktok_api("GET", "/fulfillment/202309/packages/PK2/shipping_documents")
        with S.DB_LOCK:
            tipos = {r[0]: r[1] for r in S.DB.execute("SELECT tipo, resposta FROM tiktok_amostra_v1963").fetchall()}
        ok = tipos.get("GET /fulfillment/202309/packages/{id}/shipping_documents")
        self.assertTrue(ok and "doc_url" in ok, f"a resposta boa tem de ficar guardada: {list(tipos)}")
        self.assertTrue(any(t.endswith("[erro]") for t in tipos))

    def test_link_de_outro_dominio_nao_vira_vinculada(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self.doc_url = "https://documentos.outro-dominio.com/etiqueta.pdf"
        self._importar([_pedido("TT1")])
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "ERRO")
        self.assertFalse(self._etiquetas_tiktok(), "etiqueta que a Conferência não consegue baixar não é vinculada")
