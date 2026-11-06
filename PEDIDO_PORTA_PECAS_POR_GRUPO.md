# Pedido de porta — Peças por grupo do Apollo (`pecas/grupos` e `pecas`)

Pedido da Expedição (Noromak) para o NoromakWeb (Guilherme), 06/11/2026. Mesma família das portas que
já existem (`demanda`, `estoque`, `custo`, `cliente/*`...): mesmo host (`127.0.0.1:8000`, só de dentro da
VPS), mesma chave `X-Chave-Expedicao`, mesmo jeito de responder (`ok`, `erro`). **Só leitura.**

## 1. O problema

A Demanda de Compra já lê a demanda pela porta `GET /api/v1/expedicao/demanda?codigos=` (V20.00), e ela
está certa: é a venda da empresa toda (balcão, oficina, telemarketing, e-commerce). O que falta é **saber
quais códigos perguntar.** Hoje a lista sai das regras de fornecedor da tela e dos anúncios do e-commerce,
então **a peça que vende só no balcão e não está no e-commerce nunca entra na demanda** — e é justamente a
que o dono precisa comprar também.

O dono quer escolher um **grupo de peças do Apollo** e ver a demanda de todas as peças do grupo. O
primeiro caso é o grupo **Lubrificantes**, que no Apollo inclui também os **filtros**: precisamos de
**todas** as peças do grupo, inclusive as que nunca foram anunciadas no e-commerce.

**Teste feito em 06/11** (só leitura, pela porta `demanda` que já existe): 200 filtros tirados dos anúncios
do ML; em ~3 min a porta devolveu 109 (cerca de 40 códigos por minuto), com Araçatuba, Ribeirão e Bauru
separados — ex.: CAM1005 = 59 / 0 / 1 e FCD0952 = 47 / 4 / 9 em 12 meses. Ou seja, a leitura da demanda
funciona; só falta a lista completa das peças do grupo.

## 2. O que precisamos

### 2a. Lista de grupos

```
GET /api/v1/expedicao/pecas/grupos
X-Chave-Expedicao: <a mesma chave das outras portas>
```

```json
{"ok": true, "grupos": [{"grupo": "12", "nome": "LUBRIFICANTES", "pecas": 843}, {"grupo": "15", "nome": "..."}]}
```

Para a tela mostrar a escolha com o nome que a equipe usa no Apollo. `pecas` (quantas peças ativas) é
opcional.

### 2b. Peças de um grupo

```
GET /api/v1/expedicao/pecas?grupo=12&revenda=1&pagina=1
```

```json
{
  "ok": true,
  "grupo": "12",
  "nome_grupo": "LUBRIFICANTES",
  "pagina": 1,
  "proxima_pagina": 2,
  "total": 843,
  "itens": [
    {"codigo": "0893600022", "descricao": "FILTRO DE OLEO ...", "marca": "TECFIL", "ativo": true,
     "subgrupo": "OLEO"}
  ]
}
```

- `codigo` igual ao que a porta `demanda` aceita (é com ele que perguntamos a demanda depois).
- `revenda` opcional: só se o cadastro do grupo mudar por filial. Se for igual nas três, pode ignorar.
- Paginado (até 500 por página, como a `demanda`); `proxima_pagina: null` na última.
- Peça inativa/fora de linha: pode vir com `ativo: false` (preferimos receber e filtrar aqui).
- `marca` e `subgrupo` só se for barato trazer; não são obrigatórios.

**Alternativa que também serve:** a própria `demanda` aceitar `grupo=12` no lugar de `codigos=`. Mas a
lista separada é melhor para nós: a tela mostra a peça mesmo sem demanda nenhuma (estoque parado).

## 3. Perguntas para vocês

1. Qual campo do cadastro da peça é o "grupo de peças" que a equipe usa no Apollo? Tem subgrupo?
2. O grupo é o mesmo nas três filiais?
3. Quantas peças tem o grupo Lubrificantes, mais ou menos? (para o tamanho da página e da rodada da
   demanda: a ~40 códigos por minuto, 1.000 peças levam ~25 min)
4. Filtros e óleos ficam no mesmo grupo e se separam por subgrupo? Se sim, mandar o subgrupo em cada peça.
5. Alguma limitação de chamadas por minuto nessa leitura, além das 20/min do `custo`/`estoque`?

## 4. Como a Expedição vai usar (para vocês saberem o volume)

- A lista do grupo é lida **uma vez por dia** (ou quando o dono clicar em atualizar) e guardada aqui.
- A demanda dos códigos continua pela porta `demanda` que já existe, em lotes de 200, na mesma rodada de
  hoje — só aumenta o número de códigos.
- Nada é gravado no Apollo.
