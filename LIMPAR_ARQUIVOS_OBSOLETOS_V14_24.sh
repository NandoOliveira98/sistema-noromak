#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Somente documentação histórica/caches que não participam do runtime.
rm -f ALTERACOES_*.txt ATUALIZAR_V*.txt INSTRUCOES_DEPLOY_11_*.txt CORRECAO_*_7_*.txt
rm -rf __pycache__
find . -type f -name '*.pyc' -delete
printf 'Limpeza concluída. Arquivos de runtime, configuração, migração, Zebra/QZ e extensões foram preservados.\n'
