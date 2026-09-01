#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

echo "=== SERVICO 24/7 ==="
ssh expedicao "printf 'enabled: '; systemctl is-enabled expedicao || true; printf 'active: '; systemctl is-active expedicao || true; systemctl show expedicao -p WorkingDirectory -p ExecStart -p ActiveEnterTimestamp -p NRestarts --no-pager" || true

echo
echo "=== STATUS DO SERVICO ==="
ssh expedicao "systemctl status expedicao --no-pager -l" || true

echo
echo "=== ULTIMAS MENSAGENS ==="
ssh expedicao "sudo -n journalctl -u expedicao -n 100 --no-pager" || \
  ssh expedicao "journalctl -u expedicao -n 100 --no-pager" || true

echo
echo "=== HEALTH INTERNO ==="
ssh expedicao "curl -fsS http://127.0.0.1:8100/expedicao/api/health" || true
echo

echo "=== HEALTH PUBLICO ==="
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' 'https://noromakweb.com.br/expedicao/api/health' || true
