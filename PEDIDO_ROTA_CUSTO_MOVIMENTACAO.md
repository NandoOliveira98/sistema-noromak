# Pedido de rota — Custo da movimentação (`/api/v1/expedicao/custo-movimentacao`)

Pedido da Expedição (Noromak) para o NoromakWeb (Guilherme). Mesma família das portas que já
existem (`/custo`, `/estoque`, `cliente/*`, `romaneio/*`, `caixa/*`): mesmo host
(`127.0.0.1:8000`, só de dentro da VPS) e mesma chave `X-Chave-Expedicao`.

## 1. Para que serve

O **Financeiro** da Expedição calcula o lucro de cada venda do Mercado Livre e da Shopee. O custo
do produto hoje vem de `/custo`, que devolve o **custo médio atual** de cada filial. Isso falha
em dois casos:

- **Estoque zerado:** o custo médio vai a 0 e o Financeiro fica sem custo (ou usava o de outra
  filial, o que estava errado e já foi corrigido).
- **Custo que mudou depois da venda:** a venda de 15 dias atrás passa a usar o custo de hoje.

O custo certo, segundo o dono, é o **"Unitário" da aba Movimentação** da Consulta Gerencial de
Peças do Apollo: o custo com que aquela saída foi baixada no estoque, no dia da venda.

### Exemplo real (22/10/2026)

`6007.003.315.00.8` (Protetor de pó), Araçatuba, outubro de 2026:

| Trans | Data       | Docto | Série | Quant | Unitário | Total  |
|-------|------------|-------|-------|-------|----------|--------|
| P21   | 08/10/2026 | 71345 | 1     | -1,00 | -13,02   | -13,02 |
| P22   | 14/10/2026 | 71668 | 1     | -1,00 | -13,02   | -13,02 |
| P21   | 19/10/2026 | 71927 | 1     | -1,00 | -13,02   | -13,02 |

Saldo inicial do mês: 3 un · R$ 39,06. Saldo final: 0. Hoje `/custo` devolve 0 para Araçatuba
(estoque zerado), e o Financeiro não tem como saber que essas vendas custaram R$ 13,02 cada.

## 2. O que precisamos (sugestão — o formato final é de vocês)

```
GET /api/v1/expedicao/custo-movimentacao?codigos=6007.003.315.00.8,816.3028-0,...&de=2026-10-01&ate=2026-10-22&revenda=1
X-Chave-Expedicao: <a mesma chave de /custo e /estoque>
```

- `codigos`: lista separada por vírgula (até ~200 por chamada).
- `de` / `ate`: período das movimentações (no máximo 180 dias).
- `revenda`: 1 = Araçatuba (padrão; é de onde sai o e-commerce). Aceitar 2 e 3 também ajuda.

Resposta: por código, as **saídas** do período com o unitário de cada uma, mais o último custo
conhecido antes do período (para quando não houver movimentação no intervalo).

```json
{
  "ok": true,
  "revenda": 1,
  "de": "2026-10-01",
  "ate": "2026-10-22",
  "itens": [
    {
      "codigo": "6007.003.315.00.8",
      "saldo_inicial": {"quantidade": 3, "valor": 39.06},
      "ultimo_unitario_antes": 13.02,
      "ultimo_unitario_antes_data": "2026-09-30",
      "movimentos": [
        {"data": "2026-10-08", "transacao": "P21", "documento": "71345", "serie": "1",
         "quantidade": -1, "unitario": 13.02, "total": 13.02},
        {"data": "2026-10-14", "transacao": "P22", "documento": "71668", "serie": "1",
         "quantidade": -1, "unitario": 13.02, "total": 13.02}
      ]
    }
  ],
  "nao_encontrados": [],
  "casas_sem_resposta": []
}
```

## 3. Cada campo e por que precisamos dele

| Campo | Por quê |
|---|---|
| `movimentos[].data` | Casar a saída com a venda do marketplace pelo dia (a Expedição não tem o número da NF/atendimento de toda venda). |
| `movimentos[].unitario` | **O número que importa:** custo de uma unidade naquela saída. Positivo, por favor (o Apollo mostra negativo na saída). |
| `movimentos[].transacao` | Separar venda (P21, P22...) de transferência, devolução e ajuste. Se puderem dizer quais códigos são "venda", melhor. |
| `movimentos[].documento` / `serie` | Conferência e auditoria (o dono confere no Apollo). |
| `ultimo_unitario_antes` | Venda que não tem saída no período (ainda não faturada, faturada em outra data) usa o último custo conhecido. |
| `saldo_inicial` | Opcional. Ajuda a conferir o custo médio do início do mês (39,06 ÷ 3 = 13,02). |

Mesma regra das outras portas: código ou casa que não respondeu **não** vira zero — vem em
`nao_encontrados` / `casas_sem_resposta`, e a Expedição mantém o custo que já tinha.

## 4. Como a Expedição vai usar

- **Venda nova:** quando a venda entra no Noromak, ele guarda o custo do dia (hoje é o custo
  médio de `/custo`). Com a porta, uma rotina noturna troca pelo `unitario` da saída do dia da
  venda, quando ela aparecer no Apollo.
- **Vendas antigas:** uma vez, consulta os últimos 90 dias e corrige o custo das vendas já
  registradas (hoje marcadas "estimado" no Financeiro).
- Ordem de autoridade no Financeiro: custo digitado pelo dono > unitário da movimentação >
  custo médio do dia da venda > estimativa.

A rota é **somente leitura**; nada é gravado no Apollo.

## 5. Limite de chamadas

São ~300 códigos vendidos por mês. Em lote de 200 códigos, a correção inicial são 2–3 chamadas
por mês de histórico, e a rotina diária 1–2 chamadas, de madrugada. Se a consulta for pesada para
o ERP, nos digam o melhor horário e o tamanho de lote.
