#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

echo "=== CONFIGURACAO DO SERVICO ==="
ssh expedicao "systemctl show expedicao -p WorkingDirectory -p ExecStart -p ActiveState -p SubState --no-pager" || true

echo
echo "=== STATUS DO SERVICO ==="
ssh expedicao "systemctl status expedicao --no-pager -l" || true

echo
echo "=== ULTIMAS MENSAGENS (pode exigir permissao do responsavel) ==="
ssh expedicao "sudo -n journalctl -u expedicao -n 80 --no-pager" || \
  ssh expedicao "journalctl -u expedicao -n 80 --no-pager" || true

echo
echo "=== HEALTH INTERNO ==="
ssh expedicao "curl -i http://127.0.0.1:8100/expedicao/api/health" || true
