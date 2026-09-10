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

APP_VERSION = "17.31-SHOPEE-SHIP-ORDER-REAL-FIX"
APP_MARKER = "noromak_expedicao_web"
BASE_PATH = "/expedicao"
HOST = os.getenv("EXP_HOST", "127.0.0.1")
PORT = int(os.getenv("EXP_PORT", "8100"))
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
SESSION_TTL_SECONDS = int(os.getenv("EXP_SESSION_TTL_SECONDS", str(4 * 60 * 60)))
STATION_SESSION_TTL_SECONDS = int(os.getenv("EXP_STATION_SESSION_TTL_SECONDS", str(180 * 24 * 60 * 60)))
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
CRM_STOCK_API_URL = os.getenv("CRM_STOCK_API_URL", CRM_COST_API_URL).strip() or CRM_COST_API_URL
CRM_STOCK_TIMEOUT = max(3, min(30, int(os.getenv("CRM_STOCK_TIMEOUT", str(CRM_COST_TIMEOUT)))))
CRM_COST_CACHE_SECONDS = max(30, min(600, int(os.getenv("CRM_COST_CACHE_SECONDS", "180"))))


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
PRECIFICADOR_BULK_LOCK = threading.Lock()
# V17.27: etiquetas Shopee rodam fora da requisição HTTP para evitar 504 do Nginx.
SHOPEE_LABEL_WORKER_LOCK = threading.Lock()
SHOPEE_LABEL_WAKE_EVENT = threading.Event()
SHOPEE_LABEL_WORKER_INTERVAL = max(60, min(900, int(os.getenv("SHOPEE_LABEL_WORKER_INTERVAL", "180"))))
# V17.31: evita repetir ship_order a cada ciclo depois que a Shopee já confirmou o arrange.
SHOPEE_SHIP_ORDER_CACHE_LOCK = threading.Lock()
SHOPEE_SHIP_ORDER_CACHE: dict[tuple[str, str], float] = {}
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
]

OPERATOR_WRITE_KEYS = {
    "conf_pedidos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em",
    "precificador_online_v1",
    "precificador_alertas_custo_v1",
    "ml_sku_mlb_links_v1",
    "conf_coleta_dia_v1",
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


def crm_stock_lookup(codigo: str, operador: str = "") -> dict[str, Any]:
    """Consulta estoque Apollo sem expor a CHAVE_CRM ao navegador.

    Por padrão utiliza o mesmo endpoint já usado pelo custo. Se o Apollo tiver
    um endpoint dedicado de estoque, configure CRM_STOCK_API_URL no .env.
    """
    codigo = str(codigo or "").strip()
    if not codigo:
        raise ValueError("Informe o SKU/código da peça.")
    if not CHAVE_CRM:
        raise RuntimeError("CHAVE_CRM não está configurada no servidor.")

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
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Apollo estoque respondeu HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Apollo estoque indisponível: {exc.reason}") from exc

    try:
        data = json.loads(raw or "{}")
    except Exception as exc:
        raise RuntimeError("Apollo estoque retornou resposta inválida.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Apollo estoque retornou resposta inválida.")
    if status >= 400:
        raise RuntimeError(str(data.get("erro") or data.get("detail") or f"HTTP {status}"))

    stock = _apollo_stock_payload(data)
    return {
        "ok": True,
        "codigo": str(data.get("codigo") or codigo),
        "descricao": str(data.get("descricao") or ""),
        **stock,
        "consultado_em": now_iso(),
    }


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
    with CRM_COST_CACHE_LOCK:
        CRM_COST_CACHE[cache_key] = (time.monotonic(), dict(result))
        # Evita crescimento indefinido; é um cache curto e pequeno.
        if len(CRM_COST_CACHE) > 300:
            oldest = sorted(CRM_COST_CACHE.items(), key=lambda item: item[1][0])[:100]
            for key, _ in oldest:
                CRM_COST_CACHE.pop(key, None)
    return result



def ml_precificador_links_payload(max_items: int = 5000) -> dict[str, Any]:
    """Lista anúncios do vendedor e monta automaticamente SKU -> MLB.

    Um SKU pode possuir vários MLBs. O retorno contém também anúncios sem SKU
    para que somente esses casos precisem de vínculo manual.
    """
    _, seller_id = ml_access_token()
    max_items = max(1, min(10000, int(max_items or 5000)))
    item_ids: list[str] = []
    seen_ids: set[str] = set()
    errors: list[str] = []

    # Busca anúncios ativos e pausados. Se a API aceitar "closed", também coleta
    # para preservar histórico de vínculos, sem bloquear a operação se falhar.
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

    # Metadados em lotes. ml_items_meta já extrai seller_custom_field / SELLER_SKU.
    digit_ids = [re.sub(r"\D+", "", item_id) for item_id in item_ids]
    meta_map: dict[str, dict[str, Any]] = {}
    for start in range(0, len(digit_ids), 100):
        batch = digit_ids[start:start + 100]
        try:
            meta_map.update(ml_items_meta(batch))
        except Exception as exc:
            errors.append(f"meta {start}: {str(exc)[:240]}")

    # Índice dos SKUs existentes no Precificador para preferir a grafia cadastrada.
    precifier = _promo_precifier_index()
    precifier_by_compact: dict[str, str] = {}
    for sku in precifier.keys():
        compact = re.sub(r"[^A-Z0-9]", "", sku.upper())
        if compact and compact not in precifier_by_compact:
            precifier_by_compact[compact] = sku

    links: dict[str, list[dict[str, Any]]] = {}
    unidentified: list[dict[str, Any]] = []

    for item_id in item_ids:
        digits = re.sub(r"\D+", "", item_id)
        meta = meta_map.get(digits) or {}
        raw_sku = str(meta.get("sku") or "").strip()
        sku = _ml_normalize_api_sku(raw_sku).strip().upper()

        # Alguns anúncios guardam prefixo numérico antes do SKU. Também tenta
        # equivalência compacta com os SKUs cadastrados no Precificador.
        if sku and sku not in precifier:
            compact = re.sub(r"[^A-Z0-9]", "", sku)
            if compact in precifier_by_compact:
                sku = precifier_by_compact[compact]

        row = {
            "mlb": "MLB" + digits,
            "sku": sku,
            "title": str(meta.get("title") or ""),
            "price": float(meta.get("price") or 0),
            "status": str(meta.get("status") or "unknown"),
            "available_quantity": meta.get("available_quantity"),
            "permalink": str(meta.get("permalink") or ""),
            "updatedAt": now_iso(),
        }

        if sku:
            links.setdefault(sku, []).append(row)
        else:
            unidentified.append(row)

    for sku, rows in links.items():
        rows.sort(key=lambda r: (str(r.get("status") or ""), str(r.get("mlb") or "")))

    return {
        "ok": True,
        "seller_id": str(seller_id),
        "total_items": len(item_ids),
        "linked_items": sum(len(v) for v in links.values()),
        "linked_skus": len(links),
        "unidentified": unidentified,
        "links": links,
        "errors": errors[:20],
        "updated_at": now_iso(),
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
                raw_json TEXT NOT NULL DEFAULT '',
                synced_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(period_key, document_type, detail_id, order_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_order ON ml_billing_period_detail(order_id, period_key);
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_pack ON ml_billing_period_detail(pack_id, period_key);
            CREATE INDEX IF NOT EXISTS idx_ml_billing_period_subtype ON ml_billing_period_detail(detail_sub_type, period_key);
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



ML_SKU_MLB_LINKS_STORAGE_KEY = "ml_sku_mlb_links_v1"
ML_SKU_MLB_SEED_PATH = ROOT_DIR / "DADOS" / "ml_sku_mlb_links_seed.json"


def seed_ml_sku_mlb_links_from_file() -> dict[str, int]:
    """Mescla a relação SKU↔MLB empacotada na V16.67 ao banco persistente.

    Preserva vínculos existentes/manuais e adiciona somente MLBs ausentes.
    """
    result = {"seed_skus": 0, "seed_links": 0, "added_links": 0}
    if not ML_SKU_MLB_SEED_PATH.exists():
        return result

    try:
        seed = json.loads(ML_SKU_MLB_SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return result
    if not isinstance(seed, dict):
        return result

    result["seed_skus"] = len(seed)
    result["seed_links"] = sum(len(v) for v in seed.values() if isinstance(v, list))

    with DB_LOCK:
        current_raw, version, _ = get_storage(ML_SKU_MLB_LINKS_STORAGE_KEY)
        try:
            current = json.loads(current_raw or "{}")
        except Exception:
            current = {}
        if not isinstance(current, dict):
            current = {}

        changed = False
        for raw_sku, seed_rows in seed.items():
            sku = str(raw_sku or "").strip().upper()
            if not sku or not isinstance(seed_rows, list):
                continue

            existing_rows = current.get(sku)
            if not isinstance(existing_rows, list):
                existing_rows = []

            normalized_existing: list[dict[str, Any]] = []
            by_mlb: dict[str, dict[str, Any]] = {}
            for row in existing_rows:
                obj = dict(row) if isinstance(row, dict) else {"mlb": row}
                digits = re.sub(r"\D+", "", str(obj.get("mlb") or ""))
                if not digits:
                    continue
                obj["mlb"] = "MLB" + digits
                if obj["mlb"] not in by_mlb:
                    by_mlb[obj["mlb"]] = obj
                    normalized_existing.append(obj)

            for row in seed_rows:
                obj = dict(row) if isinstance(row, dict) else {"mlb": row}
                digits = re.sub(r"\D+", "", str(obj.get("mlb") or ""))
                if not digits:
                    continue
                mlb = "MLB" + digits
                if mlb in by_mlb:
                    continue
                obj["mlb"] = mlb
                obj.setdefault("source", "planilha_importada")
                obj.setdefault("importedAt", now_iso())
                normalized_existing.append(obj)
                by_mlb[mlb] = obj
                result["added_links"] += 1
                changed = True

            if current.get(sku) != normalized_existing:
                current[sku] = normalized_existing
                changed = True

        if changed:
            new_value = json_dumps(current)
            DB.execute(
                """INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value,
                     version=excluded.version,
                     updated_at=excluded.updated_at""",
                (ML_SKU_MLB_LINKS_STORAGE_KEY, new_value, int(version or 0) + 1, now_iso()),
            )
            DB.commit()

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
    # V16.67: sobe/mescla automaticamente a planilha SKU↔MLB no banco.
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
            "Perfil": "Admin" if row["role"] == "admin" else "Operador",
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
    market=pick("Marketplace","marketplace","Origem","origem","Canal","canal").upper()
    order=pick("OrderID","order_id","Pedido","Nº Pedido","NumeroPedido","Venda","VendaID")
    pack=pick("PackID","pack_id","Pacote","PacoteID")
    item=pick("ItemID","item_id","MLB","CodigoAnuncio")
    sku=pick("SKU","Sku","sku","Código","Codigo")
    if order or pack: return f"{market}|{order}|{pack}|{item}|{sku}".upper()
    created=pick("CriadoEm","criadoEm","AdicionadoEm","adicionadoEm","Data","data")
    return f"MANUAL|{market}|{item}|{sku}|{created}|{index}".upper()

def preserve_pending_records_v1714(current_value: str | None, new_value: str) -> str:
    try:
        current=json.loads(current_value or "[]"); newer=json.loads(new_value or "[]")
    except Exception: return new_value
    if not isinstance(current,list) or not isinstance(newer,list): return new_value
    ids={_pending_identity_v1714(r,i) for i,r in enumerate(newer) if isinstance(r,dict)}
    changed=False
    for i,row in enumerate(current):
        if not isinstance(row,dict): continue
        ident=_pending_identity_v1714(row,i)
        if ident not in ids:
            newer.append(row);ids.add(ident);changed=True
    return json_dumps(newer) if changed else new_value

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
            if key in ("conf_pedidos", "conf_pedidos_rascunhos"):
                new_value = preserve_conference_names(current, new_value)
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

        save_storage(
            "precificador_online_v1", None, json_dumps(data), True,
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


def shopee_refresh_access_token(force: bool = False) -> sqlite3.Row:
    row = shopee_integration_row()
    if not row or not str(row["shop_id"] or "").strip() or not str(row["refresh_token"] or "").strip():
        raise RuntimeError("Shopee não possui refresh_token válido. Reconecte a loja.")
    if not force and str(row["access_token"] or "").strip() and int(row["token_expires_at"] or 0) > int(time.time()) + 300:
        return row

    shop_id = int(str(row["shop_id"]).strip())
    refresh_token = str(row["refresh_token"] or "").strip()
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
                return text[pos:].strip()
    return ""


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


def _shopee_ship_order_cached(order_sn: str, package_number: str) -> bool:
    key = (str(order_sn or "").strip(), str(package_number or "").strip())
    now = time.time()
    with SHOPEE_SHIP_ORDER_CACHE_LOCK:
        # Limpa entradas antigas para o dicionário permanecer pequeno.
        stale = [k for k, ts in SHOPEE_SHIP_ORDER_CACHE.items() if now - float(ts or 0) > SHOPEE_SHIP_ORDER_CACHE_TTL]
        for k in stale:
            SHOPEE_SHIP_ORDER_CACHE.pop(k, None)
        return key in SHOPEE_SHIP_ORDER_CACHE


def _shopee_mark_ship_order_cached(order_sn: str, package_number: str) -> None:
    key = (str(order_sn or "").strip(), str(package_number or "").strip())
    with SHOPEE_SHIP_ORDER_CACHE_LOCK:
        SHOPEE_SHIP_ORDER_CACHE[key] = time.time()


def shopee_sync_shipping_documents(shipments: list[dict[str,str]]) -> dict[str,Any]:
    """Organiza envio e processa etiquetas Shopee sem bloquear o request HTTP.

    V17.31: get_shipping_parameter -> ship_order -> get_tracking_number ->
    create_shipping_document -> get_shipping_document_result -> download/vínculo.
    O worker chama esta função; não existe polling síncrono no endpoint de sync.
    """
    summary={
        "consultados":0,"shipping_parameter_ok":0,"ship_order_ok":0,"ship_order_pendente":0,
        "tracking_ok":0,"tracking_pendente":0,"criados":0,"prontos":0,"baixados":0,
        "vinculados":0,"nao_zpl":0,"pendentes":0,"erros":0,"data_info_ok":0,
        "data_info_imagem":0,
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
                arranged=_shopee_ship_order_cached(order_sn,pkg)
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
                        _shopee_mark_ship_order_cached(order_sn,pkg)
                        summary["ship_order_ok"] += 1
                        shopee_log_event("ship_order",f"order={order_sn}","OK",f"modo={mode}; pacote={'sim' if pkg else 'nao'}")
                    except Exception as exc:
                        msg=_safe_err(exc)
                        low=msg.lower()
                        # ship_order é uma transição de estado. Em restart do serviço,
                        # o pedido pode já estar organizado; esses retornos são idempotentes.
                        already_tokens=("already", "has been shipped", "already shipped", "already arrange", "already arranged", "logistics_request_created")
                        if any(tok in low for tok in already_tokens):
                            arranged=True
                            _shopee_mark_ship_order_cached(order_sn,pkg)
                            summary["ship_order_ok"] += 1
                            shopee_log_event("ship_order",f"order={order_sn}","OK",f"já organizado; pacote={'sim' if pkg else 'nao'}")
                        else:
                            summary["erros"] += 1
                            summary["pendentes"] += 1
                            shopee_log_event("ship_order",f"order={order_sn}","ERRO",f"modo={mode}; {msg}")
                            continue
                else:
                    shopee_log_event("ship_order",f"order={order_sn}","OK",f"cache=sim; pacote={'sim' if pkg else 'nao'}")

                # 3) O rastreio só é consultado depois do arrange confirmado.
                q={"order_sn":order_sn}
                if pkg:
                    q["package_number"]=pkg
                tracking=""
                hint=""
                try:
                    tr=shopee_api_get("/api/v2/logistics/get_tracking_number", q)
                    tresp=tr.get("response") if isinstance(tr.get("response"),dict) else {}
                    tracking=str(tresp.get("tracking_number") or "").strip()
                    hint=str(tresp.get("hint") or "").strip()
                except Exception as exc:
                    shopee_log_event("tracking_number",f"order={order_sn}","AVISO",_safe_err(exc))
                if tracking:
                    summary["tracking_ok"] += 1
                    shopee_log_event("tracking_number",f"order={order_sn}","OK",f"obtido=sim; pacote={'sim' if pkg else 'nao'}")
                else:
                    summary["tracking_pendente"] += 1
                    summary["pendentes"] += 1
                    shopee_log_event("tracking_number",f"order={order_sn}","PENDENTE",f"obtido=nao; pacote={'sim' if pkg else 'nao'}; hint={hint[:300] or '-'}")
                    # O tracking pode ser emitido alguns minutos depois do ship_order.
                    # Worker tenta novamente no próximo ciclo; não faz polling aqui.
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

                # 5) Uma única consulta de status por ciclo; PROCESSING fica para o worker.
                result_row={"order_sn":order_sn}
                if pkg:
                    result_row["package_number"]=pkg
                if dtype:
                    result_row["shipping_document_type"]=dtype
                try:
                    result=shopee_api_post("/api/v2/logistics/get_shipping_document_result", {"order_list":[result_row]})
                    rlist=_parse_result_list(result)
                except Exception as exc:
                    msg=_safe_err(exc)
                    summary["pendentes"] += 1
                    if "shipping_document_should_print_first" in msg:
                        shopee_log_event("shipping_document_result",f"order={order_sn}","AVISO",f"create confirmado neste ciclo, mas Shopee ainda não liberou result; {msg}")
                    else:
                        summary["erros"] += 1
                        shopee_log_event("shipping_document_result",f"order={order_sn}","ERRO",msg)
                    continue

                if not rlist:
                    summary["pendentes"] += 1
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
                    shopee_log_event("shipping_document_result",f"order={order_sn}","PENDENTE",f"status={st or 'PROCESSING'}; doc={dtype or '-'}")
                elif ferr or st=="FAILED":
                    summary["erros"] += 1
                    summary["pendentes"] += 1
                    shopee_log_event("shipping_document_result",f"order={order_sn}","ERRO",f"status={st or '-'}; erro={ferr or '-'}; msg={fmsg}")
                else:
                    summary["pendentes"] += 1

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
    client = _shopee_real_text(
        recipient.get("name"),
        order.get("buyer_name"),
        address.get("name"),
    ) or _shopee_real_text(buyer_username) or "NOME NÃO DISPONÍVEL"
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
        identity = f"{order_sn}|{item.get('item_id')}|{item.get('model_id')}|{item.get('line_item_id')}|{sku}|{idx}"
        row = {
            "Pedido": order_sn,
            "Marketplace": "SHOPEE",
            "Data do Pedido": _shopee_date(order.get("create_time")),
            "Kit": "",
            "Nome do Kit": "",
            "Quantidade de Kits": "",
            "Quantidade por Kit": "",
            "Cliente": client,
            "NomeFiscalShopee": client if client != "NOME NÃO DISPONÍVEL" else "",
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



SHOPEE_MANUAL_EDIT_FIELDS = (
    "Cliente",
    "NomeFiscalShopee",
    "Nome Conferência",
    "Kit",
    "Nome do Kit",
    "Quantidade de Kits",
    "Quantidade por Kit",
    "SKU",
    "Quantidade",
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


def shopee_manual_sync() -> dict[str, Any]:
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
        # V17.27: nunca bloqueia a requisição esperando criação/READY/download de etiqueta.
        # O worker do servidor acorda imediatamente e continua o fluxo em background.
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


def shopee_label_worker_loop(stop_event: threading.Event) -> None:
    """Continua geração/consulta/download das etiquetas Shopee em background.

    A requisição de sincronização apenas acorda este worker. Assim, mesmo que a
    Shopee demore para preparar documentos, o Nginx não recebe uma requisição
    longa e não retorna HTTP 504.
    """
    # Pequeno atraso inicial para deixar HTTP/DB totalmente prontos no boot.
    stop_event.wait(5)
    while not stop_event.is_set():
        # Acorda imediatamente após sincronização manual ou no intervalo de segurança.
        SHOPEE_LABEL_WAKE_EVENT.wait(SHOPEE_LABEL_WORKER_INTERVAL)
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
            if not shipments:
                continue
            result = shopee_sync_shipping_documents(shipments)
            shopee_log_event(
                "shipping_worker",
                f"shop_id={st.get('shop_id')}",
                "OK",
                "consultados={consultados}; shipping_param={shipping_parameter_ok}; ship_order={ship_order_ok}; "
                "tracking={tracking_ok}; prontos={prontos}; baixados={baixados}; vinculados={vinculados}; "
                "pendentes={pendentes}; erros={erros}".format(**{
                    k: int(result.get(k, 0) or 0)
                    for k in ("consultados","shipping_parameter_ok","ship_order_ok","tracking_ok","prontos","baixados","vinculados","pendentes","erros")
                }),
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

    for start in range(0, len(missing), 20):
        batch = missing[start:start + 20]
        if not batch:
            continue
        ids_param = ",".join("MLB" + item for item in batch)
        payload = ml_http_json(
            "GET",
            "https://api.mercadolibre.com/items?" + urlencode({"ids": ids_param}),
        )
        rows = payload if isinstance(payload, list) else []
        fetched: dict[str, dict[str, Any]] = {}

        for wrapper in rows:
            if not isinstance(wrapper, dict):
                continue
            body = wrapper.get("body") if isinstance(wrapper.get("body"), dict) else {}
            meta = _ml_item_meta_from_body(body)
            mlb = str(meta.get("id") or "")
            if not mlb:
                continue
            fetched[mlb] = meta

        # Cache missing/error results too, briefly represented as unknown with zero stock.
        with ML_ITEM_META_CACHE_LOCK:
            for mlb in batch:
                meta = fetched.get(mlb)
                if meta:
                    meta = dict(meta)
                    meta["stock_known"] = True
                else:
                    meta = {
                        "id": mlb,
                        "title": "",
                        "sku": "",
                        "available_quantity": None,
                        "status": "unknown",
                        "price": 0,
                        "permalink": "",
                        "stock_known": False,
                    }
                ML_ITEM_META_CACHE[mlb] = (time.time(), dict(meta))
                result[mlb] = dict(meta)

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
    ml_store_token(payload, nickname=str(row["nickname"] or ""), preserve_refresh_if_missing=False, refresh_expected=True)
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
        return {"name": embedded_name, "billing_info_id": billing_id, "source": "order_embedded", "errors": []}

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
                    return {"name": name, "billing_info_id": billing_id, "source": label, "errors": errors}
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
                            "errors": errors,
                        }
                    errors.append(f"legacy_{suffix} respondeu sem nome fiscal")
                except Exception as exc:
                    errors.append(f"legacy_{suffix}: {str(exc)[:400]}")

    return {"name": "", "billing_info_id": billing_id, "source": "", "errors": errors[-8:]}

def _ml_billing_name(order: dict[str, Any]) -> str:
    # Prioriza os Dados para NF-e. Se o Mercado Livre ainda não devolver o
    # billing-info/nome fiscal, volta ao comportamento anterior e usa o nome
    # real disponível na própria order. Isso evita Cliente em branco/"—".
    fiscal = str(_ml_billing_details(order).get("name") or "").strip()
    return fiscal or _ml_real_buyer_name(order)


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


def _kit_sku_aliases(value: Any) -> list[str]:
    """Gera aliases robustos para localizar SKU de kit.

    Além das formas normal/compacta, aceita prefixos operacionais usados nos
    anúncios, como KIT-, KIT2-, KIT3-, etc. Ex.: KIT3-KS001 também procura KS001.
    """
    raw = str(value or "").strip().upper()
    if not raw:
        return []

    candidates = [raw]
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
    m = re.match(r"^KIT\s*(\d+)\s*[-_./: ]*\s*(.+?)\s*$", raw, flags=re.I)
    if not m:
        return None
    try:
        qty = int(m.group(1))
    except Exception:
        return None
    base = str(m.group(2) or "").strip()
    if qty < 2 or not base:
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
        for alias in _kit_sku_aliases(kit_sku):
            catalog.setdefault(alias, entry)
    return catalog


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
    """Normaliza apenas SKU recebido da API do Mercado Livre.

    O Mercado Livre pode devolver um prefixo numérico interno antes do SKU real,
    por exemplo ``155172-ACP429/9-B``. Quando o trecho anterior ao primeiro hífen
    contém somente dígitos, removemos apenas esse prefixo e preservamos todos os
    hífens internos do SKU restante.
    """
    raw = str(value or "").strip()
    if not raw or "-" not in raw:
        return raw
    prefix, remainder = raw.split("-", 1)
    if prefix.isdigit() and remainder.strip():
        return remainder.strip()
    return raw


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

def _ml_invoice_data(shipment_id: str, site_id: str = "MLB") -> dict[str, Any] | None:
    if not shipment_id:
        return None
    try:
        payload = ml_api_get(f"/shipments/{quote(str(shipment_id))}/invoice_data?siteId={quote(site_id or 'MLB')}")
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


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
        if matched:
            removed += 1
            continue
        kept.append(row)

    return kept, removed



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
        before = len(pending)
        pending = [
            row for row in pending
            if not (
                isinstance(row, dict)
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

        for expanded in _expand_ml_order_item(item_data, kit_catalog):
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
                "__uidLinha": "MLAPI_" + hashlib.sha1(
                    f"{order_id}|{item.get('id')}|{item.get('variation_id')}|{kit_sku}|{sku}".encode("utf-8")
                ).hexdigest()[:20],
                "OrigemCadastro": source,
                "SkuOrigemImportacao": sku_raw,
                "VendaMercadoLivre": order_id,
                "PackIdMercadoLivre": pack_id,
                "ShipmentIdMercadoLivre": shipment_id,
                "StatusMercadoLivre": status,
                "LogisticaMercadoLivre": _ml_logistic_type(shipment),
                "NomeFiscalMercadoLivre": fiscal_name,
                "DataLimiteDespachoMercadoLivre": date_for_dispatch,
                "MercadoLivreAtualizadoEm": now_iso(),
            }
            product = _ml_find_product(products, sku)
            if product:
                for field in ("SKU","Código de Barras","Descrição","Rua","Módulo","Prateleira","Nível","Posição","Foto"):
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
            "NomeFiscalMercadoLivre","DataLimiteDespachoMercadoLivre","MercadoLivreAtualizadoEm"
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
            existing_sku = _ml_normalize_api_sku(str(existing.get("SKU") or existing.get("Código de Barras") or "").strip())
            if same_ml_order and normalize(existing_sku) not in desired_keys:
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
            key = (normalize(order_number(existing)), normalize(existing_sku))
            if key not in by_key:
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
            for field in fields:
                value = imported.get(field)
                if str(value or "").strip():
                    existing[field] = value
            for field in (
                "OrigemCadastro","SkuOrigemImportacao","VendaMercadoLivre","PackIdMercadoLivre",
                "ShipmentIdMercadoLivre","StatusMercadoLivre","LogisticaMercadoLivre",
                "NomeFiscalMercadoLivre","DataLimiteDespachoMercadoLivre","MercadoLivreAtualizadoEm"
            ):
                existing[field] = imported.get(field, "")
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



def _ml_billing_detail_amount(node: dict[str, Any]) -> float | None:
    for key in ("detail_amount", "amount", "charge_amount"):
        v = _money_number(node.get(key))
        if v is not None:
            return abs(v)
    return None


def _ml_billing_flat_details(value: Any) -> list[dict[str, Any]]:
    """Coleta somente objetos que parecem detalhes financeiros do Billing ML."""
    out: list[dict[str, Any]] = []
    def walk(v: Any) -> None:
        if isinstance(v, dict):
            keys = {str(k).lower() for k in v.keys()}
            if ("detail_amount" in keys or "charge_amount" in keys) and (
                "detail_type" in keys or "detail_sub_type" in keys or "transaction_detail" in keys or "concept_type" in keys
            ):
                out.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return out


def _ml_billing_sale_fee_explicit(value: Any) -> float | None:
    """Procura campos explícitos de sale_fee/marketplace_fee sem somar valores genéricos."""
    vals: list[float] = []
    def amount_from(v: Any) -> float | None:
        if isinstance(v, (int, float, str)):
            return _money_number(v)
        if isinstance(v, dict):
            for k in ("amount", "total", "value", "detail_amount", "sale_fee_amount"):
                n = _money_number(v.get(k))
                if n is not None:
                    return n
        return None
    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                lk = str(k).lower()
                if lk in ("sale_fee", "sale_fee_amount", "sale_fee_total", "marketplace_fee"):
                    n = amount_from(x)
                    if n is not None and abs(n) > 0:
                        vals.append(abs(n))
                if isinstance(x, (dict, list)):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return max(vals) if vals else None


def _ml_billing_parse_order_result(result: dict[str, Any]) -> dict[str, Any]:
    """Consolida Billing por order sem misturar o frete, que vem de /shipments/{id}/costs."""
    fee = 0.0
    other = 0.0
    bonus = 0.0
    fee_seen = False
    other_seen = False
    descriptions: list[str] = []
    for d in _ml_billing_flat_details(result):
        amount = _ml_billing_detail_amount(d)
        if amount is None or amount == 0:
            continue
        dtype = str(d.get("detail_type") or "").upper()
        sub = str(d.get("detail_sub_type") or d.get("detail_sub_types") or "").upper()
        text = " ".join(str(d.get(k) or "") for k in (
            "transaction_detail", "transaction_detail_description", "detail_type_description",
            "detail_sub_type", "detail_sub_type_description", "concept_type", "concept_type_description",
            "marketplace_type", "description"
        )).lower()
        is_bonus = dtype in ("BONUS", "BONIFICATION", "CREDIT") or "bonif" in text or "rebate" in text
        is_shipping = any(t in text for t in ("shipping", "envio", "frete", "mercado envios")) or sub in ("CE", "BE")
        is_sale_fee = (
            sub in ("CV", "BV") or
            any(t in text for t in ("cargo por venta", "tarifa de venda", "tarifa por venda", "sale fee", "selling fee", "comissão", "comision", "commission"))
        )
        label = str(d.get("transaction_detail_description") or d.get("detail_sub_type_description") or d.get("transaction_detail") or sub or dtype)
        if label and label not in descriptions:
            descriptions.append(label)
        if is_shipping:
            # O custo de envio do vendedor é conciliado separadamente via shipment costs.
            continue
        if is_sale_fee:
            fee_seen = True
            fee += -amount if is_bonus else amount
        else:
            other_seen = True
            other += -amount if is_bonus else amount
        if is_bonus:
            bonus += amount
    explicit = _ml_billing_sale_fee_explicit(result)
    if (not fee_seen or fee <= 0) and explicit is not None:
        fee = explicit
        fee_seen = True
    return {
        "fee": max(0.0, fee) if fee_seen else None,
        "other": other if other_seen else 0.0,
        "bonus": bonus,
        "diagnostic": "; ".join(descriptions[:8]),
        "has_financial_details": bool(fee_seen or other_seen),
    }



def _ml_billing_order_ids_from_node(node: Any) -> set[str]:
    """Extrai order/pack IDs de estruturas variáveis do Billing ML."""
    out: set[str] = set()
    def walk(v: Any, parent_key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                lk = str(k).lower()
                if lk in ("order_id", "orderid", "order", "pack_id", "packid"):
                    if isinstance(x, dict):
                        candidate = x.get("id") or x.get("order_id") or x.get("pack_id")
                        if candidate not in (None, ""):
                            out.add(str(candidate))
                    elif isinstance(x, (str, int, float)) and str(x).strip():
                        text = str(x).strip()
                        if text.replace(".", "", 1).isdigit():
                            out.add(text.split(".")[0])
                walk(x, lk)
        elif isinstance(v, list):
            for x in v:
                walk(x, parent_key)
    walk(node)
    return out


def _ml_billing_period_parse(payload: Any, requested_ids: list[str], document_type: str) -> dict[str, dict[str, Any]]:
    """Classifica detalhes do Billing por order/pack, aceitando variações do contrato.

    BILL soma débitos. CREDIT_NOTE é tratada como crédito/bonificação e reduz outros custos
    ou a própria tarifa quando o detalhe identifica explicitamente sale fee.
    """
    wanted = {str(x) for x in requested_ids if str(x).strip()}
    result = {oid: {"fee": 0.0, "other": 0.0, "bonus": 0.0, "fee_bonus": 0.0, "other_bonus": 0.0, "entries": [], "seen": False, "fee_seen": False} for oid in wanted}
    doc_credit = str(document_type or "").upper() in ("CREDIT_NOTE", "CREDIT", "BONUS")
    details = _ml_billing_flat_details(payload)
    # Alguns responses trazem result rows que não atendem ao detector acima. Inclui também
    # objetos com detail_amount/amount e algum identificador de order.
    def collect_extra(v: Any, out: list[dict[str, Any]]) -> None:
        if isinstance(v, dict):
            keys = {str(k).lower() for k in v}
            if ("detail_amount" in keys or "amount" in keys or "charge_amount" in keys) and _ml_billing_order_ids_from_node(v):
                out.append(v)
            for x in v.values(): collect_extra(x, out)
        elif isinstance(v, list):
            for x in v: collect_extra(x, out)
    extra: list[dict[str, Any]] = []
    collect_extra(payload, extra)
    seen_obj: set[int] = set()
    merged: list[dict[str, Any]] = []
    for d in details + extra:
        if id(d) in seen_obj: continue
        seen_obj.add(id(d)); merged.append(d)
    for d in merged:
        ids = _ml_billing_order_ids_from_node(d) & wanted
        if not ids:
            # Em lotes unitários, algumas respostas omitem order_id do detalhe.
            if len(wanted) == 1:
                ids = set(wanted)
            else:
                raw = json.dumps(d, ensure_ascii=False)
                ids = {oid for oid in wanted if oid in raw}
        if not ids:
            continue
        amount = _ml_billing_detail_amount(d)
        if amount is None or amount == 0:
            continue
        dtype = str(d.get("detail_type") or d.get("type") or "").upper()
        sub = str(d.get("detail_sub_type") or d.get("detail_sub_types") or "").upper()
        text = " ".join(str(d.get(k) or "") for k in (
            "transaction_detail", "transaction_detail_description", "detail_type_description",
            "detail_sub_type", "detail_sub_type_description", "concept_type", "concept_type_description",
            "marketplace_type", "description", "charge_type"
        )).lower()
        is_shipping = any(t in text for t in ("shipping", "envio", "frete", "mercado envios")) or sub in ("CE", "BE")
        is_sale_fee = (sub in ("CV", "BV") or any(t in text for t in (
            "cargo por venta", "tarifa de venda", "tarifa por venda", "sale fee", "selling fee",
            "comissão", "comissao", "comision", "commission", "tarifa por unidade"
        )))
        is_bonus = doc_credit or dtype in ("BONUS", "BONIFICATION", "CREDIT") or any(t in text for t in ("bonif", "rebate", "crédito", "credito"))
        if is_shipping:
            # Frete é conciliado por shipment costs para evitar dupla contagem.
            continue
        for oid in ids:
            r = result[oid]; r["seen"] = True; r["entries"].append(d)
            if is_sale_fee:
                r["fee_seen"] = True
                if is_bonus:
                    r["bonus"] += amount
                    r["fee_bonus"] += amount
                else:
                    r["fee"] += amount
            else:
                if is_bonus:
                    r["bonus"] += amount
                    r["other_bonus"] += amount
                else:
                    r["other"] += amount
    for oid, r in result.items():
        r["fee"] = round(max(0.0, r["fee"]), 2)
        r["other"] = round(max(0.0, r["other"]), 2)
        r["bonus"] = round(max(0.0, r["bonus"]), 2)
        r["fee_bonus"] = round(max(0.0, r.get("fee_bonus") or 0.0), 2)
        r["other_bonus"] = round(max(0.0, r.get("other_bonus") or 0.0), 2)
    return result


def _ml_billing_month_key(date_value: str) -> str:
    dt = _ml_parse_datetime(date_value)
    if dt is None:
        dt = datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}-01"


def _ml_billing_fetch_period(order_ids: list[str], month_key: str, seller_id: str, document_type: str) -> dict[str, dict[str, Any]]:
    """Consulta detalhe de faturamento do período por order_ids com paginação defensiva."""
    ids = [str(x).strip() for x in order_ids if str(x).strip()]
    if not ids:
        return {}
    aggregate = {oid: {"fee": 0.0, "other": 0.0, "bonus": 0.0, "fee_bonus": 0.0, "other_bonus": 0.0, "entries": [], "seen": False, "fee_seen": False} for oid in ids}
    from_id = ""
    for _page in range(20):
        q: dict[str, str] = {
            "document_type": str(document_type),
            "order_ids": ",".join(ids),
            "limit": "100",
        }
        if seller_id:
            q["seller_id"] = str(seller_id)
        if from_id:
            q["from_id"] = from_id
        endpoint = f"/billing/integration/periods/key/{quote(month_key)}/group/ML/details?" + urlencode(q)
        payload = _ml_profitability_api_get(endpoint, attempts=3)
        parsed = _ml_billing_period_parse(payload, ids, document_type)
        for oid, pr in parsed.items():
            ar = aggregate[oid]
            ar["fee"] += float(pr.get("fee") or 0)
            ar["other"] += float(pr.get("other") or 0)
            ar["bonus"] += float(pr.get("bonus") or 0)
            ar["fee_bonus"] += float(pr.get("fee_bonus") or 0)
            ar["other_bonus"] += float(pr.get("other_bonus") or 0)
            ar["entries"].extend(pr.get("entries") or [])
            ar["seen"] = bool(ar["seen"] or pr.get("seen"))
            ar["fee_seen"] = bool(ar["fee_seen"] or pr.get("fee_seen"))
        # Contratos observados usam next_id / next_from_id / paging.next. Se nenhum vier,
        # uma consulta filtrada por até 60 orders normalmente já terminou.
        next_id = ""
        if isinstance(payload, dict):
            for k in ("next_id", "next_from_id", "from_id"):
                v = payload.get(k)
                if v not in (None, "", from_id):
                    next_id = str(v); break
            paging = payload.get("paging")
            if not next_id and isinstance(paging, dict):
                v = paging.get("next") or paging.get("next_id") or paging.get("from_id")
                if isinstance(v, dict): v = v.get("id") or v.get("from_id")
                if v not in (None, "", from_id): next_id = str(v)
        if not next_id:
            break
        from_id = next_id
    for r in aggregate.values():
        r["fee"] = round(max(0.0, r["fee"]), 2)
        r["other"] = round(max(0.0, r["other"]), 2)
        r["bonus"] = round(max(0.0, r["bonus"]), 2)
        r["fee_bonus"] = round(max(0.0, r.get("fee_bonus") or 0.0), 2)
        r["other_bonus"] = round(max(0.0, r.get("other_bonus") or 0.0), 2)
    return aggregate


def _ml_billing_fetch_orders_period(records: list[dict[str, Any]], seller_id: str) -> dict[str, dict[str, Any]]:
    """Concilia por mês de venda: BILL + CREDIT_NOTE, com fallback order/details."""
    groups: dict[str, list[str]] = {}
    for rec in records:
        oid = str(rec.get("order_id") or "").strip()
        if not oid: continue
        month = _ml_billing_month_key(str(rec.get("date") or ""))
        if oid not in groups.setdefault(month, []): groups[month].append(oid)
    output: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, list[str]] = {}
    for month, ids in groups.items():
        for start in range(0, len(ids), 60):
            chunk = ids[start:start+60]
            bill_map: dict[str, dict[str, Any]] = {}
            credit_map: dict[str, dict[str, Any]] = {}
            errors: list[str] = []
            try:
                bill_map = _ml_billing_fetch_period(chunk, month, seller_id, "BILL")
            except Exception as exc:
                errors.append("Billing período BILL: " + str(exc)[:350])
            try:
                credit_map = _ml_billing_fetch_period(chunk, month, seller_id, "CREDIT_NOTE")
            except Exception as exc:
                errors.append("Billing período CREDIT_NOTE: " + str(exc)[:350])
            for oid in chunk:
                b = bill_map.get(oid) or {}; c = credit_map.get(oid) or {}
                fee = float(b.get("fee") or 0)
                other = float(b.get("other") or 0)
                fee_bonus = float(b.get("fee_bonus") or 0) + float(c.get("fee_bonus") or 0)
                other_bonus = float(b.get("other_bonus") or 0) + float(c.get("other_bonus") or 0)
                bonus = fee_bonus + other_bonus
                seen = bool(b.get("seen") or c.get("seen"))
                fee_seen = bool(b.get("fee_seen"))
                output[oid] = {
                    "order_id": oid, "seller_id": str(seller_id or ""),
                    "sale_fee": round(max(0.0, fee - fee_bonus), 2) if fee_seen else None,
                    "other_cost": round(other - other_bonus, 2) if seen else None,
                    "bonus": round(bonus, 2),
                    "status": "ok" if fee_seen else ("partial" if seen else "partial"),
                    "diagnostic": f"Billing período {month}: " + ("tarifa identificada" if fee_seen else "detalhes encontrados, mas tarifa de venda não identificada") + (("; " + "; ".join(errors)) if errors else ""),
                    "fetched_at": now_iso(), "fetched_at_epoch": int(time.time()),
                    "raw_json": json.dumps({"bill_entries": b.get("entries") or [], "credit_entries": c.get("entries") or []}, ensure_ascii=False)[:120000],
                }
    # Fallback oficial por order para o que não teve sale fee identificada no período.
    missing = [oid for oid, row in output.items() if _money_number(row.get("sale_fee")) is None]
    if missing:
        fallback = _ml_billing_fetch_orders(missing, seller_id, force=True)
        for oid in missing:
            fb = fallback.get(oid) or {}
            if _money_number(fb.get("sale_fee")) is not None:
                row = dict(fb)
                row["diagnostic"] = "Fallback billing/order/details: " + str(row.get("diagnostic") or "")
                output[oid] = row
            elif oid in output and fb.get("diagnostic"):
                output[oid]["diagnostic"] += "; fallback: " + str(fb.get("diagnostic"))
    # Persiste resultado final no cache usado pelo dashboard.
    now_epoch = int(time.time())
    for oid, row in output.items():
        with DB_LOCK:
            DB.execute("""INSERT INTO ml_profitability_billing_cache
                (order_id,seller_id,sale_fee,other_cost,bonus,status,diagnostic,fetched_at,fetched_at_epoch,raw_json)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                seller_id=excluded.seller_id,sale_fee=excluded.sale_fee,other_cost=excluded.other_cost,bonus=excluded.bonus,
                status=excluded.status,diagnostic=excluded.diagnostic,fetched_at=excluded.fetched_at,
                fetched_at_epoch=excluded.fetched_at_epoch,raw_json=excluded.raw_json""",
                (oid, str(seller_id or ""), row.get("sale_fee"), row.get("other_cost"), row.get("bonus"),
                 row.get("status") or "partial", row.get("diagnostic") or "", now_iso(), now_epoch, row.get("raw_json") or ""))
            DB.commit()
    return output


def _ml_billing_cached(order_id: str) -> dict[str, Any] | None:
    with DB_LOCK:
        row = DB.execute("SELECT * FROM ml_profitability_billing_cache WHERE order_id=?", (str(order_id),)).fetchone()
    return dict(row) if row else None


def _ml_billing_fetch_orders(order_ids: list[str], seller_id: str, force: bool = False) -> dict[str, dict[str, Any]]:
    """Consulta Billing em lotes de até 60 e mantém cache local para evitar 429."""
    now_epoch = int(time.time())
    output: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []
    seen: set[str] = set()
    for oid in order_ids:
        oid = str(oid or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        cached = _ml_billing_cached(oid)
        # Resultados confirmados são estáticos; incompletos podem ser reconsultados após 6h.
        fresh = cached and (str(cached.get("status") or "") == "ok" or now_epoch - int(cached.get("fetched_at_epoch") or 0) < 21600)
        if cached and fresh and not force:
            output[oid] = cached
        else:
            to_fetch.append(oid)
    for start in range(0, len(to_fetch), 60):
        chunk = to_fetch[start:start+60]
        q = {"order_ids": ",".join(chunk), "sort_by": "ID", "order_by": "ASC"}
        if seller_id:
            q["seller_id"] = str(seller_id)
        endpoint = "/billing/integration/group/ML/order/details?" + urlencode(q)
        try:
            payload = ml_api_get(endpoint)
            results = payload.get("results") if isinstance(payload, dict) and isinstance(payload.get("results"), list) else []
            by_oid: dict[str, dict[str, Any]] = {}
            for result in results:
                if not isinstance(result, dict):
                    continue
                oid = str(result.get("order_id") or "").strip()
                if oid:
                    by_oid[oid] = result
            for oid in chunk:
                result = by_oid.get(oid)
                if result:
                    parsed = _ml_billing_parse_order_result(result)
                    status = "ok" if parsed.get("has_financial_details") and parsed.get("fee") is not None else "partial"
                    diagnostic = parsed.get("diagnostic") or ("Billing retornou a order, mas sem tarifa de venda identificável." if status == "partial" else "Billing conciliado")
                    row = {
                        "order_id": oid, "seller_id": str(seller_id or ""),
                        "sale_fee": parsed.get("fee"), "other_cost": parsed.get("other"), "bonus": parsed.get("bonus"),
                        "status": status, "diagnostic": diagnostic, "fetched_at": now_iso(), "fetched_at_epoch": now_epoch,
                        "raw_json": json.dumps(result, ensure_ascii=False)[:120000],
                    }
                else:
                    row = {
                        "order_id": oid, "seller_id": str(seller_id or ""), "sale_fee": None, "other_cost": None, "bonus": None,
                        "status": "partial", "diagnostic": "Billing não retornou esta order/pack nesta consulta.",
                        "fetched_at": now_iso(), "fetched_at_epoch": now_epoch, "raw_json": "",
                    }
                with DB_LOCK:
                    DB.execute("""INSERT INTO ml_profitability_billing_cache
                        (order_id,seller_id,sale_fee,other_cost,bonus,status,diagnostic,fetched_at,fetched_at_epoch,raw_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                        seller_id=excluded.seller_id,sale_fee=excluded.sale_fee,other_cost=excluded.other_cost,bonus=excluded.bonus,
                        status=excluded.status,diagnostic=excluded.diagnostic,fetched_at=excluded.fetched_at,
                        fetched_at_epoch=excluded.fetched_at_epoch,raw_json=excluded.raw_json""",
                        (row["order_id"],row["seller_id"],row["sale_fee"],row["other_cost"],row["bonus"],row["status"],row["diagnostic"],row["fetched_at"],row["fetched_at_epoch"],row["raw_json"]))
                    DB.commit()
                output[oid] = row
        except Exception as exc:
            msg = "Billing ML: " + str(exc)
            for oid in chunk:
                cached = _ml_billing_cached(oid)
                if cached:
                    output[oid] = cached
                    continue
                row = {"order_id": oid, "seller_id": str(seller_id or ""), "sale_fee": None, "other_cost": None, "bonus": None,
                       "status": "error", "diagnostic": msg, "fetched_at": now_iso(), "fetched_at_epoch": now_epoch, "raw_json": ""}
                with DB_LOCK:
                    DB.execute("""INSERT INTO ml_profitability_billing_cache
                        (order_id,seller_id,sale_fee,other_cost,bonus,status,diagnostic,fetched_at,fetched_at_epoch,raw_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET status=excluded.status,diagnostic=excluded.diagnostic,
                        fetched_at=excluded.fetched_at,fetched_at_epoch=excluded.fetched_at_epoch""",
                        (oid,row["seller_id"],None,None,None,row["status"],row["diagnostic"],row["fetched_at"],row["fetched_at_epoch"],""))
                    DB.commit()
                output[oid] = row
    return output



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
        if age >= 0 and age < ttl and str(state.get("status") or "") in ("ok", "partial", "empty"):
            return {"ok": True, "cached": True, **state}

    from_id = "0"
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
        try:
            payload = ml_api_get(endpoint)
        except Exception as exc:
            status = "error"
            diag.append(str(exc)[:500])
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
                         transaction_detail,debited_from_operation,sale_fee_net,sale_fee_gross,sale_fee_rebate,sale_fee_discount,is_credit,raw_json,synced_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(period_key,document_type,detail_id,order_id) DO UPDATE SET
                        pack_id=excluded.pack_id,shipment_id=excluded.shipment_id,detail_amount=excluded.detail_amount,
                        detail_type=excluded.detail_type,detail_sub_type=excluded.detail_sub_type,
                        transaction_detail=excluded.transaction_detail,debited_from_operation=excluded.debited_from_operation,
                        sale_fee_net=COALESCE(excluded.sale_fee_net,ml_billing_period_detail.sale_fee_net),
                        sale_fee_gross=COALESCE(excluded.sale_fee_gross,ml_billing_period_detail.sale_fee_gross),
                        sale_fee_rebate=COALESCE(excluded.sale_fee_rebate,ml_billing_period_detail.sale_fee_rebate),
                        sale_fee_discount=COALESCE(excluded.sale_fee_discount,ml_billing_period_detail.sale_fee_discount),
                        is_credit=excluded.is_credit,raw_json=excluded.raw_json,synced_at=excluded.synced_at""",
                        (period_key, doc, did, oid, pack_id, shipment_id, amount, dtype, sub, trans, debited,
                         sf.get("net"), sf.get("gross"), sf.get("rebate"), sf.get("discount"),
                         1 if doc == "CREDIT_NOTE" else 0, json.dumps(row, ensure_ascii=False)[:120000], now_iso()),
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


def _ml_billing_v1513_is_shipping(sub: str, transaction: str, raw_json: str = "") -> bool:
    sub = str(sub or "").upper()
    text = (str(transaction or "") + " " + str(raw_json or "")[:3000]).lower()
    return sub in ("CXD", "BXD", "CE", "BE", "CFLX", "BFLX") or any(x in text for x in ("mercado envios", "shipping", "frete", "envío", "envio"))


def _ml_billing_v1513_is_sale_fee(sub: str, transaction: str) -> bool:
    sub = str(sub or "").upper()
    text = str(transaction or "").lower()
    return sub in ("CV", "BV") or any(x in text for x in ("cobrança por venda", "cobrança por vender", "cargo por venta", "tarifa de venda", "sale fee", "selling fee", "comissão", "comision", "commission"))


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

    if not entries:
        return {"sale_fee": None, "other_cost": None, "bonus": 0.0, "status": "missing", "diagnostic": "Venda ainda não localizada no Billing sincronizado."}

    # sale_fee.net é a fonte mais direta para MLB e pode se repetir em vários
    # lançamentos da mesma order. Não somamos: preservamos os valores únicos e
    # usamos o maior líquido positivo quando a API repete a composição.
    net_values = sorted({round(abs(float(v)), 2) for v in (_money_number(e.get("sale_fee_net")) for e in entries) if v is not None and abs(v) > 0})
    fee_charge = 0.0; fee_credit = 0.0
    other_charge = 0.0; other_credit = 0.0; bonus = 0.0
    labels: list[str] = []
    for e in entries:
        amount = _money_number(e.get("detail_amount"))
        if amount is None or abs(amount) <= 0:
            continue
        amount = abs(amount)
        sub = str(e.get("detail_sub_type") or "").upper()
        trans = str(e.get("transaction_detail") or "")
        dtype = str(e.get("detail_type") or "").upper()
        credit = bool(int(e.get("is_credit") or 0)) or dtype in ("BONUS", "BONIFICATION", "CREDIT")
        if trans and trans not in labels:
            labels.append(trans)
        if _ml_billing_v1513_is_shipping(sub, trans, str(e.get("raw_json") or "")):
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
    return {"sale_fee": round(fee, 2) if fee is not None else None,
            "other_cost": round(other, 2), "bonus": round(bonus, 2), "status": status,
            "diagnostic": diagnostic, "source": fee_source, "entries": len(entries)}


def _ml_billing_v1513_sync_for_records(records: list[dict[str, Any]], seller_id: str, force: bool = False) -> dict[str, Any]:
    periods = sorted({_ml_billing_month_key(str(r.get("date") or "")) for r in records if r.get("order_id")})
    results: list[dict[str, Any]] = []
    for period in periods:
        for doc in ("BILL", "CREDIT_NOTE"):
            try:
                results.append(_ml_billing_v1513_sync_period(period, doc, seller_id, force=force))
            except Exception as exc:
                results.append({"ok": False, "period_key": period, "document_type": doc, "status": "error", "diagnostic": str(exc)[:700]})
    total = sum(int(r.get("item_count") or 0) for r in results if not r.get("cached"))
    errors = [r for r in results if str(r.get("status") or "") == "error"]
    return {"periods": periods, "results": results, "new_items": total, "errors": len(errors), "ok": not errors}


def _ml_billing_v1513_meta(periods: list[str]) -> dict[str, Any]:
    if not periods:
        return {"last_sync": "", "periods": [], "launches": 0, "status": "sem dados"}
    ph = ",".join("?" for _ in periods)
    with DB_LOCK:
        states = DB.execute(f"SELECT * FROM ml_billing_period_sync WHERE period_key IN ({ph}) ORDER BY synced_at_epoch DESC", tuple(periods)).fetchall()
        count = DB.execute(f"SELECT COUNT(*) c FROM ml_billing_period_detail WHERE period_key IN ({ph})", tuple(periods)).fetchone()[0]
    states = [dict(x) for x in states]
    return {"last_sync": states[0].get("synced_at") if states else "", "periods": periods,
            "launches": int(count or 0), "status": "erro" if any(x.get("status")=="error" for x in states) else ("parcial" if any(x.get("status")=="partial" for x in states) else "ok"),
            "details": states}



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

def _ml_profitability_local_sales(limit: int = 300) -> list[dict[str, Any]]:
    """Monta as vendas locais para a rentabilidade, preservando kits como uma venda única.

    Na conferência, um SKU-kit do Mercado Livre é expandido em componentes para separação.
    Para rentabilidade isso não pode virar várias vendas: usamos SkuKitOrigem para reagrupar
    os componentes e somar seus custos Apollo, enquanto venda/tarifa/frete ficam uma vez só.
    """
    source_rows: list[dict[str, Any]] = []
    raw, _, _ = get_storage("conf_pedidos")
    source_rows.extend(_rows_from_storage_value(raw))
    with DB_LOCK:
        hist = DB.execute(
            "SELECT payload,completed_at FROM order_completion_history ORDER BY id DESC LIMIT 900"
        ).fetchall()
    for h in hist:
        try:
            payload = json.loads(h["payload"] or "[]")
        except Exception:
            continue
        rows = payload if isinstance(payload, list) else [payload]
        source_rows.extend([r for r in rows if isinstance(r, dict)])

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    seen_component_uid: set[str] = set()
    for r in source_rows:
        if str(r.get("Marketplace") or "").upper() != "MERCADO LIVRE" and not r.get("VendaMercadoLivre"):
            continue
        oid = str(r.get("VendaMercadoLivre") or "").strip()
        if not oid:
            continue
        component_sku = str(r.get("SKU") or "").strip()
        is_kit = str(r.get("Kit") or "").strip().upper() == "SIM" and bool(str(r.get("SkuKitOrigem") or "").strip())
        kit_sku = str(r.get("SkuKitOrigem") or "").strip() if is_kit else ""
        logical_sku = kit_sku or component_sku
        key = (oid, logical_sku)
        if key not in grouped:
            grouped[key] = {
                "order_id": oid,
                "pack_id": str(r.get("PackIdMercadoLivre") or r.get("PackId") or ""),
                "sku": logical_sku,
                "product": str((r.get("Nome do Kit") if is_kit else r.get("Descrição")) or r.get("Descrição") or logical_sku),
                "client": str(r.get("Cliente") or ""),
                "date": str(r.get("Data do Pedido") or ""),
                "shipment_id": str(r.get("ShipmentIdMercadoLivre") or ""),
                "qty": 0,
                "sale_amount_api": None,
                "order_summary": None,
                "is_kit": bool(is_kit),
                "kit_sku": kit_sku,
                "kit_name": str(r.get("Nome do Kit") or "") if is_kit else "",
                "kit_components": [],
                "status": str(r.get("StatusMercadoLivre") or r.get("Status ML") or r.get("Status do Pedido") or r.get("Status") or "").strip().lower(),
            }
        g = grouped[key]
        if is_kit:
            g["is_kit"] = True
            if str(r.get("Nome do Kit") or "").strip():
                g["kit_name"] = str(r.get("Nome do Kit") or "").strip()
                g["product"] = g["kit_name"]
            try:
                kits_sold = max(1, int(float(r.get("Quantidade de Kits") or 1)))
            except Exception:
                kits_sold = 1
            # Todos os componentes do mesmo kit carregam Quantidade de Kits; não somar.
            g["qty"] = max(int(g.get("qty") or 0), kits_sold)
            try:
                component_total_qty = max(1, int(float(r.get("Quantidade") or 1)))
            except Exception:
                component_total_qty = 1
            try:
                qty_per_kit = max(1, int(float(r.get("Quantidade por Kit") or 0)))
            except Exception:
                qty_per_kit = max(1, round(component_total_qty / max(1, kits_sold)))
            uid = f"{oid}|{logical_sku}|{normalize(component_sku)}|{qty_per_kit}"
            if uid not in seen_component_uid:
                seen_component_uid.add(uid)
                g["kit_components"].append({
                    "sku": component_sku,
                    "qty_per_kit": qty_per_kit,
                    "total_qty": component_total_qty,
                    "product": str(r.get("Descrição") or ""),
                })
        else:
            try:
                g["qty"] += max(1, int(float(r.get("Quantidade") or 1)))
            except Exception:
                g["qty"] += 1
    return list(grouped.values())[:max(1, int(limit))]



def _ml_profitability_resolve_order(rec: dict[str, Any], cache: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Resolve a order de rentabilidade por order, pack e busca q.

    Pedidos locais antigos podem carregar IDs migrados/pack em campos diferentes. Esta
    rotina nunca estima valores: apenas encontra a order oficial correspondente e informa
    o diagnóstico usado para a conciliação.
    """
    oid = str(rec.get("order_id") or "").strip()
    pid = str(rec.get("pack_id") or "").strip()
    sku = normalize(str(rec.get("sku") or ""))
    tried: list[str] = []

    def choose(order: Any) -> dict[str, Any] | None:
        if not isinstance(order, dict) or not order.get("id"):
            return None
        return order

    if oid:
        if oid in cache:
            return cache[oid], "order cache"
        try:
            tried.append(f"GET /orders/{oid}")
            order = choose(_ml_profitability_api_get(f"/orders/{quote(oid)}", attempts=4))
            if order:
                cache[oid] = order
                return order, "order direta"
        except Exception:
            pass

    # q aceita ID de order. É um fallback útil quando o GET direto sofre falha transitória.
    if oid:
        try:
            _, seller_id = ml_access_token()
            q = urlencode({"seller": seller_id, "q": oid, "limit": "50", "offset": "0"})
            tried.append("orders/search q")
            found = ml_api_get(f"/orders/search?{q}")
            results = found.get("results") if isinstance(found, dict) else []
            for item in results or []:
                order = choose(item)
                if not order:
                    continue
                item_oid = str(order.get("id") or "").strip()
                if item_oid == oid:
                    cache[item_oid] = order
                    return order, "orders/search q"
        except Exception:
            pass

    # Quando o identificador local corresponde a um pack, resolve as orders oficiais do pack.
    pack_candidates = [x for x in (pid, oid) if x]
    seen_pack: set[str] = set()
    for pack in pack_candidates:
        if pack in seen_pack:
            continue
        seen_pack.add(pack)
        try:
            tried.append(f"GET /packs/{pack}")
            _, _, order_ids = _ml_pack_shipment(pack)
        except Exception:
            order_ids = []
        chosen_fallback = None
        for real_oid in order_ids:
            try:
                order = cache.get(real_oid) or choose(_ml_profitability_api_get(f"/orders/{quote(real_oid)}", attempts=3))
            except Exception:
                order = None
            if not order:
                continue
            cache[real_oid] = order
            if chosen_fallback is None:
                chosen_fallback = order
            items = order.get("order_items") if isinstance(order.get("order_items"), list) else []
            if sku:
                for oi in items:
                    if isinstance(oi, dict) and normalize(str(_ml_order_item_sku(oi) or "")) == sku:
                        return order, f"pack {pack} → order {real_oid}"
        if chosen_fallback and len(order_ids) == 1:
            return chosen_fallback, f"pack {pack} → única order"

    return None, ("; ".join(tried) if tried else "order não localizada")

def ml_profitability_payload(refresh: bool = False, limit: int = 2000, days: int = 30, date_from: str = "", date_to: str = "", refresh_offset: int = 0, refresh_batch: int = 40, tax_percent: float = 12.0, retry_pending: bool = False, retry_orders: str = "", status_filter: str = "valid") -> dict[str, Any]:
    """Dashboard de lucro ML com vendas buscadas diretamente do marketplace.

    O carregamento normal prioriza velocidade: lista as vendas e usa snapshots já
    existentes. O botão Atualizar custos consulta Apollo e endpoints de custo do ML.
    Valores ausentes permanecem nulos; nunca são estimados.
    """
    try:
        tax_percent = max(0.0, min(100.0, float(tax_percent or 0.0)))
    except Exception:
        tax_percent = 12.0
    tax_rate = tax_percent / 100.0

    api_error = ""
    try:
        source = _ml_profitability_recent_sales(days=days, limit=limit, date_from=date_from, date_to=date_to, status_filter=status_filter)
    except Exception as exc:
        source = []
        api_error = str(exc)

    # A API do Mercado Livre é a fonte de verdade para período/status.
    # Dados locais servem apenas para enriquecer orders que a API realmente retornou
    # (kits, nome, SKU) ou como fallback quando a API estiver indisponível no filtro padrão.
    local_all = _ml_profitability_local_sales(limit=max(limit, 300))
    source_order_ids = {str(r.get("order_id") or "").strip() for r in source if str(r.get("order_id") or "").strip()}
    def _local_date_iso(rec: dict[str, Any]) -> str:
        raw = str(rec.get("date") or "").strip()[:10]
        if not raw: return ""
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try: return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except Exception: pass
        return raw if re.match(r"^\d{4}-\d{2}-\d{2}$", raw) else ""
    def _local_in_period(rec: dict[str, Any]) -> bool:
        d = _local_date_iso(rec)
        if date_from and d and d < date_from: return False
        if date_to and d and d > date_to: return False
        return bool(d or not (date_from or date_to))
    def _local_status_ok(rec: dict[str, Any]) -> bool:
        filt = str(status_filter or "valid").lower()
        st = str(rec.get("status") or "").lower()
        if filt == "all": return True
        if filt == "valid": return (not st) or st in ("paid", "pago", "aprovado")
        if not st: return False
        aliases = {"cancelled": ("cancelled","canceled","cancelado"), "paid": ("paid","pago","aprovado"), "partially_refunded": ("partially_refunded","reembolso parcial")}
        return st in aliases.get(filt, (filt,))
    if source:
        # Dados locais apenas enriquecem a MESMA linha oficialmente retornada pela API.
        # Não é permitido criar SKU/linha adicional a partir do CRM/histórico.
        source_keys = {(str(r.get("order_id") or "").strip(), normalize(str(r.get("sku") or ""))) for r in source}
        local = [r for r in local_all if (str(r.get("order_id") or "").strip(), normalize(str(r.get("sku") or ""))) in source_keys]
        merge_records = source + local
    else:
        # V16.17: não usar conf_pedidos/histórico como fonte de faturamento.
        # Esses registros podem representar despacho/conferência de outro dia.
        # Se a API oficial não confirmar venda no período, a tela permanece sem vendas.
        local = []
        merge_records = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in merge_records:
        oid = str(rec.get("order_id") or "").strip()
        sku = str(rec.get("sku") or "").strip()
        key = (oid, sku)
        if not oid:
            continue
        if key not in by_key:
            by_key[key] = dict(rec)
        else:
            dst = by_key[key]
            for fld in ("product", "client", "date", "shipment_id", "pack_id", "status"):
                if not dst.get(fld) and rec.get(fld):
                    dst[fld] = rec.get(fld)
            if not dst.get("qty") and rec.get("qty"):
                dst["qty"] = rec.get("qty")
            if dst.get("sale_amount_api") is None and rec.get("sale_amount_api") is not None:
                dst["sale_amount_api"] = rec.get("sale_amount_api")
            if not dst.get("order_summary") and rec.get("order_summary"):
                dst["order_summary"] = rec.get("order_summary")
            # Metadados locais do kit complementam a linha oficial do /orders/search.
            if rec.get("is_kit"):
                dst["is_kit"] = True
                dst["kit_sku"] = rec.get("kit_sku") or dst.get("kit_sku") or dst.get("sku")
                dst["kit_name"] = rec.get("kit_name") or dst.get("kit_name") or dst.get("product")
                if rec.get("kit_components"):
                    dst["kit_components"] = rec.get("kit_components")
                if rec.get("qty"):
                    dst["qty"] = rec.get("qty")

    records = list(by_key.values())
    records.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    records = records[:max(1, int(limit))]

    rows: list[dict[str, Any]] = []
    kit_catalog_profit = _load_kit_catalog()
    order_cache: dict[str, dict[str, Any]] = {}
    apollo_cost_cache: dict[str, float | None] = {}
    shipping_total_cache: dict[str, float | None] = {}
    shipment_sale_totals: dict[str, float] = {}
    shipment_line_counts: dict[str, int] = {}
    for _rec in records:
        _sid = str(_rec.get("shipment_id") or "").strip()
        if not _sid:
            continue
        shipment_line_counts[_sid] = shipment_line_counts.get(_sid, 0) + 1
        _v = _money_number(_rec.get("sale_amount_api"))
        if _v is not None:
            shipment_sale_totals[_sid] = shipment_sale_totals.get(_sid, 0.0) + max(0.0, _v)
    # Atualização em lotes: a tela chama este endpoint repetidamente. Cada chamada
    # processa somente uma fatia, evitando requests gigantes e timeout do proxy/nginx.
    refresh_offset = max(0, int(refresh_offset or 0))
    refresh_batch = max(10, min(100, int(refresh_batch or 40)))
    refresh_pool = records
    retry_order_set = {x.strip() for x in str(retry_orders or "").split(",") if x.strip()}
    if refresh and retry_order_set:
        refresh_pool = [r for r in records if str(r.get("order_id") or "").strip() in retry_order_set]
    elif refresh and retry_pending:
        pending_pool: list[dict[str, Any]] = []
        for _pr in records:
            _poid = str(_pr.get("order_id") or "")
            _psku = str(_pr.get("sku") or "")
            with DB_LOCK:
                _prow = DB.execute("SELECT unit_cost,sale_amount,ml_fee,seller_shipping_cost FROM ml_profitability_snapshot WHERE order_id=? AND sku=?", (_poid, _psku)).fetchone()
            _pd = dict(_prow) if _prow else {}
            _sale = _money_number(_pd.get("sale_amount"))
            if _sale is None:
                _sale = _money_number(_pr.get("sale_amount_api"))
            _fee = _money_number(_pd.get("ml_fee"))
            _ship = _money_number(_pd.get("seller_shipping_cost"))
            _unit = _money_number(_pd.get("unit_cost"))
            if _sale is None or _unit is None or _fee is None or _fee <= 0 or _ship is None:
                pending_pool.append(_pr)
        refresh_pool = pending_pool
    refresh_records = refresh_pool[refresh_offset:refresh_offset + refresh_batch] if refresh else []
    refresh_keys = {(str(r.get("order_id") or ""), str(r.get("sku") or "")) for r in refresh_records}

    billing_map: dict[str, dict[str, Any]] = {}
    # Primeiro usa a conciliação persistida do Billing completo do período.
    for _r in records:
        _oid = str(_r.get("order_id") or "").strip()
        if not _oid: continue
        _con = _ml_billing_v1513_consolidate(_oid, str(_r.get("pack_id") or ""))
        if str(_con.get("status") or "") != "missing":
            billing_map[_oid] = _con
        else:
            _cached = _ml_billing_cached(_oid)
            if _cached: billing_map[_oid] = _cached

    billing_sync_meta: dict[str, Any] = {}
    if refresh and refresh_records:
        try:
            _, _seller_billing = ml_access_token()
            # O primeiro lote força o mês atual selecionado; lotes seguintes usam TTL
            # e só baixam períodos ainda não sincronizados.
            billing_sync_meta = _ml_billing_v1513_sync_for_records(
                refresh_records, str(_seller_billing or ""), force=(refresh_offset == 0)
            )
            for _r in refresh_records:
                _oid = str(_r.get("order_id") or "").strip()
                if _oid:
                    billing_map[_oid] = _ml_billing_v1513_consolidate(_oid, str(_r.get("pack_id") or ""))
        except Exception as _exc:
            billing_sync_meta = {"ok": False, "errors": 1, "diagnostic": str(_exc)[:700]}

    for rec in records:
        oid, sku = rec["order_id"], rec.get("sku") or ""
        with DB_LOCK:
            snap_row = DB.execute(
                "SELECT * FROM ml_profitability_snapshot WHERE order_id=? AND sku=?", (oid, sku)
            ).fetchone()
        snap = dict(snap_row) if snap_row else {}

        # Sempre podemos usar o valor da linha devolvido pelo próprio /orders/search
        # sem fazer chamadas adicionais.
        # O valor oficial da order atual sempre prevalece sobre snapshots antigos.
        # Isso corrige caches gravados antes do ajuste de descontos/total_amount.
        if rec.get("sale_amount_api") is not None:
            snap["sale_amount"] = rec.get("sale_amount_api")
        if snap.get("ml_fee") is None and rec.get("line_fee_api") is not None:
            snap["ml_fee"] = rec.get("line_fee_api")

        do_refresh = refresh and (str(oid), str(sku)) in refresh_keys
        if do_refresh:
            order = rec.get("order_summary") if isinstance(rec.get("order_summary"), dict) else None
            order_resolution = "orders/search" if order is not None else ""
            if order is None:
                order, order_resolution = _ml_profitability_resolve_order(rec, order_cache)

            # Se a linha oficial chegou sem SkuKitOrigem/expansão local, tenta
            # reconhecer diretamente pelo Cadastro de Kits. Isso cobre SKUs do ML
            # com espaços/hífens/pontos diferentes do cadastro.
            if not rec.get("kit_components"):
                kit_hit = _kit_catalog_lookup(kit_catalog_profit, rec.get("kit_sku") or rec.get("sku"))
                # Alguns anúncios usam o próprio SKU para indicar quantidade do kit
                # (ex.: KIT3-KS001 = 3 unidades de KS001). Só aplica quando não há
                # composição explícita no Cadastro de Kits.
                if not kit_hit:
                    kit_hit = _infer_quantity_kit_from_sku(rec.get("kit_sku") or rec.get("sku"))
                if not kit_hit and isinstance(order, dict):
                    for oi in (order.get("order_items") or []):
                        if not isinstance(oi, dict):
                            continue
                        candidate = _ml_order_item_sku(oi)
                        kit_hit = _kit_catalog_lookup(kit_catalog_profit, candidate)
                        if not kit_hit:
                            kit_hit = _infer_quantity_kit_from_sku(candidate)
                        if kit_hit:
                            break
                if kit_hit and kit_hit.get("ativo", True):
                    rec["is_kit"] = True
                    rec["kit_sku"] = str(kit_hit.get("sku_kit") or rec.get("sku") or "").strip()
                    rec["kit_name"] = str(kit_hit.get("nome") or rec.get("product") or rec.get("kit_sku") or "").strip()
                    rec["product"] = rec.get("kit_name") or rec.get("product")
                    rec["kit_components"] = [
                        {
                            "sku": str(comp.get("sku") or "").strip(),
                            "qty_per_kit": max(1, int(comp.get("quantidade") or 1)),
                            "total_qty": max(1, int(comp.get("quantidade") or 1)) * max(1, int(float(rec.get("qty") or 1))),
                            "product": "",
                        }
                        for comp in (kit_hit.get("componentes") or [])
                        if str(comp.get("sku") or "").strip()
                    ]

            unit_cost = _money_number(snap.get("unit_cost"))
            cost_source = str(snap.get("cost_source") or "")
            kit_cost_missing: list[str] = []
            if rec.get("is_kit") and rec.get("kit_components"):
                kit_unit_total = 0.0
                kit_ok = True
                for comp in rec.get("kit_components") or []:
                    csku = str(comp.get("sku") or "").strip()
                    if not csku:
                        continue
                    cache_key = normalize(csku)
                    if cache_key not in apollo_cost_cache:
                        try:
                            cc = crm_cost_lookup(csku, "rentabilidade-ml-kit")
                            apollo_cost_cache[cache_key] = _money_number(cc.get("custo_medio"))
                        except Exception:
                            apollo_cost_cache[cache_key] = None
                    cunit = apollo_cost_cache.get(cache_key)
                    if cunit is None:
                        kit_ok = False
                        kit_cost_missing.append(csku)
                        continue
                    try:
                        per_kit = max(1, int(float(comp.get("qty_per_kit") or 1)))
                    except Exception:
                        per_kit = 1
                    kit_unit_total += cunit * per_kit
                if kit_ok and kit_unit_total > 0:
                    unit_cost = round(kit_unit_total, 4)
                    cost_source = "Apollo · componentes do kit"
            elif unit_cost is None and sku:
                try:
                    cache_key = normalize(sku)
                    if cache_key not in apollo_cost_cache:
                        c = crm_cost_lookup(sku, "rentabilidade-ml")
                        apollo_cost_cache[cache_key] = _money_number(c.get("custo_medio"))
                    unit_cost = apollo_cost_cache.get(cache_key)
                    cost_source = "Apollo" if unit_cost is not None else cost_source
                except Exception:
                    pass

            sale_amount = _money_number(rec.get("sale_amount_api"))
            ml_fee = _money_number(snap.get("ml_fee"))
            billing = billing_map.get(str(oid)) or {}
            billing_fee = _money_number(billing.get("sale_fee"))
            billing_other = _money_number(billing.get("other_cost"))
            billing_status = str(billing.get("status") or "")
            billing_diag = str(billing.get("diagnostic") or "")
            # Billing é a fonte de conciliação financeira. Orders permanece apenas como fallback.
            if billing_fee is not None:
                ml_fee = billing_fee
            # Em refresh o frete é recalculado pelo shipment para corrigir snapshots
            # antigos que podiam repetir o custo total em cada item da mesma venda.
            ship_cost = None
            if isinstance(order, dict):
                # Recalcula valor e tarifa da linha usando o item oficial da order.
                # Para kit expandido, sku é SkuKitOrigem e volta a casar com o SKU vendido no ML.
                detailed_items = order.get("order_items") if isinstance(order.get("order_items"), list) else []
                matching = []
                for doi in detailed_items:
                    if not isinstance(doi, dict):
                        continue
                    dsku = str(_ml_order_item_sku(doi) or "").strip()
                    if sku and normalize(dsku) == normalize(sku):
                        matching.append(doi)
                # Pedidos legados podem ter SKU local diferente do SKU retornado pela API;
                # se a order possui uma única linha, ela é inequivocamente a venda correta.
                if not matching and len(detailed_items) == 1:
                    matching = [detailed_items[0]]
                if sale_amount is None and len(matching) == 1:
                    _up = _money_number(matching[0].get("unit_price"))
                    try:
                        _mq = max(1, int(float(matching[0].get("quantity") or 1)))
                    except Exception:
                        _mq = 1
                    if _up is not None:
                        sale_amount = _up * _mq
                # Só usa total da order quando ela realmente possui uma única linha;
                # evita repetir o faturamento inteiro em cada item de venda múltipla.
                if sale_amount is None and len(detailed_items) <= 1:
                    sale_amount = _ml_order_effective_sale(order)[0]
                if billing_fee is None and len(matching) == 1:
                    detailed_fee = _ml_item_fee(matching[0])
                    if detailed_fee is not None:
                        ml_fee = detailed_fee
                if billing_fee is None and ml_fee is None:
                    total_fee = _ml_order_marketplace_fee(order)
                    if total_fee is not None:
                        # Para order de uma linha a tarifa total pertence integralmente à linha.
                        if len(detailed_items) <= 1:
                            ml_fee = total_fee
                        else:
                            order_total = sum((_money_number(x.get("unit_price")) or 0) * max(1, int(float(x.get("quantity") or 1))) for x in detailed_items if isinstance(x, dict))
                            if sale_amount is not None and order_total > 0:
                                ml_fee = total_fee * (sale_amount / order_total)
                if not rec.get("date"):
                    rec["date"] = (_ml_order_effective_sale(order)[1].isoformat() if _ml_order_effective_sale(order)[1] else str(order.get("date_closed") or ""))
                if not rec.get("client"):
                    rec["client"] = _ml_billing_name(order)
                if not rec.get("shipment_id"):
                    sh = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
                    rec["shipment_id"] = str(sh.get("id") or "")
            if rec.get("shipment_id"):
                sid = str(rec.get("shipment_id"))
                if sid not in shipping_total_cache:
                    try:
                        _, seller_id_for_cost = ml_access_token()
                    except Exception:
                        seller_id_for_cost = ""
                    shipping_total_cache[sid] = _ml_seller_shipping_cost(sid, str(seller_id_for_cost or ""))
                shipping_total = shipping_total_cache.get(sid)
                if shipping_total is not None:
                    line_sale = _money_number(sale_amount)
                    group_sale = shipment_sale_totals.get(sid, 0.0)
                    if line_sale is not None and group_sale > 0:
                        ship_cost = shipping_total * max(0.0, line_sale) / group_sale
                    else:
                        ship_cost = shipping_total / max(1, shipment_line_counts.get(sid, 1))

            captured = now_iso()
            with DB_LOCK:
                DB.execute(
                    """INSERT INTO ml_profitability_snapshot
                    (order_id,sku,unit_cost,cost_source,sale_amount,ml_fee,seller_shipping_cost,other_ml_cost,captured_at,raw_summary)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id,sku) DO UPDATE SET
                    unit_cost=CASE WHEN excluded.unit_cost IS NOT NULL THEN excluded.unit_cost ELSE ml_profitability_snapshot.unit_cost END,
                    cost_source=CASE WHEN excluded.unit_cost IS NOT NULL THEN excluded.cost_source ELSE ml_profitability_snapshot.cost_source END,
                    sale_amount=COALESCE(excluded.sale_amount,ml_profitability_snapshot.sale_amount),
                    ml_fee=COALESCE(excluded.ml_fee,ml_profitability_snapshot.ml_fee),
                    seller_shipping_cost=COALESCE(excluded.seller_shipping_cost,ml_profitability_snapshot.seller_shipping_cost),
                    other_ml_cost=COALESCE(excluded.other_ml_cost,ml_profitability_snapshot.other_ml_cost),
                    captured_at=excluded.captured_at,raw_summary=excluded.raw_summary""",
                    (oid, sku, unit_cost, cost_source, sale_amount, ml_fee, ship_cost,
                     billing_other if billing_other is not None else _money_number(snap.get("other_ml_cost")), captured,
                     json.dumps({"billing_status": billing_status, "billing_diagnostic": billing_diag, "billing_source": "billing/period/details + order/details fallback" if billing else "", "order_resolution": order_resolution, "order_resolved": bool(order)}, ensure_ascii=False)),
                )
                DB.commit()
                snap_row = DB.execute(
                    "SELECT * FROM ml_profitability_snapshot WHERE order_id=? AND sku=?", (oid, sku)
                ).fetchone()
                snap = dict(snap_row) if snap_row else snap

        qty = rec.get("qty") or 1
        try:
            qty = max(1, int(float(qty)))
        except Exception:
            qty = 1
        unit = _money_number(snap.get("unit_cost"))
        product_cost = unit * qty if unit is not None else None
        sale = _money_number(rec.get("sale_amount_api"))
        if sale is None:
            sale = _money_number(snap.get("sale_amount"))
        fee = _money_number(snap.get("ml_fee"))
        shipping = _money_number(snap.get("seller_shipping_cost"))
        other = _money_number(snap.get("other_ml_cost"))
        tax_amount = (sale * tax_rate) if sale is not None else None
        known_costs = sum(v for v in (product_cost, fee, shipping, other, tax_amount) if v is not None)
        billing = billing_map.get(str(oid)) or {}
        billing_status = str(billing.get("status") or "")
        billing_diag = str(billing.get("diagnostic") or "")
        # Zero pode ser um valor oficial; ausência deve permanecer None.
        fee_confirmed = billing_status == "ok" or fee is not None
        complete = sale is not None and product_cost is not None and fee_confirmed and shipping is not None
        pending_reasons: list[str] = []
        if sale is None: pending_reasons.append("valor da venda não localizado na Order")
        if product_cost is None: pending_reasons.append("custo Apollo não localizado")
        if not fee_confirmed: pending_reasons.append("tarifa ML não conciliada")
        if shipping is None: pending_reasons.append("frete do vendedor não conciliado")
        if billing_diag and not complete and "não retornou" in billing_diag.lower(): pending_reasons.append("Billing ainda sem lançamento para a order/pack")
        profit = (sale - known_costs) if sale is not None and product_cost is not None else None
        margin = (profit / sale * 100) if profit is not None and sale else None
        rows.append({
            "order_id": oid,
            "pack_id": str(rec.get("pack_id") or ""),
            "sku": sku,
            "product": str(rec.get("product") or ""),
            "client": str(rec.get("client") or ""),
            "status": str(rec.get("status") or ""),
            "date": str(rec.get("date") or ""),
            "shipment_id": str(rec.get("shipment_id") or ""),
            "qty": qty,
            "unit_cost": unit,
            "product_cost": product_cost,
            "sale_amount": sale,
            "ml_fee": fee,
            "shipping_cost": shipping,
            "other_ml_cost": other,
            "tax_percent": tax_percent,
            "tax_amount": tax_amount,
            "profit": profit,
            "margin": margin,
            "cost_complete": complete,
            "captured_at": snap.get("captured_at") or "",
            "billing_status": billing_status,
            "billing_diagnostic": billing_diag,
            "fee_source": (str(billing.get("source") or "") if _money_number(billing.get("sale_fee")) is not None and billing.get("source") else ("Billing período ML" if _money_number(billing.get("sale_fee")) is not None else ("Orders ML" if fee is not None and fee > 0 else "Pendente de conciliação"))),
            "is_kit": bool(rec.get("is_kit")),
            "kit_sku": str(rec.get("kit_sku") or ""),
            "kit_name": str(rec.get("kit_name") or ""),
            "kit_components": rec.get("kit_components") if isinstance(rec.get("kit_components"), list) else [],
            "cost_source": str(snap.get("cost_source") or ""),
            "pending_reasons": pending_reasons,
        })

    return {
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "refreshed": refresh,
        "source": "mercadolivre" if source else "local",
        "api_warning": api_error,
        "date_from": date_from,
        "date_to": date_to,
        "refresh_offset": refresh_offset,
        "refresh_batch": refresh_batch,
        "refresh_processed": len(refresh_records) if refresh else 0,
        "refresh_total": len(refresh_pool) if refresh else len(records),
        "refresh_done": (not refresh) or (refresh_offset + len(refresh_records) >= len(refresh_pool)),
        "refresh_next_offset": (refresh_offset + len(refresh_records)) if refresh else 0,
        "retry_pending": bool(retry_pending),
        "retry_orders": sorted(retry_order_set),
        "tax_percent": tax_percent,
        "status_filter": str(status_filter or "valid"),
        "orders_count": len({str(r.get("order_id") or "") for r in rows if str(r.get("order_id") or "")}),
        "units_count": sum(int(r.get("qty") or 0) for r in rows),
        "gross_sales": round(sum((_money_number(r.get("sale_amount")) or 0.0) for r in rows), 2),
        "billing_sync": billing_sync_meta,
    }

class AppHandler(BaseHTTPRequestHandler):
    server_version = "NoromakExpedicao/16.16"

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
        if path == "/api/rentabilidade-ml":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                refresh = str((query.get("refresh") or [""])[0]).lower() in ("1", "true", "sim")
                try:
                    days = max(1, min(365, int((query.get("days") or ["30"])[0] or 30)))
                except Exception:
                    days = 30
                try:
                    limit = max(1, min(5000, int((query.get("limit") or ["2000"])[0] or 2000)))
                except Exception:
                    limit = 2000
                date_from = str((query.get("date_from") or [""])[0] or "").strip()[:10]
                date_to = str((query.get("date_to") or [""])[0] or "").strip()[:10]
                try:
                    refresh_offset = max(0, int((query.get("refresh_offset") or ["0"])[0] or 0))
                except Exception:
                    refresh_offset = 0
                try:
                    refresh_batch = max(10, min(100, int((query.get("refresh_batch") or ["40"])[0] or 40)))
                except Exception:
                    refresh_batch = 40
                try:
                    tax_percent = max(0.0, min(100.0, float((query.get("tax_percent") or ["12"])[0] or 12)))
                except Exception:
                    tax_percent = 12.0
                retry_pending = str((query.get("retry_pending") or [""])[0]).lower() in ("1", "true", "sim")
                retry_orders = str((query.get("retry_orders") or [""])[0] or "").strip()[:1200]
                status_filter = str((query.get("status_filter") or ["valid"])[0] or "valid").strip().lower()
                if status_filter not in ("valid", "paid", "partially_refunded", "cancelled", "all"):
                    status_filter = "valid"
                try:
                    self.send_json(ml_profitability_payload(refresh=refresh, limit=limit, days=days, date_from=date_from, date_to=date_to, refresh_offset=refresh_offset, refresh_batch=refresh_batch, tax_percent=tax_percent, retry_pending=retry_pending, retry_orders=retry_orders, status_filter=status_filter))
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
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_roles_get()
            return
        if path == "/api/sku-history":
            ctx = self.auth(admin_only=True)
            if ctx is not None:
                self.handle_sku_history_get(query)
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
            "/estacao-impressao": "estacao", "/estacao-impressao/": "estacao", "/estacao-impressao.html": "estacao",
            "/print-station": "estacao", "/print-station/": "estacao",
            "/precificador": "precificador", "/precificador/": "precificador", "/precificador.html": "precificador",
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
                            stock = crm_stock_lookup(sku, "analise-full")
                            rows = stock.get("estoques") if isinstance(stock.get("estoques"), list) else []
                            if filial:
                                selected = [r for r in rows if int(r.get("revenda") or 0) == filial]
                                available = sum(float(r.get("disponivel") or 0) for r in selected) if selected else None
                                known = bool(selected)
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
                                "sufficient": bool(known and float(available or 0) >= need),
                                "branches": rows,
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
                "app_role": row["role"], "app_active": True, "is_owner": is_owner_username(username),
                "permissions": permissions
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
            if key == "precificador_online_v1" and not module_allowed(ctx, "precificador"):
                self.send_json({"error": "Este usuário não possui acesso para alterar o Precificador."}, 403)
                return
            if ctx["app_role"] != "admin" and key not in OPERATOR_WRITE_KEYS:
                self.send_json({"error": f"Operador não pode alterar {key}."}, 403)
                return
            new_value = str(req.get("newValue"))
            old_value = None if "oldValue" not in req or req.get("oldValue") is None else str(req.get("oldValue"))
            force = bool(req.get("force"))
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
            "/promocoes-ml": "promocoes-ml.html",
            "/promocoes-ml/": "promocoes-ml.html",
            "/promocoes-ml.html": "promocoes-ml.html",
            "/rentabilidade-ml": "rentabilidade-ml.html",
            "/rentabilidade-ml/": "rentabilidade-ml.html",
            "/rentabilidade-ml.html": "rentabilidade-ml.html",
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


def main() -> None:
    init_db()
    backup_daily()
    watcher_stop = threading.Event()
    watcher_thread = threading.Thread(
        target=ml_pending_label_watch_loop,
        args=(watcher_stop,),
        name="ml-pending-label-watcher",
        daemon=True,
    )
    new_order_thread = threading.Thread(
        target=ml_new_order_watch_loop,
        args=(watcher_stop,),
        name="ml-new-order-watcher",
        daemon=True,
    )
    manual_reconcile_thread = threading.Thread(
        target=ml_reconcile_manual_orders_loop,
        args=(watcher_stop,),
        name="ml-manual-order-reconciler",
        daemon=True,
    )
    token_keeper_thread = threading.Thread(
        target=ml_token_keepalive_loop,
        args=(watcher_stop,),
        name="ml-token-keeper",
        daemon=True,
    )
    safety_catchup_thread = threading.Thread(
        target=ml_safety_catchup_loop,
        args=(watcher_stop,),
        name="ml-safety-catchup",
        daemon=True,
    )
    shopee_label_thread = threading.Thread(
        target=shopee_label_worker_loop,
        args=(watcher_stop,),
        name="shopee-label-worker",
        daemon=True,
    )
    _ml_worker_state_update(started_at=now_iso(), last_error="")
    watcher_thread.start()
    new_order_thread.start()
    manual_reconcile_thread.start()
    token_keeper_thread.start()
    safety_catchup_thread.start()
    shopee_label_thread.start()
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
