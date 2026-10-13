"""V18.95 — Conferência separada por filial (Araçatuba e Bauru).

Decisões do dono (10/10): cada usuário tem uma filial; só o perfil Admin pode ter "todas"
(e escolhe a filial num seletor); o FILTRO É NO SERVIDOR. O que não pode quebrar:
- quem é de Bauru não recebe (nem pela API) pedido, etiqueta, histórico ou coleta de
  Araçatuba, e não conclui/trava/exclui pedido de lá;
- a fila filtrada gravada por quem vê uma filial só NUNCA apaga a outra filial — nem vira
  tombstone ADMIN quando quem grava é um Admin com o seletor numa filial;
- "Mover de filial" é decisão do ADMIN: a importação do ML não desfaz;
- a etiqueta continua vindo da CONTA do pedido, mesmo depois de mover.
"""

import json
from unittest import mock

from base import S, SENHA_ADMIN, USUARIO_ADMIN, Cliente, TesteNoromak, gravar_como_integracao, linha, pedidos


def linha_ml(venda: str, sku: str, casa: str = "", **extra) -> dict:
    campos = dict(Marketplace="MERCADO LIVRE", VendaMercadoLivre=venda, ShipmentIdMercadoLivre="SHIP-" + venda,
                  OrigemCadastro="API Mercado Livre", **extra)
    if casa:
        campos.update(Casa=casa, ContaMercadoLivre=casa)
    return linha(venda, sku, uid=f"MLAPI_{venda}_{sku}", **campos)


class Filiais(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha_ml("A1", "SKU-A"), linha_ml("B1", "SKU-B", casa="bauru")],
                               origem="API Mercado Livre")
        self.bauru = self.novo_usuario("lucas", "bauru")

    def novo_usuario(self, login: str, filial: str, perfil: str = "operador") -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), perfil, True, SENHA_ADMIN, "teste")
            S.DB.execute("UPDATE app_users SET casa=? WHERE username=?", (filial, login))
            S.DB.commit()
        cliente = Cliente(self.url_base)
        cliente.entrar(login, SENHA_ADMIN, area="conferencia")
        return cliente

    def ler(self, cliente: Cliente, key: str, filial: str = "") -> object:
        cab = {"X-Noromak-Filial": filial} if filial else {}
        with mock.patch.object(cliente, "pedir", wraps=cliente.pedir):
            status, corpo = self.pedir(cliente, "GET", f"/api/storage?key={key}", cabecalhos=cab)
        self.assertEqual(status, 200, corpo)
        return json.loads(corpo["value"]) if corpo.get("value") else None

    def pedir(self, cliente: Cliente, metodo: str, rota: str, corpo: dict | None = None, cabecalhos: dict | None = None):
        import urllib.request, urllib.error
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(cliente.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        req.add_header("Cookie", cliente.cookie)
        for k, v in (cabecalhos or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                texto = r.read().decode("utf-8")
                return r.status, (json.loads(texto) if texto.strip() else {})
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8")
            try:
                return e.code, json.loads(texto)
            except ValueError:
                return e.code, {"corpo": texto}

    def vendas(self) -> set[str]:
        return {str(r.get("VendaMercadoLivre") or "") for r in pedidos()}

    # ------------------------------------------------------------------ usuários
    def test_migracao_admin_ve_tudo_operador_aracatuba(self):
        with S.DB_LOCK:
            casa = S.DB.execute("SELECT casa FROM app_users WHERE username=?", (USUARIO_ADMIN,)).fetchone()[0]
        self.assertEqual(casa, "todas")
        self.assertEqual(S.usuario_filial({"casa": "todas", "role": "operador"}), "aracatuba",
                         "operador nunca vê todas as filiais")
        with self.assertRaises(ValueError):
            S.filial_normalizar("Todas", "operador")
        self.assertEqual(S.filial_normalizar("Araçatuba", "operador"), "aracatuba")

    def test_tela_de_usuarios_mostra_e_grava_a_filial(self):
        usuarios = {u["Login"]: u for u in S.app_users_payload()}
        self.assertEqual(usuarios["lucas"]["Filial"], "bauru")
        self.assertEqual(usuarios[USUARIO_ADMIN]["Filial"], "todas")
        lista = list(usuarios.values())
        for u in lista:
            if u["Login"] == "lucas":
                u["Filial"] = "aracatuba"
        S.apply_app_users_payload(json.dumps(lista), USUARIO_ADMIN)
        self.assertEqual({u["Login"]: u["Filial"] for u in S.app_users_payload()}["lucas"], "aracatuba")
        for u in lista:
            if u["Login"] == "lucas":
                u["Filial"] = "todas"
        with self.assertRaises(ValueError):
            S.apply_app_users_payload(json.dumps(lista), USUARIO_ADMIN)

    # ------------------------------------------------------------------ leitura
    def test_bauru_so_recebe_a_fila_de_bauru(self):
        fila = self.ler(self.bauru, "conf_pedidos")
        self.assertEqual({r["VendaMercadoLivre"] for r in fila}, {"B1"})
        _, sessao = self.bauru.pedir("GET", "/api/auth/session")
        self.assertEqual(sessao["user"]["casa"], "bauru")
        # Admin sem seletor vê tudo; com o seletor, só a filial escolhida.
        self.assertEqual({r["VendaMercadoLivre"] for r in self.ler(self.cliente, "conf_pedidos")}, {"A1", "B1"})
        self.assertEqual({r["VendaMercadoLivre"] for r in self.ler(self.cliente, "conf_pedidos", "aracatuba")}, {"A1"})
        # Operador não escolhe filial pelo cabeçalho.
        self.assertEqual({r["VendaMercadoLivre"] for r in self.ler(self.bauru, "conf_pedidos", "aracatuba")}, {"B1"})

    def test_varias_chaves_de_uma_vez_tambem_filtram(self):
        status, corpo = self.pedir(self.bauru, "GET", "/api/storage?keys=conf_pedidos,conf_produtos")
        self.assertEqual(status, 200, corpo)
        self.assertEqual({r["VendaMercadoLivre"] for r in json.loads(corpo["values"]["conf_pedidos"])}, {"B1"})

    # ------------------------------------------------------------------ gravação
    def test_gravacao_de_bauru_nao_apaga_aracatuba(self):
        fila = self.ler(self.bauru, "conf_pedidos")
        antigo = json.dumps(fila)
        fila[0]["Conferido"] = "1"
        fila.append(linha("MANUAL-B", "SKU-M", uid="MAN-B"))
        status, corpo = self.pedir(self.bauru, "POST", "/api/storage",
                                   {"key": "conf_pedidos", "oldValue": antigo, "newValue": json.dumps(fila)})
        self.assertEqual(status, 200, corpo)
        self.assertEqual({r["VendaMercadoLivre"] for r in json.loads(corpo["value"]) if r.get("VendaMercadoLivre")}, {"B1"},
                         "a resposta da gravação vazou a outra filial")
        self.assertIn("A1", self.vendas(), "REGRESSÃO: a gravação de Bauru apagou o pedido de Araçatuba")
        manual = [r for r in pedidos() if r.get("Pedido") == "MANUAL-B"]
        self.assertEqual(manual[0].get("Casa"), "bauru", "linha incluída em Bauru tem de nascer de Bauru")

    def test_admin_com_seletor_nao_vira_tombstone_da_outra_filial(self):
        fila = self.ler(self.cliente, "conf_pedidos", "bauru")
        status, corpo = self.pedir(self.cliente, "POST", "/api/storage",
                                   {"key": "conf_pedidos", "oldValue": json.dumps(fila), "newValue": json.dumps(fila),
                                    "allowPendingDeletes": True}, cabecalhos={"X-Noromak-Filial": "bauru"})
        self.assertEqual(status, 200, corpo)
        self.assertIn("A1", self.vendas(), "REGRESSÃO: o Admin com o seletor em Bauru apagou Araçatuba")
        with S.DB_LOCK:
            tomb = S.DB.execute("SELECT COUNT(*) FROM admin_manual_overrides WHERE storage_key='conf_pedidos' "
                                "AND field_name='__ENTITY__' AND is_deleted=1").fetchone()[0]  # V19.00: era LIKE '%deleted%', que nunca casava
        self.assertEqual(tomb, 0, "virou tombstone ADMIN de pedido que o Admin nem via")

    # ------------------------------------------------------------------ rotas de pedido
    def test_bauru_nao_conclui_nem_trava_pedido_de_aracatuba(self):
        for rota, corpo in (("/api/order-complete", {"pedido": "A1"}),
                            ("/api/order-lock", {"action": "acquire", "pedido": "A1", "clientId": "x"}),
                            ("/api/order-conference-name", {"pedido": "A1", "nome": "Fulano"})):
            status, resposta = self.pedir(self.bauru, "POST", rota, corpo)
            self.assertEqual(status, 403, (rota, resposta))
        self.assertIn("A1", self.vendas())
        status, resposta = self.pedir(self.bauru, "POST", "/api/order-complete", {"pedido": "B1"})
        self.assertEqual(status, 200, resposta)
        self.assertNotIn("A1", resposta.get("value") or "", "a resposta da conclusão vazou a outra filial")

    def test_painel_do_dia_conta_so_a_filial(self):
        self.assertEqual(self.pedir(self.cliente, "POST", "/api/order-complete", {"pedido": "A1"})[0], 200)
        self.assertEqual(self.pedir(self.cliente, "POST", "/api/order-complete", {"pedido": "B1"})[0], 200)
        status, corpo = self.pedir(self.bauru, "GET", "/api/conferencia/conclusoes-dia")
        self.assertEqual([c["pedido"] for c in corpo["conclusoes"]], ["B1"])

    # ------------------------------------------------------------------ etiquetas, histórico, coleta
    def test_etiquetas_por_filial(self):
        banco = {"version": 8, "registros": {
            "e1": {"id": "e1", "marketplace": "mercado_livre", "pedido": "A1", "chaves": ["A1"], "envio": "^XA^XZ"},
            "e2": {"id": "e2", "marketplace": "mercado_livre", "pedido": "B1", "chaves": ["B1"], "envio": "^XA^XZ"}},
            "ordem": ["e1", "e2"]}
        S.save_storage("conf_etiquetas", None, json.dumps(banco), True, actor="teste", actor_role="sistema")
        visto = self.ler(self.bauru, "conf_etiquetas")
        self.assertEqual(set(visto["registros"]), {"e2"})
        visto["registros"]["e3"] = {"id": "e3", "marketplace": "mercado_livre", "pedido": "B9", "chaves": ["B9"], "envio": "^XA^XZ"}
        status, _ = self.pedir(self.bauru, "POST", "/api/storage", {"key": "conf_etiquetas", "newValue": json.dumps(visto),
                                                                    "allowLabelDeletes": True})
        self.assertEqual(status, 200)
        todos = json.loads(S.get_storage("conf_etiquetas")[0])["registros"]
        self.assertEqual(set(todos), {"e1", "e2", "e3"}, "a etiqueta de Araçatuba sumiu")
        self.assertEqual(todos["e3"].get("casa"), "bauru")

    def test_historico_por_filial(self):
        S.save_storage("conf_historico_conferencias_v2", None, json.dumps([{"id": "h1", "pedido": "A0"}]), True,
                       actor="teste", actor_role="sistema")
        self.assertEqual(self.ler(self.bauru, "conf_historico_conferencias_v2"), [])
        status, _ = self.pedir(self.bauru, "POST", "/api/storage", {"key": "conf_historico_conferencias_v2",
                                                                    "newValue": json.dumps([{"id": "h2", "pedido": "B1"}])})
        self.assertEqual(status, 200)
        todos = json.loads(S.get_storage("conf_historico_conferencias_v2")[0])
        self.assertEqual({h["id"]: h.get("Casa") for h in todos}, {"h1": None, "h2": "bauru"})

    def test_coleta_por_filial(self):
        S.save_storage("conf_coleta_dia_v1", None, json.dumps({"horario": "10h", "codigo": "ARA"}), True,
                       actor="teste", actor_role="sistema")
        self.assertEqual(self.ler(self.bauru, "conf_coleta_dia_v1"), {})
        status, _ = self.pedir(self.bauru, "POST", "/api/storage",
                               {"key": "conf_coleta_dia_v1", "newValue": json.dumps({"horario": "14h", "codigo": "BAU"})})
        self.assertEqual(status, 200)
        self.assertEqual(self.ler(self.bauru, "conf_coleta_dia_v1")["codigo"], "BAU")
        self.assertEqual(self.ler(self.cliente, "conf_coleta_dia_v1", "aracatuba"), {"horario": "10h", "codigo": "ARA"})
        # Araçatuba grava a dela e não apaga a de Bauru.
        self.pedir(self.cliente, "POST", "/api/storage",
                   {"key": "conf_coleta_dia_v1", "newValue": json.dumps({"horario": "11h", "codigo": "ARA2"})})
        self.assertEqual(self.ler(self.bauru, "conf_coleta_dia_v1")["codigo"], "BAU")

    # ------------------------------------------------------------------ mover de filial e PDF
    def test_mover_de_filial_e_decisao_do_admin(self):
        status, corpo = self.pedir(self.cliente, "POST", "/api/pedido-mover-filial", {"pedido": "B1", "filial": "aracatuba"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(self.ler(self.bauru, "conf_pedidos"), [])
        # A integração do ML regrava a linha com Casa=bauru: a decisão do ADMIN prevalece.
        gravar_como_integracao([linha_ml("A1", "SKU-A"), linha_ml("B1", "SKU-B", casa="bauru", Status="novo")],
                               origem="API Mercado Livre")
        linha_b = [r for r in pedidos() if r.get("VendaMercadoLivre") == "B1"][0]
        self.assertEqual(linha_b.get("Casa"), "aracatuba", "a importação desfez o Mover de filial do ADMIN")
        self.assertEqual(S.ml_casa_da_linha(linha_b), "bauru", "a etiqueta tem de continuar vindo da conta de Bauru")
        self.assertEqual(self.pedir(self.bauru, "POST", "/api/pedido-mover-filial", {"pedido": "A1", "filial": "bauru"})[0], 403)

    def test_etiqueta_em_pdf_com_a_conta_do_pedido(self):
        contas = []

        def baixar(caminho, **k):
            contas.append((S.ml_casa_atual(), caminho))
            return b"%PDF-1.4 teste", "application/pdf"
        with mock.patch.object(S, "ml_api_get_bytes", side_effect=baixar):
            import urllib.request
            req = urllib.request.Request(self.bauru.url_base + "/api/etiqueta-ml-pdf?pedido=B1")
            req.add_header("Cookie", self.bauru.cookie)
            with urllib.request.urlopen(req, timeout=30) as r:
                self.assertEqual(r.headers.get("Content-Type"), "application/pdf")
                self.assertTrue(r.read().startswith(b"%PDF"))
            status, _ = self.pedir(self.bauru, "GET", "/api/etiqueta-ml-pdf?pedido=A1")
            self.assertEqual(status, 403)
        self.assertEqual(contas[0][0], "bauru")
        self.assertIn("response_type=pdf", contas[0][1])


class TelasFilial(TesteNoromak):

    def test_telas_trazem_a_filial(self):
        from base import RAIZ
        app = RAIZ / "APP"
        ss = (app / "shared-storage.js").read_text(encoding="utf-8")
        self.assertIn("X-Noromak-Filial", ss)
        self.assertIn("trocarFilial", ss)
        menu = (app / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('data-nk-filial="1"', menu)
        conf = (app / "conferencia.html").read_text(encoding="utf-8")
        self.assertIn("imprimirEtiquetaPdfV1895", conf)
        self.assertIn("/api/etiqueta-ml-pdf", conf)
        self.assertRegex(conf, r"shared-storage\.js\?v=19\.\d+")   # V19.16: a versão sobe a cada mudança do shared-storage
        admin = (app / "admin.html").read_text(encoding="utf-8")
        self.assertIn("moverPedidoFilialAdminV1895", admin)
        self.assertIn('id="novoUsuarioFilial"', admin)
        self.assertIn('data-campo="Filial"', admin)


class UsuarioFilialV1896(TesteNoromak):
    """V18.96 — produção 10/10: o Lucas criado por Solicitar → Aprovar ficava em Araçatuba e a
    escolha de Bauru na lista "voltava" (a lista era trocada a cada mudança da fila)."""

    def test_aprovacao_grava_a_filial(self):
        lista = S.app_users_payload()
        lista.append({"Nome": "Lucas", "Login": "lucas", "Senha": "abc12345", "Perfil": "Operador",
                      "Filial": "bauru", "Ativo": True})
        S.apply_app_users_payload(json.dumps(lista), USUARIO_ADMIN)
        self.assertEqual({u["Login"]: u["Filial"] for u in S.app_users_payload()}["lucas"], "bauru")

    def test_tela_nao_troca_a_lista_oficial(self):
        from base import RAIZ
        admin = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('if(!window.__usrCanonicoV1896) usuariosEditorAdmin=', admin)
        self.assertIn('id="filial-aprovacao-', admin)
        self.assertIn("Filial:filialAprovada", admin)
