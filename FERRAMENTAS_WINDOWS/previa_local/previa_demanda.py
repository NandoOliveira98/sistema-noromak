"""Prévia local da Demanda de Compra por categoria/grupo do Apollo (V20.45). Testar AQUI antes de subir.
Rodar: python FERRAMENTAS_WINDOWS/previa_local/previa_demanda.py  ->  http://127.0.0.1:8162/expedicao/compras
Nunca toca produção: banco temporário novo a cada start, EXP_WORKERS=0, portas do NoromakWeb SIMULADAS.
Login de teste (só desta prévia): previa / Previa-Local-2023 (perfil Admin).
"""
import json, os, sys, tempfile, threading
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SENHA = "Previa-Local-2023"
PORTA = int(os.environ.get("PREVIA_PORTA") or 8162)
T = Path(tempfile.mkdtemp(prefix="noromak_dem_"))
os.environ.update(EXP_DB_PATH=str(T / "e.db"), EXP_BACKUP_DIR=str(T / "b"), EXP_MIGRATION_DIR=str(T / "m"),
                  EXP_ADMIN_USUARIO="previa", EXP_ADMIN_SENHA=SENHA, EXP_USUARIO_SENHA_INICIAL=SENHA,
                  EXP_PASSWORD_ITERATIONS="1000", EXP_WORKERS="0")
sys.path.insert(0, str(RAIZ))
import servidor_web as S  # noqa

S.init_db()
S.CHAVE_CRM = "previa"
S.APOLLO_PECAS_PAUSA = 0.2
S.APOLLO_ESTOQUE_PAUSA = 1.0
S.LINX_DEMANDA_PORTA_ESPERA = 1.0

# (codigo, descricao, grupo, categoria, vendas por mês)
CATALOGO = [
    ("WOE912", "FILTRO DE OLEO", "WEGA FILTROS", "111", 14),
    ("WAP331", "FILTRO DE AR", "WEGA FILTROS", "111", 6),
    ("WCF110", "FILTRO DE COMBUSTIVEL", "WEGA FILTROS", "111", 0),
    ("PSL55", "FILTRO OLEO BLINDADO LUBRIFICANTE", "TECFIL", "111", 9),
    ("ARL4147", "FITRO COMBUSTIVEL SEPARADOR", "TECFIL", "111", 3),
    ("32060853", "TEXAMATIC ATF 1L", "ICONIC", "15", 22),
    ("32052153", "OLEO CAMBIO TEXAMATIC 7045 1L", "ICONIC", "15", 11),
    ("GRX500", "GRAXA AZUL 500G", "ICONIC", "15", 0),
    ("6013.301.027.00.6", "VASO DE EXPANSAO", "AGRALE", "20", 8),
]


def mes_fechado(n):
    d = datetime.now(S.BR_TZ).date().replace(day=1)
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
    return d.strftime("%Y-%m")


def porta_get(rota, params, timeout=None):
    if rota == "pecas/grupos":
        grupos = {}
        for x in CATALOGO:
            grupos[x[2]] = grupos.get(x[2], 0) + 1
        return {"ok": True, "copia_de": "2026-11-09T02:54-03:00",
                "grupos": [{"grupo": None, "nome": g, "pecas": n} for g, n in sorted(grupos.items())]}
    if rota == "pecas/categorias":
        return {"ok": True, "categorias": [{"categoria": 111, "nome": None, "pecas": 5},
                                           {"categoria": 15, "nome": None, "pecas": 3}],
                "lidas": 8, "sem_categoria_lida": 1200}
    if rota == "pecas":
        g = set((params.get("grupo") or "").split(",")) - {""}
        c = set((params.get("categoria") or "").split(",")) - {""}
        lista = [x for x in CATALOGO if (not g or x[2] in g) and (not c or x[3] in c)]
        return {"ok": True, "copia_de": "2026-11-09T02:54-03:00", "pagina": 1, "proxima_pagina": None,
                "total": len(lista), "sem_categoria_lida": 1200 if c else None,
                "itens": [{"codigo": x[0], "descricao": x[1], "grupo": None, "nome_grupo": x[2], "marca": None,
                           "nome_marca": "NOROMAK", "categoria": x[3], "nome_categoria": None,
                           "cancelado_fabrica": None} for x in lista]}
    if rota == "demanda":
        r = {"ok": True, "itens": [], "sem_historico": [], "com_erro": [], "pendentes": []}
        por = {x[0]: x[4] for x in CATALOGO}
        for cod in params["codigos"].split(","):
            if por.get(cod):
                for m in (1, 2, 3):
                    r["itens"].append({"codigo": cod, "revenda": 1, "mes": mes_fechado(m), "DemandaBalcao": por[cod],
                                       "DemandaOficina": 0, "DemandaTelemark": 0, "DemandaTotal": por[cod],
                                       "OcorrenciaVendas": 1})
            else:
                r["sem_historico"].append(cod)
        return r
    raise S.PortaErro(f"rota simulada inexistente: {rota}")


def estoque_lote(codigos, revendas):
    return {"ok": True, "itens": [{"codigo": c, "revenda": r, "disponivel": float((len(c) * r) % 5),
                                   "tem_cadastro": True, "erro": None} for c in codigos for r in revendas]}


S._exp_porta_get = porta_get
S._apollo_estoque_lote_http = estoque_lote

S.save_storage("precificador_online_v1", None, json.dumps({"products": [
    {"sku": "WOE912", "produto": "FILTRO DE OLEO", "custo": 18.4, "custosFiliais": [{"revenda": "1", "custo_medio": 18.4}]},
    {"sku": "32060853", "produto": "TEXAMATIC ATF 1L", "custo": 31.9, "custosFiliais": [{"revenda": "1", "custo_medio": 31.9}]},
    {"sku": "6013.301.027.00.6", "produto": "VASO DE EXPANSAO", "custo": 133.58,
     "custosFiliais": [{"revenda": "1", "custo_medio": 133.58}]},
]}), False, actor="previa", actor_role="admin")
S.apollo_filtros()
S.compras_salvar_fornecedor("AGRALE", {"prefixos": ["6013"], "pedido_minimo": 1000, "prazo": 7, "cobertura": 30}, "previa")

print(f"Prévia da Demanda de Compra: http://127.0.0.1:{PORTA}/expedicao/compras  (login previa / {SENHA})")
srv = ThreadingHTTPServer(("127.0.0.1", PORTA), S.AppHandler)
srv.serve_forever()
