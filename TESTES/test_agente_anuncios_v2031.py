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
