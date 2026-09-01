#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo ">> Instalando/atualizando servico Noromak 24/7..."
sudo cp expedicao.service.example /etc/systemd/system/expedicao.service
sudo systemctl daemon-reload
sudo systemctl enable expedicao
sudo systemctl restart expedicao
sleep 2

echo ">> enabled: $(systemctl is-enabled expedicao || true)"
echo ">> active:  $(systemctl is-active expedicao || true)"
echo ">> health:"
curl -fsS http://127.0.0.1:8100/expedicao/api/health || true
echo
