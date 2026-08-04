#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

REMOTE_NAME="producao"
REMOTE_URL="expedicao:/home/expedicao/app"
REMOTE_BRANCH="master"

if ! command -v git >/dev/null 2>&1; then
  echo "ERRO: Git nao encontrado. Instale o Git for Windows e abra esta pasta pelo Git Bash."
  exit 1
fi

if ! git config user.name >/dev/null 2>&1 || ! git config user.email >/dev/null 2>&1; then
  echo "ERRO: configure seu nome e e-mail antes de continuar:"
  echo '  git config --global user.name "Seu Nome"'
  echo '  git config --global user.email "seu@email.com"'
  exit 1
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo ">> esta pasta ja e um projeto Git."
else
  echo ">> criando o projeto Git nesta pasta..."
  git init
  git branch -M "$REMOTE_BRANCH"
fi

if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  CURRENT_URL="$(git remote get-url "$REMOTE_NAME")"
  if [ "$CURRENT_URL" != "$REMOTE_URL" ]; then
    echo ">> corrigindo o endereco do servidor..."
    git remote set-url "$REMOTE_NAME" "$REMOTE_URL"
  fi
else
  echo ">> cadastrando o servidor de producao..."
  git remote add "$REMOTE_NAME" "$REMOTE_URL"
fi

# Preserva o historico que ja existe no servidor sem sobrescrever os arquivos
# desta nova versao. Depois do reset misto, o Git enxerga a nova pasta como uma
# atualizacao completa da versao anterior.
if git ls-remote --exit-code --heads "$REMOTE_NAME" "$REMOTE_BRANCH" >/dev/null 2>&1; then
  echo ">> baixando apenas o historico atual do servidor..."
  git fetch "$REMOTE_NAME" "$REMOTE_BRANCH"
  git branch -M "$REMOTE_BRANCH"
  git reset --mixed "$REMOTE_NAME/$REMOTE_BRANCH"
else
  echo ">> o servidor ainda nao possui a branch $REMOTE_BRANCH; sera criada na primeira publicacao."
fi

echo ">> preparando o primeiro commit da versao Web/Linux..."
git add .

if git diff --cached --quiet; then
  echo ">> nenhum arquivo novo para salvar. O projeto Git ja estava preparado."
else
  git commit -m "versao web linux 7.2"
fi

echo
echo "PROJETO GIT PREPARADO COM SUCESSO."
echo "Agora execute: ./publicar.sh"
