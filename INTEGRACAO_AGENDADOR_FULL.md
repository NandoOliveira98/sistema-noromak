# Integração com o Agendador Full (V19.03)

A tela **Demanda Full** (`/expedicao/analise-full`) tem a seção **Remessa sugerida**, montada
pelo servidor (`GET /api/full/remessa`). Quando a remessa está **pronta** (situação `pronta`),
o botão **Procurar datas** entrega os itens à extensão do Agendador Full por `window.postMessage`.

A extensão fica **fora do repositório**: `Desktop\NOROMAK_REMESSA_FULL` (**v0.3**, 13/10/2026). O
agendamento da data continua com o Vaguei (`Desktop\VAGUEI`), que é o produto vendável e não conhece
o Noromak. A v0.3 tem um *content script* em `https://noromakweb.com.br/expedicao/analise-full*`
(e no endereço local de teste) que escuta `message` na própria janela e responde.

**O que a v0.3 faz com a lista:** abre o "Planeje seu envio para o Full" do ML
(`/anuncios/lista/shipment_planning/plans`), busca cada MLB
(`POST /stock-management/shipment-planning/api/main/grid?search=MLB…`, corpo `{"products":[]}`) e mostra
um painel. **Só no clique** "Criar envio no ML" chama `plan-validation` → (`star-products-validation`) →
`plan-create` com `{planType:"custom", units:[{id:"MLBU…", shipmentUnits, recommendedQuantity, isStarProduct,
isSuper, benefitId:"-", stockLevel:"-", fcVirtual}]}` e os cabeçalhos `csrf-token`/`x-csrf-token` (meta
`csrf-token`); o ML responde `redirectUrl` `/shipping/inbounds/{id}/hub-v2`. Produto novo (`NOVO:`) e
anúncio com variações ficam fora, para incluir à mão. Levantado em 13/10/2026 pelo código da tela do ML
(API interna: se o ML mudar a tela, a extensão para até ser ajustada).

## Mensagem da tela para a extensão

```js
window.postMessage({
  tipo: "noromak-full-procurar-datas",
  versao: 1,
  remessa_id: 12,
  itens: [
    { anuncio: "MLB123", sku: "0893600022", titulo: "Produto X", quantidade: 30 }
  ]
}, location.origin);
```

- Só entram itens não removidos, confirmados e com quantidade > 0.
- `quantidade` = ajuste do ADMIN quando existe (`qtd_admin`), senão a sugerida (`qtd_sugerida`).
- A extensão deve conferir `event.source === window`, `event.origin` e `tipo`/`versao`.

## Resposta da extensão

Ao receber, a extensão responde na mesma janela:

```js
window.postMessage({ tipo: "noromak-full-procurar-datas-ok", remessa_id: 12 }, location.origin);
```

Sem essa resposta em **3 s**, a tela avisa: "Extensão do Agendador Full não respondeu: instale a v0.3".

## Depois de agendar

A extensão **não** grava no Noromak. Quem agenda marca na tela: **Marcar agendada** (data
AAAA-MM-DD, `POST /api/full/remessa/agendar`) e depois **Marcar recebida**
(`POST /api/full/remessa/situacao`). Mudar este contrato exige atualizar a extensão junto.
