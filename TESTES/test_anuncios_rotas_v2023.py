"""V20.23 - Anuncios fase 1: controle do anuncio, payloads e rotas."""

from base import S, TesteNoromak, Cliente, linha

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
