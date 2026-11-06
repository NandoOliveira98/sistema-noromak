# Agente de anúncios — o agente do Estúdio ajuda a criar anúncios (desenho)

Data: 06/11/2026. Entrevista com o dono (grill-me). Base: Plataforma de anúncios (V20.23–V20.28, criar anúncio
no ML = V20.24) e Estúdio de Imagens (V20.17–V20.21, agente no PC do dono com Claude pela API).

## Ideia

No rascunho do anúncio, o Admin clica em **"Pedir ao agente"**. O mesmo agente do Estúdio (PC do dono, fila do
servidor, Claude pela API) devolve uma proposta de **título, descrição e atributos obrigatórios**. O Admin revisa,
ajusta e aceita. "Conferir no ML" e "Publicar" continuam **só no clique** — o agente nunca cria nada no ML. O
servidor continua sem dependência externa (o Claude roda no agente).

## Decisões do dono (06/11)

1. **Escopo:** título + descrição + atributos obrigatórios. A **categoria** continua vindo do ML
   (`/domain_discovery` pelo título, como hoje).
2. **Um pedido, tudo junto:** o agente propõe título e descrição; o servidor pede ao ML as categorias para o
   título recomendado e escolhe a 1ª sugestão (o Admin pode trocar); com a categoria, o servidor pede os
   atributos obrigatórios ao ML e devolve o trabalho ao agente na mesma volta (2ª etapa automática). A proposta
   chega completa. Trocar a categoria pede **só** os atributos de novo.
3. **Fontes:** ficha do produto (marca, EAN, medidas…), fotos da ficha (rótulo), print de catálogo/ficha técnica
   (papel `catalogo`, como no Estúdio) e **pesquisa na internet** pelo nome/link do fornecedor. Confiança:
   **ficha do Admin > rótulo/catálogo > internet**. Cada campo volta com a origem (`ficha`, `rotulo`,
   `catalogo`, `internet` + link "confira"). Não achou = **em branco**, nunca inventado.
4. **Estilo:** as regras dos comandos `/titulo` e `/descricao` deste PC.
   - Título ≤ 60 caracteres: produto + marca + modelo/linha + característica + volume; kit "Kit A + B Marca";
     sem preço, frete, promoção, emoji, caixa alta total, sinais, outras marcas. **3 opções** com a contagem e a
     recomendada marcada; o Admin escolhe uma. A categoria sai da recomendada.
   - Descrição em texto puro: abertura (2 linhas), BENEFÍCIOS, COMO USAR, ONDE APLICAR / NÃO APLICAR,
     ESPECIFICAÇÕES, CONTEÚDO DA EMBALAGEM, fechamento curto. O que faltar vem `[CONFIRMAR]`, nunca inventado.
5. **Modelo e custo:** o mesmo modelo escolhido no Estúdio (`imagem_config_v2017`, hoje Haiku) e o mesmo
   crédito de US$ 100 que renova dia 19; cada entrega grava em `imagem_gasto_v2017` marcada **"anúncio"**, para o
   gasto aparecer separado.
6. **Onde fica:** o aceito vai para o **rascunho** (`anuncio_rascunho_v2024`) e preenche **só os campos vazios da
   ficha** (descrição, marca, modelo, volume…) com origem **admin** — foi o Admin quem aprovou. Campo que a
   ficha já tem nunca é trocado.
7. **Quem pode:** pedir e aceitar = **perfil Admin** (como publicar). Supervisor e Consulta só veem a proposta.
8. **Kit:** o servidor manda ao agente a composição do Cadastro de Kits (peças e quantidades) e, de cada peça,
   as fotos e os dados da ficha dela; peça sem ficha nem foto vai só com código e descrição do Precificador.

## Regras que valem sem precisar decidir

- Atributo com lista de valores do ML só aceita um `value_id` da lista que o ML devolveu; texto livre só onde
  o ML aceita texto. Valor fora da lista volta em branco.
- Uma proposta por rascunho de cada vez; pedido novo com um em andamento = 409. Rascunho editado durante o
  pedido não é sobrescrito: a proposta fica ao lado, o Admin aplica campo a campo (a `versao` do rascunho vale).
- O PC desligado deixa o pedido na fila (mesma reserva/vencimento do Estúdio); a tela mostra "aguardando o agente".
- Só Mercado Livre por enquanto (criar anúncio só existe para o ML).

## A conferir na implementação

- Rotas novas do agente precisam passar pelo portão do nginx, como as do Estúdio (`PEDIDO_NGINX_AGENTE_IMAGENS.md`).
- O agente roda numa etapa nova da mesma fila (`imagem_trabalho_v2017`, tipo `anuncio`) ou numa fila própria —
  decidir no plano pelo que mexe menos no agente que já roda.
