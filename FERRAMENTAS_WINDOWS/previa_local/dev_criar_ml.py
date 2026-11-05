"""V20.24: criar anuncio na previa - ML simulado (categoria, atributos, tarifa, validate, fotos, items).
Importado pelo dev_anuncios.py depois de montar o banco (usa o modulo S ja carregado).
Titulo com "TESTE INCERTO" = o POST /items fica sem resposta (timeout) -> rascunho `incerto`.
Titulo com "TESTE RECUSA" = o /items/validate recusa (causas campo a campo).
"""
import itertools
import json
import re
import struct
import time
import urllib.parse
import zlib


def instalar(S):
    cont = itertools.count(1)
    cats = {
        "MLB194027": {"nome": "Óleos de Transmissão", "caminho": ["Acessórios para Veículos", "Lubrificantes e Fluidos", "Óleos de Transmissão"]},
        "MLB1234": {"nome": "Limpa Contatos", "caminho": ["Ferramentas", "Químicos", "Limpa Contatos"]},
    }
    atributos = [
        {"id": "BRAND", "name": "Marca", "tags": {"required": True}, "value_type": "string", "value_max_length": 60},
        {"id": "MODEL", "name": "Modelo", "tags": {"required": True}, "value_type": "string"},
        {"id": "GTIN", "name": "Código universal de produto", "tags": {"conditional_required": True}, "value_type": "string",
         "hint": "EAN/GTIN da embalagem"},
        {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vazio", "tags": {"conditional_required": True}, "value_type": "list",
         "values": [{"id": "17055160", "name": "O produto não tem código cadastrado"}, {"id": "17055161", "name": "Kit"},
                    {"id": "17055162", "name": "É uma peça artesanal"}]},
        {"id": "VOLUME", "name": "Volume", "tags": {"required": True}, "value_type": "number_unit",
         "allowed_units": [{"id": "L", "name": "L"}, {"id": "mL", "name": "mL"}]},
        {"id": "VEHICLE_TYPE", "name": "Tipo de veículo", "tags": {}, "value_type": "list",
         "values": [{"id": "1", "name": "Carro/Caminhonete"}, {"id": "2", "name": "Moto"}, {"id": "3", "name": "Caminhão"}]},
        {"id": "SELLER_PACKAGE_WEIGHT", "name": "Peso da embalagem", "tags": {}, "value_type": "number_unit"},
        {"id": "SELLER_PACKAGE_HEIGHT", "name": "Altura da embalagem", "tags": {}, "value_type": "number_unit"},
        {"id": "SELLER_PACKAGE_WIDTH", "name": "Largura da embalagem", "tags": {}, "value_type": "number_unit"},
        {"id": "SELLER_PACKAGE_LENGTH", "name": "Comprimento da embalagem", "tags": {}, "value_type": "number_unit"},
        {"id": "IS_KIT", "name": "É kit", "tags": {}, "value_type": "boolean",
         "values": [{"id": "242085", "name": "Sim"}, {"id": "242084", "name": "Não"}]},
        {"id": "ITEM_CONDITION", "name": "Condição", "tags": {"hidden": True}},
    ]

    # V20.26: estoque simulado por MLB (PUT /items e a releitura). Comeca pelo estoque dos anuncios da previa.
    # MLB em S._PREVIA_ESTOQUE_NAO_MUDA = o PUT responde 200 sem mudar (o ML com varios depositos).
    # S._PREVIA_UP[mlb] = {"up": id, "locations": [...]} = anuncio com user-product (estoque por local).
    estoque_ml = {}
    for _itens in (getattr(S, "_PREVIA_ANUNCIOS", {}) or {}).values():
        for _mlb, _d in _itens.items():
            estoque_ml[_mlb] = {"qtd": _d.get("estoque"), "logistica": _d.get("logistica") or "drop_off"}
    S._PREVIA_ESTOQUE_ML = estoque_ml
    ups = getattr(S, "_PREVIA_UP", {}) or {}
    versoes = {}

    def item_estoque(mlb):
        d = estoque_ml.setdefault(mlb, {"qtd": 0, "logistica": "drop_off"})
        up = (ups.get(mlb) or {}).get("up")
        return {"id": mlb, "available_quantity": d["qtd"], "user_product_id": up,
                "shipping": {"logistic_type": d["logistica"]}, "status": "active"}

    def ml_estoque_http_falso(method, path, payload=None, headers=None):
        time.sleep(0.3)
        m = re.match(r"/user-products/([^/]+)/stock(/type/(selling_address|seller_warehouse))?$", path)  # V20.27
        mlb = next((k for k, v in ups.items() if v.get("up") == (m.group(1) if m else None)), None)
        if not m or mlb is None:
            raise RuntimeError(f"Mercado Livre respondeu HTTP 404: (prévia) {method} {path} não simulado")
        locais = ups[mlb]["locations"]
        if method == "GET":
            return {"id": ups[mlb]["up"], "locations": locais, "stock_mode": "countable"}, {"x-version": str(versoes.get(mlb, 1))}
        if str((headers or {}).get("x-version")) != str(versoes.get(mlb, 1)):
            e = RuntimeError("Mercado Livre respondeu HTTP 409: version conflict")
            e.ml_status, e.ml_corpo = 409, "version conflict"
            raise e
        if m.group(3) == "seller_warehouse":  # V20.27: multiorigem, pelo store_id do deposito
            for pedido in payload.get("locations") or []:
                for loc in locais:
                    if loc.get("type") == "seller_warehouse" and str(loc.get("store_id")) == str(pedido.get("store_id")):
                        loc["quantity"] = int(pedido["quantity"])
        for loc in locais:
            if m.group(3) == "selling_address" and loc.get("type") == "selling_address":
                loc["quantity"] = int(payload["quantity"])
        versoes[mlb] = versoes.get(mlb, 1) + 1
        estoque_ml.setdefault(mlb, {"qtd": 0, "logistica": "drop_off"})["qtd"] = sum(int(l.get("quantity") or 0) for l in locais)
        return {}, {}

    S._ml_estoque_http = ml_estoque_http_falso
    S.ANUNCIO_ESTOQUE_RELEITURA_ESPERAS = (0.3,)

    def ml_api_get_falso(caminho, **kw):
        time.sleep(0.3)
        m = re.match(r"/items/(MLB\d+)\?attributes=", caminho)  # V20.26: releitura do estoque
        if m:
            return item_estoque(m.group(1))
        if caminho.startswith("/sites/MLB/domain_discovery/search"):
            return [{"domain_id": "MLB-TRANSMISSION_OILS", "domain_name": "Óleos de transmissão", "category_id": "MLB194027",
                     "category_name": "Óleos de Transmissão"},
                    {"domain_id": "MLB-CONTACT_CLEANERS", "domain_name": "Limpa contatos", "category_id": "MLB1234",
                     "category_name": "Limpa Contatos"}]
        m = re.match(r"/items/(MLB\d+)/description$", caminho)  # V20.25: descricao do anuncio (puxar um / Trazer dados)
        if m:
            texto = (getattr(S, "_PREVIA_DESCRICOES", {}) or {}).get(m.group(1))
            if texto is None:
                raise RuntimeError("Mercado Livre respondeu HTTP 404: item sem descrição")
            return {"plain_text": texto}
        m = re.match(r"/categories/(MLB\d+)(/attributes)?$", caminho)
        if m:
            c = cats.get(m.group(1))
            if c is None:
                raise RuntimeError("Mercado Livre respondeu HTTP 404: categoria não existe")
            if m.group(2):
                return atributos if m.group(1) == "MLB194027" else [a for a in atributos if a["id"] in ("BRAND", "MODEL", "GTIN")]
            return {"id": m.group(1), "name": c["nome"], "path_from_root": [{"name": n} for n in c["caminho"]]}
        if caminho.startswith("/sites/MLB/listing_prices"):
            q = dict(urllib.parse.parse_qsl(caminho.split("?", 1)[1]))
            preco = float(q.get("price") or 0)
            pct = 16.5 if q.get("listing_type_id") == "gold_pro" else 11.5
            fixo = 6.25 if preco < 79 else 0
            return {"listing_type_id": q.get("listing_type_id"), "sale_fee_amount": round(preco * pct / 100 + fixo, 2),
                    "sale_fee_details": {"percentage_fee": pct, "fixed_fee": fixo}}
        raise RuntimeError("Mercado Livre respondeu HTTP 404: (prévia) caminho não simulado " + caminho)

    def ml_promo_http_falso(method, path, payload=None, **_kw):
        time.sleep(0.5)
        titulo = str((payload or {}).get("title") or (payload or {}).get("family_name") or "")
        if method == "POST" and path == "/items/validate":
            if "TESTE RECUSA" in titulo.upper():
                corpo = {"message": "Validation error", "cause": [
                    {"type": "error", "code": "item.attribute.invalid", "references": ["attributes.VOLUME"],
                     "message": "O atributo VOLUME precisa de unidade (ex.: 1 L)."},
                    {"type": "error", "code": "item.price.invalid", "references": ["price"],
                     "message": "Preço abaixo do mínimo da categoria."},
                    {"type": "error", "code": "item.pictures.missing", "references": ["pictures"], "message": "Sem fotos."}]}
                e = RuntimeError("Mercado Livre respondeu HTTP 400: " + json.dumps(corpo))
                e.ml_status, e.ml_corpo = 400, json.dumps(corpo)
                raise e
            return {}
        if method == "POST" and path == "/items":
            if "TESTE INCERTO" in titulo.upper():
                time.sleep(2)
                raise TimeoutError("timed out")
            n = next(cont)
            estoque_ml[f"MLB9{n:09d}"] = {"qtd": payload.get("available_quantity"), "logistica": "drop_off"}  # V20.26
            return {"id": f"MLB9{n:09d}", "title": titulo, "price": payload.get("price"),
                    "available_quantity": payload.get("available_quantity"), "status": "active",
                    "listing_type_id": payload.get("listing_type_id"), "category_id": payload.get("category_id")}
        if method == "POST" and path.endswith("/description"):
            return {"plain_text": (payload or {}).get("plain_text", "")}
        m = re.match(r"/items/(MLB\d+)$", path)  # V20.26: PUT do estoque do anuncio
        if method == "PUT" and m and set(payload or {}) == {"available_quantity"}:
            mlb = m.group(1)
            if mlb not in (getattr(S, "_PREVIA_ESTOQUE_NAO_MUDA", set()) or set()):
                estoque_ml.setdefault(mlb, {"qtd": 0, "logistica": "drop_off"})["qtd"] = int(payload["available_quantity"])
            return item_estoque(mlb)
        raise RuntimeError(f"Mercado Livre respondeu HTTP 404: (prévia) {method} {path} não simulado")

    S.ml_api_get = ml_api_get_falso
    S._ml_promo_http = ml_promo_http_falso
    S._ml_foto_upload = lambda dados, tipo: f"FOTOPREVIA{next(cont)}"
    S.ml_access_token = lambda force_refresh=False: ("token-previa", "previa")

    def png(cor, lado=640):
        cru = (b"\x00" + bytes(cor) * lado) * lado

        def bloco(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
                + bloco(b"IDAT", zlib.compress(cru, 9)) + bloco(b"IEND", b""))

    # V20.25: download das fotos do anuncio simulado (o endereco continua conferido: so https do mlstatic.com)
    def foto_ml_falsa(url):
        if not S._anuncio_foto_ml_url_ok(url):
            raise ValueError("endereço fora do mlstatic.com")
        time.sleep(0.2)
        h = sum(url.encode()) % 200
        return png((40 + h, 200 - h // 2, 120 + h // 3), 600)

    S._anuncio_foto_ml_baixar = foto_ml_falsa

    # fotos iniciais da ficha 32052153 e um trabalho pronto do Estudio
    S.anuncio_foto_adicionar("32052153", png((230, 120, 30)), "previa")
    S.anuncio_foto_adicionar("32052153", png((40, 110, 200), 800), "previa")
    with S.DB_LOCK:
        cur = S.DB.execute("INSERT INTO imagem_trabalho_v2017(tipo,sku,situacao,criado_por,criado_em,atualizado_em,resultados_json) "
                           "VALUES('principal','32052153','pronta','previa',?,?,?)",
                           (S.now_iso(), S.now_iso(), json.dumps([{"nome": "resultado_1.png", "papel": "principal"}])))
        tid = cur.lastrowid
        S.DB.commit()
    (S.IMAGENS_DIR / str(tid)).mkdir(parents=True, exist_ok=True)
    (S.IMAGENS_DIR / str(tid) / "resultado_1.png").write_bytes(png((60, 160, 90), 700))
    S.ficha_salvar("32052153", {"modelo": "Texamatic 7045", "peso_g": 1100, "altura_cm": 25, "largura_cm": 10,
                                "comprimento_cm": 8,
                                "descricao": "Óleo para transmissão automática Texaco Texamatic 7045E, 1 litro."},
                   "previa", S.ficha_ler("32052153")["versao"])
