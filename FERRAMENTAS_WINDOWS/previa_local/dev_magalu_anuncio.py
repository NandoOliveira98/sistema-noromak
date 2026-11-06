"""V20.34: Magalu SIMULADA da previa (criar anuncio e estoque). Nada sai deste PC.

Formato pela especificacao OpenAPI oficial (products.openapi.yaml), o mesmo do TESTES/test_anuncios_magalu_v2034.py.
Criar o SKU responde 202 e o SKU so "aparece" na 2a leitura (como a Magalu, assincrona). O titulo com a palavra
"recusar" faz a Magalu recusar (422); "sumir" faz o SKU nunca aparecer (vira "Confira no painel" em 1 minuto)."""
import time

CAT = "11111111-2222-3333-4444-555555555555"


def instalar(S):
    skus: dict[str, dict] = {}
    leituras: dict[str, int] = {}
    estoque: dict[str, int] = {}
    traces: dict[str, list] = {}

    def chamar(method, path, params=None, payload=None, *, timeout=35):
        if path == "/seller/v1/portfolios/category":
            return {"results": [
                {"id": CAT, "name": "Limpeza automotiva", "path": ["Automotivo", "Limpeza automotiva"], "is_leaf": True},
                {"id": "22222222-2222-3333-4444-555555555555", "name": "Óleos e fluidos", "path": ["Automotivo", "Óleos e fluidos"],
                 "is_leaf": True}]}
        if path.endswith("/attributes"):
            return {"results": [{"name": "Volume", "required": "required", "values": ["300 ml", "500 ml", "1 L", "4 L"]},
                                {"name": "Cor", "required": "optional"}]}
        if path.endswith("/datasheet"):
            return {"results": [{"name": "Fabricante", "required": "required"}, {"name": "Modelo", "required": "recommended"},
                                {"name": "Garantia", "required": "optional"}]}
        if path == "/seller/v1/portfolios/skus" and method == "POST":
            titulo = str((payload or {}).get("title") or "").lower()
            if "recusar" in titulo:
                raise S.MagaluRecusou("Magalu HTTP 422: title: palavra proibida (simulado)",
                                      {"detail": [{"loc": ["body", "title"], "msg": "palavra proibida (simulado)"}]}, 422)
            sku = payload["sku"]
            if "sumir" not in titulo:
                skus[sku] = payload
                leituras[sku] = 0
            traces["tr-" + sku] = []
            return {"trace_id": "tr-" + sku}
        if path.startswith("/seller/v1/portfolios/skus/"):
            sku = path.rsplit("/", 1)[1]
            if sku in skus:
                leituras[sku] += 1
                if leituras[sku] >= 2:
                    return {"sku": sku, "status": "PUBLISHED", "title": skus[sku]["title"]}
            raise S.MagaluRecusou("Magalu HTTP 404: not found", {"message": "not found"}, 404)
        if path.startswith("/v0/traces/"):
            return {"results": traces.get(path.rsplit("/", 1)[1], [])}
        if path.startswith("/seller/v1/portfolios/prices/"):
            return {"trace_id": "tr-preco"}
        if path.startswith("/seller/v1/portfolios/stocks/"):
            sku = path.rsplit("/", 1)[1]
            if method == "GET":
                if sku not in skus:
                    raise S.MagaluRecusou("Magalu HTTP 404", {}, 404)
                if sku not in estoque:
                    return {"results": []}
                return {"results": [{"channel": {"id": S.MAGALU_CANAL_ID}, "quantity": estoque[sku], "type": "AVAILABLE"}]}
            estoque[sku] = int(payload["quantity"])
            return {"trace_id": "tr-estoque"}
        raise S.MagaluRecusou(f"rota nao simulada na previa: {path}", None, 404)

    S._magalu_chamar = chamar
    # o anuncio que a previa publica no ML simulado (MLB9...) nao esta na lista de anuncios: da fotos falsas a ele
    orig_det = S.ml_itens_detalhe

    def detalhe(mlbs, falhas=None, com_fotos=False):
        out = orig_det(mlbs, None, com_fotos)
        for m in mlbs:
            if m not in out and str(m).startswith("MLB9"):
                out[m] = {"fotos": [f"https://http2.mlstatic.com/D_NQ_NP_{m}_{i}-O.jpg" for i in range(3)]}
            elif m not in out and falhas is not None:
                falhas.append(404)
        return out
    S.ml_itens_detalhe = detalhe
    S.MAGALU_ACOMPANHAR_PRAZO_S = 60          # na previa o "sumir" vira incerto em 1 minuto
    S.MAGALU_ACOMPANHAR_INTERVALO_S = 2
    S.MAGALU_ESTOQUE_RELEITURA_ESPERAS = (0.2, 0.5)
    # as fotos do ML simulado sao PNG falsos: na previa a conferencia da foto so olha o endereco
    S._magalu_foto_conferir = lambda url: "" if S._anuncio_foto_ml_url_ok(url) else "endereço fora do mlstatic"
    S._magalu_taxas_precificador = lambda: {}  # a tela pede a comissao (como em metade dos produtos reais)
    _ = time
