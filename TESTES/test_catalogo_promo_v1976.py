"""V19.76 — trocar a promoção pela gaveta do Catálogo ML (decisões do dono, 22/10).

O ADMIN digita o preço na gaveta e a promoção própria (PRICE_DISCOUNT) troca NA HORA no ML,
pela mesma ml_promocao_aplicar (trava do preço mínimo no servidor), de hoje até hoje + a duração
das Promoções automáticas. O anúncio fica marcado na renovação com o preço novo.
Se o ML recusar (já existe promoção valendo), NÃO encerra sozinho: devolve a pergunta; só com
`encerrar` confirmado encerra a atual e cria a nova.

Revisão antes de subir (22/10): as travas são conferidas ANTES de encerrar; a troca pega a vez do
anúncio (a mesma da renovação, V18.92) e grava o renovado_dia; com campanha do ML valendo, não
oferece encerrar.
"""
import threading
from datetime import date, timedelta

from base import S, TesteNoromak

PD_VALENDO = {"type": "PRICE_DISCOUNT", "status": "started", "price": 206.5}
DEAL_VALENDO = {"type": "DEAL", "status": "started", "price": 199.0, "name": "Ofertas da semana"}


class TrocarPromocao(TesteNoromak):

    def setUp(self):
        super().setUp()
        self.salvos = {n: getattr(S, n) for n in ("ml_promocao_aplicar", "ml_promocao_remover", "promo_renovacao_rodada",
                                                   "ml_promocoes_do_item", "CATALOGO_PROMO_ESPERA")}
        self.chamadas = []
        self.recusa = None          # erro do POST no ML
        self.trava = None           # ValueError da conferência (preço mínimo, piso/teto)
        self.trava_depois = None    # ValueError só na 2ª conferência (depois de encerrar)
        self.promos = [dict(PD_VALENDO)]

        def aplicar(mlb, preco, inicio, fim, ator="", sku="", so_validar=False):
            self.chamadas.append(("validar" if so_validar else "aplicar", mlb, preco, inicio, fim))
            if self.trava:
                raise self.trava
            if not so_validar and self.trava_depois and ("remover", mlb) in self.chamadas:
                raise self.trava_depois
            if so_validar:
                return {"ok": True, "margem": 0.15}
            if self.recusa:
                raise self.recusa
            return {"ok": True, "mlb": mlb, "preco": preco, "margem": 0.15, "fim": fim}
        S.ml_promocao_aplicar = aplicar
        S.ml_promocao_remover = lambda mlb, ator="": self.chamadas.append(("remover", mlb)) or {"ok": True}
        S.promo_renovacao_rodada = lambda *a, **k: {"ok": True}
        S.ml_promocoes_do_item = lambda mlb: [dict(x) for x in self.promos]

    def tearDown(self):
        for n, f in self.salvos.items():
            setattr(S, n, f)
        super().tearDown()

    def renovacao(self):
        with S.DB_LOCK:
            return S.DB.execute("SELECT * FROM promo_renovacao_v1873 WHERE mlb='MLB1'").fetchone()

    def acoes(self):
        return [c[0] for c in self.chamadas]

    def test_troca_direto_e_marca_a_renovacao_com_o_preco(self):
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", "205,00", "admin")
        self.assertTrue(r["ok"])
        hoje = date.today()
        fim = (hoje + timedelta(days=S.promo_auto_config()["duracao_dias"])).isoformat()
        self.assertEqual(self.chamadas[-1], ("aplicar", "MLB1", 205.0, hoje.isoformat() + "T00:00:00", fim + "T00:00:00"))
        row = self.renovacao()
        self.assertEqual((row["marcado"], row["preco"], row["conta"]), (1, 205.0, "aracatuba"))

    def test_recusa_do_ml_pergunta_e_nao_encerra(self):
        self.recusa = RuntimeError("Mercado Livre respondeu HTTP 400: item already has a promotion")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "ml_recusou")
        self.assertIn("already has", r["error"])
        self.assertNotIn("remover", self.acoes())
        self.assertIsNone(self.renovacao())

    def test_encerrar_confirmado_encerra_e_cria(self):
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertTrue(r["ok"])
        self.assertEqual(self.acoes(), ["validar", "remover", "aplicar"])

    def test_falha_depois_de_encerrar_avisa_que_ficou_sem_desconto(self):
        self.recusa = RuntimeError("HTTP 500")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "sem_desconto")
        self.assertIn("sem desconto", r["error"])

    def test_trava_do_preco_minimo_nao_oferece_encerrar(self):
        self.trava = ValueError("R$ 10,00 está abaixo do preço mínimo")
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 10, "admin")

    def test_preco_e_conta_invalidos(self):
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", "0", "admin")
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("marte", "MLB1", 10, "admin")

    # --- revisão de 22/10 ---

    def test_alto_trava_conferida_antes_de_encerrar(self):
        # a trava recusa: nada é encerrado (antes encerrava e só depois descobria)
        self.trava = ValueError("abaixo do preço mínimo")
        with self.assertRaises(ValueError):
            S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertNotIn("remover", self.acoes())

    def test_alto_trava_depois_de_encerrar_avisa_sem_desconto(self):
        # passou na conferência, encerrou, e a 2ª conferência recusou (piso do ML mudou)
        self.trava_depois = ValueError("O Mercado Livre não aceita menos de R$ 210,00")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "sem_desconto")
        self.assertIn("210", r["error"])

    def test_medio_troca_espera_a_vez_do_anuncio(self):
        S.CATALOGO_PROMO_ESPERA = 0.05
        S.PROMO_RENOVACAO_LOCK.acquire()
        try:
            with self.assertRaises(S.PromoOcupado):
                S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        finally:
            S.PROMO_RENOVACAO_LOCK.release()
        self.assertEqual(self.chamadas, [])

    def test_medio_ml_chamado_dentro_da_vez(self):
        dentro = []
        orig = S.ml_promocao_aplicar

        def espia(*a, **k):
            dentro.append(S.PROMO_RENOVACAO_LOCK.locked())
            return orig(*a, **k)
        S.ml_promocao_aplicar = espia
        S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertTrue(dentro and all(dentro))
        self.assertFalse(S.PROMO_RENOVACAO_LOCK.locked())

    def test_medio_grava_renovado_hoje(self):
        S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        self.assertEqual(self.renovacao()["renovado_dia"], date.today().isoformat())

    def test_medio_campanha_do_ml_nao_oferece_encerrar(self):
        self.promos = [dict(PD_VALENDO, status="finished"), dict(DEAL_VALENDO)]
        self.recusa = RuntimeError("HTTP 400: item in a deal")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        self.assertEqual(r["code"], "em_campanha")
        self.assertIn("Ofertas da semana", r["error"])
        # e mesmo com encerrar=True (tela antiga/duplo clique) não encerra
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertEqual(r["code"], "em_campanha")
        self.assertNotIn("remover", self.acoes())

    def test_campanha_vencida_que_o_ml_ainda_mostra_started_nao_trava(self):
        # V19.75: o ML segue devolvendo "started" horas depois do fim
        self.promos = [dict(PD_VALENDO), dict(DEAL_VALENDO, finish_date="2020-01-01T00:00:00")]
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin", encerrar=True)
        self.assertTrue(r["ok"])
        self.assertIn("remover", self.acoes())

    def test_medio_sem_desconto_proprio_valendo_nao_oferece_encerrar(self):
        self.promos = []
        self.recusa = RuntimeError("HTTP 400: outro motivo")
        r = S.catalogo_ml_trocar_promocao("aracatuba", "MLB1", 205, "admin")
        self.assertEqual(r["code"], "ml_erro")

    def test_rota(self):
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 205})
        self.assertEqual((st, d["ok"]), (200, True))
        self.recusa = RuntimeError("HTTP 400: já tem")
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 205})
        self.assertEqual((st, d["code"]), (409, "ml_recusou"))
        self.recusa = None
        self.trava = ValueError("abaixo do mínimo")
        st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 1})
        self.assertEqual(st, 400)
        self.trava = None
        S.CATALOGO_PROMO_ESPERA = 0.05
        S.PROMO_RENOVACAO_LOCK.acquire()
        try:
            st, d = self.cliente.pedir("POST", "/api/catalogo-ml/promocao", {"conta": "aracatuba", "item": "MLB1", "preco": 205})
        finally:
            S.PROMO_RENOVACAO_LOCK.release()
        self.assertEqual((st, d.get("code")), (409, "ocupado"))


if __name__ == "__main__":
    import unittest
    unittest.main()
