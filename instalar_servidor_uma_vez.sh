#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

KEY="SEGREDOS_SERVIDOR/qz-private-key.pem"
if [ ! -f "$KEY" ]; then
  echo "ERRO: chave QZ nao encontrada em $KEY"
  exit 1
fi

echo ">> criando pastas permanentes no servidor..."
ssh expedicao "mkdir -p /home/expedicao/qz /home/expedicao/dados /home/expedicao/backups && chmod 700 /home/expedicao/qz"

echo ">> enviando a chave privada QZ fora do Git..."
scp "$KEY" expedicao:/home/expedicao/qz/qz-private-key.pem
ssh expedicao "chmod 600 /home/expedicao/qz/qz-private-key.pem"

echo ">> conferindo o servico configurado (esta consulta nao usa sudo)..."
if ssh expedicao "systemctl show expedicao -p WorkingDirectory -p ExecStart --no-pager"; then
  echo
  echo "O ExecStart correto deve executar: /home/expedicao/app/expedicao"
else
  echo
  echo "AVISO: a chave QZ foi instalada, mas sua conta nao conseguiu consultar o servico."
  echo "Isso nao desfaz a instalacao da chave. O responsavel pelo servidor pode conferir"
  echo "o arquivo expedicao.service.example."
fi

echo
echo "INSTALACAO DO SEGREDO QZ CONCLUIDA."
