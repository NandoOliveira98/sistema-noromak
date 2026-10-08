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

echo ">> validando antes de publicar..."
if ! ./testar.sh; then
  echo
  echo "PUBLICACAO CANCELADA: os testes nao passaram."
  echo "Nada foi enviado para o servidor e a operacao continua no ar."
  exit 1
fi
echo

# V18.63: o que vai subir e o que esta no ar. O commit anterior e guardado para
# VOLTAR SOZINHO se a versao nova nao subir — sem isso um erro no boot deixava a
# operacao parada ate alguem corrigir na mao.
VERSAO_NOVA="$(sed -n 's/^APP_VERSION = "\(.*\)"$/\1/p' servidor_web.py)"
if [ -z "$VERSAO_NOVA" ]; then
  echo "ERRO: nao achei o APP_VERSION em servidor_web.py."
  exit 1
fi
COMMIT_NOVO="$(git rev-parse HEAD)"
QUEM="$(git config user.name || true)"; QUEM="${QUEM:-${USERNAME:-${USER:-desconhecido}}}"

echo ">> conferindo o que esta no ar..."
if ! COMMIT_ANTERIOR="$(ssh expedicao "git -C /home/expedicao/app rev-parse HEAD")"; then
  echo "ERRO: nao consegui ler a versao atual do servidor. Nada foi enviado."
  exit 1
fi
VERSAO_NO_AR="$(ssh expedicao "curl -fsS http://127.0.0.1:8100/expedicao/api/health | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"version\",\"\"))'" 2>/dev/null || true)"
echo "   no ar:   ${VERSAO_NO_AR:-(nao respondeu)}  [${COMMIT_ANTERIOR:0:8}]"
echo "   subindo: $VERSAO_NOVA  [${COMMIT_NOVO:0:8}]"
if [ "$COMMIT_ANTERIOR" = "$COMMIT_NOVO" ]; then
  echo "ERRO: este commit ja esta no ar. Nada a publicar."
  exit 1
fi
if [ -n "$VERSAO_NO_AR" ] && [ "$VERSAO_NO_AR" = "$VERSAO_NOVA" ]; then
  echo "ERRO: a versao $VERSAO_NOVA ja esta no ar com outro codigo."
  echo "Suba o APP_VERSION (servidor_web.py) e o VERSAO.txt antes de publicar —"
  echo "e pela versao que a publicacao confere se o codigo novo realmente subiu."
  exit 1
fi

# V18.63: o restart derruba o sistema por alguns segundos e a conferencia aberta
# recebe "o servidor nao esta respondendo". Mostra quem esta conferindo agora
# (bloqueios com heartbeat nos ultimos 90 s) e so continua com confirmacao.
EM_CONFERENCIA="$(ssh expedicao "python3 -" <<'PY' || true
import sqlite3
from datetime import datetime, timezone
db = sqlite3.connect("file:/home/expedicao/dados/expedicao.db?mode=ro", uri=True, timeout=10)
agora = datetime.now(timezone.utc)
for operador, computador, pedido, quando in db.execute(
        "SELECT operator, computer, order_id, updated_at FROM order_locks"):
    try:
        if (agora - datetime.fromisoformat(quando)).total_seconds() <= 90:
            print("   - %s (%s) conferindo o pedido %s" % (operador, computador, pedido))
    except Exception:
        pass
PY
)"
if [ -n "$EM_CONFERENCIA" ]; then
  echo
  echo "ATENCAO: tem gente conferindo agora:"
  echo "$EM_CONFERENCIA"
  echo "O sistema fica fora do ar alguns segundos durante a publicacao."
  if [ "${PUBLICAR_COM_OPERACAO:-}" = "1" ]; then
    echo ">> PUBLICAR_COM_OPERACAO=1: seguindo mesmo assim."
  elif [ -t 0 ]; then
    read -r -p "Publicar mesmo assim? [s/N] " RESPOSTA
    case "$RESPOSTA" in s|S|sim|SIM) ;; *) echo "PUBLICACAO CANCELADA. Nada foi enviado."; exit 1;; esac
  else
    echo "PUBLICACAO CANCELADA. Nada foi enviado."
    echo "Para publicar mesmo assim: PUBLICAR_COM_OPERACAO=1 ./publicar.sh"
    exit 1
  fi
fi
echo

# V18.78: entre um pedido e outro o bloqueio e liberado, entao a lista acima fica
# vazia mesmo as 11:25 de um dia util. E o backup pre-deploy ocupa disco e CPU por
# ~3 min numa VPS de 1 GB dividida com o CRM (travou tudo as 11:25 de 08/10; foram
# 12 deploys entre 02/10 e 08/10, 5 so em 06/10). Em dia util, das 08:00 as 18:00 de
# Brasilia, so publica com confirmacao explicita.
AGORA_BR="$(ssh expedicao "TZ=America/Sao_Paulo date '+%u %H'" 2>/dev/null || true)"
if [ -n "$AGORA_BR" ]; then
  DIA_SEMANA_BR=$((10#${AGORA_BR%% *})); HORA_BR=$((10#${AGORA_BR##* }))
  if [ "$DIA_SEMANA_BR" -le 5 ] && [ "$HORA_BR" -ge 8 ] && [ "$HORA_BR" -lt 18 ]; then
    echo "ATENCAO: horario comercial no servidor (dia util, ${HORA_BR}h de Brasilia)."
    echo "O backup pre-deploy ocupa o disco por ~3 min e o restart derruba a Expedicao."
    echo "O recomendado e publicar antes das 08:00 ou depois das 18:00, agrupando as versoes."
    if [ "${PUBLICAR_NO_EXPEDIENTE:-}" = "1" ]; then
      echo ">> PUBLICAR_NO_EXPEDIENTE=1: seguindo mesmo assim."
    elif [ -t 0 ]; then
      read -r -p "Publicar em horario comercial mesmo assim? [s/N] " RESPOSTA
      case "$RESPOSTA" in s|S|sim|SIM) ;; *) echo "PUBLICACAO CANCELADA. Nada foi enviado."; exit 1;; esac
    else
      echo "PUBLICACAO CANCELADA. Nada foi enviado."
      echo "Para publicar mesmo assim: PUBLICAR_NO_EXPEDIENTE=1 ./publicar.sh"
      exit 1
    fi
    echo
  fi
fi

# V18.11: backup consistente e COMPRIMIDO do banco ANTES de enviar/reiniciar. Se
# falhar, a publicacao para aqui — nunca subimos sem uma copia de seguranca do
# estado atual. Comprime (SQLite gzipa ~5x) e mantem os 3 mais recentes, porque o
# disco e pequeno (45 GB) e cada backup cru tem ~1 GB.
# V18.78: roda em prioridade MINIMA de CPU (nice 19) e de disco (ionice idle):
# a copia + gzip de 1,6 GB em prioridade normal parava a bipagem e o CRM.
echo ">> backup de seguranca do banco antes de publicar (prioridade baixa)..."
if ! ssh expedicao 'if command -v ionice >/dev/null 2>&1; then exec ionice -c 3 nice -n 19 python3 -; else exec nice -n 19 python3 -; fi' <<'PY'
import sqlite3, time, os, glob, sys, gzip, shutil
destino_dir = "/home/expedicao/backups"
os.makedirs(destino_dir, exist_ok=True)
base = os.path.join(destino_dir, "expedicao_predeploy_" + time.strftime("%Y%m%d_%H%M%S"))
tmp_db = base + ".db"
final = base + ".db.gz"
origem = sqlite3.connect("/home/expedicao/dados/expedicao.db", timeout=60)
saida = sqlite3.connect(tmp_db)
with saida:
    origem.backup(saida)   # backup online consistente (lida com WAL)
saida.close(); origem.close()
# Verifica que o backup abre e tem dados ANTES de comprimir.
conf = sqlite3.connect(tmp_db)
try:
    keys = conf.execute("SELECT COUNT(*) FROM storage").fetchone()[0]
    peds = conf.execute("SELECT COUNT(*) FROM completed_orders").fetchone()[0]
finally:
    conf.close()
if keys < 1:
    os.remove(tmp_db)
    print("   ERRO: o backup ficou sem dados de storage.")
    sys.exit(1)
cru = os.path.getsize(tmp_db)
with open(tmp_db, "rb") as f_in, gzip.open(final, "wb", compresslevel=6) as f_out:
    shutil.copyfileobj(f_in, f_out, 1024 * 1024)
os.remove(tmp_db)
print("   backup OK:", final)
print("   storage keys:", keys, "| completed_orders:", peds,
      "| %d MB -> %d MB" % (cru // 1048576, os.path.getsize(final) // 1048576))
# Retencao por IDADE: apaga backups de deploy com mais de 2 dias (o recem-criado
# tem data de agora, entao nunca e apagado -> sempre sobra o do deploy atual).
LIMITE_DIAS = 2
agora = time.time()
for velho in glob.glob(os.path.join(destino_dir, "expedicao_predeploy_*.db.gz")):
    idade_dias = (agora - os.path.getmtime(velho)) / 86400.0
    if idade_dias > LIMITE_DIAS:
        os.remove(velho)
        print("   removido backup antigo (%.1f dias):" % idade_dias, velho)
PY
then
  echo
  echo "PUBLICACAO CANCELADA: nao consegui fazer o backup do banco."
  echo "Nada foi enviado e a operacao continua no ar."
  exit 1
fi
echo

# V18.63: espera o /api/health responder COM a versao pedida (vazio = qualquer uma)
# e com os workers ligados — EXP_WORKERS=0 e so para teste e homologacao.
esperar_versao() {
  ssh expedicao "bash -s -- '$1'" <<'SH'
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8100/expedicao/api/health 2>/dev/null | python3 -c '
import sys, json
d = json.load(sys.stdin)
pedida = sys.argv[1]
sys.exit(0 if d.get("ok") and (not pedida or d.get("version") == pedida) and d.get("workers", True) is not False else 1)
' "$1" 2>/dev/null; then
    exit 0
  fi
  sleep 3
done
exit 1
SH
}

registrar() {
  ssh expedicao "printf '%s\t%s\t%s\t%s\t%s\n' \"\$(date '+%Y-%m-%d %H:%M:%S')\" '$1' '$2' '$3' '$QUEM' >> /home/expedicao/deploys.log" || true
}

# V18.63: volta o codigo do servidor para o commit que estava no ar e reinicia.
# O banco NAO volta (o backup de antes do deploy esta em /home/expedicao/backups).
reverter() {
  echo
  echo ">> VOLTANDO para a versao que estava no ar (${VERSAO_NO_AR:-?} [${COMMIT_ANTERIOR:0:8}])..."
  if ssh expedicao "git -C /home/expedicao/app reset --hard $COMMIT_ANTERIOR" \
     && ssh expedicao "sudo -n systemctl restart expedicao" \
     && esperar_versao "$VERSAO_NO_AR"; then
    registrar "${VERSAO_NO_AR:-?}" "$COMMIT_ANTERIOR" "REVERTIDO (falhou $VERSAO_NOVA)"
    echo ">> a versao anterior voltou e esta respondendo. A operacao continua."
    echo ">> Corrija aqui, suba o APP_VERSION e publique de novo. Diagnostico: ./verificar_servidor.sh"
  else
    registrar "${VERSAO_NO_AR:-?}" "$COMMIT_ANTERIOR" "FALHA AO REVERTER"
    echo "!! NAO CONSEGUI VOLTAR A VERSAO ANTERIOR. O SISTEMA PODE ESTAR FORA DO AR."
    echo "!! Execute ./verificar_servidor.sh agora."
  fi
}

echo ">> enviando pro servidor..."
git push producao HEAD:master

echo ">> reiniciando o sistema no servidor..."
if ! ssh expedicao "sudo -n systemctl restart expedicao"; then
  echo
  echo "ERRO: sua conta nao tem reinicio sem senha liberado para o servico expedicao."
  echo "Peca ao responsavel pelo servidor para liberar exatamente:"
  echo "  systemctl restart expedicao"
  # O codigo ja foi trocado no disco: volta para o que esta rodando, senao o proximo
  # restart automatico do systemd subiria a versao nova sem ninguem conferir.
  ssh expedicao "git -C /home/expedicao/app reset --hard $COMMIT_ANTERIOR" || true
  registrar "$VERSAO_NOVA" "$COMMIT_NOVO" "CANCELADO (sem permissao de restart)"
  exit 1
fi

echo ">> aguardando a versao $VERSAO_NOVA responder na porta 8100 (ate 90s)..."
# V18.12: espera com paciencia. A VPS e fraca e o boot pode levar alguns segundos;
# 3s fixos davam falso negativo (a operacao subia logo depois).
if ! esperar_versao "$VERSAO_NOVA"; then
  echo
  echo "ERRO: a versao $VERSAO_NOVA nao respondeu em 90s."
  ssh expedicao "sudo -n journalctl -u expedicao -n 40 --no-pager" 2>/dev/null || true
  reverter
  exit 1
fi
registrar "$VERSAO_NOVA" "$COMMIT_NOVO" "OK"
echo ">> $VERSAO_NOVA no ar."
echo

echo ">> conferindo o endereco publico..."
PUBLIC_STATUS="$(curl -s -o /dev/null -w '%{http_code}' 'https://noromakweb.com.br/expedicao/')"
echo ">> resultado publico: $PUBLIC_STATUS"

if [ "$PUBLIC_STATUS" != "200" ] && [ "$PUBLIC_STATUS" != "302" ]; then
  echo "O aplicativo respondeu internamente, mas o nginx devolveu HTTP $PUBLIC_STATUS."
  exit 1
fi

echo ">> PUBLICACAO CONCLUIDA. Abra https://noromakweb.com.br/expedicao"
