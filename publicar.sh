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

# V18.11: backup consistente e COMPRIMIDO do banco ANTES de enviar/reiniciar. Se
# falhar, a publicacao para aqui — nunca subimos sem uma copia de seguranca do
# estado atual. Comprime (SQLite gzipa ~5x) e mantem os 3 mais recentes, porque o
# disco e pequeno (45 GB) e cada backup cru tem ~1 GB.
echo ">> backup de seguranca do banco antes de publicar..."
if ! ssh expedicao "python3 -" <<'PY'
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

echo ">> aguardando o servico responder na porta 8100 (ate 90s)..."
# V18.12: espera com paciencia. A VPS e fraca e o boot pode levar alguns segundos;
# 3s fixos davam falso negativo (a operacao subia logo depois).
if ! ssh expedicao 'for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8100/expedicao/api/health && exit 0; sleep 3; done; exit 1'; then
  echo
  echo "ERRO: os arquivos foram enviados, mas o aplicativo nao respondeu na porta 8100 em 90s."
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
