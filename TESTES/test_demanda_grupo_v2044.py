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
        self._orig = (S._exp_porta_get, S._exp_porta, S.CHAVE_CRM, S.LINX_API_USUARIO)
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

        def porta_post(rota, corpo, escrita=False):
            self.chamadas.append((rota, json.loads(json.dumps(corpo))))
            self.assertEqual(rota, "estoque/lote")
            self.assertFalse(escrita)
            itens = []
            for cod in corpo["codigos"]:
                for rev in corpo["revendas"]:
                    sem = cod in self.estoque_sem_resposta
                    itens.append({"codigo": cod, "revenda": rev, "disponivel": None if sem else 3.0 * rev,
                                  "tem_cadastro": True, "erro": "casa sem resposta" if sem else None})
            return {"ok": True, "itens": itens}

        S._exp_porta_get = porta_get
        S._exp_porta = porta_post
        S.compras_salvar_fornecedor("TECFIL", {"palavras": ["TECFIL"], "grupos": "tecfil", "pedido_minimo": 0,
                                               "prazo": 7, "cobertura": 30}, "t")

    def tearDown(self):
        S._exp_porta_get, S._exp_porta, S.CHAVE_CRM, S.LINX_API_USUARIO = self._orig
        super().tearDown()

    def rodar(self, **kw):
        return S.apollo_grupo_rodada("TECFIL", "", pausa=0, **kw)

    def rotas(self, nome):
        return [p for r, p in self.chamadas if r == nome]

    def payload(self, grupos="TECFIL", categorias=""):
        return S.compras_demanda_payload("TECFIL", 30, "1", grupos, categorias)


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
        self.assertFalse(g["PSL55"]["stock_known"])
        self.assertIsNone(g["PSL55"]["available"])
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


if __name__ == "__main__":
    unittest.main()
