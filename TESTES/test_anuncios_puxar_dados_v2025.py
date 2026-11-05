"""V20.25 - Anuncios: puxar traz titulo, categoria, descricao e fotos do anuncio para a ficha.

Caso real (05/11, CAM1019): "Puxei um mas nao veio as informacoes" - so marca, EAN, peso e medidas entravam.
"""

import struct
import zlib
from unittest import mock

from base import S, TesteNoromak, Cliente

SENHA = "SenhaTeste#2024"
SELLERS = {"aracatuba": "111", "bauru": "222"}
FOTO1 = "https://http2.mlstatic.com/D_NQ_NP_111-MLB1-O.jpg"
FOTO2 = "https://http2.mlstatic.com/D_NQ_NP_222-MLB2-O.jpg"


def png(l=600, a=600) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", l, a, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 4)) + chunk(b"IEND", b""))


def item(sku="CAM1019", titulo="Camara de ar aro 26", seller="111", fotos=None, categoria="MLB1234"):
    return {"titulo": titulo, "preco": 10.5, "sku_ml": sku, "status": "active", "sub_status": [], "estoque": 3,
            "categoria": categoria, "tipo": "gold_special", "user_product": "", "produto_catalogo": "",
            "logistica": "", "seller_id": seller, "fotos": list(fotos if fotos is not None else [FOTO1, FOTO2]),
            "atributos": [{"id": "BRAND", "value_name": "Levorin"}]}


class PuxarDadosTeste(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.anuncios = {"aracatuba": {"MLB1000000001": item()},
                         "bauru": {"MLB9000000009": item(sku="BAU55", titulo="Pneu Bauru", seller="222")}}
        self.desc_casas = []
        self.baixados = []
        self.descricoes = {"MLB1000000001": "Camara de ar de borracha.", "MLB9000000009": "Pneu de Bauru."}

        def detalhe(mlbs, falhas=None, com_fotos=False):
            return {m: dict(d) for m, d in self.anuncios.get(S.ml_casa_atual(), {}).items() if m in mlbs}

        def api_get(caminho, headers=None):
            if caminho.endswith("/description"):
                self.desc_casas.append(S.ml_casa_atual())
                mlb = caminho.split("/")[2]
                if mlb not in self.descricoes:
                    raise RuntimeError("Mercado Livre HTTP 404: not found")
                return {"plain_text": self.descricoes[mlb]}
            raise AssertionError("chamada inesperada ao ML: " + caminho)

        def baixar(url):
            self.baixados.append(url)
            if "FALHA" in url:
                raise RuntimeError("timeout")
            return png(800, 800)

        dono = {v: k for k, v in SELLERS.items()}
        self._ps = [mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba", "bauru"]),
                    mock.patch.object(S, "ml_todos_os_anuncios",
                                      side_effect=lambda forcar=False: list(self.anuncios.get(S.ml_casa_atual(), {}))),
                    mock.patch.object(S, "ml_itens_detalhe", side_effect=detalhe),
                    mock.patch.object(S, "ml_api_get", side_effect=api_get),
                    mock.patch.object(S, "_anuncio_foto_ml_baixar", side_effect=baixar),
                    mock.patch.object(S, "ml_casa_do_seller", side_effect=lambda sid: dono.get(str(sid)))]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)

    def test_massa_importa_titulo_e_categoria_sem_descricao_nem_fotos(self):
        S.anuncios_ml_puxar(["aracatuba"])
        f = S.ficha_ler("CAM1019")
        self.assertEqual(f["campos"]["nome"], "Camara de ar aro 26")
        self.assertEqual(f["campos"]["categoria_ml"], "MLB1234")
        self.assertEqual(f["origem"]["nome"], "anuncio_ml")
        self.assertEqual(f["origem"]["categoria_ml"], "anuncio_ml")
        self.assertEqual(f["campos"]["marca"], "Levorin")
        self.assertNotIn("descricao", f["campos"])
        self.assertEqual(self.desc_casas, [])  # a massa nao le a descricao
        self.assertEqual(self.baixados, [])    # nem baixa fotos
        self.assertEqual(S.anuncio_fotos("CAM1019"), [])

    def test_campo_do_admin_nao_e_sobrescrito_e_apagado_continua_apagado(self):
        S.ficha_salvar("CAM1019", {"nome": "Nome do Admin", "categoria_ml": ""}, "admin_teste", None)
        r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        f = S.ficha_ler("CAM1019")
        self.assertEqual(f["campos"]["nome"], "Nome do Admin")
        self.assertNotIn("categoria_ml", f["campos"])  # apagado pelo Admin
        self.assertEqual(f["origem"]["nome"], "admin")
        self.assertNotIn("nome", r["trazidos"]["campos"])
        self.assertNotIn("categoria_ml", r["trazidos"]["campos"])

    def test_um_traz_descricao_e_fotos(self):
        r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        f = S.ficha_ler("CAM1019")
        self.assertEqual(f["campos"]["descricao"], "Camara de ar de borracha.")
        self.assertEqual(f["origem"]["descricao"], "anuncio_ml")
        fotos = S.anuncio_fotos("CAM1019")
        self.assertEqual(len(fotos), 2)
        self.assertTrue(all(x["origem"] == "anuncio_ml" for x in fotos))
        self.assertEqual(r["trazidos"]["fotos"], 2)
        for c in ("nome", "categoria_ml", "descricao", "marca"):
            self.assertIn(c, r["trazidos"]["campos"])
        self.assertIn("título", r["trazido_texto"])
        self.assertIn("descrição", r["trazido_texto"])
        self.assertIn("2 fotos", r["trazido_texto"])
        # a versao maior (-F) e tentada primeiro
        self.assertTrue(self.baixados[0].endswith("-F.jpg"), self.baixados)
        self.assertEqual(self.desc_casas, ["aracatuba"])

    def test_fotos_nao_vem_quando_a_ficha_ja_tem_e_nunca_duplica(self):
        S.anuncio_foto_adicionar("CAM1019", png(), "admin_teste")
        r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        self.assertEqual(len(S.anuncio_fotos("CAM1019")), 1)
        self.assertEqual(r["trazidos"]["fotos"], 0)
        self.assertTrue(r["trazidos"]["fotos_ja_tinha"])
        self.assertEqual(self.baixados, [])
        # sem fotos, puxar duas vezes nao duplica
        S.anuncio_foto_remover("CAM1019", S.anuncio_fotos("CAM1019")[0]["id"], "admin_teste")
        S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        self.assertEqual(len(S.anuncio_fotos("CAM1019")), 2)

    def test_url_fora_do_mlstatic_recusada(self):
        self.assertFalse(S._anuncio_foto_ml_url_ok("https://exemplo.com/foto.jpg"))
        self.assertFalse(S._anuncio_foto_ml_url_ok("http://http2.mlstatic.com/x.jpg"))
        self.assertFalse(S._anuncio_foto_ml_url_ok("https://mlstatic.com.evil.com/x.jpg"))
        self.assertTrue(S._anuncio_foto_ml_url_ok("https://http2.mlstatic.com/x.jpg"))
        self.anuncios["aracatuba"]["MLB1000000001"] = item(fotos=["https://exemplo.com/a-O.jpg", FOTO1])
        r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        self.assertFalse(any("exemplo.com" in u for u in self.baixados))
        self.assertEqual(r["trazidos"]["fotos"], 1)
        self.assertEqual(r["trazidos"]["fotos_falharam"], 1)
        # a funcao real tambem recusa sem chamar a rede
        self._ps[4].stop()
        try:
            with mock.patch.object(S.urllib.request, "build_opener", side_effect=AssertionError("rede")):
                with self.assertRaises(ValueError):
                    S._anuncio_foto_ml_baixar("https://exemplo.com/a.jpg")
        finally:
            self._ps[4].start()

    def test_falha_de_foto_e_informada_e_o_resto_fica(self):
        self.anuncios["aracatuba"]["MLB1000000001"] = item(fotos=[
            "https://http2.mlstatic.com/FALHA1-O.jpg", FOTO1, "https://http2.mlstatic.com/FALHA2-O.jpg"])
        r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        self.assertEqual(len(S.anuncio_fotos("CAM1019")), 1)
        self.assertEqual(r["trazidos"]["fotos_falharam"], 2)
        self.assertIn("2 fotos não vieram", r["trazido_texto"])
        self.assertEqual(S.ficha_ler("CAM1019")["campos"]["descricao"], "Camara de ar de borracha.")

    def test_foto_pequena_conta_como_falha(self):
        self._ps[4].stop()
        try:
            with mock.patch.object(S, "_anuncio_foto_ml_baixar", return_value=png(300, 300)):
                r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        finally:
            self._ps[4].start()
        self.assertEqual(r["trazidos"]["fotos"], 0)
        self.assertEqual(r["trazidos"]["fotos_falharam"], 2)

    def test_descricao_sem_resposta_nao_trava_o_resto(self):
        def api_get(caminho, headers=None):
            raise RuntimeError("Mercado Livre HTTP 500: erro")
        with mock.patch.object(S, "ml_api_get", side_effect=api_get):
            r = S.anuncio_puxar_um("MLB1000000001", "admin_teste")
        self.assertEqual(r["trazidos"]["fotos"], 2)
        self.assertIn("descrição não veio", r["trazido_texto"])
        self.assertNotIn("descricao", S.ficha_ler("CAM1019")["campos"])

    # ---- Trazer dados do anuncio (botao na aba Anuncios em todos os canais) ----
    def test_trazer_dados_bauru_com_o_token_dela(self):
        S.anuncios_ml_puxar(["bauru"])
        self.assertEqual(S.anuncio_fotos("BAU55"), [])
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "ml", "conta": "bauru", "anuncio_id": "MLB9000000009"})
        self.assertEqual(st, 200, c)
        self.assertEqual(self.desc_casas, ["bauru"])
        self.assertEqual(S.ficha_ler("BAU55")["campos"]["descricao"], "Pneu de Bauru.")
        self.assertEqual(len(S.anuncio_fotos("BAU55")), 2)
        self.assertEqual(c["trazidos"]["fotos"], 2)
        self.assertIn("descrição", c["trazido_texto"].lower())
        with S.DB_LOCK:
            self.assertIsNotNone(S.DB.execute("SELECT 1 FROM audit_log WHERE action='anuncios.trazer_dados'").fetchone())

    def test_trazer_dados_anuncio_desconhecido_ou_conta_errada(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "ml", "conta": "aracatuba", "anuncio_id": "MLB1000000001"})
        self.assertEqual(st, 404, c)  # ainda nao puxado
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "ml", "conta": "lugar", "anuncio_id": "MLB1000000001"})
        self.assertEqual(st, 400, c)
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "shopee", "conta": "aracatuba", "anuncio_id": "1"})
        self.assertEqual(st, 400, c)

    def test_trazer_dados_removido_pelo_admin_recusa(self):
        S.anuncios_ml_puxar(["aracatuba"])
        S.anuncio_remover("ml", "aracatuba", "MLB1000000001", "admin_teste")
        st, c = self.cliente.pedir("POST", "/api/anuncios/trazer-dados",
                                   {"canal": "ml", "conta": "aracatuba", "anuncio_id": "MLB1000000001"})
        self.assertEqual(st, 400, c)
        self.assertEqual(self.baixados, [])

    def test_trazer_dados_permissoes(self):
        S.anuncios_ml_puxar(["aracatuba"])
        corpo = {"canal": "ml", "conta": "aracatuba", "anuncio_id": "MLB1000000001"}
        with S.DB_LOCK:
            S.upsert_app_user("sup_2025", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.upsert_app_user("con_2025", "Consulta", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.execute("UPDATE app_users SET somente_leitura=1 WHERE username='con_2025'")
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2025", SENHA, area="admin")
        self.assertEqual(sup.pedir("POST", "/api/anuncios/trazer-dados", corpo)[0], 403)
        con = Cliente(self.url_base)
        con.entrar("con_2025", SENHA, area="admin")
        self.assertEqual(con.pedir("POST", "/api/anuncios/trazer-dados", corpo)[0], 403)
        self.assertEqual(self.baixados, [])
        self.assertEqual(self.desc_casas, [])


class FotoUrlTeste(TesteNoromak):

    def test_variantes_da_foto(self):
        u = S._anuncio_foto_ml_urls("http://http2.mlstatic.com/D_NQ_NP_1-MLB2-O.webp")
        self.assertEqual(u, ["https://http2.mlstatic.com/D_NQ_NP_1-MLB2-F.webp",
                             "https://http2.mlstatic.com/D_NQ_NP_1-MLB2-O.webp"])
        self.assertEqual(S._anuncio_foto_ml_urls("https://http2.mlstatic.com/x.jpg"),
                         ["https://http2.mlstatic.com/x.jpg"])
        self.assertEqual(S._anuncio_foto_ml_urls(""), [])

    def test_detalhe_pede_as_fotos(self):
        corpo = [{"code": 200, "body": {"id": "MLB1", "title": "T", "pictures": [
            {"secure_url": f"https://http2.mlstatic.com/{i}-O.jpg", "url": "http://x"} for i in range(15)]}}]
        with mock.patch.object(S, "ml_api_get", return_value=corpo) as g:
            d = S.ml_itens_detalhe(["MLB1"], com_fotos=True)
        self.assertIn("pictures", g.call_args[0][0])
        with mock.patch.object(S, "ml_api_get", return_value=corpo) as g2:
            S.ml_itens_detalhe(["MLB1"])  # V20.25: as rotinas diarias nao pedem as fotos
        self.assertNotIn("pictures", g2.call_args[0][0])
        self.assertEqual(len(d["MLB1"]["fotos"]), 12)
        self.assertEqual(d["MLB1"]["fotos"][0], "https://http2.mlstatic.com/0-O.jpg")
