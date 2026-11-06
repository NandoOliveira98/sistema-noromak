"""V20.31 - Agente de anuncios: o agente do Estudio (Claude no PC do dono) propoe titulos, descricao e
atributos no rascunho do anuncio do ML; o Admin aceita. Nada escreve no ML (Conferir/Publicar so no clique).

Decisoes do dono (06/11): docs/superpowers/specs/2026-11-06-agente-anuncios-design.md."""

import json
from pathlib import Path
from unittest import mock

from base import S, SENHA_ADMIN, TesteNoromak, Cliente

JPG = b"\xff\xd8\xff\xe0" + b"0" * 64


def foto_na_ficha(codigo, nome="f1.jpg"):
    S.ANUNCIO_FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    (S.ANUNCIO_FOTOS_DIR / nome).write_bytes(JPG)
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO anuncio_foto_v2024(ficha_codigo,arquivo,tipo,largura,altura,bytes,origem,ordem,criado_em) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (S.normalize(codigo), nome, "jpg", 800, 800, len(JPG), "pc", 0, S.now_iso()))
        S.DB.commit()


def cadastrar_kit(sku_kit, componentes):
    atual, _, _ = S.get_storage("conf_kits")
    kits = [{"sku_kit": sku_kit, "nome": "Kit teste", "ativo": True,
             "componentes": [{"sku": s, "quantidade": q} for s, q in componentes]}]
    S.save_storage("conf_kits", atual, json.dumps(kits), False, actor="admin_teste", actor_role="admin",
                   source_screen="teste", change_method="teste", client_ip="127.0.0.1")


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        S.ficha_salvar("ABC123", {"nome": "Limpador X", "marca": "Vonixx"}, "admin", None)
        foto_na_ficha("ABC123")
        self.rid = S.anuncio_rascunho_salvar({"codigo": "ABC123", "conta": "aracatuba"}, "admin", None)["id"]

    def trabalho(self, tid=None):
        with S.DB_LOCK:
            if tid is None:
                row = S.DB.execute("SELECT * FROM imagem_trabalho_v2017 ORDER BY id DESC LIMIT 1").fetchone()
            else:
                row = S.DB.execute("SELECT * FROM imagem_trabalho_v2017 WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None


class Pedido(Base):

    def test_pedido_entra_na_fila_com_as_fotos_da_ficha(self):
        r = S.anuncio_agente_pedir(self.rid, "admin")
        t = self.trabalho(r["trabalho_id"])
        self.assertEqual(t["tipo"], "anuncio")
        self.assertEqual(t["situacao"], "aguardando")
        self.assertEqual(t["rascunho_id"], self.rid)
        arquivos = json.loads(t["arquivos_json"])
        self.assertEqual([a["papel"] for a in arquivos], ["original"])
        self.assertTrue((S._imagem_pasta(t["id"]) / arquivos[0]["nome"]).is_file())
        self.assertEqual(json.loads(t["produto_json"])["ficha"]["marca"], "Vonixx")

    def test_segundo_pedido_do_mesmo_rascunho_e_recusado(self):
        S.anuncio_agente_pedir(self.rid, "admin")
        with self.assertRaises(S.ImagemConflito):
            S.anuncio_agente_pedir(self.rid, "admin")

    def test_rascunho_publicado_nao_pede(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_rascunho_v2024 SET situacao='publicado' WHERE id=?", (self.rid,))
            S.DB.commit()
        with self.assertRaises(ValueError):
            S.anuncio_agente_pedir(self.rid, "admin")

    def test_kit_leva_as_pecas_e_peca_sem_ficha_vai_so_com_codigo(self):
        cadastrar_kit("KITX", [("ABC123", 1), ("ZZZ999", 1)])
        S.ficha_salvar("KITX", {"nome": "Kit X"}, "admin", None)
        rid = S.anuncio_rascunho_salvar({"codigo": "KITX", "conta": "aracatuba"}, "admin", None)["id"]
        candidatos = {"ZZZ999": {"codigo": "ZZZ999", "nome": "Pano", "fonte": "precificador"}}
        with mock.patch.object(S, "_anuncio_produtos_candidatos", return_value=candidatos):
            r = S.anuncio_agente_pedir(rid, "admin")
        t = self.trabalho(r["trabalho_id"])
        kit = json.loads(t["produto_json"])["kit"]
        self.assertEqual([p["sku"] for p in kit], ["ABC123", "ZZZ999"])
        self.assertEqual(kit[0]["ficha"]["marca"], "Vonixx")
        self.assertEqual(kit[1], {"sku": "ZZZ999", "quantidade": 1, "descricao": "Pano"})
        papeis = [a["papel"] for a in json.loads(t["arquivos_json"])]
        self.assertEqual(papeis.count("peca"), 1, "a foto da peca com ficha vai junto")

    def test_so_perfil_admin_pede(self):
        with S.DB_LOCK:
            S.upsert_app_user("supervisor_a", "Supervisor A", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("supervisor_a", SENHA_ADMIN, area="admin")
        st, _ = sup.pedir("POST", "/api/anuncios/agente/pedir", {"rascunho_id": self.rid})
        self.assertEqual(st, 403)
        st, corpo = self.cliente.pedir("POST", "/api/anuncios/agente/pedir", {"rascunho_id": self.rid})
        self.assertEqual(st, 200, corpo)
        st, corpo = self.cliente.pedir("GET", f"/api/anuncios/agente?rascunho={self.rid}")
        self.assertEqual(st, 200, corpo)
        self.assertEqual(corpo["trabalho"]["situacao"], "aguardando")


ATRIBUTOS_ML = {"categoria": "MLB1234", "nome": "Limpadores", "atributos": [
    {"id": "BRAND", "obrigatorio": True, "condicional": False, "valores": []},
    {"id": "VOLUME", "obrigatorio": True, "condicional": False, "valores": []},
    {"id": "FORMAT", "obrigatorio": True, "condicional": False, "valores": [{"id": "1", "nome": "Líquido"}]},
    {"id": "COLOR", "obrigatorio": False, "condicional": False, "valores": []}]}
SUGERIDAS = [{"categoria": "MLB1234", "categoria_nome": "Limpadores"}, {"categoria": "MLB999", "categoria_nome": "Outros"}]
TITULOS = [{"texto": "Limpador X Vonixx 500ml", "recomendado": True},
           {"texto": "Limpador Multiuso X Vonixx 500ml", "recomendado": False},
           {"texto": "Limpador X 500ml Vonixx Original", "recomendado": False}]


class Etapas(Base):

    def setUp(self):
        super().setUp()
        for nome, valor in (("ml_categoria_sugerir", SUGERIDAS), ("ml_categoria_atributos", ATRIBUTOS_ML)):
            p = mock.patch.object(S, nome, return_value=valor)
            self.mocks = getattr(self, "mocks", {})
            self.mocks[nome] = p.start()
            self.addCleanup(p.stop)
        self.tid = S.anuncio_agente_pedir(self.rid, "admin")["trabalho_id"]

    def textos(self, titulos=TITULOS, **extra):
        t = S.imagens_agente_proximo()["trabalho"]
        self.assertEqual(t["etapa"], "anuncio_textos")
        return S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "titulos": titulos,
                                          "descricao": "Limpador.\n\nBENEFÍCIOS\n- limpa", "fontes": {}, **extra})

    def proposta(self):
        return json.loads(self.trabalho(self.tid)["textos_json"])

    def test_proximo_entrega_a_etapa_de_textos(self):
        t = S.imagens_agente_proximo()["trabalho"]
        self.assertEqual(t["etapa"], "anuncio_textos")
        self.assertEqual(t["produto"]["ficha"]["marca"], "Vonixx")

    def test_textos_viram_categoria_e_pedem_os_atributos(self):
        self.textos()
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aguardando_atributos")
        self.assertEqual(self.proposta()["categoria"], "MLB1234")
        self.mocks["ml_categoria_sugerir"].assert_called_with("Limpador X Vonixx 500ml", "aracatuba")
        t = S.imagens_agente_proximo()["trabalho"]
        self.assertEqual(t["etapa"], "anuncio_atributos")
        self.assertEqual([a["id"] for a in t["atributos"]], ["BRAND", "VOLUME", "FORMAT"])
        self.assertEqual(len(t["proposta"]["titulos"]), 3)

    def test_titulo_acima_de_60_sai_da_lista(self):
        longo = {"texto": "X" * 61, "recomendado": False}
        self.textos(titulos=TITULOS[:2] + [longo])
        self.assertEqual([t["texto"] for t in self.proposta()["titulos"]], [TITULOS[0]["texto"], TITULOS[1]["texto"]])

    def test_ml_sem_resposta_deixa_a_proposta_so_com_textos(self):
        self.mocks["ml_categoria_sugerir"].side_effect = RuntimeError("ML fora do ar")
        self.textos()
        t = self.trabalho(self.tid)
        self.assertEqual(t["situacao"], "proposta")
        self.assertTrue(t["mensagem"].startswith("Categoria não lida no ML"))
        self.assertEqual(len(self.proposta()["titulos"]), 3)
        self.assertIn("BENEFÍCIOS", self.proposta()["descricao"])

    def test_atributo_fora_da_lista_e_descartado(self):
        self.textos()
        t = S.imagens_agente_proximo()["trabalho"]
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_atributos", "atributos": [
            {"id": "BRAND", "valor": "Vonixx", "origem": "ficha"},
            {"id": "FORMAT", "valor": "Gel", "origem": "rotulo"},
            {"id": "COLOR", "valor": "Azul", "origem": "rotulo"}]})
        self.assertEqual(self.trabalho(self.tid)["situacao"], "proposta")
        attrs = {a["id"]: a for a in self.proposta()["atributos"]}
        self.assertEqual(set(attrs), {"BRAND"})
        self.assertEqual(attrs["BRAND"]["origem"], "ficha")

    def test_valor_da_lista_fica_com_o_id(self):
        self.textos()
        t = S.imagens_agente_proximo()["trabalho"]
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_atributos", "atributos": [
            {"id": "FORMAT", "valor_id": "1", "origem": "rotulo"}]})
        attrs = {a["id"]: a for a in self.proposta()["atributos"]}
        self.assertEqual((attrs["FORMAT"]["valor_id"], attrs["FORMAT"]["valor"]), ("1", "Líquido"))

    def test_entrega_de_trabalho_apagado_e_409(self):
        t = S.imagens_agente_proximo()["trabalho"]
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET apagado_em=? WHERE id=?", (S.now_iso(), t["id"]))
            S.DB.commit()
        with self.assertRaises(S.ImagemConflito):
            S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "titulos": TITULOS, "descricao": "x"})

    def test_gasto_marcado_como_anuncio(self):
        self.textos(tokens_entrada=1000, tokens_saida=500, modelo="claude-haiku-5-5")
        with S.DB_LOCK:
            origens = [r[0] for r in S.DB.execute("SELECT origem FROM imagem_gasto_v2017").fetchall()]
        self.assertEqual(origens, ["anuncio"])
        self.assertGreater(S.imagens_saldo()["por_origem"]["anuncio"], 0)

    def test_erro_do_agente_vira_erro_do_trabalho(self):
        t = S.imagens_agente_proximo()["trabalho"]
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "situacao": "erro", "mensagem": "Claude caiu"})
        self.assertEqual(self.trabalho(self.tid)["situacao"], "erro")

    def test_trocar_categoria_pede_so_os_atributos(self):
        self.mocks["ml_categoria_sugerir"].side_effect = RuntimeError("ML fora do ar")
        self.textos()
        S.anuncio_agente_trocar_categoria(self.tid, "MLB999", "admin")
        t = self.trabalho(self.tid)
        self.assertEqual(t["situacao"], "aguardando_atributos")
        self.assertEqual(self.proposta()["categoria"], "MLB999")
        self.assertEqual(len(self.proposta()["titulos"]), 3)

    def test_reserva_vencida_volta_para_onde_estava(self):
        self.textos()
        S.imagens_agente_proximo()  # pega a etapa de atributos
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET reservado_ate=1 WHERE id=?", (self.tid,))
            S.DB.commit()
        t = S.imagens_agente_proximo()["trabalho"]
        self.assertEqual(t["etapa"], "anuncio_atributos")
