# Pedido de porta — Peças por grupo e por marca do Apollo (`pecas/grupos`, `pecas/marcas` e `pecas`)

Pedido da Expedição (Noromak) para o NoromakWeb (Guilherme), 06/11/2026. Mesma família das portas que
já existem (`demanda`, `estoque`, `custo`, `cliente/*`...): mesmo host (`127.0.0.1:8000`, só de dentro da
VPS), mesma chave `X-Chave-Expedicao`, mesmo jeito de responder (`ok`, `erro`). **Só leitura.**

## 1. O problema

A Demanda de Compra já lê a demanda pela porta `GET /api/v1/expedicao/demanda?codigos=` (V20.00), e ela
está certa: é a venda da empresa toda (balcão, oficina, telemarketing, e-commerce). O que falta é **saber
quais códigos perguntar.** Hoje a lista sai das regras de fornecedor da tela e dos anúncios do e-commerce,
então **a peça que vende só no balcão e não está no e-commerce nunca entra na demanda** — e é justamente a
que o dono precisa comprar também.

Decisão do dono (06/11): a demanda passa a ser definida **pelos grupos do Apollo, todos eles**. Na tela da
Demanda de Compra o comprador terá **dois filtros, Grupo e Marca**, e escolhe o que quer puxar (só um grupo,
só uma marca, ou os dois juntos — ex.: grupo Lubrificantes + marca Tecfil). O primeiro uso será o grupo
**Lubrificantes**, que no Apollo inclui também os **filtros**.

Por isso precisamos de **todos** os grupos e **todas** as marcas, e de todas as peças de cada um,
inclusive as que nunca foram anunciadas no e-commerce.

**Teste feito em 06/11** (só leitura, pela porta `demanda` que já existe): 200 filtros tirados dos anúncios
do ML; em ~3 min a porta devolveu 109 (cerca de 40 códigos por minuto), com Araçatuba, Ribeirão e Bauru
separados — ex.: CAM1005 = 59 / 0 / 1 e FCD0952 = 47 / 4 / 9 em 12 meses. Ou seja, a leitura da demanda
funciona; só falta a lista completa das peças.

## 2. O que precisamos

### 2a. Lista de grupos (todos)

```
GET /api/v1/expedicao/pecas/grupos
X-Chave-Expedicao: <a mesma chave das outras portas>
```

```json
{"ok": true, "grupos": [{"grupo": "12", "nome": "LUBRIFICANTES", "pecas": 843,
                         "subgrupos": [{"subgrupo": "3", "nome": "FILTROS"}]},
                        {"grupo": "15", "nome": "..."}]}
```

Para a tela mostrar a escolha com o nome que a equipe usa no Apollo. `pecas` (quantas peças ativas) e
`subgrupos` são opcionais.

### 2b. Lista de marcas (todas)

```
GET /api/v1/expedicao/pecas/marcas
```

```json
{"ok": true, "marcas": [{"marca": "45", "nome": "TECFIL", "pecas": 512}, {"marca": "61", "nome": "WEGA"}]}
```

Se a marca no Apollo for só texto (sem código), pode mandar só o `nome`.

### 2c. Peças por grupo e/ou marca

```
GET /api/v1/expedicao/pecas?grupo=12&marca=45&pagina=1
```

- `grupo` e `marca` opcionais, mas **pelo menos um** (os dois juntos = as peças que estão nos dois).
- Aceitar mais de um valor separado por vírgula seria ótimo (`grupo=12,15`), mas não é obrigatório.

```json
{
  "ok": true,
  "pagina": 1,
  "proxima_pagina": 2,
  "total": 843,
  "itens": [
    {"codigo": "0893600022", "descricao": "FILTRO DE OLEO ...",
     "grupo": "12", "nome_grupo": "LUBRIFICANTES", "subgrupo": "3", "nome_subgrupo": "FILTROS",
     "marca": "45", "nome_marca": "TECFIL", "ativo": true}
  ]
}
```

- `codigo` igual ao que a porta `demanda` aceita (é com ele que perguntamos a demanda depois).
- Grupo e marca **em cada peça**: assim a tela filtra aqui sem perguntar de novo.
- Paginado (até 500 por página, como a `demanda`); `proxima_pagina: null` na última.
- Peça inativa/fora de linha: pode vir com `ativo: false` (preferimos receber e filtrar aqui).

**Alternativa que também serve:** `GET /pecas` sem filtro devolver **o cadastro inteiro** paginado (código,
grupo, marca, ativo). A gente guarda e filtra aqui; a lista é lida uma vez por dia.

## 3. Perguntas para vocês

1. Qual campo do cadastro da peça é o "grupo de peças" que a equipe usa no Apollo? Tem subgrupo?
2. A marca tem código próprio no Apollo, ou é texto?
3. Grupo e marca são os mesmos nas três filiais?
4. Quantas peças ativas tem o cadastro inteiro, e o grupo Lubrificantes, mais ou menos? (a ~40 códigos por
   minuto na `demanda`, 1.000 peças levam ~25 min)
5. Filtros e óleos ficam no mesmo grupo e se separam por subgrupo?
6. Alguma limitação de chamadas por minuto nessa leitura, além das 20/min do `custo`/`estoque`?

## 4. Como a Expedição vai usar (para vocês saberem o volume)

- Grupos, marcas e peças são lidos **uma vez por dia** (ou quando o comprador clicar em atualizar) e
  guardados aqui.
- A demanda é pedida **só das peças do grupo/marca que o comprador escolheu**, pela porta `demanda` que já
  existe, em lotes de 200 — nunca o cadastro inteiro de uma vez.
- Nada é gravado no Apollo.
