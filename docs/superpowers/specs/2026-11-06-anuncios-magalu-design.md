# Plataforma de anúncios — criar anúncio e controlar estoque na Magalu (V20.34)

Pedido do dono (06/11): depois da Shopee (V20.32), "vamos para o magalu agr". Mesmas decisões da Shopee: criar o anúncio
do zero pelo Noromak (rascunho → conferir → publicar), **o mesmo estoque de Araçatuba para os 3 marketplaces**, preço
próprio da Magalu com as taxas dela, e "siga sempre suas recomendações". O resto abaixo é decisão minha seguindo o padrão.

Pesquisa: especificação OpenAPI oficial (`developers.magalu.com/apis/products.openapi.yaml`, categorias e traces). Nada
foi visto numa resposta real: a 1ª resposta de cada chamada fica em `anuncio_amostra_v2024` (tipos `magalu_*`) — **conferir
no 1º uso**.

## Permissões (06/11)
`idm client add-scope` com `open:portfolio-skus-seller:write open:portfolio-stocks-seller:write
open:portfolio-categories-channel:read open:trace:read` → AVAILABLE na hora. A reconexão de 06/11 16:39 **não** trouxe as
novas: o token só recebe os `--scopes-default` do app + o `scope` do link de conexão. Por isso `MAGALU_OAUTH_SCOPE` passou a
pedir as quatro; depois de publicar, **reconectar a Magalu uma vez**.

## Diferenças em relação à Shopee
- **Tudo é assíncrono (202 + `trace_id`)**: criar o SKU, o preço e o estoque. O 202 sozinho nunca vira "publicado". O SKU
  enviado é o **código da ficha**, então o Noromak confere na própria Magalu (`GET /seller/v1/portfolios/skus/{sku}`).
- Situações: `rascunho → validado → publicando → aguardando → publicado | erro | incerto`. Em `aguardando` o
  acompanhamento (worker do estoque a cada volta + a tela a cada 5 s, no máximo 1 leitura por rascunho a cada 20 s) lê o
  SKU: apareceu = manda o preço (centavos, `currency: BRL` sempre — o padrão do POST é USD) e o estoque (`AVAILABLE`) e vira
  `publicado` (linha `canal='magalu'`, `conta='aracatuba'`, `anuncio_id` = SKU, `controle='noromak'`, estoque Manual).
  Preço/estoque recusado = continua `aguardando` e tenta de novo (os dois são absolutos). Não apareceu: trace com
  severidade error/critical = `erro`; 30 min sem nada = `incerto` (o Admin confere no painel: "Conferir de novo" ou
  "Não foi criado").
- Sem resposta no envio do SKU **não** é incerto: vira `aguardando` (dá para conferir pelo SKU). Processo caído no meio
  (`publicando` no boot) também vira `aguardando`.
- **SKU que já existe na Magalu não é sobrescrito**: o Publicar lê o SKU antes e recusa (produto de outra ferramenta).
- **Fotos por endereço público JPEG** (não há upload): as do Noromak ficam atrás do login do nginx (o endereço público
  devolve 302). As fotos vêm do **anúncio do ML** desta ficha (`mlstatic.com`), lidas na hora; só https do mlstatic; o
  Conferir baixa cada uma e exige JPEG. Produto sem anúncio no ML com foto = publicar no ML primeiro.
- Categoria por busca de nome (`/category?name=`); atributos de variação (`/attributes`, até 3) e ficha técnica
  (`/datasheet`, até 50) com `required` = obrigatório. Marca é texto (obrigatória), vem da ficha.
- Corpo do SKU só com os campos da spec (`additionalProperties: false`): `dimensions` = produto e pacote (os dois da
  ficha, peso em g, medidas em cm), `identifiers` com o EAN da ficha (`has_ean`), `ncm` de 8 dígitos, `origin national`,
  `condition NEW`, `channels` = Magazine Luiza (`MAGALU_CANAL_ID`).
- Preço: comissão/taxa fixa/frete do Precificador (`marketplaces.magalu`; comissão 0 = sem taxa) ou digitados na tela; só
  servem para a conta do lucro (não vão para a Magalu). Preço "de" opcional (`list_price`).

## Estoque (igual à Shopee)
Conta de estoque própria `magalu` (`ESTOQUE_CONTAS_FORA`), interruptor Simulação → Lote → Automático (OWNER), o mesmo
disponível do Apollo de Araçatuba − pendentes. Gravar = `magalu_estoque_gravar` → `GET /stocks/{sku}` (soma do AVAILABLE do
nosso canal; RESERVED fora; sem número = None, nunca 0) → `PATCH` (ou `POST` sem linha) absoluto, com o `branch` quando há um;
dois centros de distribuição = recusa; relê até ~30 s (assíncrono) e só é ok com o número. A rodada roda numa thread na
volta do worker (como a da Shopee): Magalu lenta não segura o ML.

## Fora desta versão
Agente de anúncios para a Magalu, puxar os produtos antigos da Magalu para a lista, promoção/preço depois de publicado,
`validation-info` (pede `X-Tenant-Id`).
