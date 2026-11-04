# Pedido ao NoromakWeb: liberar o agente de imagens no nginx (V20.17)

**Para:** quem cuida do nginx da VPS (portão de login do NoromakWeb)
**De:** Expedição Noromak — 04/11/2026

## O que é

A Expedição ganhou o **Estúdio de Imagens** (`/expedicao/imagens`). Quem processa as fotos é um
programa no PC do dono (o "agente de imagens"), que conversa com a Expedição pela internet. Ele não
tem sessão de navegador, então hoje o portão de login do NoromakWeb devolve
`302 → https://noromakweb.com.br/login?next=/expedicao/` e o agente nunca chega à Expedição.

## O que precisa

Liberar do portão de login **só estes 5 endereços exatos** (revisão de 04/11: nada de prefixo):

```
/expedicao/api/imagens/agente/ping
/expedicao/api/imagens/agente/proximo
/expedicao/api/imagens/agente/arquivo
/expedicao/api/imagens/agente/analise
/expedicao/api/imagens/agente/resultado
```

Os outros dois do mesmo caminho (`/agente/config`, que mostra o token, e `/agente/token-novo`, que troca o
token) são do perfil Admin/OWNER e **continuam atrás do login**.

A segurança continua na Expedição: os 5 exigem `Authorization: Bearer <token do agente>` (token próprio,
comparado com `hmac.compare_digest`; trocar exige o OWNER). Sem token ou com token errado, a Expedição
responde 401. O agente não segue redirecionamento (o token nunca vai para a tela de login).

Exemplo do bloco (ajustar ao `proxy_pass`/cabeçalhos que vocês já usam para `/expedicao/`, só sem o
`auth_request`/redirecionamento de login). `location ~` com a lista fechada, para não abrir mais nada:

```nginx
location ~ ^/expedicao/api/imagens/agente/(ping|proximo|arquivo|analise|resultado)$ {
    proxy_pass http://127.0.0.1:8100;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 45m;     # o agente devolve a imagem pronta + recortes (até ~42 MB; a Expedição recusa acima)
    proxy_read_timeout 180s;
}
```

## Conferir também (tela, com login)

A tela envia fotos em `/expedicao/api/imagens/enviar` (até 25 MB de fotos ≈ 34 MB em base64; a
Expedição recusa acima de 36 MB). Se o `client_max_body_size` do bloco `/expedicao/` for menor que
**40m**, o nginx recusa antes (413 do nginx).

## Como testar depois

Sem token, deve chegar à Expedição e voltar **401** (não 302):

```
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://noromakweb.com.br/expedicao/api/imagens/agente/ping
```

E os do Admin continuam no portão (devem voltar **302** para o login):

```
curl -s -o /dev/null -w "%{http_code}\n" https://noromakweb.com.br/expedicao/api/imagens/agente/config
```
