"""Infraestrutura dos testes automatizados do Sistema Web Noromak.

Prepara um ambiente TOTALMENTE ISOLADO antes de importar o servidor: banco SQLite
temporário, sem migração legada e sem o seed do Precificador. Nada aqui encosta no
banco de produção nem faz chamada de rede para Mercado Livre, Shopee ou Apollo.

As variáveis de ambiente precisam ser definidas ANTES do `import servidor_web`,
porque o módulo abre a conexão SQLite já no import (`DB = connect_db()`).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_TEMP = Path(tempfile.mkdtemp(prefix="noromak_testes_"))

USUARIO_ADMIN = "admin_teste"
SENHA_ADMIN = "Senha-De-Teste-123"

os.environ["EXP_DB_PATH"] = str(DIR_TEMP / "expedicao.db")
os.environ["EXP_BACKUP_DIR"] = str(DIR_TEMP / "backups")
# Aponta a migração legada para uma pasta vazia: os testes começam de um banco limpo.
os.environ["EXP_MIGRATION_DIR"] = str(DIR_TEMP / "sem_migracao")
os.environ["EXP_ADMIN_USUARIO"] = USUARIO_ADMIN
os.environ["EXP_ADMIN_SENHA"] = SENHA_ADMIN
os.environ["EXP_USUARIO_SENHA_INICIAL"] = SENHA_ADMIN

sys.path.insert(0, str(RAIZ))
import servidor_web as S  # noqa: E402

# O seed SKU↔MLB lê uma planilha de 200 KB do disco a cada init_db(). Nos testes ele é
# neutralizado para o banco começar previsível e a suíte rodar rápido.
S.ML_SKU_MLB_SEED_PATH = DIR_TEMP / "seed_inexistente.json"
# Silencia o log de acesso do BaseHTTPRequestHandler durante os testes.
S.AppHandler.log_message = lambda *args, **kwargs: None

_servidor: ThreadingHTTPServer | None = None
_url_base = ""


def iniciar_servidor() -> str:
    """Sobe o servidor real em uma porta livre, uma única vez por execução."""
    global _servidor, _url_base
    if _servidor is None:
        _servidor = ThreadingHTTPServer(("127.0.0.1", 0), S.AppHandler)
        _servidor.daemon_threads = True
        porta = _servidor.server_address[1]
        _url_base = f"http://127.0.0.1:{porta}{S.BASE_PATH}"
        threading.Thread(
            target=_servidor.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
            name="servidor-de-testes",
        ).start()
    return _url_base


def reiniciar_banco() -> None:
    """Recria o banco do zero, exatamente como em uma instalação nova."""
    # V18.56: o backup diário roda em thread própria, com conexão própria ao arquivo.
    # Espera ele terminar (e impede um novo) enquanto o arquivo é apagado — no Windows
    # um arquivo aberto não pode ser removido.
    with S.BACKUP_LOCK:
        try:
            with S.DB_LOCK:
                S.DB.close()
        except Exception:
            pass
        for sufixo in ("", "-wal", "-shm"):
            alvo = Path(str(S.DB_PATH) + sufixo)
            if alvo.exists():
                alvo.unlink()
        S.DB = S.connect_db()
        S.init_db()
    S.LOGIN_ATTEMPTS.clear()
    S.OWNER_REAUTH_FAILURES.clear()
    S._ml_full_ignorados_cache_limpar()  # V18.78: cópia em memória da tabela de Full
    S._ML_LOG_ULTIMO.clear()


class Cliente:
    """Cliente HTTP mínimo que conversa com o servidor como um navegador."""

    def __init__(self, url_base: str) -> None:
        self.url_base = url_base
        self.cookie = ""

    def pedir(self, metodo: str, rota: str, corpo: dict | None = None) -> tuple[int, dict]:
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as resposta:
                self._guardar_cookie(resposta.headers.get_all("Set-Cookie") or [])
                texto = resposta.read().decode("utf-8")
                return resposta.status, (json.loads(texto) if texto.strip() else {})
        except urllib.error.HTTPError as erro:
            self._guardar_cookie(erro.headers.get_all("Set-Cookie") or [])
            texto = erro.read().decode("utf-8")
            try:
                return erro.code, (json.loads(texto) if texto.strip() else {})
            except json.JSONDecodeError:
                return erro.code, {"corpo": texto}

    def _guardar_cookie(self, cabecalhos: list[str]) -> None:
        for bruto in cabecalhos:
            primeiro = bruto.split(";", 1)[0].strip()
            if primeiro.startswith(f"{S.SESSION_COOKIE}="):
                self.cookie = primeiro

    def entrar(self, usuario: str = USUARIO_ADMIN, senha: str = SENHA_ADMIN,
               area: str = "admin") -> dict:
        status, corpo = self.pedir(
            "POST", "/api/auth/login",
            {"username": usuario, "password": senha, "area": area},
        )
        if status != 200 or not corpo.get("ok"):
            raise AssertionError(f"Login de teste falhou ({status}): {corpo}")
        return corpo["user"]


# --------------------------------------------------------------------------
# Auxiliares de cenário
# --------------------------------------------------------------------------

def linha(pedido: str, sku: str, quantidade: int = 1, uid: str | None = None, **extra) -> dict:
    """Monta uma linha de conf_pedidos no formato usado pelo sistema.

    `uid` vira `__uidLinha`, a identidade persistente da linha. Ela precisa ser
    ESTÁVEL quando o teste altera o SKU: é justamente por isso que o sistema usa
    UID e não SKU para identificar a linha (ver `_pending_identity_v1714`).
    """
    registro = {
        "Pedido": pedido,
        "SKU": sku,
        "Quantidade": quantidade,
        "Lido": 0,
        "Cliente": "Cliente de Teste",
        "Marketplace": "SHOPEE",
        "EAN": f"789{abs(hash(sku)) % 10_000_000_000:010d}",
        "__uidLinha": uid or f"{pedido}-{sku}",
    }
    registro.update(extra)
    return registro


def pedidos() -> list[dict]:
    """Lê conf_pedidos pelo caminho canônico do servidor (com overrides aplicados)."""
    bruto, _, _ = S.get_storage("conf_pedidos")
    return json.loads(bruto or "[]")


def gravar_como_admin(linhas: list[dict], *, permitir_remocoes: bool = False) -> None:
    """Gravação explícita do ADMIN — a autoridade máxima do sistema."""
    atual, _, _ = S.get_storage("conf_pedidos")
    S.save_storage(
        "conf_pedidos", atual, json.dumps(linhas), False,
        actor=USUARIO_ADMIN, actor_role="admin",
        source_screen="Admin / Pedidos", change_method="Teste automatizado",
        client_ip="127.0.0.1",
        allow_completed_reimport=True,
        allow_pending_deletes=permitir_remocoes,
    )


def gravar_como_integracao(linhas: list[dict], *, origem: str = "API Shopee") -> None:
    """Simula uma sincronização automática.

    Espelha exatamente como `ml_import_order()` e `shopee_manual_sync()` chamam
    `save_storage`: actor_role="sistema" e sem permissão de exclusão.
    """
    atual, _, _ = S.get_storage("conf_pedidos")
    S.save_storage(
        "conf_pedidos", atual, json.dumps(linhas), False,
        actor="sistema", actor_role="sistema",
        source_screen=origem, change_method="Sincronização automática",
        client_ip="127.0.0.1",
        allow_completed_reimport=False,
    )


def numeros_de_pedido() -> set[str]:
    return {str(x.get("Pedido") or "") for x in pedidos()}


class TesteNoromak(unittest.TestCase):
    """Base comum: banco limpo e sessão ADMIN autenticada a cada teste."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.url_base = iniciar_servidor()

    def setUp(self) -> None:
        reiniciar_banco()
        self.cliente = Cliente(self.url_base)
        self.cliente.entrar()

    def salvar_admin(self, linhas: list[dict], *, permitir_remocoes: bool = False) -> dict:
        """Gravação do ADMIN pela rota HTTP real, como faz a tela de Administração."""
        atual, _, _ = S.get_storage("conf_pedidos")
        status, corpo = self.cliente.pedir("POST", "/api/storage", {
            "key": "conf_pedidos",
            "oldValue": atual,
            "newValue": json.dumps(linhas),
            "allowPendingDeletes": permitir_remocoes,
        })
        self.assertEqual(status, 200, f"gravação do ADMIN falhou: {corpo}")
        return corpo

    def excluir_admin(self, pedido: str, marketplace: str = "SHOPEE") -> dict:
        """Exclusão soberana do pedido inteiro (V17.81), pela rota HTTP real."""
        status, corpo = self.cliente.pedir("POST", "/api/order-kit-delete", {
            "pedido": pedido, "marketplace": marketplace,
        })
        self.assertEqual(status, 200, f"exclusão ADMIN falhou: {corpo}")
        self.assertTrue(corpo.get("ok"), f"exclusão ADMIN não confirmou: {corpo}")
        return corpo

    def tombstones(self) -> list[dict]:
        bruto, _, _ = S.get_storage(S.PENDING_ORDER_DELETE_KEY_V1766)
        return json.loads(bruto or "[]")

    def assertPedidoAusente(self, pedido: str, mensagem: str) -> None:
        self.assertNotIn(pedido, numeros_de_pedido(), mensagem)

    def assertPedidoPresente(self, pedido: str, mensagem: str) -> None:
        self.assertIn(pedido, numeros_de_pedido(), mensagem)
