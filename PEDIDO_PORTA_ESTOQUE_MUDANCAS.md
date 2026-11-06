# Pedido de porta — Estoque que mudou (`estoque/mudancas`) e aviso de mudança

Pedido da Expedição (Noromak) para o NoromakWeb (Guilherme), 06/11/2026. Mesma família das portas que
já existem (`estoque`, `custo`, `demanda`, `cliente/*`...): mesmo host (`127.0.0.1:8000`, só de dentro da
VPS), mesma chave `X-Chave-Expedicao`, mesmo jeito de responder (`ok`, `erro`). **Só leitura.**

## 1. O problema

Desde 05/11 o Noromak controla o estoque dos anúncios do Mercado Livre que passaram para ele (Plataforma de
anúncios, "Automático"): estoque do anúncio = **disponível do Apollo na filial − o que foi vendido e ainda não
baixou**. A venda do e-commerce o Noromak vê na hora (está na fila dele). O que ele **não** vê é a peça que sai
ou entra **pelo Apollo**: venda de balcão, oficina, nota, transferência, ajuste, entrada de compra.

Hoje, para descobrir isso, o Noromak **relê cada código** na porta `GET /api/v1/expedicao/estoque` em
rodízio, no máximo **3 por minuto** (para não tomar a cota de 20/min que Faturamento, Separação e TikTok
dividem). Funciona, mas:

- com 1 código em Automático ele é relido a cada ~20 s; com 30 códigos, cada um só a cada ~10 min;
- nesse intervalo, uma peça vendida no balcão continua à venda no ML (risco de vender sem ter);
- quase todas as releituras voltam com o mesmo número — é chamada gasta à toa nos dois lados.

O que resolve: o Noromak perguntar **"o que mudou desde a última vez"** e só reler/enviar o que mudou.

## 2. O que precisamos

### 2a. Porta "o que mudou desde" (o principal)

```
GET /api/v1/expedicao/estoque/mudancas?desde=<cursor>&revendas=1,3
X-Chave-Expedicao: <a mesma chave das outras portas>
```

- `revendas`: as filiais (1 = Araçatuba, 3 = Bauru; Ribeirão se um dia precisar). Sem o parâmetro = todas.
- `desde`: o `cursor` que veio na resposta anterior (texto que só vocês entendem: data/hora, id de
  movimento, o que for melhor). **Sem `desde`** = só devolve o cursor de agora, sem itens (é o ponto de
  partida; o Noromak já tem a leitura atual de cada código pela porta `estoque`).

Resposta:

```json
{"ok": true,
 "cursor": "2026-11-06T17:30:00.123|884512",
 "mais": false,
 "itens": [
   {"codigo": "TUNAP109", "revenda": 1, "disponivel": 23, "alterado_em": "2026-11-06T17:29:41-03:00"},
   {"codigo": "0893600022", "revenda": 3, "disponivel": null, "alterado_em": "2026-11-06T17:28:02-03:00"}
 ]}
```

Regras (as mesmas da porta `estoque`, para os dois números baterem):

1. **`disponivel` = o mesmo número da porta `estoque`** (Contábil − Negociação − Fulfillment).
   `null` = não deu para calcular agora — o Noromak **nunca** trata como zero.
2. **Qualquer causa conta**: venda de balcão, oficina, nota, transferência entre filiais, ajuste, entrada de
   compra, e também **atendimento aberto/fechado** se ele mexe na Negociação.
3. **Pode sobrar, não pode faltar**: se ficar mais fácil devolver "códigos que podem ter mudado" (ex.: todo
   código com movimento no período), tudo bem — o Noromak compara com o que já tem. O que não pode é um código
   que mudou ficar de fora.
4. **Paginação**: até 500 itens por resposta; `mais: true` = chamar de novo com o `cursor` novo até vir
   `mais: false`.
5. **Cursor vencido** (ex.: mais de 24 h sem perguntar): responder **HTTP 410**; o Noromak relê tudo pela porta
   `estoque` e pede um cursor novo.
6. Erro de leitura = `ok: false` com `erro` (nunca `itens: []` como se nada tivesse mudado).

O Noromak vai chamar **1 vez por minuto** (cabe com folga na cota) e só para as filiais com anúncio em
Automático. O rodízio atual fica como rede de segurança, bem mais devagar.

### 2b. Aviso de mudança (opcional, se vocês já tiverem um gatilho)

Se no NoromakWeb existe algum momento em que vocês **já sabem** que o estoque de um código mudou (um evento,
uma rotina que roda depois do movimento), dá para avisar o Noromak na hora:

```
POST http://127.0.0.1:8100/expedicao/api/apollo/estoque-aviso
X-Chave-Expedicao: <a mesma chave>
{"revenda": 1, "codigos": ["TUNAP109", "0893600022"]}
```

- É **só um aviso** ("olha estes códigos"): o Noromak relê esses códigos na hora pela porta `estoque` (ou pela
  `mudancas`). Não precisa mandar o número.
- Como aviso que se perde não volta, **a porta 2a continua sendo a garantia**. Se só der para fazer uma, a 2a.
- A rota do lado do Noromak a gente constrói assim que vocês confirmarem o formato.

## 3. Perguntas

1. A Linx dá para vocês algum registro de movimento com data/hora (ou id crescente) por código e filial? É o
   que faria o `cursor` funcionar.
2. **Atendimento aberto entra na Negociação?** (pergunta que já estava pendente, 05/11). Se a integração abre um
   atendimento para cada venda do e-commerce e isso já tira do Disponível, o Noromak desconta a venda duas vezes
   — hoje desconta tudo, por segurança; com a confirmação de vocês ele passa a descontar só o que ainda não tem
   atendimento.
3. Existe hoje, no NoromakWeb ou na Linx, algum aviso (webhook) de estoque que a gente não esteja vendo? A lista
   do `/openapi.json` não mostra nenhum.

Qualquer formato diferente que seja mais fácil para vocês, a gente se adapta — o importante são as regras do
item 2a (mesmo número da porta `estoque`, `null` nunca zero, pode sobrar mas não faltar).
