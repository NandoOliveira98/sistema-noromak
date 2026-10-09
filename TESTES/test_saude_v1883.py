"""V18.83 — Painel de saúde e alertas por e-mail.

O problema só aparecia quando a operação já tinha parado (conexão do ML caída, worker parado,
backup que não rodou). O alerta precisa: (1) não disparar por um soluço de reinício; (2) não
repetir a cada minuto; (3) lembrar se continuar; (4) avisar quando resolve; (5) não perder o
aviso se o SMTP falhar — e não martelar o SMTP quebrado.
"""

import os
import re
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from base import RAIZ, S, TesteNoromak

SENHA = "Senha-de-teste-1883"


def _critico(id_="ml_conexao", titulo="Mercado Livre"):
    return S._saude_item(id_, "Operação", titulo, "critico", "Desconectado")


class _Correio:
    def __init__(self, falhar=False):
        self.enviados = []
        self.falhar = falhar

    def __call__(self, assunto, texto, html, destinatarios):
        if self.falhar:
            raise RuntimeError("SMTP fora do ar")
        self.enviados.append((assunto, texto, list(destinatarios)))
        return list(destinatarios)


class AlertasDeSaude(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self._checks_orig = S.saude_checks
        self._boot_orig = S.SAUDE_BOOT_EPOCH
        S.SAUDE_BOOT_EPOCH = time.time() - 3600  # fora da carência do boot
        S._SAUDE_ENVIO["falhou_ate"] = 0.0
        self.situacao = [_critico()]
        S.saude_checks = lambda: [dict(c) for c in self.situacao]
        S.saude_config_salvar({"emails": ["dono@example.com"], "ativo": True}, "teste")

    def tearDown(self) -> None:
        S.saude_checks = self._checks_orig
        S.SAUDE_BOOT_EPOCH = self._boot_orig
        S._SAUDE_ENVIO["falhou_ate"] = 0.0
        super().tearDown()

    def rodadas(self, n, correio):
        for _ in range(n):
            S.saude_alertas_rodada(enviar=correio)

    def test_so_avisa_depois_de_rodadas_seguidas_e_uma_vez(self):
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS - 1, correio)
        self.assertEqual(correio.enviados, [], "avisou antes de confirmar o problema (soluço de reinício)")
        self.rodadas(1, correio)
        self.assertEqual(len(correio.enviados), 1)
        self.assertIn("ALERTA", correio.enviados[0][0])
        self.assertIn("Mercado Livre", correio.enviados[0][0])
        self.rodadas(5, correio)
        self.assertEqual(len(correio.enviados), 1, "o mesmo problema foi avisado de novo a cada minuto")

    def test_lembra_se_continuar_e_avisa_quando_resolve(self):
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS, correio)
        velho = (datetime.now(timezone.utc) - timedelta(hours=7)).astimezone().isoformat()
        with S.DB_LOCK:
            S.DB.execute("UPDATE saude_alerta_estado_v1883 SET ultimo_aviso_em=?", (velho,))
            S.DB.commit()
        self.rodadas(1, correio)
        self.assertEqual(len(correio.enviados), 2, "não lembrou do problema que continua há mais de 6 h")
        self.assertIn("Continuam sem solução", correio.enviados[1][1].replace("CONTINUAM SEM SOLUÇÃO", "Continuam sem solução"))
        self.situacao = [S._saude_item("ml_conexao", "Operação", "Mercado Livre", "ok", "Conectado")]
        self.rodadas(1, correio)
        self.assertEqual(len(correio.enviados), 3)
        self.assertIn("RESOLVIDO", correio.enviados[2][0])
        with S.DB_LOCK:
            self.assertEqual(S.DB.execute("SELECT COUNT(*) FROM saude_alerta_estado_v1883").fetchone()[0], 0)

    def test_problema_que_some_antes_de_confirmar_nao_gera_email(self):
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS - 1, correio)
        self.situacao = [S._saude_item("ml_conexao", "Operação", "Mercado Livre", "ok", "Conectado")]
        self.rodadas(3, correio)
        self.assertEqual(correio.enviados, [], "avisou 'resolvido' de algo que nunca foi avisado")

    def test_carencia_do_boot_segura_o_alerta(self):
        S.SAUDE_BOOT_EPOCH = time.time()
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS + 2, correio)
        self.assertEqual(correio.enviados, [], "alertou logo depois de subir, com as rotinas ainda começando")

    def test_falha_no_smtp_nao_perde_o_aviso_e_nao_martela(self):
        quebrado = _Correio(falhar=True)
        self.rodadas(S.SAUDE_ALERTA_RODADAS, quebrado)
        with S.DB_LOCK:
            falhas = S.DB.execute("SELECT COUNT(*) FROM saude_alerta_log_v1883 WHERE tipo='falha_envio'").fetchone()[0]
            alertado = S.DB.execute("SELECT alertado_em FROM saude_alerta_estado_v1883").fetchone()[0]
        self.assertEqual(falhas, 1)
        self.assertEqual(alertado, "", "marcou como avisado sem o e-mail ter saído")
        self.rodadas(3, quebrado)
        with S.DB_LOCK:
            falhas = S.DB.execute("SELECT COUNT(*) FROM saude_alerta_log_v1883 WHERE tipo='falha_envio'").fetchone()[0]
        self.assertEqual(falhas, 1, "tentou o SMTP quebrado de novo a cada minuto")
        S._SAUDE_ENVIO["falhou_ate"] = 0.0
        bom = _Correio()
        self.rodadas(1, bom)
        self.assertEqual(len(bom.enviados), 1, "o aviso que falhou não foi reenviado quando o SMTP voltou")

    def test_alertas_desligados_ficam_so_no_historico(self):
        S.saude_config_salvar({"ativo": False}, "teste")
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS + 1, correio)
        self.assertEqual(correio.enviados, [])
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM saude_alerta_log_v1883 WHERE tipo='alerta'").fetchone()[0]
        self.assertEqual(n, 1, "com o e-mail desligado o alerta tem que ficar registrado uma vez no painel")

    def test_atencao_so_avisa_quando_pedido(self):
        self.situacao = [S._saude_item("backup", "Servidor", "Backup diário", "atencao", "Atrasado")]
        correio = _Correio()
        self.rodadas(S.SAUDE_ALERTA_RODADAS + 1, correio)
        self.assertEqual(correio.enviados, [])
        S.saude_config_salvar({"avisar_atencao": True}, "teste")
        self.rodadas(S.SAUDE_ALERTA_RODADAS, correio)
        self.assertEqual(len(correio.enviados), 1)

    def test_email_invalido_recusado(self):
        with self.assertRaises(ValueError):
            S.saude_config_salvar({"emails": "dono@example.com, isso-nao-e-email"}, "teste")


class PulsoDosWorkers(unittest.TestCase):

    def setUp(self) -> None:
        with S.SAUDE_WORKERS_LOCK:
            self._antes = dict(S.SAUDE_WORKERS)
            S.SAUDE_WORKERS.clear()
        self._boot = S.SAUDE_BOOT_EPOCH
        self._ligados = S.WORKERS_ENABLED
        S.WORKERS_ENABLED = True

    def tearDown(self) -> None:
        with S.SAUDE_WORKERS_LOCK:
            S.SAUDE_WORKERS.clear()
            S.SAUDE_WORKERS.update(self._antes)
        S.SAUDE_BOOT_EPOCH = self._boot
        S.WORKERS_ENABLED = self._ligados

    def situacao(self, nome):
        return next(w for w in S.saude_workers() if w["id"] == nome)

    def test_worker_esperando_aparece_como_aguardando(self):
        parar = threading.Event()

        def ml_new_order_watch_loop(stop_event):
            while not stop_event.wait(30):
                pass

        t = threading.Thread(target=S._worker_protegido(ml_new_order_watch_loop), args=(parar,), daemon=True)
        t.start()
        time.sleep(0.3)
        w = self.situacao("ml_new_order_watch_loop")
        parar.set()
        t.join(3)
        self.assertEqual(w["nivel"], "ok")
        self.assertIn("Aguardando", w["situacao"])

    def test_worker_essencial_travado_vira_critico(self):
        with S.SAUDE_WORKERS_LOCK:
            S.SAUDE_WORKERS["ml_pending_label_watch_loop"] = {
                "iniciado": time.time() - 7200, "esperando": False, "espera_inicio": 0.0, "espera_s": 0.0,
                "acordou": time.time() - 40 * 60, "ciclos": 10, "erros": 0, "ultimo_erro": "", "ultimo_erro_em": 0.0}
        w = self.situacao("ml_pending_label_watch_loop")
        self.assertEqual(w["nivel"], "critico")
        self.assertIn("travado", w["situacao"])

    def test_worker_que_nao_subiu_so_acusa_depois_da_carencia(self):
        S.SAUDE_BOOT_EPOCH = time.time()
        self.assertEqual(self.situacao("shopee_orders_worker_loop")["nivel"], "ok")
        S.SAUDE_BOOT_EPOCH = time.time() - 3600
        self.assertEqual(self.situacao("shopee_orders_worker_loop")["nivel"], "critico")
        self.assertEqual(self.situacao("ml_visitas_loop")["nivel"], "atencao", "worker não essencial parado não é crítico")

    def test_erro_que_reinicia_o_worker_fica_visivel(self):
        parar = threading.Event()
        chamadas = []

        def ml_estoque_loop(stop_event):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise RuntimeError("explodiu")
            stop_event.wait(30)

        orig = S.WORKER_REINICIO_SEGUNDOS
        S.WORKER_REINICIO_SEGUNDOS = 0.05
        try:
            t = threading.Thread(target=S._worker_protegido(ml_estoque_loop), args=(parar,), daemon=True)
            t.start()
            time.sleep(0.4)
            w = self.situacao("ml_estoque_loop")
        finally:
            parar.set()
            S.WORKER_REINICIO_SEGUNDOS = orig
        self.assertEqual(w["erros"], 1)
        self.assertIn("explodiu", w["ultimo_erro"])
        self.assertEqual(w["nivel"], "atencao")

    def test_todo_worker_do_boot_esta_no_painel(self):
        """Worker novo no main() sem entrada em SAUDE_WORKERS_INFO ficaria invisível no painel."""
        fonte = (RAIZ / "servidor_web.py").read_text(encoding="utf-8")
        no_boot = set(re.findall(r"target=_worker_protegido\((\w+)\)", fonte))
        self.assertTrue(no_boot)
        self.assertEqual(no_boot, set(S.SAUDE_WORKERS_INFO), "lista de workers do painel diferente da do boot")

    def test_espera_da_shopee_conta_no_pulso(self):
        fonte = (RAIZ / "servidor_web.py").read_text(encoding="utf-8")
        self.assertIn("_saude_esperar(SHOPEE_LABEL_WAKE_EVENT", fonte,
                      "a Shopee espera por outro evento: sem marcar, apareceria travada")


class ChecagensEPermissoes(TesteNoromak):

    def test_backup_atrasado_e_em_dia(self):
        S.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        for velho in S.BACKUP_DIR.glob("expedicao_*.db.gz"):
            velho.unlink()
        arq = S.BACKUP_DIR / "expedicao_2026-01-01.db.gz"
        arq.write_bytes(b"x")
        antigo = time.time() - 40 * 3600
        os.utime(arq, (antigo, antigo))
        self.assertEqual(S._saude_backup()["nivel"], "critico")
        os.utime(arq, None)
        self.assertEqual(S._saude_backup()["nivel"], "ok")
        arq.unlink()

    def test_fila_da_zebra_parada(self):
        criado = (datetime.now(timezone.utc) - timedelta(minutes=40)).astimezone().isoformat(timespec="milliseconds")
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO print_jobs(id, order_key, order_id, payload, status, created_at, created_by, updated_at) "
                "VALUES('j1','k','1','{}','queued',?,'teste',?)", (criado, criado))
            S.DB.commit()
        fila = next(i for i in S._saude_impressao() if i["id"] == "impressao_fila")
        self.assertEqual(fila["nivel"], "critico")

    def test_payload_responde_para_admin(self):
        status, corpo = self.cliente.pedir("GET", "/api/saude")
        self.assertEqual(status, 200, corpo)
        self.assertIn(corpo["geral"], ("ok", "atencao", "critico", "info"))
        self.assertTrue(corpo["checks"])
        self.assertEqual(len(corpo["workers"]), len(S.SAUDE_WORKERS_INFO))

    def test_supervisor_ve_mas_nao_muda_quem_recebe(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_1883", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True, "estacao": False, "precificador": False})
            S.DB.commit()
        from base import Cliente
        sup = Cliente(self.url_base)
        sup.entrar("sup_1883", SENHA)
        self.assertEqual(sup.pedir("GET", "/api/saude")[0], 200)
        status, _ = sup.pedir("POST", "/api/saude/config", {"emails": ["x@example.com"]})
        self.assertEqual(status, 403, "Supervisor conseguiu mudar quem recebe os alertas")
        status, corpo = self.cliente.pedir("POST", "/api/saude/config", {"emails": ["dono@example.com"]})
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["config"]["emails"], ["dono@example.com"])

    def test_operador_sem_admin_nao_ve(self):
        with S.DB_LOCK:
            S.upsert_app_user("op_1883", "Operador", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.DB.commit()
        from base import Cliente
        op = Cliente(self.url_base)
        op.entrar("op_1883", SENHA, area="conferencia")
        self.assertEqual(op.pedir("GET", "/api/saude")[0], 403)


if __name__ == "__main__":
    unittest.main()
