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
import io
import gzip
import hashlib
import html as html_module
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import unicodedata
import zipfile
from datetime import datetime, timezone, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

# V18.65: a fonte do tema (APP/fonts/*.woff2) fica no próprio servidor — o CSP só
# aceita font-src 'self'. Nem todo sistema conhece o tipo do .woff2.
mimetypes.add_type("font/woff2", ".woff2")

APP_VERSION = "18.66-VISUAL-E-KITS"
APP_MARKER = "noromak_expedicao_web"
BASE_PATH = "/expedicao"
HOST = os.getenv("EXP_HOST", "127.0.0.1")
PORT = int(os.getenv("EXP_PORT", "8100"))
# V18.63: "0" desliga TODOS os workers (ML, Shopee, vendas, Ads, Linx, relatório,
# manutenção e backup do boot). Só para teste de boot e homologação — nunca em produção.
WORKERS_ENABLED = os.getenv("EXP_WORKERS", "1").strip().lower() not in {"0", "false", "nao", "não"}
ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "APP"
DB_PATH = Path(os.getenv("EXP_DB_PATH", "/home/expedicao/dados/expedicao.db"))
BACKUP_DIR = Path(os.getenv("EXP_BACKUP_DIR", "/home/expedicao/backups"))
ADMIN_USERNAME = os.getenv("EXP_ADMIN_USUARIO", "luis").strip().lower()
OWNER_USERNAME = os.getenv("EXP_OWNER_USUARIO", ADMIN_USERNAME).strip().lower()
OWNER_TOTP_SECRET = os.getenv("EXP_OWNER_TOTP_SECRET", "").replace(" ", "").strip().upper()
OWNER_RECOVERY_HASHES = {x.strip().lower() for x in os.getenv("EXP_OWNER_RECOVERY_HASHES", "").split(",") if x.strip()}
CRITICAL_REAUTH_TTL_SECONDS = int(os.getenv("EXP_CRITICAL_REAUTH_TTL_SECONDS", "300"))
SECURITY_HEADERS_ENABLED = os.getenv("EXP_SECURITY_HEADERS", "1").strip().lower() not in {"0","false","nao","não"}
QZ_BUNDLED_CERT_PATH = ROOT_DIR / "QZ_CERTIFICADO_WEB" / "digital-certificate.txt"
QZ_CONFIGURED_CERT_PATH = Path(os.getenv("QZ_CERT_PATH", str(QZ_BUNDLED_CERT_PATH)))
QZ_PRIVATE_KEY_PATH = Path(os.getenv("QZ_PRIVATE_KEY_PATH", "/home/expedicao/qz/qz-private-key.pem"))
MIGRATION_DIR = Path(os.getenv("EXP_MIGRATION_DIR", str(ROOT_DIR / "MIGRACAO_INICIAL")))
MAX_BODY = 100 * 1024 * 1024
LOCK_STALE_SECONDS = 90  # V16.72: 3 heartbeats perdidos liberam bloqueio órfão
PRINT_CLAIM_STALE_SECONDS = int(os.getenv("EXP_PRINT_CLAIM_STALE_SECONDS", "120"))
PRINT_STATION_ONLINE_SECONDS = int(os.getenv("EXP_PRINT_STATION_ONLINE_SECONDS", "20"))
SESSION_COOKIE = "expedicao_estoque"
# V18.52: login único com o NoromakWeb (contrato de 23/09/2026). O portão do
# nginx manda X-NoromakWeb-Usuario + X-NoromakWeb-Segredo. Vazio = desligado.
NOROMAKWEB_SSO_SECRET = os.getenv("NOROMAKWEB_SSO_SECRET", "").strip()
NOROMAKWEB_SSO_MIN_SECRET_LEN = 32
SESSION_TTL_SECONDS = int(os.getenv("EXP_SESSION_TTL_SECONDS", str(4 * 60 * 60)))
STATION_SESSION_TTL_SECONDS = int(os.getenv("EXP_STATION_SESSION_TTL_SECONDS", str(180 * 24 * 60 * 60)))
SUPERVISOR_SESSION_TTL_SECONDS = int(os.getenv("EXP_SUPERVISOR_SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60)))
PASSWORD_ITERATIONS = int(os.getenv("EXP_PASSWORD_ITERATIONS", "600000"))
PASSWORD_MASK = "********"
INITIAL_ADMIN_PASSWORD = os.getenv("EXP_ADMIN_SENHA", "").strip()
INITIAL_OPERATOR_PASSWORD = os.getenv("EXP_USUARIO_SENHA_INICIAL", "").strip()
INITIAL_CREDENTIALS_PATH = Path(
    os.getenv("EXP_CREDENCIAIS_INICIAIS_PATH", str(DB_PATH.parent / "CREDENCIAIS_INICIAIS.txt"))
)

# Shopee Open Platform — base preparada para produção.
# Partner Key e tokens ficam somente no servidor; nunca são enviados ao navegador.
SHOPEE_PARTNER_ID = os.getenv("SHOPEE_PARTNER_ID", "").strip()
SHOPEE_PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY", "").strip()
SHOPEE_REDIRECT_URI = os.getenv(
    "SHOPEE_REDIRECT_URI",
    "https://noromakweb.com.br/expedicao/api/integracoes/shopee/callback",
).strip()
# Shopee Open Platform V2 — produção.
SHOPEE_API_BASE = os.getenv("SHOPEE_API_BASE", "https://partner.shopeemobile.com").strip().rstrip("/")
SHOPEE_AUTH_PATH = os.getenv("SHOPEE_AUTH_PATH", "/api/v2/shop/auth_partner").strip() or "/api/v2/shop/auth_partner"
SHOPEE_TOKEN_PATH = os.getenv("SHOPEE_TOKEN_PATH", "/api/v2/auth/token/get").strip() or "/api/v2/auth/token/get"
SHOPEE_REFRESH_PATH = os.getenv("SHOPEE_REFRESH_PATH", "/api/v2/auth/access_token/get").strip() or "/api/v2/auth/access_token/get"
SHOPEE_AUTH_URL = os.getenv("SHOPEE_AUTH_URL", "").strip()

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
ML_OAUTH_SCOPE = os.getenv("ML_OAUTH_SCOPE", "offline_access read write").strip()
ML_HTTP_TIMEOUT = int(os.getenv("ML_HTTP_TIMEOUT", "20"))
ML_MANUAL_SYNC_LIMIT = max(1, min(50, int(os.getenv("ML_MANUAL_SYNC_LIMIT", "20"))))
ML_PENDING_WATCH_INTERVAL = max(10, min(120, int(os.getenv("ML_PENDING_WATCH_INTERVAL", "15"))))
ML_PENDING_WATCH_BATCH = max(1, min(30, int(os.getenv("ML_PENDING_WATCH_BATCH", "20"))))
ML_NEW_ORDER_WATCH_INTERVAL = max(15, min(120, int(os.getenv("ML_NEW_ORDER_WATCH_INTERVAL", "20"))))
ML_NEW_ORDER_WATCH_LIMIT = max(5, min(50, int(os.getenv("ML_NEW_ORDER_WATCH_LIMIT", "20"))))
ML_CATCHUP_FALLBACK_DAYS = max(1, min(30, int(os.getenv("ML_CATCHUP_FALLBACK_DAYS", "7"))))
ML_CATCHUP_PAGE_SIZE = max(10, min(50, int(os.getenv("ML_CATCHUP_PAGE_SIZE", "50"))))
ML_CATCHUP_MAX_PAGES = max(2, min(100, int(os.getenv("ML_CATCHUP_MAX_PAGES", "40"))))
ML_MANUAL_RECONCILE_INTERVAL = max(30, min(600, int(os.getenv("ML_MANUAL_RECONCILE_INTERVAL", "60"))))
ML_MANUAL_RECONCILE_DAYS = max(1, min(90, int(os.getenv("ML_MANUAL_RECONCILE_DAYS", "30"))))
# V16.0: rotinas 100% server-side. Não dependem de navegador, sessão web ou PC da expedição.
ML_TOKEN_KEEPALIVE_INTERVAL = max(60, min(1800, int(os.getenv("ML_TOKEN_KEEPALIVE_INTERVAL", "300"))))
ML_TOKEN_REFRESH_AHEAD_SECONDS = max(300, min(7200, int(os.getenv("ML_TOKEN_REFRESH_AHEAD_SECONDS", "1800"))))
ML_SAFETY_CATCHUP_INTERVAL = max(300, min(7200, int(os.getenv("ML_SAFETY_CATCHUP_INTERVAL", "900"))))
ML_SAFETY_CATCHUP_HOURS = max(6, min(168, int(os.getenv("ML_SAFETY_CATCHUP_HOURS", "36"))))


CRM_COST_API_URL = os.getenv(
    "CRM_COST_API_URL",
    "http://127.0.0.1:8000/api/v1/expedicao/custo",
).strip()
CHAVE_CRM = os.getenv("CHAVE_CRM", "").strip()
CRM_COST_TIMEOUT = max(3, min(30, int(os.getenv("CRM_COST_TIMEOUT", "20"))))
def _crm_stock_url_padrao() -> str:
    """V17.94: a rota de estoque é /api/v1/expedicao/estoque, irmã de /custo.

    Antes o padrão caía na própria rota de custo, que não devolve estoque.
    Uma CRM_STOCK_API_URL explícita e diferente da de custo continua valendo.
    """
    explicita = os.getenv("CRM_STOCK_API_URL", "").strip()
    if explicita and explicita.rstrip("/") != CRM_COST_API_URL.rstrip("/"):
        return explicita
    base = CRM_COST_API_URL.rstrip("/")
    if base.endswith("/custo"):
        return base[: -len("/custo")] + "/estoque"
    return "http://127.0.0.1:8000/api/v1/expedicao/estoque"


CRM_STOCK_API_URL = _crm_stock_url_padrao()
CRM_STOCK_TIMEOUT = max(3, min(30, int(os.getenv("CRM_STOCK_TIMEOUT", str(CRM_COST_TIMEOUT)))))
CRM_COST_CACHE_SECONDS = max(30, min(600, int(os.getenv("CRM_COST_CACHE_SECONDS", "180"))))
# V17.94: /custo e /estoque dividem o MESMO limite no Apollo (20 chamadas/min).
# Ficamos um pouco abaixo para não esbarrar no 429 por diferença de relógio.
CRM_RATE_LIMIT_PER_MINUTE = max(1, min(20, int(os.getenv("CRM_RATE_LIMIT_PER_MINUTE", "18"))))


DB_LOCK = threading.RLock()
AUTH_LOCK = threading.Lock()
# V17.12: estado efêmero de segurança. Nunca é persistido em disco.
# A liberação crítica desaparece ao reiniciar o serviço, por desenho.
CRITICAL_REAUTH: dict[str, float] = {}
OWNER_REAUTH_FAILURES: dict[str, list[float]] = {}
ML_NEW_ORDER_WATCH_LOCK = threading.Lock()
ML_CATCHUP_LOCK = threading.Lock()
ML_MANUAL_RECONCILE_LOCK = threading.Lock()
ML_PENDING_WATCH_CURSOR_LOCK = threading.Lock()
ML_PENDING_WATCH_CURSOR = 0
ML_WORKER_STATE_LOCK = threading.Lock()
ML_WORKER_STATE: dict[str, Any] = {
    "started_at": "",
    "last_order_poll_at": "",
    "last_order_poll_result": {},
    "last_token_check_at": "",
    "last_token_refresh_at": "",
    "last_catchup_at": "",
    "last_catchup_result": {},
    "last_error": "",
}
CRM_COST_CACHE_LOCK = threading.Lock()
CRM_COST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CRM_STOCK_CACHE_LOCK = threading.Lock()
CRM_STOCK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CRM_RATE_LOCK = threading.Lock()
CRM_RATE_CALLS: list[float] = []
PRECIFICADOR_BULK_LOCK = threading.Lock()
# V17.27: etiquetas Shopee rodam fora da requisição HTTP para evitar 504 do Nginx.
SHOPEE_LABEL_WORKER_LOCK = threading.Lock()
SHOPEE_LABEL_WAKE_EVENT = threading.Event()
SHOPEE_LABEL_WORKER_INTERVAL = max(60, min(900, int(os.getenv("SHOPEE_LABEL_WORKER_INTERVAL", "180"))))
# V18.62: com pedido no meio do fluxo (rastreio ou documento ainda saindo) o worker
# volta em 30 s em vez de 3 min — no máximo 8 vezes seguidas sem progresso, para um
# pedido que nunca anda (ex.: sem NF-e) não prender o ritmo rápido.
SHOPEE_LABEL_FOLLOWUP_INTERVAL = max(10, min(120, int(os.getenv("SHOPEE_LABEL_FOLLOWUP_INTERVAL", "30"))))
SHOPEE_LABEL_FOLLOWUP_MAX = 8
# V18.62: espera curta antes de reconsultar rastreio/documento no mesmo ciclo.
SHOPEE_DOC_RESULT_RETRY_WAIT = 3.0
# V17.31: evita repetir ship_order a cada ciclo depois que a Shopee já confirmou o arrange.
SHOPEE_SHIP_ORDER_CACHE_LOCK = threading.Lock()
SHOPEE_SHIP_ORDER_CACHE: dict[tuple[str, str], float] = {}
SHOPEE_SHIP_ORDER_UNSPLIT_CACHE: set[tuple[str, str]] = set()
SHOPEE_SHIP_ORDER_CACHE_TTL = max(300, min(21600, int(os.getenv("SHOPEE_SHIP_ORDER_CACHE_TTL", "7200"))))

PRECIFICADOR_BULK_STATUS_LOCK = threading.Lock()
PRECIFICADOR_BULK_STATUS: dict[str, Any] = {
    "running": False, "total": 0, "processed": 0, "updated": 0,
    "cost_changes": 0, "descriptions": 0, "curves": 0,
    "kits": 0, "errors": 0, "current_sku": "",
    "started_at": "", "finished_at": "", "message": "",
}
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
GENERATED_INITIAL_CREDENTIALS: dict[str, str] = {}

KNOWN_KEYS = [
    "conf_pedidos",
    "conf_pedidos_rascunhos",
    "conf_produtos",
    "conf_kits",
    "conf_usuarios",
    "conf_solicitacoes_usuarios",
    "conf_etiquetas",
    "conf_fotos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em",
    "precificador_online_v1",
    "precificador_alertas_custo_v1",
    "ml_sku_mlb_links_v1",
    "conf_coleta_dia_v1",
    "conf_nomes_conferencia_v1",
    "conf_shopee_exclusoes_v1",
]

# V17.63: upload/importação de etiquetas é uma função operacional.
# Qualquer usuário já autenticado pode gravar conf_etiquetas/conf_fotos sem
# reautenticação OWNER/Admin. A autenticação normal da sessão continua obrigatória.
OPERATOR_WRITE_KEYS = {
    "conf_pedidos",
    "conf_etiquetas",
    "conf_fotos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em",
    "precificador_online_v1",
    "precificador_alertas_custo_v1",
    "ml_sku_mlb_links_v1",
    "ml_promocoes_datas_v1",
    "conf_coleta_dia_v1",
    "conf_shopee_exclusoes_v1",
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


def normalize_abc_curve_value(*values: Any) -> str:
    """Normaliza a curva ABC do Apollo/CRM para A/B/C/D."""
    def one(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in (
                "curva_abc", "curvaABC", "abc", "classificacao_abc",
                "curva_item", "classificacao", "curva", "valor", "value"
            ):
                if key in value:
                    found = one(value.get(key))
                    if found:
                        return found
            return ""
        if isinstance(value, (list, tuple)):
            for item in value:
                found = one(item)
                if found:
                    return found
            return ""
        text = str(value).strip().upper()
        if not text:
            return ""
        match = re.search(r"(?:^|[^A-Z])([ABCD])(?:\d|$|[^A-Z])", text)
        if not match:
            match = re.match(r"^([ABCD])", text)
        return match.group(1) if match else ""

    for value in values:
        found = one(value)
        if found:
            return found
    return ""


def margin_for_abc_curve(curve: str) -> float | None:
    return {"A": 0.20, "B": 0.20, "C": 0.17, "D": 0.15}.get(str(curve or "").strip().upper())


def apply_curve_margin_to_product(product: dict[str, Any], curve: str) -> None:
    margin = margin_for_abc_curve(curve)
    if margin is None:
        return
    marketplaces = product.get("marketplaces")
    if not isinstance(marketplaces, dict):
        marketplaces = {}
        product["marketplaces"] = marketplaces
    defaults = {
        "ml": {"taxaFixa": 0, "comissao": 0.12, "frete": 0},
        "shopee": {"frete": 0},
        "magalu": {"taxaFixa": 0, "comissao": 0.099, "frete": 0},
    }
    for market, base_values in defaults.items():
        current = marketplaces.get(market)
        if not isinstance(current, dict):
            current = dict(base_values)
            marketplaces[market] = current
        current["margem"] = margin



def _apollo_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d+", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", text):
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def _apollo_stock_from_row(row: dict[str, Any]) -> float | None:
    aliases = (
        "estoque_disponivel", "saldo_disponivel", "quantidade_disponivel",
        "disponivel", "estoque", "saldo", "quantidade", "qtd_disponivel",
        "qtd_estoque", "estoque_atual", "saldo_atual", "stock", "available_stock",
        "available_quantity",
    )
    for key in aliases:
        if key in row:
            number = _apollo_number(row.get(key))
            if number is not None:
                return max(0.0, number)
    return None


def _apollo_stock_payload(data: dict[str, Any]) -> dict[str, Any]:
    casa_nomes = {1: "Araçatuba", 2: "Ribeirão", 3: "Bauru"}
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    for list_key in ("estoques", "saldos", "stocks", "filiais", "custos"):
        raw = data.get(list_key)
        if not isinstance(raw, list):
            continue
        for row in raw:
            if not isinstance(row, dict):
                continue
            try:
                revenda = int(row.get("revenda") or row.get("filial") or row.get("casa") or 0)
            except Exception:
                revenda = 0
            stock = _apollo_stock_from_row(row)
            if stock is None:
                continue
            if revenda in seen and revenda:
                continue
            if revenda:
                seen.add(revenda)
            rows.append({
                "revenda": revenda or None,
                "casa": casa_nomes.get(revenda, str(row.get("nome") or row.get("casa_nome") or revenda or "Apollo")),
                "disponivel": round(stock, 4),
                "conhecido": True,
            })

    top = _apollo_stock_from_row(data)
    if not rows and top is not None:
        rows = [{
            "revenda": int(data.get("revenda_principal") or 1),
            "casa": casa_nomes.get(int(data.get("revenda_principal") or 1), "Apollo"),
            "disponivel": round(top, 4),
            "conhecido": True,
        }]

    total = round(sum(float(r["disponivel"]) for r in rows), 4) if rows else None
    return {
        "estoque_conhecido": bool(rows),
        "estoque_total": total,
        "estoques": rows,
    }


class CrmLimiteAtingido(RuntimeError):
    """V17.94: o limite de 20 chamadas/min do Apollo está ocupado agora."""

    def __init__(self, retry_after: float):
        self.retry_after = max(1, int(retry_after + 0.999))
        super().__init__(
            f"Limite do Apollo (20 consultas/min) atingido. Nova tentativa em {self.retry_after} s."
        )


def crm_rate_acquire(wait_seconds: float = 0.0) -> float:
    """Reserva uma vaga no limite compartilhado /custo + /estoque.

    Devolve 0 quando conseguiu a vaga; senão, em quantos segundos abre a próxima.
    Espera no máximo `wait_seconds`.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        with CRM_RATE_LOCK:
            now = time.monotonic()
            CRM_RATE_CALLS[:] = [t for t in CRM_RATE_CALLS if now - t < 60.0]
            if len(CRM_RATE_CALLS) < CRM_RATE_LIMIT_PER_MINUTE:
                CRM_RATE_CALLS.append(now)
                return 0.0
            retry = 60.0 - (now - CRM_RATE_CALLS[0]) + 0.05
        if retry > deadline - time.monotonic():
            return retry
        time.sleep(retry)


def crm_rate_note() -> None:
    """Conta uma chamada a /custo no balde, sem bloquear quem já chamava antes."""
    with CRM_RATE_LOCK:
        now = time.monotonic()
        CRM_RATE_CALLS[:] = [t for t in CRM_RATE_CALLS if now - t < 60.0]
        CRM_RATE_CALLS.append(now)


def _apollo_stock_contrato(data: dict[str, Any]) -> dict[str, Any]:
    """V17.94: lê o contrato de /api/v1/expedicao/estoque.

    `disponivel: null` / `tem_cadastro: null` é casa que NÃO respondeu — nunca
    vira zero. `disponivel_total` já vem líquido do FULL; com casa sem resposta
    ele é um piso ("pelo menos").
    """
    casa_nomes = {1: "Araçatuba", 2: "Ribeirão", 3: "Bauru"}
    sem_resposta: set[int] = set()
    for item in data.get("casas_sem_resposta") or []:
        try:
            sem_resposta.add(int(item))
        except (TypeError, ValueError):
            continue
    rows: list[dict[str, Any]] = []
    for row in data.get("estoques") or []:
        if not isinstance(row, dict):
            continue
        try:
            revenda = int(row.get("revenda"))
        except (TypeError, ValueError):
            continue
        tem_cadastro = row.get("tem_cadastro")
        if tem_cadastro not in (True, False, None):
            tem_cadastro = None
        disponivel = _apollo_number(row.get("disponivel"))
        respondeu = tem_cadastro is not None and disponivel is not None and revenda not in sem_resposta
        if not respondeu:
            sem_resposta.add(revenda)
        rows.append({
            "revenda": revenda,
            "casa": casa_nomes.get(revenda, f"Filial {revenda}"),
            "disponivel": round(max(0.0, disponivel), 4) if respondeu else None,
            "tem_cadastro": tem_cadastro,
            "respondeu": respondeu,
            "conhecido": respondeu,
            "localizacao": str(row.get("localizacao") or ""),
            "erro": str(row.get("erro") or "") or None,
        })
    respondidas = [r for r in rows if r["respondeu"]]
    total = _apollo_number(data.get("disponivel_total"))
    if total is None and respondidas:
        total = sum(float(r["disponivel"]) for r in respondidas)
    conhecido = total is not None and (bool(respondidas) or not rows)
    return {
        "estoque_conhecido": conhecido,
        "estoque_total": round(max(0.0, total), 4) if conhecido else None,
        "estoques": rows,
        "casas_sem_resposta": sorted(sem_resposta),
        "parcial": conhecido and bool(sem_resposta),
    }


def crm_stock_lookup(codigo: str, operador: str = "", wait_seconds: float = 0.0) -> dict[str, Any]:
    """Consulta o estoque disponível no Apollo sem expor a CHAVE_CRM ao navegador.

    V17.94: usa /api/v1/expedicao/estoque (contrato em INTEGRACAO_ESTOQUE_APOLLO.md),
    com cache curto e o limite de chamadas compartilhado com /custo. Com o
    limite ocupado levanta CrmLimiteAtingido em vez de chamar e tomar 429.
    """
    codigo = str(codigo or "").strip()
    if not codigo:
        raise ValueError("Informe o SKU/código da peça.")
    if not CHAVE_CRM:
        raise RuntimeError("CHAVE_CRM não está configurada no servidor.")

    cache_key = codigo.upper()
    with CRM_STOCK_CACHE_LOCK:
        cached = CRM_STOCK_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] <= CRM_COST_CACHE_SECONDS:
            result = dict(cached[1])
            result["cache"] = True
            return result

    retry = crm_rate_acquire(wait_seconds)
    if retry > 0:
        raise CrmLimiteAtingido(retry)

    params = {"codigo": codigo}
    if operador:
        params["operador"] = operador
    separator = "&" if "?" in CRM_STOCK_API_URL else "?"
    url = CRM_STOCK_API_URL + separator + urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "X-Chave-Expedicao": CHAVE_CRM,
            "Accept": "application/json",
            "User-Agent": f"NoromakExpedicao/{APP_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=CRM_STOCK_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # O Apollo contou chamadas que não passaram por aqui: enche o balde.
            with CRM_RATE_LOCK:
                CRM_RATE_CALLS[:] = [time.monotonic()] * CRM_RATE_LIMIT_PER_MINUTE
            raise CrmLimiteAtingido(60) from exc
        if exc.code == 401:
            raise RuntimeError("O Apollo recusou a chave de serviço (HTTP 401).") from exc
        if exc.code == 404:
            raise RuntimeError("Rota de estoque do Apollo indisponível (HTTP 404).") from exc
        raise RuntimeError(f"Apollo estoque respondeu HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Apollo estoque indisponível: {exc.reason}") from exc

    try:
        data = json.loads(raw or "{}")
    except Exception as exc:
        raise RuntimeError("Apollo estoque retornou resposta inválida.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Apollo estoque retornou resposta inválida.")
    if data.get("ok") is False:
        # Peça inexistente em todas as casas, ou o ERP recusou a consulta inteira.
        raise RuntimeError(str(data.get("erro") or "Peça não encontrada no Apollo."))

    if "disponivel_total" in data or "casas_sem_resposta" in data:
        stock = _apollo_stock_contrato(data)
    else:
        stock = _apollo_stock_payload(data)
        stock.update({"casas_sem_resposta": [], "parcial": False})
    result = {
        "ok": True,
        "codigo": str(data.get("codigo") or codigo),
        "descricao": str(data.get("descricao") or ""),
        "unidade": str(data.get("unidade") or ""),
        **stock,
        "cache": False,
        "consultado_em": now_iso(),
    }
    # Total parcial (casa sem resposta) não entra no cache: a próxima consulta
    # pode trazer a rede inteira.
    if not result.get("parcial"):
        with CRM_STOCK_CACHE_LOCK:
            CRM_STOCK_CACHE[cache_key] = (time.monotonic(), dict(result))
            if len(CRM_STOCK_CACHE) > 2000:
                oldest = sorted(CRM_STOCK_CACHE.items(), key=lambda item: item[1][0])[:500]
                for key, _ in oldest:
                    CRM_STOCK_CACHE.pop(key, None)
    return result


def crm_cost_lookup(codigo: str, operador: str = "") -> dict[str, Any]:
    """Consulta custo médio no Apollo através da API local fornecida ao sistema.

    A CHAVE_CRM nunca é enviada ao navegador. A consulta acontece somente
    servidor->127.0.0.1 dentro da VPS.
    """
    codigo = str(codigo or "").strip()
    operador = str(operador or "").strip()
    if not codigo:
        raise ValueError("Informe o SKU/código da peça.")
    if not CHAVE_CRM:
        raise RuntimeError("CHAVE_CRM não está configurada no servidor.")

    cache_key = codigo.upper()
    now_mono = time.monotonic()
    with CRM_COST_CACHE_LOCK:
        cached = CRM_COST_CACHE.get(cache_key)
        if cached and now_mono - cached[0] <= CRM_COST_CACHE_SECONDS:
            result = dict(cached[1])
            result["cache"] = True
            return result

    params = {"codigo": codigo}
    if operador:
        params["operador"] = operador
    separator = "&" if "?" in CRM_COST_API_URL else "?"
    url = CRM_COST_API_URL + separator + urlencode(params)
    crm_rate_note()  # V17.94: mesmo balde de 20/min da rota de estoque.
    req = urllib.request.Request(
        url,
        headers={
            "X-Chave-Expedicao": CHAVE_CRM,
            "Accept": "application/json",
            "User-Agent": f"NoromakExpedicao/{APP_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=CRM_COST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429:
            raise RuntimeError("Limite da consulta de custo atingido. Aguarde um pouco e tente novamente.") from exc
        if exc.code in (401, 403):
            raise RuntimeError("A API de custo recusou a chave configurada no servidor.") from exc
        raise RuntimeError(f"API de custo respondeu HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API de custo indisponível: {exc.reason}") from exc

    try:
        data = json.loads(raw or "{}")
    except Exception as exc:
        raise RuntimeError("API de custo retornou uma resposta inválida.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("API de custo retornou uma resposta inválida.")
    if status >= 400:
        raise RuntimeError(str(data.get("erro") or data.get("detail") or f"HTTP {status}"))
    if not data.get("ok"):
        raise RuntimeError(str(data.get("erro") or "O Apollo não devolveu o custo desta peça."))

    # V16.32 — contrato de 3 casas do CRM.
    # A lista `custos` é a fonte oficial. O custo_medio do topo existe apenas
    # por compatibilidade e pode ser null quando a peça só existe em outra filial.
    casa_nomes = {1: "Araçatuba", 2: "Ribeirão", 3: "Bauru"}
    custos_raw = data.get("custos")
    custos_filiais: list[dict[str, Any]] = []
    if isinstance(custos_raw, list):
        for row in custos_raw:
            if not isinstance(row, dict):
                continue
            try:
                revenda = int(row.get("revenda"))
            except (TypeError, ValueError):
                continue
            custo_val = row.get("custo_medio")
            try:
                custo_num = round(float(custo_val), 4) if custo_val is not None else None
            except (TypeError, ValueError):
                custo_num = None
            tem_cadastro = row.get("tem_cadastro")
            if tem_cadastro not in (True, False, None):
                tem_cadastro = None
            custos_filiais.append({
                "revenda": revenda,
                "casa": casa_nomes.get(revenda, str(revenda)),
                "custo_medio": custo_num,
                "tem_cadastro": tem_cadastro,
                "erro": str(row.get("erro") or "") or None,
            })

    # Compatibilidade com endpoint antigo: se ainda não houver lista, monta
    # somente a casa principal com o campo legado.
    if not custos_filiais:
        principal = int(data.get("revenda_principal") or 1)
        legado = data.get("custo_medio")
        try:
            legado_num = round(float(legado), 4) if legado is not None else None
        except (TypeError, ValueError):
            legado_num = None
        custos_filiais = [{
            "revenda": principal,
            "casa": casa_nomes.get(principal, str(principal)),
            "custo_medio": legado_num,
            "tem_cadastro": True if legado_num is not None else False,
            "erro": None,
        }]

    # Mantém custo_medio no retorno por compatibilidade, sem exigir que exista.
    custo_topo = data.get("custo_medio")
    try:
        custo = round(float(custo_topo), 4) if custo_topo is not None else None
    except (TypeError, ValueError):
        custo = None

    # Contrato oficial da API Apollo/CRM:
    # - curva_abc: A/B/C/D (fonte oficial para precificação)
    # - curva_123: 1/2/3
    # - curva: classificação composta, por exemplo A3 ou D3
    # Mantemos os três campos separados para não confundir a curva ABC com a composta.
    curva_abc = normalize_abc_curve_value(data.get("curva_abc"))
    curva_123 = str(data.get("curva_123") or "").strip()
    curva_composta = str(data.get("curva") or "").strip().upper()

    # Compatibilidade somente para versões antigas do endpoint que eventualmente
    # ainda não devolvam curva_abc. A resposta oficial sempre prevalece.
    if not curva_abc:
        curva_abc = normalize_abc_curve_value(
            data.get("abc"),
            data.get("classificacao_abc"),
            data.get("curva_item"),
            data.get("classificacao"),
            # O contrato do Apollo também devolve a classificação combinada
            # em `curva` (ex.: A3, C2). Se curva_abc vier vazia por alguma
            # inconsistência temporária, extraímos somente a letra ABCD.
            data.get("curva"),
            data.get("curvaABC"),
            data.get("curva_abcd"),
        )

    result = {
        "ok": True,
        "codigo": str(data.get("codigo") or codigo),
        "descricao": str(data.get("descricao") or ""),
        "unidade": str(data.get("unidade") or ""),
        "custo_medio": custo,
        "revenda_principal": int(data.get("revenda_principal") or 1),
        "custos": custos_filiais,
        "curva_abc": curva_abc,
        "curva_123": curva_123,
        "curva": curva_composta,
        "cache": False,
        "consultado_em": now_iso(),
    }
    # V18.07: guarda o último custo > 0 visto (reserva da Demanda de Compra).
    try:
        positivo = custo if custo and custo > 0 else next(
            (c["custo_medio"] for c in custos_filiais if (c.get("custo_medio") or 0) > 0), None)
        if positivo:
            custos_registrar_auto([(result["codigo"], positivo)], "Apollo")
    except Exception:
        pass
    with CRM_COST_CACHE_LOCK:
        CRM_COST_CACHE[cache_key] = (time.monotonic(), dict(result))
        # Evita crescimento indefinido; é um cache curto e pequeno.
        if len(CRM_COST_CACHE) > 300:
            oldest = sorted(CRM_COST_CACHE.items(), key=lambda item: item[1][0])[:100]
            for key, _ in oldest:
                CRM_COST_CACHE.pop(key, None)
    return result



def _ml_precificador_persisted_links() -> dict[str, list[dict[str, Any]]]:
    """Retorna vínculos MLB↔SKU do storage canônico já com overrides do ADMIN.

    A seed é aplicada pela migração V17.61. O endpoint não injeta a planilha novamente por
    fora do storage, porque isso poderia ressuscitar um vínculo que o ADMIN removeu depois.
    """
    # Se o servidor foi atualizado mas o processo ainda não aplicou a revisão por algum motivo,
    # tenta a migração idempotente antes de responder ao Precificador.
    try:
        with DB_LOCK:
            marker = DB.execute(
                "SELECT value FROM storage WHERE key=?", (ML_SKU_MLB_SEED_REVISION_KEY,)
            ).fetchone()
        marker_value = str(marker["value"] or "") if marker else ""
        if marker_value.strip() != ML_SKU_MLB_SEED_REVISION:
            seed_ml_sku_mlb_links_from_file()
    except Exception as exc:
        print(f"Aviso: revisão MLB/SKU não pôde ser verificada: {exc}", file=sys.stderr)

    try:
        with DB_LOCK:
            raw_value, _, _ = get_storage(ML_SKU_MLB_LINKS_STORAGE_KEY)
        payload = json.loads(raw_value or "{}")
    except Exception as exc:
        print(f"Aviso: vínculos MLB/SKU persistidos não puderam ser lidos: {exc}", file=sys.stderr)
        payload = {}

    merged: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(payload, dict):
        return merged
    for raw_sku, raw_rows in payload.items():
        sku = str(raw_sku or "").strip().upper()
        if not sku or not isinstance(raw_rows, list):
            continue
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_rows:
            obj = dict(raw) if isinstance(raw, dict) else {"mlb": raw}
            digits = re.sub(r"\D+", "", str(obj.get("mlb") or ""))
            if not digits:
                continue
            mlb = "MLB" + digits
            if mlb in seen:
                continue
            seen.add(mlb)
            obj["mlb"] = mlb
            obj.setdefault("sku", sku)
            rows.append(obj)
        if rows:
            merged[sku] = rows
    return _ml_merge_seed_links(merged)

def ml_precificador_links_payload(max_items: int = 5000) -> dict[str, Any]:
    """Monta SKU -> MLB pela base canônica e enriquece com a API do ML."""
    max_items = max(1, min(10000, int(max_items or 5000)))
    links = _ml_precificador_persisted_links()
    item_ids: list[str] = []
    seen_ids: set[str] = set()
    errors: list[str] = []
    seller_id = ""
    live_available = True

    persisted_by_mlb: dict[str, list[dict[str, Any]]] = {}
    for sku, rows in links.items():
        for obj in rows:
            if not isinstance(obj, dict):
                continue
            mlb = str(obj.get("mlb") or "").strip().upper()
            if mlb:
                persisted_by_mlb.setdefault(mlb, []).append(obj)

    try:
        _, seller_id = ml_access_token()
    except Exception as exc:
        live_available = False
        errors.append("Mercado Livre indisponível para enriquecimento: " + str(exc)[:240])

    if live_available:
        for status in ("active", "paused", "closed"):
            offset = 0
            while len(item_ids) < max_items:
                limit = min(100, max_items - len(item_ids))
                path = (
                    f"/users/{quote(str(seller_id))}/items/search"
                    f"?status={quote(status)}&offset={offset}&limit={limit}"
                )
                try:
                    payload = ml_api_get(path)
                except Exception as exc:
                    errors.append(f"{status}: {str(exc)[:240]}")
                    break
                results = payload.get("results") if isinstance(payload, dict) else []
                if not isinstance(results, list) or not results:
                    break
                for raw_id in results:
                    item_id = str(raw_id or "").strip().upper()
                    if not item_id:
                        continue
                    if not item_id.startswith("MLB"):
                        item_id = "MLB" + re.sub(r"\D+", "", item_id)
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        item_ids.append(item_id)
                        if len(item_ids) >= max_items:
                            break
                paging = payload.get("paging") if isinstance(payload, dict) else {}
                total = int((paging or {}).get("total") or 0)
                offset += len(results)
                if not results or (total and offset >= total):
                    break

    digit_ids = [re.sub(r"\D+", "", item_id) for item_id in item_ids]
    meta_map: dict[str, dict[str, Any]] = {}
    for start in range(0, len(digit_ids), 100):
        batch = digit_ids[start:start + 100]
        try:
            meta_map.update(ml_items_meta(batch))
        except Exception as exc:
            errors.append(f"meta {start}: {str(exc)[:240]}")

    precifier = _promo_precifier_index()
    precifier_by_compact: dict[str, str] = {}
    for sku in precifier.keys():
        compact = re.sub(r"[^A-Z0-9]", "", sku.upper())
        if compact and compact not in precifier_by_compact:
            precifier_by_compact[compact] = sku

    unidentified: list[dict[str, Any]] = []
    live_linked = 0
    for item_id in item_ids:
        digits = re.sub(r"\D+", "", item_id)
        mlb = "MLB" + digits
        meta = meta_map.get(digits) or {}
        raw_sku = str(meta.get("sku") or "").strip()
        sku = _ml_normalize_api_sku(raw_sku).strip().upper()
        if sku and sku not in precifier:
            compact = re.sub(r"[^A-Z0-9]", "", sku)
            if compact in precifier_by_compact:
                sku = precifier_by_compact[compact]
        row = {
            "mlb": mlb,
            "sku": sku,
            "title": str(meta.get("title") or ""),
            "price": float(meta.get("price") or 0),
            "status": str(meta.get("status") or "unknown"),
            "available_quantity": meta.get("available_quantity"),
            "permalink": str(meta.get("permalink") or ""),
            "updatedAt": now_iso(),
            "sourceLive": "mercado_livre_api",
        }
        existing_rows = persisted_by_mlb.get(mlb) or []
        if existing_rows:
            for existing in existing_rows:
                for field in ("title", "price", "status", "available_quantity", "permalink", "updatedAt"):
                    value = row.get(field)
                    if value not in (None, "", 0):
                        existing[field] = value
            live_linked += 1
            continue
        if sku:
            rows = links.setdefault(sku, [])
            rows.append(row)
            persisted_by_mlb.setdefault(mlb, []).append(row)
            live_linked += 1
        else:
            unidentified.append(row)

    for sku, rows in links.items():
        rows.sort(key=lambda r: (str(r.get("status") or ""), str(r.get("mlb") or "")))

    linked_items = sum(len(v) for v in links.values())
    return {
        "ok": True,
        "seller_id": str(seller_id),
        "total_items": len(item_ids),
        "linked_items": linked_items,
        "linked_skus": len(links),
        "live_linked_items": live_linked,
        "canonical_links": linked_items,
        "unidentified": unidentified,
        "links": links,
        "errors": errors[:20],
        "updated_at": now_iso(),
    }



def ml_precificador_sync_promo_links(mlb_values: list[Any], actor: str = "") -> dict[str, Any]:
    """V17.67 — vincula MLB -> SKU usando somente os MLBs pedidos pela tela de Promoções."""
    ids: list[str] = []
    seen: set[str] = set()
    for value in mlb_values or []:
        digits = re.sub(r"\D+", "", str(value or ""))
        if not digits or digits in seen:
            continue
        seen.add(digits)
        ids.append(digits)
        if len(ids) >= 100:
            break

    if not ids:
        return {"ok": True, "processed": 0, "linked": 0, "already_linked": 0,
                "unresolved": 0, "errors": 0, "results": [], "affected": {}}

    links = _ml_precificador_persisted_links()
    inverse: dict[str, str] = {}
    for sku, rows in links.items():
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            mlb = _ml_normalize_link_mlb(row.get("mlb"))
            if mlb and mlb not in inverse:
                inverse[mlb] = _ml_normalize_link_sku(sku)

    meta_map = ml_items_meta(ids)
    precifier = _promo_precifier_index()
    compact_index: dict[str, str] = {}
    for sku in precifier.keys():
        compact = re.sub(r"[^A-Z0-9]", "", _ml_normalize_link_sku(sku))
        if compact and compact not in compact_index:
            compact_index[compact] = _ml_normalize_link_sku(sku)

    with DB_LOCK:
        tombstones = _ml_manual_link_tombstones()

    results: list[dict[str, Any]] = []
    affected_skus: set[str] = set()
    linked = 0
    already_linked = 0
    unresolved = 0
    errors = 0
    changed = False
    novos_vinculos: list[tuple[str, dict[str, Any]]] = []

    for digits in ids:
        mlb = "MLB" + digits

        existing_sku = inverse.get(mlb, "")
        if existing_sku:
            meta = meta_map.get(digits) or {}
            results.append({
                "mlb": mlb, "sku": existing_sku, "status": "already_linked",
                "title": str(meta.get("title") or ""), "price": float(meta.get("price") or 0),
            })
            already_linked += 1
            continue

        meta = meta_map.get(digits) or {}
        raw_sku = str(meta.get("sku") or "").strip()
        if not meta or (meta.get("stock_known") is False and not raw_sku):
            results.append({"mlb": mlb, "sku": "", "status": "api_error",
                            "reason": "Anúncio não retornado pelo Mercado Livre"})
            errors += 1
            continue

        sku = _ml_normalize_api_sku(raw_sku).strip().upper()
        if not sku:
            results.append({"mlb": mlb, "sku": "", "status": "no_sku",
                            "reason": "SKU não informado no anúncio"})
            unresolved += 1
            continue

        resolved_sku = sku if sku in precifier else ""
        if not resolved_sku:
            compact = re.sub(r"[^A-Z0-9]", "", sku)
            resolved_sku = compact_index.get(compact, "")

        if not resolved_sku:
            results.append({
                "mlb": mlb, "sku": sku, "status": "sku_not_in_precificador",
                "reason": "SKU encontrado no ML, mas não localizado no Precificador",
                "title": str(meta.get("title") or ""),
            })
            unresolved += 1
            continue

        if (resolved_sku, mlb) in tombstones:
            results.append({
                "mlb": mlb, "sku": resolved_sku, "status": "manual_tombstone",
                "reason": "Vínculo removido manualmente pelo ADMIN",
            })
            unresolved += 1
            continue

        row = {
            "mlb": mlb, "sku": resolved_sku,
            "title": str(meta.get("title") or ""),
            "price": float(meta.get("price") or 0),
            "status": str(meta.get("status") or "Vinculado"),
            "available_quantity": meta.get("available_quantity"),
            "permalink": str(meta.get("permalink") or ""),
            "source": "mercado_livre_api_sync_promocoes",
            "autoLinked": True,
            "updatedAt": now_iso(),
            "linkedBy": actor or "usuario",
        }
        links.setdefault(resolved_sku, []).append(row)
        novos_vinculos.append((resolved_sku, row))
        inverse[mlb] = resolved_sku
        affected_skus.add(resolved_sku)
        changed = True
        linked += 1
        results.append({"mlb": mlb, "sku": resolved_sku, "status": "linked",
                        "title": row["title"], "price": row["price"]})

    affected: dict[str, list[dict[str, Any]]] = {}
    if changed:
        with DB_LOCK:
            # V18.56: relê os vínculos e os tombstones SOB A TRAVA e só acrescenta os
            # novos. Antes gravava o retrato lido antes da chamada ao ML (segundos de
            # rede): um vínculo incluído à mão nessa janela era apagado.
            links = _ml_precificador_persisted_links()
            tombstones = _ml_manual_link_tombstones()
            ja_vinculados = {
                _ml_normalize_link_mlb(r.get("mlb"))
                for rows in links.values() for r in (rows if isinstance(rows, list) else [])
                if isinstance(r, dict)
            }
            for sku_novo, row_novo in novos_vinculos:
                mlb_novo = _ml_normalize_link_mlb(row_novo.get("mlb"))
                if mlb_novo in ja_vinculados or (sku_novo, mlb_novo) in tombstones:
                    continue
                links.setdefault(sku_novo, []).append(row_novo)
                ja_vinculados.add(mlb_novo)
            canonical = _ml_merge_seed_links(links)
            DB.execute("BEGIN IMMEDIATE")
            try:
                row = DB.execute("SELECT version FROM storage WHERE key=?",
                                 (ML_SKU_MLB_LINKS_STORAGE_KEY,)).fetchone()
                version = int(row["version"] or 0) + 1 if row else 1
                DB.execute(
                    """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                    (ML_SKU_MLB_LINKS_STORAGE_KEY, json_dumps(canonical), version, now_iso()),
                )
                for sku in sorted(affected_skus):
                    rows = canonical.get(sku, [])
                    affected[sku] = rows
                    DB.execute(
                        "DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id='__ROOT__' AND field_name=?",
                        (ML_SKU_MLB_LINKS_STORAGE_KEY, sku),
                    )
                    _admin_override_upsert(
                        ML_SKU_MLB_LINKS_STORAGE_KEY, "__ROOT__", sku, rows,
                        deleted=False,
                        actor="SYNC_MLB_SKU_PROMOCOES:" + (actor or "usuario"),
                    )
                DB.commit()
            except Exception:
                DB.rollback()
                raise

    return {
        "ok": True, "processed": len(ids), "linked": linked,
        "already_linked": already_linked, "unresolved": unresolved, "errors": errors,
        "results": results, "affected": affected, "updated_at": now_iso(),
    }


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


def is_supervisor_user(row: sqlite3.Row | dict[str, Any]) -> bool:
    """Supervisor = operador com acesso operacional amplo ao Admin, sem poderes críticos de administrador."""
    try:
        role = str(row["role"]).lower()
        can_admin = bool(row["can_admin"])
    except Exception:
        role = str(row.get("role", "operador")).lower()
        can_admin = bool(row.get("can_admin"))
    return role != "admin" and can_admin


def session_ttl_for_user_area(area: str, row: sqlite3.Row | dict[str, Any]) -> int:
    if str(area or "").lower() == "admin" and is_supervisor_user(row):
        return SUPERVISOR_SESSION_TTL_SECONDS
    return session_ttl_for_area(area)


def ensure_ml_billing_data_schema() -> None:
    """V18.37: acrescenta detail_date e preenche a partir do raw_json já guardado.

    Sem a data, a publicidade da fatura entrava inteira em qualquer período
    escolhido — pedir 01 a 15 trazia o mês todo.
    """
    with DB_LOCK:
        info = DB.execute("PRAGMA table_info(ml_billing_period_detail)").fetchall()
        if not info:
            return
        if "detail_date" not in {str(r["name"]) for r in info}:
            DB.execute("ALTER TABLE ml_billing_period_detail ADD COLUMN detail_date TEXT NOT NULL DEFAULT ''")
            DB.execute("CREATE INDEX IF NOT EXISTS idx_ml_billing_period_data ON ml_billing_period_detail(detail_date)")
            DB.commit()
        # Só o começo do raw_json: a data mora no início do charge_info e o campo
        # inteiro tem até 120 KB. Ler tudo de 11 mil linhas derrubaria o boot.
        pendentes = DB.execute(
            "SELECT period_key, document_type, detail_id, order_id, substr(raw_json,1,700) AS inicio "
            "FROM ml_billing_period_detail WHERE detail_date='' AND raw_json<>'' LIMIT 40000"
        ).fetchall()
    padrao = re.compile(r'"(?:creation_date_time|detail_date)"\s*:\s*"(\d{4}-\d{2}-\d{2})')
    atualizacoes: list[tuple[str, str, str, str, str]] = []
    for r in pendentes:
        achado = padrao.search(str(r["inicio"] or ""))
        if achado:
            atualizacoes.append((achado.group(1), r["period_key"], r["document_type"],
                                 r["detail_id"], r["order_id"]))
    for i in range(0, len(atualizacoes), 2000):
        with DB_LOCK:
            DB.executemany(
                "UPDATE ml_billing_period_detail SET detail_date=? "
                "WHERE period_key=? AND document_type=? AND detail_id=? AND order_id=?",
                atualizacoes[i:i + 2000])
            DB.commit()


def ensure_ml_billing_credito_schema() -> None:
    """V18.36: corrige o is_credit das linhas gravadas até a V18.35.

    Elas foram marcadas pelo document_type pedido (BILL x CREDIT_NOTE), e como a
    API devolve os mesmos lançamentos nos dois, metade virou "crédito" e anulou
    a cobrança. Aqui o valor passa a vir do próprio lançamento. A leitura já
    deduplica por detail_id; isto só deixa o banco coerente.
    """
    with DB_LOCK:
        if not DB.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ml_billing_period_detail'").fetchone():
            return
        DB.execute(
            "UPDATE ml_billing_period_detail SET is_credit=1 "
            "WHERE is_credit=0 AND UPPER(detail_type) IN ('BONUS','BONIFICATION','CREDIT')")
        DB.execute(
            "UPDATE ml_billing_period_detail SET is_credit=0 "
            "WHERE is_credit=1 AND UPPER(detail_type) NOT IN ('BONUS','BONIFICATION','CREDIT') "
            "AND UPPER(detail_sub_type) NOT LIKE 'B%'")
        DB.commit()


def ensure_shopee_escrow_schema() -> None:
    """V18.35: acrescenta ams_commission (comissão de afiliados/AMS da Shopee).

    O valor já vinha no payload e estava guardado em order_income_json — o
    backfill lê dali, sem refazer uma única chamada à Shopee.
    """
    with DB_LOCK:
        info = DB.execute("PRAGMA table_info(shopee_escrow_v1810)").fetchall()
        if not info:
            return
        if "ams_commission" not in {str(r["name"]) for r in info}:
            DB.execute("ALTER TABLE shopee_escrow_v1810 ADD COLUMN ams_commission REAL")
            DB.commit()
        pendentes = DB.execute(
            "SELECT order_sn, order_income_json FROM shopee_escrow_v1810 "
            "WHERE ams_commission IS NULL AND order_income_json<>'' AND erro=''"
        ).fetchall()
    atualizacoes: list[tuple[float, str]] = []
    for r in pendentes:
        try:
            oi = json.loads(r["order_income_json"] or "{}")
        except Exception:
            continue  # JSON truncado: fica nulo e o cálculo usa o resíduo do escrow
        atualizacoes.append((round(_shopee_escrow_num(oi, "order_ams_commission_fee", "ams_commission_fee"), 2),
                             str(r["order_sn"])))
    if atualizacoes:
        with DB_LOCK:
            DB.executemany("UPDATE shopee_escrow_v1810 SET ams_commission=? WHERE order_sn=?", atualizacoes)
            DB.commit()


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
        # V17.82: o UPDATE acima roda SEMPRE, então o COMMIT também precisa rodar sempre.
        # Em uma instalação nova as colunas já nascem no executescript (changed=False) e o
        # UPDATE ficava em transação implícita aberta, fazendo o BEGIN IMMEDIATE de
        # migrate_legacy_if_empty() falhar e impedir o servidor de subir.
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
            CREATE TABLE IF NOT EXISTS admin_manual_overrides (
                storage_key TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                value_json TEXT NOT NULL DEFAULT 'null',
                is_deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(storage_key,entity_id,field_name)
            );
            CREATE INDEX IF NOT EXISTS idx_admin_overrides_key ON admin_manual_overrides(storage_key);
            CREATE TABLE IF NOT EXISTS ml_sku_mlb_manual_tombstones (
                sku TEXT NOT NULL,
                mlb TEXT NOT NULL,
                removed_at TEXT NOT NULL,
                removed_by TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(sku,mlb)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_sku_mlb_tombstones_sku ON ml_sku_mlb_manual_tombstones(sku);
            CREATE TABLE IF NOT EXISTS admin_manual_orders_v1752 (
                entity_id TEXT PRIMARY KEY,
                order_key TEXT NOT NULL DEFAULT '',
                marketplace TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_admin_manual_orders_order ON admin_manual_orders_v1752(order_key);
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
            CREATE TABLE IF NOT EXISTS shopee_integration (
                id INTEGER PRIMARY KEY CHECK (id=1),
                shop_id TEXT NOT NULL DEFAULT '',
                shop_name TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                token_expires_at INTEGER NOT NULL DEFAULT 0,
                connected_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                last_sync_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        DB.execute(
            """
            CREATE TABLE IF NOT EXISTS shopee_event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                resource TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # V18.56: a limpeza diária dos logs filtra por data.
        DB.execute("CREATE INDEX IF NOT EXISTS idx_shopee_event_log_created ON shopee_event_log(created_at)")
        DB.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS ml_profitability_snapshot (
                order_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                unit_cost REAL,
                cost_source TEXT NOT NULL DEFAULT '',
                sale_amount REAL,
                ml_fee REAL,
                seller_shipping_cost REAL,
                other_ml_cost REAL,
                captured_at TEXT NOT NULL,
                raw_summary TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(order_id, sku)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_profit_snapshot_captured ON ml_profitability_snapshot(captured_at DESC);
            -- V17.99: vendas dos marketplaces (ML e Shopee: valor, quantidade e data
            -- real) para o Dashboard de vendas. Cópia local das APIs; o marketplace
            -- continua sendo a fonte — a janela recente é regravada a cada sincronização.
            CREATE TABLE IF NOT EXISTS vendas_v1799 (
                marketplace TEXT NOT NULL,
                order_id TEXT NOT NULL,
                line_key TEXT NOT NULL,
                pack_id TEXT NOT NULL DEFAULT '',
                sale_at TEXT NOT NULL,
                sale_day TEXT NOT NULL,
                sku TEXT NOT NULL DEFAULT '',
                anuncio TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                qty INTEGER NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(marketplace, order_id, line_key)
            );
            CREATE INDEX IF NOT EXISTS idx_vendas_v1799_day ON vendas_v1799(sale_day, marketplace);
            CREATE INDEX IF NOT EXISTS idx_vendas_v1799_sku ON vendas_v1799(sku, sale_day);
            -- V18.10: custos reais por pedido da Shopee (API get_escrow_detail).
            -- escrow_amount é a RENDA líquida do vendedor — a autoridade. Cache
            -- porque o escrow é estável depois de pago e o gasto de chamada é alto.
            CREATE TABLE IF NOT EXISTS shopee_escrow_v1810 (
                order_sn TEXT PRIMARY KEY,
                selling_price REAL,
                escrow_amount REAL,
                commission_fee REAL,
                service_fee REAL,
                transaction_fee REAL,
                shipping_net REAL,
                ams_commission REAL,
                order_income_json TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT '',
                fetched_epoch INTEGER NOT NULL DEFAULT 0,
                erro TEXT NOT NULL DEFAULT ''
            );
            -- V18.00: fornecedores da Demanda de Compra (pedido mínimo, prazo,
            -- cobertura e as regras que dizem quais peças são de cada um).
            -- V18.07: custo de reserva da Demanda de Compra. O custo médio do Apollo
            -- vai a 0 quando o estoque zera; aqui fica o último custo > 0 visto
            -- (automático) e o custo digitado na tela (manual).
            CREATE TABLE IF NOT EXISTS custos_conhecidos_v1807 (
                codigo TEXT PRIMARY KEY,
                custo_auto REAL,
                origem_auto TEXT NOT NULL DEFAULT '',
                data_auto TEXT NOT NULL DEFAULT '',
                custo_manual REAL,
                data_manual TEXT NOT NULL DEFAULT '',
                manual_por TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS compras_fornecedores_v1800 (
                nome TEXT PRIMARY KEY,
                config TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS vendas_meta_v1799 (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ml_profitability_billing_cache (
                order_id TEXT PRIMARY KEY,
                seller_id TEXT NOT NULL DEFAULT '',
                sale_fee REAL,
                other_cost REAL,
                bonus REAL,
                status TEXT NOT NULL DEFAULT '',
                diagnostic TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT '',
                fetched_at_epoch INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_ml_profit_billing_fetched ON ml_profitability_billing_cache(fetched_at_epoch DESC);
            CREATE TABLE IF NOT EXISTS ml_billing_period_sync (
                period_key TEXT NOT NULL,
                document_type TEXT NOT NULL,
                seller_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                last_id TEXT NOT NULL DEFAULT '',
                item_count INTEGER NOT NULL DEFAULT 0,
                diagnostic TEXT NOT NULL DEFAULT '',
                synced_at TEXT NOT NULL DEFAULT '',
                synced_at_epoch INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(period_key, document_type)
            );
            CREATE TABLE IF NOT EXISTS ml_billing_period_detail (
                period_key TEXT NOT NULL,
                document_type TEXT NOT NULL,
                detail_id TEXT NOT NULL,
                order_id TEXT NOT NULL DEFAULT '',
                pack_id TEXT NOT NULL DEFAULT '',
                shipment_id TEXT NOT NULL DEFAULT '',
                detail_amount REAL,
                detail_type TEXT NOT NULL DEFAULT '',
                detail_sub_type TEXT NOT NULL DEFAULT '',
                transaction_detail TEXT NOT NULL DEFAULT '',
                debited_from_operation TEXT NOT NULL DEFAULT '',
                sale_fee_net REAL,
                sale_fee_gross REAL,
                sale_fee_rebate REAL,
                sale_fee_discount REAL,
                is_credit INTEGER NOT NULL DEFAULT 0,
                detail_date TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '',
                synced_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(period_key, document_type, detail_id, order_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_order ON ml_billing_period_detail(order_id, period_key);
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_pack ON ml_billing_period_detail(pack_id, period_key);
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_subtype ON ml_billing_period_detail(detail_sub_type, period_key);
            -- V18.30: gasto com Publicidade (Product Ads) do Mercado Livre, dia a dia.
            -- A API só devolve 90 dias para trás e consolida o dia às 10:00 GMT-3,
            -- por isso guardamos aqui: o histórico é nosso, a janela recente é
            -- regravada a cada sincronização (campanha pausada/estornada some).
            CREATE TABLE IF NOT EXISTS ml_ads_dia_v1830 (
                dia TEXT PRIMARY KEY,
                cost REAL NOT NULL DEFAULT 0,
                clicks INTEGER NOT NULL DEFAULT 0,
                prints INTEGER NOT NULL DEFAULT 0,
                units INTEGER NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                campanhas INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ml_ads_campanha_dia_v1830 (
                dia TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                nome TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                cost REAL NOT NULL DEFAULT 0,
                clicks INTEGER NOT NULL DEFAULT 0,
                prints INTEGER NOT NULL DEFAULT 0,
                units INTEGER NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(dia, campaign_id)
            );
            CREATE TABLE IF NOT EXISTS ml_ads_item_dia_v1830 (
                dia TEXT NOT NULL,
                ad_group_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                sku TEXT NOT NULL DEFAULT '',
                cost REAL NOT NULL DEFAULT 0,
                clicks INTEGER NOT NULL DEFAULT 0,
                prints INTEGER NOT NULL DEFAULT 0,
                units INTEGER NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(dia, ad_group_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_ads_item_dia ON ml_ads_item_dia_v1830(dia);
            CREATE INDEX IF NOT EXISTS idx_ml_ads_item_sku ON ml_ads_item_dia_v1830(sku, dia);
            -- Mapa ad_group -> anúncio/SKU. Resolvido uma vez e reaproveitado:
            -- o ad_group agrupa as variações do mesmo produto (novo fluxo do ML).
            CREATE TABLE IF NOT EXISTS ml_ads_grupo_v1830 (
                ad_group_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                sku TEXT NOT NULL DEFAULT '',
                titulo TEXT NOT NULL DEFAULT '',
                tipo TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ml_ads_meta_v1830 (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            -- V18.38: investimento em Shopee Ads (CPC). Não está no escrow do
            -- pedido — é crédito pré-pago, cobrado fora da venda. Vem de
            -- /api/v2/ads/get_all_cpc_ads_daily_performance, que é a mesma fonte
            -- do painel "Desempenho de Anúncios de Produtos" do Seller Centre.
            CREATE TABLE IF NOT EXISTS shopee_ads_dia_v1838 (
                dia TEXT PRIMARY KEY,
                expense REAL NOT NULL DEFAULT 0,
                clicks INTEGER NOT NULL DEFAULT 0,
                impressions INTEGER NOT NULL DEFAULT 0,
                gmv REAL NOT NULL DEFAULT 0,
                pedidos INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            -- V18.54: Análise de ADS. O relatório "Anúncios patrocinados" que o ML
            -- deixa baixar em Publicidade > Relatórios (.xlsx), importado à mão.
            -- Um relatório por período: importar o mesmo período de novo substitui.
            CREATE TABLE IF NOT EXISTS ads_relatorio_v1854 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                periodo_inicio TEXT NOT NULL,
                periodo_fim TEXT NOT NULL,
                arquivo TEXT NOT NULL DEFAULT '',
                linhas INTEGER NOT NULL DEFAULT 0,
                anuncios INTEGER NOT NULL DEFAULT 0,
                investimento REAL NOT NULL DEFAULT 0,
                receita REAL NOT NULL DEFAULT 0,
                importado_em TEXT NOT NULL DEFAULT '',
                importado_por TEXT NOT NULL DEFAULT '',
                UNIQUE(periodo_inicio, periodo_fim)
            );
            CREATE TABLE IF NOT EXISTS ads_relatorio_linha_v1854 (
                relatorio_id INTEGER NOT NULL,
                ordem INTEGER NOT NULL,
                mlb TEXT NOT NULL,
                titulo TEXT NOT NULL DEFAULT '',
                campanha TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                impressoes INTEGER NOT NULL DEFAULT 0,
                cliques INTEGER NOT NULL DEFAULT 0,
                investimento REAL NOT NULL DEFAULT 0,
                receita REAL NOT NULL DEFAULT 0,
                vendas_diretas INTEGER NOT NULL DEFAULT 0,
                vendas_indiretas INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(relatorio_id, ordem)
            );
            CREATE INDEX IF NOT EXISTS idx_ads_relatorio_linha_mlb ON ads_relatorio_linha_v1854(mlb);
            -- V18.31: custo da venda NO ATO DA VENDA, como o ML mostra no detalhe do
            -- pedido ("Tarifa de venda total" e "Tarifa do Mercado Envios por sua
            -- conta"). Capturado junto com a sincronização de vendas, sem nenhuma
            -- chamada extra: a tarifa já vem em order_items[].sale_fee.
            -- A fatura (ml_billing_period_detail) continua sendo a conferência: quando
            -- o lançamento aparece, ele prevalece e a diferença fica visível.
            CREATE TABLE IF NOT EXISTS ml_venda_custo_v1830 (
                order_id TEXT NOT NULL,
                line_key TEXT NOT NULL,
                sku TEXT NOT NULL DEFAULT '',
                sale_fee REAL,
                shipment_id TEXT NOT NULL DEFAULT '',
                sale_day TEXT NOT NULL DEFAULT '',
                captured_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(order_id, line_key)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_venda_custo_dia ON ml_venda_custo_v1830(sale_day);
            CREATE INDEX IF NOT EXISTS idx_ml_venda_custo_envio ON ml_venda_custo_v1830(shipment_id);
            -- Frete que fica por conta do vendedor, por envio (/shipments/{id}/costs,
            -- senders[].cost). Uma chamada por envio, guardada para sempre.
            CREATE TABLE IF NOT EXISTS ml_frete_envio_v1830 (
                shipment_id TEXT PRIMARY KEY,
                cost REAL,
                captured_at TEXT NOT NULL DEFAULT '',
                captured_epoch INTEGER NOT NULL DEFAULT 0,
                erro TEXT NOT NULL DEFAULT ''
            );
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
            -- V17.86: CPF/CNPJ e nome de "Dados para a NF-e" lidos pela extensão
            -- na página da venda, e o código do cliente no Linx depois de
            -- atualizado. Tabela própria de propósito: a sincronização do ML
            -- reescreve NomeFiscalMercadoLivre/CPF nas linhas de conf_pedidos a
            -- cada poucos minutos (com vazio, já que a API bloqueia o billing),
            -- e apagaria o que fosse guardado ali.
            CREATE TABLE IF NOT EXISTS cliente_fiscal (
                order_key TEXT PRIMARY KEY,
                pedido TEXT NOT NULL DEFAULT '',
                documento_tipo TEXT NOT NULL DEFAULT '',
                documento_numero TEXT NOT NULL DEFAULT '',
                nome_fiscal TEXT NOT NULL DEFAULT '',
                fonte TEXT NOT NULL DEFAULT '',
                linx_cliente TEXT NOT NULL DEFAULT '',
                linx_atualizado_em TEXT NOT NULL DEFAULT '',
                atualizado_em TEXT NOT NULL DEFAULT '',
                atualizado_por TEXT NOT NULL DEFAULT ''
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
    ensure_shopee_escrow_schema()
    ensure_ml_billing_credito_schema()
    ensure_ml_billing_data_schema()
    ensure_cliente_fiscal_linx_api_schema()  # V18.60
    ensure_linx_operacoes_schema()  # V18.60
    ensure_linx_robo_schema()  # V18.60
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



ML_SKU_MLB_LINKS_STORAGE_KEY = "ml_sku_mlb_links_v1"
ML_SKU_MLB_SEED_PATH = ROOT_DIR / "DADOS" / "ml_sku_mlb_links_seed.json"
# V17.60: revisão da planilha Código do anúncio ↔ Código OEM/SKU enviada em 14/09/2026.
# O marcador faz a migração rodar UMA vez por revisão. Assim, se o ADMIN remover
# um vínculo depois da importação, um simples restart não recria o vínculo apagado.
ML_SKU_MLB_SEED_REVISION = "2026-09-14-oem-v4-read-repair-manual-tombstones"
ML_SKU_MLB_SEED_REVISION_KEY = "ml_sku_mlb_seed_revision_v1"



def _ml_normalize_link_sku(value: Any) -> str:
    return str(value or "").strip().upper()


def _ml_normalize_link_mlb(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return ("MLB" + digits) if digits else ""


def _ml_seed_links_normalized() -> dict[str, list[dict[str, Any]]]:
    """Lê a planilha/seed empacotada e devolve o mapa canônico SKU -> MLB."""
    if not ML_SKU_MLB_SEED_PATH.exists():
        return {}
    try:
        raw = json.loads(ML_SKU_MLB_SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for raw_sku, rows in raw.items():
        sku = _ml_normalize_link_sku(raw_sku)
        if not sku or not isinstance(rows, list):
            continue
        dest: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            obj = dict(row) if isinstance(row, dict) else {"mlb": row}
            mlb = _ml_normalize_link_mlb(obj.get("mlb"))
            if not mlb or mlb in seen:
                continue
            seen.add(mlb)
            obj["mlb"] = mlb
            obj.setdefault("sku", sku)
            obj.setdefault("source", "planilha_codigos_anuncio_oem_2026-09-14")
            obj.setdefault("importedAt", "2026-09-14")
            dest.append(obj)
        if dest:
            out[sku] = dest
    return out


def _ml_manual_link_tombstones() -> set[tuple[str, str]]:
    rows = DB.execute("SELECT sku,mlb FROM ml_sku_mlb_manual_tombstones").fetchall()
    return {
        (_ml_normalize_link_sku(r["sku"]), _ml_normalize_link_mlb(r["mlb"]))
        for r in rows
        if _ml_normalize_link_sku(r["sku"]) and _ml_normalize_link_mlb(r["mlb"])
    }


def _ml_merge_seed_links(links: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Reaplica a planilha em toda leitura, sem ressuscitar remoções manuais explícitas.

    A V17.60/V17.61 podia ser destruída por um navegador com cache parcial que salvava
    novamente o objeto inteiro. A partir da V17.62, a planilha é uma camada canônica de
    leitura e só um tombstone explícito pode ocultar um vínculo dela.
    """
    seed = _ml_seed_links_normalized()
    with DB_LOCK:
        tombstones = _ml_manual_link_tombstones()
    merged: dict[str, list[dict[str, Any]]] = {}
    indexes: dict[str, set[str]] = {}
    for raw_sku, rows in (links or {}).items():
        sku = _ml_normalize_link_sku(raw_sku)
        if not sku or not isinstance(rows, list):
            continue
        dest = merged.setdefault(sku, [])
        seen = indexes.setdefault(sku, set())
        for row in rows:
            obj = dict(row) if isinstance(row, dict) else {"mlb": row}
            mlb = _ml_normalize_link_mlb(obj.get("mlb"))
            if not mlb or (sku, mlb) in tombstones or mlb in seen:
                continue
            obj["mlb"] = mlb
            obj.setdefault("sku", sku)
            dest.append(obj)
            seen.add(mlb)
    for sku, rows in seed.items():
        dest = merged.setdefault(sku, [])
        seen = indexes.setdefault(sku, set())
        for row in rows:
            mlb = _ml_normalize_link_mlb(row.get("mlb"))
            if not mlb or (sku, mlb) in tombstones or mlb in seen:
                continue
            obj = dict(row)
            obj["mlb"] = mlb
            obj.setdefault("sku", sku)
            dest.append(obj)
            seen.add(mlb)
    return {sku: rows for sku, rows in merged.items() if rows}


def ml_precificador_manual_link_update(sku_value: Any, mlb_value: Any, action: str, actor: str) -> list[dict[str, Any]]:
    """Adiciona/remove um vínculo manual sem permitir que snapshots parciais apaguem o mapa inteiro."""
    sku = _ml_normalize_link_sku(sku_value)
    mlb = _ml_normalize_link_mlb(mlb_value)
    action = str(action or "").strip().lower()
    if not sku:
        raise ValueError("Informe o SKU.")
    if not mlb:
        raise ValueError("Informe um MLB válido.")
    if action not in {"add", "remove"}:
        raise ValueError("Ação de vínculo inválida.")

    with DB_LOCK:
        # Parte do estado efetivo + seed para nunca trabalhar sobre um snapshot parcial.
        links = _ml_precificador_persisted_links()
        rows = [dict(x) for x in links.get(sku, []) if isinstance(x, dict)]
        if action == "remove":
            DB.execute(
                """INSERT INTO ml_sku_mlb_manual_tombstones(sku,mlb,removed_at,removed_by)
                   VALUES(?,?,?,?)
                   ON CONFLICT(sku,mlb) DO UPDATE SET
                     removed_at=excluded.removed_at,removed_by=excluded.removed_by""",
                (sku, mlb, now_iso(), actor or "usuario"),
            )
            rows = [x for x in rows if _ml_normalize_link_mlb(x.get("mlb")) != mlb]
        else:
            DB.execute("DELETE FROM ml_sku_mlb_manual_tombstones WHERE sku=? AND mlb=?", (sku, mlb))
            existing = next((x for x in rows if _ml_normalize_link_mlb(x.get("mlb")) == mlb), None)
            if existing is None:
                rows.append({
                    "mlb": mlb,
                    "sku": sku,
                    "source": "manual_precificador",
                    "createdAt": now_iso(),
                    "createdBy": actor or "usuario",
                    "status": "Vinculado",
                })

        # Reconstroi o mapa canônico completo e grava uma versão consistente.
        current = _ml_precificador_persisted_links()
        current[sku] = rows
        if action == "remove" and not rows:
            current.pop(sku, None)
        current = _ml_merge_seed_links(current)
        # O tombstone foi criado antes do merge, então o seed removido não volta.
        raw = json_dumps(current)
        row = DB.execute("SELECT version FROM storage WHERE key=?", (ML_SKU_MLB_LINKS_STORAGE_KEY,)).fetchone()
        version = int(row["version"] or 0) + 1 if row else 1
        DB.execute(
            """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
            (ML_SKU_MLB_LINKS_STORAGE_KEY, raw, version, now_iso()),
        )
        # Remove override raiz legado do SKU. A partir daqui a decisão explícita é o tombstone/manual row.
        DB.execute(
            "DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id='__ROOT__' AND field_name=?",
            (ML_SKU_MLB_LINKS_STORAGE_KEY, sku),
        )
        DB.commit()

    effective = _ml_precificador_persisted_links()
    return effective.get(sku, [])


def _ml_seed_row_is_previous_import(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    source = str(row.get("source") or "").strip().lower()
    return source.startswith("planilha_") or source in {"planilha_importada", "seed_importado"}


def seed_ml_sku_mlb_links_from_file() -> dict[str, int]:
    """Aplica a relação SKU↔MLB da planilha diretamente na base canônica.

    V17.62:
    - lê o estado EFETIVO (storage + overrides do ADMIN), para não perder vínculos manuais;
    - reaplica a planilha de 14/09 em uma revisão nova, mesmo que a V17.60 tenha marcado a
      migração anterior como concluída;
    - grava o resultado no storage real e também atualiza o override raiz de cada SKU
      afetado. Isso elimina tombstones/snapshots antigos que faziam o Precificador receber
      "0 anúncios" apesar da planilha já ter sido importada;
    - preserva vínculos manuais/automáticos que não são seed antigo;
    - quando um MLB mudou de SKU entre planilhas, remove apenas o vínculo originado de
      seed/planilha anterior no SKU incorreto.
    """
    result = {
        "seed_skus": 0,
        "seed_links": 0,
        "added_links": 0,
        "removed_stale_links": 0,
        "updated_seed_links": 0,
        "override_skus": 0,
        "already_applied": 0,
    }
    if not ML_SKU_MLB_SEED_PATH.exists():
        return result

    try:
        seed = json.loads(ML_SKU_MLB_SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return result
    if not isinstance(seed, dict):
        return result

    normalized_seed: dict[str, list[dict[str, Any]]] = {}
    authoritative_mlb_skus: dict[str, set[str]] = {}
    for raw_sku, seed_rows in seed.items():
        sku = str(raw_sku or "").strip().upper()
        if not sku or not isinstance(seed_rows, list):
            continue
        target = normalized_seed.setdefault(sku, [])
        seen: set[str] = set()
        for row in seed_rows:
            obj = dict(row) if isinstance(row, dict) else {"mlb": row}
            digits = re.sub(r"\D+", "", str(obj.get("mlb") or ""))
            if not digits:
                continue
            mlb = "MLB" + digits
            if mlb in seen:
                continue
            seen.add(mlb)
            obj["mlb"] = mlb
            obj.setdefault("source", "planilha_codigos_anuncio_oem_2026-09-14")
            obj.setdefault("importedAt", "2026-09-14")
            target.append(obj)
            authoritative_mlb_skus.setdefault(mlb, set()).add(sku)

    result["seed_skus"] = len(normalized_seed)
    result["seed_links"] = sum(len(v) for v in normalized_seed.values())

    with DB_LOCK:
        marker_row = DB.execute(
            "SELECT value FROM storage WHERE key=?", (ML_SKU_MLB_SEED_REVISION_KEY,)
        ).fetchone()
        marker_raw = str(marker_row["value"] or "") if marker_row else ""
        if marker_raw.strip() == ML_SKU_MLB_SEED_REVISION:
            result["already_applied"] = 1
            return result

        manual_tombstones = _ml_manual_link_tombstones()

        current_row = DB.execute(
            "SELECT value,version FROM storage WHERE key=?", (ML_SKU_MLB_LINKS_STORAGE_KEY,)
        ).fetchone()
        current_raw = str(current_row["value"] or "{}") if current_row else "{}"
        version = int(current_row["version"] or 0) if current_row else 0

        # A causa do V17.60 não aparecer na tela podia ser um override/tombstone antigo
        # do próprio ml_sku_mlb_links_v1. Para preservar edições manuais e, ao mesmo tempo,
        # cumprir a importação explícita solicitada pelo ADMIN, partimos do valor EFETIVO.
        effective_raw = current_raw
        try:
            if "_admin_override_apply" in globals():
                effective_raw = _admin_override_apply(ML_SKU_MLB_LINKS_STORAGE_KEY, current_raw)
        except Exception as exc:
            print(f"Aviso: override MLB/SKU não pôde ser lido antes da migração: {exc}", file=sys.stderr)

        try:
            current = json.loads(effective_raw or "{}")
        except Exception:
            current = {}
        if not isinstance(current, dict):
            current = {}

        cleaned: dict[str, list[dict[str, Any]]] = {}
        by_sku_mlb: dict[str, dict[str, dict[str, Any]]] = {}
        affected_skus: set[str] = set(normalized_seed.keys())
        changed = False

        for raw_sku, existing_rows in current.items():
            sku = str(raw_sku or "").strip().upper()
            if not sku or not isinstance(existing_rows, list):
                continue
            dest = cleaned.setdefault(sku, [])
            index = by_sku_mlb.setdefault(sku, {})

            for row in existing_rows:
                obj = dict(row) if isinstance(row, dict) else {"mlb": row}
                digits = re.sub(r"\D+", "", str(obj.get("mlb") or ""))
                if not digits:
                    continue
                mlb = "MLB" + digits
                obj["mlb"] = mlb

                wanted_skus = authoritative_mlb_skus.get(mlb)
                if wanted_skus and sku not in wanted_skus and _ml_seed_row_is_previous_import(obj):
                    result["removed_stale_links"] += 1
                    affected_skus.add(sku)
                    changed = True
                    continue

                if mlb in index:
                    changed = True
                    continue
                index[mlb] = obj
                dest.append(obj)

        for sku, seed_rows in normalized_seed.items():
            dest = cleaned.setdefault(sku, [])
            index = by_sku_mlb.setdefault(sku, {})
            for seed_obj in seed_rows:
                mlb = seed_obj["mlb"]
                if (sku, mlb) in manual_tombstones:
                    continue
                existing = index.get(mlb)
                if existing is None:
                    obj = dict(seed_obj)
                    dest.append(obj)
                    index[mlb] = obj
                    result["added_links"] += 1
                    changed = True
                    continue
                if _ml_seed_row_is_previous_import(existing):
                    new_source = str(seed_obj.get("source") or "planilha_codigos_anuncio_oem_2026-09-14")
                    new_date = str(seed_obj.get("importedAt") or "2026-09-14")
                    if existing.get("source") != new_source or existing.get("importedAt") != new_date:
                        existing["source"] = new_source
                        existing["importedAt"] = new_date
                        result["updated_seed_links"] += 1
                        changed = True

        new_value = json_dumps(cleaned)
        if changed or current_raw != new_value:
            DB.execute(
                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value,
                     version=excluded.version,
                     updated_at=excluded.updated_at""",
                (ML_SKU_MLB_LINKS_STORAGE_KEY, new_value, int(version or 0) + 1, now_iso()),
            )

        # IMPORTANTe: o sistema global "ADMIN sempre prevalece" reaplica overrides em toda
        # leitura. Por isso não basta alterar a tabela storage. Esta importação foi solicitada
        # explicitamente pelo ADMIN, então ela também precisa substituir tombstones/snapshots
        # antigos SOMENTE nos SKUs afetados, preservando as linhas manuais já mescladas acima.
        for sku in sorted(affected_skus):
            DB.execute(
                "DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id='__ROOT__' AND field_name=?",
                (ML_SKU_MLB_LINKS_STORAGE_KEY, sku),
            )
            if sku in cleaned:
                _admin_override_upsert(
                    ML_SKU_MLB_LINKS_STORAGE_KEY,
                    "__ROOT__",
                    sku,
                    cleaned[sku],
                    deleted=False,
                    actor="MIGRACAO_V17_62_PLANILHA_14_09",
                )
            else:
                _admin_override_upsert(
                    ML_SKU_MLB_LINKS_STORAGE_KEY,
                    "__ROOT__",
                    sku,
                    None,
                    deleted=True,
                    actor="MIGRACAO_V17_62_PLANILHA_14_09",
                )
            result["override_skus"] += 1

        marker_row = DB.execute(
            "SELECT version FROM storage WHERE key=?", (ML_SKU_MLB_SEED_REVISION_KEY,)
        ).fetchone()
        marker_version = int(marker_row[0] or 0) + 1 if marker_row else 1
        DB.execute(
            """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 version=excluded.version,
                 updated_at=excluded.updated_at""",
            (ML_SKU_MLB_SEED_REVISION_KEY, ML_SKU_MLB_SEED_REVISION, marker_version, now_iso()),
        )
        DB.commit()

    print(
        "V17.62 MLB/SKU: "
        f"{result['seed_links']} vínculos / {result['seed_skus']} SKUs; "
        f"{result['added_links']} adicionados; {result['removed_stale_links']} antigos removidos; "
        f"{result['override_skus']} SKUs consolidados no override canônico.",
        file=sys.stderr,
    )
    return result

def ensure_storage_defaults() -> None:
    defaults = {
        "conf_pedidos": "[]",
        "conf_pedidos_rascunhos": "[]",
        "conf_usuarios": "[]",
        "conf_logs": "[]",
        "conf_historico_conferencias_v2": "[]",
        "precificador_online_v1": "",
        "ml_sku_mlb_links_v1": "{}",
        "conf_coleta_dia_v1": "{}",
        "conf_nomes_conferencia_v1": "{}",
    }
    with DB_LOCK:
        ts = now_iso()
        for key, value in defaults.items():
            DB.execute(
                "INSERT OR IGNORE INTO storage(key,value,version,updated_at) VALUES(?,?,1,?)",
                (key, value, ts),
            )
        DB.commit()
    # V17.60: aplica uma vez a revisão atual da planilha SKU↔MLB.
    seed_ml_sku_mlb_links_from_file()


def is_owner_username(username: str) -> bool:
    return bool(OWNER_USERNAME) and str(username or "").strip().lower() == OWNER_USERNAME


def require_owner_context(ctx: dict[str, Any] | None) -> bool:
    return bool(ctx and is_owner_username(str(ctx.get("username") or "")))


def _b32decode_nopad(value: str) -> bytes:
    value = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    value += "=" * ((8 - len(value) % 8) % 8)
    return base64.b32decode(value, casefold=True)

def totp_code(secret: str, when: int | None = None) -> str:
    if not secret: return ""
    counter = int((when or int(time.time())) // 30)
    digest = hmac.new(_b32decode_nopad(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 15
    return str((struct.unpack(">I", digest[off:off+4])[0] & 0x7fffffff) % 1000000).zfill(6)

def verify_totp(secret: str, code: str) -> bool:
    code = re.sub(r"\D", "", str(code or ""))
    now = int(time.time())
    return len(code) == 6 and bool(secret) and any(hmac.compare_digest(totp_code(secret, now + x*30), code) for x in (-1,0,1))

def recovery_hash(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().upper().encode()).hexdigest().lower()

def verify_owner_second_factor(code: str) -> tuple[bool, str]:
    if verify_totp(OWNER_TOTP_SECRET, code): return True, "totp"
    if recovery_hash(code) in OWNER_RECOVERY_HASHES: return True, "recovery"
    return False, ""

def owner_reauth_rate_key(ctx: dict[str, Any] | None) -> str:
    return str((ctx or {}).get("session_key") or (ctx or {}).get("username") or "owner")

def owner_reauth_limited(ctx: dict[str, Any] | None) -> bool:
    now = time.time()
    key = owner_reauth_rate_key(ctx)
    with AUTH_LOCK:
        recent = [x for x in OWNER_REAUTH_FAILURES.get(key, []) if x > now - 300]
        if recent:
            OWNER_REAUTH_FAILURES[key] = recent
        else:
            OWNER_REAUTH_FAILURES.pop(key, None)
        # Também elimina autorizações críticas expiradas.
        expired = [k for k, until in CRITICAL_REAUTH.items() if until < now]
        for k in expired:
            CRITICAL_REAUTH.pop(k, None)
        return len(recent) >= 5

def owner_reauth_failure(ctx: dict[str, Any] | None) -> None:
    with AUTH_LOCK:
        OWNER_REAUTH_FAILURES.setdefault(owner_reauth_rate_key(ctx), []).append(time.time())

def owner_reauth_clear(ctx: dict[str, Any] | None) -> None:
    with AUTH_LOCK:
        OWNER_REAUTH_FAILURES.pop(owner_reauth_rate_key(ctx), None)

def verify_user_password(username: str, password: str) -> bool:
    with DB_LOCK:
        row = DB.execute("SELECT * FROM app_users WHERE username=?", (str(username or "").strip().lower(),)).fetchone()
    return bool(row and row["active"] and password_matches(str(password or ""), row))

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
    owner_account = is_owner_username(username)
    if owner_account:
        role = "admin"
        active = True
        permissions = {"conferencia": True, "admin": True, "estacao": True, "precificador": True}
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
            "Perfil": ("Admin" if row["role"] == "admin" else ("Supervisor" if is_supervisor_user(row) else "Operador")),
            "Owner": is_owner_username(row["username"]),
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


BACKUP_DAILY_KEEP = 3  # V18.11: quantos diários comprimidos manter (decisão do dono)


# V18.56: o backup tem trava PRÓPRIA. A thread do boot e o primeiro save do dia
# chamavam backup_daily() juntos; a segunda execução apagava o .tmp da primeira
# no meio da cópia.
BACKUP_LOCK = threading.Lock()


def _backup_alvo_hoje() -> Path:
    return BACKUP_DIR / f"expedicao_{datetime.now().strftime('%Y-%m-%d')}.db.gz"


def backup_daily_em_segundo_plano() -> None:
    """V18.56: chamado pelas requisições. Nunca faz a cópia dentro da requisição do
    usuário — antes o primeiro save do dia copiava ~1 GB e a bipagem parava."""
    if BACKUP_LOCK.locked():
        return
    try:
        if _backup_alvo_hoje().exists():
            return
    except Exception:
        return
    threading.Thread(target=backup_daily, name="backup-diario", daemon=True).start()


def backup_daily() -> None:
    if not BACKUP_LOCK.acquire(blocking=False):
        return  # outra execução já está fazendo o backup de hoje
    try:
        _backup_daily_locked()
    finally:
        BACKUP_LOCK.release()


def _backup_daily_locked() -> None:
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dia = datetime.now().strftime("%Y-%m-%d")
        alvo_gz = BACKUP_DIR / f"expedicao_{dia}.db.gz"
        if alvo_gz.exists():
            return
        # V18.11: comprimido, cada diário cai de ~1 GB para ~75 MB; mantém só os
        # BACKUP_DAILY_KEEP mais recentes.
        tmp = BACKUP_DIR / f"expedicao_{dia}.db.tmp"
        gz_tmp = BACKUP_DIR / f"expedicao_{dia}.db.gz.tmp"
        for lixo in list(BACKUP_DIR.glob("expedicao_*.db.tmp")) + list(BACKUP_DIR.glob("expedicao_*.db.gz.tmp")):
            lixo.unlink(missing_ok=True)
        # V18.56: a cópia usa uma conexão de LEITURA própria, sem o DB_LOCK. O banco
        # está em WAL: o backup lê um retrato consistente do que já foi gravado e as
        # escritas da operação continuam livres. Antes a cópia de ~1 GB segurava o
        # DB_LOCK e toda bipagem esperava.
        src = sqlite3.connect(DB_PATH, timeout=30)
        try:
            dest = sqlite3.connect(tmp)
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()
        # Verifica que o backup abre e tem dados ANTES de comprimir.
        conf = sqlite3.connect(tmp)
        try:
            tem_dados = conf.execute("SELECT COUNT(*) FROM storage").fetchone()[0] > 0
        finally:
            conf.close()
        if not tem_dados:
            tmp.unlink(missing_ok=True)
            print("Aviso: backup diário sem dados de storage; ignorado.", file=sys.stderr)
            return
        # V18.56: comprime num .gz.tmp e só então troca de nome. Gravando direto no
        # nome final, uma queda no meio deixava um .gz cortado que "contava" como o
        # backup do dia — e a retenção podia apagar os bons.
        with open(tmp, "rb") as f_in, gzip.open(gz_tmp, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, 1024 * 1024)
        os.replace(gz_tmp, alvo_gz)
        tmp.unlink(missing_ok=True)
        # Retenção: mantém os N diários comprimidos mais recentes.
        comprimidos = sorted(
            BACKUP_DIR.glob("expedicao_????-??-??.db.gz"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for antigo in comprimidos[BACKUP_DAILY_KEEP:]:
            antigo.unlink(missing_ok=True)
        # Limpa diários antigos NÃO comprimidos (formato anterior, ~1 GB cada).
        for cru in BACKUP_DIR.glob("expedicao_????-??-??.db"):
            cru.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Aviso: backup diário não realizado: {exc}", file=sys.stderr)


def get_storage(key: str) -> tuple[str | None, int, str | None]:
    # V18.06: toda a leitura roda sob DB_LOCK. A conexão SQLite é COMPARTILHADA
    # entre as threads do ThreadingHTTPServer (check_same_thread=False) e as
    # escritas já usam o lock; sem ele, get_storage/completed_keys corriam com as
    # escritas e a conexão devolvia linhas corrompidas (IndexError em row[0]),
    # derrubando de forma intermitente o import da Shopee e a leitura de etiquetas.
    # DB_LOCK é RLock (re-entrante), então as chamadas internas que também o pegam
    # continuam funcionando.
    with DB_LOCK:
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
        # V17.50: o servidor é canônico e o ADMIN prevalece também na LEITURA.
        # Mesmo que algum job legado grave diretamente em storage, os overrides/tombstones
        # persistentes são reaplicados antes de qualquer tela receber o banco.
        # V18.56: cada etapa tem o seu próprio try. Antes um único try cobria todas,
        # e uma exceção no cofre ou no tombstone PULAVA o filtro final de concluídos —
        # o pedido reinjetado pelo override/cofre voltava para a Conferência.
        etapas = []
        if value is not None and "_admin_override_apply" in globals():
            etapas.append(("override", lambda v: _admin_override_apply(key, v)))
        if key == "conf_pedidos" and "_admin_manual_order_vault_apply" in globals():
            etapas.append(("cofre manual", _admin_manual_order_vault_apply))
        if key in ("conf_pedidos", "conf_pedidos_rascunhos") and "_pending_filter_manual_deleted_orders_value" in globals():
            etapas.append(("tombstone", _pending_filter_manual_deleted_orders_value))
        # V17.82: conclusão é a ÚLTIMA barreira da fila também na LEITURA, exatamente
        # como save_storage() já faz na escrita. Sem isto, o override __ENTITY__ e o
        # cofre manual reinjetavam um pedido já concluído (eles rodam depois do
        # filter_completed_orders acima), e o pedido voltava para a Conferência.
        # A reimportação explícita do ADMIN continua possível porque
        # reactivate_completed_orders_from_delta remove o registro de completed_orders.
        if key in ("conf_pedidos", "conf_pedidos_rascunhos") and "filter_completed_orders_v1772" in globals():
            etapas.append(("concluidos", filter_completed_orders_v1772))
        for nome_etapa, etapa in etapas:
            try:
                value = etapa(value)
            except Exception as exc:
                print(f"Aviso: etapa '{nome_etapa}' não aplicada na leitura de {key}: {exc}", file=sys.stderr)
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



def preserve_conference_names(current_value: str | None, new_value: str) -> str:
    """Preserva o Nome Conferência em atualizações automáticas/concorrentes.

    Esse campo é manual e não deve ser apagado por sincronizações do Mercado Livre
    ou por uma tela que ainda esteja usando um snapshot anterior do pedido.
    """
    try:
        current_rows = json.loads(current_value or "[]")
        new_rows = json.loads(new_value or "[]")
    except Exception:
        return new_value
    if not isinstance(current_rows, list) or not isinstance(new_rows, list):
        return new_value

    by_uid: dict[str, str] = {}
    by_key: dict[tuple[str, str], str] = {}
    by_order: dict[str, str] = {}
    for row in current_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Nome Conferência") or "").strip()
        if not name:
            continue
        uid = str(row.get("__uidLinha") or row.get("__uidPedido") or "").strip()
        if uid:
            by_uid[uid] = name
        order = normalize(order_number(row))
        sku = normalize(row.get("SKU") or row.get("Código de Barras"))
        if order and sku:
            by_key[(order, sku)] = name
        if order and order not in by_order:
            by_order[order] = name

    changed = False
    for row in new_rows:
        if not isinstance(row, dict):
            continue
        # Campo explicitamente preenchido/vazio por uma edição manual deve prevalecer
        # apenas quando a própria requisição realmente o contém.
        if "Nome Conferência" in row and str(row.get("Nome Conferência") or "").strip():
            continue
        uid = str(row.get("__uidLinha") or row.get("__uidPedido") or "").strip()
        order = normalize(order_number(row))
        sku = normalize(row.get("SKU") or row.get("Código de Barras"))
        name = (by_uid.get(uid) if uid else "") or by_key.get((order, sku), "") or by_order.get(order, "")
        if name:
            row["Nome Conferência"] = name
            changed = True
    return json_dumps(new_rows) if changed else new_value



def preserve_label_records(current_value: str | None, new_value: str) -> str:
    """Protege conf_etiquetas contra exclusões acidentais por telas com snapshot antigo.

    Escritas normais podem adicionar/atualizar vínculos, mas não podem apagar
    registros existentes. Remoção de etiqueta deve ser explícita no endpoint.
    """
    try:
        current = json.loads(current_value or "{}")
        newer = json.loads(new_value or "{}")
    except Exception:
        return new_value
    if not isinstance(current, dict) or not isinstance(newer, dict):
        return new_value

    current_records = current.get("registros") if isinstance(current.get("registros"), dict) else {}
    new_records = newer.get("registros") if isinstance(newer.get("registros"), dict) else {}
    changed = False
    for ident, record in current_records.items():
        if ident not in new_records:
            new_records[ident] = record
            changed = True

    if changed:
        newer["registros"] = new_records
        current_order = current.get("ordem") if isinstance(current.get("ordem"), list) else list(current_records)
        new_order = newer.get("ordem") if isinstance(newer.get("ordem"), list) else list(new_records)
        ordem = []
        for ident in [*current_order, *new_order, *new_records.keys()]:
            ident = str(ident)
            if ident in new_records and ident not in ordem:
                ordem.append(ident)
        newer["ordem"] = ordem
        newer = rebuild_label_bank(newer)
        newer["atualizadoEm"] = str(newer.get("atualizadoEm") or now_iso())
        return json_dumps(newer)
    return new_value



def _pending_identity_v1714(row: dict[str, Any], index: int = 0) -> str:
    def pick(*keys):
        for k in keys:
            v=row.get(k)
            if v is not None and str(v).strip(): return str(v).strip()
        return ""
    # V17.46: a identidade persistente da linha é o UID quando disponível.
    # O SKU é editável e NÃO pode fazer a mesma linha parecer um pedido novo,
    # senão preserve_pending_records reaplica a cópia antiga e desfaz a edição.
    uid=pick("__uidLinha","__uidPedido")
    if uid: return f"UID|{uid}".upper()
    market=pick("Marketplace","marketplace","Origem","origem","Canal","canal").upper()
    order=pick("OrderID","order_id","Pedido","Nº Pedido","NumeroPedido","Venda","VendaID")
    pack=pick("PackID","pack_id","Pacote","PacoteID")
    item=pick("ItemID","item_id","MLB","CodigoAnuncio")
    sku=pick("SKU","Sku","sku","Código","Codigo")
    if order or pack: return f"{market}|{order}|{pack}|{item}|{sku}".upper()
    created=pick("CriadoEm","criadoEm","AdicionadoEm","adicionadoEm","Data","data")
    return f"MANUAL|{market}|{item}|{sku}|{created}|{index}".upper()

def _ml_legacy_sku_pair(a: Any, b: Any) -> bool:
    """Reconhece somente o caso legado SKU curto x SKU real prefixado.

    Ex.: 0893117732 <-> 158529-0893117732. Não considera SKUs iguais nem
    equivalências genéricas, para não colapsar dois itens legítimos do pedido.
    """
    sa = str(a or "").strip()
    sb = str(b or "").strip()
    if not sa or not sb or normalize(sa) == normalize(sb):
        return False
    long_s, short_s = (sa, sb) if len(sa) > len(sb) else (sb, sa)
    if "-" not in long_s:
        return False
    prefix, remainder = long_s.split("-", 1)
    return bool(prefix.strip() and normalize(remainder) == normalize(short_s))


def _pending_ml_row(row: dict[str, Any]) -> bool:
    market = normalize(row.get("Marketplace"))
    origem = normalize(row.get("OrigemCadastro"))
    return bool(
        "MERCADO" in market
        or str(row.get("VendaMercadoLivre") or "").strip()
        or "MERCADO LIVRE" in origem
    )


def _same_pending_order(a: dict[str, Any], b: dict[str, Any]) -> bool:
    va = str(a.get("VendaMercadoLivre") or "").strip()
    vb = str(b.get("VendaMercadoLivre") or "").strip()
    if va and vb:
        return va == vb
    return normalize(order_number(a)) == normalize(order_number(b)) and bool(normalize(order_number(a)))


def _dedupe_ml_legacy_sku_rows(rows: list[Any]) -> list[Any]:
    """Remove duplicação histórica criada pela mudança SKU curto -> SKU real.

    Mantém a linha manual quando existir; caso contrário prefere o SKU operacional
    sem o prefixo numérico do anúncio. Quantidade não é somada porque representam a mesma
    venda/item. Campos vazios da linha escolhida são enriquecidos pela descartada.
    """
    result: list[Any] = []
    for raw in rows:
        if not isinstance(raw, dict) or not _pending_ml_row(raw):
            result.append(raw)
            continue
        duplicate_index = None
        for i, existing in enumerate(result):
            if not isinstance(existing, dict) or not _pending_ml_row(existing):
                continue
            if not _same_pending_order(existing, raw):
                continue
            if not _ml_legacy_sku_pair(existing.get("SKU"), raw.get("SKU")):
                continue
            q1 = str(existing.get("Quantidade") or "1").strip()
            q2 = str(raw.get("Quantidade") or "1").strip()
            if q1 != q2:
                continue
            duplicate_index = i
            break
        if duplicate_index is None:
            result.append(raw)
            continue

        existing = result[duplicate_index]
        ex_manual = normalize(existing.get("SkuManualOverride")) == "SIM"
        raw_manual = normalize(raw.get("SkuManualOverride")) == "SIM"
        if ex_manual and raw_manual:
            # Duas correções manuais distintas nunca são colapsadas automaticamente.
            result.append(raw)
            continue
        if raw_manual and not ex_manual:
            keep, other = raw, existing
        elif ex_manual:
            keep, other = existing, raw
        else:
            ex_sku = str(existing.get("SKU") or "")
            raw_sku = str(raw.get("SKU") or "")
            # V17.49: o SKU operacional é o trecho após o prefixo numérico do ML.
            # Na migração do legado, portanto, preferimos a forma curta/efetiva.
            keep, other = (raw, existing) if len(raw_sku) < len(ex_sku) else (existing, raw)
        for k, v in other.items():
            if k not in keep or not str(keep.get(k) or "").strip():
                keep[k] = v
        keep["Lido"] = max(int(keep.get("Lido") or 0), int(other.get("Lido") or 0))
        result[duplicate_index] = keep
    return result



def preserve_conference_progress_v1770(current_value: str | None, new_value: str) -> str:
    """V17.70 — progresso de bip é monotônico enquanto o pedido estiver pendente.

    Qualquer sync/import/poll que escreva uma cópia antiga de conf_pedidos não pode
    reduzir Lido de uma linha já conferida. A identidade prioriza __uidLinha/__uidPedido,
    a mesma usada pelo merge persistente de pedidos.
    """
    try:
        current = json.loads(current_value or "[]")
        newer = json.loads(new_value or "[]")
    except Exception:
        return new_value
    if not isinstance(current, list) or not isinstance(newer, list):
        return new_value

    current_by_id: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(current):
        if not isinstance(row, dict):
            continue
        current_by_id[_pending_identity_v1714(row, i)] = row

    changed = False
    for i, row in enumerate(newer):
        if not isinstance(row, dict):
            continue
        prev = current_by_id.get(_pending_identity_v1714(row, i))
        if not prev:
            continue
        try:
            old_lido = max(0, int(prev.get("Lido") or 0))
        except Exception:
            old_lido = 0
        try:
            new_lido = max(0, int(row.get("Lido") or 0))
        except Exception:
            new_lido = 0
        if old_lido > new_lido:
            row["Lido"] = old_lido
            changed = True

        # Metadados de conferência já confirmados também não podem ser apagados por
        # snapshots antigos de integrações.
        for field in (
            "Conferido", "ConferidoEm", "ConferidoPor",
            "StatusConferencia", "FinalizadoConferenciaEm"
        ):
            prev_value = prev.get(field)
            cur_value = row.get(field)
            if prev_value not in (None, "", False) and cur_value in (None, "", False):
                row[field] = prev_value
                changed = True

    return json_dumps(newer) if changed else new_value



def filter_completed_orders_v1772(value: str) -> str:
    """V17.72 — pedido concluído não volta para a fila ativa.

    completed_orders é a autoridade canônica de conclusão. Qualquer importador,
    polling ou snapshot antigo que tente reintroduzir um pedido já concluído tem
    essas linhas removidas antes de gravar conf_pedidos.
    """
    try:
        rows = json.loads(value or "[]")
        if not isinstance(rows, list) or not rows:
            return value
        completed = {
            str(r["order_key"] or "")
            for r in DB.execute("SELECT order_key FROM completed_orders").fetchall()
            if str(r["order_key"] or "")
        }
        if not completed:
            return value
        kept = [
            row for row in rows
            if not (
                isinstance(row, dict)
                and normalize(order_number(row)) in completed
            )
        ]
        return json_dumps(kept) if len(kept) != len(rows) else value
    except Exception:
        return value


def preserve_pending_records_v1714(current_value: str | None, new_value: str) -> str:
    try:
        current=json.loads(current_value or "[]"); newer=json.loads(new_value or "[]")
    except Exception: return new_value
    if not isinstance(current,list) or not isinstance(newer,list): return new_value
    ids={_pending_identity_v1714(r,i) for i,r in enumerate(newer) if isinstance(r,dict)}
    # V17.72: completed_orders é tombstone canônico da fila de Conferência.
    try:
        completed_keys={
            str(r["order_key"] or "")
            for r in DB.execute("SELECT order_key FROM completed_orders").fetchall()
            if str(r["order_key"] or "")
        }
    except Exception:
        completed_keys=set()
    changed=False
    for i,row in enumerate(current):
        if not isinstance(row,dict): continue
        if normalize(order_number(row)) in completed_keys:
            changed=True
            continue
        ident=_pending_identity_v1714(row,i)
        if ident not in ids:
            # V17.49: não ressuscita a cópia antiga de um item ML quando a lista
            # nova já contém o mesmo pedido com o SKU operacional equivalente.
            # Migração segura do legado 158529-089... -> 089....
            if _pending_ml_row(row) and normalize(row.get("SkuManualOverride")) != "SIM":
                equivalent = any(
                    isinstance(n,dict)
                    and _pending_ml_row(n)
                    and _same_pending_order(row,n)
                    and _ml_legacy_sku_pair(row.get("SKU"),n.get("SKU"))
                    and str(row.get("Quantidade") or "1").strip() == str(n.get("Quantidade") or "1").strip()
                    for n in newer
                )
                if equivalent:
                    continue
            newer.append(row);ids.add(ident);changed=True
    deduped=_dedupe_ml_legacy_sku_rows(newer)
    if deduped != newer:
        newer=deduped;changed=True
    return json_dumps(newer) if changed else new_value

# V18.49: o banco do Precificador é UM objeto {"products": [...], "selectedId", ...}.
# O merge abaixo só juntava listas; para objeto ficava com a última gravação
# INTEIRA. Como o Precificador grava o banco todo até ao clicar num produto (ou no
# F5), quem abria a tela com uma cópia antiga apagava o que o outro acabara de
# alterar. Agora cada gravação aplica só o que ELA mudou em relação ao que tinha
# lido (old): produto por produto (pelo _id) e campo por campo. Mesmo campo
# alterado pelos dois = vale a última gravação.
_PRECIFICADOR_SEM_CAMPO = object()


def _precificador_produto_id(produto: Any, index: int) -> str:
    if isinstance(produto, dict):
        if str(produto.get("_id") or "").strip():
            return "id:" + str(produto["_id"]).strip()
        if str(produto.get("sku") or "").strip():
            return "sku:" + normalize(produto["sku"])
    return "hash:" + hashlib.sha1(json_dumps(produto).encode()).hexdigest()


def _merge_campos_3vias(atual: dict[str, Any], antigo: dict[str, Any], novo: dict[str, Any]) -> dict[str, Any]:
    resultado = dict(atual)
    for campo in set(antigo) | set(novo):
        valor_novo = novo.get(campo, _PRECIFICADOR_SEM_CAMPO)
        if valor_novo == antigo.get(campo, _PRECIFICADOR_SEM_CAMPO):
            continue
        if valor_novo is _PRECIFICADOR_SEM_CAMPO:
            resultado.pop(campo, None)
        else:
            resultado[campo] = valor_novo
    return resultado


def merge_precificador(current: dict[str, Any], old: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
    # Produto sem _id em algum dos três lados (banco antigo; a tela cria o _id na
    # primeira gravação): esse SKU é casado pelo SKU nos três, senão o mesmo
    # produto viraria "excluído + novo" e a gravação passaria por cima do outro.
    sem_id = {
        normalize(p.get("sku"))
        for banco in (current, old, newer)
        for p in (banco.get("products") if isinstance(banco.get("products"), list) else [])
        if isinstance(p, dict) and not str(p.get("_id") or "").strip() and str(p.get("sku") or "").strip()
    }

    def mapa(banco: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        lista = banco.get("products") if isinstance(banco.get("products"), list) else []
        ids = [("sku:" + normalize(p.get("sku"))) if isinstance(p, dict) and normalize(p.get("sku")) in sem_id
               else _precificador_produto_id(p, i) for i, p in enumerate(lista)]
        return ids, dict(zip(ids, lista))

    atual_ids, atual = mapa(current)
    _, antigos = mapa(old)
    novos_ids, novos = mapa(newer)
    for ident in antigos:
        if ident not in novos:
            atual.pop(ident, None)  # excluído por esta gravação
    acrescentados: list[str] = []
    for ident in novos_ids:
        novo = novos[ident]
        antigo = antigos.get(ident)
        if antigo is None or ident not in atual:
            # Produto novo desta gravação (ou editado aqui depois de excluído por
            # outra pessoa: a edição explícita prevalece, como em merge_arrays).
            if antigo is None or antigo != novo:
                atual[ident] = novo
                acrescentados.append(ident)
            continue
        if antigo != novo:
            if isinstance(atual[ident], dict) and isinstance(antigo, dict) and isinstance(novo, dict):
                atual[ident] = _merge_campos_3vias(atual[ident], antigo, novo)
            else:
                atual[ident] = novo
    ordem: list[str] = []
    for ident in atual_ids + acrescentados:
        if ident in atual and ident not in ordem:
            ordem.append(ident)
    produtos_antigos = {k: v for k, v in current.items() if k != "products"}
    resultado = _merge_campos_3vias(produtos_antigos, {k: v for k, v in old.items() if k != "products"},
                                    {k: v for k, v in newer.items() if k != "products"})
    resultado["products"] = [atual[ident] for ident in ordem]
    return resultado


def merge_value(key: str, current: str | None, old: str | None, newer: str, force: bool) -> str:
    if force or current is None or current == old:
        return newer
    try:
        current_obj = json.loads(current)
        old_obj = json.loads(old or "")
        new_obj = json.loads(newer)
        if all(isinstance(x, list) for x in (current_obj, old_obj, new_obj)):
            return json_dumps(merge_arrays(key, current_obj, old_obj, new_obj))
        if key == "precificador_online_v1" and all(
            isinstance(x, dict) and isinstance(x.get("products"), list) for x in (current_obj, old_obj, new_obj)
        ):
            return json_dumps(merge_precificador(current_obj, old_obj, new_obj))
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
    # V18.06: sob DB_LOCK e com fetchall — a conexão SQLite compartilhada corria
    # com as escritas e o cursor devolvia tuplas vazias (IndexError em row[0]).
    with DB_LOCK:
        return {row[0] for row in DB.execute("SELECT order_key FROM completed_orders").fetchall()}


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


def _marketplace_de_pedido_concluido(payload_json: str) -> str:
    """Marketplace de um pedido concluído: lê do payload salvo na conclusão. Se
    vier vazio, devolve "" e o front infere pelo número do pedido."""
    try:
        rows = json.loads(payload_json or "[]")
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    mp = str(r.get("Marketplace") or r.get("marketplace") or "").strip()
                    if mp:
                        return mp
    except Exception:
        pass
    return ""


def conferencia_conclusoes_do_dia(horas: int = 48) -> list[dict[str, str]]:
    """V18.08 — conclusões recentes lidas do servidor (completed_orders), que é a
    fonte COMPARTILHADA entre máquinas. O painel do dia da Conferência conta os
    "conferidos" daqui, para o número aparecer igual em qualquer computador. Antes
    o painel contava do historicoConferencias, que é local do navegador: quem
    conferia numa máquina não aparecia no contador de outra. O corte de "hoje"
    fica no front (fuso do próprio navegador), igual ao lado dos pendentes; aqui
    devolvemos uma janela ampla (48h) para o front filtrar."""
    corte = (datetime.now(timezone.utc).astimezone() - timedelta(hours=horas)).isoformat(timespec="milliseconds")
    with DB_LOCK:
        rows = DB.execute(
            "SELECT order_id, completed_at, completed_by, payload FROM completed_orders "
            "WHERE completed_at >= ? ORDER BY completed_at DESC LIMIT 5000",
            (corte,),
        ).fetchall()
    saida: list[dict[str, str]] = []
    for r in rows:
        saida.append({
            "pedido": str(r["order_id"] or ""),
            "marketplace": _marketplace_de_pedido_concluido(str(r["payload"] or "")),
            "concluidoEm": str(r["completed_at"] or ""),
            "concluidoPor": str(r["completed_by"] or ""),
        })
    return saida


# V17.48 — ADMIN SEMPRE PREVALECE
# Toda ação explícita feita por um administrador através do storage vira uma
# sobreposição persistente. Jobs, importações e sincronizações podem continuar
# atualizando dados automáticos, mas jamais sobrescrevem um campo alterado pelo
# admin, recriam uma entidade removida por ele ou apagam uma entidade adicionada.
def _admin_override_entity_id(storage_key: str, item: Any, index: int = 0) -> str:
    if not isinstance(item, dict):
        return f"IDX:{index}"
    if storage_key == "conf_pedidos":
        return _pending_identity_v1714(item, index)
    candidates = (
        "__uidLinha", "__uidPedido", "uid", "uuid", "id", "ID", "Id",
        "productId", "produtoId", "mlb", "MLB", "codigo", "Codigo", "Código",
        "Pedido", "pedido", "OrderID", "order_id", "sku", "SKU", "Sku",
        "ean", "EAN", "key", "chave", "nome", "Nome"
    )
    for k in candidates:
        v = item.get(k)
        if v is not None and str(v).strip():
            return f"{k}:{str(v).strip()}".upper()
    # Fallback conservador: posição. Só é usado quando o módulo não fornece
    # qualquer identificador; ainda preserva a edição sem inventar equivalência.
    return f"IDX:{index}"


def _admin_override_load(storage_key: str) -> list[sqlite3.Row]:
    return DB.execute(
        "SELECT entity_id,field_name,value_json,is_deleted,updated_at,updated_by "
        "FROM admin_manual_overrides WHERE storage_key=? ORDER BY entity_id,field_name",
        (storage_key,),
    ).fetchall()


def _admin_override_delete_entity(storage_key: str, entity_id: str) -> None:
    DB.execute("DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id=?", (storage_key, entity_id))


def _admin_override_upsert(storage_key: str, entity_id: str, field_name: str, value: Any,
                           *, deleted: bool, actor: str) -> None:
    DB.execute(
        """INSERT INTO admin_manual_overrides(storage_key,entity_id,field_name,value_json,is_deleted,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(storage_key,entity_id,field_name) DO UPDATE SET
             value_json=excluded.value_json,is_deleted=excluded.is_deleted,
             updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
        (storage_key, entity_id, field_name, json_dumps(value), 1 if deleted else 0, now_iso(), actor or "admin"),
    )


# V18.49: bancos-objeto em que o override de objeto-raiz (__ROOT__) CONGELA tudo.
# No Precificador ele guardava o campo "products" INTEIRO da última gravação de um
# Admin e o reaplicava por cima de qualquer gravação posterior (inclusive a
# atualização geral do Apollo, que era descartada) e de toda leitura. A proteção do
# Precificador é o merge por produto/campo (merge_precificador). As linhas antigas
# de admin_manual_overrides dessa chave ficam no banco, só deixam de ser aplicadas.
ADMIN_OVERRIDE_SEM_ROOT = {"conf_etiquetas", "precificador_online_v1"}


def _admin_override_record_delta(storage_key: str, current_value: str | None, requested_value: str,
                                 actor: str) -> int:
    """Registra somente o delta explícito feito pelo admin.

    Listas de objetos são comparadas por identidade estável; inclusão grava a
    entidade completa, remoção cria tombstone e edição protege apenas os campos
    realmente modificados. Objetos-raiz recebem o mesmo tratamento por chave.
    """
    # V17.69: conf_etiquetas é uma base agregada alimentada simultaneamente pelas
    # APIs e por uploads. Override do objeto-raiz congela registros/ordem e pode
    # esconder etiquetas automáticas adicionadas depois.
    if storage_key in ADMIN_OVERRIDE_SEM_ROOT:
        return 0
    try:
        old = json.loads(current_value) if current_value not in (None, "") else None
        new = json.loads(requested_value)
    except Exception:
        return 0
    changed = 0
    if isinstance(old, list) and isinstance(new, list):
        old_map = {_admin_override_entity_id(storage_key, x, i): x for i, x in enumerate(old) if isinstance(x, dict)}
        new_map = {_admin_override_entity_id(storage_key, x, i): x for i, x in enumerate(new) if isinstance(x, dict)}
        # Remoção manual = tombstone persistente.
        for eid in old_map.keys() - new_map.keys():
            _admin_override_delete_entity(storage_key, eid)
            _admin_override_upsert(storage_key, eid, "__ENTITY__", None, deleted=True, actor=actor)
            changed += 1
        # Inclusão/edição manual.
        for eid, obj in new_map.items():
            prev = old_map.get(eid)
            if prev is None:
                _admin_override_delete_entity(storage_key, eid)
                _admin_override_upsert(storage_key, eid, "__ENTITY__", obj, deleted=False, actor=actor)
                changed += 1
                continue
            # Restaurar SKU da integração: limpa as travas específicas do SKU.
            restoring_sku = storage_key == "conf_pedidos" and normalize(prev.get("SkuManualOverride")) == "SIM" and normalize(obj.get("SkuManualOverride")) != "SIM"
            if restoring_sku:
                for fld in ("SKU", "Sku", "sku", "SkuManualOverride", "SkuOriginalIntegracao"):
                    DB.execute("DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id=? AND field_name=?", (storage_key,eid,fld))
            for fld in set(prev.keys()) | set(obj.keys()):
                if restoring_sku and fld in ("SKU", "Sku", "sku", "SkuManualOverride", "SkuOriginalIntegracao"):
                    continue
                before = prev.get(fld, None)
                after_present = fld in obj
                after = obj.get(fld, None)
                if (fld in prev) != after_present or before != after:
                    _admin_override_upsert(storage_key, eid, fld, after, deleted=not after_present, actor=actor)
                    changed += 1
    elif isinstance(old, dict) and isinstance(new, dict):
        eid = "__ROOT__"
        for fld in set(old.keys()) | set(new.keys()):
            before = old.get(fld, None); present = fld in new; after = new.get(fld, None)
            if (fld in old) != present or before != after:
                _admin_override_upsert(storage_key, eid, fld, after, deleted=not present, actor=actor)
                changed += 1
    return changed


def _admin_override_apply(storage_key: str, merged_value: str) -> str:
    # V17.69: etiquetas usam proteção própria contra remoção; não reaplicar
    # snapshots-raiz antigos sobre registros novos vindos das integrações.
    if storage_key in ADMIN_OVERRIDE_SEM_ROOT:
        return merged_value
    rows = _admin_override_load(storage_key)
    if not rows:
        return merged_value
    try:
        data = json.loads(merged_value)
    except Exception:
        return merged_value
    if isinstance(data, list):
        items = [x for x in data]
        index_by_id = {_admin_override_entity_id(storage_key, x, i): i for i, x in enumerate(items) if isinstance(x, dict)}
        grouped: dict[str, list[sqlite3.Row]] = {}
        for r in rows: grouped.setdefault(str(r["entity_id"]), []).append(r)
        # Tombstones primeiro.
        tombstones = {eid for eid, rs in grouped.items() if any(str(r["field_name"]) == "__ENTITY__" and int(r["is_deleted"] or 0) == 1 for r in rs)}
        if tombstones:
            items = [x for i,x in enumerate(items) if _admin_override_entity_id(storage_key,x,i) not in tombstones]
            index_by_id = {_admin_override_entity_id(storage_key, x, i): i for i, x in enumerate(items) if isinstance(x, dict)}
        for eid, rs in grouped.items():
            if eid in tombstones: continue
            entity_override = next((r for r in rs if str(r["field_name"]) == "__ENTITY__" and int(r["is_deleted"] or 0) == 0), None)
            if eid not in index_by_id and entity_override is not None:
                try: obj = json.loads(str(entity_override["value_json"] or "{}"))
                except Exception: obj = {}
                if isinstance(obj, dict):
                    items.append(obj); index_by_id[eid] = len(items)-1
            pos = index_by_id.get(eid)
            if pos is None or not isinstance(items[pos], dict): continue
            obj = dict(items[pos])
            for r in rs:
                fld = str(r["field_name"])
                if fld == "__ENTITY__": continue
                if int(r["is_deleted"] or 0): obj.pop(fld, None)
                else:
                    try: obj[fld] = json.loads(str(r["value_json"]))
                    except Exception: obj[fld] = str(r["value_json"])
            obj["__adminOverride"] = True
            items[pos] = obj
        return json_dumps(items)
    if isinstance(data, dict):
        obj = dict(data)
        for r in rows:
            if str(r["entity_id"]) != "__ROOT__": continue
            fld = str(r["field_name"])
            if int(r["is_deleted"] or 0): obj.pop(fld, None)
            else:
                try: obj[fld] = json.loads(str(r["value_json"]))
                except Exception: obj[fld] = str(r["value_json"])
        return json_dumps(obj)
    return merged_value





def _admin_manual_order_vault_rows() -> list[dict[str, Any]]:
    rows = DB.execute(
        "SELECT entity_id,order_key,marketplace,payload,deleted,updated_at,updated_by FROM admin_manual_orders_v1752 WHERE deleted=0"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            obj = json.loads(str(r["payload"] or "{}"))
        except Exception:
            obj = {}
        if not isinstance(obj, dict):
            continue
        obj["ManualAdminProtegido"] = "SIM"
        obj["__manualAdminVault"] = True
        out.append(obj)
    return out


def _admin_manual_order_vault_apply(value: str | None) -> str:
    """V17.52: cofre independente para pedidos que o ADMIN criou/alterou.

    Nenhum job, polling, snapshot ou integração consegue apagar uma linha guardada
    aqui. O cofre é aplicado por último em toda leitura/gravação de conf_pedidos.
    """
    try:
        rows = json.loads(value or "[]")
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    out = [dict(x) if isinstance(x, dict) else x for x in rows]
    pos = {}
    for i, row in enumerate(out):
        if isinstance(row, dict):
            pos[_pending_identity_v1714(row, i)] = i
    for vault in _admin_manual_order_vault_rows():
        eid = _pending_identity_v1714(vault, len(out))
        if eid in pos:
            merged = dict(out[pos[eid]]) if isinstance(out[pos[eid]], dict) else {}
            # ADMIN vence campo a campo.
            merged.update(vault)
            merged["ManualAdminProtegido"] = "SIM"
            merged["__manualAdminVault"] = True
            out[pos[eid]] = merged
        else:
            out.append(dict(vault))
            pos[eid] = len(out) - 1
    return json_dumps(out)


def _admin_manual_order_vault_sync(current_value: str | None, new_value: str, actor: str, allow_deletes: bool) -> int:
    """Sincroniza o cofre APENAS em gravação explícita de ADMIN.

    - linhas ManualAdminProtegido/EXTENSAO_CHROME/MANUAL_ADMIN são armazenadas;
    - remoção só marca deleted quando a operação explicitamente permite exclusões;
    - ausência em snapshots normais nunca apaga o cofre.
    """
    try:
        current = json.loads(current_value or "[]")
        newer = json.loads(new_value or "[]")
    except Exception:
        return 0
    if not isinstance(current, list) or not isinstance(newer, list):
        return 0
    changed = 0
    cur_map = {_pending_identity_v1714(r, i): r for i, r in enumerate(current) if isinstance(r, dict)}
    new_map = {_pending_identity_v1714(r, i): r for i, r in enumerate(newer) if isinstance(r, dict)}

    if allow_deletes:
        for eid, row in cur_map.items():
            if eid in new_map:
                continue
            if _admin_manual_pending_protected(row) or bool(row.get("__manualAdminVault")):
                DB.execute(
                    "UPDATE admin_manual_orders_v1752 SET deleted=1,updated_at=?,updated_by=? WHERE entity_id=?",
                    (now_iso(), actor or "admin", eid),
                )
                changed += 1

    for eid, row in new_map.items():
        if not (_admin_manual_pending_protected(row) or bool(row.get("__manualAdminVault"))):
            continue
        payload = dict(row)
        payload["ManualAdminProtegido"] = "SIM"
        payload["__manualAdminVault"] = True
        DB.execute(
            """INSERT INTO admin_manual_orders_v1752(entity_id,order_key,marketplace,payload,deleted,updated_at,updated_by)
               VALUES(?,?,?,?,0,?,?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 order_key=excluded.order_key,marketplace=excluded.marketplace,payload=excluded.payload,
                 deleted=0,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (eid, normalize(order_number(payload)), str(payload.get("Marketplace") or ""), json_dumps(payload), now_iso(), actor or "admin"),
        )
        changed += 1
    return changed


def _admin_manual_pending_protected(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if normalize(row.get("ManualAdminProtegido")) == "SIM":
        return True
    origem = normalize(row.get("OrigemCadastro"))
    return origem.startswith("EXTENSAO_CHROME_") or origem in {"MANUAL_ADMIN", "ADMIN_MANUAL"}


def _preserve_admin_manual_pending(current_value: str | None, requested_value: str) -> str:
    """V17.51: proteção redundante para inclusões explícitas do ADMIN.

    Mesmo se algum sincronizador legado montar um snapshot sem a linha criada pela
    extensão/Admin, linhas marcadas como manuais permanecem e seus campos atuais
    prevalecem. Exclusão explícita pelo próprio admin continua possível porque esta
    função só é usada em gravações automáticas/não-admin.
    """
    try:
        current = json.loads(current_value or "[]")
        requested = json.loads(requested_value or "[]")
    except Exception:
        return requested_value
    if not isinstance(current, list) or not isinstance(requested, list):
        return requested_value
    out = [dict(x) if isinstance(x, dict) else x for x in requested]
    pos = {}
    for i, row in enumerate(out):
        if isinstance(row, dict):
            pos[_pending_identity_v1714(row, i)] = i
    for i, row in enumerate(current):
        if not _admin_manual_pending_protected(row):
            continue
        eid = _pending_identity_v1714(row, i)
        if eid in pos:
            # A versão manual/corrigida pelo admin vence campo a campo.
            merged = dict(out[pos[eid]]) if isinstance(out[pos[eid]], dict) else {}
            merged.update(row)
            merged["ManualAdminProtegido"] = "SIM"
            out[pos[eid]] = merged
        else:
            keep = dict(row)
            keep["ManualAdminProtegido"] = "SIM"
            out.append(keep)
            pos[eid] = len(out)-1
    return json_dumps(out)


def _pending_drop_identities_v1852(value: str, identities: set[str]) -> str:
    try:
        rows = json.loads(value or "[]")
    except Exception:
        return value
    if not isinstance(rows, list):
        return value
    kept = [
        r for i, r in enumerate(rows)
        if not (isinstance(r, dict)
                and not _admin_manual_pending_protected(r)
                and _pending_identity_v1714(r, i) in identities)
    ]
    return json_dumps(kept) if len(kept) != len(rows) else value


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
    allow_pending_deletes: bool = False,
    remove_pending_identities: set[str] | None = None,
) -> tuple[str, int, str]:
    if len(new_value.encode("utf-8")) > MAX_BODY:
        raise ValueError("Conteúdo excede 100 MB")
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            if key == "conf_pedidos" and allow_completed_reimport and not force and old_value is not None:
                new_value, _ = reactivate_completed_orders_from_delta(old_value, new_value, actor or "admin")
            current, version, _ = get_storage(key)
            # V17.52: o cofre de pedidos manuais é independente do snapshot principal.
            # Uma inclusão/edição do ADMIN fica registrada antes de qualquer merge.
            if key == "conf_pedidos" and normalize(actor_role) == "ADMIN":
                _admin_manual_order_vault_sync(current, new_value, actor or "admin", allow_pending_deletes)
            # V17.48: toda gravação explícita feita por ADMIN registra somente o
            # delta manual antes de qualquer merge. Assim o servidor pode aplicar
            # essas decisões novamente sobre sincronizações futuras.
            admin_override_count = 0
            if normalize(actor_role) == "ADMIN":
                admin_override_count = _admin_override_record_delta(key, current, new_value, actor or "admin")
            # V17.44: conf_pedidos é cumulativo por padrão. Qualquer sincronização
            # automática/importação deve MESCLAR com o banco atual e nunca apagar
            # pedidos que não vieram na resposta/arquivo desta rodada. Remoções só
            # são aceitas quando a operação explicitamente autoriza exclusão
            # (ex.: botão Excluir do Admin/Conferência). Pedidos concluídos continuam
            # sendo retirados abaixo por filter_completed_orders().
            if key == "conf_pedidos":
                # V17.72: conclusão é canônica. Um pedido que já está em
                # completed_orders nunca pode ser ressuscitado por import/sync/cache.
                new_value = filter_completed_orders_v1772(new_value)
                # V17.70: o bip confirmado pelo operador é monotônico.
                new_value = preserve_conference_progress_v1770(current, new_value)
            if key == "conf_pedidos" and not allow_pending_deletes:
                # V17.51: sincronizações/jobs nunca podem apagar ou reverter linhas
                # criadas/alteradas explicitamente pelo ADMIN (inclusive extensão Chrome).
                if normalize(actor_role) != "ADMIN":
                    new_value = _preserve_admin_manual_pending(current, new_value)
                new_value = preserve_pending_records_v1714(current, new_value)
            if key == "conf_pedidos" and remove_pending_identities:
                # V18.52: remoção automática NOMEADA (venda cancelada ou Full no ML).
                # Sem isto a preservação acima devolvia a linha no mesmo save e o
                # watcher "removia" o mesmo pedido a cada ~25 s para sempre. Só sai
                # a linha citada; ausência em snapshot continua não apagando nada, e
                # linha protegida pelo ADMIN nunca sai por aqui.
                new_value = _pending_drop_identities_v1852(new_value, remove_pending_identities)
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                new_value = preserve_conference_names(current, new_value)
            merged = merge_value(key, current, old_value, new_value, force)
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                try:
                    merged_rows = json.loads(merged)
                    if isinstance(merged_rows, list):
                        if key == "conf_pedidos":
                            merged_rows = _dedupe_ml_legacy_sku_rows(merged_rows)
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
            # V17.48: ADMIN sempre prevalece. Aplicado por último para que nenhuma
            # integração/job/importação possa sobrescrever decisão manual salva.
            merged = _admin_override_apply(key, merged)
            if key == "conf_pedidos":
                merged = _admin_manual_order_vault_apply(merged)
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                merged = _pending_filter_manual_deleted_orders_value(merged)
                # V17.78: conclusão é a última barreira da fila. Overrides, cofre
                # manual, merge e sync não podem ressuscitar pedido concluído.
                # Reimportação ADMIN explícita continua possível porque
                # allow_completed_reimport remove o tombstone antes deste ponto.
                merged = filter_completed_orders_v1772(merged)
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
    backup_daily_em_segundo_plano()
    return merged, version, updated



def _precificador_bulk_set_status(**changes: Any) -> None:
    with PRECIFICADOR_BULK_STATUS_LOCK:
        PRECIFICADOR_BULK_STATUS.update(changes)


def precificador_bulk_status() -> dict[str, Any]:
    with PRECIFICADOR_BULK_STATUS_LOCK:
        return dict(PRECIFICADOR_BULK_STATUS)


def _precificador_recalculate_kits(products: list[dict[str, Any]]) -> int:
    by_id = {
        str(p.get("_id")): p for p in products
        if isinstance(p, dict) and p.get("_id")
    }
    by_sku = {
        str(p.get("sku") or "").strip().upper(): p for p in products
        if isinstance(p, dict) and str(p.get("sku") or "").strip()
    }

    def money(value: Any) -> float:
        try:
            return round(float(value or 0), 2)
        except Exception:
            return 0.0

    def recalc(product: dict[str, Any], visiting: set[str] | None = None) -> float:
        if not product.get("isKit"):
            return money(product.get("custo"))
        visiting = set(visiting or set())
        pid = str(product.get("_id") or product.get("sku") or "")
        if pid in visiting:
            return money(product.get("custo"))
        visiting.add(pid)
        total = 0.0
        for item in product.get("kitItems") or []:
            if not isinstance(item, dict):
                continue
            linked = None
            product_id = str(item.get("productId") or "")
            sku = str(item.get("sku") or "").strip().upper()
            if product_id:
                linked = by_id.get(product_id)
            if linked is None and sku:
                linked = by_sku.get(sku)
            if linked is not None:
                unit_cost = recalc(linked, visiting.copy())
                item["productId"] = linked.get("_id")
                item["sku"] = linked.get("sku")
                item["produto"] = linked.get("produto")
                item["custoUnitario"] = unit_cost
            else:
                unit_cost = money(item.get("custoUnitario"))
            try:
                qty = max(1.0, float(item.get("quantidade") or 1))
            except Exception:
                qty = 1.0
            total += unit_cost * qty
        product["custo"] = round(total, 2)
        return product["custo"]

    count = 0
    for product in products:
        if isinstance(product, dict) and product.get("isKit"):
            recalc(product)
            count += 1
    return count


def precificador_bulk_update_worker(username: str) -> None:
    if not PRECIFICADOR_BULK_LOCK.acquire(blocking=False):
        return
    try:
        _precificador_bulk_set_status(
            running=True, total=0, processed=0, updated=0, cost_changes=0,
            descriptions=0, curves=0, kits=0, errors=0, current_sku="",
            started_at=now_iso(), finished_at="", message="Carregando banco do Precificador...",
        )

        with DB_LOCK:
            raw, _, _ = get_storage("precificador_online_v1")
            alert_raw, _, _ = get_storage("precificador_alertas_custo_v1")
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}
        products = data.get("products") if isinstance(data, dict) else []
        if not isinstance(products, list):
            raise RuntimeError("Banco do Precificador inválido.")

        try:
            alerts = json.loads(alert_raw or "[]")
        except Exception:
            alerts = []
        if not isinstance(alerts, list):
            alerts = []

        targets = [
            p for p in products
            if isinstance(p, dict)
            and str(p.get("sku") or "").strip()
            and not p.get("isKit")
        ]
        _precificador_bulk_set_status(total=len(targets), message="Atualizando produtos pelo Apollo...")

        updated = cost_changes = descriptions = curves = errors = 0

        for pos, product in enumerate(targets, 1):
            sku = str(product.get("sku") or "").strip()
            _precificador_bulk_set_status(processed=pos - 1, current_sku=sku)
            try:
                result = crm_cost_lookup(sku, username or "ATUALIZACAO_GERAL_PRECIFICADOR")
                old_cost = round(float(product.get("custo") or 0), 2)
                filial_custo = int(product.get("filialCusto") or 1)
                branch_rows = result.get("custos") if isinstance(result.get("custos"), list) else []
                product["custosFiliais"] = branch_rows
                product["filialCusto"] = filial_custo
                selected_branch = next(
                    (row for row in branch_rows if isinstance(row, dict) and int(row.get("revenda") or 0) == filial_custo),
                    None,
                )
                selected_cost = selected_branch.get("custo_medio") if isinstance(selected_branch, dict) else None
                new_cost = round(float(selected_cost), 2) if selected_cost is not None else None
                description = str(result.get("descricao") or "").strip()
                curve = str(result.get("curva_abc") or "").strip().upper()

                if new_cost is not None:
                    product["custo"] = new_cost
                if description and str(product.get("produto") or "").strip() != description:
                    product["produto"] = description
                    descriptions += 1
                old_curve = str(product.get("curva") or "").strip().upper()
                if curve in ("A", "B", "C", "D") and old_curve != curve:
                    product["curva"] = curve
                    apply_curve_margin_to_product(product, curve)
                    curves += 1

                if new_cost is not None and abs(new_cost - old_cost) >= 0.01:
                    diff = round(new_cost - old_cost, 2)
                    pct = (diff / old_cost) if old_cost else 0.0
                    alerts.insert(0, {
                        "id": f"bulk-{int(time.time()*1000)}-{pos}",
                        "sku": sku,
                        "productId": str(product.get("_id") or ""),
                        "produto": description or str(product.get("produto") or ""),
                        "oldCost": old_cost,
                        "newCost": new_cost,
                        "difference": diff,
                        "percent": pct,
                        "createdAt": now_iso(),
                        "read": False,
                    })
                    cost_changes += 1
                updated += 1
            except Exception:
                errors += 1

            _precificador_bulk_set_status(
                processed=pos, updated=updated, cost_changes=cost_changes,
                descriptions=descriptions, curves=curves, errors=errors,
            )
            # Contrato CRM 3 casas: máximo 20 consultas/minuto no sistema inteiro.
            if pos < len(targets):
                time.sleep(3.2)

        kits = _precificador_recalculate_kits(products)
        data["products"] = products
        data["updatedAt"] = now_iso()
        data["updatedBy"] = username or "ATUALIZACAO_GERAL_APOLLO"

        # V18.49: a atualização leva até ~1 h (20 consultas/min no Apollo). Gravar com
        # force=True apagava tudo o que as pessoas alteraram nesse tempo; agora o
        # merge aplica só os campos que a atualização mudou em relação ao que leu.
        save_storage(
            "precificador_online_v1", raw, json_dumps(data), False,
            actor=username or "sistema", actor_role="operador",
            source_screen="precificador", change_method="apollo.bulk",
        )
        save_storage(
            "precificador_alertas_custo_v1", None, json_dumps(alerts[:300]), True,
            actor=username or "sistema", actor_role="operador",
            source_screen="precificador", change_method="apollo.bulk",
        )
        audit(username or "sistema", "precificador.apollo.bulk",
              f"updated={updated};cost_changes={cost_changes};curves={curves};errors={errors}")
        _precificador_bulk_set_status(
            running=False, current_sku="", kits=kits, finished_at=now_iso(),
            message="Atualização geral concluída.",
        )
    except Exception as exc:
        _precificador_bulk_set_status(
            running=False, current_sku="", finished_at=now_iso(),
            message="Erro na atualização geral: " + str(exc)[:500],
        )
    finally:
        PRECIFICADOR_BULK_LOCK.release()


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


NOROMAKWEB_SSO_AUDITED: dict[str, float] = {}


def noromakweb_sso_context(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    """V18.52: identidade vinda do portão do NoromakWeb.

    Só vale com o segredo combinado — o 127.0.0.1 sozinho não prova nada, porque
    qualquer processo da VPS alcança esta porta. O usuário precisa existir e estar
    ativo AQUI, e as permissões continuam sendo as da Expedição.
    """
    secret = NOROMAKWEB_SSO_SECRET
    if len(secret) < NOROMAKWEB_SSO_MIN_SECRET_LEN:
        return None
    supplied = str(handler.headers.get("X-NoromakWeb-Segredo") or "").strip()
    username = str(handler.headers.get("X-NoromakWeb-Usuario") or "").strip().lower()
    if not supplied or not username:
        return None
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        return None
    with DB_LOCK:
        row = DB.execute(
            """SELECT username,display_name,role,active,
                      can_conferencia,can_admin,can_estacao,can_precificador
               FROM app_users WHERE username=?""",
            (username,),
        ).fetchone()
    if not row or not row["active"]:
        return None
    now = time.time()
    if now - NOROMAKWEB_SSO_AUDITED.get(username, 0) > 3600:
        NOROMAKWEB_SSO_AUDITED[username] = now
        audit(username, "auth.login.noromakweb", client_computer(handler))
    return {
        "username": row["username"],
        "nome": row["display_name"] or row["username"],
        "app_role": row["role"],
        "app_active": True,
        "permissions": user_permissions_from_row(row),
        "area": "noromakweb",
        # Sem cookie próprio: a janela de reautenticação crítica fica por usuário.
        "session_key": hashlib.sha256(f"noromakweb-sso|{username}".encode("utf-8")).hexdigest(),
        "is_owner": is_owner_username(row["username"]),
        "sso": "noromakweb",
    }


def session_context(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    # V18.52: a sessão do NoromakWeb vence o cookie antigo — quem está logado lá
    # é quem está usando a tela. Sem cabeçalho válido, segue o login próprio.
    sso = noromakweb_sso_context(handler)
    if sso is not None:
        return sso
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
            (now_iso(), now_epoch + session_ttl_for_user_area(area, row), token_hash),
        )
        DB.commit()
    return {
        "username": row["username"],
        "nome": row["display_name"] or row["username"],
        "app_role": row["role"],
        "app_active": True,
        "permissions": user_permissions_from_row(row),
        "area": str(row["area"] or "conferencia"),
        # Identificador irreversível da sessão para a janela de reautenticação crítica.
        "session_key": token_hash,
        "is_owner": is_owner_username(row["username"]),
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

# Shopee Open Platform — OAuth Live V17.17
def shopee_integration_row() -> sqlite3.Row | None:
    with DB_LOCK:
        return DB.execute("SELECT * FROM shopee_integration WHERE id=1").fetchone()


def shopee_log_event(topic: str, resource: str, result: str, detail: str = "") -> None:
    with DB_LOCK:
        DB.execute(
            "INSERT INTO shopee_event_log(created_at,topic,resource,result,detail) VALUES(?,?,?,?,?)",
            (now_iso(), str(topic or "")[:120], str(resource or "")[:500],
             str(result or "")[:80], str(detail or "")[:2000]),
        )
        DB.commit()


def shopee_sign(path: str, timestamp: int, access_token: str = "", shop_id: str = "") -> str:
    base = f"{SHOPEE_PARTNER_ID}{path}{timestamp}"
    if access_token or shop_id:
        base += f"{access_token}{shop_id}"
    return hmac.new(SHOPEE_PARTNER_KEY.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()


def shopee_status_payload() -> dict[str, Any]:
    row = shopee_integration_row()
    configured = bool(SHOPEE_PARTNER_ID and SHOPEE_PARTNER_KEY)
    connected = bool(row and str(row["shop_id"] or "").strip() and str(row["access_token"] or "").strip())
    return {
        "configured": configured,
        "connected": connected,
        "go_live_pending": False,
        "partner_id": SHOPEE_PARTNER_ID,
        "shop_id": str(row["shop_id"] or "") if row else "",
        "shop_name": str(row["shop_name"] or "") if row else "",
        "last_sync_at": str(row["last_sync_at"] or "") if row else "",
        "last_error": str(row["last_error"] or "") if row else "",
        "redirect_uri": SHOPEE_REDIRECT_URI,
        "token_expires_at": int(row["token_expires_at"] or 0) if row else 0,
        "credentials_server_side": True,
    }


def shopee_connect_url() -> str:
    if not SHOPEE_PARTNER_ID:
        raise RuntimeError("SHOPEE_PARTNER_ID não foi configurado no servidor.")
    if not SHOPEE_PARTNER_KEY:
        raise RuntimeError("SHOPEE_PARTNER_KEY não foi configurado no servidor.")
    timestamp = int(time.time())
    path = SHOPEE_AUTH_PATH
    sign = shopee_sign(path, timestamp)
    base = SHOPEE_AUTH_URL or (SHOPEE_API_BASE + path)
    params = {
        "partner_id": int(SHOPEE_PARTNER_ID),
        "timestamp": timestamp,
        "sign": sign,
        "redirect": SHOPEE_REDIRECT_URI,
    }
    return base + ("&" if "?" in base else "?") + urlencode(params)


def shopee_exchange_code(code: str, shop_id: str) -> dict[str, Any]:
    if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
        raise RuntimeError("Credenciais Live da Shopee não configuradas.")
    try:
        shop_int = int(str(shop_id).strip())
    except Exception:
        raise RuntimeError("Shop ID retornado pela Shopee é inválido.")
    timestamp = int(time.time())
    path = SHOPEE_TOKEN_PATH
    sign = shopee_sign(path, timestamp)
    url = SHOPEE_API_BASE + path + "?" + urlencode({
        "partner_id": int(SHOPEE_PARTNER_ID),
        "timestamp": timestamp,
        "sign": sign,
    })
    payload = json.dumps({
        "code": str(code).strip(),
        "shop_id": shop_int,
        "partner_id": int(SHOPEE_PARTNER_ID),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Shopee token HTTP {exc.code}: {body[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao obter token Shopee: {exc.reason}")
    if not isinstance(data, dict):
        raise RuntimeError("Resposta inválida da Shopee ao obter token.")
    if data.get("error"):
        raise RuntimeError(f"Shopee recusou o token: {data.get('error')} {data.get('message') or ''}".strip())
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise RuntimeError("Shopee não retornou access_token/refresh_token.")
    expire_in = int(data.get("expire_in") or 14400)
    now_ts = int(time.time())
    with DB_LOCK:
        DB.execute(
            """INSERT INTO shopee_integration
               (id,shop_id,shop_name,access_token,refresh_token,token_expires_at,connected_at,updated_at,last_sync_at,last_error)
               VALUES(1,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 shop_id=excluded.shop_id, access_token=excluded.access_token,
                 refresh_token=excluded.refresh_token, token_expires_at=excluded.token_expires_at,
                 connected_at=CASE WHEN shopee_integration.connected_at='' THEN excluded.connected_at ELSE shopee_integration.connected_at END,
                 updated_at=excluded.updated_at, last_error=''""",
            (str(shop_int), "", access_token, refresh_token, now_ts + max(60, expire_in),
             now_iso(), now_iso(), "", ""),
        )
        DB.commit()
    shopee_log_event("oauth_token", f"shop_id={shop_int}", "OK", "Loja Live conectada; tokens armazenados somente no servidor.")
    return {"ok": True, "shop_id": str(shop_int), "expire_in": expire_in}


def _shopee_token_valido(row: sqlite3.Row | None, margem: int = 300) -> bool:
    return bool(row and str(row["access_token"] or "").strip()
               and int(row["token_expires_at"] or 0) > int(time.time()) + margem)


def _shopee_chamar_refresh(shop_id: int, refresh_token: str) -> dict[str, Any]:
    """Só a chamada de rede à Shopee. Isolada para poder ser testada."""
    timestamp = int(time.time())
    path = SHOPEE_REFRESH_PATH
    sign = shopee_sign(path, timestamp)
    url = SHOPEE_API_BASE + path + "?" + urlencode({
        "partner_id": int(SHOPEE_PARTNER_ID), "timestamp": timestamp, "sign": sign,
    })
    payload = json.dumps({
        "refresh_token": refresh_token,
        "shop_id": shop_id,
        "partner_id": int(SHOPEE_PARTNER_ID),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Shopee refresh HTTP {exc.code}: {body[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao renovar token Shopee: {exc.reason}")
    if not isinstance(data, dict) or data.get("error"):
        raise RuntimeError(f"Shopee recusou a renovação do token: {data.get('error') or 'resposta inválida'} {data.get('message') or ''}".strip())
    return data


# V18.01: a chave de refresh da Shopee é de USO ÚNICO. Com os workers de pedidos
# (60s) e de etiquetas (3min) batendo na Shopee ao mesmo tempo, duas renovações
# concorrentes gastavam a mesma chave e a integração quebrava até reconectar.
# Esta trava serializa a renovação: um renova, o outro espera e reaproveita.
SHOPEE_TOKEN_LOCK = threading.Lock()


def shopee_refresh_access_token(force: bool = False) -> sqlite3.Row:
    row = shopee_integration_row()
    if not row or not str(row["shop_id"] or "").strip() or not str(row["refresh_token"] or "").strip():
        raise RuntimeError("Shopee não possui refresh_token válido. Reconecte a loja.")
    token_antes = str(row["access_token"] or "").strip()
    if not force and _shopee_token_valido(row):
        return row

    with SHOPEE_TOKEN_LOCK:
        # Relê sob a trava: outro processo pode ter renovado enquanto esperávamos.
        row = shopee_integration_row()
        if not row or not str(row["refresh_token"] or "").strip():
            raise RuntimeError("Shopee não possui refresh_token válido. Reconecte a loja.")
        # O token mudou (outro renovou) e ainda é utilizável: reaproveita, não
        # gasta a chave de novo — vale inclusive no modo force (após um 401).
        if str(row["access_token"] or "").strip() != token_antes and _shopee_token_valido(row, margem=60):
            return row
        if not force and _shopee_token_valido(row):
            return row

        shop_id = int(str(row["shop_id"]).strip())
        refresh_token = str(row["refresh_token"] or "").strip()
        data = _shopee_chamar_refresh(shop_id, refresh_token)
        access_token = str(data.get("access_token") or "").strip()
        new_refresh = str(data.get("refresh_token") or "").strip()
        if not access_token or not new_refresh:
            raise RuntimeError("Shopee não retornou novos tokens durante a renovação.")
        expire_in = int(data.get("expire_in") or 14400)
        with DB_LOCK:
            DB.execute(
                """UPDATE shopee_integration
                   SET access_token=?,refresh_token=?,token_expires_at=?,updated_at=?,last_error=''
                   WHERE id=1""",
                (access_token, new_refresh, int(time.time()) + max(60, expire_in), now_iso()),
            )
            DB.commit()
        shopee_log_event("oauth_refresh", f"shop_id={shop_id}", "OK", "Access token renovado automaticamente.")
        row = shopee_integration_row()
        if not row:
            raise RuntimeError("Integração Shopee desapareceu após renovar o token.")
        return row


def shopee_api_get(path: str, params: dict[str, Any] | None = None, retry_auth: bool = True) -> dict[str, Any]:
    row = shopee_refresh_access_token(False)
    shop_id = str(row["shop_id"] or "").strip()
    access_token = str(row["access_token"] or "").strip()
    timestamp = int(time.time())
    query = {
        "partner_id": int(SHOPEE_PARTNER_ID),
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": int(shop_id),
        "sign": shopee_sign(path, timestamp, access_token, shop_id),
    }
    if params:
        for key, value in params.items():
            if value is not None and value != "":
                query[key] = value
    url = SHOPEE_API_BASE + path + "?" + urlencode(query)
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if retry_auth and exc.code in (401, 403):
            shopee_refresh_access_token(True)
            return shopee_api_get(path, params, False)
        raise RuntimeError(f"Shopee API HTTP {exc.code}: {body[:700]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede na Shopee API: {exc.reason}")
    if not isinstance(data, dict):
        raise RuntimeError("Resposta inválida da Shopee API.")
    error = str(data.get("error") or "").strip()
    if error:
        auth_error = error.lower() in {"error_auth", "error_authentication", "invalid_access_token", "error_token"}
        if retry_auth and auth_error:
            shopee_refresh_access_token(True)
            return shopee_api_get(path, params, False)
        raise RuntimeError(f"Shopee API: {error} {data.get('message') or ''}".strip())
    return data



def shopee_api_post(path: str, payload: dict[str, Any] | None = None, retry_auth: bool = True) -> dict[str, Any]:
    row = shopee_refresh_access_token(False)
    shop_id = str(row["shop_id"] or "").strip()
    access_token = str(row["access_token"] or "").strip()
    timestamp = int(time.time())
    query = {"partner_id": int(SHOPEE_PARTNER_ID), "timestamp": timestamp,
             "access_token": access_token, "shop_id": int(shop_id),
             "sign": shopee_sign(path, timestamp, access_token, shop_id)}
    url = SHOPEE_API_BASE + path + "?" + urlencode(query)
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Accept":"application/json","Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        if retry_auth and exc.code in (401,403):
            shopee_refresh_access_token(True); return shopee_api_post(path, payload, False)
        raise RuntimeError(f"Shopee API HTTP {exc.code}: {raw[:700]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede na Shopee API: {exc.reason}")
    if not isinstance(data, dict): raise RuntimeError("Resposta inválida da Shopee API.")
    if data.get("error"):
        err=str(data.get("error") or "")
        if retry_auth and err.lower() in {"error_auth","error_authentication","invalid_access_token","error_token"}:
            shopee_refresh_access_token(True); return shopee_api_post(path, payload, False)
        raise RuntimeError(f"Shopee API: {err} {data.get('message') or ''}".strip())
    return data


def shopee_api_post_bytes(path: str, payload: dict[str, Any]) -> tuple[bytes, str]:
    row=shopee_refresh_access_token(False); shop_id=str(row["shop_id"] or "").strip(); token=str(row["access_token"] or "").strip(); ts=int(time.time())
    q={"partner_id":int(SHOPEE_PARTNER_ID),"timestamp":ts,"access_token":token,"shop_id":int(shop_id),"sign":shopee_sign(path,ts,token,shop_id)}
    req=urllib.request.Request(SHOPEE_API_BASE+path+"?"+urlencode(q), data=json.dumps(payload).encode(), headers={"Accept":"*/*","Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read(), str(resp.headers.get("Content-Type") or "application/octet-stream")


def _shopee_logistics_order_list(shipments: list[dict[str,str]]) -> list[dict[str,str]]:
    return [{"order_sn":x["order_sn"], **({"package_number":x["package_number"]} if x.get("package_number") else {})} for x in shipments if x.get("order_sn")]


def _shopee_extract_zpl(raw: bytes) -> str:
    """Extrai ZPL de resposta direta ou ZIP da Shopee, quando disponível."""
    if not raw:
        return ""
    candidates: list[bytes] = [raw]
    if raw[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".zpl", ".txt", ".prn")):
                        candidates.append(zf.read(name))
        except Exception:
            pass
    for blob in candidates:
        for enc in ("utf-8", "latin-1"):
            try:
                text = blob.decode(enc, errors="ignore")
            except Exception:
                continue
            pos = text.find("^XA")
            if pos >= 0 and "^XZ" in text[pos:]:
                # V17.99: a Shopee manda a etiqueta como uma imagem baixada por ~DG
                # ANTES do ^XA (o ^XA só imprime a imagem com ^XG). Cortar no ^XA
                # jogava a imagem fora e a etiqueta saía em branco.
                grafico = text.find("~DG")
                if 0 <= grafico < pos:
                    pos = grafico
                return text[pos:].strip()
    return ""


def shopee_reparar_etiquetas_sem_grafico() -> int:
    """V17.99: regrava as etiquetas Shopee vindas da API que ficaram sem a imagem
    (~DG), relendo o arquivo original salvo em shopee_labels. Idempotente: só mexe
    em etiqueta da API que manda imprimir uma imagem (^XG) que ela não carrega."""
    raw, _, _ = get_storage("conf_etiquetas")
    try:
        bank = json.loads(raw) if raw else {}
    except Exception:
        return 0
    registros = bank.get("registros") if isinstance(bank, dict) else None
    if not isinstance(registros, dict):
        return 0
    labels_dir = DB_PATH.parent / "shopee_labels"
    reparadas = 0
    for rec in list(registros.values()):
        if not isinstance(rec, dict) or rec.get("origem") != "api_shopee":
            continue
        envio = str(rec.get("envio") or "")
        if "~DG" in envio or "^XG" not in envio:
            continue
        meta = rec.get("metadados") if isinstance(rec.get("metadados"), dict) else {}
        order_sn = str(meta.get("pedido") or rec.get("pedido") or "").strip()
        pkg = str(meta.get("package_number") or "").strip()
        if not order_sn:
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", order_sn + ("_" + pkg if pkg else ""))
        for ext in ("zip", "bin", "pdf"):
            arquivo = labels_dir / f"{safe}.{ext}"
            if not arquivo.exists():
                continue
            zpl = _shopee_extract_zpl(arquivo.read_bytes())
            if "~DG" in zpl and _shopee_store_linked_label(order_sn, pkg, zpl):
                reparadas += 1
                shopee_log_event("etiqueta_reparada", f"order={order_sn}", "OK", "imagem ~DG restaurada (V17.99)")
            break
    return reparadas


def _kit_e_residuo_prefixo(row: dict[str, Any]) -> bool:
    """V18.04: True se a linha é um produto SIMPLES que foi marcado como kit pelo
    bug do alias (kit KIT2-X vazando para a venda do X puro).

    Distingue do kit de verdade pelo SKU VENDIDO (SkuOrigemImportacao): num kit
    real ele é "KIT2-X" (mesmo com prefixo numérico do ML, ex.: 152285-KIT2-X);
    no resíduo é só o SKU simples (ex.: 152285-0893014105), sem "KIT".
    """
    if not isinstance(row, dict):
        return False
    sku_kit = str(row.get("SkuKitOrigem") or "").strip()
    if not re.match(r"^KIT\s*\d", sku_kit, re.I):
        return False
    if str(row.get("Kit") or "").upper() != "SIM" and not sku_kit:
        return False
    origem = str(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or "").strip()
    if not origem:
        return False
    operacional = re.sub(r"^\d+-", "", origem, count=1)  # tira o prefixo numérico do ML
    if operacional.upper().startswith("KIT"):
        return False  # kit de verdade
    return True


def _kits_residuo_prefixo_reparar_rows(rows: Any) -> int:
    """Limpa o selo de kit das linhas de resíduo, in place. Retorna quantas mudaram.

    A quantidade correta é a quantidade vendida (Quantidade de Kits); os campos de
    kit são zerados para a tela parar de recalcular (Qtd de Kits × Qtd por Kit).
    """
    if not isinstance(rows, list):
        return 0
    mudou = 0
    for row in rows:
        if not _kit_e_residuo_prefixo(row):
            continue
        try:
            vendidas = max(1, int(float(row.get("Quantidade de Kits") or 1)))
        except Exception:
            vendidas = 1
        row["Quantidade"] = str(vendidas)
        row["Kit"] = ""
        row["Nome do Kit"] = ""
        row["Quantidade de Kits"] = ""
        row["Quantidade por Kit"] = ""
        row["SkuKitOrigem"] = ""
        mudou += 1
    return mudou


def reparar_kits_residuo_prefixo_v1803() -> int:
    """V18.04: reparo de boot para o resíduo do bug do kit-prefixo (V18.03).

    A sincronização do ML já corrige a QUANTIDADE, mas o merge preserva os campos
    de kit antigos, então a tela continua mostrando o dobro. Aqui grava DIRETO
    (sem passar pelo merge, que reporia o selo) só nas linhas de resíduo.
    Idempotente: depois de limpo, não acha mais nada.
    """
    row = DB.execute("SELECT value,version FROM storage WHERE key='conf_pedidos'").fetchone()
    if not row:
        return 0
    try:
        items = json.loads(str(row["value"] or "[]"))
    except Exception:
        return 0
    mudou = _kits_residuo_prefixo_reparar_rows(items)
    if mudou:
        DB.execute(
            "UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_pedidos'",
            (json_dumps(items), int(row["version"] or 0) + 1, now_iso()),
        )
        DB.commit()
    return mudou


def _shopee_store_linked_label(order_sn: str, package_number: str, zpl: str) -> bool:
    """Vincula a etiqueta ZPL Shopee ao banco usado pela Expedição/Conferência."""
    order_sn = str(order_sn or "").strip()
    package_number = str(package_number or "").strip()
    if not order_sn or not str(zpl or "").strip():
        return False
    raw, _, _ = get_storage("conf_etiquetas")
    try:
        bank = json.loads(raw) if raw else {}
    except Exception:
        bank = {}
    if not isinstance(bank, dict):
        bank = {}
    bank["version"] = max(8, int(bank.get("version") or 0))
    bank.setdefault("registros", {})
    bank.setdefault("ordem", [])
    bank.setdefault("porChave", {})
    bank.setdefault("avisos", [])
    bank.setdefault("auditoria", [])
    bank.setdefault("resumo", {})
    bank.setdefault("vinculados", {})

    rid = "etq_shopee_api_" + hashlib.sha1((order_sn + "|" + package_number).encode("utf-8")).hexdigest()[:18]
    keys = [x for x in (order_sn, package_number) if x]
    record = {
        "id": rid,
        "marketplace": "shopee",
        "envio": zpl,
        "danfe": "",
        "arquivo": f"Shopee API · pedido {order_sn}",
        "metadados": {"pedido": order_sn, "package_number": package_number, "rastreio": "", "nf": "", "chaveAcesso": ""},
        "chaves": keys,
        "vinculadoA": [order_sn],
        "pedido": order_sn,
        "vinculo": "api_shopee_ready_to_print",
        "origem": "api_shopee",
        "adicionadaEm": now_iso(),
        "adicionadaPor": "shopee-api",
    }
    bank["registros"][rid] = record
    if rid not in bank["ordem"]:
        bank["ordem"].append(rid)
    bank["porChave"] = {}
    for record_id in bank["ordem"]:
        rec = bank["registros"].get(record_id)
        if not isinstance(rec, dict):
            continue
        for key in rec.get("chaves") or []:
            key = str(key or "").strip()
            if not key:
                continue
            bank["porChave"].setdefault(key, [])
            if record_id not in bank["porChave"][key]:
                bank["porChave"][key].append(record_id)
    bank["atualizadoEm"] = now_iso()
    if not any(isinstance(a, dict) and a.get("etiqueta") == rid for a in bank["auditoria"]):
        bank["auditoria"].append({"marketplace":"shopee","pedido":order_sn,"criterio":"api_ready_to_print","etiqueta":rid,"arquivo":record["arquivo"]})
        bank["auditoria"] = bank["auditoria"][-250:]
    save_storage("conf_etiquetas", raw, json_dumps(bank), False,
                 actor="shopee-api", actor_role="sistema",
                 source_screen="API Shopee", change_method="Etiqueta automática Shopee",
                 client_ip="127.0.0.1")
    return True


def _shopee_shipping_mode_payload(order_sn: str, package_number: str) -> tuple[dict[str, Any] | None, str, str]:
    """Consulta get_shipping_parameter e monta somente os campos exigidos por ship_order.

    Retorna (payload, modo, detalhe). payload=None significa que a modalidade exige
    um dado manual/externo que a integração não deve inventar.
    """
    q: dict[str, Any] = {"order_sn": order_sn}
    if package_number:
        q["package_number"] = package_number
    data = shopee_api_get("/api/v2/logistics/get_shipping_parameter", q)
    resp = data.get("response") if isinstance(data, dict) and isinstance(data.get("response"), dict) else {}
    info = resp.get("info_needed") if isinstance(resp.get("info_needed"), dict) else {}

    def _need(name: str) -> list[str]:
        value = info.get(name)
        if value is None and name == "non_integrated":
            value = info.get("non-integrated")
        if not isinstance(value, list):
            return []
        return [str(x or "").strip().lower() for x in value if str(x or "").strip()]

    pickup_need = _need("pickup")
    dropoff_need = _need("dropoff")
    nonint_need = _need("non_integrated")
    pickup_info = resp.get("pickup") if isinstance(resp.get("pickup"), dict) else {}
    dropoff_info = resp.get("dropoff") if isinstance(resp.get("dropoff"), dict) else {}
    pickup_addresses = pickup_info.get("address_list") if isinstance(pickup_info.get("address_list"), list) else []
    dropoff_branches = dropoff_info.get("branch_list") if isinstance(dropoff_info.get("branch_list"), list) else []
    dropoff_slugs = dropoff_info.get("slug_list") if isinstance(dropoff_info.get("slug_list"), list) else []

    # A Shopee define a modalidade pelos campos info_needed. Se a lista vier vazia,
    # a própria documentação exige ainda enviar o objeto da modalidade; nesse caso
    # inferimos pelo bloco de dados efetivamente retornado.
    mode = ""
    if pickup_need:
        mode = "pickup"
    elif dropoff_need:
        mode = "dropoff"
    elif nonint_need:
        mode = "non_integrated"
    elif pickup_addresses and not (dropoff_branches or dropoff_slugs):
        mode = "pickup"
    elif (dropoff_branches or dropoff_slugs) and not pickup_addresses:
        mode = "dropoff"
    elif isinstance(resp.get("pickup"), dict) and not isinstance(resp.get("dropoff"), dict):
        mode = "pickup"
    elif isinstance(resp.get("dropoff"), dict) and not isinstance(resp.get("pickup"), dict):
        mode = "dropoff"
    elif "pickup" in info and "dropoff" not in info:
        mode = "pickup"
    elif "dropoff" in info and "pickup" not in info:
        mode = "dropoff"
    elif "non_integrated" in info or "non-integrated" in info:
        mode = "non_integrated"

    base: dict[str, Any] = {"order_sn": order_sn}
    if package_number:
        base["package_number"] = package_number

    if mode == "pickup":
        # tracking_number em info_needed é um rastreio de terceiro a ser informado
        # antes do arrange; não é o tracking gerado pela Shopee depois de ship_order.
        if any(x in {"tracking_number", "tracking_no"} for x in pickup_need):
            return None, mode, "pickup exige tracking externo antes do arrange"
        addresses = [x for x in pickup_addresses if isinstance(x, dict)]
        chosen = None
        # Prefere endereço de coleta/default/current; se não houver flag, usa o primeiro válido.
        for addr in addresses:
            flags = [str(x or "").strip().lower() for x in (addr.get("address_flag") or addr.get("address_type") or []) if str(x or "").strip()]
            if any(x in {"pickup_address", "pick_up_address", "current_address", "default_address"} for x in flags):
                chosen = addr
                break
        if chosen is None and addresses:
            chosen = addresses[0]
        pickup_payload: dict[str, Any] = {}
        if chosen:
            aid = chosen.get("address_id")
            if aid not in (None, ""):
                try:
                    pickup_payload["address_id"] = int(aid)
                except Exception:
                    pickup_payload["address_id"] = aid
            slots = chosen.get("time_slot_list") if isinstance(chosen.get("time_slot_list"), list) else []
            valid_slots = [x for x in slots if isinstance(x, dict) and str(x.get("pickup_time_id") or "").strip()]
            selected_slot = None
            for slot in valid_slots:
                flags = [str(x or "").strip().lower() for x in (slot.get("flags") or [])]
                if "recommended" in flags:
                    selected_slot = slot
                    break
            if selected_slot is None and valid_slots:
                selected_slot = valid_slots[0]
            if selected_slot:
                pickup_payload["pickup_time_id"] = str(selected_slot.get("pickup_time_id") or "").strip()
        if "address_id" in pickup_need and not pickup_payload.get("address_id"):
            return None, mode, "pickup sem address_id disponível"
        # Alguns canais retornam pickup_time_id como necessário mas não ofertam slots;
        # a documentação permite arrange sem slot nesses casos. Portanto só enviamos se existir.
        base["pickup"] = pickup_payload
        return base, mode, f"pickup; needs={','.join(pickup_need) or '-'}; address={'sim' if pickup_payload.get('address_id') else 'nao'}; slot={'sim' if pickup_payload.get('pickup_time_id') else 'nao'}"

    if mode == "dropoff":
        if any(x in {"tracking_number", "tracking_no", "sender_real_name"} for x in dropoff_need):
            # Esses campos dependem de dado de transportadora/remetente externo e não
            # podem ser inventados automaticamente.
            return None, mode, f"dropoff exige dado manual: {','.join(dropoff_need)}"
        dropoff_payload: dict[str, Any] = {}
        if "branch_id" in dropoff_need:
            branches = [x for x in dropoff_branches if isinstance(x, dict) and x.get("branch_id") not in (None, "")]
            if not branches:
                return None, mode, "dropoff exige branch_id, mas não há branch disponível"
            bid = branches[0].get("branch_id")
            try:
                dropoff_payload["branch_id"] = int(bid)
            except Exception:
                dropoff_payload["branch_id"] = bid
        if "slug" in dropoff_need:
            slugs = [x for x in dropoff_slugs if isinstance(x, dict) and str(x.get("slug") or "").strip()]
            if not slugs:
                return None, mode, "dropoff exige slug, mas não há slug disponível"
            dropoff_payload["slug"] = str(slugs[0].get("slug") or "").strip()
        base["dropoff"] = dropoff_payload
        return base, mode, f"dropoff; needs={','.join(dropoff_need) or '-'}; branch={'sim' if dropoff_payload.get('branch_id') else 'nao'}; slug={'sim' if dropoff_payload.get('slug') else 'nao'}"

    if mode == "non_integrated":
        if nonint_need:
            return None, mode, f"canal não integrado exige dado manual: {','.join(nonint_need)}"
        base["non_integrated"] = {}
        return base, mode, "non_integrated sem parâmetro adicional"

    # Não escolhe pickup/dropoff arbitrariamente quando a Shopee não informa qual usar.
    keys = sorted(str(k) for k in resp.keys())
    return None, "indefinido", f"modalidade não identificada; response_keys={','.join(keys)}"


def _shopee_ship_order_cached(order_sn: str, package_number: str) -> tuple[bool, bool]:
    key = (str(order_sn or "").strip(), str(package_number or "").strip())
    now = time.time()
    with SHOPEE_SHIP_ORDER_CACHE_LOCK:
        # Limpa entradas antigas para o dicionário permanecer pequeno.
        stale = [k for k, ts in SHOPEE_SHIP_ORDER_CACHE.items() if now - float(ts or 0) > SHOPEE_SHIP_ORDER_CACHE_TTL]
        for k in stale:
            SHOPEE_SHIP_ORDER_CACHE.pop(k, None)
            SHOPEE_SHIP_ORDER_UNSPLIT_CACHE.discard(k)
        return key in SHOPEE_SHIP_ORDER_CACHE, key in SHOPEE_SHIP_ORDER_UNSPLIT_CACHE


def _shopee_forget_ship_order(order_sn: str, package_number: str) -> None:
    """V17.90 — descarta o "organizado" em cache de um pedido ainda sem rastreio."""
    key = (str(order_sn or "").strip(), str(package_number or "").strip())
    with SHOPEE_SHIP_ORDER_CACHE_LOCK:
        SHOPEE_SHIP_ORDER_CACHE.pop(key, None)
        SHOPEE_SHIP_ORDER_UNSPLIT_CACHE.discard(key)


def _shopee_mark_ship_order_cached(order_sn: str, package_number: str, *, unsplit: bool = False) -> None:
    key = (str(order_sn or "").strip(), str(package_number or "").strip())
    with SHOPEE_SHIP_ORDER_CACHE_LOCK:
        SHOPEE_SHIP_ORDER_CACHE[key] = time.time()
        if unsplit:
            SHOPEE_SHIP_ORDER_UNSPLIT_CACHE.add(key)
        else:
            SHOPEE_SHIP_ORDER_UNSPLIT_CACHE.discard(key)


def _shopee_package_not_needed_error(message: str) -> bool:
    # V17.89: a mensagem real da Shopee é
    #   "logistics.ship_order_not_need_pacakge_number Please don’t request with
    #    package_number for this unsplit order."
    # — "pacakge" com erro de digitação e apóstrofo tipográfico (’). Nenhuma das
    # comparações antigas batia, o fallback sem package_number nunca rodava e as
    # etiquetas Shopee pararam de sair sozinhas em 10/09/2026.
    low = str(message or "").lower().replace("’", "'").replace("‘", "'")
    return (
        bool(re.search(r"not_need_pa[a-z]{2,4}ge_number", low))
        or "request with package_number" in low
    )


def shopee_sync_shipping_documents(shipments: list[dict[str,str]],
                                   ja_organizados: set[str] | frozenset[str] = frozenset()) -> dict[str,Any]:
    """Organiza envio e processa etiquetas Shopee sem bloquear o request HTTP.

    V17.32: get_shipping_parameter -> ship_order -> get_tracking_number ->
    create_shipping_document -> get_shipping_document_result -> download/vínculo.
    Para pedidos unsplit, ship_order e tracking omitem package_number.
    O worker chama esta função; não existe polling síncrono no endpoint de sync.
    """
    summary={
        "consultados":0,"shipping_parameter_ok":0,"ship_order_ok":0,"ship_order_pendente":0,
        "tracking_ok":0,"tracking_pendente":0,"criados":0,"prontos":0,"baixados":0,
        "vinculados":0,"nao_zpl":0,"pendentes":0,"erros":0,"data_info_ok":0,
        "data_info_imagem":0,"aguardando":0,
    }
    orders=_shopee_logistics_order_list(shipments)
    if not orders:
        return summary

    labels_dir = DB_PATH.parent / "shopee_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    def _key(row: dict[str,Any]) -> tuple[str,str]:
        return (str(row.get("order_sn") or "").strip(), str(row.get("package_number") or "").strip())

    def _safe_err(exc: Exception) -> str:
        return str(exc or "")[:900]

    def _parse_result_list(data: dict[str,Any]) -> list[dict[str,Any]]:
        resp=data.get("response") if isinstance(data,dict) and isinstance(data.get("response"),dict) else {}
        vals=resp.get("result_list") if isinstance(resp.get("result_list"),list) else []
        return [x for x in vals if isinstance(x,dict)]

    for off in range(0,len(orders),50):
        base_batch=orders[off:off+50]
        summary["consultados"] += len(base_batch)
        try:
            # Documento: descobre package_number e tipo sugerido antes de qualquer create.
            try:
                par=shopee_api_post("/api/v2/logistics/get_shipping_document_parameter", {"order_list":base_batch})
                presults=_parse_result_list(par)
            except Exception as exc:
                shopee_log_event("shipping_document_parameter","batch","ERRO",_safe_err(exc))
                summary["erros"] += len(base_batch)
                continue

            work: list[dict[str,Any]]=[]
            seen:set[tuple[str,str]]=set()
            for pr in presults:
                order_sn,pkg=_key(pr)
                if not order_sn:
                    continue
                ferr=str(pr.get("fail_error") or "").strip()
                if ferr:
                    summary["erros"] += 1
                    shopee_log_event("shipping_document_parameter",f"order={order_sn}","ERRO",f"erro={ferr}; msg={str(pr.get('fail_message') or '')[:450]}")
                    continue
                dtype=str(pr.get("suggest_shipping_document_type") or "").strip()
                selectable=pr.get("selectable_shipping_document_type")
                if not dtype and isinstance(selectable,list) and selectable:
                    dtype=str(selectable[0] or "").strip()
                k=(order_sn,pkg)
                if k in seen:
                    continue
                seen.add(k)
                row={"order_sn":order_sn}
                if pkg:
                    row["package_number"]=pkg
                if dtype:
                    row["shipping_document_type"]=dtype
                work.append(row)
                shopee_log_event("shipping_document_parameter",f"order={order_sn}","OK",f"pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")

            if not work:
                for src in base_batch:
                    order_sn,pkg=_key(src)
                    if not order_sn:
                        continue
                    row={"order_sn":order_sn}
                    if pkg:
                        row["package_number"]=pkg
                    work.append(row)

            for row in work:
                order_sn=str(row.get("order_sn") or "").strip()
                pkg=str(row.get("package_number") or "").strip()
                dtype=str(row.get("shipping_document_type") or "").strip()
                if not order_sn:
                    continue

                # 1) get_shipping_parameter -> 2) ship_order. Só pula o POST quando
                # este mesmo processo já recebeu confirmação recente para o pacote.
                arranged,unsplit_order=_shopee_ship_order_cached(order_sn,pkg)
                organizado_neste_ciclo=not arranged and order_sn not in ja_organizados
                # V17.91: pedido que não está mais na lista "a organizar" da Shopee
                # (get_shipment_list) já foi organizado — vai direto ao rastreio.
                if order_sn in ja_organizados:
                    arranged,unsplit_order=True,True
                if not arranged:
                    try:
                        ship_payload,mode,detail=_shopee_shipping_mode_payload(order_sn,pkg)
                        summary["shipping_parameter_ok"] += 1
                        shopee_log_event("shipping_parameter",f"order={order_sn}","OK",f"modo={mode}; pacote={'sim' if pkg else 'nao'}; {detail}")
                    except Exception as exc:
                        summary["erros"] += 1
                        summary["pendentes"] += 1
                        shopee_log_event("shipping_parameter",f"order={order_sn}","ERRO",_safe_err(exc))
                        continue

                    if not ship_payload:
                        summary["ship_order_pendente"] += 1
                        summary["pendentes"] += 1
                        shopee_log_event("ship_order",f"order={order_sn}","PENDENTE",f"modo={mode}; {detail}")
                        continue

                    try:
                        shopee_api_post("/api/v2/logistics/ship_order", ship_payload)
                        arranged=True
                        _shopee_mark_ship_order_cached(order_sn,pkg,unsplit=False)
                        summary["ship_order_ok"] += 1
                        shopee_log_event("ship_order",f"order={order_sn}","OK",f"modo={mode}; pacote={'sim' if pkg else 'nao'}")
                    except Exception as exc:
                        msg=_safe_err(exc)
                        low=msg.lower()
                        if "lack_of_invoice" in low:
                            summary["pendentes"] += 1
                            shopee_log_event("ship_order",f"order={order_sn}","PENDENTE","aguardando NF-e na Shopee; "+msg[:200])
                            continue
                        # Shopee devolve package_number no parâmetro de documento mesmo
                        # para pedidos UNSPLIT, mas ship_order rejeita esse campo. Nesse
                        # caso refazemos UMA vez sem package_number e lembramos o estado
                        # para que get_tracking_number também seja chamado sem o pacote.
                        if pkg and _shopee_package_not_needed_error(msg):
                            retry_payload=dict(ship_payload)
                            retry_payload.pop("package_number",None)
                            try:
                                shopee_api_post("/api/v2/logistics/ship_order", retry_payload)
                                arranged=True
                                unsplit_order=True
                                _shopee_mark_ship_order_cached(order_sn,pkg,unsplit=True)
                                summary["ship_order_ok"] += 1
                                shopee_log_event("ship_order",f"order={order_sn}","OK",f"modo={mode}; unsplit=sim; fallback_sem_package=sim")
                            except Exception as retry_exc:
                                retry_msg=_safe_err(retry_exc)
                                retry_low=retry_msg.lower()
                                # V17.91: "lack_of_invoice_data ... already submitted" contém
                                # "already" e era lido como "já organizado". É falta de NF-e.
                                if "lack_of_invoice" in retry_low:
                                    summary["pendentes"] += 1
                                    shopee_log_event("ship_order",f"order={order_sn}","PENDENTE","aguardando NF-e na Shopee; "+retry_msg[:200])
                                    continue
                                already_tokens=("already", "has been shipped", "already shipped", "already arrange", "already arranged", "logistics_request_created")
                                if any(tok in retry_low for tok in already_tokens):
                                    arranged=True
                                    unsplit_order=True
                                    _shopee_mark_ship_order_cached(order_sn,pkg,unsplit=True)
                                    summary["ship_order_ok"] += 1
                                    # V17.90: registra a mensagem real — em 18/09/2026 este "já
                                    # organizado" escondia a recusa da Shopee por falta de NF-e.
                                    shopee_log_event("ship_order",f"order={order_sn}","OK",f"já organizado; unsplit=sim; fallback_sem_package=sim; resposta={retry_msg[:240]}")
                                else:
                                    summary["erros"] += 1
                                    summary["pendentes"] += 1
                                    shopee_log_event("ship_order",f"order={order_sn}","ERRO",f"modo={mode}; fallback_sem_package=sim; {retry_msg}")
                                    continue
                        else:
                            # ship_order é uma transição de estado. Em restart do serviço,
                            # o pedido pode já estar organizado; esses retornos são idempotentes.
                            already_tokens=("already", "has been shipped", "already shipped", "already arrange", "already arranged", "logistics_request_created")
                            if any(tok in low for tok in already_tokens):
                                arranged=True
                                _shopee_mark_ship_order_cached(order_sn,pkg,unsplit=unsplit_order)
                                summary["ship_order_ok"] += 1
                                shopee_log_event("ship_order",f"order={order_sn}","OK",f"já organizado; pacote={'sim' if pkg else 'nao'}; resposta={msg[:240]}")
                            else:
                                summary["erros"] += 1
                                summary["pendentes"] += 1
                                shopee_log_event("ship_order",f"order={order_sn}","ERRO",f"modo={mode}; {msg}")
                                continue
                else:
                    shopee_log_event("ship_order",f"order={order_sn}","OK",f"cache=sim; unsplit={'sim' if unsplit_order else 'nao'}; pacote={'sim' if pkg else 'nao'}")

                # 3) O rastreio só é consultado depois do arrange confirmado.
                # Pedido UNSPLIT deve omitir package_number também nesta etapa.
                q={"order_sn":order_sn}
                if pkg and not unsplit_order:
                    q["package_number"]=pkg
                tracking=""
                hint=""
                # V18.62: logo após o ship_order o rastreio costuma levar alguns
                # segundos; uma segunda tentativa curta evita esperar o ciclo seguinte.
                for tentativa in range(2 if organizado_neste_ciclo else 1):
                    if tentativa:
                        time.sleep(SHOPEE_DOC_RESULT_RETRY_WAIT)
                    try:
                        tr=shopee_api_get("/api/v2/logistics/get_tracking_number", q)
                        tresp=tr.get("response") if isinstance(tr.get("response"),dict) else {}
                        tracking=str(tresp.get("tracking_number") or "").strip()
                        hint=str(tresp.get("hint") or "").strip()
                        # V17.91: pedido que chegou já organizado não sabe se é unsplit;
                        # sem rastreio consultando sem o pacote, tenta com o pacote.
                        if not tracking and pkg and "package_number" not in q:
                            tr=shopee_api_get("/api/v2/logistics/get_tracking_number", {"order_sn":order_sn,"package_number":pkg})
                            tresp=tr.get("response") if isinstance(tr.get("response"),dict) else {}
                            tracking=str(tresp.get("tracking_number") or "").strip()
                            hint=hint or str(tresp.get("hint") or "").strip()
                    except Exception as exc:
                        shopee_log_event("tracking_number",f"order={order_sn}","AVISO",_safe_err(exc))
                    if tracking:
                        break
                if tracking:
                    summary["tracking_ok"] += 1
                    shopee_log_event("tracking_number",f"order={order_sn}","OK",f"obtido=sim; pacote={'sim' if pkg and not unsplit_order else 'nao'}; unsplit={'sim' if unsplit_order else 'nao'}")
                else:
                    summary["tracking_pendente"] += 1
                    summary["pendentes"] += 1
                    summary["aguardando"] += 1
                    shopee_log_event("tracking_number",f"order={order_sn}","PENDENTE",f"obtido=nao; pacote={'sim' if pkg and not unsplit_order else 'nao'}; unsplit={'sim' if unsplit_order else 'nao'}; hint={hint[:300] or '-'}")
                    # O tracking pode ser emitido alguns minutos depois do ship_order.
                    # Worker tenta novamente no próximo ciclo; não faz polling aqui.
                    # V17.91: a falta de NF-e agora é tratada no ship_order (não entra
                    # mais no cache), e o pedido organizado segue no ciclo seguinte
                    # pela lista de pendentes sem etiqueta — não precisa esquecer aqui.
                    continue

                # 4) Cria documento somente com tracking disponível.
                create_row={"order_sn":order_sn,"tracking_number":tracking}
                if pkg:
                    create_row["package_number"]=pkg
                if dtype:
                    create_row["shipping_document_type"]=dtype
                create_ok=False
                create_soft=False
                try:
                    created=shopee_api_post("/api/v2/logistics/create_shipping_document", {"order_list":[create_row]})
                    crlist=_parse_result_list(created)
                    failures=[x for x in crlist if str(x.get("fail_error") or "").strip()]
                    if failures:
                        fr=failures[0]
                        ferr=str(fr.get("fail_error") or "").strip()
                        fmsg=str(fr.get("fail_message") or "")[:500]
                        if ferr in {"logistics.shipping_document_processing","logistics.shipping_document_already_exist","logistics.shipping_document_already_exists"}:
                            create_soft=True
                            shopee_log_event("shipping_document_create",f"order={order_sn}","OK",f"estado_existente={ferr}; pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")
                        else:
                            summary["erros"] += 1
                            summary["pendentes"] += 1
                            shopee_log_event("shipping_document_create",f"order={order_sn}","ERRO",f"erro={ferr}; msg={fmsg}; tracking=sim; pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")
                    else:
                        create_ok=True
                        summary["criados"] += 1
                        shopee_log_event("shipping_document_create",f"order={order_sn}","OK",f"tracking=sim; pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")
                except Exception as exc:
                    msg=_safe_err(exc)
                    low=msg.lower()
                    if "already" in low or "processing" in low:
                        create_soft=True
                        shopee_log_event("shipping_document_create",f"order={order_sn}","OK",f"estado_existente; pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")
                    else:
                        shopee_log_event("shipping_document_create",f"order={order_sn}","ERRO",f"{msg}; tracking=sim; pacote={'sim' if pkg else 'nao'}; doc={dtype or '-'}")
                        summary["erros"] += 1
                        summary["pendentes"] += 1

                # Regra crítica: nunca consulta result se create não foi confirmado.
                if not (create_ok or create_soft):
                    continue

                # 5) Consulta de status. V18.62: se ainda estiver gerando, espera
                # alguns segundos e consulta UMA vez mais; o resto fica para o worker.
                result_row={"order_sn":order_sn}
                if pkg:
                    result_row["package_number"]=pkg
                if dtype:
                    result_row["shipping_document_type"]=dtype
                rlist=[]
                result_exc=None
                for tentativa in range(2):
                    if tentativa:
                        time.sleep(SHOPEE_DOC_RESULT_RETRY_WAIT)
                    try:
                        result=shopee_api_post("/api/v2/logistics/get_shipping_document_result", {"order_list":[result_row]})
                        rlist=_parse_result_list(result)
                        result_exc=None
                    except Exception as exc:
                        rlist=[]
                        result_exc=exc
                        if "shipping_document_should_print_first" not in _safe_err(exc):
                            break
                        continue
                    st_tmp=str((rlist[0] if rlist else {}).get("status") or "").upper().strip()
                    ferr_tmp=str((rlist[0] if rlist else {}).get("fail_error") or "").strip()
                    if rlist and (ferr_tmp or st_tmp not in {"PROCESSING","PENDING",""}):
                        break
                if result_exc is not None:
                    msg=_safe_err(result_exc)
                    summary["pendentes"] += 1
                    if "shipping_document_should_print_first" in msg:
                        summary["aguardando"] += 1
                        shopee_log_event("shipping_document_result",f"order={order_sn}","AVISO",f"create confirmado neste ciclo, mas Shopee ainda não liberou result; {msg}")
                    else:
                        summary["erros"] += 1
                        shopee_log_event("shipping_document_result",f"order={order_sn}","ERRO",msg)
                    continue

                if not rlist:
                    summary["pendentes"] += 1
                    summary["aguardando"] += 1
                    shopee_log_event("shipping_document_result",f"order={order_sn}","PENDENTE","result_list vazio")
                    continue

                rr=rlist[0]
                st=str(rr.get("status") or "").upper().strip()
                ferr=str(rr.get("fail_error") or "").strip()
                fmsg=str(rr.get("fail_message") or "")[:500]
                if st=="READY":
                    summary["prontos"] += 1
                    shopee_log_event("shipping_document_result",f"order={order_sn}","OK",f"status=READY; doc={dtype or '-'}")
                    reqrow={"order_sn":order_sn}
                    if pkg:
                        reqrow["package_number"]=pkg
                    payload={"order_list":[reqrow]}
                    if dtype:
                        payload["shipping_document_type"]=dtype
                    try:
                        raw,ctype=shopee_api_post_bytes("/api/v2/logistics/download_shipping_document", payload)
                        if raw:
                            if raw.startswith(b"%PDF") or "pdf" in ctype.lower():
                                ext="pdf"
                            elif raw[:4]==b"PK\x03\x04":
                                ext="zip"
                            else:
                                ext="bin"
                            safe=re.sub(r"[^A-Za-z0-9_.-]+","_",order_sn+("_"+pkg if pkg else ""))
                            fp=labels_dir/f"{safe}.{ext}"
                            fp.write_bytes(raw)
                            summary["baixados"] += 1
                            zpl=_shopee_extract_zpl(raw)
                            if zpl and _shopee_store_linked_label(order_sn,pkg,zpl):
                                summary["vinculados"] += 1
                                state="VINCULADA_ZPL"
                            else:
                                summary["nao_zpl"] += 1
                                state="BAIXADA_SEM_ZPL"
                            shopee_log_event("shipping_document_download",f"order={order_sn}","OK",f"arquivo={fp.name}; bytes={len(raw)}; tipo={ctype[:80]}; doc={dtype}; estado={state}")
                    except Exception as exc:
                        summary["erros"] += 1
                        shopee_log_event("shipping_document_download",f"order={order_sn}","ERRO",_safe_err(exc))
                elif st in {"PROCESSING","PENDING",""} and not ferr:
                    summary["pendentes"] += 1
                    summary["aguardando"] += 1
                    shopee_log_event("shipping_document_result",f"order={order_sn}","PENDENTE",f"status={st or 'PROCESSING'}; doc={dtype or '-'}")
                    continue
                elif ferr or st=="FAILED":
                    summary["erros"] += 1
                    summary["pendentes"] += 1
                    shopee_log_event("shipping_document_result",f"order={order_sn}","ERRO",f"status={st or '-'}; erro={ferr or '-'}; msg={fmsg}")
                else:
                    summary["pendentes"] += 1

                # V18.62: o diagnóstico era uma chamada extra por pedido em todo ciclo,
                # inclusive com a etiqueta já baixada. Agora só roda quando algo falhou.
                if st=="READY":
                    continue
                # Diagnóstico por pedido; nunca bloqueia o fluxo principal se falhar.
                try:
                    dinfo_payload={"order_sn":order_sn}
                    if pkg:
                        dinfo_payload["package_number"]=pkg
                    info_data=shopee_api_post("/api/v2/logistics/get_shipping_document_data_info", dinfo_payload)
                    resp=info_data.get("response") if isinstance(info_data.get("response"),dict) else {}
                    if resp:
                        summary["data_info_ok"] += 1
                        def has_image(v):
                            if isinstance(v,dict):
                                return any(("image" in str(k).lower() and bool(x)) or has_image(x) for k,x in v.items())
                            if isinstance(v,list):
                                return any(has_image(x) for x in v)
                            return False
                        if has_image(resp):
                            summary["data_info_imagem"] += 1
                        keys=sorted(str(k) for k in resp.keys())
                        shopee_log_event("shipping_data_info",f"order={order_sn}","OK",f"chaves={','.join(keys)[:800]}; com_imagem={1 if has_image(resp) else 0}")
                except Exception as exc:
                    shopee_log_event("shipping_data_info",f"order={order_sn}","AVISO",_safe_err(exc))
        except Exception as exc:
            summary["erros"] += len(base_batch)
            shopee_log_event("shipping_documents","batch","ERRO",_safe_err(exc))
    return summary

def shopee_pedidos_sem_etiqueta(dias: int = 10, limite: int = 40) -> list[dict[str, str]]:
    """V17.91 — pedidos Shopee pendentes no Noromak que ainda não têm etiqueta
    vinculada no banco da Conferência. Só os recentes, para não varrer histórico."""
    corte = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    with DB_LOCK:
        pendentes, _, _ = get_storage("conf_pedidos")
        etiquetas, _, _ = get_storage("conf_etiquetas")
    try:
        banco = json.loads(etiquetas or "{}")
    except Exception:
        banco = {}
    vinculados = set((banco.get("porChave") or {}).keys()) if isinstance(banco, dict) else set()
    candidatos: list[tuple[str, dict[str, str]]] = []
    vistos: set[str] = set()
    for row in _rows_from_storage_value(pendentes):
        if not isinstance(row, dict) or "SHOPEE" not in str(row.get("Marketplace") or "").upper():
            continue
        order_sn = str(row.get("Pedido") or "").strip()
        if not order_sn or order_sn in vistos or order_sn in vinculados:
            continue
        data = _vendas_data_iso(row.get("Data do Pedido"))
        if data < corte:
            continue
        vistos.add(order_sn)
        candidatos.append((data, {"order_sn": order_sn, "package_number": str(row.get("PacoteShopee") or "").strip()}))
    # V18.62: mais recentes primeiro. Antes o corte em `limite` seguia a ordem do
    # banco e pedidos travados (ex.: sem NF-e) deixavam o pedido novo de fora.
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return [x for _, x in candidatos[:limite]]


def shopee_current_shipments() -> list[dict[str, str]]:
    """Lista o que realmente está aguardando expedição na Shopee.

    get_shipment_list é preferido aqui porque não depende de janela de 15 dias e
    retorna os pedidos/pacotes em READY_TO_SHIP/RETRY_SHIP.
    """
    path = "/api/v2/order/get_shipment_list"
    cursor = ""
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _ in range(30):
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["cursor"] = cursor
        data = shopee_api_get(path, params)
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        order_list = response.get("order_list") if isinstance(response.get("order_list"), list) else []
        for item in order_list:
            if not isinstance(item, dict):
                continue
            order_sn = str(item.get("order_sn") or "").strip()
            package_number = str(item.get("package_number") or "").strip()
            key = (order_sn, package_number)
            if order_sn and key not in seen:
                found.append({"order_sn": order_sn, "package_number": package_number})
                seen.add(key)
        if not bool(response.get("more")):
            break
        next_cursor = str(response.get("next_cursor") or "").strip()
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return found


def shopee_order_details(order_sns: list[str]) -> list[dict[str, Any]]:
    path = "/api/v2/order/get_order_detail"
    details: list[dict[str, Any]] = []
    unique = list(dict.fromkeys(str(x).strip() for x in order_sns if str(x).strip()))
    optional = "recipient_address,item_list,package_list,shipping_carrier,invoice_data,buyer_cpf_id,buyer_cnpj_id,buyer_user_id,buyer_username,buyer_name,buyer_phone_number,address"
    for offset in range(0, len(unique), 50):
        batch = unique[offset:offset + 50]
        data = shopee_api_get(path, {
            "order_sn_list": ",".join(batch),
            "response_optional_fields": optional,
        })
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        rows = response.get("order_list") if isinstance(response.get("order_list"), list) else []
        details.extend(x for x in rows if isinstance(x, dict))
    return details


def _shopee_date(timestamp: Any) -> str:
    try:
        value = int(timestamp or 0)
        if value > 0:
            return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%d/%m/%Y")
    except Exception:
        pass
    return datetime.now().astimezone().strftime("%d/%m/%Y")


def _shopee_build_pending_lines(order: dict[str, Any], package_numbers: list[str], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_sn = str(order.get("order_sn") or "").strip()
    if not order_sn:
        return []
    recipient = order.get("recipient_address") if isinstance(order.get("recipient_address"), dict) else {}
    address = order.get("address") if isinstance(order.get("address"), dict) else {}

    def _shopee_real_text(*values: Any) -> str:
        # A API pode devolver dados mascarados como ****. Não grava máscara como nome.
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            visible = re.sub(r"[\s*•xX._-]+", "", text)
            if visible:
                return text
        return ""

    # Nome usado na expedição/NF-e: prioriza nome real. A Shopee BR está
    # mascarando recipient_address.name nesta conta; por solicitação operacional,
    # usa buyer_username apenas como fallback visível para permitir conferência
    # manual até que a API libere o nome fiscal completo.
    buyer_username = str(order.get("buyer_username") or "").strip()
    # V17.88: nome real separado do fallback. O nome de usuário continua
    # servindo para a coluna Cliente, mas NUNCA como nome fiscal — ele ia para
    # o cadastro do Linx como se fosse o nome da nota.
    shopee_nome_real = _shopee_real_text(
        recipient.get("name"),
        order.get("buyer_name"),
        address.get("name"),
    )
    client = shopee_nome_real or _shopee_real_text(buyer_username) or "NOME NÃO DISPONÍVEL"
    buyer_cpf = _shopee_real_text(order.get("buyer_cpf_id"))
    buyer_cnpj = _shopee_real_text(order.get("buyer_cnpj_id"))
    buyer_phone = _shopee_real_text(order.get("buyer_phone_number"), recipient.get("phone"), address.get("phone"))

    # Diagnóstico seguro: registra apenas presença/mascaramento dos campos, sem
    # gravar tokens e sem colocar CPF/CNPJ no log de integração.
    def _field_state(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "ausente"
        return "visivel" if _shopee_real_text(text) else "mascarado"
    shopee_name_diag = ";".join((
        f"buyer_name={_field_state(order.get('buyer_name'))}",
        f"recipient_name={_field_state(recipient.get('name'))}",
        f"address_name={_field_state(address.get('name'))}",
        f"buyer_username={_field_state(buyer_username)}",
        f"buyer_cpf_id={_field_state(order.get('buyer_cpf_id'))}",
        f"buyer_cnpj_id={_field_state(order.get('buyer_cnpj_id'))}",
    ))
    status = str(order.get("order_status") or "READY_TO_SHIP").strip()
    packages = order.get("package_list") if isinstance(order.get("package_list"), list) else []
    api_packages = [str(x.get("package_number") or "").strip() for x in packages if isinstance(x, dict) and str(x.get("package_number") or "").strip()]
    all_packages = list(dict.fromkeys([*package_numbers, *api_packages]))
    invoice = order.get("invoice_data") if isinstance(order.get("invoice_data"), dict) else {}
    invoice_number = str(invoice.get("number") or "").strip()
    invoice_key = str(invoice.get("access_key") or "").strip()
    shipping_carrier = str(order.get("shipping_carrier") or "").strip()
    lines: list[dict[str, Any]] = []
    for idx, item in enumerate(order.get("item_list") or []):
        if not isinstance(item, dict):
            continue
        sku = str(item.get("model_sku") or item.get("item_sku") or "").strip()
        if not sku:
            continue
        quantity = item.get("active_qty")
        try:
            quantity = int(quantity or 0)
        except Exception:
            quantity = 0
        if quantity <= 0:
            try:
                quantity = int(item.get("model_quantity_purchased") or 1)
            except Exception:
                quantity = 1
        description = str(item.get("item_name") or "").strip()
        model_name = str(item.get("model_name") or "").strip()
        if model_name and normalize(model_name) not in normalize(description):
            description = f"{description} - {model_name}".strip(" -")
        identity = f"{order_sn}|{item.get('item_id')}|{item.get('model_id')}|{item.get('line_item_id')}|{sku}"
        row = {
            "Pedido": order_sn,
            "Marketplace": "SHOPEE",
            "Data do Pedido": _shopee_date(order.get("create_time")),
            "Kit": "",
            "Nome do Kit": "",
            "Quantidade de Kits": "",
            "Quantidade por Kit": "",
            "Cliente": client,
            "NomeFiscalShopee": shopee_nome_real,
            "UsuarioShopee": buyer_username,
            "CPFShopee": buyer_cpf,
            "CNPJShopee": buyer_cnpj,
            "TelefoneShopee": buyer_phone,
            "DiagnosticoNomeShopee": shopee_name_diag,
            "SKU": sku,
            "Quantidade": str(max(1, quantity)),
            "Código de Barras": "",
            "Descrição": description,
            "Rua": "", "Módulo": "", "Prateleira": "", "Nível": "", "Posição": "", "Foto": "",
            "Código de Rastreio": "",
            "NF": invoice_number,
            "Nota Fiscal": invoice_number,
            "Chave de Acesso": invoice_key,
            "PacoteShopee": ",".join(all_packages),
            "TransportadoraShopee": shipping_carrier,
            "StatusShopee": status,
            "StatusOrigemShopee": status,
            "OrigemCadastro": "API_SHOPEE",
            "ShopIdShopee": str((shopee_integration_row() or {"shop_id": ""})["shop_id"] if shopee_integration_row() else ""),
            "SkuOrigemImportacao": sku,
            "SkuPrincipalShopee": str(item.get("item_sku") or "").strip(),
            "SkuReferenciaShopee": str(item.get("model_sku") or "").strip(),
            "VariacaoShopee": model_name,
            "ItemIdShopee": str(item.get("item_id") or ""),
            "ModelIdShopee": str(item.get("model_id") or ""),
            "LineItemIdShopee": str(item.get("line_item_id") or ""),
            "ShopeeAtualizadoEm": now_iso(),
            "CriadoEm": now_iso(),
            "__uidLinha": "SHOPEEAPI_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20],
        }
        product = _ml_find_product(products, sku)
        if product:
            for field in ("SKU", "Código de Barras", "Descrição", "Rua", "Módulo", "Prateleira", "Nível", "Posição", "Foto"):
                value = product.get(field)
                if str(value or "").strip():
                    row[field] = value
        lines.append(row)
    return lines



def _shopee_consolidate_api_quantities_v1778(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consolida linhas repetidas da mesma venda/item Shopee sem inflar quantidade."""
    result: list[dict[str, Any]]=[]
    positions: dict[tuple[str,str,str,str,str],int]={}
    for row in lines:
        if not isinstance(row,dict):
            continue
        key=(
            normalize(order_number(row)),
            str(row.get("ItemIdShopee") or ""),
            str(row.get("ModelIdShopee") or ""),
            str(row.get("LineItemIdShopee") or ""),
            normalize(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU")),
        )
        # line_item_id presente identifica uma linha real única. Sem ele,
        # item/model/SKU formam a unidade comercial e duplicatas são somadas.
        if key not in positions:
            positions[key]=len(result)
            result.append(dict(row))
            continue
        i=positions[key]
        try: a=max(1,int(float(result[i].get("Quantidade") or 1)))
        except Exception: a=1
        try: b=max(1,int(float(row.get("Quantidade") or 1)))
        except Exception: b=1
        result[i]["Quantidade"]=str(a+b)
    return result


def _shopee_filter_artificial_kit_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove linhas artificiais de kit quando os produtos reais já vieram da Shopee.

    Casos cobertos de forma conservadora:
    - SKU composto por barras (A/B) quando A e B já existem no mesmo pedido;
    - SKU no padrão kit2-SKU quando o SKU real também existe no mesmo pedido e
      sua quantidade já contempla a multiplicidade indicada no prefixo.
    """
    if not lines:
        return []
    by_order: dict[str, list[dict[str, Any]]] = {}
    for row in lines:
        by_order.setdefault(normalize(order_number(row)), []).append(row)
    result: list[dict[str, Any]] = []
    for _, group in by_order.items():
        real_qty: dict[str, int] = {}
        for row in group:
            sku = str(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU") or "").strip()
            norm = normalize(sku)
            if not norm or "/" in sku or re.match(r"^KIT\s*\d*[-_: ]+", sku, re.I):
                continue
            try:
                qty = max(1, int(float(row.get("Quantidade") or 1)))
            except Exception:
                qty = 1
            real_qty[norm] = real_qty.get(norm, 0) + qty
        for row in group:
            sku = str(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU") or "").strip()
            if "/" in sku:
                parts = [normalize(x) for x in sku.split("/") if normalize(x)]
                if len(parts) >= 2 and all(part in real_qty for part in set(parts)):
                    continue
            m = re.match(r"^KIT\s*(\d+)?[-_: ]+(.+)$", sku, re.I)
            if m:
                mult = max(1, int(m.group(1) or 1))
                base = normalize(m.group(2))
                if base and real_qty.get(base, 0) >= mult:
                    continue
            result.append(row)
    return result



PENDING_ORDER_DELETE_KEY_V1766 = "conf_pedidos_exclusoes_v1766"


def _pending_order_manual_deletions() -> list[dict[str, Any]]:
    row = DB.execute("SELECT value FROM storage WHERE key=?", (PENDING_ORDER_DELETE_KEY_V1766,)).fetchone()
    if not row:
        return []
    try:
        parsed = json.loads(str(row["value"] or "[]"))
    except Exception:
        return []
    return [dict(x) for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []


def _pending_order_is_manually_deleted(row: dict[str, Any], deletions: list[dict[str, Any]] | None = None) -> bool:
    order = normalize(order_number(row))
    if not order:
        return False
    for item in (deletions if deletions is not None else _pending_order_manual_deletions()):
        if normalize(item.get("Pedido")) == order and bool(item.get("Ativo", True)):
            return True
    return False


def _pending_admin_restore_orders(order_keys: set[str]) -> None:
    """V17.78 — inclusão explícita do ADMIN vence exclusão/tombstone anterior."""
    keys={normalize(x) for x in order_keys if normalize(x)}
    if not keys:
        return
    # V18.56: a conexão é UMA só para todas as threads. Sem o DB_LOCK, o COMMIT
    # abaixo gravava pela metade a transação de um save_storage() de outra thread.
    with DB_LOCK:
        _pending_admin_restore_orders_locked(keys)


def _pending_admin_restore_orders_locked(keys: set[str]) -> None:
    # Tombstone genérico V17.66.
    row=DB.execute("SELECT value,version FROM storage WHERE key=?", (PENDING_ORDER_DELETE_KEY_V1766,)).fetchone()
    if row:
        try: items=json.loads(str(row["value"] or "[]"))
        except Exception: items=[]
        changed=False
        if isinstance(items,list):
            for item in items:
                if isinstance(item,dict) and normalize(item.get("Pedido")) in keys and bool(item.get("Ativo",True)):
                    item["Ativo"]=False
                    item["RestauradoPorAdminEm"]=now_iso()
                    changed=True
        if changed:
            DB.execute("UPDATE storage SET value=?,version=?,updated_at=? WHERE key=?",
                       (json_dumps(items),int(row["version"] or 0)+1,now_iso(),PENDING_ORDER_DELETE_KEY_V1766))
    # Tombstones Shopee por pedido/linha também não podem bloquear uma reinclusão ADMIN.
    row=DB.execute("SELECT value,version FROM storage WHERE key='conf_shopee_exclusoes_v1'").fetchone()
    if row:
        try: items=json.loads(str(row["value"] or "[]"))
        except Exception: items=[]
        changed=False
        if isinstance(items,list):
            kept=[]
            for item in items:
                if isinstance(item,dict) and normalize(item.get("Pedido")) in keys:
                    changed=True
                    continue
                kept.append(item)
            if changed:
                DB.execute("UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_shopee_exclusoes_v1'",
                           (json_dumps(kept),int(row["version"] or 0)+1,now_iso()))
    # V17.82: os UPDATEs acima abrem transação implícita. Sem este COMMIT, o
    # BEGIN IMMEDIATE do save_storage() seguinte falhava e a reinclusão explícita
    # do ADMIN morria em HTTP 409 — deixando o pedido preso no tombstone.
    DB.commit()


def _pending_filter_manual_deleted_orders_value(value: str | None) -> str:
    try:
        rows = json.loads(value or "[]")
    except Exception:
        return value or "[]"
    if not isinstance(rows, list):
        return value or "[]"
    deletions = _pending_order_manual_deletions()
    if not deletions:
        return json_dumps(rows)
    return json_dumps([
        row for row in rows
        if not (isinstance(row, dict) and _pending_order_is_manually_deleted(row, deletions))
    ])


def _shopee_load_manual_deletions() -> list[dict[str, Any]]:
    raw, _, _ = get_storage("conf_shopee_exclusoes_v1")
    rows = _rows_from_storage_value(raw)
    return [x for x in rows if isinstance(x, dict)]


def _shopee_is_manually_deleted(row: dict[str, Any], deletions: list[dict[str, Any]]) -> bool:
    order = normalize(order_number(row))
    uid = str(row.get("__uidLinha") or "").strip()
    source_sku = normalize(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU"))
    for item in deletions:
        if normalize(item.get("Pedido")) != order:
            continue
        # V17.66: exclusão do KIT/pedido inteiro pelo ADMIN vence qualquer sync Shopee.
        if bool(item.get("ExcluirPedido")) or normalize(item.get("Tipo")) in {"PEDIDO", "KIT_INTEIRO"}:
            return True
        deleted_uid = str(item.get("UidLinha") or "").strip()
        deleted_sku = normalize(item.get("SkuOrigem") or item.get("SKU"))
        if deleted_uid and uid and deleted_uid == uid:
            return True
        if deleted_sku and source_sku and deleted_sku == source_sku:
            return True
    return False


SHOPEE_MANUAL_EDIT_FIELDS = (
    "Cliente",
    "NomeFiscalShopee",
    "Nome Conferência",
    "Kit",
    "Nome do Kit",
    "Quantidade de Kits",
    "Quantidade por Kit",
    "SKU",
    # V17.78: Quantidade não entra na proteção automática Shopee. A quantidade
    # real da venda vem da API; alterações explícitas do ADMIN continuam soberanas
    # pelo admin_manual_overrides aplicado no final do save_storage.
    "Código de Barras",
    "Descrição",
    "Rua",
    "Módulo",
    "Prateleira",
    "Nível",
    "Posição",
    "Foto",
    "NF",
    "Nota Fiscal",
    "Chave de Acesso",
)


def _shopee_snapshot_editable(row: dict[str, Any]) -> dict[str, Any]:
    """Snapshot dos valores fornecidos automaticamente pela Shopee/Noromak.

    Ele permite distinguir, nas sincronizações seguintes, um valor que veio da API
    de um valor que o operador alterou manualmente na tela.
    """
    return {field: row.get(field, "") for field in SHOPEE_MANUAL_EDIT_FIELDS}


def _shopee_parse_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("ShopeeApiSnapshot")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _shopee_line_match_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """Chave estável para casar a linha já existente com o novo retorno da API."""
    uid = str(row.get("__uidLinha") or "").strip()
    if uid:
        return ("uid", uid, "")
    order = normalize(order_number(row))
    source_sku = normalize(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU"))
    return ("order_sku", order, source_sku)


def _shopee_merge_api_line_preserving_manual(old_row: dict[str, Any], api_row: dict[str, Any]) -> dict[str, Any]:
    """Atualiza dados automáticos sem sobrescrever edição manual do operador.

    Regra: se o valor atual for diferente do último snapshot automático, ele foi
    editado manualmente e prevalece. Status/logística/metadados da API continuam
    sendo atualizados normalmente.
    """
    previous_snapshot = _shopee_parse_snapshot(old_row)
    if not previous_snapshot:
        # Migração V17.25 -> V17.26: o usuário confirmou que ainda não editou os
        # pedidos Shopee, então o estado existente é uma linha-base segura.
        previous_snapshot = _shopee_snapshot_editable(old_row)

    merged = dict(old_row)
    # Primeiro recebe tudo que é novo/operacional da API.
    merged.update(api_row)

    for field in SHOPEE_MANUAL_EDIT_FIELDS:
        old_value = old_row.get(field, "")
        baseline_value = previous_snapshot.get(field, old_value)
        # Qualquer diferença em relação ao último valor automático significa edição
        # manual (inclusive apagar um valor). Nesse caso não sobrescrever.
        if old_value != baseline_value:
            merged[field] = old_value

    # O snapshot sempre guarda o que a API teria aplicado nesta sincronização,
    # independentemente de existir uma edição manual preservada.
    merged["ShopeeApiSnapshot"] = json_dumps(_shopee_snapshot_editable(api_row))
    merged["ShopeeProtecaoManual"] = "ATIVA"
    return merged


def _shopee_merge_pending_lines(current: list[dict[str, Any]], api_lines: list[dict[str, Any]], active_orders: set[str], detail_orders: set[str]) -> tuple[list[dict[str, Any]], int, int]:
    """Reconcilia somente linhas API_SHOPEE e preserva campos editados à mão."""
    current_api: dict[tuple[str, str, str], dict[str, Any]] = {}
    preserved_non_api: list[dict[str, Any]] = []
    removed = 0
    active_norm = {normalize(x) for x in active_orders}
    detail_norm = {normalize(x) for x in detail_orders}

    for row in current:
        if not isinstance(row, dict):
            continue
        if normalize(row.get("OrigemCadastro")) != "API_SHOPEE":
            preserved_non_api.append(row)
            continue
        oid_norm = normalize(order_number(row))
        if oid_norm not in active_norm:
            removed += 1
            continue
        current_api[_shopee_line_match_key(row)] = row

    merged_api: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    protected_count = 0
    for api_row in api_lines:
        key = _shopee_line_match_key(api_row)
        old = current_api.get(key)
        if old:
            before = {f: old.get(f, "") for f in SHOPEE_MANUAL_EDIT_FIELDS}
            merged = _shopee_merge_api_line_preserving_manual(old, api_row)
            after_api = _shopee_snapshot_editable(api_row)
            if any(before.get(f) != _shopee_parse_snapshot(old).get(f, before.get(f)) for f in SHOPEE_MANUAL_EDIT_FIELDS):
                protected_count += 1
            merged_api.append(merged)
        else:
            fresh = dict(api_row)
            fresh["ShopeeApiSnapshot"] = json_dumps(_shopee_snapshot_editable(api_row))
            fresh["ShopeeProtecaoManual"] = "ATIVA"
            merged_api.append(fresh)
        seen_keys.add(key)

    # Se o detalhe de um pedido ativo falhou nesta rodada, mantém as linhas antigas.
    for key, row in current_api.items():
        if key in seen_keys:
            continue
        oid_norm = normalize(order_number(row))
        if oid_norm in active_norm and oid_norm not in detail_norm:
            merged_api.append(row)

    return [*preserved_non_api, *merged_api], removed, protected_count


SHOPEE_ORDERS_SYNC_LOCK = threading.Lock()


def shopee_manual_sync(*, acordar_etiquetas: bool = True) -> dict[str, Any]:
    # V17.92: o worker automático e o botão de sincronizar não rodam juntos.
    with SHOPEE_ORDERS_SYNC_LOCK:
        return _shopee_manual_sync_sem_trava(acordar_etiquetas)


def _shopee_manual_sync_sem_trava(acordar_etiquetas: bool = True) -> dict[str, Any]:
    status = shopee_status_payload()
    if not status["connected"]:
        raise RuntimeError("Shopee ainda não está conectada.")
    sync_started = now_iso()
    try:
        shipments = shopee_current_shipments()
        packages_by_order: dict[str, list[str]] = {}
        for shipment in shipments:
            order_sn = str(shipment.get("order_sn") or "").strip()
            package = str(shipment.get("package_number") or "").strip()
            if order_sn:
                packages_by_order.setdefault(order_sn, [])
                if package and package not in packages_by_order[order_sn]:
                    packages_by_order[order_sn].append(package)

        order_sns = list(packages_by_order)
        details = shopee_order_details(order_sns) if order_sns else []
        products_raw, _, _ = get_storage("conf_produtos")
        products = _rows_from_storage_value(products_raw)
        api_lines: list[dict[str, Any]] = []
        detail_orders: set[str] = set()
        for order in details:
            order_sn = str(order.get("order_sn") or "").strip()
            if not order_sn:
                continue
            detail_orders.add(order_sn)
            api_lines.extend(_shopee_build_pending_lines(order, packages_by_order.get(order_sn, []), products))

        # V17.43: remove linhas artificiais de kit antes da reconciliação e respeita
        # exclusões manuais persistentes feitas no Admin/Conferência.
        api_lines = _shopee_consolidate_api_quantities_v1778(api_lines)
        api_lines = _shopee_filter_artificial_kit_lines(api_lines)
        manual_deletions = _shopee_load_manual_deletions()
        if manual_deletions:
            api_lines = [row for row in api_lines if not _shopee_is_manually_deleted(row, manual_deletions)]

        current_raw, _, _ = get_storage("conf_pedidos")
        current = _rows_from_storage_value(current_raw)
        active_orders = set(order_sns)
        # V17.26: reconcilia as linhas Shopee sem sobrescrever campos que o operador
        # alterou manualmente. Pedidos manuais/ML/Magalu continuam intocados.
        pending, removed, protected_count = _shopee_merge_pending_lines(
            current, api_lines, active_orders, detail_orders
        )
        saved, _, _ = save_storage(
            "conf_pedidos", current_raw, json_dumps(pending), False,
            actor="sistema", actor_role="sistema", source_screen="Admin / Integrações / Shopee",
            change_method="Sincronização API Shopee Live", client_ip="127.0.0.1",
            allow_completed_reimport=False,
        )
        final_rows = _rows_from_storage_value(saved)
        imported_orders = len({order_number(r) for r in final_rows if normalize(r.get("OrigemCadastro")) == "API_SHOPEE"})
        # V18.62: pedido que entrou agora na fila acorda o worker de etiquetas na
        # hora (antes esperava até 3 min). Sem pedido novo, segue a regra da V17.92.
        pedidos_antes = {order_number(r) for r in current if isinstance(r, dict)}
        pedidos_novos = {
            order_number(r) for r in final_rows
            if isinstance(r, dict) and normalize(r.get("OrigemCadastro")) == "API_SHOPEE"
        } - pedidos_antes
        # V17.27: nunca bloqueia a requisição esperando criação/READY/download de etiqueta.
        # O worker do servidor acorda imediatamente e continua o fluxo em background.
        # V17.92: a sincronização automática (a cada minuto) não acorda o worker
        # de etiquetas — ele segue no intervalo próprio, sem multiplicar chamadas.
        if acordar_etiquetas or pedidos_novos:
            SHOPEE_LABEL_WAKE_EVENT.set()
        shipping_docs = {
            "enfileirados": len(shipments),
            "baixados": 0,
            "vinculados": 0,
            "pendentes": len(shipments),
            "background": True,
        }
        with DB_LOCK:
            DB.execute("UPDATE shopee_integration SET last_sync_at=?,updated_at=?,last_error='' WHERE id=1", (now_iso(), now_iso()))
            DB.commit()
        shopee_log_event("orders_sync", f"shop_id={status.get('shop_id')}", "OK",
                         f"{len(order_sns)} pedido(s) para expedição; {len(api_lines)} linha(s); {removed} linha(s) API antiga(s) reconciliada(s); {protected_count} linha(s) com edição manual preservada.")
        # Resumo seguro para diagnosticar o que a Shopee realmente liberou.
        diag_counts = {"recipient_visivel": 0, "recipient_mascarado": 0, "recipient_ausente": 0, "buyer_visivel": 0}
        for order in details:
            recipient = order.get("recipient_address") if isinstance(order.get("recipient_address"), dict) else {}
            rn = str(recipient.get("name") or "").strip()
            bn = str(order.get("buyer_name") or "").strip()
            def _visible_name(v: str) -> bool:
                return bool(re.sub(r"[\s*•xX._-]+", "", v or ""))
            if not rn:
                diag_counts["recipient_ausente"] += 1
            elif _visible_name(rn):
                diag_counts["recipient_visivel"] += 1
            else:
                diag_counts["recipient_mascarado"] += 1
            if _visible_name(bn):
                diag_counts["buyer_visivel"] += 1
        shopee_log_event("recipient_diag", f"shop_id={status.get('shop_id')}", "OK", json_dumps(diag_counts))
        return {
            "ok": True,
            "imported": imported_orders,
            "orders_found": len(order_sns),
            "lines_imported": len(api_lines),
            "removed_api_lines": removed,
            "detail_missing": max(0, len(order_sns) - len(detail_orders)),
            "name_diagnostic": diag_counts,
            "shipping_documents": shipping_docs,
            "sync_started_at": sync_started,
            "message": f"Shopee sincronizada: {len(order_sns)} pedido(s), {len(api_lines)} item(ns). Etiquetas enviadas ao processamento automático do servidor: {len(shipments)}.",
        }
    except Exception as exc:
        with DB_LOCK:
            DB.execute("UPDATE shopee_integration SET last_error=?,updated_at=? WHERE id=1", (str(exc)[:1000], now_iso()))
            DB.commit()
        shopee_log_event("orders_sync", f"shop_id={status.get('shop_id')}", "ERRO", str(exc))
        raise


SHOPEE_ORDERS_WORKER_INTERVAL = max(30, min(900, int(os.getenv("SHOPEE_ORDERS_WORKER_INTERVAL", "60"))))


def shopee_orders_worker_loop(stop_event: threading.Event) -> None:
    """V17.92 — importa os pedidos da Shopee sozinho, como o Mercado Livre.

    Até aqui a importação só rodava pelo botão de sincronizar da tela de
    Integrações. Mesma função do botão (shopee_manual_sync), a cada minuto.
    """
    stop_event.wait(20)
    while not stop_event.is_set():
        try:
            if shopee_status_payload().get("connected"):
                shopee_manual_sync(acordar_etiquetas=False)
        except Exception as exc:
            shopee_log_event("orders_worker", "background", "ERRO", str(exc)[:800])
        stop_event.wait(SHOPEE_ORDERS_WORKER_INTERVAL)


def _shopee_label_proxima_espera(result: dict[str, Any], rapidos: int) -> tuple[int, int]:
    """V18.62 — quanto o worker de etiquetas espera até o próximo ciclo.

    Com pedido no meio do fluxo (rastreio ou documento ainda saindo) volta em
    SHOPEE_LABEL_FOLLOWUP_INTERVAL. Sem progresso, no máximo
    SHOPEE_LABEL_FOLLOWUP_MAX vezes seguidas; etiqueta vinculada ou envio
    organizado zera a contagem. Retorna (segundos, ciclos rápidos seguidos).
    """
    if int(result.get("vinculados", 0) or 0) or int(result.get("ship_order_ok", 0) or 0):
        rapidos = 0
    if int(result.get("aguardando", 0) or 0) and rapidos < SHOPEE_LABEL_FOLLOWUP_MAX:
        return SHOPEE_LABEL_FOLLOWUP_INTERVAL, rapidos + 1
    return SHOPEE_LABEL_WORKER_INTERVAL, 0


def shopee_label_worker_loop(stop_event: threading.Event) -> None:
    """Continua geração/consulta/download das etiquetas Shopee em background.

    A requisição de sincronização apenas acorda este worker. Assim, mesmo que a
    Shopee demore para preparar documentos, o Nginx não recebe uma requisição
    longa e não retorna HTTP 504.
    """
    # Pequeno atraso inicial para deixar HTTP/DB totalmente prontos no boot.
    stop_event.wait(5)
    # V17.99: etiquetas da API gravadas sem a imagem (saíam em branco) são
    # regravadas a partir do arquivo original. Só age em quem está quebrado.
    try:
        with SHOPEE_LABEL_WORKER_LOCK:
            shopee_reparar_etiquetas_sem_grafico()
    except Exception as exc:
        try:
            shopee_log_event("etiqueta_reparada", "boot", "ERRO", str(exc)[:500])
        except Exception:
            pass
    espera, rapidos = SHOPEE_LABEL_WORKER_INTERVAL, 0
    while not stop_event.is_set():
        # Acorda imediatamente após sincronização manual / pedido novo, ou no intervalo.
        SHOPEE_LABEL_WAKE_EVENT.wait(espera)
        espera = SHOPEE_LABEL_WORKER_INTERVAL
        SHOPEE_LABEL_WAKE_EVENT.clear()
        if stop_event.is_set():
            break
        if not SHOPEE_LABEL_WORKER_LOCK.acquire(blocking=False):
            continue
        try:
            st = shopee_status_payload()
            if not st.get("connected"):
                continue
            shipments = shopee_current_shipments()
            # V17.91: organizado, o pedido sai de get_shipment_list — justamente
            # quando a etiqueta fica disponível. Os pendentes no Noromak sem
            # etiqueta e fora dessa lista seguem direto para rastreio/etiqueta.
            na_lista = {x.get("order_sn") for x in shipments}
            organizados = [x for x in shopee_pedidos_sem_etiqueta() if x["order_sn"] not in na_lista]
            if not shipments and not organizados:
                continue
            result = shopee_sync_shipping_documents(
                shipments + organizados, ja_organizados={x["order_sn"] for x in organizados})
            espera, rapidos = _shopee_label_proxima_espera(result, rapidos)
            shopee_log_event(
                "shipping_worker",
                f"shop_id={st.get('shop_id')}",
                "OK",
                "consultados={consultados}; shipping_param={shipping_parameter_ok}; ship_order={ship_order_ok}; "
                "tracking={tracking_ok}; prontos={prontos}; baixados={baixados}; vinculados={vinculados}; "
                "pendentes={pendentes}; erros={erros}; aguardando={aguardando}".format(**{
                    k: int(result.get(k, 0) or 0)
                    for k in ("consultados","shipping_parameter_ok","ship_order_ok","tracking_ok","prontos","baixados","vinculados","pendentes","erros","aguardando")
                }) + f"; proximo_ciclo={espera}s",
            )
        except Exception as exc:
            shopee_log_event("shipping_worker", "background", "ERRO", str(exc)[:800])
        finally:
            SHOPEE_LABEL_WORKER_LOCK.release()


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
    has_access = bool(row and row["access_token"] and row["seller_id"])
    has_refresh = bool(row and str(row["refresh_token"] or "").strip())
    scope_text = str(row["scope"] if row else "")
    scope_set = {x.strip().lower() for x in re.split(r"[\s,]+", scope_text) if x.strip()}
    offline_access = "offline_access" in scope_set
    expires_at = int(row["expires_at_epoch"] or 0) if row else 0
    now_epoch = int(time.time())
    token_expired = bool(has_access and expires_at and expires_at <= now_epoch)
    # Um access_token vencido sem refresh_token NÃO é conexão ativa. Antes isso
    # aparecia como "conectado" e mascarava a falha de Orders/etiquetas após 6h.
    connected = bool(has_access and (not token_expired or has_refresh))
    needs_reconnect = bool(has_access and not has_refresh)
    return {
        "configured": ml_credentials_configured(),
        "connected": connected,
        "needs_reconnect": needs_reconnect,
        "seller_id": str(row["seller_id"] if row else ""),
        "nickname": str(row["nickname"] if row else ""),
        "scope": scope_text,
        "offline_access_granted": offline_access,
        "refresh_token_available": has_refresh,
        "oauth_scope_requested": ML_OAUTH_SCOPE,
        "expires_at_epoch": expires_at,
        "token_expired": token_expired,
        "token_seconds_remaining": max(0, expires_at - now_epoch) if expires_at else 0,
        "connected_at": str(row["connected_at"] if row else ""),
        "updated_at": str(row["updated_at"] if row else ""),
        "last_sync_at": str(row["last_sync_at"] if row else ""),
        "last_error": str(row["last_error"] if row else ""),
        "redirect_uri": ML_REDIRECT_URI,
        "notification_url": ML_NOTIFICATION_URL,
        "client_id_suffix": (ML_CLIENT_ID[-6:] if ML_CLIENT_ID else ""),
        "worker": ml_worker_status_payload(),
    }



ML_ITEM_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
ML_ITEM_META_CACHE_LOCK = threading.RLock()
ML_ITEM_META_CACHE_TTL = 20 * 60
ML_ITEM_META_CACHE_MAX = 20000


def _cache_podar(cache: dict[str, tuple[float, Any]], ttl: float, limite: int) -> None:
    """V18.56: os caches em memória tinham TTL mas nenhuma limpeza — entradas
    vencidas ficavam para sempre. Remove as vencidas e, se ainda passar do limite,
    as mais antigas. Chamar com a trava do cache (quando houver)."""
    if len(cache) < limite:
        return
    agora = time.time()
    for chave in [k for k, (t, _) in cache.items() if agora - t >= ttl]:
        cache.pop(chave, None)
    if len(cache) >= limite:
        for chave, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[: len(cache) - limite // 2]:
            cache.pop(chave, None)


def _ml_item_meta_from_body(body: dict[str, Any]) -> dict[str, Any]:
    sku = str(body.get("seller_custom_field") or "").strip()
    if not sku:
        attrs = body.get("attributes") if isinstance(body.get("attributes"), list) else []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            if str(attr.get("id") or "").upper() in ("SELLER_SKU", "SKU"):
                sku = str(attr.get("value_name") or attr.get("value_id") or "").strip()
                if sku:
                    break
    return {
        "id": str(body.get("id") or "").replace("MLB", ""),
        "title": str(body.get("title") or "").strip(),
        "sku": sku,
        "available_quantity": max(0, int(body.get("available_quantity") or 0)),
        "status": str(body.get("status") or "").strip().lower(),
        "price": float(body.get("price") or 0),
        "permalink": str(body.get("permalink") or "").strip(),
        "stock_known": True,
    }


def ml_items_meta(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Lê metadados/SKU de anúncios do Mercado Livre.

    V17.68: tenta primeiro o endpoint multi-get autenticado. Algumas aplicações
    recebem HTTP 403 / PolicyAgent nessa rota mesmo com Bearer válido. Nesse caso,
    cai automaticamente para GET /items/{MLB} individual, que é o mesmo caminho
    autenticado já usado pela análise de promoções do sistema.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for value in ids:
        raw = re.sub(r"\D+", "", str(value or ""))
        if not raw or raw in seen:
            continue
        seen.add(raw)
        normalized.append(raw)
        if len(normalized) >= 100:
            break

    now = time.time()
    result: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    with ML_ITEM_META_CACHE_LOCK:
        for mlb in normalized:
            cached = ML_ITEM_META_CACHE.get(mlb)
            if cached and now - cached[0] < ML_ITEM_META_CACHE_TTL:
                result[mlb] = dict(cached[1])
            else:
                missing.append(mlb)

    def unknown_meta(mlb: str, error: str = "") -> dict[str, Any]:
        return {
            "id": mlb,
            "title": "",
            "sku": "",
            "available_quantity": None,
            "status": "unknown",
            "price": 0,
            "permalink": "",
            "stock_known": False,
            "error": str(error or "")[:500],
        }

    for start_idx in range(0, len(missing), 20):
        batch = missing[start_idx:start_idx + 20]
        if not batch:
            continue

        fetched: dict[str, dict[str, Any]] = {}
        batch_error = ""
        try:
            ids_param = ",".join("MLB" + item for item in batch)
            payload = ml_api_get("/items?" + urlencode({"ids": ids_param}))
            rows = payload if isinstance(payload, list) else []
            for wrapper in rows:
                if not isinstance(wrapper, dict):
                    continue
                body = wrapper.get("body") if isinstance(wrapper.get("body"), dict) else {}
                meta = _ml_item_meta_from_body(body)
                mlb = str(meta.get("id") or "")
                if mlb:
                    fetched[mlb] = meta
        except Exception as exc:
            batch_error = str(exc)
            policy_blocked = (
                "HTTP 403" in batch_error
                or "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in batch_error
                or "PolicyAgent" in batch_error
            )
            if not policy_blocked:
                raise

            # Fallback autenticado por item. Esse endpoint já é usado com sucesso
            # em ml_promotions_analysis_payload.load_item().
            for mlb in batch:
                try:
                    body = ml_api_get(f"/items/{quote('MLB' + mlb)}")
                    if isinstance(body, dict):
                        meta = _ml_item_meta_from_body(body)
                        meta_id = str(meta.get("id") or mlb)
                        fetched[meta_id] = meta
                except Exception as item_exc:
                    fetched[mlb] = unknown_meta(mlb, str(item_exc))

        with ML_ITEM_META_CACHE_LOCK:
            for mlb in batch:
                meta = fetched.get(mlb)
                if meta and str(meta.get("sku") or "").strip():
                    meta = dict(meta)
                    meta["stock_known"] = True
                elif meta and meta.get("stock_known") is not False:
                    meta = dict(meta)
                    meta["stock_known"] = True
                elif not meta:
                    meta = unknown_meta(mlb, batch_error)
                ML_ITEM_META_CACHE[mlb] = (time.time(), dict(meta))
                result[mlb] = dict(meta)
            _cache_podar(ML_ITEM_META_CACHE, ML_ITEM_META_CACHE_TTL, ML_ITEM_META_CACHE_MAX)

    return result


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


def ml_store_token(
    payload: dict[str, Any],
    *,
    nickname: str = "",
    preserve_refresh_if_missing: bool = True,
    refresh_expected: bool = False,
) -> None:
    seller_id = str(payload.get("user_id") or "")
    access_token = str(payload.get("access_token") or "")
    refresh_received = str(payload.get("refresh_token") or "").strip()
    token_type = str(payload.get("token_type") or "Bearer")
    scope = str(payload.get("scope") or "")
    expires_in = int(payload.get("expires_in") or 0)
    expires_at = int(time.time()) + max(0, expires_in)
    if not access_token or not seller_id:
        raise RuntimeError("Resposta de token do Mercado Livre incompleta.")

    previous = ml_integration_row()
    refresh_token = refresh_received
    if previous and not refresh_token and preserve_refresh_if_missing:
        refresh_token = str(previous["refresh_token"] or "").strip()

    scope_set = {x.strip().lower() for x in re.split(r"[\s,]+", scope) if x.strip()}
    token_error = ""
    if refresh_expected and not refresh_received:
        if "offline_access" not in scope_set:
            token_error = (
                "Mercado Livre retornou access_token sem refresh_token/offline_access. "
                "O token funcionará somente até expirar (normalmente 6 horas)."
            )
        else:
            token_error = (
                "Mercado Livre retornou offline_access sem refresh_token; a renovação automática não poderá funcionar."
            )

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
                 refresh_token=excluded.refresh_token,
                 token_type=excluded.token_type,
                 scope=excluded.scope,
                 expires_at_epoch=excluded.expires_at_epoch,
                 connected_at=CASE WHEN ml_integration.connected_at='' THEN excluded.connected_at ELSE ml_integration.connected_at END,
                 updated_at=excluded.updated_at,
                 last_error=excluded.last_error""",
            (
                seller_id, nickname, access_token, refresh_token, token_type, scope,
                expires_at, connected_at, now_iso(),
                str(previous["last_sync_at"] or "") if previous else "",
                token_error,
            ),
        )
        DB.commit()

    try:
        ml_log_event(
            "oauth_token", "/oauth/token", seller_id, ML_CLIENT_ID,
            "REFRESH_TOKEN_OK" if refresh_received else "REFRESH_TOKEN_AUSENTE",
            json_dumps({
                "scope": scope,
                "offline_access": "offline_access" in scope_set,
                "refresh_token_received": bool(refresh_received),
                "expires_in": expires_in,
                "payload_keys": sorted(str(k) for k in payload.keys()),
            }),
        )
    except Exception:
        pass


# V18.56: o refresh_token do ML também é de USO ÚNICO. Seis workers + os handlers
# HTTP pedem o token; quando ele entrava nos últimos 300 s, dois renovavam juntos com
# a mesma chave e um levava invalid_grant (ou gravava um par já ultrapassado).
# Mesma solução da Shopee (V18.01): um renova, os outros esperam e reaproveitam.
ML_TOKEN_LOCK = threading.Lock()


def ml_access_token(*, force_refresh: bool = False) -> tuple[str, str]:
    row = ml_integration_row()
    if not row or not row["access_token"]:
        raise RuntimeError("Mercado Livre ainda não está conectado.")
    if not ml_credentials_configured():
        raise RuntimeError("ML_CLIENT_ID / ML_CLIENT_SECRET não estão configurados no servidor.")
    if not force_refresh and int(row["expires_at_epoch"] or 0) > int(time.time()) + 300:
        return str(row["access_token"]), str(row["seller_id"])
    token_antes = str(row["access_token"] or "")
    with ML_TOKEN_LOCK:
        row = ml_integration_row()
        if not row or not row["access_token"]:
            raise RuntimeError("Mercado Livre ainda não está conectado.")
        expira = int(row["expires_at_epoch"] or 0)
        # Outro renovou enquanto esperávamos: reaproveita (vale também após um 401).
        if str(row["access_token"] or "") != token_antes and expira > int(time.time()) + 60:
            return str(row["access_token"]), str(row["seller_id"])
        if not force_refresh and expira > int(time.time()) + 300:
            return str(row["access_token"]), str(row["seller_id"])
        return _ml_refresh_token_locked(row)


def _ml_refresh_token_locked(row: sqlite3.Row) -> tuple[str, str]:
    refresh_token = str(row["refresh_token"] or "")
    if not refresh_token:
        if int(row["expires_at_epoch"] or 0) <= int(time.time()):
            raise RuntimeError("TOKEN_ML_EXPIRADO_SEM_REFRESH: o access_token venceu e o Mercado Livre não forneceu refresh_token. Orders, etiquetas e custos ficam indisponíveis até uma nova autorização que retorne refresh_token.")
        raise RuntimeError("Refresh token do Mercado Livre não está disponível; o access_token atual ainda está temporariamente válido.")
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
    # V18.01: PRESERVE o refresh_token quando o ML responder sem um novo.
    # Com preserve=False, uma resposta "access_token sem refresh_token" (cenário
    # que o próprio ml_store_token já prevê) apagava a chave de renovação e a
    # integração morria até reconectar à mão — derrubando o fim de semana inteiro.
    # Manter a chave anterior é a única chance de continuar renovando.
    ml_store_token(payload, nickname=str(row["nickname"] or ""), preserve_refresh_if_missing=True, refresh_expected=True)
    fresh = ml_integration_row()
    return str(fresh["access_token"]), str(fresh["seller_id"])



def _ml_worker_state_update(**values: Any) -> None:
    with ML_WORKER_STATE_LOCK:
        ML_WORKER_STATE.update(values)


def ml_worker_status_payload() -> dict[str, Any]:
    with ML_WORKER_STATE_LOCK:
        state = dict(ML_WORKER_STATE)
    row = ml_integration_row()
    expires_at = int(row["expires_at_epoch"] or 0) if row else 0
    now_ts = int(time.time())
    state.update({
        "server_worker": True,
        "browser_required": False,
        "server_time": now_iso(),
        "token_expires_at_epoch": expires_at,
        "token_seconds_remaining": max(0, expires_at - now_ts) if expires_at else 0,
        "has_refresh_token": bool(row and str(row["refresh_token"] or "").strip()),
        "ml_connected": bool(row and str(row["access_token"] or "").strip()),
        "token_expired": bool(expires_at and expires_at <= now_ts),
        "new_order_interval_seconds": ML_NEW_ORDER_WATCH_INTERVAL,
        "token_keepalive_interval_seconds": ML_TOKEN_KEEPALIVE_INTERVAL,
        "safety_catchup_interval_seconds": ML_SAFETY_CATCHUP_INTERVAL,
        "safety_catchup_hours": ML_SAFETY_CATCHUP_HOURS,
    })
    return state


def ml_token_keepalive_once() -> dict[str, Any]:
    """Mantém OAuth renovado no servidor, mesmo sem qualquer navegador aberto."""
    checked_at = now_iso()
    row = ml_integration_row()
    if not row or not str(row["access_token"] or "").strip():
        result = {"ok": True, "connected": False, "refreshed": False, "reason": "not_connected"}
        _ml_worker_state_update(last_token_check_at=checked_at, last_error="")
        return result
    expires_at = int(row["expires_at_epoch"] or 0)
    remaining = expires_at - int(time.time())
    try:
        # Renovação proativa evita que o watcher encontre um token vencido no meio da noite.
        force = remaining <= ML_TOKEN_REFRESH_AHEAD_SECONDS
        ml_access_token(force_refresh=force)
        result = {"ok": True, "connected": True, "refreshed": bool(force), "seconds_remaining_before": remaining}
        updates = {"last_token_check_at": checked_at, "last_error": ""}
        if force:
            updates["last_token_refresh_at"] = now_iso()
            ml_log_event("token_keeper", "/oauth/token", str(row["seller_id"] or ""), ML_CLIENT_ID,
                         "TOKEN RENOVADO NO SERVIDOR", json_dumps(result))
        _ml_worker_state_update(**updates)
        return result
    except Exception as exc:
        message = str(exc)[:1200]
        _ml_worker_state_update(last_token_check_at=checked_at, last_error=message)
        try:
            ml_log_event("token_keeper", "/oauth/token", str(row["seller_id"] or ""), ML_CLIENT_ID, "ERRO", message)
        except Exception:
            pass
        return {"ok": False, "connected": True, "refreshed": False, "error": message}


def ml_token_keepalive_loop(stop_event: threading.Event) -> None:
    # Verifica poucos segundos após o boot e depois continuamente na VPS.
    if stop_event.wait(8):
        return
    while not stop_event.is_set():
        ml_token_keepalive_once()
        if stop_event.wait(ML_TOKEN_KEEPALIVE_INTERVAL):
            break


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


def ml_http_bytes(url: str, *, token: str = "", headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    final_headers = {"Accept": "*/*", "User-Agent": f"NoromakExpedicao/{APP_VERSION}"}
    if token:
        final_headers["Authorization"] = f"Bearer {token}"
    final_headers.update(headers or {})
    req = urllib.request.Request(url, headers=final_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=ML_HTTP_TIMEOUT) as resp:
            return resp.read(), str(resp.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mercado Livre HTTP {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de conexão com Mercado Livre: {exc.reason}") from exc


def ml_api_get_bytes(path_or_url: str, *, headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    token, _ = ml_access_token()
    url = path_or_url if path_or_url.startswith("http") else f"{ML_API_BASE}{path_or_url}"
    try:
        return ml_http_bytes(url, token=token, headers=headers)
    except RuntimeError as exc:
        if "HTTP 401" not in str(exc):
            raise
    token, _ = ml_access_token(force_refresh=True)
    return ml_http_bytes(url, token=token, headers=headers)



def _promo_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def _promo_precifier_index() -> dict[str, dict[str, Any]]:
    raw, _, _ = get_storage("precificador_online_v1")
    try:
        products = json.loads(raw or "[]")
    except Exception:
        products = []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(products, dict):
        products = products.get("products") or products.get("produtos") or []
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        sku = str(product.get("sku") or "").strip().upper()
        if sku:
            out[sku] = product
    return out


def _promo_item_sku(item: dict[str, Any]) -> str:
    sku = str(item.get("seller_custom_field") or "").strip()
    if sku:
        return sku
    attrs = item.get("attributes") or []
    for attr in attrs if isinstance(attrs, list) else []:
        if not isinstance(attr, dict):
            continue
        aid = str(attr.get("id") or "").upper()
        if aid in ("SELLER_SKU", "SKU"):
            value = str(attr.get("value_name") or attr.get("value_id") or "").strip()
            if value:
                return value
    return ""


def ml_promotions_analysis_payload(max_items: int = 250, tax_percent: float = 12.0, simulated_discount: float = 10.0) -> dict[str, Any]:
    """Modo somente leitura: cruza Seller Promotions com Precificador/Apollo.

    V16.28: se a rota geral /seller-promotions/users/{seller} for bloqueada por
    PolicyAgent, usa fallback oficial por item: lista anúncios ativos do vendedor e
    consulta /seller-promotions/items/{ITEM_ID}?app_version=v2.
    """
    _, seller_id = ml_access_token()
    precifier = _promo_precifier_index()
    item_cache: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    max_items = max(1, min(1000, int(max_items or 250)))
    source_mode = "campaigns"
    campaigns: list[dict[str, Any]] = []

    def load_item(item_id: str) -> dict[str, Any]:
        item = item_cache.get(item_id)
        if item is not None:
            return item
        try:
            fetched = ml_api_get(f"/items/{quote(item_id)}")
            item = fetched if isinstance(fetched, dict) else {}
        except Exception as exc:
            item = {"id": item_id, "title": "", "_error": str(exc)}
        item_cache[item_id] = item
        return item

    def append_offer(offer: dict[str, Any], campaign: dict[str, Any]) -> None:
        if len(rows) >= max_items or not isinstance(offer, dict):
            return
        item_id = str(offer.get("id") or offer.get("item_id") or "").strip()
        if not item_id:
            return
        item = load_item(item_id)
        sku = _promo_item_sku(item)
        p = precifier.get(sku.upper()) if sku else None
        cost = None
        curve = ""
        description = str(item.get("title") or "")
        if p:
            cost = _promo_float(p.get("custo"), 0)
            curve = str(p.get("curva") or "").strip().upper()[:1]
            description = str(p.get("produto") or description)
        if sku and (cost is None or cost <= 0):
            try:
                ap = crm_cost_lookup(sku, "promocoes-ml")
                cost = _promo_float(ap.get("custo_medio"), 0)
                curve = str(ap.get("curva_abc") or curve).strip().upper()[:1]
                description = str(ap.get("descricao") or description)
            except Exception:
                pass
        margin_target = {"A": 20.0, "B": 20.0, "C": 17.0, "D": 15.0}.get(curve, 20.0)
        mlcfg = ((p or {}).get("marketplaces") or {}).get("ml") or {}
        commission = _promo_float(mlcfg.get("comissao"), 0.12)
        if commission > 1:
            commission /= 100.0
        fixed_fee = _promo_float(mlcfg.get("taxaFixa"), 0)
        freight = _promo_float(mlcfg.get("frete"), 0)
        original = _promo_float(offer.get("original_price") or item.get("price"), 0)
        buyer_price = _promo_float(offer.get("total_price_for_boosted_offer") or offer.get("price"), 0)
        if buyer_price <= 0:
            buyer_price = _promo_float(offer.get("suggested_discounted_price") or offer.get("max_discounted_price"), 0)
        seller_pct = offer.get("seller_percentage")
        meli_pct = offer.get("meli_percentage")
        if seller_pct is None:
            benefits = campaign.get("benefits") or {}
            seller_pct = benefits.get("seller_percent") if isinstance(benefits, dict) else None
            meli_pct = benefits.get("meli_percent") if isinstance(benefits, dict) else meli_pct
        seller_pct_f = _promo_float(seller_pct, 0)
        meli_pct_f = _promo_float(meli_pct, 0)
        economic_price = original * (1 - seller_pct_f / 100.0) if original > 0 and seller_pct is not None else buyer_price
        if economic_price <= 0:
            economic_price = buyer_price or original
        tax = economic_price * max(0.0, tax_percent) / 100.0
        fee = economic_price * commission + fixed_fee
        profit = None if not cost or economic_price <= 0 else economic_price - cost - fee - freight - tax
        margin = None if profit is None or economic_price <= 0 else profit / economic_price * 100.0
        if margin is None:
            decision = "SEM_CUSTO"
        elif margin < 0:
            decision = "PREJUIZO"
        elif margin + 1e-9 >= margin_target:
            decision = "RECOMENDADA"
        else:
            decision = "ATENCAO"
        denom = 1 - commission - max(0.0, tax_percent) / 100.0 - margin_target / 100.0
        min_safe = (cost + fixed_fee + freight) / denom if cost and denom > 0 else None
        max_discount = max(0.0, (1 - min_safe / original) * 100.0) if min_safe is not None and original > 0 else None
        status = str(offer.get("status") or campaign.get("status") or "").lower()
        promotion_id = str(offer.get("promotion_id") or campaign.get("id") or "").strip()
        promotion_type = str(offer.get("type") or offer.get("promotion_type") or campaign.get("type") or "").strip()
        campaign_name = str(offer.get("name") or campaign.get("name") or promotion_id or promotion_type)
        rows.append({
            "campaign_id": promotion_id, "campaign": campaign_name,
            "promotion_type": promotion_type, "campaign_status": str(campaign.get("status") or ""),
            "item_status": status, "item_id": item_id, "sku": sku, "product": description,
            "original_price": round(original, 2), "buyer_price": round(buyer_price, 2),
            "economic_price": round(economic_price, 2), "seller_percentage": seller_pct_f,
            "meli_percentage": meli_pct_f, "boosted_offer": bool(offer.get("boosted_offer")),
            "boost_amount": round(_promo_float(offer.get("discount_meli_boost_amount"), 0), 2),
            "cost": round(cost, 2) if cost is not None else None, "curve": curve,
            "commission_percent": round(commission * 100, 2), "fixed_fee": round(fixed_fee, 2),
            "freight": round(freight, 2), "tax_percent": round(tax_percent, 2), "tax": round(tax, 2),
            "estimated_fee": round(fee, 2), "profit": round(profit, 2) if profit is not None else None,
            "margin": round(margin, 2) if margin is not None else None, "target_margin": margin_target,
            "min_safe_price": round(min_safe, 2) if min_safe is not None else None,
            "max_safe_discount": round(max_discount, 2) if max_discount is not None else None,
            "decision": decision, "start_date": str(offer.get("start_date") or campaign.get("start_date") or ""),
            "finish_date": str(offer.get("end_date") or campaign.get("finish_date") or ""),
            "candidate_price_min": offer.get("min_discounted_price"), "candidate_price_max": offer.get("max_discounted_price"),
            "suggested_price": offer.get("suggested_discounted_price"),
        })

    # Caminho principal: campanhas do vendedor.
    try:
        campaigns_payload = ml_api_get(f"/seller-promotions/users/{quote(str(seller_id))}?app_version=v2")
        campaigns = campaigns_payload.get("results") if isinstance(campaigns_payload, dict) else []
        campaigns = campaigns if isinstance(campaigns, list) else []
    except Exception as exc:
        msg = str(exc)
        if "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" not in msg and "HTTP 403" not in msg:
            raise
        source_mode = "items_fallback"
        errors.append("Consulta geral de campanhas bloqueada pelo PolicyAgent; usando consulta individual por anúncio.")

    if source_mode == "campaigns":
        for campaign in campaigns:
            if len(rows) >= max_items or not isinstance(campaign, dict):
                break
            promotion_id = str(campaign.get("id") or "").strip()
            promotion_type = str(campaign.get("type") or "").strip()
            if not promotion_id or not promotion_type:
                continue
            search_after = ""
            for _page in range(30):
                if len(rows) >= max_items:
                    break
                params = {"promotion_type": promotion_type, "app_version": "v2", "limit": "50"}
                if search_after:
                    params["search_after"] = search_after
                try:
                    page = ml_api_get(f"/seller-promotions/promotions/{quote(promotion_id)}/items?{urlencode(params)}")
                except Exception as exc:
                    errors.append(f"{promotion_id}: {str(exc)[:180]}")
                    break
                results = page.get("results") if isinstance(page, dict) else []
                results = results if isinstance(results, list) else []
                for offer in results:
                    append_offer(offer, campaign)
                if not isinstance(page, dict):
                    break
                search_after = str(page.get("search_after") or page.get("searchAfter") or "").strip()
                if not search_after or not results:
                    break
    else:
        # Fallback V16.28: anúncios ativos -> promoções associadas a cada item.
        # A API de busca do vendedor usa offset/limit. Mantemos limite pequeno para teste.
        item_ids: list[str] = []
        offset = 0
        wanted = min(max_items, 300)
        for _page in range(10):
            if len(item_ids) >= wanted:
                break
            q = urlencode({"status": "active", "limit": "50", "offset": str(offset)})
            try:
                search = ml_api_get(f"/users/{quote(str(seller_id))}/items/search?{q}")
            except Exception as exc:
                msg = str(exc)
                if "HTTP 403" in msg or "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in msg:
                    source_mode = "local_simulation"
                    errors.append("A API privada do Mercado Livre está bloqueada pelo PolicyAgent. Exibindo simulação local do Precificador.")
                    break
                raise RuntimeError(f"Fallback não conseguiu listar os anúncios ativos: {exc}") from exc
            ids = search.get("results") if isinstance(search, dict) else []
            ids = ids if isinstance(ids, list) else []
            item_ids.extend(str(x).strip() for x in ids if str(x).strip())
            if len(ids) < 50:
                break
            offset += 50
        item_ids = item_ids[:wanted]
        blocked_item_promos = 0
        for item_id in item_ids:
            if len(rows) >= max_items:
                break
            try:
                promos = ml_api_get(f"/seller-promotions/items/{quote(item_id)}?app_version=v2")
            except Exception as exc:
                msg = str(exc)
                if "HTTP 403" in msg or "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in msg:
                    blocked_item_promos += 1
                    if blocked_item_promos <= 3:
                        errors.append(f"{item_id}: Seller Promotions bloqueado pelo PolicyAgent")
                    continue
                errors.append(f"{item_id}: {msg[:160]}")
                continue
            if isinstance(promos, dict):
                promos = promos.get("results") or promos.get("promotions") or []
            promos = promos if isinstance(promos, list) else []
            for offer in promos:
                if not isinstance(offer, dict):
                    continue
                offer = dict(offer)
                offer.setdefault("id", item_id)
                pseudo_campaign = {
                    "id": offer.get("id") if str(offer.get("id") or "").startswith(("P-", "C-", "LGH-")) else "",
                    "type": offer.get("type") or "",
                    "status": offer.get("status") or "",
                    "name": offer.get("name") or offer.get("type") or "Promoção do item",
                    "start_date": offer.get("start_date") or "",
                    "finish_date": offer.get("end_date") or "",
                }
                # Em /seller-promotions/items/{ITEM_ID}, o campo id pode ser o id da promoção.
                if pseudo_campaign["id"]:
                    offer["promotion_id"] = pseudo_campaign["id"]
                offer["id"] = item_id
                append_offer(offer, pseudo_campaign)
        if blocked_item_promos and not rows:
            errors.insert(0, f"O aplicativo também foi bloqueado em {blocked_item_promos} consulta(s) individuais de promoção. A autorização do app Mercado Livre precisa liberar Seller Promotions.")
            source_mode = "local_simulation"

    # V16.29: fallback funcional para teste quando o app ML não tem permissão
    # para Seller Promotions/listagem privada. Usa somente dados locais do Precificador.
    if source_mode == "local_simulation" and not rows:
        disc = max(0.0, min(90.0, _promo_float(simulated_discount, 10.0)))
        for sku, p in list(precifier.items())[:max_items]:
            if not isinstance(p, dict):
                continue
            cost = _promo_float(p.get("custo"), 0)
            curve = str(p.get("curva") or "").strip().upper()[:1]
            target = {"A": 20.0, "B": 20.0, "C": 17.0, "D": 15.0}.get(curve, 20.0)
            mlcfg = (p.get("marketplaces") or {}).get("ml") or {}
            commission = _promo_float(mlcfg.get("comissao"), 0.12)
            if commission > 1:
                commission /= 100.0
            fixed_fee = _promo_float(mlcfg.get("taxaFixa"), 0)
            freight = _promo_float(mlcfg.get("frete"), 0)
            denom = 1 - commission - max(0.0, tax_percent)/100.0 - target/100.0
            reference = (cost + fixed_fee + freight) / denom if cost > 0 and denom > 0 else 0.0
            buyer = reference * (1 - disc/100.0) if reference > 0 else 0.0
            tax = buyer * max(0.0, tax_percent)/100.0
            fee = buyer * commission + fixed_fee
            profit = buyer - cost - fee - freight - tax if cost > 0 and buyer > 0 else None
            margin = profit / buyer * 100.0 if profit is not None and buyer > 0 else None
            if margin is None:
                decision = "SEM_CUSTO"
            elif margin < 0:
                decision = "PREJUIZO"
            elif margin + 1e-9 >= target:
                decision = "RECOMENDADA"
            else:
                decision = "ATENCAO"
            rows.append({
                "campaign_id":"SIMULACAO", "campaign":f"Simulação {disc:g}% OFF", "promotion_type":"SIMULACAO_LOCAL",
                "campaign_status":"simulation", "item_status":"candidate", "item_id":"", "sku":sku,
                "product":str(p.get("produto") or ""), "original_price":round(reference,2), "buyer_price":round(buyer,2),
                "economic_price":round(buyer,2), "seller_percentage":disc, "meli_percentage":0.0,
                "boosted_offer":False, "boost_amount":0.0, "cost":round(cost,2) if cost else None, "curve":curve,
                "commission_percent":round(commission*100,2), "fixed_fee":round(fixed_fee,2), "freight":round(freight,2),
                "tax_percent":round(tax_percent,2), "tax":round(tax,2), "estimated_fee":round(fee,2),
                "profit":round(profit,2) if profit is not None else None, "margin":round(margin,2) if margin is not None else None,
                "target_margin":target, "min_safe_price":round(reference,2) if reference else None,
                "max_safe_discount":0.0 if reference else None, "decision":decision, "start_date":"", "finish_date":"",
                "candidate_price_min":None, "candidate_price_max":None, "suggested_price":round(buyer,2) if buyer else None,
                "simulation":True
            })
        errors.append(f"Simulação local ativa: {disc:g}% de desconto sobre o preço de referência calculado pelo Precificador.")

    summary = {
        "total": len(rows), "recommended": sum(r["decision"] == "RECOMENDADA" for r in rows),
        "attention": sum(r["decision"] == "ATENCAO" for r in rows),
        "loss": sum(r["decision"] == "PREJUIZO" for r in rows),
        "missing_cost": sum(r["decision"] == "SEM_CUSTO" for r in rows),
        "candidate": sum(r["item_status"] == "candidate" for r in rows),
        "active": sum(r["item_status"] in ("started", "active") for r in rows),
    }
    return {"ok": True, "mode": "read_only", "source_mode": source_mode, "seller_id": seller_id,
            "summary": summary, "campaigns_count": len(campaigns), "rows": rows,
            "errors": errors[:30], "tax_percent": tax_percent}

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
    # V18.22: o scope PRECISA ir aqui, na autorização. Sem ele o Mercado Livre
    # concede só o padrão da aplicação — sem offline_access e, portanto, SEM
    # refresh_token. Era por isso que a conexão morria a cada ~6h e os pedidos
    # paravam de cair até alguém reconectar à mão. ML_OAUTH_SCOPE existia, mas
    # só era exibido no status; nunca era enviado.
    parametros = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if ML_OAUTH_SCOPE:
        parametros["scope"] = ML_OAUTH_SCOPE
    query = urlencode(parametros)
    return f"{ML_AUTH_URL}?{query}"


def ml_exchange_authorization_code(code: str, state: str) -> dict[str, Any]:
    previous_integration = ml_integration_row()
    recovery_since = str(previous_integration["last_sync_at"] or "") if previous_integration else ""
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
    ml_store_token(payload, preserve_refresh_if_missing=False, refresh_expected=True)
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
    status = ml_status_payload()
    status["recovery_since"] = recovery_since
    return status


def _ml_parse_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return "-".join(match.groups()) if match else ""


def _ml_logistic_type(shipment: dict[str, Any] | None) -> str:
    shipment = shipment or {}
    logistic = shipment.get("logistic") if isinstance(shipment.get("logistic"), dict) else {}
    shipping_option = shipment.get("shipping_option") if isinstance(shipment.get("shipping_option"), dict) else {}
    candidates = [
        logistic.get("type"),
        shipment.get("logistic_type"),
        shipping_option.get("logistic_type"),
    ]
    for value in candidates:
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _ml_is_full_shipment(shipment: dict[str, Any] | None) -> bool:
    logistic_type = _ml_logistic_type(shipment)
    if logistic_type in {"fulfillment", "full"}:
        return True
    tags = shipment.get("tags") if isinstance(shipment, dict) and isinstance(shipment.get("tags"), list) else []
    return any(str(tag or "").strip().lower() in {"fulfillment", "full"} for tag in tags)



def _nfe_xml_dest_name(xml_text: str) -> str:
    """Extrai dest/xNome quando o arquivo fiscal persistido for XML NF-e."""
    text = str(xml_text or "").strip()
    if not text or "<" not in text or "xNome" not in text:
        return ""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text.encode("utf-8", errors="ignore"))
        for dest in root.iter():
            if str(dest.tag).split("}")[-1].lower() != "dest":
                continue
            for child in list(dest):
                if str(child.tag).split("}")[-1].lower() == "xnome":
                    return " ".join(str(child.text or "").split()).strip()
    except Exception:
        return ""
    return ""


def _zpl_decode_fh_text(value: Any) -> str:
    """Decodifica sequências ^FH do ZPL, inclusive UTF-8 (_C3_A7 etc.)."""
    text = str(value or "")
    def repl(match: re.Match[str]) -> str:
        seq = match.group(0)
        try:
            raw = bytes(int(x, 16) for x in re.findall(r"_([0-9A-Fa-f]{2})", seq))
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return seq
    text = re.sub(r"(?:_[0-9A-Fa-f]{2})+", repl, text)
    return " ".join(text.replace("\\&", " ").split()).strip()


def _nfe_name_candidate(value: Any) -> str:
    text = _zpl_decode_fh_text(value)
    text = re.sub(r"^[\s:;\-–—]+|[\s:;\-–—]+$", "", text).strip()
    if not (4 <= len(text) <= 120) or not re.search(r"[A-Za-zÀ-ÿ]", text):
        return ""
    norm = normalize(text)
    blocked = (
        "DESTINATARIO", "DESTINATÁRIO", "REMETENTE", "NOME RAZAO SOCIAL", "NOME RAZÃO SOCIAL",
        "ENDERECO", "ENDEREÇO", "MUNICIPIO", "MUNICÍPIO", "CEP", "CPF", "CNPJ", "CHAVE DE ACESSO",
        "DANFE", "NOTA FISCAL", "INSCRICAO", "INSCRIÇÃO", "BAIRRO", "FONE", "UF", "DATA DE EMISSAO",
        "MERCADO LIVRE", "NOROMAK CAMINHOES E ONIBUS", "NOROMAK CAMINHÕES E ÔNIBUS"
    )
    if any(norm == normalize(x) or norm.startswith(normalize(x) + " ") for x in blocked):
        return ""
    # Nome/razão social deve ter ao menos duas partes, exceto razão social longa sem espaço incomum.
    if len(text.split()) < 2:
        return ""
    return text


def _nfe_name_from_zpl(zpl: str) -> str:
    text = str(zpl or "")
    if not text:
        return ""
    xml_name = _nfe_xml_dest_name(text)
    if xml_name:
        return xml_name
    fields = [_zpl_decode_fh_text(x) for x in re.findall(r"\^FD([\s\S]*?)\^FS", text, flags=re.IGNORECASE)]
    fields = [x for x in fields if x]
    # 1) Nome no mesmo campo do rótulo.
    for field in fields:
        m = re.search(r"(?:DESTINAT[ÁA]RIO|NOME\s*/?\s*RAZ[ÃA]O\s+SOCIAL|CLIENTE)\s*[:\-]\s*(.+)$", field, flags=re.I)
        if m:
            candidate = _nfe_name_candidate(m.group(1))
            if candidate:
                return candidate
    # 2) Nome logo após NOME/RAZÃO SOCIAL; é o padrão mais seguro em DANFE.
    label_indexes = []
    for i, field in enumerate(fields):
        norm = normalize(field)
        if ("NOME" in norm and "RAZAO" in norm and "SOCIAL" in norm) or norm in {"DESTINATARIO", "DESTINATÁRIO"}:
            label_indexes.append(i)
    for i in label_indexes:
        for field in fields[i+1:i+7]:
            candidate = _nfe_name_candidate(field)
            if candidate:
                return candidate
    return ""


def _walk_string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_string_values(child)


def _nfe_name_from_label_record(record: dict[str, Any]) -> str:
    """Lê o nome diretamente do arquivo fiscal/etiqueta que já está no banco."""
    if not isinstance(record, dict):
        return ""
    # Campos explícitos, caso uma versão/importador já os tenha extraído.
    meta = record.get("metadados") if isinstance(record.get("metadados"), dict) else {}
    for source in (record, meta):
        for key in ("nomeNFe", "nome_nfe", "destinatario", "destinatário", "cliente", "xNome", "razaoSocial", "razãoSocial"):
            candidate = _nfe_name_candidate(source.get(key))
            if candidate:
                return candidate
    # Prioriza DANFE/XML; depois procura qualquer string do registro que contenha XML NF-e.
    for key in ("danfe", "xml", "nfe_xml", "nota_xml", "conteudo_xml"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            name = _nfe_xml_dest_name(raw) or _nfe_name_from_zpl(raw)
            if name:
                return name
    for raw in _walk_string_values(record):
        if "<" in raw and "xNome" in raw:
            name = _nfe_xml_dest_name(raw)
            if name:
                return name
    return ""


def _ml_aliases_from_values(*values: Any) -> set[str]:
    aliases: set[str] = set()
    for raw in values:
        if isinstance(raw, (list, tuple, set)):
            aliases.update(_ml_aliases_from_values(*raw))
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        aliases.add(normalize(text))
        digits = re.sub(r"\D+", "", text)
        if digits:
            aliases.update(_ml_order_aliases(digits, ""))
    return {normalize(x) for x in aliases if normalize(x)}


def ml_update_pending_names_from_uploaded_nfe() -> dict[str, Any]:
    """Reprocessa pedidos pendentes usando a NF-e/DANFE já vinculada no banco de etiquetas.

    Não depende de OAuth nem de uma nova consulta ao Mercado Livre. Somente altera
    conf_pedidos (pendentes), nunca histórico/concluídos.
    """
    orders_raw, _, _ = get_storage("conf_pedidos")
    labels_raw, _, _ = get_storage("conf_etiquetas")
    pending = _rows_from_storage_value(orders_raw)
    try:
        bank = json.loads(labels_raw or "{}")
    except Exception:
        bank = {}
    records = bank.get("registros") if isinstance(bank, dict) and isinstance(bank.get("registros"), dict) else {}

    # Indexa cada arquivo fiscal vinculado por todas as chaves conhecidas.
    nfe_by_alias: dict[str, tuple[str, str]] = {}
    records_with_name = 0
    records_examined = 0
    for rid, rec in records.items():
        if not isinstance(rec, dict):
            continue
        marketplace = normalize(rec.get("marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        records_examined += 1
        name = _nfe_name_from_label_record(rec)
        if not name:
            continue
        records_with_name += 1
        meta = rec.get("metadados") if isinstance(rec.get("metadados"), dict) else {}
        aliases = _ml_aliases_from_values(
            rec.get("pedido"), rec.get("vinculadoA"), rec.get("chaves"),
            meta.get("pedido"), meta.get("venda"), meta.get("pack_id"), meta.get("shipment_id"),
            meta.get("nf"), meta.get("chaveAcesso"), meta.get("rastreio"),
        )
        for alias in aliases:
            nfe_by_alias.setdefault(alias, (name, str(rid)))

    pending_order_keys: dict[str, list[dict[str, Any]]] = {}
    for row in pending:
        if not isinstance(row, dict):
            continue
        marketplace = normalize(row.get("Marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        order_key = normalize(order_number(row) or row.get("VendaMercadoLivre") or row.get("PackIdMercadoLivre"))
        if order_key:
            pending_order_keys.setdefault(order_key, []).append(row)

    updated_orders = 0
    updated_rows = 0
    already_equal = 0
    no_linked_nfe = 0
    details = []
    timestamp = now_iso()
    for order_key, rows in pending_order_keys.items():
        aliases: set[str] = set()
        for row in rows:
            aliases.update(_ml_aliases_from_values(
                order_number(row), row.get("VendaMercadoLivre"), row.get("PackIdMercadoLivre"),
                row.get("ShipmentIdMercadoLivre"), row.get("Código de Rastreio"),
                row.get("NF"), row.get("Nota Fiscal"), row.get("Chave de Acesso"),
            ))
        found = next((nfe_by_alias[a] for a in aliases if a in nfe_by_alias), None)
        if not found:
            no_linked_nfe += 1
            continue
        name, rid = found
        changed = 0
        for row in rows:
            if str(row.get("Cliente") or "").strip() != name:
                row["Cliente"] = name
                changed += 1
            row["NomeNFeArquivo"] = name
            row["FonteNomeClienteMercadoLivre"] = "nfe_arquivo_vinculada"
            row["NomeNFeAtualizadoEm"] = timestamp
        if changed:
            updated_orders += 1
            updated_rows += changed
        else:
            already_equal += 1
        if len(details) < 60:
            details.append({"pedido": order_number(rows[0]), "nome": name, "etiqueta": rid, "linhas": changed})

    if updated_rows:
        save_storage(
            "conf_pedidos", orders_raw, json_dumps(pending), False,
            actor="sistema", actor_role="sistema", source_screen="Admin / Integrações",
            change_method="Atualizar nomes pelas NF-e já vinculadas", client_ip="127.0.0.1",
            allow_completed_reimport=False,
        )
    return {
        "ok": True,
        "pending_orders": len(pending_order_keys),
        "invoice_records_examined": records_examined,
        "invoice_records_with_name": records_with_name,
        "updated_orders": updated_orders,
        "updated_rows": updated_rows,
        "already_equal": already_equal,
        "without_linked_invoice_name": no_linked_nfe,
        "details": details,
    }


def _ml_billing_details(order: dict[str, Any]) -> dict[str, Any]:
    """Obtém exclusivamente nome/razão social de Dados para NF-e do Mercado Livre.

    Fluxo oficial: /orders/{id} -> buyer.billing_info.id ->
    /orders/billing-info/{site_id}/{billing_info_id}.

    Durante a migração do ML algumas contas ainda devolvem billing-info em formatos
    diferentes (objeto fiscal direto na raiz, endpoint legado e headers distintos).
    Esta função aceita essas variações SEM usar nickname/username/destinatário.
    """
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    raw_embedded = buyer.get("billing_info")
    raw_root_billing = order.get("billing_info")
    embedded = raw_embedded if isinstance(raw_embedded, dict) else {}
    root_billing = raw_root_billing if isinstance(raw_root_billing, dict) else {}
    billing_id = str(
        embedded.get("id") or root_billing.get("id")
        or (raw_embedded if isinstance(raw_embedded, (str, int)) else "")
        or (raw_root_billing if isinstance(raw_root_billing, (str, int)) else "") or ""
    ).strip()
    site_id = str(
        order.get("site_id")
        or ((order.get("context") or {}).get("site") if isinstance(order.get("context"), dict) else "")
        or "MLB"
    ).strip().upper() or "MLB"
    order_id = str(order.get("id") or "").strip()
    errors: list[str] = []
    # V17.84: o documento fiscal já vinha no mesmo payload do nome e era descartado.
    # extract_name() preenche este dicionário ao encontrar identification.
    documento: dict[str, str] = {}

    def extract_name(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        fiscal_buyer = payload.get("buyer") if isinstance(payload.get("buyer"), dict) else {}
        info = fiscal_buyer.get("billing_info") if isinstance(fiscal_buyer.get("billing_info"), dict) else {}
        if not info and isinstance(payload.get("billing_info"), dict):
            info = payload["billing_info"]

        # Algumas respostas do endpoint legado/V2 entregam o próprio objeto
        # billing_info na raiz, por exemplo {"name":..., "last_name":...}.
        if not info and any(k in payload for k in (
            "name", "first_name", "last_name", "surname", "business_name",
            "corporate_name", "company_name", "additional_info", "identification"
        )):
            info = payload

        if not isinstance(info, dict) or not info:
            return ""

        additional = info.get("additional_info") if isinstance(info.get("additional_info"), list) else []
        vals: dict[str, str] = {}
        for item in additional:
            if not isinstance(item, dict):
                continue
            key = str(item.get("type") or item.get("key") or item.get("name") or "").strip().upper()
            value = str(item.get("value") or item.get("description") or "").strip()
            if key and value and key not in vals:
                vals[key] = value

        first = str(
            info.get("name") or info.get("first_name")
            or vals.get("FIRST_NAME") or vals.get("NAME") or ""
        ).strip()
        last = str(
            info.get("last_name") or info.get("surname")
            or vals.get("LAST_NAME") or vals.get("SURNAME") or ""
        ).strip()
        business = str(
            info.get("business_name") or info.get("corporate_name") or info.get("company_name")
            or vals.get("BUSINESS_NAME") or vals.get("CORPORATE_NAME") or vals.get("COMPANY_NAME") or ""
        ).strip()
        identification = info.get("identification") if isinstance(info.get("identification"), dict) else {}
        doc_type = str(identification.get("type") or vals.get("DOC_TYPE") or "").strip().upper()

        # V17.84: guarda o CPF/CNPJ do comprador. O Linx DMS localiza o cliente
        # por documento, então sem isto não há como atualizar cadastro de venda ML.
        # Só grava a primeira ocorrência: o payload oficial vem antes do legado.
        numero = re.sub(r"\D", "", str(identification.get("number") or vals.get("DOC_NUMBER") or ""))
        if numero and not documento.get("number"):
            documento["number"] = numero
            documento["type"] = doc_type or ("CNPJ" if len(numero) == 14 else "CPF")

        # No MLB pessoa jurídica vem normalmente com name=razão social.
        if doc_type == "CNPJ":
            return business or first
        if business and not (first or last):
            return business
        return " ".join(x for x in (first, last) if x).strip() or business

    # Se o próprio /orders já trouxe o objeto fiscal completo, aproveita.
    embedded_name = extract_name({"buyer": {"billing_info": embedded}}) if embedded else ""
    if embedded_name and any(k in embedded for k in (
        "name", "first_name", "business_name", "corporate_name", "identification", "additional_info"
    )):
        return {"name": embedded_name, "billing_info_id": billing_id, "source": "order_embedded",
                "doc_type": documento.get("type", ""), "doc_number": documento.get("number", ""), "errors": []}

    # Endpoint oficial atual. Testa sem header primeiro e depois as duas grafias
    # encontradas nos contratos do ML durante a migração.
    if billing_id:
        endpoint = f"/orders/billing-info/{quote(site_id)}/{quote(billing_id)}"
        for hdr, label in (
            (None, "billing_info_current"),
            ({"X-Api-Version": "2"}, "billing_info_api_v2"),
            ({"x-version": "2"}, "billing_info_x_v2"),
        ):
            try:
                payload = ml_api_get(endpoint, headers=hdr)
                name = extract_name(payload)
                if name:
                    return {"name": name, "billing_info_id": billing_id, "source": label,
                            "doc_type": documento.get("type", ""), "doc_number": documento.get("number", ""), "errors": errors}
                errors.append(f"{label} respondeu sem nome fiscal")
            except Exception as exc:
                errors.append(f"{label}: {str(exc)[:400]}")
    else:
        errors.append("buyer.billing_info.id ausente em /orders")

    # Compatibilidade temporária enquanto o endpoint legado ainda responde em
    # determinadas contas. Aceita underscore e hífen e payload fiscal direto.
    if order_id:
        legacy_paths = (
            f"/orders/{quote(order_id)}/billing_info",
            f"/orders/{quote(order_id)}/billing-info",
        )
        header_variants = (
            ({"X-Api-Version": "2"}, "api_v2"),
            ({"x-version": "2"}, "x_v2"),
            (None, "v1"),
        )
        for legacy_path in legacy_paths:
            for hdr, suffix in header_variants:
                try:
                    payload = ml_api_get(legacy_path, headers=hdr)
                    name = extract_name(payload)
                    if name:
                        # Tenta também descobrir o id se ele vier somente no fallback.
                        fallback_buyer = payload.get("buyer") if isinstance(payload, dict) and isinstance(payload.get("buyer"), dict) else {}
                        fallback_info = fallback_buyer.get("billing_info") if isinstance(fallback_buyer.get("billing_info"), dict) else {}
                        fallback_id = str(fallback_info.get("id") or (payload.get("id") if isinstance(payload, dict) else "") or "").strip()
                        return {
                            "name": name,
                            "billing_info_id": billing_id or fallback_id,
                            "source": f"legacy_{suffix}",
                            "doc_type": documento.get("type", ""),
                            "doc_number": documento.get("number", ""),
                            "errors": errors,
                        }
                    errors.append(f"legacy_{suffix} respondeu sem nome fiscal")
                except Exception as exc:
                    errors.append(f"legacy_{suffix}: {str(exc)[:400]}")

    return {"name": "", "billing_info_id": billing_id, "source": "",
            "doc_type": documento.get("type", ""), "doc_number": documento.get("number", ""), "errors": errors[-8:]}

def _ml_real_buyer_name(order: dict[str, Any]) -> str:
    """Nome real do comprador da order, sem usar nickname.

    Usado apenas enquanto Dados para NF-e ainda não estão disponíveis.
    Nunca usa destinatário/receiver_name e nunca usa nickname/username.
    """
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    first = str(buyer.get("first_name") or buyer.get("name") or "").strip()
    last = str(buyer.get("last_name") or buyer.get("surname") or "").strip()
    full = " ".join(x for x in (first, last) if x).strip()
    if full:
        return full

    # Algumas variantes da API encapsulam os dados pessoais em buyer.user.
    user = buyer.get("user") if isinstance(buyer.get("user"), dict) else {}
    first = str(user.get("first_name") or user.get("name") or "").strip()
    last = str(user.get("last_name") or user.get("surname") or "").strip()
    return " ".join(x for x in (first, last) if x).strip()

def _ml_dispatch_date(shipment_id: str, shipment: dict[str, Any] | None) -> str:
    """Data limite de despacho prometida pelo Mercado Livre.

    Prioriza /shipments/{id}/sla.expected_date. O campo antigo
    estimated_handling_limit permanece somente como fallback de compatibilidade.
    """
    if shipment_id and not _ml_is_full_shipment(shipment):
        try:
            sla = ml_api_get(f"/shipments/{quote(str(shipment_id))}/sla")
            if isinstance(sla, dict):
                parsed = _ml_parse_iso_date(sla.get("expected_date"))
                if parsed:
                    return parsed
        except Exception:
            pass

    shipment = shipment or {}
    shipping_option = shipment.get("shipping_option") if isinstance(shipment.get("shipping_option"), dict) else {}
    candidates = [
        shipping_option.get("estimated_handling_limit"),
        shipment.get("estimated_handling_limit"),
    ]
    for value in candidates:
        parsed = _ml_parse_iso_date(value)
        if parsed:
            return parsed
    return datetime.now().astimezone().date().isoformat()


def _kit_sku_aliases(value: Any, strip_kit_prefix: bool = True) -> list[str]:
    """Gera aliases robustos para localizar SKU de kit.

    Além das formas normal/compacta, aceita prefixos operacionais usados nos
    anúncios, como KIT-, KIT2-, KIT3-, etc. Ex.: KIT3-KS001 também procura KS001.

    V18.03: `strip_kit_prefix` só pode ser True na BUSCA (um pedido "KIT2-X" acha
    um cadastro gravado como "X"). Na INDEXAÇÃO do catálogo tem que ser False —
    senão o kit "KIT2-X" fica indexado também sob "X" e a venda do produto SIMPLES
    "X" casa com o kit e a quantidade dobra (bug do 152285-0893014105).
    """
    raw = str(value or "").strip().upper()
    if not raw:
        return []

    candidates = [raw]
    if strip_kit_prefix:
        # Prefixos operacionais comuns do marketplace não fazem parte necessariamente
        # do SKU-base usado no Cadastro de Kits.
        stripped = re.sub(r"^KIT\s*\d*\s*[-_./: ]+", "", raw, flags=re.I).strip()
        if stripped and stripped != raw:
            candidates.append(stripped)

        # Também cobre KIT3KS001 (sem separador): remove apenas quando há conteúdo
        # suficiente após o prefixo, evitando transformar SKUs curtos indevidamente.
        m = re.match(r"^KIT\s*\d+(?=[A-Z0-9])(.+)$", raw, flags=re.I)
        if m and m.group(1).strip():
            candidates.append(m.group(1).strip())

    out: list[str] = []
    for candidate in candidates:
        exact = normalize(candidate)
        compact = "".join(ch for ch in candidate if ch.isalnum())
        for key in (exact, compact):
            if key and key not in out:
                out.append(key)
    return out


def _kit_catalog_lookup(catalog: dict[str, dict[str, Any]], sku: Any) -> dict[str, Any] | None:
    for key in _kit_sku_aliases(sku):
        hit = catalog.get(key)
        if hit:
            return hit
    return None


def _infer_quantity_kit_from_sku(sku: Any) -> dict[str, Any] | None:
    """Reconhece kits homogêneos codificados como KIT2-SKU, KIT3-SKU etc.

    O Cadastro de Kits explícito continua tendo prioridade. Este fallback só é usado
    quando não há composição cadastrada e transforma, por exemplo, KIT3-KS001
    em 3 unidades do componente KS001.
    """
    raw = str(sku or "").strip()
    if not raw:
        return None
    # V18.19: o separador entre a quantidade e o SKU-base é OBRIGATÓRIO e a
    # quantidade tem teto. Sem isso, "KIT" usado como RÓTULO seguido de códigos de
    # produto (ex.: "kit27200154", "KIT10291406 - 99090203") era lido como
    # "2.720.015 unidades do 4" / "10.291.406 unidades do 99090203" e explodia a
    # Demanda de Compra e a Demanda Full. Kit homogêneo real é "KITn-BASE" (n pequeno).
    m = re.match(r"^KIT\s*(\d+)\s*[-_./: ]+\s*(.+?)\s*$", raw, flags=re.I)
    if not m:
        return None
    try:
        qty = int(m.group(1))
    except Exception:
        return None
    base = str(m.group(2) or "").strip()
    if qty < 2 or qty > 99 or not base:
        return None
    return {
        "sku_kit": raw,
        "nome": raw,
        "ativo": True,
        "componentes": [{"sku": base, "quantidade": qty}],
        "inferido_por_prefixo": True,
    }


def _load_kit_catalog() -> dict[str, dict[str, Any]]:
    """Carrega o cadastro compartilhado de kits indexado pelo SKU do kit."""
    raw, _, _ = get_storage("conf_kits")
    try:
        rows = json.loads(raw or "[]")
    except Exception:
        rows = []
    catalog: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return catalog
    for row in rows:
        if not isinstance(row, dict):
            continue
        kit_sku = str(row.get("sku_kit") or row.get("SKU Kit") or row.get("sku") or "").strip()
        if not kit_sku:
            continue
        components_raw = row.get("componentes")
        if not isinstance(components_raw, list):
            components_raw = []
        components: list[dict[str, Any]] = []
        for comp in components_raw:
            if not isinstance(comp, dict):
                continue
            sku = str(comp.get("sku") or comp.get("SKU") or "").strip()
            try:
                qty = int(comp.get("quantidade") or comp.get("qtd") or comp.get("Quantidade") or 0)
            except (TypeError, ValueError):
                qty = 0
            if sku and qty > 0:
                components.append({"sku": sku, "quantidade": qty})
        if not components:
            continue
        entry = {
            "sku_kit": kit_sku,
            "nome": str(row.get("nome") or row.get("Nome") or kit_sku).strip() or kit_sku,
            "componentes": components,
            "ativo": bool(row.get("ativo", True)),
        }
        # V18.03: indexa SÓ pelo SKU do próprio kit (sem tirar o prefixo KIT),
        # para o kit "KIT2-X" nunca vazar para a venda do produto simples "X".
        for alias in _kit_sku_aliases(kit_sku, strip_kit_prefix=False):
            catalog.setdefault(alias, entry)
    return catalog


def full_kit_compositions(skus: list[Any]) -> dict[str, dict[str, Any]]:
    """V17.96: composição dos kits presentes na planilha da Demanda Full.

    Mesma regra dos pedidos ML: Cadastro de Kits primeiro, depois o prefixo
    KIT2-/KIT3-. SKU comum fica de fora. Como o cadastro também é indexado pelo
    SKU sem o prefixo KIT, um SKU comum só vira kit quando é exatamente o SKU
    cadastrado — senão "X" herdaria a composição de "KIT2-X".
    """
    catalog = _load_kit_catalog()
    out: dict[str, dict[str, Any]] = {}
    for value in skus:
        sku = str(value or "").strip()
        if not sku or sku in out:
            continue
        entry = _kit_catalog_lookup(catalog, sku)
        fonte = "cadastro"
        if entry and (not entry.get("ativo", True) or (
            not re.match(r"^KIT", sku, flags=re.I) and normalize(entry.get("sku_kit")) != normalize(sku)
        )):
            entry = None
        if not entry:
            entry = _infer_quantity_kit_from_sku(sku)
            fonte = "prefixo"
        if not entry:
            continue
        comps = [
            {"sku": str(c.get("sku") or "").strip(), "quantidade": int(c.get("quantidade") or 0)}
            for c in entry.get("componentes") or []
            if str(c.get("sku") or "").strip() and int(c.get("quantidade") or 0) > 0
        ]
        if not comps or (len(comps) == 1 and comps[0]["quantidade"] == 1 and normalize(comps[0]["sku"]) == normalize(sku)):
            continue
        out[sku] = {"componentes": comps, "fonte": fonte, "nome": str(entry.get("nome") or sku)}
    return out


def full_anuncios_vendas(anuncios: list[Any], dias: int = 30) -> dict[str, dict[str, Any]]:
    """V18.61: vendas ML dos últimos `dias` por anúncio, para a Demanda Full.

    O relatório Full lista os anúncios de cada produto só pelo número
    ("1079290698 | 3615301139"); a tela precisa saber qual deles vende para
    montar a remessa. Aceita com ou sem o prefixo MLB. Anúncio sem venda volta
    com zero. Somente leitura de `vendas_v1799` (não distingue Full de Flex).
    """
    ids: list[str] = []
    for value in anuncios:
        digitos = re.sub(r"\D", "", str(value or ""))
        mlb = f"MLB{digitos}" if digitos else ""
        if mlb and mlb not in ids:
            ids.append(mlb)
    out = {mlb: {"unidades": 0, "pedidos": 0, "titulo": ""} for mlb in ids}
    if not ids:
        return out
    inicio = (datetime.now(BR_TZ) - timedelta(days=dias)).strftime("%Y-%m-%d")
    with DB_LOCK:
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            marcas = ",".join("?" * len(lote))
            rows = DB.execute(
                f"SELECT anuncio, COALESCE(SUM(qty),0) AS unidades, COUNT(DISTINCT order_id) AS pedidos, MAX(title) AS titulo "
                f"FROM vendas_v1799 WHERE marketplace='ml' AND sale_day >= ? AND anuncio IN ({marcas}) GROUP BY anuncio",
                (inicio, *lote),
            ).fetchall()
            for r in rows:
                out[r["anuncio"]] = {"unidades": int(r["unidades"] or 0), "pedidos": int(r["pedidos"] or 0), "titulo": str(r["titulo"] or "")}
    return out


def _expand_ml_order_item(item_data: dict[str, Any], kit_catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Expande um item ML cadastrado como kit em seus componentes.

    Retorna uma ou mais linhas lógicas. A quantidade do componente é:
    quantidade comprada do kit × quantidade cadastrada do componente.
    """
    item = item_data.get("item") if isinstance(item_data.get("item"), dict) else {}
    sku_raw = _ml_order_item_sku_raw(item_data)
    sku = _ml_normalize_api_sku(sku_raw)
    try:
        sold_qty = max(1, int(item_data.get("quantity") or 1))
    except (TypeError, ValueError):
        sold_qty = 1
    title = str(item.get("title") or "").strip()

    # V17.46: identificação de KIT usa SOMENTE o SKU real recebido do ML.
    # Nunca removemos prefixos numéricos (ex.: 150922-0893764272) para procurar
    # cadastro de kit, pois isso transformava anúncios normais em kits incorretos.
    # _kit_catalog_lookup continua aceitando apenas aliases próprios de KIT
    # (KIT2-, KIT3- etc.), sem tratar prefixos numéricos como descartáveis.
    kit = _kit_catalog_lookup(kit_catalog, sku)
    if not kit:
        kit = _infer_quantity_kit_from_sku(sku)
    if not kit or not kit.get("ativo", True):
        return [{
            "sku": sku,
            "sku_raw": sku_raw,
            "quantity": sold_qty,
            "description": title,
            "is_kit": False,
            "kit_sku": "",
            "kit_name": "",
            "kits_sold": 0,
            "qty_per_kit": 0,
            "kit_unregistered": ("KIT" in normalize(sku) or "KIT" in normalize(title)),
        }]

    expanded: list[dict[str, Any]] = []
    for component in kit.get("componentes") or []:
        component_sku = str(component.get("sku") or "").strip()
        qty_per_kit = max(1, int(component.get("quantidade") or 1))
        expanded.append({
            "sku": component_sku,
            "sku_raw": sku_raw,
            "quantity": sold_qty * qty_per_kit,
            "description": title,
            "is_kit": True,
            "kit_sku": str(kit.get("sku_kit") or sku).strip(),
            "kit_name": str(kit.get("nome") or kit.get("sku_kit") or sku).strip(),
            "kits_sold": sold_qty,
            "qty_per_kit": qty_per_kit,
            "kit_unregistered": False,
        })
    return expanded


def _ml_find_product(products: list[dict[str, Any]], sku: str) -> dict[str, Any] | None:
    key = normalize(sku)
    if not key:
        return None
    for product in products:
        if normalize(product.get("SKU")) == key:
            return product
    return None


def _ml_normalize_api_sku(value: Any) -> str:
    """Retorna o SKU operacional usado pela Noromak.

    O Mercado Livre pode prefixar o SKU real com um código numérico interno do
    anúncio, por exemplo ``158529-0893117732``. Para conferência/cadastro o SKU
    efetivo é ``0893117732``. Prefixos semânticos como KIT2-/KIT3- são mantidos.
    O valor bruto da integração continua preservado separadamente em
    ``SkuOrigemImportacao`` para auditoria/restauração.
    """
    raw = str(value or "").strip()
    if "-" in raw:
        prefix, remainder = raw.split("-", 1)
        if prefix.strip().isdigit() and remainder.strip():
            return remainder.strip()
    return raw


def _ml_sku_lookup_candidates(value: Any) -> list[str]:
    """Candidatos de cadastro a partir do SKU operacional, sem heurística ampla."""
    effective = _ml_normalize_api_sku(value)
    return [effective] if effective else []


def _ml_order_item_sku_raw(item_data: dict[str, Any]) -> str:
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


def _ml_order_item_sku(item_data: dict[str, Any]) -> str:
    return _ml_normalize_api_sku(_ml_order_item_sku_raw(item_data))


def ml_order_shipments(order_id: str) -> tuple[str, dict[str, Any] | None]:
    """Resolve o shipment real da order aceitando contratos antigos e novos."""
    shipment_id = ""
    shipment_detail: dict[str, Any] | None = None
    errors: list[str] = []

    def pick_forward(payload: Any) -> str:
        shipments = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) and payload.get("id") else [])
        if not shipments:
            return ""
        for item in shipments:
            if not isinstance(item, dict):
                continue
            typ = str(item.get("type") or "").strip().lower()
            direction = str(item.get("direction") or "").strip().lower()
            if typ == "forward" or direction == "forward":
                return str(item.get("id") or "").strip()
        if len(shipments) == 1 and isinstance(shipments[0], dict):
            typ = str(shipments[0].get("type") or "").strip().lower()
            direction = str(shipments[0].get("direction") or "").strip().lower()
            if typ not in {"return", "return_to_buyer", "reverse"} and direction not in {"return", "reverse"}:
                return str(shipments[0].get("id") or "").strip()
        for item in shipments:
            if not isinstance(item, dict):
                continue
            marker = " ".join([
                str(item.get("type") or ""), str(item.get("direction") or ""),
                str(item.get("status") or ""), str(item.get("substatus") or ""),
                " ".join(str(x) for x in (item.get("tags") or []) if x is not None),
            ]).lower()
            if not any(x in marker for x in ("return", "reverse", "devol")):
                sid = str(item.get("id") or "").strip()
                if sid:
                    return sid
        return ""

    attempts = [
        (f"/orders/{quote(str(order_id))}/shipments", None),
        (f"/orders/{quote(str(order_id))}/shipments", {"x-format-new": "true"}),
        (f"/orders/{quote(str(order_id))}/shipments?list_all=true", None),
        (f"/orders/{quote(str(order_id))}/shipments?list_all=true", {"x-format-new": "true"}),
    ]
    for path, headers in attempts:
        if shipment_id:
            break
        try:
            result = ml_api_get(path, headers=headers)
            shipment_id = pick_forward(result)
        except Exception as exc:
            errors.append(f"{path}:{exc}")

    if shipment_id:
        try:
            detail = ml_api_get(
                f"/shipments/{quote(shipment_id)}?views=destination",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            if isinstance(detail, dict):
                shipment_detail = detail
        except Exception as exc:
            errors.append(f"detail:{exc}")

    if not shipment_id and errors:
        try:
            ml_log_event("shipment_resolver", f"/orders/{order_id}/shipments", "", ML_CLIENT_ID,
                         "SEM SHIPMENT", " | ".join(errors)[:1200])
        except Exception:
            pass
    return shipment_id, shipment_detail


def _ml_pack_shipment(pack_id: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Resolve shipment e orders pelo pack, que é o número exibido em várias telas do ML."""
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        return "", None, []
    payload = ml_api_get(f"/packs/{quote(pack_id)}")
    if not isinstance(payload, dict):
        return "", None, []
    shipment_obj = payload.get("shipment") if isinstance(payload.get("shipment"), dict) else {}
    shipment_id = str(shipment_obj.get("id") or "").strip()
    order_ids: list[str] = []
    for item in payload.get("orders") or []:
        if isinstance(item, dict):
            oid = str(item.get("id") or "").strip()
            if oid and oid not in order_ids:
                order_ids.append(oid)
    detail = None
    if shipment_id:
        try:
            got = ml_api_get(
                f"/shipments/{quote(shipment_id)}?views=destination",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            if isinstance(got, dict):
                detail = got
        except Exception:
            detail = None
    return shipment_id, detail, order_ids


def _ml_orders_for_shipment(shipment_id: str) -> list[str]:
    shipment_id = str(shipment_id or "").strip()
    if not shipment_id:
        return []
    try:
        payload = ml_api_get(f"/shipments/{quote(shipment_id)}/orders", headers={"X-New-Domain": "true"})
    except Exception:
        return []
    items = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            oid = str(item.get("order_id") or item.get("id") or "").strip()
            if oid and oid not in out:
                out.append(oid)
    return out


def _ml_resolve_forward_shipment(order_id: str, order_hint: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    """Order -> shipment; se falhar, usa o pack oficial; por último shipping.id legado."""
    shipment_id, shipment = ml_order_shipments(order_id)
    if shipment_id:
        return shipment_id, shipment

    order_hint = order_hint if isinstance(order_hint, dict) else {}
    pack_id = str(order_hint.get("pack_id") or "").strip()
    if pack_id:
        try:
            sid, detail, _ = _ml_pack_shipment(pack_id)
            if sid:
                return sid, detail
        except Exception as exc:
            try:
                ml_log_event("shipment_resolver", f"/packs/{pack_id}", "", ML_CLIENT_ID,
                             "PACK FALLBACK ERRO", str(exc)[:1200])
            except Exception:
                pass

    shipping = order_hint.get("shipping") if isinstance(order_hint.get("shipping"), dict) else {}
    hinted_id = str(shipping.get("id") or "").strip()
    if hinted_id:
        try:
            detail = ml_api_get(
                f"/shipments/{quote(hinted_id)}?views=destination",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            return hinted_id, detail if isinstance(detail, dict) else None
        except Exception:
            return hinted_id, None
    return "", None

def _ml_invoice_available(invoice: dict[str, Any] | None) -> bool:
    if not isinstance(invoice, dict):
        return False
    status = str(invoice.get("status") or "").strip().lower()
    return bool(str(invoice.get("fiscal_key") or "").strip()
                or str(invoice.get("invoice_number") or "").strip()
                or status in {"approved", "authorized", "authorized_to_use"})


def _ml_extract_zpl_from_label_response(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [n for n in archive.namelist() if n.lower().endswith((".txt",".zpl",".prn"))]
                chunks = []
                for name in sorted(names):
                    text = archive.read(name).decode("utf-8", errors="replace").strip()
                    if text:
                        chunks.append(text)
                return "\n".join(chunks).strip()
        except zipfile.BadZipFile:
            return ""
    text = raw.decode("utf-8", errors="replace").strip()
    return text if "^XA" in text or "~DG" in text else ""


def _ml_only_shipping_label_zpl(zpl: str) -> str:
    """Remove a DANFE simplificada quando o Mercado Livre devolve mais de um documento ZPL.

    Alguns shipments retornam, no mesmo payload zpl2, uma etiqueta fiscal (DANFE
    simplificada) seguida da etiqueta logística. A expedição Noromak deve imprimir
    apenas a etiqueta de envio. Quando não conseguimos identificar com segurança
    uma DANFE, preservamos o ZPL original para não descartar uma etiqueta válida.
    """
    text = str(zpl or "").strip()
    if not text:
        return ""

    docs = re.findall(r"\^XA[\s\S]*?\^XZ", text, flags=re.IGNORECASE)
    if len(docs) <= 1:
        return text

    fiscal_markers = (
        "DANFE SIMPLIFICADO",
        "DANFE SIMPLIFICADA",
        "PROTOCOLO DE AUTORIZACAO DE USO",
        "PROTOCOLO DE AUTORIZAÇÃO DE USO",
    )

    def normalized(doc: str) -> str:
        return unicodedata.normalize("NFKD", doc).encode("ascii", "ignore").decode("ascii").upper()

    kept = []
    removed = 0
    for doc in docs:
        ndoc = normalized(doc)
        if any(marker in ndoc for marker in fiscal_markers):
            removed += 1
            continue
        kept.append(doc.strip())

    # Só altera o arquivo quando uma DANFE foi realmente identificada e ainda
    # resta ao menos um documento de envio.
    if removed and kept:
        return "\n".join(kept).strip()
    return text


def _ml_fetch_shipping_zpl(shipment_id: str) -> str:
    query = urlencode({"shipment_ids": str(shipment_id), "response_type": "zpl2"})
    raw, _ = ml_api_get_bytes(f"/shipment_labels?{query}")
    return _ml_only_shipping_label_zpl(_ml_extract_zpl_from_label_response(raw))


def _ml_store_linked_label(*, operational_order_id: str, order_id: str, pack_id: str,
                           shipment_id: str, tracking: str, zpl: str,
                           invoice: dict[str, Any] | None) -> bool:
    if not operational_order_id or not shipment_id or not zpl.strip():
        return False
    # Limpa a trava-raiz legada que podia esconder a etiqueta recém-baixada.
    with DB_LOCK:
        DB.execute(
            "DELETE FROM admin_manual_overrides WHERE storage_key=? AND entity_id='__ROOT__'",
            ("conf_etiquetas",),
        )
        DB.commit()
    raw, _, _ = get_storage("conf_etiquetas")
    try:
        bank = json.loads(raw) if raw else {}
    except Exception:
        bank = {}
    if not isinstance(bank, dict):
        bank = {}
    bank["version"] = max(8, int(bank.get("version") or 0))
    bank.setdefault("registros", {})
    bank.setdefault("ordem", [])
    bank.setdefault("porChave", {})
    bank.setdefault("avisos", [])
    bank.setdefault("auditoria", [])
    bank.setdefault("resumo", {})
    bank.setdefault("vinculados", {})

    existing_id = ""
    for rid, rec in bank["registros"].items():
        if not isinstance(rec, dict) or rec.get("marketplace") != "mercado_livre":
            continue
        meta = rec.get("metadados") if isinstance(rec.get("metadados"), dict) else {}
        if str(meta.get("shipment_id") or "") == str(shipment_id) or (
            str(rec.get("pedido") or "") == str(operational_order_id)
            and str(rec.get("origem") or "").startswith("api_mercadolivre")
        ):
            existing_id = str(rid)
            break

    fiscal_key = str((invoice or {}).get("fiscal_key") or "").strip()
    invoice_number = str((invoice or {}).get("invoice_number") or "").strip()
    keys = [operational_order_id, order_id, pack_id, shipment_id, tracking, fiscal_key, invoice_number]
    keys = [str(x).strip() for x in keys if str(x or "").strip()]
    rid = existing_id or ("etq_ml_api_" + hashlib.sha1(str(shipment_id).encode("utf-8")).hexdigest()[:18])

    record = {
        "id": rid, "marketplace": "mercado_livre", "envio": zpl, "danfe": "",
        "arquivo": f"Mercado Livre API · shipment {shipment_id}",
        "metadados": {"pedido": operational_order_id, "venda": order_id, "pack_id": pack_id,
                      "shipment_id": shipment_id, "rastreio": tracking,
                      "nf": invoice_number, "chaveAcesso": fiscal_key},
        "chaves": keys, "vinculadoA": [operational_order_id], "pedido": operational_order_id,
        "vinculo": "api_mercadolivre_ready_to_print", "origem": "api_mercadolivre",
        "adicionadaEm": now_iso(), "adicionadaPor": "mercadolivre-api",
    }
    bank["registros"][rid] = record
    if rid not in bank["ordem"]:
        bank["ordem"].append(rid)
    bank["porChave"] = {}
    for record_id in bank["ordem"]:
        rec = bank["registros"].get(record_id)
        if not isinstance(rec, dict):
            continue
        for key in rec.get("chaves") or []:
            key = str(key or "").strip()
            if not key:
                continue
            bank["porChave"].setdefault(key, [])
            if record_id not in bank["porChave"][key]:
                bank["porChave"][key].append(record_id)
    bank["atualizadoEm"] = now_iso()
    if not any(isinstance(a, dict) and a.get("etiqueta")==rid and a.get("pedido")==operational_order_id
               for a in bank["auditoria"]):
        bank["auditoria"].append({"marketplace":"mercado_livre","pedido":operational_order_id,
                                  "criterio":"api_ready_to_print","etiqueta":rid,"arquivo":record["arquivo"]})
        bank["auditoria"] = bank["auditoria"][-250:]
    save_storage("conf_etiquetas", raw, json_dumps(bank), False,
                 actor="mercadolivre-api", actor_role="sistema",
                 source_screen="API Mercado Livre", change_method="Etiqueta automática após NF",
                 client_ip="127.0.0.1")
    return True


def _ml_fiscal_and_label_state(*, order: dict[str, Any], order_id: str, operational_order_id: str,
                               pack_id: str, shipment_id: str, shipment: dict[str, Any] | None,
                               tracking: str) -> dict[str, Any]:
    site_id = str(order.get("site_id") or "MLB").strip().upper() or "MLB"
    status = str((shipment or {}).get("status") or "").strip().lower()
    substatus = str((shipment or {}).get("substatus") or "").strip().lower()

    invoice: dict[str, Any] = {}
    invoice_available = False
    invoice_unavailable_reason = ""

    # Dados fiscais são apenas enriquecimento. Nesta aplicação o Mercado Livre
    # pode responder HTTP 403 / PolicyAgent; isso não bloqueia o fluxo operacional.
    if shipment_id:
        try:
            payload = ml_api_get(
                f"/shipments/{quote(str(shipment_id))}/invoice_data?siteId={quote(site_id)}"
            )
            if isinstance(payload, dict):
                invoice = payload
                invoice_available = _ml_invoice_available(invoice)
        except Exception as exc:
            msg = str(exc)
            if "HTTP 403" in msg or "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in msg or "PolicyAgent" in msg:
                invoice_unavailable_reason = "Dados fiscais indisponíveis para esta aplicação (HTTP 403 / PolicyAgent)."
            else:
                invoice_unavailable_reason = msg[:500]

    invoice_pending = status == "ready_to_ship" and substatus == "invoice_pending"

    # V14.4: o substatus invoice_pending pode ficar defasado por alguns minutos
    # mesmo depois de a etiqueta já estar disponível na interface do Mercado Livre.
    # A fonte de verdade operacional passa a ser o próprio endpoint de etiquetas:
    # se o shipment está ready_to_ship, tentamos buscar o ZPL.
    should_try_label = bool(shipment_id) and status == "ready_to_ship"

    label_stored = False
    label_error = ""
    if should_try_label:
        try:
            zpl = _ml_fetch_shipping_zpl(shipment_id)
            if zpl:
                label_stored = _ml_store_linked_label(
                    operational_order_id=operational_order_id,
                    order_id=order_id,
                    pack_id=pack_id,
                    shipment_id=shipment_id,
                    tracking=tracking,
                    zpl=zpl,
                    invoice=invoice,
                )
            else:
                label_error = "Mercado Livre respondeu sem ZPL de etiqueta."
        except Exception as exc:
            label_error = str(exc)[:1200]

    if label_stored:
        fiscal_status = "DISPONÍVEL"
    elif invoice_pending:
        fiscal_status = "AGUARDANDO"
    elif status == "ready_to_ship":
        fiscal_status = "PROCESSANDO"
    else:
        fiscal_status = "AGUARDANDO"

    return {
        "invoice": invoice,
        "invoice_available": invoice_available,
        "invoice_unavailable_reason": invoice_unavailable_reason,
        "invoice_pending": invoice_pending,
        "fiscal_ready": bool(label_stored) or not invoice_pending,
        "fiscal_status": fiscal_status,
        "shipment_status": status,
        "shipment_substatus": substatus,
        "label_stored": label_stored,
        "label_error": label_error,
        "ready_for_conference": bool(label_stored),
    }


def _ml_order_aliases(order_id: str, pack_id: str = "") -> set[str]:
    """Identificadores equivalentes usados pela API e pela tela do Mercado Livre.

    A tela/print pode exibir a venda sem o prefixo técnico 20000 do order_id.
    """
    aliases: set[str] = set()
    for raw in (order_id, pack_id):
        value = re.sub(r"\D+", "", str(raw or ""))
        if not value:
            continue
        aliases.add(value)
        if value.startswith("20000") and len(value) > 5:
            aliases.add(value[5:])
        # Fallback tolerante para números copiados da interface: últimos 11/12 dígitos.
        if len(value) > 12:
            aliases.add(value[-11:])
            aliases.add(value[-12:])
    return {normalize(x) for x in aliases if x}


def _ml_bind_manual_pending_rows(
    pending: list[dict[str, Any]],
    *, order_id: str, pack_id: str, shipment_id: str, shipment: dict[str, Any] | None,
) -> int:
    """Vincula e também corrige linhas que gravaram PACK no campo VendaMercadoLivre."""
    aliases = _ml_order_aliases(order_id, pack_id)
    if not aliases:
        return 0
    operational_order_id = pack_id or order_id
    logistic = _ml_logistic_type(shipment)
    bound = 0
    for row in pending:
        if not isinstance(row, dict):
            continue
        marketplace = normalize(row.get("Marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        row_aliases: set[str] = set()
        for raw in (order_number(row), row.get("VendaMercadoLivre"), row.get("PackIdMercadoLivre")):
            digits = re.sub(r"\D+", "", str(raw or ""))
            if digits:
                row_aliases.update(_ml_order_aliases(digits, ""))
        if not row_aliases.intersection(aliases):
            continue
        old = (str(row.get("VendaMercadoLivre") or ""), str(row.get("PackIdMercadoLivre") or ""), str(row.get("ShipmentIdMercadoLivre") or ""))
        row["Pedido"] = operational_order_id
        row["VendaMercadoLivre"] = order_id
        row["PackIdMercadoLivre"] = pack_id
        row["ShipmentIdMercadoLivre"] = shipment_id
        row["LogisticaMercadoLivre"] = logistic
        row["MercadoLivreAtualizadoEm"] = now_iso()
        original_source = str(row.get("OrigemCadastro") or "").strip()
        if "reconciliado mercado livre" not in original_source.lower():
            row["OrigemCadastro"] = (original_source + " / " if original_source else "") + "Reconciliado Mercado Livre"
        new = (order_id, pack_id, shipment_id)
        if old != new:
            bound += 1
    return bound

def _ml_manual_pending_numbers() -> set[str]:
    raw, _, _ = get_storage("conf_pedidos")
    linked = _ml_linked_order_ids()
    result: set[str] = set()
    for row in _rows_from_storage_value(raw):
        if not isinstance(row, dict):
            continue
        marketplace = normalize(row.get("Marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        sale_id = str(row.get("VendaMercadoLivre") or "").strip()
        if sale_id and sale_id in linked:
            continue
        for raw_id in (order_number(row), sale_id, row.get("PackIdMercadoLivre")):
            digits = re.sub(r"\D+", "", str(raw_id or ""))
            if digits:
                result.add(normalize(digits))
    return result

def ml_reconcile_manual_orders_once() -> dict[str, int]:
    """Reconcilia pedidos de print/extensão com a API e os coloca no watcher de etiqueta."""
    if not ML_MANUAL_RECONCILE_LOCK.acquire(blocking=False):
        return {"manual": 0, "matched": 0, "ready": 0, "errors": 0}
    try:
        manual_numbers = _ml_manual_pending_numbers()
        if not manual_numbers:
            return {"manual": 0, "matched": 0, "ready": 0, "errors": 0}

        _, seller_id = ml_access_token()
        cutoff = datetime.now(timezone.utc) - timedelta(days=ML_MANUAL_RECONCILE_DAYS)
        matched = ready = errors = 0
        remaining = set(manual_numbers)

        for page in range(ML_CATCHUP_MAX_PAGES):
            query = urlencode({
                "seller": seller_id,
                "order.status": "paid",
                "sort": "date_desc",
                "limit": str(ML_CATCHUP_PAGE_SIZE),
                "offset": str(page * ML_CATCHUP_PAGE_SIZE),
            })
            result = _ml_profitability_api_get(f"/orders/search?{query}", attempts=3)
            orders = result.get("results") if isinstance(result, dict) else []
            if not orders:
                break

            stop_by_date = False
            for order in orders:
                if not isinstance(order, dict):
                    continue
                created = _ml_parse_datetime(order.get("date_created"))
                if created is not None and created < cutoff:
                    stop_by_date = True
                    break

                order_id = str(order.get("id") or "").strip()
                pack_id = str(order.get("pack_id") or "").strip()
                aliases = _ml_order_aliases(order_id, pack_id)
                if not aliases.intersection(remaining):
                    continue

                try:
                    detail = ml_import_order(order_id, source="API Mercado Livre / reconciliação pedido manual")
                    matched += 1
                    remaining.difference_update(aliases)
                    if detail.get("ready_for_conference"):
                        ready += 1
                    ml_log_event(
                        "manual_reconcile", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                        "PEDIDO MANUAL VINCULADO",
                        json_dumps({
                            "order_id": order_id,
                            "pack_id": detail.get("pack_id") or "",
                            "shipment_id": detail.get("shipment_id") or "",
                            "ready_for_conference": bool(detail.get("ready_for_conference")),
                        })
                    )
                except Exception as exc:
                    errors += 1
                    ml_log_event(
                        "manual_reconcile", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                        "ERRO", str(exc)[:1200]
                    )
                if not remaining:
                    break

            if not remaining or stop_by_date or len(orders) < ML_CATCHUP_PAGE_SIZE:
                break

        return {"manual": len(manual_numbers), "matched": matched, "ready": ready, "errors": errors}
    finally:
        ML_MANUAL_RECONCILE_LOCK.release()


def ml_reconcile_manual_orders_loop(stop_event: threading.Event) -> None:
    """Roda inteiramente na VPS; navegador/PC pode ficar desligado."""
    # Primeira tentativa poucos segundos após o servidor iniciar.
    if stop_event.wait(5):
        return
    while not stop_event.is_set():
        try:
            row = ml_integration_row()
            # V14.6: reconcilia enquanto houver access_token utilizável. A ausência
            # de refresh_token impede apenas a renovação futura, não o uso do token atual.
            if row and str(row["access_token"] or "").strip():
                ml_reconcile_manual_orders_once()
        except Exception as exc:
            try:
                ml_log_event("manual_reconcile", "/orders/search", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])
            except Exception:
                pass
        if stop_event.wait(ML_MANUAL_RECONCILE_INTERVAL):
            break


def _ml_remove_cancelled_from_pending(
    pending: list[dict[str, Any]],
    *,
    order_id: str,
    pack_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Remove da fila pendente qualquer linha pertencente a uma venda ML cancelada."""
    order_id = str(order_id or "").strip()
    pack_id = str(pack_id or "").strip()
    removed = 0
    kept: list[dict[str, Any]] = []

    for row in pending:
        if not isinstance(row, dict):
            kept.append(row)
            continue

        marketplace = normalize(row.get("Marketplace"))
        is_ml = (
            "MERCADO LIVRE" in marketplace
            or "MERCADOLIVRE" in marketplace
            or bool(str(row.get("VendaMercadoLivre") or "").strip())
            or "mercado livre" in str(row.get("OrigemCadastro") or "").lower()
        )
        if not is_ml:
            kept.append(row)
            continue

        identifiers = {
            str(row.get("VendaMercadoLivre") or "").strip(),
            str(row.get("PackIdMercadoLivre") or "").strip(),
            str(order_number(row) or "").strip(),
        }
        identifiers.discard("")

        matched = bool(order_id and order_id in identifiers) or bool(pack_id and pack_id in identifiers)
        # V18.52: ADMIN > integração — pedido incluído/protegido pelo ADMIN fica.
        if matched and not _admin_manual_pending_protected(row):
            removed += 1
            continue
        kept.append(row)

    return kept, removed



def _pending_removed_identities_v1852(before: list[Any], after: list[Any]) -> set[str]:
    """Identidades das linhas que saíram de `before` para `after` (V18.52)."""
    kept = {id(r) for r in after}
    return {
        _pending_identity_v1714(r, i)
        for i, r in enumerate(before)
        if isinstance(r, dict) and id(r) not in kept
    }


def _ml_row_manual_kit_override(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    return normalize(row.get("KitManualOverride")) in {"SIM", "YES", "TRUE", "1"}


def _ml_same_pending_sale(row: dict[str, Any], order_id: str, pack_id: str) -> bool:
    if not isinstance(row, dict):
        return False
    row_order = str(row.get("VendaMercadoLivre") or "").strip()
    row_pack = str(row.get("PackIdMercadoLivre") or "").strip()
    visible_order = str(order_number(row) or "").strip()
    return bool(
        (order_id and order_id in {row_order, visible_order})
        or (pack_id and pack_id in {row_pack, visible_order})
    )


def ml_import_order(order_id: str, *, source: str = "API Mercado Livre") -> dict[str, Any]:
    order_id = str(order_id or "").strip()
    if not order_id:
        raise RuntimeError("Order ID vazio.")
    order = ml_api_get(f"/orders/{quote(order_id)}")
    if not isinstance(order, dict):
        raise RuntimeError(f"Pedido {order_id}: resposta inválida.")

    # V14.20: em algumas contas MLB o order padrão chega sem buyer.billing_info.id,
    # embora a tela do Mercado Livre mostre os Dados para NF-e. Nesse caso refaz a
    # leitura no contrato v2 e incorpora SOMENTE o identificador fiscal do comprador.
    buyer0 = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    billing0 = buyer0.get("billing_info")
    billing0_id = (billing0.get("id") if isinstance(billing0, dict) else billing0) or ""
    if not str(billing0_id).strip():
        try:
            try:
                order_v2 = ml_api_get(f"/orders/{quote(order_id)}", headers={"X-Api-Version": "2"})
            except Exception:
                order_v2 = ml_api_get(f"/orders/{quote(order_id)}", headers={"x-version": "2"})
            if isinstance(order_v2, dict):
                buyer_v2 = order_v2.get("buyer") if isinstance(order_v2.get("buyer"), dict) else {}
                billing_v2 = buyer_v2.get("billing_info")
                billing_v2_id = (billing_v2.get("id") if isinstance(billing_v2, dict) else billing_v2) or ""
                if str(billing_v2_id).strip():
                    buyer_merged = dict(buyer0)
                    buyer_merged["billing_info"] = billing_v2
                    order["buyer"] = buyer_merged
                # Se o contrato v2 trouxer site_id e o padrão não, aproveita também.
                if not str(order.get("site_id") or "").strip() and str(order_v2.get("site_id") or "").strip():
                    order["site_id"] = order_v2.get("site_id")
        except Exception as exc:
            try:
                ml_log_event("billing_info", f"/orders/{order_id}", "", ML_CLIENT_ID, "FALLBACK_V2_ERRO", str(exc)[:1200])
            except Exception:
                pass
    _, seller_id = ml_access_token()
    seller = order.get("seller") if isinstance(order.get("seller"), dict) else {}
    order_seller = str(seller.get("id") or "")
    if order_seller and seller_id and order_seller != seller_id:
        raise RuntimeError(f"Pedido {order_id} pertence a outro seller.")

    status = str(order.get("status") or "")
    shipment_id, shipment = _ml_resolve_forward_shipment(order_id, order)
    pack_id = str(order.get("pack_id") or "").strip()
    operational_order_id = pack_id or order_id

    current_raw, _, _ = get_storage("conf_pedidos")
    pending = _rows_from_storage_value(current_raw)

    # Antes de criar qualquer linha da API, tenta reconhecer pedidos que entraram
    # por print/extensão usando o número de venda visível do Mercado Livre.
    manual_rows_bound = _ml_bind_manual_pending_rows(
        pending,
        order_id=order_id,
        pack_id=pack_id,
        shipment_id=shipment_id,
        shipment=shipment,
    )

    # V16.74: se este pedido foi corrigido manualmente no Admin, a composição
    # e as quantidades locais passam a ser a fonte de verdade. Notificações,
    # refresh manual e sincronizações do Mercado Livre não podem desfazer isso.
    manual_kit_override = any(
        _ml_same_pending_sale(row, order_id, pack_id) and _ml_row_manual_kit_override(row)
        for row in pending if isinstance(row, dict)
    )

    # V16.36: vendas canceladas não podem continuar aparecendo em Pedidos pendentes.
    # O Mercado Livre pode enviar "cancelled" ou "canceled" conforme o recurso.
    if status.strip().lower() in {"cancelled", "canceled"}:
        before_rows = pending
        pending, removed = _ml_remove_cancelled_from_pending(
            pending, order_id=order_id, pack_id=pack_id
        )
        if removed:
            save_storage(
                "conf_pedidos", current_raw, json_dumps(pending), False,
                actor="mercadolivre-api", actor_role="sistema",
                source_screen="API Mercado Livre",
                change_method="Pedido cancelado no Mercado Livre",
                client_ip="127.0.0.1", allow_completed_reimport=False,
                remove_pending_identities=_pending_removed_identities_v1852(before_rows, pending),
            )
        ml_log_event(
            "order_cancelled", f"/orders/{order_id}", "", ML_CLIENT_ID,
            "REMOVIDO DOS PENDENTES",
            json_dumps({"order_id": order_id, "pack_id": pack_id, "removed_rows": removed}),
        )
        return {
            "order_id": order_id,
            "pack_id": pack_id,
            "shipment_id": shipment_id,
            "status": status,
            "cancelled": True,
            "removed_pending_rows": removed,
            "manual_rows_bound": manual_rows_bound,
            "ready_for_conference": False,
            "fiscal_ready": False,
            "label_stored": False,
        }

    # Full/Fulfillment é preparado e expedido pelo próprio Mercado Livre.
    # Não deve entrar na fila operacional da Expedição Noromak. Se a linha já
    # tiver sido criada por uma versão anterior da API, removemos somente as
    # linhas originadas pela própria API ao reencontrar esse pedido.
    if _ml_is_full_shipment(shipment):
        before_rows = pending
        before = len(pending)
        pending = [
            row for row in pending
            if not (
                isinstance(row, dict)
                and not _admin_manual_pending_protected(row)
                and (
                    str(row.get("VendaMercadoLivre") or "").strip() == order_id
                    or (pack_id and str(row.get("PackIdMercadoLivre") or "").strip() == pack_id)
                )
                and (
                    str(row.get("VendaMercadoLivre") or "").strip()
                    or "mercado livre" in str(row.get("OrigemCadastro") or "").lower()
                )
            )
        ]
        removed = before - len(pending)
        if removed:
            save_storage(
                "conf_pedidos", current_raw, json_dumps(pending), False,
                actor="mercadolivre-api", actor_role="sistema",
                source_screen="API Mercado Livre",
                change_method="Ignorado - Mercado Envios Full",
                client_ip="127.0.0.1", allow_completed_reimport=False,
                remove_pending_identities=_pending_removed_identities_v1852(before_rows, pending),
            )
        return {
            "order_id": order_id,
            "pack_id": pack_id,
            "shipment_id": shipment_id,
            "status": status,
            "ignored": True,
            "reason": "FULL",
            "logistic_type": _ml_logistic_type(shipment),
            "removed_existing_api_rows": removed,
            "inserted": 0,
            "updated": 0,
            "items": 0,
        }

    receiver = ""
    if isinstance(shipment, dict):
        # Contrato Hosted/New Format (2026): o destinatário vem em
        # destination.receiver_name. Mantém receiver_address como fallback
        # para compatibilidade com a vista anterior.
        destination = shipment.get("destination") if isinstance(shipment.get("destination"), dict) else {}
        addr = shipment.get("receiver_address") if isinstance(shipment.get("receiver_address"), dict) else {}
        receiver = str(
            destination.get("receiver_name")
            or addr.get("receiver_name")
            or addr.get("receiver")
            or ""
        ).strip()
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}

    # V14.27: prioriza sempre Dados para NF-e. Em pedidos recém-criados o
    # billing-info pode ainda não existir; nesse intervalo usa somente o NOME REAL
    # do comprador da própria order (first_name + last_name). Nunca usa nickname,
    # username, receiver_name ou "Quem recebe". Assim Cliente não fica vazio e,
    # quando o fiscal aparecer, um refresh posterior o substitui automaticamente.
    billing_details = _ml_billing_details(order)
    fiscal_name = str(billing_details.get("name") or "").strip()
    buyer_real_name = _ml_real_buyer_name(order)
    client = fiscal_name or buyer_real_name
    client_name_source = "dados_fiscais_nf" if fiscal_name else ("comprador_order" if buyer_real_name else "")
    # V17.84: CPF/CNPJ do comprador, usado para localizar o cliente no Linx DMS.
    fiscal_doc_type = str(billing_details.get("doc_type") or "").strip().upper()
    fiscal_doc_number = re.sub(r"\D", "", str(billing_details.get("doc_number") or ""))
    billing_info_id = str(billing_details.get("billing_info_id") or "").strip()
    billing_errors = billing_details.get("errors") if isinstance(billing_details.get("errors"), list) else []

    date_for_dispatch = _ml_dispatch_date(shipment_id, shipment)
    tracking = ""
    if isinstance(shipment, dict):
        tracking = str(shipment.get("tracking_number") or shipment.get("tracking_id") or "").strip()

    fiscal_state = _ml_fiscal_and_label_state(
        order=order, order_id=order_id, operational_order_id=operational_order_id,
        pack_id=pack_id, shipment_id=shipment_id, shipment=shipment, tracking=tracking
    )
    invoice = fiscal_state.get("invoice") if isinstance(fiscal_state.get("invoice"), dict) else {}
    invoice_number = str(invoice.get("invoice_number") or "").strip()
    fiscal_key = str(invoice.get("fiscal_key") or "").strip()

    products_raw, _, _ = get_storage("conf_produtos")
    products = _rows_from_storage_value(products_raw)
    lines: list[dict[str, Any]] = []

    kit_catalog = _load_kit_catalog()
    kit_applied = 0
    kit_unregistered = 0

    for item_data in order.get("order_items") or []:
        if not isinstance(item_data, dict):
            continue
        item = item_data.get("item") if isinstance(item_data.get("item"), dict) else {}

        for expanded_index, expanded in enumerate(_expand_ml_order_item(item_data, kit_catalog)):
            sku_raw = str(expanded.get("sku_raw") or "")
            sku = str(expanded.get("sku") or "").strip()
            quantity = max(1, int(expanded.get("quantity") or 1))
            is_kit = bool(expanded.get("is_kit"))
            if is_kit:
                kit_applied += 1
            if expanded.get("kit_unregistered"):
                kit_unregistered += 1

            description = str(expanded.get("description") or item.get("title") or "").strip()
            kit_name = str(expanded.get("kit_name") or "").strip()
            kit_sku = str(expanded.get("kit_sku") or "").strip()
            warning = ""
            if expanded.get("kit_unregistered"):
                warning = "KIT NÃO CADASTRADO — VERIFICAR COMPOSIÇÃO"

            row = {
                "Pedido": operational_order_id,
                "Marketplace": "MERCADO LIVRE",
                "Data do Pedido": date_for_dispatch,
                "Kit": "SIM" if is_kit else ("VERIFICAR" if warning else ""),
                "Nome do Kit": kit_name,
                "Quantidade de Kits": str(expanded.get("kits_sold") or "") if is_kit else "",
                "Quantidade por Kit": str(expanded.get("qty_per_kit") or "") if is_kit else "",
                "SkuKitOrigem": kit_sku,
                "Observação": warning,
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
                "NF": invoice_number,
                "Nota Fiscal": invoice_number,
                "Chave de Acesso": fiscal_key,
                "Status NF Mercado Livre": str(fiscal_state.get("fiscal_status") or "AGUARDANDO"),
                "Etiqueta API Mercado Livre": "VINCULADA" if fiscal_state.get("label_stored") else "AGUARDANDO",
                # V17.47: identidade da linha ML independe do SKU. O SKU pode
                # mudar/corrigir sem criar uma segunda venda no banco.
                "MercadoLivreLinhaKey": f"{order_id}|{item.get('id')}|{item.get('variation_id')}|{expanded_index}",
                "MercadoLivreItemId": str(item.get("id") or ""),
                "MercadoLivreVariationId": str(item.get("variation_id") or ""),
                "__uidLinha": "MLAPI_" + hashlib.sha1(
                    f"{order_id}|{item.get('id')}|{item.get('variation_id')}|{expanded_index}".encode("utf-8")
                ).hexdigest()[:20],
                "OrigemCadastro": source,
                "SkuOrigemImportacao": sku_raw,
                "VendaMercadoLivre": order_id,
                "PackIdMercadoLivre": pack_id,
                "ShipmentIdMercadoLivre": shipment_id,
                "StatusMercadoLivre": status,
                "LogisticaMercadoLivre": _ml_logistic_type(shipment),
                "NomeFiscalMercadoLivre": fiscal_name,
                "CPFMercadoLivre": fiscal_doc_number if fiscal_doc_type == "CPF" else "",
                "CNPJMercadoLivre": fiscal_doc_number if fiscal_doc_type == "CNPJ" else "",
                "DataLimiteDespachoMercadoLivre": date_for_dispatch,
                "MercadoLivreAtualizadoEm": now_iso(),
            }
            product = None
            for candidate in _ml_sku_lookup_candidates(sku):
                product = _ml_find_product(products, candidate)
                if product:
                    break
            if product:
                # O cadastro interno pode enriquecer EAN/localização, mas nunca
                # substituir o SKU real recebido do Mercado Livre.
                for field in ("Código de Barras","Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto"):
                    value = product.get(field)
                    if str(value or "").strip():
                        row[field] = value
            lines.append(row)

    # Consolida componentes repetidos do mesmo pedido/SKU. Isso também permite
    # somar um item avulso com o mesmo SKU de um componente de kit.
    consolidated: dict[str, dict[str, Any]] = {}
    for row in lines:
        key = normalize(row.get("SKU"))
        if not key:
            key = str(row.get("__uidLinha") or "")
        if key not in consolidated:
            consolidated[key] = row
            continue
        target = consolidated[key]
        target["Quantidade"] = str(int(target.get("Quantidade") or 0) + int(row.get("Quantidade") or 0))
        if row.get("Kit") == "SIM":
            target["Kit"] = "SIM"
            names = [str(target.get("Nome do Kit") or "").strip(), str(row.get("Nome do Kit") or "").strip()]
            target["Nome do Kit"] = " + ".join(dict.fromkeys(x for x in names if x))
        if row.get("Observação"):
            target["Observação"] = str(row.get("Observação"))
    lines = list(consolidated.values())

    if not lines:
        raise RuntimeError(f"Pedido {order_id} não trouxe itens.")

    if manual_kit_override:
        # Mantém EXATAMENTE SKU, Quantidade e metadados do kit corrigidos manualmente.
        # Atualiza apenas informações operacionais/fiscais vindas do Mercado Livre.
        inserted = 0
        updated = 0
        safe_fields = (
            "Marketplace","Data do Pedido","Cliente","Código de Rastreio",
            "NF","Nota Fiscal","Chave de Acesso","Status NF Mercado Livre",
            "Etiqueta API Mercado Livre","Observação"
        )
        api_fields = (
            "OrigemCadastro","VendaMercadoLivre","PackIdMercadoLivre",
            "ShipmentIdMercadoLivre","StatusMercadoLivre","LogisticaMercadoLivre",
            "NomeFiscalMercadoLivre","CPFMercadoLivre","CNPJMercadoLivre",
            "DataLimiteDespachoMercadoLivre","MercadoLivreAtualizadoEm"
        )

        # Dados operacionais são iguais por venda; usa a primeira linha importada
        # e preserva o cadastro manual de cada componente.
        reference = lines[0] if lines else {}
        for existing in pending:
            if not isinstance(existing, dict) or not _ml_same_pending_sale(existing, order_id, pack_id):
                continue
            if not _ml_row_manual_kit_override(existing):
                continue

            lido = int(existing.get("Lido") or 0)
            for field in safe_fields:
                value = reference.get(field)
                if str(value or "").strip():
                    existing[field] = value
            for field in api_fields:
                existing[field] = reference.get(field, existing.get(field, ""))

            # Reafirma explicitamente os campos protegidos.
            existing["KitManualOverride"] = "SIM"
            existing["Lido"] = lido
            if int(existing.get("Quantidade") or 0) < lido:
                existing["Quantidade"] = str(lido)
            updated += 1

        stale_removed = 0
    else:
        # Remove linhas antigas deste pedido que não existem mais na composição atual.
        # Isso permite cadastrar um kit depois e, na sincronização seguinte, substituir
        # a antiga linha KITxxxx pelos componentes corretos sem duplicação.
        desired_keys = {normalize(row.get("SKU")) for row in lines if normalize(row.get("SKU"))}
        cleaned_pending: list[dict[str, Any]] = []
        stale_removed = 0
        for existing in pending:
            if not isinstance(existing, dict):
                cleaned_pending.append(existing)
                continue
            same_ml_order = str(existing.get("VendaMercadoLivre") or "").strip() == order_id
            manual_sku = normalize(existing.get("SkuManualOverride")) == "sim"
            existing_sku = _ml_normalize_api_sku(str(existing.get("SKU") or existing.get("Código de Barras") or "").strip())
            source_sku = _ml_normalize_api_sku(str(existing.get("SkuOrigemImportacao") or "").strip())
            if same_ml_order and not manual_sku and normalize(existing_sku) not in desired_keys and normalize(source_sku) not in desired_keys:
                stale_removed += 1
                continue
            cleaned_pending.append(existing)
        pending = cleaned_pending

        # Atualiza pela combinação Pedido + SKU; quando não existe, cria nova linha.
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for existing in pending:
            existing_sku = str(existing.get("SKU") or existing.get("Código de Barras") or "").strip()
            is_ml_api_row = bool(
                str(existing.get("VendaMercadoLivre") or "").strip()
                or "mercado livre" in str(existing.get("OrigemCadastro") or "").lower()
            )
            if is_ml_api_row:
                existing_sku = _ml_normalize_api_sku(existing_sku)
            pedido_key = normalize(order_number(existing))
            keys = [normalize(existing_sku)]
            # Quando o SKU foi corrigido manualmente, o SKU original da API vira
            # a chave de reconciliação. Assim a sincronização encontra a mesma linha
            # sem desfazer a correção do operador.
            if normalize(existing.get("SkuManualOverride")) == "sim":
                original = _ml_normalize_api_sku(existing.get("SkuOrigemImportacao"))
                if normalize(original):
                    keys.append(normalize(original))
            for sku_key in keys:
                key = (pedido_key, sku_key)
                if sku_key and key not in by_key:
                    by_key[key] = existing

        inserted = updated = 0
        fields = (
            "Pedido","Marketplace","Data do Pedido","Cliente","SKU","Quantidade","Código de Barras",
            "Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto","Código de Rastreio",
            "NF","Nota Fiscal","Chave de Acesso","Status NF Mercado Livre","Etiqueta API Mercado Livre",
            "Kit","Nome do Kit","Quantidade de Kits","Quantidade por Kit","SkuKitOrigem","Observação"
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
            manual_sku = normalize(existing.get("SkuManualOverride")) == "sim"
            protected_when_manual = {"SKU","Código de Barras","Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto"}
            for field in fields:
                if manual_sku and field in protected_when_manual:
                    continue
                value = imported.get(field)
                if str(value or "").strip():
                    existing[field] = value
            for field in (
                "OrigemCadastro","VendaMercadoLivre","PackIdMercadoLivre",
                "ShipmentIdMercadoLivre","StatusMercadoLivre","LogisticaMercadoLivre",
                "NomeFiscalMercadoLivre","CPFMercadoLivre","CNPJMercadoLivre",
                "DataLimiteDespachoMercadoLivre","MercadoLivreAtualizadoEm",
                "MercadoLivreLinhaKey","MercadoLivreItemId","MercadoLivreVariationId"
            ):
                existing[field] = imported.get(field, "")
            # O SKU original da integração é mantido como referência de reconciliação.
            if not str(existing.get("SkuOrigemImportacao") or "").strip():
                existing["SkuOrigemImportacao"] = imported.get("SkuOrigemImportacao", "")
            existing["Lido"] = lido
            if int(existing.get("Quantidade") or 0) < lido:
                existing["Quantidade"] = str(lido)
            updated += 1

    # V14.18: o nome do cliente pertence ao pedido, não ao SKU.
    # Força a atualização em TODAS as linhas ligadas à mesma venda ML, mesmo quando
    # Pedido+SKU mudou (kit, SKU normalizado, linha manual antiga etc.). Isso evita
    # que uma linha velha preserve nickname/nome incorreto após o refresh.
    forced_client_rows = 0
    if client:
        for existing in pending:
            if not isinstance(existing, dict):
                continue
            same_order = str(existing.get("VendaMercadoLivre") or "").strip() == order_id
            same_pack = bool(pack_id) and str(existing.get("PackIdMercadoLivre") or "").strip() == pack_id
            if same_order or same_pack:
                if str(existing.get("Cliente") or "").strip() != client:
                    forced_client_rows += 1
                existing["Cliente"] = client
                existing["NomeFiscalMercadoLivre"] = fiscal_name
                if fiscal_doc_number:
                    existing["CPFMercadoLivre"] = fiscal_doc_number if fiscal_doc_type == "CPF" else ""
                    existing["CNPJMercadoLivre"] = fiscal_doc_number if fiscal_doc_type == "CNPJ" else ""
                existing["FonteNomeClienteMercadoLivre"] = client_name_source
                existing["MercadoLivreAtualizadoEm"] = now_iso()

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
        "invoice_available": bool(fiscal_state.get("invoice_available")),
        "invoice_unavailable_reason": str(fiscal_state.get("invoice_unavailable_reason") or ""),
        "fiscal_ready": bool(fiscal_state.get("fiscal_ready")),
        "fiscal_status": str(fiscal_state.get("fiscal_status") or ""),
        "label_stored": bool(fiscal_state.get("label_stored")),
        "ready_for_conference": bool(fiscal_state.get("ready_for_conference")),
        "shipment_substatus": str(fiscal_state.get("shipment_substatus") or ""),
        "label_error": str(fiscal_state.get("label_error") or ""),
        "fiscal_name": fiscal_name,
        "fiscal_name_updated": bool(fiscal_name),
        "buyer_real_name": buyer_real_name,
        "client_name": client,
        "client_name_source": client_name_source,
        "billing_info_id": billing_info_id,
        "billing_info_errors": billing_errors,
        "fiscal_name_available": bool(fiscal_name),
        "forced_client_rows": forced_client_rows,
        "kit_components_applied": kit_applied,
        "kit_unregistered": kit_unregistered,
        "stale_rows_removed": stale_removed,
        "manual_rows_bound": manual_rows_bound,
        "manual_kit_override_preserved": bool(manual_kit_override),
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
            if result.get("cancelled"):
                event_result, prefix = "CANCELADO / REMOVIDO DOS PENDENTES", ""
            elif result.get("ignored"):
                event_result, prefix = "FULL IGNORADO", ""
            elif result.get("ready_for_conference"):
                event_result, prefix = "PRONTO PARA CONFERÊNCIA", "Etiqueta vinculada automaticamente · "
            elif result.get("fiscal_status") == "PROCESSANDO":
                event_result, prefix = "NF LIBERADA / ETIQUETA AGUARDANDO", ""
            elif result.get("fiscal_name_updated"):
                event_result, prefix = "NOME FISCAL ATUALIZADO", ""
            else:
                event_result, prefix = "NF AGUARDANDO", ""
            ml_log_event(topic, resource, user_id, app_id, event_result, prefix + json_dumps(result))
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
            all_ignored = bool(results) and all(bool(item.get("ignored")) for item in results if isinstance(item, dict))
            any_ready = any(bool(item.get("ready_for_conference")) for item in results if isinstance(item, dict))
            any_fiscal = any(bool(item.get("fiscal_ready")) for item in results if isinstance(item, dict))
            if all_ignored:
                event_result, prefix = "FULL IGNORADO", ""
            elif any_ready:
                event_result, prefix = "PRONTO PARA CONFERÊNCIA", "Etiqueta vinculada automaticamente · "
            elif any_fiscal:
                event_result, prefix = "NF LIBERADA / ETIQUETA AGUARDANDO", ""
            else:
                event_result, prefix = "NF AGUARDANDO", ""
            ml_log_event(topic, resource, user_id, app_id, event_result, prefix + json_dumps(results))
            return
        ml_log_event(topic, resource, user_id, app_id, "IGNORADO", "Tópico/recurso sem ação configurada")
    except Exception as exc:
        ml_log_event(topic, resource, user_id, app_id, "ERRO", str(exc))
        with DB_LOCK:
            DB.execute("UPDATE ml_integration SET last_error=?,updated_at=? WHERE id=1", (str(exc)[:2000], now_iso()))
            DB.commit()


def _ml_all_pending_label_candidates() -> list[tuple[str, str]]:
    """Retorna todos os pares únicos ainda aguardando etiqueta."""
    raw, _, _ = get_storage("conf_pedidos")
    rows = _rows_from_storage_value(raw)
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        marketplace = normalize(row.get("Marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        if str(row.get("Etiqueta API Mercado Livre") or "").strip().upper() == "VINCULADA":
            continue
        if str(row.get("LogisticaMercadoLivre") or "").strip().lower() == "fulfillment":
            continue
        order_id = str(row.get("VendaMercadoLivre") or "").strip()
        shipment_id = str(row.get("ShipmentIdMercadoLivre") or "").strip()
        if not order_id or not shipment_id:
            continue
        key = (order_id, shipment_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(key)
    return candidates


def _ml_pending_label_candidates() -> list[tuple[str, str]]:
    """Retorna um lote circular; os primeiros 20 nunca bloqueiam os seguintes."""
    global ML_PENDING_WATCH_CURSOR
    all_candidates = _ml_all_pending_label_candidates()
    total = len(all_candidates)
    if total <= ML_PENDING_WATCH_BATCH:
        with ML_PENDING_WATCH_CURSOR_LOCK:
            ML_PENDING_WATCH_CURSOR = 0
        return all_candidates
    with ML_PENDING_WATCH_CURSOR_LOCK:
        start = ML_PENDING_WATCH_CURSOR % total
        count = min(ML_PENDING_WATCH_BATCH, total)
        batch = [all_candidates[(start + i) % total] for i in range(count)]
        ML_PENDING_WATCH_CURSOR = (start + count) % total
    return batch


def ml_pending_label_watch_once() -> dict[str, int]:
    """Reconsulta somente shipments pendentes; importa o pedido só quando há chance de etiqueta."""
    checked = triggered = ready = errors = 0
    for order_id, shipment_id in _ml_pending_label_candidates():
        checked += 1
        try:
            shipment = ml_api_get(
                f"/shipments/{quote(shipment_id)}?views=destination",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            if not isinstance(shipment, dict):
                continue
            status = str(shipment.get("status") or "").strip().lower()
            substatus = str(shipment.get("substatus") or "").strip().lower()

            # V16.36: se o shipment foi cancelado, reconsulta a order para retirar
            # imediatamente a venda da fila de pendentes.
            if status in {"cancelled", "canceled"}:
                triggered += 1
                result = ml_import_order(order_id, source="API Mercado Livre / watcher cancelamento")
                ml_log_event(
                    "shipment_watcher", f"/shipments/{shipment_id}",
                    "", ML_CLIENT_ID, "CANCELAMENTO PROCESSADO",
                    json_dumps({"order_id": order_id, "shipment_id": shipment_id,
                                "removed": result.get("removed_pending_rows", 0)})
                )
                continue

            # V14.4: invoice_pending pode estar atrasado em relação à disponibilidade
            # real da etiqueta. Para não fazer uma reimportação pesada desnecessária,
            # quando esse substatus aparece fazemos primeiro uma sondagem direta do ZPL.
            # Se o endpoint já devolver a etiqueta, reimportamos e vinculamos o pedido.
            if status == "ready_to_ship" and substatus == "invoice_pending":
                try:
                    if not _ml_fetch_shipping_zpl(shipment_id):
                        continue
                except Exception:
                    continue

            # Em ready_to_ship, reimporta: a rotina tenta o ZPL e só libera a
            # conferência quando o vínculo da etiqueta for realmente gravado.
            if status == "ready_to_ship":
                triggered += 1
                result = ml_import_order(order_id, source="API Mercado Livre / watcher etiqueta")
                if result.get("ready_for_conference"):
                    ready += 1
                    ml_log_event(
                        "shipment_watcher", f"/shipments/{shipment_id}",
                        "", ML_CLIENT_ID, "PRONTO PARA CONFERÊNCIA",
                        json_dumps({"order_id": order_id, "shipment_id": shipment_id,
                                    "substatus": substatus, "fiscal_name": result.get("fiscal_name") or ""})
                    )
                elif result.get("fiscal_name_updated"):
                    ml_log_event(
                        "shipment_watcher", f"/orders/{order_id}",
                        "", ML_CLIENT_ID, "NOME FISCAL ATUALIZADO",
                        json_dumps({"order_id": order_id, "shipment_id": shipment_id,
                                    "nome": result.get("fiscal_name") or ""})
                    )
        except Exception as exc:
            errors += 1
            ml_log_event(
                "shipment_watcher", f"/shipments/{shipment_id}",
                "", ML_CLIENT_ID, "ERRO", str(exc)[:1200]
            )
    return {"checked": checked, "triggered": triggered, "ready": ready, "errors": errors}


def _ml_recover_one_identifier(identifier: str, *, source: str) -> dict[str, Any]:
    """Trata o número como order, pack e busca q; importa todas as orders relacionadas."""
    ident = re.sub(r"\D+", "", str(identifier or ""))
    out: dict[str, Any] = {"identifier": ident, "orders": [], "imported": 0, "ready": 0, "errors": []}
    if not ident:
        return out
    order_ids: list[str] = []
    try:
        order = ml_api_get(f"/orders/{quote(ident)}")
        if isinstance(order, dict):
            oid = str(order.get("id") or "").strip()
            if oid:
                order_ids.append(oid)
    except Exception as exc:
        out["errors"].append(f"order:{str(exc)[:220]}")
    if not order_ids:
        try:
            _, _, pack_orders = _ml_pack_shipment(ident)
            order_ids.extend(x for x in pack_orders if x not in order_ids)
        except Exception as exc:
            out["errors"].append(f"pack:{str(exc)[:220]}")
    if not order_ids:
        try:
            _, seller_id = ml_access_token()
            query = urlencode({"seller": seller_id, "q": ident, "limit": "50", "offset": "0"})
            found = ml_api_get(f"/orders/search?{query}")
            for item in (found.get("results") if isinstance(found, dict) else []) or []:
                if not isinstance(item, dict):
                    continue
                oid = str(item.get("id") or "").strip()
                pid = str(item.get("pack_id") or "").strip()
                if ident in {oid, pid} and oid and oid not in order_ids:
                    order_ids.append(oid)
        except Exception as exc:
            out["errors"].append(f"search:{str(exc)[:220]}")
    out["orders"] = order_ids
    for oid in order_ids:
        try:
            detail = ml_import_order(oid, source=source)
            out["imported"] += 1
            if detail.get("ready_for_conference"):
                out["ready"] += 1
        except Exception as exc:
            out["errors"].append(f"import {oid}:{str(exc)[:300]}")
    return out


def _ml_recover_local_pending_identifiers_once(*, max_identifiers: int = 80) -> dict[str, Any]:
    """Recupera pedidos locais presos por IDs salvos em campos errados ou migração de API."""
    raw, _, _ = get_storage("conf_pedidos")
    linked = _ml_linked_order_ids()
    completed = _ml_completed_order_ids()
    identifiers: list[str] = []
    shipment_ids: list[str] = []
    for row in _rows_from_storage_value(raw):
        if not isinstance(row, dict):
            continue
        marketplace = normalize(row.get("Marketplace"))
        if "MERCADO LIVRE" not in marketplace and "MERCADOLIVRE" not in marketplace:
            continue
        oid = str(row.get("VendaMercadoLivre") or "").strip()
        if oid and (oid in linked or oid in completed):
            continue
        sid = re.sub(r"\D+", "", str(row.get("ShipmentIdMercadoLivre") or ""))
        if sid and sid not in shipment_ids:
            shipment_ids.append(sid)
        for raw_id in (order_number(row), oid, row.get("PackIdMercadoLivre")):
            ident = re.sub(r"\D+", "", str(raw_id or ""))
            if ident and ident not in identifiers:
                identifiers.append(ident)

    imported = ready = errors = resolved_by_shipment = 0
    details: list[dict[str, Any]] = []
    for sid in shipment_ids[:max_identifiers]:
        order_ids = _ml_orders_for_shipment(sid)
        if order_ids:
            resolved_by_shipment += 1
        for oid in order_ids:
            try:
                detail = ml_import_order(oid, source="API Mercado Livre / recuperação por shipment")
                imported += 1
                if detail.get("ready_for_conference"):
                    ready += 1
            except Exception as exc:
                errors += 1
                details.append({"shipment_id": sid, "order_id": oid, "error": str(exc)[:300]})
    for ident in identifiers[:max_identifiers]:
        result = _ml_recover_one_identifier(ident, source="API Mercado Livre / recuperação identificador local")
        imported += int(result.get("imported") or 0)
        ready += int(result.get("ready") or 0)
        if result.get("errors") and not result.get("orders"):
            errors += 1
        if result.get("orders") or result.get("errors"):
            details.append(result)
    summary = {"local_identifiers": len(identifiers), "local_shipments": len(shipment_ids),
               "resolved_by_shipment": resolved_by_shipment, "imported": imported,
               "ready": ready, "errors": errors, "details": details[:20]}
    try:
        ml_log_event("stuck_order_recovery", "/local-pending", "", ML_CLIENT_ID,
                     "RECUPERAÇÃO CONCLUÍDA", json_dumps(summary))
    except Exception:
        pass
    return summary


def _ml_discover_untracked_ready_labels_once(*, days: int | None = None, max_pages: int | None = None) -> dict[str, int]:
    """V14.11: descobre na própria API pedidos com etiqueta que ainda não existem localmente.

    A V14.10 conseguia recuperar apenas pedidos já presentes em conf_pedidos ou
    reconciliáveis por número. Aqui percorremos /orders/search dentro da janela
    configurada e inspecionamos somente pedidos cujo order_id ainda não está na
    base local/concluída. Se o shipment estiver ready_to_ship e o ZPL existir,
    importamos o pedido diretamente.
    """
    _, seller_id = ml_access_token()
    scan_days = max(1, min(90, int(days or ML_MANUAL_RECONCILE_DAYS)))
    page_limit = max(1, min(ML_CATCHUP_MAX_PAGES, int(max_pages or ML_CATCHUP_MAX_PAGES)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=scan_days)
    completed = _ml_completed_order_ids()
    linked_local = _ml_linked_order_ids()
    checked = discovered = available = linked = errors = 0
    stop_by_date = False

    for page in range(page_limit):
        query = urlencode({
            "seller": seller_id,
            "order.status": "paid",
            "sort": "date_desc",
            "limit": str(ML_CATCHUP_PAGE_SIZE),
            "offset": str(page * ML_CATCHUP_PAGE_SIZE),
        })
        result = ml_api_get(f"/orders/search?{query}")
        orders = result.get("results") if isinstance(result, dict) else []
        if not orders:
            break

        for order in orders:
            if not isinstance(order, dict):
                continue
            created = _ml_parse_datetime(order.get("date_created"))
            if created is not None and created < cutoff:
                stop_by_date = True
                break

            order_id = str(order.get("id") or "").strip()
            if not order_id or order_id in completed or order_id in linked_local:
                continue
            # Mesmo que o pedido já exista localmente, ele precisa ser reconsultado
            # quando ainda não possui etiqueta vinculada. Isso corrige linhas
            # incompletas que antes eram ignoradas para sempre.
            discovered += 1

            try:
                shipment_id, shipment = _ml_resolve_forward_shipment(order_id, order)

                checked += 1
                if not shipment_id or not isinstance(shipment, dict):
                    continue
                if str(shipment.get("status") or "").strip().lower() != "ready_to_ship":
                    continue

                try:
                    zpl = _ml_fetch_shipping_zpl(shipment_id)
                except Exception:
                    zpl = ""
                if not zpl:
                    continue
                available += 1

                detail = ml_import_order(order_id, source="API Mercado Livre / descoberta etiqueta não rastreada")
                if detail.get("ready_for_conference"):
                    linked_local.add(order_id)
                    linked += 1
                    ml_log_event(
                        "manual_label_discovery", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                        "PEDIDO DESCOBERTO / PRONTO PARA CONFERÊNCIA",
                        json_dumps({"order_id": order_id, "shipment_id": shipment_id})
                    )
            except Exception as exc:
                errors += 1
                ml_log_event(
                    "manual_label_discovery", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                    "ERRO", str(exc)[:1200]
                )

        if stop_by_date or len(orders) < ML_CATCHUP_PAGE_SIZE:
            break

    return {
        "api_discovered": discovered,
        "api_checked": checked,
        "api_available": available,
        "api_linked": linked,
        "api_errors": errors,
    }


def ml_check_all_available_labels_once() -> dict[str, int]:
    """Varredura manual completa usada pelo botão do Admin.

    V14.11: reconcilia os pedidos locais e também consulta /orders/search para
    encontrar pedidos pagos com etiqueta pronta que nunca chegaram à base local.
    """
    local_recovery: dict[str, Any] = {"imported": 0, "ready": 0, "errors": 0}
    try:
        local_recovery = _ml_recover_local_pending_identifiers_once()
    except Exception as exc:
        local_recovery = {"imported": 0, "ready": 0, "errors": 1, "details": [{"error": str(exc)[:400]}]}
        ml_log_event("manual_label_check", "/local-recovery", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])

    reconcile = {"manual": 0, "matched": 0, "ready": 0, "errors": 0}
    try:
        reconcile = ml_reconcile_manual_orders_once()
    except Exception as exc:
        reconcile = {"manual": 0, "matched": 0, "ready": 0, "errors": 1}
        ml_log_event("manual_label_check", "/reconcile", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])

    checked = triggered = ready = errors = 0
    for order_id, shipment_id in _ml_all_pending_label_candidates():
        checked += 1
        try:
            shipment = ml_api_get(
                f"/shipments/{quote(shipment_id)}?views=destination",
                headers={"x-format-new": "true", "X-Api-Version": "2"},
            )
            if not isinstance(shipment, dict):
                continue
            status = str(shipment.get("status") or "").strip().lower()
            if status != "ready_to_ship":
                continue
            try:
                zpl = _ml_fetch_shipping_zpl(shipment_id)
            except Exception:
                zpl = ""
            if not zpl:
                continue
            triggered += 1
            result = ml_import_order(order_id, source="API Mercado Livre / verificação manual etiquetas")
            if result.get("ready_for_conference"):
                ready += 1
        except Exception as exc:
            errors += 1
            ml_log_event("manual_label_check", f"/shipments/{shipment_id}", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])

    discovery = {"api_discovered": 0, "api_checked": 0, "api_available": 0, "api_linked": 0, "api_errors": 0}
    try:
        discovery = _ml_discover_untracked_ready_labels_once()
    except Exception as exc:
        discovery["api_errors"] = 1
        ml_log_event("manual_label_discovery", "/orders/search", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])

    total_errors = errors + int(reconcile.get("errors") or 0) + int(discovery.get("api_errors") or 0) + int(local_recovery.get("errors") or 0)
    summary = {
        "reconciled_pending": int(reconcile.get("manual") or 0),
        "reconciled_matched": int(reconcile.get("matched") or 0),
        "reconciled_ready": int(reconcile.get("ready") or 0),
        "checked": checked + int(discovery.get("api_checked") or 0),
        "available": triggered + int(discovery.get("api_available") or 0),
        "linked": ready + int(discovery.get("api_linked") or 0),
        "api_discovered": int(discovery.get("api_discovered") or 0),
        "local_recovery_imported": int(local_recovery.get("imported") or 0),
        "local_recovery_ready": int(local_recovery.get("ready") or 0),
        "errors": total_errors,
    }
    ml_log_event("manual_label_check", "/pending", "", ML_CLIENT_ID, "CONCLUÍDO", json_dumps(summary))
    return {"ok": total_errors == 0, **summary}


def ml_pending_label_watch_loop(stop_event: threading.Event) -> None:
    """Watcher de etiquetas locais + descoberta periódica direta na API.

    Além de revisar shipments já conhecidos, a cada ~60 s varre pedidos pagos
    recentes no Mercado Livre. Assim um pedido que entrou localmente incompleto
    (ou que perdeu webhook) volta a ser sincronizado quando a etiqueta surgir.
    """
    cycles = 0
    discovery_every = max(1, int(round(120 / max(1, ML_PENDING_WATCH_INTERVAL))))
    while not stop_event.wait(ML_PENDING_WATCH_INTERVAL):
        try:
            row = ml_integration_row()
            if not row or not str(row["access_token"] or "").strip():
                continue
            ml_pending_label_watch_once()
            cycles += 1
            if cycles % discovery_every == 0:
                try:
                    # Varredura automática curta: até 3 dias / 4 páginas (200 pedidos).
                    # A verificação manual continua usando a janela completa configurada.
                    recovery = _ml_recover_local_pending_identifiers_once(max_identifiers=30)
                    if recovery.get("ready"):
                        ml_log_event("shipment_watcher", "/local-recovery", "", ML_CLIENT_ID,
                                     "PEDIDOS PRESOS RECUPERADOS", json_dumps(recovery))
                    result = _ml_discover_untracked_ready_labels_once(days=3, max_pages=4)
                    if result.get("api_linked"):
                        ml_log_event("shipment_watcher", "/orders/search", "", ML_CLIENT_ID,
                                     "ETIQUETAS RECUPERADAS", json_dumps(result))
                except Exception as discovery_exc:
                    ml_log_event("shipment_watcher", "/orders/search", "", ML_CLIENT_ID,
                                 "ERRO DESCOBERTA", str(discovery_exc)[:1200])
        except Exception as exc:
            try:
                ml_log_event("shipment_watcher", "/pending", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])
            except Exception:
                pass


def _ml_linked_order_ids() -> set[str]:
    """Pedidos cuja etiqueta ML realmente existe no banco de etiquetas.

    Não confia apenas no texto VINCULADA da linha: versões antigas podiam deixar
    esse marcador sem um registro ZPL correspondente, fazendo o watcher ignorar
    o pedido indefinidamente.
    """
    label_raw, _, _ = get_storage("conf_etiquetas")
    try:
        label_bank = json.loads(label_raw) if isinstance(label_raw, str) and label_raw.strip() else {}
    except Exception:
        label_bank = {}
    registros = label_bank.get("registros") if isinstance(label_bank, dict) and isinstance(label_bank.get("registros"), dict) else {}
    valid_orders: set[str] = set()
    valid_shipments: set[str] = set()
    for rec in registros.values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("origem") or "").strip().lower() != "api_mercadolivre":
            continue
        envio = rec.get("envio")
        if not isinstance(envio, str) or not envio.strip():
            continue
        meta = rec.get("metadados") if isinstance(rec.get("metadados"), dict) else {}
        oid = str(meta.get("venda") or "").strip()
        sid = str(meta.get("shipment_id") or "").strip()
        if oid: valid_orders.add(oid)
        if sid: valid_shipments.add(sid)

    ids: set[str] = set()
    raw, _, _ = get_storage("conf_pedidos")
    for row in _rows_from_storage_value(raw):
        if not isinstance(row, dict):
            continue
        if str(row.get("Etiqueta API Mercado Livre") or "").strip().upper() != "VINCULADA":
            continue
        oid = str(row.get("VendaMercadoLivre") or "").strip()
        sid = str(row.get("ShipmentIdMercadoLivre") or "").strip()
        if oid and (oid in valid_orders or (sid and sid in valid_shipments)):
            ids.add(oid)
    return ids


def _ml_completed_order_ids() -> set[str]:
    """Pedidos já concluídos, que não devem voltar para pendentes."""
    ids: set[str] = set()
    with DB_LOCK:
        try:
            rows = DB.execute("SELECT payload FROM completed_orders").fetchall()
        except Exception:
            rows = []
    for dbrow in rows:
        try:
            payload = json.loads(dbrow["payload"] if hasattr(dbrow, "keys") else dbrow[0])
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for row in candidates:
            if isinstance(row, dict):
                oid = str(row.get("VendaMercadoLivre") or "").strip()
                if oid:
                    ids.add(oid)
    return ids


def _ml_existing_order_ids() -> set[str]:
    ids: set[str] = set()
    raw, _, _ = get_storage("conf_pedidos")
    for row in _rows_from_storage_value(raw):
        if isinstance(row, dict):
            oid = str(row.get("VendaMercadoLivre") or "").strip()
            if oid:
                ids.add(oid)
    # Pedidos já concluídos também não devem voltar para pendentes.
    with DB_LOCK:
        try:
            rows = DB.execute("SELECT payload FROM completed_orders").fetchall()
        except Exception:
            rows = []
    for dbrow in rows:
        try:
            payload = json.loads(dbrow["payload"] if hasattr(dbrow, "keys") else dbrow[0])
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for row in candidates:
            if isinstance(row, dict):
                oid = str(row.get("VendaMercadoLivre") or "").strip()
                if oid:
                    ids.add(oid)
    return ids


def _ml_parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        # Mercado Livre e o próprio banco podem usar Z ou offset ISO.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _ml_update_last_sync(marker: str, *, error: str = "") -> None:
    with DB_LOCK:
        DB.execute(
            "UPDATE ml_integration SET last_sync_at=?,updated_at=?,last_error=? WHERE id=1",
            (str(marker or now_iso()), now_iso(), str(error or "")[:3000]),
        )
        DB.commit()


def ml_catchup_orders(recovery_since: str = "", *, source: str = "reconexão") -> dict[str, Any]:
    """Recupera pedidos pagos perdidos desde o último ponto confiável.

    Pagina /orders/search até alcançar recovery_since. O mesmo lock do watcher
    impede que o poller de 20 s importe em paralelo durante a recuperação.
    """
    if not ML_CATCHUP_LOCK.acquire(blocking=False):
        return {"ok": True, "running": True, "imported": 0, "ignored_full": 0, "errors": []}

    watcher_locked = False
    scan_started_at = now_iso()
    try:
        ML_NEW_ORDER_WATCH_LOCK.acquire()
        watcher_locked = True

        _, seller_id = ml_access_token()
        since_dt = _ml_parse_datetime(recovery_since)
        if since_dt is None:
            row = ml_integration_row()
            since_dt = _ml_parse_datetime(row["last_sync_at"] if row else "")
        if since_dt is None:
            since_dt = datetime.now(timezone.utc) - timedelta(days=ML_CATCHUP_FALLBACK_DAYS)

        # Pequena sobreposição evita perder pedidos exatamente na borda do marcador.
        since_dt = since_dt - timedelta(minutes=5)
        existing = _ml_existing_order_ids()

        imported = 0
        ignored_full = 0
        checked = 0
        errors: list[dict[str, str]] = []
        reached_marker = False
        exhausted = False

        for page in range(ML_CATCHUP_MAX_PAGES):
            offset = page * ML_CATCHUP_PAGE_SIZE
            query = urlencode({
                "seller": seller_id,
                "order.status": "paid",
                "sort": "date_desc",
                "limit": str(ML_CATCHUP_PAGE_SIZE),
                "offset": str(offset),
            })
            result = ml_api_get(f"/orders/search?{query}")
            orders = result.get("results") if isinstance(result, dict) else []
            if not orders:
                exhausted = True
                break

            for order in orders:
                if not isinstance(order, dict):
                    continue
                created_dt = _ml_parse_datetime(order.get("date_created"))
                if created_dt is not None and created_dt < since_dt:
                    reached_marker = True
                    break

                order_id = str(order.get("id") or "").strip()
                if not order_id:
                    continue
                checked += 1
                if order_id in existing:
                    continue
                try:
                    detail = ml_import_order(order_id, source=f"API Mercado Livre / catch-up {source}")
                    if detail.get("ignored") and detail.get("reason") == "FULL":
                        ignored_full += 1
                        existing.add(order_id)
                        ml_log_event(
                            "catchup", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                            "FULL IGNORADO", json_dumps({"order_id": order_id, "source": source})
                        )
                    else:
                        imported += 1
                        existing.add(order_id)
                        ml_log_event(
                            "catchup", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                            "PEDIDO RECUPERADO",
                            json_dumps({
                                "order_id": order_id,
                                "source": source,
                                "ready_for_conference": bool(detail.get("ready_for_conference")),
                                "fiscal_name": detail.get("fiscal_name") or "",
                            })
                        )
                except Exception as exc:
                    errors.append({"order_id": order_id, "error": str(exc)[:700]})

            if reached_marker:
                break
            if len(orders) < ML_CATCHUP_PAGE_SIZE:
                exhausted = True
                break

        completed_scan = reached_marker or exhausted
        if completed_scan and not errors:
            _ml_update_last_sync(scan_started_at)
        else:
            # Mantém o marcador antigo para uma futura tentativa não saltar pedidos.
            with DB_LOCK:
                DB.execute(
                    "UPDATE ml_integration SET updated_at=?,last_error=? WHERE id=1",
                    (
                        now_iso(),
                        "" if not errors else json_dumps(errors[:5]),
                    ),
                )
                DB.commit()

        result_payload = {
            "ok": completed_scan and not errors,
            "checked": checked,
            "imported": imported,
            "ignored_full": ignored_full,
            "errors": errors,
            "reached_marker": reached_marker,
            "exhausted": exhausted,
            "since": since_dt.isoformat(),
            "scan_started_at": scan_started_at,
        }
        ml_log_event(
            "catchup", "/orders/search", seller_id, ML_CLIENT_ID,
            "CATCH-UP CONCLUÍDO" if result_payload["ok"] else "CATCH-UP PARCIAL",
            json_dumps(result_payload),
        )
        return result_payload
    except Exception as exc:
        try:
            ml_log_event("catchup", "/orders/search", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])
        except Exception:
            pass
        return {"ok": False, "checked": 0, "imported": 0, "ignored_full": 0, "errors": [{"error": str(exc)}]}
    finally:
        if watcher_locked:
            ML_NEW_ORDER_WATCH_LOCK.release()
        ML_CATCHUP_LOCK.release()


def ml_catchup_after_reconnect(recovery_since: str = "") -> None:
    # Executa fora do callback HTTP para devolver o navegador imediatamente.
    time.sleep(0.5)
    ml_catchup_orders(recovery_since, source="reconexão OAuth")



def ml_safety_catchup_once() -> dict[str, Any]:
    """Varredura de segurança independente do last_sync.

    Reconsulta uma janela fixa recente para recuperar pedidos que o webhook/poller
    não tenha importado. Como pedidos já existentes são ignorados, a rotina é
    idempotente e pode rodar 24/7 no servidor.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=ML_SAFETY_CATCHUP_HOURS)).isoformat()
    result = ml_catchup_orders(since, source=f"segurança {ML_SAFETY_CATCHUP_HOURS}h")
    _ml_worker_state_update(
        last_catchup_at=now_iso(),
        last_catchup_result=result,
        last_error="" if result.get("ok") else str((result.get("errors") or [{}])[0].get("error") or "catch-up parcial")[:1200],
    )
    return result


def ml_safety_catchup_loop(stop_event: threading.Event) -> None:
    # Após o boot, deixa o poller curto rodar primeiro e então recupera a janela maior.
    if stop_event.wait(20):
        return
    while not stop_event.is_set():
        try:
            row = ml_integration_row()
            if row and str(row["access_token"] or "").strip():
                ml_safety_catchup_once()
        except Exception as exc:
            _ml_worker_state_update(last_catchup_at=now_iso(), last_error=str(exc)[:1200])
            try:
                ml_log_event("safety_catchup", "/orders/search", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])
            except Exception:
                pass
        if stop_event.wait(ML_SAFETY_CATCHUP_INTERVAL):
            break


def ml_new_order_watch_once() -> dict[str, int]:
    """Contingência do webhook: busca somente os pedidos pagos mais recentes."""
    poll_started_at = now_iso()
    if not ML_NEW_ORDER_WATCH_LOCK.acquire(blocking=False):
        return {"checked": 0, "imported": 0, "ignored_full": 0, "errors": 0}
    try:
        _, seller_id = ml_access_token()
        query = urlencode({
            "seller": seller_id,
            "order.status": "paid",
            "sort": "date_desc",
            "limit": str(ML_NEW_ORDER_WATCH_LIMIT),
            "offset": "0",
        })
        result = ml_api_get(f"/orders/search?{query}")
        orders = result.get("results") if isinstance(result, dict) else []
        completed = _ml_completed_order_ids()
        linked_local = _ml_linked_order_ids()
        local_or_completed = _ml_existing_order_ids()
        checked = imported = refreshed_pending = ignored_full = errors = 0
        for order in orders or []:
            if not isinstance(order, dict):
                continue
            order_id = str(order.get("id") or "").strip()
            if not order_id:
                continue
            checked += 1
            if order_id in completed or order_id in linked_local:
                continue
            try:
                # Pedido já existe localmente, mas ainda sem etiqueta: não fazemos
                # uma reimportação pesada a cada 20 s. Só reimportamos quando o
                # shipment estiver ready_to_ship e o ZPL estiver realmente liberado.
                if order_id in local_or_completed:
                    shipment_id, shipment = _ml_resolve_forward_shipment(order_id, order)
                    if not shipment_id or not isinstance(shipment, dict):
                        continue
                    if str(shipment.get("status") or "").strip().lower() != "ready_to_ship":
                        continue
                    try:
                        if not _ml_fetch_shipping_zpl(shipment_id):
                            continue
                    except Exception:
                        continue
                detail = ml_import_order(order_id, source="API Mercado Livre / entrada automática")
                if detail.get("ignored") and detail.get("reason") == "FULL":
                    ignored_full += 1
                    ml_log_event("new_order_watcher", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                                 "FULL IGNORADO", json_dumps({"order_id": order_id}))
                else:
                    imported += 1
                    if detail.get("ready_for_conference"):
                        linked_local.add(order_id)
                    else:
                        refreshed_pending += 1
                    ml_log_event("new_order_watcher", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                                 "PEDIDO IMPORTADO/ATUALIZADO",
                                 json_dumps({"order_id": order_id,
                                             "ready_for_conference": bool(detail.get("ready_for_conference")),
                                             "fiscal_name": detail.get("fiscal_name") or "",
                                             "kit_components_applied": detail.get("kit_components_applied") or 0}))
            except Exception as exc:
                errors += 1
                ml_log_event("new_order_watcher", f"/orders/{order_id}", seller_id, ML_CLIENT_ID,
                             "ERRO", str(exc)[:1200])
        if errors == 0:
            _ml_update_last_sync(poll_started_at)
        return {"checked": checked, "imported": imported, "refreshed_pending": refreshed_pending, "ignored_full": ignored_full, "errors": errors}
    finally:
        ML_NEW_ORDER_WATCH_LOCK.release()


def ml_new_order_watch_loop(stop_event: threading.Event) -> None:
    """Busca pedidos novos periodicamente quando webhook orders_v2 atrasar/falhar."""
    consecutive_errors = 0
    # Faz uma primeira busca logo após o servidor subir.
    while not stop_event.is_set():
        try:
            row = ml_integration_row()
            if row and str(row["access_token"] or "").strip():
                result = ml_new_order_watch_once()
                _ml_worker_state_update(
                    last_order_poll_at=now_iso(),
                    last_order_poll_result=result,
                    last_error="" if not result.get("errors") else f"poller: {result.get('errors')} erro(s)",
                )
                consecutive_errors = 0 if not result.get("errors") else min(consecutive_errors + 1, 4)
            else:
                consecutive_errors = 0
        except Exception as exc:
            consecutive_errors = min(consecutive_errors + 1, 4)
            _ml_worker_state_update(last_order_poll_at=now_iso(), last_error=str(exc)[:1200])
            try:
                ml_log_event("new_order_watcher", "/orders/search", "", ML_CLIENT_ID, "ERRO", str(exc)[:1200])
            except Exception:
                pass
        wait_seconds = min(120, ML_NEW_ORDER_WATCH_INTERVAL * (2 ** consecutive_errors))
        if stop_event.wait(wait_seconds):
            break


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
    ignored_full = []
    errors = []
    for order in orders or []:
        order_id = str(order.get("id") or "") if isinstance(order, dict) else ""
        if not order_id:
            continue
        try:
            detail = ml_import_order(order_id, source="API Mercado Livre / sincronização manual")
            if detail.get("ignored") and detail.get("reason") == "FULL":
                ignored_full.append(detail)
            else:
                imported.append(detail)
        except Exception as exc:
            errors.append({"order_id": order_id, "error": str(exc)})
    with DB_LOCK:
        DB.execute(
            "UPDATE ml_integration SET last_sync_at=?,updated_at=?,last_error=? WHERE id=1",
            (now_iso(), now_iso(), (json_dumps(errors[:5]) if errors else "")),
        )
        DB.commit()
    ml_log_event(
        "manual_sync", "/orders/search", seller_id, ML_CLIENT_ID,
        "OK" if not errors else "PARCIAL",
        json_dumps({
            "imported": len(imported),
            "ignored_full": len(ignored_full),
            "ready_for_conference": sum(1 for item in imported if item.get("ready_for_conference")),
            "label_waiting": sum(1 for item in imported if item.get("fiscal_status") == "PROCESSANDO" and not item.get("ready_for_conference")),
            "nf_waiting": sum(1 for item in imported if item.get("fiscal_status") == "AGUARDANDO"),
            "errors": errors[:10],
        })
    )
    return {
        "ok": not errors,
        "imported": len(imported),
        "ignored_full": len(ignored_full),
        "processed": len(imported) + len(ignored_full),
        "ready_for_conference": sum(1 for item in imported if item.get("ready_for_conference")),
        "label_waiting": sum(1 for item in imported if item.get("fiscal_status") == "PROCESSANDO" and not item.get("ready_for_conference")),
        "nf_waiting": sum(1 for item in imported if item.get("fiscal_status") == "AGUARDANDO"),
        "errors": errors,
        "details": imported,
        "ignored_details": ignored_full,
    }



# ---------------------------------------------------------------------------
# V17.83 — Vendas do período: o que entrou dos marketplaces, item a item.
#
# Serve à Central de Pedidos do Dia. A agregação é feita no SERVIDOR porque ela
# precisa enxergar duas fontes ao mesmo tempo — a fila atual (conf_pedidos) e o
# que já foi concluído (completed_orders.payload) — e porque o cruzamento futuro
# com o custo do Apollo só pode acontecer aqui: a CHAVE_CRM nunca vai ao
# navegador. Somente leitura: nenhuma rota daqui grava.
# ---------------------------------------------------------------------------

# V17.84 — documento fiscal do comprador, para localizar o cliente no Linx DMS.
# Cada marketplace guarda o dado com um nome próprio; quem consome não precisa saber.
DOCUMENTO_CAMPOS_CPF = ("CPFMercadoLivre", "CPFShopee", "CPF", "Cpf", "cpf")
DOCUMENTO_CAMPOS_CNPJ = ("CNPJMercadoLivre", "CNPJShopee", "CNPJ", "Cnpj", "cnpj")


def documento_do_pedido(row: dict[str, Any]) -> dict[str, str]:
    """Devolve {"tipo": "CPF"|"CNPJ"|"", "numero": "<só dígitos>"}.

    O Linx DMS localiza o cliente por documento (ProcurarCliente/CnpjCpf), então
    esta é a chave que liga um pedido de marketplace ao cadastro do ERP.
    """
    if not isinstance(row, dict):
        return {"tipo": "", "numero": ""}
    for campos, tipo in ((DOCUMENTO_CAMPOS_CNPJ, "CNPJ"), (DOCUMENTO_CAMPOS_CPF, "CPF")):
        for campo in campos:
            numero = re.sub(r"\D", "", str(row.get(campo) or ""))
            if numero:
                # O comprimento manda: campo trocado é erro comum de integração.
                if len(numero) == 14:
                    return {"tipo": "CNPJ", "numero": numero}
                if len(numero) == 11:
                    return {"tipo": "CPF", "numero": numero}
                return {"tipo": tipo, "numero": numero}
    return {"tipo": "", "numero": ""}


# V17.84 — nome fiscal: o que está em "Dados para a NF-e" do marketplace.
# É o nome que DEVE ir para o cadastro do Linx DMS. Nunca o apelido da conta,
# nunca o destinatário da entrega — eles divergem com frequência, e o cadastro
# do ERP precisa bater com a nota.
NOME_FISCAL_CAMPOS = ("NomeFiscalMercadoLivre", "NomeFiscalShopee", "NomeFiscal")


def nome_fiscal_do_pedido(row: dict[str, Any]) -> str:
    """Nome de Dados para NF-e. Vazio quando o marketplace ainda não devolveu.

    Devolve vazio de propósito em vez de cair para `Cliente`: `Cliente` pode
    conter o nome do comprador da conta, e gravar isso no ERP produziria uma
    nota no nome errado. Quem chama decide o que fazer com o vazio.
    """
    if not isinstance(row, dict):
        return ""
    # V17.88: pedidos Shopee importados antes da correção têm o nome de
    # usuário gravado em NomeFiscalShopee. Usuário nunca é nome fiscal.
    usuarios = {normalize(row.get(c)) for c in ("UsuarioShopee", "UsuarioMercadoLivre") if str(row.get(c) or "").strip()}
    for campo in NOME_FISCAL_CAMPOS:
        nome = str(row.get(campo) or "").strip()
        if nome and normalize(nome) in usuarios:
            continue
        # normalize() também remove os espaços, então o marcador da Shopee
        # precisa passar pela mesma função dos dois lados da comparação.
        if nome and normalize(nome) != normalize("NOME NÃO DISPONÍVEL"):
            return nome
    return ""


# V17.85 — botão "Cliente" da Central de Pedidos do Dia.
# O servidor NÃO fala com o Linx DMS (a VPS não enxerga a rede do escritório).
# Ele só entrega CPF/CNPJ e nome de "Dados para a NF-e" do pedido; quem grava
# no Linx é a extensão "Noromak - Cadastro de Cliente no Linx", no Chrome de
# quem clicou. Somente leitura: nada aqui grava no banco.
CLIENTE_FISCAL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CLIENTE_FISCAL_CACHE_SEGUNDOS = 600
CLIENTE_FISCAL_CACHE_MAX = 5000


def _linhas_do_pedido(pedido: str) -> list[dict[str, Any]]:
    """Linhas do pedido na fila atual; se já foi concluído, as do snapshot."""
    alvo = normalize(pedido)
    if not alvo:
        return []
    pendentes, _, _ = get_storage("conf_pedidos")
    linhas = [
        row for row in _rows_from_storage_value(pendentes)
        if isinstance(row, dict) and normalize(order_number(row)) == alvo
    ]
    if linhas:
        return linhas
    for db_row in DB.execute("SELECT order_key,payload FROM completed_orders").fetchall():
        if normalize(db_row["order_key"]) != alvo:
            continue
        try:
            payload = json.loads(str(db_row["payload"] or "[]"))
        except Exception:
            continue
        return [r for r in (payload if isinstance(payload, list) else [payload]) if isinstance(r, dict)]
    return []


def _ml_billing_do_pedido(order_id: str) -> dict[str, Any]:
    """Relê Dados para NF-e direto da API do ML. Mesmo caminho do import
    (ml_import_order), inclusive o contrato v2 quando o padrão vem sem billing."""
    order = ml_api_get(f"/orders/{quote(order_id)}")
    if not isinstance(order, dict):
        return {}
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    billing = buyer.get("billing_info")
    billing_id = (billing.get("id") if isinstance(billing, dict) else billing) or ""
    if not str(billing_id).strip():
        try:
            order_v2 = ml_api_get(f"/orders/{quote(order_id)}", headers={"X-Api-Version": "2"})
            if isinstance(order_v2, dict):
                buyer_v2 = order_v2.get("buyer") if isinstance(order_v2.get("buyer"), dict) else {}
                if buyer_v2.get("billing_info"):
                    order["buyer"] = {**buyer, "billing_info": buyer_v2.get("billing_info")}
        except Exception:
            pass
    return _ml_billing_details(order)


def _nome_fiscal_valido(nome: str) -> bool:
    """Pelo menos duas palavras e quase sem número. Barra texto de tela lido
    por engano (a extensão já pegou "Observações" uma vez, em 18/09/2026)."""
    nome = str(nome or "").strip()
    if not (5 <= len(nome) <= 150) or len(re.findall(r"\d", nome)) > 2:
        return False
    palavras = [p for p in nome.split() if re.search(r"[A-Za-zÀ-ÿ]{2,}", p)]
    return len(palavras) >= 2


def salvar_cliente_fiscal(pedido: str, documento: str, nome: str, *, linx_cliente: str = "",
                          fonte: str = "", usuario: str = "") -> dict[str, Any]:
    """V17.86 — guarda o que a extensão leu na venda (e o código no Linx)."""
    pedido = str(pedido or "").strip()
    numero = re.sub(r"\D", "", str(documento or ""))
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()
    linx_cliente = re.sub(r"\D", "", str(linx_cliente or ""))
    if not pedido:
        return {"ok": False, "error": "Informe o pedido."}
    if len(numero) not in (11, 14):
        return {"ok": False, "error": "CPF precisa ter 11 dígitos e CNPJ, 14."}
    if not _nome_fiscal_valido(nome):
        return {"ok": False, "error": "Nome da NF-e inválido: precisa de nome e sobrenome."}
    with DB_LOCK:
        if not _linhas_do_pedido(pedido):
            return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
        agora = now_iso()
        DB.execute(
            """INSERT INTO cliente_fiscal(order_key,pedido,documento_tipo,documento_numero,nome_fiscal,
                   fonte,linx_cliente,linx_atualizado_em,atualizado_em,atualizado_por)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(order_key) DO UPDATE SET
                   pedido=excluded.pedido, documento_tipo=excluded.documento_tipo,
                   documento_numero=excluded.documento_numero, nome_fiscal=excluded.nome_fiscal,
                   fonte=excluded.fonte,
                   linx_cliente=CASE WHEN excluded.linx_cliente<>'' THEN excluded.linx_cliente ELSE cliente_fiscal.linx_cliente END,
                   linx_atualizado_em=CASE WHEN excluded.linx_cliente<>'' THEN excluded.linx_atualizado_em ELSE cliente_fiscal.linx_atualizado_em END,
                   atualizado_em=excluded.atualizado_em, atualizado_por=excluded.atualizado_por""",
            (normalize(pedido), pedido, "CNPJ" if len(numero) == 14 else "CPF", numero, nome,
             str(fonte or "")[:40], linx_cliente, agora if linx_cliente else "", agora, str(usuario or "")[:80]),
        )
        DB.commit()
    # V17.87: o nome da NF-e vira o Nome Conferência, se ainda não houver um.
    conferencia = preencher_nome_conferencia_se_vazio(pedido, nome, actor=str(usuario or "extensao-nfe"))
    return {"ok": True, "pedido": pedido, "conferencia_preenchida": conferencia}


CAMPOS_NOME_CONFERENCIA = ("Nome Conferência", "NomeConferencia", "ConferenciaNome", "Conferência")


def preencher_nome_conferencia_se_vazio(pedido: str, nome: str, actor: str = "extensao-nfe") -> int:
    """V17.87 — Nome Conferência = nome de "Dados para a NF-e", só onde está vazio.

    Diferente de /api/order-conference-name (V17.73), que é ação humana e cria
    override ADMIN: aqui é preenchimento automático, então NUNCA sobrescreve um
    nome já existente e NÃO cria override — quem quiser pode corrigir à mão
    depois. preserve_conference_names() já impede que a sincronização limpe o
    nome preenchido. Devolve quantas linhas foram preenchidas.
    """
    nome = " ".join(str(nome or "").split())
    pedido_key = normalize(pedido)
    if not pedido_key or not _nome_fiscal_valido(nome) or len(nome) > 180:
        return 0
    preenchidas = 0
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            for storage_key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                raw_row = DB.execute("SELECT value,version FROM storage WHERE key=?", (storage_key,)).fetchone()
                if not raw_row:
                    continue
                rows = json.loads(str(raw_row["value"] or "[]"))
                if not isinstance(rows, list):
                    continue
                linhas = [r for r in rows if isinstance(r, dict) and normalize(order_number(r)) == pedido_key]
                # Se QUALQUER linha do pedido já tem nome, o pedido já tem dono: não mexe.
                if not linhas or any(str(r.get(c) or "").strip() for r in linhas for c in CAMPOS_NOME_CONFERENCIA):
                    continue
                for r in linhas:
                    for c in CAMPOS_NOME_CONFERENCIA:
                        r[c] = nome
                    r["NomeConferenciaFonte"] = "nfe_extensao"
                    preenchidas += 1
                DB.execute(
                    "UPDATE storage SET value=?,version=?,updated_at=? WHERE key=?",
                    (json_dumps(rows), int(raw_row["version"] or 0) + 1, now_iso(), storage_key),
                )
            if preenchidas:
                map_row = DB.execute(
                    "SELECT value,version FROM storage WHERE key='conf_nomes_conferencia_v1'"
                ).fetchone()
                try:
                    mapa = json.loads(str(map_row["value"] or "{}")) if map_row else {}
                except Exception:
                    mapa = {}
                if isinstance(mapa, dict) and not str(mapa.get(pedido_key) or "").strip():
                    mapa[pedido_key] = nome
                    if map_row:
                        DB.execute(
                            "UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_nomes_conferencia_v1'",
                            (json_dumps(mapa), int(map_row["version"] or 0) + 1, now_iso()),
                        )
                    else:
                        DB.execute(
                            "INSERT INTO storage(key,value,version,updated_at) VALUES('conf_nomes_conferencia_v1',?,?,?)",
                            (json_dumps(mapa), 1, now_iso()),
                        )
            DB.commit()
        except Exception:
            DB.rollback()
            raise
    if preenchidas:
        audit(actor, "order.conference_name.auto_nfe", f"{pedido}|{nome}")
    return preenchidas


def _cliente_fiscal_guardado(pedido: str) -> dict[str, Any] | None:
    with DB_LOCK:
        row = DB.execute("SELECT * FROM cliente_fiscal WHERE order_key=?", (normalize(pedido),)).fetchone()
    return dict(row) if row else None


def pedidos_sem_nome_fiscal(dias: int = 10, limite: int = 40) -> dict[str, Any]:
    """V17.93 — pedidos pendentes do Mercado Livre e da Shopee que ainda não têm
    nome/CPF da nota guardados. A extensão lê esta lista em segundo plano, sem
    depender de uma aba do admin aberta (a leitura pela tela parava em silêncio
    quando a aba era fechada, congelada ou não recarregada)."""
    corte = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    with DB_LOCK:
        pendentes, _, _ = get_storage("conf_pedidos")
        feitos = {
            r["order_key"] for r in DB.execute(
                "SELECT order_key FROM cliente_fiscal WHERE nome_fiscal<>'' AND documento_numero<>''"
            ).fetchall()
        }
    saida: list[dict[str, str]] = []
    vistos: set[str] = set()
    for row in _rows_from_storage_value(pendentes):
        if not isinstance(row, dict):
            continue
        marketplace = _vendas_marketplace(row.get("Marketplace") or row.get("Origem"))
        if marketplace not in ("Mercado Livre", "Shopee"):
            continue
        pedido = str(order_number(row) or "").strip()
        chave = normalize(pedido)
        if not pedido or chave in vistos or chave in feitos:
            continue
        if _vendas_data_iso(row.get("Data do Pedido")) < corte:
            continue
        vistos.add(chave)
        saida.append({"pedido": pedido, "marketplace": marketplace})
        if len(saida) >= limite:
            break
    return {"ok": True, "pedidos": saida}


def lista_cliente_fiscal(dias: int = 45) -> dict[str, Any]:
    """Tudo o que já foi lido nos últimos dias, por pedido. Para a Central."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    with DB_LOCK:
        rows = DB.execute(
            "SELECT * FROM cliente_fiscal WHERE atualizado_em>=? ORDER BY atualizado_em", (limite,)
        ).fetchall()
    return {"ok": True, "clientes": {
        r["pedido"]: {
            "documento": {"tipo": r["documento_tipo"], "numero": r["documento_numero"]},
            "nome_fiscal": r["nome_fiscal"],
            "linx_cliente": r["linx_cliente"],
            "linx_atualizado_em": r["linx_atualizado_em"],
            # V18.60: consulta automática na Linx Smart API (só leitura). Resultado
            # de outro documento (CPF corrigido depois) não vale: volta a pendente.
            "linx_api": {
                "status": r["linx_api_status"] if r["linx_api_documento"] == r["documento_numero"] else "",
                "cliente": r["linx_api_cliente"],
                "nome": r["linx_api_nome"],
                "erro": r["linx_api_erro"],
                "consultado_em": r["linx_api_consultado_em"],
                "credito": _json_obj(r["linx_api_credito"]),
            },
        } for r in rows
    }}


def cliente_fiscal_do_pedido(pedido: str) -> dict[str, Any]:
    """CPF/CNPJ e nome fiscal de um pedido, para atualizar o cliente no Linx.

    1. O que o pedido já guarda (V17.84 em diante).
    2. Mercado Livre sem o dado guardado (pedidos anteriores à V17.84): relê o
       billing-info na API do ML. Nunca cai para apelido nem para quem recebe.
    """
    pedido = str(pedido or "").strip()
    with DB_LOCK:
        linhas = _linhas_do_pedido(pedido)
    if not linhas:
        return {"ok": False, "error": f"Pedido {pedido} não encontrado."}

    marketplace = _vendas_marketplace(linhas[0].get("Marketplace") or linhas[0].get("Origem"))
    documento = {"tipo": "", "numero": ""}
    nome = ""
    for row in linhas:
        if not documento["numero"]:
            documento = documento_do_pedido(row)
        if not nome:
            nome = nome_fiscal_do_pedido(row)
    fonte = "pedido" if (documento["numero"] and nome) else ""
    aviso = ""
    linx_cliente = ""

    # V17.86: o que a extensão já leu na página da venda vale mais que o pedido
    # (que a sincronização do ML reescreve com vazio) e evita outra leitura.
    guardado = _cliente_fiscal_guardado(pedido)
    if guardado:
        linx_cliente = guardado.get("linx_cliente") or ""
        if guardado.get("documento_numero") and guardado.get("nome_fiscal"):
            documento = {"tipo": guardado["documento_tipo"], "numero": guardado["documento_numero"]}
            nome = guardado["nome_fiscal"]
            fonte = "noromak"

    if (not documento["numero"] or not nome) and marketplace == "Mercado Livre":
        order_id = ""
        for row in linhas:
            order_id = re.sub(r"\D", "", str(row.get("VendaMercadoLivre") or ""))
            if order_id:
                break
        order_id = order_id or re.sub(r"\D", "", pedido)
        agora = time.time()
        guardado = CLIENTE_FISCAL_CACHE.get(order_id)
        if guardado and agora - guardado[0] < CLIENTE_FISCAL_CACHE_SEGUNDOS:
            billing = guardado[1]
        else:
            try:
                billing = _ml_billing_do_pedido(order_id) if order_id else {}
            except Exception as exc:
                billing = {}
                aviso = f"Não consegui ler Dados para a NF-e no Mercado Livre: {str(exc)[:200]}"
            if billing.get("name") or billing.get("doc_number"):
                CLIENTE_FISCAL_CACHE[order_id] = (agora, billing)
                _cache_podar(CLIENTE_FISCAL_CACHE, CLIENTE_FISCAL_CACHE_SEGUNDOS, CLIENTE_FISCAL_CACHE_MAX)
        numero_api = re.sub(r"\D", "", str(billing.get("doc_number") or ""))
        if not documento["numero"] and len(numero_api) in (11, 14):
            documento = {"tipo": "CNPJ" if len(numero_api) == 14 else "CPF", "numero": numero_api}
        if not nome:
            nome = str(billing.get("name") or "").strip()
        if documento["numero"] or nome:
            fonte = "mercado_livre"

    if not aviso and marketplace == "Shopee" and not documento["numero"]:
        aviso = "A Shopee não informa o CPF do comprador. Digite o CPF e o nome da nota."
    elif not aviso and (not documento["numero"] or not nome):
        aviso = "Faltou dado da NF-e neste pedido. Confira e complete antes de consultar."

    return {
        "ok": True,
        "pedido": pedido,
        "marketplace": marketplace,
        "documento": documento,
        "nome_fiscal": nome,
        "fonte": fonte,
        "aviso": aviso,
        "linx_cliente": linx_cliente,
    }


# ---------------------------------------------------------------------------
# V18.60 — Linx Smart API (a API oficial do Linx DMS, na nuvem da Linx).
# Etapa 1: conexão + "este cliente já está cadastrado no Linx?".
#
# SOMENTE LEITURA. Esta seção só pede token e consulta cliente por CPF/CNPJ
# (ConsultaClientes, catálogo V01 Veículos CRM). Nenhuma função daqui grava no
# Linx — cadastro, atendimento e romaneio ficam para etapas com confirmação.
#
# Diferente do botão Cliente (V17.85), que fala com o Linx LOCAL (10.1.15.1) pela
# extensão: o gateway da Smart API é público, então quem chama é o servidor.
# Chave da assinatura, usuário e senha só no .env; nunca vão ao navegador nem ao log.
# Contrato: INTEGRACAO_LINX_SMART_API.md.
# ---------------------------------------------------------------------------
LINX_API_BASE = os.getenv("LINX_API_BASE", "https://auto-gwsmartapi.linx.com.br").strip().rstrip("/")
LINX_API_CHAVE = os.getenv("LINX_API_CHAVE", "").strip()
LINX_API_USUARIO = os.getenv("LINX_API_USUARIO", "").strip()
LINX_API_SENHA = os.getenv("LINX_API_SENHA", "").strip()
LINX_API_CNPJ = re.sub(r"\D", "", os.getenv("LINX_API_CNPJ", ""))
LINX_EMPRESA = int(os.getenv("LINX_EMPRESA", "1") or 1)
LINX_REVENDA = int(os.getenv("LINX_REVENDA", "1") or 1)
LINX_USUARIO_CODIGO = int(os.getenv("LINX_USUARIO_CODIGO", "0") or 0)
LINX_CODIGO_ORIGEM = int(os.getenv("LINX_CODIGO_ORIGEM", "32") or 32)
LINX_PATH_SEGURANCA = os.getenv("LINX_PATH_SEGURANCA", "/api-seguranca").strip().rstrip("/")
LINX_PATH_CLIENTES = os.getenv("LINX_PATH_CLIENTES", "/api-veiculos-crm").strip().rstrip("/")
# O portal não diz como o token vai nas chamadas; "Bearer" é o mais provável.
LINX_AUTH_ESQUEMA = os.getenv("LINX_AUTH_ESQUEMA", "Bearer").strip()
LINX_HTTP_TIMEOUT = max(5, min(60, int(os.getenv("LINX_HTTP_TIMEOUT", "25") or 25)))
LINX_CLIENTES_INTERVALO = max(60, int(os.getenv("LINX_CLIENTES_INTERVALO", "120") or 120))
LINX_CLIENTES_LOTE = 10
LINX_CLIENTES_DIAS = 45
# "Não cadastrado" é consultado de novo: alguém (ou o robô) pode ter cadastrado.
LINX_RECONSULTA_SEM_CADASTRO = 6 * 3600
LINX_RECONSULTA_ERRO = 15 * 60

LINX_TOKEN_LOCK = threading.Lock()
LINX_TOKEN: dict[str, Any] = {"access_token": "", "expira": 0.0}
LINX_ESTADO: dict[str, str] = {"ultimo_ok": "", "ultimo_erro": "", "ultimo_erro_em": ""}


class LinxErro(RuntimeError):
    def __init__(self, mensagem: str, status: int = 0):
        self.status = status
        super().__init__(mensagem)


def linx_configuracao_faltando() -> list[str]:
    """Nomes (nunca valores) das variáveis do .env que faltam."""
    return [nome for nome, valor in (
        ("LINX_API_CHAVE", LINX_API_CHAVE),
        ("LINX_API_USUARIO", LINX_API_USUARIO),
        ("LINX_API_SENHA", LINX_API_SENHA),
    ) if not valor]


def _linx_registrar(erro: str = "") -> None:
    if erro:
        LINX_ESTADO["ultimo_erro"] = erro[:500]
        LINX_ESTADO["ultimo_erro_em"] = now_iso()
    else:
        LINX_ESTADO["ultimo_ok"] = now_iso()


def _linx_http(metodo: str, caminho: str, *, corpo: Any = None, form: dict[str, str] | None = None,
               com_token: bool = True) -> Any:
    return _linx_http_status(metodo, caminho, corpo=corpo, form=form, com_token=com_token)[1]


def _linx_http_status(metodo: str, caminho: str, *, corpo: Any = None, form: dict[str, str] | None = None,
                      com_token: bool = True) -> tuple[int, Any]:
    """Devolve (status, json). O status importa: a Linx responde 208 quando quer
    uma confirmação do usuário (diálogo), e isso NUNCA é respondido sozinho."""
    headers = {
        "Ocp-Apim-Subscription-Key": LINX_API_CHAVE,
        "Accept": "application/json",
        "User-Agent": f"NoromakExpedicao/{APP_VERSION}",
    }
    dados = None
    if form is not None:
        # /token pede multipart/form-data (contrato do portal).
        limite = "----noromak" + secrets.token_hex(12)
        partes = []
        for nome, valor in form.items():
            partes.append(
                f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'
            )
        partes.append(f"--{limite}--\r\n")
        dados = "".join(partes).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={limite}"
    elif corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if com_token:
        token = linx_obter_token()
        headers["Authorization"] = f"{LINX_AUTH_ESQUEMA} {token}".strip()
    req = urllib.request.Request(LINX_API_BASE + caminho, data=dados, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=LINX_HTTP_TIMEOUT) as resp:
            bruto = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        texto = ""
        try:
            texto = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detalhe = ""
        try:
            erro = json.loads(texto or "{}")
            if isinstance(erro, dict):
                detalhe = str(erro.get("MensagemDeErro") or erro.get("message") or erro.get("Message")
                              or erro.get("error_description") or erro.get("error") or "")
        except Exception:
            detalhe = texto[:200]
        raise LinxErro(f"Linx respondeu HTTP {exc.code}" + (f": {detalhe[:300]}" if detalhe else "."), exc.code) from exc
    except urllib.error.URLError as exc:
        raise LinxErro(f"Linx Smart API indisponível: {exc.reason}") from exc
    if not bruto.strip():
        return status, None
    try:
        return status, json.loads(bruto)
    except Exception as exc:
        raise LinxErro("Linx Smart API devolveu resposta que não é JSON.") from exc


def linx_obter_token(forcar: bool = False) -> str:
    faltando = linx_configuracao_faltando()
    if faltando:
        raise LinxErro("Linx Smart API não configurada no servidor: falta " + ", ".join(faltando) + ".")
    with LINX_TOKEN_LOCK:
        if not forcar and LINX_TOKEN["access_token"] and time.time() < LINX_TOKEN["expira"] - 60:
            return LINX_TOKEN["access_token"]
        form = {"grant_type": "client_credentials", "username": LINX_API_USUARIO, "password": LINX_API_SENHA}
        if LINX_API_CNPJ:
            form["cnpjEmpresa"] = LINX_API_CNPJ
        resposta = _linx_http("POST", f"{LINX_PATH_SEGURANCA}/token", form=form, com_token=False)
        dados = resposta if isinstance(resposta, dict) else {}
        if isinstance(dados.get("Token"), dict):
            dados = dados["Token"]
        token = str(dados.get("access_token") or "").strip()
        if not token:
            raise LinxErro("A Linx não devolveu o token (confira usuário, senha e chave da assinatura).")
        try:
            validade = float(dados.get("expires_in") or 3600)
        except (TypeError, ValueError):
            validade = 3600.0
        LINX_TOKEN["access_token"] = token
        LINX_TOKEN["expira"] = time.time() + max(120.0, validade)
        return token


def linx_chamar(metodo: str, caminho: str, corpo: Any = None) -> Any:
    return linx_chamar_status(metodo, caminho, corpo)[1]


def linx_chamar_status(metodo: str, caminho: str, corpo: Any = None) -> tuple[int, Any]:
    """Chamada autenticada. Token recusado (401) → pede outro e tenta uma vez."""
    try:
        return _linx_http_status(metodo, caminho, corpo=corpo)
    except LinxErro as exc:
        if exc.status != 401:
            raise
        linx_obter_token(forcar=True)
        return _linx_http_status(metodo, caminho, corpo=corpo)


def _linx_documento_do_registro(reg: dict[str, Any]) -> str:
    cgc = re.sub(r"\D", "", str(reg.get("Cgc") or reg.get("CgcCpf") or ""))
    if cgc and int(cgc or 0):
        return cgc.zfill(14) if len(cgc) > 11 else cgc.zfill(11)
    cpf = re.sub(r"\D", "", str(reg.get("Cpf") or ""))
    if cpf and int(cpf or 0):
        return cpf.zfill(11)
    return ""


def linx_consultar_cliente(documento: str) -> dict[str, Any]:
    """Procura o cliente pelo CPF/CNPJ. Só leitura.

    status: "encontrado" (um código só), "varios" (mais de um código para o
    mesmo documento — nunca escolhe sozinho) ou "nao_cadastrado".
    """
    numero = re.sub(r"\D", "", str(documento or ""))
    if len(numero) not in (11, 14):
        raise ValueError("CPF precisa ter 11 dígitos e CNPJ, 14.")
    corpo = {
        "CgcOuCpf": numero,
        "PessoaFisica": len(numero) == 11,
        "Empresa": LINX_EMPRESA,
        "Revenda": LINX_REVENDA,
        "Usuario": LINX_USUARIO_CODIGO,
        "CodigoOrigem": LINX_CODIGO_ORIGEM,
    }
    resposta = linx_chamar("POST", f"{LINX_PATH_CLIENTES}/Geral/ConsultaClientes/ConsultaClientes", corpo)
    if isinstance(resposta, dict):
        resposta = resposta.get("Registros") or resposta.get("Clientes") or ([resposta] if resposta.get("Cliente") else [])
    registros = [r for r in (resposta or []) if isinstance(r, dict) and str(r.get("Cliente") or "").strip() not in ("", "0")]
    com_documento = [r for r in registros if _linx_documento_do_registro(r)]
    if com_documento:
        # A busca já filtra pelo documento; a conferência aqui só evita aceitar
        # um cliente de outro CPF caso a Linx devolva resultado aproximado.
        registros = [r for r in com_documento if _linx_documento_do_registro(r) == numero]
    codigos: dict[str, str] = {}
    creditos: dict[str, dict[str, str]] = {}
    for r in registros:
        codigo = str(r.get("Cliente")).strip()
        codigos.setdefault(codigo, str(r.get("Nome") or "").strip())
        creditos.setdefault(codigo, _linx_credito_do_registro(r))
    _linx_registrar()
    if not codigos:
        return {"status": "nao_cadastrado", "cliente": "", "nome": "", "credito": {}, "candidatos": []}
    candidatos = [{"cliente": c, "nome": n} for c, n in codigos.items()]
    if len(candidatos) == 1:
        return {"status": "encontrado", "cliente": candidatos[0]["cliente"], "nome": candidatos[0]["nome"],
                "credito": creditos[candidatos[0]["cliente"]], "candidatos": candidatos}
    return {"status": "varios", "cliente": "", "nome": "", "credito": {}, "candidatos": candidatos}


def _linx_credito_do_registro(reg: dict[str, Any]) -> dict[str, str]:
    """V18.60: situação de crédito como a Linx devolve (texto cru — o significado de
    cada código ainda não foi confirmado). A API NÃO altera crédito; só mostra."""
    return {campo: str(reg.get(origem) or "").strip() for campo, origem in (
        ("situacao", "SituacaoCredito"), ("bloqueio", "BloqueioCredito"),
        ("disponibilidade", "DisponibilidadeCredito"), ("conceito", "Conceito"),
    ) if str(reg.get(origem) or "").strip()}


def ensure_cliente_fiscal_linx_api_schema() -> None:
    """V18.60: resultado da consulta automática ao Linx, separado de linx_cliente.

    linx_cliente continua sendo o que a extensão GRAVOU no Linx (cadastro
    atualizado com o nome da nota). As colunas linx_api_* só dizem se o
    cliente já existe lá — a consulta nunca apaga nem troca o que a extensão guardou.
    """
    colunas = {
        "linx_api_documento": "TEXT NOT NULL DEFAULT ''",
        "linx_api_status": "TEXT NOT NULL DEFAULT ''",
        "linx_api_cliente": "TEXT NOT NULL DEFAULT ''",
        "linx_api_nome": "TEXT NOT NULL DEFAULT ''",
        "linx_api_erro": "TEXT NOT NULL DEFAULT ''",
        "linx_api_consultado_em": "TEXT NOT NULL DEFAULT ''",
        "linx_api_consultado_epoch": "INTEGER NOT NULL DEFAULT 0",
        "linx_api_credito": "TEXT NOT NULL DEFAULT ''",
    }
    with DB_LOCK:
        existentes = {str(r["name"]) for r in DB.execute("PRAGMA table_info(cliente_fiscal)").fetchall()}
        if not existentes:
            return
        for nome, tipo in colunas.items():
            if nome not in existentes:
                DB.execute(f"ALTER TABLE cliente_fiscal ADD COLUMN {nome} {tipo}")
        DB.commit()


def linx_clientes_pendentes(limite: int = LINX_CLIENTES_LOTE) -> list[dict[str, str]]:
    """Pedidos com CPF/CNPJ guardado que ainda não sabem se o cliente existe no Linx."""
    agora = int(time.time())
    corte = (datetime.now(timezone.utc) - timedelta(days=LINX_CLIENTES_DIAS)).isoformat()
    with DB_LOCK:
        rows = DB.execute(
            """SELECT order_key, documento_numero FROM cliente_fiscal
               WHERE documento_numero<>'' AND linx_cliente='' AND atualizado_em>=?
                 AND (linx_api_documento<>documento_numero OR linx_api_status=''
                      OR (linx_api_status IN ('nao_cadastrado','varios') AND linx_api_consultado_epoch<?)
                      OR (linx_api_status='erro' AND linx_api_consultado_epoch<?))
               ORDER BY linx_api_consultado_epoch, atualizado_em DESC LIMIT ?""",
            (corte, agora - LINX_RECONSULTA_SEM_CADASTRO, agora - LINX_RECONSULTA_ERRO, int(limite)),
        ).fetchall()
    return [{"order_key": r["order_key"], "documento": r["documento_numero"]} for r in rows]


def _linx_guardar_consulta(order_key: str, documento: str, resultado: dict[str, Any] | None, erro: str = "") -> None:
    with DB_LOCK:
        DB.execute(
            """UPDATE cliente_fiscal SET linx_api_documento=?, linx_api_status=?, linx_api_cliente=?,
                   linx_api_nome=?, linx_api_erro=?, linx_api_consultado_em=?, linx_api_consultado_epoch=?,
                   linx_api_credito=?
               WHERE order_key=? AND documento_numero=?""",
            (documento, "erro" if erro else str(resultado.get("status") or ""),
             "" if erro else str(resultado.get("cliente") or ""),
             "" if erro else str(resultado.get("nome") or "")[:150],
             erro[:300], now_iso(), int(time.time()),
             "" if erro else json_dumps(resultado.get("credito") or {}), order_key, documento),
        )
        DB.commit()


def linx_clientes_sincronizar(limite: int = LINX_CLIENTES_LOTE) -> dict[str, Any]:
    """Consulta no Linx os clientes pendentes. Só leitura no Linx."""
    resumo = {"ok": True, "consultados": 0, "encontrados": 0, "nao_cadastrados": 0, "varios": 0, "erros": 0}
    faltando = linx_configuracao_faltando()
    if faltando:
        return {**resumo, "ok": False, "error": "Linx Smart API não configurada: falta " + ", ".join(faltando) + "."}
    for item in linx_clientes_pendentes(limite):
        try:
            resultado = linx_consultar_cliente(item["documento"])
        except LinxErro as exc:
            _linx_registrar(str(exc))
            _linx_guardar_consulta(item["order_key"], item["documento"], None, str(exc))
            resumo["erros"] += 1
            if exc.status in (0, 401, 403, 429) or exc.status >= 500:
                # Credencial, limite ou Linx fora do ar: parar o lote em vez de insistir.
                resumo.update(ok=False, error=str(exc))
                break
            continue
        except ValueError as exc:
            _linx_guardar_consulta(item["order_key"], item["documento"], None, str(exc))
            resumo["erros"] += 1
            continue
        _linx_guardar_consulta(item["order_key"], item["documento"], resultado)
        resumo["consultados"] += 1
        chave = {"encontrado": "encontrados", "nao_cadastrado": "nao_cadastrados", "varios": "varios"}.get(resultado["status"])
        if chave:
            resumo[chave] += 1
    return resumo


def linx_clientes_loop(stop_event: threading.Event) -> None:
    """V18.60: consulta sozinha os clientes novos. Sem .env da Linx, não faz nada."""
    if stop_event.wait(90):
        return
    while not stop_event.is_set():
        if not linx_configuracao_faltando():
            try:
                linx_clientes_sincronizar()
            except Exception as exc:
                _linx_registrar(str(exc))
                print(f"[linx-clientes] falhou: {exc}", file=sys.stderr, flush=True)
        if stop_event.wait(LINX_CLIENTES_INTERVALO):
            break


def linx_status_payload() -> dict[str, Any]:
    with DB_LOCK:
        contagem = {
            r["s"] or "pendente": int(r["n"]) for r in DB.execute(
                """SELECT CASE WHEN linx_api_documento<>documento_numero THEN '' ELSE linx_api_status END AS s,
                          COUNT(*) AS n FROM cliente_fiscal
                   WHERE documento_numero<>'' AND linx_cliente='' GROUP BY s"""
            ).fetchall()
        }
    faltando = linx_configuracao_faltando()
    return {
        "ok": True,
        "configurado": not faltando,
        "faltando": faltando,
        "gateway": LINX_API_BASE,
        "empresa": LINX_EMPRESA,
        "revenda": LINX_REVENDA,
        "token_valido": bool(LINX_TOKEN["access_token"]) and time.time() < LINX_TOKEN["expira"],
        "escrita_ativa": LINX_ESCRITA_ATIVA,
        "ultimo_ok": LINX_ESTADO["ultimo_ok"],
        "ultimo_erro": LINX_ESTADO["ultimo_erro"],
        "ultimo_erro_em": LINX_ESTADO["ultimo_erro_em"],
        "clientes": contagem,
    }


def linx_testar_conexao(documento: str = "") -> dict[str, Any]:
    """Pede um token novo e, se vier documento, consulta o cliente. Não grava nada."""
    try:
        linx_obter_token(forcar=True)
        _linx_registrar()
        saida: dict[str, Any] = {"ok": True, "token": True}
        if re.sub(r"\D", "", str(documento or "")):
            saida["consulta"] = linx_consultar_cliente(documento)
        return saida
    except (LinxErro, ValueError) as exc:
        _linx_registrar(str(exc))
        return {"ok": False, "error": str(exc)}


def linx_clientes_sem_cadastro() -> dict[str, Any]:
    """Fila de cadastro: pedidos cujo cliente a Linx diz que não existe."""
    corte = (datetime.now(timezone.utc) - timedelta(days=LINX_CLIENTES_DIAS)).isoformat()
    with DB_LOCK:
        rows = DB.execute(
            """SELECT pedido, documento_tipo, documento_numero, nome_fiscal, linx_api_consultado_em
               FROM cliente_fiscal
               WHERE linx_cliente='' AND linx_api_status='nao_cadastrado'
                 AND linx_api_documento=documento_numero AND atualizado_em>=?
               ORDER BY atualizado_em""",
            (corte,),
        ).fetchall()
    return {"ok": True, "pedidos": [{
        "pedido": r["pedido"],
        "documento": {"tipo": r["documento_tipo"], "numero": r["documento_numero"]},
        "nome_fiscal": r["nome_fiscal"],
        "consultado_em": r["linx_api_consultado_em"],
    } for r in rows]}


def _json_obj(texto: Any) -> dict[str, Any]:
    try:
        valor = json.loads(str(texto or "") or "{}")
    except Exception:
        return {}
    return valor if isinstance(valor, dict) else {}


# ---------------------------------------------------------------------------
# V18.60 — romaneio e cadastro pela Smart API (catálogos P02/P03 e V01).
#
# O atendimento (Contato) JÁ EXISTE: a integração do Mercado Livre com o Linx cria
# sozinha. O Noromak não cria contato nem item; ele só acha o contato do pedido,
# grava a Observação (marketplace + data do pedido) e confirma o romaneio.
#
# ConfirmaRomaneio MOVIMENTA ESTOQUE e imprime no depósito em até ~1 min — não
# volta atrás. Por isso:
#   - toda gravação exige LINX_ESCRITA_ATIVA=1 no .env (desligada por padrão,
#     até a leitura real ser conferida com o dono);
#   - só roda por clique humano depois de ver atendimento, itens e observação;
#   - relê a lista na hora (nunca confia na lista que a tela mostrou);
#   - registra cada operação em linx_operacoes_v1860 e recusa repetir;
#   - resposta 208 (a Linx perguntando algo) NUNCA é respondida sozinha.
# NÃO coberto pela API assinada: liberar crédito, trocar condição de pagamento
# (Shopee 971 / Magalu 983) e achar atendimentos pelo cliente diretamente.
# ---------------------------------------------------------------------------
LINX_ESCRITA_ATIVA = os.getenv("LINX_ESCRITA_ATIVA", "0").strip().lower() in {"1", "true", "sim"}
LINX_PATH_PECAS = os.getenv("LINX_PATH_PECAS", "/api-pecas-balcao-premium").strip().rstrip("/")
# Usuário dono dos atendimentos que a integração do ML cria (a Agenda é por usuário).
LINX_USUARIO_ATENDIMENTOS = int(os.getenv("LINX_USUARIO_ATENDIMENTOS", "0") or 0)
LINX_ROMANEIO_MAX_CONTATOS = 6
# Regra do dono (17/09/2026) para o cadastro de cliente de marketplace.
LINX_CADASTRO_EMAIL = "lojavirtual@noromak.com.br"
LINX_CADASTRO_DDD = 11
LINX_CADASTRO_TELEFONE = 111111111
LINX_OBSERVACAO_MARKETPLACE = {"Mercado Livre": "MERCADO LIVRE", "Shopee": "SHOPEE", "Magalu": "MAGALU"}


def ensure_linx_operacoes_schema() -> None:
    with DB_LOCK:
        DB.execute(
            """CREATE TABLE IF NOT EXISTS linx_operacoes_v1860 (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   order_key TEXT NOT NULL,
                   pedido TEXT NOT NULL DEFAULT '',
                   etapa TEXT NOT NULL,
                   contato TEXT NOT NULL DEFAULT '',
                   situacao TEXT NOT NULL,
                   detalhe TEXT NOT NULL DEFAULT '',
                   em TEXT NOT NULL,
                   por TEXT NOT NULL DEFAULT ''
               )"""
        )
        DB.execute("CREATE INDEX IF NOT EXISTS idx_linx_operacoes_pedido ON linx_operacoes_v1860(order_key, etapa)")
        DB.commit()


def _linx_operacao_registrar(pedido: str, etapa: str, situacao: str, detalhe: Any = "",
                             contato: Any = "", por: str = "") -> None:
    texto = detalhe if isinstance(detalhe, str) else json_dumps(detalhe)
    with DB_LOCK:
        DB.execute(
            "INSERT INTO linx_operacoes_v1860(order_key,pedido,etapa,contato,situacao,detalhe,em,por) VALUES(?,?,?,?,?,?,?,?)",
            (normalize(pedido), str(pedido), etapa, str(contato or ""), situacao, texto[:4000], now_iso(), str(por or "")[:80]),
        )
        DB.commit()


def linx_operacoes_do_pedido(pedido: str) -> list[dict[str, Any]]:
    with DB_LOCK:
        rows = DB.execute(
            "SELECT etapa,contato,situacao,detalhe,em,por FROM linx_operacoes_v1860 WHERE order_key=? ORDER BY id",
            (normalize(pedido),),
        ).fetchall()
    return [dict(r) for r in rows]


def _linx_identificacao(contato: int) -> dict[str, Any]:
    return {"Contato": int(contato), "Empresa": LINX_EMPRESA, "Revenda": LINX_REVENDA,
            "Usuario": LINX_USUARIO_CODIGO, "CodigoOrigem": LINX_CODIGO_ORIGEM}


def _linx_lista_romaneio(contato: int) -> dict[str, Any]:
    """GeraListaRomaneio: o que falta separar no atendimento. Só leitura."""
    corpo = {**_linx_identificacao(contato), "CodigoItemEstoque": None, "Ordem": None, "IdentificadorOrigem": ""}
    resposta = linx_chamar("POST", f"{LINX_PATH_PECAS}/Pecas/ManutencaoRomaneio/PecRomaneio/GeraListaRomaneio", corpo)
    return resposta if isinstance(resposta, dict) else {}


def _linx_itens_resumo(lista: dict[str, Any]) -> list[dict[str, Any]]:
    itens = []
    for i in lista.get("ItensRomaneio") or []:
        if isinstance(i, dict):
            itens.append({
                "item": str(i.get("ItemEstoquePub") or "").strip(),
                "descricao": str(i.get("DescricaoItemEstoque") or "").strip(),
                "quantidade": i.get("QuantidadeParaImprimir", i.get("QuantidadeSaldo")),
                "requisitada": i.get("QuantidadeRequisitada"),
            })
    return itens


def _linx_contatos_do_cliente(cliente: str) -> list[dict[str, Any]]:
    """Atendimentos do cliente pela Agenda do usuário da integração. A Smart API
    não tem 'atendimentos do cliente' (ObterAtendimentosDoCliente é só do Linx local)."""
    usuario = LINX_USUARIO_ATENDIMENTOS or LINX_USUARIO_CODIGO
    resposta = linx_chamar(
        "GET", f"{LINX_PATH_PECAS}/VendaExternaPecas/Contatos/Agenda/{LINX_EMPRESA}/{LINX_REVENDA}/{usuario}"
    )
    registros = resposta if isinstance(resposta, list) else (
        (resposta or {}).get("Registros") or (resposta or {}).get("Contatos") or [] if isinstance(resposta, dict) else []
    )
    vistos: dict[str, dict[str, Any]] = {}
    for r in registros:
        if not isinstance(r, dict) or str(r.get("Cliente") or "").strip() != str(cliente):
            continue
        contato = str(r.get("Contato") or "").strip()
        if contato and contato != "0" and contato not in vistos:
            vistos[contato] = {
                "contato": contato,
                "situacao": str(r.get("DescricaoSituacaoContato") or r.get("SituacaoCon") or "").strip(),
                "data": str(r.get("DataContato") or "").strip(),
            }
    return sorted(vistos.values(), key=lambda c: c["data"], reverse=True)


def _linx_codigos_do_pedido(linhas: list[dict[str, Any]]) -> set[str]:
    """SKUs do pedido nas formas em que o código Apollo pode aparecer
    (puro, prefixo-código, A/B)."""
    codigos: set[str] = set()
    for row in linhas:
        sku = str(row.get("SKU") or row.get("Sku") or "").upper()
        for parte in re.split(r"[/\-\s]+", sku):
            parte = re.sub(r"[^0-9A-Z]", "", parte)
            if parte:
                codigos.add(parte)
    return codigos


def _linx_observacao(marketplace: str, linhas: list[dict[str, Any]]) -> str:
    data = _vendas_data_da_linha(linhas[0]) if linhas else ""
    nome = LINX_OBSERVACAO_MARKETPLACE.get(marketplace, str(marketplace or "").upper())
    return f"{nome} {data[8:10]}/{data[5:7]}".strip() if data else nome


def _linx_documento_do_pedido(pedido: str, linhas: list[dict[str, Any]]) -> tuple[str, str]:
    guardado = _cliente_fiscal_guardado(pedido) or {}
    if guardado.get("documento_numero"):
        return guardado["documento_numero"], str(guardado.get("nome_fiscal") or "")
    for row in linhas:
        doc = documento_do_pedido(row)
        if doc["numero"]:
            return doc["numero"], nome_fiscal_do_pedido(row)
    return "", ""


def linx_romaneio_preparar(pedido: str, contatos: list[Any] | None = None) -> dict[str, Any]:
    """Mostra o que o romaneio faria. Só leitura no Linx.

    `contatos`: atendimentos que a extensão achou no Linx local
    (ObterAtendimentosDoCliente) — usados no lugar da Agenda quando vierem."""
    pedido = str(pedido or "").strip()
    with DB_LOCK:
        linhas = _linhas_do_pedido(pedido)
    if not linhas:
        return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
    marketplace = _vendas_marketplace(linhas[0].get("Marketplace") or linhas[0].get("Origem"))
    saida: dict[str, Any] = {
        "ok": True, "pedido": pedido, "marketplace": marketplace,
        "observacao": _linx_observacao(marketplace, linhas),
        "escrita_ativa": LINX_ESCRITA_ATIVA,
        "precisa_condicao": marketplace in ("Shopee", "Magalu"),
        "operacoes": linx_operacoes_do_pedido(pedido),
        "cadastro": _linx_cadastro_situacao(pedido),
        "robo": linx_robo_fila_do_pedido(pedido),
        "cliente": None, "candidatos": [], "escolhido": "",
    }
    documento, nome = _linx_documento_do_pedido(pedido, linhas)
    if not documento:
        return {**saida, "situacao": "sem_documento",
                "aviso": "Este pedido ainda não tem CPF/CNPJ da nota. Puxe nome e CPF primeiro."}
    consulta = linx_consultar_cliente(documento)
    saida["cliente"] = {"documento": documento, "nome_fiscal": nome, **consulta}
    if consulta["status"] != "encontrado":
        return {**saida, "situacao": consulta["status"],
                "aviso": "Cliente não cadastrado no Linx." if consulta["status"] == "nao_cadastrado"
                else "Mais de um cliente com este CPF/CNPJ no Linx — confira no Linx."}
    codigos = _linx_codigos_do_pedido(linhas)
    candidatos = []
    informados = []
    for c in contatos or []:
        numero = re.sub(r"\D", "", str(c or ""))
        if numero and numero != "0" and numero not in [x["contato"] for x in informados]:
            informados.append({"contato": numero, "situacao": "informado pelo Linx", "data": ""})
    saida["fonte_contatos"] = "extensao" if informados else "agenda"
    lista_contatos = informados or _linx_contatos_do_cliente(consulta["cliente"])
    for c in lista_contatos[:LINX_ROMANEIO_MAX_CONTATOS]:
        itens = _linx_itens_resumo(_linx_lista_romaneio(int(c["contato"])))
        if not itens:
            continue  # nada a separar: romaneio já feito ou atendimento sem peça
        bate = sum(1 for i in itens if re.sub(r"[^0-9A-Z]", "", i["item"].upper()) in codigos)
        candidatos.append({**c, "itens": itens, "itens_do_pedido": bate})
    saida["candidatos"] = candidatos
    # Escolhe sozinho só quando não há dúvida: um atendimento com itens, ou um
    # único cujas peças batem com o pedido. Senão, quem escolhe é a pessoa.
    if len(candidatos) == 1:
        saida["escolhido"] = candidatos[0]["contato"]
    else:
        batem = [c for c in candidatos if c["itens_do_pedido"]]
        if len(batem) == 1:
            saida["escolhido"] = batem[0]["contato"]
    if not candidatos:
        return {**saida, "situacao": "sem_atendimento",
                "aviso": "Nenhum atendimento deste cliente com peças a separar (o romaneio pode já ter sido feito)."}
    return {**saida, "situacao": "pronto" if saida["escolhido"] else "escolher"}


def _linx_escrita_bloqueada() -> str:
    if linx_configuracao_faltando():
        return "Linx Smart API não configurada no servidor."
    if not LINX_ESCRITA_ATIVA:
        return ("Gravação no Linx desligada (LINX_ESCRITA_ATIVA=0). Ela é ligada depois que a "
                "consulta real for conferida.")
    return ""


def _linx_dialogo(resposta: Any) -> str:
    if isinstance(resposta, dict):
        return str(resposta.get("TextoDeConfirmacao") or resposta.get("MensagemErro") or "").strip()
    return ""


def _linx_cadastro_situacao(pedido: str) -> dict[str, bool]:
    """V18.60: o cadastro vem ANTES do romaneio (regra do dono, 02/10/2026).
    feito = o Noromak tem o código do cliente gravado (pela extensão, robô ou API).
    pela_api = a última atualização foi pela Smart API, que NÃO libera crédito."""
    guardado = _cliente_fiscal_guardado(pedido) or {}
    cadastros = [o for o in linx_operacoes_do_pedido(pedido)
                 if o["etapa"] in ("cadastro", "cadastro_robo") and o["situacao"] == "ok"]
    ultimo = cadastros[-1] if cadastros else None
    return {"feito": bool(str(guardado.get("linx_cliente") or "").strip()) or ultimo is not None,
            "pela_api": bool(ultimo and ultimo["etapa"] == "cadastro")}


def linx_romaneio_confirmar(pedido: str, contato: Any, observacao: str, *, condicao_conferida: bool = False,
                            credito_conferido: bool = False, usuario: str = "") -> dict[str, Any]:
    """Grava a Observação e confirma o romaneio (IMPRIME e movimenta estoque)."""
    pedido = str(pedido or "").strip()
    observacao = re.sub(r"\s+", " ", str(observacao or "")).strip().upper()
    bloqueio = _linx_escrita_bloqueada()
    if bloqueio:
        return {"ok": False, "error": bloqueio}
    try:
        contato_num = int(str(contato or "").strip())
    except ValueError:
        contato_num = 0
    if contato_num <= 0:
        return {"ok": False, "error": "Escolha o atendimento (Contato)."}
    if not observacao or len(observacao) > 120:
        return {"ok": False, "error": "Informe a observação do romaneio (ex.: MERCADO LIVRE 21/09)."}
    with DB_LOCK:
        linhas = _linhas_do_pedido(pedido)
    if not linhas:
        return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
    marketplace = _vendas_marketplace(linhas[0].get("Marketplace") or linhas[0].get("Origem"))
    condicao_pela_extensao = any(o["etapa"] == "condicao" and o["situacao"] == "ok"
                                 for o in linx_operacoes_do_pedido(pedido))
    if marketplace in ("Shopee", "Magalu") and not (condicao_conferida or condicao_pela_extensao):
        return {"ok": False, "error": f"Confira no Linx a condição de pagamento do {marketplace} "
                                      "(Shopee 971, Magalu 983) antes do romaneio. A API não troca a condição."}
    if any(o["etapa"] == "romaneio" and o["situacao"] == "ok" for o in linx_operacoes_do_pedido(pedido)):
        return {"ok": False, "error": "O romaneio deste pedido já foi impresso pelo Noromak."}
    cadastro = _linx_cadastro_situacao(pedido)
    if not cadastro["feito"]:
        return {"ok": False, "error": "Atualize o cadastro do cliente no Linx antes do romaneio."}
    if cadastro["pela_api"] and not credito_conferido:
        return {"ok": False, "error": "O cadastro foi atualizado pela API, que não libera crédito. Libere no Linx "
                                      "(Aprovado + Crédito normal) e marque que conferiu."}
    try:
        lista = _linx_lista_romaneio(contato_num)
        itens = _linx_itens_resumo(lista)
        if not itens:
            return {"ok": False, "error": "Este atendimento não tem itens a separar — o romaneio pode já ter sido feito."}
        lista["Observacao"] = observacao
        atualizada = linx_chamar(
            "POST", f"{LINX_PATH_PECAS}/Pecas/ManutencaoRomaneio/PecRomaneio/AtualizaListaRomaneio/AtualizaQuantidades",
            {"Empresa": LINX_EMPRESA, "ListaRomaneio": lista, "ItensParaAjuste": []},
        )
        if isinstance(atualizada, dict) and isinstance(atualizada.get("ItensRomaneio"), list):
            lista = atualizada
            lista["Observacao"] = observacao
        status, resposta = linx_chamar_status(
            "POST", f"{LINX_PATH_PECAS}/Pecas/ManutencaoRomaneio/PecRomaneio/ConfirmaRomaneio",
            {"IdentificacaoAtendimento": _linx_identificacao(contato_num), "ListaRomaneio": lista},
        )
    except LinxErro as exc:
        _linx_registrar(str(exc))
        _linx_operacao_registrar(pedido, "romaneio", "erro", str(exc), contato_num, usuario)
        return {"ok": False, "error": str(exc)}
    if status == 208:
        pergunta = _linx_dialogo(resposta) or "A Linx pediu uma confirmação."
        _linx_operacao_registrar(pedido, "romaneio", "pergunta", resposta, contato_num, usuario)
        return {"ok": False, "situacao": "pergunta", "error": f"A Linx perguntou: {pergunta} Resolva no Linx; nada foi respondido."}
    _linx_operacao_registrar(pedido, "romaneio", "ok", {"observacao": observacao, "itens": itens, "resposta": resposta},
                             contato_num, usuario)
    audit(usuario, "linx.romaneio", f"{pedido}|{contato_num}|{observacao}")
    mensagens = [str(m) for m in resposta] if isinstance(resposta, list) else []
    return {"ok": True, "situacao": "impresso", "pedido": pedido, "contato": contato_num,
            "observacao": observacao, "itens": itens, "mensagens": mensagens}


def linx_cadastro_atualizar(pedido: str, usuario: str = "") -> dict[str, Any]:
    """Nome da NF-e (maiúsculo), telefones e e-mail pela regra de 17/09/2026, pela
    rota AtualizarCadastroClienteAAR — só estes campos. Endereço e CRÉDITO não
    passam por aqui (a API não altera crédito)."""
    pedido = str(pedido or "").strip()
    bloqueio = _linx_escrita_bloqueada()
    if bloqueio:
        return {"ok": False, "error": bloqueio}
    with DB_LOCK:
        linhas = _linhas_do_pedido(pedido)
    if not linhas:
        return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
    documento, nome = _linx_documento_do_pedido(pedido, linhas)
    if not documento or not _nome_fiscal_valido(nome):
        return {"ok": False, "error": "Falta CPF/CNPJ ou nome da nota neste pedido. Puxe nome e CPF primeiro."}
    try:
        consulta = linx_consultar_cliente(documento)
        if consulta["status"] != "encontrado":
            return {"ok": False, "error": "Cliente não cadastrado no Linx (ou mais de um com este CPF). "
                                          "A API atualiza, mas não inclui sem conferência."}
        nome_linx = re.sub(r"\s+", " ", nome).strip().upper()
        corpo = {
            "Nome": nome_linx, "Email": LINX_CADASTRO_EMAIL, "CPFCNPJ": documento,
            "DDDTelefone": LINX_CADASTRO_DDD, "Telefone": LINX_CADASTRO_TELEFONE,
            "DDDCelular": LINX_CADASTRO_DDD, "Celular": LINX_CADASTRO_TELEFONE,
            "Empresa": LINX_EMPRESA, "Revenda": LINX_REVENDA, "Cliente": int(consulta["cliente"]),
        }
        linx_chamar("POST", f"{LINX_PATH_CLIENTES}/Geral/ManutencaoClienteSimplificado/AtualizarCadastroClienteAAR", corpo)
        # Neste gateway 200 não é confirmação: relê e confere o nome.
        depois = linx_consultar_cliente(documento)
    except LinxErro as exc:
        _linx_registrar(str(exc))
        _linx_operacao_registrar(pedido, "cadastro", "erro", str(exc), "", usuario)
        return {"ok": False, "error": str(exc)}
    conferido = normalize(depois.get("nome")) == normalize(nome_linx)
    _linx_operacao_registrar(pedido, "cadastro", "ok" if conferido else "nao_conferido",
                             {"cliente": consulta["cliente"], "nome": nome_linx, "nome_depois": depois.get("nome")},
                             "", usuario)
    audit(usuario, "linx.cadastro", f"{pedido}|{consulta['cliente']}|{'ok' if conferido else 'nao_conferido'}")
    if not conferido:
        return {"ok": False, "error": f"A Linx aceitou, mas o nome continua \"{depois.get('nome') or '—'}\". Confira no Linx."}
    salvar_cliente_fiscal(pedido, documento, nome, linx_cliente=str(consulta["cliente"]), fonte="linx_api", usuario=usuario)
    return {"ok": True, "pedido": pedido, "cliente": consulta["cliente"], "nome": nome_linx,
            "credito": depois.get("credito") or {}}


# ---------------------------------------------------------------------------
# V18.60 — o que a Smart API não faz, quem faz:
#
#   cadastro completo + CRÉDITO  → robô do Apollo desktop (apollo-robo.ps1), no PC
#                                   do escritório: busca a fila aqui, grava pelo app
#                                   desktop (o Linx web quebra ao gravar cliente desde
#                                   02/10/2026) e devolve o resultado;
#   condição de pagamento        → extensão "Cadastro de Cliente no Linx" (Linx local),
#   (Shopee 971 / Magalu 983)       chamada pela tela; o resultado é registrado aqui;
#   envio ao caixa               → extensão, idem.
#
# O robô se autentica com um token próprio (mesmo modelo do agente Zebra), só
# enxerga a fila e só devolve resultado: não tem acesso a mais nada do sistema.
# Só entra na fila o que um admin mandou, pedido a pedido.
# ---------------------------------------------------------------------------
LINX_ROBO_TOKEN_STORAGE_KEY = "linx_robo_token_v1"
LINX_ROBO_TRAVA_SEGUNDOS = 15 * 60
LINX_ROBO_MAX_TENTATIVAS = 3
LINX_OPERACOES_DA_EXTENSAO = {"condicao", "caixa"}


def ensure_linx_robo_schema() -> None:
    with DB_LOCK:
        DB.execute(
            """CREATE TABLE IF NOT EXISTS linx_robo_fila_v1860 (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   order_key TEXT NOT NULL,
                   pedido TEXT NOT NULL,
                   documento TEXT NOT NULL,
                   nome TEXT NOT NULL,
                   situacao TEXT NOT NULL DEFAULT 'pendente',
                   tentativas INTEGER NOT NULL DEFAULT 0,
                   pego_epoch INTEGER NOT NULL DEFAULT 0,
                   mensagem TEXT NOT NULL DEFAULT '',
                   criado_em TEXT NOT NULL,
                   criado_por TEXT NOT NULL DEFAULT '',
                   atualizado_em TEXT NOT NULL
               )"""
        )
        DB.execute("CREATE INDEX IF NOT EXISTS idx_linx_robo_situacao ON linx_robo_fila_v1860(situacao, id)")
        DB.commit()


def get_or_create_linx_robo_token() -> str:
    env_token = os.getenv("LINX_ROBO_TOKEN", "").strip()
    if env_token:
        return env_token
    with DB_LOCK:
        row = DB.execute("SELECT value FROM storage WHERE key=?", (LINX_ROBO_TOKEN_STORAGE_KEY,)).fetchone()
        token = str(row["value"] if row else "").strip()
        if token:
            return token
        token = secrets.token_urlsafe(36)
        DB.execute(
            "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=storage.version+1,updated_at=excluded.updated_at",
            (LINX_ROBO_TOKEN_STORAGE_KEY, token, now_iso()),
        )
        DB.commit()
        return token


def rotate_linx_robo_token() -> str:
    if os.getenv("LINX_ROBO_TOKEN", "").strip():
        raise RuntimeError("O token está definido por LINX_ROBO_TOKEN no servidor e não pode ser renovado pelo painel.")
    token = secrets.token_urlsafe(36)
    with DB_LOCK:
        DB.execute(
            "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=storage.version+1,updated_at=excluded.updated_at",
            (LINX_ROBO_TOKEN_STORAGE_KEY, token, now_iso()),
        )
        DB.commit()
    return token


def linx_robo_fila_do_pedido(pedido: str) -> dict[str, Any] | None:
    with DB_LOCK:
        row = DB.execute(
            "SELECT id,situacao,tentativas,mensagem,criado_em,criado_por,atualizado_em FROM linx_robo_fila_v1860 "
            "WHERE order_key=? ORDER BY id DESC LIMIT 1", (normalize(pedido),),
        ).fetchone()
    return dict(row) if row else None


def linx_robo_enfileirar(pedido: str, usuario: str = "") -> dict[str, Any]:
    """Admin manda o cadastro completo (com crédito) deste pedido para o robô."""
    pedido = str(pedido or "").strip()
    with DB_LOCK:
        linhas = _linhas_do_pedido(pedido)
    if not linhas:
        return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
    documento, nome = _linx_documento_do_pedido(pedido, linhas)
    if len(documento) != 11:
        # O robô só sabe pessoa física (a extensão também bloqueia CNPJ).
        return {"ok": False, "error": "O robô cadastra só CPF (pessoa física). Puxe nome e CPF da nota primeiro."
                if not documento else "O robô cadastra só CPF (pessoa física); CNPJ fica para o Linx."}
    if not _nome_fiscal_valido(nome):
        return {"ok": False, "error": "Falta o nome da nota neste pedido. Puxe nome e CPF primeiro."}
    atual = linx_robo_fila_do_pedido(pedido)
    if atual and atual["situacao"] in ("pendente", "em_andamento"):
        return {"ok": True, "ja_na_fila": True, "fila": atual}
    agora = now_iso()
    with DB_LOCK:
        DB.execute(
            """INSERT INTO linx_robo_fila_v1860(order_key,pedido,documento,nome,situacao,criado_em,criado_por,atualizado_em)
               VALUES(?,?,?,?,'pendente',?,?,?)""",
            (normalize(pedido), pedido, documento, re.sub(r"\s+", " ", nome).strip().upper(), agora,
             str(usuario or "")[:80], agora),
        )
        DB.commit()
    audit(usuario, "linx.robo.enfileirar", pedido)
    return {"ok": True, "fila": linx_robo_fila_do_pedido(pedido)}


def linx_robo_proximo() -> dict[str, Any]:
    """O robô pega UM cadastro. Item pego e não devolvido em 15 min volta à fila."""
    agora = int(time.time())
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            row = DB.execute(
                """SELECT id,pedido,documento,nome,tentativas FROM linx_robo_fila_v1860
                   WHERE situacao='pendente' OR (situacao='em_andamento' AND pego_epoch<?)
                   ORDER BY id LIMIT 1""",
                (agora - LINX_ROBO_TRAVA_SEGUNDOS,),
            ).fetchone()
            if row:
                DB.execute(
                    "UPDATE linx_robo_fila_v1860 SET situacao='em_andamento', pego_epoch=?, tentativas=tentativas+1, "
                    "atualizado_em=? WHERE id=?", (agora, now_iso(), row["id"]),
                )
            DB.commit()
        except Exception:
            DB.rollback()
            raise
    if not row:
        return {"ok": True, "item": None}
    return {"ok": True, "item": {"id": row["id"], "pedido": row["pedido"], "cpf": row["documento"], "nome": row["nome"]}}


def linx_robo_resultado(item_id: Any, ok: bool, mensagem: str = "", cliente: str = "") -> dict[str, Any]:
    """O robô devolve o que aconteceu. ok = gravou cadastro E crédito e releu."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Item inválido."}
    with DB_LOCK:
        row = DB.execute("SELECT * FROM linx_robo_fila_v1860 WHERE id=?", (item_id,)).fetchone()
    if not row or row["situacao"] != "em_andamento":
        return {"ok": False, "error": "Item não está com o robô."}
    mensagem = str(mensagem or "")[:1000]
    cliente = re.sub(r"\D", "", str(cliente or ""))
    if ok and not cliente:
        # O robô não leu o código: tenta pela Smart API (só leitura).
        try:
            consulta = linx_consultar_cliente(row["documento"]) if not linx_configuracao_faltando() else {}
            if consulta.get("status") == "encontrado":
                cliente = consulta["cliente"]
        except (LinxErro, ValueError):
            pass
    if ok:
        situacao = "ok"
    else:
        situacao = "erro" if int(row["tentativas"] or 0) >= LINX_ROBO_MAX_TENTATIVAS else "pendente"
    with DB_LOCK:
        DB.execute("UPDATE linx_robo_fila_v1860 SET situacao=?, mensagem=?, atualizado_em=? WHERE id=?",
                   (situacao, mensagem, now_iso(), item_id))
        DB.commit()
    _linx_operacao_registrar(row["pedido"], "cadastro_robo", "ok" if ok else "erro",
                             {"mensagem": mensagem, "cliente": cliente}, "", "robo-apollo")
    if ok:
        # Mesmo marcador que a extensão usa quando grava: cadastro feito (com crédito).
        salvar_cliente_fiscal(row["pedido"], row["documento"], row["nome"], linx_cliente=cliente or "robo",
                              fonte="robo_apollo", usuario="robo-apollo")
    audit("robo-apollo", "linx.robo.resultado", f"{row['pedido']}|{situacao}|{mensagem[:120]}")
    return {"ok": True, "situacao": situacao}


def linx_registrar_operacao_extensao(pedido: str, etapa: str, situacao: str, detalhe: Any,
                                     contato: Any = "", usuario: str = "") -> dict[str, Any]:
    """A tela registra o que a extensão fez no Linx local (condição, caixa)."""
    pedido = str(pedido or "").strip()
    if etapa not in LINX_OPERACOES_DA_EXTENSAO:
        return {"ok": False, "error": "Etapa inválida."}
    if situacao not in ("ok", "pendente", "erro"):
        return {"ok": False, "error": "Situação inválida."}
    with DB_LOCK:
        if not _linhas_do_pedido(pedido):
            return {"ok": False, "error": f"Pedido {pedido} não encontrado."}
    if etapa == "caixa" and situacao == "ok" and not any(
        o["etapa"] == "romaneio" and o["situacao"] == "ok" for o in linx_operacoes_do_pedido(pedido)
    ):
        # Registro só; quem envia é a extensão. Mas a ordem fica visível.
        detalhe = {"aviso": "enviado ao caixa sem romaneio registrado pelo Noromak", "detalhe": detalhe}
    _linx_operacao_registrar(pedido, etapa, situacao, detalhe, contato, usuario)
    audit(usuario, f"linx.{etapa}", f"{pedido}|{situacao}")
    return {"ok": True}


VENDAS_CAMPOS_DATA = ("Data do Pedido", "DataPedido", "DataVenda", "Data", "CriadoEm")
VENDAS_CAMPOS_DESCRICAO = ("Descrição", "Descricao", "descricao", "Produto", "produto", "Nome")


def _vendas_data_iso(value: Any) -> str:
    """AAAA-MM-DD a partir dos formatos que circulam no sistema.

    Convivem no banco a data ISO do servidor (2026-09-17T10:07:00-03:00) e a
    data brasileira que vem das telas e do Apollo (17/09/2026 10:07:00).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    br = re.match(r"^(\d{2})/(\d{2})/(\d{4})", text)
    if br:
        return f"{br.group(3)}-{br.group(2)}-{br.group(1)}"
    return ""


def _vendas_data_da_linha(row: dict[str, Any], fallback: Any = "") -> str:
    for campo in VENDAS_CAMPOS_DATA:
        achou = _vendas_data_iso(row.get(campo))
        if achou:
            return achou
    return _vendas_data_iso(fallback)


def _vendas_marketplace(value: Any) -> str:
    texto = normalize(value)
    if "MERCADOLIVRE" in texto or "MERCADO" in texto or texto in {"ML", "MELI"}:
        return "Mercado Livre"
    if "SHOPEE" in texto:
        return "Shopee"
    if "MAGALU" in texto or "MAGAZINE" in texto:
        return "Magalu"
    return str(value or "").strip() or "Outros"


def _vendas_descricao(row: dict[str, Any]) -> str:
    for campo in VENDAS_CAMPOS_DESCRICAO:
        texto = str(row.get(campo) or "").strip()
        if texto:
            return texto
    return ""


def _vendas_quantidade(row: dict[str, Any]) -> int:
    for campo in ("Quantidade", "quantidade", "Qtd", "qtd"):
        if campo in row:
            try:
                return max(0, int(float(str(row.get(campo) or 0).replace(",", "."))))
            except Exception:
                continue
    return 0


def _vendas_linhas_do_periodo(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Junta fila atual e concluídos, sem contar o mesmo pedido duas vezes."""
    coletadas: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def aceitar(row: Any, *, concluido: bool, fallback_data: Any = "") -> None:
        if not isinstance(row, dict):
            return
        pedido = str(order_number(row) or "").strip()
        if not pedido:
            return
        data = _vendas_data_da_linha(row, fallback_data)
        if date_from and (not data or data < date_from):
            return
        if date_to and (not data or data > date_to):
            return
        coletadas.append({
            "pedido": pedido,
            "data": data,
            "sku": str(row.get("SKU") or row.get("Sku") or row.get("sku") or "").strip(),
            "descricao": _vendas_descricao(row),
            "ean": str(row.get("EAN") or row.get("Código de Barras") or "").strip(),
            "quantidade": _vendas_quantidade(row),
            "marketplace": _vendas_marketplace(row.get("Marketplace") or row.get("Origem")),
            "cliente": str(row.get("Cliente") or "").strip(),
            "concluido": concluido,
        })

    pendentes, _, _ = get_storage("conf_pedidos")
    for row in _rows_from_storage_value(pendentes):
        if isinstance(row, dict):
            chave = normalize(order_number(row))
            if chave:
                vistos.add(chave)
            aceitar(row, concluido=False)

    for db_row in DB.execute("SELECT order_key,completed_at,payload FROM completed_orders").fetchall():
        chave = normalize(db_row["order_key"])
        if chave and chave in vistos:
            continue
        try:
            payload = json.loads(str(db_row["payload"] or "[]"))
        except Exception:
            continue
        for row in (payload if isinstance(payload, list) else [payload]):
            aceitar(row, concluido=True, fallback_data=db_row["completed_at"])

    return coletadas


def vendas_payload(date_from: str = "", date_to: str = "") -> dict[str, Any]:
    """O que vendeu no período, agregado por SKU.

    Base para rentabilidade, curva ABC e solicitação de compra: o que interessa
    é quantas unidades de cada peça saíram, em quantos pedidos e por qual canal.
    """
    linhas = _vendas_linhas_do_periodo(date_from, date_to)

    por_sku: dict[str, dict[str, Any]] = {}
    por_marketplace: dict[str, dict[str, Any]] = {}
    pedidos_totais: set[str] = set()
    pedidos_concluidos: set[str] = set()
    unidades_totais = 0

    for linha in linhas:
        pedido = linha["pedido"]
        pedidos_totais.add(normalize(pedido))
        if linha["concluido"]:
            pedidos_concluidos.add(normalize(pedido))
        unidades_totais += linha["quantidade"]

        chave_sku = normalize(linha["sku"]) or f"SEM-SKU|{normalize(linha['ean'])}"
        item = por_sku.setdefault(chave_sku, {
            "sku": linha["sku"] or "(sem SKU)",
            "descricao": linha["descricao"],
            "ean": linha["ean"],
            "unidades": 0,
            "pedidos": set(),
            "marketplaces": {},
        })
        item["unidades"] += linha["quantidade"]
        item["pedidos"].add(normalize(pedido))
        if not item["descricao"] and linha["descricao"]:
            item["descricao"] = linha["descricao"]
        if not item["ean"] and linha["ean"]:
            item["ean"] = linha["ean"]
        item["marketplaces"][linha["marketplace"]] = item["marketplaces"].get(linha["marketplace"], 0) + linha["quantidade"]

        canal = por_marketplace.setdefault(linha["marketplace"], {
            "marketplace": linha["marketplace"], "unidades": 0, "pedidos": set(),
        })
        canal["unidades"] += linha["quantidade"]
        canal["pedidos"].add(normalize(pedido))

    itens = sorted(
        (
            {
                "sku": dados["sku"],
                "descricao": dados["descricao"],
                "ean": dados["ean"],
                "unidades": dados["unidades"],
                "pedidos": len(dados["pedidos"]),
                "marketplaces": dados["marketplaces"],
            }
            for dados in por_sku.values()
        ),
        key=lambda x: (-x["unidades"], x["sku"]),
    )
    canais = sorted(
        (
            {"marketplace": d["marketplace"], "unidades": d["unidades"], "pedidos": len(d["pedidos"])}
            for d in por_marketplace.values()
        ),
        key=lambda x: (-x["unidades"], x["marketplace"]),
    )

    return {
        "ok": True,
        "periodo": {"inicio": date_from, "fim": date_to},
        "resumo": {
            "pedidos": len(pedidos_totais),
            "pedidos_concluidos": len(pedidos_concluidos),
            "pedidos_pendentes": len(pedidos_totais) - len(pedidos_concluidos),
            "skus": len(itens),
            "linhas": len(linhas),
            "unidades": unidades_totais,
        },
        "marketplaces": canais,
        "itens": itens,
    }


PRINT_AGENT_TOKEN_STORAGE_KEY = "print_agent_token_v1"


def get_or_create_print_agent_token() -> str:
    env_token = os.getenv("EXP_PRINT_AGENT_TOKEN", "").strip()
    if env_token:
        return env_token
    with DB_LOCK:
        value, _, _ = get_storage(PRINT_AGENT_TOKEN_STORAGE_KEY)
        token = str(value or "").strip()
        if token:
            return token
        token = secrets.token_urlsafe(36)
        DB.execute(
            "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=storage.version+1,updated_at=excluded.updated_at",
            (PRINT_AGENT_TOKEN_STORAGE_KEY, token, now_iso()),
        )
        DB.commit()
        return token


def rotate_print_agent_token() -> str:
    if os.getenv("EXP_PRINT_AGENT_TOKEN", "").strip():
        raise RuntimeError("O token está definido por EXP_PRINT_AGENT_TOKEN no servidor e não pode ser renovado pelo painel.")
    token = secrets.token_urlsafe(36)
    with DB_LOCK:
        DB.execute(
            "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=storage.version+1,updated_at=excluded.updated_at",
            (PRINT_AGENT_TOKEN_STORAGE_KEY, token, now_iso()),
        )
        DB.commit()
    return token



def _money_number(value: Any) -> float | None:
    try:
        if value is None or value == "": return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ml_item_fee(oi: dict[str, Any]) -> float | None:
    """Tarifa da linha exatamente como informada pelo Mercado Livre.

    Em orders, ``sale_fee`` é a tarifa de venda por unidade. Alguns contratos
    também expõem ``sale_fee_amount`` já totalizado; quando existir, ele tem
    prioridade para não fazer suposições.
    """
    if not isinstance(oi, dict):
        return None
    for key in ("sale_fee_amount", "sale_fee_total"):
        v = _money_number(oi.get(key))
        if v is not None:
            return abs(v)
    v = _money_number(oi.get("sale_fee"))
    if v is None:
        return None
    try:
        qty = max(1, int(float(oi.get("quantity") or 1)))
    except Exception:
        qty = 1
    return abs(v) * qty


def _ml_order_marketplace_fee(order: dict[str, Any]) -> float | None:
    """Obtém a tarifa real total do pedido sem estimar percentuais.

    Alguns payloads do ML trazem ``marketplace_fee: 0`` mesmo quando cada
    ``order_item`` possui ``sale_fee``. Por isso zero não encerra a busca: a
    função procura uma cobrança positiva nas demais fontes oficiais antes de
    concluir que a tarifa é realmente zero.
    """
    if not isinstance(order, dict):
        return None

    candidates: list[float] = []
    for key in ("marketplace_fee", "sale_fee_amount"):
        v = _money_number(order.get(key))
        if v is not None:
            candidates.append(abs(v))

    payment_total = 0.0
    payment_found = False
    payments = order.get("payments") if isinstance(order.get("payments"), list) else []
    for pay in payments:
        if not isinstance(pay, dict):
            continue
        for key in ("marketplace_fee", "fee_amount", "mercadopago_fee"):
            v = _money_number(pay.get(key))
            if v is not None and abs(v) > 0:
                payment_total += abs(v)
                payment_found = True
                break
    if payment_found:
        candidates.append(payment_total)

    item_fees = []
    for oi in order.get("order_items") or []:
        v = _ml_item_fee(oi) if isinstance(oi, dict) else None
        if v is not None:
            item_fees.append(abs(v))
    if item_fees:
        candidates.append(sum(item_fees))

    # Prioriza o total oficial do pedido quando ele é positivo. Se ele veio 0,
    # usa a cobrança positiva encontrada em payments/order_items.
    top = _money_number(order.get("marketplace_fee"))
    if top is not None and abs(top) > 0:
        return abs(top)
    for value in candidates:
        if value > 0:
            return value
    return 0.0 if candidates else None



def _ml_billing_month_key(date_value: str) -> str:
    dt = _ml_parse_datetime(date_value)
    if dt is None:
        dt = datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}-01"


# V15.13 — sincronização financeira por período.
# A API de Billing devolve charge_info e sales_info como irmãos; por isso a
# conciliação precisa preservar o lançamento completo, não apenas charge_info.
def _ml_billing_v1513_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        vals = payload.get("results")
        if isinstance(vals, list):
            return [x for x in vals if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _ml_billing_v1513_ids(row: dict[str, Any]) -> tuple[list[str], str, str]:
    order_ids: list[str] = []
    sales = row.get("sales_info") if isinstance(row.get("sales_info"), list) else []
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        oid = str(sale.get("order_id") or "").strip()
        if oid and oid not in order_ids:
            order_ids.append(oid)
    items = row.get("items_info") if isinstance(row.get("items_info"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("order_id") or "").strip()
        if oid and oid not in order_ids:
            order_ids.append(oid)
    shipping = row.get("shipping_info") if isinstance(row.get("shipping_info"), dict) else {}
    nested_order = shipping.get("order") if isinstance(shipping.get("order"), dict) else {}
    oid = str(nested_order.get("order_id") or "").strip()
    if oid and oid not in order_ids:
        order_ids.append(oid)
    pack_id = str(shipping.get("pack_id") or "").strip()
    shipment_id = str(shipping.get("shipping_id") or "").strip()
    return order_ids, pack_id, shipment_id


def _ml_billing_v1513_sale_fee_for_order(row: dict[str, Any], order_id: str) -> dict[str, float | None]:
    sales = row.get("sales_info") if isinstance(row.get("sales_info"), list) else []
    for sale in sales:
        if not isinstance(sale, dict) or str(sale.get("order_id") or "").strip() != str(order_id):
            continue
        sf = sale.get("sale_fee") if isinstance(sale.get("sale_fee"), dict) else {}
        return {
            "net": _money_number(sf.get("net")),
            "gross": _money_number(sf.get("gross")),
            "rebate": _money_number(sf.get("rebate")),
            "discount": _money_number(sf.get("discount")),
        }
    return {"net": None, "gross": None, "rebate": None, "discount": None}


def _ml_billing_credito_do_lancamento(detail_type: str, sub_type: str) -> int:
    """V18.36: crédito é o que o DADO diz, não o documento que pedimos.

    O Billing do MLB devolve os mesmos lançamentos para document_type=BILL e
    para CREDIT_NOTE (os dois sincronismos de setembro voltaram com 5.773 itens
    idênticos). Marcar crédito pelo parâmetro da requisição fazia cada cobrança
    ganhar uma cópia "negativa" que anulava a original — a tarifa de venda do
    mês inteiro virava R$ 0,00. Quem diz que é estorno é detail_type=BONUS
    (sub_type começando com B: BVVML, BVVPRC, BXDE...).
    """
    dt = str(detail_type or "").upper()
    if dt in ("BONUS", "BONIFICATION", "CREDIT"):
        return 1
    sub = str(sub_type or "").upper()
    if sub.startswith("B") and (sub in ML_FATURA_TARIFA or sub in ML_FATURA_FRETE or sub in ML_FATURA_ADS):
        return 1
    return 0


def _ml_billing_dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Um lançamento por detail_id. O mesmo detail_id chega em BILL e em
    CREDIT_NOTE; somar os dois dobraria (ou zeraria) o custo da venda."""
    vistos: set[str] = set()
    saida: list[dict[str, Any]] = []
    for e in entries:
        chave = str(e.get("detail_id") or "")
        if not chave:
            chave = "|".join(str(e.get(c) or "") for c in
                             ("order_id", "detail_amount", "detail_sub_type", "transaction_detail"))
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(e)
    return saida


def _ml_billing_v1513_sync_state(period_key: str, document_type: str) -> dict[str, Any] | None:
    with DB_LOCK:
        row = DB.execute(
            "SELECT * FROM ml_billing_period_sync WHERE period_key=? AND document_type=?",
            (str(period_key), str(document_type).upper()),
        ).fetchone()
    return dict(row) if row else None


def _ml_billing_v1513_sync_period(period_key: str, document_type: str, seller_id: str = "", force: bool = False) -> dict[str, Any]:
    """Baixa sequencialmente todo o Billing ML do período e persiste cada lançamento.

    Usa limit=1000, sort_by=ID/order_by=ASC e avança exatamente pelo last_id da
    resposta, conforme a paginação oficial. O endpoint do período identifica o
    vendedor pelo token; seller_id é mantido apenas como metadado local.
    """
    doc = str(document_type or "BILL").upper()
    if doc not in ("BILL", "CREDIT_NOTE"):
        raise ValueError("document_type inválido")
    now_epoch = int(time.time())
    state = _ml_billing_v1513_sync_state(period_key, doc)
    # Cache: aberto/recente pode ser atualizado algumas vezes por dia; histórico
    # não precisa ser baixado a cada abertura do dashboard.
    if state and not force:
        age = now_epoch - int(state.get("synced_at_epoch") or 0)
        try:
            current_key = _ml_billing_month_key(now_iso())
        except Exception:
            current_key = period_key
        ttl = 4 * 3600 if period_key == current_key else 24 * 3600
        # V18.34: período que ficou pela metade (429) não pode dormir 4 h — ele
        # volta em 10 min e continua de onde parou, senão o mês nunca fecha.
        if str(state.get("status") or "") == "partial":
            ttl = 600
        if age >= 0 and age < ttl and str(state.get("status") or "") in ("ok", "partial", "empty"):
            return {"ok": True, "cached": True, **state}

    # Retoma de onde parou quando a página anterior morreu no meio.
    from_id = "0"
    if state and str(state.get("status") or "") == "partial" and str(state.get("last_id") or "").isdigit():
        from_id = str(state.get("last_id"))
    pages = 0
    saved = 0
    last_seen = ""
    diag: list[str] = []
    status = "ok"
    for _ in range(250):
        q = {
            "document_type": doc,
            "limit": "1000",
            "from_id": from_id,
            "sort_by": "ID",
            "order_by": "ASC",
        }
        endpoint = f"/billing/integration/periods/key/{quote(period_key)}/group/ML/details?" + urlencode(q)
        # V18.34: o Billing devolve HTTP 429 quando a sincronização puxa muitas
        # páginas seguidas — foi assim que setembro parou no meio, com 854 dos 987
        # pedidos. Espera e tenta de novo a MESMA página antes de desistir.
        payload = None
        ultimo_erro = ""
        for tentativa in range(4):
            try:
                payload = ml_api_get(endpoint)
                ultimo_erro = ""
                break
            except Exception as exc:
                ultimo_erro = str(exc)[:500]
                if "429" not in ultimo_erro or tentativa == 3:
                    break
                time.sleep(3 * (tentativa + 1))
        if payload is None:
            status = "partial" if saved else "error"
            diag.append(ultimo_erro)
            break
        pages += 1
        results = _ml_billing_v1513_results(payload)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            status = "partial"
            diag.append("API retornou avisos: " + json.dumps(errors, ensure_ascii=False)[:700])

        for row in results:
            charge = row.get("charge_info") if isinstance(row.get("charge_info"), dict) else {}
            did = str(charge.get("detail_id") or "").strip()
            if not did:
                # Ainda deduplica respostas atípicas sem detail_id.
                did = hashlib.sha1(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            amount = _money_number(charge.get("detail_amount"))
            dtype = str(charge.get("detail_type") or "").upper()
            sub = str(charge.get("detail_sub_type") or "").upper()
            trans = str(charge.get("transaction_detail") or "")
            debited = str(charge.get("debited_from_operation") or "").upper()
            # V18.37: a data do lançamento (charge_info.creation_date_time). É o que
            # permite somar a publicidade DO PERÍODO escolhido, e não da fatura inteira.
            ddata = str(charge.get("creation_date_time") or charge.get("detail_date") or "")[:10]
            order_ids, pack_id, shipment_id = _ml_billing_v1513_ids(row)
            # Lançamentos sem order continuam úteis para auditoria do período, mas
            # não entram no lucro de uma venda específica.
            ids_for_storage = order_ids or [""]
            for oid in ids_for_storage:
                sf = _ml_billing_v1513_sale_fee_for_order(row, oid) if oid else {"net":None,"gross":None,"rebate":None,"discount":None}
                with DB_LOCK:
                    DB.execute(
                        """INSERT INTO ml_billing_period_detail
                        (period_key,document_type,detail_id,order_id,pack_id,shipment_id,detail_amount,detail_type,detail_sub_type,
                         transaction_detail,debited_from_operation,sale_fee_net,sale_fee_gross,sale_fee_rebate,sale_fee_discount,is_credit,detail_date,raw_json,synced_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(period_key,document_type,detail_id,order_id) DO UPDATE SET
                        pack_id=excluded.pack_id,shipment_id=excluded.shipment_id,detail_amount=excluded.detail_amount,
                        detail_type=excluded.detail_type,detail_sub_type=excluded.detail_sub_type,
                        transaction_detail=excluded.transaction_detail,debited_from_operation=excluded.debited_from_operation,
                        sale_fee_net=COALESCE(excluded.sale_fee_net,ml_billing_period_detail.sale_fee_net),
                        sale_fee_gross=COALESCE(excluded.sale_fee_gross,ml_billing_period_detail.sale_fee_gross),
                        sale_fee_rebate=COALESCE(excluded.sale_fee_rebate,ml_billing_period_detail.sale_fee_rebate),
                        sale_fee_discount=COALESCE(excluded.sale_fee_discount,ml_billing_period_detail.sale_fee_discount),
                        is_credit=excluded.is_credit,detail_date=excluded.detail_date,
                        raw_json=excluded.raw_json,synced_at=excluded.synced_at""",
                        (period_key, doc, did, oid, pack_id, shipment_id, amount, dtype, sub, trans, debited,
                         sf.get("net"), sf.get("gross"), sf.get("rebate"), sf.get("discount"),
                         _ml_billing_credito_do_lancamento(dtype, sub), ddata,
                         json.dumps(row, ensure_ascii=False)[:120000], now_iso()),
                    )
                    DB.commit()
                saved += 1

        if not results:
            break
        next_id = ""
        if isinstance(payload, dict):
            v = payload.get("last_id")
            if v not in (None, ""):
                next_id = str(v)
        if not next_id or next_id == from_id or next_id == last_seen:
            break
        last_seen = from_id
        from_id = next_id
        # Uma página menor que o limite normalmente encerra o período; ainda
        # respeitamos last_id quando fornecido, mas a página seguinte vazia fecha.

    if status != "error" and saved == 0:
        status = "empty" if status == "ok" else status
    with DB_LOCK:
        DB.execute(
            """INSERT INTO ml_billing_period_sync(period_key,document_type,seller_id,status,last_id,item_count,diagnostic,synced_at,synced_at_epoch)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(period_key,document_type) DO UPDATE SET
            seller_id=excluded.seller_id,status=excluded.status,last_id=excluded.last_id,item_count=excluded.item_count,
            diagnostic=excluded.diagnostic,synced_at=excluded.synced_at,synced_at_epoch=excluded.synced_at_epoch""",
            (period_key, doc, str(seller_id or ""), status, from_id, saved, "; ".join(diag)[:1600], now_iso(), now_epoch),
        )
        DB.commit()
    return {"ok": status != "error", "cached": False, "period_key": period_key, "document_type": doc,
            "status": status, "pages": pages, "item_count": saved, "last_id": from_id, "diagnostic": "; ".join(diag)}


# ---------------------------------------------------------------------------
# V18.34: sub_types REAIS da fatura do MLB, levantados na conta 224625106 em
# 28/09/2026 (setembro inteiro, 4.867 lançamentos). Até aqui o código procurava
# "CV"/"BV" e textos como "cobrança por venda" — nada disso existe na fatura
# brasileira, e a tarifa de venda inteira (R$ 11,4 mil em setembro) ia parar em
# "outros" em vez da coluna Taxas.
#
#   CVVML  / BVVML   Custo por vender no Mercado Livre     -> tarifa de venda
#   CVVPRC / BVVPRC  Custo por cobrar no Mercado Pago      -> tarifa de venda
#   CVVFNU / BVVFNU  Taxa de recebimento                   -> tarifa de venda
#   CXDE   / BXDE    Tarifa de envio extra/intermunicipal  -> frete
#   CFFE   / BFFE    Tarifa de envio extra/intermunicipal  -> frete
#   PADS   / BPADS   Campanha de publicidade Product Ads   -> Ads
#   CFONPN / BFONPN  Taxa de parcelamento (= ao acréscimo) -> NEUTRO (ver abaixo)
#   CFWA, CDIFAL e demais                                  -> outros custos
#
# O "B" na frente é o cancelamento do "C" correspondente (detail_type BONUS) e
# entra como crédito, abatendo o custo.
# ---------------------------------------------------------------------------
ML_FATURA_TARIFA = {"CV", "BV", "CVVML", "BVVML", "CVVPRC", "BVVPRC", "CVVFNU", "BVVFNU"}
ML_FATURA_FRETE = {"CXD", "BXD", "CE", "BE", "CFLX", "BFLX", "CXDE", "BXDE", "CFFE", "BFFE"}
ML_FATURA_ADS = {"PADS", "BPADS"}
# A taxa de parcelamento é cobrada do vendedor e devolvida pelo acréscimo que o
# COMPRADOR paga — no detalhe da venda o ML mostra as duas somadas em R$ 0,00.
# Como o acréscimo não entra no nosso faturamento (usamos o preço do produto),
# cobrar a taxa aqui puniria o lucro duas vezes. Fica de fora dos dois lados.
ML_FATURA_NEUTRO = {"CFONPN", "BFONPN"}


def _ml_billing_v1513_is_shipping(sub: str, transaction: str, raw_json: str = "") -> bool:
    sub = str(sub or "").upper()
    if sub in ML_FATURA_FRETE:
        return True
    if sub in ML_FATURA_TARIFA or sub in ML_FATURA_ADS or sub in ML_FATURA_NEUTRO:
        return False
    text = (str(transaction or "") + " " + str(raw_json or "")[:3000]).lower()
    return any(x in text for x in ("mercado envios", "shipping", "frete", "envío", "envio"))


def _ml_billing_v1513_is_sale_fee(sub: str, transaction: str) -> bool:
    sub = str(sub or "").upper()
    if sub in ML_FATURA_TARIFA:
        return True
    if sub in ML_FATURA_FRETE or sub in ML_FATURA_ADS or sub in ML_FATURA_NEUTRO:
        return False
    text = str(transaction or "").lower()
    return any(x in text for x in ("cobrança por venda", "cobrança por vender", "custo por vender",
                                   "custo por cobrar", "taxa de recebimento", "cargo por venta",
                                   "tarifa de venda", "sale fee", "selling fee", "comissão",
                                   "comision", "commission"))


def _ml_billing_is_publicidade(sub: str, dtype: str, transaction: str) -> bool:
    """V18.30: lançamento de Publicidade (Product Ads) na fatura do ML."""
    if str(sub or "").upper() in ML_FATURA_ADS:
        return True
    texto = (str(transaction or "") + " " + str(dtype or "") + " " + str(sub or "")).lower()
    return any(x in texto for x in ("publicidade", "product ads", "mercado ads", "advertising", "publicidad"))


def _ml_billing_is_neutro(sub: str) -> bool:
    """Lançamento que se anula com o que o comprador pagou (parcelamento)."""
    return str(sub or "").upper() in ML_FATURA_NEUTRO


def _ml_billing_v1513_consolidate(order_id: str, pack_id: str = "") -> dict[str, Any]:
    oid = str(order_id or "").strip(); pid = str(pack_id or "").strip()
    if not oid:
        return {"sale_fee": None, "other_cost": None, "bonus": 0.0, "status": "missing", "diagnostic": "Order ID ausente."}
    with DB_LOCK:
        rows = DB.execute(
            "SELECT * FROM ml_billing_period_detail WHERE order_id=? ORDER BY period_key,document_type,detail_id",
            (oid,),
        ).fetchall()
    entries = [dict(r) for r in rows]
    # Se o lançamento é apenas de pack, só usa quando não existe vínculo direto
    # de order. Custos de envio do pack são excluídos e vêm de shipment costs.
    if not entries and pid:
        with DB_LOCK:
            rows = DB.execute(
                "SELECT * FROM ml_billing_period_detail WHERE order_id='' AND pack_id=? ORDER BY period_key,document_type,detail_id",
                (pid,),
            ).fetchall()
        entries = [dict(r) for r in rows]
    return _ml_billing_consolidar_lancamentos(entries)


def _ml_billing_consolidar_lancamentos(entries: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: C901
    """Regra única de leitura da fatura do ML para UMA venda.

    V18.30: extraído de _ml_billing_v1513_consolidate para que o Financeiro possa
    conciliar o período inteiro em lote com exatamente a mesma regra, e passou a
    devolver também o frete da fatura (shipping) — que o cálculo por pedido
    continua ignorando, porque lá o frete vem do custo do shipment.
    """
    entries = _ml_billing_dedupe(entries)
    if not entries:
        return {"sale_fee": None, "other_cost": None, "bonus": 0.0, "shipping": None, "publicidade": 0.0,
                "status": "missing", "diagnostic": "Venda ainda não localizada no Billing sincronizado."}

    # sale_fee.net é a fonte mais direta para MLB e pode se repetir em vários
    # lançamentos da mesma order. Não somamos: preservamos os valores únicos e
    # usamos o maior líquido positivo quando a API repete a composição.
    net_values = sorted({round(abs(float(v)), 2) for v in (_money_number(e.get("sale_fee_net")) for e in entries) if v is not None and abs(v) > 0})
    fee_charge = 0.0; fee_credit = 0.0
    other_charge = 0.0; other_credit = 0.0; bonus = 0.0
    ship_charge = 0.0; ship_credit = 0.0; pub_charge = 0.0
    labels: list[str] = []
    for e in entries:
        amount = _money_number(e.get("detail_amount"))
        if amount is None or abs(amount) <= 0:
            continue
        amount = abs(amount)
        sub = str(e.get("detail_sub_type") or "").upper()
        trans = str(e.get("transaction_detail") or "")
        dtype = str(e.get("detail_type") or "").upper()
        # V18.36: o dado manda. Linhas gravadas antes desta versao tem is_credit
        # vindo do document_type pedido, que era sempre falso para metade delas.
        credit = bool(_ml_billing_credito_do_lancamento(dtype, sub))
        if trans and trans not in labels:
            labels.append(trans)
        if _ml_billing_is_neutro(sub):
            # V18.34: taxa de parcelamento — se anula com o acréscimo do comprador.
            continue
        if _ml_billing_v1513_is_shipping(sub, trans, str(e.get("raw_json") or "")):
            # V18.30: o frete da fatura passou a ser somado (o cálculo por pedido
            # continua usando o custo do shipment; quem usa este número é o Financeiro).
            if credit: ship_credit += amount
            else: ship_charge += amount
            continue
        if _ml_billing_is_publicidade(sub, dtype, trans):
            # V18.34: PADS vem lançado POR PEDIDO na fatura do MLB. Sai daqui como
            # bucket próprio (não entra em "outros") e é a fonte de Ads do
            # Financeiro enquanto a API de Publicidade não estiver liberada.
            if credit: pub_charge -= amount
            else: pub_charge += amount
            continue
        if _ml_billing_v1513_is_sale_fee(sub, trans):
            if credit: fee_credit += amount
            else: fee_charge += amount
            if credit: bonus += amount
            continue
        # Só considera outros débitos efetivamente atribuídos à operação. NO é
        # informação financeira do comprador/financiamento e não custo do seller.
        debited = str(e.get("debited_from_operation") or "").upper()
        if not credit and debited == "NO":
            continue
        if credit:
            other_credit += amount; bonus += amount
        else:
            other_charge += amount

    if net_values:
        fee = max(net_values)
        fee_source = "Billing ML sale_fee.net"
    elif fee_charge > 0 or fee_credit > 0:
        fee = max(0.0, fee_charge - fee_credit)
        fee_source = "Billing ML CV/BV"
    else:
        fee = None; fee_source = "Pendente de conciliação"
    other = other_charge - other_credit
    status = "ok" if fee is not None else "partial"
    diagnostic = f"{fee_source}; {len(entries)} lançamento(s) conciliado(s)."
    if labels:
        diagnostic += " " + "; ".join(labels[:6])
    shipping = ship_charge - ship_credit
    return {"sale_fee": round(fee, 2) if fee is not None else None,
            "other_cost": round(other, 2), "bonus": round(bonus, 2), "status": status,
            "diagnostic": diagnostic, "source": fee_source, "entries": len(entries),
            "shipping": round(shipping, 2) if (ship_charge or ship_credit) else None,
            "publicidade": round(max(0.0, pub_charge), 2)}


def _ml_profitability_api_get(path_or_url: str, *, headers: dict[str, str] | None = None, attempts: int = 3) -> dict[str, Any] | list[Any]:
    """GET resiliente usado somente pela rentabilidade ML.

    Repete falhas transitórias/limites e preserva a renovação normal do token
    feita por ml_api_get. Não transforma erro em custo zero.
    """
    last_exc: Exception | None = None
    for attempt in range(max(1, int(attempts or 1))):
        try:
            return ml_api_get(path_or_url, headers=headers)
        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            retryable = any(x in text for x in ("http 429", "http 500", "http 502", "http 503", "http 504", "timeout", "timed out", "falha de conexão", "tempor"))
            if attempt >= attempts - 1 or not retryable:
                raise
            time.sleep(0.7 + attempt * 1.1)
    if last_exc:
        raise last_exc
    raise RuntimeError("Falha ao consultar Mercado Livre")


def _ml_seller_shipping_cost(shipment_id: str, seller_id: str = "") -> float | None:
    if not shipment_id:
        return None
    try:
        data = _ml_profitability_api_get(f"/shipments/{quote(shipment_id)}/costs", headers={"x-format-new": "true"}, attempts=3)
    except Exception:
        return None
    return _ml_shipping_cost_parse(data, seller_id)


def _ml_shipping_cost_parse(data: Any, seller_id: str = "") -> float | None:
    """Quanto do envio ficou POR CONTA DO VENDEDOR.

    V18.31: extraído de _ml_seller_shipping_cost para que a captura no ato da
    venda e a conciliação usem exatamente a mesma leitura do payload.
    """
    if not isinstance(data, dict):
        return None
    # Fonte oficial para conciliação: senders[].cost. Se houver vários senders,
    # prioriza o seller da conta e só soma quando não for possível identificá-lo.
    senders = data.get("senders")
    if isinstance(senders, list):
        matched = []
        all_vals = []
        for x in senders:
            if not isinstance(x, dict):
                continue
            v = _money_number(x.get("cost"))
            if v is None:
                continue
            v = abs(v)
            all_vals.append(v)
            uid = str(x.get("user_id") or "").strip()
            if seller_id and uid == str(seller_id):
                matched.append(v)
        if matched:
            return sum(matched)
        if all_vals:
            return sum(all_vals)
    # Compatibilidade com contratos legados, sem usar gross_amount como custo.
    for key in ("sender_cost", "seller_cost"):
        v = _money_number(data.get(key))
        if v is not None:
            return abs(v)
    return None


def _ml_profitability_buyer_name_fast(order: dict[str, Any]) -> str:
    """Nome leve para listagem de rentabilidade, sem chamadas extras à API.

    Buscar período deve consultar apenas /orders/search. Dados fiscais completos
    continuam sendo tratados pelos fluxos próprios do sistema e não bloqueiam
    a abertura da Rentabilidade.
    """
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    first = str(buyer.get("first_name") or "").strip()
    last = str(buyer.get("last_name") or "").strip()
    name = " ".join(x for x in (first, last) if x).strip()
    if name:
        return name
    return str(buyer.get("nickname") or buyer.get("id") or "").strip()


def _ml_order_effective_sale(order: dict[str, Any]) -> tuple[float | None, datetime | None]:
    """Valor/data efetivos da venda para conciliar com o painel de vendas do ML.

    Para uma venda paga, usa pagamentos aprovados. O valor de mercadoria vem de
    transaction_amount (não total_paid_amount, que pode incluir frete/juros).
    A data é date_approved do pagamento; fallback para date_closed da order.
    Reembolsos aprovados são abatidos via transaction_amount_refunded.
    """
    if not isinstance(order, dict):
        return None, None
    payments = order.get("payments") if isinstance(order.get("payments"), list) else []
    total = 0.0
    found = False
    approved_dates: list[datetime] = []
    for pay in payments:
        if not isinstance(pay, dict):
            continue
        pstatus = str(pay.get("status") or "").lower()
        if pstatus != "approved":
            continue
        amount = _money_number(pay.get("transaction_amount"))
        if amount is None:
            continue
        refunded = _money_number(pay.get("transaction_amount_refunded")) or 0.0
        net = max(0.0, float(amount) - max(0.0, float(refunded)))
        total += net
        found = True
        d = _ml_parse_datetime(pay.get("date_approved") or pay.get("date_created"))
        if d is not None:
            approved_dates.append(d)
    if found:
        # Em pagamentos fracionados, a venda passa a contar quando o último pagamento
        # aprovado completa a order.
        sale_dt = max(approved_dates) if approved_dates else _ml_parse_datetime(order.get("date_closed"))
        return total, sale_dt
    # Compatibilidade com orders antigas sem payments completos.
    if str(order.get("status") or "").lower() in ("paid", "partially_refunded"):
        return _money_number(order.get("paid_amount") or order.get("total_amount")), _ml_parse_datetime(order.get("date_closed"))
    return None, None


def _ml_profitability_recent_sales(days: int = 30, limit: int = 2000, date_from: str = "", date_to: str = "", status_filter: str = "valid") -> list[dict[str, Any]]:
    """Busca vendas ML do período sem depender de uma única forma de consulta.

    V16.16:
    1) tenta /orders/search com order.date_created.from/to;
    2) para filtros com mais de um status, consulta cada status separadamente;
    3) se o endpoint com datas falhar ou vier vazio, faz varredura paginada por data_desc
       e filtra o intervalo localmente (sem Billing por pedido, portanto continua leve).
    """
    _, seller_id = ml_access_token()
    br_tz = timezone(timedelta(hours=-3))
    now_br = datetime.now(br_tz)

    def parse_day(value: str, end: bool = False) -> datetime | None:
        value = str(value or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                d = datetime.strptime(value[:10], fmt).replace(tzinfo=br_tz)
                if end:
                    d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
                return d
            except Exception:
                pass
        return None

    dt_to = parse_day(date_to, True) or now_br.replace(hour=23, minute=59, second=59, microsecond=999000)
    dt_from = parse_day(date_from, False)
    if dt_from is None:
        dt_from = (dt_to - timedelta(days=max(1, min(int(days or 30), 365)) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if dt_from > dt_to:
        a, b = dt_from, dt_to
        dt_from = b.replace(hour=0, minute=0, second=0, microsecond=0)
        dt_to = a.replace(hour=23, minute=59, second=59, microsecond=999000)
    floor = now_br - timedelta(days=365)
    if dt_from < floor:
        dt_from = floor

    lim = max(1, min(int(limit or 2000), 5000))
    status_filter = str(status_filter or "valid").strip().lower()
    status_map = {
        "valid": ("paid",),
        "paid": ("paid",),
        "partially_refunded": ("partially_refunded",),
        "cancelled": ("cancelled",),
        "all": (),
    }
    allowed_statuses = status_map.get(status_filter, status_map["valid"])
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []

    def add_orders(orders):
        nonlocal out
        for order in orders or []:
            if not isinstance(order, dict):
                continue
            status = str(order.get("status") or "").lower()
            if allowed_statuses and status not in allowed_statuses:
                continue
            # Data da venda: pagamento aprovado / date_closed. Isso acompanha melhor
            # o painel "Vendas de hoje" do vendedor do que date_created.
            effective_total, sale_dt = _ml_order_effective_sale(order)
            if sale_dt is None:
                continue
            sale_br = sale_dt.astimezone(br_tz)
            if sale_br < dt_from or sale_br > dt_to:
                continue
            oid = str(order.get("id") or "").strip()
            if not oid:
                continue
            buyer = _ml_profitability_buyer_name_fast(order)
            shipment = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
            shipment_id = str(shipment.get("id") or "").strip()
            pack_id = str(order.get("pack_id") or "").strip()
            items = order.get("order_items") if isinstance(order.get("order_items"), list) else []
            if not items:
                key = (oid, "")
                if key not in seen:
                    seen.add(key)
                    out.append({"order_id": oid, "pack_id": pack_id, "sku": "", "product": "", "client": buyer,
                        "date": sale_br.isoformat(), "shipment_id": shipment_id,
                        "qty": 1, "sale_amount_api": effective_total,
                        "line_fee_api": _ml_order_marketplace_fee(order), "order_summary": order, "status": status})
                continue
            order_fee = _ml_order_marketplace_fee(order)
            # Faturamento deve fechar com o total oficial da order. Somar simplesmente
            # unit_price * quantity pode divergir quando há desconto/promoção.
            official_order_total = effective_total
            line_infos = []
            order_lines_total = 0.0
            for idx, oi in enumerate(items):
                if not isinstance(oi, dict):
                    continue
                item = oi.get("item") if isinstance(oi.get("item"), dict) else {}
                sku = str(_ml_order_item_sku(oi) or "").strip()
                title = str(item.get("title") or oi.get("title") or "").strip()
                try: qty = max(1, int(float(oi.get("quantity") or 1)))
                except Exception: qty = 1
                unit_price = _money_number(oi.get("unit_price"))
                raw_line_total = (unit_price * qty) if unit_price is not None else None
                if raw_line_total is not None: order_lines_total += raw_line_total
                line_infos.append((idx, oi, item, sku, title, qty, raw_line_total))
            for idx, oi, item, sku, title, qty, raw_line_total in line_infos:
                line_identity = sku or str(item.get("id") or "") + ":" + str(item.get("variation_id") or idx)
                key = (oid, line_identity)
                if key in seen: continue
                seen.add(key)
                # Distribui o total oficial da order entre as linhas. Assim a soma das
                # linhas é exatamente order.total_amount e a order nunca é duplicada.
                if official_order_total is not None:
                    if len(line_infos) == 1:
                        line_total = official_order_total
                    elif raw_line_total is not None and order_lines_total > 0:
                        line_total = official_order_total * (raw_line_total / order_lines_total)
                    else:
                        line_total = official_order_total / max(1, len(line_infos))
                else:
                    line_total = raw_line_total
                item_fee = _ml_item_fee(oi)
                if item_fee is None and order_fee is not None:
                    if len(line_infos) == 1: item_fee = order_fee
                    elif raw_line_total is not None and order_lines_total > 0: item_fee = order_fee * (raw_line_total / order_lines_total)
                out.append({"order_id": oid, "pack_id": pack_id, "sku": sku, "product": title, "client": buyer,
                    "date": sale_br.isoformat(), "shipment_id": shipment_id,
                    "qty": qty, "sale_amount_api": line_total, "line_fee_api": item_fee, "order_summary": order, "status": status,
                    "order_total_api": official_order_total})
                if len(out) >= lim:
                    return

    def search_with_dates(status_value: str | None):
        page_size = 50
        for page in range(120):
            params = {
                "seller": seller_id,
                "order.date_closed.from": dt_from.isoformat(timespec="milliseconds"),
                "order.date_closed.to": dt_to.isoformat(timespec="milliseconds"),
                "sort": "date_desc", "limit": str(page_size), "offset": str(page * page_size),
            }
            if status_value:
                params["order.status"] = status_value
            result = ml_api_get(f"/orders/search?{urlencode(params)}")
            orders = result.get("results") if isinstance(result, dict) else []
            if not isinstance(orders, list) or not orders:
                break
            add_orders(orders)
            if len(out) >= lim:
                return
            paging = result.get("paging") if isinstance(result, dict) else {}
            total = int((paging or {}).get("total") or 0) if isinstance(paging, dict) else 0
            if len(orders) < page_size or (total and (page + 1) * page_size >= total):
                break

    first_error = None
    statuses_to_query = list(allowed_statuses) if allowed_statuses else [None]
    for st in statuses_to_query:
        try:
            search_with_dates(st)
        except Exception as exc:
            first_error = first_error or exc

    # Fallback oficial: varredura por data_desc sem from/to. Evita zerar a tela quando
    # o ML rejeita/instabiliza o filtro de datas, sem voltar às consultas pesadas por pedido.
    if not out:
        page_size = 50
        for st in statuses_to_query:
            reached_before = False
            for page in range(60):
                params = {"seller": seller_id, "sort": "date_desc", "limit": str(page_size), "offset": str(page * page_size)}
                if st:
                    params["order.status"] = st
                try:
                    result = ml_api_get(f"/orders/search?{urlencode(params)}")
                except Exception as exc:
                    first_error = first_error or exc
                    break
                orders = result.get("results") if isinstance(result, dict) else []
                if not isinstance(orders, list) or not orders:
                    break
                add_orders(orders)
                for order in orders:
                    if not isinstance(order, dict):
                        continue
                    _, odt = _ml_order_effective_sale(order)
                    if odt is not None and odt.astimezone(br_tz) < dt_from:
                        reached_before = True
                        break
                if reached_before or len(out) >= lim or len(orders) < page_size:
                    break

    if not out and first_error is not None:
        raise first_error
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return out[:lim]

# ---------------------------------------------------------------------------
# V17.99: Dashboard de vendas — Mercado Livre e Shopee. As vendas (valor,
# quantidade e data real) ficam numa tabela local para a tela abrir rápido e o
# histórico não depender de o marketplace responder. A janela recente é
# regravada a cada ciclo, então venda cancelada/reembolsada some sozinha.
# ---------------------------------------------------------------------------
VENDAS_SYNC_INTERVAL = max(120, min(3600, int(os.getenv("VENDAS_SYNC_INTERVAL", "600"))))
VENDAS_HISTORY_DAYS = max(30, min(365, int(os.getenv("VENDAS_HISTORY_DAYS", "365"))))
VENDAS_SYNC_LOCK = threading.Lock()
VENDAS_ML_LIMIT = 5000
BR_TZ = timezone(timedelta(hours=-3))
VENDAS_MARKETPLACES = {"ml": "Mercado Livre", "shopee": "Shopee"}
# A Shopee só aceita janelas de até 15 dias em get_order_list.
VENDAS_JANELA_MAX = {"ml": 30, "shopee": 15}
# Pedido Shopee que conta como venda: pago e não cancelado/devolvido.
SHOPEE_STATUS_VENDA = {"READY_TO_SHIP", "PROCESSED", "RETRY_SHIP", "SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED"}


def _vendas_meta(key: str, value: str | None = None) -> str:
    with DB_LOCK:
        if value is not None:
            DB.execute(
                "INSERT INTO vendas_meta_v1799(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            DB.commit()
            return value
        row = DB.execute("SELECT value FROM vendas_meta_v1799 WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def _vendas_gravar(marketplace: str, date_from: str, date_to: str, records: list[tuple[Any, ...]], complete: bool) -> None:
    """Só apaga a janela quando a busca veio completa; senão apenas acrescenta,
    para nunca perder venda por uma busca truncada."""
    with DB_LOCK:
        if complete:
            DB.execute("DELETE FROM vendas_v1799 WHERE marketplace=? AND sale_day BETWEEN ? AND ?", (marketplace, date_from, date_to))
        DB.executemany(
            "INSERT OR REPLACE INTO vendas_v1799(marketplace,order_id,line_key,pack_id,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        DB.commit()


def vendas_ml_sync_window(date_from: str, date_to: str) -> dict[str, Any]:
    """Regrava as vendas ML de date_from a date_to (AAAA-MM-DD, horário de Brasília)."""
    # O /orders/search do ML recusa order.status=partially_refunded (HTTP 400,
    # visto em produção). Busca sem filtro de status e fica só com as vendas
    # pagas — com reembolso parcial o valor já vem líquido do reembolso.
    todas = _ml_profitability_recent_sales(date_from=date_from, date_to=date_to, status_filter="all", limit=VENDAS_ML_LIMIT)
    complete = len(todas) < VENDAS_ML_LIMIT
    rows = [r for r in todas if str(r.get("status") or "").lower() in ("paid", "partially_refunded")]
    stamp = now_iso()
    records: dict[tuple[str, str], tuple[Any, ...]] = {}
    custos_venda: dict[tuple[str, str], tuple[Any, ...]] = {}
    for r in rows:
        order_id = str(r.get("order_id") or "").strip()
        sale_at = str(r.get("date") or "")
        if not order_id or len(sale_at) < 10:
            continue
        sku = str(r.get("sku") or "").strip()
        anuncio = ""
        order = r.get("order_summary") if isinstance(r.get("order_summary"), dict) else {}
        for oi in order.get("order_items") or []:
            if isinstance(oi, dict) and str(_ml_order_item_sku(oi) or "").strip() == sku:
                item = oi.get("item") if isinstance(oi.get("item"), dict) else {}
                anuncio = str(item.get("id") or "").strip()
                break
        line_key = sku or anuncio or "?"
        try:
            qty = int(r.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        amount = _money_number(r.get("sale_amount_api")) or 0.0
        records[(order_id, line_key)] = (
            "ml", order_id, line_key, str(r.get("pack_id") or ""), sale_at, sale_at[:10], sku, anuncio,
            str(r.get("product") or "")[:300], qty, round(float(amount), 2), str(r.get("status") or ""), stamp,
        )
        # V18.31: a tarifa de venda já veio dentro da própria order (sale_fee por
        # unidade). Guardar aqui é de graça e é o que o ML mostra no ato da venda.
        custos_venda[(order_id, line_key)] = (
            order_id, line_key, sku, _money_number(r.get("line_fee_api")),
            str(r.get("shipment_id") or ""), sale_at[:10], stamp,
        )
    _vendas_gravar("ml", date_from, date_to, list(records.values()), complete)
    # Nunca pode derrubar a sincronização de vendas: é só o custo, e ele se
    # completa sozinho no ciclo seguinte.
    try:
        if custos_venda:
            with DB_LOCK:
                DB.executemany(
                    "INSERT INTO ml_venda_custo_v1830(order_id,line_key,sku,sale_fee,shipment_id,sale_day,captured_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(order_id,line_key) DO UPDATE SET sku=excluded.sku,"
                    "sale_fee=COALESCE(excluded.sale_fee,ml_venda_custo_v1830.sale_fee),"
                    "shipment_id=CASE WHEN excluded.shipment_id<>'' THEN excluded.shipment_id ELSE ml_venda_custo_v1830.shipment_id END,"
                    "sale_day=excluded.sale_day,captured_at=excluded.captured_at",
                    list(custos_venda.values()),
                )
                DB.commit()
    except Exception:
        pass
    return {"marketplace": "ml", "de": date_from, "ate": date_to, "linhas": len(records), "completa": complete}


def vendas_shopee_sync_window(date_from: str, date_to: str) -> dict[str, Any]:
    """Regrava as vendas Shopee de date_from a date_to (no máximo 15 dias).

    get_order_list lista os pedidos criados na janela; get_order_detail traz os
    itens com preço. Valor da linha = preço com desconto × quantidade (produto,
    sem frete — mesmo critério do ML).
    """
    d1 = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=BR_TZ)
    d2 = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=BR_TZ) + timedelta(days=1) - timedelta(seconds=1)
    if (d2 - d1).days >= VENDAS_JANELA_MAX["shopee"]:
        raise ValueError("A Shopee aceita no máximo 15 dias por consulta.")
    order_sns: list[str] = []
    cursor = ""
    complete = False
    for _ in range(200):
        params: dict[str, Any] = {
            "time_range_field": "create_time", "time_from": int(d1.timestamp()), "time_to": int(d2.timestamp()),
            "page_size": 100, "response_optional_fields": "order_status",
        }
        if cursor:
            params["cursor"] = cursor
        data = shopee_api_get("/api/v2/order/get_order_list", params)
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        for o in response.get("order_list") or []:
            if not isinstance(o, dict):
                continue
            sn = str(o.get("order_sn") or "").strip()
            if sn and str(o.get("order_status") or "").upper() in SHOPEE_STATUS_VENDA:
                order_sns.append(sn)
        next_cursor = str(response.get("next_cursor") or "").strip()
        if not response.get("more") or not next_cursor or next_cursor == cursor:
            complete = True
            break
        cursor = next_cursor
    stamp = now_iso()
    records: dict[tuple[str, str], tuple[Any, ...]] = {}
    for order in shopee_order_details(list(dict.fromkeys(order_sns))) if order_sns else []:
        sn = str(order.get("order_sn") or "").strip()
        status = str(order.get("order_status") or "").upper()
        if not sn or (status and status not in SHOPEE_STATUS_VENDA):
            continue
        try:
            created = datetime.fromtimestamp(int(order.get("create_time") or 0), BR_TZ)
        except (TypeError, ValueError, OSError):
            continue
        if created.year < 2000:
            continue
        sale_at = created.isoformat()
        for idx, it in enumerate(order.get("item_list") or []):
            if not isinstance(it, dict):
                continue
            sku = str(it.get("model_sku") or it.get("item_sku") or "").strip()
            anuncio = str(it.get("item_id") or "").strip()
            line_key = f"{anuncio}:{it.get('model_id') or ''}" if anuncio else (sku or str(idx))
            try:
                qty = int(it.get("model_quantity_purchased") or 0)
            except (TypeError, ValueError):
                qty = 0
            price = _money_number(it.get("model_discounted_price"))
            if price is None or price <= 0:
                price = _money_number(it.get("model_original_price")) or 0.0
            title = str(it.get("item_name") or "").strip()
            model = str(it.get("model_name") or "").strip()
            records[(sn, line_key)] = (
                "shopee", sn, line_key, "", sale_at, sale_at[:10], sku, anuncio,
                (f"{title} — {model}" if model else title)[:300], qty, round(float(price) * qty, 2), status, stamp,
            )
    _vendas_gravar("shopee", date_from, date_to, list(records.values()), complete)
    return {"marketplace": "shopee", "de": date_from, "ate": date_to, "linhas": len(records), "completa": complete}


VENDAS_SYNC_WINDOW = {"ml": vendas_ml_sync_window, "shopee": vendas_shopee_sync_window}


def _vendas_sync_intervalo(marketplace: str, inicio: date, fim: date) -> list[dict[str, Any]]:
    """Divide o intervalo em janelas que o marketplace aceita."""
    feito = []
    passo = VENDAS_JANELA_MAX[marketplace]
    atual = inicio
    while atual <= fim:
        ate = min(fim, atual + timedelta(days=passo - 1))
        feito.append(VENDAS_SYNC_WINDOW[marketplace](atual.isoformat(), ate.isoformat()))
        atual = ate + timedelta(days=1)
    return feito


def vendas_marketplaces_conectados() -> list[str]:
    out = []
    try:
        row = ml_integration_row()
        if row and str(row["access_token"] or "").strip():
            out.append("ml")
    except Exception:
        pass
    try:
        if shopee_status_payload().get("connected"):
            out.append("shopee")
    except Exception:
        pass
    return out


def vendas_sync_marketplace(marketplace: str) -> list[dict[str, Any]]:
    """Um ciclo de um marketplace: últimos 3 dias sempre; 30 dias uma vez por dia
    (reembolsos/cancelamentos tardios); e uma janela de histórico por ciclo."""
    hoje = datetime.now(BR_TZ).date()
    feito = _vendas_sync_intervalo(marketplace, hoje - timedelta(days=2), hoje)
    if _vendas_meta(f"resync_30d_{marketplace}") != hoje.isoformat():
        feito += _vendas_sync_intervalo(marketplace, hoje - timedelta(days=30), hoje - timedelta(days=3))
        _vendas_meta(f"resync_30d_{marketplace}", hoje.isoformat())
    mais_antigo = hoje - timedelta(days=VENDAS_HISTORY_DAYS - 1)
    try:
        carregado_ate = datetime.strptime(_vendas_meta(f"historico_desde_{marketplace}") or "", "%Y-%m-%d").date()
    except ValueError:
        carregado_ate = hoje - timedelta(days=2)
    if carregado_ate > mais_antigo:
        fim = carregado_ate - timedelta(days=1)
        inicio = max(mais_antigo, fim - timedelta(days=VENDAS_JANELA_MAX[marketplace] - 1))
        feito.append(VENDAS_SYNC_WINDOW[marketplace](inicio.isoformat(), fim.isoformat()))
        _vendas_meta(f"historico_desde_{marketplace}", inicio.isoformat())
    _vendas_meta(f"ultima_sincronizacao_{marketplace}", now_iso())
    _vendas_meta(f"ultimo_erro_{marketplace}", "")
    return feito


def vendas_sync_once() -> dict[str, Any]:
    if not VENDAS_SYNC_LOCK.acquire(blocking=False):
        return {"ok": False, "ocupado": True}
    try:
        resultado: dict[str, Any] = {"ok": True}
        for marketplace in vendas_marketplaces_conectados():
            try:
                resultado[marketplace] = vendas_sync_marketplace(marketplace)
            except Exception as exc:
                _vendas_meta(f"ultimo_erro_{marketplace}", f"{now_iso()} {str(exc)[:500]}")
                resultado[marketplace] = {"erro": str(exc)[:500]}
        return resultado
    finally:
        VENDAS_SYNC_LOCK.release()


def vendas_historico_completo(marketplace: str) -> bool:
    try:
        desde = datetime.strptime(_vendas_meta(f"historico_desde_{marketplace}") or "", "%Y-%m-%d").date()
    except ValueError:
        return False
    return desde <= datetime.now(BR_TZ).date() - timedelta(days=VENDAS_HISTORY_DAYS - 1)


def vendas_sync_loop(stop_event: threading.Event) -> None:
    if stop_event.wait(45):
        return
    while not stop_event.is_set():
        conectados: list[str] = []
        try:
            conectados = vendas_marketplaces_conectados()
            if conectados:
                vendas_sync_once()
        except Exception as exc:
            # V18.56: antes o erro era engolido sem rastro.
            print(f"[vendas-sync] falhou: {exc}", file=sys.stderr, flush=True)
        # V18.22: esta linha ficava FORA do try. Se levantasse, a thread morria
        # calada e as vendas paravam de sincronizar até alguém reiniciar o serviço
        # — foi assim que o relatório de 22/09 saiu com o dia congelado às 19h.
        carregando = False
        try:
            carregando = any(not vendas_historico_completo(m) for m in conectados)
        except Exception:
            pass
        if stop_event.wait(60 if carregando else VENDAS_SYNC_INTERVAL):
            break


def vendas_dashboard_payload(date_from: str, date_to: str, marketplace: str = "") -> dict[str, Any]:
    """Resumo de vendas do período (todos os marketplaces ou um) + período anterior."""
    d1 = datetime.strptime(date_from, "%Y-%m-%d").date()
    d2 = datetime.strptime(date_to, "%Y-%m-%d").date()
    if d1 > d2:
        d1, d2 = d2, d1
    marketplace = marketplace if marketplace in VENDAS_MARKETPLACES else ""
    dias = (d2 - d1).days + 1
    p2 = d1 - timedelta(days=1)
    p1 = p2 - timedelta(days=dias - 1)
    produto = "COALESCE(NULLIF(sku,''), NULLIF(anuncio,''), '(sem SKU)')"
    pedido = "marketplace || ':' || order_id"
    filtro = " AND marketplace=?" if marketplace else ""
    extra = (marketplace,) if marketplace else ()

    def periodo(a: date, b: date) -> tuple[Any, ...]:
        return (a.isoformat(), b.isoformat()) + extra

    def resumo(a: date, b: date) -> dict[str, Any]:
        row = DB.execute(
            f"SELECT COUNT(DISTINCT {pedido}) AS pedidos, COALESCE(SUM(qty),0) AS unidades, "
            f"COALESCE(SUM(amount),0) AS faturamento FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?{filtro}",
            periodo(a, b),
        ).fetchone()
        pedidos = int(row["pedidos"] or 0)
        fat = round(float(row["faturamento"] or 0), 2)
        return {"pedidos": pedidos, "unidades": int(row["unidades"] or 0), "faturamento": fat,
                "ticket_medio": round(fat / pedidos, 2) if pedidos else 0.0}

    def top(ordem: str) -> list[dict[str, Any]]:
        rows = DB.execute(
            f"SELECT {produto} AS sku, MAX(title) AS titulo, SUM(qty) AS unidades, SUM(amount) AS faturamento, "
            f"COUNT(DISTINCT {pedido}) AS pedidos, SUM(CASE WHEN marketplace='ml' THEN qty ELSE 0 END) AS un_ml, "
            f"SUM(CASE WHEN marketplace='shopee' THEN qty ELSE 0 END) AS un_shopee FROM vendas_v1799 "
            f"WHERE sale_day BETWEEN ? AND ?{filtro} GROUP BY {produto} ORDER BY {ordem} DESC LIMIT 20",
            periodo(d1, d2),
        ).fetchall()
        return [{"sku": r["sku"], "titulo": r["titulo"] or "", "unidades": int(r["unidades"] or 0),
                 "faturamento": round(float(r["faturamento"] or 0), 2), "pedidos": int(r["pedidos"] or 0),
                 "por_marketplace": {"ml": int(r["un_ml"] or 0), "shopee": int(r["un_shopee"] or 0)}} for r in rows]

    atual = resumo(d1, d2)
    anterior = resumo(p1, p2)
    por_dia_raw: dict[str, dict[str, Any]] = {}
    for r in DB.execute(
        f"SELECT sale_day, marketplace, COUNT(DISTINCT order_id) AS pedidos, SUM(qty) AS unidades, SUM(amount) AS faturamento "
        f"FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?{filtro} GROUP BY sale_day, marketplace",
        periodo(d1, d2),
    ).fetchall():
        dia = por_dia_raw.setdefault(r["sale_day"], {})
        dia[r["marketplace"]] = {"pedidos": int(r["pedidos"] or 0), "unidades": int(r["unidades"] or 0),
                                 "faturamento": round(float(r["faturamento"] or 0), 2)}
    por_dia = []
    for i in range(dias):
        dia = (d1 + timedelta(days=i)).isoformat()
        canais = por_dia_raw.get(dia, {})
        por_dia.append({
            "dia": dia,
            "pedidos": sum(c["pedidos"] for c in canais.values()),
            "unidades": sum(c["unidades"] for c in canais.values()),
            "faturamento": round(sum(c["faturamento"] for c in canais.values()), 2),
            "por_marketplace": {m: canais.get(m, {"pedidos": 0, "unidades": 0, "faturamento": 0.0}) for m in VENDAS_MARKETPLACES},
        })
    por_marketplace = []
    for m, nome in VENDAS_MARKETPLACES.items():
        if marketplace and m != marketplace:
            continue
        row = DB.execute(
            f"SELECT COUNT(DISTINCT order_id) AS pedidos, COALESCE(SUM(qty),0) AS unidades, COALESCE(SUM(amount),0) AS faturamento "
            f"FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ? AND marketplace=?",
            (d1.isoformat(), d2.isoformat(), m),
        ).fetchone()
        por_marketplace.append({"marketplace": m, "nome": nome, "pedidos": int(row["pedidos"] or 0),
                                "unidades": int(row["unidades"] or 0), "faturamento": round(float(row["faturamento"] or 0), 2)})
    skus_vendidos = DB.execute(
        f"SELECT COUNT(DISTINCT {produto}) AS n FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?{filtro}",
        periodo(d1, d2),
    ).fetchone()["n"]
    conectados = vendas_marketplaces_conectados()
    sincronizacao = {}
    for m in VENDAS_MARKETPLACES:
        primeiro = DB.execute("SELECT MIN(sale_day) AS d FROM vendas_v1799 WHERE marketplace=?", (m,)).fetchone()["d"]
        sincronizacao[m] = {
            "conectado": m in conectados,
            "ultima": _vendas_meta(f"ultima_sincronizacao_{m}"),
            "erro": _vendas_meta(f"ultimo_erro_{m}"),
            "historico_desde": primeiro or "",
            "historico_completo": vendas_historico_completo(m),
        }
    return {
        "ok": True,
        "marketplace": marketplace or "todos",
        "periodo": {"inicio": d1.isoformat(), "fim": d2.isoformat(), "dias": dias},
        "periodo_anterior": {"inicio": p1.isoformat(), "fim": p2.isoformat()},
        "resumo": {**atual, "skus": int(skus_vendidos or 0)},
        "anterior": anterior,
        "por_marketplace": por_marketplace,
        "por_dia": por_dia,
        "top_unidades": top("unidades"),
        "top_faturamento": top("faturamento"),
        "sincronizacao": sincronizacao,
    }


# ---------------------------------------------------------------------------
# V18.10: custos REAIS de venda por marketplace, para a gaveta de detalhe do
# Dashboard (mais vendidos / maior faturamento). Cada marketplace cobra diferente:
#   - Mercado Livre: tarifa de venda + Mercado Envios. Já capturado por pedido em
#     ml_profitability_snapshot (ml_fee, seller_shipping_cost).
#   - Shopee: comissão + taxa de serviço + frete. Vem da API get_escrow_detail;
#     escrow_amount é a RENDA líquida do vendedor (a autoridade). Buscado sob
#     demanda e cacheado (o escrow é estável depois de pago).
# O custo do produto (Apollo/Precificador) é o MESMO nos dois e entra no front.
# ---------------------------------------------------------------------------
SHOPEE_ESCROW_TTL_SEG = 30 * 24 * 3600


def _shopee_escrow_num(oi: dict[str, Any], *keys: str) -> float:
    for k in keys:
        v = oi.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    return 0.0


def _shopee_escrow_parse(order_income: dict[str, Any]) -> dict[str, float]:
    oi = order_income or {}
    selling = _shopee_escrow_num(oi, "order_selling_price", "original_price", "order_original_price", "buyer_total_amount")
    # V18.35: quando existe ajuste, é ele que diz o que realmente entrou.
    escrow = _shopee_escrow_num(oi, "escrow_amount_after_adjustment", "escrow_amount")
    commission = _shopee_escrow_num(oi, "commission_fee")
    service = _shopee_escrow_num(oi, "service_fee")
    transaction = _shopee_escrow_num(oi, "seller_transaction_fee")
    actual_ship = _shopee_escrow_num(oi, "actual_shipping_fee", "final_shipping_fee")
    rebate = _shopee_escrow_num(oi, "shopee_shipping_rebate")
    buyer_ship = _shopee_escrow_num(oi, "buyer_paid_shipping_fee")
    # Frete líquido que sobra para o vendedor (o comprador/Shopee cobrem parte).
    shipping_net = max(0.0, round(actual_ship - rebate - buyer_ship, 2))
    # V18.35: comissão do programa de afiliados/AMS. É marketing, não tarifa, e
    # estava fora da conta: sozinha era 3,5% do preço em setembro (R$ 654,29).
    ams = _shopee_escrow_num(oi, "order_ams_commission_fee", "ams_commission_fee")
    return {"selling_price": round(selling, 2), "escrow_amount": round(escrow, 2),
            "commission_fee": round(commission, 2), "service_fee": round(service, 2),
            "transaction_fee": round(transaction, 2), "shipping_net": shipping_net,
            "ams_commission": round(ams, 2)}


def shopee_escrow_lookup(order_sn: str, permitir_api: bool = True) -> dict[str, float] | None:
    """Custos reais de um pedido Shopee (get_escrow_detail), com cache local.
    Devolve None quando não há dado nem foi possível buscar."""
    order_sn = str(order_sn or "").strip()
    if not order_sn:
        return None
    campos = ("selling_price", "escrow_amount", "commission_fee", "service_fee", "transaction_fee",
              "shipping_net", "ams_commission")
    agora = int(time.time())
    with DB_LOCK:
        row = DB.execute("SELECT * FROM shopee_escrow_v1810 WHERE order_sn=?", (order_sn,)).fetchone()
    if row and not row["erro"] and (agora - int(row["fetched_epoch"] or 0) < SHOPEE_ESCROW_TTL_SEG):
        return {c: (row[c] if row[c] is not None else 0.0) for c in campos}
    if not permitir_api:
        return {c: (row[c] if row[c] is not None else 0.0) for c in campos} if row and not row["erro"] else None
    try:
        data = shopee_api_get("/api/v2/payment/get_escrow_detail", {"order_sn": order_sn})
        oi = ((data or {}).get("response") or {}).get("order_income") or {}
        parsed = _shopee_escrow_parse(oi)
        with DB_LOCK:
            DB.execute(
                """INSERT INTO shopee_escrow_v1810
                   (order_sn,selling_price,escrow_amount,commission_fee,service_fee,transaction_fee,shipping_net,ams_commission,order_income_json,fetched_at,fetched_epoch,erro)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'')
                   ON CONFLICT(order_sn) DO UPDATE SET selling_price=excluded.selling_price,escrow_amount=excluded.escrow_amount,
                   commission_fee=excluded.commission_fee,service_fee=excluded.service_fee,transaction_fee=excluded.transaction_fee,
                   shipping_net=excluded.shipping_net,ams_commission=excluded.ams_commission,
                   order_income_json=excluded.order_income_json,fetched_at=excluded.fetched_at,
                   fetched_epoch=excluded.fetched_epoch,erro=''""",
                (order_sn, parsed["selling_price"], parsed["escrow_amount"], parsed["commission_fee"],
                 parsed["service_fee"], parsed["transaction_fee"], parsed["shipping_net"],
                 parsed["ams_commission"], json_dumps(oi)[:20000], now_iso(), agora),
            )
            DB.commit()
        return parsed
    except Exception as exc:
        with DB_LOCK:
            DB.execute(
                """INSERT INTO shopee_escrow_v1810(order_sn,order_income_json,fetched_at,fetched_epoch,erro)
                   VALUES(?,?,?,?,?) ON CONFLICT(order_sn) DO UPDATE SET erro=excluded.erro,
                   fetched_at=excluded.fetched_at,fetched_epoch=excluded.fetched_epoch""",
                (order_sn, "", now_iso(), agora, str(exc)[:300]),
            )
            DB.commit()
        return None


def _ml_custo_venda_por_pedido(order_id: str, sku: str = "") -> tuple[float, float, bool]:
    """(tarifa, frete, tem_dado) de um pedido do ML.

    V18.33: lê o custo capturado no ato da venda (V18.31). Quando o pedido é
    antigo e só existe no snapshot da antiga Rentabilidade, usa o que está lá —
    o histórico não se perde com a remoção daquela tela.
    """
    oid = str(order_id or "").strip()
    if not oid:
        return 0.0, 0.0, False
    with DB_LOCK:
        linhas = DB.execute(
            "SELECT sale_fee, shipment_id FROM ml_venda_custo_v1830 WHERE order_id=?", (oid,)).fetchall()
    tarifa = 0.0
    envios: set[str] = set()
    tem = False
    for l in linhas:
        v = _money_number(l["sale_fee"])
        if v is not None:
            tarifa += abs(v)
            tem = True
        if l["shipment_id"]:
            envios.add(str(l["shipment_id"]))
    frete = 0.0
    for sid in envios:
        with DB_LOCK:
            row = DB.execute("SELECT cost FROM ml_frete_envio_v1830 WHERE shipment_id=? AND cost IS NOT NULL",
                             (sid,)).fetchone()
        if row is not None:
            frete += float(row["cost"] or 0)
            tem = True
    if tem:
        return round(tarifa, 2), round(frete, 2), True
    with DB_LOCK:
        snap = DB.execute(
            "SELECT ml_fee, seller_shipping_cost FROM ml_profitability_snapshot WHERE order_id=? AND sku=?",
            (oid, str(sku or "")),
        ).fetchone()
    if snap and (snap["ml_fee"] is not None or snap["seller_shipping_cost"] is not None):
        return round(float(snap["ml_fee"] or 0), 2), round(float(snap["seller_shipping_cost"] or 0), 2), True
    return 0.0, 0.0, False


def dashboard_produto_custos(sku: str, dias: int = 30, max_shopee: int = 8) -> dict[str, Any]:
    """Custos reais de venda de UM SKU no período, separados por marketplace.
    ML: tarifa + envios (ml_profitability_snapshot). Shopee: comissão + serviço +
    frete e a renda líquida (get_escrow_detail). O custo do produto é o mesmo nos
    dois e entra no front (Precificador)."""
    sku = str(sku or "").strip()
    if not sku:
        return {"ok": False, "error": "SKU não informado."}
    dias = max(1, min(int(dias or 30), 400))
    hoje = datetime.now(BR_TZ).date()
    inicio = (hoje - timedelta(days=dias - 1)).isoformat()
    fim = hoje.isoformat()
    with DB_LOCK:
        rows = DB.execute(
            "SELECT marketplace, order_id, SUM(qty) AS qty, SUM(amount) AS amount "
            "FROM vendas_v1799 WHERE sku=? AND sale_day BETWEEN ? AND ? GROUP BY marketplace, order_id",
            (sku, inicio, fim),
        ).fetchall()

    ml = {"unidades": 0, "pedidos": 0, "vendas": 0.0, "tarifa": 0.0, "envios": 0.0, "com_dado": 0}
    shp = {"unidades": 0, "pedidos": 0, "vendas": 0.0, "comissao": 0.0, "servico": 0.0, "frete": 0.0, "renda": 0.0, "com_dado": 0}
    shopee_orders: list[dict[str, Any]] = []
    for r in rows:
        mp = str(r["marketplace"] or "").lower()
        qty = int(r["qty"] or 0)
        amount = float(r["amount"] or 0)
        if mp == "ml":
            ml["unidades"] += qty
            ml["pedidos"] += 1
            ml["vendas"] += amount
            # V18.33: a fonte passou a ser o custo capturado no ato da venda
            # (ml_venda_custo_v1830 + ml_frete_envio_v1830). O snapshot antigo da
            # Rentabilidade fica como fallback do que já estava gravado.
            tarifa, envio, tem = _ml_custo_venda_por_pedido(str(r["order_id"]), sku)
            if tem:
                ml["com_dado"] += 1
                ml["tarifa"] += tarifa
                ml["envios"] += envio
        elif mp == "shopee":
            shp["unidades"] += qty
            shp["pedidos"] += 1
            shp["vendas"] += amount
            shopee_orders.append({"order_sn": str(r["order_id"]), "amount": amount})

    # Shopee: busca o escrow dos pedidos mais recentes (limita o número de chamadas).
    for o in shopee_orders[:max_shopee]:
        esc = shopee_escrow_lookup(o["order_sn"])
        if esc:
            shp["com_dado"] += 1
            shp["comissao"] += esc.get("commission_fee", 0.0)
            shp["servico"] += esc.get("service_fee", 0.0) + esc.get("transaction_fee", 0.0)
            shp["frete"] += esc.get("shipping_net", 0.0)
            shp["renda"] += esc.get("escrow_amount", 0.0)

    def arred(d: dict[str, Any]) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}

    resultado: dict[str, Any] = {"ok": True, "sku": sku, "dias": dias}
    resultado["ml"] = arred({**ml, "renda": ml["vendas"] - ml["tarifa"] - ml["envios"]}) if ml["pedidos"] else None
    resultado["shopee"] = arred(shp) if shp["pedidos"] else None
    return resultado


# ---------------------------------------------------------------------------
# V18.30: PUBLICIDADE (Product Ads) do Mercado Livre.
#
# Levantado na documentação oficial em 28/09/2026:
#   GET /advertising/advertisers?product_id=PADS            (Api-Version: 1)
#   GET /advertising/{SITE}/advertisers/{ADV}/product_ads/campaigns/search
#       ?date_from&date_to&metrics=...                      (api-version: 2)
#   GET /advertising/{SITE}/product_ads/campaigns/{ID}/ad_groups/metrics
#       ?date_from=dia&date_to=dia&metrics=...              (um dia por chamada)
#   GET /advertising/{SITE}/product_ads/ad_groups/{ID}      (anúncio do ad group)
#
# Três coisas que a API impõe e que definem o desenho daqui:
#   1. As métricas só voltam 90 dias para trás — por isso o histórico fica no
#      nosso banco; a API é a fonte, o banco é a memória.
#   2. O dia só consolida às 10:00 GMT-3; o dia de hoje e o de ontem podem mudar.
#      A janela recente é REGRAVADA a cada sincronização, nunca somada.
#   3. Se a conta não tem Publicidade habilitada (ou o app não tem a permissão
#      Publicidade no DevCenter), o endpoint de anunciantes devolve 403/404
#      "No permissions found for user_id". Isso não é erro do sistema: fica
#      registrado e a tela mostra "Ads não conectado" com o gasto em branco —
#      nunca estimado.
# ---------------------------------------------------------------------------
ML_ADS_SITE_ID = os.getenv("ML_ADS_SITE_ID", "MLB").strip().upper() or "MLB"
ML_ADS_SYNC_INTERVAL = max(600, min(21600, int(os.getenv("ML_ADS_SYNC_INTERVAL", "3600"))))
# Dias sempre regravados (o ML ainda pode mexer neles).
ML_ADS_JANELA_RECENTE = max(2, min(15, int(os.getenv("ML_ADS_JANELA_RECENTE", "5"))))
ML_ADS_HISTORICO_DIAS = max(7, min(90, int(os.getenv("ML_ADS_HISTORICO_DIAS", "90"))))
# Gasto por anúncio custa uma chamada por campanha por dia: limitado por rodada.
ML_ADS_ITENS_DIAS = max(0, min(90, int(os.getenv("ML_ADS_ITENS_DIAS", "35"))))
ML_ADS_CHAMADAS_ITENS = max(0, min(400, int(os.getenv("ML_ADS_CHAMADAS_ITENS", "80"))))
ML_ADS_SYNC_LOCK = threading.Lock()
ML_ADS_METRICAS = "clicks,prints,cost,cpc,ctr,acos,units_quantity,total_amount"
ML_ADS_METRICAS_ITEM = "clicks,prints,cost,units_quantity,total_amount"
# V18.38: Shopee Ads (CPC). A janela recente é regravada; o histórico entra de
# trás para frente, uma fatia de 30 dias por rodada.
SHOPEE_ADS_JANELA_RECENTE = max(2, min(30, int(os.getenv("SHOPEE_ADS_JANELA_RECENTE", "7"))))
SHOPEE_ADS_HISTORICO_DIAS = max(30, min(365, int(os.getenv("SHOPEE_ADS_HISTORICO_DIAS", "180"))))


def _ml_ads_meta(key: str, value: str | None = None) -> str:
    with DB_LOCK:
        if value is not None:
            DB.execute(
                "INSERT INTO ml_ads_meta_v1830(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            DB.commit()
            return value
        row = DB.execute("SELECT value FROM ml_ads_meta_v1830 WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def _ml_ads_api(path: str, api_version: str = "2") -> Any:
    return ml_api_get(path, headers={"Api-Version": api_version})


def _ml_ads_int(valor: Any) -> int:
    try:
        return int(round(float(valor or 0)))
    except (TypeError, ValueError):
        return 0


def ml_ads_advertiser(force: bool = False) -> dict[str, Any]:
    """Anunciante de Product Ads da conta, com cache de 6 h.

    Nunca levanta: devolve {"ok": False, "erro": ...} para a tela poder dizer
    exatamente o que falta (permissão do app x Publicidade da conta).
    """
    if not force:
        try:
            cache = json.loads(_ml_ads_meta("advertiser") or "{}")
        except Exception:
            cache = {}
        if cache.get("ok") and int(time.time()) - int(cache.get("epoch") or 0) < 6 * 3600:
            return cache
    info: dict[str, Any]
    try:
        data = _ml_ads_api("/advertising/advertisers?product_id=PADS", "1")
    except Exception as exc:
        texto = str(exc)
        if "No permissions found" in texto or "HTTP 404" in texto or "HTTP 403" in texto:
            erro = ("A conta não tem Product Ads liberado para este aplicativo. "
                    "Marque a permissão Publicidade no DevCenter, reconecte o Mercado Livre "
                    "e confirme Publicidade em Meu perfil no ML.")
        else:
            erro = texto[:400]
        info = {"ok": False, "advertiser_id": "", "site_id": ML_ADS_SITE_ID, "nome": "", "conta": "",
                "erro": erro, "epoch": int(time.time())}
        _ml_ads_meta("advertiser", json_dumps(info))
        return info
    lista = data.get("advertisers") if isinstance(data, dict) else None
    lista = [a for a in (lista or []) if isinstance(a, dict)]
    escolhido = next((a for a in lista if str(a.get("site_id") or "").upper() == ML_ADS_SITE_ID), None)
    if escolhido is None and lista:
        escolhido = lista[0]
    escolhido = escolhido or {}
    info = {
        "ok": bool(escolhido.get("advertiser_id")),
        "advertiser_id": str(escolhido.get("advertiser_id") or ""),
        "site_id": str(escolhido.get("site_id") or ML_ADS_SITE_ID).upper(),
        "nome": str(escolhido.get("advertiser_name") or ""),
        "conta": str(escolhido.get("account_name") or ""),
        "erro": "" if escolhido.get("advertiser_id") else "A conta não devolveu anunciante de Product Ads.",
        "epoch": int(time.time()),
    }
    _ml_ads_meta("advertiser", json_dumps(info))
    return info


def _ml_ads_grupo_cache(ad_group_id: str) -> dict[str, str] | None:
    gid = str(ad_group_id or "").strip()
    if not gid:
        return None
    with DB_LOCK:
        row = DB.execute("SELECT * FROM ml_ads_grupo_v1830 WHERE ad_group_id=?", (gid,)).fetchone()
    if row and row["item_id"]:
        return {"item_id": row["item_id"], "sku": row["sku"], "titulo": row["titulo"], "campaign_id": row["campaign_id"]}
    return None


def _ml_ads_grupo_resolver(ad_group_id: str, site_id: str, vinculos: dict[str, str]) -> dict[str, str]:
    """ad_group -> anúncio (MLB) e SKU. Resolvido uma vez e guardado."""
    gid = str(ad_group_id or "").strip()
    if not gid:
        return {}
    em_cache = _ml_ads_grupo_cache(gid)
    if em_cache:
        return em_cache
    try:
        data = _ml_ads_api(f"/advertising/{quote(site_id)}/product_ads/ad_groups/{quote(gid)}")
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    item = str(data.get("ad_group_external_id") or "").strip().upper()
    dados = {
        "item_id": item,
        "sku": vinculos.get(item, ""),
        "titulo": str(data.get("title") or "")[:200],
        "campaign_id": str(data.get("campaign_id") or ""),
        "tipo": str(data.get("ad_group_type") or ""),
    }
    with DB_LOCK:
        DB.execute(
            "INSERT INTO ml_ads_grupo_v1830(ad_group_id,campaign_id,item_id,sku,titulo,tipo,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ad_group_id) DO UPDATE SET campaign_id=excluded.campaign_id,"
            "item_id=excluded.item_id,sku=CASE WHEN excluded.sku<>'' THEN excluded.sku ELSE ml_ads_grupo_v1830.sku END,"
            "titulo=excluded.titulo,tipo=excluded.tipo,updated_at=excluded.updated_at",
            (gid, dados["campaign_id"], dados["item_id"], dados["sku"], dados["titulo"], dados["tipo"], now_iso()),
        )
        DB.commit()
    return dados


def ml_ads_sync_dia(dia: str, advertiser: dict[str, Any], com_itens: bool = True, orcamento_chamadas: int = 0) -> dict[str, Any]:
    """Regrava o gasto de UM dia: total, por campanha e (quando couber) por anúncio."""
    adv = str(advertiser.get("advertiser_id") or "")
    site = str(advertiser.get("site_id") or ML_ADS_SITE_ID)
    if not adv:
        raise RuntimeError("Anunciante de Product Ads não identificado.")
    campanhas: list[dict[str, Any]] = []
    offset = 0
    for _ in range(40):
        q = urlencode({"limit": "50", "offset": str(offset), "date_from": dia, "date_to": dia,
                       "metrics": ML_ADS_METRICAS})
        data = _ml_ads_api(f"/advertising/{quote(site)}/advertisers/{quote(adv)}/product_ads/campaigns/search?{q}")
        results = [r for r in ((data or {}).get("results") or []) if isinstance(r, dict)] if isinstance(data, dict) else []
        campanhas.extend(results)
        total = _ml_ads_int(((data or {}).get("paging") or {}).get("total")) if isinstance(data, dict) else 0
        offset += len(results)
        if not results or offset >= total:
            break

    agora = now_iso()
    linhas = []
    for c in campanhas:
        m = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
        linhas.append((
            dia, str(c.get("id") or ""), str(c.get("name") or "")[:200], str(c.get("status") or ""),
            round(float(_money_number(m.get("cost")) or 0.0), 2), _ml_ads_int(m.get("clicks")),
            _ml_ads_int(m.get("prints")), _ml_ads_int(m.get("units_quantity")),
            round(float(_money_number(m.get("total_amount")) or 0.0), 2), agora,
        ))
    with DB_LOCK:
        # O dia é regravado inteiro: campanha que sumiu da resposta não fica para trás.
        DB.execute("DELETE FROM ml_ads_campanha_dia_v1830 WHERE dia=?", (dia,))
        if linhas:
            DB.executemany(
                "INSERT INTO ml_ads_campanha_dia_v1830(dia,campaign_id,nome,status,cost,clicks,prints,units,total_amount,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)", linhas)
        DB.execute(
            "INSERT INTO ml_ads_dia_v1830(dia,cost,clicks,prints,units,total_amount,campanhas,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(dia) DO UPDATE SET cost=excluded.cost,clicks=excluded.clicks,"
            "prints=excluded.prints,units=excluded.units,total_amount=excluded.total_amount,"
            "campanhas=excluded.campanhas,updated_at=excluded.updated_at",
            (dia, round(sum(x[4] for x in linhas), 2), sum(x[5] for x in linhas), sum(x[6] for x in linhas),
             sum(x[7] for x in linhas), round(sum(x[8] for x in linhas), 2), len(linhas), agora),
        )
        DB.commit()

    chamadas = 0
    itens_gravados = 0
    if com_itens and orcamento_chamadas > 0:
        vinculos = _ml_vinculos_mlb_sku()
        registros: list[tuple[Any, ...]] = []
        for c in campanhas:
            if chamadas >= orcamento_chamadas:
                break
            m = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
            if (_money_number(m.get("cost")) or 0.0) <= 0:
                continue
            cid = str(c.get("id") or "")
            if not cid:
                continue
            q = urlencode({"date_from": dia, "date_to": dia, "metrics": ML_ADS_METRICAS_ITEM})
            try:
                data = _ml_ads_api(f"/advertising/{quote(site)}/product_ads/campaigns/{quote(cid)}/ad_groups/metrics?{q}")
            except Exception:
                chamadas += 1
                continue
            chamadas += 1
            for r in ((data or {}).get("results") or []) if isinstance(data, dict) else []:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("ad_group_id") or "").strip()
                mm = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
                custo = round(float(_money_number(mm.get("cost")) or 0.0), 2)
                if not gid or custo <= 0:
                    continue
                # Grupo já conhecido não gasta chamada; só o desconhecido custa uma.
                grupo = _ml_ads_grupo_cache(gid) or {}
                if not grupo and chamadas < orcamento_chamadas:
                    grupo = _ml_ads_grupo_resolver(gid, site, vinculos)
                    chamadas += 1
                registros.append((dia, gid, cid, grupo.get("item_id", ""), grupo.get("sku", ""), custo,
                                  _ml_ads_int(mm.get("clicks")), _ml_ads_int(mm.get("prints")),
                                  _ml_ads_int(mm.get("units_quantity")),
                                  round(float(_money_number(mm.get("total_amount")) or 0.0), 2), agora))
        with DB_LOCK:
            DB.execute("DELETE FROM ml_ads_item_dia_v1830 WHERE dia=?", (dia,))
            if registros:
                DB.executemany(
                    "INSERT INTO ml_ads_item_dia_v1830(dia,ad_group_id,campaign_id,item_id,sku,cost,clicks,prints,units,total_amount,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)", registros)
            DB.commit()
        itens_gravados = len(registros)

    return {"dia": dia, "campanhas": len(linhas), "cost": round(sum(x[4] for x in linhas), 2),
            "itens": itens_gravados, "chamadas_itens": chamadas}


def ml_ads_sync_once(dias: int = 0) -> dict[str, Any]:
    """Sincroniza a janela recente e vai completando o histórico (até 90 dias)."""
    if not ML_ADS_SYNC_LOCK.acquire(blocking=False):
        return {"ok": True, "em_andamento": True}
    try:
        advertiser = ml_ads_advertiser()
        if not advertiser.get("ok"):
            _ml_ads_meta("ultimo_erro", str(advertiser.get("erro") or ""))
            return {"ok": False, "conectado": False, "erro": advertiser.get("erro") or ""}
        hoje = datetime.now(BR_TZ).date()
        recentes = [(hoje - timedelta(days=i)).isoformat() for i in range(max(1, int(dias or ML_ADS_JANELA_RECENTE)))]
        # Histórico: o dia mais antigo ainda não gravado, um por rodada.
        with DB_LOCK:
            ja_temos = {r["dia"] for r in DB.execute(
                "SELECT dia FROM ml_ads_dia_v1830 WHERE dia >= ?",
                ((hoje - timedelta(days=ML_ADS_HISTORICO_DIAS)).isoformat(),)).fetchall()}
        faltando = [(hoje - timedelta(days=i)).isoformat() for i in range(ML_ADS_HISTORICO_DIAS)]
        faltando = [d for d in faltando if d not in ja_temos and d not in recentes]
        alvo = recentes + faltando[:3]
        limite_itens = (hoje - timedelta(days=ML_ADS_ITENS_DIAS)).isoformat()
        orcamento = ML_ADS_CHAMADAS_ITENS
        feitos, erros = [], []
        for dia in alvo:
            try:
                r = ml_ads_sync_dia(dia, advertiser, com_itens=dia >= limite_itens, orcamento_chamadas=orcamento)
                orcamento = max(0, orcamento - int(r.get("chamadas_itens") or 0))
                feitos.append(r)
            except Exception as exc:
                erros.append({"dia": dia, "erro": str(exc)[:300]})
        _ml_ads_meta("ultima_sincronizacao", now_iso())
        _ml_ads_meta("ultimo_erro", json_dumps(erros[0]) if erros else "")
        return {"ok": not erros, "conectado": True, "dias": len(feitos), "erros": erros,
                "faltando_historico": max(0, len(faltando) - 3)}
    finally:
        ML_ADS_SYNC_LOCK.release()


def ml_ads_sync_loop(stop_event: threading.Event) -> None:
    if stop_event.wait(90):
        return
    while not stop_event.is_set():
        try:
            ml_ads_sync_once()
        except Exception as exc:
            print(f"[ml-ads-sync] falhou: {exc}", file=sys.stderr, flush=True)
        try:
            shopee_ads_sync_once()
        except Exception as exc:
            print(f"[shopee-ads-sync] falhou: {exc}", file=sys.stderr, flush=True)
        if stop_event.wait(ML_ADS_SYNC_INTERVAL):
            break


def shopee_ads_sync(dia_inicio: date, dia_fim: date) -> dict[str, Any]:
    """Investimento diário em Shopee Ads (CPC) da janela pedida.

    V18.38: o escrow do pedido só traz a comissão de afiliados (AMS). O CPC é
    crédito pré-pago, cobrado fora da venda, e só existe aqui. A janela é
    regravada: o dia de hoje ainda muda.
    """
    if dia_inicio > dia_fim:
        dia_inicio, dia_fim = dia_fim, dia_inicio
    gravados = 0
    atual = dia_inicio
    while atual <= dia_fim:
        fim_lote = min(atual + timedelta(days=29), dia_fim)
        data = shopee_api_get("/api/v2/ads/get_all_cpc_ads_daily_performance", {
            "start_date": atual.strftime("%d-%m-%Y"),
            "end_date": fim_lote.strftime("%d-%m-%Y"),
        })
        linhas = data.get("response") if isinstance(data, dict) else None
        agora = now_iso()
        registros = []
        for x in linhas if isinstance(linhas, list) else []:
            if not isinstance(x, dict):
                continue
            partes = str(x.get("date") or "").split("-")
            if len(partes) != 3:
                continue
            dia_iso = f"{partes[2]}-{partes[1]}-{partes[0]}"
            registros.append((dia_iso, round(float(x.get("expense") or 0), 2),
                              int(x.get("clicks") or 0), int(x.get("impression") or 0),
                              round(float(x.get("broad_gmv") or 0), 2),
                              int(x.get("broad_order") or 0), agora))
        if registros:
            with DB_LOCK:
                DB.executemany(
                    "INSERT INTO shopee_ads_dia_v1838(dia,expense,clicks,impressions,gmv,pedidos,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(dia) DO UPDATE SET expense=excluded.expense,"
                    "clicks=excluded.clicks,impressions=excluded.impressions,gmv=excluded.gmv,"
                    "pedidos=excluded.pedidos,updated_at=excluded.updated_at", registros)
                DB.commit()
            gravados += len(registros)
        atual = fim_lote + timedelta(days=1)
    return {"ok": True, "dias": gravados}


def shopee_ads_sync_once(dias: int = 0) -> dict[str, Any]:
    """Janela recente + o que faltar do histórico, uma fatia por rodada."""
    hoje = datetime.now(BR_TZ).date()
    recente = max(2, min(30, int(dias or SHOPEE_ADS_JANELA_RECENTE)))
    resultado = shopee_ads_sync(hoje - timedelta(days=recente - 1), hoje)
    with DB_LOCK:
        primeiro = DB.execute("SELECT MIN(dia) d FROM shopee_ads_dia_v1838").fetchone()["d"]
    limite = (hoje - timedelta(days=SHOPEE_ADS_HISTORICO_DIAS)).isoformat()
    if primeiro and primeiro > limite:
        fim = date.fromisoformat(primeiro) - timedelta(days=1)
        inicio = max(date.fromisoformat(limite), fim - timedelta(days=29))
        if inicio <= fim:
            hist = shopee_ads_sync(inicio, fim)
            resultado["historico"] = hist.get("dias", 0)
    return resultado


def ml_ads_status() -> dict[str, Any]:
    """Estado da coleta de Ads, para a tela dizer a verdade sobre o número."""
    try:
        advertiser = json.loads(_ml_ads_meta("advertiser") or "{}")
    except Exception:
        advertiser = {}
    with DB_LOCK:
        row = DB.execute("SELECT MIN(dia) a, MAX(dia) b, COUNT(*) c FROM ml_ads_dia_v1830").fetchone()
    return {
        "conectado": bool(advertiser.get("ok")),
        "advertiser_id": str(advertiser.get("advertiser_id") or ""),
        "conta": str(advertiser.get("conta") or ""),
        "erro": str(advertiser.get("erro") or "") or _ml_ads_meta("ultimo_erro"),
        "ultima_sincronizacao": _ml_ads_meta("ultima_sincronizacao"),
        "primeiro_dia": (row["a"] or "") if row else "",
        "ultimo_dia": (row["b"] or "") if row else "",
        "dias_gravados": int((row["c"] if row else 0) or 0),
        "limite_api_dias": ML_ADS_HISTORICO_DIAS,
    }


# ---------------------------------------------------------------------------
# V18.30: FINANCEIRO — faturamento, gastos e lucro (ML + Shopee).
#
# A conta, por linha de venda (vendas_v1799 é o faturamento; o marketplace é a
# fonte e a janela é regravada, então venda cancelada some sozinha):
#
#   faturamento    = preço pago pelo comprador pelos itens (sem frete do comprador)
#   CMV            = custo do produto × quantidade (Apollo; kit vira componentes)
#   taxa           = tarifa de venda do marketplace
#                    ML     -> fatura oficial (sale_fee.net / CV-BV) por pedido
#                    Shopee -> comissão + taxa de serviço + transação (escrow)
#   frete          = o que o VENDEDOR paga de envio
#                    ML     -> lançamentos de envio da mesma fatura
#                    Shopee -> frete líquido do escrow
#   outros         = demais débitos da fatura atribuídos ao pedido (menos publicidade)
#   ads            = Product Ads do dia (API de Publicidade), custo do PERÍODO
#   imposto        = FINANCEIRO_IMPOSTO_PERCENT % sobre a venda (padrão 12%)
#
#   lucro bruto    = faturamento − CMV
#   lucro líquido  = lucro bruto − taxa − frete − outros − ads − imposto
#
# Regra de ouro herdada do resto do sistema: o que não é conhecido fica NULO e
# aparece como pendência. Nada aqui é estimado, arredondado para cima nem
# preenchido por média — um lucro bonito e errado é pior que um número faltando.
# ---------------------------------------------------------------------------
try:
    FINANCEIRO_IMPOSTO_PERCENT = max(0.0, min(100.0, float(os.getenv("FINANCEIRO_IMPOSTO_PERCENT", "12") or 12)))
except (TypeError, ValueError):
    FINANCEIRO_IMPOSTO_PERCENT = 12.0
FINANCEIRO_SYNC_LOCK = threading.Lock()
FINANCEIRO_SHOPEE_POR_RODADA = max(10, min(500, int(os.getenv("FINANCEIRO_SHOPEE_POR_RODADA", "150"))))
FINANCEIRO_FRETE_POR_RODADA = max(5, min(300, int(os.getenv("FINANCEIRO_FRETE_POR_RODADA", "60"))))
FINANCEIRO_FRETE_INTERVALO = max(60, min(3600, int(os.getenv("FINANCEIRO_FRETE_INTERVALO", "300"))))


def _financeiro_periodos_fatura(d1: date, d2: date) -> list[str]:
    """Chaves de período da fatura do ML que podem conter os lançamentos.

    Inclui o mês seguinte ao fim do período: venda do dia 30 costuma ser cobrada
    na fatura do mês seguinte.
    """
    chaves: list[str] = []
    ano, mes = d1.year, d1.month
    while (ano, mes) <= (d2.year, d2.month):
        chaves.append(f"{ano:04d}-{mes:02d}-01")
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    chaves.append(f"{ano:04d}-{mes:02d}-01")
    return chaves


def _financeiro_chunks(valores: list[str], tamanho: int = 400):
    for i in range(0, len(valores), tamanho):
        yield valores[i:i + tamanho]


def ml_frete_envio_preencher(limite: int = 0) -> dict[str, Any]:
    """Busca o frete por conta do vendedor dos envios que ainda não têm valor.

    V18.31: uma chamada por ENVIO (/shipments/{id}/costs), guardada para sempre.
    É o mesmo número que o ML mostra na venda como "Tarifa do Mercado Envios
    (por sua conta)". Envio que deu erro é tentado de novo depois de 6 h.
    """
    limite = max(1, min(300, int(limite or FINANCEIRO_FRETE_POR_RODADA)))
    agora = int(time.time())
    with DB_LOCK:
        pendentes = [str(r["shipment_id"]) for r in DB.execute(
            "SELECT DISTINCT v.shipment_id FROM ml_venda_custo_v1830 v "
            "LEFT JOIN ml_frete_envio_v1830 f ON f.shipment_id = v.shipment_id "
            "WHERE v.shipment_id <> '' AND (f.shipment_id IS NULL OR (f.erro <> '' AND f.captured_epoch < ?)) "
            "ORDER BY v.sale_day DESC LIMIT ?", (agora - 6 * 3600, limite)).fetchall()]
    if not pendentes:
        return {"pendentes": 0, "buscados": 0}
    try:
        _, seller_id = ml_access_token()
    except Exception as exc:
        return {"pendentes": len(pendentes), "buscados": 0, "erro": str(exc)[:200]}
    buscados = 0
    for sid in pendentes:
        try:
            data = _ml_profitability_api_get(f"/shipments/{quote(sid)}/costs",
                                             headers={"x-format-new": "true"}, attempts=2)
            custo = _ml_shipping_cost_parse(data, str(seller_id or ""))
            erro = "" if custo is not None else "envio sem custo de remetente no payload"
        except Exception as exc:
            custo, erro = None, str(exc)[:200]
        with DB_LOCK:
            DB.execute(
                "INSERT INTO ml_frete_envio_v1830(shipment_id,cost,captured_at,captured_epoch,erro) VALUES(?,?,?,?,?) "
                "ON CONFLICT(shipment_id) DO UPDATE SET cost=excluded.cost,captured_at=excluded.captured_at,"
                "captured_epoch=excluded.captured_epoch,erro=excluded.erro",
                (sid, custo, now_iso(), agora, erro))
            DB.commit()
        if custo is not None:
            buscados += 1
    return {"pendentes": len(pendentes), "buscados": buscados}


def financeiro_frete_loop(stop_event: threading.Event) -> None:
    """Mantém o frete das vendas recentes preenchido, sem depender de ninguém abrir a tela."""
    if stop_event.wait(120):
        return
    while not stop_event.is_set():
        try:
            ml_frete_envio_preencher()
        except Exception as exc:
            print(f"[financeiro-frete] falhou: {exc}", file=sys.stderr, flush=True)
        if stop_event.wait(FINANCEIRO_FRETE_INTERVALO):
            break


def _financeiro_custos_venda_ml(order_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Tarifa e frete como o ML mostrou NO ATO DA VENDA, por pedido.

    Tarifa: soma do sale_fee das linhas do pedido (é o "Tarifa de venda total").
    Frete: custo do envio; quando o mesmo envio cobre vários pedidos (pack), o
    custo é dividido entre eles — senão o frete apareceria duplicado.
    """
    alvo = [str(o).strip() for o in order_ids if str(o).strip()]
    if not alvo:
        return {}
    linhas: list[dict[str, Any]] = []
    for lote in _financeiro_chunks(alvo):
        ph = ",".join("?" for _ in lote)
        with DB_LOCK:
            linhas.extend(dict(r) for r in DB.execute(
                f"SELECT order_id, line_key, sale_fee, shipment_id FROM ml_venda_custo_v1830 WHERE order_id IN ({ph})",
                tuple(lote)).fetchall())
    if not linhas:
        return {}
    envios = sorted({str(l["shipment_id"] or "") for l in linhas if l["shipment_id"]})
    custo_envio: dict[str, float] = {}
    pedidos_por_envio: dict[str, int] = {}
    for lote in _financeiro_chunks(envios):
        ph = ",".join("?" for _ in lote)
        with DB_LOCK:
            for r in DB.execute(f"SELECT shipment_id, cost FROM ml_frete_envio_v1830 WHERE shipment_id IN ({ph}) AND cost IS NOT NULL",
                                tuple(lote)).fetchall():
                custo_envio[str(r["shipment_id"])] = float(r["cost"] or 0)
            for r in DB.execute(
                    f"SELECT shipment_id, COUNT(DISTINCT order_id) AS pedidos FROM ml_venda_custo_v1830 "
                    f"WHERE shipment_id IN ({ph}) GROUP BY shipment_id", tuple(lote)).fetchall():
                pedidos_por_envio[str(r["shipment_id"])] = max(1, int(r["pedidos"] or 1))

    out: dict[str, dict[str, Any]] = {}
    envios_do_pedido: dict[str, set[str]] = {}
    for l in linhas:
        oid = str(l["order_id"])
        alvo_pedido = out.setdefault(oid, {"taxa": None, "frete": None, "linhas": 0})
        alvo_pedido["linhas"] += 1
        fee = _money_number(l["sale_fee"])
        if fee is not None:
            alvo_pedido["taxa"] = round((alvo_pedido["taxa"] or 0.0) + abs(fee), 2)
        sid = str(l["shipment_id"] or "")
        if sid:
            envios_do_pedido.setdefault(oid, set()).add(sid)
    for oid, sids in envios_do_pedido.items():
        total = 0.0
        achou = False
        for sid in sids:
            if sid in custo_envio:
                achou = True
                total += custo_envio[sid] / pedidos_por_envio.get(sid, 1)
        if achou:
            out[oid]["frete"] = round(total, 2)
    return out


def _financeiro_billing_ml(period_keys: list[str], order_ids: list[str],
                           dia_inicio: str = "", dia_fim: str = "") -> dict[str, Any]:
    """Concilia a fatura do ML do período: por pedido e o que não tem pedido.

    Usa a MESMA regra do cálculo por venda (_ml_billing_consolidar_lancamentos).
    O raw_json é truncado no SELECT porque aqui são milhares de lançamentos de
    uma vez; a classificação por sub_type e por transaction_detail não depende
    dele — ele é só o último fallback para reconhecer envio.
    """
    vazio = {"por_pedido": {},
             "sem_pedido": {"publicidade": 0.0, "outros": 0.0, "credito": 0.0, "itens": [], "por_dia": {}},
             "periodos": period_keys, "lancamentos": 0}
    if not period_keys:
        return vazio
    colunas = ("detail_id,order_id,detail_amount,detail_type,detail_sub_type,transaction_detail,"
               "debited_from_operation,sale_fee_net,is_credit,detail_date,"
               "substr(raw_json,1,1200) AS raw_json")
    ph_periodos = ",".join("?" for _ in period_keys)
    por_pedido_entries: dict[str, list[dict[str, Any]]] = {}
    alvo = {str(o).strip() for o in order_ids if str(o).strip()}
    total = 0
    for lote in _financeiro_chunks(sorted(alvo)):
        ph_ids = ",".join("?" for _ in lote)
        with DB_LOCK:
            rows = DB.execute(
                f"SELECT {colunas} FROM ml_billing_period_detail "
                f"WHERE period_key IN ({ph_periodos}) AND order_id IN ({ph_ids})",
                tuple(period_keys) + tuple(lote),
            ).fetchall()
        for r in rows:
            total += 1
            por_pedido_entries.setdefault(str(r["order_id"]), []).append(dict(r))
    por_pedido = {oid: _ml_billing_consolidar_lancamentos(ents) for oid, ents in por_pedido_entries.items()}

    # Lançamentos sem pedido: publicidade, mensalidades, ajustes do período.
    pub = 0.0
    outros = 0.0
    credito = 0.0
    itens: dict[str, float] = {}
    pub_por_dia: dict[str, float] = {}
    with DB_LOCK:
        rows = DB.execute(
            f"SELECT {colunas} FROM ml_billing_period_detail WHERE period_key IN ({ph_periodos}) AND order_id=''",
            tuple(period_keys),
        ).fetchall()
    for r in _ml_billing_dedupe([dict(x) for x in rows]):
        total += 1
        valor = _money_number(r["detail_amount"])
        if valor is None or abs(valor) <= 0:
            continue
        valor = abs(valor)
        # V18.37: fora da janela escolhida, o lançamento é de outro período. Sem
        # data (fatura antiga, antes do backfill) ele entra, para não sumir.
        dia_lanc = str(r["detail_date"] or "")
        if dia_lanc and dia_inicio and dia_fim and not (dia_inicio <= dia_lanc <= dia_fim):
            continue
        descricao = str(r["transaction_detail"] or r["detail_sub_type"] or r["detail_type"] or "Outros")[:120]
        if _ml_billing_credito_do_lancamento(str(r["detail_type"] or ""), str(r["detail_sub_type"] or "")):
            credito += valor
            continue
        if _ml_billing_is_neutro(str(r["detail_sub_type"] or "")):
            continue
        if _ml_billing_is_publicidade(str(r["detail_sub_type"] or ""), str(r["detail_type"] or ""), str(r["transaction_detail"] or "")):
            pub += valor
            if dia_lanc:
                pub_por_dia[dia_lanc] = round(pub_por_dia.get(dia_lanc, 0.0) + valor, 2)
        else:
            outros += valor
        itens[descricao] = round(itens.get(descricao, 0.0) + valor, 2)
    ordenados = sorted(itens.items(), key=lambda kv: -kv[1])[:20]
    return {"por_pedido": por_pedido,
            "sem_pedido": {"publicidade": round(pub, 2), "outros": round(outros, 2), "credito": round(credito, 2),
                           "itens": [{"descricao": k, "valor": v} for k, v in ordenados],
                           "por_dia": pub_por_dia},
            "periodos": period_keys, "lancamentos": total}


def _financeiro_custo_unitario(sku: str, produtos: dict[str, Any], reservas: dict[str, Any],
                               kits: dict[str, Any], cache: dict[str, Any]) -> tuple[float | None, str]:
    """Custo de UMA unidade do que foi vendido nesse anúncio (kit = soma dos
    componentes). Devolve (custo, origem); custo None = não sabemos, e aí a linha
    entra como pendência em vez de virar lucro inventado."""
    chave = str(sku or "").strip().upper()
    if chave in cache:
        return cache[chave]
    if not chave:
        cache[chave] = (None, "")
        return cache[chave]
    comps = (kits.get(str(sku or "").strip()) or {}).get("componentes")
    if not comps:
        partes = [x for x in chave.split("/") if x.strip()] if "/" in chave else [chave]
        comps = [{"sku": x, "quantidade": 1} for x in partes]
    total = 0.0
    origens: list[str] = []
    completo = True
    for c in comps:
        codigo = _compras_codigo(str(c.get("sku") or ""), produtos)
        if not codigo:
            completo = False
            continue
        base = (produtos.get(codigo) or {}).get("custo")
        custo, origem, _data = _compras_resolver_custo(base, reservas.get(codigo, {}))
        if custo is None:
            completo = False
            continue
        try:
            unidades = max(1, int(c.get("quantidade") or 1))
        except (TypeError, ValueError):
            unidades = 1
        total += float(custo) * unidades
        origens.append(origem)
    resultado = (round(total, 4), ("kit" if len(comps) > 1 else (origens[0] if origens else ""))) if (completo and total > 0) else (None, "")
    cache[chave] = resultado
    return resultado


def _financeiro_ads_periodo(d1: date, d2: date) -> dict[str, Any]:
    """Gasto com Product Ads no período: total, por dia e por SKU."""
    with DB_LOCK:
        dias = DB.execute("SELECT dia, cost FROM ml_ads_dia_v1830 WHERE dia BETWEEN ? AND ?",
                          (d1.isoformat(), d2.isoformat())).fetchall()
        por_sku = DB.execute(
            "SELECT UPPER(sku) AS sku, SUM(cost) AS cost FROM ml_ads_item_dia_v1830 "
            "WHERE dia BETWEEN ? AND ? AND sku<>'' GROUP BY UPPER(sku)",
            (d1.isoformat(), d2.isoformat())).fetchall()
        sem_sku = DB.execute(
            "SELECT COALESCE(SUM(cost),0) AS cost FROM ml_ads_item_dia_v1830 "
            "WHERE dia BETWEEN ? AND ? AND sku=''", (d1.isoformat(), d2.isoformat())).fetchone()
    return {
        "total": round(sum(float(r["cost"] or 0) for r in dias), 2),
        "por_dia": {str(r["dia"]): round(float(r["cost"] or 0), 2) for r in dias},
        "por_sku": {str(r["sku"] or ""): round(float(r["cost"] or 0), 2) for r in por_sku},
        "sem_sku": round(float((sem_sku["cost"] if sem_sku else 0) or 0), 2),
        "dias_com_dado": len(dias),
        "status": ml_ads_status(),
    }


def financeiro_payload(date_from: str, date_to: str, marketplace: str = "", limite_pedidos: int = 400) -> dict[str, Any]:
    """Faturamento, gastos e lucro do período, com a origem de cada número."""
    d1 = datetime.strptime(date_from, "%Y-%m-%d").date()
    d2 = datetime.strptime(date_to, "%Y-%m-%d").date()
    if d1 > d2:
        d1, d2 = d2, d1
    if (d2 - d1).days > 400:
        raise ValueError("Período muito longo (máximo 400 dias).")
    canal = marketplace if marketplace in VENDAS_MARKETPLACES else ""
    filtro = " AND marketplace=?" if canal else ""
    args: tuple[Any, ...] = (d1.isoformat(), d2.isoformat()) + ((canal,) if canal else ())
    with DB_LOCK:
        vendas = [dict(r) for r in DB.execute(
            "SELECT marketplace, order_id, line_key, sale_day, sku, anuncio, title, qty, amount "
            f"FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?{filtro} ORDER BY sale_day, order_id",
            args).fetchall()]

    produtos = _compras_produtos_precificador()
    reservas = _compras_custos_reserva(produtos)
    kits = full_kit_compositions([v["sku"] for v in vendas])
    cache_custo: dict[str, Any] = {}
    imposto_rate = FINANCEIRO_IMPOSTO_PERCENT / 100.0

    # --- custos por pedido -------------------------------------------------
    pedidos_ml = sorted({str(v["order_id"]) for v in vendas if v["marketplace"] == "ml"})
    pedidos_shopee = sorted({str(v["order_id"]) for v in vendas if v["marketplace"] == "shopee"})
    billing = _financeiro_billing_ml(_financeiro_periodos_fatura(d1, d2), pedidos_ml,
                                     d1.isoformat(), d2.isoformat()) if pedidos_ml else \
        {"por_pedido": {},
         "sem_pedido": {"publicidade": 0.0, "outros": 0.0, "credito": 0.0, "itens": [], "por_dia": {}},
         "periodos": [], "lancamentos": 0}
    escrow: dict[str, dict[str, Any]] = {}
    for lote in _financeiro_chunks(pedidos_shopee):
        ph = ",".join("?" for _ in lote)
        with DB_LOCK:
            for r in DB.execute(f"SELECT * FROM shopee_escrow_v1810 WHERE order_sn IN ({ph}) AND erro=''", tuple(lote)).fetchall():
                escrow[str(r["order_sn"])] = dict(r)

    venda_custos = _financeiro_custos_venda_ml(pedidos_ml) if pedidos_ml else {}

    custos_pedido: dict[tuple[str, str], dict[str, Any]] = {}
    for oid in pedidos_ml:
        b = billing["por_pedido"].get(oid) or {}
        v = venda_custos.get(oid) or {}
        # V18.31: a tarifa e o frete já aparecem NO ATO DA VENDA (é o que o ML
        # mostra no detalhe do pedido). A fatura é a conferência: quando o
        # lançamento existe, ele manda, porque é o que foi de fato cobrado.
        taxa_fatura = _money_number(b.get("sale_fee"))
        frete_fatura = _money_number(b.get("shipping"))
        taxa_venda = _money_number(v.get("taxa"))
        frete_venda = _money_number(v.get("frete"))
        taxa = taxa_fatura if taxa_fatura is not None else taxa_venda
        # V18.53: no FRETE a ordem é a inversa. A fatura lança o envio CHEIO (parte
        # do comprador + parte do vendedor = gross_amount do /shipments/costs), e a
        # parte do comprador não está no nosso faturamento. Venda 2000015151309807:
        # fatura 128,89, comprador pagou 105,99, vendedor 22,90. O custo do envio
        # (senders[].cost) é a parte do vendedor; a fatura só vale sem ele.
        frete = frete_venda if frete_venda is not None else frete_fatura
        outros = _money_number(b.get("other_cost"))
        # V18.34: PADS (publicidade) já sai separado do consolidador e nunca entra
        # em "outros". Vira o Ads do pedido quando a API de Publicidade não responde.
        pub = float(b.get("publicidade") or 0.0)
        if taxa_fatura is not None:
            origem = str(b.get("source") or "") or "Fatura ML"
        elif taxa_venda is not None:
            origem = "Tarifa da venda (Orders ML)"
        else:
            origem = "Tarifa ainda não capturada"
        custos_pedido[("ml", oid)] = {
            "taxa": taxa, "frete": frete, "outros": outros, "ads_fatura": pub,
            "bonus": float(b.get("bonus") or 0.0), "origem": origem,
            "conciliado": taxa is not None, "na_fatura": taxa_fatura is not None,
            "taxa_venda": taxa_venda, "taxa_fatura": taxa_fatura,
            "frete_venda": frete_venda, "frete_fatura": frete_fatura,
            "divergencia_taxa": (round(taxa_fatura - taxa_venda, 2)
                                 if (taxa_fatura is not None and taxa_venda is not None) else None),
            "divergencia_frete": (round(frete_fatura - frete_venda, 2)
                                  if (frete_fatura is not None and frete_venda is not None) else None),
            "frete_so_fatura": frete_venda is None and frete_fatura is not None,
        }
    for oid in pedidos_shopee:
        e = escrow.get(oid)
        if not e:
            custos_pedido[("shopee", oid)] = {"taxa": None, "frete": None, "outros": None, "bonus": 0.0,
                                              "origem": "Escrow Shopee ainda não consultado", "conciliado": False}
            continue
        # V18.35: o escrow_amount é o que REALMENTE entra na conta — ele é a
        # autoridade, como a fatura é no ML. As colunas explicam a maior parte
        # do que a Shopee reteve; o que sobrar vira "outros" em vez de virar
        # margem. Sem isso faltavam 3,5% do preço (a comissão de afiliados/AMS,
        # R$ 654,29 em setembro, que agora entra como Ads).
        taxa = round(float(e["commission_fee"] or 0) + float(e["service_fee"] or 0) + float(e["transaction_fee"] or 0), 2)
        frete_shopee = round(float(e["shipping_net"] or 0), 2)
        ams = round(float((e["ams_commission"] if "ams_commission" in e.keys() else 0) or 0), 2)
        preco = float(e["selling_price"] or 0)
        recebido = float(e["escrow_amount"] or 0)
        retido = round(preco - recebido, 2) if (preco > 0 and recebido > 0) else None
        outros_shopee = 0.0
        if retido is not None:
            outros_shopee = round(max(0.0, retido - taxa - frete_shopee - ams), 2)
        custos_pedido[("shopee", oid)] = {"taxa": taxa, "frete": frete_shopee,
                                          "outros": outros_shopee, "ads_fatura": ams, "bonus": 0.0,
                                          "origem": "Shopee get_escrow_detail", "conciliado": True,
                                          "renda_liquida": round(recebido, 2), "retido": retido}

    # Receita por pedido para ratear a taxa/frete entre as linhas.
    receita_pedido: dict[tuple[str, str], float] = {}
    for v in vendas:
        chave = (str(v["marketplace"]), str(v["order_id"]))
        receita_pedido[chave] = receita_pedido.get(chave, 0.0) + float(v["amount"] or 0)

    ads = _financeiro_ads_periodo(d1, d2)
    # V18.34: o gasto com Ads também está na fatura do ML (sub_type PADS), lançado
    # POR PEDIDO. Quando a API de Publicidade não responde — hoje é o caso — a
    # fatura é a fonte, e ainda dá o custo por pedido e por SKU, que a API só
    # daria com muito mais chamada. Uma fonte OU a outra; nunca as duas somadas.
    ads_fatura_pedidos = round(sum(float((c or {}).get("ads_fatura") or 0.0)
                                   for k, c in custos_pedido.items() if k[0] == "ml"), 2)
    # A Shopee cobra a comissão de afiliados (AMS) dentro do escrow: é marketing
    # e entra sempre, independente da API de Publicidade do ML.
    ads_shopee = round(sum(float((c or {}).get("ads_fatura") or 0.0)
                           for k, c in custos_pedido.items() if k[0] == "shopee"), 2)
    # V18.38: o CPC do Shopee Ads é custo do PERÍODO (crédito pré-pago, fora do
    # pedido). Vem por dia e entra no dia em que foi gasto.
    ads_shopee_cpc_por_dia: dict[str, float] = {}
    if not canal or canal == "shopee":
        with DB_LOCK:
            for r in DB.execute("SELECT dia, expense FROM shopee_ads_dia_v1838 WHERE dia BETWEEN ? AND ?",
                                (d1.isoformat(), d2.isoformat())).fetchall():
                ads_shopee_cpc_por_dia[str(r["dia"])] = round(float(r["expense"] or 0), 2)
    ads_shopee_cpc = round(sum(ads_shopee_cpc_por_dia.values()), 2)
    ads_fatura_sem_pedido = round(float((billing.get("sem_pedido") or {}).get("publicidade") or 0.0), 2)
    ads_api_total = ads["total"] if (not canal or canal == "ml") else 0.0
    if ads_api_total > 0:
        ads_fonte = "api"
    elif (ads_fatura_pedidos + ads_fatura_sem_pedido) > 0 and (not canal or canal == "ml"):
        ads_fonte = "fatura"
    elif ads_shopee > 0 or ads_shopee_cpc > 0:
        ads_fonte = "shopee"
    else:
        ads_fonte = ""
    usar_ads_da_fatura = ads_fonte == "fatura"

    def bloco() -> dict[str, Any]:
        return {"faturamento": 0.0, "unidades": 0, "pedidos": set(), "cmv": 0.0, "taxas": 0.0, "frete": 0.0,
                "outros": 0.0, "imposto": 0.0, "ads": 0.0, "sem_custo": 0.0, "sem_taxa": 0.0, "sem_frete": 0.0}

    totais = bloco()
    por_mp: dict[str, dict[str, Any]] = {m: bloco() for m in VENDAS_MARKETPLACES}
    por_dia: dict[str, dict[str, Any]] = {}
    por_sku: dict[str, dict[str, Any]] = {}
    pedidos_detalhe: dict[tuple[str, str], dict[str, Any]] = {}

    for v in vendas:
        mp = str(v["marketplace"])
        oid = str(v["order_id"])
        chave = (mp, oid)
        receita = round(float(v["amount"] or 0), 2)
        qty = int(v["qty"] or 0)
        sku = str(v["sku"] or "").strip()
        sku_key = sku.upper() or "(sem SKU)"
        unit, origem_custo = _financeiro_custo_unitario(sku, produtos, reservas, kits, cache_custo)
        cmv = round(unit * qty, 2) if unit is not None else None
        pc = custos_pedido.get(chave) or {}
        total_pedido = receita_pedido.get(chave, 0.0)
        fatia = (receita / total_pedido) if total_pedido > 0 else 1.0
        taxa = round(pc["taxa"] * fatia, 2) if pc.get("taxa") is not None else None
        frete = round(pc["frete"] * fatia, 2) if pc.get("frete") is not None else None
        outros = round(pc["outros"] * fatia, 2) if pc.get("outros") is not None else None
        imposto = round(receita * imposto_rate, 2)
        # Shopee: AMS sempre. ML: só quando a API de Publicidade não respondeu.
        usa_ads_desta_linha = (mp == "shopee") or usar_ads_da_fatura
        ads_linha = round(float(pc.get("ads_fatura") or 0.0) * fatia, 2) if usa_ads_desta_linha else 0.0

        alvos = [totais, por_mp.setdefault(mp, bloco()),
                 por_dia.setdefault(str(v["sale_day"]), {**bloco(), "dia": str(v["sale_day"])})]
        for alvo in alvos:
            alvo["faturamento"] = round(alvo["faturamento"] + receita, 2)
            alvo["unidades"] += qty
            alvo["pedidos"].add(chave)
            alvo["imposto"] = round(alvo["imposto"] + imposto, 2)
            if cmv is None:
                alvo["sem_custo"] = round(alvo["sem_custo"] + receita, 2)
            else:
                alvo["cmv"] = round(alvo["cmv"] + cmv, 2)
            if taxa is None:
                alvo["sem_taxa"] = round(alvo["sem_taxa"] + receita, 2)
            else:
                alvo["taxas"] = round(alvo["taxas"] + taxa, 2)
            # V18.34: frete desconhecido é pendência, não zero. Um pedido pode ter
            # frete 0 de verdade (frete grátis do comprador) — a diferença tem que
            # aparecer na tela em vez de virar margem inventada.
            if frete is None:
                alvo["sem_frete"] = round(alvo["sem_frete"] + receita, 2)
            else:
                alvo["frete"] = round(alvo["frete"] + frete, 2)
            if outros is not None:
                alvo["outros"] = round(alvo["outros"] + outros, 2)
            if ads_linha:
                alvo["ads"] = round(alvo["ads"] + ads_linha, 2)

        s = por_sku.setdefault(sku_key, {"sku": sku_key, "titulo": str(v["title"] or ""), "codigo": _compras_codigo(sku, produtos),
                                         "unidades": 0, "faturamento": 0.0, "cmv": 0.0, "taxas": 0.0, "frete": 0.0,
                                         "outros": 0.0, "imposto": 0.0, "ads": 0.0, "custo_unitario": unit,
                                         "custo_origem": origem_custo, "sem_custo": False, "pedidos": set(),
                                         "sem_taxa": 0.0, "sem_frete": 0.0})
        s["unidades"] += qty
        s["faturamento"] = round(s["faturamento"] + receita, 2)
        s["imposto"] = round(s["imposto"] + imposto, 2)
        s["pedidos"].add(chave)
        if cmv is None:
            s["sem_custo"] = True
        else:
            s["cmv"] = round(s["cmv"] + cmv, 2)
        if taxa is None:
            s["sem_taxa"] = round(s["sem_taxa"] + receita, 2)
        else:
            s["taxas"] = round(s["taxas"] + taxa, 2)
        if frete is None:
            s["sem_frete"] = round(s["sem_frete"] + receita, 2)
        else:
            s["frete"] = round(s["frete"] + frete, 2)
        if outros is not None:
            s["outros"] = round(s["outros"] + outros, 2)
        if ads_linha:
            s["ads"] = round(s["ads"] + ads_linha, 2)
        if not s["titulo"] and v["title"]:
            s["titulo"] = str(v["title"])

        p = pedidos_detalhe.setdefault(chave, {
            "marketplace": mp, "order_id": oid, "dia": str(v["sale_day"]), "itens": [], "unidades": 0,
            "faturamento": 0.0, "cmv": 0.0, "taxa": pc.get("taxa"), "frete": pc.get("frete"),
            "outros": pc.get("outros"), "imposto": 0.0, "origem_custo_venda": pc.get("origem", ""),
            "ads": round(float(pc.get("ads_fatura") or 0.0), 2) if usa_ads_desta_linha else 0.0,
            "conciliado": bool(pc.get("conciliado")), "sem_custo": False,
            "na_fatura": bool(pc.get("na_fatura")), "taxa_venda": pc.get("taxa_venda"),
            "taxa_fatura": pc.get("taxa_fatura"), "frete_venda": pc.get("frete_venda"),
            "frete_fatura": pc.get("frete_fatura"), "divergencia_taxa": pc.get("divergencia_taxa"),
            "divergencia_frete": pc.get("divergencia_frete"),
        })
        p["unidades"] += qty
        p["faturamento"] = round(p["faturamento"] + receita, 2)
        p["imposto"] = round(p["imposto"] + imposto, 2)
        # V18.54: o anúncio (MLB) da linha — a Análise de ADS soma o lucro por anúncio.
        p["itens"].append({"sku": sku, "titulo": str(v["title"] or ""), "qty": qty, "valor": receita,
                           "custo_unitario": unit, "cmv": cmv, "anuncio": str(v["anuncio"] or "")})
        if cmv is None:
            p["sem_custo"] = True
        else:
            p["cmv"] = round(p["cmv"] + cmv, 2)

    # --- Ads -----------------------------------------------------------------
    # Pela API de Publicidade o gasto é do PERÍODO (por dia). Pela fatura ele já
    # foi somado pedido a pedido no laço acima; aqui só entra o que a fatura
    # cobrou sem vincular a um pedido.
    ads_sem_alvo = 0.0
    if ads_shopee_cpc:
        totais["ads"] = round(totais["ads"] + ads_shopee_cpc, 2)
        if "shopee" in por_mp:
            por_mp["shopee"]["ads"] = round(por_mp["shopee"]["ads"] + ads_shopee_cpc, 2)
        for dia_cpc, valor_cpc in ads_shopee_cpc_por_dia.items():
            alvo = por_dia.get(dia_cpc)
            if alvo is not None:
                alvo["ads"] = round(alvo["ads"] + valor_cpc, 2)
        ads_sem_alvo = round(ads_sem_alvo + ads_shopee_cpc, 2)
    if usar_ads_da_fatura:
        # O Ads do ML já foi somado pedido a pedido; falta o que a fatura cobrou
        # sem vincular a nenhuma venda.
        ads_sem_alvo = ads_fatura_sem_pedido
        if ads_sem_alvo:
            totais["ads"] = round(totais["ads"] + ads_sem_alvo, 2)
            if "ml" in por_mp:
                por_mp["ml"]["ads"] = round(por_mp["ml"]["ads"] + ads_sem_alvo, 2)
            # V18.37: o PADS vem sem pedido, mas COM data — dá para pôr no dia certo.
            for dia_pub, valor_pub in ((billing.get("sem_pedido") or {}).get("por_dia") or {}).items():
                alvo = por_dia.get(dia_pub)
                if alvo is not None:
                    alvo["ads"] = round(alvo["ads"] + valor_pub, 2)
    else:
        # Pela API o gasto é do período (por dia), não do pedido.
        totais["ads"] = round(totais["ads"] + ads_api_total, 2)
        if "ml" in por_mp:
            por_mp["ml"]["ads"] = round(por_mp["ml"]["ads"] + ads_api_total, 2)
        if not canal or canal == "ml":
            for dia, valor in ads["por_dia"].items():
                alvo = por_dia.get(dia)
                if alvo is not None:
                    alvo["ads"] = round(alvo["ads"] + valor, 2)
        identificado_api = 0.0
        for sku_key, valor in ads["por_sku"].items():
            alvo = por_sku.get(sku_key)
            if alvo is not None:
                alvo["ads"] = round(alvo["ads"] + valor, 2)
                identificado_api = round(identificado_api + valor, 2)
        ads_sem_alvo = round(ads_sem_alvo + max(0.0, ads_api_total - identificado_api), 2)
    ads_total = totais["ads"]
    ads_sku_usado = round(sum(s["ads"] for s in por_sku.values()), 2)

    def fechar(b: dict[str, Any]) -> dict[str, Any]:
        pedidos = len(b.pop("pedidos"))
        fat = round(b["faturamento"], 2)
        bruto = round(fat - b["cmv"], 2)
        liquido = round(bruto - b["taxas"] - b["frete"] - b["outros"] - b["ads"] - b["imposto"], 2)
        return {**b, "faturamento": fat, "pedidos": pedidos,
                "ticket_medio": round(fat / pedidos, 2) if pedidos else 0.0,
                "lucro_bruto": bruto, "lucro_liquido": liquido,
                "margem_bruta": round(bruto / fat * 100, 2) if fat else None,
                "margem_liquida": round(liquido / fat * 100, 2) if fat else None}

    lista_sku = []
    for s in por_sku.values():
        s["pedidos"] = len(s["pedidos"])
        bruto = round(s["faturamento"] - s["cmv"], 2)
        s["lucro_bruto"] = bruto
        s["lucro_liquido"] = round(bruto - s["taxas"] - s["frete"] - s["outros"] - s["ads"] - s["imposto"], 2)
        s["margem_liquida"] = round(s["lucro_liquido"] / s["faturamento"] * 100, 2) if s["faturamento"] else None
        lista_sku.append(s)
    lista_sku.sort(key=lambda x: -x["faturamento"])

    lista_pedidos = []
    for p in pedidos_detalhe.values():
        bruto = round(p["faturamento"] - p["cmv"], 2)
        p["lucro_bruto"] = bruto
        conhecidos = sum(v for v in (p["taxa"], p["frete"], p["outros"]) if v is not None) + float(p.get("ads") or 0.0)
        p["lucro_liquido"] = round(bruto - conhecidos - p["imposto"], 2) if not p["sem_custo"] else None
        p["margem_liquida"] = round(p["lucro_liquido"] / p["faturamento"] * 100, 2) if (p["lucro_liquido"] is not None and p["faturamento"]) else None
        lista_pedidos.append(p)
    lista_pedidos.sort(key=lambda x: (x["lucro_liquido"] is None, x["lucro_liquido"] if x["lucro_liquido"] is not None else 0))
    # V18.54: limite_pedidos=0 devolve todos (uso interno da Análise de ADS; a rota
    # /api/financeiro/resumo continua com o padrão de 400).
    limite = len(lista_pedidos) if limite_pedidos == 0 else max(20, min(2000, int(limite_pedidos or 400)))

    dias_lista = []
    dia_corrente = d1
    while dia_corrente <= d2:
        chave_dia = dia_corrente.isoformat()
        vazio_do_dia = {**bloco(), "dia": chave_dia}
        if not usar_ads_da_fatura and (not canal or canal == "ml"):
            vazio_do_dia["ads"] = ads["por_dia"].get(chave_dia, 0.0)
        b = por_dia.get(chave_dia) or vazio_do_dia
        dias_lista.append({"dia": chave_dia, **fechar(b)})
        dia_corrente += timedelta(days=1)

    ml_conciliados = sum(1 for o in pedidos_ml if (custos_pedido.get(("ml", o)) or {}).get("conciliado"))
    shopee_conciliados = sum(1 for o in pedidos_shopee if (custos_pedido.get(("shopee", o)) or {}).get("conciliado"))
    ml_na_fatura = sum(1 for o in pedidos_ml if (custos_pedido.get(("ml", o)) or {}).get("na_fatura"))
    # Onde a fatura discordou do que o ML mostrou na venda: é o que precisa de olho.
    divergentes = [(o, (custos_pedido.get(("ml", o)) or {}).get("divergencia_taxa")) for o in pedidos_ml]
    divergentes = [(o, d) for o, d in divergentes if d is not None and abs(d) >= 0.01]
    totais_fechados = fechar(totais)
    return {
        "ok": True,
        "periodo": {"inicio": d1.isoformat(), "fim": d2.isoformat(), "dias": (d2 - d1).days + 1},
        "marketplace": canal or "todos",
        "imposto_percent": FINANCEIRO_IMPOSTO_PERCENT,
        "totais": totais_fechados,
        "por_marketplace": {m: fechar(b) for m, b in por_mp.items()},
        "por_dia": dias_lista,
        "por_sku": lista_sku[:300],
        "pedidos": lista_pedidos[:limite],
        "pedidos_total": len(lista_pedidos),
        "ads": {"total": ads_total, "por_sku_identificado": ads_sku_usado,
                "sem_sku": ads_sem_alvo, "dias_com_dado": ads["dias_com_dado"],
                "fonte": ads_fonte, "da_fatura": round(ads_fatura_pedidos + ads_fatura_sem_pedido, 2),
                "da_api": ads_api_total, "shopee_afiliados": ads_shopee, "shopee_cpc": ads_shopee_cpc, **ads["status"]},
        "fatura_ml": {"periodos": billing["periodos"], "lancamentos": billing["lancamentos"],
                      "sem_pedido": billing["sem_pedido"]},
        "cobertura": {
            "pedidos_ml": len(pedidos_ml), "pedidos_ml_conciliados": ml_conciliados,
            "pedidos_ml_na_fatura": ml_na_fatura,
            "pedidos_ml_taxa_da_venda": max(0, ml_conciliados - ml_na_fatura),
            # V18.53: frete só da fatura = envio ainda não consultado; pode incluir a
            # parte do comprador até o worker de frete preencher o custo do envio.
            "pedidos_ml_frete_so_fatura": sum(1 for o in pedidos_ml
                                              if (custos_pedido.get(("ml", o)) or {}).get("frete_so_fatura")),
            "pedidos_shopee": len(pedidos_shopee), "pedidos_shopee_conciliados": shopee_conciliados,
            "faturamento_sem_custo": totais_fechados["sem_custo"],
            "faturamento_sem_taxa": totais_fechados["sem_taxa"],
            "faturamento_sem_frete": totais_fechados["sem_frete"],
            "divergencias": len(divergentes),
            "divergencia_total": round(sum(d for _o, d in divergentes), 2),
            "linhas": len(vendas),
        },
    }


def financeiro_sincronizar(date_from: str, date_to: str) -> dict[str, Any]:
    """Busca o que falta para fechar o período: fatura do ML, escrow da Shopee e Ads.

    Não recalcula nada: só preenche as fontes oficiais. Pode ser chamada de novo
    — cada rodada avança um pedaço (a Shopee tem limite de chamadas).
    """
    if not FINANCEIRO_SYNC_LOCK.acquire(blocking=False):
        return {"ok": True, "em_andamento": True}
    try:
        d1 = datetime.strptime(date_from, "%Y-%m-%d").date()
        d2 = datetime.strptime(date_to, "%Y-%m-%d").date()
        if d1 > d2:
            d1, d2 = d2, d1
        resultado: dict[str, Any] = {"ok": True, "periodo": {"inicio": d1.isoformat(), "fim": d2.isoformat()}}
        # 1) Fatura do ML (mês do período + o seguinte).
        try:
            _, seller_id = ml_access_token()
        except Exception as exc:
            seller_id = ""
            resultado["ml_token"] = str(exc)[:200]
        faturas = []
        mes_atual = _ml_billing_month_key(now_iso())
        for period_key in _financeiro_periodos_fatura(d1, d2):
            for doc in ("BILL", "CREDIT_NOTE"):
                try:
                    faturas.append(_ml_billing_v1513_sync_period(period_key, doc, str(seller_id or ""),
                                                                 force=(period_key == mes_atual)))
                except Exception as exc:
                    faturas.append({"ok": False, "period_key": period_key, "document_type": doc,
                                    "status": "error", "diagnostic": str(exc)[:300]})
        resultado["fatura_ml"] = {"periodos": len(faturas),
                                  "erros": [f for f in faturas if str(f.get("status") or "") == "error"][:5]}
        # 2) Frete por conta do vendedor dos envios ainda sem valor.
        try:
            resultado["frete_ml"] = ml_frete_envio_preencher(FINANCEIRO_FRETE_POR_RODADA)
        except Exception as exc:
            resultado["frete_ml"] = {"erro": str(exc)[:200]}
        # 3) Escrow da Shopee dos pedidos do período que ainda não temos.
        with DB_LOCK:
            pendentes = [str(r["order_id"]) for r in DB.execute(
                "SELECT DISTINCT v.order_id FROM vendas_v1799 v "
                "LEFT JOIN shopee_escrow_v1810 e ON e.order_sn = v.order_id AND e.erro='' "
                "WHERE v.marketplace='shopee' AND v.sale_day BETWEEN ? AND ? AND e.order_sn IS NULL "
                "ORDER BY v.sale_day DESC", (d1.isoformat(), d2.isoformat())).fetchall()]
        buscados = 0
        for sn in pendentes[:FINANCEIRO_SHOPEE_POR_RODADA]:
            if shopee_escrow_lookup(sn):
                buscados += 1
        resultado["shopee"] = {"pendentes": len(pendentes), "buscados": buscados,
                               "faltam": max(0, len(pendentes) - buscados)}
        # 4) Publicidade.
        try:
            resultado["ads"] = ml_ads_sync_once()
        except Exception as exc:
            resultado["ads"] = {"ok": False, "erro": str(exc)[:300]}
        try:
            resultado["ads_shopee"] = shopee_ads_sync(d1, d2)
        except Exception as exc:
            resultado["ads_shopee"] = {"ok": False, "erro": str(exc)[:300]}
        return resultado
    finally:
        FINANCEIRO_SYNC_LOCK.release()


# ---------------------------------------------------------------------------
# V18.13: Agente de RELATÓRIO DIÁRIO por e-mail. Todo dia às 00:01 monta o resumo
# do dia que fechou (tudo que vendeu, de vendas_v1799) e envia por e-mail (smtplib,
# biblioteca padrão — sem dependência externa). Só leitura; não usa IA nem grava
# em dados de operação (apenas marca o último dia enviado em vendas_meta_v1799).
# Configuração toda por .env: SMTP_* e RELATORIO_EMAILS.
# ---------------------------------------------------------------------------
_RELATORIO_MP_NOME = {"ml": "Mercado Livre", "shopee": "Shopee", "magalu": "Magalu"}


def _brl_srv(v: Any) -> str:
    try:
        return "R$ " + ("%0.2f" % float(v or 0)).replace(".", "#").replace(",", ".").replace("#", ",")
    except Exception:
        return "R$ 0,00"


def _data_br(dia_iso: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(dia_iso or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else str(dia_iso or "")


def relatorio_diario_config() -> dict[str, Any]:
    def _flag(nome: str, padrao: str) -> bool:
        return str(os.getenv(nome, padrao)).strip().lower() not in ("0", "", "false", "nao", "não")
    return {
        "ativo": _flag("RELATORIO_DIARIO_ATIVO", "1"),
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or "587"),
        "user": os.getenv("SMTP_USER", "").strip(),
        "senha": os.getenv("SMTP_SENHA", "").strip(),
        "remetente": (os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()),
        "tls": _flag("SMTP_TLS", "1"),
        "destinatarios": [e.strip() for e in os.getenv("RELATORIO_EMAILS", "").split(",") if e.strip()],
    }


def relatorio_diario_dados(dia: str) -> dict[str, Any]:
    """Resumo de tudo que vendeu num dia (vendas_v1799). dia = 'YYYY-MM-DD'."""
    with DB_LOCK:
        tot = DB.execute(
            "SELECT COUNT(DISTINCT marketplace||':'||order_id) AS pedidos, "
            "COALESCE(SUM(qty),0) AS unidades, COALESCE(SUM(amount),0) AS faturamento "
            "FROM vendas_v1799 WHERE sale_day=?",
            (dia,),
        ).fetchone()
        por_mp = DB.execute(
            "SELECT marketplace, COUNT(DISTINCT order_id) AS pedidos, COALESCE(SUM(qty),0) AS unidades, "
            "COALESCE(SUM(amount),0) AS faturamento FROM vendas_v1799 WHERE sale_day=? "
            "GROUP BY marketplace ORDER BY faturamento DESC",
            (dia,),
        ).fetchall()
        linhas = DB.execute(
            "SELECT marketplace, sku, MAX(NULLIF(title,'')) AS titulo, MAX(NULLIF(anuncio,'')) AS anuncio, "
            "SUM(qty) AS qtd, SUM(amount) AS valor, COUNT(DISTINCT order_id) AS pedidos "
            "FROM vendas_v1799 WHERE sale_day=? GROUP BY marketplace, sku ORDER BY valor DESC",
            (dia,),
        ).fetchall()
    produtos = [{
        "marketplace": r["marketplace"], "sku": r["sku"] or "(sem SKU)",
        "titulo": r["titulo"] or r["anuncio"] or r["sku"] or "(sem título)",
        "qtd": int(r["qtd"] or 0), "valor": round(float(r["valor"] or 0), 2), "pedidos": int(r["pedidos"] or 0),
    } for r in linhas]
    faturamento = round(float(tot["faturamento"] or 0), 2)
    pedidos = int(tot["pedidos"] or 0)
    return {
        "dia": dia, "faturamento": faturamento, "pedidos": pedidos, "unidades": int(tot["unidades"] or 0),
        "ticket_medio": round(faturamento / pedidos, 2) if pedidos else 0.0,
        "por_marketplace": [{
            "marketplace": r["marketplace"], "nome": _RELATORIO_MP_NOME.get(r["marketplace"], r["marketplace"]),
            "pedidos": int(r["pedidos"] or 0), "unidades": int(r["unidades"] or 0),
            "faturamento": round(float(r["faturamento"] or 0), 2),
        } for r in por_mp],
        "produtos": produtos,
    }


def relatorio_diario_html(d: dict[str, Any]) -> str:
    def esc(s: Any) -> str:
        return html_module.escape(str(s if s is not None else ""))
    cor = {"ml": "#2f6fd0", "shopee": "#ee4d2d", "magalu": "#0086ff"}
    canais = "".join(
        f"<tr><td style='padding:6px 10px'><span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
        f"background:{cor.get(c['marketplace'], '#888')};margin-right:6px'></span>{esc(c['nome'])}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{c['pedidos']}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{c['unidades']}</td>"
        f"<td style='padding:6px 10px;text-align:right;font-weight:700'>{_brl_srv(c['faturamento'])}</td></tr>"
        for c in d["por_marketplace"]
    ) or "<tr><td colspan='4' style='padding:10px;color:#888'>Sem vendas.</td></tr>"
    linhas = "".join(
        f"<tr style='border-top:1px solid #eef2f7'>"
        f"<td style='padding:6px 10px'><b>{esc(p['titulo'])}</b><br><span style='color:#748295;font-size:12px'>"
        f"{esc(p['sku'])} · <span style='color:{cor.get(p['marketplace'], '#888')}'>{esc(_RELATORIO_MP_NOME.get(p['marketplace'], p['marketplace']))}</span></span></td>"
        f"<td style='padding:6px 10px;text-align:right'>{p['qtd']}</td>"
        f"<td style='padding:6px 10px;text-align:right;font-weight:700'>{_brl_srv(p['valor'])}</td></tr>"
        for p in d["produtos"]
    ) or "<tr><td colspan='3' style='padding:10px;color:#888'>Nenhuma venda neste dia.</td></tr>"
    # V18.22: dado possivelmente incompleto avisa na cara, em vez de passar por certo.
    aviso = str(d.get("aviso") or "")
    aviso_html = (
        f"<div style='background:#fff6dc;border:1px solid #f0d98a;border-left:5px solid #d99b00;"
        f"border-radius:10px;padding:11px 14px;margin:0 0 16px;color:#7a5200;font-size:13px'>"
        f"<b>⚠ Este número pode estar incompleto.</b><br>{esc(aviso)}<br>"
        f"<span style='color:#94722a'>Confira o Dashboard de vendas antes de decidir por ele.</span></div>"
    ) if aviso else ""
    return f"""<div style="font-family:'Segoe UI',system-ui,sans-serif;color:#1e293b;max-width:680px;margin:auto">
<h2 style="color:#123a63;margin:0 0 4px">Noromak — Vendas de {esc(_data_br(d['dia']))}</h2>
<p style="color:#748295;margin:0 0 16px">Resumo do dia que fechou.</p>
{aviso_html}
<table style="width:100%;border-collapse:collapse;margin-bottom:18px">
  <tr>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800;color:#123a63">{_brl_srv(d['faturamento'])}</div><div style="font-size:12px;color:#748295">Faturamento</div></td>
    <td style="width:8px"></td>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800">{d['pedidos']}</div><div style="font-size:12px;color:#748295">Pedidos</div></td>
    <td style="width:8px"></td>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800">{d['unidades']}</div><div style="font-size:12px;color:#748295">Unidades</div></td>
    <td style="width:8px"></td>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800">{_brl_srv(d['ticket_medio'])}</div><div style="font-size:12px;color:#748295">Ticket médio</div></td>
  </tr>
</table>
<h3 style="color:#123a63;margin:0 0 6px;font-size:15px">Por marketplace</h3>
<table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:18px">
  <thead><tr style="background:#123a63;color:#fff;font-size:11px;text-transform:uppercase">
    <th style="padding:6px 10px;text-align:left">Canal</th><th style="padding:6px 10px;text-align:right">Pedidos</th>
    <th style="padding:6px 10px;text-align:right">Unid.</th><th style="padding:6px 10px;text-align:right">Faturamento</th></tr></thead>
  <tbody>{canais}</tbody>
</table>
<h3 style="color:#123a63;margin:0 0 6px;font-size:15px">Tudo que vendeu ({len(d['produtos'])} item(ns))</h3>
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <thead><tr style="background:#123a63;color:#fff;font-size:11px;text-transform:uppercase">
    <th style="padding:6px 10px;text-align:left">Produto</th><th style="padding:6px 10px;text-align:right">Qtd</th>
    <th style="padding:6px 10px;text-align:right">Valor</th></tr></thead>
  <tbody>{linhas}</tbody>
</table>
<p style="color:#94a3b8;font-size:11px;margin-top:18px">Gerado automaticamente pelo Sistema Noromak às 00:01. Dados de Mercado Livre e Shopee.</p>
</div>"""


def relatorio_diario_texto(d: dict[str, Any]) -> str:
    linhas = [
        f"Noromak - Vendas de {_data_br(d['dia'])}",
    ]
    if d.get("aviso"):
        linhas.append(f"ATENCAO: este numero pode estar incompleto - {d['aviso']}")
    linhas += [
        f"Faturamento: {_brl_srv(d['faturamento'])} | Pedidos: {d['pedidos']} | Unidades: {d['unidades']} | Ticket: {_brl_srv(d['ticket_medio'])}",
        "",
        "Por marketplace:",
    ]
    for c in d["por_marketplace"]:
        linhas.append(f"  - {c['nome']}: {_brl_srv(c['faturamento'])} ({c['pedidos']} ped, {c['unidades']} un)")
    linhas += ["", f"Tudo que vendeu ({len(d['produtos'])} item(ns)):"]
    for p in d["produtos"]:
        linhas.append(f"  - {p['titulo']} [{p['sku']} / {_RELATORIO_MP_NOME.get(p['marketplace'], p['marketplace'])}] x{p['qtd']} = {_brl_srv(p['valor'])}")
    if not d["produtos"]:
        linhas.append("  (nenhuma venda neste dia)")
    return "\n".join(linhas)


def enviar_email_smtp(assunto: str, texto: str, html: str, destinatarios: list[str],
                      anexos: list[dict[str, Any]] | None = None) -> list[str]:
    """Envio genérico por SMTP (config do .env). Usado pelo relatório diário e pela
    Demanda de Compra. Não depende de biblioteca externa (smtplib padrão).
    `anexos`: lista de {nome, conteudo(bytes), subtype}."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    cfg = relatorio_diario_config()
    faltando = [k for k in ("host", "user", "senha") if not cfg[k]]
    if faltando:
        raise RuntimeError("SMTP não configurado. Defina SMTP_HOST, SMTP_USER e SMTP_SENHA no /home/expedicao/.env.")
    dest = [e.strip() for e in (destinatarios or []) if e and e.strip()]
    if not dest:
        raise RuntimeError("Nenhum destinatário informado para o e-mail.")
    corpo = MIMEMultipart("alternative")
    corpo.attach(MIMEText(texto, "plain", "utf-8"))
    corpo.attach(MIMEText(html, "html", "utf-8"))
    anexos = anexos or []
    if anexos:
        msg: Any = MIMEMultipart("mixed")
        msg.attach(corpo)
        for a in anexos:
            part = MIMEApplication(a["conteudo"], _subtype=a.get("subtype", "octet-stream"))
            part.add_header("Content-Disposition", "attachment", filename=a.get("nome", "anexo"))
            msg.attach(part)
    else:
        msg = corpo
    msg["Subject"] = assunto
    msg["From"] = cfg["remetente"]
    msg["To"] = ", ".join(dest)
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
        if cfg["tls"]:
            s.starttls()
        s.login(cfg["user"], cfg["senha"])
        s.sendmail(cfg["remetente"], dest, msg.as_string())
    return dest


def relatorio_diario_enviar_email(assunto: str, texto: str, html: str) -> list[str]:
    cfg = relatorio_diario_config()
    if not cfg["destinatarios"]:
        raise RuntimeError("Defina RELATORIO_EMAILS no /home/expedicao/.env.")
    return enviar_email_smtp(assunto, texto, html, cfg["destinatarios"])


def relatorio_diario_frescor(dia: str) -> str:
    """V18.22: o relatório não pode publicar um dia incompleto em silêncio.

    Em 22/09/2026 o e-mail saiu com R$ 3.775,37 enquanto o dia real fechou em
    R$ 6.814,47: a sincronização do ML tinha parado às 19h e nada avisou.
    Devolve o aviso a mostrar no e-mail, ou "" quando o dado é confiável.
    """
    try:
        fim_do_dia = datetime.fromisoformat(dia + "T23:59:59").replace(tzinfo=BR_TZ)
    except ValueError:
        return ""
    # Só cobra o marketplace que realmente vendeu nos últimos dias; um canal que
    # nunca foi configurado não pode gerar aviso todo dia.
    desde = (datetime.fromisoformat(dia).date() - timedelta(days=7)).isoformat()
    with DB_LOCK:
        ativos = [r["marketplace"] for r in DB.execute(
            "SELECT DISTINCT marketplace FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?", (desde, dia))]
    problemas: list[str] = []
    for mp in ativos:
        nome = _RELATORIO_MP_NOME.get(mp, mp)
        erro = _vendas_meta(f"ultimo_erro_{mp}")
        if erro:
            problemas.append(f"{nome}: a última sincronização falhou")
            continue
        quando = _vendas_meta(f"ultima_sincronizacao_{mp}")
        try:
            if datetime.fromisoformat(quando) < fim_do_dia:
                problemas.append(f"{nome}: sem sincronizar desde o fim do dia")
        except (TypeError, ValueError):
            problemas.append(f"{nome}: sem registro de sincronização")
    return " · ".join(problemas)


def relatorio_diario_enviar(dia: str, forcar: bool = False) -> dict[str, Any]:
    if not forcar and _vendas_meta("relatorio_diario_ultimo") == dia:
        return {"ok": True, "pulado": True, "motivo": "já enviado para este dia", "dia": dia}
    # V18.22: puxa o que faltar ANTES de publicar o número. Sem isso o relatório
    # apenas repete o que estiver no banco, mesmo que esteja parado há horas.
    try:
        vendas_sync_once()
    except Exception as exc:
        print(f"[relatorio-diario] sincronização falhou: {exc}", file=sys.stderr)
    dados = relatorio_diario_dados(dia)
    dados["aviso"] = relatorio_diario_frescor(dia)
    assunto = f"Noromak — Vendas de {_data_br(dia)}: {_brl_srv(dados['faturamento'])} em {dados['pedidos']} pedido(s)"
    destinatarios = relatorio_diario_enviar_email(assunto, relatorio_diario_texto(dados), relatorio_diario_html(dados))
    _vendas_meta("relatorio_diario_ultimo", dia)
    return {"ok": True, "dia": dia, "faturamento": dados["faturamento"], "pedidos": dados["pedidos"], "destinatarios": destinatarios}


def relatorio_diario_loop(stop: threading.Event) -> None:
    """Agente: acorda todo dia no horário de RELATORIO_HORA (padrão 08:00) e
    envia o relatório do dia que fechou. V18.22: era 00:01, mas na virada a
    sincronização do dia nem sempre terminou — de manhã o número já está firme."""
    while not stop.is_set():
        agora = datetime.now(BR_TZ)
        try:
            hora = max(0, min(23, int(os.getenv("RELATORIO_HORA", "8"))))
        except ValueError:
            hora = 8
        alvo = agora.replace(hour=hora, minute=0, second=0, microsecond=0)
        if agora >= alvo:
            alvo += timedelta(days=1)
        if stop.wait((alvo - agora).total_seconds()):
            return
        cfg = relatorio_diario_config()
        # Enquanto o SMTP/destinatários não estiverem no .env, o agente fica ocioso
        # em silêncio (sem erro diário). Ativa sozinho quando o .env for preenchido.
        if not cfg["ativo"] or not (cfg["host"] and cfg["user"] and cfg["senha"] and cfg["destinatarios"]):
            continue
        try:
            dia = (datetime.now(BR_TZ).date() - timedelta(days=1)).isoformat()
            res = relatorio_diario_enviar(dia)
            print(f"[relatorio-diario] {dia}: {res}", flush=True)
        except Exception as exc:
            print(f"[relatorio-diario] falhou: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# V18.14: "Enviar ao comprador" da Demanda de Compra. O cálculo de quanto comprar
# é feito no navegador (junta vendas/custo do servidor com o estoque do Apollo).
# O botão manda esse pedido montado para o comprador por e-mail (mesmo SMTP do
# relatório). Um comprador para tudo, guardado em vendas_meta_v1799.
# ---------------------------------------------------------------------------
def compras_comprador_email(email: str | None = None) -> str:
    if email is not None:
        return _vendas_meta("compras_comprador_email", str(email or "").strip())
    return _vendas_meta("compras_comprador_email")


def compras_limite_transferencia(valor: Any = None) -> float:
    """V18.17 — valor mínimo (R$) para uma transferência entre casas compensar o
    frete. Global (um para tudo). Guardado em vendas_meta_v1799."""
    if valor is not None:
        try:
            v = max(0.0, float(str(valor).replace(",", ".")))
        except Exception:
            v = 0.0
        _vendas_meta("compras_limite_transferencia", str(v))
        return v
    try:
        return float(_vendas_meta("compras_limite_transferencia") or 0)
    except Exception:
        return 0.0


def compras_pedido_email_html(p: dict[str, Any]) -> str:
    def esc(s: Any) -> str:
        return html_module.escape(str(s if s is not None else ""))
    def _qtd(it: dict) -> Any:  # V18.18: aceita 'qtd' (legado) ou 'comprar'
        return it.get("qtd") if it.get("qtd") is not None else it.get("comprar")
    itens = p.get("itens") or []
    linhas = "".join(
        f"<tr style='border-top:1px solid #eef2f7'>"
        f"<td style='padding:6px 10px'><b>{esc(it.get('descricao') or it.get('codigo'))}</b>"
        f"<br><span style='color:#748295;font-size:12px'>{esc(it.get('codigo'))}</span></td>"
        f"<td style='padding:6px 10px;text-align:right'>{esc(_qtd(it))}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{_brl_srv(it.get('custo'))}</td>"
        f"<td style='padding:6px 10px;text-align:right;font-weight:700'>{_brl_srv(it.get('subtotal'))}</td></tr>"
        for it in itens
    ) or "<tr><td colspan='4' style='padding:10px;color:#888'>Sem itens no pedido.</td></tr>"
    minimo = float(p.get("minimo") or 0)
    total = float(p.get("total") or 0)
    status = ("<span style='color:#16a34a;font-weight:700'>atinge o pedido mínimo</span>"
              if p.get("atingiu_minimo") else
              f"<span style='color:#d97706;font-weight:700'>faltam {_brl_srv(max(0.0, minimo - total))} para o mínimo</span>")
    # V18.18: seções de transferência (uma por casa) que evitam compra.
    transf = p.get("transferencias") or {}
    blocos_tr = ""
    for casa in ("Ribeirão", "Bauru"):
        tr_itens = transf.get(casa) or []
        if not tr_itens:
            continue
        tot_q = sum(int(i.get("qtd") or 0) for i in tr_itens)
        tot_v = sum(float(i.get("valor") or 0) for i in tr_itens)
        tr_linhas = "".join(
            f"<tr style='border-top:1px solid #eef2f7'>"
            f"<td style='padding:5px 10px'><b>{esc(i.get('descricao') or i.get('codigo'))}</b>"
            f"<br><span style='color:#748295;font-size:12px'>{esc(i.get('codigo'))}</span></td>"
            f"<td style='padding:5px 10px;text-align:right'>{esc(i.get('qtd'))}</td>"
            f"<td style='padding:5px 10px;text-align:right;font-weight:700'>{_brl_srv(i.get('valor'))}</td></tr>"
            for i in tr_itens
        )
        blocos_tr += f"""<div style="border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;margin-bottom:10px">
  <div style="background:#f2ecfd;color:#6d3fc0;font-weight:800;padding:10px 14px;display:flex;justify-content:space-between">
    <span>↔ Transferir de {esc(casa)} para Araçatuba</span><span>{tot_q} peça(s) · {_brl_srv(tot_v)}</span></div>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead><tr style="background:#eef3f8;color:#6a7a8c;font-size:11px;text-transform:uppercase">
      <th style="padding:5px 10px;text-align:left">Produto</th><th style="padding:5px 10px;text-align:right">Qtd</th>
      <th style="padding:5px 10px;text-align:right">Valor</th></tr></thead>
    <tbody>{tr_linhas}</tbody></table></div>"""
    if blocos_tr:
        blocos_tr = ('<h3 style="color:#6d3fc0;margin:22px 0 10px;font-size:15px">Transferências sugeridas → Araçatuba '
                     '<span style="color:#94a3b8;font-weight:500;font-size:12px">(evitam compra)</span></h3>' + blocos_tr)
    return f"""<div style="font-family:'Segoe UI',system-ui,sans-serif;color:#1e293b;max-width:680px;margin:auto">
<h2 style="color:#123a63;margin:0 0 4px">Pedido de compra — {esc(p.get('fornecedor'))}</h2>
<p style="color:#748295;margin:0 0 14px">Sugestão gerada pela Demanda de Compra (base: últimos {esc(p.get('periodo_dias') or 60)} dias de venda).</p>
<table style="width:100%;border-collapse:collapse;margin-bottom:16px">
  <tr>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800;color:#123a63">{_brl_srv(total)}</div><div style="font-size:12px;color:#748295">Total do pedido</div></td>
    <td style="width:8px"></td>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:22px;font-weight:800">{len(itens)}</div><div style="font-size:12px;color:#748295">Itens</div></td>
    <td style="width:8px"></td>
    <td style="background:#f6f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:14px;margin-top:4px">{status}</div><div style="font-size:12px;color:#748295">Mínimo: {_brl_srv(minimo)}</div></td>
  </tr>
</table>
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <thead><tr style="background:#123a63;color:#fff;font-size:11px;text-transform:uppercase">
    <th style="padding:6px 10px;text-align:left">Produto</th><th style="padding:6px 10px;text-align:right">Qtd</th>
    <th style="padding:6px 10px;text-align:right">Custo un.</th><th style="padding:6px 10px;text-align:right">Subtotal</th></tr></thead>
  <tbody>{linhas}</tbody>
</table>
{blocos_tr}
<p style="color:#94a3b8;font-size:11px;margin-top:18px">📎 A planilha completa do pedido segue em anexo (.xlsx). Enviado pelo Sistema Noromak (Demanda de Compra). Confira as quantidades antes de fechar o pedido com o fornecedor.</p>
</div>"""


def compras_pedido_email_texto(p: dict[str, Any]) -> str:
    linhas = [
        f"Pedido de compra - {p.get('fornecedor')}",
        f"Total: {_brl_srv(p.get('total'))} | Itens: {len(p.get('itens') or [])} | Minimo: {_brl_srv(p.get('minimo'))}"
        + ("  (atinge o minimo)" if p.get("atingiu_minimo") else "  (abaixo do minimo)"),
        "",
    ]
    for it in (p.get("itens") or []):
        _q = it.get("qtd") if it.get("qtd") is not None else it.get("comprar")
        linhas.append(f"  - {it.get('descricao') or it.get('codigo')} [{it.get('codigo')}] x{_q} @ {_brl_srv(it.get('custo'))} = {_brl_srv(it.get('subtotal'))}")
    if not (p.get("itens") or []):
        linhas.append("  (sem itens)")
    # V18.18: transferências sugeridas (uma seção por casa).
    transf = p.get("transferencias") or {}
    for casa in ("Ribeirão", "Bauru"):
        tr_itens = transf.get(casa) or []
        if not tr_itens:
            continue
        tot_q = sum(int(i.get("qtd") or 0) for i in tr_itens)
        tot_v = sum(float(i.get("valor") or 0) for i in tr_itens)
        linhas.append("")
        linhas.append(f"Transferir de {casa} para Aracatuba - {tot_q} peca(s) / {_brl_srv(tot_v)}:")
        for i in tr_itens:
            linhas.append(f"  - {i.get('descricao') or i.get('codigo')} [{i.get('codigo')}] x{i.get('qtd')} = {_brl_srv(i.get('valor'))}")
    return "\n".join(linhas)


def _xlsx_col(n: int) -> str:
    s = ""; n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def compras_pedido_xlsx(p: dict[str, Any]) -> bytes:
    """V18.18 — planilha DESENHADA do pedido de compra (.xlsx montado à mão, com
    cabeçalho colorido e seções de transferência), sem depender de biblioteca
    externa. Estilos: 1=cabeçalho navy, 2=moeda, 3=título de seção, 4=negrito."""
    import io
    import zipfile
    esc = html_module.escape

    linhas: list[list[tuple[Any, int]]] = []
    fornecedor = str(p.get("fornecedor") or "Fornecedor")
    linhas.append([(f"Pedido de compra — {fornecedor}", 4)])
    linhas.append([("Total da compra: %s   |   %s   |   base: %s dias" % (
        _brl_srv(p.get("total")),
        "atinge o mínimo" if p.get("atingiu_minimo") else "abaixo do mínimo",
        p.get("periodo_dias") or 60), 0)])
    linhas.append([])
    heads = ["Código", "Descrição", "Curva", "Vendido", "Média/dia", "Araçatuba", "Ribeirão", "Bauru",
             "A caminho", "Cobertura (dias)", "Necessidade", "Comprar", "Custo un.", "Total"]
    linhas.append([(h, 1) for h in heads])
    for it in (p.get("itens") or []):
        def n(v):
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else ("" if v is None else v)
        linhas.append([
            (it.get("codigo"), 0), (it.get("descricao"), 0), (it.get("curva"), 0),
            (n(it.get("vendido")), 0), (n(it.get("media")), 0),
            (n(it.get("araca")), 0), (n(it.get("rib")), 0), (n(it.get("bau")), 0),
            (n(it.get("caminho")), 0), (n(it.get("cobertura")), 0),
            (n(it.get("necessidade")), 0), (n(it.get("comprar")), 4),
            (n(it.get("custo")), 2), (n(it.get("subtotal")), 2),
        ])
    transf = p.get("transferencias") or {}
    for casa in ("Ribeirão", "Bauru"):
        itens = transf.get(casa) or []
        if not itens:
            continue
        tot_q = sum(int(i.get("qtd") or 0) for i in itens)
        tot_v = sum(float(i.get("valor") or 0) for i in itens)
        linhas.append([])
        linhas.append([("Transferir de %s para Araçatuba — %s peça(s) · %s" % (casa, tot_q, _brl_srv(tot_v)), 3)])
        linhas.append([(h, 1) for h in ["Código", "Descrição", "Qtd", "Valor"]])
        for i in itens:
            linhas.append([(i.get("codigo"), 0), (i.get("descricao"), 0), (int(i.get("qtd") or 0), 0), (float(i.get("valor") or 0), 2)])

    def cell(ci: int, ri: int, value: Any, style: int) -> str:
        ref = "%s%d" % (_xlsx_col(ci), ri)
        s = ' s="%d"' % style if style else ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return '<c r="%s"%s><v>%s</v></c>' % (ref, s, value)
        return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, s, esc(str(value if value is not None else "")))

    rows_xml = []
    for ri, cells in enumerate(linhas, 1):
        cs = "".join(cell(ci, ri, v, st) for ci, (v, st) in enumerate(cells))
        rows_xml.append('<row r="%d">%s</row>' % (ri, cs))
    cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w)
                   for i, w in enumerate([14, 46, 7, 9, 11, 11, 10, 9, 11, 15, 12, 9, 13, 14]))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<cols>%s</cols><sheetData>%s</sheetData></worksheet>' % (cols, "".join(rows_xml)))
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;R$&quot;\\ #,##0.00"/></numFmts>'
              '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="4"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF123B63"/></patternFill></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF2ECFD"/></patternFill></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="5"><xf/>'
              '<xf fontId="1" fillId="2" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>'
              '<xf numFmtId="164" applyNumberFormat="1"/>'
              '<xf fontId="2" fillId="3" applyFont="1" applyFill="1"/>'
              '<xf fontId="2" applyFont="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Pedido" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def compras_enviar_comprador(p: dict[str, Any]) -> dict[str, Any]:
    comprador = compras_comprador_email()
    if not comprador:
        raise RuntimeError("E-mail do comprador não configurado. Preencha na tela da Demanda de Compra.")
    fornecedor = str(p.get("fornecedor") or "").strip() or "Fornecedor"
    assunto = f"Pedido de compra — {fornecedor}: {_brl_srv(p.get('total'))} ({len(p.get('itens') or [])} itens)"
    # V18.18: a planilha DESENHADA é montada no servidor (mesmas colunas da tela +
    # seções de transferência), sem depender do XLSX cru do navegador.
    anexos: list[dict[str, Any]] = []
    try:
        conteudo = compras_pedido_xlsx(p)
        nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", fornecedor).strip("_") or "fornecedor"
        anexos.append({"nome": f"pedido_compra_{nome_seguro}.xlsx", "conteudo": conteudo,
                       "subtype": "vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    except Exception:
        pass  # planilha com erro não impede o e-mail do pedido
    dest = enviar_email_smtp(assunto, compras_pedido_email_texto(p), compras_pedido_email_html(p), [comprador], anexos)
    return {"ok": True, "comprador": dest, "total": p.get("total"),
            "itens": len(p.get("itens") or []), "com_planilha": bool(anexos)}


# ---------------------------------------------------------------------------
# V18.00: Demanda de Compra (versão provisória). Enquanto o Apollo não entrega a
# rota de vendas por período (PEDIDO_ROTA_DEMANDA_APOLLO.md), a demanda usa:
#   - vendas do ML e da Shopee (vendas_v1799) — não enxerga balcão/oficina;
#   - custo e curva da lista do Precificador (código = código Apollo);
#   - estoque do Apollo pela mesma rota da Demanda Full (no navegador).
# Marca não existe no cadastro: cada fornecedor é definido por regras.
# ---------------------------------------------------------------------------
def _compras_normalizar_lista(valor: Any) -> list[str]:
    if isinstance(valor, str):
        valor = re.split(r"[\n,;]+", valor)
    if not isinstance(valor, list):
        return []
    out: list[str] = []
    for item in valor:
        texto = str(item or "").strip().upper()
        if texto and texto not in out:
            out.append(texto)
    return out[:2000]


def compras_fornecedor_config(body: dict[str, Any]) -> dict[str, Any]:
    def numero(chave: str, padrao: float, minimo: float, maximo: float) -> float:
        try:
            return max(minimo, min(maximo, float(body.get(chave, padrao))))
        except (TypeError, ValueError):
            return padrao
    return {
        "pedido_minimo": round(numero("pedido_minimo", 0, 0, 10_000_000), 2),
        "prazo": int(numero("prazo", 7, 0, 365)),
        "cobertura": int(numero("cobertura", 30, 0, 365)),
        "prefixos": _compras_normalizar_lista(body.get("prefixos")),
        "palavras": _compras_normalizar_lista(body.get("palavras")),
        "codigos": _compras_normalizar_lista(body.get("codigos")),
    }


def compras_fornecedores() -> list[dict[str, Any]]:
    with DB_LOCK:
        rows = DB.execute("SELECT nome, config, updated_at, updated_by FROM compras_fornecedores_v1800 ORDER BY nome").fetchall()
    out = []
    for r in rows:
        try:
            cfg = json.loads(r["config"] or "{}")
        except Exception:
            cfg = {}
        out.append({"nome": r["nome"], **compras_fornecedor_config(cfg), "atualizado_em": r["updated_at"], "atualizado_por": r["updated_by"]})
    return out


def compras_salvar_fornecedor(nome: str, body: dict[str, Any], usuario: str) -> dict[str, Any]:
    nome = re.sub(r"\s+", " ", str(nome or "").strip().upper())[:80]
    if not nome:
        raise ValueError("Informe o nome do fornecedor/marca.")
    cfg = compras_fornecedor_config(body)
    if not (cfg["prefixos"] or cfg["palavras"] or cfg["codigos"]):
        raise ValueError("Informe ao menos uma regra: começo do código, palavra na descrição ou lista de códigos.")
    with DB_LOCK:
        DB.execute(
            "INSERT INTO compras_fornecedores_v1800(nome,config,updated_at,updated_by) VALUES(?,?,?,?) "
            "ON CONFLICT(nome) DO UPDATE SET config=excluded.config, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (nome, json.dumps(cfg, ensure_ascii=False), now_iso(), usuario),
        )
        DB.commit()
    return {"nome": nome, **cfg}


def compras_excluir_fornecedor(nome: str) -> None:
    with DB_LOCK:
        DB.execute("DELETE FROM compras_fornecedores_v1800 WHERE nome=?", (str(nome or "").strip().upper(),))
        DB.commit()


def custos_registrar_auto(itens: list[tuple[str, float]], origem: str) -> None:
    """V18.07: guarda o último custo > 0 visto de cada código (nunca grava zero)."""
    linhas = []
    agora = now_iso()
    for codigo, custo in itens:
        codigo = str(codigo or "").strip().upper()
        try:
            valor = float(custo)
        except (TypeError, ValueError):
            continue
        if codigo and valor > 0:
            linhas.append((codigo, round(valor, 4), origem, agora))
    if not linhas:
        return
    with DB_LOCK:
        DB.executemany(
            "INSERT INTO custos_conhecidos_v1807(codigo,custo_auto,origem_auto,data_auto) VALUES(?,?,?,?) "
            "ON CONFLICT(codigo) DO UPDATE SET custo_auto=excluded.custo_auto, origem_auto=excluded.origem_auto, "
            "data_auto=excluded.data_auto",
            linhas,
        )
        DB.commit()


def custo_manual_salvar(codigo: str, custo: Any, usuario: str) -> dict[str, Any]:
    """V18.07: custo digitado na Demanda de Compra. Vazio/0 apaga."""
    codigo = str(codigo or "").strip().upper()
    if not codigo:
        raise ValueError("Informe o código da peça.")
    valor = None
    if str(custo if custo is not None else "").strip():
        # Aceita "152,52" e "1.234,56", como é digitado no Brasil.
        valor = float(custo) if isinstance(custo, (int, float)) else _apollo_number(custo)
        if valor is None or valor < 0 or valor > 10_000_000:
            raise ValueError("Custo inválido.")
    valor = round(valor, 4) if valor else None
    with DB_LOCK:
        DB.execute(
            "INSERT INTO custos_conhecidos_v1807(codigo,custo_manual,data_manual,manual_por) VALUES(?,?,?,?) "
            "ON CONFLICT(codigo) DO UPDATE SET custo_manual=excluded.custo_manual, data_manual=excluded.data_manual, "
            "manual_por=excluded.manual_por",
            (codigo, valor, now_iso() if valor else "", usuario if valor else ""),
        )
        DB.commit()
    return {"codigo": codigo, "custo_manual": valor}


def _compras_custos_reserva(conhecidos: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Custos de reserva por código: manual, último custo visto e custo gravado na
    venda do ML (rentabilidade), cada um com a data."""
    out: dict[str, dict[str, Any]] = {}
    with DB_LOCK:
        for r in DB.execute("SELECT * FROM custos_conhecidos_v1807").fetchall():
            out[r["codigo"]] = {"manual": r["custo_manual"], "data_manual": r["data_manual"], "manual_por": r["manual_por"],
                                "auto": r["custo_auto"], "origem_auto": r["origem_auto"], "data_auto": r["data_auto"]}
        vendas = DB.execute(
            "SELECT sku, unit_cost, captured_at FROM ml_profitability_snapshot WHERE unit_cost > 0 ORDER BY captured_at"
        ).fetchall()
    for r in vendas:
        codigo = _compras_codigo(str(r["sku"] or ""), conhecidos)
        if codigo:
            alvo = out.setdefault(codigo, {})
            alvo["venda"] = r["unit_cost"]
            alvo["data_venda"] = r["captured_at"]
    return out


def _compras_resolver_custo(atual: float | None, reserva: dict[str, Any]) -> tuple[float | None, str, str]:
    """(custo, origem, data). O custo médio atual do Apollo manda; sem ele, vale o
    digitado, depois o último custo visto, depois o custo da última venda no ML."""
    if atual:
        return atual, "apollo", ""
    if reserva.get("manual"):
        return float(reserva["manual"]), "manual", reserva.get("data_manual") or ""
    candidatos = []
    if reserva.get("auto"):
        candidatos.append((reserva.get("data_auto") or "", float(reserva["auto"]), "ultimo"))
    if reserva.get("venda"):
        candidatos.append((reserva.get("data_venda") or "", float(reserva["venda"]), "venda"))
    if candidatos:
        data, custo, origem = max(candidatos)
        return custo, origem, data
    return None, "", ""


# ---------------------------------------------------------------------------
# V18.23: Promoções do Mercado Livre dentro do Precificador.
#
# Levantamento feito na API real em 23/09/2026 (conta 224625106):
#   GET  /seller-promotions/users/{id}      -> campanhas oferecidas ao vendedor
#   GET  /seller-promotions/items/{MLB}     -> o que se aplica ao anúncio, com
#        original_price, suggested_discounted_price e o PISO/TETO do próprio ML
#        (min_discounted_price / max_discounted_price)
#   POST /seller-promotions/items/{MLB}     -> aplica (deal_price + datas)
#   DELETE /seller-promotions/items/{MLB}   -> remove
#
# O tipo que a Noromak usa é PRICE_DISCOUNT (desconto próprio): em 23/09 havia
# 8 promoções ativas, todas de 29-30 dias. A documentação fala em "máximo 14
# dias", mas a conta real contradiz — mandamos a data que o dono escolher e
# deixamos o ML recusar se for o caso, em vez de inventar um limite.
# ---------------------------------------------------------------------------
ML_PROMO_TIPO = "PRICE_DISCOUNT"


def _ml_promo_http(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """Chamada JSON à API de promoções. A ml_http_json() manda form-urlencoded,
    que esta API não aceita no POST."""
    url = ML_API_BASE + path

    def chamar(token: str) -> Any:
        body = json_dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "User-Agent": f"NoromakExpedicao/{APP_VERSION}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        with urllib.request.urlopen(req, timeout=ML_HTTP_TIMEOUT) as resp:
            texto = resp.read().decode("utf-8", "replace").strip()
            return json.loads(texto) if texto else {}

    token, _ = ml_access_token()
    try:
        return chamar(token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            token, _ = ml_access_token(force_refresh=True)
            return chamar(token)
        detalhe = ""
        try:
            detalhe = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"Mercado Livre respondeu HTTP {exc.code}: {detalhe}") from exc


def ml_promocoes_do_item(mlb: str) -> list[dict[str, Any]]:
    mlb = str(mlb or "").strip().upper()
    if not mlb.startswith("MLB"):
        mlb = "MLB" + re.sub(r"\D", "", mlb)
    try:
        dados = _ml_promo_http("GET", f"/seller-promotions/items/{mlb}?app_version=v2")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return []   # anúncio sem promoção disponível
        raise
    if isinstance(dados, list):
        return [x for x in dados if isinstance(x, dict)]
    resultados = dados.get("results") if isinstance(dados, dict) else None
    if isinstance(resultados, list):
        return [x for x in resultados if isinstance(x, dict)]
    return [dados] if isinstance(dados, dict) and dados else []


def _precificador_produtos_ml() -> dict[str, dict[str, Any]]:
    """SKU -> custo/imposto e as taxas do ML, como o Precificador já calcula."""
    raw, _, _ = get_storage("precificador_online_v1")
    try:
        dados = json.loads(raw or "{}")
    except Exception:
        dados = {}
    out: dict[str, dict[str, Any]] = {}
    for p in (dados.get("products") if isinstance(dados, dict) else None) or []:
        if not isinstance(p, dict) or p.get("isKit"):
            continue
        sku = str(p.get("sku") or "").strip().upper()
        if not sku or sku in out:
            continue
        ml = (p.get("marketplaces") or {}).get("ml") or {}
        custo = _money_number(p.get("custo")) or 0.0
        out[sku] = {
            "sku": sku,
            "produto": str(p.get("produto") or "").strip(),
            "curva": normalize_abc_curve_value(p.get("curva")) or "",
            "custo": float(custo),
            "imposto": float(p.get("imposto") or 0),
            "comissao": float(ml.get("comissao") or 0),
            "taxa_fixa": float(ml.get("taxaFixa") or 0) + float(ml.get("frete") or 0),
            "margem_alvo": float(ml.get("margem") or 0.15),
        }
    return out


def promo_conta(prod: dict[str, Any], preco: float) -> dict[str, Any]:
    """Mesma conta do Precificador: preço − comissão − taxa − imposto − custo."""
    preco = float(preco or 0)
    comissao = preco * prod["comissao"]
    imposto = preco * prod["imposto"]
    lucro = preco - comissao - prod["taxa_fixa"] - imposto - prod["custo"]
    return {
        "preco": round(preco, 2), "comissao": round(comissao, 2), "imposto": round(imposto, 2),
        "taxa_fixa": round(prod["taxa_fixa"], 2), "custo": round(prod["custo"], 2),
        "lucro": round(lucro, 2), "margem": round(lucro / preco, 4) if preco > 0 else 0.0,
    }


def promo_preco_minimo(prod: dict[str, Any]) -> float | None:
    """Preço em que o lucro é zero. Abaixo disso é vender no prejuízo."""
    divisor = 1 - prod["comissao"] - prod["imposto"]
    if divisor <= 0 or prod["custo"] <= 0:
        return None
    return round((prod["custo"] + prod["taxa_fixa"]) / divisor, 2)


def promo_preco_ideal(prod: dict[str, Any]) -> float | None:
    divisor = 1 - prod["comissao"] - prod["imposto"] - prod["margem_alvo"]
    if divisor <= 0 or prod["custo"] <= 0:
        return None
    return round((prod["custo"] + prod["taxa_fixa"]) / divisor, 2)


PROMO_PAINEL_MAX = 40


def _ml_vinculos_mlb_sku() -> dict[str, str]:
    raw, _, _ = get_storage("ml_sku_mlb_links_v1")
    try:
        dados = json.loads(raw or "{}")
    except Exception:
        dados = {}
    out: dict[str, str] = {}
    if isinstance(dados, dict):
        for sku, linhas in dados.items():
            for row in (linhas if isinstance(linhas, list) else []):
                mlb = row.get("mlb") if isinstance(row, dict) else row
                mlb = str(mlb or "").strip().upper()
                if mlb and mlb not in out:
                    out[mlb] = str(sku or "").strip().upper()
    return out


def ml_sku_do_anuncio(bruto: Any, conhecidos: dict[str, Any] | None = None) -> str:
    """V18.27: no SKU do anúncio do ML o código é o que vem DEPOIS do traço
    (ex.: 1304-6008.004.206.00.6 -> 6008.004.206.00.6).

    O prefixo do ML é sempre numérico, e isso importa: há códigos do Apollo que
    já têm traço (816.6090-0). Cortar sem olhar transformaria esse em "0".
    Só corta quando o pedaço antes do traço é só dígitos.
    """
    texto = str(bruto or "").strip().upper()
    if not texto:
        return ""
    if conhecidos and texto in conhecidos:
        return texto                      # já é o código do cadastro
    if "-" in texto:
        antes, depois = texto.split("-", 1)
        antes, depois = antes.strip(), depois.strip()
        if depois and antes.isdigit():
            return depois
    return texto


ML_PROMO_TIPO_NOME = {
    "PRICE_DISCOUNT": "desconto próprio",
    "DEAL": "campanha",
    "SMART": "campanha do ML",
    "MARKETPLACE_CAMPAIGN": "campanha do ML",
    "SELLER_CAMPAIGN": "campanha do vendedor",
    "LIGHTNING": "oferta relâmpago",
    "DOD": "oferta do dia",
    "UNHEALTHY_STOCK": "estoque parado",
    "PRICE_MATCHING": "preço competitivo",
    "PRICE_MATCHING_MELI_ALL": "preço competitivo",
    "PRE_NEGOTIATED": "pré-negociada",
    "VOLUME": "desconto por volume",
    "BANK": "Pix / banco",
}


def ml_promo_vigente(promos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """V18.42: a promoção que está valendo AGORA no anúncio, de qualquer tipo.
    Preferência: o desconto próprio; depois qualquer outra com preço; o BANK
    (desconto no Pix) só se não houver outra — ele não muda o preço do anúncio."""
    ativas = [x for x in promos if str(x.get("status") or "").lower() == "started"]
    if not ativas:
        return None
    def peso(x: dict[str, Any]) -> int:
        tipo = str(x.get("type") or "")
        if tipo == ML_PROMO_TIPO:
            return 0
        if tipo == "BANK":
            return 3
        return 1 if x.get("price") else 2
    return sorted(ativas, key=peso)[0]


def promocoes_painel(mlbs: list[str]) -> dict[str, Any]:
    """Junta o que o ML diz da promoção com a conta do Precificador.

    V18.25: o SKU vem de duas fontes — o vínculo MLB↔SKU (canônico) e, quando
    ele não existe, o `seller_custom_field` do próprio anúncio. Assim a tela
    mostra o anúncio mesmo sem vínculo e oferece criar o vínculo ali.
    """
    produtos = _precificador_produtos_ml()
    vinculos = _ml_vinculos_mlb_sku()
    detalhes = ml_itens_detalhe(list(mlbs)[:PROMO_PAINEL_MAX])
    itens: list[dict[str, Any]] = []
    erros: list[dict[str, str]] = []
    for bruto in list(mlbs)[:PROMO_PAINEL_MAX]:
        mlb = str(bruto or "").strip().upper()
        if not mlb:
            continue
        if not mlb.startswith("MLB"):
            mlb = "MLB" + re.sub(r"\D", "", mlb)
        det = detalhes.get(mlb) or {}
        status_ml = str(det.get("status") or "")
        # V18.41: anúncio sem estoque, pausado ou encerrado NÃO some da tabela.
        # O encerrado nem é consultado (o ML responde 400 "status not allowed");
        # o pausado que o ML recusar aparece com o motivo, e não só no aviso.
        promos: list[dict[str, Any]] = []
        erro_promo = ""
        if status_ml == "closed":
            erro_promo = "Anúncio encerrado no Mercado Livre — reative lá para criar promoção."
        else:
            try:
                promos = ml_promocoes_do_item(mlb)
            except Exception as exc:
                if status_ml in ("", "active"):
                    erros.append({"mlb": mlb, "erro": str(exc)[:220]})
                    continue
                erro_promo = "O Mercado Livre não aceita promoção neste anúncio agora (" + status_ml + ")."
        proprio = next((x for x in promos if x.get("type") == ML_PROMO_TIPO), None)
        campanha = next((x for x in promos if x.get("type") == "DEAL"), None)
        base = proprio or campanha or {}
        vigente = ml_promo_vigente(promos)
        sku_ml = str(det.get("sku_ml") or "").strip().upper()
        # O anúncio costuma trazer "prefixo-código" (ex.: 1304-6008.004.206.00.6);
        # o Precificador guarda só o código. _compras_codigo já resolve os dois.
        sku_ml_codigo = ml_sku_do_anuncio(sku_ml, produtos)
        sku_vinculo = vinculos.get(mlb, "")
        # O vínculo manda; sem ele, usa o SKU que está no próprio anúncio.
        sku = sku_vinculo or (sku_ml_codigo if sku_ml_codigo in produtos else "")
        prod = produtos.get(sku)
        linha: dict[str, Any] = {
            "mlb": mlb, "sku": sku,
            "vinculado": bool(sku_vinculo),
            "sku_anuncio": sku_ml,
            "sku_anuncio_codigo": sku_ml_codigo,
            "sku_anuncio_existe": bool(sku_ml_codigo and sku_ml_codigo in produtos),
            "titulo_ml": det.get("titulo") or "",
            "produto": prod["produto"] if prod else (det.get("titulo") or ""),
            "curva": prod["curva"] if prod else "",
            "preco_cheio": base.get("original_price") or det.get("preco"),
            "status_ml": status_ml,
            "sub_status_ml": det.get("sub_status") or [],
            "estoque_ml": det.get("estoque"),
            "erro_promo": erro_promo,
            "sugerido_ml": base.get("suggested_discounted_price"),
            "piso_ml": base.get("min_discounted_price"),
            "teto_ml": base.get("max_discounted_price"),
            # V18.42: a promoção EM VIGOR pode ser de qualquer tipo (SMART,
            # DEAL, relâmpago...), não só o desconto próprio. Olhar só o
            # PRICE_DISCOUNT fazia anúncio em promoção aparecer "sem promoção".
            "status": (vigente or proprio or {}).get("status") or "",
            "preco_promo": (vigente or proprio or {}).get("price") or None,
            "inicio": (vigente or proprio or {}).get("start_date"),
            "fim": (vigente or proprio or {}).get("finish_date"),
            "promo_tipo": (vigente or {}).get("type") or "",
            "promo_tipo_nome": ML_PROMO_TIPO_NOME.get(str((vigente or {}).get("type") or ""),
                                                      str((vigente or {}).get("type") or "")),
            "promo_nome": (vigente or {}).get("name") or "",
            "tem_custo": bool(prod),
            "campanha": None,
        }
        if campanha:
            linha["campanha"] = {
                "id": campanha.get("id"), "nome": campanha.get("name"),
                "inicio": campanha.get("start_date"), "fim": campanha.get("finish_date"),
                "status": campanha.get("status"),
            }
        if prod:
            linha["minimo"] = promo_preco_minimo(prod)
            linha["ideal"] = promo_preco_ideal(prod)
            # V18.28: os mesmos campos que o Precificador mostra, para decidir a
            # promoção sem precisar abrir a outra tela.
            linha["precificador"] = {
                "custo": round(prod["custo"], 2),
                "imposto": prod["imposto"],
                "comissao": prod["comissao"],
                "taxa_fixa": round(prod["taxa_fixa"], 2),
                "margem_alvo": prod["margem_alvo"],
                "curva": prod["curva"],
            }
            if linha["ideal"]:
                no_ideal = promo_conta(prod, linha["ideal"])
                linha["precificador"]["lucro_ideal"] = no_ideal["lucro"]
                linha["precificador"]["margem_ideal"] = no_ideal["margem"]
            referencia = linha["preco_promo"] or linha["sugerido_ml"] or 0
            if referencia:
                linha["conta"] = promo_conta(prod, float(referencia))
        itens.append(linha)
    # V18.27: a tela precisa dos SKUs válidos para autocompletar e validar o
    # vínculo digitado à mão (o SKU do anúncio no ML nem sempre é o do Apollo —
    # há anúncios com "ID ALEPH: 46347830" no lugar).
    catalogo = [{"sku": c, "nome": p["produto"]} for c, p in sorted(produtos.items())]
    return {"ok": True, "itens": itens, "erros": erros, "limite": PROMO_PAINEL_MAX,
            "skus": catalogo}


_ML_ANUNCIOS_CACHE: dict[str, Any] = {"ids": [], "quando": 0.0}
ML_ANUNCIOS_CACHE_SEGUNDOS = 600


def ml_todos_os_anuncios(forcar: bool = False) -> list[str]:
    """V18.28: TODOS os anúncios do vendedor, em qualquer status.

    Duas armadilhas que faziam a tela mostrar menos do que existe:
      - `status=active` escondia 380 anúncios pausados (1174 de 1591);
      - a paginação por offset estoura em ~1000 ("Invalid limit and offset
        values"), então o resto era inalcançável.
    O jeito certo é `search_type=scan` com scroll_id. São ~16 chamadas para
    1591 anúncios (≈4s), por isso o resultado fica em cache.
    """
    agora = time.time()
    if (not forcar and _ML_ANUNCIOS_CACHE["ids"]
            and agora - float(_ML_ANUNCIOS_CACHE["quando"]) < ML_ANUNCIOS_CACHE_SEGUNDOS):
        return list(_ML_ANUNCIOS_CACHE["ids"])
    linha = ml_integration_row()
    seller = str((linha["seller_id"] if linha else "") or "").strip()
    if not seller:
        raise RuntimeError("Mercado Livre não está conectado. Conecte em Admin → Integrações.")
    base = f"/users/{seller}/items/search?search_type=scan&limit=100"
    ids: list[str] = []
    scroll = ""
    for _ in range(60):   # teto de segurança: 6.000 anúncios
        url = base + ("&scroll_id=" + quote(scroll) if scroll else "")
        dados = ml_api_get(url)
        if not isinstance(dados, dict):
            break
        resultados = [str(x) for x in (dados.get("results") or []) if x]
        if not resultados:
            break
        ids.extend(resultados)
        scroll = str(dados.get("scroll_id") or scroll)
        if not scroll:
            break
    vistos: set[str] = set()
    unicos = [x for x in ids if not (x in vistos or vistos.add(x))]
    _ML_ANUNCIOS_CACHE["ids"] = unicos
    _ML_ANUNCIOS_CACHE["quando"] = agora
    return list(unicos)


def ml_itens_do_vendedor(offset: int = 0, limite: int = 50, forcar: bool = False,
                         busca: str = "") -> dict[str, Any]:
    """Uma página da lista completa de anúncios (ver ml_todos_os_anuncios).

    V18.40: a busca da tela filtrava só os 40 anúncios da página aberta — o
    anúncio que estava na página 20 "não existia". Agora a busca roda aqui,
    sobre TODOS os anúncios (índice com título/SKU/status).
    V18.41: encerrados e sem estoque continuam na lista (vão para o fim).
    """
    limite = max(1, min(50, int(limite or 50)))
    offset = max(0, int(offset or 0))
    todos = ml_todos_os_anuncios(forcar)
    try:
        indice = ml_indice_anuncios(todos, forcar)
    except Exception:
        indice = {}   # sem índice, lista como antes (nunca some anúncio por erro)
    # V18.41: nada sai da lista (estoque zerado/pausado/encerrado também
    # aparece); só a ORDEM muda: ativos primeiro, encerrados no fim.
    # Sem detalhe = status desconhecido: fica junto dos ativos.
    ordem = {"": 0, "active": 0, "paused": 1, "closed": 3}
    todos = sorted(todos, key=lambda m: ordem.get(str((indice.get(m) or {}).get("status") or ""), 2))
    if str(busca or "").strip():
        todos = ml_anuncios_filtrar(todos, indice, busca)
    return {
        "mlbs": todos[offset:offset + limite],
        "total": len(todos),
        "offset": offset, "limite": limite,
    }


def ml_itens_detalhe(mlbs: list[str]) -> dict[str, dict[str, Any]]:
    """Título, preço e o SKU que o vendedor cadastrou no próprio anúncio."""
    out: dict[str, dict[str, Any]] = {}
    campos = "id,title,price,status,sub_status,available_quantity,seller_custom_field,attributes"
    for i in range(0, len(mlbs), 20):   # a API aceita 20 ids por chamada
        lote = [x for x in mlbs[i:i + 20] if x]
        if not lote:
            continue
        try:
            resp = ml_api_get("/items?ids=" + ",".join(lote) + "&attributes=" + campos)
        except Exception:
            continue
        for linha in (resp if isinstance(resp, list) else []):
            corpo = (linha or {}).get("body") or {}
            mlb = str(corpo.get("id") or "").strip().upper()
            if not mlb:
                continue
            # V18.27: o atributo SELLER_SKU é a fonte boa. O seller_custom_field
            # é campo livre e muitas vezes tem outra coisa (ex.: "Id Aleph:
            # 46347830"), o que fazia a tela mostrar SKU errado.
            sku = ""
            for attr in corpo.get("attributes") or []:
                if str(attr.get("id") or "").upper() == "SELLER_SKU":
                    sku = str(attr.get("value_name") or attr.get("value_id") or "").strip()
                    break
            if not sku:
                sku = str(corpo.get("seller_custom_field") or "").strip()
            out[mlb] = {"titulo": str(corpo.get("title") or "").strip(),
                        "preco": corpo.get("price"), "sku_ml": sku,
                        "status": corpo.get("status"),
                        "sub_status": corpo.get("sub_status") or [],
                        "estoque": corpo.get("available_quantity")}
    return out


_ML_INDICE_CACHE: dict[str, Any] = {"itens": {}, "quando": 0.0}
_ML_INDICE_LOCK = threading.Lock()
ML_INDICE_CACHE_SEGUNDOS = 1800


def ml_indice_anuncios(mlbs: list[str], forcar: bool = False) -> dict[str, dict[str, Any]]:
    """V18.40: título, SKU e status de todos os anúncios, para buscar e filtrar
    sem depender da página aberta. ~80 chamadas de 20 ids para 1.600 anúncios,
    em 4 linhas paralelas; fica em cache por 30 min e só busca o que falta."""
    with _ML_INDICE_LOCK:
        agora = time.time()
        if forcar or agora - float(_ML_INDICE_CACHE["quando"]) >= ML_INDICE_CACHE_SEGUNDOS:
            _ML_INDICE_CACHE["itens"] = {}
            _ML_INDICE_CACHE["quando"] = agora
        itens: dict[str, dict[str, Any]] = _ML_INDICE_CACHE["itens"]
        faltam = [m for m in mlbs if m and m not in itens]
        if faltam:
            ml_access_token()   # renova o token antes, e não em 4 linhas ao mesmo tempo
            lotes = [faltam[i:i + 20] for i in range(0, len(faltam), 20)]
            fila = list(lotes)
            trava = threading.Lock()

            def trabalhar() -> None:
                while True:
                    with trava:
                        if not fila:
                            return
                        lote = fila.pop()
                    resultado = ml_itens_detalhe(lote)
                    with trava:
                        itens.update(resultado)

            linhas = [threading.Thread(target=trabalhar, daemon=True) for _ in range(min(4, len(lotes)))]
            for t in linhas:
                t.start()
            for t in linhas:
                t.join(timeout=120)
        return dict(itens)


def _ml_busca_normalizar(texto: Any) -> str:
    base = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return base.lower().strip()


def ml_anuncios_filtrar(mlbs: list[str], indice: dict[str, dict[str, Any]], busca: str) -> list[str]:
    """V18.40: acha o anúncio pelo número do MLB (com ou sem "MLB"), pelo SKU do
    anúncio, pelo SKU vinculado ou por palavras do título.

    Busca que parece código (só dígitos, ponto e traço — ex.: "816", o começo
    dos códigos da Schulz) procura nos SKUs e NÃO dentro do número do MLB:
    "816" aparece no meio de centenas de MLBs e afogaria os produtos. O número
    do MLB só entra com 6+ dígitos. Quem COMEÇA com o código vem primeiro.
    """
    q = _ml_busca_normalizar(busca)
    if not q:
        return list(mlbs)
    numero = re.fullmatch(r"(?:mlb)?[\s-]*(\d{6,})", q)
    codigo = bool(re.fullmatch(r"[\d.\-/]+", q))
    palavras = [p for p in q.split() if p]
    vinculos = _ml_vinculos_mlb_sku()
    comeca: list[str] = []
    contem: list[str] = []
    for mlb in mlbs:
        det = indice.get(mlb) or {}
        skus = [_ml_busca_normalizar(x) for x in (
            det.get("sku_ml"), ml_sku_do_anuncio(det.get("sku_ml")), vinculos.get(mlb, "")) if x]
        if codigo and any(x.startswith(q) for x in skus):
            comeca.append(mlb)
            continue
        if numero and numero.group(1) in re.sub(r"\D", "", mlb):
            comeca.append(mlb)
            continue
        if codigo:
            if any(q in x for x in skus):
                contem.append(mlb)
            continue
        texto = _ml_busca_normalizar(" ".join([mlb, *skus, str(det.get("titulo") or "")]))
        if all(p in texto for p in palavras):
            contem.append(mlb)
    return comeca + contem


PROMO_DATAS_KEY = "ml_promocoes_datas_v1"


def promo_atualizar_data_planilha(mlb: str, fim: str, ator: str = "") -> bool:
    """V18.26: a base do monitor de promoções (a planilha importada) é a outra
    fonte de data. Se ela não souber do novo vencimento, o anúncio continua
    listado como VENCIDO lá mesmo depois de a promoção ser renovada aqui.
    Guarda o MLB só com dígitos, como o resto da base já faz."""
    dia = str(fim or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", dia):
        return False
    numero = re.sub(r"\D", "", str(mlb or ""))
    if not numero:
        return False
    atual, _, _ = get_storage(PROMO_DATAS_KEY)
    try:
        dados = json.loads(atual or "{}")
    except Exception:
        return False
    if not isinstance(dados, dict) or not isinstance(dados.get("rows"), list):
        return False
    achou = False
    for linha in dados["rows"]:
        if isinstance(linha, dict) and re.sub(r"\D", "", str(linha.get("mlb") or "")) == numero:
            linha["date"] = dia
            achou = True
    if not achou:
        dados["rows"].append({"mlb": numero, "date": dia, "manualZero": False, "priceOk": False})
    dados["updatedAt"] = now_iso()
    save_storage(PROMO_DATAS_KEY, atual, json_dumps(dados), False,
                 actor=ator or "promocoes", actor_role="admin", source_screen="promocoes")
    return True


def ml_promocao_aplicar(mlb: str, preco: Any, inicio: str, fim: str, ator: str = "") -> dict[str, Any]:
    """Sobe um desconto próprio no ML. A trava do preço mínimo mora AQUI, no
    servidor — a tela também avisa, mas quem decide é este ponto."""
    mlb = str(mlb or "").strip().upper()
    if not mlb.startswith("MLB"):
        mlb = "MLB" + re.sub(r"\D", "", mlb)
    try:
        preco = round(float(str(preco).replace(",", ".")), 2)
    except (TypeError, ValueError):
        raise ValueError("Preço inválido.")
    if preco <= 0:
        raise ValueError("Preço inválido.")

    sku = _ml_vinculos_mlb_sku().get(mlb, "")
    prod = _precificador_produtos_ml().get(sku)
    if not prod:
        raise ValueError(f"{mlb} não tem SKU com custo no Precificador. Sem custo não dá para garantir a margem.")
    minimo = promo_preco_minimo(prod)
    if minimo is None:
        raise ValueError(f"Sem custo válido para {sku}: não dá para calcular o preço mínimo.")
    if preco < minimo:
        perda = promo_conta(prod, preco)["lucro"]
        raise ValueError(
            f"{_brl_srv(preco)} está abaixo do preço mínimo ({_brl_srv(minimo)}) de {sku}. "
            f"Nesse preço a venda dá prejuízo de {_brl_srv(abs(perda))}."
        )

    # Piso e teto que o próprio Mercado Livre impõe para este anúncio.
    base = next((x for x in ml_promocoes_do_item(mlb) if x.get("type") == ML_PROMO_TIPO), {})
    piso, teto = base.get("min_discounted_price"), base.get("max_discounted_price")
    if piso and preco < float(piso):
        raise ValueError(f"O Mercado Livre não aceita menos de {_brl_srv(float(piso))} neste anúncio.")
    if teto and preco > float(teto):
        raise ValueError(f"O Mercado Livre não aceita mais de {_brl_srv(float(teto))} neste anúncio.")

    payload = {
        "deal_price": preco,
        "start_date": str(inicio or "").strip(),
        "finish_date": str(fim or "").strip(),
        "promotion_type": ML_PROMO_TIPO,
    }
    resposta = _ml_promo_http("POST", f"/seller-promotions/items/{mlb}?app_version=v2", payload)
    # Sem isto o monitor de promoções (base da planilha) continua dizendo que
    # este anúncio está vencido, e alguém teria de corrigir a data na mão.
    data_atualizada = False
    try:
        data_atualizada = promo_atualizar_data_planilha(mlb, payload["finish_date"], ator)
    except Exception as exc:
        print(f"[promocoes] nao foi possivel atualizar a data da planilha de {mlb}: {exc}", file=sys.stderr)
    try:
        ml_log_event("promocao", f"/seller-promotions/items/{mlb}", sku, ator or "",
                     f"PROMOCAO APLICADA {_brl_srv(preco)}", json_dumps(payload))
    except Exception:
        pass
    return {"ok": True, "mlb": mlb, "sku": sku, "preco": preco,
            "inicio": payload["start_date"], "fim": payload["finish_date"],
            "data_planilha_atualizada": data_atualizada,
            "margem": promo_conta(prod, preco)["margem"], "resposta": resposta}


def ml_promocao_remover(mlb: str, ator: str = "") -> dict[str, Any]:
    mlb = str(mlb or "").strip().upper()
    if not mlb.startswith("MLB"):
        mlb = "MLB" + re.sub(r"\D", "", mlb)
    resposta = _ml_promo_http(
        "DELETE", f"/seller-promotions/items/{mlb}?promotion_type={ML_PROMO_TIPO}&app_version=v2")
    try:
        ml_log_event("promocao", f"/seller-promotions/items/{mlb}", "", ator or "", "PROMOCAO REMOVIDA", "")
    except Exception:
        pass
    return {"ok": True, "mlb": mlb, "resposta": resposta}


def _compras_produtos_precificador() -> dict[str, dict[str, Any]]:
    """Código Apollo -> {descricao, custo, curva}, a partir da lista do Precificador."""
    raw, _, _ = get_storage("precificador_online_v1")
    try:
        dados = json.loads(raw or "{}")
    except Exception:
        dados = {}
    produtos = dados.get("products") if isinstance(dados, dict) else None
    out: dict[str, dict[str, Any]] = {}
    for p in produtos if isinstance(produtos, list) else []:
        if not isinstance(p, dict) or p.get("isKit"):
            continue
        codigo = str(p.get("sku") or "").strip().upper()
        if not codigo or codigo in out:
            continue
        custo = _money_number(p.get("custo"))
        out[codigo] = {
            "descricao": str(p.get("produto") or "").strip(),
            "custo": round(float(custo), 4) if custo is not None and custo > 0 else None,
            "curva": normalize_abc_curve_value(p.get("curva")) or "",
        }
    return out


def _compras_codigo(sku: str, conhecidos: dict[str, Any]) -> str:
    """SKU do anúncio -> código Apollo (o do Precificador). Aceita o código puro
    e o formato prefixo-código (ex.: 157507-0893556005)."""
    s = str(sku or "").strip().upper()
    if not s:
        return ""
    if s in conhecidos:
        return s
    if "-" in s:
        antes, depois = s.split("-", 1)
        for candidato in (depois.strip(), antes.strip()):
            if candidato in conhecidos:
                return candidato
    return ""


def compras_vendas_por_codigo(dias: int, conhecidos: dict[str, Any]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Unidades vendidas por código Apollo nos últimos `dias` (ML e Shopee).
    Kit vira as unidades de cada componente; "A/B" (anúncio com duas peças) conta
    uma de cada."""
    desde = (datetime.now(BR_TZ).date() - timedelta(days=max(1, dias) - 1)).isoformat()
    with DB_LOCK:
        rows = DB.execute(
            "SELECT sku, marketplace, SUM(qty) AS qtd FROM vendas_v1799 WHERE sale_day >= ? GROUP BY sku, marketplace",
            (desde,),
        ).fetchall()
    kits = full_kit_compositions([r["sku"] for r in rows])
    vendas: dict[str, dict[str, int]] = {}
    sem_codigo: dict[str, int] = {}
    for r in rows:
        sku = str(r["sku"] or "").strip()
        qtd = int(r["qtd"] or 0)
        if not sku or qtd <= 0:
            continue
        comps = (kits.get(sku) or {}).get("componentes")
        if not comps:
            partes = [x for x in sku.split("/") if x.strip()] if "/" in sku else [sku]
            comps = [{"sku": x, "quantidade": 1} for x in partes]
        for c in comps:
            codigo = _compras_codigo(c["sku"], conhecidos)
            if not codigo:
                sem_codigo[sku] = sem_codigo.get(sku, 0) + qtd
                continue
            alvo = vendas.setdefault(codigo, {"ml": 0, "shopee": 0})
            alvo[r["marketplace"]] = alvo.get(r["marketplace"], 0) + qtd * int(c["quantidade"])
    return vendas, sem_codigo


def compras_demanda_payload(fornecedor: str, dias: int) -> dict[str, Any]:
    nome = str(fornecedor or "").strip().upper()
    cfg = next((f for f in compras_fornecedores() if f["nome"] == nome), None)
    if not cfg:
        raise ValueError("Fornecedor não cadastrado.")
    dias = max(7, min(365, int(dias or 60)))
    produtos = _compras_produtos_precificador()
    # Marca preenchida no cadastro de produtos também conta.
    raw, _, _ = get_storage("conf_produtos")
    marca_cadastro: set[str] = set()
    for p in _rows_from_storage_value(raw):
        if str(p.get("Marca") or "").strip().upper() == nome:
            codigo = _compras_codigo(str(p.get("SKU") or ""), produtos)
            if codigo:
                marca_cadastro.add(codigo)
    codigos = set(cfg["codigos"])

    def do_fornecedor(codigo: str, desc: str) -> bool:
        texto = desc.upper()
        return (codigo in codigos or codigo in marca_cadastro
                or any(codigo.startswith(p) for p in cfg["prefixos"])
                or any(p in texto for p in cfg["palavras"]))

    vendas, sem_codigo = compras_vendas_por_codigo(dias, produtos)
    # V18.07: todo custo > 0 do Precificador vira "último custo conhecido", para
    # continuar valendo quando o estoque zerar e o Apollo devolver custo 0.
    custos_registrar_auto([(c, p["custo"]) for c, p in produtos.items() if p["custo"]], "Precificador")
    reservas = _compras_custos_reserva(produtos)
    itens = []
    for codigo, p in produtos.items():
        if not do_fornecedor(codigo, p["descricao"]):
            continue
        v = vendas.get(codigo, {})
        custo, origem, data_custo = _compras_resolver_custo(p["custo"], reservas.get(codigo, {}))
        itens.append({"codigo": codigo, "descricao": p["descricao"], "curva": p["curva"], "custo": custo,
                      "custo_origem": origem, "custo_data": data_custo,
                      "vendido": int(v.get("ml", 0) + v.get("shopee", 0)),
                      "vendido_ml": int(v.get("ml", 0)), "vendido_shopee": int(v.get("shopee", 0))})
    itens.sort(key=lambda x: (-x["vendido"], x["codigo"]))
    with DB_LOCK:
        primeiro = DB.execute("SELECT MIN(sale_day) AS d FROM vendas_v1799").fetchone()["d"]
    return {
        "ok": True,
        "fornecedor": cfg,
        "dias": dias,
        "vendas_desde": primeiro or "",
        "itens": itens,
        "sem_custo": sum(1 for x in itens if x["custo"] is None),
        "anuncios_sem_codigo": len(sem_codigo),
    }


# ---------------------------------------------------------------------------
# V18.54: Análise de ADS — o relatório "Anúncios patrocinados" do ML (.xlsx,
# Publicidade > Relatórios) cruzado com o lucro de cada anúncio.
#
# O relatório é importado à mão e lido só com a biblioteca padrão (zipfile +
# ElementTree). O lucro vem do MESMO cálculo do Financeiro (financeiro_payload):
# custo do produto, tarifa, frete e imposto de TODAS as vendas do anúncio no
# período. O Ads vem SÓ do relatório — o PADS da fatura não entra, senão o
# mesmo gasto contaria duas vezes. Nada é estimado: venda sem custo fica nula.
# ---------------------------------------------------------------------------
ADS_RELATORIO_MAX_BYTES = 20 * 1024 * 1024
ADS_RELATORIO_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
                       "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
# Cabeçalho do relatório (primeira linha de cada coluna, sem acento) -> campo.
ADS_RELATORIO_COLUNAS = {
    "desde": "desde", "ate": "ate", "campanha": "campanha",
    "titulo do anuncio patrocinado": "titulo", "codigo do anuncio": "mlb", "status": "status",
    "impressoes": "impressoes", "cliques": "cliques", "receita": "receita",
    "investimento": "investimento", "vendas diretas": "vendas_diretas",
    "vendas indiretas": "vendas_indiretas",
}


def _ads_norm(texto: Any) -> str:
    primeira = str(texto or "").strip().split("\n", 1)[0]
    sem_acento = unicodedata.normalize("NFKD", primeira).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def _ads_numero(valor: Any) -> float:
    """'2091.0', '0,46' ou '-' (o ML usa '-' para métrica sem valor) -> número."""
    s = str(valor if valor is not None else "").strip()
    if not s or s == "-":
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _ads_data(valor: Any) -> str:
    """'31-ago-2026' (como o ML escreve) ou '31/08/2026' -> '2026-08-31'."""
    try:
        return _ads_data_iso(valor)
    except ValueError:
        return ""


def _ads_data_iso(valor: Any) -> str:
    s = str(valor or "").strip().lower()
    m = re.match(r"^(\d{1,2})[-/ ]([a-zç]{3})[a-zç]*\.?[-/ ](\d{4})$", s)
    if m and m.group(2)[:3] in ADS_RELATORIO_MESES:
        return datetime(int(m.group(3)), ADS_RELATORIO_MESES[m.group(2)[:3]], int(m.group(1))).date().isoformat()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
    return ""


ADS_XLSX_MAX_DESCOMPACTADO = 150 * 1024 * 1024
ADS_XLSX_MAX_COLUNAS = 16384


def _ads_xlsx_planilhas(conteudo: bytes):
    """Gera (nome, linhas) de cada planilha; cada linha é uma lista de textos."""
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile:
        raise ValueError("O arquivo não é uma planilha .xlsx.")
    nomes = set(z.namelist())
    # V18.56: limita também a SOMA descompactada (antes só cada parte, 80 MB) — um
    # zip com várias partes grandes esgotava a memória do processo único da expedição.
    total = 0
    for info in z.infolist():
        total += max(0, int(info.file_size or 0))
        if info.file_size > 80 * 1024 * 1024 or total > ADS_XLSX_MAX_DESCOMPACTADO:
            raise ValueError("Planilha grande demais para ser um relatório de Ads.")
    textos: list[str] = []
    if "xl/sharedStrings.xml" in nomes:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{ns}si"):
            textos.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    if "xl/workbook.xml" not in nomes:
        raise ValueError("O arquivo não é uma planilha .xlsx.")
    alvos: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in nomes:
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            alvo = str(rel.get("Target") or "").lstrip("/")
            alvos[str(rel.get("Id") or "")] = alvo if alvo.startswith("xl/") else "xl/" + alvo
    folhas = ET.fromstring(z.read("xl/workbook.xml")).find(f"{ns}sheets")
    for i, folha in enumerate(list(folhas) if folhas is not None else [], start=1):
        caminho = alvos.get(str(folha.get(f"{ns_rel}id") or ""), f"xl/worksheets/sheet{i}.xml")
        if caminho not in nomes:
            continue
        linhas: list[list[str]] = []
        for row in ET.fromstring(z.read(caminho)).iter(f"{ns}row"):
            celulas: dict[int, str] = {}
            for c in row.findall(f"{ns}c"):
                ref = re.match(r"[A-Z]+", str(c.get("r") or ""))
                if not ref:
                    continue
                # V18.56: o Excel vai até XFD (16.384 colunas). Uma referência como
                # "ZZZZZZZZZZ1" montava uma lista de ~10^14 itens e travava o servidor.
                if len(ref.group(0)) > 3:
                    raise ValueError("Planilha com coluna inválida — não parece o relatório de Ads.")
                col = 0
                for ch in ref.group(0):
                    col = col * 26 + ord(ch) - 64
                if col > ADS_XLSX_MAX_COLUNAS:
                    raise ValueError("Planilha com coluna inválida — não parece o relatório de Ads.")
                v = c.find(f"{ns}v")
                if c.get("t") == "inlineStr":
                    texto = "".join(t.text or "" for t in c.iter(f"{ns}t"))
                elif v is None:
                    continue
                elif c.get("t") == "s":
                    idx = int(v.text or 0)
                    texto = textos[idx] if 0 <= idx < len(textos) else ""
                else:
                    texto = v.text or ""
                celulas[col - 1] = texto
            if celulas:
                linhas.append([celulas.get(k, "") for k in range(max(celulas) + 1)])
        yield str(folha.get("name") or ""), linhas


def ads_relatorio_ler_xlsx(conteudo: bytes) -> dict[str, Any]:
    """Lê o relatório "Anúncios patrocinados" do ML. Erro claro se não for ele."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > ADS_RELATORIO_MAX_BYTES:
        raise ValueError("Arquivo maior que 20 MB — não parece o relatório de Ads.")
    for _nome, linhas in _ads_xlsx_planilhas(conteudo):
        for pos, cab in enumerate(linhas):
            mapa = {}
            for idx, titulo in enumerate(cab):
                campo = ADS_RELATORIO_COLUNAS.get(_ads_norm(titulo))
                if campo and campo not in mapa:
                    mapa[campo] = idx
            if not {"mlb", "investimento", "receita", "desde", "ate"} <= set(mapa):
                continue
            saida: list[dict[str, Any]] = []
            inicios: set[str] = set()
            fins: set[str] = set()
            for bruta in linhas[pos + 1:]:
                get = lambda campo: bruta[mapa[campo]] if (campo in mapa and mapa[campo] < len(bruta)) else ""  # noqa: E731
                mlb = re.sub(r"[^A-Z0-9]", "", str(get("mlb")).upper())
                if not re.match(r"^MLB\d+$", mlb):
                    continue
                d1, d2 = _ads_data(get("desde")), _ads_data(get("ate"))
                if d1:
                    inicios.add(d1)
                if d2:
                    fins.add(d2)
                saida.append({
                    "mlb": mlb, "titulo": str(get("titulo")).strip(), "campanha": str(get("campanha")).strip(),
                    "status": str(get("status")).strip(),
                    "impressoes": int(_ads_numero(get("impressoes"))), "cliques": int(_ads_numero(get("cliques"))),
                    "investimento": round(_ads_numero(get("investimento")), 2),
                    "receita": round(_ads_numero(get("receita")), 2),
                    "vendas_diretas": int(_ads_numero(get("vendas_diretas"))),
                    "vendas_indiretas": int(_ads_numero(get("vendas_indiretas"))),
                })
            if not saida:
                raise ValueError("O relatório não tem nenhum anúncio (MLB).")
            if not inicios or not fins:
                raise ValueError("Não encontrei o período do relatório (colunas Desde/Até).")
            return {"periodo_inicio": min(inicios), "periodo_fim": max(fins), "linhas": saida}
    raise ValueError("Esta planilha não é o relatório de Anúncios patrocinados do Mercado Livre "
                     "(Publicidade > Relatórios > Relatório de anúncios).")


def ads_relatorio_importar(conteudo: bytes, arquivo: str = "", usuario: str = "") -> dict[str, Any]:
    """Grava o relatório. O mesmo período importado de novo substitui o anterior."""
    lido = ads_relatorio_ler_xlsx(conteudo)
    linhas = lido["linhas"]
    investimento = round(sum(x["investimento"] for x in linhas), 2)
    receita = round(sum(x["receita"] for x in linhas), 2)
    anuncios = len({x["mlb"] for x in linhas})
    with DB_LOCK:
        try:
            DB.execute("BEGIN IMMEDIATE")
            antigo = DB.execute("SELECT id FROM ads_relatorio_v1854 WHERE periodo_inicio=? AND periodo_fim=?",
                                (lido["periodo_inicio"], lido["periodo_fim"])).fetchone()
            if antigo:
                DB.execute("DELETE FROM ads_relatorio_linha_v1854 WHERE relatorio_id=?", (antigo["id"],))
                DB.execute("DELETE FROM ads_relatorio_v1854 WHERE id=?", (antigo["id"],))
            cur = DB.execute(
                "INSERT INTO ads_relatorio_v1854(periodo_inicio,periodo_fim,arquivo,linhas,anuncios,investimento,"
                "receita,importado_em,importado_por) VALUES(?,?,?,?,?,?,?,?,?)",
                (lido["periodo_inicio"], lido["periodo_fim"], str(arquivo or "")[:200], len(linhas), anuncios,
                 investimento, receita, now_iso(), str(usuario or "")))
            rid = int(cur.lastrowid)
            DB.executemany(
                "INSERT INTO ads_relatorio_linha_v1854(relatorio_id,ordem,mlb,titulo,campanha,status,impressoes,"
                "cliques,investimento,receita,vendas_diretas,vendas_indiretas) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(rid, i, x["mlb"], x["titulo"], x["campanha"], x["status"], x["impressoes"], x["cliques"],
                  x["investimento"], x["receita"], x["vendas_diretas"], x["vendas_indiretas"])
                 for i, x in enumerate(linhas)])
            DB.commit()
        except Exception:
            DB.rollback()
            raise
    return {"ok": True, "id": rid, "periodo_inicio": lido["periodo_inicio"], "periodo_fim": lido["periodo_fim"],
            "linhas": len(linhas), "anuncios": anuncios, "investimento": investimento, "receita": receita,
            "substituiu": bool(antigo)}


def ads_relatorios_lista() -> list[dict[str, Any]]:
    with DB_LOCK:
        return [dict(r) for r in DB.execute(
            "SELECT id, periodo_inicio, periodo_fim, arquivo, linhas, anuncios, investimento, receita, "
            "importado_em, importado_por FROM ads_relatorio_v1854 ORDER BY periodo_fim DESC, id DESC").fetchall()]


def ads_analise_payload(relatorio_id: int | None = None) -> dict[str, Any]:
    relatorios = ads_relatorios_lista()
    rel = next((r for r in relatorios if relatorio_id and int(r["id"]) == int(relatorio_id)), None) \
        or (relatorios[0] if relatorios else None)
    if rel is None:
        return {"ok": True, "relatorio": None, "relatorios": []}
    with DB_LOCK:
        linhas = [dict(r) for r in DB.execute(
            "SELECT mlb, titulo, campanha, status, impressoes, cliques, investimento, receita, vendas_diretas, "
            "vendas_indiretas FROM ads_relatorio_linha_v1854 WHERE relatorio_id=? ORDER BY ordem", (rel["id"],)).fetchall()]
    mlbs = {x["mlb"] for x in linhas}

    fin = financeiro_payload(rel["periodo_inicio"], rel["periodo_fim"], "ml", limite_pedidos=0)
    taxa_imposto = FINANCEIRO_IMPOSTO_PERCENT / 100.0
    por: dict[str, dict[str, Any]] = {}
    for ped in fin["pedidos"]:
        fat_pedido = float(ped["faturamento"] or 0)
        for it in ped["itens"]:
            mlb = re.sub(r"[^A-Z0-9]", "", str(it.get("anuncio") or "").upper())
            if mlb not in mlbs:
                continue
            a = por.setdefault(mlb, {"sku": it["sku"], "unidades": 0, "pedidos": 0, "faturamento": 0.0, "cmv": 0.0,
                                     "taxa": 0.0, "frete": 0.0, "outros": 0.0, "imposto": 0.0,
                                     "sem_custo": 0.0, "sem_taxa": 0.0, "sem_frete": 0.0})
            valor = float(it["valor"] or 0)
            # Tarifa e frete são do PEDIDO: divididos entre as linhas pelo valor, como no Financeiro.
            fatia = (valor / fat_pedido) if fat_pedido else 1.0
            a["unidades"] += int(it["qty"] or 0)
            a["pedidos"] += 1
            a["faturamento"] += valor
            a["imposto"] += round(valor * taxa_imposto, 2)
            if it["cmv"] is None:
                a["sem_custo"] += valor
            else:
                a["cmv"] += float(it["cmv"])
            if ped["taxa"] is None:
                a["sem_taxa"] += valor
            else:
                a["taxa"] += float(ped["taxa"]) * fatia
            if ped["frete"] is None:
                a["sem_frete"] += valor
            else:
                a["frete"] += float(ped["frete"]) * fatia
            if ped["outros"] is not None:
                a["outros"] += float(ped["outros"]) * fatia
    for a in por.values():
        for k, v in list(a.items()):
            if isinstance(v, float):
                a[k] = round(v, 2)
        # Margem só quando tudo é conhecido; senão fica nula (pendência visível, nunca estimada).
        conhecido = a["sem_custo"] == 0 and a["sem_taxa"] == 0 and a["sem_frete"] == 0
        a["margem"] = round(a["faturamento"] - a["cmv"] - a["taxa"] - a["frete"] - a["outros"] - a["imposto"], 2) \
            if conhecido else None
    return {
        "ok": True, "relatorio": rel, "relatorios": relatorios,
        "linhas": [[x["mlb"], x["titulo"], x["campanha"], x["status"], x["impressoes"], x["cliques"],
                    x["investimento"], x["receita"], x["vendas_diretas"], x["vendas_indiretas"]] for x in linhas],
        "financeiro": por,
        "imposto_percent": FINANCEIRO_IMPOSTO_PERCENT,
        "faturamento_ml": fin["totais"]["faturamento"],
        "ads_fatura_ml": fin["ads"]["da_fatura"],
        "cobertura": {k: fin["cobertura"].get(k) for k in ("pedidos_ml", "pedidos_ml_frete_so_fatura",
                                                         "faturamento_sem_custo", "faturamento_sem_frete")},
    }


# ---------------------------------------------------------------------------
# V18.47: Oportunidades de Kit — o que os clientes JÁ compram junto (ML + Shopee)
# e pode virar um anúncio de kit para subir o ticket médio. Somente leitura:
# lê vendas_v1799, o Cadastro de Kits e o custo do Precificador. Não cria kit,
# não mexe em anúncio nem em pedido.
#
# Pedido = cesta. No ML um carrinho vira vários orders com o mesmo pack_id, então
# a cesta é o pack_id (ou o order_id quando não há pack). Na Shopee é o order_sn.
# Kit vendido continua sendo UM produto (não é expandido): senão os componentes
# de um kit que já existe apareceriam como "comprados juntos".
# ---------------------------------------------------------------------------
KITS_ANALISE_MAX_ITENS_CESTA = 12  # cesta maior que isso é atacado/revenda, não sinal de kit
KITS_ANALISE_FAIXAS_TICKET = [(0, 30), (30, 50), (50, 79), (79, 120), (120, 200), (200, None)]


def kits_analise_payload(dias: int = 90, marketplace: str = "", min_pedidos: int = 2,
                         limite_preco: float = 79.0) -> dict[str, Any]:
    dias = max(7, min(365, int(dias or 90)))
    min_pedidos = max(1, min(50, int(min_pedidos or 2)))
    limite_preco = max(1.0, min(1000.0, float(limite_preco or 79.0)))
    canal = marketplace if marketplace in VENDAS_MARKETPLACES else ""
    hoje = datetime.now(BR_TZ).date()
    d1 = hoje - timedelta(days=dias - 1)
    filtro = " AND marketplace=?" if canal else ""
    args: tuple[Any, ...] = (d1.isoformat(), hoje.isoformat()) + ((canal,) if canal else ())
    with DB_LOCK:
        vendas = [dict(r) for r in DB.execute(
            "SELECT marketplace, order_id, line_key, pack_id, sku, anuncio, title, qty, amount "
            f"FROM vendas_v1799 WHERE sale_day BETWEEN ? AND ?{filtro}", args).fetchall()]
        tarifas = {(str(r["order_id"]), str(r["line_key"])): float(r["sale_fee"])
                   for r in DB.execute(
                       "SELECT order_id, line_key, sale_fee FROM ml_venda_custo_v1830 "
                       "WHERE sale_day BETWEEN ? AND ? AND sale_fee IS NOT NULL",
                       (d1.isoformat(), hoje.isoformat())).fetchall()}
        primeiro = DB.execute("SELECT MIN(sale_day) AS d FROM vendas_v1799").fetchone()["d"]

    produtos = _compras_produtos_precificador()
    reservas = _compras_custos_reserva(produtos)
    kits = full_kit_compositions([v["sku"] for v in vendas])
    cache_custo: dict[str, Any] = {}

    def codigo_de(sku: str) -> str:
        s = str(sku or "").strip()
        return _compras_codigo(s, produtos) or s.upper()

    def chave_produto(sku: str, anuncio: str) -> str:
        s = str(sku or "").strip()
        if not s:
            return f"ANÚNCIO {anuncio}" if anuncio else ""
        if s in kits or "/" in s:
            return s.upper()  # kit ou anúncio "A/B" já é um produto próprio
        return codigo_de(s)

    # Combinações que JÁ existem como kit: Cadastro de Kits + anúncios "A/B" + kits vendidos.
    combos_existentes: dict[frozenset, str] = {}
    qtd_existentes: dict[str, set[int]] = {}

    def registrar_kit(nome: str, comps: list[dict[str, Any]]) -> None:
        codigos = {codigo_de(c.get("sku")) for c in comps if str(c.get("sku") or "").strip()}
        if len(codigos) >= 2:
            combos_existentes.setdefault(frozenset(codigos), nome)
        elif len(codigos) == 1 and len(comps) == 1:
            try:
                n = int(comps[0].get("quantidade") or 0)
            except (TypeError, ValueError):
                n = 0
            if n >= 2:
                qtd_existentes.setdefault(next(iter(codigos)), set()).add(n)

    for entry in {id(e): e for e in _load_kit_catalog().values()}.values():
        if entry.get("ativo", True):
            registrar_kit(str(entry.get("sku_kit") or ""), entry.get("componentes") or [])
    for sku_kit, info in kits.items():
        registrar_kit(sku_kit, info.get("componentes") or [])
    for v in vendas:
        s = str(v["sku"] or "").strip()
        if "/" in s and s not in kits:
            registrar_kit(s, [{"sku": x, "quantidade": 1} for x in s.split("/") if x.strip()])

    prod: dict[str, dict[str, Any]] = {}
    cestas: dict[str, dict[str, Any]] = {}
    linhas_sem_sku = 0
    for v in vendas:
        mp = str(v["marketplace"])
        sku = str(v["sku"] or "").strip()
        anuncio = str(v["anuncio"] or "").strip()
        chave = chave_produto(sku, anuncio)
        qty = max(0, int(v["qty"] or 0))
        valor = float(v["amount"] or 0)
        if not sku:
            linhas_sem_sku += 1
        cesta_id = f"{mp}:{(str(v['pack_id'] or '').strip() if mp == 'ml' else '') or v['order_id']}"
        cesta = cestas.setdefault(cesta_id, {"mp": mp, "valor": 0.0, "itens": {}})
        cesta["valor"] += valor
        if not chave or qty <= 0:
            continue
        cesta["itens"][chave] = cesta["itens"].get(chave, 0) + qty
        p = prod.setdefault(chave, {
            "produto": chave, "titulo": "", "sku": sku, "e_kit": sku in kits or "/" in sku,
            "unidades": 0, "faturamento": 0.0, "un_ml": 0, "un_shopee": 0,
            "fat_ml_tarifado": 0.0, "tarifa_ml": 0.0,
        })
        titulo = str(v["title"] or "").strip()
        if titulo and len(titulo) > len(p["titulo"]):
            p["titulo"] = titulo
        p["unidades"] += qty
        p["faturamento"] += valor
        p["un_ml" if mp == "ml" else "un_shopee"] += qty
        if mp == "ml":
            fee = tarifas.get((str(v["order_id"]), str(v["line_key"])))
            if fee is not None and valor > 0:
                p["fat_ml_tarifado"] += valor
                p["tarifa_ml"] += fee

    # Por produto: em quantos pedidos entrou e com quantas unidades.
    for c in cestas.values():
        for chave, qty in c["itens"].items():
            p = prod[chave]
            p.setdefault("qtd_pedidos", []).append(qty)

    def custo_de(p: dict[str, Any]) -> float | None:
        custo, _origem = _financeiro_custo_unitario(p["sku"] or p["produto"], produtos, reservas, kits, cache_custo)
        return custo

    for p in prod.values():
        qs = p.pop("qtd_pedidos", [])
        p["pedidos"] = len(qs)
        p["pedidos_multi_un"] = sum(1 for q in qs if q >= 2)
        p["dist_qtd"] = {"1": sum(1 for q in qs if q == 1), "2": sum(1 for q in qs if q == 2),
                         "3": sum(1 for q in qs if q == 3), "4+": sum(1 for q in qs if q >= 4)}
        multi = [q for q in qs if q >= 2]
        p["qtd_comum"] = max(sorted(set(multi)), key=multi.count) if multi else 0
        p["preco_unit"] = round(p["faturamento"] / p["unidades"], 2) if p["unidades"] else 0.0
        p["custo_unit"] = custo_de(p)
        descr = (produtos.get(p["produto"]) or {}).get("descricao")
        if descr and not p["titulo"]:
            p["titulo"] = descr
        p["margem_bruta"] = (round((p["preco_unit"] - p["custo_unit"]) / p["preco_unit"] * 100, 1)
                             if p["custo_unit"] is not None and p["preco_unit"] > 0 else None)
        p["tarifa_ml_pct"] = (round(p["tarifa_ml"] / p["fat_ml_tarifado"] * 100, 1)
                              if p["fat_ml_tarifado"] > 0 else None)
        p["faturamento"] = round(p["faturamento"], 2)

    # --- resumo e faixas de ticket ------------------------------------------
    n_cestas = sum(1 for c in cestas.values() if c["itens"])
    fat_total = sum(c["valor"] for c in cestas.values() if c["itens"])
    um_item = [c for c in cestas.values() if len(c["itens"]) == 1 and sum(c["itens"].values()) == 1]
    varios = [c for c in cestas.values() if c["itens"] and not (len(c["itens"]) == 1 and sum(c["itens"].values()) == 1)]
    multi_prod = [c for c in cestas.values() if len(c["itens"]) >= 2]
    multi_un = [c for c in cestas.values() if any(q >= 2 for q in c["itens"].values())]
    abaixo = [c for c in cestas.values() if c["itens"] and c["valor"] < limite_preco]
    faixas = []
    for lo, hi in KITS_ANALISE_FAIXAS_TICKET:
        dentro = [c for c in cestas.values() if c["itens"] and c["valor"] >= lo and (hi is None or c["valor"] < hi)]
        faixas.append({"de": lo, "ate": hi, "pedidos": len(dentro),
                       "faturamento": round(sum(c["valor"] for c in dentro), 2)})

    def media(lista: list[dict[str, Any]]) -> float:
        return round(sum(c["valor"] for c in lista) / len(lista), 2) if lista else 0.0

    pct = (lambda n: round(n / n_cestas * 100, 1) if n_cestas else 0.0)
    resumo = {
        "pedidos": n_cestas, "faturamento": round(fat_total, 2), "ticket_medio": media([c for c in cestas.values() if c["itens"]]),
        "unidades": sum(sum(c["itens"].values()) for c in cestas.values()),
        "produtos_por_pedido": round(sum(len(c["itens"]) for c in cestas.values()) / n_cestas, 2) if n_cestas else 0.0,
        "pedidos_multiproduto": len(multi_prod), "pct_multiproduto": pct(len(multi_prod)),
        "pedidos_multiunidade": len(multi_un), "pct_multiunidade": pct(len(multi_un)),
        "pedidos_um_item": len(um_item), "ticket_um_item": media(um_item),
        "pedidos_varios_itens": len(varios), "ticket_varios_itens": media(varios),
        "pedidos_abaixo_limite": len(abaixo), "pct_abaixo_limite": pct(len(abaixo)),
    }

    # --- comprados juntos ----------------------------------------------------
    pares: dict[tuple[str, str], dict[str, Any]] = {}
    cestas_grandes = 0
    for c in cestas.values():
        itens = sorted(c["itens"])
        if len(itens) < 2:
            continue
        if len(itens) > KITS_ANALISE_MAX_ITENS_CESTA:
            cestas_grandes += 1
            continue
        for i, a in enumerate(itens):
            for b in itens[i + 1:]:
                par = pares.setdefault((a, b), {"n": 0, "valor": 0.0, "ml": 0, "shopee": 0})
                par["n"] += 1
                par["valor"] += c["valor"]
                par[c["mp"]] += 1
    lista_pares = []
    for (a, b), par in pares.items():
        if par["n"] < min_pedidos:
            continue
        pa, pb = prod[a], prod[b]
        combo = frozenset({a, b})
        existente = combos_existentes.get(combo) or next(
            (nome for k, nome in combos_existentes.items() if combo <= k), "")
        custo = (pa["custo_unit"] + pb["custo_unit"]) if (pa["custo_unit"] is not None and pb["custo_unit"] is not None) else None
        preco = round(pa["preco_unit"] + pb["preco_unit"], 2)
        lista_pares.append({
            "a": a, "b": b, "titulo_a": pa["titulo"], "titulo_b": pb["titulo"],
            "kit_a": pa["e_kit"], "kit_b": pb["e_kit"],
            "pedidos_juntos": par["n"], "ml": par["ml"], "shopee": par["shopee"],
            "pedidos_a": pa["pedidos"], "pedidos_b": pb["pedidos"],
            # % de quem compra A que leva B junto (e vice-versa).
            "conf_a_b": round(par["n"] / pa["pedidos"] * 100, 1) if pa["pedidos"] else 0.0,
            "conf_b_a": round(par["n"] / pb["pedidos"] * 100, 1) if pb["pedidos"] else 0.0,
            # lift > 1: saem juntos mais do que o acaso explicaria.
            "lift": round(par["n"] * n_cestas / (pa["pedidos"] * pb["pedidos"]), 2) if pa["pedidos"] and pb["pedidos"] else 0.0,
            "ticket_juntos": round(par["valor"] / par["n"], 2),
            "preco_kit": preco, "custo_kit": round(custo, 2) if custo is not None else None,
            "margem_bruta": round((preco - custo) / preco * 100, 1) if (custo is not None and preco > 0) else None,
            "ja_existe": existente,
        })
    lista_pares.sort(key=lambda x: (bool(x["ja_existe"]), -x["pedidos_juntos"], -x["lift"]))

    # --- kit de quantidade (o cliente já leva 2, 3...) -----------------------
    quantidade = []
    for p in prod.values():
        if p["e_kit"] or p["pedidos_multi_un"] < min_pedidos:
            continue
        n = p["qtd_comum"] or 2
        quantidade.append({
            "produto": p["produto"], "titulo": p["titulo"], "pedidos": p["pedidos"],
            "pedidos_multi_un": p["pedidos_multi_un"],
            "pct_multi_un": round(p["pedidos_multi_un"] / p["pedidos"] * 100, 1) if p["pedidos"] else 0.0,
            "dist_qtd": p["dist_qtd"], "qtd_sugerida": n, "preco_unit": p["preco_unit"],
            "preco_kit": round(p["preco_unit"] * n, 2),
            "custo_unit": p["custo_unit"], "margem_bruta": p["margem_bruta"], "tarifa_ml_pct": p["tarifa_ml_pct"],
            "ja_existe": sorted(qtd_existentes.get(p["produto"], set())),
        })
    quantidade.sort(key=lambda x: (-x["pedidos_multi_un"], -x["pct_multi_un"]))

    # --- ticket baixo (abaixo do limite de custo fixo do ML) -----------------
    baixo = []
    for p in prod.values():
        if p["e_kit"] or p["preco_unit"] <= 0 or p["preco_unit"] >= limite_preco:
            continue
        n = max(2, -(-int(limite_preco * 100) // int(round(p["preco_unit"] * 100))))
        baixo.append({
            "produto": p["produto"], "titulo": p["titulo"], "pedidos": p["pedidos"], "unidades": p["unidades"],
            "un_ml": p["un_ml"], "un_shopee": p["un_shopee"],
            "preco_unit": p["preco_unit"], "tarifa_ml_pct": p["tarifa_ml_pct"],
            "custo_unit": p["custo_unit"], "margem_bruta": p["margem_bruta"],
            "pct_multi_un": round(p["pedidos_multi_un"] / p["pedidos"] * 100, 1) if p["pedidos"] else 0.0,
            "unidades_para_limite": n, "preco_kit": round(p["preco_unit"] * n, 2),
            "ja_existe": sorted(qtd_existentes.get(p["produto"], set())),
        })
    baixo.sort(key=lambda x: (-x["pedidos"], x["preco_unit"]))

    return {
        "ok": True,
        "periodo": {"inicio": d1.isoformat(), "fim": hoje.isoformat(), "dias": dias},
        "marketplace": canal or "todos",
        "parametros": {"min_pedidos": min_pedidos, "limite_preco": limite_preco,
                       "max_itens_cesta": KITS_ANALISE_MAX_ITENS_CESTA},
        "vendas_desde": primeiro or "",
        "resumo": resumo,
        "faixas_ticket": faixas,
        "pares": lista_pares[:300],
        "pares_total": len(lista_pares),
        "quantidade": quantidade[:300],
        "ticket_baixo": baixo[:300],
        "avisos": {"linhas_sem_sku": linhas_sem_sku, "cestas_grandes_ignoradas": cestas_grandes,
                   "kits_cadastrados": len(combos_existentes) + sum(len(x) for x in qtd_existentes.values())},
    }


# V18.33: a tela Rentabilidade ML foi substituída pelo Financeiro (/financeiro).
# ml_profitability_payload() e a rota /api/rentabilidade-ml foram removidas daqui.
# O que sobrou continua em uso e NÃO pode ser apagado junto:
#   _ml_profitability_recent_sales      -> sincronização de vendas (vendas_v1799)
#   _ml_billing_* / _ml_seller_shipping_cost -> fatura e frete do Financeiro
#   ml_profitability_snapshot (tabela)  -> histórico de custo por venda, ainda lido
#     pelo custo de reserva da Demanda de Compra (_compras_custos_reserva).
#     Ninguém mais escreve nela: tarifa e frete agora vêm de ml_venda_custo_v1830 e
#     ml_frete_envio_v1830, capturados no ato da venda (V18.31).


class AppHandler(BaseHTTPRequestHandler):
    server_version = "NoromakExpedicao/16.16"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        # V18.29: SAMEORIGIN (era DENY) para o sistema poder embutir as PRÓPRIAS
        # telas — a de Promoções abre o Precificador do produto dentro do painel.
        # Site externo continua impedido de embutir o Noromak (clickjacking).
        self.send_header("X-Frame-Options", "SAMEORIGIN")
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
            # V17.89: o painel da extensão "Cadastro de Cliente no Linx" é uma
            # página da própria extensão aberta em iframe sobre o admin. Sem
            # isto, default-src 'self' bloqueava o conteúdo e o painel ficava
            # em branco. V18.29: frame-ancestors passou a 'self' — o Noromak embute
            # as PRÓPRIAS telas (Promoções abre o Precificador do item); site externo continua barrado.
            "frame-src 'self' chrome-extension:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'",
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

    def auth_full_admin(self) -> dict[str, Any] | None:
        """Ações críticas: exige perfil ADMIN real; Supervisor não é aceito."""
        ctx = self.auth(admin_only=True)
        if ctx is None:
            return None
        if str(ctx.get("app_role") or "").lower() != "admin":
            self.send_json({"error": "Esta ação é exclusiva do Administrador."}, 403)
            return None
        return ctx

    def linx_robo_auth(self) -> bool:
        authorization = str(self.headers.get("Authorization") or "").strip()
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not supplied or not hmac.compare_digest(supplied, get_or_create_linx_robo_token()):
            self.send_json({"ok": False, "error": "Token do robô do Linx inválido."}, 401)
            return False
        return True

    def print_agent_auth(self) -> bool:
        authorization = str(self.headers.get("Authorization") or "").strip()
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        expected = get_or_create_print_agent_token()
        if not supplied or not hmac.compare_digest(supplied, expected):
            self.send_json({"ok": False, "error": "Token do agente Zebra inválido."}, 401)
            return False
        return True

    @staticmethod
    def agent_station_id(query: dict[str, list[str]], req: dict[str, Any] | None = None) -> str:
        req = req or {}
        return str(
            req.get("agent_id") or req.get("station_id")
            or (query.get("agent_id") or [""])[0]
            or (query.get("station_id") or [""])[0]
            or (query.get("computer") or [""])[0]
            or "noromak-print-agent"
        ).strip()

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

    def end_headers(self) -> None:
        if SECURITY_HEADERS_ENABLED:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header("Cache-Control", "no-store" if self.path.startswith(BASE_PATH + "/api/") else "no-cache")
        super().end_headers()

    def require_owner_reauth(self, ctx: dict[str, Any] | None) -> bool:
        if not require_owner_context(ctx):
            self.send_json({"error": "Somente o proprietário pode executar esta ação."}, 403)
            return False
        token = str((ctx or {}).get("session_key") or "")
        if CRITICAL_REAUTH.get(token, 0) < time.time():
            self.send_json({"error": "Reautenticação OWNER necessária (senha + 2FA).", "code": "OWNER_REAUTH_REQUIRED"}, 428)
            return False
        return True

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
                    # V18.63: o publicar.sh exige true em produção.
                    "workers": WORKERS_ENABLED,
                    "mercadolivre_worker": {
                        "server_side": True,
                        "started_at": ml_worker_status_payload().get("started_at", ""),
                        "last_order_poll_at": ml_worker_status_payload().get("last_order_poll_at", ""),
                        "last_catchup_at": ml_worker_status_payload().get("last_catchup_at", ""),
                    },
                }
            )
            return
        if path in ("/api/auth/session", "/api/me"):
            ctx = session_context(self)
            self.send_json({"authenticated": bool(ctx), "user": ctx} if ctx else {"authenticated": False})
            return
        if path == "/api/conferencia/conclusoes-dia":
            # V18.08: painel do dia lê os "conferidos" do servidor (compartilhado).
            ctx = self.auth()
            if ctx is None:
                return
            if not (module_allowed(ctx, "conferencia") or module_allowed(ctx, "admin")):
                self.send_json({"error": "Sem acesso à Conferência."}, 403)
                return
            self.send_json({"ok": True, "conclusoes": conferencia_conclusoes_do_dia()})
            return
        if path == "/api/precificador/promocoes":
            # V18.23: promoções do ML + a conta do Precificador, por anúncio.
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    brutos = (query.get("mlbs", [""])[0] or "").split(",")
                    mlbs = [x.strip() for x in brutos if x.strip()]
                    total = 0
                    offset = int(query.get("offset", ["0"])[0] or 0)
                    if not mlbs:
                        # V18.25: sem lista explícita, lista os anúncios ATIVOS do
                        # vendedor (não só os com vínculo — eram 7% da base).
                        forcar = (query.get("recarregar", [""])[0] or "") == "1"
                        busca = (query.get("q", [""])[0] or "").strip()[:80]
                        pagina = ml_itens_do_vendedor(offset, PROMO_PAINEL_MAX, forcar, busca)
                        mlbs = pagina["mlbs"]
                        total = pagina["total"]
                        if not mlbs and busca:
                            # V18.40: busca sem resultado não é erro
                            self.send_json({"ok": True, "itens": [], "erros": [], "skus": [],
                                            "total_anuncios": 0, "offset": 0, "busca": busca})
                            return
                    if not mlbs:
                        raise ValueError("Nenhum anúncio ativo encontrado no Mercado Livre.")
                    resposta = promocoes_painel(mlbs)
                    resposta["total_anuncios"] = total
                    resposta["offset"] = offset
                    self.send_json(resposta)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/integracoes/shopee/callback":
            code = str((query.get("code") or [""])[0] or "").strip()
            shop_id = str((query.get("shop_id") or [""])[0] or "").strip()
            if not code or not shop_id:
                shopee_log_event("oauth_callback", f"shop_id={shop_id}", "ERRO", "Callback sem code ou shop_id.")
                self.send_redirect(BASE_PATH + "/admin?aba=integracoes&shopee=erro&motivo=callback_incompleto")
                return
            try:
                shopee_log_event("oauth_callback", f"shop_id={shop_id}", "RECEBIDO", "Autorização Live recebida.")
                shopee_exchange_code(code, shop_id)
                self.send_redirect(BASE_PATH + "/admin?aba=integracoes&shopee=conectada")
            except Exception as exc:
                shopee_log_event("oauth_callback", f"shop_id={shop_id}", "ERRO", str(exc))
                self.send_redirect(BASE_PATH + "/admin?aba=integracoes&shopee=erro&motivo=token")
            return
        if path == "/api/integracoes/shopee/status":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.send_json({"ok": True, "shopee": shopee_status_payload()})
            return
        if path == "/api/integracoes/shopee/connect":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    self.send_redirect(shopee_connect_url())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 503)
            return
        if path == "/api/integracoes/shopee/logs":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                limit = max(1, min(200, int((query.get("limit") or ["50"])[0] or 50)))
                with DB_LOCK:
                    rows = DB.execute("SELECT * FROM shopee_event_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                self.send_json({"ok": True, "logs": [dict(row) for row in rows]})
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
        if path == "/api/promocoes-ml":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    max_items = max(1, min(1000, int((query.get("limit") or ["250"])[0] or 250)))
                    tax_percent = max(0.0, min(100.0, float((query.get("tax_percent") or ["12"])[0] or 12)))
                    simulated_discount = max(0.0, min(90.0, float((query.get("simulated_discount") or ["10"])[0] or 10)))
                    self.send_json(ml_promotions_analysis_payload(max_items=max_items, tax_percent=tax_percent, simulated_discount=simulated_discount))
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/precificador/atualizar-tudo/status":
            ctx = self.auth()
            if ctx is None:
                return
            if not module_allowed(ctx, "precificador"):
                self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
                return
            self.send_json({"ok": True, "job": precificador_bulk_status()})
            return
        if path == "/api/precificador/ml-items":
            ctx = self.auth()
            if ctx is None:
                return
            if not module_allowed(ctx, "precificador"):
                self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
                return
            raw_ids = str((query.get("ids") or [""])[0] or "")
            ids = [re.sub(r"\D+", "", x) for x in raw_ids.split(",")]
            ids = [x for x in ids if x][:100]
            if not ids:
                self.send_json({"ok": True, "items": {}})
                return
            try:
                self.send_json({"ok": True, "items": ml_items_meta(ids)})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 502)
            return

        if path == "/api/precificador/ml-vinculos":
            ctx = self.auth()
            if ctx is None:
                return
            if not module_allowed(ctx, "precificador"):
                self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
                return
            try:
                limit = max(1, min(10000, int((query.get("limit") or ["5000"])[0] or 5000)))
                self.send_json(ml_precificador_links_payload(limit))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 502)
            return

        if path == "/api/precificador/custo":
            ctx = self.auth()
            if ctx is None:
                return
            if not module_allowed(ctx, "precificador"):
                self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
                return
            codigo = str((query.get("codigo") or [""])[0] or "").strip()
            if not codigo:
                self.send_json({"ok": False, "error": "Informe o SKU/código da peça."}, 400)
                return
            operador = str(ctx.get("nome") or ctx.get("username") or "").strip()
            try:
                self.send_json(crm_cost_lookup(codigo, operador))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 502)
            return
        if path == "/api/storage":
            ctx = self.auth()
            if ctx is not None:
                self.handle_storage_get(query)
            return
        if path == "/api/roles":
            ctx = self.auth_full_admin()
            if ctx is not None:
                self.handle_roles_get()
            return
        if path == "/api/sku-history":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_sku_history_get(query)
            return
        # V17.83 — o que vendeu no período, agregado por SKU. Somente leitura.
        if path == "/api/compras/fornecedores":
            if self.auth(admin_only=True) is None:
                return
            self.send_json({"ok": True, "fornecedores": compras_fornecedores()})
            return
        if path == "/api/compras/demanda":
            # V18.00: demanda de compra por fornecedor (versão provisória).
            if self.auth(admin_only=True) is None:
                return
            try:
                fornecedor = str((query.get("fornecedor") or [""])[0] or "")
                dias = int(str((query.get("dias") or ["60"])[0] or "60"))
                self.send_json(compras_demanda_payload(fornecedor, dias))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/comprador":
            # V18.14: e-mail do comprador (um para tudo). V18.17: + limite de transferência.
            if self.auth(admin_only=True) is None:
                return
            self.send_json({"ok": True, "email": compras_comprador_email(),
                            "limite_transferencia": compras_limite_transferencia()})
            return
        if path == "/api/dashboard/vendas":
            # V17.99: Dashboard de vendas (Mercado Livre e Shopee, com valor).
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            hoje = datetime.now(BR_TZ).date()
            inicio = str((query.get("data_inicio") or [""])[0] or "").strip() or (hoje - timedelta(days=29)).isoformat()
            fim = str((query.get("data_fim") or [""])[0] or "").strip() or hoje.isoformat()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", inicio) or not re.match(r"^\d{4}-\d{2}-\d{2}$", fim):
                self.send_json({"ok": False, "error": "Datas no formato AAAA-MM-DD."}, 400)
                return
            canal = str((query.get("marketplace") or [""])[0] or "").strip().lower()
            try:
                with DB_LOCK:  # V18.56: calcula sob o lock, envia fora dele
                    resposta = vendas_dashboard_payload(inicio, fim, canal)
                self.send_json(resposta)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/financeiro/resumo":
            # V18.30: faturamento, gastos e lucro do período (ML + Shopee).
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            hoje = datetime.now(BR_TZ).date()
            inicio = str((query.get("data_inicio") or [""])[0] or "").strip() or hoje.replace(day=1).isoformat()
            fim = str((query.get("data_fim") or [""])[0] or "").strip() or hoje.isoformat()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", inicio) or not re.match(r"^\d{4}-\d{2}-\d{2}$", fim):
                self.send_json({"ok": False, "error": "Datas no formato AAAA-MM-DD."}, 400)
                return
            canal = str((query.get("marketplace") or [""])[0] or "").strip().lower()
            try:
                self.send_json(financeiro_payload(inicio, fim, canal))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/ads-analise":
            # V18.54: Análise de ADS (relatório do ML + lucro por anúncio). Somente leitura.
            if self.auth(admin_only=True) is None:
                return
            try:
                rid = str((query.get("relatorio") or [""])[0] or "").strip()
                self.send_json(ads_analise_payload(int(rid) if rid.isdigit() else None))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/kits-analise":
            # V18.47: Oportunidades de Kit (o que já sai junto nos pedidos). Somente leitura.
            if self.auth(admin_only=True) is None:
                return
            try:
                self.send_json(kits_analise_payload(
                    int(str((query.get("dias") or ["90"])[0] or "90")),
                    str((query.get("marketplace") or [""])[0] or "").strip().lower(),
                    int(str((query.get("min_pedidos") or ["2"])[0] or "2")),
                    float(str((query.get("limite_preco") or ["79"])[0] or "79").replace(",", ".")),
                ))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/financeiro/ads":
            # V18.30: estado da coleta de Publicidade (Product Ads).
            if self.auth(admin_only=True) is None:
                return
            try:
                self.send_json({"ok": True, **ml_ads_status()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/dashboard/produto-custos":
            # V18.10: custos reais de venda de um SKU, separados por marketplace.
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            sku = str((query.get("sku") or [""])[0] or "").strip()
            dias_txt = str((query.get("dias") or ["30"])[0] or "30").strip()
            dias = int(dias_txt) if dias_txt.isdigit() else 30
            try:
                self.send_json(dashboard_produto_custos(sku, dias))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/relatorio-diario/preview":
            # V18.13: pré-visualiza o relatório diário (HTML) sem enviar e-mail.
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            dia = str((query.get("dia") or [""])[0] or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", dia):
                dia = (datetime.now(BR_TZ).date() - timedelta(days=1)).isoformat()
            try:
                body = relatorio_diario_html(relatorio_diario_dados(dia)).encode("utf-8")
                self.send_response(200)
                self.send_security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/vendas":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            inicio = str((query.get("data_inicio") or [""])[0] or "").strip()
            fim = str((query.get("data_fim") or [""])[0] or "").strip()
            for rotulo, valor in (("data_inicio", inicio), ("data_fim", fim)):
                if valor and not re.match(r"^\d{4}-\d{2}-\d{2}$", valor):
                    self.send_json({"ok": False, "error": f"{rotulo} deve estar no formato AAAA-MM-DD."}, 400)
                    return
            if inicio and fim and inicio > fim:
                self.send_json({"ok": False, "error": "A data inicial não pode ser maior que a final."}, 400)
                return
            try:
                with DB_LOCK:  # V18.56: calcula sob o lock, envia fora dele
                    resposta = vendas_payload(inicio, fim)
                self.send_json(resposta)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V18.60 — Linx Smart API: status da conexão e fila de quem não tem cadastro.
        # V18.60 — romaneio: o que seria impresso (só leitura no Linx).
        if path == "/api/linx/romaneio":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                contatos = [c for c in str((query.get("contatos") or [""])[0] or "").split(",") if c.strip()]
                resultado = linx_romaneio_preparar(str((query.get("pedido") or [""])[0] or ""), contatos or None)
                self.send_json(resultado, 200 if resultado.get("ok") else 404)
            except (LinxErro, ValueError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 502)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path in ("/api/linx/status", "/api/linx/clientes/sem-cadastro"):
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                self.send_json(linx_status_payload() if path == "/api/linx/status" else linx_clientes_sem_cadastro())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V17.93 — o que a extensão ainda precisa ler (segundo plano).
        if path == "/api/cliente-fiscal/pendentes":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                self.send_json(pedidos_sem_nome_fiscal())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V17.86 — tudo o que a extensão já leu, para a Central mostrar.
        if path == "/api/cliente-fiscal/lista":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                self.send_json(lista_cliente_fiscal())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V17.85 — CPF/CNPJ e nome fiscal para o botão "Cliente". Somente leitura.
        if path == "/api/cliente-fiscal":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            pedido = str((query.get("pedido") or [""])[0] or "").strip()
            if not pedido:
                self.send_json({"ok": False, "error": "Informe o pedido."}, 400)
                return
            try:
                resultado = cliente_fiscal_do_pedido(pedido)
                self.send_json(resultado, 200 if resultado.get("ok") else 404)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V18.60 — robô do Apollo desktop: token próprio, só a fila.
        if path == "/api/linx/robo/proximo":
            if self.linx_robo_auth():
                try:
                    self.send_json(linx_robo_proximo())
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/linx/robo/config":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.send_json({"ok": True, "token": get_or_create_linx_robo_token()})
            return
        if path == "/api/print/agent/ping":
            if self.print_agent_auth():
                self.send_json({"ok": True, "version": APP_VERSION, "mode": "autonomous", "server_time": now_iso()})
            return
        if path == "/api/print/agent/next":
            if self.print_agent_auth():
                self.handle_print_agent_next(query)
            return
        if path == "/api/print/agent/config":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.send_json({"ok": True, "token": get_or_create_print_agent_token(), "version": APP_VERSION})
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

        if path == "/api/security/status":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.send_json({
                    "ok": True, "version": APP_VERSION,
                    "owner": {"username": OWNER_USERNAME, "current_user_is_owner": require_owner_context(ctx)},
                    "security": {
                        "owner_lock": True,
                        "user_management_owner_only": True,
                        "integration_disconnect_owner_only": True,
                        "print_token_rotation_owner_only": True,
                        "password_pbkdf2_iterations": PASSWORD_ITERATIONS,
                        "session_ttl_seconds": SESSION_TTL_SECONDS,
                        "secrets_server_side": True,
                        "owner_2fa_configured": bool(OWNER_TOTP_SECRET),
                        "owner_recovery_codes_configured": bool(OWNER_RECOVERY_HASHES),
                        "owner_recovery_code_count": len(OWNER_RECOVERY_HASHES),
                        "critical_reauth_ttl_seconds": CRITICAL_REAUTH_TTL_SECONDS,
                        "critical_reauth_active": CRITICAL_REAUTH.get(str(ctx.get("session_key") or ""), 0) >= time.time(),
                        "critical_reauth_remaining_seconds": max(0, int(CRITICAL_REAUTH.get(str(ctx.get("session_key") or ""), 0) - time.time())),
                    }
                })
            return

        # Páginas dos módulos: além do login externo, exigem sessão e permissão granular.
        page_modules = {
            "/conferencia": "conferencia", "/conferencia/": "conferencia", "/conferencia.html": "conferencia",
            "/pedidos": "conferencia", "/pedidos/": "conferencia",
            "/admin": "admin", "/admin/": "admin", "/admin.html": "admin",
            "/administracao": "admin", "/administracao/": "admin",
            # V17.99: Central de Pendências e Promoções ML foram removidas; os endereços antigos abrem o Admin.
            "/pendencias": "admin", "/pendencias/": "admin", "/central-pendencias": "admin", "/central-pendencias/": "admin", "/central-pendencias.html": "admin",
            "/promocoes-ml": "admin", "/promocoes-ml/": "admin", "/promocoes-ml.html": "admin",
            # V18.00: Demanda de Compra.
            "/compras": "admin", "/compras/": "admin", "/compras.html": "admin",
            # V18.30: Financeiro (faturamento, gastos e lucro).
            "/financeiro": "admin", "/financeiro/": "admin", "/financeiro.html": "admin",
            # V18.47: Oportunidades de Kit.
            "/kits-analise": "admin", "/kits-analise/": "admin", "/kits-analise.html": "admin",
            # V18.54: Análise de ADS.
            "/analise-ads": "admin", "/analise-ads/": "admin", "/analise-ads.html": "admin",
            # V18.33: a Rentabilidade ML virou o Financeiro; o endereço antigo abre a tela nova.
            "/rentabilidade-ml": "admin", "/rentabilidade-ml/": "admin", "/rentabilidade-ml.html": "admin",
            "/estacao-impressao": "estacao", "/estacao-impressao/": "estacao", "/estacao-impressao.html": "estacao",
            "/print-station": "estacao", "/print-station/": "estacao",
            "/precificador": "precificador", "/precificador/": "precificador", "/precificador.html": "precificador",
            # V18.23: Promoções do ML (usa custo/margem do Precificador).
            "/promocoes": "precificador", "/promocoes/": "precificador", "/promocoes.html": "precificador",
            "/precificacao": "precificador", "/precificacao/": "precificador",
            "/analise-full": "precificador", "/analise-full/": "precificador", "/analise-full.html": "precificador",
            "/full": "precificador", "/full/": "precificador",
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
        # V17.86 — a extensão leu CPF/nome na venda (ou gravou no Linx): guarda.
        # Tabela própria (cliente_fiscal); não toca em conf_pedidos.
        if path == "/api/cliente-fiscal":
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                body = self.read_json()
                resultado = salvar_cliente_fiscal(
                    str(body.get("pedido") or ""), str(body.get("documento") or ""),
                    str(body.get("nome") or ""), linx_cliente=str(body.get("linx_cliente") or ""),
                    fonte=str(body.get("fonte") or "extensao"), usuario=str(ctx.get("username") or ""),
                )
                self.send_json(resultado, 200 if resultado.get("ok") else 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V18.60 — Linx Smart API. Só leitura no Linx: testar pede token (e consulta
        # um CPF, se vier); sincronizar consulta agora os clientes pendentes.
        # V18.60 — robô do Apollo desktop devolve o resultado (token próprio).
        if path == "/api/linx/robo/resultado":
            if self.linx_robo_auth():
                try:
                    body = self.read_json()
                    resultado = linx_robo_resultado(body.get("id"), body.get("ok") is True,
                                                    str(body.get("mensagem") or ""), str(body.get("cliente") or ""))
                    self.send_json(resultado, 200 if resultado.get("ok") else 409)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V18.60 — fila do robô, token do robô e o que a extensão fez (condição, caixa).
        if path in ("/api/linx/robo/fila", "/api/linx/robo/renovar", "/api/linx/operacao"):
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                body = self.read_json()
                usuario = str(ctx.get("username") or "")
                if path == "/api/linx/robo/fila":
                    resultado = linx_robo_enfileirar(str(body.get("pedido") or ""), usuario)
                elif path == "/api/linx/robo/renovar":
                    # Mesma regra do token do agente Zebra: só o proprietário, reautenticado.
                    if not self.require_owner_reauth(ctx):
                        audit(usuario, "security.denied.owner_reauth", "linx.robo.rotate_token")
                        self.send_json({"ok": False, "error": "Somente o proprietário pode renovar esta credencial."}, 403)
                        return
                    resultado = {"ok": True, "token": rotate_linx_robo_token()}
                    audit(usuario, "linx.robo.token_renovado", "")
                else:
                    resultado = linx_registrar_operacao_extensao(
                        str(body.get("pedido") or ""), str(body.get("etapa") or ""), str(body.get("situacao") or ""),
                        body.get("detalhe"), body.get("contato") or "", usuario)
                self.send_json(resultado, 200 if resultado.get("ok") else 409)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        # V18.60 — GRAVAM no Linx: só com LINX_ESCRITA_ATIVA=1 e clique de um admin.
        if path in ("/api/linx/romaneio", "/api/linx/cadastro"):
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                body = self.read_json()
                usuario = str(ctx.get("username") or "")
                if path == "/api/linx/romaneio":
                    resultado = linx_romaneio_confirmar(
                        str(body.get("pedido") or ""), body.get("contato"), str(body.get("observacao") or ""),
                        condicao_conferida=body.get("condicao_conferida") is True,
                        credito_conferido=body.get("credito_conferido") is True, usuario=usuario,
                    )
                else:
                    resultado = linx_cadastro_atualizar(str(body.get("pedido") or ""), usuario=usuario)
                self.send_json(resultado, 200 if resultado.get("ok") else 409)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path in ("/api/linx/testar", "/api/linx/clientes/sincronizar"):
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                body = self.read_json()
                if path == "/api/linx/testar":
                    resultado = linx_testar_conexao(str(body.get("documento") or ""))
                else:
                    resultado = linx_clientes_sincronizar()
                audit(str(ctx.get("username") or ""), "linx." + path.rsplit("/", 1)[-1],
                      "ok" if resultado.get("ok") else str(resultado.get("error") or "")[:200])
                self.send_json(resultado, 200 if resultado.get("ok") else 502)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/custo":
            # V18.07: custo digitado na Demanda de Compra (reserva quando o Apollo está sem custo).
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    body = self.read_json()
                    salvo = custo_manual_salvar(str(body.get("codigo") or ""), body.get("custo"), ctx["username"])
                    audit(ctx["username"], "compras.custo.manual", f"{salvo['codigo']}={salvo['custo_manual']}")
                    self.send_json({"ok": True, **salvo})
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/comprador":
            # V18.14: e-mail do comprador. V18.17: + limite de transferência (global).
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    body = self.read_json() or {}
                    if "email" in body:
                        email = str(body.get("email") or "").strip()
                        if email and "@" not in email:
                            raise ValueError("E-mail inválido.")
                        compras_comprador_email(email)
                        audit(ctx["username"], "compras.comprador", email)
                    if "limite_transferencia" in body:
                        lim = compras_limite_transferencia(body.get("limite_transferencia"))
                        audit(ctx["username"], "compras.limite_transferencia", str(lim))
                    self.send_json({"ok": True, "email": compras_comprador_email(),
                                    "limite_transferencia": compras_limite_transferencia()})
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/precificador/promocoes/aplicar":
            # V18.23: sobe o desconto no ML. A trava do preço mínimo é no servidor.
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    body = self.read_json() or {}
                    if body.get("remover"):
                        res = ml_promocao_remover(str(body.get("mlb") or ""), ctx["username"])
                        audit(ctx["username"], "promocao.remover", str(body.get("mlb") or ""))
                    else:
                        res = ml_promocao_aplicar(
                            str(body.get("mlb") or ""), body.get("preco"),
                            str(body.get("inicio") or ""), str(body.get("fim") or ""),
                            ctx["username"])
                        audit(ctx["username"], "promocao.aplicar",
                              f"{body.get('mlb')}|{body.get('preco')}|{body.get('inicio')}..{body.get('fim')}")
                    self.send_json(res)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/enviar-comprador":
            # V18.14: envia o pedido de compra montado na tela para o comprador.
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    pedido = self.read_json() or {}
                    if not (pedido.get("itens") or []):
                        raise ValueError("Nenhum item no pedido para enviar.")
                    res = compras_enviar_comprador(pedido)
                    audit(ctx["username"], "compras.enviar_comprador", f"{pedido.get('fornecedor')}|{pedido.get('total')}")
                    self.send_json(res)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/planilha":
            # V18.18: baixa a planilha DESENHADA do pedido (mesma do e-mail ao comprador).
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    pedido = self.read_json() or {}
                    conteudo = compras_pedido_xlsx(pedido)
                    forn = re.sub(r"[^A-Za-z0-9_-]+", "_", str(pedido.get("fornecedor") or "")).strip("_") or "fornecedor"
                    body, compressed = self.encoded_body(
                        conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_response(200)
                    self.send_security_headers()
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_header("Content-Disposition", f'attachment; filename="pedido_compra_{forn}.xlsx"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    if compressed:
                        self.send_header("Content-Encoding", "gzip")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/compras/fornecedores":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    body = self.read_json()
                    if body.get("excluir"):
                        compras_excluir_fornecedor(str(body.get("nome") or ""))
                        audit(ctx["username"], "compras.fornecedor.excluir", str(body.get("nome") or "")[:80])
                        self.send_json({"ok": True, "fornecedores": compras_fornecedores()})
                        return
                    salvo = compras_salvar_fornecedor(str(body.get("nome") or ""), body, ctx["username"])
                    audit(ctx["username"], "compras.fornecedor.salvar", salvo["nome"])
                    self.send_json({"ok": True, "fornecedor": salvo, "fornecedores": compras_fornecedores()})
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/dashboard/vendas/sincronizar":
            # V17.99: sincronização manual das vendas (roda em segundo plano).
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if VENDAS_SYNC_LOCK.locked():
                    self.send_json({"ok": True, "em_andamento": True})
                    return
                threading.Thread(target=vendas_sync_once, name="vendas-sync-manual", daemon=True).start()
                audit(ctx["username"], "dashboard.vendas.sincronizar", "")
                self.send_json({"ok": True, "iniciada": True})
            return
        if path == "/api/ads-analise/importar":
            # V18.54: importa o relatório de Anúncios patrocinados (.xlsx) do ML.
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                corpo = self.read_json()
                conteudo = base64.b64decode(str(corpo.get("conteudo_base64") or ""), validate=True)
                arquivo = str(corpo.get("arquivo") or "")
                r = ads_relatorio_importar(conteudo, arquivo, ctx["username"])
                audit(ctx["username"], "ads.importar",
                      f"{r['periodo_inicio']}..{r['periodo_fim']} · {r['linhas']} linhas · {arquivo[:80]}")
                self.send_json(r)
            except ValueError as exc:  # binascii.Error (base64 inválido) é um ValueError
                self.send_json({"ok": False, "error": str(exc) or "Arquivo inválido."}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/financeiro/sincronizar":
            # V18.30: busca fatura do ML, escrow da Shopee e Ads do período.
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            if FINANCEIRO_SYNC_LOCK.locked():
                self.send_json({"ok": True, "em_andamento": True})
                return
            req = self.read_json() or {}
            hoje = datetime.now(BR_TZ).date()
            inicio = str(req.get("data_inicio") or "").strip() or hoje.replace(day=1).isoformat()
            fim = str(req.get("data_fim") or "").strip() or hoje.isoformat()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", inicio) or not re.match(r"^\d{4}-\d{2}-\d{2}$", fim):
                self.send_json({"ok": False, "error": "Datas no formato AAAA-MM-DD."}, 400)
                return
            threading.Thread(target=financeiro_sincronizar, args=(inicio, fim),
                             name="financeiro-sync", daemon=True).start()
            audit(ctx["username"], "financeiro.sincronizar", f"{inicio}..{fim}")
            self.send_json({"ok": True, "iniciada": True})
            return
        if path == "/api/relatorio-diario/enviar":
            # V18.13: envia o relatório diário agora (teste manual do ADMIN).
            ctx = self.auth(admin_only=True)
            if ctx is None:
                return
            try:
                req = self.read_json()
                dia = str((req or {}).get("dia") or "").strip()
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", dia):
                    dia = (datetime.now(BR_TZ).date() - timedelta(days=1)).isoformat()
                res = relatorio_diario_enviar(dia, forcar=True)
                audit(ctx["username"], "relatorio.diario.enviar", dia)
                self.send_json(res)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        if path == "/api/full/kits":
            # V17.96: composição dos kits para a Demanda Full (somente leitura).
            # Um anúncio "kit2-X" vende 2 unidades de X por venda.
            ctx = self.auth()
            if ctx is not None:
                if not module_allowed(ctx, "precificador") and ctx.get("app_role") != "admin":
                    self.send_json({"error": "Acesso ao módulo de análise Full não autorizado."}, 403)
                    return
                try:
                    body = self.read_json()
                    skus = body.get("skus") if isinstance(body.get("skus"), list) else []
                    if len(skus) > 5000:
                        raise ValueError("Limite de 5000 SKUs por consulta.")
                    self.send_json({"ok": True, "kits": full_kit_compositions(skus)})
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
            return
        if path == "/api/full/anuncios":
            # V18.61: MLBs do relatório Full com as vendas de 30 dias (somente leitura).
            ctx = self.auth()
            if ctx is not None:
                if not module_allowed(ctx, "precificador") and ctx.get("app_role") != "admin":
                    self.send_json({"error": "Acesso ao módulo de análise Full não autorizado."}, 403)
                    return
                try:
                    body = self.read_json()
                    anuncios = body.get("anuncios") if isinstance(body.get("anuncios"), list) else []
                    if len(anuncios) > 10000:
                        raise ValueError("Limite de 10000 anúncios por consulta.")
                    self.send_json({"ok": True, "dias": 30, "anuncios": full_anuncios_vendas(anuncios)})
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
            return
        if path == "/api/full/apollo-stock":
            ctx = self.auth()
            if ctx is not None:
                if not module_allowed(ctx, "precificador") and ctx.get("app_role") != "admin":
                    self.send_json({"error": "Acesso ao módulo de análise Full não autorizado."}, 403)
                    return
                try:
                    body = self.read_json()
                    items = body.get("items") if isinstance(body.get("items"), list) else []
                    if len(items) > 500:
                        raise ValueError("Limite de 500 SKUs por consulta.")
                    filial = int(body.get("filial") or 0)
                    # V17.94: o Apollo aceita 20 chamadas/min. Com `nao_esperar`
                    # o que não coube no limite volta `pending` + `retry_after`
                    # e o navegador reenvia depois; sem ele (chamadores antigos)
                    # a requisição espera vaga por até 40 s no total.
                    nao_esperar = bool(body.get("nao_esperar"))
                    prazo = time.monotonic() + (0.0 if nao_esperar else 40.0)
                    results = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        sku = str(item.get("sku") or "").strip()
                        if not sku:
                            continue
                        try:
                            need = max(0.0, float(item.get("need") or 0))
                        except Exception:
                            need = 0.0
                        try:
                            stock = crm_stock_lookup(sku, "analise-full", max(0.0, prazo - time.monotonic()))
                            rows = stock.get("estoques") if isinstance(stock.get("estoques"), list) else []
                            missing = [int(x) for x in stock.get("casas_sem_resposta") or []]
                            partial = bool(stock.get("parcial"))
                            if filial:
                                selected = [r for r in rows if int(r.get("revenda") or 0) == filial]
                                # Casa sem resposta NÃO é zero: fica desconhecido.
                                known = bool(selected) and filial not in missing and all(r.get("disponivel") is not None for r in selected)
                                available = sum(float(r.get("disponivel") or 0) for r in selected) if known else None
                                partial = False
                            else:
                                available = stock.get("estoque_total")
                                known = bool(stock.get("estoque_conhecido"))
                            shortage = max(0.0, need - float(available or 0)) if known else None
                            results.append({
                                "sku": sku,
                                "ok": True,
                                "stock_known": known,
                                "available": available,
                                "need": need,
                                "shortage": shortage,
                                "can_send": min(need, float(available or 0)) if known else None,
                                "sufficient": bool(known and float(available or 0) >= need),
                                "partial": partial,
                                "missing_branches": missing,
                                "description": stock.get("descricao") or "",
                                "branches": rows,
                            })
                        except CrmLimiteAtingido as exc:
                            results.append({
                                "sku": sku, "ok": False, "pending": True, "retry_after": exc.retry_after,
                                "stock_known": False, "available": None, "need": need, "shortage": None,
                                "sufficient": False, "branches": [], "error": str(exc),
                            })
                        except Exception as exc:
                            results.append({
                                "sku": sku, "ok": False, "stock_known": False,
                                "available": None, "need": need, "shortage": None,
                                "sufficient": False, "branches": [], "error": str(exc)[:300],
                            })
                    audit(ctx["username"], "full.apollo.stock", f"{len(results)} SKU(s)")
                    self.send_json({"ok": True, "results": results, "filial": filial, "version": APP_VERSION})
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
            return

        if path == "/api/security/owner/reauth":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if not require_owner_context(ctx):
                    self.send_json({"error": "Somente o proprietário."}, 403); return
                if owner_reauth_limited(ctx):
                    audit(ctx["username"], "security.owner.reauth_blocked", "rate_limit")
                    self.send_json({"error": "Muitas tentativas. Aguarde 5 minutos para tentar novamente."}, 429); return
                body = self.read_json()
                if not verify_user_password(ctx["username"], str(body.get("password") or "")):
                    owner_reauth_failure(ctx)
                    audit(ctx["username"], "security.owner.reauth_failed", "password")
                    self.send_json({"error": "Senha inválida."}, 401); return
                if not OWNER_TOTP_SECRET:
                    self.send_json({"error": "2FA OWNER não configurado.", "code": "OWNER_2FA_NOT_CONFIGURED"}, 409); return
                ok2, method = verify_owner_second_factor(str(body.get("code") or ""))
                if not ok2:
                    owner_reauth_failure(ctx)
                    audit(ctx["username"], "security.owner.reauth_failed", "2fa")
                    self.send_json({"error": "Código 2FA inválido."}, 401); return
                CRITICAL_REAUTH[str(ctx.get("session_key") or "")] = time.time() + CRITICAL_REAUTH_TTL_SECONDS
                owner_reauth_clear(ctx)
                audit(ctx["username"], "security.owner.reauth_ok", method)
                self.send_json({"ok": True, "valid_for_seconds": CRITICAL_REAUTH_TTL_SECONDS, "method": method})
            return
        if path == "/api/security/owner/verify-2fa":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if not require_owner_context(ctx):
                    self.send_json({"error": "Somente o proprietário."}, 403); return
                body = self.read_json()
                ok2, method = verify_owner_second_factor(str(body.get("code") or ""))
                self.send_json({"ok": ok2, "method": method if ok2 else ""}, 200 if ok2 else 401)
            return
        if path == "/api/integracoes/shopee/sync":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    self.send_json(shopee_manual_sync())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 503)
            return
        if path == "/api/integracoes/shopee/disconnect":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if not self.require_owner_reauth(ctx):
                    audit(ctx["username"], "security.denied.owner_reauth", "shopee.disconnect")
                    self.send_json({"error": "Somente o proprietário pode desconectar integrações."}, 403)
                    return
                with DB_LOCK:
                    DB.execute("DELETE FROM shopee_integration WHERE id=1")
                    DB.commit()
                shopee_log_event("disconnect", "", "OK", f"Desconectado por {ctx.get('username') or 'admin'}")
                audit(ctx["username"], "shopee.disconnect", "Shopee")
                self.send_json({"ok": True})
            return
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
        if path == "/api/integracoes/mercadolivre/verificar-etiquetas":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    self.send_json(ml_check_all_available_labels_once())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 502)
            return
        if path == "/api/integracoes/mercadolivre/atualizar-pedido":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    payload = self.read_json()
                    order_id = str(payload.get("order_id") or "").strip()
                    if not order_id:
                        self.send_json({"error": "Venda Mercado Livre não identificada neste pedido."}, 400)
                        return
                    detail = ml_import_order(order_id, source=f"API Mercado Livre / refresh manual por {ctx.get('username') or 'admin'}")
                    audit(ctx["username"], "ml.order.refresh", order_id)
                    self.send_json({"ok": True, "detail": detail})
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 502)
            return
        if path == "/api/integracoes/mercadolivre/atualizar-nomes-nfe":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                try:
                    result = ml_update_pending_names_from_uploaded_nfe()
                    audit(ctx["username"], "ml.pending_names.from_nfe", json_dumps({k: v for k, v in result.items() if k != "details"})[:4000])
                    self.send_json(result)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
            return
        if path == "/api/integracoes/mercadolivre/disconnect":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if not self.require_owner_reauth(ctx):
                    audit(ctx["username"], "security.denied.owner_reauth", "mercadolivre.disconnect")
                    self.send_json({"error": "Somente o proprietário pode desconectar integrações."}, 403)
                    return
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
        if path == "/api/precificador/atualizar-tudo":
            ctx = self.auth()
            if ctx is None:
                return
            if not module_allowed(ctx, "precificador"):
                self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
                return
            current = precificador_bulk_status()
            if current.get("running"):
                self.send_json({"ok": True, "started": False, "job": current})
                return

            _precificador_bulk_set_status(
                running=True, total=0, processed=0, updated=0, cost_changes=0,
                descriptions=0, curves=0, kits=0, errors=0, current_sku="",
                started_at=now_iso(), finished_at="", message="Iniciando atualização geral...",
            )
            thread = threading.Thread(
                target=precificador_bulk_update_worker,
                args=(str(ctx.get("username") or ""),),
                name="precificador-apollo-bulk",
                daemon=True,
            )
            thread.start()
            self.send_json({"ok": True, "started": True, "job": precificador_bulk_status()})
            return
        if path == "/api/precificador/ml-vinculos/sync-promos":
            ctx = self.auth()
            if ctx is not None:
                self.handle_precificador_ml_vinculos_sync_promos(ctx)
            return
        if path == "/api/precificador/ml-vinculos/manual":
            ctx = self.auth()
            if ctx is not None:
                self.handle_precificador_ml_vinculo_manual(ctx)
            return
        if path == "/api/storage":
            ctx = self.auth()
            if ctx is not None:
                self.handle_storage_post(ctx)
            return
        if path == "/api/admin-order-upsert":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_admin_order_upsert(ctx)
            return
        if path == "/api/order-kit-delete":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_order_kit_delete(ctx)
            return
        if path == "/api/order-line-delete":
            ctx = self.auth()
            if ctx is not None:
                self.handle_order_line_delete(ctx)
            return
        if path == "/api/order-conference-name":
            # V17.76: ação operacional estreita. Qualquer usuário Noromak
            # autenticado pode preencher somente o Nome de Conferência.
            ctx = self.auth()
            if ctx is not None:
                self.handle_order_conference_name(ctx)
            return
        if path == "/api/order-sku":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_order_sku(ctx)
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
            ctx = self.auth_full_admin()
            if ctx is not None:
                self.handle_roles_post(ctx)
            return
        if path == "/api/print/agent/result":
            if self.print_agent_auth():
                self.handle_print_agent_result(parse_qs(urlparse(self.path).query))
            return
        if path == "/api/print/agent/rotate-token":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                if not self.require_owner_reauth(ctx):
                    audit(ctx["username"], "security.denied.owner_reauth", "print.agent.rotate_token")
                    self.send_json({"error": "Somente o proprietário pode renovar esta credencial."}, 403)
                    return
                try:
                    token = rotate_print_agent_token()
                    audit(ctx["username"], "print.agent.rotate_token", "Token renovado")
                    self.send_json({"ok": True, "token": token})
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
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
            threading.Thread(
                target=ml_catchup_after_reconnect,
                args=(str(status.get("recovery_since") or ""),),
                name="ml-catchup-reconnect",
                daemon=True,
            ).start()
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
            # V18.66 (decisão do dono, 05/10): apagar uma base inteira é só do Admin e
            # com a confirmação do proprietário (senha + 2FA). Antes o Supervisor
            # apagava produtos, kits ou etiquetas de uma vez.
            key = (query.get("key") or [""])[0].strip()
            quem = session_context(self)
            if quem is not None and not require_owner_context(quem):
                audit(quem["username"], "security.denied.storage_delete", key)
            ctx = self.auth_full_admin()
            if ctx is None or not self.require_owner_reauth(ctx):
                return
            if not key:
                self.send_json({"error": "Chave não informada."}, 400)
                return
            delete_storage(key)
            audit(ctx["username"], "storage.delete", key)
            self.send_json({"ok": True})
            return
        if path == "/api/roles":
            ctx = self.auth_full_admin()
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
            login_ttl = session_ttl_for_user_area(area, row)
            with DB_LOCK:
                if old:
                    DB.execute("DELETE FROM app_sessions WHERE token_hash=?", (hashlib.sha256(old.encode("utf-8")).hexdigest(),))
                DB.execute(
                    """INSERT INTO app_sessions(token_hash,username,area,created_at,last_seen,expires_at,ip,user_agent)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (token_hash, username, area, now_iso(), now_iso(), now_epoch + login_ttl,
                     client_computer(self), self.headers.get("User-Agent", "")[:500]),
                )
                DB.commit()
            user = {
                "username": username, "nome": row["display_name"] or username,
                "app_role": row["role"], "app_active": True, "is_owner": is_owner_username(username),
                "permissions": permissions
            }
            audit(username, "auth.login", f"{area}:{client_computer(self)}")
            self.send_json(
                {"ok": True, "user": user},
                headers={"Set-Cookie": self.session_cookie_header(token, max_age=login_ttl)},
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
        # V18.56: monta a resposta sob o DB_LOCK e ENVIA fora dele. Antes a escrita no
        # socket (conf_pedidos inteiro, a cada polling de cada aba) segurava o lock
        # global: um cliente lento na rede parava a bipagem de todo mundo.
        with DB_LOCK:
            resposta, status = self._storage_get_resposta(query)
        self.send_json(resposta, status)

    def _storage_get_resposta(self, query: dict[str, list[str]]) -> tuple[dict[str, Any], int]:
        with DB_LOCK:
            if (query.get("list") or [""])[0] == "1":
                keys = [row[0] for row in DB.execute("SELECT key FROM storage ORDER BY key")]
                return {"keys": keys}, 200
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
                return {"versions": {key: rows.get(key, 0) for key in requested}}, 200
            keys_arg = (query.get("keys") or [""])[0]
            if keys_arg:
                values: dict[str, Any] = {}
                versions: dict[str, int] = {}
                for key in keys_arg.split(","):
                    value, version, _ = get_storage(key)
                    values[key] = value
                    versions[key] = version
                return {"values": values, "versions": versions}, 200
            key = (query.get("key") or [""])[0]
            if not key:
                return {"error": "Chave não informada."}, 400
            value, version, updated = get_storage(key)
            return {"exists": value is not None, "value": value, "version": version, "updated_at": updated}, 200

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

    def handle_precificador_ml_vinculos_sync_promos(self, ctx: dict[str, Any]) -> None:
        if not module_allowed(ctx, "precificador"):
            self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
            return
        try:
            req = self.read_json()
            mlbs = req.get("mlbs") or req.get("ids") or []
            if isinstance(mlbs, str):
                mlbs = [x.strip() for x in mlbs.split(",") if x.strip()]
            if not isinstance(mlbs, list):
                raise ValueError("Lista de MLBs inválida.")
            result = ml_precificador_sync_promo_links(
                mlbs, actor=str(ctx.get("username") or "usuario")
            )
            audit(
                str(ctx.get("username") or "usuario"),
                "precificador.mlb_sku.sync_promos",
                f"processados={result.get('processed',0)} vinculados={result.get('linked',0)} pendentes={result.get('unresolved',0)}",
            )
            self.send_json(result, 200 if result.get("ok") else 502)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def handle_precificador_ml_vinculo_manual(self, ctx: dict[str, Any]) -> None:
        if not module_allowed(ctx, "precificador"):
            self.send_json({"ok": False, "error": "Acesso ao Precificador não autorizado."}, 403)
            return
        try:
            req = self.read_json()
            sku = str(req.get("sku") or "").strip()
            mlb = str(req.get("mlb") or "").strip()
            action = str(req.get("action") or "").strip().lower()
            rows = ml_precificador_manual_link_update(sku, mlb, action, str(ctx.get("username") or ""))
            audit(str(ctx.get("username") or "usuario"), f"precificador.mlb_link.{action}", f"{sku}|{mlb}")
            self.send_json({"ok": True, "sku": _ml_normalize_link_sku(sku), "rows": rows})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def handle_storage_post(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            key = str(req.get("key") or "").strip()
            if not key or "newValue" not in req:
                raise ValueError("Chave ou valor inválido.")
            if key == "precificador_online_v1" and not module_allowed(ctx, "precificador"):
                self.send_json({"error": "Este usuário não possui acesso para alterar o Precificador."}, 403)
                return
            if key == "ml_promocoes_datas_v1" and not module_allowed(ctx, "precificador"):
                self.send_json({"error": "Este usuário não possui acesso para alterar as promoções do Precificador."}, 403)
                return
            if ctx["app_role"] != "admin" and not module_allowed(ctx, "admin") and key not in OPERATOR_WRITE_KEYS:
                self.send_json({"error": f"Operador não pode alterar {key}."}, 403)
                return
            new_value = str(req.get("newValue"))
            old_value = None if "oldValue" not in req or req.get("oldValue") is None else str(req.get("oldValue"))
            force = bool(req.get("force"))
            # V17.62: snapshots completos do navegador não são mais autoridade para MLB↔SKU.
            # Um cache parcial não pode apagar centenas de vínculos importados. Inclusão/remoção
            # explícita usa /api/precificador/ml-vinculos/manual.
            if key == ML_SKU_MLB_LINKS_STORAGE_KEY:
                try:
                    incoming = json.loads(new_value or "{}")
                except Exception:
                    incoming = {}
                canonical = _ml_precificador_persisted_links()
                if isinstance(incoming, dict):
                    for raw_sku, raw_rows in incoming.items():
                        sku = _ml_normalize_link_sku(raw_sku)
                        if not sku or not isinstance(raw_rows, list):
                            continue
                        dest = canonical.setdefault(sku, [])
                        seen = {_ml_normalize_link_mlb(x.get("mlb")) for x in dest if isinstance(x, dict)}
                        for raw_link in raw_rows:
                            obj = dict(raw_link) if isinstance(raw_link, dict) else {"mlb": raw_link}
                            mlb = _ml_normalize_link_mlb(obj.get("mlb"))
                            if not mlb or mlb in seen:
                                continue
                            obj["mlb"] = mlb
                            obj.setdefault("sku", sku)
                            dest.append(obj)
                            seen.add(mlb)
                new_value = json_dumps(_ml_merge_seed_links(canonical))
                old_value = None
                force = True
            allow_label_deletes = bool(req.get("allowLabelDeletes"))
            allow_pending_deletes = bool(req.get("allowPendingDeletes"))
            if key == "conf_etiquetas" and not allow_label_deletes:
                current_labels, _, _ = get_storage("conf_etiquetas")
                new_value = preserve_label_records(current_labels, new_value)
            if key == "conf_pedidos" and not allow_pending_deletes:
                current_pending, _, _ = get_storage("conf_pedidos")
                new_value = preserve_pending_records_v1714(current_pending, new_value)
            if key == "conf_usuarios":
                if not self.require_owner_reauth(ctx):
                    audit(ctx["username"], "security.denied.owner_reauth", "conf_usuarios")
                    self.send_json({"error": "SEGURANÇA V17: somente o proprietário do sistema pode alterar usuários e administradores."}, 403)
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
                allow_pending_deletes=allow_pending_deletes,
            )
            audit(ctx["username"], "storage.save", key)
            nfe_name_update = None
            if key == "conf_etiquetas":
                try:
                    nfe_name_update = ml_update_pending_names_from_uploaded_nfe()
                except Exception as exc:
                    nfe_name_update = {"ok": False, "error": str(exc)[:500]}
            self.send_json({"ok": True, "value": value, "version": version, "updated_at": updated,
                            "nfe_name_update": nfe_name_update})
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
            backup_daily_em_segundo_plano()
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

    def handle_admin_order_upsert(self, ctx: dict[str, Any]) -> None:
        """V17.53: inclusão/edição canônica de pedidos feita explicitamente pelo ADMIN.

        Recebe somente as linhas que o administrador confirmou e faz merge aditivo no
        servidor. O pipeline ``save_storage`` registra o cofre manual, reabre pedidos
        concluídos quando necessário e reaplica os overrides antes de responder.
        """
        try:
            req = self.read_json()
            rows = req.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValueError("Nenhuma linha de pedido foi informada.")
            if len(rows) > 500:
                raise ValueError("Muitas linhas em uma única inclusão manual.")
            source = str(req.get("source") or "PAINEL_ADMIN").strip()[:120]
            now = now_iso()
            incoming: list[dict[str, Any]] = []
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                order_id = str(order_number(row) or "").strip()
                sku = str(row.get("SKU") or row.get("Sku") or "").strip()
                ean = str(row.get("Código de Barras") or row.get("EAN") or "").strip()
                if not order_id:
                    raise ValueError(f"Linha {idx+1}: pedido não informado.")
                if not (sku or ean):
                    raise ValueError(f"Linha {idx+1}: SKU/EAN não informado.")
                row["Pedido"] = order_id
                row["__uidLinha"] = str(row.get("__uidLinha") or row.get("__uidPedido") or ("PED-" + secrets.token_urlsafe(12)))
                row["OrigemCadastro"] = str(row.get("OrigemCadastro") or source or "MANUAL_ADMIN")
                row["ManualAdminProtegido"] = "SIM"
                row["ManualAdminOrigem"] = str(row.get("ManualAdminOrigem") or source or "PAINEL_ADMIN")
                row["ManualAdminCriadoEm"] = str(row.get("ManualAdminCriadoEm") or now)
                row["ManualAdminAtualizadoEm"] = now
                row["__manualAdminVault"] = True
                incoming.append(row)
            if not incoming:
                raise ValueError("Nenhuma linha válida foi recebida.")

            # V17.78: ao adicionar manualmente, o ADMIN está explicitamente
            # restaurando o pedido; tombstones antigos não podem ocultar a inclusão.
            _pending_admin_restore_orders({str(order_number(r) or "") for r in incoming})
            current_raw, _, _ = get_storage("conf_pedidos")
            current = _rows_from_storage_value(current_raw)
            merged = [dict(r) if isinstance(r, dict) else r for r in current]
            positions = {
                _pending_identity_v1714(r, i): i
                for i, r in enumerate(merged) if isinstance(r, dict)
            }
            for row in incoming:
                eid = _pending_identity_v1714(row, len(merged))
                if eid in positions:
                    old = merged[positions[eid]] if isinstance(merged[positions[eid]], dict) else {}
                    combined = dict(old)
                    combined.update(row)
                    merged[positions[eid]] = combined
                else:
                    positions[eid] = len(merged)
                    merged.append(row)

            # old_value é o snapshot canônico imediatamente anterior. Como o ADMIN
            # acabou de adicionar as linhas a ``merged``, save_storage reconhece uma
            # reimportação explícita até mesmo se o mesmo pedido já foi concluído.
            saved, version, updated = save_storage(
                "conf_pedidos", current_raw, json_dumps(merged), False,
                actor=str(ctx.get("username") or "admin"), actor_role="admin",
                source_screen="Admin / Extensão Marketplace",
                change_method=f"Upsert manual protegido: {source}",
                client_ip=client_computer(self),
                allow_completed_reimport=True,
                allow_pending_deletes=False,
            )
            order_keys = sorted({normalize(order_number(r)) for r in incoming if order_number(r)})
            audit(str(ctx.get("username") or "admin"), "order.admin_upsert", f"{source}|linhas={len(incoming)}|pedidos={len(order_keys)}")
            self.send_json({"ok": True, "value": saved, "version": version, "updated_at": updated,
                            "inserted_rows": len(incoming), "orders": order_keys})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 409)


    def handle_order_kit_delete(self, ctx: dict[str, Any]) -> None:
        """V17.66 — exclusão soberana do ADMIN para KIT/pedido inteiro.

        Remove todas as linhas do pedido de pending/drafts, marca o pedido inteiro
        como excluído e bloqueia qualquer reinserção automática futura. Para Shopee,
        cria também tombstone por pedido em conf_shopee_exclusoes_v1.
        """
        try:
            req = self.read_json()
            ref_order = str(req.get("pedido") or "").strip()
            if not ref_order:
                raise ValueError("Pedido não informado.")
            order_norm = normalize(ref_order)
            marketplace_hint = str(req.get("marketplace") or "").strip()
            actor = str(ctx.get("username") or "admin")
            timestamp = now_iso()

            def load_raw(key: str) -> tuple[list[dict[str, Any]], int]:
                row = DB.execute("SELECT value,version FROM storage WHERE key=?", (key,)).fetchone()
                raw = str(row["value"] or "[]") if row else "[]"
                version = int(row["version"] or 0) if row else 0
                try:
                    parsed = json.loads(raw)
                except Exception as exc:
                    raise ValueError(f"Os dados de {key} estão inválidos.") from exc
                if not isinstance(parsed, list):
                    parsed = []
                return [dict(x) for x in parsed if isinstance(x, dict)], version

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    removed_rows: list[dict[str, Any]] = []
                    for key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                        rows, version = load_raw(key)
                        kept: list[dict[str, Any]] = []
                        changed = False
                        for idx, row in enumerate(rows):
                            if normalize(order_number(row)) == order_norm:
                                removed_rows.append(row)
                                changed = True
                                entity_id = _admin_override_entity_id(key, row, idx)
                                _admin_override_upsert(key, entity_id, "__ENTITY__", None, deleted=True, actor=actor)
                            else:
                                kept.append(row)
                        if changed:
                            DB.execute(
                                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                                (key, json_dumps(kept), version + 1, timestamp),
                            )

                    # Tombstone no nível do pedido. É a autoridade final para QUALQUER integração.
                    del_row = DB.execute("SELECT value,version FROM storage WHERE key=?", (PENDING_ORDER_DELETE_KEY_V1766,)).fetchone()
                    del_raw = str(del_row["value"] or "[]") if del_row else "[]"
                    del_ver = int(del_row["version"] or 0) if del_row else 0
                    try:
                        deletions = json.loads(del_raw)
                    except Exception:
                        deletions = []
                    if not isinstance(deletions, list):
                        deletions = []
                    kept_del = [x for x in deletions if not (isinstance(x, dict) and normalize(x.get("Pedido")) == order_norm)]
                    kept_del.append({
                        "Pedido": ref_order,
                        "Marketplace": marketplace_hint or (str(removed_rows[0].get("Marketplace") or "") if removed_rows else ""),
                        "Tipo": "KIT_INTEIRO",
                        "Ativo": True,
                        "ExcluidoEm": timestamp,
                        "ExcluidoPor": actor,
                        "Origem": "ADMIN",
                    })
                    DB.execute(
                        """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                        (PENDING_ORDER_DELETE_KEY_V1766, json_dumps(kept_del), del_ver + 1, timestamp),
                    )

                    # Se alguma linha estava no cofre manual do ADMIN, marca como removida também.
                    DB.execute(
                        "UPDATE admin_manual_orders_v1752 SET deleted=1,updated_at=?,updated_by=? WHERE order_key=?",
                        (timestamp, actor, order_norm),
                    )

                    # Tombstone de PEDIDO Shopee: o importador descarta qualquer componente futuro desse pedido.
                    is_shopee = normalize(marketplace_hint) == "SHOPEE" or any(
                        normalize(str(r.get("Marketplace") or "")) == "SHOPEE"
                        or "SHOPEE" in normalize(str(r.get("OrigemCadastro") or ""))
                        or bool(str(r.get("SkuReferenciaShopee") or "").strip())
                        for r in removed_rows
                    )
                    if is_shopee:
                        ex_row = DB.execute("SELECT value,version FROM storage WHERE key=?", ("conf_shopee_exclusoes_v1",)).fetchone()
                        ex_raw = str(ex_row["value"] or "[]") if ex_row else "[]"
                        ex_ver = int(ex_row["version"] or 0) if ex_row else 0
                        try:
                            exclusions = json.loads(ex_raw)
                        except Exception:
                            exclusions = []
                        if not isinstance(exclusions, list):
                            exclusions = []
                        exclusions = [x for x in exclusions if not (
                            isinstance(x, dict)
                            and normalize(x.get("Pedido")) == order_norm
                            and (bool(x.get("ExcluirPedido")) or normalize(x.get("Tipo")) in {"PEDIDO", "KIT_INTEIRO"})
                        )]
                        exclusions.append({
                            "Pedido": ref_order,
                            "Tipo": "PEDIDO",
                            "ExcluirPedido": True,
                            "ExcluidoEm": timestamp,
                            "ExcluidoPor": actor,
                            "Origem": "ADMIN",
                        })
                        DB.execute(
                            """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                            ("conf_shopee_exclusoes_v1", json_dumps(exclusions), ex_ver + 1, timestamp),
                        )

                    DB.commit()
                    canonical, canonical_version, _ = get_storage("conf_pedidos")
                    draft_canonical, _, _ = get_storage("conf_pedidos_rascunhos")
                    verify_rows=json.loads(canonical or "[]")
                    verify_drafts=json.loads(draft_canonical or "[]")
                    if any(isinstance(r,dict) and normalize(order_number(r))==order_norm for r in (verify_rows+verify_drafts)):
                        self.send_json({"ok":False,"error":f"Falha de segurança: pedido {ref_order} ainda existe após exclusão ADMIN."},409)
                        return
                    audit(actor, "order.admin.delete", f"{ref_order}|linhas={len(removed_rows)}|marketplace={marketplace_hint}")
                    self.send_json({
                        "ok": True, "verified": True,
                        "pedido": ref_order,
                        "removed_rows": len(removed_rows),
                        "order_tombstone": True,
                        "shopee_order_tombstone": bool(is_shopee),
                        "value": canonical or "[]", "version": canonical_version,
                    })
                except Exception:
                    DB.rollback()
                    raise
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 409)

    def handle_order_line_delete(self, ctx: dict[str, Any]) -> None:
        """V17.64 — exclusão canônica de uma linha/componente de pedido.

        Remove a linha diretamente de conf_pedidos/conf_pedidos_rascunhos por UID,
        cria tombstone persistente de override e, quando a origem é Shopee, registra
        também a exclusão manual da Shopee. Assim um componente de KIT não volta por
        F5, polling ou sincronização.
        """
        try:
            req = self.read_json()
            uid = str(req.get("uid") or "").strip()
            ref_order = str(req.get("pedido") or "").strip()
            ref_sku = str(req.get("sku") or "").strip()
            if not uid:
                raise ValueError("Identificação da linha não informada.")

            def load_raw(key: str) -> tuple[list[dict[str, Any]], int]:
                raw_row = DB.execute("SELECT value,version FROM storage WHERE key=?", (key,)).fetchone()
                raw = str(raw_row["value"] or "[]") if raw_row else "[]"
                version = int(raw_row["version"] or 0) if raw_row else 0
                try:
                    rows = json.loads(raw)
                except Exception as exc:
                    raise ValueError(f"Os dados de {key} estão inválidos.") from exc
                if not isinstance(rows, list):
                    rows = []
                return [dict(x) for x in rows if isinstance(x, dict)], version

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    timestamp = now_iso()
                    actor = str(ctx.get("username") or "usuario")
                    removed_rows: list[tuple[str, int, dict[str, Any], str]] = []

                    for key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                        rows, version = load_raw(key)
                        kept: list[dict[str, Any]] = []
                        changed = False
                        for idx, row in enumerate(rows):
                            row_uid = str(row.get("__uidLinha") or row.get("__uidPedido") or "").strip()
                            match = bool(row_uid and row_uid == uid)
                            if not match and not row_uid and ref_order and ref_sku:
                                match = (
                                    normalize(order_number(row)) == normalize(ref_order)
                                    and normalize(str(row.get("SKU") or "")) == normalize(ref_sku)
                                )
                            if match:
                                entity_id = _admin_override_entity_id(key, row, idx)
                                removed_rows.append((key, idx, row, entity_id))
                                changed = True
                                continue
                            kept.append(row)
                        if changed:
                            DB.execute(
                                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                                (key, json_dumps(kept), version + 1, timestamp),
                            )

                    if not removed_rows:
                        DB.rollback()
                        self.send_json({"ok": True, "already_deleted": True, "uid": uid})
                        return

                    # Tombstone genérico: protege a exclusão contra qualquer merge legado.
                    for key, _idx, row, entity_id in removed_rows:
                        _admin_override_upsert(key, entity_id, "__ENTITY__", None, deleted=True, actor=actor)

                    # Tombstone específico Shopee: protege contra novo pull da API Shopee,
                    # independente do valor histórico de OrigemCadastro.
                    shopee_rows = [
                        row for _key, _idx, row, _eid in removed_rows
                        if normalize(str(row.get("Marketplace") or "")) == "SHOPEE"
                        or normalize(str(row.get("OrigemCadastro") or "")).find("SHOPEE") >= 0
                        or bool(str(row.get("SkuReferenciaShopee") or "").strip())
                    ]
                    if shopee_rows:
                        ex_raw_row = DB.execute("SELECT value,version FROM storage WHERE key=?", ("conf_shopee_exclusoes_v1",)).fetchone()
                        ex_raw = str(ex_raw_row["value"] or "[]") if ex_raw_row else "[]"
                        ex_ver = int(ex_raw_row["version"] or 0) if ex_raw_row else 0
                        try:
                            exclusions = json.loads(ex_raw)
                        except Exception:
                            exclusions = []
                        if not isinstance(exclusions, list):
                            exclusions = []
                        seen = {
                            (
                                normalize(x.get("Pedido")),
                                normalize(x.get("SkuOrigem") or x.get("SKU")),
                                str(x.get("UidLinha") or "").strip(),
                            )
                            for x in exclusions if isinstance(x, dict)
                        }
                        for row in shopee_rows:
                            pedido = str(order_number(row) or row.get("Pedido") or "").strip()
                            sku_origem = str(row.get("SkuOrigemImportacao") or row.get("SkuReferenciaShopee") or row.get("SKU") or "").strip()
                            row_uid = str(row.get("__uidLinha") or row.get("__uidPedido") or uid).strip()
                            sig = (normalize(pedido), normalize(sku_origem), row_uid)
                            if sig in seen:
                                continue
                            exclusions.append({
                                "Pedido": pedido,
                                "SkuOrigem": sku_origem,
                                "SKU": str(row.get("SKU") or ""),
                                "UidLinha": row_uid,
                                "ExcluidoEm": timestamp,
                                "Origem": "MANUAL_ADMIN",
                                "ExcluidoPor": actor,
                            })
                            seen.add(sig)
                        DB.execute(
                            """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                            ("conf_shopee_exclusoes_v1", json_dumps(exclusions), ex_ver + 1, timestamp),
                        )

                    DB.commit()
                    audit(actor, "order.line.delete", f"{uid}|{ref_order}|{ref_sku}")
                    canonical, canonical_version, _ = get_storage("conf_pedidos")
                    self.send_json({
                        "ok": True,
                        "uid": uid,
                        "removed": len(removed_rows),
                        "shopee_tombstone": bool(shopee_rows),
                        "value": canonical or "[]",
                        "version": canonical_version,
                    })
                except Exception:
                    DB.rollback()
                    raise
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 409)

    def handle_order_conference_name(self, ctx: dict[str, Any]) -> None:
        """V17.73 — grava Nome Conferência diretamente no pedido canônico.

        O vínculo é pelo número exato do pedido. Todas as linhas do mesmo pedido
        recebem o nome e cada linha ganha override manual, impedindo integrações de
        restaurarem o valor anterior.
        """
        try:
            req = self.read_json()
            pedido = str(req.get("pedido") or "").strip()
            nome = " ".join(str(req.get("nome") or "").strip().split())
            if not pedido:
                raise ValueError("Pedido não informado.")
            if not nome:
                raise ValueError("Nome de conferência não informado.")
            if len(nome) > 180 or any(ord(ch) < 32 for ch in nome):
                raise ValueError("Nome de conferência inválido.")
            pedido_key = normalize(pedido)
            actor = str(ctx.get("username") or "admin")

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    total = 0
                    updated_keys = []
                    for storage_key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                        raw_row = DB.execute(
                            "SELECT value,version FROM storage WHERE key=?", (storage_key,)
                        ).fetchone()
                        raw = str(raw_row["value"] or "[]") if raw_row else "[]"
                        version = int(raw_row["version"] or 0) if raw_row else 0
                        rows = json.loads(raw or "[]")
                        if not isinstance(rows, list):
                            raise ValueError(f"Dados inválidos em {storage_key}.")
                        changed = False
                        for idx, row in enumerate(rows):
                            if not isinstance(row, dict) or normalize(order_number(row)) != pedido_key:
                                continue
                            row["Nome Conferência"] = nome
                            row["NomeConferencia"] = nome
                            row["ConferenciaNome"] = nome
                            row["Conferência"] = nome
                            entity_id = _admin_override_entity_id(storage_key, row, idx)
                            for field in ("Nome Conferência", "NomeConferencia", "ConferenciaNome", "Conferência"):
                                _admin_override_upsert(
                                    storage_key, entity_id, field, nome,
                                    deleted=False, actor=actor
                                )
                            total += 1
                            changed = True
                        if changed:
                            value = json_dumps(rows)
                            timestamp = now_iso()
                            if raw_row:
                                DB.execute(
                                    "UPDATE storage SET value=?,version=?,updated_at=? WHERE key=?",
                                    (value, version + 1, timestamp, storage_key),
                                )
                            else:
                                DB.execute(
                                    "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)",
                                    (storage_key, value, 1, timestamp),
                                )
                            updated_keys.append(storage_key)

                    if total <= 0:
                        raise ValueError(f"Pedido {pedido} não encontrado em Pedidos Pendentes.")

                    # Mantém também o mapa legado em sincronia para telas antigas.
                    map_row = DB.execute(
                        "SELECT value,version FROM storage WHERE key='conf_nomes_conferencia_v1'"
                    ).fetchone()
                    try:
                        mapa = json.loads(str(map_row["value"] or "{}")) if map_row else {}
                    except Exception:
                        mapa = {}
                    if not isinstance(mapa, dict):
                        mapa = {}
                    mapa[pedido_key] = nome
                    map_value = json_dumps(mapa)
                    if map_row:
                        DB.execute(
                            "UPDATE storage SET value=?,version=?,updated_at=? WHERE key='conf_nomes_conferencia_v1'",
                            (map_value, int(map_row["version"] or 0) + 1, now_iso()),
                        )
                    else:
                        DB.execute(
                            "INSERT INTO storage(key,value,version,updated_at) VALUES('conf_nomes_conferencia_v1',?,?,?)",
                            (map_value, 1, now_iso()),
                        )

                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise

            # V17.75: sucesso somente depois de RELER a fonte canônica e confirmar.
            persisted, persisted_version, _ = get_storage("conf_pedidos")
            try:
                persisted_rows = json.loads(persisted or "[]")
            except Exception:
                persisted_rows = []
            matched = [
                r for r in persisted_rows
                if isinstance(r, dict) and normalize(order_number(r)) == pedido_key
            ]
            verified = bool(matched) and all(
                str(
                    r.get("Nome Conferência")
                    or r.get("NomeConferencia")
                    or r.get("ConferenciaNome")
                    or r.get("Conferência")
                    or ""
                ).strip() == nome
                for r in matched
            )
            if not verified:
                self.send_json({
                    "ok": False,
                    "verified": False,
                    "error": f"O pedido {pedido} foi localizado, mas o Nome de Conferência não permaneceu gravado no banco canônico."
                }, 409)
                return
            audit(actor, "order.conference_name", f"{pedido}|{nome}")
            self.send_json({
                "ok": True, "verified": True, "pedido": pedido, "nome": nome,
                "updated": len(matched), "version": persisted_version,
                "storages": updated_keys,
            })
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def handle_order_sku(self, ctx: dict[str, Any]) -> None:
        """V17.50 — salva SKU manual de forma transacional e canônica.

        A alteração é gravada diretamente no banco do servidor e também registrada
        como override persistente por UID. Assim F5, polling e sincronizações externas
        não conseguem restaurar o valor antigo.
        """
        try:
            req = self.read_json()
            uid = str(req.get("uid") or "").strip()
            sku = str(req.get("sku") or "").strip()
            ref_order = str(req.get("pedido") or "").strip()
            old_sku = str(req.get("oldSku") or "").strip()
            ref_description = str(req.get("descricao") or "").strip()
            if not uid:
                raise ValueError("Identificação da linha não informada.")
            if not sku:
                raise ValueError("Informe o SKU.")
            if len(sku) > 180 or any(ord(char) < 32 for char in sku):
                raise ValueError("SKU inválido ou muito longo.")

            def parse_raw_rows(key: str) -> tuple[list[dict[str, Any]], int]:
                raw_row = DB.execute("SELECT value,version FROM storage WHERE key=?", (key,)).fetchone()
                raw = str(raw_row["value"] or "[]") if raw_row else "[]"
                version = int(raw_row["version"] or 0) if raw_row else 0
                try:
                    rows = json.loads(raw)
                except Exception as exc:
                    raise ValueError(f"Os dados de {key} estão inválidos.") from exc
                if not isinstance(rows, list):
                    raise ValueError(f"Os dados de {key} não são uma lista.")
                return [dict(row) for row in rows if isinstance(row, dict)], version

            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                try:
                    pending, pending_version = parse_raw_rows("conf_pedidos")
                    drafts, draft_version = parse_raw_rows("conf_pedidos_rascunhos")
                    locations: list[tuple[str, int, dict[str, Any]]] = []
                    for key, rows in (("conf_pedidos", pending), ("conf_pedidos_rascunhos", drafts)):
                        for idx, row in enumerate(rows):
                            if str(row.get("__uidLinha") or row.get("__uidPedido") or "").strip() == uid:
                                locations.append((key, idx, row))
                    # fallback estrito apenas para migração de linhas antigas sem UID estável
                    if not locations and ref_order:
                        for key, rows in (("conf_pedidos", pending), ("conf_pedidos_rascunhos", drafts)):
                            for idx, row in enumerate(rows):
                                if normalize(order_number(row)) != normalize(ref_order):
                                    continue
                                if old_sku and normalize(str(row.get("SKU") or "")) != normalize(old_sku):
                                    continue
                                if ref_description and normalize(str(row.get("Descrição") or "")) != normalize(ref_description):
                                    continue
                                locations.append((key, idx, row))
                    if len(locations) != 1:
                        if not locations:
                            raise ValueError("A linha não foi localizada no banco atual do servidor.")
                        raise ValueError("Há mais de uma linha compatível; atualize a tela e tente novamente.")

                    source_key, source_index, row = locations[0]
                    previous = str(row.get("SKU") or "").strip()
                    if not str(row.get("SkuOrigemImportacao") or "").strip():
                        row["SkuOrigemImportacao"] = str(row.get("SkuOrigemIntegracaoRaw") or previous or old_sku).strip()
                    row["__uidLinha"] = uid
                    row["SKU"] = sku
                    row["SkuManualOverride"] = "SIM"
                    row["SkuManualAnterior"] = previous
                    row["SkuManualEditadoEm"] = now_iso()
                    row["SkuManualEditadoPor"] = str(ctx.get("username") or "admin")

                    rows = pending if source_key == "conf_pedidos" else drafts
                    rows[source_index] = row
                    timestamp = now_iso()
                    version = pending_version if source_key == "conf_pedidos" else draft_version
                    next_version = version + 1
                    DB.execute(
                        """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                        (source_key, json_dumps(rows), next_version, timestamp),
                    )

                    entity_id = _admin_override_entity_id(source_key, row, source_index)
                    for fld, val in (
                        ("SKU", sku),
                        ("SkuManualOverride", "SIM"),
                        ("SkuManualAnterior", previous),
                        ("SkuManualEditadoEm", row["SkuManualEditadoEm"]),
                        ("SkuManualEditadoPor", row["SkuManualEditadoPor"]),
                        ("SkuOrigemImportacao", row.get("SkuOrigemImportacao", "")),
                    ):
                        _admin_override_upsert(source_key, entity_id, fld, val, deleted=False, actor=str(ctx.get("username") or "admin"))

                    # O mesmo UID não pode existir nas duas bases; se existir em legado, protege também.
                    other_key = "conf_pedidos_rascunhos" if source_key == "conf_pedidos" else "conf_pedidos"
                    other_rows = drafts if other_key == "conf_pedidos_rascunhos" else pending
                    other_version = draft_version if other_key == "conf_pedidos_rascunhos" else pending_version
                    other_changed = False
                    for idx, other in enumerate(other_rows):
                        if str(other.get("__uidLinha") or other.get("__uidPedido") or "").strip() == uid:
                            other["SKU"] = sku
                            other["SkuManualOverride"] = "SIM"
                            other["SkuManualEditadoEm"] = row["SkuManualEditadoEm"]
                            other["SkuManualEditadoPor"] = row["SkuManualEditadoPor"]
                            other_rows[idx] = other
                            other_eid = _admin_override_entity_id(other_key, other, idx)
                            _admin_override_upsert(other_key, other_eid, "SKU", sku, deleted=False, actor=str(ctx.get("username") or "admin"))
                            _admin_override_upsert(other_key, other_eid, "SkuManualOverride", "SIM", deleted=False, actor=str(ctx.get("username") or "admin"))
                            other_changed = True
                    versions = {source_key: next_version}
                    updated_keys = [source_key]
                    if other_changed:
                        other_next = other_version + 1
                        DB.execute(
                            """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version,updated_at=excluded.updated_at""",
                            (other_key, json_dumps(other_rows), other_next, timestamp),
                        )
                        versions[other_key] = other_next
                        updated_keys.append(other_key)
                    DB.commit()
                except Exception:
                    DB.rollback()
                    raise

            audit(ctx["username"], "order.sku.manual_override", f"{ref_order}|{uid}|{old_sku}->{sku}"[:4000])
            backup_daily_em_segundo_plano()
            self.send_json({
                "ok": True,
                "uid": uid,
                "pedido": order_number(row),
                "sku": sku,
                "sku_original": str(row.get("SkuOrigemImportacao") or ""),
                "updated_at": row.get("SkuManualEditadoEm"),
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
            backup_daily_em_segundo_plano()
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
            backup_daily_em_segundo_plano()
            # V17.72: devolve o estado canônico já concluído para o cache da
            # estação que finalizou, sem depender de uma segunda leitura/race.
            self.send_json({
                "ok": True, "pedido": pedido, "removed": bool(matching),
                "already_removed": not matching,
                "value": json_dumps(remaining),
                "version": version + 1,
            })
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
        payload = payload if isinstance(payload, list) else []
        if str(item.get("marketplace") or "").strip().lower() == "mercado_livre":
            payload = [_ml_only_shipping_label_zpl(x) for x in payload if isinstance(x, str) and x.strip()]
            payload = [x for x in payload if x.strip()]
        item["payload"] = payload
        item["pending"] = item.get("status") in ("queued", "claimed")
        return item

    def handle_print_queue_enqueue(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            pedido = str(req.get("pedido") or "").strip()
            marketplace = str(req.get("marketplace") or "").strip()
            payload = req.get("payload")
            reprint = bool(req.get("reprint"))
            if marketplace.lower() == "mercado_livre" and isinstance(payload, list):
                payload = [_ml_only_shipping_label_zpl(x) for x in payload if isinstance(x, str) and x.strip()]
                payload = [x for x in payload if x.strip()]
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

    def touch_print_agent_station(self, station_id: str, computer: str, printer_name: str, error: str = "") -> None:
        now_epoch = int(time.time())
        with DB_LOCK:
            DB.execute(
                """INSERT INTO print_stations(station_id,station_name,printer_name,username,last_seen_epoch,last_seen_at,last_error)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(station_id) DO UPDATE SET
                   station_name=excluded.station_name,printer_name=excluded.printer_name,username=excluded.username,
                   last_seen_epoch=excluded.last_seen_epoch,last_seen_at=excluded.last_seen_at,last_error=excluded.last_error""",
                (station_id, computer or "Agente Zebra", printer_name, "agente-zebra", now_epoch, now_iso(), error[:1000]),
            )
            DB.commit()

    def handle_print_agent_next(self, query: dict[str, list[str]]) -> None:
        station_id = self.agent_station_id(query)
        computer = str((query.get("computer") or [station_id])[0] or station_id).strip()
        printer_name = str((query.get("printer") or query.get("printer_name") or [""])[0] or "").strip()
        self.touch_print_agent_station(station_id, computer, printer_name)
        now_epoch = int(time.time())
        stale_before = now_epoch - PRINT_CLAIM_STALE_SECONDS
        with DB_LOCK:
            DB.execute("BEGIN IMMEDIATE")
            try:
                DB.execute(
                    """UPDATE print_jobs SET status='queued',claimed_by='',claimed_at_epoch=0,updated_at=?,
                       error=CASE WHEN error='' THEN 'Trabalho devolvido à fila após perda de comunicação com o agente Zebra.' ELSE error END
                       WHERE status='claimed' AND claimed_at_epoch>0 AND claimed_at_epoch<?""",
                    (now_iso(), stale_before),
                )
                row = DB.execute("SELECT * FROM print_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
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
        if not row:
            self.send_json({"ok": True, "job": None, "server_time": now_iso()})
            return
        parsed = self.print_job_payload(row) or {}
        self.send_json({
            "ok": True,
            "job": {
                "id": parsed.get("id"),
                "order_id": parsed.get("order_id"),
                "documents": parsed.get("payload") or [],
                "kind": "zpl",
            },
            "server_time": now_iso(),
        })

    def handle_print_agent_result(self, query: dict[str, list[str]]) -> None:
        try:
            req = self.read_json()
            job_id = str(req.get("id") or req.get("job_id") or "").strip()
            station_id = self.agent_station_id(query, req)
            computer = str(req.get("computer") or (query.get("computer") or [station_id])[0] or station_id).strip()
            printer_name = str(req.get("printer_name") or (query.get("printer") or query.get("printer_name") or [""])[0] or "").strip()
            success = req.get("ok") if "ok" in req else req.get("success")
            success = bool(success)
            error = str(req.get("error") or "").strip()
            if not job_id:
                raise ValueError("ID da impressão não informado pelo agente.")
            self.touch_print_agent_station(station_id, computer, printer_name, "" if success else error)
            status = "printed" if success else "failed"
            now = now_iso()
            with DB_LOCK:
                row = DB.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    self.send_json({"ok": False, "error": "Impressão não encontrada."}, 404)
                    return
                if row["status"] != "printed":
                    DB.execute(
                        """UPDATE print_jobs SET status=?,updated_at=?,printed_at=?,error=?,claimed_by=?,claimed_at_epoch=? WHERE id=?""",
                        (status, now, now if success else "", error[:2000], station_id, int(time.time()), job_id),
                    )
                    DB.commit()
            audit("agente-zebra", "print.agent.result", f"{job_id}:{status}:{station_id}")
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"Não foi possível confirmar a impressão: {exc}"}, 500)

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
        users = []
        for row in rows:
            item = dict(row)
            item["display_profile"] = "Admin" if item.get("role") == "admin" else ("Supervisor" if is_supervisor_user(row) else "Operador")
            users.append(item)
        self.send_json({"users": users, "seed_admin": ADMIN_USERNAME, "login_interno": True})

    def handle_roles_post(self, ctx: dict[str, Any]) -> None:
        try:
            req = self.read_json()
            username = str(req.get("username") or "").strip().lower()
            display_name = str(req.get("display_name") or username).strip()
            requested_role = str(req.get("role") or "operador").strip().lower()
            supervisor = requested_role == "supervisor"
            role = "operador" if supervisor else requested_role
            active = req.get("active", True) not in (False, 0, "0", "false")
            password = str(req.get("password") or "").strip() or None
            raw_permissions = req.get("permissions") if isinstance(req.get("permissions"), dict) else {}
            if supervisor:
                permissions = {"conferencia": True, "admin": True, "estacao": True, "precificador": True}
            else:
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
            "/pendencias": "admin.html",
            "/pendencias/": "admin.html",
            "/central-pendencias": "admin.html",
            "/central-pendencias/": "admin.html",
            "/central-pendencias.html": "admin.html",
            "/estacao-impressao": "estacao-impressao.html",
            "/estacao-impressao/": "estacao-impressao.html",
            "/estacao-impressao.html": "estacao-impressao.html",
            "/print-station": "estacao-impressao.html",
            "/print-station/": "estacao-impressao.html",
            "/promocoes-ml": "admin.html",
            "/compras": "compras.html",
            "/compras/": "compras.html",
            "/compras.html": "compras.html",
            "/financeiro": "financeiro.html",
            "/financeiro/": "financeiro.html",
            "/financeiro.html": "financeiro.html",
            "/kits-analise": "kits-analise.html",
            "/kits-analise/": "kits-analise.html",
            "/kits-analise.html": "kits-analise.html",
            "/analise-ads": "analise-ads.html",
            "/analise-ads/": "analise-ads.html",
            "/analise-ads.html": "analise-ads.html",
            "/rentabilidade-ml": "financeiro.html",
            "/rentabilidade-ml/": "financeiro.html",
            "/rentabilidade-ml.html": "financeiro.html",
            "/promocoes-ml/": "admin.html",
            "/promocoes-ml.html": "admin.html",
            "/promocoes": "promocoes.html",
            "/promocoes/": "promocoes.html",
            "/promocoes.html": "promocoes.html",
            "/precificador": "precificador.html",
            "/precificador/": "precificador.html",
            "/precificador.html": "precificador.html",
            "/precificacao": "precificador.html",
            "/precificacao/": "precificador.html",
            "/analise-full": "analise-full.html",
            "/analise-full/": "analise-full.html",
            "/analise-full.html": "analise-full.html",
            "/full": "analise-full.html",
            "/full/": "analise-full.html",
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


# V18.56: uma exceção fora do try interno matava a thread em silêncio (keepalive do
# token, workers da Shopee, relatório diário) e a integração parava até alguém
# reiniciar o serviço. Este invólucro registra o erro e reinicia o worker.
WORKER_REINICIO_SEGUNDOS = 60


def _worker_protegido(alvo: Any) -> Any:
    def executar(stop_event: threading.Event) -> None:
        nome = getattr(alvo, "__name__", "worker")
        while not stop_event.is_set():
            try:
                alvo(stop_event)
                return  # o loop terminou normalmente (stop)
            except Exception as exc:
                try:
                    print(f"[{nome}] parou com erro e será reiniciado: {exc}", file=sys.stderr, flush=True)
                    _ml_worker_state_update(last_error=f"{nome}: {str(exc)[:500]}")
                except Exception:
                    pass
                if stop_event.wait(WORKER_REINICIO_SEGUNDOS):
                    return
    executar.__name__ = f"protegido_{getattr(alvo, '__name__', 'worker')}"
    return executar


# V18.56: ml_event_log e shopee_event_log cresciam para sempre (o worker de
# etiquetas Shopee grava um "OK" a cada ciclo com trabalho). audit_log é histórico
# e NÃO é limpo.
LOG_EVENTOS_DIAS = 90


def limpar_logs_antigos(dias: int = LOG_EVENTOS_DIAS) -> dict[str, int]:
    corte = (datetime.now(timezone.utc).astimezone() - timedelta(days=dias)).isoformat(timespec="milliseconds")
    removidos: dict[str, int] = {}
    for tabela in ("ml_event_log", "shopee_event_log"):
        with DB_LOCK:
            cur = DB.execute(f"DELETE FROM {tabela} WHERE created_at < ?", (corte,))
            DB.commit()
        removidos[tabela] = int(cur.rowcount or 0)
    return removidos


def manutencao_diaria_loop(stop_event: threading.Event) -> None:
    if stop_event.wait(300):
        return
    while not stop_event.is_set():
        try:
            removidos = limpar_logs_antigos()
            if any(removidos.values()):
                print(f"[manutencao] logs antigos removidos: {removidos}", flush=True)
        except Exception as exc:
            print(f"[manutencao] falhou: {exc}", file=sys.stderr, flush=True)
        backup_daily_em_segundo_plano()  # garante o backup do dia mesmo sem ninguém salvar
        if stop_event.wait(6 * 3600):
            break


def main() -> None:
    init_db()
    # V18.04: limpa, uma vez no boot, o selo de kit que sobrou nas vendas de
    # produto simples marcadas como kit pelo bug do alias (V18.03). Idempotente.
    try:
        _kit_residuo = reparar_kits_residuo_prefixo_v1803()
        if _kit_residuo:
            print(f"[boot] V18.04: {_kit_residuo} linha(s) de residuo de kit-prefixo limpa(s).")
    except Exception as _exc:  # nunca impede o boot
        print(f"[boot] reparo de kit-residuo falhou: {_exc}")
    # V18.12: o backup diário comprime ~1 GB e NÃO pode bloquear o boot — senão a
    # porta 8100 demora a abrir e o deploy parece ter falhado (aconteceu na V18.11).
    # Roda em segundo plano: a porta abre na hora e o backup comprime depois.
    if WORKERS_ENABLED:
        threading.Thread(target=backup_daily, name="backup-inicial", daemon=True).start()
    watcher_stop = threading.Event()
    watcher_thread = threading.Thread(
        target=_worker_protegido(ml_pending_label_watch_loop),
        args=(watcher_stop,),
        name="ml-pending-label-watcher",
        daemon=True,
    )
    new_order_thread = threading.Thread(
        target=_worker_protegido(ml_new_order_watch_loop),
        args=(watcher_stop,),
        name="ml-new-order-watcher",
        daemon=True,
    )
    manual_reconcile_thread = threading.Thread(
        target=_worker_protegido(ml_reconcile_manual_orders_loop),
        args=(watcher_stop,),
        name="ml-manual-order-reconciler",
        daemon=True,
    )
    token_keeper_thread = threading.Thread(
        target=_worker_protegido(ml_token_keepalive_loop),
        args=(watcher_stop,),
        name="ml-token-keeper",
        daemon=True,
    )
    safety_catchup_thread = threading.Thread(
        target=_worker_protegido(ml_safety_catchup_loop),
        args=(watcher_stop,),
        name="ml-safety-catchup",
        daemon=True,
    )
    shopee_label_thread = threading.Thread(
        target=_worker_protegido(shopee_label_worker_loop),
        args=(watcher_stop,),
        name="shopee-label-worker",
        daemon=True,
    )
    # V17.92: pedidos Shopee entram sozinhos, sem o botão de sincronizar.
    shopee_orders_thread = threading.Thread(
        target=_worker_protegido(shopee_orders_worker_loop),
        args=(watcher_stop,),
        name="shopee-orders-worker",
        daemon=True,
    )
    # V17.99: vendas do ML e da Shopee para o Dashboard de vendas.
    vendas_ml_thread = threading.Thread(
        target=_worker_protegido(vendas_sync_loop),
        args=(watcher_stop,),
        name="vendas-sync",
        daemon=True,
    )
    # V18.31: frete por conta do vendedor das vendas recentes (Financeiro).
    frete_thread = threading.Thread(
        target=_worker_protegido(financeiro_frete_loop),
        args=(watcher_stop,),
        name="financeiro-frete",
        daemon=True,
    )
    # V18.30: gasto com Publicidade (Product Ads) para o Financeiro.
    ads_thread = threading.Thread(
        target=_worker_protegido(ml_ads_sync_loop),
        args=(watcher_stop,),
        name="ml-ads-sync",
        daemon=True,
    )
    # V18.13: agente de relatório diário por e-mail (00:01).
    relatorio_thread = threading.Thread(
        target=_worker_protegido(relatorio_diario_loop),
        args=(watcher_stop,),
        name="relatorio-diario",
        daemon=True,
    )
    manutencao_thread = threading.Thread(
        target=_worker_protegido(manutencao_diaria_loop),
        args=(watcher_stop,),
        name="manutencao-diaria",
        daemon=True,
    )
    # V18.60: cliente já cadastrado no Linx? (Smart API, só leitura). Sem .env, ocioso.
    linx_clientes_thread = threading.Thread(
        target=_worker_protegido(linx_clientes_loop),
        args=(watcher_stop,),
        name="linx-clientes",
        daemon=True,
    )
    _ml_worker_state_update(started_at=now_iso(), last_error="")
    # V18.63: EXP_WORKERS=0 sobe só o HTTP — teste de boot e homologação. Uma cópia do
    # banco tem os tokens de produção e o refresh_token do ML é de uso único: um
    # worker rodando fora da produção derrubaria a conexão de verdade.
    if WORKERS_ENABLED:
        manutencao_thread.start()
        watcher_thread.start()
        new_order_thread.start()
        manual_reconcile_thread.start()
        token_keeper_thread.start()
        safety_catchup_thread.start()
        shopee_label_thread.start()
        shopee_orders_thread.start()
        vendas_ml_thread.start()
        frete_thread.start()
        ads_thread.start()
        relatorio_thread.start()
        linx_clientes_thread.start()
    else:
        print("[boot] EXP_WORKERS=0: workers de integração DESLIGADOS (só HTTP).", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    server.daemon_threads = True
    print(f"Sistema de Conferência Web {APP_VERSION} em http://{HOST}:{PORT}{BASE_PATH}", flush=True)
    print(f"Banco SQLite: {DB_PATH}", flush=True)
    print(f"Watcher Mercado Livre: pendências a cada {ML_PENDING_WATCH_INTERVAL}s", flush=True)
    print(f"Watcher Mercado Livre: pedidos novos a cada {ML_NEW_ORDER_WATCH_INTERVAL}s", flush=True)
    print(f"Reconciliação ML: pedidos manuais a cada {ML_MANUAL_RECONCILE_INTERVAL}s", flush=True)
    print(f"OAuth ML: renovação server-side a cada {ML_TOKEN_KEEPALIVE_INTERVAL}s", flush=True)
    print(f"Recuperação ML: janela de {ML_SAFETY_CATCHUP_HOURS}h a cada {ML_SAFETY_CATCHUP_INTERVAL}s", flush=True)
    print(f"Worker Shopee: etiquetas em background a cada {SHOPEE_LABEL_WORKER_INTERVAL}s ou sob demanda", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        watcher_stop.set()
        watcher_thread.join(timeout=3)
        new_order_thread.join(timeout=3)
        manual_reconcile_thread.join(timeout=3)
        token_keeper_thread.join(timeout=3)
        safety_catchup_thread.join(timeout=3)
        SHOPEE_LABEL_WAKE_EVENT.set()
        shopee_label_thread.join(timeout=3)
        server.server_close()
        with DB_LOCK:
            DB.close()


if __name__ == "__main__":
    main()
