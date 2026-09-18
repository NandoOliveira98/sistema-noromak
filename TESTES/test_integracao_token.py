"""V18.01 — renovação de token não pode morrer sozinha no fim de semana.

Dois problemas reais que deixavam ML e Shopee sem sincronizar por dias, até
alguém reconectar à mão:

- Mercado Livre: quando o ML devolvia a renovação SEM um refresh_token novo, o
  servidor gravava a chave de renovação vazia por cima da boa — e não havia
  mais como renovar.
- Shopee: a chave de refresh é de uso único; dois workers renovando ao mesmo
  tempo gastavam a mesma chave e quebravam a integração.
"""

import threading
import time
import unittest

from base import S


def setUpModule():
    S.init_db()


class MlPreservaRefreshToken(unittest.TestCase):
    """O refresh do ML nunca pode apagar a chave de renovação existente."""

    def setUp(self):
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM ml_integration")
            S.DB.execute(
                """INSERT INTO ml_integration(id,seller_id,nickname,access_token,refresh_token,
                     token_type,scope,expires_at_epoch,connected_at,updated_at,last_sync_at,last_error)
                   VALUES(1,'123','loja','ACCESS_ANTIGO','REFRESH_BOM','Bearer','offline_access read',?,?,?,'','')""",
                (int(time.time()) + 60, S.now_iso(), S.now_iso()),
            )
            S.DB.commit()

    def test_resposta_sem_refresh_token_preserva_a_chave(self):
        # É o que ml_access_token faz ao renovar (V18.01: preserve=True).
        S.ml_store_token(
            {"user_id": "123", "access_token": "ACCESS_NOVO", "expires_in": 21600,
             "scope": "offline_access read"},  # <-- sem refresh_token
            preserve_refresh_if_missing=True, refresh_expected=True,
        )
        row = S.ml_integration_row()
        self.assertEqual(row["access_token"], "ACCESS_NOVO", "o access_token novo tem que entrar")
        self.assertEqual(row["refresh_token"], "REFRESH_BOM",
                         "REGRESSÃO: a chave de renovação foi apagada — a integração morreria")

    def test_o_bug_antigo_apagava_a_chave(self):
        # Prova de que o comportamento antigo (preserve=False) era o assassino.
        S.ml_store_token(
            {"user_id": "123", "access_token": "ACCESS_NOVO", "expires_in": 21600,
             "scope": "offline_access read"},
            preserve_refresh_if_missing=False, refresh_expected=True,
        )
        self.assertEqual(S.ml_integration_row()["refresh_token"], "",
                         "com preserve=False a chave era apagada — por isso a V18.01 usa preserve=True")

    def test_resposta_com_refresh_novo_rotaciona(self):
        S.ml_store_token(
            {"user_id": "123", "access_token": "A2", "refresh_token": "REFRESH_NOVO",
             "expires_in": 21600, "scope": "offline_access read"},
            preserve_refresh_if_missing=True, refresh_expected=True,
        )
        self.assertEqual(S.ml_integration_row()["refresh_token"], "REFRESH_NOVO")


class ShopeeRefreshSerializado(unittest.TestCase):
    """A renovação da Shopee não pode gastar a chave de uso único duas vezes."""

    def setUp(self):
        self.original = S._shopee_chamar_refresh
        self.chamadas = []
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM shopee_integration")
            S.DB.execute(
                """INSERT INTO shopee_integration(id,shop_id,access_token,refresh_token,
                     token_expires_at,connected_at,updated_at) VALUES(1,'55','ACC0','REF0',?,?,?)""",
                (int(time.time()) - 10, S.now_iso(), S.now_iso()),  # já vencido
            )
            S.DB.commit()

    def tearDown(self):
        S._shopee_chamar_refresh = self.original

    def fingir_rede(self, atraso=0.0):
        def falso(shop_id, refresh_token):
            self.chamadas.append(refresh_token)
            if atraso:
                time.sleep(atraso)
            n = len(self.chamadas)
            return {"access_token": f"ACC{n}", "refresh_token": f"REF{n}", "expire_in": 14400}
        S._shopee_chamar_refresh = falso

    def test_uma_renovacao_por_vez_mesmo_com_workers_concorrentes(self):
        self.fingir_rede(atraso=0.15)
        erros = []
        def worker():
            try:
                S.shopee_refresh_access_token(False)
            except Exception as exc:  # pragma: no cover
                erros.append(str(exc))
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(erros, [], f"algum worker falhou: {erros}")
        self.assertEqual(len(self.chamadas), 1,
                         "REGRESSÃO: 5 workers gastaram a chave de refresh mais de uma vez")
        self.assertEqual(S.shopee_integration_row()["access_token"], "ACC1")

    def test_forca_concorrente_apos_401_renova_uma_vez_so(self):
        # Dois workers recebem 401 com o mesmo token e forçam renovar juntos.
        # Só um pode gastar a chave; o outro reaproveita o token novo.
        self.fingir_rede(atraso=0.15)
        def worker():
            S.shopee_refresh_access_token(True)
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.chamadas), 1,
                         "force concorrente gastou a chave de refresh mais de uma vez")

    def test_forca_sequencial_renova_de_novo(self):
        # Mesma thread forçando duas vezes: é legítimo renovar de novo
        # (o token anterior foi de fato rejeitado).
        self.fingir_rede()
        S.shopee_refresh_access_token(True)
        S.shopee_refresh_access_token(True)
        self.assertEqual(len(self.chamadas), 2)

    def test_token_valido_nao_renova(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE shopee_integration SET access_token='ACCX',token_expires_at=? WHERE id=1",
                         (int(time.time()) + 3600,))
            S.DB.commit()
        self.fingir_rede()
        S.shopee_refresh_access_token(False)
        self.assertEqual(self.chamadas, [], "não devia renovar um token ainda válido")


if __name__ == "__main__":
    unittest.main()
