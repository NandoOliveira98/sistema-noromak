#!/usr/bin/env bash
# Valida o sistema ANTES de qualquer publicação.
#
#   ./testar.sh
#
# 1. Compila o backend Python (pega erro de sintaxe).
# 2. Roda a suíte automatizada das invariantes críticas em um banco temporário.
#
# Nada aqui toca o banco de produção nem faz chamada para Mercado Livre,
# Shopee ou Apollo. Sai com código != 0 se qualquer etapa falhar.

set -uo pipefail
cd "$(dirname "$0")"

encontrar_python() {
  local candidatos=(
    "$HOME/AppData/Local/Programs/Python/Python312/python.exe"
    "$HOME/AppData/Local/Programs/Python/Python313/python.exe"
    python3
    python
  )
  for candidato in "${candidatos[@]}"; do
    if "$candidato" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then
      echo "$candidato"
      return 0
    fi
  done
  return 1
}

PY="$(encontrar_python)" || {
  echo "ERRO: Python 3 nao encontrado nesta maquina."
  echo "Instale com:  winget install --id Python.Python.3.12 --scope user"
  exit 1
}

echo ">> usando: $("$PY" --version 2>&1)"
echo

echo ">> 1/2 compilando o backend..."
if ! "$PY" -m py_compile servidor_web.py; then
  echo
  echo "FALHOU: erro de sintaxe em servidor_web.py. NAO publique."
  exit 1
fi
echo "   backend compila sem erro."
echo

echo ">> 2/2 rodando a suite de invariantes criticas..."
export PYTHONIOENCODING=utf-8
if ! "$PY" -m unittest discover -s TESTES -t TESTES -p "test_*.py"; then
  echo
  echo "FALHOU: uma invariante critica quebrou. NAO publique."
  echo "Cada teste representa um problema que ja aconteceu em producao."
  exit 1
fi

echo
echo ">> TUDO VERDE. O sistema esta pronto para ser apresentado para autorizacao."
echo ">> Publicar so depois do 'pode subir'."
