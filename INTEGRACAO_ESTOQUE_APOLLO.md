# Consulta de Estoque Disponível para a Expedição — contrato da rota `/api/v1/expedicao/estoque`

Documento para quem for integrar do lado da Expedição (app do Luis). Descreve uma
rota que **já está no ar em produção**, ligada em 18/09/2026, e que devolve o
estoque físico disponível de uma peça, casa por casa — **sem contar o que foi
para o FULL do Mercado Livre**.

## 1. O que essa rota resolve

Vocês já consomem a rota irmã de custo (`GET /api/v1/expedicao/custo`) — esta é a
mesma família, mesmo host, mesma chave, mesmo jeito de chamar. A diferença é só
o que ela devolve: em vez de custo médio, devolve **quanto tem na prateleira**.

O número (`disponivel_total`) responde exatamente a pergunta que originou o
pedido: quanto dá para planejar mandar para o FULL, sem contar o que já está lá.

## 2. Como chamar

```
GET /api/v1/expedicao/estoque?codigo=<CODIGO_DA_PECA>
Host: 127.0.0.1:8000        (mesmo host que /custo já usa — a rota só responde
                              a quem chama de DENTRO da própria VPS, nunca da
                              internet; se o app de vocês já fala com /custo
                              nesse endereço, não muda nada aqui)
X-Chave-Expedicao: <a mesma chave de serviço que /custo já usa>
```

- `codigo` (obrigatório): o código da peça, exatamente como aparece no sistema
  (ex.: `4045`). Busca é EXATA — não é busca por aproximação.
- `operador` (opcional): identifica quem/o quê disparou a consulta do lado de
  vocês, só para a nossa trilha de auditoria. Pode omitir.

⚠️ Não incluímos a chave de serviço neste documento por segurança — ela já é a
mesma que vocês usam em `/custo` hoje. Se precisar reemitir, é um pedido
separado, fora deste texto.

## 3. Exemplo real (testado em produção, 18/09/2026)

Requisição:
```
GET /api/v1/expedicao/estoque?codigo=4045
```

Resposta (HTTP 200):
```json
{
  "ok": true,
  "codigo": "4045",
  "descricao": "HS334 VEDADOR ANAEROBICO ELIMINA JUNTAS 50G",
  "unidade": "PC",
  "disponivel_total": 34,
  "casas_sem_resposta": [],
  "revenda_principal": 1,
  "estoques": [
    {"revenda": 1, "disponivel": 34.0, "localizacao": "ZZ.9.14.A.99", "tem_cadastro": true, "erro": null},
    {"revenda": 2, "disponivel": 0.0,  "localizacao": "ZZ.3.1.B.2",   "tem_cadastro": true, "erro": null},
    {"revenda": 3, "disponivel": 0.0,  "localizacao": "GV.1.4.B.2",   "tem_cadastro": true, "erro": null}
  ]
}
```

## 4. O que cada campo quer dizer

| Campo | Tipo | O que é |
|---|---|---|
| `ok` | bool | `false` só quando a peça não existe em NENHUMA casa, ou o ERP recusou a consulta inteira. Nesse caso a resposta vem `{"ok": false, "erro": "..."}` — sem os campos abaixo. |
| `codigo` / `descricao` / `unidade` | texto | identificação da peça; `unidade` é unidade de MEDIDA ("PC", "LT", "KG"...), nunca filial. |
| `disponivel_total` | número | **o número que interessa**: soma do disponível das casas que responderam. Já é o físico na prateleira — ver seção 6. |
| `casas_sem_resposta` | lista de números | revendas que NÃO responderam nesta consulta (ERP fora do ar naquela casa, timeout, etc.). Lista **vazia** = o total é da rede inteira. Lista **com número dentro** = o total é um PISO (pode ter mais peça na casa que não respondeu) — trate como "pelo menos isso", não como "exatamente isso". |
| `revenda_principal` | número | qual revenda é a matriz/principal da concessionária. |
| `estoques[]` | lista | uma linha por revenda (hoje sempre 1, 2 e 3) |
| `estoques[].disponivel` | número ou `null` | quanto tem NAQUELA casa. **`null` não é zero** — ver a tabela abaixo. |
| `estoques[].localizacao` | texto | a prateleira, como o ERP cadastrou (ex. `ZZ.9.14.A.99`). Pode vir `....` quando a casa não cadastrou localização — não é erro nosso, é assim que o ERP devolve. |
| `estoques[].tem_cadastro` | `true` / `false` / `null` | ver tabela abaixo |
| `estoques[].erro` | texto ou `null` | só vem preenchido quando `tem_cadastro` é `null` |

### Os três estados de `tem_cadastro` (importante — não simplifique para booleano)

| `tem_cadastro` | `disponivel` | Significado | O que fazer |
|---|---|---|---|
| `true` | número real | a casa tem a peça cadastrada; o número é o estoque de lá | usar o número |
| `false` | `0` | perguntamos e **essa casa não tem essa peça cadastrada** | zero de verdade, pode confiar |
| `null` | `null` | **a casa não respondeu** (ERP fora do ar, timeout) | **NÃO tratar como zero** — a peça pode existir lá e a consulta simplesmente falhou. Está fora do `disponivel_total` de propósito, e o número dessa casa aparece em `casas_sem_resposta` |

Ler `null` como `0` é o erro mais caro possível aqui: faria vocês pularem uma peça
que na verdade existe, só porque uma casa demorou para responder.

## 5. Erros

| HTTP | Quando | O que fazer |
|---|---|---|
| `200` com `"ok": false` | peça não existe em nenhuma casa, ou erro geral no ERP | mostrar `erro` do corpo, não é falha de vocês |
| `401` | chave errada/ausente | conferir o cabeçalho `X-Chave-Expedicao` |
| `404` | rota desligada, OU a chamada não veio de dentro da VPS | isso não deveria acontecer se vocês chamam do mesmo jeito que já chamam `/custo` hoje — se aparecer, avisar a gente |
| `429` | passou do limite de chamadas | ver limite abaixo |

## 6. O que esse número JÁ desconta, e o que ele NÃO conta

**JÁ desconta o FULL do Mercado Livre.** Medido em 18/09/2026 direto na tela do
ERP (Consulta Gerencial de Peças): o "Disponível" tem, por trás, um balde próprio
chamado `Fulfillment` que já é subtraído antes do número chegar até aqui —
conferimos a conta exata (Contábil − Negociação − Fulfillment − Fulfillment Neg.
= Disponível) e ela bateu. **Vocês não precisam subtrair nada do que já foi
mandado para o FULL — o número já vem líquido disso.**

**NÃO conta o que vai chegar.** Pedido de compra em aberto (comprado, ainda não
chegou na loja) não entra em `disponivel_total`. Isso é proposital: "disponível"
aqui significa disponível AGORA, na prateleira — não "vai ter".

**NÃO conta o que está em trânsito** entre as casas da concessionária.

## 7. Limite de chamadas

20 chamadas por minuto, **somado com a rota `/custo`** (é o mesmo balde — do
nosso lado as duas contam como uma só origem). Cada chamada aqui já pergunta às
3 revendas de uma vez, então não precisa (e não deve) chamar uma vez por revenda.

## 8. Se algo não bater

Isso é o contrato de UMA rota que já está em produção e testada (25 casos
automatizados do lado de vocês recebendo dado real). Se o comportamento
observado divergir de qualquer coisa escrita aqui, o problema é nosso — avisem
antes de contornar do lado de vocês.
