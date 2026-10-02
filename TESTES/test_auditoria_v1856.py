"""V18.56 — correções da auditoria de integridade de 02/10/2026.

Cada classe reproduz uma falha encontrada na auditoria:

- token do ML renovado por duas threads ao mesmo tempo (chave de uso único);
- backup diário correndo consigo mesmo, segurando o DB_LOCK e deixando .gz cortado;
- reinclusão ADMIN gravando/commitando sem o DB_LOCK (conexão compartilhada);
- leitura que pulava o filtro de concluídos quando uma etapa anterior falhava;
- vínculo MLB↔SKU manual apagado pela sincronização de Promoções;
- .xlsx de Ads com coluna absurda travando o servidor;
- workers que morriam calados; logs e caches que cresciam para sempre.
"""

import gzip
import io
import json
import os
import threading
import time
import unittest
import zipfile

from base import S


def setUpModule():
    S.init_db()


class MlTokenRenovacaoSerializada(unittest.TestCase):
    def setUp(self):
        self.orig_http = S.ml_http_json
        self.orig_cred = S.ml_credentials_configured
        self.chamadas = []
        self.trava_chamadas = threading.Lock()

        def falso_http(method, url, **kwargs):
            with self.trava_chamadas:
                self.chamadas.append(url)
                n = len(self.chamadas)
            time.sleep(0.2)  # a rede demora: é aqui que a corrida acontecia
            return {"user_id": "123", "access_token": f"ACC{n}", "refresh_token": f"REF{n}",
                    "expires_in": 21600, "scope": "offline_access read"}

        S.ml_http_json = falso_http
        S.ml_credentials_configured = lambda: True
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM ml_integration")
            S.DB.execute(
                """INSERT INTO ml_integration(id,seller_id,nickname,access_token,refresh_token,
                     token_type,scope,expires_at_epoch,connected_at,updated_at,last_sync_at,last_error)
                   VALUES(1,'123','loja','ACC0','REF0','Bearer','offline_access read',?,?,?,'','')""",
                (int(time.time()) + 100, S.now_iso(), S.now_iso()),  # dentro da janela de 300 s
            )
            S.DB.commit()

    def tearDown(self):
        S.ml_http_json = self.orig_http
        S.ml_credentials_configured = self.orig_cred

    def _concorrentes(self, **kwargs):
        barreira = threading.Barrier(5)
        tokens = []

        def pedir():
            barreira.wait()
            tokens.append(S.ml_access_token(**kwargs)[0])

        threads = [threading.Thread(target=pedir) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        return tokens

    def test_cinco_threads_renovam_uma_vez(self):
        tokens = self._concorrentes()
        self.assertEqual(len(self.chamadas), 1, f"a chave de uso único foi gasta {len(self.chamadas)} vezes")
        self.assertEqual(set(tokens), {"ACC1"})
        self.assertEqual(S.ml_integration_row()["refresh_token"], "REF1")

    def test_force_refresh_concorrente_reaproveita(self):
        # Vários 401 ao mesmo tempo: um renova, os outros pegam o token novo.
        tokens = self._concorrentes(force_refresh=True)
        self.assertEqual(len(self.chamadas), 1)
        self.assertEqual(set(tokens), {"ACC1"})

    def test_token_valido_nao_renova(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE ml_integration SET expires_at_epoch=?", (int(time.time()) + 5000,))
            S.DB.commit()
        self.assertEqual(S.ml_access_token()[0], "ACC0")
        self.assertEqual(self.chamadas, [])


class BackupDiarioSeguro(unittest.TestCase):
    def setUp(self):
        with S.BACKUP_LOCK:  # espera qualquer backup em segundo plano de outro teste
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
        self.hoje = S._backup_alvo_hoje()

    def test_execucoes_simultaneas_geram_um_backup_integro(self):
        threads = [threading.Thread(target=S.backup_daily) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertTrue(self.hoje.exists())
        with gzip.open(self.hoje, "rb") as g:
            self.assertTrue(g.read(16).startswith(b"SQLite format 3"))
        sobras = list(S.BACKUP_DIR.glob("*.tmp"))
        self.assertEqual(sobras, [], f"sobrou temporário: {sobras}")

    def test_backup_nao_precisa_do_db_lock(self):
        # Antes a cópia rodava sob o DB_LOCK: toda bipagem esperava o backup.
        t = threading.Thread(target=S.backup_daily)
        with S.DB_LOCK:
            t.start()
            t.join(30)
            self.assertFalse(t.is_alive(), "o backup ficou esperando o DB_LOCK")
        self.assertTrue(self.hoje.exists())

    def test_falha_na_compressao_nao_deixa_gz_final(self):
        original = S.gzip.open

        class Quebra:
            def __init__(self, *a, **k):
                self.f = original(*a, **k)

            def __enter__(self):
                return self

            def write(self, dados):
                self.f.write(dados[:10])
                raise OSError("disco cheio no meio da compressão")

            def __exit__(self, *a):
                self.f.close()
                return False

        S.gzip.open = Quebra
        try:
            S.backup_daily()
        finally:
            S.gzip.open = original
        self.assertFalse(self.hoje.exists(), "um .gz cortado contaria como o backup do dia")
        S.backup_daily()  # a próxima tentativa refaz e limpa o temporário
        self.assertTrue(self.hoje.exists())
        self.assertEqual(list(S.BACKUP_DIR.glob("*.tmp")), [])

    def test_requisicao_dispara_em_segundo_plano(self):
        inicio = time.monotonic()
        with S.DB_LOCK:  # mesmo com o lock preso a chamada volta na hora
            S.backup_daily_em_segundo_plano()
        self.assertLess(time.monotonic() - inicio, 1.0)
        limite = time.time() + 30
        while not self.hoje.exists() and time.time() < limite:
            time.sleep(0.05)
        with S.BACKUP_LOCK:
            pass
        self.assertTrue(self.hoje.exists())


class ReinclusaoAdminUsaDbLock(unittest.TestCase):
    def test_espera_o_db_lock(self):
        segura = threading.Event()
        solta = threading.Event()
        terminou = threading.Event()

        def dono_do_lock():
            with S.DB_LOCK:
                segura.set()
                solta.wait(5)

        def reincluir():
            S._pending_admin_restore_orders({"PEDIDO-X"})
            terminou.set()

        a = threading.Thread(target=dono_do_lock)
        a.start()
        segura.wait(5)
        b = threading.Thread(target=reincluir)
        b.start()
        time.sleep(0.3)
        self.assertFalse(terminou.is_set(), "a reinclusão gravou/commitou sem o DB_LOCK")
        solta.set()
        a.join(5)
        b.join(5)
        self.assertTrue(terminou.is_set())


class LeituraSempreFiltraConcluidos(unittest.TestCase):
    def test_falha_no_cofre_nao_pula_o_filtro_final(self):
        S.save_storage("conf_pedidos", None, json.dumps([{"Pedido": "P-1", "SKU": "A", "Quantidade": 1}]), True)
        orig_cofre = S._admin_manual_order_vault_apply
        orig_filtro = S.filter_completed_orders_v1772
        chamadas = []

        def cofre_quebrado(value):
            raise RuntimeError("cofre corrompido")

        def filtro(value):
            chamadas.append(True)
            return orig_filtro(value)

        S._admin_manual_order_vault_apply = cofre_quebrado
        S.filter_completed_orders_v1772 = filtro
        try:
            value, _, _ = S.get_storage("conf_pedidos")
        finally:
            S._admin_manual_order_vault_apply = orig_cofre
            S.filter_completed_orders_v1772 = orig_filtro
        self.assertEqual(chamadas, [True], "o filtro de concluídos foi pulado")
        self.assertIsNotNone(value)


class VinculoManualSobreviveSyncPromocoes(unittest.TestCase):
    def test_vinculo_incluido_durante_a_chamada_ao_ml_nao_some(self):
        orig_meta = S.ml_items_meta
        orig_idx = S._promo_precifier_index
        chave = S.ML_SKU_MLB_LINKS_STORAGE_KEY

        def meta_lenta(ids):
            # Enquanto o ML responde, o ADMIN inclui um vínculo à mão.
            with S.DB_LOCK:
                raw = S.DB.execute("SELECT value,version FROM storage WHERE key=?", (chave,)).fetchone()
                atual = json.loads(raw["value"]) if raw else {}
                atual.setdefault("SKU-MANUAL", []).append({"mlb": "MLB999", "sku": "SKU-MANUAL"})
                S.DB.execute(
                    """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version""",
                    (chave, json.dumps(atual), (int(raw["version"]) + 1) if raw else 1, S.now_iso()),
                )
                S.DB.commit()
            return {d: {"sku": "SKU-AUTO", "title": "Item", "price": 10} for d in ids}

        S.ml_items_meta = meta_lenta
        S._promo_precifier_index = lambda: {"SKU-AUTO": {}, "SKU-MANUAL": {}}
        try:
            r = S.ml_precificador_sync_promo_links(["MLB111"], actor="teste")
        finally:
            S.ml_items_meta = orig_meta
            S._promo_precifier_index = orig_idx
        self.assertEqual(r["linked"], 1)
        links = json.loads(S.get_storage(chave)[0] or "{}")
        mlbs = {row.get("mlb") for rows in links.values() for row in rows}
        self.assertIn("MLB111", mlbs)
        self.assertIn("MLB999", mlbs, "o vínculo manual incluído durante a sincronização foi apagado")


class XlsxAdsBlindado(unittest.TestCase):
    def _zip(self, folha_xml: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml",
                       '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       '<sheets><sheet name="A" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", folha_xml)
        return buf.getvalue()

    def test_coluna_absurda_recusada_rapido(self):
        xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
               '<row r="1"><c r="ZZZZZZZZZZ1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>')
        inicio = time.monotonic()
        with self.assertRaises(ValueError):
            S.ads_relatorio_ler_xlsx(self._zip(xml))
        self.assertLess(time.monotonic() - inicio, 2.0)

    def test_soma_descompactada_limitada(self):
        orig = S.ADS_XLSX_MAX_DESCOMPACTADO
        S.ADS_XLSX_MAX_DESCOMPACTADO = 1000
        try:
            xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                   + "<!--" + "x" * 5000 + "-->" + "</sheetData></worksheet>")
            with self.assertRaises(ValueError):
                S.ads_relatorio_ler_xlsx(self._zip(xml))
        finally:
            S.ADS_XLSX_MAX_DESCOMPACTADO = orig


class WorkerProtegido(unittest.TestCase):
    def test_reinicia_apos_erro_e_para_no_stop(self):
        orig = S.WORKER_REINICIO_SEGUNDOS
        S.WORKER_REINICIO_SEGUNDOS = 0.01
        execucoes = []

        def loop_instavel(stop):
            execucoes.append(1)
            if len(execucoes) < 3:
                raise RuntimeError("database is locked")

        try:
            stop = threading.Event()
            t = threading.Thread(target=S._worker_protegido(loop_instavel), args=(stop,))
            t.start()
            t.join(5)
        finally:
            S.WORKER_REINICIO_SEGUNDOS = orig
        self.assertFalse(t.is_alive())
        self.assertEqual(len(execucoes), 3, "o worker deveria ter sido reiniciado depois de cada erro")

    def test_stop_interrompe_reinicio(self):
        stop = threading.Event()
        stop.set()
        chamadas = []
        S._worker_protegido(lambda s: chamadas.append(1))(stop)
        self.assertEqual(chamadas, [])


class LimpezaLogsECaches(unittest.TestCase):
    def test_logs_antigos_sao_removidos_e_recentes_ficam(self):
        velho = "2020-01-01T00:00:00.000-03:00"
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM ml_event_log")
            S.DB.execute("DELETE FROM shopee_event_log")
            S.DB.execute("INSERT INTO ml_event_log(created_at,topic) VALUES(?, 'velho')", (velho,))
            S.DB.execute("INSERT INTO ml_event_log(created_at,topic) VALUES(?, 'novo')", (S.now_iso(),))
            S.DB.execute("INSERT INTO shopee_event_log(created_at,topic) VALUES(?, 'velho')", (velho,))
            S.DB.execute("INSERT INTO shopee_event_log(created_at,topic) VALUES(?, 'novo')", (S.now_iso(),))
            S.DB.commit()
        removidos = S.limpar_logs_antigos()
        self.assertEqual(removidos, {"ml_event_log": 1, "shopee_event_log": 1})
        with S.DB_LOCK:
            restantes = [r[0] for r in S.DB.execute("SELECT topic FROM ml_event_log")]
        self.assertEqual(restantes, ["novo"])

    def test_cache_podado(self):
        agora = time.time()
        cache = {f"v{i}": (agora - 10_000, {}) for i in range(100)}
        cache.update({f"n{i}": (agora, {}) for i in range(5)})
        S._cache_podar(cache, ttl=600, limite=50)
        self.assertEqual(set(cache), {f"n{i}" for i in range(5)})

    def test_cache_podado_pelo_limite_mesmo_sem_vencidos(self):
        agora = time.time()
        cache = {f"k{i}": (agora + i, {}) for i in range(100)}
        S._cache_podar(cache, ttl=600, limite=50)
        self.assertLessEqual(len(cache), 50)
        self.assertIn("k99", cache, "deveria manter os mais recentes")


if __name__ == "__main__":
    unittest.main()
