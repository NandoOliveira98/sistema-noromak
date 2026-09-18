# Pedido de rota — Demanda de compra (`/api/v1/expedicao/demanda`)

Pedido da Expedição (Noromak) para a equipe do Apollo. Mesma família de
`/api/v1/expedicao/custo` e `/api/v1/expedicao/estoque`: mesmo host
(`127.0.0.1:8000`, só de dentro da VPS) e mesma chave `X-Chave-Expedicao`.

## 1. Para que serve

Montar a **demanda de compra** por marca: quanto cada peça vendeu num período,
quanto tem hoje e quanto já foi comprado e ainda não chegou. A partir disso a
Expedição calcula a necessidade de compra e monta o pedido até o valor mínimo
de cada fornecedor (ex.: Schulz, R$ 5.000).

Hoje isso não é possível: `/custo` e `/estoque` respondem **uma peça por
chamada**, dentro do limite de 20 chamadas/min. Uma marca com 500 peças levaria
quase uma hora. E nenhuma das duas devolve venda.

## 2. O que precisamos (sugestão — o formato final é de vocês)

```
GET /api/v1/expedicao/demanda?marca=SCHULZ&dias=60
GET /api/v1/expedicao/demanda?codigos=4045,0893556005,...&dias=60
X-Chave-Expedicao: <a mesma chave de /custo e /estoque>
```

- `marca` **ou** `codigos` (lista separada por vírgula, até ~500).
- `dias`: período de vendas contado até hoje (ex.: 30, 60, 90, 180).
- `revenda` (opcional): 1, 2, 3 ou omitido para a rede inteira.

Resposta: **uma linha por peça**, numa chamada só.

```json
{
  "ok": true,
  "dias": 60,
  "data_inicio": "2026-07-20",
  "data_fim": "2026-09-18",
  "itens": [
    {
      "codigo": "4045",
      "descricao": "HS334 VEDADOR ANAEROBICO ELIMINA JUNTAS 50G",
      "marca": "HS BOND",
      "unidade": "PC",
      "vendido": 100,
      "vendido_por_revenda": {"1": 80, "2": 15, "3": 5},
      "disponivel": 34,
      "compra_em_aberto": 50,
      "custo_medio": 16.32,
      "curva_abc": "A",
      "dias_sem_estoque": 3
    }
  ],
  "casas_sem_resposta": []
}
```

## 3. Cada campo e por que precisamos dele

| Campo | Por quê |
|---|---|
| `vendido` | Quantidade vendida no período, **todos os canais** (balcão, oficina, marketplaces). É a base da média diária. Devoluções já descontadas, se possível. |
| `vendido_por_revenda` | Saber de qual casa sai a venda (opcional, mas ajuda a decidir onde comprar). |
| `disponivel` | O mesmo número de `/estoque` (`disponivel_total`, já líquido do FULL). |
| `compra_em_aberto` | **Importante.** O `/estoque` não conta pedido de compra que ainda não chegou. Sem esse número o sistema pediria de novo o que já está a caminho. |
| `custo_medio` | O mesmo de `/custo`, para somar o valor do pedido até o mínimo do fornecedor. |
| `marca` | Para agrupar o pedido por fornecedor. |
| `curva_abc` | Priorizar o que entra primeiro quando o mínimo aperta. |
| `dias_sem_estoque` | Opcional. Se a peça ficou zerada parte do período, a venda subestima a demanda real. |

Mesma regra do `/estoque`: casa que não respondeu **não** vira zero — vem em
`casas_sem_resposta` e o número é tratado como piso.

## 4. Como a Expedição vai usar

```
venda média diária  = vendido ÷ dias
necessidade         = média diária × (prazo de entrega + dias de cobertura)
                      − disponivel − compra_em_aberto
```

Prazo de entrega, cobertura e pedido mínimo ficam configurados por marca do
lado da Expedição. A rota é **somente leitura**; nada é gravado no Apollo.

## 5. Limite de chamadas

Com a rota em lote, uma marca inteira é uma chamada. Se a consulta for pesada,
podemos rodar uma vez por dia de madrugada e guardar o resultado — nos digam o
que é melhor para o ERP.
