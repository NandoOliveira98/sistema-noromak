"""V20.58 — Anúncios dividido por casa (dono, 12/11).

1. "Os produtos de Bauru estão aparecendo em Araçatuba": com uma casa escolhida, a ficha que só tem anúncio na outra
   casa não aparece; ficha sem nenhum anúncio (produto novo) aparece nas duas.
2. "Não existe opção para controlar o Mercado Livre de Bauru": os cartões do Estoque automático dizem o canal
   ("Mercado Livre · Bauru") e, sem anúncio controlado, explicam o que fazer.
3. "O estoque do TikTok precisa ser controlado por Anúncios": "Estoque TikTok" saiu do menu, o endereço antigo abre
   Anúncios em Bauru e o vínculo à mão do SKU do TikTok (com as sugestões do SKU cortado) vem para o anúncio.
"""
from pathlib import Path

from base import S, TesteNoromak

APP = Path(__file__).resolve().parent.parent / "APP"
HTML = (APP / "anuncios.html").read_text(encoding="utf-8")


def _anuncio(canal, conta, anuncio_id, ficha, controle="alephee", removido=""):
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
                     "lido_em,removido_em) VALUES(?,?,?,?,?,?,?,?,?)",
                     (canal, conta, anuncio_id, ficha, ficha, controle, "Titulo " + ficha, S.now_iso(), removido))
        S.DB.commit()


def _codigos(conta):
    return {f["codigo"] for f in S.anuncios_lista_payload(conta=conta)["fichas"]}


class ProdutosPorCasa(TesteNoromak):
    def setUp(self):
        super().setUp()
        for cod in ("PECAARA", "PECABAU", "PECADUAS", "PECANOVA", "PECAREMOV"):
            S.ficha_importar(cod, {"nome": "Nome " + cod}, "anuncio_ml")
        _anuncio("ml", "aracatuba", "MLB1", "PECAARA")
        _anuncio("tiktok", "bauru", "1738000000000000001", "PECABAU", controle="tiktok")
        _anuncio("ml", "aracatuba", "MLB2", "PECADUAS")
        _anuncio("ml", "bauru", "MLB3", "PECADUAS")
        _anuncio("ml", "bauru", "MLB4", "PECAREMOV", removido=S.now_iso())

    def test_aracatuba_nao_mostra_produto_so_de_bauru(self):
        cods = _codigos("aracatuba")
        self.assertIn("PECAARA", cods)
        self.assertIn("PECADUAS", cods)
        self.assertNotIn("PECABAU", cods, "produto só com anúncio de Bauru não aparece em Araçatuba")
        self.assertNotIn("PECAREMOV", cods, "anúncio removido continua sendo da casa dele")

    def test_bauru_nao_mostra_produto_so_de_aracatuba(self):
        cods = _codigos("bauru")
        self.assertIn("PECABAU", cods)
        self.assertIn("PECADUAS", cods)
        self.assertIn("PECAREMOV", cods)
        self.assertNotIn("PECAARA", cods)

    def test_produto_sem_anuncio_aparece_nas_duas_e_todas_mostra_tudo(self):
        self.assertIn("PECANOVA", _codigos("aracatuba"))
        self.assertIn("PECANOVA", _codigos("bauru"))
        self.assertEqual(_codigos(""), {"PECAARA", "PECABAU", "PECADUAS", "PECANOVA", "PECAREMOV"})

    def test_cartao_fichas_conta_so_a_casa(self):
        self.assertEqual(S.anuncios_lista_payload(conta="aracatuba")["resumo"]["fichas"], 3)
        self.assertEqual(S.anuncios_lista_payload(conta="")["resumo"]["fichas"], 5)

    def test_rota_com_a_casa(self):
        st, corpo = self.cliente.pedir("GET", "/api/anuncios?conta=aracatuba")
        self.assertEqual(st, 200, corpo)
        self.assertNotIn("PECABAU", {f["codigo"] for f in corpo["fichas"]})


class EstoqueDasContas(TesteNoromak):
    def test_cartao_do_tiktok_diz_a_fonte(self):
        self.assertEqual(S.anuncios_estoque_payload("tiktok")["tiktok_fonte"], S.tiktok_fonte())
        self.assertNotIn("tiktok_fonte", S.anuncios_estoque_payload("bauru"))

    def test_bauru_tem_cartao_e_nome_do_canal(self):
        p = S.anuncios_estoque_payload("bauru")
        self.assertEqual(p["conta"], "bauru")
        self.assertIn('bauru: "Mercado Livre · Bauru"', HTML)
        self.assertIn("Nenhum anúncio do Mercado Livre de", HTML)


class TiktokPorAnuncios(TesteNoromak):
    def test_menu_sem_estoque_tiktok_e_endereco_leva_para_anuncios(self):
        menu = (APP / "nk-menu.js").read_text(encoding="utf-8")
        self.assertNotIn('href: "/tiktok-estoque"', menu)
        tela = (APP / "tiktok-estoque.html").read_text(encoding="utf-8")
        self.assertIn('location.replace("/expedicao/anuncios?casa=bauru")', tela)
        self.assertIn('has("antiga")', tela, "a tela antiga continua por ?antiga=1")
        self.assertIn('get("casa")', HTML, "Anúncios aceita ?casa=bauru")

    def test_menu_com_versao_nova_em_todas_as_telas(self):
        for p in APP.glob("*.html"):
            texto = p.read_text(encoding="utf-8")
            if "nk-menu.js?v=" in texto:
                self.assertIn("nk-menu.js?v=20.58", texto, p.name)
        self.assertIn("noromak-expedicao-pwa-20.58", (APP / "sw.js").read_text(encoding="utf-8"))

    def test_ficha_traz_o_vinculo_e_as_sugestoes_do_sku_cortado(self):
        cortado = ("2005001/" * 7)[:S.TIKTOK_SKU_LIMITE]
        _anuncio("tiktok", "bauru", "1738000000000000009", "FICHACORTADA", controle="tiktok")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET sku_anuncio=? WHERE anuncio_id='1738000000000000009'", (cortado,))
            S.DB.commit()
        from unittest import mock
        ultima = cortado.rsplit("/", 1)[1]
        with mock.patch.object(S, "_separacao_codigos_conhecidos", return_value={ultima + "9"}):
            f = S.anuncio_ficha_payload("FICHACORTADA")
        a = f["anuncios"][0]
        self.assertTrue(a["tiktok_cortado"])
        self.assertEqual(a["tiktok_vinculo"], "")
        self.assertEqual(a["sugestoes"], [cortado.rsplit("/", 1)[0] + "/" + ultima + "9"])
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO tiktok_vinculo_v1964(sku_id,codigos,por,em) VALUES(?,?,?,?)",
                         ("1738000000000000009", "2005001/2005009", "t", S.now_iso()))
            S.DB.commit()
        a = S.anuncio_ficha_payload("FICHACORTADA")["anuncios"][0]
        self.assertEqual(a["tiktok_vinculo"], "2005001/2005009")
        self.assertFalse(a["tiktok_cortado"])

    def test_tela_tem_vincular_e_controlar_aqui(self):
        self.assertIn("async function vincularTiktok(", HTML)
        self.assertIn('"/api/tiktok-estoque/vinculo"', HTML)
        self.assertIn("Controlar estoque aqui", HTML)
        self.assertNotIn('href="/expedicao/tiktok-estoque" target', HTML, "o anúncio não manda mais para a tela antiga")
