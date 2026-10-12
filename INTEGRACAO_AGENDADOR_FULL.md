# Integração com o Agendador Full (V19.03)

A tela **Demanda Full** (`/expedicao/analise-full`) tem a seção **Remessa sugerida**, montada
pelo servidor (`GET /api/full/remessa`). Quando a remessa está **pronta** (situação `pronta`),
o botão **Procurar datas** entrega os itens à extensão do Agendador Full por `window.postMessage`.

A extensão fica **fora do repositório**: `Desktop\NOROMAK_AGENDADOR_FULL`. A partir da **v0.3**
ela precisa de um *content script* que rode em `https://noromakweb.com.br/expedicao/analise-full*`
(e no endereço local de teste), escute `message` na própria janela e responda.

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
