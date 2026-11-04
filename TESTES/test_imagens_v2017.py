"""V20.17 — Estúdio de Imagens (decisões do dono, 04/11; desenho em
docs/superpowers/specs/2026-11-04-estudio-imagens-design.md).

O servidor só guarda os arquivos numa pasta e cuida da fila; quem processa é o agente no PC do
dono (Real-ESRGAN + Claude). O que não pode quebrar:
- foto vai para o disco, nunca para o banco; tipo conferido pelo conteúdo; limite de tamanho;
- módulo próprio "imagens" (Admin nasce com ele; Consulta só vê; Compras não tem);
- o agente entra só com o token dele, e o token nunca sai pelo /api/storage;
- um trabalho é entregue a um agente por vez; reserva vencida volta para a fila;
- arte só é gerada depois que o operador aprova os textos; versão antiga = 409;
- apagar remove a pasta e mantém a linha (histórico); nada é apagado sozinho;
- espaço usado e "agente offline" aparecem na tela e no Painel de saúde.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from base import S, SENHA_ADMIN, USUARIO_ADMIN, Cliente, TesteNoromak

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


class EstudioImagens(TesteNoromak):

    # ---------- auxiliares ----------

    def usuario(self, login: str, role: str = "operador", permissions: dict | None = None) -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), role, True, SENHA_ADMIN, "teste", permissions=permissions)
            S.DB.commit()
        c = Cliente(self.url_base)
        c.entrar(login, SENHA_ADMIN, area="portal")
        return c

    def token(self) -> str:
        st, corpo = self.cliente.pedir("GET", "/api/imagens/agente/config")
        self.assertEqual(st, 200, corpo)
        return corpo["token"]

    def agente(self, metodo: str, rota: str, corpo: dict | None = None, token: str | None = None,
               bruto: bool = False):
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + (self.token() if token is None else token))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                texto = r.read()
                return r.status, (texto if bruto else json.loads(texto or b"{}"))
        except urllib.error.HTTPError as e:
            texto = e.read()
            try:
                return e.code, json.loads(texto or b"{}")
            except json.JSONDecodeError:
                return e.code, {"corpo": texto.decode("utf-8", "replace")}

    def baixar(self, c: Cliente, rota: str) -> tuple[int, bytes, str]:
        req = urllib.request.Request(self.url_base + rota, headers={"Cookie": c.cookie})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read(), r.headers.get("Content-Disposition") or ""
        except urllib.error.HTTPError as e:
            return e.code, e.read(), ""

    def enviar(self, tipo: str = "principal", fotos: list[bytes] | None = None, sku: str = "0893600022",
               c: Cliente | None = None):
        fotos = fotos if fotos is not None else [PNG]
        corpo = {"tipo": tipo, "sku": sku,
                 "fotos": [{"nome": f"foto{i}.png", "dados": b64(f)} for i, f in enumerate(fotos)]}
        return (c or self.cliente).pedir("POST", "/api/imagens/enviar", corpo)

    def lista(self, c: Cliente | None = None) -> dict:
        st, corpo = (c or self.cliente).pedir("GET", "/api/imagens")
        self.assertEqual(st, 200, corpo)
        return corpo

    def item(self, id_: int) -> dict:
        return next(x for x in self.lista()["itens"] if x["id"] == id_)

    # ---------- envio e disco ----------

    def test_envio_grava_no_disco_e_nao_no_banco(self):
        st, corpo = self.enviar(fotos=[PNG, JPG])
        self.assertEqual(st, 200, corpo)
        # Foto principal: cada foto vira um trabalho.
        self.assertEqual(len(corpo["ids"]), 2)
        for id_ in corpo["ids"]:
            pasta = S.IMAGENS_DIR / str(id_)
            self.assertTrue(pasta.is_dir())
            self.assertEqual(len(list(pasta.iterdir())), 1)
            self.assertEqual(self.item(id_)["situacao"], "aguardando")
        with S.DB_LOCK:
            linhas = S.DB.execute("SELECT * FROM imagem_trabalho_v2017").fetchall()
        for row in linhas:
            for valor in dict(row).values():
                self.assertNotIn("iVBOR", str(valor or ""))  # base64 de PNG nunca vai ao banco

    def test_arte_de_kit_junta_as_fotos_num_trabalho_so(self):
        st, corpo = self.enviar("juntos", [PNG, JPG, WEBP])
        self.assertEqual((st, len(corpo["ids"])), (200, 1))
        self.assertEqual(len(self.item(corpo["ids"][0])["arquivos"]), 3)
        # Kit juntos sai sem texto: vai direto para o agente, como a foto principal.
        self.assertEqual(self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]["etapa"], "principal")

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        st, corpo = self.enviar(fotos=[b"%PDF-1.4 nao sou imagem"])
        self.assertEqual(st, 400, corpo)
        st, corpo = self.enviar(fotos=[b"MZ\x90\x00 executavel"])
        self.assertEqual(st, 400, corpo)
        self.assertEqual(self.lista()["itens"], [])

    def test_limites_de_tamanho_quantidade_e_tipo(self):
        grande = b"\x89PNG\r\n\x1a\n" + b"\x00" * (S.IMAGEM_MAX_BYTES + 1)
        self.assertEqual(self.enviar(fotos=[grande])[0], 400)
        self.assertEqual(self.enviar(fotos=[PNG] * (S.IMAGEM_MAX_FOTOS + 1))[0], 400)
        self.assertEqual(self.enviar("inventado")[0], 400)
        self.assertEqual(self.enviar(fotos=[])[0], 400)
        self.assertEqual(self.enviar("juntos", [PNG])[0], 400)  # kit precisa de 2+ fotos
        self.assertEqual(self.enviar("beneficios", [PNG, PNG, PNG])[0], 400)  # frente e verso no máximo

    # ---------- permissões ----------

    def test_modulo_imagens(self):
        sem = self.usuario("sem_img")
        self.assertEqual(sem.pedir("GET", "/api/imagens")[0], 403)
        self.assertEqual(self.enviar(c=sem)[0], 403)
        com = self.usuario("com_img", permissions={"conferencia": True, "imagens": True})
        self.assertEqual(self.enviar(c=com)[0], 200)
        self.assertEqual(com.pedir("GET", "/api/imagens/agente/config")[0], 403)  # token só do perfil Admin
        # Salvar o usuário sem a chave "imagens" mantém o módulo.
        st, _ = self.cliente.pedir("POST", "/api/roles", {"username": "com_img", "role": "operador",
                                                          "permissions": {"conferencia": True}})
        self.assertEqual(st, 200)
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE username='com_img'").fetchone()
        self.assertTrue(S.user_permissions_from_row(row)["imagens"])

    def test_admin_nasce_com_o_modulo_e_consulta_so_ve(self):
        with S.DB_LOCK:
            row = S.DB.execute("SELECT * FROM app_users WHERE role='admin' LIMIT 1").fetchone()
        self.assertTrue(S.user_permissions_from_row(row)["imagens"])
        consulta = self.usuario("cons_img", role="consulta")
        self.assertEqual(consulta.pedir("GET", "/api/imagens")[0], 200)
        self.assertEqual(self.enviar(c=consulta)[0], 403)
        compras = self.usuario("comp_img", role="compras")
        self.assertEqual(compras.pedir("GET", "/api/imagens")[0], 403)

    # ---------- agente ----------

    def test_agente_sem_token_nao_entra_e_token_nao_sai_pelo_storage(self):
        self.enviar()
        self.assertEqual(self.agente("GET", "/api/imagens/agente/proximo", token="errado")[0], 401)
        self.assertEqual(self.agente("GET", "/api/imagens/agente/proximo", token="")[0], 401)
        token = self.token()
        st, corpo = self.cliente.pedir("GET", "/api/storage?list=1")
        self.assertNotIn(token, json.dumps(corpo))

    def test_reserva_unica_e_vencida_volta_para_a_fila(self):
        st, corpo = self.enviar()
        id_ = corpo["ids"][0]
        st, t1 = self.agente("GET", "/api/imagens/agente/proximo")
        self.assertEqual((st, t1["trabalho"]["id"], t1["trabalho"]["etapa"]), (200, id_, "principal"))
        self.assertEqual(t1["trabalho"]["modelo"], "claude-haiku-5-5")
        st, t2 = self.agente("GET", "/api/imagens/agente/proximo")
        self.assertIsNone(t2["trabalho"])  # reservado: ninguém mais pega
        self.assertEqual(self.item(id_)["situacao"], "processando")
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET reservado_ate=? WHERE id=?", (time.time() - 1, id_))
            S.DB.commit()
        st, t3 = self.agente("GET", "/api/imagens/agente/proximo")
        self.assertEqual(t3["trabalho"]["id"], id_)

    def test_agente_baixa_a_original_e_entrega_o_resultado(self):
        st, corpo = self.enviar(fotos=[JPG])
        id_ = corpo["ids"][0]
        trabalho = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        nome = trabalho["arquivos"][0]["nome"]
        st, dados = self.agente("GET", f"/api/imagens/agente/arquivo?id={id_}&nome={nome}", bruto=True)
        self.assertEqual((st, dados), (200, JPG))
        st, r = self.agente("POST", "/api/imagens/agente/resultado", {
            "id": id_, "situacao": "pronta", "mensagem": "OK",
            "relatorio": {"textos": [{"trecho": "500 g", "ok": True}]},
            "tokens_entrada": 4200, "tokens_saida": 300, "modelo": "claude-haiku-5-5",
            "arquivos": [{"papel": "resultado", "dados": b64(JPG)},
                         {"papel": "zoom", "legenda": "500 g", "dados": b64(PNG)}]})
        self.assertEqual(st, 200, r)
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta")
        self.assertEqual([a["papel"] for a in it["resultados"]], ["resultado", "zoom"])
        st, dados, disp = self.baixar(self.cliente, f"/api/imagens/arquivo?id={id_}&nome={it['resultados'][0]['nome']}")
        self.assertEqual((st, dados), (200, JPG))
        self.assertIn("0893600022_principal", disp)
        # Resultado repetido (agente reenviou) não é aceito: o trabalho já saiu da vez dele.
        st, _ = self.agente("POST", "/api/imagens/agente/resultado",
                            {"id": id_, "situacao": "pronta", "arquivos": [{"papel": "resultado", "dados": b64(JPG)}]})
        self.assertEqual(st, 409)

    def test_download_nao_sai_da_pasta_do_trabalho(self):
        st, corpo = self.enviar()
        id_ = corpo["ids"][0]
        for nome in ("../../expedicao.db", "..\\..\\expedicao.db", "/etc/passwd", "inexistente.png"):
            st, _, _ = self.baixar(self.cliente, f"/api/imagens/arquivo?id={id_}&nome={nome}")
            self.assertEqual(st, 404, nome)

    def test_arte_so_e_gerada_depois_dos_textos_aprovados(self):
        st, corpo = self.enviar("beneficios", [PNG, JPG])
        id_ = corpo["ids"][0]
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        self.assertEqual(t["etapa"], "analise")
        campos = [{"chave": "b1", "rotulo": "Benefício 1", "valor": "Hidrata", "origem": "rotulo"},
                  {"chave": "b2", "rotulo": "Benefício 2", "valor": "", "origem": "nao_encontrado"}]
        st, _ = self.agente("POST", "/api/imagens/agente/analise",
                            {"id": id_, "situacao": "textos_para_aprovar", "textos": {"campos": campos}})
        self.assertEqual(st, 200)
        it = self.item(id_)
        self.assertEqual(it["situacao"], "textos_para_aprovar")
        # Enquanto não aprovar, o agente não recebe a arte para gerar.
        self.assertIsNone(self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"])
        # Aprovar com a versão antiga = 409.
        st, r = self.cliente.pedir("POST", "/api/imagens/textos",
                                   {"id": id_, "versao": it["versao"] - 1, "campos": campos})
        self.assertEqual(st, 409, r)
        campos[1]["valor"] = "Protege"
        st, r = self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": campos})
        self.assertEqual(st, 200, r)
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        self.assertEqual((t["id"], t["etapa"]), (id_, "arte"))
        self.assertEqual([c["valor"] for c in t["textos"]["campos"]], ["Hidrata", "Protege"])
        self.assertEqual(self.item(id_)["textos"]["aprovado_por"], USUARIO_ADMIN)

    def test_foto_que_nao_serve_volta_com_o_motivo(self):
        st, corpo = self.enviar()
        id_ = corpo["ids"][0]
        self.agente("GET", "/api/imagens/agente/proximo")
        st, _ = self.agente("POST", "/api/imagens/agente/resultado",
                            {"id": id_, "situacao": "outra_foto", "mensagem": "Fundo colorido: mande foto em fundo branco."})
        self.assertEqual(st, 200)
        it = self.item(id_)
        self.assertEqual((it["situacao"], it["mensagem"]), ("outra_foto", "Fundo colorido: mande foto em fundo branco."))

    # ---------- refazer, apagar, espaço ----------

    def test_refazer_volta_para_a_fila_e_apaga_o_resultado_antigo(self):
        st, corpo = self.enviar()
        id_ = corpo["ids"][0]
        self.agente("GET", "/api/imagens/agente/proximo")
        self.agente("POST", "/api/imagens/agente/resultado",
                    {"id": id_, "situacao": "revisar", "arquivos": [{"papel": "resultado", "dados": b64(JPG)}]})
        it = self.item(id_)
        st, r = self.cliente.pedir("POST", "/api/imagens/refazer", {"id": id_, "versao": it["versao"], "obs": "letra torta"})
        self.assertEqual(st, 200, r)
        it = self.item(id_)
        self.assertEqual((it["situacao"], it["resultados"]), ("aguardando", []))
        self.assertEqual(len(list((S.IMAGENS_DIR / str(id_)).iterdir())), 1)  # só a original
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        self.assertEqual(t["obs"], "letra torta")

    def test_apagar_remove_a_pasta_e_mantem_o_historico(self):
        st, corpo = self.enviar(fotos=[PNG, PNG])
        a, b = corpo["ids"]
        usado = self.lista()["espaco"]["bytes"]
        self.assertEqual(usado, 2 * len(PNG))
        st, r = self.cliente.pedir("POST", "/api/imagens/apagar", {"ids": [a]})
        self.assertEqual(st, 200, r)
        self.assertFalse((S.IMAGENS_DIR / str(a)).exists())
        self.assertTrue((S.IMAGENS_DIR / str(b)).exists())
        corpo = self.lista()
        self.assertEqual([x["id"] for x in corpo["itens"]], [b])
        self.assertEqual(corpo["espaco"]["bytes"], len(PNG))
        with S.DB_LOCK:
            row = S.DB.execute("SELECT apagado_em, apagado_por FROM imagem_trabalho_v2017 WHERE id=?", (a,)).fetchone()
        self.assertTrue(row["apagado_em"])
        # O agente não recebe trabalho apagado e não consegue entregar resultado dele.
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        self.assertEqual(t["id"], b)
        st, _ = self.agente("POST", "/api/imagens/agente/resultado", {"id": a, "situacao": "pronta"})
        self.assertEqual(st, 409)

    def test_aviso_de_espaco_acima_do_limite(self):
        self.enviar()
        self.assertFalse(self.lista()["espaco"]["aviso"])
        antigo = S.IMAGEM_AVISO_BYTES
        try:
            S.IMAGEM_AVISO_BYTES = 10
            self.assertTrue(self.lista()["espaco"]["aviso"])
        finally:
            S.IMAGEM_AVISO_BYTES = antigo

    # ---------- agente offline e saúde ----------

    def test_agente_offline_na_tela_e_no_painel_de_saude(self):
        self.assertFalse(self.lista()["agente"]["online"])
        saude = {i["id"]: i for i in S.saude_checks()}
        self.assertEqual(saude["imagens_agente"]["nivel"], "ok")  # fila vazia: nada a fazer
        self.enviar()
        saude = {i["id"]: i for i in S.saude_checks()}
        self.assertEqual(saude["imagens_agente"]["nivel"], "atencao")
        self.agente("POST", "/api/imagens/agente/ping", {"versao": "0.1", "computador": "PC-DONO"})
        corpo = self.lista()
        self.assertTrue(corpo["agente"]["online"])
        self.assertEqual(corpo["agente"]["computador"], "PC-DONO")
        saude = {i["id"]: i for i in S.saude_checks()}
        self.assertEqual(saude["imagens_agente"]["nivel"], "ok")

    def test_modelo_e_configuravel_so_pelo_perfil_admin(self):
        self.assertEqual(self.lista()["modelo"], "claude-haiku-5-5")
        com = self.usuario("sup_img", permissions={"conferencia": True, "admin": True, "imagens": True})
        self.assertEqual(com.pedir("POST", "/api/imagens/config", {"modelo": "claude-opus-5-5"})[0], 403)
        self.assertEqual(self.cliente.pedir("POST", "/api/imagens/config", {"modelo": "gpt-qualquer"})[0], 400)
        st, _ = self.cliente.pedir("POST", "/api/imagens/config", {"modelo": "claude-opus-5-5"})
        self.assertEqual(st, 200)
        self.enviar()
        self.assertEqual(self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]["modelo"], "claude-opus-5-5")

    def test_pagina_exige_o_modulo(self):
        sem = self.usuario("sem_pag")
        com = self.usuario("com_pag", permissions={"conferencia": True, "imagens": True})
        for c, esperado in ((sem, 403), (com, 200)):
            st, _, _ = self.baixar(c, "/imagens")
            self.assertEqual(st, esperado)

    # ---------- crédito da API (dono, 04/11: US$ 100 que renova todo dia 19) ----------

    def test_ciclo_do_credito(self):
        from datetime import date
        self.assertEqual(S._imagem_ciclo(date(2026, 11, 4), 19), (date(2026, 10, 19), date(2026, 11, 19)))
        self.assertEqual(S._imagem_ciclo(date(2026, 11, 19), 19), (date(2026, 11, 19), date(2026, 12, 19)))
        self.assertEqual(S._imagem_ciclo(date(2027, 1, 5), 19), (date(2026, 12, 19), date(2027, 1, 19)))
        self.assertEqual(S._imagem_ciclo(date(2027, 2, 10), 31), (date(2027, 1, 31), date(2027, 2, 28)))

    def test_credito_soma_cada_entrega_e_nao_volta_ao_apagar(self):
        saldo = self.lista()["saldo"]
        self.assertEqual((saldo["valor"], saldo["dia"], saldo["usado"]), (100.0, 19, 0.0))
        id_ = self.enviar()[1]["ids"][0]
        self.agente("GET", "/api/imagens/agente/proximo")
        self.agente("POST", "/api/imagens/agente/resultado", {
            "id": id_, "situacao": "pronta", "modelo": "claude-haiku-5-5",
            "tokens_entrada": 1_000_000, "tokens_saida": 1_000_000,
            "arquivos": [{"papel": "resultado", "dados": b64(JPG)}]})
        saldo = self.lista()["saldo"]
        self.assertAlmostEqual(saldo["usado"], 0.60)          # 1M × 0,10 + 1M × 0,50
        self.assertAlmostEqual(saldo["restante"], 99.40)
        self.assertAlmostEqual(saldo["pct"], 0.6)
        # Gasto de um ciclo anterior não conta no atual.
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO imagem_gasto_v2017(dia,quando,modelo,entrada,saida,usd) "
                         "VALUES('2020-01-01','x','claude-haiku-5-5',0,0,50)")
            S.DB.commit()
        self.cliente.pedir("POST", "/api/imagens/apagar", {"ids": [id_]})
        self.assertAlmostEqual(self.lista()["saldo"]["usado"], 0.60)  # apagar não devolve crédito

    def test_credito_configuravel_so_pelo_perfil_admin(self):
        sup = self.usuario("sup_cred", permissions={"conferencia": True, "admin": True, "imagens": True})
        self.assertEqual(sup.pedir("POST", "/api/imagens/config", {"saldo_valor": 50})[0], 403)
        for ruim in ({"saldo_valor": 0}, {"saldo_valor": "abc"}, {"saldo_dia": 32}, {}):
            self.assertEqual(self.cliente.pedir("POST", "/api/imagens/config", ruim)[0], 400, ruim)
        st, r = self.cliente.pedir("POST", "/api/imagens/config", {"saldo_valor": "50,5", "saldo_dia": 5})
        self.assertEqual(st, 200, r)
        saldo = self.lista()["saldo"]
        self.assertEqual((saldo["valor"], saldo["dia"]), (50.5, 5))
        self.assertEqual(self.lista()["modelo"], "claude-haiku-5-5")  # mudar o crédito não mexe no modelo

    # ---------- imagem de referência (dono, 04/11: o agente copia o layout dela) ----------

    def test_referencia_vai_junto_da_arte(self):
        corpo = {"tipo": "beneficios", "sku": "X", "fotos": [{"dados": b64(PNG)}], "referencia": {"dados": b64(WEBP)}}
        st, r = self.cliente.pedir("POST", "/api/imagens/enviar", corpo)
        self.assertEqual(st, 200, r)
        id_ = r["ids"][0]
        arquivos = self.item(id_)["arquivos"]
        self.assertEqual([a["papel"] for a in arquivos], ["frente", "referencia"])
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        ref = next(a for a in t["arquivos"] if a["papel"] == "referencia")
        st, dados = self.agente("GET", f"/api/imagens/agente/arquivo?id={id_}&nome={ref['nome']}", bruto=True)
        self.assertEqual((st, dados), (200, WEBP))

    def test_referencia_so_nas_artes_e_so_imagem(self):
        for tipo in ("principal", "juntos"):
            fotos = [{"dados": b64(PNG)}] * (2 if tipo == "juntos" else 1)
            st, _ = self.cliente.pedir("POST", "/api/imagens/enviar",
                                       {"tipo": tipo, "fotos": fotos, "referencia": {"dados": b64(PNG)}})
            self.assertEqual(st, 400, tipo)
        st, _ = self.cliente.pedir("POST", "/api/imagens/enviar", {"tipo": "modo", "fotos": [{"dados": b64(PNG)}],
                                                                   "referencia": {"dados": b64(b"%PDF-1.4")}})
        self.assertEqual(st, 400)
        self.assertEqual(self.lista()["itens"], [])

    # ---------- nome do produto / link do fornecedor (dono, 04/11: o agente pesquisa na internet) ----------

    def test_nome_e_link_vao_para_o_agente(self):
        corpo = {"tipo": "modo", "fotos": [{"dados": b64(PNG)}], "produto_nome": "  Câmara de freio   43-272 ",
                 "produto_link": "https://fornecedor.com.br/produto/43-272"}
        st, r = self.cliente.pedir("POST", "/api/imagens/enviar", corpo)
        self.assertEqual(st, 200, r)
        id_ = r["ids"][0]
        self.assertEqual(self.item(id_)["produto"], {"nome": "Câmara de freio 43-272",
                                                      "link": "https://fornecedor.com.br/produto/43-272"})
        t = self.agente("GET", "/api/imagens/agente/proximo")[1]["trabalho"]
        self.assertEqual(t["produto"]["link"], "https://fornecedor.com.br/produto/43-272")
        # Textos com a fonte da internet sobrevivem à aprovação.
        campos = [{"chave": "passo_1", "rotulo": "Passo 1", "valor": "Fixe com M16x1,5", "origem": "internet",
                   "fonte": "https://fornecedor.com.br/produto/43-272"}]
        self.agente("POST", "/api/imagens/agente/analise", {"id": id_, "situacao": "textos_para_aprovar",
                                                            "textos": {"campos": campos}, "buscas_web": 2,
                                                            "tokens_entrada": 1000, "tokens_saida": 100})
        it = self.item(id_)
        st, r = self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": campos})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.item(id_)["textos"]["campos"][0]["fonte"], "https://fornecedor.com.br/produto/43-272")
        # Cada busca na internet custa US$ 0,01 além dos tokens.
        self.assertAlmostEqual(self.lista()["saldo"]["usado"], 0.02 + (1000 * 0.10 + 100 * 0.50) / 1_000_000, places=3)

    def test_link_invalido_e_produto_fora_das_artes_sao_recusados(self):
        for link in ("javascript:alert(1)", "ftp://x.com/a", "fornecedor.com.br", "https://a.com/" + "x" * 600,
                     "https://a.com/a b"):
            st, _ = self.cliente.pedir("POST", "/api/imagens/enviar",
                                       {"tipo": "beneficios", "fotos": [{"dados": b64(PNG)}], "produto_link": link})
            self.assertEqual(st, 400, link)
        st, _ = self.cliente.pedir("POST", "/api/imagens/enviar",
                                   {"tipo": "principal", "fotos": [{"dados": b64(PNG)}], "produto_nome": "X"})
        self.assertEqual(st, 400)
        self.assertEqual(self.lista()["itens"], [])

    # ---------- memória da VPS (revisão antes de subir, 04/11) ----------

    def test_soma_das_fotos_acima_de_25_mb_e_recusada(self):
        foto = b"\x89PNG\r\n\x1a\n" + b"\x00" * (9 * 1024 * 1024)      # 3 × 9 MB = 27 MB, cada uma abaixo de 10 MB
        with self.assertRaises(ValueError) as erro:
            S.imagens_enviar("admin_teste", {"tipo": "principal", "fotos": [{"dados": b64(foto)}] * 3})
        self.assertIn("25 MB", str(erro.exception))
        self.assertEqual(self.lista()["itens"], [])

    def test_corpo_grande_e_recusado_antes_de_ser_lido(self):
        # 20 fotos de celular (~100 MB em base64) ocupavam 300+ MB de memória na VPS de 1 GB: o servidor
        # recusa pelo Content-Length, sem ler nem decodificar o corpo.
        import http.client
        from urllib.parse import urlparse
        u = urlparse(self.url_base)
        for rota in ("/api/imagens/enviar", "/api/imagens/agente/resultado"):
            con = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
            con.putrequest("POST", u.path + rota)
            con.putheader("Content-Type", "application/json")
            con.putheader("Content-Length", str(60 * 1024 * 1024))
            if self.cliente.cookie:
                con.putheader("Cookie", self.cliente.cookie)
            con.endheaders()
            con.send(b"{}")                                   # manda só um pedaço e espera a resposta
            resp = con.getresponse()
            self.assertEqual(resp.status, 413, rota)
            con.close()
