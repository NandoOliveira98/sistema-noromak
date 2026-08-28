#!/usr/bin/env python3
"""Sistema de Conferência Noromak — edição Web/Linux 11.9.

Sem dependências externas: usa apenas a biblioteca padrão do Python.
- Escuta em 127.0.0.1:8100.
- Considera o prefixo /expedicao (o nginx não remove o prefixo).
- O nginx mantém o login do NoromakWeb e o app exige um segundo login próprio do estoque.
- Persiste em SQLite fora da pasta do código.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

APP_VERSION = "12.2"
APP_MARKER = "noromak_expedicao_web"
BASE_PATH = "/expedicao"
HOST = os.getenv("EXP_HOST", "127.0.0.1")
PORT = int(os.getenv("EXP_PORT", "8100"))
ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "APP"
DB_PATH = Path(os.getenv("EXP_DB_PATH", "/home/expedicao/dados/expedicao.db"))
BACKUP_DIR = Path(os.getenv("EXP_BACKUP_DIR", "/home/expedicao/backups"))
ADMIN_USERNAME = os.getenv("EXP_ADMIN_USUARIO", "luis").strip().lower()
QZ_BUNDLED_CERT_PATH = ROOT_DIR / "QZ_CERTIFICADO_WEB" / "digital-certificate.txt"
QZ_CONFIGURED_CERT_PATH = Path(os.getenv("QZ_CERT_PATH", str(QZ_BUNDLED_CERT_PATH)))
QZ_PRIVATE_KEY_PATH = Path(os.getenv("QZ_PRIVATE_KEY_PATH", "/home/expedicao/qz/qz-private-key.pem"))
MIGRATION_DIR = Path(os.getenv("EXP_MIGRATION_DIR", str(ROOT_DIR / "MIGRACAO_INICIAL")))
MAX_BODY = 100 * 1024 * 1024
LOCK_STALE_SECONDS = 5 * 60
PRINT_CLAIM_STALE_SECONDS = int(os.getenv("EXP_PRINT_CLAIM_STALE_SECONDS", "120"))
PRINT_STATION_ONLINE_SECONDS = int(os.getenv("EXP_PRINT_STATION_ONLINE_SECONDS", "20"))
SESSION_COOKIE = "expedicao_estoque"
SESSION_TTL_SECONDS = int(os.getenv("EXP_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
STATION_SESSION_TTL_SECONDS = int(os.getenv("EXP_STATION_SESSION_TTL_SECONDS", str(180 * 24 * 60 * 60)))
PASSWORD_ITERATIONS = int(os.getenv("EXP_PASSWORD_ITERATIONS", "240000"))
PASSWORD_MASK = "********"
INITIAL_ADMIN_PASSWORD = os.getenv("EXP_ADMIN_SENHA", "").strip()
INITIAL_OPERATOR_PASSWORD = os.getenv("EXP_USUARIO_SENHA_INICIAL", "").strip()
INITIAL_CREDENTIALS_PATH = Path(
    os.getenv("EXP_CREDENCIAIS_INICIAIS_PATH", str(DB_PATH.parent / "CREDENCIAIS_INICIAIS.txt"))
)

# Mercado Livre API / OAuth. O Client Secret fica somente no .env do servidor.
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://noromakweb.com.br/expedicao/api/integracoes/mercadolivre/callback",
).strip()
ML_NOTIFICATION_URL = os.getenv(
    "ML_NOTIFICATION_URL",
    "https://noromakweb.com.br/expedicao/api/integracoes/mercadolivre/notificacoes",
).strip()
ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_API_BASE = "https://api.mercadolibre.com"
ML_HTTP_TIMEOUT = int(os.getenv("ML_HTTP_TIMEOUT", "20"))
ML_MANUAL_SYNC_LIMIT = max(1, min(50, int(os.getenv("ML_MANUAL_SYNC_LIMIT", "20"))))

DB_LOCK = threading.RLock()
AUTH_LOCK = threading.Lock()
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
GENERATED_INITIAL_CREDENTIALS: dict[str, str] = {}

KNOWN_KEYS = [
    "conf_pedidos",
    "conf_pedidos_rascunhos",
    "conf_produtos",
    "conf_usuarios",
    "conf_solicitacoes_usuarios",
    "conf_etiquetas",
    "conf_fotos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em",
]

OPERATOR_WRITE_KEYS = {
    "conf_pedidos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em",
}


def _openssl_output(args: list[str], *, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["openssl", *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "Falha no OpenSSL")
    return proc.stdout


def _qz_cert_public_key(path: Path) -> bytes:
    return b"".join(_openssl_output(["x509", "-in", str(path), "-pubkey", "-noout"]).split())


def _qz_private_public_key(path: Path) -> bytes:
    return b"".join(_openssl_output(["pkey", "-in", str(path), "-pubout"]).split())


def _qz_cert_fingerprint(path: Path) -> str:
    output = _openssl_output(["x509", "-in", str(path), "-outform", "DER"])
    digest = hashlib.sha256(output).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _qz_cert_text(path: Path, field: str) -> str:
    value = _openssl_output(["x509", "-in", str(path), "-noout", f"-{field}"]).decode("utf-8", errors="replace").strip()
    return value.split("=", 1)[-1].strip() if "=" in value else value


def select_qz_cert_path() -> Path:
    # O instalador Windows autoriza exatamente o certificado incluído no pacote.
    # Para impedir divergências entre o certificado autorizado e o certificado
    # apresentado ao QZ Tray, o servidor usa sempre o arquivo empacotado.
    if QZ_BUNDLED_CERT_PATH.exists():
        return QZ_BUNDLED_CERT_PATH
    return QZ_CONFIGURED_CERT_PATH


def qz_material_status() -> dict[str, Any]:
    cert_path = select_qz_cert_path()
    result: dict[str, Any] = {
        "certificate_path": str(cert_path),
        "private_key_path": str(QZ_PRIVATE_KEY_PATH),
        "certificate_exists": cert_path.exists(),
        "private_key_exists": QZ_PRIVATE_KEY_PATH.exists(),
        "key_match": False,
        "fingerprint_sha256": "",
        "subject": "",
        "issuer": "",
        "error": "",
    }
    try:
        if not cert_path.exists():
            raise FileNotFoundError(f"Certificado QZ não encontrado em {cert_path}")
        result["fingerprint_sha256"] = _qz_cert_fingerprint(cert_path)
        result["subject"] = _qz_cert_text(cert_path, "subject")
        result["issuer"] = _qz_cert_text(cert_path, "issuer")
        if not QZ_PRIVATE_KEY_PATH.exists():
            raise FileNotFoundError(f"Chave privada QZ não encontrada em {QZ_PRIVATE_KEY_PATH}")
        result["key_match"] = hmac.compare_digest(
            _qz_cert_public_key(cert_path),
            _qz_private_public_key(QZ_PRIVATE_KEY_PATH),
        )
        if not result["key_match"]:
            result["error"] = "A chave privada do servidor não corresponde ao certificado QZ publicado."
    except Exception as exc:
        result["error"] = str(exc)
    return result


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def normalize(value: Any) -> str:
    return "".join(str(value if value is not None else "").strip().upper().split())


def order_number(item: dict[str, Any]) -> str:
    for key in ("Pedido", "pedido"):
        if key in item:
            return str(item.get(key) or "").strip()
    return ""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


DB = connect_db()


PRINT_JOB_REQUIRED_COLUMNS = {
    "id", "order_key", "order_id", "marketplace", "payload", "document_count",
    "status", "created_at", "created_by", "updated_at", "claimed_by",
    "claimed_at_epoch", "printed_at", "error", "attempts",
}

PRINT_JOB_CREATE_SQL = """
CREATE TABLE print_jobs (
    id TEXT PRIMARY KEY,
    order_key TEXT NOT NULL,
    order_id TEXT NOT NULL,
    marketplace TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    document_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('queued','claimed','printed','failed','cancelled')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_by TEXT NOT NULL DEFAULT '',
    claimed_at_epoch INTEGER NOT NULL DEFAULT 0,
    printed_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0
)
"""


def infer_marketplace_code(order_id: Any) -> str:
    token = normalize(order_id)
    if (token.isdigit() and len(token) == 11) or (token.startswith("20000") and token.isdigit() and len(token) == 16):
        return "mercado_livre"
    if token.startswith("LU") and len(token) >= 10:
        return "magalu"
    if token.isdigit() and len(token) == 16:
        return "magalu"
    if token.startswith("26") and any(char.isalpha() for char in token):
        return "shopee"
    return ""


def parse_legacy_print_payload(raw: Any) -> list[str]:
    if raw is None:
        return []
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except Exception:
            value = [raw] if raw.strip() else []
    if isinstance(value, dict):
        value = value.get("documents") or value.get("payload") or []
    if not isinstance(value, list):
        value = [value]
    return [str(item) for item in value if isinstance(item, (str, int, float)) and str(item).strip()]


def normalize_legacy_print_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    mapping = {
        "pending": "queued",
        "queued": "queued",
        "leased": "queued",
        "printing": "queued",
        "claimed": "queued",
        "completed": "printed",
        "complete": "printed",
        "done": "printed",
        "success": "printed",
        "printed": "printed",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    return mapping.get(status, "failed")


def ensure_print_queue_schema() -> None:
    """Atualiza bancos antigos da fila de impressão sem apagar pedidos ou histórico.

    A versão 8.0 usava outra estrutura em ``print_jobs`` (documents, kind,
    created_epoch etc.). O SQLite não adiciona colunas quando usamos
    ``CREATE TABLE IF NOT EXISTS``. Por isso, ao atualizar um banco existente,
    a fila móvel podia falhar com ``no column named marketplace``.
    """
    with DB_LOCK:
        info = DB.execute("PRAGMA table_info(print_jobs)").fetchall()
        if not info:
            DB.execute(PRINT_JOB_CREATE_SQL)
            DB.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_status_created ON print_jobs(status, created_at)")
            DB.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_order ON print_jobs(order_key, created_at)")
            DB.commit()
            return

        columns = {str(row["name"]) for row in info}
        if PRINT_JOB_REQUIRED_COLUMNS.issubset(columns):
            # Remove apenas o índice legado da versão do agente local, se ainda existir.
            DB.execute("DROP INDEX IF EXISTS idx_print_jobs_queue")
            DB.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_status_created ON print_jobs(status, created_at)")
            DB.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_order ON print_jobs(order_key, created_at)")
            DB.commit()
            return

        old_rows = [dict(row) for row in DB.execute("SELECT * FROM print_jobs").fetchall()]
        is_legacy_agent_schema = "documents" in columns and "payload" not in columns
        legacy_name = f"print_jobs_legacy_{int(time.time())}"
        suffix = 1
        while DB.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_name,)).fetchone():
            suffix += 1
            legacy_name = f"print_jobs_legacy_{int(time.time())}_{suffix}"

        indexes = [
            row["name"]
            for row in DB.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='print_jobs' AND sql IS NOT NULL"
            ).fetchall()
        ]

        DB.execute("BEGIN IMMEDIATE")
        try:
            for index_name in indexes:
                safe_index = str(index_name).replace('"', '""')
                DB.execute(f'DROP INDEX IF EXISTS "{safe_index}"')
            DB.execute(f'ALTER TABLE print_jobs RENAME TO "{legacy_name}"')
            DB.execute(PRINT_JOB_CREATE_SQL)
            DB.execute("CREATE INDEX idx_print_jobs_status_created ON print_jobs(status, created_at)")
            DB.execute("CREATE INDEX idx_print_jobs_order ON print_jobs(order_key, created_at)")

            for old in old_rows:
                payload = parse_legacy_print_payload(old.get("payload", old.get("documents")))
                order_id = str(old.get("order_id") or old.get("pedido") or "").strip()
                order_key = str(old.get("order_key") or normalize(order_id)).strip()
                created_at = str(old.get("created_at") or now_iso())
                raw_status = str(old.get("status") or "").strip().lower()
                status = normalize_legacy_print_status(raw_status)
                migration_error = str(old.get("error") or "")
                # Trabalhos ainda pendentes do agente abandonado da versão 8.0
                # não voltam para a fila, evitando impressão duplicada de etiquetas antigas.
                if is_legacy_agent_schema and raw_status in {"pending", "leased", "printing", "claimed", "queued"}:
                    status = "cancelled"
                    migration_error = (migration_error + " | " if migration_error else "") + "Fila antiga arquivada na atualização 9.1"
                printed_at = str(old.get("printed_at") or old.get("completed_at") or "") if status == "printed" else ""
                updated_at = str(old.get("updated_at") or printed_at or created_at)
                marketplace = str(old.get("marketplace") or infer_marketplace_code(order_id)).strip()
                claimed_by = str(old.get("claimed_by") or old.get("leased_by") or "") if status == "claimed" else ""
                claimed_at_epoch = int(old.get("claimed_at_epoch") or 0) if status == "claimed" else 0
                DB.execute(
                    """INSERT OR IGNORE INTO print_jobs(
                           id,order_key,order_id,marketplace,payload,document_count,status,
                           created_at,created_by,updated_at,claimed_by,claimed_at_epoch,
                           printed_at,error,attempts
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(old.get("id") or ("PRN-MIG-" + secrets.token_urlsafe(12))),
                        order_key,
                        order_id,
                        marketplace,
                        json_dumps(payload),
                        len(payload),
                        status,
                        created_at,
                        str(old.get("created_by") or "migracao"),
                        updated_at,
                        claimed_by,
                        claimed_at_epoch,
                        printed_at,
                        migration_error,
                        int(old.get("attempts") or 0),
                    ),
                )
            DB.commit()
        except Exception:
            DB.rollback()
            raise



def ensure_app_sessions_schema() -> None:
    """Adiciona a área da sessão em bancos anteriores à versão 9.5.

    A Estação Zebra usa uma sessão persistente própria, para que o computador
    possa reiniciar e voltar a imprimir sem pedir o login do estoque todos os dias.
    """
    with DB_LOCK:
        info = DB.execute("PRAGMA table_info(app_sessions)").fetchall()
        if not info:
            return
        columns = {str(row["name"]) for row in info}
        if "area" not in columns:
            DB.execute("ALTER TABLE app_sessions ADD COLUMN area TEXT NOT NULL DEFAULT 'conferencia'")
            DB.commit()


def session_ttl_for_area(area: str) -> int:
    return STATION_SESSION_TTL_SECONDS if str(area or "").lower() in {"estacao", "station", "impressao"} else SESSION_TTL_SECONDS


def ensure_app_user_permissions_schema() -> None:
    """Adiciona permissões granulares sem retirar acesso dos usuários existentes."""
    with DB_LOCK:
        info = DB.execute("PRAGMA table_info(app_users)").fetchall()
        if not info:
            return
        columns = {str(row["name"]) for row in info}
        additions = {
            "can_conferencia": "INTEGER NOT NULL DEFAULT 1",
            "can_admin": "INTEGER NOT NULL DEFAULT 0",
            "can_estacao": "INTEGER NOT NULL DEFAULT 0",
            "can_precificador": "INTEGER NOT NULL DEFAULT 0",
        }
        changed = False
        for name, ddl in additions.items():
            if name not in columns:
                DB.execute(f"ALTER TABLE app_users ADD COLUMN {name} {ddl}")
                changed = True
        # Migração segura: administradores existentes recebem tudo; operadores, Conferência.
        DB.execute(
            """UPDATE app_users SET
                 can_conferencia=1,
                 can_admin=CASE WHEN role='admin' THEN 1 ELSE COALESCE(can_admin,0) END,
                 can_estacao=CASE WHEN role='admin' THEN 1 ELSE COALESCE(can_estacao,0) END,
                 can_precificador=CASE WHEN role='admin' THEN 1 ELSE COALESCE(can_precificador,0) END
               WHERE role='admin' OR can_conferencia IS NULL"""
        )
        if changed:
            DB.commit()


def user_permissions_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, bool]:
    role = str(row["role"] if "role" in row.keys() else row.get("role", "operador")).lower()
    def flag(name: str, admin_default: bool = False) -> bool:
        try:
            value = row[name]
            return bool(value)
        except Exception:
            return admin_default if role == "admin" else (name == "can_conferencia")
    return {
        "conferencia": flag("can_conferencia", True),
        "admin": flag("can_admin", True),
        "estacao": flag("can_estacao", True),
        "precificador": flag("can_precificador", True),
    }


def module_allowed(ctx: dict[str, Any] | None, module: str) -> bool:
    if not ctx:
        return False
    permissions = ctx.get("permissions") or {}
    return bool(permissions.get(module))


def init_db() -> None:
    with DB_LOCK:
        DB.executescript(
            """
            CREATE TABLE IF NOT EXISTS storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completed_orders (
                order_key TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                completed_by TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_completion_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_key TEXT NOT NULL,
                order_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                completed_by TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '',
                UNIQUE(order_key, completed_at, completed_by)
            );
            CREATE INDEX IF NOT EXISTS idx_order_completion_history_key
              ON order_completion_history(order_key, id DESC);
            CREATE TABLE IF NOT EXISTS order_locks (
                order_key TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                operator TEXT NOT NULL,
                computer TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_roles (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK(role IN ('admin','operador')),
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','operador')),
                active INTEGER NOT NULL DEFAULT 1,
                can_conferencia INTEGER NOT NULL DEFAULT 1,
                can_admin INTEGER NOT NULL DEFAULT 0,
                can_estacao INTEGER NOT NULL DEFAULT 0,
                can_precificador INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                area TEXT NOT NULL DEFAULT 'conferencia',
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                ip TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(username) REFERENCES app_users(username) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_app_sessions_expires ON app_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sku_change_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_at TEXT NOT NULL,
                username TEXT NOT NULL,
                user_role TEXT NOT NULL DEFAULT '',
                source_screen TEXT NOT NULL DEFAULT '',
                change_method TEXT NOT NULL DEFAULT '',
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                order_id TEXT NOT NULL DEFAULT '',
                marketplace TEXT NOT NULL DEFAULT '',
                old_sku TEXT NOT NULL DEFAULT '',
                new_sku TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                client_ip TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sku_history_changed_at ON sku_change_history(changed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sku_history_order ON sku_change_history(order_id, changed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sku_history_skus ON sku_change_history(old_sku, new_sku);
            CREATE TABLE IF NOT EXISTS sku_state (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                sku TEXT NOT NULL DEFAULT '',
                order_id TEXT NOT NULL DEFAULT '',
                marketplace TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(entity_type, entity_id)
            );
            CREATE TABLE IF NOT EXISTS ml_integration (
                id INTEGER PRIMARY KEY CHECK(id=1),
                seller_id TEXT NOT NULL DEFAULT '',
                nickname TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                token_type TEXT NOT NULL DEFAULT 'Bearer',
                scope TEXT NOT NULL DEFAULT '',
                expires_at_epoch INTEGER NOT NULL DEFAULT 0,
                connected_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                last_sync_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ml_oauth_state (
                state TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at_epoch INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ml_oauth_state_expires ON ml_oauth_state(expires_at_epoch);
            CREATE TABLE IF NOT EXISTS ml_event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                resource TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                application_id TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_ml_event_log_created ON ml_event_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ml_event_log_resource ON ml_event_log(resource, created_at DESC);
            CREATE TABLE IF NOT EXISTS print_jobs (
                id TEXT PRIMARY KEY,
                order_key TEXT NOT NULL,
                order_id TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                document_count INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL CHECK(status IN ('queued','claimed','printed','failed','cancelled')),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                claimed_by TEXT NOT NULL DEFAULT '',
                claimed_at_epoch INTEGER NOT NULL DEFAULT 0,
                printed_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_print_jobs_status_created ON print_jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_print_jobs_order ON print_jobs(order_key, created_at);
            CREATE TABLE IF NOT EXISTS print_stations (
                station_id TEXT PRIMARY KEY,
                station_name TEXT NOT NULL DEFAULT '',
                printer_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                last_seen_epoch INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        if ADMIN_USERNAME:
            DB.execute(
                """INSERT INTO user_roles(username, display_name, role, active, updated_at, updated_by)
                   VALUES(?, ?, 'admin', 1, ?, 'EXP_ADMIN_USUARIO')
                   ON CONFLICT(username) DO NOTHING""",
                (ADMIN_USERNAME, ADMIN_USERNAME, now_iso()),
            )
        DB.commit()
    ensure_print_queue_schema()
    ensure_app_sessions_schema()
    ensure_app_user_permissions_schema()
    migrate_legacy_if_empty()
    ensure_storage_defaults()
    seed_sku_state_from_storage()
    migrate_completion_history()
    migrate_app_users()
    sync_app_users_storage()
    purge_expired_sessions()


def legacy_key_from_filename(name: str) -> str | None:
    upper = name.upper()
    mapping = {
        "CONF_PEDIDOS_RASCUNHOS": "conf_pedidos_rascunhos",
        "CONF_PRODUTOS": "conf_produtos",
        "CONF_USUARIOS": "conf_usuarios",
        "CONF_SOLICITACOES_USUARIOS": "conf_solicitacoes_usuarios",
        "CONF_ETIQUETAS": "conf_etiquetas",
        "CONF_FOTOS": "conf_fotos",
        "CONF_HISTORICO_CONFERENCIAS_V2": "conf_historico_conferencias_v2",
        "CONF_LOGS": "conf_logs",
        "CONF_BANCO_ATUALIZADO_EM": "conf_banco_atualizado_em",
    }
    for marker, key in mapping.items():
        if marker in upper:
            return key
    return None


def sanitize_legacy_users(value: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        rows = json.loads(value)
    except Exception:
        return "[]", []
    if not isinstance(rows, list):
        return "[]", []
    clean: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        login = str(raw.get("Login") or raw.get("login") or raw.get("Usuario") or raw.get("Usuário") or "").strip()
        if not login:
            continue
        role_text = str(raw.get("Perfil") or "Operador").lower()
        role = "admin" if "admin" in role_text else "operador"
        active = raw.get("Ativo", True) not in (False, "false", "0", 0, "inativo")
        clean.append(
            {
                "Nome": str(raw.get("Nome") or login).strip(),
                "Login": login,
                "Senha": str(raw.get("Senha") or "").strip(),
                "Perfil": "Admin" if role == "admin" else "Operador",
                "Ativo": active,
                "Aprovado": active,
            }
        )
    return json_dumps(clean), clean


def migrate_legacy_if_empty() -> None:
    with DB_LOCK:
        count = DB.execute("SELECT COUNT(*) FROM storage").fetchone()[0]
    if count or not MIGRATION_DIR.exists():
        return

    imported: dict[str, str] = {}
    banco_dir = MIGRATION_DIR / "BANCO"
    if banco_dir.exists():
        for path in banco_dir.glob("*.json"):
            key = legacy_key_from_filename(path.name)
            if not key:
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8-sig"))
                value = doc.get("value", doc.get("Value", ""))
                if not isinstance(value, str):
                    value = json_dumps(value)
                imported[key] = value
            except Exception as exc:
                print(f"Aviso: não foi possível migrar {path.name}: {exc}", file=sys.stderr)

    orders: list[dict[str, Any]] = []
    orders_dir = MIGRATION_DIR / "PEDIDOS"
    if orders_dir.exists():
        for path in orders_dir.glob("*.json"):
            try:
                doc = json.loads(path.read_text(encoding="utf-8-sig"))
                items = doc.get("items", doc.get("Items", []))
                if isinstance(items, list):
                    orders.extend(x for x in items if isinstance(x, dict))
            except Exception:
                pass
    if orders:
        imported["conf_pedidos"] = json_dumps(orders)

    legacy_roles: list[dict[str, Any]] = []
    if "conf_usuarios" in imported:
        imported["conf_usuarios"], legacy_roles = sanitize_legacy_users(imported["conf_usuarios"])

    with DB_LOCK:
        ts = now_iso()
        DB.execute("BEGIN IMMEDIATE")
        try:
            for key, value in imported.items():
                DB.execute(
                    "INSERT OR IGNORE INTO storage(key,value,version,updated_at) VALUES(?,?,1,?)",
                    (key, value, ts),
                )
            for user in legacy_roles:
                username = str(user["Login"]).strip().lower()
                if not username:
                    continue
                DB.execute(
                    """INSERT INTO user_roles(username,display_name,role,active,updated_at,updated_by)
                       VALUES(?,?,?,?,?,'migração 6.9') ON CONFLICT(username) DO NOTHING""",
                    (
                        username,
                        user["Nome"],
                        "admin" if user["Perfil"] == "Admin" else "operador",
                        1 if user["Ativo"] else 0,
                        ts,
                    ),
                )
            completed_root = MIGRATION_DIR / "CONCLUIDOS"
            if completed_root.exists():
                for path in completed_root.rglob("*.json"):
                    try:
                        doc = json.loads(path.read_text(encoding="utf-8-sig"))
                        pedido = str(doc.get("pedido") or doc.get("Pedido") or "").strip()
                        items = doc.get("items") or doc.get("Items") or []
                        if pedido:
                            DB.execute(
                                "INSERT OR IGNORE INTO completed_orders(order_key,order_id,completed_at,completed_by,payload) VALUES(?,?,?,?,?)",
                                (normalize(pedido), pedido, ts, "migração 6.9", json_dumps(items)),
                            )
                    except Exception:
                        pass
            DB.commit()
        except Exception:
            DB.rollback()
            raise


def ensure_storage_defaults() -> None:
    defaults = {
        "conf_pedidos": "[]",
        "conf_pedidos_rascunhos": "[]",
        "conf_usuarios": "[]",
        "conf_logs": "[]",
        "conf_historico_conferencias_v2": "[]",
    }
    with DB_LOCK:
        ts = now_iso()
        for key, value in defaults.items():
            DB.execute(
                "INSERT OR IGNORE INTO storage(key,value,version,updated_at) VALUES(?,?,1,?)",
                (key, value, ts),
            )
        DB.commit()


def password_digest(password: str, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> tuple[str, str, int]:
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(derived).decode("ascii"), base64.b64encode(salt).decode("ascii"), iterations


def password_matches(password: str, row: sqlite3.Row | dict[str, Any]) -> bool:
    try:
        salt = base64.b64decode(row["password_salt"])
        iterations = int(row["password_iterations"])
        expected = base64.b64decode(row["password_hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def persist_generated_credentials() -> None:
    """Grava credenciais de bootstrap fora do código, acessíveis só ao dono."""
    if not GENERATED_INITIAL_CREDENTIALS:
        return
    INITIAL_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "CREDENCIAIS INICIAIS — SISTEMA DE EXPEDIÇÃO",
        "Troque estas senhas no primeiro acesso e depois remova este arquivo.",
        "",
    ]
    for username, password in sorted(GENERATED_INITIAL_CREDENTIALS.items()):
        lines.extend([f"Usuário: {username}", f"Senha: {password}", ""])
    INITIAL_CREDENTIALS_PATH.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(INITIAL_CREDENTIALS_PATH, 0o600)
    except OSError:
        pass


def initial_password_for(username: str, role: str) -> str:
    configured = INITIAL_ADMIN_PASSWORD if role == "admin" else INITIAL_OPERATOR_PASSWORD
    if configured:
        return configured
    username = username.strip().lower()
    password = GENERATED_INITIAL_CREDENTIALS.get(username)
    if not password:
        password = secrets.token_urlsafe(18)
        GENERATED_INITIAL_CREDENTIALS[username] = password
        persist_generated_credentials()
    return password


def upsert_app_user(
    username: str,
    display_name: str,
    role: str,
    active: bool,
    password: str | None,
    updated_by: str,
    only_if_missing: bool = False,
    permissions: dict[str, bool] | None = None,
) -> None:
    username = username.strip().lower()
    if not username:
        return
    role = "admin" if str(role).lower().startswith("admin") else "operador"
    existing = DB.execute("SELECT * FROM app_users WHERE username=?", (username,)).fetchone()
    if existing and only_if_missing:
        return
    if password:
        pwd_hash, pwd_salt, iterations = password_digest(password)
    elif existing:
        pwd_hash, pwd_salt, iterations = existing["password_hash"], existing["password_salt"], existing["password_iterations"]
    else:
        fallback = initial_password_for(username, role)
        pwd_hash, pwd_salt, iterations = password_digest(fallback)
    if permissions is None:
        if existing:
            perms = user_permissions_from_row(existing)
        elif role == "admin":
            perms = {"conferencia": True, "admin": True, "estacao": True, "precificador": True}
        else:
            perms = {"conferencia": True, "admin": False, "estacao": False, "precificador": False}
    else:
        perms = {
            "conferencia": bool(permissions.get("conferencia")),
            "admin": bool(permissions.get("admin")),
            "estacao": bool(permissions.get("estacao")),
            "precificador": bool(permissions.get("precificador")),
        }
    DB.execute(
        """INSERT INTO app_users(
             username,display_name,password_hash,password_salt,password_iterations,role,active,
             can_conferencia,can_admin,can_estacao,can_precificador,updated_at,updated_by
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET
           display_name=excluded.display_name,password_hash=excluded.password_hash,password_salt=excluded.password_salt,
           password_iterations=excluded.password_iterations,role=excluded.role,active=excluded.active,
           can_conferencia=excluded.can_conferencia,can_admin=excluded.can_admin,
           can_estacao=excluded.can_estacao,can_precificador=excluded.can_precificador,
           updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
        (
            username, display_name.strip() or username, pwd_hash, pwd_salt, iterations, role, 1 if active else 0,
            1 if perms["conferencia"] else 0, 1 if perms["admin"] else 0,
            1 if perms["estacao"] else 0, 1 if perms["precificador"] else 0,
            now_iso(), updated_by,
        ),
    )


def legacy_users_from_migration() -> list[dict[str, Any]]:
    banco_dir = MIGRATION_DIR / "BANCO"
    if not banco_dir.exists():
        return []
    for path in banco_dir.glob("*.json"):
        if legacy_key_from_filename(path.name) != "conf_usuarios":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
            value = doc.get("value", doc.get("Value", "[]"))
            rows = json.loads(value) if isinstance(value, str) else value
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except Exception:
            return []
    return []


def migrate_app_users() -> None:
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            for raw in legacy_users_from_migration():
                username = str(raw.get("Login") or raw.get("Usuario") or raw.get("Usuário") or "").strip().lower()
                if not username:
                    continue
                role = "admin" if "admin" in str(raw.get("Perfil") or "").lower() else "operador"
                active = raw.get("Ativo", True) not in (False, "false", "0", 0, "inativo")
                password = str(raw.get("Senha") or "").strip() or None
                upsert_app_user(username, str(raw.get("Nome") or username), role, active, password, "migração portátil", only_if_missing=True)

            current = DB.execute("SELECT value FROM storage WHERE key='conf_usuarios'").fetchone()
            if current:
                try:
                    rows = json.loads(current["value"] or "[]")
                except Exception:
                    rows = []
                if isinstance(rows, list):
                    for raw in rows:
                        if not isinstance(raw, dict):
                            continue
                        username = str(raw.get("Login") or raw.get("Usuario") or raw.get("Usuário") or "").strip().lower()
                        if not username:
                            continue
                        role = "admin" if "admin" in str(raw.get("Perfil") or "").lower() else "operador"
                        active = raw.get("Ativo", raw.get("Aprovado", True)) not in (False, "false", "0", 0, "inativo")
                        password = str(raw.get("Senha") or "").strip()
                        if password in ("", PASSWORD_MASK):
                            password = None
                        upsert_app_user(username, str(raw.get("Nome") or username), role, active, password, "migração banco", only_if_missing=True)

            # Perfis criados na versão 7.3 recebem senha inicial para poderem entrar no novo login interno.
            for row in DB.execute("SELECT username,display_name,role,active FROM user_roles").fetchall():
                initial = initial_password_for(row["username"], row["role"])
                upsert_app_user(row["username"], row["display_name"] or row["username"], row["role"], bool(row["active"]), initial, "migração 7.3", only_if_missing=True)

            # Instalação realmente nova: cria somente o administrador configurado.
            # A antiga conta genérica ``admin`` não é mais criada automaticamente.
            if not DB.execute("SELECT 1 FROM app_users LIMIT 1").fetchone():
                username = ADMIN_USERNAME or "luis"
                initial = initial_password_for(username, "admin")
                upsert_app_user(username, username.title(), "admin", True, initial, "bootstrap seguro 10.9", only_if_missing=True)
            DB.commit()
        except Exception:
            DB.rollback()
            raise


def app_users_payload() -> list[dict[str, Any]]:
    rows = DB.execute(
        """SELECT username,display_name,role,active,can_conferencia,can_admin,can_estacao,can_precificador,
                  updated_at,updated_by FROM app_users ORDER BY display_name,username"""
    ).fetchall()
    return [
        {
            "__uidUsuario": "WEB_" + hashlib.sha1(row["username"].encode("utf-8")).hexdigest()[:12],
            "Nome": row["display_name"] or row["username"],
            "Login": row["username"],
            "Senha": PASSWORD_MASK,
            "Perfil": "Admin" if row["role"] == "admin" else "Operador",
            "Ativo": bool(row["active"]),
            "Aprovado": bool(row["active"]),
            "AcessoConferencia": bool(row["can_conferencia"]),
            "AcessoAdmin": bool(row["can_admin"]),
            "AcessoEstacao": bool(row["can_estacao"]),
            "AcessoPrecificador": bool(row["can_precificador"]),
            "Acessos": {
                "conferencia": bool(row["can_conferencia"]),
                "admin": bool(row["can_admin"]),
                "estacao": bool(row["can_estacao"]),
                "precificador": bool(row["can_precificador"]),
            },
            "AtualizadoEm": row["updated_at"],
            "AtualizadoPor": row["updated_by"],
            "SenhaInicialPendente": str(row["updated_by"] or "").lower().startswith(("migração", "bootstrap")),
        }
        for row in rows
    ]


def sync_app_users_storage() -> None:
    with DB_LOCK:
        value = json_dumps(app_users_payload())
        current = DB.execute("SELECT version,value FROM storage WHERE key='conf_usuarios'").fetchone()
        if current and current["value"] == value:
            return
        version = (current["version"] if current else 0) + 1
        DB.execute(
            """INSERT INTO storage(key,value,version,updated_at) VALUES('conf_usuarios',?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
            (value, version, now_iso()),
        )
        DB.commit()


def apply_app_users_payload(value: str, actor: str) -> str:
    try:
        rows = json.loads(value)
    except Exception as exc:
        raise ValueError("Lista de usuários inválida.") from exc
    if not isinstance(rows, list):
        raise ValueError("Lista de usuários inválida.")

    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    with DB_LOCK:
        existing = {row["username"]: row for row in DB.execute("SELECT * FROM app_users").fetchall()}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            username = str(raw.get("Login") or raw.get("Usuario") or raw.get("Usuário") or "").strip().lower()
            if not username:
                continue
            if any(ch.isspace() for ch in username):
                raise ValueError(f"O login '{username}' não pode conter espaços.")
            if username in seen:
                raise ValueError(f"O login '{username}' está duplicado.")
            seen.add(username)
            display_name = str(raw.get("Nome") or username).strip() or username
            role = "admin" if "admin" in str(raw.get("Perfil") or "").lower() else "operador"
            active = raw.get("Ativo", raw.get("Aprovado", True)) not in (False, "false", "0", 0, "inativo")
            password = str(raw.get("Senha") or "").strip()
            if password == PASSWORD_MASK:
                password = ""
            if username not in existing and len(password) < 4:
                raise ValueError(f"Informe uma senha com pelo menos 4 caracteres para o novo usuário '{username}'.")
            if password and len(password) < 4:
                raise ValueError(f"A senha de '{username}' deve ter pelo menos 4 caracteres.")
            raw_access = raw.get("Acessos") if isinstance(raw.get("Acessos"), dict) else {}
            if role == "admin" and not any(k in raw for k in ("AcessoConferencia","AcessoAdmin","AcessoEstacao","AcessoPrecificador")) and not raw_access:
                permissions = {"conferencia": True, "admin": True, "estacao": True, "precificador": True}
            else:
                permissions = {
                    "conferencia": bool(raw.get("AcessoConferencia", raw_access.get("conferencia", role != "admin"))),
                    "admin": bool(raw.get("AcessoAdmin", raw_access.get("admin", role == "admin"))),
                    "estacao": bool(raw.get("AcessoEstacao", raw_access.get("estacao", role == "admin"))),
                    "precificador": bool(raw.get("AcessoPrecificador", raw_access.get("precificador", role == "admin"))),
                }
            prepared.append({
                "username": username, "display_name": display_name, "role": role,
                "active": active, "password": password or None, "permissions": permissions
            })

        actor_row = existing.get(actor)
        actor_new = next((row for row in prepared if row["username"] == actor), None)
        if actor_row and actor_row["active"]:
            if not actor_new or not actor_new["active"] or not actor_new["permissions"]["admin"]:
                raise ValueError("Você não pode retirar o acesso ao Painel Administrativo do usuário que está usando esta sessão.")
        if not any(row["active"] and row["permissions"]["admin"] for row in prepared):
            raise ValueError("É necessário manter pelo menos um usuário ativo com acesso ao Painel Administrativo.")

        DB.execute("BEGIN IMMEDIATE")
        try:
            for row in prepared:
                upsert_app_user(
                    row["username"], row["display_name"], row["role"], row["active"], row["password"], actor,
                    permissions=row["permissions"]
                )
            for username in set(existing) - seen:
                DB.execute("DELETE FROM app_users WHERE username=?", (username,))
            DB.execute("DELETE FROM app_sessions WHERE username NOT IN (SELECT username FROM app_users) OR username IN (SELECT username FROM app_users WHERE active=0)")
            DB.commit()
        except Exception:
            DB.rollback()
            raise
    return json_dumps(app_users_payload())


def purge_expired_sessions() -> None:
    with DB_LOCK:
        DB.execute("DELETE FROM app_sessions WHERE expires_at <= ?", (int(time.time()),))
        DB.commit()


def backup_daily() -> None:
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        target = BACKUP_DIR / f"expedicao_{datetime.now().strftime('%Y-%m-%d')}.db"
        if target.exists():
            return
        with DB_LOCK:
            dest = sqlite3.connect(target)
            try:
                DB.backup(dest)
            finally:
                dest.close()
        files = sorted(BACKUP_DIR.glob("expedicao_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[15:]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Aviso: backup diário não realizado: {exc}", file=sys.stderr)


def get_storage(key: str) -> tuple[str | None, int, str | None]:
    row = DB.execute("SELECT value,version,updated_at FROM storage WHERE key=?", (key,)).fetchone()
    if not row:
        return None, 0, None
    value = row["value"]
    if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
        value = filter_completed_orders(value)
        try:
            parsed = json.loads(value or "[]")
            if isinstance(parsed, list):
                value = json_dumps(_enrich_rows_with_completion_history(parsed))
        except Exception:
            pass
    return value, int(row["version"]), row["updated_at"]


SKU_TRACKED_STORAGE_KEYS = {"conf_pedidos", "conf_pedidos_rascunhos", "conf_produtos"}


def _sku_entity_identity(key: str, item: dict[str, Any], index: int) -> tuple[str, str]:
    if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
        entity_type = "pedido"
        explicit = str(item.get("__uidLinha") or item.get("__uidPedido") or "").strip()
        if explicit:
            return entity_type, explicit
        fingerprint = "|".join([
            normalize(order_number(item)),
            normalize(item.get("Código de Barras")),
            normalize(item.get("Descrição")),
            normalize(item.get("Cliente")),
            str(index),
        ])
        return entity_type, "fallback:" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()

    entity_type = "produto"
    explicit = str(item.get("__uidProduto") or "").strip()
    if explicit:
        return entity_type, explicit
    fingerprint = "|".join([
        normalize(item.get("Código de Barras")),
        normalize(item.get("Descrição")),
        normalize(item.get("Marca")),
        normalize(item.get("Fabricante")),
        str(index),
    ])
    return entity_type, "fallback:" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


def _sku_row_metadata(key: str, item: dict[str, Any], index: int) -> dict[str, str]:
    entity_type, entity_id = _sku_entity_identity(key, item, index)
    order_id = order_number(item) if entity_type == "pedido" else ""
    marketplace = str(item.get("Marketplace") or "").strip()
    description = str(item.get("Descrição") or item.get("Descricao") or "").strip()
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "sku": str(item.get("SKU") or "").strip(),
        "order_id": order_id,
        "marketplace": marketplace,
        "description": description,
    }


def _rows_from_storage_value(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return [row for row in parsed if isinstance(row, dict)] if isinstance(parsed, list) else []


def seed_sku_state_from_storage() -> None:
    """Cria o estado-base sem gerar histórico na primeira execução da versão."""
    with DB_LOCK:
        for key in SKU_TRACKED_STORAGE_KEYS:
            value, _, _ = get_storage(key)
            for index, item in enumerate(_rows_from_storage_value(value)):
                meta = _sku_row_metadata(key, item, index)
                DB.execute(
                    """INSERT OR IGNORE INTO sku_state(
                         entity_type,entity_id,sku,order_id,marketplace,description,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        meta["entity_type"], meta["entity_id"], meta["sku"], meta["order_id"],
                        meta["marketplace"], meta["description"], now_iso(),
                    ),
                )
        DB.commit()


def _default_sku_change_method(storage_key: str, source_screen: str) -> str:
    screen = str(source_screen or "").strip().lower()
    if storage_key == "conf_produtos":
        return "Banco de produtos"
    if screen.startswith("confer"):
        return "Tela de conferência"
    if screen.startswith("admin"):
        return "Pedidos pendentes / importação"
    return "Sincronização do sistema"


def track_sku_changes(
    storage_key: str,
    merged_value: str,
    *,
    username: str,
    user_role: str,
    source_screen: str,
    change_method: str,
    client_ip: str,
) -> int:
    if storage_key not in SKU_TRACKED_STORAGE_KEYS:
        return 0
    total = 0
    stamp = now_iso()
    method = str(change_method or "").strip() or _default_sku_change_method(storage_key, source_screen)
    screen = str(source_screen or "").strip() or "Sistema"
    for index, item in enumerate(_rows_from_storage_value(merged_value)):
        meta = _sku_row_metadata(storage_key, item, index)
        previous = DB.execute(
            "SELECT sku FROM sku_state WHERE entity_type=? AND entity_id=?",
            (meta["entity_type"], meta["entity_id"]),
        ).fetchone()
        if previous is None:
            DB.execute(
                """INSERT INTO sku_state(entity_type,entity_id,sku,order_id,marketplace,description,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    meta["entity_type"], meta["entity_id"], meta["sku"], meta["order_id"],
                    meta["marketplace"], meta["description"], stamp,
                ),
            )
            continue

        old_sku = str(previous["sku"] or "").strip()
        new_sku = meta["sku"]
        if old_sku != new_sku:
            DB.execute(
                """INSERT INTO sku_change_history(
                     changed_at,username,user_role,source_screen,change_method,entity_type,entity_id,
                     order_id,marketplace,old_sku,new_sku,description,client_ip
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stamp, username, user_role, screen, method, meta["entity_type"], meta["entity_id"],
                    meta["order_id"], meta["marketplace"], old_sku, new_sku, meta["description"], client_ip,
                ),
            )
            total += 1
        DB.execute(
            """UPDATE sku_state SET sku=?,order_id=?,marketplace=?,description=?,updated_at=?
               WHERE entity_type=? AND entity_id=?""",
            (
                meta["sku"], meta["order_id"], meta["marketplace"], meta["description"], stamp,
                meta["entity_type"], meta["entity_id"],
            ),
        )
    return total


def item_id(key: str, item: Any, index: int) -> str:
    if not isinstance(item, dict):
        return "primitive:" + hashlib.sha1(json_dumps(item).encode()).hexdigest()
    fields: list[str] = []
    if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
        fields = ["__uidLinha", "__uidPedido"]
    elif key == "conf_produtos":
        fields = ["__uidProduto", "SKU", "Código de Barras"]
    elif key == "conf_usuarios":
        fields = ["__uidUsuario", "Login", "login", "Email"]
    elif key == "conf_solicitacoes_usuarios":
        fields = ["id", "__uidSolicitacao", "Login", "login"]
    elif key in ("conf_historico_conferencias_v2", "conf_logs"):
        fields = ["id", "__uid", "uid", "dataHora", "data"]
    for field in fields:
        if field in item and str(item.get(field) or "").strip():
            return field + ":" + normalize(item[field])
    if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
        composite = "|".join(
            normalize(item.get(field)) for field in ("Pedido", "SKU", "Código de Barras", "Descrição")
        )
        if composite.strip("|"):
            return "pedido:" + composite
    return "hash:" + hashlib.sha1(json_dumps(item).encode()).hexdigest()


def merge_arrays(key: str, current: list[Any], old: list[Any], newer: list[Any]) -> list[Any]:
    current_ids = [item_id(key, item, i) for i, item in enumerate(current)]
    current_map = {item_id(key, item, i): item for i, item in enumerate(current)}
    old_map = {item_id(key, item, i): item for i, item in enumerate(old)}
    new_map = {item_id(key, item, i): item for i, item in enumerate(newer)}
    for ident in old_map:
        if ident not in new_map:
            current_map.pop(ident, None)
    changed: list[str] = []
    for i, item in enumerate(newer):
        ident = item_id(key, item, i)
        if ident not in old_map or old_map[ident] != item:
            current_map[ident] = item
            changed.append(ident)
    result: list[Any] = []
    used: set[str] = set()
    for ident in current_ids + changed + sorted(current_map):
        if ident in current_map and ident not in used:
            result.append(current_map[ident])
            used.add(ident)
    return result


def rebuild_label_bank(bank: dict[str, Any]) -> dict[str, Any]:
    records = bank.get("registros") if isinstance(bank.get("registros"), dict) else {}
    order = bank.get("ordem") if isinstance(bank.get("ordem"), list) else list(records)
    order = [str(ident) for ident in order if str(ident) in records]
    for ident in records:
        if ident not in order:
            order.append(ident)
    index: dict[str, list[str]] = {}
    resumo = {"mercado_livre": 0, "magalu": 0, "shopee": 0, "desconhecido": 0}
    vinculados = {"mercado_livre": 0, "magalu": 0, "shopee": 0}
    for ident in order:
        reg = records.get(ident)
        if not isinstance(reg, dict):
            continue
        reg["id"] = ident
        marketplace = str(reg.get("marketplace") or "desconhecido")
        resumo[marketplace] = resumo.get(marketplace, 0) + 1
        if reg.get("pedido") and marketplace in vinculados:
            vinculados[marketplace] += 1
        chaves = reg.get("chaves") if isinstance(reg.get("chaves"), list) else []
        for raw in chaves:
            chave = str(raw or "").strip()
            if not chave:
                continue
            index.setdefault(chave, [])
            if ident not in index[chave]:
                index[chave].append(ident)
    bank["version"] = max(int(bank.get("version") or 0), 8)
    bank["registros"] = records
    bank["ordem"] = order
    bank["porChave"] = index
    bank["resumo"] = resumo
    bank["vinculados"] = vinculados
    bank["avisos"] = bank.get("avisos") if isinstance(bank.get("avisos"), list) else []
    bank["auditoria"] = bank.get("auditoria") if isinstance(bank.get("auditoria"), list) else []
    return bank


def merge_label_banks(current: dict[str, Any], old: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    current_records = dict(current.get("registros") or {}) if isinstance(current.get("registros"), dict) else {}
    old_records = dict(old.get("registros") or {}) if isinstance(old.get("registros"), dict) else {}
    new_records = dict(newer.get("registros") or {}) if isinstance(newer.get("registros"), dict) else {}

    # Remoções individuais ou em massa: só remove IDs que existiam no snapshot do cliente
    # e desapareceram da nova versão. Etiquetas adicionadas por outra máquina são preservadas.
    for ident in old_records:
        if ident not in new_records:
            current_records.pop(ident, None)

    changed: list[str] = []
    for ident, record in new_records.items():
        if ident not in old_records or old_records.get(ident) != record:
            current_records[ident] = record
            changed.append(ident)

    current_order = current.get("ordem") if isinstance(current.get("ordem"), list) else list(current_records)
    new_order = newer.get("ordem") if isinstance(newer.get("ordem"), list) else list(new_records)
    order: list[str] = []
    for ident in [*current_order, *new_order, *changed, *current_records.keys()]:
        ident = str(ident)
        if ident in current_records and ident not in order:
            order.append(ident)

    result.update({k: v for k, v in newer.items() if k not in {"registros", "ordem", "porChave", "resumo", "vinculados"}})
    result["registros"] = current_records
    result["ordem"] = order
    return rebuild_label_bank(result)


def merge_value(key: str, current: str | None, old: str | None, newer: str, force: bool) -> str:
    if force or current is None or current == old:
        return newer
    try:
        current_obj = json.loads(current)
        old_obj = json.loads(old or "")
        new_obj = json.loads(newer)
        if all(isinstance(x, list) for x in (current_obj, old_obj, new_obj)):
            return json_dumps(merge_arrays(key, current_obj, old_obj, new_obj))
        if key == "conf_etiquetas" and all(isinstance(x, dict) for x in (current_obj, old_obj, new_obj)):
            return json_dumps(merge_label_banks(current_obj, old_obj, new_obj))
    except Exception:
        pass
    return newer


def migrate_completion_history() -> None:
    """Mantém um histórico permanente, inclusive depois que um pedido é reaberto.

    A versão 10.0 removia o registro de ``completed_orders`` ao reimportar, pois essa
    tabela também bloqueia pedidos já finalizados. Com isso, o status visual podia
    depender apenas dos metadados embutidos em ``conf_pedidos``. Aqui preservamos uma
    segunda tabela somente de histórico e também reconstruímos registros antigos pelo
    log de auditoria quando possível.
    """
    with DB_LOCK:
        DB.execute(
            """INSERT OR IGNORE INTO order_completion_history
               (order_key,order_id,completed_at,completed_by,payload)
               SELECT order_key,order_id,completed_at,completed_by,payload
               FROM completed_orders"""
        )
        # Recupera conclusões antigas que já tenham sido reabertas e, por isso,
        # deixaram de existir em completed_orders. O audit_log é permanente.
        for row in DB.execute(
            "SELECT created_at,username,detail FROM audit_log WHERE action='order.complete' ORDER BY id"
        ).fetchall():
            pedido = str(row["detail"] or "").strip()
            key = normalize(pedido)
            if not key:
                continue
            DB.execute(
                """INSERT OR IGNORE INTO order_completion_history
                   (order_key,order_id,completed_at,completed_by,payload)
                   VALUES(?,?,?,?,?)""",
                (key, pedido, str(row["created_at"] or ""), str(row["username"] or ""), "[]"),
            )
        DB.commit()


def latest_completion_history(order_key: str) -> sqlite3.Row | None:
    key = normalize(order_key)
    if not key:
        return None
    return DB.execute(
        """SELECT order_key,order_id,completed_at,completed_by,payload
           FROM order_completion_history
           WHERE order_key=? ORDER BY id DESC LIMIT 1""",
        (key,),
    ).fetchone()


def _enrich_rows_with_completion_history(rows: list[Any]) -> list[Any]:
    """Reaplica a tag de histórico diretamente do SQLite.

    Assim a indicação JÁ FEITO não desaparece se outro navegador salvar a linha,
    se o cache for limpo ou se os metadados internos forem perdidos no front-end.
    """
    if not isinstance(rows, list):
        return rows
    keys = []
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = normalize(order_number(item))
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        return rows
    history: dict[str, sqlite3.Row] = {}
    # Evita ultrapassar o limite de parâmetros do SQLite em listas muito grandes.
    for start in range(0, len(keys), 400):
        chunk = keys[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        query = f"""SELECT h.order_key,h.order_id,h.completed_at,h.completed_by,h.payload
                    FROM order_completion_history h
                    JOIN (
                      SELECT order_key,MAX(id) AS max_id
                      FROM order_completion_history
                      WHERE order_key IN ({placeholders})
                      GROUP BY order_key
                    ) x ON x.max_id=h.id"""
        for row in DB.execute(query, chunk).fetchall():
            history[str(row["order_key"])] = row
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = normalize(order_number(item))
        previous = history.get(key)
        if not previous:
            continue
        item["__jaConcluidoAnteriormente"] = True
        item["__concluidoAnteriorEm"] = str(previous["completed_at"] or "")
        item["__concluidoAnteriorPor"] = str(previous["completed_by"] or "")
    return _propagate_previous_completion_metadata(rows)


def completed_keys() -> set[str]:
    return {row[0] for row in DB.execute("SELECT order_key FROM completed_orders")}


def filter_completed_orders(value: str) -> str:
    try:
        rows = json.loads(value)
    except Exception:
        return value
    if not isinstance(rows, list):
        return value
    done = completed_keys()
    filtered = [
        item
        for item in rows
        if isinstance(item, dict) and order_number(item) and normalize(order_number(item)) not in done
    ]
    return json_dumps(filtered)


def _order_keys_from_json(value: str | None) -> set[str]:
    if not value:
        return set()
    try:
        rows = json.loads(value)
    except Exception:
        return set()
    if not isinstance(rows, list):
        return set()
    return {
        normalize(order_number(item))
        for item in rows
        if isinstance(item, dict) and order_number(item)
    }


def _propagate_previous_completion_metadata(rows: list[Any]) -> list[Any]:
    metadata_by_order: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = normalize(order_number(item))
        if not key:
            continue
        if item.get("__jaConcluidoAnteriormente") or item.get("__concluidoAnteriorEm"):
            metadata_by_order[key] = {
                "__jaConcluidoAnteriormente": True,
                "__concluidoAnteriorEm": str(item.get("__concluidoAnteriorEm") or ""),
                "__concluidoAnteriorPor": str(item.get("__concluidoAnteriorPor") or ""),
                "__reimportadoEm": str(item.get("__reimportadoEm") or ""),
            }
    if not metadata_by_order:
        return rows
    for item in rows:
        if not isinstance(item, dict):
            continue
        meta = metadata_by_order.get(normalize(order_number(item)))
        if meta:
            item.update(meta)
    return rows


def reactivate_completed_orders_from_delta(old_value: str | None, new_value: str, actor: str) -> tuple[str, list[dict[str, str]]]:
    """Permite reimportar um pedido já concluído sem reabrir pedidos por salvamento antigo.

    Só considera pedidos que aparecem em ``new_value`` e não existiam no snapshot
    ``old_value`` enviado pelo navegador. Assim, uma tela antiga não ressuscita pedidos
    já concluídos por outra máquina, mas uma nova importação explícita do Admin consegue
    cadastrá-los novamente. A data/operador da conclusão anterior é copiada para a linha.
    """
    old_keys = _order_keys_from_json(old_value)
    try:
        rows = json.loads(new_value)
    except Exception:
        return new_value, []
    if not isinstance(rows, list):
        return new_value, []

    new_keys = {
        normalize(order_number(item))
        for item in rows
        if isinstance(item, dict) and order_number(item)
    }
    candidates = sorted(key for key in (new_keys - old_keys) if key)
    if not candidates:
        return json_dumps(_propagate_previous_completion_metadata(rows)), []

    reactivated: list[dict[str, str]] = []
    for key in candidates:
        previous = DB.execute(
            "SELECT order_key,order_id,completed_at,completed_by,payload FROM completed_orders WHERE order_key=?",
            (key,),
        ).fetchone()
        if not previous:
            previous = latest_completion_history(key)
        if not previous:
            continue
        DB.execute(
            """INSERT OR IGNORE INTO order_completion_history
               (order_key,order_id,completed_at,completed_by,payload) VALUES(?,?,?,?,?)""",
            (key, str(previous["order_id"] or ""), str(previous["completed_at"] or ""),
             str(previous["completed_by"] or ""), str(previous["payload"] or "[]")),
        )
        reimported_at = now_iso()
        meta = {
            "__jaConcluidoAnteriormente": True,
            "__concluidoAnteriorEm": str(previous["completed_at"] or ""),
            "__concluidoAnteriorPor": str(previous["completed_by"] or ""),
            "__reimportadoEm": reimported_at,
        }
        for item in rows:
            if isinstance(item, dict) and normalize(order_number(item)) == key:
                item.update(meta)
                # Uma reimportação deve iniciar uma nova conferência do zero.
                item["Lido"] = 0
        DB.execute("DELETE FROM completed_orders WHERE order_key=?", (key,))
        DB.execute(
            "INSERT INTO audit_log(created_at,username,action,detail) VALUES(?,?,?,?)",
            (reimported_at, actor, "order.reimport", f"{previous['order_id']}|anterior={previous['completed_at']}|por={previous['completed_by']}"[:4000]),
        )
        reactivated.append({
            "pedido": str(previous["order_id"] or ""),
            "concluido_em": str(previous["completed_at"] or ""),
            "concluido_por": str(previous["completed_by"] or ""),
        })

    rows = _enrich_rows_with_completion_history(rows)
    return json_dumps(rows), reactivated


def save_storage(
    key: str,
    old_value: str | None,
    new_value: str,
    force: bool,
    *,
    actor: str = "",
    actor_role: str = "",
    source_screen: str = "",
    change_method: str = "",
    client_ip: str = "",
    allow_completed_reimport: bool = False,
) -> tuple[str, int, str]:
    if len(new_value.encode("utf-8")) > MAX_BODY:
        raise ValueError("Conteúdo excede 100 MB")
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            if key == "conf_pedidos" and allow_completed_reimport and not force and old_value is not None:
                new_value, _ = reactivate_completed_orders_from_delta(old_value, new_value, actor or "admin")
            current, version, _ = get_storage(key)
            merged = merge_value(key, current, old_value, new_value, force)
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                try:
                    merged_rows = json.loads(merged)
                    if isinstance(merged_rows, list):
                        merged = json_dumps(_propagate_previous_completion_metadata(merged_rows))
                except Exception:
                    pass
                merged = filter_completed_orders(merged)
                try:
                    enriched_rows = json.loads(merged or "[]")
                    if isinstance(enriched_rows, list):
                        merged = json_dumps(_enrich_rows_with_completion_history(enriched_rows))
                except Exception:
                    pass
            version += 1
            updated = now_iso()
            DB.execute(
                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                (key, merged, version, updated),
            )
            track_sku_changes(
                key,
                merged,
                username=actor or "desconhecido",
                user_role=actor_role,
                source_screen=source_screen,
                change_method=change_method,
                client_ip=client_ip,
            )
            DB.commit()
        except Exception:
            DB.rollback()
            raise
    backup_daily()
    return merged, version, updated


def delete_storage(key: str) -> None:
    with DB_LOCK:
        DB.execute("DELETE FROM storage WHERE key=?", (key,))
        DB.commit()


def audit(username: str, action: str, detail: str = "") -> None:
    with DB_LOCK:
        DB.execute(
            "INSERT INTO audit_log(created_at,username,action,detail) VALUES(?,?,?,?)",
            (now_iso(), username, action, detail[:4000]),
        )
        DB.commit()


def cookie_token(handler: BaseHTTPRequestHandler) -> str:
    raw = handler.headers.get("Cookie", "")
    if not raw:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return ""
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else ""


def session_context(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    token = cookie_token(handler)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now_epoch = int(time.time())
    with DB_LOCK:
        row = DB.execute(
            """SELECT s.token_hash,s.username,s.area,s.expires_at,u.display_name,u.role,u.active,
                      u.can_conferencia,u.can_admin,u.can_estacao,u.can_precificador
               FROM app_sessions s JOIN app_users u ON u.username=s.username
               WHERE s.token_hash=?""",
            (token_hash,),
        ).fetchone()
        if not row or not row["active"] or int(row["expires_at"]) <= now_epoch:
            DB.execute("DELETE FROM app_sessions WHERE token_hash=?", (token_hash,))
            DB.commit()
            return None
        area = str(row["area"] or "conferencia")
        DB.execute(
            "UPDATE app_sessions SET last_seen=?,expires_at=? WHERE token_hash=?",
            (now_iso(), now_epoch + session_ttl_for_area(area), token_hash),
        )
        DB.commit()
    return {
        "username": row["username"],
        "nome": row["display_name"] or row["username"],
        "app_role": row["role"],
        "app_active": True,
        "permissions": user_permissions_from_row(row),
        "area": str(row["area"] or "conferencia"),
    }


def client_computer(handler: BaseHTTPRequestHandler) -> str:
    # O nginx sobrescreve X-Real-IP com o CF-Connecting-IP validado. O primeiro
    # X-Forwarded-For pode ser fornecido pelo próprio cliente e não é confiável.
    real = handler.headers.get("X-Real-IP", "").strip()
    return real or handler.client_address[0]


def login_rate_key(handler: BaseHTTPRequestHandler, username: str) -> str:
    return f"{client_computer(handler)}:{username.strip().lower()}"


def login_rate_limited(handler: BaseHTTPRequestHandler, username: str) -> bool:
    now = time.time()
    key = login_rate_key(handler, username)
    with AUTH_LOCK:
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(key, []) if stamp > now - 300]
        LOGIN_ATTEMPTS[key] = attempts
        return len(attempts) >= 8


def register_login_failure(handler: BaseHTTPRequestHandler, username: str) -> None:
    key = login_rate_key(handler, username)
    with AUTH_LOCK:
        LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def clear_login_failures(handler: BaseHTTPRequestHandler, username: str) -> None:
    with AUTH_LOCK:
        LOGIN_ATTEMPTS.pop(login_rate_key(handler, username), None)



# ---------------------------------------------------------------------------
# Mercado Livre API / OAuth / Orders
# ---------------------------------------------------------------------------

def ml_credentials_configured() -> bool:
    return bool(ML_CLIENT_ID and ML_CLIENT_SECRET and ML_REDIRECT_URI)


def ml_log_event(topic: str, resource: str, user_id: Any, application_id: Any, result: str, detail: str = "") -> None:
    with DB_LOCK:
        DB.execute(
            """INSERT INTO ml_event_log(created_at,topic,resource,user_id,application_id,result,detail)
               VALUES(?,?,?,?,?,?,?)""",
            (
                now_iso(),
                str(topic or "")[:100],
                str(resource or "")[:500],
                str(user_id or "")[:80],
                str(application_id or "")[:80],
                str(result or "")[:80],
                str(detail or "")[:2000],
            ),
        )
        DB.commit()


def ml_integration_row() -> sqlite3.Row | None:
    with DB_LOCK:
        return DB.execute("SELECT * FROM ml_integration WHERE id=1").fetchone()


def ml_status_payload() -> dict[str, Any]:
    row = ml_integration_row()
    connected = bool(row and row["access_token"] and row["seller_id"])
    return {
        "configured": ml_credentials_configured(),
        "connected": connected,
        "seller_id": str(row["seller_id"] if row else ""),
        "nickname": str(row["nickname"] if row else ""),
        "scope": str(row["scope"] if row else ""),
        "expires_at_epoch": int(row["expires_at_epoch"] if row else 0),
        "connected_at": str(row["connected_at"] if row else ""),
        "updated_at": str(row["updated_at"] if row else ""),
        "last_sync_at": str(row["last_sync_at"] if row else ""),
        "last_error": str(row["last_error"] if row else ""),
        "redirect_uri": ML_REDIRECT_URI,
        "notification_url": ML_NOTIFICATION_URL,
        "client_id_suffix": (ML_CLIENT_ID[-6:] if ML_CLIENT_ID else ""),
    }


def ml_http_json(
    method: str,
    url: str,
    *,
    token: str = "",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    body = None
    final_headers = {"Accept": "application/json", "User-Agent": f"NoromakExpedicao/{APP_VERSION}"}
    if token:
        final_headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = urlencode({k: "" if v is None else str(v) for k, v in data.items()}).encode("utf-8")
        final_headers["Content-Type"] = "application/x-www-form-urlencoded"
    final_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=final_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=ML_HTTP_TIMEOUT) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw
        raise RuntimeError(f"Mercado Livre HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de conexão com Mercado Livre: {exc.reason}") from exc


def ml_store_token(payload: dict[str, Any], *, nickname: str = "") -> None:
    seller_id = str(payload.get("user_id") or "")
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    token_type = str(payload.get("token_type") or "Bearer")
    scope = str(payload.get("scope") or "")
    expires_in = int(payload.get("expires_in") or 0)
    expires_at = int(time.time()) + max(0, expires_in)
    if not access_token or not seller_id:
        raise RuntimeError("Resposta de token do Mercado Livre incompleta.")
    previous = ml_integration_row()
    if previous and not refresh_token:
        refresh_token = str(previous["refresh_token"] or "")
    connected_at = str(previous["connected_at"] or "") if previous else ""
    if not connected_at:
        connected_at = now_iso()
    with DB_LOCK:
        DB.execute(
            """INSERT INTO ml_integration(
                 id,seller_id,nickname,access_token,refresh_token,token_type,scope,
                 expires_at_epoch,connected_at,updated_at,last_sync_at,last_error
               ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 seller_id=excluded.seller_id,
                 nickname=CASE WHEN excluded.nickname<>'' THEN excluded.nickname ELSE ml_integration.nickname END,
                 access_token=excluded.access_token,
                 refresh_token=CASE WHEN excluded.refresh_token<>'' THEN excluded.refresh_token ELSE ml_integration.refresh_token END,
                 token_type=excluded.token_type,
                 scope=excluded.scope,
                 expires_at_epoch=excluded.expires_at_epoch,
                 connected_at=CASE WHEN ml_integration.connected_at='' THEN excluded.connected_at ELSE ml_integration.connected_at END,
                 updated_at=excluded.updated_at,
                 last_error=''""",
            (
                seller_id, nickname, access_token, refresh_token, token_type, scope,
                expires_at, connected_at, now_iso(),
                str(previous["last_sync_at"] or "") if previous else "",
                "",
            ),
        )
        DB.commit()


def ml_access_token(*, force_refresh: bool = False) -> tuple[str, str]:
    row = ml_integration_row()
    if not row or not row["access_token"]:
        raise RuntimeError("Mercado Livre ainda não está conectado.")
    if not ml_credentials_configured():
        raise RuntimeError("ML_CLIENT_ID / ML_CLIENT_SECRET não estão configurados no servidor.")
    if not force_refresh and int(row["expires_at_epoch"] or 0) > int(time.time()) + 300:
        return str(row["access_token"]), str(row["seller_id"])
    refresh_token = str(row["refresh_token"] or "")
    if not refresh_token:
        raise RuntimeError("Refresh token do Mercado Livre não está disponível. Reconecte a conta.")
    payload = ml_http_json(
        "POST",
        f"{ML_API_BASE}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": refresh_token,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Resposta inválida ao renovar o token Mercado Livre.")
    ml_store_token(payload, nickname=str(row["nickname"] or ""))
    fresh = ml_integration_row()
    return str(fresh["access_token"]), str(fresh["seller_id"])


def ml_api_get(path_or_url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any]:
    token, _ = ml_access_token()
    url = path_or_url if path_or_url.startswith("http") else f"{ML_API_BASE}{path_or_url}"
    try:
        return ml_http_json("GET", url, token=token, headers=headers)
    except RuntimeError as exc:
        if "HTTP 401" not in str(exc):
            raise
    token, _ = ml_access_token(force_refresh=True)
    return ml_http_json("GET", url, token=token, headers=headers)


def ml_oauth_authorization_url() -> str:
    if not ml_credentials_configured():
        raise RuntimeError("Configure ML_CLIENT_ID e ML_CLIENT_SECRET no /home/expedicao/.env antes de conectar.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("ascii").rstrip("=")
    now_epoch = int(time.time())
    with DB_LOCK:
        DB.execute("DELETE FROM ml_oauth_state WHERE expires_at_epoch<?", (now_epoch,))
        DB.execute(
            "INSERT INTO ml_oauth_state(state,code_verifier,created_at,expires_at_epoch) VALUES(?,?,?,?)",
            (state, verifier, now_iso(), now_epoch + 15 * 60),
        )
        DB.commit()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": ML_CLIENT_ID,
            "redirect_uri": ML_REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{ML_AUTH_URL}?{query}"


def ml_exchange_authorization_code(code: str, state: str) -> dict[str, Any]:
    now_epoch = int(time.time())
    with DB_LOCK:
        row = DB.execute("SELECT * FROM ml_oauth_state WHERE state=?", (state,)).fetchone()
        DB.execute("DELETE FROM ml_oauth_state WHERE expires_at_epoch<?", (now_epoch,))
        if row:
            DB.execute("DELETE FROM ml_oauth_state WHERE state=?", (state,))
        DB.commit()
    if not row or int(row["expires_at_epoch"] or 0) < now_epoch:
        raise RuntimeError("Estado OAuth inválido ou expirado. Inicie a conexão novamente.")
    payload = ml_http_json(
        "POST",
        f"{ML_API_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": ML_REDIRECT_URI,
            "code_verifier": str(row["code_verifier"]),
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Resposta inválida do OAuth Mercado Livre.")
    ml_store_token(payload)
    token, _ = ml_access_token()
    me = ml_http_json("GET", f"{ML_API_BASE}/users/me", token=token)
    if isinstance(me, dict):
        nickname = str(me.get("nickname") or me.get("first_name") or "")
        if nickname:
            current = ml_integration_row()
            ml_store_token(
                {
                    "user_id": current["seller_id"],
                    "access_token": current["access_token"],
                    "refresh_token": current["refresh_token"],
                    "token_type": current["token_type"],
                    "scope": current["scope"],
                    "expires_in": max(0, int(current["expires_at_epoch"]) - int(time.time())),
                },
                nickname=nickname,
            )
    return ml_status_payload()


def _ml_parse_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return "-".join(match.groups()) if match else ""


def _ml_dispatch_date(shipment: dict[str, Any] | None) -> str:
    shipment = shipment or {}
    shipping_option = shipment.get("shipping_option") if isinstance(shipment.get("shipping_option"), dict) else {}
    candidates = [
        shipping_option.get("estimated_handling_limit"),
        shipping_option.get("estimated_delivery_limit"),
        shipment.get("estimated_handling_limit"),
        shipment.get("estimated_delivery_limit"),
    ]
    for value in candidates:
        parsed = _ml_parse_iso_date(value)
        if parsed:
            return parsed
    return datetime.now().astimezone().date().isoformat()


def _ml_find_product(products: list[dict[str, Any]], sku: str) -> dict[str, Any] | None:
    key = normalize(sku)
    if not key:
        return None
    for product in products:
        if normalize(product.get("SKU")) == key:
            return product
    return None


def _ml_order_item_sku(item_data: dict[str, Any]) -> str:
    item = item_data.get("item") if isinstance(item_data.get("item"), dict) else {}
    for value in (
        item.get("seller_sku"),
        item.get("seller_custom_field"),
        item_data.get("seller_sku"),
    ):
        if str(value or "").strip():
            return str(value).strip()
    for attr in item.get("variation_attributes") or []:
        if not isinstance(attr, dict):
            continue
        if str(attr.get("id") or "").upper() == "SELLER_SKU" and str(attr.get("value_name") or "").strip():
            return str(attr["value_name"]).strip()
    return ""


def ml_order_shipments(order_id: str) -> tuple[str, dict[str, Any] | None]:
    shipment_id = ""
    shipment_detail: dict[str, Any] | None = None
    try:
        result = ml_api_get(
            f"/orders/{quote(str(order_id))}/shipments?list_all=true",
            headers={"X-New-Domain": "true", "X-Api-Version": "2"},
        )
        shipments = result if isinstance(result, list) else ([result] if isinstance(result, dict) and result.get("id") else [])
        forward = next((x for x in shipments if isinstance(x, dict) and str(x.get("direction") or "forward") == "forward"), None)
        selected = forward or (shipments[0] if shipments else None)
        if isinstance(selected, dict):
            shipment_id = str(selected.get("id") or "")
    except Exception:
        shipment_id = ""
    if shipment_id:
        try:
            detail = ml_api_get(
                f"/shipments/{quote(shipment_id)}",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            if isinstance(detail, dict):
                shipment_detail = detail
        except Exception:
            shipment_detail = None
    return shipment_id, shipment_detail


def ml_import_order(order_id: str, *, source: str = "API Mercado Livre") -> dict[str, Any]:
    order_id = str(order_id or "").strip()
    if not order_id:
        raise RuntimeError("Order ID vazio.")
    order = ml_api_get(f"/orders/{quote(order_id)}")
    if not isinstance(order, dict):
        raise RuntimeError(f"Pedido {order_id}: resposta inválida.")
    _, seller_id = ml_access_token()
    seller = order.get("seller") if isinstance(order.get("seller"), dict) else {}
    order_seller = str(seller.get("id") or "")
    if order_seller and seller_id and order_seller != seller_id:
        raise RuntimeError(f"Pedido {order_id} pertence a outro seller.")

    status = str(order.get("status") or "")
    shipment_id, shipment = ml_order_shipments(order_id)
    receiver = ""
    if isinstance(shipment, dict):
        addr = shipment.get("receiver_address") if isinstance(shipment.get("receiver_address"), dict) else {}
        receiver = str(addr.get("receiver_name") or addr.get("receiver") or "").strip()
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    client = receiver or str(buyer.get("nickname") or "").strip()
    if not client:
        names = [str(buyer.get("first_name") or "").strip(), str(buyer.get("last_name") or "").strip()]
        client = " ".join(x for x in names if x).strip()

    pack_id = str(order.get("pack_id") or "").strip()
    operational_order_id = pack_id or order_id
    date_for_dispatch = _ml_dispatch_date(shipment)
    tracking = ""
    if isinstance(shipment, dict):
        tracking = str(shipment.get("tracking_number") or shipment.get("tracking_id") or "").strip()

    current_raw, _, _ = get_storage("conf_pedidos")
    products_raw, _, _ = get_storage("conf_produtos")
    pending = _rows_from_storage_value(current_raw)
    products = _rows_from_storage_value(products_raw)
    lines: list[dict[str, Any]] = []

    for item_data in order.get("order_items") or []:
        if not isinstance(item_data, dict):
            continue
        item = item_data.get("item") if isinstance(item_data.get("item"), dict) else {}
        sku = _ml_order_item_sku(item_data)
        description = str(item.get("title") or "").strip()
        quantity = max(1, int(item_data.get("quantity") or 1))
        row = {
            "Pedido": operational_order_id,
            "Marketplace": "MERCADO LIVRE",
            "Data do Pedido": date_for_dispatch,
            "Kit": "",
            "Nome do Kit": "",
            "Quantidade de Kits": "",
            "Quantidade por Kit": "",
            "Cliente": client,
            "SKU": sku,
            "Quantidade": str(quantity),
            "Código de Barras": "",
            "Descrição": description,
            "Rua": "",
            "Módulo": "",
            "Prateleira": "",
            "Nível": "",
            "Posição": "",
            "Foto": "",
            "Código de Rastreio": tracking,
            "__uidLinha": "MLAPI_" + hashlib.sha1(f"{order_id}|{item.get('id')}|{item.get('variation_id')}|{sku}".encode("utf-8")).hexdigest()[:20],
            "OrigemCadastro": source,
            "SkuOrigemImportacao": sku,
            "VendaMercadoLivre": order_id,
            "PackIdMercadoLivre": pack_id,
            "ShipmentIdMercadoLivre": shipment_id,
            "StatusMercadoLivre": status,
            "MercadoLivreAtualizadoEm": now_iso(),
        }
        product = _ml_find_product(products, sku)
        if product:
            for field in ("SKU","Código de Barras","Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto"):
                value = product.get(field)
                if str(value or "").strip():
                    row[field] = value
        lines.append(row)

    if not lines:
        raise RuntimeError(f"Pedido {order_id} não trouxe itens.")

    # Atualiza pela combinação Pedido + SKU; quando não existe, cria nova linha.
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for existing in pending:
        key = (normalize(order_number(existing)), normalize(existing.get("SKU") or existing.get("Código de Barras")))
        if key not in by_key:
            by_key[key] = existing
    inserted = updated = 0
    fields = (
        "Pedido","Marketplace","Data do Pedido","Cliente","SKU","Quantidade","Código de Barras",
        "Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto","Código de Rastreio"
    )
    for imported in lines:
        key = (normalize(imported["Pedido"]), normalize(imported.get("SKU") or imported.get("Código de Barras")))
        existing = by_key.get(key)
        if existing is None:
            pending.append(imported)
            by_key[key] = imported
            inserted += 1
            continue
        lido = int(existing.get("Lido") or 0)
        for field in fields:
            value = imported.get(field)
            if str(value or "").strip():
                existing[field] = value
        for field in (
            "OrigemCadastro","SkuOrigemImportacao","VendaMercadoLivre","PackIdMercadoLivre",
            "ShipmentIdMercadoLivre","StatusMercadoLivre","MercadoLivreAtualizadoEm"
        ):
            existing[field] = imported.get(field, "")
        existing["Lido"] = lido
        if int(existing.get("Quantidade") or 0) < lido:
            existing["Quantidade"] = str(lido)
        updated += 1

    # Cancelamentos não apagam o pedido; apenas ficam marcados para auditoria/ação manual.
    new_json = json_dumps(pending)
    save_storage(
        "conf_pedidos",
        current_raw,
        new_json,
        False,
        actor="mercadolivre-api",
        actor_role="sistema",
        source_screen="API Mercado Livre",
        change_method="Notificação / sincronização",
        client_ip="127.0.0.1",
        allow_completed_reimport=False,
    )
    with DB_LOCK:
        DB.execute(
            "UPDATE ml_integration SET last_sync_at=?, updated_at=?, last_error='' WHERE id=1",
            (now_iso(), now_iso()),
        )
        DB.commit()
    return {
        "order_id": order_id,
        "pack_id": pack_id,
        "shipment_id": shipment_id,
        "status": status,
        "inserted": inserted,
        "updated": updated,
        "items": len(lines),
    }


def ml_process_notification(payload: dict[str, Any]) -> None:
    topic = str(payload.get("topic") or "").strip()
    resource = str(payload.get("resource") or "").strip()
    user_id = str(payload.get("user_id") or "")
    app_id = str(payload.get("application_id") or "")
    if ML_CLIENT_ID and app_id and app_id != ML_CLIENT_ID:
        ml_log_event(topic, resource, user_id, app_id, "IGNORADO", "application_id diferente do aplicativo configurado")
        return
    integration = ml_integration_row()
    if integration and integration["seller_id"] and user_id and str(integration["seller_id"]) != user_id:
        ml_log_event(topic, resource, user_id, app_id, "IGNORADO", "user_id diferente do seller conectado")
        return
    try:
        if topic in {"orders_v2", "orders"} and "/orders/" in resource:
            order_id = resource.rstrip("/").split("/")[-1]
            result = ml_import_order(order_id, source="API Mercado Livre / orders_v2")
            ml_log_event(topic, resource, user_id, app_id, "OK", json_dumps(result))
            return
        if topic == "shipments" and "/shipments/" in resource:
            shipment_id = resource.rstrip("/").split("/")[-1]
            orders = ml_api_get(
                f"/shipments/{quote(shipment_id)}/orders",
                headers={"X-New-Domain": "true"},
            )
            results = []
            if isinstance(orders, list):
                for item in orders:
                    if isinstance(item, dict) and item.get("order_id"):
                        results.append(ml_import_order(str(item["order_id"]), source="API Mercado Livre / shipments"))
            ml_log_event(topic, resource, user_id, app_id, "OK", json_dumps(results))
            return
        ml_log_event(topic, resource, user_id, app_id, "IGNORADO", "Tópico/recurso sem ação configurada")
    except Exception as exc:
        ml_log_event(topic, resource, user_id, app_id, "ERRO", str(exc))
        with DB_LOCK:
            DB.execute("UPDATE ml_integration SET last_error=?,updated_at=? WHERE id=1", (str(exc)[:2000], now_iso()))
            DB.commit()


def ml_manual_sync() -> dict[str, Any]:
    _, seller_id = ml_access_token()
    query = urlencode(
        {
            "seller": seller_id,
            "order.status": "paid",
            "sort": "date_desc",
            "limit": str(ML_MANUAL_SYNC_LIMIT),
            "offset": "0",
        }
    )
    result = ml_api_get(f"/orders/search?{query}")
    orders = result.get("results") if isinstance(result, dict) else []
    imported = []
    errors = []
    for order in orders or []:
        order_id = str(order.get("id") or "") if isinstance(order, dict) else ""
        if not order_id:
            continue
        try:
            imported.append(ml_import_order(order_id, source="API Mercado Livre / sincronização manual"))
        except Exception as exc:
            errors.append({"order_id": order_id, "error": str(exc)})
    with DB_LOCK:
        DB.execute(
            "UPDATE ml_integration SET last_sync_at=?,updated_at=?,last_error=? WHERE id=1",
            (now_iso(), now_iso(), (json_dumps(errors[:5]) if errors else "")),
        )
        DB.commit()
    ml_log_event("manual_sync", "/orders/search", seller_id, ML_CLIENT_ID, "OK" if not errors else "PARCIAL", json_dumps({"imported": len(imported), "errors": errors[:10]}))
    return {"ok": not errors, "imported": len(imported), "errors": errors, "details": imported}



class AppHandler(BaseHTTPRequestHandler):
    server_version = "NoromakExpedicao/12.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* wss://127.0.0.1:* "
            "http://localhost:* https://localhost:* ws://localhost:* wss://localhost:* http://127.0.0.1:* https://127.0.0.1:* https://cdn.jsdelivr.net https://unpkg.com; "
            "worker-src 'self' blob: https://cdn.jsdelivr.net https://unpkg.com; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )

    def encoded_body(self, body: bytes, content_type: str) -> tuple[bytes, bool]:
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        compressible = (
            content_type.startswith("text/")
            or "json" in content_type
            or "javascript" in content_type
            or "manifest" in content_type
            or "svg" in content_type
        )
        if accepts_gzip and compressible and len(body) >= 1024:
            return gzip.compress(body, compresslevel=6), True
        return body, False

    def send_redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.end_headers()

    def send_json(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        content_type = "application/json; charset=utf-8"
        body, compressed = self.encoded_body(json.dumps(data, ensure_ascii=False).encode("utf-8"), content_type)
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Noromak-Version", APP_VERSION)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        body, compressed = self.encoded_body(text.encode("utf-8"), content_type)
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Noromak-Version", APP_VERSION)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            raise ValueError("Conteúdo excede 100 MB")
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict[str, Any]:
        raw = self.read_body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Corpo JSON inválido")
        return data

    def auth(self, admin_only: bool = False) -> dict[str, Any] | None:
        ctx = session_context(self)
        if ctx is None:
            self.send_json({"error": "Faça login com um usuário do estoque.", "login_required": True}, 401)
            return None
        if admin_only and not module_allowed(ctx, "admin"):
            self.send_json({"error": "Este usuário não possui acesso ao Painel Administrativo."}, 403)
            return None
        return ctx

    def session_cookie_header(self, token: str, expire: bool = False, max_age: int | None = None) -> str:
        parts = [f"{SESSION_COOKIE}={token}", f"Path={BASE_PATH}", "HttpOnly", "SameSite=Lax"]
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        host = self.headers.get("Host", "").split(":", 1)[0].strip().lower()
        if forwarded_proto == "https" or host == "noromakweb.com.br" or host.endswith(".noromakweb.com.br"):
            parts.append("Secure")
        if expire:
            parts.extend(["Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
        elif max_age is not None and max_age > 0:
            parts.append(f"Max-Age={int(max_age)}")
        return "; ".join(parts)


    def route_path(self) -> str:
        path = unquote(urlparse(self.path).path)
        if path == BASE_PATH:
            return "/"
        if path.startswith(BASE_PATH + "/"):
            return path[len(BASE_PATH):]
        return path

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = self.route_path()
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "version": APP_VERSION,
                    "server_time": now_iso(),
                }
            )
            return
        if path in ("/api/auth/session", "/api/me"):
            ctx = session_context(self)
            self.send_json({"authenticated": bool(ctx), "user": ctx} if ctx else {"authenticated": False})
            return
        if path == "/api/integracoes/mercadolivre/callback":
            self.handle_ml_callback(query)
            return
        if path == "/api/integracoes/mercadolivre/status":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.send_json({"ok": True, "mercadolivre": ml_status_payload()})
            return
        if path == "/api/integracoes/mercadolivre/connect":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    self.send_redirect(ml_oauth_authorization_url())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 503)
            return
        if path == "/api/integracoes/mercadolivre/logs":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                limit = max(1, min(200, int((query.get("limit") or ["50"])[0] or 50)))
                with DB_LOCK:
                    rows = DB.execute(
                        "SELECT * FROM ml_event_log ORDER BY id DESC LIMIT ?", (limit,)
                    ).fetchall()
                self.send_json({"ok": True, "logs": [dict(row) for row in rows]})
            return
        if path == "/api/storage":
            ctx = self.auth()
            if ctx is not None:
                self.handle_storage_get(query)
            return
        if path == "/api/roles":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_roles_get()
            return
        if path == "/api/sku-history":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_sku_history_get(query)
            return
        if path == "/api/print/queue/status":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_status(query)
            return
        if path == "/api/print/queue/next":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_next(ctx, query)
            return
        if path == "/api/print/queue/summary":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_summary()
            return
        if path == "/api/qz/certificate":
            ctx = self.auth()
            if ctx is None:
                return
            status = qz_material_status()
            cert_path = Path(status["certificate_path"])
            if not status["certificate_exists"]:
                self.send_json({"error": status["error"] or f"Certificado QZ não encontrado em {cert_path}"}, 503)
                return
            self.send_text(cert_path.read_text(encoding="utf-8"))
            return
        if path == "/api/qz/status":
            ctx = self.auth()
            if ctx is not None:
                status = qz_material_status()
                self.send_json({"ok": bool(status["key_match"]), "qz": status, "version": APP_VERSION})
            return

        # Páginas dos módulos: além do login externo, exigem sessão e permissão granular.
        page_modules = {
            "/conferencia": "conferencia", "/conferencia/": "conferencia", "/conferencia.html": "conferencia",
            "/pedidos": "conferencia", "/pedidos/": "conferencia",
            "/admin": "admin", "/admin/": "admin", "/admin.html": "admin",
            "/administracao": "admin", "/administracao/": "admin",
            "/estacao-impressao": "estacao", "/estacao-impressao/": "estacao", "/estacao-impressao.html": "estacao",
            "/print-station": "estacao", "/print-station/": "estacao",
            "/precificador": "precificador", "/precificador/": "precificador", "/precificador.html": "precificador",
            "/precificacao": "precificador", "/precificacao/": "precificador",
        }
        required = page_modules.get(path)
        if required:
            ctx = session_context(self)
            if not ctx:
                self.send_redirect(f"{BASE_PATH}/")
                return
            if not module_allowed(ctx, required):
                self.send_json({"error": f"Acesso ao módulo {required} não autorizado para este usuário."}, 403)
                return
        self.serve_static(path)


    def do_POST(self) -> None:
        path = self.route_path()
        if path == "/api/integracoes/mercadolivre/notificacoes":
            self.handle_ml_notification()
            return
        if path == "/api/integracoes/mercadolivre/sync":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    self.send_json(ml_manual_sync())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 502)
            return
        if path == "/api/integracoes/mercadolivre/disconnect":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                with DB_LOCK:
                    DB.execute("DELETE FROM ml_integration WHERE id=1")
                    DB.execute("DELETE FROM ml_oauth_state")
                    DB.commit()
                audit(ctx["username"], "ml.disconnect", "Mercado Livre")
                self.send_json({"ok": True})
            return
        if path == "/api/auth/login":
            self.handle_auth_login()
            return
        if path == "/api/auth/logout":
            self.handle_auth_logout()
            return
        if path == "/api/storage":
            ctx = self.auth()
            if ctx is not None:
                self.handle_storage_post(ctx)
            return
        if path == "/api/order-ean":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_order_ean(ctx)
            return
        if path == "/api/product-ean":
            ctx = self.auth()
            if ctx is not None:
                self.handle_product_ean(ctx)
            return
        if path == "/api/order-lock":
            ctx = self.auth()
            if ctx is not None:
                self.handle_order_lock(ctx)
            return
        if path == "/api/order-complete":
            ctx = self.auth()
            if ctx is not None:
                self.handle_order_complete(ctx)
            return
        if path == "/api/roles":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_roles_post(ctx)
            return
        if path == "/api/print/queue/enqueue":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_enqueue(ctx)
            return
        if path == "/api/print/queue/result":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_result(ctx)
            return
        if path == "/api/print/queue/heartbeat":
            ctx = self.auth()
            if ctx is not None:
                self.handle_print_queue_heartbeat(ctx)
            return
        if path == "/api/qz/sign":
            ctx = self.auth()
            if ctx is not None:
                self.handle_qz_sign()
            return
        self.send_json({"error": "Rota não encontrada."}, 404)


    def handle_ml_callback(self, query: dict[str, list[str]]) -> None:
        code = str((query.get("code") or [""])[0] or "").strip()
        state = str((query.get("state") or [""])[0] or "").strip()
        error = str((query.get("error") or [""])[0] or "").strip()
        if error:
            self.send_redirect(f"{BASE_PATH}/admin?aba=integracoes&ml=erro&motivo={quote(error)}")
            return
        if not code or not state:
            self.send_json({"error": "Callback Mercado Livre sem code/state."}, 400)
            return
        try:
            status = ml_exchange_authorization_code(code, state)
            ml_log_event("oauth", "/callback", status.get("seller_id"), ML_CLIENT_ID, "CONECTADO", status.get("nickname") or "")
            self.send_redirect(f"{BASE_PATH}/admin?aba=integracoes&ml=conectado")
        except Exception as exc:
            ml_log_event("oauth", "/callback", "", ML_CLIENT_ID, "ERRO", str(exc))
            self.send_redirect(f"{BASE_PATH}/admin?aba=integracoes&ml=erro&motivo={quote(str(exc)[:300])}")

    def handle_ml_notification(self) -> None:
        # O Mercado Livre recomenda resposta rápida. Confirmamos o recebimento e
        # processamos a consulta do recurso em thread separada.
        try:
            body = self.read_body()
            payload = json.loads(body.decode("utf-8", errors="replace") or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        self.send_json({"ok": True})
        if payload:
            threading.Thread(target=ml_process_notification, args=(payload,), daemon=True).start()

    def do_DELETE(self) -> None:
        path = self.route_path()
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/storage":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            key = (query.get("key") or [""])[0].strip()
            if not key:
                self.send_json({"error": "Chave não informada."}, 400)
                return
            delete_storage(key)
            audit(ctx["username"], "storage.delete", key)
            self.send_json({"ok": True})
            return
        if path == "/api/roles":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_roles_delete(ctx, query)
            return
        self.send_json({"error": "Rota não encontrada."}, 404)

    def handle_auth_login(self) -> None:
        try:
            req = self.read_json()
            username = str(req.get("username") or "").strip().lower()
            password = str(req.get("password") or "")
            area = str(req.get("area") or "conferencia").strip().lower()
            if area in {"station", "impressao"}:
                area = "estacao"
            if not username or not password:
                raise ValueError("Informe usuário e senha.")
            if login_rate_limited(self, username):
                self.send_json({"error": "Muitas tentativas. Aguarde alguns minutos e tente novamente."}, 429)
                return
            with DB_LOCK:
                row = DB.execute("SELECT * FROM app_users WHERE username=?", (username,)).fetchone()
            if not row or not row["active"] or not password_matches(password, row):
                register_login_failure(self, username)
                audit(username or "desconhecido", "auth.failed", client_computer(self))
                self.send_json({"error": "Usuário ou senha incorretos."}, 401)
                return
            permissions = user_permissions_from_row(row)
            required_module = {
                "admin": "admin",
                "conferencia": "conferencia",
                "estacao": "estacao",
                "station": "estacao",
                "impressao": "estacao",
                "precificador": "precificador",
                "portal": None,
                "acesso": None,
            }.get(area, "conferencia")
            if required_module and not permissions.get(required_module):
                register_login_failure(self, username)
                self.send_json({"error": f"Este usuário não possui acesso ao módulo {required_module}."}, 403)
                return
            clear_login_failures(self, username)
            old = cookie_token(self)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            now_epoch = int(time.time())
            with DB_LOCK:
                if old:
                    DB.execute("DELETE FROM app_sessions WHERE token_hash=?", (hashlib.sha256(old.encode("utf-8")).hexdigest(),))
                DB.execute(
                    """INSERT INTO app_sessions(token_hash,username,area,created_at,last_seen,expires_at,ip,user_agent)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (token_hash, username, area, now_iso(), now_iso(), now_epoch + session_ttl_for_area(area),
                     client_computer(self), self.headers.get("User-Agent", "")[:500]),
                )
                DB.commit()
            user = {
                "username": username, "nome": row["display_name"] or username,
                "app_role": row["role"], "app_active": True, "permissions": permissions
            }
            audit(username, "auth.login", f"{area}:{client_computer(self)}")
            self.send_json(
                {"ok": True, "user": user},
                headers={"Set-Cookie": self.session_cookie_header(token, max_age=session_ttl_for_area(area))},
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"Não foi possível entrar: {exc}"}, 500)

    def handle_auth_logout(self) -> None:
        ctx = session_context(self)
        token = cookie_token(self)
        if token:
            with DB_LOCK:
                DB.execute("DELETE FROM app_sessions WHERE token_hash=?", (hashlib.sha256(token.encode("utf-8")).hexdigest(),))
                DB.commit()
        if ctx:
            audit(ctx["username"], "auth.logout", client_computer(self))
        self.send_json(
            {"ok": True},
            headers={"Set-Cookie": self.session_cookie_header("", expire=True)},
        )

    def handle_storage_get(self, query: dict[str, list[str]]) -> None:
        with DB_LOCK:
            if (query.get("list") or [""])[0] == "1":
                keys = [row[0] for row in DB.execute("SELECT key FROM storage ORDER BY key")]
                self.send_json({"keys": keys})
                return
            versions_arg = (query.get("versions") or [""])[0]
            if versions_arg:
                requested = [key.strip() for key in versions_arg.split(",") if key.strip()]
                rows = {
                    row["key"]: int(row["version"])
                    for row in DB.execute(
                        f"SELECT key,version FROM storage WHERE key IN ({','.join('?' for _ in requested)})",
                        requested,
                    ).fetchall()
                } if requested else {}
                self.send_json({"versions": {key: rows.get(key, 0) for key in requested}})
                return
            keys_arg = (query.get("keys") or [""])[0]
            if keys_arg:
                values: dict[str, Any] = {}
                versions: dict[str, int] = {}
                for key in keys_arg.split(","):
                    value, version, _ = get_storage(key)
                    values[key] = value
                    versions[key] = version
                self.send_json({"values": values, "versions": versions})
                return
            key = (query.get("key") or [""])[0]
            if not key:
                self.send_json({"error": "Chave não informada."}, 400)
                return
            value, version, updated = get_storage(key)
            self.send_json({"exists": value is not None, "value": value, "version": version, "updated_at": updated})

    def handle_sku_history_get(self, query: dict[str, list[str]]) -> None:
        def q(name: str) -> str:
            return str((query.get(name) or [""])[0]).strip()

        clauses: list[str] = []
        params: list[Any] = []
        pedido = q("pedido")
        sku = q("sku")
        usuario = q("usuario")
        tela = q("tela")
        data_inicio = q("data_inicio")
        data_fim = q("data_fim")

        if pedido:
            clauses.append("order_id LIKE ?")
            params.append(f"%{pedido}%")
        if sku:
            clauses.append("(old_sku LIKE ? OR new_sku LIKE ?)")
            params.extend([f"%{sku}%", f"%{sku}%"])
        if usuario:
            clauses.append("username LIKE ?")
            params.append(f"%{usuario}%")
        if tela:
            clauses.append("source_screen LIKE ?")
            params.append(f"%{tela}%")
        if data_inicio:
            clauses.append("changed_at >= ?")
            params.append(data_inicio + ("T00:00:00" if "T" not in data_inicio else ""))
        if data_fim:
            clauses.append("changed_at <= ?")
            params.append(data_fim + ("T23:59:59.999999" if "T" not in data_fim else ""))

        try:
            limit = max(1, min(2000, int(q("limit") or "500")))
        except ValueError:
            limit = 500
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with DB_LOCK:
            count = int(DB.execute("SELECT COUNT(*) FROM sku_change_history" + where, params).fetchone()[0])
            rows = DB.execute(
                """SELECT id,changed_at,username,user_role,source_screen,change_method,entity_type,
                          order_id,marketplace,old_sku,new_sku,description,client_ip
                   FROM sku_change_history"""
                + where + " ORDER BY id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        self.send_json({
            "ok": True,
            "total": count,
            "items": [dict(row) for row in rows],
        })

    def handle_storage_post(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            key = str(req.get("key") or "").strip()
            if not key or "newValue" not in req:
                raise ValueError("Chave ou valor inválido.")
            if ctx["app_role"] != "admin" and key not in OPERATOR_WRITE_KEYS:
                self.send_json({"error": f"Operador não pode alterar {key}."}, 403)
                return
            new_value = str(req.get("newValue"))
            old_value = None if "oldValue" not in req or req.get("oldValue") is None else str(req.get("oldValue"))
            force = bool(req.get("force"))
            if key == "conf_usuarios":
                if ctx["app_role"] != "admin":
                    self.send_json({"error": "Somente administradores podem alterar usuários."}, 403)
                    return
                new_value = apply_app_users_payload(new_value, ctx["username"])
                old_value = None
                force = True
            source_screen = str(self.headers.get("X-Expedicao-Tela") or "").strip()
            change_method = str(self.headers.get("X-Expedicao-Forma") or "").strip()
            value, version, updated = save_storage(
                key, old_value, new_value, force,
                actor=ctx["username"],
                actor_role=ctx.get("app_role", ""),
                source_screen=source_screen,
                change_method=change_method,
                client_ip=client_computer(self),
                allow_completed_reimport=(ctx["app_role"] == "admin"),
            )
            audit(ctx["username"], "storage.save", key)
            self.send_json({"ok": True, "value": value, "version": version, "updated_at": updated})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 409)

    def handle_product_ean(self, ctx: dict[str, Any]) -> None:
        """Cadastro rápido e restrito de EAN pelo SKU na tela de conferência."""
        try:
            req = self.read_json()
            sku = str(req.get("sku") or "").strip()
            ean = str(req.get("ean") or "").strip()
            ref_order = str(req.get("pedido") or "").strip()
            if not sku:
                raise ValueError("Informe o SKU do produto.")
            if not ean:
                raise ValueError("Informe o Código de Barras / EAN.")
            if len(sku) > 160 or any(ord(char) < 32 for char in sku):
                raise ValueError("SKU inválido ou muito longo.")
            if len(ean) > 128 or any(ord(char) < 32 for char in ean):
                raise ValueError("Código de barras inválido ou muito longo.")

            def parse_rows(key: str) -> tuple[list[dict[str, Any]], int]:
                value, version, _ = get_storage(key)
                try:
                    rows = json.loads(value or "[]")
                except Exception as exc:
                    raise ValueError(f"Os dados de {key} estão inválidos.") from exc
                if not isinstance(rows, list):
                    raise ValueError(f"Os dados de {key} não são uma lista.")
                return [row for row in rows if isinstance(row, dict)], version

            def write_rows(key: str, rows: list[dict[str, Any]], version: int, timestamp: str) -> int:
                next_version = version + 1
                DB.execute(
                    """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                    (key, json_dumps(rows), next_version, timestamp),
                )
                return next_version

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    products, product_version = parse_rows("conf_produtos")
                    pending, pending_version = parse_rows("conf_pedidos")
                    drafts, draft_version = parse_rows("conf_pedidos_rascunhos")
                    sku_key = normalize(sku)
                    product_matches = [product for product in products if normalize(product.get("SKU")) == sku_key]
                    order_matches = [
                        row for row in [*pending, *drafts]
                        if normalize(row.get("SKU")) == sku_key
                    ]
                    if not product_matches and not order_matches:
                        raise ValueError(f"O SKU {sku} não foi localizado no Banco de Produtos nem nos pedidos pendentes.")

                    selected_matches = [
                        row for row in order_matches
                        if ref_order and normalize(order_number(row)) == normalize(ref_order)
                    ]
                    template = (selected_matches or order_matches or product_matches)[0]
                    created = False
                    previous_eans = sorted({
                        str(product.get("Código de Barras") or "").strip()
                        for product in product_matches
                        if str(product.get("Código de Barras") or "").strip()
                    })
                    if not product_matches:
                        product = {
                            "__uidProduto": "PROD-" + secrets.token_urlsafe(12),
                            "SKU": sku,
                            "Código de Barras": ean,
                        }
                        for field in (
                            "Descrição", "Marca", "Fabricante", "Aplicação", "Categoria", "Rua", "Módulo",
                            "Prateleira", "Nível", "Posição", "Foto", "Peso", "Unidade", "Ativo",
                        ):
                            product[field] = str(template.get(field) or "")
                        products.append(product)
                        product_matches = [product]
                        created = True
                    else:
                        for product in product_matches:
                            product["Código de Barras"] = ean

                    # O cadastro rápido é global por SKU: além do Banco de Produtos,
                    # corrige todas as linhas ainda pendentes, mesmo que alguma delas
                    # já possua um EAN antigo. Pedidos concluídos ficam fora porque não
                    # pertencem mais a conf_pedidos/conf_pedidos_rascunhos.
                    pending_count = 0
                    draft_count = 0
                    pending_orders: set[str] = set()
                    matched_pending_count = 0
                    for row in pending:
                        if normalize(row.get("SKU")) != sku_key:
                            continue
                        matched_pending_count += 1
                        if str(row.get("Código de Barras") or "").strip() == ean:
                            continue
                        row["Código de Barras"] = ean
                        pending_count += 1
                        number = normalize(order_number(row))
                        if number:
                            pending_orders.add(number)
                    for row in drafts:
                        if normalize(row.get("SKU")) != sku_key:
                            continue
                        if str(row.get("Código de Barras") or "").strip() == ean:
                            continue
                        row["Código de Barras"] = ean
                        draft_count += 1

                    timestamp = now_iso()
                    versions = {
                        "conf_produtos": write_rows("conf_produtos", products, product_version, timestamp),
                    }
                    updated_keys = ["conf_produtos"]
                    if pending_count:
                        versions["conf_pedidos"] = write_rows("conf_pedidos", pending, pending_version, timestamp)
                        updated_keys.append("conf_pedidos")
                    if draft_count:
                        versions["conf_pedidos_rascunhos"] = write_rows(
                            "conf_pedidos_rascunhos", drafts, draft_version, timestamp
                        )
                        updated_keys.append("conf_pedidos_rascunhos")
                    _, marker_version, _ = get_storage("conf_banco_atualizado_em")
                    versions["conf_banco_atualizado_em"] = marker_version + 1
                    DB.execute(
                        """INSERT INTO storage(key,value,version,updated_at) VALUES('conf_banco_atualizado_em',?,?,?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                        (timestamp, marker_version + 1, timestamp),
                    )
                    updated_keys.append("conf_banco_atualizado_em")
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            audit(
                ctx["username"],
                "product.ean.quick_update",
                (
                    f"{sku}|{','.join(previous_eans) or '(vazio)'}->{ean}"
                    f"|produtos={len(product_matches)}|linhas_pendentes={pending_count}"
                    f"|pedidos_pendentes={len(pending_orders)}|rascunhos={draft_count}"
                    f"|pedido_referencia={ref_order or '-'}"
                )[:4000],
            )
            backup_daily()
            self.send_json({
                "ok": True,
                "sku": sku,
                "ean": ean,
                "created": created,
                "product_count": len(product_matches),
                "order_count": pending_count + draft_count,
                "pending_line_count": pending_count,
                "pending_order_count": len(pending_orders),
                "draft_line_count": draft_count,
                "matched_pending_line_count": matched_pending_count,
                "description": str(template.get("Descrição") or ""),
                "previous_eans": previous_eans,
                "updated_keys": updated_keys,
                "versions": versions,
            })
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 409)

    def handle_order_ean(self, ctx: dict[str, Any]) -> None:
        """Atualiza uma linha pendente e, opcionalmente, o produto em uma transação."""
        try:
            req = self.read_json()
            uid = str(req.get("uid") or "").strip()
            ean = str(req.get("ean") or "").strip()
            ref_order = str(req.get("pedido") or "").strip()
            ref_sku = str(req.get("sku") or "").strip()
            old_ean = str(req.get("oldEan") or "").strip()
            ref_description = str(req.get("descricao") or "").strip()
            update_product = bool(req.get("updateProduct"))
            if not uid:
                raise ValueError("Identificação da linha não informada.")
            if len(ean) > 128 or any(ord(char) < 32 for char in ean):
                raise ValueError("Código de barras inválido ou muito longo.")

            def parse_rows(key: str) -> tuple[list[dict[str, Any]], int]:
                value, version, _ = get_storage(key)
                try:
                    rows = json.loads(value or "[]")
                except Exception as exc:
                    raise ValueError(f"Os dados de {key} estão inválidos.") from exc
                if not isinstance(rows, list):
                    raise ValueError(f"Os dados de {key} não são uma lista.")
                return [row for row in rows if isinstance(row, dict)], version

            def valid_order_row(row: dict[str, Any]) -> bool:
                try:
                    quantity = float(str(row.get("Quantidade") or "0").replace(",", "."))
                except ValueError:
                    quantity = 0
                return bool(
                    normalize(order_number(row))
                    and (normalize(row.get("SKU")) or normalize(row.get("Código de Barras")))
                    and quantity > 0
                )

            def write_rows(key: str, rows: list[dict[str, Any]], version: int, timestamp: str) -> int:
                next_version = version + 1
                DB.execute(
                    """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                    (key, json_dumps(rows), next_version, timestamp),
                )
                return next_version

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    pending, pending_version = parse_rows("conf_pedidos")
                    drafts, draft_version = parse_rows("conf_pedidos_rascunhos")
                    locations: list[tuple[str, int, dict[str, Any]]] = []
                    for key, rows in (("conf_pedidos", pending), ("conf_pedidos_rascunhos", drafts)):
                        for index, row in enumerate(rows):
                            if str(row.get("__uidLinha") or row.get("__uidPedido") or "").strip() == uid:
                                locations.append((key, index, row))
                    if not locations and ref_order:
                        candidates: list[tuple[str, int, dict[str, Any]]] = []
                        for key, rows in (("conf_pedidos", pending), ("conf_pedidos_rascunhos", drafts)):
                            for index, row in enumerate(rows):
                                if normalize(order_number(row)) != normalize(ref_order):
                                    continue
                                if ref_sku and normalize(row.get("SKU")) != normalize(ref_sku):
                                    continue
                                if normalize(row.get("Código de Barras")) != normalize(old_ean):
                                    continue
                                candidates.append((key, index, row))
                        if len(candidates) > 1 and ref_description:
                            candidates = [item for item in candidates if normalize(item[2].get("Descrição")) == normalize(ref_description)]
                        locations = candidates
                    if len(locations) != 1:
                        if not locations:
                            raise ValueError("A linha não está mais nos pedidos pendentes. Atualize a página.")
                        raise ValueError("Há mais de uma linha compatível. Atualize a página antes de salvar.")

                    source_key, source_index, row = locations[0]
                    row["__uidLinha"] = uid
                    row["Código de Barras"] = ean
                    source_rows = pending if source_key == "conf_pedidos" else drafts
                    source_rows[source_index] = row
                    destination_key = "conf_pedidos" if valid_order_row(row) else "conf_pedidos_rascunhos"
                    if destination_key != source_key:
                        source_rows.pop(source_index)
                        (pending if destination_key == "conf_pedidos" else drafts).append(row)

                    product_count = 0
                    matched_order_line_count = 1
                    propagated_order_count = 0
                    pending_order_count = 0
                    matched_draft_line_count = 0
                    products: list[dict[str, Any]] = []
                    product_version = 0
                    sku = str(row.get("SKU") or "").strip()
                    if update_product:
                        if not sku:
                            raise ValueError("A linha não possui SKU; o Banco de Produtos não foi alterado.")
                        products, product_version = parse_rows("conf_produtos")
                        matched = [product for product in products if normalize(product.get("SKU")) == normalize(sku)]
                        if not matched:
                            raise ValueError(f"O SKU {sku} não foi localizado no Banco de Produtos.")
                        for product in matched:
                            product["Código de Barras"] = ean
                        product_count = len(matched)

                        # A ação "Banco" também é um cadastro global do SKU.
                        # Mantém todas as linhas pendentes alinhadas ao mesmo EAN.
                        sku_key = normalize(sku)
                        matched_order_line_count = 0
                        pending_orders: set[str] = set()
                        matched_draft_line_count = 0
                        for pending_row in pending:
                            if normalize(pending_row.get("SKU")) != sku_key:
                                continue
                            matched_order_line_count += 1
                            number = normalize(order_number(pending_row))
                            if number:
                                pending_orders.add(number)
                            if str(pending_row.get("Código de Barras") or "").strip() != ean:
                                pending_row["Código de Barras"] = ean
                                propagated_order_count += 1
                        for draft_row in drafts:
                            if normalize(draft_row.get("SKU")) != sku_key:
                                continue
                            matched_order_line_count += 1
                            matched_draft_line_count += 1
                            if str(draft_row.get("Código de Barras") or "").strip() != ean:
                                draft_row["Código de Barras"] = ean
                                propagated_order_count += 1
                        pending_order_count = len(pending_orders)

                    timestamp = now_iso()
                    versions = {
                        "conf_pedidos": write_rows("conf_pedidos", pending, pending_version, timestamp),
                        "conf_pedidos_rascunhos": write_rows("conf_pedidos_rascunhos", drafts, draft_version, timestamp),
                    }
                    updated_keys = ["conf_pedidos", "conf_pedidos_rascunhos"]
                    if update_product:
                        versions["conf_produtos"] = write_rows("conf_produtos", products, product_version, timestamp)
                        updated_keys.append("conf_produtos")
                    marker, marker_version, _ = get_storage("conf_banco_atualizado_em")
                    versions["conf_banco_atualizado_em"] = marker_version + 1
                    DB.execute(
                        """INSERT INTO storage(key,value,version,updated_at) VALUES('conf_banco_atualizado_em',?,?,?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                        (timestamp, marker_version + 1, timestamp),
                    )
                    updated_keys.append("conf_banco_atualizado_em")
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            audit(
                ctx["username"],
                "order.ean.update_product" if update_product else "order.ean.update",
                (
                    f"{order_number(row)}|{sku}|{old_ean}->{ean}|produtos={product_count}"
                    f"|linhas_sku={matched_order_line_count}|propagadas={propagated_order_count}"
                    f"|pedidos_pendentes={pending_order_count}|rascunhos={matched_draft_line_count}"
                )[:4000],
            )
            backup_daily()
            self.send_json({
                "ok": True,
                "uid": uid,
                "pedido": order_number(row),
                "sku": sku,
                "ean": ean,
                "updated_keys": updated_keys,
                "versions": versions,
                "product_count": product_count,
                "order_count": matched_order_line_count,
                "propagated_order_count": propagated_order_count,
                "pending_order_count": pending_order_count,
                "draft_line_count": matched_draft_line_count,
                "destination": destination_key,
            })
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 409)

    def handle_order_complete(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            pedido = str(req.get("pedido") or "").strip()
            if not pedido:
                raise ValueError("Pedido não informado.")
            key = normalize(pedido)
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    current, version, _ = get_storage("conf_pedidos")
                    rows = json.loads(current or "[]")
                    matching = [x for x in rows if isinstance(x, dict) and normalize(order_number(x)) == key]
                    remaining = [x for x in rows if not (isinstance(x, dict) and normalize(order_number(x)) == key)]
                    completed_at = now_iso()
                    completed_payload = json_dumps(matching)
                    DB.execute(
                        """INSERT INTO completed_orders(order_key,order_id,completed_at,completed_by,payload)
                           VALUES(?,?,?,?,?) ON CONFLICT(order_key) DO UPDATE SET
                           completed_at=excluded.completed_at,completed_by=excluded.completed_by,payload=excluded.payload""",
                        (key, pedido, completed_at, ctx["username"], completed_payload),
                    )
                    DB.execute(
                        """INSERT OR IGNORE INTO order_completion_history
                           (order_key,order_id,completed_at,completed_by,payload) VALUES(?,?,?,?,?)""",
                        (key, pedido, completed_at, ctx["username"], completed_payload),
                    )
                    DB.execute(
                        "UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_pedidos'",
                        (json_dumps(remaining), version + 1, now_iso()),
                    )
                    drafts, dver, _ = get_storage("conf_pedidos_rascunhos")
                    draft_rows = json.loads(drafts or "[]")
                    draft_remaining = [x for x in draft_rows if not (isinstance(x, dict) and normalize(order_number(x)) == key)]
                    DB.execute(
                        "UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_pedidos_rascunhos'",
                        (json_dumps(draft_remaining), dver + 1, now_iso()),
                    )
                    DB.execute("DELETE FROM order_locks WHERE order_key=?", (key,))
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            audit(ctx["username"], "order.complete", pedido)
            backup_daily()
            self.send_json({"ok": True, "pedido": pedido, "removed": bool(matching), "already_removed": not matching})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def handle_order_lock(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            action = str(req.get("action") or "")
            pedido = str(req.get("pedido") or "").strip()
            client_id = str(req.get("clientId") or "").strip()
            operador = str(req.get("operador") or ctx.get("nome") or ctx["username"]).strip()
            if not pedido or not client_id:
                raise ValueError("Pedido ou identificação da máquina não informado.")
            key = normalize(pedido)
            now = now_iso()
            with DB_LOCK:
                stale_cutoff = time.time() - LOCK_STALE_SECONDS
                row = DB.execute("SELECT * FROM order_locks WHERE order_key=?", (key,)).fetchone()
                if row:
                    try:
                        updated_ts = datetime.fromisoformat(row["updated_at"]).timestamp()
                    except Exception:
                        updated_ts = 0
                    if updated_ts < stale_cutoff:
                        DB.execute("DELETE FROM order_locks WHERE order_key=?", (key,))
                        DB.commit()
                        row = None
                current = dict(row) if row else None
                if action == "acquire":
                    if current and current["client_id"] != client_id:
                        self.send_json({"ok": False, "locked": True, "lock": self.lock_payload(current)}, 409)
                        return
                    started = current["started_at"] if current else now
                    DB.execute(
                        """INSERT INTO order_locks(order_key,order_id,client_id,operator,computer,started_at,updated_at)
                           VALUES(?,?,?,?,?,?,?) ON CONFLICT(order_key) DO UPDATE SET
                           client_id=excluded.client_id,operator=excluded.operator,computer=excluded.computer,updated_at=excluded.updated_at""",
                        (key, pedido, client_id, operador, client_computer(self), started, now),
                    )
                    DB.commit()
                    self.send_json({"ok": True, "lock": {"Pedido": pedido, "ClientID": client_id, "Operador": operador, "Computador": client_computer(self), "Atualizado": now, "Iniciado": started}})
                    return
                if action == "heartbeat":
                    if not current:
                        self.send_json({"ok": False, "expired": True}, 409)
                        return
                    if current["client_id"] != client_id:
                        self.send_json({"ok": False, "locked": True, "lock": self.lock_payload(current)}, 409)
                        return
                    DB.execute("UPDATE order_locks SET updated_at=? WHERE order_key=?", (now, key))
                    DB.commit()
                    self.send_json({"ok": True})
                    return
                if action == "release":
                    if not current or current["client_id"] == client_id or req.get("force") is True:
                        DB.execute("DELETE FROM order_locks WHERE order_key=?", (key,))
                        DB.commit()
                        self.send_json({"ok": True})
                        return
                    self.send_json({"ok": False, "locked": True, "lock": self.lock_payload(current)}, 409)
                    return
                if action == "status":
                    self.send_json({"ok": True, "lock": self.lock_payload(current) if current else None})
                    return
                raise ValueError("Ação inválida.")
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    @staticmethod
    def lock_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "Pedido": row["order_id"],
            "ClientID": row["client_id"],
            "Operador": row["operator"],
            "Computador": row["computer"],
            "Atualizado": row["updated_at"],
            "Iniciado": row["started_at"],
        }

    @staticmethod
    def print_job_payload(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        try:
            payload = json.loads(item.pop("payload", "[]"))
        except Exception:
            payload = []
        item["payload"] = payload if isinstance(payload, list) else []
        item["pending"] = item.get("status") in ("queued", "claimed")
        return item

    def handle_print_queue_enqueue(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            pedido = str(req.get("pedido") or "").strip()
            marketplace = str(req.get("marketplace") or "").strip()
            payload = req.get("payload")
            reprint = bool(req.get("reprint"))
            if not pedido:
                raise ValueError("Pedido não informado para impressão.")
            if not isinstance(payload, list) or not payload or not all(isinstance(x, str) and x.strip() for x in payload):
                raise ValueError("Dados ZPL inválidos ou vazios.")
            if sum(len(x.encode("utf-8")) for x in payload) > 20 * 1024 * 1024:
                raise ValueError("A etiqueta excede 20 MB.")
            order_key = normalize(pedido)
            now = now_iso()
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    current = DB.execute(
                        """SELECT * FROM print_jobs WHERE order_key=? AND status IN ('queued','claimed')
                           ORDER BY created_at DESC LIMIT 1""",
                        (order_key,),
                    ).fetchone()
                    if current:
                        DB.commit()
                        self.send_json({"ok": True, "existing": True, "job": self.print_job_payload(current)})
                        return
                    if not reprint:
                        printed = DB.execute(
                            "SELECT * FROM print_jobs WHERE order_key=? AND status='printed' ORDER BY created_at DESC LIMIT 1",
                            (order_key,),
                        ).fetchone()
                        if printed:
                            DB.commit()
                            self.send_json({"ok": True, "existing": True, "already_printed": True, "job": self.print_job_payload(printed)})
                            return
                    job_id = "PRN-" + secrets.token_urlsafe(18)
                    DB.execute(
                        """INSERT INTO print_jobs(
                               id,order_key,order_id,marketplace,payload,document_count,status,
                               created_at,created_by,updated_at
                           ) VALUES(?,?,?,?,?,?, 'queued',?,?,?)""",
                        (job_id, order_key, pedido, marketplace, json_dumps(payload), len(payload), now, ctx["username"], now),
                    )
                    row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            audit(ctx["username"], "print.enqueue", f"{pedido}:{job_id}")
            self.send_json({"ok": True, "existing": False, "job": self.print_job_payload(row)}, 201)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"Não foi possível colocar a etiqueta na fila: {exc}"}, 500)

    def handle_print_queue_status(self, query: dict[str, list[str]]) -> None:
        job_id = (query.get("id") or [""])[0].strip()
        pedido = (query.get("pedido") or [""])[0].strip()
        with DB_LOCK:
            if job_id:
                row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
            elif pedido:
                row = DB.execute(
                    "SELECT * FROM print_jobs WHERE order_key=? ORDER BY created_at DESC LIMIT 1",
                    (normalize(pedido),),
                ).fetchone()
            else:
                self.send_json({"error": "Informe o ID da impressão ou o pedido."}, 400)
                return
        if not row:
            self.send_json({"error": "Impressão não encontrada."}, 404)
            return
        payload = self.print_job_payload(row)
        if payload:
            payload.pop("payload", None)
        self.send_json({"ok": True, "job": payload, "station_online": self.any_print_station_online()})

    def touch_print_station(self, ctx: dict[str, Any], station_id: str, station_name: str, printer_name: str, error: str = "") -> None:
        if not station_id:
            return
        now_epoch = int(time.time())
        with DB_LOCK:
            DB.execute(
                """INSERT INTO print_stations(station_id,station_name,printer_name,username,last_seen_epoch,last_seen_at,last_error)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(station_id) DO UPDATE SET
                   station_name=excluded.station_name,printer_name=excluded.printer_name,username=excluded.username,
                   last_seen_epoch=excluded.last_seen_epoch,last_seen_at=excluded.last_seen_at,last_error=excluded.last_error""",
                (station_id, station_name, printer_name, ctx["username"], now_epoch, now_iso(), error[:1000]),
            )
            DB.commit()

    def any_print_station_online(self) -> bool:
        cutoff = int(time.time()) - PRINT_STATION_ONLINE_SECONDS
        with DB_LOCK:
            return bool(DB.execute("SELECT 1 FROM print_stations WHERE last_seen_epoch>=? LIMIT 1", (cutoff,)).fetchone())

    def handle_print_queue_heartbeat(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            station_id = str(req.get("station_id") or "").strip()
            if not station_id:
                raise ValueError("Identificação da estação não informada.")
            self.touch_print_station(
                ctx,
                station_id,
                str(req.get("station_name") or "Estação Zebra").strip(),
                str(req.get("printer_name") or "").strip(),
                str(req.get("error") or "").strip(),
            )
            self.send_json({"ok": True, "server_time": now_iso()})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_print_queue_next(self, ctx: dict[str, Any], query: dict[str, list[str]]) -> None:
        station_id = (query.get("station_id") or [""])[0].strip()
        station_name = (query.get("station_name") or ["Estação Zebra"])[0].strip()
        printer_name = (query.get("printer_name") or [""])[0].strip()
        try:
            wait_seconds = max(0, min(25, int((query.get("wait") or ["0"])[0] or 0)))
        except ValueError:
            wait_seconds = 0
        if not station_id:
            self.send_json({"error": "Identificação da estação não informada."}, 400)
            return

        deadline = time.monotonic() + wait_seconds
        row = None
        while True:
            self.touch_print_station(ctx, station_id, station_name, printer_name)
            now_epoch = int(time.time())
            stale_before = now_epoch - PRINT_CLAIM_STALE_SECONDS
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    DB.execute(
                        """UPDATE print_jobs SET status='queued',claimed_by='',claimed_at_epoch=0,updated_at=?,
                           error=CASE WHEN error='' THEN 'Trabalho devolvido à fila após perda de comunicação com a estação.' ELSE error END
                           WHERE status='claimed' AND claimed_at_epoch>0 AND claimed_at_epoch<?""",
                        (now_iso(), stale_before),
                    )
                    row = DB.execute(
                        "SELECT * FROM print_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()
                    if row:
                        DB.execute(
                            """UPDATE print_jobs SET status='claimed',claimed_by=?,claimed_at_epoch=?,updated_at=?,attempts=attempts+1,error=''
                               WHERE id=? AND status='queued'""",
                            (station_id, now_epoch, now_iso(), row["id"]),
                        )
                        row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (row["id"],)).fetchone()
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            if row or time.monotonic() >= deadline:
                break
            time.sleep(0.8)

        self.send_json({"ok": True, "job": self.print_job_payload(row), "server_time": now_iso()})

    def handle_print_queue_result(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            job_id = str(req.get("id") or "").strip()
            station_id = str(req.get("station_id") or "").strip()
            success = bool(req.get("success"))
            error = str(req.get("error") or "").strip()
            printer_name = str(req.get("printer_name") or "").strip()
            if not job_id or not station_id:
                raise ValueError("Impressão ou estação não informada.")
            self.touch_print_station(ctx, station_id, str(req.get("station_name") or "Estação Zebra"), printer_name, "" if success else error)
            status = "printed" if success else "failed"
            now = now_iso()
            with DB_LOCK:
                row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    self.send_json({"error": "Impressão não encontrada."}, 404)
                    return
                if row["status"] == "printed":
                    self.send_json({"ok": True, "already_printed": True, "job": self.print_job_payload(row)})
                    return
                DB.execute(
                    """UPDATE print_jobs SET status=?,updated_at=?,printed_at=?,error=?,claimed_by=?,claimed_at_epoch=? WHERE id=?""",
                    (status, now, now if success else "", error[:2000], station_id, int(time.time()), job_id),
                )
                row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
                DB.commit()
            audit(ctx["username"], "print.result", f"{job_id}:{status}:{station_id}")
            self.send_json({"ok": True, "job": self.print_job_payload(row)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"Não foi possível confirmar a impressão: {exc}"}, 500)

    def handle_print_queue_summary(self) -> None:
        cutoff = int(time.time()) - PRINT_STATION_ONLINE_SECONDS
        with DB_LOCK:
            counts = {row["status"]: row["qtd"] for row in DB.execute("SELECT status,COUNT(*) qtd FROM print_jobs GROUP BY status")}
            stations = [dict(row) for row in DB.execute(
                "SELECT station_id,station_name,printer_name,username,last_seen_epoch,last_seen_at,last_error FROM print_stations ORDER BY last_seen_epoch DESC LIMIT 10"
            )]
        for station in stations:
            station["online"] = int(station.get("last_seen_epoch") or 0) >= cutoff
        self.send_json({"ok": True, "counts": counts, "stations": stations, "server_time": now_iso()})

    def handle_roles_get(self) -> None:
        with DB_LOCK:
            rows = DB.execute(
                """SELECT username,display_name,role,active,can_conferencia,can_admin,can_estacao,can_precificador,
                          updated_at,updated_by FROM app_users ORDER BY display_name,username"""
            ).fetchall()
        self.send_json({"users": [dict(row) for row in rows], "seed_admin": ADMIN_USERNAME, "login_interno": True})

    def handle_roles_post(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            username = str(req.get("username") or "").strip().lower()
            display_name = str(req.get("display_name") or username).strip()
            role = str(req.get("role") or "operador").strip().lower()
            active = req.get("active", True) not in (False, 0, "0", "false")
            password = str(req.get("password") or "").strip() or None
            raw_permissions = req.get("permissions") if isinstance(req.get("permissions"), dict) else {}
            permissions = {
                "conferencia": bool(raw_permissions.get("conferencia", role != "admin")),
                "admin": bool(raw_permissions.get("admin", role == "admin")),
                "estacao": bool(raw_permissions.get("estacao", role == "admin")),
                "precificador": bool(raw_permissions.get("precificador", role == "admin")),
            }
            if not username or any(ch.isspace() for ch in username):
                raise ValueError("Informe o login do estoque sem espaços.")
            if role not in ("admin", "operador"):
                raise ValueError("Perfil inválido.")
            current = DB.execute("SELECT * FROM app_users WHERE username=?", (username,)).fetchone()
            if not current and not password:
                raise ValueError("Informe uma senha para o novo usuário.")
            if username == ctx["username"] and (not active or not permissions["admin"]):
                raise ValueError("Você não pode retirar seu próprio acesso ao Painel Administrativo.")
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    upsert_app_user(username, display_name, role, active, password, ctx["username"], permissions=permissions)
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise
            sync_app_users_storage()
            audit(ctx["username"], "user.save", f"{username}:{role}:{int(active)}")
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


    def handle_roles_delete(self, ctx: dict[str, Any], query: dict[str, list[str]]) -> None:
        username = (query.get("username") or [""])[0].strip().lower()
        if not username:
            self.send_json({"error": "Username não informado."}, 400)
            return
        if username == ctx["username"]:
            self.send_json({"error": "Você não pode remover seu próprio acesso."}, 400)
            return
        with DB_LOCK:
            target = DB.execute("SELECT role,active FROM app_users WHERE username=?", (username,)).fetchone()
            if target and target["role"] == "admin" and target["active"]:
                admins = DB.execute("SELECT COUNT(*) FROM app_users WHERE role='admin' AND active=1").fetchone()[0]
                if admins <= 1:
                    self.send_json({"error": "Não é possível remover o último administrador ativo."}, 400)
                    return
            DB.execute("DELETE FROM app_users WHERE username=?", (username,))
            DB.commit()
        sync_app_users_storage()
        audit(ctx["username"], "role.delete", username)
        self.send_json({"ok": True})

    def handle_qz_sign(self) -> None:
        try:
            payload = self.read_body()
            if not payload:
                raise ValueError("Solicitação de assinatura vazia.")
            status = qz_material_status()
            if not status["private_key_exists"]:
                self.send_json({"error": f"Chave privada QZ não instalada em {QZ_PRIVATE_KEY_PATH}."}, 503)
                return
            if not status["key_match"]:
                self.send_json({"error": status["error"] or "Certificado e chave privada QZ não correspondem."}, 503)
                return
            proc = subprocess.run(
                ["openssl", "dgst", "-sha512", "-sign", str(QZ_PRIVATE_KEY_PATH)],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "Falha no OpenSSL")
            self.send_text(base64.b64encode(proc.stdout).decode("ascii"))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except FileNotFoundError:
            self.send_json({"error": "OpenSSL não está instalado no servidor."}, 503)
        except Exception as exc:
            self.send_json({"error": f"Falha ao assinar solicitação QZ: {exc}"}, 500)

    def serve_static(self, path: str) -> None:
        routes = {
            "": "acesso.html",
            "/": "acesso.html",
            "/acesso": "acesso.html",
            "/acesso/": "acesso.html",
            "/acesso.html": "acesso.html",
            "/conferencia": "conferencia.html",
            "/conferencia/": "conferencia.html",
            "/conferencia.html": "conferencia.html",
            "/pedidos": "conferencia.html",
            "/pedidos/": "conferencia.html",
            "/admin": "admin.html",
            "/admin/": "admin.html",
            "/admin.html": "admin.html",
            "/administracao": "admin.html",
            "/administracao/": "admin.html",
            "/estacao-impressao": "estacao-impressao.html",
            "/estacao-impressao/": "estacao-impressao.html",
            "/estacao-impressao.html": "estacao-impressao.html",
            "/print-station": "estacao-impressao.html",
            "/print-station/": "estacao-impressao.html",
            "/precificador": "precificador.html",
            "/precificador/": "precificador.html",
            "/precificador.html": "precificador.html",
            "/precificacao": "precificador.html",
            "/precificacao/": "precificador.html",
            "/shared-storage.js": "shared-storage.js",
            "/web-auth.js": "web-auth.js",
        }
        filename = routes.get(path, path.lstrip("/"))
        safe = Path(filename)
        if ".." in safe.parts:
            self.send_json({"error": "Acesso negado."}, 403)
            return
        full = APP_DIR / safe
        if not full.is_file():
            self.send_json({"error": "Arquivo não encontrado."}, 404)
            return
        body = full.read_bytes()
        if full.suffix == ".webmanifest":
            content_type = "application/manifest+json; charset=utf-8"
        else:
            content_type = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or full.suffix in (".js", ".json"):
                content_type += "; charset=utf-8"
        body, compressed = self.encoded_body(body, content_type)
        self.send_response(200)
        self.send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if full.name == "sw.js":
            self.send_header("Service-Worker-Allowed", BASE_PATH + "/")
        self.send_header("X-Noromak-Version", APP_VERSION)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main() -> None:
    init_db()
    backup_daily()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    server.daemon_threads = True
    print(f"Sistema de Conferência Web {APP_VERSION} em http://{HOST}:{PORT}{BASE_PATH}", flush=True)
    print(f"Banco SQLite: {DB_PATH}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with DB_LOCK:
            DB.close()


if __name__ == "__main__":
    main()
