"""V20.23 - Anuncios fase 1: controle do anuncio, payloads e rotas."""

from unittest import mock

import urllib.request

from base import RAIZ, S, SENHA_ADMIN, TesteNoromak, Cliente, linha

SENHA = "SenhaTeste#2023"


def inserir(canal="ml", conta="aracatuba", anuncio_id="MLB1", ficha="P1", controle="alephee", logistica="", removido=""):
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT OR REPLACE INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
            "logistica,removido_em) VALUES(?,?,?,?,?,?,?,?,?)",
            (canal, conta, anuncio_id, ficha, ficha, controle, "T " + anuncio_id, logistica, removido))
        S.DB.commit()


class RotasAnunciosTeste(TesteNoromak):

    def test_lista_e_ficha_admin(self):
        S.ficha_salvar("P1", {"nome": "Peca um"}, "luis", None)
        inserir()
        st, c = self.cliente.pedir("GET", "/api/anuncios")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["fichas"][0]["codigo"], "P1")
        self.assertEqual(c["fichas"][0]["anuncios"], 1)
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=p1")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["ficha"]["campos"]["nome"], "Peca um")
        self.assertEqual(len(c["anuncios"]), 1)
        self.assertEqual(self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=NAOEXISTE")[0], 404)

    def test_filtros_e_busca(self):
        S.ficha_salvar("P1", {"nome": "Peca um"}, "luis", None)
        inserir(ficha="SEMFICHA")
        st, c = self.cliente.pedir("GET", "/api/anuncios?filtro=sem_ficha")
        self.assertEqual([f["codigo"] for f in c["fichas"]], ["SEMFICHA"])
        st, c = self.cliente.pedir("GET", "/api/anuncios?busca=peca")
        self.assertEqual([f["codigo"] for f in c["fichas"]], ["P1"])

    def test_supervisor_ve_mas_nao_grava(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_2023", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2023", SENHA, area="admin")
        self.assertEqual(sup.pedir("GET", "/api/anuncios")[0], 200)
        st, _ = sup.pedir("POST", "/api/anuncios/ficha", {"codigo": "P9", "campos": {"nome": "x"}})
        self.assertEqual(st, 403)

    def test_operador_sem_admin_403(self):
        with S.DB_LOCK:
            S.upsert_app_user("op_2023", "Operador", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.DB.commit()
        op = Cliente(self.url_base)
        op.entrar("op_2023", SENHA, area="conferencia")
        self.assertEqual(op.pedir("GET", "/api/anuncios")[0], 403)
        self.assertEqual(op.pedir("GET", "/api/anuncios/ficha?codigo=P1")[0], 403)

    def test_consulta_nao_grava(self):
        with S.DB_LOCK:
            S.upsert_app_user("con_2023", "Consulta", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.execute("UPDATE app_users SET somente_leitura=1 WHERE username='con_2023'")
            S.DB.commit()
        con = Cliente(self.url_base)
        con.entrar("con_2023", SENHA, area="admin")
        self.assertEqual(con.pedir("GET", "/api/anuncios")[0], 200)
        st, _ = con.pedir("POST", "/api/anuncios/puxar", {})
        self.assertEqual(st, 403)

    def test_puxar_um_por_vez(self):
        self.assertTrue(S.ANUNCIOS_PUXAR_LOCK.acquire(blocking=False))
        try:
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {})
            self.assertEqual(st, 409, c)
        finally:
            S.ANUNCIOS_PUXAR_LOCK.release()

    def test_conflito_409(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P1", "campos": {"nome": "A"}})
        self.assertEqual(st, 200, c)
        v = c["ficha"]["versao"]
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P1", "campos": {"nome": "B"}, "versao": v})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P1", "campos": {"nome": "C"}, "versao": v})
        self.assertEqual(st, 409, c)
        self.assertEqual(c["error"], "CONFLITO")
        self.assertEqual(c["atual"]["campos"]["nome"], "B")
        # sem versao numa ficha que ja existe tambem e conflito
        st, _ = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P1", "campos": {"nome": "D"}})
        self.assertEqual(st, 409)

    def test_medida_invalida_400(self):
        for ruim in (0, -3, "abc", "Infinity", "nan"):
            st, _ = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P2", "campos": {"peso_g": ruim}})
            self.assertEqual(st, 400, ruim)
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "P2", "campos": {"peso_g": "12,5", "altura_cm": ""}})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["ficha"]["campos"]["peso_g"], 12.5)
        self.assertNotIn("altura_cm", c["ficha"]["campos"])

    def test_passar_para_noromak_exige_confirmacao(self):
        inserir()
        inserir(anuncio_id="MLBF", logistica="fulfillment", controle="full")
        base = {"canal": "ml", "conta": "aracatuba", "anuncio_id": "MLB1", "controle": "noromak"}
        st, c = self.cliente.pedir("POST", "/api/anuncios/controle", base)
        self.assertEqual(st, 400, c)
        st, c = self.cliente.pedir("POST", "/api/anuncios/controle", {**base, "confirmou_alephee": True})
        self.assertEqual(st, 200, c)
        with S.DB_LOCK:
            row = S.DB.execute("SELECT controle,controle_por FROM anuncio_canal_v2023 WHERE anuncio_id='MLB1'").fetchone()
        self.assertEqual((row["controle"], row["controle_por"] != ""), ("noromak", True))
        st, c = self.cliente.pedir("POST", "/api/anuncios/controle",
                                   {**base, "anuncio_id": "MLBF", "confirmou_alephee": True})
        self.assertEqual(st, 400, c)

    def test_misto(self):
        inserir(anuncio_id="MLB1", controle="alephee")
        inserir(anuncio_id="MLB2", controle="noromak")
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1")
        self.assertTrue(c["misto"])
        # contas diferentes nao sao mistas
        inserir(anuncio_id="MLB2", conta="bauru", controle="noromak")
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM anuncio_canal_v2023 WHERE anuncio_id='MLB2' AND conta='aracatuba'")
            S.DB.commit()
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1")
        self.assertFalse(c["misto"])

    def test_remover_e_reincluir(self):
        inserir()
        corpo = {"canal": "ml", "conta": "aracatuba", "anuncio_id": "MLB1"}
        self.assertEqual(self.cliente.pedir("POST", "/api/anuncios/remover", corpo)[0], 200)
        _, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1")
        self.assertEqual((len(c["anuncios"]), len(c["removidos"])), (0, 1))
        self.assertEqual(self.cliente.pedir("POST", "/api/anuncios/reincluir", corpo)[0], 200)
        self.assertEqual(self.cliente.pedir("POST", "/api/anuncios/remover", {**corpo, "anuncio_id": "X"})[0], 400)

    def test_kit_pela_rota_e_da_para_montar_com_pendente(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/kit", {
            "sku_kit": "KITX1", "nome": "Kit X",
            "componentes": [{"sku": "ab-12", "quantidade": 2}]})
        self.assertEqual(st, 200, c)
        inserir(ficha="KITX1")
        orig = S.apollo_disponivel_filial
        S.apollo_disponivel_filial = lambda codigos, filial: {k: 10.0 for k in codigos}
        try:
            _, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=KITX1&apollo=1")
            self.assertEqual(c["estoque"]["aracatuba"]["da_para_montar"], 5)
            # venda pendente da peca (chave do Apollo = _exp_norm_codigo) reduz o que da para montar
            self.salvar_admin([linha("PED1", "AB-12", quantidade=4, uid="u1")])
            _, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=KITX1&apollo=1")
            est = c["estoque"]["aracatuba"]
            self.assertEqual(list(est["pendente"].values()), [4], est)
            self.assertEqual(est["da_para_montar"], 3, est)
        finally:
            S.apollo_disponivel_filial = orig

    def test_sem_apollo_nao_vira_zero(self):
        S.ficha_salvar("P1", {"nome": "x"}, "luis", None)
        _, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1")
        est = c["estoque"]["aracatuba"]
        self.assertIsNone(est["da_para_montar"])
        self.assertTrue(all(v is None for v in est["disponivel"].values()))

    def test_pendente_desconhecido_nao_vira_zero(self):
        S.ficha_salvar("P1", {"nome": "x"}, "luis", None)
        orig = S.apollo_disponivel_filial
        S.apollo_disponivel_filial = lambda codigos, filial: {k: 10.0 for k in codigos}
        try:
            with mock.patch.object(S, "_rows_from_storage_value", side_effect=RuntimeError("fila ilegivel")):
                _, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1&apollo=1")
        finally:
            S.apollo_disponivel_filial = orig
        for filial in ("aracatuba", "bauru"):
            est = c["estoque"][filial]
            self.assertIsNone(est["pendente"])
            self.assertIsNone(est["da_para_montar"])
            self.assertTrue(est["erro"])

    def test_puxar_conta_desconhecida_400(self):
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]):
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {"contas": ["marte"]})
            self.assertEqual(st, 400, c)
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {"contas": "aracatuba"})
            self.assertEqual(st, 400, c)
        # o 400 nao deixa a trava presa
        self.assertTrue(S.ANUNCIOS_PUXAR_LOCK.acquire(blocking=False))
        S.ANUNCIOS_PUXAR_LOCK.release()

    def test_puxar_sucesso(self):
        item = {"sku_ml": "0893117732", "titulo": "Titulo", "preco": 10.0, "estoque": 3, "status": "active",
                "tipo": "gold_special", "categoria": "MLB1", "logistica": "", "user_product": "", "atributos": []}
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]),                 mock.patch.object(S, "ml_todos_os_anuncios", return_value=["MLB9"]),                 mock.patch.object(S, "ml_itens_detalhe", return_value={"MLB9": item}):
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {"contas": ["aracatuba"]})
        self.assertEqual(st, 200, c)
        self.assertTrue(c["ok"])
        self.assertEqual(c["contas"]["aracatuba"]["novos"], 1)
        with S.DB_LOCK:
            a = S.DB.execute("SELECT 1 FROM audit_log WHERE action='anuncios.puxar'").fetchone()
        self.assertIsNotNone(a)

    def test_compras_403(self):
        st, r = self.cliente.pedir("POST", "/api/roles", {
            "username": "carla23", "display_name": "Carla", "role": "compras", "active": True, "password": SENHA_ADMIN})
        self.assertEqual(st, 200, r)
        cp = Cliente(self.url_base)
        cp.entrar("carla23", SENHA_ADMIN, area="portal")
        self.assertEqual(cp.pedir("GET", "/api/anuncios")[0], 403)
        self.assertEqual(cp.pedir("GET", "/api/anuncios/ficha?codigo=P1")[0], 403)
        for rota in ("ficha", "kit", "puxar", "controle", "remover", "reincluir"):
            self.assertEqual(cp.pedir("POST", "/api/anuncios/" + rota, {})[0], 403, rota)

    def test_pagina_abre(self):
        """V20.23: /expedicao/anuncios entrega a tela (com a sessao do Admin)."""
        req = urllib.request.Request(self.url_base + "/anuncios")
        req.add_header("Cookie", self.cliente.cookie)
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
            corpo = r.read().decode("utf-8")
        self.assertIn("<html", corpo.lower())
        self.assertIn("/api/anuncios", corpo)

    def test_tela_tem_as_pecas_padrao(self):
        """V20.23: a tela usa as pecas comuns (nk-ui) e o menu unico tem o item."""
        html = (RAIZ / "APP" / "anuncios.html").read_text(encoding="utf-8")
        for peca in ("nk-ui.css", "nk-ui.js", "nk-pagina", "nk-cab", "nk-rotulo", "data-nk-tabela"):
            self.assertIn(peca, html, peca)
        self.assertRegex(html, r'nk-menu\.js\?v=[^"]+" data-shell')
        menu = (RAIZ / "APP" / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('href: "/anuncios"', menu)
        # V20.23: escrita so para o perfil Admin; Passar para o Noromak pergunta da Alephee
        self.assertIn("confirmou_alephee", html)
        # o Salvar manda so os campos alterados (camposMudados) + a versao da ficha lida
        self.assertIn("mud.forEach(c => { campos[c]", html)
        self.assertIn("corpo.versao = F.ficha.versao", html)

    def test_tela_tem_criar_anuncio(self):
        """V20.24: a tela cria o anuncio em 7 passos, na ordem do dono, e usa as rotas da fase 2."""
        html = (RAIZ / "APP" / "anuncios.html").read_text(encoding="utf-8")
        passos = ["Conta e título", "Categoria", "Obrigatórios da categoria", "Fotos do anúncio",
                  "Preço, tipo, envio, garantia e quantidade", "Conferir no ML", "Publicar"]
        inicio = html.find("const PASSOS = [")
        self.assertGreater(inicio, 0)
        posicoes = [html.find('"' + p + '"', inicio) for p in passos]
        self.assertTrue(all(i > 0 for i in posicoes), dict(zip(passos, posicoes)))
        self.assertEqual(posicoes, sorted(posicoes), "os passos fora da ordem do dono")
        for rota in ("/api/anuncios/ml/categorias", "/api/anuncios/ml/atributos", "/api/anuncios/ml/tarifa",
                     "/api/anuncios/rascunho", "/api/anuncios/rascunhos", "/rascunho/validar", "/rascunho/publicar",
                     "/rascunho/liberar", "/api/anuncios/foto", "/foto/remover", "/foto/ordenar", "/api/anuncios/estudio"):
            self.assertIn(rota, html, rota)
        # o Publicar nao e repetido sozinho: erro de rede/504 rele o rascunho; incerto pergunta o MLB
        self.assertIn("relerRascunhoDepoisDoPublicar", html)
        self.assertIn("Achei, é o MLB", html)
        self.assertIn("Não foi criado", html)
        self.assertIn("versao: CR.r.versao", html)  # cada passo grava com a versao do rascunho

    def test_tarifa_e_atributos_com_a_ficha(self):
        """V20.24: com `codigo`, a tarifa traz custo/imposto/margem-alvo e os atributos o que a ficha preenche."""
        S.ficha_salvar("P9", {"nome": "Peca nove", "marca": "Wurth", "peso_g": 450}, "luis", None)
        atributos = [{"id": "BRAND", "name": "Marca", "tags": {"required": True}}]

        def ml_falso(caminho, **kw):
            if "listing_prices" in caminho:
                return {"sale_fee_amount": 14, "sale_fee_details": {"percentage_fee": 14, "fixed_fee": 0}}
            if caminho.endswith("/attributes"):
                return atributos
            return {"id": "MLB77", "name": "Pecas", "path_from_root": [{"name": "Pecas"}]}

        prod = {"custo": 99.0, "margem_alvo": 0.2}  # V20.24 (revisao): o custo NAO vem mais das Promocoes
        filial = {"revenda": "1", "custo_medio": 40.0}  # custo de Aracatuba lido do Apollo (V19.77/V19.79)
        prec = {"products": [{"_id": "p9", "sku": "P9", "custo": 99.0, "custosFiliais": [filial]}]}
        with mock.patch.object(S, "precificador_dados", return_value=prec),                 mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]),                 mock.patch.object(S, "ml_api_get", ml_falso),                 mock.patch.object(S, "_promo_produtos", return_value={}),                 mock.patch.object(S, "_promo_produto", return_value=prod):
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/tarifa?preco=100&tipo=gold_special&categoria=MLB77"
                                              "&conta=aracatuba&codigo=p9")
            self.assertEqual(st, 200, c)
            self.assertEqual((c["custo"], c["margem_alvo"], c["imposto"]), (40.0, 0.2, 0.12))
            filial["custo_medio"] = 0
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/tarifa?preco=100&tipo=gold_special&categoria=MLB77"
                                              "&conta=aracatuba&codigo=P9")
            self.assertIsNone(c["custo"])  # custo zero = desconhecido, nunca zero
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/atributos?categoria=MLB77&conta=aracatuba&codigo=P9")
            self.assertEqual(st, 200, c)
            self.assertEqual(c["da_ficha"]["BRAND"], "Wurth")
            self.assertEqual(c["da_ficha"]["SELLER_PACKAGE_WEIGHT"], "450 g")
            st, c = self.cliente.pedir("GET", "/api/anuncios/ml/tarifa?preco=100&tipo=gold_special&categoria=MLB77"
                                              "&conta=aracatuba")
            self.assertNotIn("custo", c)

    def test_kit_sem_ficha_abre_pela_composicao(self):
        """V20.23: o "Abrir" de um kit do cadastro (ou KITn-X) sem ficha nem anuncio abre a composicao, nao 404;
        e uma ficha gravada como peca para um codigo de kit continua mostrando a composicao."""
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=KIT3-0893117732")
        self.assertEqual(st, 200, c)
        self.assertIsNone(c["ficha"])
        self.assertEqual(c["composicao"]["componentes"], [{"sku": "0893117732", "quantidade": 3}])
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "KIT3-0893117732", "campos": {"nome": "Kit"}})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=KIT3-0893117732")
        self.assertEqual(st, 200, c)
        self.assertIsNotNone(c["composicao"])
        self.assertEqual(c["ficha"]["tipo"], "kit")


class NovoProdutoTeste(TesteNoromak):
    """V20.24: POST /api/anuncios/ficha com criar=true."""

    def test_criar_ficha_nova(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "NP1", "campos": {"nome": "Peca nova"}, "criar": True})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["ficha"]["codigo"], "NP1")
        self.assertEqual(c["ficha"]["tipo"], "peca")
        self.assertEqual(c["ficha"]["campos"]["nome"], "Peca nova")

    def test_criar_existente_409(self):
        S.ficha_salvar("NP2", {"nome": "Ja existe"}, "luis", None)
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "np2", "campos": {"nome": "Outro"}, "criar": True})
        self.assertEqual(st, 409, c)
        self.assertEqual(c["error"], "JA_EXISTE")
        self.assertEqual(c["atual"]["campos"]["nome"], "Ja existe")
        self.assertEqual(S.ficha_ler("NP2")["campos"]["nome"], "Ja existe")

    def test_criar_sem_nome_400(self):
        for corpo in ({"codigo": "NP3", "campos": {}}, {"codigo": "NP3", "campos": {"nome": "  "}},
                      {"codigo": "", "campos": {"nome": "x"}}, {"codigo": "A/B", "campos": {"nome": "x"}},
                      {"codigo": "X" * 61, "campos": {"nome": "x"}}):
            corpo["criar"] = True
            self.assertEqual(self.cliente.pedir("POST", "/api/anuncios/ficha", corpo)[0], 400, corpo)
        self.assertIsNone(S.ficha_ler("NP3"))

    def test_criar_codigo_de_kit_vira_kit(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/ficha", {"codigo": "KIT3-0893117732", "campos": {"nome": "Kit tres"}, "criar": True})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["ficha"]["tipo"], "kit")

    def test_supervisor_nao_cria(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_np", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_np", SENHA, area="admin")
        st, _ = sup.pedir("POST", "/api/anuncios/ficha", {"codigo": "NP5", "campos": {"nome": "x"}, "criar": True})
        self.assertEqual(st, 403)
        self.assertIsNone(S.ficha_ler("NP5"))


class SeletorDeCasaTeste(TesteNoromak):
    """V20.24: ?conta= na lista e o seletor Todas/Araçatuba/Bauru na tela."""

    def _monta(self):
        S.ficha_salvar("P1", {"nome": "Peca um"}, "luis", None)
        S.ficha_salvar("P2", {"nome": "Peca dois"}, "luis", None)
        inserir(conta="aracatuba", anuncio_id="MLB1", ficha="P1", controle="alephee")
        inserir(conta="aracatuba", anuncio_id="MLB2", ficha="P1", controle="noromak")  # misto em Araçatuba
        inserir(conta="bauru", anuncio_id="MLB3", ficha="P1", controle="alephee")
        inserir(conta="bauru", anuncio_id="MLB4", ficha="SEMFICHA", controle="alephee")

    def _fichas(self, c):
        return {f["codigo"]: f for f in c["fichas"]}

    def test_filtro_com_anuncio(self):
        # V20.24: o cartão "Anúncios" da lista filtra os produtos que têm anúncio (na casa escolhida)
        self._monta()
        st, c = self.cliente.pedir("GET", "/api/anuncios?filtro=com_anuncio")
        self.assertEqual(st, 200, c)
        self.assertIn("P1", self._fichas(c))
        self.assertNotIn("P2", self._fichas(c))
        st, c = self.cliente.pedir("GET", "/api/anuncios?filtro=com_anuncio&conta=bauru")
        self.assertIn("P1", self._fichas(c))
        self.assertNotIn("P2", self._fichas(c))
        html = (S.APP_DIR / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn('k("com_anuncio"', html)

    def test_contagem_por_conta(self):
        self._monta()
        st, c = self.cliente.pedir("GET", "/api/anuncios")
        self.assertEqual(self._fichas(c)["P1"]["anuncios"], 3)
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=bauru")
        self.assertEqual(st, 200, c)
        f = self._fichas(c)
        self.assertEqual(f["P1"]["anuncios"], 1)
        self.assertEqual(f["P2"]["anuncios"], 0)  # ficha compartilhada continua na lista
        self.assertEqual(c["resumo"]["anuncios"], 2)
        self.assertEqual(c["resumo"]["sem_ficha"], 1)
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=aracatuba")
        self.assertEqual(self._fichas(c)["P1"]["anuncios"], 2)
        self.assertEqual(c["resumo"]["anuncios"], 2)
        self.assertEqual(c["resumo"]["sem_ficha"], 0)
        self.assertEqual(c["total_fichas"], 2)

    def test_misto_so_dentro_da_conta(self):
        self._monta()
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=bauru&filtro=misto")
        self.assertEqual(c["fichas"], [])
        self.assertEqual(c["resumo"]["misto"], 0)
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=aracatuba&filtro=misto")
        self.assertEqual([f["codigo"] for f in c["fichas"]], ["P1"])
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=bauru&filtro=noromak")
        self.assertEqual(c["fichas"], [])
        st, c = self.cliente.pedir("GET", "/api/anuncios?conta=bauru&filtro=sem_ficha")
        self.assertEqual([f["codigo"] for f in c["fichas"]], ["SEMFICHA"])

    def test_conta_invalida_400(self):
        self.assertEqual(self.cliente.pedir("GET", "/api/anuncios?conta=ribeirao")[0], 400)

    def test_tela_tem_seletor_de_casa(self):
        html = (RAIZ / "APP" / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn("nk_anuncios_casa_v2024", html)
        self.assertIn('[["todas", "Todas"], ["aracatuba", "Araçatuba"], ["bauru", "Bauru"]]', html)
        self.assertIn("&conta=", html)
