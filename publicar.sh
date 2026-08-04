#!/usr/bin/env bash
set -e
echo ">> preparando aqui na minha maquina..."
# Se seu projeto precisa ser "montado" antes (ex: npm run build),
# apague o # da linha abaixo e troque pelo comando certo.
# npm run build
echo ">> enviando pro servidor..."
git push producao "$(git branch --show-current)"
echo ">> reiniciando o sistema no servidor..."
ssh expedicao "sudo systemctl restart expedicao"
sleep 3
curl -s -o /dev/null -w ">> resultado: %{http_code}\n"
https://noromakweb.com.br/expedicao/
