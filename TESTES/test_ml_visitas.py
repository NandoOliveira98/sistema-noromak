"""V18.68 — coleta diária das visitas dos anúncios do ML (base da conversão).

O ML é simulado: nenhum teste aqui chama a API de verdade.
"""

import unittest
from datetime import date

from base import S, TesteNoromak

HOJE = date(2026, 10, 6)


def resposta(item_id, dias):
    return {"item_id": item_id, "unit": "day", "last": 30,
            "results": [{"date": f"{d}T00:00:00Z", "total": v} for d, v in dias.items()]}


class VisitasML(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_todos_os_anuncios, S.ml_integration_row)
        S.ml_integration_row = lambda: {"access_token": "TESTE", "seller_id": "1"}
        S.ml_todos_os_anuncios = lambda forcar=False: ["MLB1", "MLB2", "MLB3"]
        self.chamadas = []
        self.respostas = {
            "MLB1": resposta("MLB1", {"2026-10-04": 10, "2026-10-05": 7}),
            "MLB2": resposta("MLB2", {"2026-10-05": 3}),
            "MLB3": resposta("MLB3", {"2026-10-05": 0}),
        }

        def falso(rota, **_):
            self.chamadas.append(rota)
            item = rota.split("/")[2]
            r = self.respostas[item]
            if isinstance(r, Exception):
                raise r
            return r
        S.ml_api_get = falso

    def tearDown(self):
        S.ml_api_get, S.ml_todos_os_anuncios, S.ml_integration_row = self._originais
        super().tearDown()

    def rodar(self):
        return S.ml_visitas_rodada(hoje=HOJE, forcar_horario=True, pausa=0)

    def visitas(self):
        with S.DB_LOCK:
            return {(r["item_id"], r["dia"]): r["visitas"] for r in S.DB.execute(
                "SELECT item_id, dia, visitas FROM ml_visitas_dia_v1868").fetchall()}

    def test_grava_as_visitas_por_anuncio_e_por_dia(self):
        r = self.rodar()
        self.assertEqual(r["buscados"], 3)
        v = self.visitas()
        self.assertEqual(v[("MLB1", "2026-10-04")], 10)
        self.assertEqual(v[("MLB1", "2026-10-05")], 7)
        self.assertEqual(v[("MLB3", "2026-10-05")], 0, "zero de verdade também é gravado")
        self.assertIn("last=30&unit=day&ending=2026-10-06", self.chamadas[0])

    def test_segunda_rodada_no_mesmo_dia_nao_busca_de_novo(self):
        self.rodar()
        self.chamadas.clear()
        r = self.rodar()
        self.assertEqual(self.chamadas, [], "anúncio já buscado hoje foi buscado de novo")
        self.assertEqual(r["ja_buscados_hoje"], 3)

    def test_dia_seguinte_regrava_a_janela_e_guarda_o_historico(self):
        self.rodar()
        # no dia seguinte o ML corrige o dia 05 e o dia 04 já saiu da resposta
        self.respostas["MLB1"] = resposta("MLB1", {"2026-10-05": 9, "2026-10-06": 4})
        S.ml_visitas_rodada(hoje=date(2026, 10, 7), forcar_horario=True, pausa=0)
        v = self.visitas()
        self.assertEqual(v[("MLB1", "2026-10-05")], 9, "o dia corrigido pelo ML não foi regravado")
        self.assertEqual(v[("MLB1", "2026-10-04")], 10, "o histórico fora da janela foi apagado")

    def test_erro_fica_pendente_e_e_retomado(self):
        self.respostas["MLB2"] = RuntimeError("Falha de conexão com Mercado Livre: timeout")
        r = self.rodar()
        self.assertEqual((r["buscados"], r["erros"]), (2, 1))
        self.respostas["MLB2"] = resposta("MLB2", {"2026-10-05": 3})
        self.chamadas.clear()
        r = self.rodar()
        self.assertEqual(len(self.chamadas), 1, "a retomada deveria buscar só o que falhou")
        self.assertEqual(self.visitas()[("MLB2", "2026-10-05")], 3)

    def test_anuncio_inexistente_nao_trava_a_rodada(self):
        self.respostas["MLB1"] = RuntimeError('Mercado Livre HTTP 404: {"error":"not_found"}')
        r = self.rodar()
        self.assertEqual((r["buscados"], r["nao_encontrados"], r["erros"]), (2, 1, 0))
        self.chamadas.clear()
        self.rodar()
        self.assertEqual(self.chamadas, [], "anúncio 404 deveria esperar o dia seguinte")

    def test_sem_permissao_para_a_rodada_sem_martelar_o_ml(self):
        S.ml_todos_os_anuncios = lambda forcar=False: [f"MLB{i}" for i in range(1, 21)]
        erro = RuntimeError("Mercado Livre HTTP 403: PA_UNAUTHORIZED_RESULT_FROM_POLICIES")
        self.respostas = {f"MLB{i}": erro for i in range(1, 21)}
        r = self.rodar()
        self.assertTrue(r["interrompido"])
        self.assertEqual(len(self.chamadas), S.ML_VISITAS_ERROS_SEGUIDOS)

    def test_ml_desconectado_nao_chama_a_api(self):
        S.ml_integration_row = lambda: None
        r = self.rodar()
        self.assertIn("pulado", r)
        self.assertEqual(self.chamadas, [])

    def test_status_separa_anuncio_sumido_de_erro(self):
        self.respostas["MLB1"] = RuntimeError('Mercado Livre HTTP 404: {"error":"not_found"}')
        S.ml_visitas_rodada(forcar_horario=True, pausa=0)   # hoje de verdade, como o status
        st = S.ml_visitas_status()
        self.assertEqual((st["com_erro"], st["nao_encontrados_hoje"]), (0, 1),
                         "anúncio que o ML não acha não é falha da coleta")

    def test_status_so_para_admin(self):
        self.rodar()
        status, corpo = self.cliente.pedir("GET", "/api/ml-visitas/status")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["linhas"], 4)
        self.assertEqual(corpo["historico_desde"], "2026-10-04")


if __name__ == "__main__":
    unittest.main()
