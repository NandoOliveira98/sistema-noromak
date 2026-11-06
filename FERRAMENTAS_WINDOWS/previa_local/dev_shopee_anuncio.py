"""V20.32: Shopee SIMULADA da previa (criar anuncio e estoque). Nada sai deste PC.

Formato pelo SDK gerado da doc oficial (o mesmo do TESTES/test_anuncios_shopee_v2032.py). O titulo com a palavra
"recusar" faz o add_item recusar (para ver o erro na tela); "sumir" faz a Shopee nao responder (incerto)."""
import itertools

_ITEM = itertools.count(880000001)


def instalar(S):
    estoque: dict[str, int] = {}
    skus: dict[str, str] = {}

    def chamar(method, path, params=None, payload=None, *, multipart=None, timeout=35, _renovou=False, publico=False):
        if path.endswith("get_item_limit"):
            return {"response": {"item_name_length_limit": {"min_limit": 10, "max_limit": 120},
                                 "item_description_length_limit": {"min_limit": 20, "max_limit": 3000},
                                 "item_image_count_limit": {"min_limit": 1, "max_limit": 9}}}
        if path.endswith("get_category"):
            return {"response": {"category_list": [
                {"category_id": 100630, "parent_category_id": 0, "display_category_name": "Automotivo", "has_children": True},
                {"category_id": 100640, "parent_category_id": 100630, "display_category_name": "Cuidados com o carro",
                 "has_children": True},
                {"category_id": 100641, "parent_category_id": 100640, "display_category_name": "Limpeza interna",
                 "has_children": False},
                {"category_id": 100642, "parent_category_id": 100640, "display_category_name": "Ceras e polidores",
                 "has_children": False}]}}
        if path.endswith("category_recommend"):
            return {"response": {"category_id": [100641, 100642, 100640]}}
        if path.endswith("get_attribute_tree"):
            return {"response": {"list": [{"category_id": int(params["category_id_list"]), "attribute_tree": [
                {"attribute_id": 11, "name": "Volume", "mandatory": True,
                 "attribute_info": {"input_type": 3, "attribute_unit_list": ["ml", "L"]}},
                {"attribute_id": 12, "name": "Forma do produto", "mandatory": True, "attribute_info": {"input_type": 1},
                 "attribute_value_list": [{"value_id": 501, "name": "Espuma"}, {"value_id": 502, "name": "Líquido"},
                                          {"value_id": 503, "name": "Gel"}]},
                {"attribute_id": 13, "name": "Fragrância", "mandatory": False, "attribute_info": {"input_type": 2},
                 "attribute_value_list": [{"value_id": 601, "name": "Limão"}, {"value_id": 602, "name": "Neutro"}]}]}]}}
        if path.endswith("get_brand_list"):
            return {"response": {"brand_list": [{"brand_id": 7001, "original_brand_name": "TUNAP"},
                                                {"brand_id": 7002, "original_brand_name": "Vonixx"},
                                                {"brand_id": 7003, "original_brand_name": "Wurth"}],
                                 "has_next_page": False, "is_mandatory": False}}
        if path.endswith("get_channel_list"):
            return {"response": {"logistics_channel_list": [
                {"logistics_channel_id": 90016, "logistics_channel_name": "Shopee Xpress", "enabled": True,
                 "fee_type": "SIZE_INPUT", "weight_limit": {"item_max_weight": 30}},
                {"logistics_channel_id": 90024, "logistics_channel_name": "Retirada", "enabled": False,
                 "fee_type": "FIXED_DEFAULT_PRICE"}]}}
        if path.endswith("upload_image"):
            return {"response": {"image_info": {"image_id": f"br-previa-{next(_ITEM)}"}}}
        if path.endswith("add_item"):
            nome = str((payload or {}).get("item_name") or "").lower()
            if "recusar" in nome:
                raise S.ShopeeRecusou("error_param: tax_info.ncm is required (simulado)", {"error": "error_param"})
            if "sumir" in nome:
                raise S.ShopeeSemResposta("timeout (simulado)")
            iid = str(next(_ITEM))
            estoque[iid] = int(((payload or {}).get("seller_stock") or [{}])[0].get("stock") or 0)
            skus[iid] = str((payload or {}).get("item_sku") or "")
            return {"response": {"item_id": int(iid), "item_status": "NORMAL"}}
        if path.endswith("get_item_base_info"):
            iid = str(params["item_id_list"])
            if iid not in estoque:
                return {"response": {"item_list": []}}
            return {"response": {"item_list": [{"item_id": int(iid), "item_status": "NORMAL", "has_model": False,
                                                "item_sku": skus.get(iid, ""),
                                                "stock_info_v2": {"seller_stock": [{"stock": estoque.get(iid, 0)}]}}]}}
        if path.endswith("update_stock"):
            iid = str(payload["item_id"])
            estoque[iid] = payload["stock_list"][0]["seller_stock"][0]["stock"]
            return {"response": {"success_list": [{"model_id": 0}], "failure_list": []}}
        raise S.ShopeeRecusou(f"rota nao simulada na previa: {path}")

    S._shopee_chamar = chamar
    S.shopee_refresh_access_token = lambda *_a, **_k: {"shop_id": "1", "access_token": "previa"}
