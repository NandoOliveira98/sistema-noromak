"""V20.24 - Anuncios: puxar em massa em segundo plano e um anuncio so pelo MLB ou link."""

import threading
import time
from unittest import mock

from base import S, SENHA_ADMIN, TesteNoromak, Cliente

SENHA = "SenhaTeste#2024"


def item(sku="0893117732", titulo="Produto", seller="111", logistica=""):
    return {"titulo": titulo, "preco": 10.5, "sku_ml": sku, "status": "active", "sub_status": [], "estoque": 3,
            "categoria": "MLB1000000001", "tipo": "gold_special", "user_product": "", "produto_catalogo": "",
            "logistica": logistica, "atributos": [], "seller_id": seller}


SELLERS = {"aracatuba": "111", "bauru": "222"}


def esperar_fim(limite=10.0):
    fim = time.time() + limite
    while time.time() < fim:
        if not S.anuncios_puxar_situacao()["rodando"]:
            return S.anuncios_puxar_situacao()
        time.sleep(0.02)
    raise AssertionError("a puxada nao terminou")


def linha(mlb):
    with S.DB_LOCK:
        r = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='ml' AND anuncio_id=?", (mlb,)).fetchone()
    return dict(r) if r else None


class PuxarFundoTeste(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.anuncios = {"aracatuba": {"MLB1000000001": item(), "MLB2000000002": item(sku="0893117733")},
                         "bauru": {"MLB9000000009": item(sku="KIT2-81794", seller="222")}}
        self.porta = None  # threading.Event: segura a 1a conta ate o teste soltar
        self.casas_lidas = []

        def todos(forcar=False):
            casa = S.ml_casa_atual()
            self.casas_lidas.append(casa)
            if self.porta is not None:
                self.porta.wait(5)
            return list(self.anuncios.get(casa, {}))

        def detalhe(mlbs, falhas=None):
            return {m: dict(d) for m, d in self.anuncios.get(S.ml_casa_atual(), {}).items() if m in mlbs}
        self._ps = [mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba", "bauru"]),
                    mock.patch.object(S, "ml_todos_os_anuncios", side_effect=todos),
                    mock.patch.object(S, "ml_itens_detalhe", side_effect=detalhe)]
        for p in self._ps:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: esperar_fim() if S.anuncios_puxar_situacao()["rodando"] else None)

    # ---- em massa, em segundo plano ----
    def test_rota_inicia_e_responde_na_hora(self):
        self.porta = threading.Event()
        st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {})
        self.assertEqual(st, 200, c)
        self.assertTrue(c["ok"] and c["iniciado"])
        st, s = self.cliente.pedir("GET", "/api/anuncios/puxar/situacao")
        self.assertEqual(st, 200, s)
        self.assertTrue(s["rodando"])
        self.assertEqual(s["iniciado_por"], "admin_teste")
        self.assertEqual(s["conta_atual"], "aracatuba")
        # a 2a enquanto roda = 409
        st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {})
        self.assertEqual(st, 409, c)
        self.assertIn("Já está puxando", c["error"])
        self.porta.set()
        s = esperar_fim()
        self.assertFalse(s["rodando"])
        self.assertTrue(s["fim_em"])
        self.assertEqual(s["conta_atual"], "")
        self.assertTrue(s["ok"])
        self.assertEqual(s["resultado"]["aracatuba"]["novos"], 2)
        self.assertEqual(s["resultado"]["bauru"]["novos"], 1)
        self.assertEqual(linha("MLB9000000009")["conta"], "bauru")
        self.assertTrue(S.ANUNCIOS_PUXAR_LOCK.acquire(blocking=False))  # trava solta no fim
        S.ANUNCIOS_PUXAR_LOCK.release()
        with S.DB_LOCK:
            a = S.DB.execute("SELECT username FROM audit_log WHERE action='anuncios.puxar'").fetchall()
        self.assertEqual([r["username"] for r in a], ["admin_teste"])

    def test_so_uma_conta(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/puxar", {"contas": ["bauru"]})
        self.assertEqual(st, 200, c)
        s = esperar_fim()
        self.assertEqual(list(s["resultado"]), ["bauru"])
        self.assertIsNone(linha("MLB1000000001"))

    def test_falha_inesperada_solta_a_trava_e_fecha_o_estado(self):
        with mock.patch.object(S, "_anuncio_puxar_conta", side_effect=RuntimeError("boom")):
            self.assertTrue(S.anuncios_puxar_iniciar(None, "luis"))
            s = esperar_fim()
        self.assertFalse(s["rodando"])
        self.assertFalse(s["ok"])
        self.assertIn("boom", s["erro"])
        self.assertTrue(S.ANUNCIOS_PUXAR_LOCK.acquire(blocking=False))
        S.ANUNCIOS_PUXAR_LOCK.release()
        self.assertTrue(S.anuncios_puxar_iniciar(None, "luis"))  # pode puxar de novo
        self.assertTrue(esperar_fim()["ok"])

    def test_erro_no_meio_guarda_os_lotes_e_conta_parcial(self):
        self.anuncios["aracatuba"] = {f"MLB{i}": item(sku=f"09{i:08d}") for i in range(1, 6)}
        orig = S._anuncio_gravar_lote
        vezes = []

        def lote(conta, l, res):
            vezes.append(1)
            if len(vezes) == 2:
                raise RuntimeError("disco")
            return orig(conta, l, res)
        with mock.patch.object(S, "ANUNCIO_LOTE_GRAVACAO", 2), mock.patch.object(S, "_anuncio_gravar_lote", lote):
            S.anuncios_puxar_iniciar(["aracatuba"], "luis")
            s = esperar_fim()
        r = s["resultado"]["aracatuba"]
        self.assertTrue(r["erro"])
        self.assertEqual(r["novos"], 2)  # o 1o lote ficou gravado
        self.assertFalse(s["ok"])

    def test_situacao_supervisor_ve_e_puxar_e_so_admin(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_2024", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2024", SENHA, area="admin")
        self.assertEqual(sup.pedir("GET", "/api/anuncios/puxar/situacao")[0], 200)
        self.assertEqual(sup.pedir("POST", "/api/anuncios/puxar", {})[0], 403)
        self.assertEqual(sup.pedir("POST", "/api/anuncios/puxar/um", {"mlb_ou_link": "MLB1000000001"})[0], 403)
        self.assertFalse(S.anuncios_puxar_situacao()["rodando"])

    # ---- um anuncio so ----
    def test_mlb_do_texto(self):
        f = S.anuncio_mlb_do_texto
        self.assertEqual(f("MLB1234567890"), "MLB1234567890")
        self.assertEqual(f(" mlb-1234567890 "), "MLB1234567890")
        self.assertEqual(f("https://produto.mercadolivre.com.br/MLB-1234567890-oleo-texaco-_JM"), "MLB1234567890")
        self.assertEqual(f("https://articulo.mercadolibre.com.ar/MLB-1234567890-x"), "MLB1234567890")
        self.assertEqual(f("https://www.mercadolivre.com.br/oleo-texaco/up/MLBU123?x=1#MLB1234567890"), "MLB1234567890")
        self.assertEqual(f("https://www.mercadolivre.com.br/oleo-x_MLB1234567890"), "MLB1234567890")
        with self.assertRaises(ValueError) as e:
            f("https://www.mercadolivre.com.br/oleo-texaco/p/MLB19876543")
        self.assertIn("catálogo", str(e.exception))
        for ruim in ("", "abc", "https://www.mercadolivre.com.br/x/up/MLBU123", "MLB12"):
            with self.assertRaises(ValueError):
                f(ruim)

    def dono(self, sid):
        return {v: k for k, v in SELLERS.items()}.get(str(sid))

    def test_um_de_bauru(self):
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar/um",
                                       {"mlb_ou_link": "https://produto.mercadolivre.com.br/MLB-9000000009-kit"})
        self.assertEqual(st, 200, c)
        self.assertEqual((c["conta"], c["mlb"], c["ficha_codigo"]), ("bauru", "MLB9000000009", "KIT2-81794"))
        self.assertEqual(c["anuncio"]["conta"], "bauru")
        self.assertEqual(S.ficha_ler("KIT2-81794")["tipo"], "kit")  # mesmo caminho da puxada em massa
        self.assertIsNone(linha("MLB1000000001"))  # so ele
        with S.DB_LOCK:
            self.assertIsNotNone(S.DB.execute("SELECT 1 FROM audit_log WHERE action='anuncios.puxar_um'").fetchone())

    def test_um_lido_por_outro_token_vai_para_o_dono(self):
        # o /items?ids= e publico: a conta de Aracatuba ve o anuncio de Bauru
        self.anuncios["aracatuba"]["MLB9000000009"] = item(sku="KIT2-81794", seller="222")
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            r = S.anuncio_puxar_um("MLB9000000009", "luis")
        self.assertEqual(r["conta"], "bauru")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM anuncio_canal_v2023 WHERE anuncio_id='MLB9000000009'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(linha("MLB9000000009")["conta"], "bauru")

    def test_um_releitura_do_dono_falhou_nada_gravado(self):
        # V20.24 (revisao): sem a leitura pela conta dona, nao grava a leitura publica (estoque/SKU incompletos)
        self.anuncios["aracatuba"]["MLB9000000009"] = item(sku="KIT2-81794", seller="222")
        del self.anuncios["bauru"]["MLB9000000009"]
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            with self.assertRaises(S.AnuncioMLSemResposta):
                S.anuncio_puxar_um("MLB9000000009", "luis")
        self.assertIsNone(linha("MLB9000000009"))

    def test_sku_corrigido_no_ml_troca_a_ficha_e_sem_sku_mantem(self):
        # V20.24 (revisao): SKU vazio mantem a ficha; SKU novo (mesmo sem ficha ainda) troca
        S.anuncios_ml_puxar(["aracatuba"])
        self.assertEqual(linha("MLB1000000001")["ficha_codigo"], "0893117732")
        self.anuncios["aracatuba"]["MLB1000000001"] = item(sku="")
        S.anuncios_ml_puxar(["aracatuba"])
        self.assertEqual(linha("MLB1000000001")["ficha_codigo"], "0893117732")
        self.anuncios["aracatuba"]["MLB1000000001"] = item(sku="NOVO-555")
        S.anuncios_ml_puxar(["aracatuba"])
        self.assertEqual(linha("MLB1000000001")["ficha_codigo"], "NOVO-555")

    def test_um_sem_ficha(self):
        self.anuncios["aracatuba"]["MLB3000000003"] = item(sku="")
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            r = S.anuncio_puxar_um("MLB3000000003", "luis")
        self.assertEqual((r["conta"], r["ficha_codigo"]), ("aracatuba", ""))

    def test_um_de_outro_vendedor_404_nada_gravado(self):
        self.anuncios["aracatuba"]["MLB7000000007"] = item(seller="999")
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar/um", {"mlb_ou_link": "MLB7000000007"})
            self.assertEqual(st, 404, c)
            self.assertIn("não é de nenhuma conta", c["error"])
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar/um", {"mlb_ou_link": "MLB4040404040"})
            self.assertEqual(st, 404, c)
        self.assertIsNone(linha("MLB7000000007"))

    def test_um_ml_sem_resposta_502(self):
        def falhou(mlbs, falhas=None):
            if falhas is not None:
                falhas.append("erro")
            return {}
        with mock.patch.object(S, "ml_itens_detalhe", side_effect=falhou):
            st, c = self.cliente.pedir("POST", "/api/anuncios/puxar/um", {"mlb_ou_link": "MLB1000000001"})
        self.assertEqual(st, 502, c)
        self.assertIsNone(linha("MLB1000000001"))

    def test_um_catalogo_400(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/puxar/um",
                                   {"mlb_ou_link": "https://www.mercadolivre.com.br/x/p/MLB123456"})
        self.assertEqual(st, 400, c)
        self.assertIn("catálogo", c["error"])

    def test_um_removido_pelo_admin_nao_volta(self):
        with mock.patch.object(S, "ml_casa_do_seller", side_effect=self.dono):
            S.anuncio_puxar_um("MLB1000000001", "luis")
            S.anuncio_remover("ml", "aracatuba", "MLB1000000001", "luis")
            r = S.anuncio_puxar_um("MLB1000000001", "luis")
        self.assertTrue(r["removido"])
        self.assertTrue(linha("MLB1000000001")["removido_em"])

    def test_ml_itens_detalhe_traz_o_vendedor_e_as_falhas(self):
        self._ps[2].stop()  # usa a funcao real
        resp = [{"code": 200, "body": {"id": "MLB1", "title": "T", "seller_id": 111, "attributes": []}},
                {"code": 404, "body": {"message": "not found"}}]
        falhas = []
        with mock.patch.object(S, "ml_api_get", return_value=resp) as g:
            out = S.ml_itens_detalhe(["MLB1", "MLB2"], falhas=falhas)
        self.assertIn("seller_id", g.call_args[0][0])
        self.assertEqual(out["MLB1"]["seller_id"], "111")
        self.assertEqual(falhas, [404])
        with mock.patch.object(S, "ml_api_get", side_effect=RuntimeError("fora")):
            falhas = []
            self.assertEqual(S.ml_itens_detalhe(["MLB1"], falhas=falhas), {})
        self.assertEqual(falhas, ["erro"])
        self._ps[2].start()

    def test_compras_403(self):
        st, r = self.cliente.pedir("POST", "/api/roles", {
            "username": "carla24", "display_name": "Carla", "role": "compras", "active": True, "password": SENHA_ADMIN})
        self.assertEqual(st, 200, r)
        cp = Cliente(self.url_base)
        cp.entrar("carla24", SENHA_ADMIN, area="portal")
        self.assertEqual(cp.pedir("POST", "/api/anuncios/puxar/um", {})[0], 403)
        self.assertEqual(cp.pedir("GET", "/api/anuncios/puxar/situacao")[0], 403)


if __name__ == "__main__":
    import unittest
    unittest.main()
