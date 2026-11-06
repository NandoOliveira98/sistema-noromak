"""revisar-antes-de-subir da V20.32 (anuncios na Shopee), 06/11 - o dono pediu "corrija tudo"."""

import json
import threading
import time
from unittest import mock

from base import S

from test_anuncios_shopee_v2032 import Base
from test_anuncios_shopee_estoque_v2032 import ShopeeEstoque, inserir_shopee, linha_shopee

_CHAMAR_REAL = S._shopee_chamar


class PublicarRevisao(Base):

    def pronto(self, **extra):
        r = self.rascunho(**extra)
        self.assertTrue(S.shopee_rascunho_conferir(r["id"], "admin")["ok"])
        return r["id"]

    # alto 2: erro interno da Shopee no add_item = incerto (pode ter criado), nunca "recusado"
    def test_erro_interno_da_shopee_vira_incerto(self):
        for exc in (S.ShopeeRecusou("error_server: Internal error, please try later", {"error": "error_server"}),
                    S.ShopeeRecusou("Shopee HTTP 429: error_busy", {"error": "error_busy"}, status=429),
                    S.ShopeeRecusou("Shopee HTTP 408: timeout", None, status=408)):
            rid = self.pronto()
            self.shopee.add_item_erro = exc
            with self.apollo():
                res = S.shopee_publicar(rid, "admin")
            self.assertEqual(res["situacao"], "incerto", str(exc))
            S.shopee_incerto_liberar(rid, "", "admin")  # conferido no Seller Center: nao foi criado

    # medio 3: o produto ja tem anuncio na Shopee do Noromak = nao publica outro
    def test_produto_com_anuncio_shopee_nao_publica_outro(self):
        rid = self.pronto()
        with self.apollo():
            self.assertTrue(S.shopee_publicar(rid, "admin")["ok"])
        rid2 = self.pronto()
        with self.apollo(), self.assertRaises(ValueError) as cm:
            S.shopee_publicar(rid2, "admin")
        self.assertIn("já tem anúncio na Shopee", str(cm.exception))
        self.assertEqual(sum(1 for c in self.shopee.chamadas if c[1].endswith("add_item")), 1)

    # medio 8: foto WEBP nao passa no Conferir
    def test_foto_webp_barrada_no_conferir(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_foto_v2024 SET tipo='webp' WHERE id=?", (self.foto,))
            S.DB.commit()
        r = self.rascunho()
        erros = " ".join(S.shopee_rascunho_conferir(r["id"], "admin")["erros"])
        self.assertIn("JPG ou PNG", erros)

    # baixo: numero do item nao se perde se o banco falhar depois de criar
    def test_numero_do_item_guardado_mesmo_com_falha_no_banco(self):
        rid = self.pronto()
        with self.apollo(), mock.patch.object(S, "_shopee_canal_noromak", side_effect=RuntimeError("disco")):
            with self.assertRaises(RuntimeError):
                S.shopee_publicar(rid, "admin")
        r = S.shopee_rascunho_ler(rid)
        self.assertEqual((r["situacao"], r["item_id"]), ("incerto", "987654321"))

    # baixo: conferir com edicao no meio nao marca validado o conteudo velho
    def test_conferir_com_edicao_no_meio_e_conflito(self):
        r = self.rascunho()
        original = S._shopee_conferir_local

        def editar_no_meio(rr):
            S.shopee_rascunho_salvar({"id": r["id"], "titulo": "Outro título editado"}, "outra_aba", r["versao"])
            return original(rr)

        with mock.patch.object(S, "_shopee_conferir_local", side_effect=editar_no_meio):
            with self.assertRaises(S.FichaConflito):
                S.shopee_rascunho_conferir(r["id"], "admin")
        self.assertEqual(S.shopee_rascunho_ler(r["id"])["situacao"], "rascunho")

    # integracao: a foto tenta a assinatura publica quando a de loja da erro de assinatura
    def test_foto_tenta_assinatura_publica(self):
        rid = self.pronto()
        original = self.shopee.__call__
        usou_publico = []

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            if path.endswith("upload_image") and not publico:
                raise S.ShopeeRecusou("error_sign: Wrong sign", {"error": "error_sign"})
            if publico:
                usou_publico.append(path)
            return original(method, path, params, payload, multipart=multipart, timeout=timeout)

        with mock.patch.object(S, "_shopee_chamar", new=chamar), self.apollo():
            res = S.shopee_publicar(rid, "admin")
        self.assertTrue(res["ok"], res)
        self.assertTrue(usou_publico)

    # aviso: marca da ficha fora da lista da Shopee
    def test_marca_da_ficha_fora_da_lista_avisa(self):
        S.ficha_salvar("TUNAP109", {"marca": "Desconhecida"}, "admin", S.ficha_ler("TUNAP109")["versao"])
        r = self.rascunho()
        res = S.shopee_rascunho_conferir(r["id"], "admin")
        self.assertTrue(res["ok"])
        self.assertIn("No Brand", " ".join(res.get("avisos") or []))


class RascunhoRevisao(Base):

    def test_preco_infinito_e_fotos_repetidas_recusados(self):
        for preco in ("inf", "nan", "1e400"):
            with self.assertRaises(ValueError):
                self.rascunho(preco=preco)
        with self.assertRaises(ValueError):
            self.rascunho(fotos=[True])
        r = self.rascunho(fotos=[self.foto, self.foto])
        self.assertEqual(r["fotos"], [self.foto])
        with self.assertRaises(ValueError):
            self.rascunho(fotos=list(range(1, 12)))
        c = S.shopee_conta_preco("TUNAP109", "inf")
        self.assertIsNone(c["preco"])

    def test_trocar_categoria_zera_a_marca(self):
        r = self.rascunho(marca={"id": "7001", "nome": "TUNAP"})
        r2 = S.shopee_rascunho_salvar({"id": r["id"], "categoria": "300"}, "admin", r["versao"])
        self.assertEqual(r2["marca"], {})

    def test_supervisor_ve_os_rascunhos(self):
        from base import SENHA_ADMIN, Cliente
        with S.DB_LOCK:
            S.upsert_app_user("sup_sh2", "Sup", "operador", True, SENHA_ADMIN, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_sh2", SENHA_ADMIN, area="admin")
        st, _ = sup.pedir("GET", "/api/anuncios/shopee/rascunhos?codigo=TUNAP109")
        self.assertEqual(st, 200)


class PublicarRota(Base):

    def test_publicar_pela_rota_em_segundo_plano(self):
        r = self.rascunho()
        S.shopee_rascunho_conferir(r["id"], "admin")
        with self.apollo():
            st, c = self.cliente.pedir("POST", "/api/anuncios/shopee/publicar", {"id": r["id"]})
            self.assertEqual(st, 200, c)
            self.assertTrue(c.get("iniciado"))
            fim = time.time() + 10
            while time.time() < fim and S.shopee_rascunho_ler(r["id"])["situacao"] == "publicando":
                time.sleep(0.05)
        self.assertEqual(S.shopee_rascunho_ler(r["id"])["situacao"], "publicado")
        self.assertFalse(S.ANUNCIOS_PUBLICAR_LOCK.locked())


class LiberarRevisao(Base):

    def setUp(self):
        super().setUp()
        r = self.rascunho()
        S.shopee_rascunho_conferir(r["id"], "admin")
        self.shopee.add_item_erro = S.ShopeeSemResposta("timeout")
        with self.apollo():
            S.shopee_publicar(r["id"], "admin")
        self.rid = r["id"]
        self.itens = {"555": "TUNAP109", "666": "OUTRO99"}
        original = self.shopee.__call__

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            if path.endswith("get_item_base_info"):
                iid = str(params["item_id_list"])
                if iid not in self.itens:
                    return {"response": {"item_list": []}}
                return {"response": {"item_list": [{"item_id": int(iid), "item_sku": self.itens[iid], "item_status": "NORMAL",
                                                    "stock_info_v2": {"seller_stock": [{"stock": 2}]}}]}}
            return original(method, path, params, payload, multipart=multipart, timeout=timeout)
        p = mock.patch.object(S, "_shopee_chamar", new=chamar)
        p.start()
        self.addCleanup(p.stop)

    def test_liberar_confere_o_sku_do_item(self):
        with self.assertRaises(ValueError) as cm:
            S.shopee_incerto_liberar(self.rid, "666", "admin")
        self.assertIn("OUTRO99", str(cm.exception))
        with self.assertRaises(ValueError):
            S.shopee_incerto_liberar(self.rid, "55 5x", "admin")
        self.assertEqual(S.shopee_incerto_liberar(self.rid, "555", "admin")["situacao"], "publicado")


class EstoqueRevisao(ShopeeEstoque):

    def test_sku_diferente_nao_recebe_estoque(self):
        inserir_shopee("111", "P1")
        original = self.sh.__call__

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            r = original(method, path, params, payload, multipart=multipart)
            if path.endswith("get_item_base_info"):
                r["response"]["item_list"][0]["item_sku"] = "OUTRO"
            return r
        with mock.patch.object(S, "_shopee_chamar", new=chamar):
            r = S.anuncio_estoque_manual("shopee", "111", 5, "luis")
        self.assertFalse(r["ok"])
        self.assertIn("OUTRO", r["erro"])
        self.assertEqual(self.sh.escritas(), [])

    def test_dois_depositos_recusa_e_um_deposito_manda_o_local(self):
        inserir_shopee("111", "P1")
        original = self.sh.__call__
        locais = [[{"location_id": "BRZ1", "stock": 3}, {"location_id": "BRZ2", "stock": 1}]]

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            if path.endswith("get_item_base_info"):
                return {"response": {"item_list": [{"item_id": 111, "item_sku": "P1", "item_status": "NORMAL",
                                                    "stock_info_v2": {"seller_stock": locais[0]}}]}}
            if path.endswith("update_stock"):
                self.sh.chamadas.append((method, path, params, payload))
                locais[0] = [{"location_id": "BRZ1", "stock": payload["stock_list"][0]["seller_stock"][0]["stock"]}]
                return {"response": {"failure_list": []}}
            return original(method, path, params, payload)
        with mock.patch.object(S, "_shopee_chamar", new=chamar):
            r = S.anuncio_estoque_manual("shopee", "111", 5, "luis")
            self.assertFalse(r["ok"])
            self.assertIn("BRZ2", r["erro"])
            self.assertEqual(self.sh.escritas(), [])
            locais[0] = [{"location_id": "BRZ1", "stock": 3}]
            r = S.anuncio_estoque_manual("shopee", "111", 5, "luis")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.sh.escritas()[-1][3]["stock_list"][0]["seller_stock"], [{"location_id": "BRZ1", "stock": 5}])

    def test_estoque_sem_numero_nao_vira_zero(self):
        inserir_shopee("111", "P1", estoque=4)
        original = self.sh.__call__

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            if path.endswith("get_item_base_info"):
                return {"response": {"item_list": [{"item_id": 111, "item_sku": "P1", "item_status": "NORMAL",
                                                    "stock_info_v2": {"seller_stock": [{"location_id": "BRZ1"}]}}]}}
            return original(method, path, params, payload)
        with mock.patch.object(S, "_shopee_chamar", new=chamar):
            r = S.anuncio_estoque_manual("shopee", "111", 5, "luis")
        self.assertFalse(r["ok"])
        self.assertEqual(linha_shopee()["estoque"], 4, "sem número não grava 0 na lista")
        self.assertEqual(self.sh.escritas(), [])

    def test_item_excluido_na_shopee_nao_recebe(self):
        inserir_shopee("111", "P1")
        original = self.sh.__call__

        def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
            r = original(method, path, params, payload, multipart=multipart)
            if path.endswith("get_item_base_info"):
                r["response"]["item_list"][0]["item_status"] = "SELLER_DELETE"
            return r
        with mock.patch.object(S, "_shopee_chamar", new=chamar):
            r = S.anuncio_estoque_manual("shopee", "111", 5, "luis")
        self.assertFalse(r["ok"])
        self.assertEqual(self.sh.escritas(), [])

    def test_shopee_lenta_nao_segura_o_ml(self):
        inserir_shopee("111", "PSH", modo="automatico")
        self.apollo["PSH"] = {1: 4}
        S.anuncio_estoque_config_definir("shopee", "automatico", "luis")
        libera = threading.Event()
        original = S.anuncios_estoque_rodada

        def rodada(conta, **kw):
            if conta == "shopee":
                libera.wait(5)
            return original(conta, **kw)
        with mock.patch.object(S, "anuncios_estoque_rodada", side_effect=rodada), \
                mock.patch.object(S, "ANUNCIO_ESTOQUE_SHOPEE_ESPERA_S", 0.05):
            inicio = time.time()
            S.anuncios_estoque_volta({})
            self.assertLess(time.time() - inicio, 2, "a volta não espera a Shopee")
            libera.set()
            time.sleep(0.3)


class ChamarRevisao(Base):

    def test_403_sem_erro_de_token_nao_renova(self):
        import urllib.error
        import io
        renovou = []
        erro = urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(json.dumps({"error": "error_permission",
                                                                                  "message": "no permission"}).encode()))
        with mock.patch.object(S, "_shopee_chamar", new=_CHAMAR_REAL), \
                mock.patch.object(S, "shopee_refresh_access_token",
                                  side_effect=lambda forcar=False: renovou.append(forcar) or {"shop_id": "1", "access_token": "t"}), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=erro),                 mock.patch.object(S, "SHOPEE_PARTNER_ID", "1"):
            with self.assertRaises(S.ShopeeRecusou):
                _CHAMAR_REAL("GET", "/api/v2/product/get_item_limit")
        self.assertEqual(renovou, [False], "403 de permissão não troca o refresh_token")


class ListaRevisao(Base):

    def test_misto_so_no_mesmo_canal(self):
        linhas = [{"ficha_codigo": "P1", "conta": "aracatuba", "controle": "alephee", "removido_em": "", "canal": "ml"},
                  {"ficha_codigo": "P1", "conta": "aracatuba", "controle": "noromak", "removido_em": "", "canal": "shopee"}]
        self.assertEqual(S._anuncio_misto_por_codigo(linhas), {})


if __name__ == "__main__":
    import unittest
    unittest.main()
