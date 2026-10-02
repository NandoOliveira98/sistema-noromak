#!/usr/bin/env bash
# V18.63: volta a PRODUCAO para a versao publicada antes da atual.
#
#   ./voltar_versao.sh            volta para o deploy OK anterior (deploys.log)
#   ./voltar_versao.sh <commit>   volta para um commit especifico
#
# Para quando a versao nova SUBIU (o publicar.sh nao reverteu sozinho), mas tem um
# bug de comportamento. Volta so o CODIGO; o banco continua como esta. Se o banco
# tambem precisar voltar, o backup de antes de cada deploy fica em
# /home/expedicao/backups/expedicao_predeploy_*.db.gz.
#
# E uma acao em producao: como o publicar.sh, so com autorizacao do dono.
set -euo pipefail
cd "$(dirname "$0")"

LOG=/home/expedicao/deploys.log
QUEM="$(git config user.name || true)"; QUEM="${QUEM:-${USERNAME:-${USER:-desconhecido}}}"

COMMIT_ATUAL="$(ssh expedicao "git -C /home/expedicao/app rev-parse HEAD")"
VERSAO_ATUAL="$(ssh expedicao "curl -fsS http://127.0.0.1:8100/expedicao/api/health | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"version\",\"\"))'" 2>/dev/null || true)"

echo "=== NO AR ==="
echo "   ${VERSAO_ATUAL:-(nao respondeu)}  [${COMMIT_ATUAL:0:8}]"
echo
echo "=== ULTIMAS PUBLICACOES (data | versao | commit | resultado | quem) ==="
ssh expedicao "tail -n 10 $LOG 2>/dev/null" || echo "   (sem historico ainda)"
echo

if [ $# -ge 1 ]; then
  ALVO="$1"
  ALVO_VERSAO="(commit informado)"
else
  # Ultimo deploy OK cujo commit nao e o que esta no ar.
  LINHA="$(ssh expedicao "awk -F'\t' -v atual='$COMMIT_ATUAL' '\$4 ~ /^OK/ && \$3 != atual {l=\$0} END {print l}' $LOG 2>/dev/null" || true)"
  if [ -z "$LINHA" ]; then
    echo "ERRO: nao achei um deploy anterior no historico."
    echo "Informe o commit: ./voltar_versao.sh <commit>"
    echo "Commits no servidor:"
    ssh expedicao "git -C /home/expedicao/app log --oneline -8"
    exit 1
  fi
  ALVO="$(printf '%s' "$LINHA" | cut -f3)"
  ALVO_VERSAO="$(printf '%s' "$LINHA" | cut -f2)"
fi

if ! ALVO="$(ssh expedicao "git -C /home/expedicao/app rev-parse --verify '$ALVO^{commit}'")"; then
  echo "ERRO: o commit $ALVO nao existe no servidor."
  exit 1
fi
if [ "$ALVO" = "$COMMIT_ATUAL" ]; then
  echo "ERRO: esse commit ja e o que esta no ar."
  exit 1
fi

echo ">> voltar para: $ALVO_VERSAO  [${ALVO:0:8}]"
ssh expedicao "git -C /home/expedicao/app log --oneline -1 $ALVO"
if [ "${VOLTAR_CONFIRMADO:-}" != "1" ]; then
  if [ -t 0 ]; then
    read -r -p "Confirmar a volta em PRODUCAO? [s/N] " RESPOSTA
    case "$RESPOSTA" in s|S|sim|SIM) ;; *) echo "Cancelado. Nada mudou."; exit 1;; esac
  else
    echo "Cancelado: confirme com VOLTAR_CONFIRMADO=1 ./voltar_versao.sh"
    exit 1
  fi
fi

ssh expedicao "git -C /home/expedicao/app reset --hard $ALVO"
ssh expedicao "sudo -n systemctl restart expedicao"

echo ">> aguardando o servico responder (ate 90s)..."
if ssh expedicao 'for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8100/expedicao/api/health >/dev/null 2>&1 && exit 0; sleep 3; done; exit 1'; then
  VERSAO_FINAL="$(ssh expedicao "curl -fsS http://127.0.0.1:8100/expedicao/api/health | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"version\",\"\"))'" || true)"
  ssh expedicao "printf '%s\t%s\t%s\t%s\t%s\n' \"\$(date '+%Y-%m-%d %H:%M:%S')\" '$VERSAO_FINAL' '$ALVO' 'VOLTA MANUAL (era ${VERSAO_ATUAL:-?})' '$QUEM' >> $LOG" || true
  echo ">> no ar: $VERSAO_FINAL"
  echo
  echo "Aqui no seu computador o codigo NAO voltou. Corrija (ou 'git revert'),"
  echo "suba o APP_VERSION e publique uma versao nova com ./publicar.sh."
else
  echo "!! o servico nao respondeu em 90s. Execute ./verificar_servidor.sh agora."
  exit 1
fi
