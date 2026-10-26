"""Leitura SO da prateleira (localizacao) de Aracatuba no Apollo para uma lista de codigos.

Roda no servidor (python3 - CODIGO1 CODIGO2 ..., via ssh) e imprime JSON {codigo: prateleira}.
Usa a mesma rota de estoque e a mesma chave do Noromak (/home/expedicao/.env). Nao grava nada.
Fica FORA do limite de 20/min do sistema: por isso vai devagar (1 a cada 15 s) e espera 60 s no 429.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REVENDA = 1   # Aracatuba
ENV = {}
for linha in open("/home/expedicao/.env", encoding="utf-8"):
    linha = linha.strip()
    if linha and not linha.startswith("#") and "=" in linha:
        k, v = linha.split("=", 1)
        ENV[k.strip()] = v.strip().strip('"').strip("'")
CHAVE = ENV.get("CHAVE_CRM", "")
custo = ENV.get("CRM_COST_API_URL", "http://127.0.0.1:8000/api/v1/expedicao/custo").rstrip("/")
URL = ENV.get("CRM_STOCK_API_URL", "")
if not URL or URL.rstrip("/") == custo:
    URL = custo[:-len("/custo")] + "/estoque" if custo.endswith("/custo") else \
        "http://127.0.0.1:8000/api/v1/expedicao/estoque"


def ler(codigo):
    url = URL + ("&" if "?" in URL else "?") + urllib.parse.urlencode({"codigo": codigo, "operador": "separacao"})
    req = urllib.request.Request(url, headers={"X-Chave-Expedicao": CHAVE, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "replace") or "{}")
    if data.get("ok") is False:
        return "ERRO: " + str(data.get("erro") or "nao encontrado")
    for row in data.get("estoques") or []:
        try:
            rev = int(row.get("revenda") or 0)
        except (TypeError, ValueError):
            continue
        if rev == REVENDA:
            loc = str(row.get("localizacao") or "").strip()
            return loc if loc.strip(".") else ""
    return "ERRO: Aracatuba sem resposta"


saida = {}
for n, cod in enumerate(sys.argv[1:]):
    if n:
        time.sleep(15)
    for tentativa in (1, 2):
        try:
            saida[cod] = ler(cod)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and tentativa == 1:
                time.sleep(60)
                continue
            saida[cod] = "ERRO: HTTP %s" % exc.code
            break
        except Exception as exc:
            saida[cod] = "ERRO: %s" % exc
            break
    sys.stderr.write("%d/%d %s\n" % (n + 1, len(sys.argv) - 1, cod))
print(json.dumps(saida, ensure_ascii=True))
