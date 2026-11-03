"""V20.16 — o aviso do Mercado Livre (webhook orders_v2/shipments) é quem traz pedido e etiqueta.

Decisão do dono (03/11/2026): "só um push quando houver movimentação". O aviso do ML já chega
nas duas contas; os vigias de 15 s / 20 s viram rede de segurança de até 5 minutos para o aviso
que se perder. O vigia de etiquetas revê todos os pendentes a cada volta (antes: 20 por ciclo).
"""

import time
import unittest
from datetime import datetime, timedelta, timezone

from base import S, TesteNoromak, gravar_como_integracao, linha


class IntervalosDaRedeDeSeguranca(unittest.TestCase):

    def test_vigias_do_ml_rodam_a_cada_5_minutos(self):
        self.assertEqual(S.ML_NEW_ORDER_WATCH_INTERVAL, 300)
        self.assertEqual(S.ML_PENDING_WATCH_INTERVAL, 300)

    def test_erro_nao_acelera_o_vigia(self):
        # o recuo por erro limitava a espera a 120 s: com o intervalo de 300 s, erro faria rodar MAIS
        self.assertGreaterEqual(S._ml_new_order_espera(0), 300)
        self.assertGreaterEqual(S._ml_new_order_espera(3), 300)


class VoltaCompletaNaFila(TesteNoromak):

    def test_uma_volta_revisa_todos_os_pendentes(self):
        linhas = [linha(f"20000000000{i:02d}", "SKU", uid=f"u{i}", Marketplace="MERCADO LIVRE",
                        VendaMercadoLivre=f"20000000000{i:02d}", ShipmentIdMercadoLivre=f"44000000000{i:02d}",
                        OrigemCadastro="API Mercado Livre") for i in range(70)]
        gravar_como_integracao(linhas, origem="API Mercado Livre")
        lote = S._ml_pending_label_candidates()
        self.assertEqual(len(lote), 70,
                         "com o vigia a cada 5 min, revisar só 20 por volta deixaria pedido 20 min sem olhar")


class SaudeComIntervaloNovo(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._orig = (S.ml_status_payload, S.ml_worker_status_payload, S.WORKERS_ENABLED, S.SAUDE_BOOT_EPOCH)
        S.ml_status_payload = lambda: {"configured": True, "connected": True, "needs_reconnect": False,
                                       "token_seconds_remaining": 9000, "token_expired": False,
                                       "nickname": "NOROMAK", "seller_id": "1", "last_error": ""}
        S.WORKERS_ENABLED = True
        S.SAUDE_BOOT_EPOCH = time.time() - 3600

    def tearDown(self) -> None:
        S.ml_status_payload, S.ml_worker_status_payload, S.WORKERS_ENABLED, S.SAUDE_BOOT_EPOCH = self._orig
        super().tearDown()

    def _nivel(self, minutos: float) -> str:
        quando = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()
        S.ml_worker_status_payload = lambda: {"last_order_poll_at": quando}
        item = [i for i in S._saude_ml() if i.get("id") == "ml_pedidos" or "pedidos do ML" in str(i)][0]
        return item["nivel"]

    def test_busca_de_6_minutos_nao_e_atraso(self):
        self.assertEqual(self._nivel(6), "ok", "o painel acusaria atraso a cada volta normal de 5 min")

    def test_busca_parada_continua_critica(self):
        self.assertEqual(self._nivel(25), "critico")


if __name__ == "__main__":
    unittest.main()
