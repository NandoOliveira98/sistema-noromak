"""V19.03 — Remessa Full automática. Apollo e ML simulados."""
import unittest
from base import S, TesteNoromak


class NaoAceito(TesteNoromak):
    def test_marcar_e_liberar(self):
        S.full_nao_aceito_marcar("0893600022", "inflamável", "admin")
        self.assertIn("0893600022", S.full_nao_aceito_lista())
        self.assertEqual(S.full_nao_aceito_lista()["0893600022"]["motivo"], "inflamável")
        self.assertTrue(S.full_nao_aceito_liberar("0893600022", "admin"))
        self.assertNotIn("0893600022", S.full_nao_aceito_lista())

    def test_codigo_normalizado(self):
        S.full_nao_aceito_marcar("  abc-1 ", "", "admin")
        self.assertIn("ABC-1", S.full_nao_aceito_lista())


from datetime import date, timedelta

HOJE = date(2026, 10, 11)


class Calculo(TesteNoromak):
    def anuncio(self, mlb, sku, no_full, venda, dia=HOJE):
        d = (dia - timedelta(days=1)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_estoque_dia_v1872(item_id,dia,checagens,estoque_ultimo,logistica,updated_at) "
                         "VALUES(?,?,1,?,'fulfillment','')", (mlb, d, no_full))
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,sku,titulo) VALUES(?,?,?)", (mlb, sku, "T " + mlb))
            if venda:
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES('ml',?,'1',?,?,?,?,'t',?,10,'paid','')", (f"o{mlb}", d, d, sku, mlb, venda))
            S.DB.commit()

    def setUp(self):
        super().setUp()
        self.conhecidos = {"A": {}, "B": {}, "C": {}}
        self._orig = S._compras_produtos_precificador
        S._compras_produtos_precificador = lambda: self.conhecidos

    def tearDown(self):
        S._compras_produtos_precificador = self._orig
        super().tearDown()

    def calc(self, estoque):
        return S.full_remessa_calcular(S.full_remessa_candidatos(HOJE), estoque)

    def test_necessidade_30_dias_menos_o_full(self):
        self.anuncio("MLB1", "A", no_full=10, venda=60)
        r = self.calc({"A": 500})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 50)
        self.assertEqual(r["unidades"], 50)

    def test_limitado_ao_apollo_e_mais_urgente_primeiro(self):
        self.anuncio("MLB1", "A", no_full=50, venda=60)   # 25 dias
        self.anuncio("MLB2", "A", no_full=0, venda=30)    # urgente
        r = {i["anuncio"]: i for i in self.calc({"A": 35})["itens"]}
        self.assertEqual(r["MLB2"]["qtd_sugerida"], 30)
        self.assertEqual(r["MLB1"]["qtd_sugerida"], 5)

    def test_apollo_sem_resposta_nao_vira_zero_nem_estoque(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        i = self.calc({"A": None})["itens"][0]
        self.assertIsNone(i["estoque_apollo"])
        self.assertEqual(i["qtd_sugerida"], 0)
        self.assertIn("Apollo", i["motivo"])

    def test_v1905_apollo_zerado_ou_sem_necessidade_fica_fora(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)    # Apollo 0
        self.anuncio("MLB2", "B", no_full=90, venda=30)   # Full cobre 90 dias
        self.anuncio("MLB3", "C", no_full=0, venda=30)    # tem estoque
        r = self.calc({"A": 0, "B": 100, "C": 5})
        self.assertEqual([i["anuncio"] for i in r["itens"]], ["MLB3"])
        motivos = {f["anuncio"]: f["motivo"] for f in r["fora"]}
        self.assertEqual(motivos["MLB1"], "sem estoque no Apollo")
        self.assertEqual(motivos["MLB2"], "Full já cobre 30 dias")

    def test_kit_conta_1_e_consome_componentes(self):
        self.anuncio("MLB9", "KIT2-A", no_full=0, venda=30)
        r = self.calc({"A": 20})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 10)   # 20 peças / 2 por kit
        self.assertEqual(r["unidades"], 10)

    def test_nao_aceito_sai(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        S.full_nao_aceito_marcar("A", "recusado", "admin")
        r = self.calc({"A": 100})
        self.assertEqual(r["itens"], [])
        self.assertTrue(any("não aceito" in f["motivo"] for f in r["fora"]))

    def test_anuncio_sem_codigo_fica_fora_com_motivo(self):
        self.anuncio("MLB1", "ZZZ", no_full=0, venda=30)
        r = self.calc({})
        self.assertEqual(r["itens"], [])
        self.assertEqual(r["fora"][0]["anuncio"], "MLB1")

    def test_novo_sugerido_ate_10_sem_confirmar(self):
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = self.calc({"C": 100})
        self.assertEqual(r["novos"][0]["qtd_sugerida"], 10)
        self.assertEqual(r["novos"][0]["confirmado"], 0)
        self.assertEqual(r["unidades"], 0)

if __name__ == "__main__":
    unittest.main()


class Rodada(Calculo):
    def test_pronta_com_150(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(r["situacao"], "pronta")
        self.assertEqual(r["unidades"], 160)

    def test_abaixo_de_150_espera(self):
        self.anuncio("MLB1", "A", no_full=0, venda=100)
        self.assertEqual(S.full_remessa_rodada(HOJE, estoque={"A": 1000})["situacao"], "montando")

    def test_ajuste_admin_sobrevive_a_rodada(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=40, usuario="admin")
        r2 = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(r2["remessa_id"], r["remessa_id"])
        item = S.full_remessa_atual()["itens"][0]
        self.assertEqual(item["qtd_admin"], 40)
        self.assertEqual(r2["situacao"], "montando")   # 40 < 150

    def test_agendada_vira_a_caminho_e_nao_repete(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        r2 = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertNotEqual(r2["remessa_id"], r["remessa_id"])
        self.assertEqual(r2["unidades"], 0)

    def test_apollo_caido_nao_marca_pronta(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": None})
        self.assertEqual(r["situacao"], "montando")
        self.assertIn("Apollo", r["obs"])

    def test_confirmar_novo_entra_na_conta(self):
        self.anuncio("MLB1", "A", no_full=0, venda=145)
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000, "C": 100})
        self.assertEqual(r["situacao"], "montando")
        S.full_remessa_ajustar(r["remessa_id"], "NOVO:C", confirmado=True, usuario="admin")
        self.assertEqual(S.full_remessa_rodada(HOJE, estoque={"A": 1000, "C": 100})["situacao"], "pronta")

    def test_ajuste_invalido_e_agendada_recusa(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        with self.assertRaises(ValueError):
            S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=-1, usuario="admin")
        with self.assertRaises(ValueError):
            S.full_remessa_ajustar(r["remessa_id"], "MLB999", qtd=1, usuario="admin")
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        with self.assertRaisesRegex(ValueError, "agendada"):
            S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=1, usuario="admin")

    def test_recebida_manual(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        self.assertEqual(S.full_remessa_situacao(r["remessa_id"], "recebida", "admin")["situacao"], "recebida")

    def _situacao(self, rid):
        with S.DB_LOCK:
            return S.DB.execute("SELECT situacao FROM full_remessa_v1903 WHERE remessa_id=?", (rid,)).fetchone()[0]

    def test_recebida_automatica_quando_full_sobe(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(self._situacao(r["remessa_id"]), "agendada")
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_estoque_dia_v1872(item_id,dia,checagens,estoque_ultimo,logistica,updated_at) "
                         "VALUES('MLB1',?,1,80,'fulfillment','')", (HOJE.isoformat(),))
            S.DB.commit()
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(self._situacao(r["remessa_id"]), "recebida")

    def test_estoque_apollo_revenda_1_e_casa_sem_resposta(self):
        orig = S.crm_stock_lookup

        def falso(cod, op="", wait_seconds=0.0):
            if cod == "A":
                return {"estoques": [{"revenda": 1, "disponivel": 7}, {"revenda": 2, "disponivel": 50}], "casas_sem_resposta": []}
            if cod == "B":
                return {"estoques": [{"revenda": 1, "disponivel": None}], "casas_sem_resposta": [1]}
            raise S.CrmLimiteAtingido(30)
        S.crm_stock_lookup = falso
        try:
            e = S.full_remessa_estoque_apollo({"A", "B", "C"}, prazo_s=0)
        finally:
            S.crm_stock_lookup = orig
        self.assertEqual(e, {"A": 7.0, "B": None, "C": None})

    def test_v1907_localizacao_vem_da_mesma_consulta(self):
        orig = S.crm_stock_lookup
        S.crm_stock_lookup = lambda cod, op="", wait_seconds=0.0: {"estoques": [
            {"revenda": 1, "disponivel": 7, "localizacao": "ZZ.9.14" if cod == "A" else "...."},
            {"revenda": 2, "disponivel": 5, "localizacao": "X.1"}], "casas_sem_resposta": []}
        locais = {}
        try:
            S.full_remessa_estoque_apollo({"A", "B"}, prazo_s=0, locais=locais)
        finally:
            S.crm_stock_lookup = orig
        self.assertEqual(locais, {"A": "ZZ.9.14"})

    def test_v1907_lista_de_separacao(self):
        rows = [
            {"anuncio": "M1", "codigo": "A", "titulo": "Peça A", "confirmado": 1, "removido": 0, "qtd_sugerida": 5,
             "qtd_admin": None, "componentes_json": '[{"codigo": "A", "qtd": 1}]'},
            {"anuncio": "M2", "codigo": "KIT2-B", "titulo": "Kit B", "confirmado": 1, "removido": 0, "qtd_sugerida": 3,
             "qtd_admin": None, "componentes_json": '[{"codigo": "B", "qtd": 2}]'},
            {"anuncio": "M3", "codigo": "A", "titulo": "Peça A", "confirmado": 1, "removido": 0, "qtd_sugerida": 9,
             "qtd_admin": 2, "componentes_json": '[{"codigo": "A", "qtd": 1}]'},
            {"anuncio": "M4", "codigo": "C", "titulo": "C", "confirmado": 1, "removido": 1, "qtd_sugerida": 4,
             "qtd_admin": None, "componentes_json": "[]"},
            {"anuncio": "M5", "codigo": "D", "titulo": "Novo D", "confirmado": 0, "removido": 0, "qtd_sugerida": 4,
             "qtd_admin": None, "componentes_json": "[]"},
            {"anuncio": "M6", "codigo": "E", "titulo": "E", "confirmado": 1, "removido": 0, "qtd_sugerida": 1,
             "qtd_admin": None, "componentes_json": "[]"},
        ]
        lista = S.full_remessa_separacao(rows, {"A": "ZZ.9.14", "B": "ZZ.9.9"})
        self.assertEqual([(p["codigo"], p["qtd"]) for p in lista], [("B", 6), ("A", 7), ("E", 1)])
        self.assertEqual(lista[2]["localizacao"], "")
        self.assertIn("kit", lista[0]["descricao"])

    def test_v1907_rodada_entrega_a_lista(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        sep = S.full_remessa_atual()["separacao"]
        self.assertEqual([(p["codigo"], p["qtd"]) for p in sep], [("A", 160)])

    def test_novo_nao_confirmado_nao_vai_a_caminho(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000, "C": 100})
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        self.assertNotIn("NOVO:C", S._full_a_caminho())
        self.assertEqual(S._full_a_caminho()["MLB1"], 160)

    def test_agendar_durante_a_leitura_descarta_a_rodada(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        orig = S.full_remessa_estoque_apollo

        def lendo(codigos, prazo_s=600, locais=None):
            S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
            return {"A": 1000}
        S.full_remessa_estoque_apollo = lendo
        try:
            r2 = S.full_remessa_rodada(HOJE)
        finally:
            S.full_remessa_estoque_apollo = orig
        self.assertIn("rodada descartada", r2["obs"])
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM full_remessa_v1903").fetchone()[0]
        self.assertEqual(n, 1)   # nenhuma remessa nova com o cálculo velho


class Aviso(Calculo):
    def test_avisa_uma_vez_por_dia(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertTrue(S.full_remessa_avisar(HOJE))
        self.assertFalse(S.full_remessa_avisar(HOJE))
        self.assertTrue(S.full_remessa_avisar(HOJE + timedelta(days=1)))
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM aviso_venda_v1893 WHERE marketplace='full'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_montando_nao_avisa(self):
        self.anuncio("MLB1", "A", no_full=0, venda=10)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertFalse(S.full_remessa_avisar(HOJE))

    def test_linha_do_email(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertIn("160", S.full_remessa_resumo_email())

    def test_worker_no_painel_de_saude(self):
        self.assertIn("full_remessa_loop", S.SAUDE_WORKERS_INFO)


from base import Cliente
SENHA = "Senha-de-teste-1903"


class Rotas(Calculo):
    def test_operador_403(self):
        with S.DB_LOCK:
            S.upsert_app_user("op_1903", "Operador", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.DB.commit()
        op = Cliente(self.url_base)
        op.entrar("op_1903", SENHA, area="conferencia")
        self.assertEqual(op.pedir("GET", "/api/full/remessa")[0], 403)
        self.assertEqual(op.pedir("POST", "/api/full/nao-aceito", {"codigo": "X"})[0], 403)

    def test_nao_aceito_pela_rota(self):
        st, c = self.cliente.pedir("POST", "/api/full/nao-aceito", {"codigo": "abc1", "motivo": "frágil"})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("GET", "/api/full/remessa")
        self.assertEqual(st, 200, c)
        self.assertIn("ABC1", S.full_nao_aceito_lista())
        st, c = self.cliente.pedir("POST", "/api/full/nao-aceito/liberar", {"codigo": "abc1"})
        self.assertEqual(st, 200, c)
        self.assertNotIn("ABC1", S.full_nao_aceito_lista())

    def test_nao_aceito_na_remessa(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.cliente.pedir("POST", "/api/full/nao-aceito", {"codigo": "ZZ", "motivo": "m"})
        st, c = self.cliente.pedir("GET", "/api/full/remessa")
        self.assertEqual([n["codigo"] for n in c["remessa"]["nao_aceito"]], ["ZZ"])

    def test_agendada_recusa_ajuste_e_data_ruim(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        rid = r["remessa_id"]
        st, c = self.cliente.pedir("POST", "/api/full/remessa/agendar", {"remessa_id": rid, "data": "20/10/2026"})
        self.assertEqual(st, 400, c)
        st, c = self.cliente.pedir("POST", "/api/full/remessa/agendar", {"remessa_id": rid, "data": "2026-10-20"})
        self.assertEqual(st, 200, c)
        st, c = self.cliente.pedir("POST", "/api/full/remessa/ajustar", {"remessa_id": rid, "anuncio": "MLB1", "qtd": 1})
        self.assertEqual(st, 400, c)
        st, c = self.cliente.pedir("GET", "/api/full/remessa")
        self.assertEqual(c["historico"][0]["remessa_id"], rid)


class TelaRemessa(unittest.TestCase):
    """V19.03: seção Remessa sugerida na Demanda Full e mensagem para o Agendador."""

    def test_tela_chama_a_api_e_conversa_com_a_extensao(self):
        from base import RAIZ
        html = (RAIZ / "APP" / "analise-full.html").read_text(encoding="utf-8")
        self.assertIn("/api/full/remessa", html)
        self.assertIn("noromak-full-procurar-datas", html)
        self.assertIn("noromak-full-procurar-datas-ok", html)
        self.assertIn('id="remessaFull"', html)
        self.assertTrue((RAIZ / "INTEGRACAO_AGENDADOR_FULL.md").exists())


class RevisaoFinal(Calculo):
    """V19.03: ajustes da revisão final."""

    def _venda_nova(self, sku, qtd):
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml',?,'1',?,?,?,'MLB77','t',?,10,'paid','')", (f"n{sku}", d, d, sku, qtd))
            S.DB.commit()

    def test_apollo_parcial_com_151_conhecidas_fica_pronta(self):
        self.anuncio("MLB1", "A", no_full=0, venda=151)
        self.anuncio("MLB2", "B", no_full=0, venda=10)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000, "B": None})
        self.assertEqual(r["situacao"], "pronta")
        self.assertIn("Apollo não respondeu para 1", r["obs"])

    def test_apollo_todo_desconhecido_fica_montando(self):
        self.anuncio("MLB1", "A", no_full=0, venda=151)
        self.anuncio("MLB2", "B", no_full=0, venda=10)
        r = S.full_remessa_rodada(HOJE, estoque={"A": None, "B": None})
        self.assertEqual(r["situacao"], "montando")
        # ajuste do ADMIN em item sem Apollo não libera a pronta
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=200, usuario="admin")
        self.assertEqual(S.full_remessa_atual()["situacao"], "montando")

    def test_a_caminho_expira_10_dias_apos_a_data(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], (HOJE - timedelta(days=11)).isoformat(), "admin")
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        with S.DB_LOCK:
            c = S.DB.execute("SELECT situacao, obs FROM full_remessa_v1903 WHERE remessa_id=?", (r["remessa_id"],)).fetchone()
        self.assertEqual(c["situacao"], "recebida")
        self.assertIn("prazo", c["obs"])

    def test_a_caminho_dentro_do_prazo_continua_agendada(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], (HOJE - timedelta(days=10)).isoformat(), "admin")
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(self._situacao(r["remessa_id"]), "agendada")

    def _situacao(self, rid):
        with S.DB_LOCK:
            return S.DB.execute("SELECT situacao FROM full_remessa_v1903 WHERE remessa_id=?", (rid,)).fetchone()[0]

    def test_sku_a_barra_b_consome_um_de_cada_e_conta_1(self):
        self.anuncio("MLB5", "A/B", no_full=0, venda=30)
        r = self.calc({"A": 10, "B": 100})
        i = r["itens"][0]
        self.assertEqual(sorted(c["codigo"] for c in i["componentes"]), ["A", "B"])
        self.assertEqual(i["qtd_sugerida"], 10)
        self.assertEqual(r["unidades"], 10)

    def test_sku_a_barra_desconhecido_fica_fora(self):
        self.anuncio("MLB5", "A/ZZZ", no_full=0, venda=30)
        r = self.calc({"A": 10})
        self.assertEqual(r["itens"], [])
        self.assertEqual(r["fora"][0]["motivo"], "SKU sem código Apollo")

    def test_novo_sem_estoque_vai_para_fora(self):
        self._venda_nova("B", 14)
        self._venda_nova("C", 14)
        r = self.calc({"B": 0, "C": None})
        self.assertEqual(r["novos"], [])
        motivos = {f["codigo"]: f["motivo"] for f in r["fora"]}
        self.assertEqual(motivos["B"], "sem estoque no Apollo")
        self.assertEqual(motivos["C"], "Apollo não respondeu")

    def test_qtd_null_volta_a_sugestao(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=40, usuario="admin")
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", removido=False, usuario="admin")
        self.assertEqual(S.full_remessa_atual()["itens"][0]["qtd_admin"], 40)   # ausente = sem mudança
        res = S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=None, usuario="admin")
        self.assertIsNone(S.full_remessa_atual()["itens"][0]["qtd_admin"])
        self.assertEqual(res["situacao"], "pronta")
