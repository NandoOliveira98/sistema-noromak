"""V20.18 — reforço do aviso do Mercado Livre (webhook /api/integracoes/mercadolivre/notificacoes).

Revisão de segurança de 08/10: o endereço é aberto (quem chama é o ML) e aceitava qualquer aviso.
Desde a V20.16 é o aviso que traz pedido e etiqueta, então ele precisa aguentar aviso falso:

- aviso sem application_id, ou de outro aplicativo, não chega ao ML;
- aviso sem user_id, ou de conta que não está conectada, não chega ao ML (antes ia para Araçatuba);
- só pedido e envio com número (/orders/123, /shipments/456) chegam ao ML;
- o aviso recusado não enche o log (um registro por motivo, não um por aviso);
- os avisos vão para uma fila de tamanho fixo tratada por poucas tarefas (antes: uma tarefa por aviso,
  sem limite); o mesmo recurso ainda na fila não entra duas vezes; fila cheia descarta (o vigia de 5 min
  cobre); corpo grande é recusado sem ser lido.

Em produção (04/11, 6.000 últimas linhas do ml_event_log) os 2.244 avisos de pedido e envio das duas
contas chegaram todos com application_id e user_id: exigir os dois não derruba aviso de verdade.
"""

import json
import queue
import unittest
import urllib.error
import urllib.request
from unittest import mock

from base import S, TesteNoromak


def token(seller: str, acesso: str) -> dict:
    return {"user_id": seller, "access_token": acesso, "refresh_token": "R", "token_type": "Bearer",
            "scope": "offline_access read write", "expires_in": 21600}


def aviso(**extra) -> dict:
    corpo = {"topic": "orders_v2", "resource": "/orders/2000019000000001", "user_id": "111",
             "application_id": "APP1"}
    corpo.update(extra)
    return corpo


class ReforcoDoAviso(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for alvo, valor in (("ml_credentials_configured", True),):
            p = mock.patch.object(S, alvo, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(S, "ML_CLIENT_ID", "APP1")
        p.start()
        self.addCleanup(p.stop)
        with S.ml_na_casa("aracatuba"):
            S.ml_store_token(token("111", "TOK-ARA"), nickname="conta-aracatuba")
        with S.ml_na_casa("bauru"):
            S.ml_store_token(token("222", "TOK-BAU"), nickname="conta-bauru")
        S._ML_LOG_ULTIMO.clear()
        self.importados: list[tuple[str, str]] = []
        self.envios: list[str] = []
        p = mock.patch.object(S, "ml_import_order",
                              side_effect=lambda oid, **k: self.importados.append((oid, S.ml_casa_atual())) or {})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(S, "ml_api_get", side_effect=lambda caminho, **k: self.envios.append(caminho) or [])
        p.start()
        self.addCleanup(p.stop)

    def chamadas_ao_ml(self) -> int:
        return len(self.importados) + len(self.envios)

    def linhas_do_log(self) -> int:
        with S.DB_LOCK:
            return S.DB.execute("SELECT COUNT(*) FROM ml_event_log").fetchone()[0]

    # --- quem manda --------------------------------------------------------

    def test_aviso_valido_continua_entrando(self):
        S.ml_process_notification(aviso())
        S.ml_process_notification(aviso(topic="shipments", resource="/shipments/46000000001", user_id="222"))
        self.assertEqual(self.importados, [("2000019000000001", "aracatuba")])
        self.assertEqual(self.envios, ["/shipments/46000000001/orders"], "o envio de Bauru tem de ser lido")

    def test_sem_application_id_nao_chega_ao_ml(self):
        S.ml_process_notification(aviso(application_id=""))
        payload = aviso()
        del payload["application_id"]
        S.ml_process_notification(payload)
        self.assertEqual(self.chamadas_ao_ml(), 0, "aviso sem application_id era aceito (revisão de 08/10)")

    def test_outro_aplicativo_nao_chega_ao_ml(self):
        S.ml_process_notification(aviso(application_id="OUTRO"))
        self.assertEqual(self.chamadas_ao_ml(), 0)

    def test_sem_user_id_nao_vai_para_aracatuba(self):
        S.ml_process_notification(aviso(user_id=""))
        payload = aviso()
        del payload["user_id"]
        S.ml_process_notification(payload)
        self.assertEqual(self.chamadas_ao_ml(), 0, "aviso sem user_id era tratado como de Araçatuba")

    def test_conta_que_nao_esta_conectada_nao_chega_ao_ml(self):
        S.ml_process_notification(aviso(user_id="999"))
        self.assertEqual(self.chamadas_ao_ml(), 0)

    # --- o que pede --------------------------------------------------------

    def test_so_pedido_e_envio_com_numero(self):
        for topico, recurso in (
                ("orders_v2", "/orders/abc"),
                ("orders_v2", "/orders/1/../../users/me"),
                ("orders_v2", "/orders/"),
                ("orders_v2", "/shipments/123"),          # tópico de pedido com recurso de envio
                ("shipments", "/orders/123"),
                ("shipments", "/shipments/123?x=1"),
                ("questions", "/questions/123"),          # tópico que o Noromak não trata
                ("orders_v2", "/orders/" + "9" * 40),     # número absurdo
        ):
            S.ml_process_notification(aviso(topic=topico, resource=recurso))
        self.assertEqual(self.chamadas_ao_ml(), 0, f"chegou ao ML: {self.importados} {self.envios}")

    def test_orders_antigo_e_barra_no_fim_continuam(self):
        S.ml_process_notification(aviso(topic="orders", resource="/orders/2000019000000002/"))
        self.assertEqual(self.importados, [("2000019000000002", "aracatuba")])

    # --- o log não vira alvo ----------------------------------------------

    def test_aviso_recusado_nao_enche_o_log(self):
        antes = self.linhas_do_log()
        for i in range(50):
            S.ml_process_notification(aviso(application_id="", resource=f"/orders/{1000 + i}"))
        self.assertLessEqual(self.linhas_do_log() - antes, 1, "cada aviso falso virava um INSERT no banco")

    def test_recusa_fica_registrada_uma_vez(self):
        S.ml_process_notification(aviso(user_id="999"))
        with S.DB_LOCK:
            linha = S.DB.execute("SELECT result, detail FROM ml_event_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(linha[0], "IGNORADO")
        self.assertIn("conta", linha[1].lower())


class FilaDosAvisos(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self.fila: queue.Queue = queue.Queue(maxsize=3)
        for alvo, valor in (("_ML_AVISO_FILA", self.fila), ("_ML_AVISO_NA_FILA", set())):
            p = mock.patch.object(S, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(S, "_ml_aviso_iniciar_tarefas")   # sem tarefas: a fila só enche
        p.start()
        self.addCleanup(p.stop)

    def test_mesmo_recurso_na_fila_entra_uma_vez(self):
        self.assertEqual(S.ml_aviso_enfileirar(aviso()), "na_fila")
        self.assertEqual(S.ml_aviso_enfileirar(aviso()), "repetido")
        self.assertEqual(self.fila.qsize(), 1)

    def test_fila_cheia_descarta(self):
        for i in range(3):
            self.assertEqual(S.ml_aviso_enfileirar(aviso(resource=f"/orders/{i + 1}")), "na_fila")
        self.assertEqual(S.ml_aviso_enfileirar(aviso(resource="/orders/99")), "fila_cheia")
        self.assertEqual(self.fila.qsize(), 3)

    def test_recurso_volta_a_entrar_quando_sai_da_fila(self):
        S.ml_aviso_enfileirar(aviso())
        item = self.fila.get_nowait()
        S._ml_aviso_saiu_da_fila(item)
        self.assertEqual(S.ml_aviso_enfileirar(aviso()), "na_fila",
                         "aviso novo do mesmo pedido durante o tratamento precisa entrar (o pedido mudou)")

    def test_rota_responde_ok_e_enfileira(self):
        status, corpo = self.cliente.pedir("POST", "/api/integracoes/mercadolivre/notificacoes", aviso())
        self.assertEqual((status, corpo.get("ok")), (200, True))
        self.assertEqual(self.fila.qsize(), 1, "a rota tem de usar a fila, não abrir uma tarefa por aviso")

    def test_corpo_grande_recusado_sem_ler(self):
        dados = json.dumps(aviso(lixo="x" * (S.ML_AVISO_MAX_BYTES + 10))).encode()
        req = urllib.request.Request(self.url_base + "/api/integracoes/mercadolivre/notificacoes",
                                     data=dados, method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as erro:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(erro.exception.code, 413)
        self.assertEqual(self.fila.qsize(), 0)

    def test_tarefas_limitadas(self):
        self.assertLessEqual(S.ML_AVISO_TAREFAS, 4)
        self.assertLessEqual(S.ML_AVISO_FILA_MAX, 1000)


if __name__ == "__main__":
    unittest.main()
