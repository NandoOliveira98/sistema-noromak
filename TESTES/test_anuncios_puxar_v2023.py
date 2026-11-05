"""V20.23 - Anuncios fase 1: puxar os anuncios do ML (so leitura) e ligar a ficha."""

from unittest import mock

from base import S, TesteNoromak


def item(sku="0893117732", titulo="Produto", logistica="", attrs=None, preco=10.5, estoque=3):
    return {"titulo": titulo, "preco": preco, "sku_ml": sku, "status": "active", "sub_status": [],
            "estoque": estoque, "categoria": "MLB1", "tipo": "gold_special", "user_product": "",
            "produto_catalogo": "", "logistica": logistica, "atributos": attrs or []}


def attr(aid, nome=None, num=None, unit=None):
    a = {"id": aid}
    if nome is not None:
        a["value_name"] = nome
    if num is not None:
        a["value_struct"] = {"number": num, "unit": unit}
    return a


class PuxarTeste(TesteNoromak):

    def puxar(self, itens, lista=None):
        lista = list(itens) if lista is None else lista
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]), \
                mock.patch.object(S, "ml_todos_os_anuncios", return_value=lista), \
                mock.patch.object(S, "ml_itens_detalhe", return_value=itens):
            return S.anuncios_ml_puxar()

    def linha_(self, mlb):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='ml' AND anuncio_id=?", (mlb,)).fetchone()
        return dict(r) if r else None

    def todas(self):
        with S.DB_LOCK:
            return [dict(r) for r in S.DB.execute("SELECT * FROM anuncio_canal_v2023 ORDER BY anuncio_id").fetchall()]

    def test_puxa_e_liga_a_ficha(self):
        r = self.puxar({"MLB1": item(attrs=[attr("BRAND", "Würth"), attr("GTIN", "7891234567895")])})
        self.assertTrue(r["ok"])
        self.assertEqual(r["contas"]["aracatuba"]["novos"], 1)
        l = self.linha_("MLB1")
        self.assertEqual(l["controle"], "alephee")
        self.assertEqual(l["ficha_codigo"], "0893117732")
        f = S.ficha_ler("0893117732")
        self.assertEqual(f["campos"]["marca"], "Würth")
        self.assertEqual(f["campos"]["ean"], "7891234567895")
        self.assertEqual(f["origem"]["marca"], "anuncio_ml")

    def test_full_fica_full(self):
        self.puxar({"MLB1": item(logistica="fulfillment")})
        self.assertEqual(self.linha_("MLB1")["controle"], "full")

    def test_nao_muda_controle_existente(self):
        self.puxar({"MLB1": item()})
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET controle='noromak' WHERE anuncio_id='MLB1'")
            S.DB.commit()
        r = self.puxar({"MLB1": item(titulo="Novo titulo", logistica="fulfillment")})
        self.assertEqual(r["contas"]["aracatuba"]["atualizados"], 1)
        l = self.linha_("MLB1")
        self.assertEqual(l["controle"], "noromak")
        self.assertEqual(l["titulo"], "Novo titulo")

    def test_ml_fora_nao_apaga(self):
        itens = {"MLB1": item(), "MLB2": item(sku="0893117733")}
        self.puxar(itens)
        antes = self.todas()
        with mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"]), \
                mock.patch.object(S, "ml_todos_os_anuncios", side_effect=RuntimeError("fora")):
            r = S.anuncios_ml_puxar()
        self.assertFalse(r["ok"])
        self.assertTrue(r["contas"]["aracatuba"]["erro"])
        self.assertEqual(self.todas(), antes)
        # lista vazia = ML nao respondeu, nao "conta sem anuncios"
        r = self.puxar({}, lista=[])
        self.assertTrue(r["contas"]["aracatuba"]["erro"])
        self.assertEqual(self.todas(), antes)
        # anuncio que sumiu da lista nao e apagado
        self.puxar({"MLB1": item()})
        self.assertIsNotNone(self.linha_("MLB2"))

    def test_removido_nao_volta(self):
        self.puxar({"MLB1": item(titulo="A")})
        S.anuncio_remover("ml", "aracatuba", "MLB1", "luis")
        self.puxar({"MLB1": item(titulo="B")})
        l = self.linha_("MLB1")
        self.assertTrue(l["removido_em"])
        self.assertEqual(l["titulo"], "A")
        S.anuncio_reincluir("ml", "aracatuba", "MLB1", "luis")
        self.puxar({"MLB1": item(titulo="B")})
        l = self.linha_("MLB1")
        self.assertEqual(l["removido_em"], "")
        self.assertEqual(l["titulo"], "B")

    def test_sku_de_kit_e_ab(self):
        r = self.puxar({"MLB1": item(sku="KIT2-81794"), "MLB2": item(sku="0893600022/0893764272"),
                        "MLB3": item(sku="")})
        self.assertEqual(self.linha_("MLB1")["ficha_codigo"], "KIT281794")
        self.assertEqual(S.ficha_ler("KIT281794")["tipo"], "kit")
        self.assertEqual(self.linha_("MLB2")["ficha_codigo"], "08936000220893764272")
        self.assertEqual(S.ficha_ler("08936000220893764272")["tipo"], "kit")
        self.assertEqual(self.linha_("MLB3")["ficha_codigo"], "")
        self.assertEqual(r["contas"]["aracatuba"]["sem_ficha"], 1)

    def test_vinculo_manual_vence_o_sku_do_anuncio(self):
        S.ml_precificador_manual_link_update("0893117999", "MLB1", "add", "luis")
        self.puxar({"MLB1": item(sku="OUTRO123")})
        self.assertEqual(self.linha_("MLB1")["ficha_codigo"], "0893117999")

    def test_admin_vence_importacao(self):
        S.ficha_salvar("0893117732", {"marca": "Würth"}, "luis", None)
        self.puxar({"MLB1": item(attrs=[attr("BRAND", "WURTH")])})
        self.assertEqual(S.ficha_ler("0893117732")["campos"]["marca"], "Würth")
        self.assertEqual(S.ficha_ler("0893117732")["origem"]["marca"], "admin")

    def test_peso_em_kg_e_medidas_em_mm(self):
        self.puxar({"MLB1": item(attrs=[attr("SELLER_PACKAGE_WEIGHT", num=0.45, unit="kg"),
                                        attr("SELLER_PACKAGE_HEIGHT", num=50, unit="mm"),
                                        attr("SELLER_PACKAGE_WIDTH", num=10, unit="cm"),
                                        attr("SELLER_PACKAGE_LENGTH", num=0, unit="cm")])})
        c = S.ficha_ler("0893117732")["campos"]
        self.assertEqual(c["peso_g"], 450)
        self.assertEqual(c["altura_cm"], 5)
        self.assertEqual(c["largura_cm"], 10)
        self.assertNotIn("comprimento_cm", c)
