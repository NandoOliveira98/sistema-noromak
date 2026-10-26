"""Leitura SO do banco de producao para a lista de separacao do dia (antes da V19.85 subir).

Roda no servidor (python3 -, via ssh) e imprime JSON (so ASCII: o PowerShell 5.1 estraga acento
no pipe). Abre o banco em modo somente leitura, le UMA chave (conf_pedidos), os concluidos
daqueles pedidos e as prateleiras de Aracatuba guardadas pela ultima remessa Full (V19.07).
Nao chama o Apollo nem grava nada.
"""
import json
import sqlite3

DB = "file:/home/expedicao/dados/expedicao.db?mode=ro"
DESCRICAO = "Descri" + chr(231) + chr(227) + "o"


def normalize(v):
    return "".join(str(v if v is not None else "").strip().upper().split())


con = sqlite3.connect(DB, uri=True, timeout=10)
row = con.execute("SELECT value FROM storage WHERE key='conf_pedidos'").fetchone()
dados = json.loads(row[0]) if row and row[0] else []
linhas = [r for r in dados if isinstance(r, dict)] if isinstance(dados, list) else []
pedidos = sorted({normalize(r.get("Pedido") or r.get("pedido")) for r in linhas} - {""})
concluidos = set()
for i in range(0, len(pedidos), 500):
    lote = pedidos[i:i + 500]
    q = "SELECT order_key FROM completed_orders WHERE order_key IN (%s)" % ",".join("?" * len(lote))
    concluidos |= {k for (k,) in con.execute(q, lote)}
loc = con.execute("SELECT locais_json FROM full_remessa_v1903 WHERE locais_json != '{}' "
                  "ORDER BY remessa_id DESC LIMIT 1").fetchone()
saida = {"locais": json.loads(loc[0]) if loc else {}, "linhas": []}
for r in linhas:
    if normalize(r.get("Pedido") or r.get("pedido")) in concluidos:
        continue
    item = {k: r.get(k) for k in ("Pedido", "SKU", "Quantidade", "Marketplace", "Origem", "Casa")}
    item["Descricao"] = r.get(DESCRICAO) or r.get("Descricao") or ""
    saida["linhas"].append(item)
print(json.dumps(saida, ensure_ascii=True))
