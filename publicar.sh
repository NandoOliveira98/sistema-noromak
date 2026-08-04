#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERRO: esta pasta ainda nao possui .git."
  echo "Execute primeiro: ./PREPARAR_GIT_UMA_VEZ.sh"
  exit 1
fi

if ! git remote get-url producao >/dev/null 2>&1; then
  echo "ERRO: o remoto 'producao' nao esta configurado."
  echo "Execute primeiro: ./PREPARAR_GIT_UMA_VEZ.sh"
  exit 1
fi

echo ">> preparando aqui na minha maquina..."
if [ -n "$(git status --porcelain)" ]; then
  echo "ERRO: existem alteracoes ainda nao salvas em commit."
  echo 'Use: git add . && git commit -m "descreva a alteracao"'
  exit 1
fi

echo ">> enviando pro servidor..."
git push producao HEAD:master

echo ">> reiniciando o sistema no servidor..."
if ! ssh expedicao "sudo -n systemctl restart expedicao"; then
  echo
  echo "ERRO: sua conta nao tem reinicio sem senha liberado para o servico expedicao."
  echo "Peca ao responsavel pelo servidor para liberar exatamente:"
  echo "  systemctl restart expedicao"
  exit 1
fi

sleep 3

echo ">> conferindo diretamente a porta 8100..."
if ! ssh expedicao "curl -fsS http://127.0.0.1:8100/expedicao/api/health"; then
  echo
  echo "ERRO: os arquivos foram enviados, mas o aplicativo nao respondeu na porta 8100."
  echo "Execute: ./verificar_servidor.sh"
  exit 1
fi
echo

echo ">> conferindo o endereco publico..."
PUBLIC_STATUS="$(curl -s -o /dev/null -w '%{http_code}' 'https://noromakweb.com.br/expedicao/')"
echo ">> resultado publico: $PUBLIC_STATUS"

if [ "$PUBLIC_STATUS" != "200" ] && [ "$PUBLIC_STATUS" != "302" ]; then
  echo "O aplicativo respondeu internamente, mas o nginx devolveu HTTP $PUBLIC_STATUS."
  exit 1
fi

echo ">> PUBLICACAO CONCLUIDA. Abra https://noromakweb.com.br/expedicao"
