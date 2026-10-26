"""V19.86 — Ruptura: filtro por marca para o Compras (dono, 26/10). A marca vem dos fornecedores
da Demanda de Compra (regras + marca escolhida à mão); kit/"A/B" entra em cada marca das peças;
anúncio sem fornecedor reconhecido fica sem marca."""
import unittest
from datetime import date

from base import S, TesteNoromak, RAIZ

HOJE = date(2026, 10, 6)


class RupturaPorMarca(TesteNoromak):
    def anuncio(self, mlb, sku, estoque=0):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,"
                         "qty,amount,status,updated_at) VALUES('ml',?,'1','2026-09-20','2026-09-20',?,?,'t',5,100,'paid','')",
                         (f"o{mlb}", sku, mlb))
            S.DB.commit()
        S._ml_estoque_registrar({mlb: {"titulo": "Peça", "preco": 50.0, "sku_ml": sku, "status": "active",
                                       "sub_status": [], "estoque": estoque, "categoria": "", "logistica": "drop_off"}},
                                "2026-10-06", quando="2026-10-06T10:00:00")

    def setUp(self):
        super().setUp()
        self._prod = S._compras_produtos_precificador
        produtos = {"0893764272": "HIGIENIZADOR", "4045": "VEDADOR HS", "10252986": "PAINEL VOLARE", "XYZ1": "OUTRA PECA"}
        S._compras_produtos_precificador = lambda: {c: {"descricao": d, "curva": "A"} for c, d in produtos.items()}
        S.compras_salvar_fornecedor("WURTH", {"prefixos": ["0893"]}, "admin")
        S.compras_salvar_fornecedor("HS BOND", {"codigos": ["4045"]}, "admin")
        S.compras_salvar_fornecedor("VOLARE", {"palavras": ["VOLARE"]}, "admin")

    def tearDown(self):
        S._compras_produtos_precificador = self._prod
        super().tearDown()

    def itens(self):
        d = S.ruptura_payload(30, hoje=HOJE)
        return d, {i["mlb"]: i for i in d["itens"]}

    def test_marca_pelas_regras_da_demanda(self):
        self.anuncio("MLB1", "150922-0893764272")
        self.anuncio("MLB4", "10252986")
        self.anuncio("MLB3", "XYZ1")
        self.anuncio("MLB5", "NAOEXISTE")
        d, i = self.itens()
        self.assertEqual(i["MLB1"]["marcas"], ["WURTH"])
        self.assertEqual(i["MLB4"]["marcas"], ["VOLARE"])
        self.assertEqual(i["MLB3"]["marcas"], [])
        self.assertEqual(i["MLB5"]["marcas"], [])
        self.assertEqual(d["marcas"], ["VOLARE", "WURTH"])   # só as que têm anúncio na lista

    def test_kit_e_a_barra_b_entram_em_cada_marca(self):
        self.anuncio("MLB2", "0893764272/4045")
        _, i = self.itens()
        self.assertEqual(i["MLB2"]["marcas"], ["HS BOND", "WURTH"])

    def test_marca_escolhida_a_mao_vence_a_regra(self):
        S.compras_marca_manual_salvar("0893764272", "HS BOND", "admin")
        self.anuncio("MLB1", "0893764272")
        _, i = self.itens()
        self.assertEqual(i["MLB1"]["marcas"], ["HS BOND"])

    def test_tela_tem_o_filtro(self):
        html = (RAIZ / "APP" / "ruptura.html").read_text(encoding="utf-8")
        self.assertIn('id="fMarca"', html)
        self.assertIn("nk_ruptura_marca_v1986", html)


if __name__ == "__main__":
    unittest.main()
