# Pedido de porta — Acertar o valor das peças do atendimento (`atendimento/precos`)

Pedido da Expedição (Noromak) para o NoromakWeb (Guilherme), 28/10/2026. Mesma família das portas que
já existem (`cliente/*`, `atendimentos/*`, `romaneio/*`, `caixa/*`): mesmo host (`127.0.0.1:8000`, só
de dentro da VPS), mesma chave `X-Chave-Expedicao`, mesmo jeito de responder (`ok`, `erro`).

## 1. O problema

O atendimento que a integração da Shopee cria no Apollo vem com o valor **errado** em quase todo pedido
com desconto. A Expedição confere o valor antes de soltar o romaneio e enviar ao caixa — e barra, como
deve. Hoje há 19 pedidos da Shopee parados assim na fila de faturamento.

O valor certo (decisão do dono, 28/10) é o **preço da unidade × quantidade** da venda, o mesmo da coluna
"Preço da unidade" do pedido na Shopee. A integração grava o que o **comprador pagou**: tira o cupom da
loja, o cupom da Shopee e as moedas Shopee. Conferido em 9 pedidos, todos exatos:

| Pedido Shopee  | Venda (certo) | Cupom loja | Cupom Shopee | Moedas | Apollo hoje |
|----------------|--------------:|-----------:|-------------:|-------:|------------:|
| 261028RBEGQ5X1 | 52,80  | 1,59  | —     | 0,01 | 51,20  |
| 261028RSB9AC4M | 25,90  | —     | 2,59  | —    | 23,31  |
| 261028R5AQ2NFU | 24,90  | —     | 3,49  | —    | 21,41  |
| 2610229R5RRRCR | 25,90  | —     | —     | 5,08 | 20,82  |
| 26101919P3CYKJ | 42,80  | —     | 5,14  | 0,90 | 36,76  |
| 261017SE35BAJQ | 256,80 | 17,98 | 11,95 | —    | 226,87 |
| 261016Q6HP72HT | 31,91  | —     | 9,58  | —    | 22,33  |
| 2610191HPA34G4 | 31,91  | —     | —     | 0,23 | 31,68  |
| 261017TUJK1FKC | 31,91  | —     | —     | 0,53 | 31,38  |

No Mercado Livre a diferença é de centavos, no kit: venda 2000019200894362 de R$ 132,90 virou o
atendimento 790938 com 46190130 (1 × 103,13) + 27200153 (4 × 7,45) = **R$ 132,93**.

Também pedimos à Linx que a própria integração grave o valor certo. Enquanto isso não vem, a Expedição
precisa acertar o atendimento antes de soltar o romaneio.

## 2. O que precisamos

Uma porta que **troca o valor unitário** dos itens de um atendimento **aberto** (antes do romaneio).

```
POST /api/v1/expedicao/atendimento/precos
X-Chave-Expedicao: <a mesma chave das outras portas>
```

```json
{
  "revenda": 1,
  "atendimento": 790938,
  "operador": "noromak",
  "motivo": "Valor da venda ML 2000019200894362",
  "valor_esperado_antes": 132.93,
  "itens": [
    {"item": 1, "codigo": "46190130", "quantidade": 1, "unitario": 103.10},
    {"item": 2, "codigo": "27200153", "quantidade": 4, "unitario": 7.45}
  ]
}
```

- `item`: a sequência/identificação do item no atendimento (a que vocês usarem). Se `codigo` +
  `quantidade` já identificam a linha sem ambiguidade, pode ser só isso.
- `valor_esperado_antes`: o total que a Expedição viu. **Se o atendimento não estiver mais com esse
  total, recusar sem gravar** (`ok: false`, `situacao: "mudou"`) — alguém mexeu no meio.
- Mandamos **todos** os itens do atendimento. Item que falta ou sobra = recusar sem gravar.
- Tudo ou nada: se um item falhar, nenhum é gravado.

Resposta:

```json
{
  "ok": true,
  "situacao": "gravado",
  "atendimento": 790938,
  "valor_antes": 132.93,
  "valor_depois": 132.90,
  "itens": [
    {"item": 1, "codigo": "46190130", "quantidade": 1, "unitario": 103.10, "total": 103.10},
    {"item": 2, "codigo": "27200153", "quantidade": 4, "unitario": 7.45, "total": 29.80}
  ]
}
```

O `valor_depois` é **relido do Apollo** depois de gravar — é com ele que a Expedição confere que bateu.

Outras situações (`ok: false`): `romaneio_solto` (já tem romaneio — não mexer), `fechado` (já foi ao
caixa/faturado), `mudou`, `item_divergente`, `nao_encontrado`. Erro de rede numa escrita: a Expedição
trata como **incerto** e não repete sozinha (mesma regra do romaneio e do caixa).

### Leitura

Para calcular os unitários precisamos do **unitário atual e da sequência de cada item**. Se o
`atendimentos/itens` (ou o `romaneio/previa`) já puder devolver `item`, `unitario` e `total` por linha,
basta; senão, por favor incluir.

### Perguntas para vocês

1. Quantas casas decimais o Apollo aceita no unitário? (2 casas resolvem quase tudo; com 4 casas a
   Expedição consegue acertar também quando nenhuma peça tem quantidade 1.)
2. Mudar o unitário mexe em mais alguma coisa do atendimento (desconto, comissão, tabela de preço)?
3. Fica registro no Apollo de quem mudou (o `operador`)?

## 3. Como a Expedição vai usar (para vocês saberem o volume)

Só no botão do romaneio e na fila de faturamento, **antes** de soltar o romaneio, e só quando a diferença
tem explicação (decisão do dono, 28/10):

- **Shopee:** diferença = cupom da loja + cupom da Shopee + moedas do pedido (± R$ 0,05).
- **Mercado Livre:** diferença de até R$ 0,10 (arredondamento do kit).
- Fora disso continua barrando, como hoje.

Divisão: cada peça mantém a parte que já tem no atendimento, ajustada ao total da venda; o centavo que
sobra vai numa peça de quantidade 1. Volume esperado: algumas dezenas por dia, uma chamada por pedido.

---

## Anexo — mensagem para o suporte da Linx (integração Shopee → Apollo)

> Olá. Na integração da Shopee com o Linx DMS (Apollo), o atendimento/pedido está sendo criado com o
> valor que o comprador pagou, descontando o **cupom da Shopee** e as **moedas Shopee**, que são pagos
> pela própria Shopee ao vendedor, e também o **cupom da loja**. Precisamos que o valor dos itens seja o
> **preço de venda da Shopee** (campo "Preço da unidade" do pedido; na API, `order_selling_price` /
> `model_discounted_price` × quantidade), sem esses descontos.
>
> Exemplos:
> - Pedido 261017SE35BAJQ: preço R$ 256,80; cupom da loja R$ 17,98 e cupom da Shopee R$ 11,95; o
>   atendimento entrou com R$ 226,87 (deveria ser R$ 256,80).
> - Pedido 261016Q6HP72HT: preço R$ 31,91; cupom da Shopee R$ 9,58; entrou com R$ 22,33.
> - Pedido 2610229R5RRRCR: preço R$ 25,90; moedas Shopee R$ 5,08; entrou com R$ 20,82.
>
> Existe configuração na integração para considerar o preço de venda sem cupons e moedas? Se não
> existir, podem abrir a solicitação de ajuste?
>
> No Mercado Livre há também uma diferença de centavos em kits: a venda 2000019200894362 de R$ 132,90
> entrou como R$ 132,93 (46190130 a R$ 103,13 + 4 × 27200153 a R$ 7,45). O total do atendimento deveria
> ser igual ao da venda.
