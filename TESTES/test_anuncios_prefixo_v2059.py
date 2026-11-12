"""V20.59 — prefixo do anúncio no código da ficha (dono, 12/11: "puxou duplicado assim": 158582-2005001 × 2005001).

O SKU do anúncio no ML muitas vezes vem com um prefixo de anúncio de 5 a 6 dígitos ("158582-2005001", "151490-0893270212").
A separação e o estoque já tiravam o prefixo (`_separacao_partes`); a ficha da puxada usava o SKU inteiro e criou um produto
duplicado para 720 anúncios (680 de Araçatuba, 40 de Bauru). Agora a ficha é o código sem o prefixo, e uma correção única no
boot move os anúncios já gravados (a ficha duplicada só da importação é juntada e apagada; a que tem algo do Admin fica).
Não mudam: os dois códigos de 7+ dígitos ("0893600022-0893764272", V20.28) e o kit cadastrado com o SKU exato.
"""
import json

from base import S, TesteNoromak


def _linha(canal, conta, anuncio_id, ficha, sku, controle="alephee"):
    with S.DB_LOCK:
        S.DB.execute("INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,lido_em) "
                     "VALUES(?,?,?,?,?,?,?,?)", (canal, conta, anuncio_id, ficha, sku, controle, "T " + sku, S.now_iso()))
        S.DB.commit()


def _ficha_da_linha(anuncio_id):
    with S.DB_LOCK:
        return str(S.DB.execute("SELECT ficha_codigo FROM anuncio_canal_v2023 WHERE anuncio_id=?",
                                (anuncio_id,)).fetchone()["ficha_codigo"])


class FichaSemPrefixo(TesteNoromak):
    def test_prefixo_de_anuncio_sai(self):
        self.assertEqual(S.anuncio_ficha_do_sku("158582-2005001", {}, set())[0], "2005001")
        self.assertEqual(S.anuncio_ficha_do_sku("151490-0893270212", {}, set())[0], "0893270212")
        self.assertEqual(S.anuncio_ficha_do_sku("81799-WAP103", {}, set())[0], "WAP103")

    def test_dois_codigos_e_codigo_comum_nao_mudam(self):
        self.assertEqual(S.anuncio_ficha_do_sku("0893600022-0893764272", {}, set())[0], "0893600022-0893764272")
        self.assertEqual(S.anuncio_ficha_do_sku("2005001", {}, set())[0], "2005001")
        self.assertEqual(S.anuncio_ficha_do_sku("6007.003.315.00.8", {}, set())[0], "6007.003.315.00.8")
        self.assertEqual(S.anuncio_ficha_do_sku("1234-5678", {}, set())[0], "1234-5678", "4 dígitos não é prefixo")

    def test_kit_cadastrado_com_o_sku_exato_fica(self):
        catalogo = {"158582-2005001": {"sku_kit": "158582-2005001", "nome": "Kit",
                                       "componentes": [{"sku": "2005001", "quantidade": 2}], "ativo": True}}
        cod, tipo = S.anuncio_ficha_do_sku("158582-2005001", catalogo, set())
        self.assertEqual((cod, tipo), ("158582-2005001", "kit"))

    def test_prefixo_com_a_b_vira_kit_das_pecas(self):
        cod, tipo = S.anuncio_ficha_do_sku("151490-0893600022/0893764272", {}, set())
        self.assertEqual((cod, tipo), ("0893600022/0893764272", "kit"))


class CorrecaoNoBoot(TesteNoromak):
    def test_junta_na_ficha_que_ja_existe_e_apaga_a_duplicada(self):
        S.ficha_importar("2005001", {"nome": "Kit 2 Aplicadores"}, "anuncio_tiktok")
        S.ficha_importar("158582-2005001", {"nome": "Nome do ML", "marca": "Vonixx"}, "anuncio_ml")
        _linha("ml", "bauru", "MLB100", "158582-2005001", "158582-2005001")
        res = S.anuncios_prefixo_corrigir()
        self.assertEqual(_ficha_da_linha("MLB100"), "2005001")
        self.assertIsNone(S.ficha_ler("158582-2005001"), "a ficha duplicada só da importação sai")
        f = S.ficha_ler("2005001")
        self.assertEqual(f["campos"]["marca"], "Vonixx", "o que a duplicada tinha passa para a ficha certa")
        self.assertEqual(res["movidos"], 1)
        self.assertEqual(res["apagadas"], 1)

    def test_sem_ficha_certa_a_duplicada_e_renomeada(self):
        S.ficha_importar("100197-ABC123", {"nome": "Produto"}, "anuncio_ml")
        _linha("ml", "aracatuba", "MLB101", "100197-ABC123", "100197-ABC123")
        S.anuncios_prefixo_corrigir()
        self.assertEqual(_ficha_da_linha("MLB101"), "ABC123")
        self.assertEqual(S.ficha_ler("ABC123")["campos"]["nome"], "Produto")
        self.assertIsNone(S.ficha_ler("100197-ABC123"))

    def test_ficha_com_algo_do_admin_fica(self):
        S.ficha_importar("2005009", {"nome": "Certa"}, "anuncio_ml")
        S.ficha_importar("150142-2005009", {"nome": "Do ML"}, "anuncio_ml")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_ficha_v2023 SET origem_json=? WHERE codigo='150142-2005009'",
                         (json.dumps({"nome": "admin"}),))
            S.DB.commit()
        _linha("ml", "aracatuba", "MLB102", "150142-2005009", "150142-2005009")
        res = S.anuncios_prefixo_corrigir()
        self.assertEqual(_ficha_da_linha("MLB102"), "2005009", "o anúncio vai para o produto certo")
        self.assertIsNotNone(S.ficha_ler("150142-2005009"), "a ficha com dado do Admin nunca é apagada")
        self.assertEqual(res["mantidas"], 1)

    def test_ficha_ligada_por_vinculo_a_outro_codigo_nao_muda(self):
        _linha("ml", "aracatuba", "MLB103", "157244-XYZ999", "OUTRO")  # ficha não é o SKU do anúncio: decisão de alguém
        S.anuncios_prefixo_corrigir()
        self.assertEqual(_ficha_da_linha("MLB103"), "157244-XYZ999")

    def test_idempotente_e_sem_mexer_no_resto(self):
        _linha("ml", "aracatuba", "MLB104", "2005010", "2005010")
        _linha("ml", "aracatuba", "MLB105", "0893600022-0893764272", "0893600022-0893764272")
        _linha("ml", "aracatuba", "MLB106", "144984-2005011", "144984-2005011")
        S.anuncios_prefixo_corrigir()
        res = S.anuncios_prefixo_corrigir()
        self.assertEqual(res["movidos"], 0)
        self.assertEqual(_ficha_da_linha("MLB104"), "2005010")
        self.assertEqual(_ficha_da_linha("MLB105"), "0893600022-0893764272")
        self.assertEqual(_ficha_da_linha("MLB106"), "2005011")

    def test_tiktok_tambem_sem_prefixo(self):
        S._tiktok_sku_gravar({"1738000000000000777": {"product_id": "P1", "titulo": "Peça", "status": "ACTIVATE",
                                                       "seller_sku": "151490-2005012", "warehouse_id": "W",
                                                       "armazens": 1, "qtd": 3}}, apagar_ausentes=False)
        self.assertEqual(_ficha_da_linha("1738000000000000777"), "2005012")


class TrocaDeCasa(TesteNoromak):
    """V20.59 (dono, 12/11: "a mudança de casas está demorando para puxar os anúncios corretos de cada casa")."""

    def test_so_vale_a_ultima_leitura_e_a_lista_antiga_some(self):
        from pathlib import Path
        html = (Path(S.APP_DIR) / "anuncios.html").read_text(encoding="utf-8")
        corpo = html.split("async function carregarLista()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("seq !== LISTA_SEQ", corpo, "resposta atrasada da casa anterior é descartada")
        self.assertIn("L.dados = null; desenharLista()", html, "a lista da casa anterior não fica na tela")


class TelaV2059(TesteNoromak):
    def test_painel_de_estoque_vira_um_botao(self):
        from pathlib import Path
        html = (Path(S.APP_DIR) / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn("function alternarPainelEstoque()", html)
        self.assertIn("if (!PE_ABERTO) { el.innerHTML = botao; return; }", html, "fechado só mostra o botão com o resumo")

    def test_kit_lido_pelo_codigo_abre_o_editor_com_as_pecas(self):
        from pathlib import Path
        html = (Path(S.APP_DIR) / "anuncios.html").read_text(encoding="utf-8")
        corpo = html.split("function desenharKits()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('replace(/^KIT\d*-/i, "").split("/")', corpo, "as peças vêm do código")
        self.assertIn("novoTipo: true", corpo, "salva pelo mesmo caminho do kit novo (Cadastro de Kits)")
