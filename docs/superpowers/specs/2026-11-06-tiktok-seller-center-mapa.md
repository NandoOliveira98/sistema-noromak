# TikTok Shop (Bauru) pelo Seller Center — mapa das chamadas internas

Levantado em 06/11/2026 no Seller Center logado pelo dono (navegador do app), **só leitura**.
Motivo: a API oficial exige gerente de contas (suporte do TikTok confirmou em 06/11); o caminho
do Noromak é a extensão `NOROMAK_TIKTOK_ESTOQUE` (Edge, fora do repo), com a sessão do vendedor.

## Como chamar

- Origem `https://seller-br.tiktok.com`, cookies da sessão (`credentials: "include"`).
- Parâmetros da URL copiados da última chamada `/api/v1/...` da própria página (o `base()` do
  `executor.js`), **sem** `msToken`, `X-Bogus`, `X-Gnarly`, `_signature`. Conferido: as chamadas
  abaixo respondem `code: 0` sem as assinaturas.
- Loja: `oec_seller_id = seller_id = 7494984494369899563`; depósito "Noromak Bauru"
  `warehouse_id = 7698763258001884929`.

## Pedidos

`POST /api/fulfillment/order/list` — corpo:

```json
{"search_condition":{"condition_list":{"order_status":{"value":["1"]},"search_tab":{"value":["101"]}}},
 "offset":0,"count":20,"sort_info":"1","search_cursor":"","pagination_type":0}
```

- Aba "Para enviar" = `search_tab 101` + `order_status 1`. "Todos" = `condition_list: {}`.
  "Enviado" = `search_tab 102`. Contagem por aba: `POST /api/fulfillment/order/search_count`
  `{"search_key_list":["101","1100","1200","102","110"]}`.
- Resposta `data.main_orders[]` (+ `total_count`, `has_more`). Por pedido:
  - `main_order_id`; `trade_order_module`: `create_time`, `payment_time` (epoch s),
    `latest_rts_time` (prazo para despachar), `need_invoice_flag`;
  - `order_status_module[]`: `main_order_status` / `main_sub_order_status` por linha
    (enviado = `102/305`; os códigos de "a enviar" e "cancelado" faltam conferir com um pedido real);
  - `sku_module[]`: `seller_sku_name` (= nosso SKU), `quantity`, `sku_unit_price.price_val`,
    `sku_total_price`, `product_id`, `sku_id`, `product_name`;
  - `delivery_module[]`: `tracking_no`, `warehouse_name`, transportadora, `invoices[]`
    (`invoice_status`), `package_sub_status`;
  - `print_label_module[]`: `label_status`; `fulfill_unit_id_mapper[]`: `package_id`.
  - `buyer_info_module.cpf` vem **vazio** na lista.

`POST /api/fulfillment/order/get` — corpo `{"main_order_id":["<id>"]}` → `data.main_order[0]`:

- `buyer_info_module.cpf` (11 dígitos, sem máscara) e `cpf_name` (nome da nota);
- `buyer_info_module.shipping_address.items[]` (chave/valor): `name`, `geo_l1` (UF), `geo_l2`
  (cidade), `zipcode`, `neighborhood` (bairro — a Shopee não traz), `street_name`,
  `street_number`, `address_detail`; `phone` e `email` **mascarados**;
- `price_module`: `main_order_origin_sale_price` (preço cheio), `seller_discount_total`
  (desconto da loja), `platform_discount_total` (subsídio do TikTok), `sub_total`,
  `grand_total`, `shipping_fee`, `promotion_infos[]`.
  Exemplo real: 2 × 89,90 = 179,80 − 50,00 da loja = 129,80 (venda pela regra do dono:
  preço com o desconto da loja, sem o subsídio do TikTok) − 6,49 do TikTok = 123,31 pago.

## Anúncios

`GET /api/v1/product/local/products/list?profile=1&tab_id=1&page_number=1&page_size=50&sku_number=20&is_need_target_stock=true&same_product_page_size=3&product_sort_fields=3&product_sort_types=0`

- `data.total_product_count`, `data.products[]`: `product_id`, `product_name`, `product_status`,
  `audit_status`, `categories`, `brand`, `package_weight/width/height/length`, `image`;
- `skus[]`: `id`, `seller_sku`, `base_price.sale_price`, `quantities[]` por depósito
  (`warehouse_id`, `available_quantity`, `total_quantity`, `reserved_quantity`).
- `tab_id` 1 = todos (a página abre em 2 — conferir o significado de cada aba).

Estoque (já em uso pela extensão 0.6): `POST /api/v1/product/stock/sku/list`,
`/api/v1/product/stock/query/sku`, `/api/v1/product/sku/price/stocks/update`.

## A conferir com o 1º pedido real "a enviar"

Status de "a enviar" e de cancelado; se o detalhe traz o CPF também antes de despachar; o que
muda quando a nota é anexada (`invoice_status`). Guardar a 1ª resposta como amostra no Noromak.
