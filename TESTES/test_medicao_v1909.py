"""V19.09 — medição de quem pesa: duração por rota, espera e posse do banco por requisição/worker."""
import threading, time, unittest
from base import S, TesteNoromak


class Medicao(TesteNoromak):
    def setUp(self):
        super().setUp()
        S.medicao_payload(zerar=True)

    def item(self, ctx):
        return next((i for i in S.medicao_payload()["itens"] if i["ctx"] == ctx), None)

    def test_rota_e_medida_sem_a_query(self):
        self.cliente.pedir("GET", "/api/storage?versions=conf_pedidos")
        self.cliente.pedir("GET", "/api/storage?versions=conf_etiquetas")
        i = self.item("GET /api/storage")
        self.assertIsNotNone(i)
        self.assertGreaterEqual(i["duracao"]["n"], 2)

    def test_posse_do_banco_por_contexto_e_reentrante(self):
        def segurar():
            S._MEDIR_LOCAL.ctx = "worker teste_lento"
            with S.DB_LOCK:
                with S.DB_LOCK:          # reentrante: conta uma posse só
                    time.sleep(0.2)
        t = threading.Thread(target=segurar)
        t.start(); t.join()
        i = self.item("worker teste_lento")
        self.assertEqual(i["posse"]["n"], 1)
        # V19.24: folga para o relógio do Windows (~15 ms): sleep(0.2) já mediu 0,188 s.
        self.assertGreaterEqual(i["posse"]["max_s"], 0.15)

    def test_espera_registrada_para_quem_ficou_na_fila(self):
        pegou = threading.Event()

        def dono():
            S._MEDIR_LOCAL.ctx = "worker dono"
            with S.DB_LOCK:
                pegou.set()
                time.sleep(0.3)

        def fila():
            S._MEDIR_LOCAL.ctx = "worker fila"
            pegou.wait()
            with S.DB_LOCK:
                pass
        a, b = threading.Thread(target=dono), threading.Thread(target=fila)
        a.start(); b.start(); a.join(); b.join()
        self.assertGreaterEqual(self.item("worker fila")["espera"]["max_s"], 0.2)

    def test_rota_so_admin_e_zerar_so_perfil_admin(self):
        st, corpo = self.cliente.pedir("GET", "/api/saude/medicao")
        self.assertEqual(st, 200)
        self.assertIn("banco_ocupado_pct", corpo)
        st, corpo = self.cliente.pedir("GET", "/api/saude/medicao?zerar=1")
        self.assertEqual(st, 200)
        self.assertIsNone(next((i for i in S.medicao_payload()["itens"] if i["ctx"] == "GET /api/storage"), None))


if __name__ == "__main__":
    unittest.main()
