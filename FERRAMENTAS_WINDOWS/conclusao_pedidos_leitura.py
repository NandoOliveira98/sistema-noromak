"""Leitura SO: quando e por quem cada pedido foi concluido na Conferencia.

Roda no servidor (python3 - PEDIDO1 PEDIDO2 ..., via ssh) e imprime JSON. Banco em modo somente
leitura; consulta por chave (indice), so as colunas pequenas. Nao grava nada.
"""
import json
import sqlite3
import sys

con = sqlite3.connect("file:/home/expedicao/dados/expedicao.db?mode=ro", uri=True, timeout=10)
saida = {}
for pedido in sys.argv[1:]:
    chave = "".join(pedido.strip().upper().split())
    hist = con.execute("SELECT completed_at, completed_by FROM order_completion_history WHERE order_key=? "
                       "ORDER BY id DESC LIMIT 3", (chave,)).fetchall()
    atual = con.execute("SELECT completed_at, completed_by FROM completed_orders WHERE order_key=?",
                        (chave,)).fetchone()
    saida[pedido] = {"concluido": list(atual) if atual else None, "historico": [list(h) for h in hist]}
print(json.dumps(saida, ensure_ascii=True))
