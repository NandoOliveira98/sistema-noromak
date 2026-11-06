# Plataforma de anúncios — criar anúncio e controlar estoque na Shopee (V20.32)

Pedido do dono (06/11): depois do 1º anúncio criado pelo Noromak no ML (TUNAP109, MLB5522017197), "conectar Magalu e
Shopee de Araçatuba para testar o envio desse mesmo produto". Entrevista (grill-me) — decisões:

1. **Criar o anúncio do zero pelo Noromak** (ficha → rascunho → conferir → publicar), como no ML. Não é só estoque.
2. **Shopee primeiro**, Magalu depois (o token da Magalu só lê produto/estoque: precisa de escopos novos e reconexão).
3. **Mesmo estoque para os 3 marketplaces**: o disponível do Apollo de Araçatuba − o que foi vendido e ainda não baixou
   vai igual para ML, Shopee (e Magalu); a venda em qualquer um desconta de todos. Nada é dividido entre canais.
4. **Preço da Shopee próprio**: os cartões do ML com as taxas da Shopee (`SHOPEE_FAIXAS_TAXA` + 3,5%, imposto, custo de
   Araçatuba), campo começa no preço ideal pela margem-alvo; o preço do ML não é copiado.
5. Dono, depois da 4ª pergunta: "siga sempre suas recomendações" — o resto abaixo é decisão minha seguindo o padrão.

Pesquisa da API (06/11, SDK gerado da doc oficial `congminh1254/shopee-sdk`; open.shopee.com não abre aqui): tudo o
que não foi visto numa resposta real fica guardado na 1ª resposta (`anuncio_amostra_v2024`, tipos `shopee_*`) — **conferir
no 1º uso**.

## O que a Shopee NÃO tem
- **Não há "conferir sem criar"** (o ML tem `/items/validate`). O "Conferir" da Shopee é **local**, contra as regras que a
  própria Shopee publica: `get_item_limit` (tamanho do título e da descrição, nº de fotos, faixa de preço),
  `get_attribute_tree` (obrigatórios), `get_brand_list` (`is_mandatory`) e `get_channel_list` (canais ligados e limite
  de peso/medidas). O 1º "Publicar" real é o teste de verdade; a recusa vem com o texto da Shopee.

## Fluxo (rascunho próprio da Shopee)
Tabela `anuncio_rascunho_shopee_v2032` (um por tentativa; `versao` → 409 `CONFLITO`; mesma máquina de situação do ML:
`rascunho → validado → publicando → publicado | erro | incerto`). Conta só Araçatuba (a Shopee é só de Araçatuba).

1. **Título** — limite do `get_item_limit` (sem a leitura: 120).
2. **Categoria** — `category_recommend(item_name)` (ids) + nomes/caminho do `get_category` (cache 24 h). Só folha.
3. **Atributos obrigatórios + marca** — `get_attribute_tree` da categoria: obrigatório com lista = escolher da lista
   (`value_id`); texto livre = `value_id 0` + `original_value_name`; com unidade = `value_unit`. Marca: a da ficha casada
   (sem acento/caixa) com o `get_brand_list` da categoria; sem casar = "No Brand" (`brand_id 0`) **só se a marca não é
   obrigatória** — obrigatória e sem casar = o Admin escolhe da lista.
4. **Fotos** — as da ficha (mesma escolha/ordem do ML), até o limite da Shopee.
5. **Preço, quantidade e envio** — preço com os cartões da Shopee; quantidade = no máximo o que dá para montar (Apollo
   lido agora); **peso e medidas sempre lidos da ficha na hora** (nunca copiados para o rascunho — o problema do ML de
   06/11, ficha corrigida depois e rascunho velho); canais de envio = os ligados na loja que aceitam o peso/medidas;
   NCM da ficha vai em `tax_info.ncm` quando a ficha tem (8 dígitos), `origin` 0.
6. **Conferir** — local (acima); certo = `validado`.
7. **Publicar** (perfil Admin, a ÚNICA escrita na criação) — sob `ANUNCIOS_PUBLICAR_LOCK`; troca de situação sob o
   `DB_LOCK`; Apollo relido (quantidade só pode diminuir); sobe as fotos (`media_space/upload_image`, multipart) e
   `product/add_item` com `item_status NORMAL`, `condition NEW`, `item_sku` = código da ficha. Falha antes do envio =
   volta a `validado` com o motivo. Rede/timeout/5xx ou resposta sem `item_id` = **incerto** (pode ter criado: nunca
   repete; o Admin confere no Seller Center e libera com o `item_id` ou sem). Recusa da Shopee = `erro`. Publicado =
   linha em `anuncio_canal_v2023` (`canal='shopee'`, `conta='aracatuba'`, `controle='noromak'`, estoque **Manual**).

## Estoque (Manual · Automático) — igual ao ML
- Mesmo cartão "Controle de estoque" na aba Anúncios em todos os canais; mesma conta do Automático (Apollo de Araçatuba
  − pendentes da fila de Araçatuba; kit = a peça que dá menos; sem leitura/Apollo sem resposta/leitura > 6 h = não envia).
- Interruptor **próprio da Shopee** (`anuncio_estoque_config_v2026.conta = 'shopee'`): Simulação (padrão) → Lote →
  Automático (ligar = senha + 2FA do OWNER). O rodízio do Apollo é o mesmo (códigos do ML e da Shopee de Araçatuba
  juntos, ≤ 3/min).
- Gravar = `product/update_stock` (`model_id 0`, `seller_stock [{stock}]`, absoluto), `failure_list` não vazia = erro;
  relê `get_item_base_info` e só é ok se o estoque mostrar o número. Anúncio com variações = não entra.

## Fora desta versão
Magalu (aguarda escopos), agente de anúncios para a Shopee (o texto do ML pode ser copiado à mão), puxar os anúncios
antigos da Shopee para a lista, promoção e preço depois de publicado.
