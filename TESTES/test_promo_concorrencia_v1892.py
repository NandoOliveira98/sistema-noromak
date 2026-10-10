"""V18.92 — Promoções automáticas com a conferência rodando e dois computadores abertos.

Em produção (09/10): a conferência de hora em hora segurava a vez por ~5 min. O Renovar
clicado nesse meio esperava até 120 s e o nginx (60 s) devolvia HTTP 504; o anúncio
marcado nesse meio não era conferido (a conferência imediata desistia em 0,01 s e a
rodada já tinha montado a lista dela). Agora a vez é por anúncio: o clique entra entre
um anúncio e outro, e a rodada relê a linha antes de gravar (desmarcar, remover ou mudar
o preço em outro computador não é desfeito por uma conferência que começou antes).
"""

import threading
import time

from base import S
from test_promo_renovacao import HOJE, Base, promo_propria


class ConferenciaRodando(Base):

    def setUp(self):
        super().setUp()
        self.promos["MLB3"] = [promo_propria("candidate")]
        self.itens["MLB3"] = dict(self.itens["MLB1"], titulo="Peça 3")
        for m in ("MLB1", "MLB2", "MLB3"):
            self.marcar(m)
        self.modo("lote")

    def rodada_lenta(self, pausa=0.8):
        """Rodada completa do worker (3 anúncios, `pausa` entre eles), em segundo plano."""
        t = threading.Thread(target=lambda: S.promo_renovacao_rodada(hoje=HOJE, pausa=pausa), daemon=True)
        t.start()
        for _ in range(100):
            if S._PROMO_RENOVACAO_ESTADO.get("rodando"):
                break
            time.sleep(0.01)
        return t

    def test_clique_renovar_nao_espera_a_rodada_inteira(self):
        t = self.rodada_lenta()
        time.sleep(0.2)
        inicio = time.monotonic()
        r = S.promo_renovacao_rodada(hoje=HOJE, pausa=0, so=["MLB3"], ator="admin_teste")
        gasto = time.monotonic() - inicio
        t.join(10)
        self.assertEqual(r["renovadas"], 1, r)
        self.assertLess(gasto, 1.0, "o clique esperou a rodada inteira (era o HTTP 504)")

    def test_marcar_durante_a_rodada_confere_na_hora(self):
        self.promos["MLB4"] = [promo_propria("candidate")]
        self.itens["MLB4"] = dict(self.itens["MLB1"], titulo="Peça 4")
        t = self.rodada_lenta()
        time.sleep(0.2)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar",
                                           {"mlb": "MLB4", "ativo": True, "preco": "90"})
        self.assertEqual(status, 200, corpo)
        for _ in range(15):
            if self.item("MLB4")["situacao"] != "nao_checado":
                break
            time.sleep(0.1)
        situacao = self.item("MLB4")["situacao"]
        t.join(10)
        self.assertEqual(situacao, "pronta", "marcado durante a rodada ficou sem conferir")

    def test_desmarcar_durante_a_rodada_nao_volta_marcado(self):
        t = self.rodada_lenta()
        time.sleep(0.2)
        S.promo_renovacao_marcar("MLB3", False, "outro_pc")
        t.join(10)
        self.assertFalse(self.item("MLB3")["marcado"], "a rodada religou o que foi desmarcado")

    def test_remover_durante_a_rodada_nao_volta_para_a_lista(self):
        t = self.rodada_lenta()
        time.sleep(0.2)
        S.promo_renovacao_remover(["MLB3"], "outro_pc")
        t.join(10)
        self.assertNotIn("MLB3", {i["mlb"] for i in S.promo_renovacao_payload(hoje=HOJE)["itens"]})

    def test_preco_mudado_durante_a_rodada_nao_e_sobrescrito(self):
        t = self.rodada_lenta()
        time.sleep(0.2)
        S.promo_renovacao_marcar("MLB3", True, "outro_pc", preco=85.0)
        t.join(10)
        self.assertEqual(self.item("MLB3")["preco"], 85.0)

    def test_renovar_nos_dois_computadores_cria_uma_promocao(self):
        resultados = []
        corre = [threading.Thread(target=lambda: resultados.append(S.promo_renovacao_rodada(
            hoje=HOJE, pausa=0, so=["MLB1"], ator=pc)), daemon=True) for pc in ("pc1", "pc2")]
        for t in corre:
            t.start()
        for t in corre:
            t.join(10)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(sum(r["renovadas"] for r in resultados), 1)

    def test_rota_checar_diz_que_ja_esta_conferindo(self):
        t = self.rodada_lenta()
        time.sleep(0.2)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/checar", {})
        t.join(10)
        self.assertEqual(status, 200, corpo)
        self.assertEqual((corpo["iniciado"], corpo["rodando"]), (False, True))


class CliqueComPrazo(Base):
    """O clique responde antes do limite do nginx; o que não coube volta em `pendentes`."""

    def test_o_que_nao_coube_no_prazo_volta_pendente(self):
        self.marcar("MLB1")
        self.marcar("MLB2")
        original = S.ml_promocoes_do_item
        S.ml_promocoes_do_item = lambda mlb: (time.sleep(0.4), original(mlb))[1]
        r = S.promo_renovacao_rodada(hoje=HOJE, pausa=0, so=["MLB1", "MLB2"], ator="admin_teste", prazo=0.2)
        self.assertEqual([x["mlb"] for x in r["resultados"]], ["MLB1"])
        self.assertEqual(r["pendentes"], ["MLB2"])
        self.assertEqual(len(self.posts), 1)

    def test_rota_renovar_usa_o_prazo(self):
        self.marcar("MLB1")
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/renovar", {"mlbs": ["MLB1"]})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["pendentes"], [])
        self.assertLess(S.PROMO_MANUAL_PRAZO, 60, "precisa responder antes do proxy_read_timeout do nginx")


class DoisComputadores(Base):

    def test_preco_alterado_em_outro_computador_avisa_em_vez_de_sobrescrever(self):
        self.marcar("MLB1", preco=90.0)
        S.promo_renovacao_marcar("MLB1", True, "outro_pc", preco=95.0)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar",
                                           {"mlb": "MLB1", "ativo": True, "preco": "80", "preco_antes": 90})
        self.assertEqual(status, 409, corpo)
        self.assertIn("95,00", corpo["error"])
        self.assertEqual(self.item()["preco"], 95.0)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar",
                                           {"mlb": "MLB1", "ativo": True, "preco": "80", "preco_antes": 95})
        self.assertEqual(status, 200, corpo)

    def test_sem_preco_antes_continua_como_era(self):
        self.marcar("MLB1", preco=90.0)
        status, corpo = self.cliente.pedir("POST", "/api/promocoes-auto/renovacao/marcar",
                                           {"mlb": "MLB1", "ativo": True, "preco": "80"})
        self.assertEqual(status, 200, corpo)

    def test_versao_muda_quando_outro_computador_altera(self):
        v0 = S.promo_renovacao_versao()["versao"]
        self.marcar("MLB1")
        v1 = S.promo_renovacao_versao()["versao"]
        self.assertNotEqual(v0, v1)
        S.promo_renovacao_remover(["MLB1"], "outro_pc")
        self.assertNotEqual(v1, S.promo_renovacao_versao()["versao"])

    def test_conferencia_sem_mudanca_nao_muda_a_versao(self):
        """Senão a tela recarregaria a lista inteira a cada 10 s durante a rodada."""
        self.marcar("MLB1")
        self.rodar()
        v1 = S.promo_renovacao_versao()["versao"]
        self.rodar()
        self.assertEqual(v1, S.promo_renovacao_versao()["versao"])

    def test_rota_versao(self):
        status, corpo = self.cliente.pedir("GET", "/api/promocoes-auto/renovacao/versao")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["versao"])
