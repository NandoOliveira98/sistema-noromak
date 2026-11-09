"""V20.44 — Demanda de Compra por GRUPO/CATEGORIA do Apollo (dono, 09/11).

Portas do NoromakWeb (simuladas aqui): pecas/grupos, pecas/categorias, pecas?grupo=&categoria=
(paginada) e estoque/lote. O que importa prender:
- com grupo escolhido valem SÓ as peças do Apollo (+ lista de códigos do fornecedor); a palavra
  na descrição do fornecedor não puxa peça de outro fabricante;
- "puxar apenas o que vendeu": peça lida sem venda não é perguntada de novo por 30 dias, o
  estoque só é lido de quem vendeu e a tela só mostra quem vendeu;
- leitura que falha no meio não apaga a lista anterior; grupo grande demais é recusado;
- estoque sem resposta nunca vira zero.
"""

import json
import unittest
from datetime import datetime

from base import S, TesteNoromak


def mes_fechado():
    hoje = datetime.now(S.BR_TZ).date().replace(day=1)
    ano, mes = hoje.year, hoje.month - 1
    if mes == 0:
        ano, mes = ano - 1, 12
    return f"{ano:04d}-{mes:02d}"


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S._exp_porta_get, S._apollo_estoque_lote_http, S.CHAVE_CRM, S.LINX_API_USUARIO,
                      S.APOLLO_ESTOQUE_ESPERA_S, S._linx_noite_acabou)
        S._apollo_lote_zerar()
        S.APOLLO_ESTOQUE_ESPERA_S = 0      # revisão: sem vaga na porta, não espera nos testes
        S.CHAVE_CRM = "chave-teste"
        S.LINX_API_USUARIO = ""
        S._APOLLO_FILTROS_CACHE.update(em=0.0, dados=None)
        self.chamadas = []
        self.catalogo = [  # (codigo, descricao, grupo, categoria)
            ("PSL55", "FILTRO OLEO", "TECFIL", "111"),
            ("ARL4147", "FILTRO COMBUSTIVEL", "TECFIL", "111"),
            ("TEC999", "FILTRO PARADO", "TECFIL", "111"),
            ("WG100", "FILTRO WEGA TECFIL", "WEGA", "111"),
        ]
        self.vendas = {"PSL55": 4, "ARL4147": 2}   # demanda Linx do último mês fechado
        self.falha_pagina = None
        self.estoque_sem_resposta = set()
        self.por_pagina_forcado = None

        def porta_get(rota, params):
            self.chamadas.append((rota, dict(params)))
            if rota == "pecas/grupos":
                return {"ok": True, "copia_de": "2026-11-09T02:54-03:00",
                        "grupos": [{"grupo": None, "nome": "TECFIL", "pecas": 3}, {"grupo": None, "nome": "WEGA", "pecas": 1}]}
            if rota == "pecas/categorias":
                return {"ok": True, "categorias": [], "lidas": 0, "sem_categoria_lida": 10}
            if rota == "pecas":
                g = set((params.get("grupo") or "").split(",")) - {""}
                c = set((params.get("categoria") or "").split(",")) - {""}
                lista = [x for x in self.catalogo if (not g or x[2] in g) and (not c or x[3] in c)]
                pp = self.por_pagina_forcado or int(params["por_pagina"])
                pag = int(params["pagina"])
                if self.falha_pagina == pag:
                    return {"ok": False, "erro": "cópia indisponível"}
                fatia = lista[(pag - 1) * pp:pag * pp]
                return {"ok": True, "copia_de": "2026-11-09T02:54-03:00", "pagina": pag,
                        "proxima_pagina": pag + 1 if pag * pp < len(lista) else None, "total": len(lista),
                        "sem_categoria_lida": 5 if c else None,
                        "itens": [{"codigo": x[0], "descricao": x[1], "grupo": None, "nome_grupo": x[2],
                                   "marca": None, "nome_marca": "NOROMAK", "categoria": x[3], "nome_categoria": None,
                                   "cancelado_fabrica": None} for x in fatia]}
            if rota == "demanda":
                r = {"ok": True, "itens": [], "sem_historico": [], "com_erro": [], "pendentes": []}
                for cod in params["codigos"].split(","):
                    if cod in self.vendas:
                        r["itens"].append({"codigo": cod, "revenda": 1, "mes": mes_fechado(),
                                           "DemandaBalcao": self.vendas[cod], "DemandaOficina": 0, "DemandaTelemark": 0,
                                           "DemandaTotal": self.vendas[cod], "OcorrenciaVendas": 1})
                    else:
                        r["sem_historico"].append(cod)
                return r
            raise AssertionError(f"rota inesperada {rota}")

        self.estoque_resposta = None       # revisão: resposta fixa da porta (ex.: {} sem itens)
        self.estoque_recusa = set()        # revisão: código que faz a porta recusar a chamada inteira (422)
        self.estoque_tem_cadastro = {}     # revisão: código -> valor de tem_cadastro

        def porta_post(codigos, revendas):
            corpo = {"codigos": list(codigos), "revendas": list(revendas)}
            self.chamadas.append(("estoque/lote", corpo))
            if set(codigos) & self.estoque_recusa:
                e = S.PortaErro("HTTP 422: código inválido")
                e.status = 422
                raise e
            if self.estoque_resposta is not None:
                return self.estoque_resposta
            itens = []
            for cod in corpo["codigos"]:
                for rev in corpo["revendas"]:
                    sem = cod in self.estoque_sem_resposta
                    itens.append({"codigo": cod, "revenda": rev, "disponivel": None if sem else 3.0 * rev,
                                  "tem_cadastro": self.estoque_tem_cadastro.get(cod, True),
                                  "erro": "casa sem resposta" if sem else None})
            return {"ok": True, "itens": itens}

        S._exp_porta_get = porta_get
        S._apollo_estoque_lote_http = porta_post
        S.compras_salvar_fornecedor("TECFIL", {"palavras": ["TECFIL"], "grupos": "tecfil", "pedido_minimo": 0,
                                               "prazo": 7, "cobertura": 30}, "t")

    def tearDown(self):
        (S._exp_porta_get, S._apollo_estoque_lote_http, S.CHAVE_CRM, S.LINX_API_USUARIO,
         S.APOLLO_ESTOQUE_ESPERA_S, S._linx_noite_acabou) = self._orig
        S._apollo_lote_zerar()
        super().tearDown()

    def rodar(self, **kw):
        return S.apollo_grupo_rodada("TECFIL", "", pausa=0, **kw)

    def rotas(self, nome):
        return [p for r, p in self.chamadas if r == nome]

    def payload(self, grupos="TECFIL", categorias="", pode_iniciar=True, fornecedor="TECFIL"):
        return S.compras_demanda_payload(fornecedor, 30, "1", grupos, categorias, pode_iniciar=pode_iniciar)


class Filtros(Base):

    def test_lista_normaliza_e_chave_nao_depende_da_ordem(self):
        self.assertEqual(S.apollo_filtro_lista(" tecfil, Wega ,TECFIL"), ["TECFIL", "WEGA"])
        self.assertEqual(S.apollo_filtro_chave("WEGA,TECFIL", "15,111"), S.apollo_filtro_chave("tecfil,wega", "111,15"))
        self.assertEqual(S.apollo_filtro_chave("", ""), "")

    def test_fornecedor_guarda_grupo_e_categoria(self):
        f = next(x for x in S.compras_fornecedores() if x["nome"] == "TECFIL")
        self.assertEqual(f["grupos"], ["TECFIL"])
        self.assertEqual(f["categorias"], [])
        S.compras_salvar_fornecedor("LUBRIFICANTES", {"categorias": "111,15"}, "t")   # só categoria já basta

    def test_grupos_e_categorias_da_porta(self):
        d = S.apollo_filtros()
        self.assertEqual([g["nome"] for g in d["grupos"]], ["TECFIL", "WEGA"])
        self.assertEqual(d["sem_categoria_lida"], 10)


class Leitura(Base):

    def test_le_todas_as_paginas(self):
        self.por_pagina_forcado = 2
        r = S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["pecas"], 3)
        self.assertEqual(len(self.rotas("pecas")), 2)

    def test_falha_no_meio_nao_apaga_a_lista_anterior(self):
        S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.por_pagina_forcado = 2
        self.falha_pagina = 2
        r = S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.assertFalse(r["ok"])
        chave = S.apollo_filtro_chave("TECFIL", "")
        self.assertEqual(len(S.apollo_pecas_do_filtro(chave)), 3)
        self.assertIn("cópia", S.apollo_filtro_info(chave)["erro"])

    def test_grupo_grande_demais_e_recusado(self):
        orig = S.APOLLO_PECAS_MAX
        S.APOLLO_PECAS_MAX = 2
        try:
            r = S.apollo_pecas_ler("TECFIL", "", pausa=0)
        finally:
            S.APOLLO_PECAS_MAX = orig
        self.assertFalse(r["ok"])
        self.assertIn("categoria", r["erro"])
        self.assertEqual(S.apollo_pecas_do_filtro(S.apollo_filtro_chave("TECFIL", "")), [])


class SoOQueVendeu(Base):

    def test_estoque_so_de_quem_vendeu(self):
        r = self.rodar()
        self.assertTrue(r["ok"], r)
        lotes = self.rotas("estoque/lote")
        self.assertEqual(len(lotes), 1)
        self.assertEqual(set(lotes[0]["codigos"]), {"PSL55", "ARL4147"})
        self.assertEqual(r["venderam"], 2)

    def test_peca_sem_venda_nao_e_perguntada_de_novo(self):
        self.rodar()
        self.chamadas.clear()
        self.rodar(forcar=True)   # botão "Puxar do Apollo"
        perguntadas = {c for p in self.rotas("demanda") for c in p["codigos"].split(",")}
        self.assertNotIn("TEC999", perguntadas)
        self.assertEqual(perguntadas, {"PSL55", "ARL4147"})

    def test_noite_tambem_pula_a_sem_venda(self):
        S.apollo_filtro_marcar_uso("TECFIL", "")
        self.rodar()
        self.assertNotIn("TEC999", S.apollo_codigos_em_uso())
        self.assertIn("PSL55", S.apollo_codigos_em_uso())

    def test_tela_so_mostra_quem_vendeu_e_so_do_apollo(self):
        self.rodar()
        d = self.payload()
        cods = {x["codigo"] for x in d["itens"]}
        self.assertEqual(cods, {"PSL55", "ARL4147"})
        self.assertNotIn("WG100", cods, "a palavra TECFIL na descrição não puxa peça da WEGA")
        self.assertEqual(d["apollo"]["sem_venda"], 1)
        item = {x["codigo"]: x for x in d["itens"]}["PSL55"]
        self.assertEqual(item["estoque"]["available"], 3.0)
        self.assertEqual(item["linx_total"], 4)

    def test_sem_grupo_continua_pelas_regras(self):
        S.save_storage("precificador_online_v1", None, json.dumps({"products": [
            {"sku": "WG100", "produto": "FILTRO WEGA TECFIL", "custo": 2.0}]}), False, actor="t", actor_role="admin")
        d = S.compras_demanda_payload("TECFIL", 30, "1")
        self.assertIsNone(d["apollo"])
        self.assertEqual({x["codigo"] for x in d["itens"]}, {"WG100"})


class Estoque(Base):

    def test_sem_resposta_nunca_vira_zero(self):
        self.estoque_sem_resposta = {"PSL55"}
        self.rodar()
        g = S.compras_estoque_guardado(["PSL55", "ARL4147"])
        self.assertNotIn("PSL55", g, "nenhuma casa respondeu: nada gravado (nunca zero, e a leitura anterior fica)")
        self.assertEqual(g["ARL4147"]["available"], 3.0)


class Tela(Base):

    def test_categoria_incompleta_avisa(self):
        S.apollo_grupo_rodada("", "111", pausa=0)
        d = self.payload("", "111")
        self.assertTrue(d["apollo"]["incompleta"])

    def test_filtro_nunca_lido_inicia_sozinho(self):
        chamado = []
        orig = S.apollo_grupo_iniciar
        S.apollo_grupo_iniciar = lambda g, c, forcar=False: chamado.append((g, c)) or True
        try:
            d = self.payload()
        finally:
            S.apollo_grupo_iniciar = orig
        self.assertEqual(chamado, [("TECFIL", "")])
        self.assertTrue(d["apollo"]["iniciado"])
        self.assertEqual(d["itens"], [])

    def test_rotas(self):
        html = (S.APP_DIR / "compras.html").read_text(encoding="utf-8")
        self.assertIn("filtroGrupo", html)
        self.assertIn("/api/compras/apollo/puxar", html)
        self.assertIn("/api/compras/apollo/puxar", S.COMPRAS_POST_SO_GESTOR)


class Revisao(Base):
    """revisar-antes-de-subir da V20.44 (09/11, "corrigir todos")."""

    def iniciar_contado(self):
        chamado = []
        orig = S.apollo_grupo_iniciar
        S.apollo_grupo_iniciar = lambda g, c, forcar=False: chamado.append((g, c)) or True
        self.addCleanup(setattr, S, "apollo_grupo_iniciar", orig)
        return chamado

    # achado 1: leitura que falha não vira laço
    def test_erro_recente_nao_reinicia_sozinho(self):
        self.falha_pagina = 1
        S.apollo_pecas_ler("TECFIL", "", pausa=0)
        chamado = self.iniciar_contado()
        d = self.payload()
        self.assertEqual(chamado, [])
        self.assertFalse(d["apollo"]["iniciado"])
        self.assertIn("cópia", d["apollo"]["erro"])

    # achado 2: Compras/Consulta não disparam a leitura nem gravam filtro
    def test_quem_nao_e_gestor_nao_inicia_nem_marca_uso(self):
        chamado = self.iniciar_contado()
        d = self.payload(pode_iniciar=False)
        self.assertEqual(chamado, [])
        self.assertFalse(d["apollo"]["iniciado"])
        self.assertFalse(any(f["usado_em"] for f in S.apollo_filtros_em_uso()), "o GET não marca uso")

    def test_grupo_que_nao_existe_no_apollo_e_recusado(self):
        chamado = self.iniciar_contado()
        d = self.payload("XPTO")
        self.assertEqual(chamado, [])
        self.assertIn("XPTO", d["apollo"]["erro"])
        self.assertFalse(any(f["usado_em"] for f in S.apollo_filtros_em_uso()), "o GET não marca uso")
        with self.assertRaises(ValueError):
            S.apollo_filtro_validar("XPTO", "")
        with self.assertRaises(ValueError):
            S.apollo_filtro_validar("TECFIL", "abc")      # categoria é código numérico
        S.apollo_filtro_validar("tecfil, WEGA", "111,15")

    # achado 3: limite de 6/min dividido com o estoque dos anúncios (V20.42)
    def test_usa_o_contador_da_v2042_e_deixa_folga(self):
        S.apollo_estoque_lote(["PSL55"], pausa=0)
        self.assertEqual(len(S._APOLLO_LOTE_CHAMADAS), 1, "a chamada da demanda entra na mesma conta")
        S._APOLLO_LOTE_CHAMADAS[:] = [S.time.monotonic()] * (S.ANUNCIO_APOLLO_LOTE_POR_MINUTO - 1)
        self.chamadas.clear()
        r = S.apollo_estoque_lote(["ARL4147"], pausa=0)
        self.assertEqual(self.rotas("estoque/lote"), [], "deixa a vaga dos anúncios")
        self.assertFalse(r["ok"])
        self.assertEqual(S.compras_estoque_guardado(["ARL4147"]), {})

    # achado 4: código longo / recusado não derruba o lote
    def test_codigo_longo_fica_fora_e_recusado_nao_derruba(self):
        longo = "X" * 31
        self.estoque_recusa = {"RUIM1"}
        r = S.apollo_estoque_lote(["PSL55", longo, "RUIM1"], pausa=0)
        pedidos = {c for p in self.rotas("estoque/lote") for c in p["codigos"]}
        self.assertNotIn(longo, pedidos)
        g = S.compras_estoque_guardado(["PSL55", "RUIM1", longo])
        self.assertEqual(set(g), {"PSL55"})
        self.assertEqual(r["fora"], 2)

    # achado 5: marca à mão para outro fornecedor vence o grupo
    def test_marca_a_mao_de_outro_fornecedor_sai_do_grupo(self):
        S.compras_salvar_fornecedor("WEGA", {"grupos": "WEGA"}, "t")
        S.compras_marca_manual_salvar("PSL55", "WEGA", "t")
        self.rodar()
        self.assertNotIn("PSL55", {x["codigo"] for x in self.payload()["itens"]})

    # achado 6: resposta ruim não apaga estoque bom; tem_cadastro como na V20.42
    def gravar_bom(self, cod):
        S.apollo_estoque_lote([cod], pausa=0)
        S._apollo_lote_zerar()
        self.assertEqual(S.compras_estoque_guardado([cod])[cod]["available"], 3.0)

    def test_resposta_sem_itens_nao_apaga(self):
        self.gravar_bom("PSL55")
        self.estoque_resposta = {}
        r = S.apollo_estoque_lote(["PSL55"], pausa=0)
        self.assertFalse(r["ok"])
        self.assertEqual(S.compras_estoque_guardado(["PSL55"])["PSL55"]["available"], 3.0)

    def test_codigo_que_nao_voltou_nao_apaga(self):
        self.gravar_bom("PSL55")
        self.estoque_resposta = {"ok": True, "itens": []}
        S.apollo_estoque_lote(["PSL55"], pausa=0)
        self.assertEqual(S.compras_estoque_guardado(["PSL55"])["PSL55"]["available"], 3.0)

    def test_tem_cadastro_null_nao_vira_zero(self):
        self.estoque_tem_cadastro = {"PSL55": None}
        S.apollo_estoque_lote(["PSL55"], pausa=0)
        self.assertEqual(S.compras_estoque_guardado(["PSL55"]), {})

    # achado 7: lista vazia ou cortada não substitui a anterior
    def test_lista_vazia_nao_substitui(self):
        S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.catalogo = [x for x in self.catalogo if x[2] != "TECFIL"]
        r = S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.assertFalse(r["ok"])
        self.assertEqual(len(S.apollo_pecas_do_filtro(S.apollo_filtro_chave("TECFIL", ""))), 3)

    def test_lista_cortada_e_recusada(self):
        orig = self.catalogo
        porta = S._exp_porta_get

        def curta(rota, params):
            r = porta(rota, params)
            if rota == "pecas":
                r = {**r, "total": 30}
            return r
        S._exp_porta_get = curta
        r = S.apollo_pecas_ler("TECFIL", "", pausa=0)
        self.assertFalse(r["ok"])
        self.assertIn("incompleta", r["erro"])
        self.catalogo = orig

    # achado 8: não lida ≠ sem venda; Linx ocupada avisa
    def test_peca_ainda_nao_lida_e_contada_a_parte(self):
        S.apollo_pecas_ler("TECFIL", "", pausa=0)
        a = self.payload()["apollo"]
        self.assertEqual(a["nao_lidas"], 3)
        self.assertEqual(a["sem_venda"], 0)

    def test_linx_ocupada_nao_finge_que_leu(self):
        self.assertTrue(S.LINX_DEMANDA_LOCK.acquire(blocking=False))
        try:
            r = self.rodar()
        finally:
            S.LINX_DEMANDA_LOCK.release()
        self.assertFalse(r["ok"])
        self.assertIn("rodando", r["erro"])

    # achado 9: à noite o estoque recente não é relido
    def test_noite_nao_rele_estoque_recente(self):
        S.apollo_filtro_marcar_uso("TECFIL", "")
        self.rodar()
        self.chamadas.clear()
        S._apollo_lote_zerar()
        S.apollo_grupos_noite()
        self.assertEqual(self.rotas("estoque/lote"), [])

    # achado 10: a rodada da noite para quando a janela acaba
    def test_rodada_da_noite_para_no_fim_da_janela(self):
        S._linx_noite_acabou = lambda: True
        S.linx_demanda_rodada(pausa=0, codigos=["PSL55"], noite=True)
        self.assertEqual(self.rotas("demanda"), [])

    # achado 11: fornecedor só com grupo continua com marca na geral/Ruptura e na rodada da Linx
    def test_fornecedor_com_grupo_tem_marca_nas_outras_telas(self):
        self.rodar()
        produtos = S._compras_produtos_precificador()
        marcas = S._compras_regras_marca(produtos)
        self.assertEqual(marcas("PSL55", ""), ["TECFIL"])
        self.assertEqual(marcas("WG100", "FILTRO WEGA TECFIL"), [], "a palavra não vale com grupo")
        self.assertIn("PSL55", S._compras_codigos_dos_fornecedores("TECFIL"))

    # achado 12: e-commerce de peça que não está no Precificador conta
    def test_venda_do_ecommerce_de_peca_fora_do_precificador(self):
        self.vendas = {}
        hoje = S.datetime.now(S.BR_TZ).date().isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,amount,updated_at) "
                         "VALUES('ml','1','L1',?,?,'PSL55',3,90,?)", (hoje + "T10:00:00", hoje, hoje))
            S.DB.commit()
        self.rodar()
        itens = {x["codigo"]: x for x in self.payload()["itens"]}
        self.assertIn("PSL55", itens)
        self.assertEqual(itens["PSL55"]["vendido"], 3)


if __name__ == "__main__":
    unittest.main()
