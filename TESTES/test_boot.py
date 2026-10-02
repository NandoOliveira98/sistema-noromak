"""V18.63: o servidor precisa SUBIR de verdade antes de qualquer publicação.

Os outros testes importam o módulo e montam o ThreadingHTTPServer na mão — nunca
passam pelo main(). Um erro no main() (worker novo com nome errado, reparo de boot
que explode, porta que não abre) só aparecia em produção, depois do restart, com a
operação já parada. Aqui o servidor_web.py roda como processo próprio, igual ao
systemd, em um banco temporário e com os workers desligados (EXP_WORKERS=0: nenhuma
chamada para Mercado Livre, Shopee, Apollo ou Linx), e o /api/health precisa
responder com a versão deste código.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _versao_do_codigo() -> str:
    for linha in (RAIZ / "servidor_web.py").read_text(encoding="utf-8").splitlines():
        if linha.startswith("APP_VERSION = "):
            return linha.split("=", 1)[1].strip().strip('"')
    raise AssertionError("APP_VERSION não encontrado em servidor_web.py")


class BootRealTest(unittest.TestCase):
    def test_servidor_sobe_pelo_main_e_responde_health_com_a_versao(self):
        tmp = Path(tempfile.mkdtemp(prefix="noromak_boot_"))
        porta = _porta_livre()
        env = dict(os.environ)
        env.update(
            {
                "EXP_HOST": "127.0.0.1",
                "EXP_PORT": str(porta),
                "EXP_DB_PATH": str(tmp / "expedicao.db"),
                "EXP_BACKUP_DIR": str(tmp / "backups"),
                "EXP_MIGRATION_DIR": str(tmp / "sem_migracao"),
                "EXP_ADMIN_USUARIO": "admin_boot",
                "EXP_ADMIN_SENHA": "Senha-De-Boot-123",
                "EXP_USUARIO_SENHA_INICIAL": "Senha-De-Boot-123",
                "EXP_WORKERS": "0",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        proc = subprocess.Popen(
            [sys.executable, str(RAIZ / "servidor_web.py")],
            cwd=str(RAIZ),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{porta}/expedicao/api/health"
        dados = None
        morreu = False
        try:
            limite = time.time() + 60
            while time.time() < limite:
                if proc.poll() is not None:
                    morreu = True
                    break
                try:
                    with urllib.request.urlopen(url, timeout=2) as resp:
                        dados = json.loads(resp.read().decode("utf-8"))
                    break
                except Exception:
                    time.sleep(0.3)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                saida, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                saida, _ = proc.communicate()
        texto = (saida or b"").decode("utf-8", "replace")
        self.assertFalse(morreu, f"o servidor morreu no boot (código {proc.returncode}):\n{texto[-4000:]}")
        self.assertIsNotNone(dados, f"o /api/health não respondeu em 60 s:\n{texto[-4000:]}")
        self.assertTrue(dados.get("ok"))
        self.assertEqual(dados.get("version"), _versao_do_codigo())
        self.assertIs(dados.get("workers"), False, "EXP_WORKERS=0 precisa desligar os workers")
        self.assertIn("workers de integração DESLIGADOS", texto)
        self.assertNotIn("Traceback", texto, texto[-4000:])

    def test_versao_txt_igual_ao_app_version(self):
        # publicar.sh confere o /api/health contra o APP_VERSION; o VERSAO.txt
        # esquecido para trás já confundiu quem olhava a pasta.
        versao_txt = (RAIZ / "VERSAO.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(versao_txt, _versao_do_codigo())


if __name__ == "__main__":
    unittest.main()
