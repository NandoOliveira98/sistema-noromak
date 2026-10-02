"""V18.11 — backup diário comprimido, verificado e com retenção curta.

Antes: `backup_daily` gravava ~1 GB sem compressão e mantinha 15 diários (até
15 GB), enchendo o disco de 45 GB da VPS; e segurava o DB_LOCK durante a cópia.
Agora: comprime (gzip, ~75 MB), verifica que abre e tem dados, mantém só os
BACKUP_DAILY_KEEP mais recentes e apaga os diários antigos não comprimidos.
"""

import gzip
import os
import time
import unittest

from base import S, reiniciar_banco


def setUpModule():
    reiniciar_banco()


class BackupDiarioComprimido(unittest.TestCase):
    def _limpar(self):
        with S.BACKUP_LOCK:  # V18.56: espera um backup em segundo plano de outro teste
            pass
        S.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        for p in S.BACKUP_DIR.glob("expedicao_*"):
            p.unlink(missing_ok=True)

    def _garantir_storage(self):
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT OR IGNORE INTO storage(key,value,version,updated_at) VALUES('teste_backup','[]',1,?)",
                (S.now_iso(),),
            )
            S.DB.commit()

    def test_cria_comprimido_mantem_3_e_limpa_crus(self):
        self._limpar()
        self._garantir_storage()
        # Diários comprimidos antigos (datas passadas) + diários crus (formato antigo).
        antigo = time.time() - 86400 * 10
        for d in ("2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"):
            f = S.BACKUP_DIR / f"expedicao_{d}.db.gz"
            with gzip.open(f, "wb") as g:
                g.write(b"velho")
            os.utime(f, (antigo, antigo))
        for d in ("2026-08-10", "2026-08-11"):
            (S.BACKUP_DIR / f"expedicao_{d}.db").write_bytes(b"cru de 1GB no mundo real")

        S.backup_daily()

        hoje = S.BACKUP_DIR / ("expedicao_" + S.datetime.now().strftime("%Y-%m-%d") + ".db.gz")
        self.assertTrue(hoje.exists(), "o diário comprimido de hoje deveria ter sido criado")

        # Abre o gzip e confirma que é um SQLite com dados.
        import sqlite3, tempfile
        with gzip.open(hoje, "rb") as g:
            bruto = g.read()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.write(bruto); tmp.close()
        conf = sqlite3.connect(tmp.name)
        try:
            self.assertGreater(conf.execute("SELECT COUNT(*) FROM storage").fetchone()[0], 0)
        finally:
            conf.close()
            os.unlink(tmp.name)

        gz = list(S.BACKUP_DIR.glob("expedicao_????-??-??.db.gz"))
        self.assertLessEqual(len(gz), S.BACKUP_DAILY_KEEP, f"deveria manter {S.BACKUP_DAILY_KEEP}: {gz}")
        self.assertIn(hoje, gz)
        self.assertEqual(list(S.BACKUP_DIR.glob("expedicao_????-??-??.db")), [], "diários crus deveriam sumir")
        self.assertEqual(list(S.BACKUP_DIR.glob("expedicao_*.db.tmp")), [], "não pode sobrar arquivo temporário")

    def test_nao_recria_no_mesmo_dia(self):
        self._limpar()
        self._garantir_storage()
        S.backup_daily()
        hoje = S.BACKUP_DIR / ("expedicao_" + S.datetime.now().strftime("%Y-%m-%d") + ".db.gz")
        m1 = hoje.stat().st_mtime
        time.sleep(0.05)
        S.backup_daily()  # mesma data: sai cedo, não regrava
        self.assertEqual(hoje.stat().st_mtime, m1)


if __name__ == "__main__":
    unittest.main()
