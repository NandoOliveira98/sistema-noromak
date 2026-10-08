"""V18.78 — o servidor refazia o mesmo trabalho o dia inteiro (medido em produção, 08/10/2026).

Na VPS de 1 GB dividida com o CRM, a Expedição consumia ~40% de um núcleo 24 h por dia
e o ml_event_log tinha 2,85 milhões de linhas (0,8 GB) em 43 dias. Causas:

- o vigia de pedidos novos (20 s) reimportava CADA pedido Full da primeira página a
  cada ciclo — 16 pedidos × ~383 registros "FULL IGNORADO" em 12 h;
- a reconciliação de pedidos manuais (60 s) reimportava pedidos já vinculados pela API
  enquanto a etiqueta não saía — 71 pedidos × ~168 registros em 12 h;
- cada registro era um INSERT + commit sob o DB_LOCK, disputando com a bipagem;
- no primeiro deploy do dia o boot fazia um segundo backup completo logo depois do
  backup pré-deploy do publicar.sh.
"""

import gzip
import os
import time
import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha, reiniciar_banco


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE",
                 VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre", **extra)


def registros_log(topic: str, result: str) -> int:
    with S.DB_LOCK:
        return int(S.DB.execute(
            "SELECT COUNT(*) FROM ml_event_log WHERE topic=? AND result=?", (topic, result)
        ).fetchone()[0])


class FullNaoReimporta(TesteNoromak):
    """Pedido Full é classificado uma vez; o vigia e o catch-up não voltam a ele."""

    def setUp(self) -> None:
        super().setUp()
        self._originais = (S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment, S.ml_import_order)
        S.ml_access_token = lambda *a, **k: ("token", "SELLER")
        S._ML_LOG_ULTIMO.clear()
        self.importacoes: list[str] = []
        original_import = self._originais[3]

        def contar_import(order_id, **kw):
            self.importacoes.append(str(order_id))
            return original_import(order_id, **kw)
        S.ml_import_order = contar_import

    def tearDown(self) -> None:
        S.ml_api_get, S.ml_access_token, S._ml_resolve_forward_shipment, S.ml_import_order = self._originais
        super().tearDown()

    def _ml_falso(self, pedidos_api: list[str], full: set[str]):
        def api(path, *a, **k):
            if path.startswith("/orders/search"):
                return {"results": [{"id": oid, "date_created": S.now_iso(), "status": "paid"} for oid in pedidos_api]}
            oid = path.rstrip("/").split("/")[-1].split("?")[0]
            return {"id": oid, "status": "paid", "seller": {"id": "SELLER"},
                    "buyer": {"billing_info": {"id": "B1"}}}
        S.ml_api_get = api

        def shipment(order_id, order=None, *a, **k):
            oid = str(order_id)
            return ("SHIP-" + oid, {"logistic_type": "fulfillment" if oid in full else "drop_off"})
        S._ml_resolve_forward_shipment = shipment

    def test_import_registra_full_na_memoria(self):
        self._ml_falso(["F1"], full={"F1"})
        resultado = S.ml_import_order("F1", source="teste")
        self.assertTrue(resultado.get("ignored"))
        self.assertEqual(resultado.get("reason"), "FULL")
        self.assertIn("F1", S._ml_full_ignorados(), "o pedido Full não ficou na memória")

    def test_memoria_expira(self):
        self._ml_falso(["F1"], full={"F1"})
        S.ml_import_order("F1", source="teste")
        self.assertNotIn("F1", S._ml_full_ignorados(dias=-1),
                         "com corte no futuro a memória tem que estar vazia — a data de corte não vale")

    def test_vigia_importa_full_uma_unica_vez(self):
        self._ml_falso(["F1"], full={"F1"})
        r1 = S.ml_new_order_watch_once()
        self.assertEqual(r1.get("ignored_full"), 1)
        r2 = S.ml_new_order_watch_once()
        r3 = S.ml_new_order_watch_once()
        self.assertEqual((r2.get("ignored_full"), r3.get("ignored_full")), (0, 0),
                         "REGRESSÃO: o vigia voltou a reimportar o pedido Full a cada ciclo")
        self.assertEqual(self.importacoes.count("F1"), 1,
                         f"ml_import_order foi chamado {self.importacoes.count('F1')}x para o mesmo Full")
        self.assertEqual(registros_log("new_order_watcher", "FULL IGNORADO"), 1,
                         "o log ganhou uma linha repetida para o mesmo pedido Full")

    def test_vigia_continua_importando_pedido_normal(self):
        # Garante que a memória de Full não engoliu os pedidos que vão para a expedição.
        self._ml_falso(["F1", "N1"], full={"F1"})
        S.ml_new_order_watch_once()
        self.assertIn("N1", self.importacoes, "o pedido normal deixou de ser importado")
        self.assertNotIn("N1", S._ml_full_ignorados(), "pedido normal entrou na memória de Full")
        S.ml_new_order_watch_once()
        self.assertEqual(self.importacoes.count("N1"), 2, "pedido normal sem etiqueta continua sendo acompanhado")

    def test_catchup_pula_full_conhecido(self):
        self._ml_falso(["F1"], full={"F1"})
        S.ml_import_order("F1", source="teste")
        self.importacoes.clear()
        S.ml_safety_catchup_once()
        self.assertNotIn("F1", self.importacoes, "o catch-up reimportou um Full já conhecido")


class ReconciliacaoSemRepeticao(TesteNoromak):
    """Linha já reconhecida pela API (venda + envio) sai da reconciliação de 60 s."""

    def test_linha_ja_vinculada_nao_e_reconciliada(self):
        gravar_como_integracao([
            # já passou pela API: tem venda E envio — quem cuida é o vigia de etiquetas
            linha_ml("2000000000001", "SKU-A", ShipmentIdMercadoLivre="4400000000001"),
            # veio da extensão/print: só o número da venda, ainda sem envio — reconcilia
            linha("2000000000002", "SKU-B", uid="m2", Marketplace="MERCADO LIVRE"),
        ], origem="API Mercado Livre")
        numeros = S._ml_manual_pending_numbers()
        self.assertNotIn("2000000000001", numeros,
                         "REGRESSÃO: pedido já vinculado continuou sendo reconciliado a cada 60 s")
        self.assertIn("2000000000002", numeros, "o pedido manual sem envio saiu da reconciliação")

    def test_linha_vinculada_continua_no_vigia_de_etiquetas(self):
        gravar_como_integracao([
            linha_ml("2000000000001", "SKU-A", ShipmentIdMercadoLivre="4400000000001"),
        ], origem="API Mercado Livre")
        self.assertIn(("2000000000001", "4400000000001"), S._ml_all_pending_label_candidates(),
                      "a linha saiu da reconciliação mas ninguém mais acompanha a etiqueta dela")


class LogSoQuandoMuda(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._ML_LOG_ULTIMO.clear()

    def test_mesmo_resultado_nao_repete(self):
        for _ in range(5):
            S.ml_log_event_se_mudou("teste", "/orders/1", "", "", "FULL IGNORADO", "{}")
        self.assertEqual(registros_log("teste", "FULL IGNORADO"), 1)

    def test_resultado_diferente_registra(self):
        S.ml_log_event_se_mudou("teste", "/orders/1", "", "", "NF AGUARDANDO", "{}")
        S.ml_log_event_se_mudou("teste", "/orders/1", "", "", "PRONTO PARA CONFERÊNCIA", "{}")
        S.ml_log_event_se_mudou("teste", "/orders/1", "", "", "PRONTO PARA CONFERÊNCIA", '{"x":1}')
        with S.DB_LOCK:
            total = S.DB.execute("SELECT COUNT(*) FROM ml_event_log WHERE topic='teste'").fetchone()[0]
        self.assertEqual(total, 3, "mudança de resultado ou de detalhe tem que ir para o log")

    def test_recursos_diferentes_nao_se_misturam(self):
        S.ml_log_event_se_mudou("teste", "/orders/1", "", "", "FULL IGNORADO", "{}")
        S.ml_log_event_se_mudou("teste", "/orders/2", "", "", "FULL IGNORADO", "{}")
        self.assertEqual(registros_log("teste", "FULL IGNORADO"), 2)

    def test_retencao_do_log_e_curta(self):
        self.assertLessEqual(S.LOG_EVENTOS_DIAS, 30,
                             "ml_event_log é diagnóstico: 2,85 milhões de linhas em 43 dias com 90")


class BackupDoBootNaoDuplicaPreDeploy(unittest.TestCase):

    def _limpar(self):
        reiniciar_banco()
        with S.BACKUP_LOCK:
            pass
        S.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        for p in S.BACKUP_DIR.glob("expedicao_*"):
            p.unlink(missing_ok=True)
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT OR IGNORE INTO storage(key,value,version,updated_at) VALUES('teste_backup','[]',1,?)",
                (S.now_iso(),),
            )
            S.DB.commit()

    def _predeploy(self, idade_segundos: float):
        f = S.BACKUP_DIR / "expedicao_predeploy_20261008_112500.db.gz"
        with gzip.open(f, "wb") as g:
            g.write(b"copia do publicar.sh")
        t = time.time() - idade_segundos
        os.utime(f, (t, t))
        return f

    def test_predeploy_recente_adia_o_diario(self):
        self._limpar()
        self._predeploy(idade_segundos=60)
        S.backup_daily()
        self.assertFalse(S._backup_alvo_hoje().exists(),
                         "REGRESSÃO: o boot fez um segundo backup completo logo depois do pré-deploy")

    def test_predeploy_antigo_nao_impede_o_diario(self):
        self._limpar()
        self._predeploy(idade_segundos=3 * 3600)
        S.backup_daily()
        self.assertTrue(S._backup_alvo_hoje().exists(), "pré-deploy de 3 h atrás não pode segurar o diário")

    def test_sem_predeploy_faz_o_diario(self):
        self._limpar()
        S.backup_daily()
        self.assertTrue(S._backup_alvo_hoje().exists())


if __name__ == "__main__":
    unittest.main()
