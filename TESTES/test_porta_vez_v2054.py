"""V20.54 — a vez do painel na porta do Apollo (dono, 11/11: "Muitas chamadas ao Apollo neste minuto" em todos os 3 PCs).

A porta do NoromakWeb aceita 12 leituras/min e 6 gravações/min POR ROTA, e todas as chamadas saem do servidor do
Noromak: os 3 computadores e as rotinas automáticas (Faturamento, cadastro automático, TikTok, Full) dividem o mesmo
teto. Regras:
  - toda chamada é contada por rota e por origem (painel = gente, rotina = automático) e aparece na medição;
  - nas rotas de LEITURA a rotina usa no máximo EXP_PORTA_ROTINA_LEITURAS (6) por minuto: o resto fica para o painel.
    Passou disso, a rotina recebe o mesmo PortaErro passageiro de um 429 (que todas já tratam) SEM chamar a porta;
  - depois de um 429 numa rota, a rotina não chama essa rota até o minuto virar;
  - gravação não é segurada (já tem o tratamento dela);
  - a marca de rotina passa para as tarefas em paralelo (prévias juntas).
"""
from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from base import S, reiniciar_banco


class _Resp:
    def __init__(self, corpo=b'{"ok": true}'):
        self.corpo = corpo

    def read(self):
        return self.corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class VezDoPainel(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        S.exp_porta_uso_zerar()
        self.chamadas = []

        def urlopen(req, timeout=None):
            self.chamadas.append(req.full_url.rsplit("/expedicao/", 1)[-1])
            return _Resp()
        self.p1 = mock.patch.object(S.urllib.request, "urlopen", side_effect=urlopen)
        self.p2 = mock.patch.object(S, "CHAVE_CRM", "chave-teste")
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def ler(self, rota="romaneio/previa"):
        return S._exp_porta(rota, {"revenda": 1, "atendimento": 1, "operador": "x"})

    def test_rotina_usa_no_maximo_a_metade_das_leituras(self):
        with S.exp_porta_rotina():
            for _ in range(S.EXP_PORTA_ROTINA_LEITURAS):
                self.assertTrue(self.ler()["ok"])
            with self.assertRaises(S.PortaErro) as erro:
                self.ler()
        self.assertTrue(erro.exception.passageiro)
        self.assertEqual(len(self.chamadas), S.EXP_PORTA_ROTINA_LEITURAS)   # a 7ª nem saiu
        # o painel continua passando no mesmo minuto
        self.assertTrue(self.ler()["ok"])
        self.assertEqual(len(self.chamadas), S.EXP_PORTA_ROTINA_LEITURAS + 1)

    def test_a_reserva_e_por_rota(self):
        with S.exp_porta_rotina():
            for _ in range(S.EXP_PORTA_ROTINA_LEITURAS):
                self.ler("romaneio/previa")
            self.assertTrue(self.ler("cliente/situacao")["ok"])

    def test_painel_nao_e_segurado_aqui(self):
        for _ in range(12):
            self.ler()
        self.assertEqual(len(self.chamadas), 12)

    def test_gravacao_da_rotina_nao_e_segurada(self):
        with S.exp_porta_rotina():
            for _ in range(S.EXP_PORTA_ROTINA_LEITURAS + 2):
                S._exp_porta("romaneio/soltar", {"revenda": 1, "atendimento": 1, "operador": "x"}, escrita=True)
        self.assertEqual(len(self.chamadas), S.EXP_PORTA_ROTINA_LEITURAS + 2)

    def test_depois_de_um_429_a_rotina_espera_o_minuto(self):
        erro = urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b'{"error":"Rate limit exceeded"}'))
        with mock.patch.object(S.urllib.request, "urlopen", side_effect=erro):
            with self.assertRaises(S.PortaErro):
                self.ler()                       # painel levou 429
        with S.exp_porta_rotina():
            with self.assertRaises(S.PortaErro):
                self.ler()                       # rotina nem tenta
        self.assertEqual(self.chamadas, [])
        uso = S.exp_porta_uso()["rotas"]["romaneio/previa"]
        self.assertEqual(uso["recusas_429"], 1)
        self.assertEqual(uso["seguradas"], 1)

    def test_paralelo_leva_a_marca_de_rotina(self):
        with S.exp_porta_rotina():
            S._exp_em_paralelo([lambda: self.ler(), lambda: self.ler()])
        uso = S.exp_porta_uso()["rotas"]["romaneio/previa"]
        self.assertEqual((uso["rotina_1min"], uso["painel_1min"]), (2, 0))

    def test_contagem_por_origem_na_medicao(self):
        self.ler()
        with S.exp_porta_rotina():
            self.ler()
        uso = S.medicao_payload()["porta_apollo"]["rotas"]["romaneio/previa"]
        self.assertEqual((uso["painel_1min"], uso["rotina_1min"], uso["total"]), (1, 1, 2))


class RotinasMarcadas(unittest.TestCase):
    def test_as_rodadas_automaticas_rodam_como_rotina(self):
        vistos = {}

        def espia(nome):
            def f(*a, **k):
                vistos[nome] = S._exp_porta_e_rotina()
                return {} if nome != "faturamento" else 0
            return f
        evento = mock.Mock()
        evento.is_set.side_effect = [False, True]
        evento.wait.return_value = False
        for nome, loop, rodada in (("faturamento", S.faturamento_loop, "faturamento_rodada"),
                                   ("cadastro", S.cadastro_auto_loop, "cadastro_auto_rodada"),
                                   ("full", S.cadastro_full_loop, "cadastro_full_rodada"),
                                   ("tiktok", S.tiktok_atendimento_loop, "tiktok_atendimento_rodada")):
            evento.is_set.side_effect = [False, True]
            with mock.patch.object(S, rodada, side_effect=espia(nome)):
                loop(evento)
            self.assertTrue(vistos.get(nome), nome)
        self.assertFalse(S._exp_porta_e_rotina())


if __name__ == "__main__":
    unittest.main()
