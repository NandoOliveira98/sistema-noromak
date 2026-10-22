"""V19.76 — trocar a promoção pela gaveta do Catálogo ML (decisões do dono, 22/10).

O ADMIN digita o preço na gaveta e a promoção própria (PRICE_DISCOUNT) troca NA HORA no ML,
pela mesma ml_promocao_aplicar (trava do preço mínimo no servidor), de hoje até hoje + a duração
das Promoções automáticas. O anúncio fica marcado na renovação com o preço novo.
Se o ML recusar (já existe promoção valendo), NÃO encerra sozinho: devolve a pergunta; só com
`encerrar` confirmado encerra a atual e cria a nova.
"""
from datetime import date, timedelta

from base import S, TesteNoromak


class TrocarPromocao(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.salvos = {n: getattr(S, n) for n in ("ml_promocao_aplicar", "ml_promocao_remover", "promo_renovacao_rodada")}
        self.chamadas = []
        self.recusa = None

        def aplicar(mlb, preco, inicio, fim, ator="", sku=""):
            self.chamadas.append(("aplicar", mlb, preco, inicio, fim))
            if self.recusa:
                raise self.recusa
            return {"ok": True, "mlb": mlb, "preco": preco, "margem": 0.15, "fim": fim}
        S.ml_promocao_aplicar = aplicar
        S.ml_promocao_remover = lambda mlb, ator="": self.chamadas.append(("remover", mlb)) or {"ok": True}
        S.promo_renovacao_rodada = lambda *a, **k: {"ok": True}

    def tearDown(self):
        for n, f in self.salvos.items():
            setattr(S, n, f)
        super().tearDown()

    def renovacao(self):
        with S.DB_LOCK:
            return S.DB.execute("SELECT * FROM promo_renovacao_v1873 WHERE mlb='MLB1'").fetchone()

    def test_troca_direto_e_marca_a_renovacao_com_o_preco(self):
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", "205,00", "admin")
        self.assertTrue(r["ok"])
        hoje = date.today()
        fim = (hoje + timedelta(days=S.promo_auto_config()["duracao_dias"])).isoformat()
        self.assertEqual(self.chamadas, [("aplicar", "MLB1", 205.0, hoje.isoformat() + "T00:00:00", fim + "T00:00:00")])
        row = self.renovacao()
        self.assertEqual((row["marcado"], row["preco"], row["conta"]), (1, 205.0, "aracatuba"))

    def test_recusa_do_ml_pergunta_e_nao_encerra(self):
        self.recusa = RuntimeError("Mercado Livre respondeu HTTP 400: item already has a promotion")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "ml_recusou")
        self.assertIn("already has", r["error"])
        self.assertNotIn("remover", [c[0] for c in self.chamadas])
        self.assertIsNone(self.renovacao())

    def test_encerrar_confirmado_encerra_e_cria(self):
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertTrue(r["ok"])
        self.assertEqual([c[0] for c in self.chamadas], ["remover", "aplicar"])

    def test_falha_depois_de_encerrar_avisa_que_ficou_sem_desconto(self):
        self.recusa = RuntimeError("HTTP 500")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "sem_desconto")
        self.assertIn("sem desconto", r["error"])

    def test_trava_do_preco_minimo_nao_oferece_encerrar(self):
        self.recusa = ValueError("R$ 10,00 está abaixo do preço mínimo")
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 10, "admin")

    def test_preco_e_conta_invalidos(self):
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", "0", "admin")
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("marte", "MLB1", 10, "admin")

    def test_rota(self):
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 205})
        self.assertEqual((st, d["ok"]), (200, True))
        self.recusa = RuntimeError("HTTP 400: já tem")
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 205})
        self.assertEqual((st, d["code"]), (409, "ml_recusou"))
        self.recusa = ValueError("abaixo do mínimo")
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 1})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
