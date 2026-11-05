"""Previa local da Plataforma de anuncios (V20.23+). Testar AQUI antes de subir (decisao do dono, 05/11).
Rodar: python FERRAMENTAS_WINDOWS/previa_local/previa_anuncios.py  ->  http://127.0.0.1:8158/expedicao/anuncios
Nunca toca producao: banco temporario novo a cada start, EXP_WORKERS=0, ML e Apollo simulados.

Banco temporario novo a cada start, sem workers. Mercado Livre e Apollo SIMULADOS (nada sai deste PC).
Logins de teste (so desta previa):
  previa            / Previa-Local-2023  -> perfil Admin (grava)
  supervisor_previa / Previa-Local-2023  -> Supervisor (ve, nao grava)
  consulta_previa   / Previa-Local-2023  -> Consulta (so le)
"""
import json, os, sys, tempfile
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]  # raiz do repositorio
SENHA = "Previa-Local-2023"
T = Path(tempfile.mkdtemp(prefix="noromak_anu_"))
os.environ.update(EXP_DB_PATH=str(T / "e.db"), EXP_BACKUP_DIR=str(T / "b"), EXP_MIGRATION_DIR=str(T / "m"),
                  EXP_ADMIN_USUARIO="previa", EXP_ADMIN_SENHA=SENHA, EXP_USUARIO_SENHA_INICIAL=SENHA,
                  EXP_PASSWORD_ITERATIONS="1000", EXP_WORKERS="0")
sys.path.insert(0, str(RAIZ))
import servidor_web as S  # noqa

S.init_db()
S.ML_SKU_MLB_SEED_PATH = T / "nada.json"

# ---------- usuarios de teste ----------
with S.DB_LOCK:
    S.upsert_app_user("supervisor_previa", "Supervisor Previa", "operador", True, SENHA, "previa",
                      permissions={"conferencia": True, "admin": True})
    S.upsert_app_user("consulta_previa", "Consulta Previa", "consulta", True, SENHA, "previa")
    S.DB.commit()


def gravar(chave, valor):
    b, _, _ = S.get_storage(chave)
    S.save_storage(chave, b, json.dumps(valor), False, actor="previa", actor_role="admin")


# ---------- Precificador (produtos comuns: um kit novo com esses codigos e recusado) ----------
gravar("precificador_online_v1", {"products": [
    {"sku": "32052153", "produto": "Oleo Cambio Texaco Texamatic 7045 1 L", "custo": 43.30, "custosFiliais": [{"revenda": "1", "custo_medio": 43.30}]},
    {"sku": "0893117732", "produto": "Limpa Contato Wurth 300 ml", "custo": 23.10, "custosFiliais": [{"revenda": "1", "custo_medio": 23.10}]},
    {"sku": "0893600022", "produto": "Graxa Wurth 500 g", "custo": 31.00, "custosFiliais": [{"revenda": "1", "custo_medio": 31.00}]},
    {"sku": "0893121", "produto": "Produto de teste 0893121", "custo": 38.50, "custosFiliais": [{"revenda": "1", "custo_medio": 38.50}]},
    {"sku": "0893764272", "produto": "Desengripante Wurth 300 ml", "custo": 27.50, "custosFiliais": [{"revenda": "1", "custo_medio": 27.50}]},
]})

# ---------- Cadastro de Kits (o mesmo da Conferencia) ----------
gravar("conf_kits", [
    {"id": "KIT_previa1", "sku_kit": "KIT32052153", "nome": "Oleo Texamatic 7045 Texaco 3 Litros",
     "componentes": [{"sku": "32052153", "quantidade": 3}], "ativo": True},
    {"id": "KIT_previa2", "sku_kit": "KIT42052153", "nome": "Oleo Texamatic 7045 Texaco 4 Litros",
     "componentes": [{"sku": "32052153", "quantidade": 4}], "ativo": True},
    {"id": "KIT_previa3", "sku_kit": "KITLIMPEZA", "nome": "Kit Limpeza Wurth (inativo)",
     "componentes": [{"sku": "0893117732", "quantidade": 2}, {"sku": "0893764272", "quantidade": 1}], "ativo": False},
])

# ---------- Fila de conferencia: pedido pendente do kit de 3 L (testa a trava de trocar peca) ----------
gravar("conf_pedidos", [
    {"Pedido": "261105PREVIA01", "Marketplace": "SHOPEE", "SKU": "32052153", "Quantidade": 3, "Lido": 0,
     "__uidLinha": "KITDB_261105PREVIA01_KIT32052153_32052153_0", "SkuKitOrigem": "KIT32052153",
     "SkuReferenciaShopee": "KIT32052153", "Descricao": "Oleo Texamatic 7045 1 L (kit 3 L)"},
    {"Pedido": "261105PREVIA02", "Marketplace": "MERCADO LIVRE", "SKU": "0893117732", "Quantidade": 2, "Lido": 0,
     "__uidLinha": "PREVIA02_0893117732", "Descricao": "Limpa Contato Wurth"},
])

# ---------- Fichas iniciais (o resto aparece ao clicar em Puxar anuncios do ML) ----------
S.ficha_importar("32052153", {"nome": "Oleo Cambio Texaco Texamatic 7045 1 L", "marca": "Texaco",
                                    "ean": "7891165080241", "ncm": "27101932"}, "apollo")
S.ficha_importar("0893117732", {"nome": "Limpa Contato Wurth 300 ml", "marca": "Wurth"}, "alephee")

# ---------- Apollo simulado (estoque por filial; revenda 1 = Aracatuba, 3 = Bauru) ----------
ESTOQUE = {"32052153": (24, 6), "0893117732": (10, None), "0893600022": (5, 2), "0893764272": (0, 1)}


def crm_stock_lookup_falso(codigo, operador="", wait_seconds=0.0):
    ara, bau = ESTOQUE.get(str(codigo), (12, 4))  # previa: codigo novo tambem tem estoque
    return {"ok": True, "codigo": codigo, "estoques": [
        {"revenda": 1, "casa": "Araçatuba", "disponivel": ara, "respondeu": ara is not None},
        {"revenda": 3, "casa": "Bauru", "disponivel": bau, "respondeu": bau is not None}]}


S.crm_stock_lookup = crm_stock_lookup_falso

# ---------- Mercado Livre simulado (so leitura) ----------


def atr(i, nome=None, num=None, unid=None):
    a = {"id": i, "value_name": nome}
    if num is not None:
        a["value_struct"] = {"number": num, "unit": unid}
        a["value_name"] = f"{num} {unid}"
    return a


ANUNCIOS = {
    "aracatuba": {
        "MLB1129551371": {"titulo": "Óleo Transmissão Automatica Texamatic 7045 E Texaco 3 Litros", "sku_ml": "KIT32052153",
                          "preco": 399.90, "estoque": 8, "status": "active", "tipo": "gold_special", "categoria": "MLB194027",
                          "atributos": [atr("BRAND", "TEXACO"), atr("GTIN", "7891165080241"),
                                        atr("SELLER_PACKAGE_WEIGHT", num=3.2, unid="kg")]},
        "MLB1129555578": {"titulo": "Óleo Transmissão Automatica Texamatic 7045 E Texaco 4 Litros", "sku_ml": "KIT42052153",
                          "preco": 533.20, "estoque": 6, "status": "active", "tipo": "gold_special", "categoria": "MLB194027"},
        "MLB3000000002": {"titulo": "Óleo Câmbio Texamatic 7045 1 Litro", "sku_ml": "32052153", "preco": 139.90,
                          "estoque": 30, "status": "active", "tipo": "gold_pro", "categoria": "MLB194027",
                          "logistica": "fulfillment"},
        "MLB5000000004": {"titulo": "Limpa Contato Wurth 300ml", "sku_ml": "0893117732", "preco": 49.90, "estoque": 10,
                          "status": "active", "tipo": "gold_special", "categoria": "MLB1234",
                          "atributos": [atr("BRAND", "WURTH"), atr("MODEL", "0893117732"), atr("GTIN", "4048962065124"),
                                        atr("SELLER_PACKAGE_WEIGHT", num=450, unid="g"),
                                        atr("SELLER_PACKAGE_HEIGHT", num=25, unid="cm"),
                                        atr("SELLER_PACKAGE_WIDTH", num=80, unid="mm"),
                                        atr("SELLER_PACKAGE_LENGTH", num=8, unid="cm")]},
        "MLB6000000005": {"titulo": "Anúncio sem SKU cadastrado", "sku_ml": "", "preco": 19.90, "estoque": 3,
                          "status": "paused", "tipo": "gold_special", "categoria": "MLB999"},
        "MLB7000000006": {"titulo": "Kit Graxa + Desengripante Wurth", "sku_ml": "0893600022/0893764272", "preco": 89.90,
                          "estoque": 0, "status": "active", "tipo": "gold_special", "categoria": "MLB1234"},
    },
    "bauru": {
        "MLB2000000001": {"titulo": "Óleo Texamatic 7045 Texaco 3 Litros", "sku_ml": "KIT32052153", "preco": 409.90,
                          "estoque": 2, "status": "active", "tipo": "gold_special", "categoria": "MLB194027"},
    },
}
# V20.25: fotos (enderecos falsos do mlstatic; o download e simulado no dev_criar_ml) e descricao de cada anuncio
S._PREVIA_DESCRICOES = {}
for _itens in ANUNCIOS.values():
    for _n, (_mlb, _d) in enumerate(_itens.items()):
        _qtd = 0 if _mlb == "MLB6000000005" else 2 + _n % 4
        _d.setdefault("fotos", [f"https://http2.mlstatic.com/D_NQ_NP_{_mlb}_{i}-O.jpg" for i in range(_qtd)])
        if _mlb != "MLB7000000006":  # este fica sem descricao no ML (404), para ver o caso
            S._PREVIA_DESCRICOES[_mlb] = f"{_d['titulo']}.\n\nDescrição simulada da prévia: produto original, nota fiscal."
S.ml_casas_conectadas = lambda: ["aracatuba", "bauru"]
# V20.24: vendedor de cada conta (o "Puxar um" acha a conta dona pelo seller_id do anuncio)
SELLER = {"aracatuba": "100001", "bauru": "200002"}
S.ml_casa_do_seller = lambda sid: {v: k for k, v in SELLER.items()}.get(str(sid or ""))


def _todos_os_anuncios(forcar=False):
    import time
    time.sleep(3)  # V20.24: a puxada leva uns segundos por conta, para ver o andamento na tela
    return list(ANUNCIOS.get(S.ml_casa_atual(), {}))


def _itens_detalhe(mlbs, falhas=None, com_fotos=False):
    # como o /items?ids= real: publico (qualquer conta le o anuncio de qualquer vendedor), com o seller_id do dono;
    # id que nao existe volta 404 (vai para `falhas`)
    out = {}
    for m in mlbs:
        for conta, itens in ANUNCIOS.items():
            if m in itens:
                out[m] = {**itens[m], "seller_id": SELLER[conta]}
                break
        else:
            if falhas is not None:
                falhas.append(404)
    return out


S.ml_todos_os_anuncios = _todos_os_anuncios
S.ml_itens_detalhe = _itens_detalhe

# V20.24: criar anuncio (ML simulado: categoria, atributos, tarifa, validate, fotos, items)
sys.path.insert(0, str(Path(__file__).parent))
# V20.26: estoque simulado no ML (PUT /items + releitura). O 4 L tem depósito do vendedor (sem selling_address):
# mandar estoque nele mostra o erro "ajuste no ML". O Apollo continua o simulado acima.
S._PREVIA_ANUNCIOS = ANUNCIOS
S._PREVIA_UP = {"MLB1129555578": {"up": "MLBU900000001", "locations": [{"type": "seller_warehouse", "quantity": 6}]}}
S._PREVIA_ESTOQUE_NAO_MUDA = set()
import dev_criar_ml  # noqa
dev_criar_ml.instalar(S)

# Anuncio de teste ja publicado (ML simulado), para a lista nao comecar vazia. Pelas rotas reais, logo depois do start.
def _semear_anuncio_de_teste():
    import base64, struct, time, urllib.request, zlib
    from http.cookiejar import CookieJar

    def png(lado=800):
        linha = b"\x00" + b"\xff\xff\xff" * lado
        ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
                + ch(b"IDAT", zlib.compress(linha * lado)) + ch(b"IEND", b""))

    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def api(rota, corpo=None):
        req = urllib.request.Request("http://127.0.0.1:8158/expedicao" + rota,
                                     data=None if corpo is None else json.dumps(corpo).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if corpo is None else "POST")
        return json.loads(op.open(req, timeout=60).read() or b"{}")

    time.sleep(1.5)
    try:
        api("/api/auth/login", {"username": "previa", "password": SENHA, "area": "admin"})
        foto = api("/api/anuncios/foto", {"codigo": "0893117732", "base64": base64.b64encode(png()).decode()})
        fid = (foto.get("foto") or foto)["id"]
        r = api("/api/anuncios/rascunho", {"codigo": "0893117732", "conta": "aracatuba",
                                           "titulo": "Limpa Contato Wurth 300ml Spray Eletrico"})["rascunho"]
        r = api("/api/anuncios/rascunho", {"id": r["id"], "versao": r["versao"], "categoria": "MLB1234"})["rascunho"]
        d = api("/api/anuncios/ml/atributos?categoria=MLB1234&conta=aracatuba&codigo=0893117732")
        at = dict(d.get("da_ficha") or {})
        for a in d.get("atributos") or []:
            if (a.get("obrigatorio") or a.get("condicional")) and not at.get(a["id"]):
                at[a["id"]] = a["valores"][0]["nome"] if a.get("valores") else ("4048962065124" if a["id"] == "GTIN" else "Wurth")
        r = api("/api/anuncios/rascunho", {"id": r["id"], "versao": r["versao"], "atributos": at})["rascunho"]
        r = api("/api/anuncios/rascunho", {"id": r["id"], "versao": r["versao"], "fotos": [fid]})["rascunho"]
        r = api("/api/anuncios/rascunho", {"id": r["id"], "versao": r["versao"], "preco": 49.9, "quantidade": 8,
                                           "tipo_anuncio": "gold_special", "frete_gratis": False, "garantia": "90 dias",
                                           "descricao": "Anuncio de TESTE da previa."})["rascunho"]
        api("/api/anuncios/rascunho/validar", {"id": r["id"]})
        p = api("/api/anuncios/rascunho/publicar", {"id": r["id"]})
        print("anuncio de teste:", p.get("situacao"), p.get("mlb"), flush=True)
    except Exception as exc:
        print("anuncio de teste nao criado:", exc, flush=True)


import threading as _th  # noqa: E402
_th.Thread(target=_semear_anuncio_de_teste, daemon=True).start()


# V20.26: a previa sobe sem workers (EXP_WORKERS=0); so o estoque automatico roda aqui, a cada 5 s, com o ML e o
# Apollo simulados (rodizio mais rapido para ver na tela).
def _estoque_automatico_previa():
    import time
    S.ANUNCIO_APOLLO_POR_MINUTO = 30
    estado = {}
    while True:
        time.sleep(5)
        try:
            S.anuncios_estoque_volta(estado)
        except Exception as exc:
            print("estoque automatico (previa):", exc, flush=True)


_th.Thread(target=_estoque_automatico_previa, daemon=True).start()

srv = ThreadingHTTPServer(("127.0.0.1", 8158), S.AppHandler)
print("ok 8158", T, flush=True)
srv.serve_forever()
