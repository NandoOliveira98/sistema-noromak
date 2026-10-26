"""Leitura SO: todas as colunas das linhas da fila (conf_pedidos) de alguns pedidos.

Roda no servidor (python3 - PEDIDO1 PEDIDO2 ..., via ssh) e imprime JSON (so ASCII).
Banco em modo somente leitura; le uma chave so. Nao grava nada.
"""
import json
import sqlite3
import sys


def normalize(v):
    return "".join(str(v if v is not None else "").strip().upper().split())


alvo = {normalize(p) for p in sys.argv[1:]}
con = sqlite3.connect("file:/home/expedicao/dados/expedicao.db?mode=ro", uri=True, timeout=10)
row = con.execute("SELECT value FROM storage WHERE key='conf_pedidos'").fetchone()
dados = json.loads(row[0]) if row and row[0] else []
saida = [r for r in dados if isinstance(r, dict) and normalize(r.get("Pedido") or r.get("pedido")) in alvo]
print(json.dumps(saida, ensure_ascii=True))
