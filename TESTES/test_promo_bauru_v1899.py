"""V18.99 — Promoções automáticas na conta de Bauru.

Decisões do dono (10/10): mesmas REGRAS (margem mínima da promoção, duração, estoque) para as
duas contas; MODO e limite do dia separados por conta, Bauru começando em Simulação; uma lista
só com filtro de conta; convites de campanha de Bauru com modo próprio.
O que não pode quebrar: cada anúncio é renovado/aceito com a conta DELE; o modo de Araçatuba
nunca liga o automático de Bauru; a rodada de uma conta não apaga convites da outra; o anúncio
de Bauru não entra no monitor do Precificador (que é de Araçatuba).
"""

from test_promo_renovacao import HOJE, Base, promo_propria

from base import S


class PromoBauru(Base):

    def setUp(self):
        super().setUp()
        # as duas contas conectadas, cada uma com o seu vendedor
        S.ml_integration_row = lambda: {"access_token": "TOK-" + S.ml_casa_atual(),
                                        "seller_id": "1" if S.ml_casa_atual() == S.CASA_PRINCIPAL else "2"}
        self.casas_http = []
        anterior = S._ml_promo_http

        def http(metodo, caminho, payload=None):
            self.casas_http.append((S.ml_casa_atual(), caminho.split("/items/")[-1].split("?")[0]))
            return anterior(metodo, caminho, payload)
        S._ml_promo_http = http
        self.monitor = []
        S.promo_atualizar_data_planilha = lambda mlb, *a, **k: self.monitor.append(mlb) or True
        self.promos["MLB3"] = [promo_propria("candidate")]
        self.itens["MLB3"] = dict(self.itens["MLB1"], titulo="Peça de Bauru")
        S._ml_vinculos_mlb_sku = lambda: {"MLB1": "SKU1", "MLB2": "SKU1", "MLB3": "SKU1"}

    def marcar_bauru(self, mlb="MLB3"):
        S.promo_renovacao_marcar(mlb, True, "admin_teste", preco=90.0, conta="bauru")

    def test_modo_separado_regras_iguais(self):
        self.modo("automatico")
        self.assertEqual(S.promo_auto_config("bauru")["modo_renovacao"], "simulacao",
                         "o automático de Araçatuba ligou Bauru")
        S.promo_auto_config_salvar({"modo_renovacao": "lote", "limite_dia": 5}, "admin_teste", "bauru")
        self.assertEqual(S.promo_auto_config("aracatuba")["modo_renovacao"], "automatico")
        self.assertEqual((S.promo_auto_config("bauru")["modo_renovacao"], S.promo_auto_config("bauru")["limite_dia"]),
                         ("lote", 5))
        S.promo_auto_config_salvar({"margem_min_promo": 18}, "admin_teste", "aracatuba")
        self.assertEqual(S.promo_auto_config("bauru")["margem_min_promo"], 18, "a regra de margem é a mesma")
        with S.ml_na_casa("bauru"):
            self.assertEqual(S.promo_auto_config()["modo_renovacao"], "lote", "dentro de Bauru vale o modo de Bauru")

    def test_cada_anuncio_renovado_com_a_sua_conta_e_o_seu_modo(self):
        self.marcar()           # MLB1, Araçatuba
        self.marcar_bauru()     # MLB3, Bauru
        self.modo("automatico")
        r = self.rodar()
        self.assertEqual(self.casas_http, [("aracatuba", "MLB1")], "Bauru (Simulação) renovou sozinho")
        self.assertEqual(r["por_conta"]["bauru"]["renovaria"], 1)
        S.promo_auto_config_salvar({"modo_renovacao": "automatico"}, "admin_teste", "bauru")
        self.promos["MLB3"] = [promo_propria("candidate")]
        self.rodar()
        self.assertIn(("bauru", "MLB3"), self.casas_http, "o anúncio de Bauru tem de ir com a conta de Bauru")
        self.assertNotIn(("aracatuba", "MLB3"), self.casas_http)
        self.assertEqual(self.monitor, ["MLB1"], "anúncio de Bauru entrou no monitor do Precificador")

    def test_limite_do_dia_e_de_cada_conta(self):
        self.marcar()
        self.marcar_bauru()
        self.modo("automatico")
        S.promo_auto_config_salvar({"limite_dia": 1}, "admin_teste", "aracatuba")
        S.promo_auto_config_salvar({"modo_renovacao": "automatico", "limite_dia": 1}, "admin_teste", "bauru")
        r = self.rodar()
        self.assertEqual(r["renovadas"], 2, "o limite de Araçatuba barrou Bauru")

    def test_clique_renova_as_duas_contas_cada_uma_com_a_sua(self):
        self.marcar()
        self.marcar_bauru()
        r = S.promo_renovacao_rodada(hoje=HOJE, pausa=0, so=["MLB1", "MLB3"], ator="admin_teste")
        self.assertEqual(r["renovadas"], 2)
        self.assertEqual(sorted(self.casas_http), [("aracatuba", "MLB1"), ("bauru", "MLB3")])

    def test_buscar_novos_traz_as_duas_contas(self):
        listas = {"aracatuba": ["MLB1"], "bauru": ["MLB3"]}
        anterior = S.ml_todos_os_anuncios
        S.ml_todos_os_anuncios = lambda forcar=False: listas[S.ml_casa_atual()]
        try:
            r = S.promo_renovacao_buscar_novos("admin_teste")
        finally:
            S.ml_todos_os_anuncios = anterior
        self.assertEqual(r["incluidos"], 2)
        casas = {i["mlb"]: i["casa"] for i in S.promo_renovacao_payload(hoje=HOJE)["itens"]}
        self.assertEqual((casas["MLB1"], casas["MLB3"]), ("aracatuba", "bauru"))

    def test_convites_de_uma_conta_nao_apagam_os_da_outra(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_campanha_v1889(conta,promotion_id,tipo,status) VALUES('aracatuba','P1','SMART','started')")
            S.DB.execute("INSERT INTO ml_convite_v1889(promotion_id,mlb,tipo,status_item,conta) "
                         "VALUES('P1','MLB1','SMART','candidate','aracatuba')")
            S.DB.commit()
        S._ml_promo_http = lambda metodo, caminho, payload=None: {"results": [], "paging": {"total": 0}}
        with S.ml_na_casa("bauru"):
            S._promo_convites_rodada_conta(pausa=0)
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM ml_convite_v1889 WHERE conta='aracatuba'").fetchone()[0]
            st = S.DB.execute("SELECT status FROM ml_campanha_v1889 WHERE promotion_id='P1'").fetchone()[0]
        self.assertEqual((n, st), (1, "started"), "a rodada de Bauru apagou os convites de Araçatuba")

    def test_mesma_campanha_nas_duas_contas(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_campanha_v1889(conta,promotion_id,tipo,status) VALUES('aracatuba','P9','DEAL','started')")
            S.DB.execute("INSERT INTO ml_campanha_v1889(conta,promotion_id,tipo,status) VALUES('bauru','P9','DEAL','started')")
            S.DB.commit()
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM ml_campanha_v1889 WHERE promotion_id='P9'").fetchone()[0], 2)

    def test_tela_mostra_a_conta(self):
        self.marcar_bauru()
        d = S.promo_renovacao_payload(hoje=HOJE)
        self.assertEqual({i["mlb"]: i["casa"] for i in d["itens"]}["MLB3"], "bauru")
        self.assertEqual(d["config_contas"]["bauru"]["modo_renovacao"], "simulacao")
        self.assertEqual([c["conta"] for c in d["contas"]], ["aracatuba", "bauru"])
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/config",
                                           {"conta": "bauru", "modo_renovacao": "lote"})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(S.promo_auto_config("bauru")["modo_renovacao"], "lote")
        self.assertEqual(S.promo_auto_config("aracatuba")["modo_renovacao"], "simulacao")

    def test_tela_tem_filtro_e_selo_de_conta(self):
        from base import RAIZ
        h = (RAIZ / "APP" / "promocoes-auto.html").read_text(encoding="utf-8")
        self.assertIn('id="contaSeg"', h)
        self.assertIn("corpo={...corpo,conta:contaModo()}", h, "o modo salvo tem de ir com a conta do filtro")
        self.assertIn("modo_convites:m,conta:P.contaModo()", h)
        self.assertIn('id="novoConta"', h)
