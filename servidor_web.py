#!/usr/bin/env python3
"""Sistema de Conferência Noromak — edição Web/Linux 8.8.

Sem dependências externas: usa apenas a biblioteca padrão do Python.
- Escuta em 127.0.0.1:8100.
- Considera o prefixo /expedicao (o nginx não remove o prefixo).
- O nginx mantém o login do NoromakWeb e o app exige um segundo login próprio do estoque.
- Persiste em SQLite fora da pasta do código.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
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
from urllib.parse import parse_qs, unquote, urlparse

APP_VERSION = "8.8"
APP_MARKER = "noromak_expedicao_web"
BASE_PATH = "/expedicao"
HOST = os.getenv("EXP_HOST", "127.0.0.1")
PORT = int(os.getenv("EXP_PORT", "8100"))
ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "APP"
DB_PATH = Path(os.getenv("EXP_DB_PATH", "/home/expedicao/dados/expedicao.db"))
BACKUP_DIR = Path(os.getenv("EXP_BACKUP_DIR", "/home/expedicao/backups"))
AUTH_ME_URL = os.getenv("EXP_AUTH_ME_URL", "http://127.0.0.1:8000/api/v1/auth/me")
ADMIN_USERNAME = os.getenv("EXP_ADMIN_USUARIO", "luis").strip().lower()
QZ_CERT_PATH = Path(os.getenv("QZ_CERT_PATH", str(ROOT_DIR / "QZ_CERTIFICADO_WEB" / "digital-certificate.txt")))
QZ_PRIVATE_KEY_PATH = Path(os.getenv("QZ_PRIVATE_KEY_PATH", "/home/expedicao/qz/qz-private-key.pem"))
MIGRATION_DIR = Path(os.getenv("EXP_MIGRATION_DIR", str(ROOT_DIR / "MIGRACAO_INICIAL")))
MAX_BODY = 100 * 1024 * 1024
LOCK_STALE_SECONDS = 5 * 60
AUTH_CACHE_SECONDS = 180
SESSION_COOKIE = "expedicao_estoque"
SESSION_TTL_SECONDS = int(os.getenv("EXP_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
PASSWORD_ITERATIONS = int(os.getenv("EXP_PASSWORD_ITERATIONS", "240000"))
PASSWORD_MASK = "********"
INITIAL_ADMIN_PASSWORD = os.getenv("EXP_ADMIN_SENHA", "01101998")
INITIAL_OPERATOR_PASSWORD = os.getenv("EXP_USUARIO_SENHA_INICIAL", "123")

DB_LOCK = threading.RLock()
AUTH_LOCK = threading.Lock()
AUTH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
LOGIN_ATTEMPTS: dict[str, list[float]] = {}

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
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
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
    migrate_legacy_if_empty()
    ensure_storage_defaults()
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


def upsert_app_user(
    username: str,
    display_name: str,
    role: str,
    active: bool,
    password: str | None,
    updated_by: str,
    only_if_missing: bool = False,
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
        fallback = INITIAL_ADMIN_PASSWORD if role == "admin" else INITIAL_OPERATOR_PASSWORD
        pwd_hash, pwd_salt, iterations = password_digest(fallback)
    DB.execute(
        """INSERT INTO app_users(username,display_name,password_hash,password_salt,password_iterations,role,active,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET
           display_name=excluded.display_name,password_hash=excluded.password_hash,password_salt=excluded.password_salt,
           password_iterations=excluded.password_iterations,role=excluded.role,active=excluded.active,
           updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
        (username, display_name.strip() or username, pwd_hash, pwd_salt, iterations, role, 1 if active else 0, now_iso(), updated_by),
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
            # Compatibilidade com o administrador principal da versão portátil.
            upsert_app_user("admin", "Administrador principal", "admin", True, INITIAL_ADMIN_PASSWORD, "migração 7.5", only_if_missing=True)
            if ADMIN_USERNAME:
                upsert_app_user(ADMIN_USERNAME, ADMIN_USERNAME.title(), "admin", True, INITIAL_ADMIN_PASSWORD, "EXP_ADMIN_USUARIO", only_if_missing=True)

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
                initial = INITIAL_ADMIN_PASSWORD if row["role"] == "admin" else INITIAL_OPERATOR_PASSWORD
                upsert_app_user(row["username"], row["display_name"] or row["username"], row["role"], bool(row["active"]), initial, "migração 7.3", only_if_missing=True)
            DB.commit()
        except Exception:
            DB.rollback()
            raise


def app_users_payload() -> list[dict[str, Any]]:
    rows = DB.execute(
        "SELECT username,display_name,role,active,updated_at,updated_by FROM app_users ORDER BY display_name,username"
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
            "AtualizadoEm": row["updated_at"],
            "AtualizadoPor": row["updated_by"],
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
            prepared.append({"username": username, "display_name": display_name, "role": role, "active": active, "password": password or None})

        actor_row = existing.get(actor)
        actor_new = next((row for row in prepared if row["username"] == actor), None)
        if actor_row and actor_row["role"] == "admin" and actor_row["active"]:
            if not actor_new or actor_new["role"] != "admin" or not actor_new["active"]:
                raise ValueError("Você não pode remover ou rebaixar o administrador que está usando esta sessão.")
        if not any(row["role"] == "admin" and row["active"] for row in prepared):
            raise ValueError("É necessário manter pelo menos um administrador ativo.")

        DB.execute("BEGIN IMMEDIATE")
        try:
            for row in prepared:
                upsert_app_user(
                    row["username"], row["display_name"], row["role"], row["active"], row["password"], actor
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
    return value, int(row["version"]), row["updated_at"]


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


def merge_value(key: str, current: str | None, old: str | None, newer: str, force: bool) -> str:
    if force or current is None or current == old:
        return newer
    try:
        current_arr = json.loads(current)
        old_arr = json.loads(old or "")
        new_arr = json.loads(newer)
        if all(isinstance(x, list) for x in (current_arr, old_arr, new_arr)):
            return json_dumps(merge_arrays(key, current_arr, old_arr, new_arr))
    except Exception:
        pass
    return newer


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


def save_storage(key: str, old_value: str | None, new_value: str, force: bool) -> tuple[str, int, str]:
    if len(new_value.encode("utf-8")) > MAX_BODY:
        raise ValueError("Conteúdo excede 100 MB")
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            current, version, _ = get_storage(key)
            merged = merge_value(key, current, old_value, new_value, force)
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                merged = filter_completed_orders(merged)
            version += 1
            updated = now_iso()
            DB.execute(
                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                (key, merged, version, updated),
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
            """SELECT s.token_hash,s.username,s.expires_at,u.display_name,u.role,u.active
               FROM app_sessions s JOIN app_users u ON u.username=s.username
               WHERE s.token_hash=?""",
            (token_hash,),
        ).fetchone()
        if not row or not row["active"] or int(row["expires_at"]) <= now_epoch:
            DB.execute("DELETE FROM app_sessions WHERE token_hash=?", (token_hash,))
            DB.commit()
            return None
        DB.execute(
            "UPDATE app_sessions SET last_seen=?,expires_at=? WHERE token_hash=?",
            (now_iso(), now_epoch + SESSION_TTL_SECONDS, token_hash),
        )
        DB.commit()
    return {
        "username": row["username"],
        "nome": row["display_name"] or row["username"],
        "app_role": row["role"],
        "app_active": True,
    }


def client_computer(handler: BaseHTTPRequestHandler) -> str:
    forwarded = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or handler.client_address[0]


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


class AppHandler(BaseHTTPRequestHandler):
    server_version = "NoromakExpedicao/8.8"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Noromak-Version", APP_VERSION)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        if admin_only and ctx.get("app_role") != "admin":
            self.send_json({"error": "Acesso exclusivo de administrador."}, 403)
            return None
        return ctx

    def session_cookie_header(self, token: str, expire: bool = False) -> str:
        parts = [f"{SESSION_COOKIE}={token}", f"Path={BASE_PATH}", "HttpOnly", "SameSite=Lax"]
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
        if forwarded_proto == "https" or self.headers.get("Host", "").startswith("noromakweb.com.br"):
            parts.append("Secure")
        if expire:
            parts.extend(["Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
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
                    "marker": APP_MARKER,
                    "version": APP_VERSION,
                    "base_path": BASE_PATH,
                    "database": str(DB_PATH),
                    "server_time": now_iso(),
                }
            )
            return
        if path in ("/api/auth/session", "/api/me"):
            ctx = session_context(self)
            self.send_json({"authenticated": bool(ctx), "user": ctx} if ctx else {"authenticated": False})
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
        if path == "/api/qz/certificate":
            ctx = self.auth()
            if ctx is None:
                return
            if not QZ_CERT_PATH.exists():
                self.send_json({"error": f"Certificado QZ não encontrado em {QZ_CERT_PATH}"}, 503)
                return
            self.send_text(QZ_CERT_PATH.read_text(encoding="utf-8"))
            return

        # O nginx já exige o login do NoromakWeb. Estas páginas ficam públicas
        # dentro de /expedicao para conseguirem mostrar o segundo login, próprio do estoque.
        self.serve_static(path)


    def do_POST(self) -> None:
        path = self.route_path()
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
        if path == "/api/qz/sign":
            ctx = self.auth()
            if ctx is not None:
                self.handle_qz_sign()
            return
        self.send_json({"error": "Rota não encontrada."}, 404)


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
            if area == "admin" and row["role"] != "admin":
                register_login_failure(self, username)
                self.send_json({"error": "Este usuário não possui perfil de Administrador."}, 403)
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
                    """INSERT INTO app_sessions(token_hash,username,created_at,last_seen,expires_at,ip,user_agent)
                       VALUES(?,?,?,?,?,?,?)""",
                    (token_hash, username, now_iso(), now_iso(), now_epoch + SESSION_TTL_SECONDS,
                     client_computer(self), self.headers.get("User-Agent", "")[:500]),
                )
                DB.commit()
            user = {"username": username, "nome": row["display_name"] or username, "app_role": row["role"], "app_active": True}
            audit(username, "auth.login", f"{area}:{client_computer(self)}")
            self.send_json(
                {"ok": True, "user": user},
                headers={"Set-Cookie": self.session_cookie_header(token)},
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
            value, version, updated = save_storage(key, old_value, new_value, force)
            audit(ctx["username"], "storage.save", key)
            self.send_json({"ok": True, "value": value, "version": version, "updated_at": updated})
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
                    DB.execute(
                        """INSERT INTO completed_orders(order_key,order_id,completed_at,completed_by,payload)
                           VALUES(?,?,?,?,?) ON CONFLICT(order_key) DO UPDATE SET
                           completed_at=excluded.completed_at,completed_by=excluded.completed_by,payload=excluded.payload""",
                        (key, pedido, now_iso(), ctx["username"], json_dumps(matching)),
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

    def handle_roles_get(self) -> None:
        with DB_LOCK:
            rows = DB.execute(
                "SELECT username,display_name,role,active,updated_at,updated_by FROM app_users ORDER BY display_name,username"
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
            if not username or any(ch.isspace() for ch in username):
                raise ValueError("Informe o login do estoque sem espaços.")
            if role not in ("admin", "operador"):
                raise ValueError("Perfil inválido.")
            current = DB.execute("SELECT * FROM app_users WHERE username=?", (username,)).fetchone()
            if not current and not password:
                raise ValueError("Informe uma senha para o novo usuário.")
            if username == ctx["username"] and (role != "admin" or not active):
                raise ValueError("Você não pode retirar sua própria permissão de administrador.")
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    upsert_app_user(username, display_name, role, active, password, ctx["username"])
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
            if not QZ_PRIVATE_KEY_PATH.exists():
                self.send_json({"error": f"Chave privada QZ não instalada em {QZ_PRIVATE_KEY_PATH}. Execute instalar_servidor_uma_vez.sh."}, 503)
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
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
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
