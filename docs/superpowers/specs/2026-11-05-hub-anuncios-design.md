# Plataforma de anúncios e integração (substituta da Alephee) — desenho

Conversa com o dono em 05/11/2026. Telas de referência (protótipos descartáveis, não código):
ficha do produto com todos os canais e tela de kits, feitas no padrão Noromak (`nk-ui.css`).

## 1. Por quê

Hoje a Noromak usa a **Alephee** como hub entre os marketplaces e o Apollo. Ela:
- cria os anúncios (Araçatuba: Mercado Livre, Shopee, Magalu; Bauru: Mercado Livre);
- controla o estoque dos anúncios a partir do Apollo — **é aqui que dói**: lenta, estoque desencontrado;
- quando vende, cria o cliente no Apollo e o atendimento que compromete as peças — **quando funciona**:
  pedido que não cai no Apollo.

Um defeito visto no próprio print do dono: o kit de 3 litros (KIT32052153) está com **quantidade 1** do
componente na Alephee — mostra 24 kits quando há 8 e compromete 1 litro por venda.

A Alephee também **não tem** Amazon, TikTok e outros canais que a Noromak quer abrir.

**Objetivo:** construir, **dentro do Noromak**, a plataforma que faz o papel da Alephee e mais:
- uma **ficha por produto** com **todos os anúncios de todos os canais** dentro dela (a Alephee não tem);
- estoque certo e explicado, venda que sempre chega ao Apollo (ou fica visível que não chegou);
- abrir canais novos sem tela nova por canal.

Depois de funcionar para a Noromak, pode virar produto para vender a outras lojas. **Isso não é desta
fase**: nada aqui precisa ser multiempresa. Só não amarrar à toa o que for fácil manter genérico (ex.:
o canal é um dado, não um `if` espalhado).

## 2. Metas, não-metas e o que seria fracasso

**Metas**
1. Ficha do produto (peça ou kit) como centro: dados, fotos, ficha técnica, kits, anúncios por canal.
2. Criar anúncio no Mercado Livre a partir da ficha (categoria, atributos obrigatórios, fotos,
   descrição, preço, tipo, envio), e **puxar os anúncios existentes** sem perder histórico.
3. Estoque dos anúncios controlados pela plataforma = Apollo da filial − o que vendeu e não baixou,
   kit pela peça que dá menos kits; cada envio registrado.
4. Venda de anúncio controlado pela plataforma → cliente + atendimento no Apollo pelas portas do
   Guilherme, entrando no fluxo que já existe (romaneio, caixa, Faturamento).
5. Depois do ML: Shopee, Magalu, TikTok, Amazon, na mesma ficha.

**Não-metas (agora)**
- Multiempresa / vender o produto.
- Cancelar atendimento no Apollo sozinho (só avisa).
- Mudar preço sozinho (só o Admin muda; a plataforma sugere).
- Mexer em estoque do Full (quem controla é o ML).
- Desligar a Alephee de uma vez.

**Fracasso, mesmo funcionando tecnicamente**
- Duas ferramentas mexendo no estoque do mesmo anúncio (Noromak e Alephee brigando).
- Pôr zero no anúncio porque o Apollo não respondeu.
- Venda que não chega ao Apollo **sem ninguém ver**.
- Atendimento duplicado no Apollo para a mesma venda.
- Kit vendendo com quantidade de componente errada.
- Qualquer coisa que reverta decisão do ADMIN (regra da seção 1 do CLAUDE.md).

## 3. Decisões do dono (05/11)

| Assunto | Decisão |
|---|---|
| Onde | **Dentro do Noromak** primeiro; separar para vender é depois. |
| Ordem | **Mercado Livre primeiro** (integração mais robusta): montar o ecossistema e testar criar **um anúncio novo** pela plataforma — estoque, fotos, campos obrigatórios, preço, taxas. Funcionou → replicar para os outros canais. |
| Anúncios antigos | **Puxar** os existentes (mesmo MLB, sem perder histórico de vendas) e migrar **aos poucos**. |
| Convivência com a Alephee | A Alephee só cria atendimento/controla anúncio que foi **ativado nela** para aquele produto. Anúncio novo da plataforma não é ativado lá. Migrar = desativar na Alephee e ativar na plataforma, anúncio por anúncio. (Como desativar na Alephee: verificar antes do 1º anúncio migrado.) |
| Quem usa | Só perfil **Admin** por enquanto; quem mais pode criar fica para depois. |
| Estoque | **Cada filial puxa do seu Apollo**: Araçatuba (revenda 1) para ML/Shopee/Magalu de Araçatuba; Bauru (revenda 3) para os canais de Bauru. Canais da mesma filial **dividem** o estoque. |
| Folga | **Nenhuma**: mostra tudo até zerar. |
| Venda cancelada com atendimento aberto | **Não cancela sozinho**: avisa, o cancelamento é à mão no Apollo. |
| Peso e medidas | Digitados à mão por produto; dá para **exportar da Alephee** (a confirmar). |
| NCM / EAN | Do **Apollo** e da embalagem do produto. |
| Tela | **Padrão Noromak**. Igual à Alephee em estrutura: abre o produto e dentro dele estão as seções e os kits. |
| Vários canais | **Dentro da ficha do produto ficam os anúncios de todos os marketplaces** (produto X = anúncio ABC no ML, D na Shopee, E no TikTok, F na Magalu…). |
| Kits | Dentro do produto, uma área de **kits que usam a peça**; cada kit tem a lista de componentes com **quantidade editável**. Kit de 3 = 3 unidades: estoque ÷ 3, e a venda **compromete 3** no Apollo. |
| Promoções | O preço da ficha é o **preço cheio** do anúncio (o "de"). As Promoções automáticas continuam cuidando do desconto por cima, como hoje; a ficha mostra a promoção valendo e o preço com desconto. |
| Preço | Cada canal tem o **seu preço, definido pelo Admin**. A plataforma mostra o **preço ideal daquele canal** (margem do Precificador com as taxas reais do canal) e um botão "usar o ideal". **Nunca muda preço sozinha.** (Proposta do Claude aceita pelo dono ao autorizar este documento.) |

## 4. Como funciona

### 4.1 Ficha do produto

Uma ficha por **código Apollo** (peça) e uma por **kit**. Abas: **Ficha do produto · Kits · Anúncios em
todos os canais**. Na ficha, seções com índice à esquerda e bolinha verde/amarela/vermelha por seção:
Produto (categoria, nome, marca, descrição) · Componentes (só kit) · Fotos e vídeo · Ficha técnica ·
Variações · Aplicações · Estoque · Preço e taxas · Preferências por canal.

Campos novos que o Noromak não tem hoje: **peso, medidas da embalagem, NCM, modelo, EAN** (o EAN existe
em `conf_produtos`, mas não ligado a uma ficha), descrição longa, atributos por categoria.

Fontes de cada campo, em ordem de autoridade (cada campo mostra de onde veio — "da ficha", "do Apollo",
"da Alephee", "do anúncio"):
1. O que o Admin digitou na plataforma — **vence sempre** e não é sobrescrito por importação.
2. Planilha exportada da Alephee.
3. O que veio dos anúncios puxados do ML.
4. Cadastro do Apollo (descrição, NCM, EAN) — depende de porta nova (seção 6).

Custo = regra do Financeiro (custo de **Araçatuba**, `_financeiro_custo_aracatuba`/Precificador). Preço
ideal = margem-alvo do Precificador com as taxas do canal.

### 4.2 Kits

- Aba "Kits" na ficha da peça: lista a unidade sozinha e todos os kits que usam a peça, com composição
  ("3 × esta peça") e quantos kits dá para montar.
- Ao abrir um kit: tabela de componentes (código Apollo, peça, **quantidade editável**, disponível, a
  baixar, dá para montar, custo unitário, custo no kit), "+ Adicionar componente", "Remover".
- Estoque do kit = menor `floor((disponível − a baixar) ÷ quantidade)` entre os componentes.
- Custo do kit = soma de quantidade × custo de cada peça.
- Venda de 1 kit → o atendimento recebe as peças **abertas**, com a quantidade × unidades vendidas.
- **Uma lista só de kits**: o Noromak já tem o Cadastro de Kits (`_load_kit_catalog`,
  `full_kit_compositions`), usado pela Conferência, Financeiro, Demanda e Shopee (`_shopee_expand_kit_lines`).
  A tela de kits da plataforma **edita esse mesmo cadastro**, não uma cópia (**decisão do dono, 05/11**):
  a mudança vale na hora para anúncios, Conferência, Shopee, Financeiro e Demanda. Kits que hoje só
  existem pela regra do nome (`KIT2-X`, "A/B") ou como produto comum no Precificador ganham entrada no
  Cadastro de Kits quando são abertos/salvos na ficha. Construtor: conferir o efeito em pedidos já na fila
  (não podem perder bip — bip é monotônico) antes de gravar.
- Salvar quantidade recalcula o estoque de todos os anúncios daquele kit.

### 4.3 Anúncios em todos os canais

Tabela dentro da ficha, uma linha por anúncio de qualquer canal/conta:
canal e conta/filial · id do anúncio · **quem controla** (Noromak / Alephee / Full) · situação · preço ·
taxas do canal · lucro · margem · estoque · vendas 30 dias · último envio de estoque (de → para, aceito
ou recusado e por quê). Canal ainda sem anúncio aparece com "Publicar". Erro do canal (ex.: "pausado:
falta INMETRO", "envio recusado") fica na linha.

"**Passar para o Noromak**" na linha de anúncio da Alephee: marca o anúncio como controlado pela
plataforma. Só depois de o Admin confirmar que desativou na Alephee (a tela pede essa confirmação).

Um anúncio pertence a **uma** ficha (peça ou kit); a ficha pode ter vários anúncios no mesmo canal
(ex.: 3 L e 4 L são fichas diferentes; Clássico e Premium do mesmo kit são dois anúncios da mesma ficha).

### 4.4 Criar anúncio no Mercado Livre

Fluxo a partir da ficha, conta escolhida (Araçatuba no piloto):
1. **Categoria**: sugerida pelo ML (`/sites/MLB/domain_discovery/search` pelo nome) e trocável.
2. **Atributos** da categoria (`/categories/{id}/attributes`; ficha técnica do domínio): mostra quantos
   obrigatórios estão preenchidos (ex.: 16/19) e quais faltam; preenche com o que a ficha já tem.
   GTIN: obrigatório quando a categoria exige; senão `EMPTY_GTIN_REASON` (kit = "Kit").
3. **Fotos**: da ficha (Estúdio de Imagens ou enviadas). O Estúdio fica atrás do login do nginx, então o
   servidor envia o arquivo ao ML (`POST /pictures/items/upload`, multipart) — não por link.
4. **Descrição** (`/items/{id}/description`, texto simples).
5. **Preço, tipo** (Clássico/Premium), **envio** (ME2/Flex, frete grátis), garantia.
6. **Conferir** (`POST /items/validate`) — mostra os erros do ML na tela antes de publicar.
7. **Publicar** (`POST /items`). O ML está migrando para o modelo **User Products** (nome do produto em
   `family_name`, título montado pelo ML, cada variação vira um anúncio): a plataforma nasce nesse modelo
   e confere na 1ª publicação real qual corpo o ML aceita (guardar a 1ª resposta como amostra, como as
   outras integrações novas fazem).

Escrita no ML usa o caminho JSON com renovação de token que já existe (`_ml_promo_http`), dentro de
`ml_na_casa(conta)`. Nada é criado no ML sem o clique do Admin.

### 4.5 Puxar anúncios existentes

- Lê todos os anúncios da conta (`ml_todos_os_anuncios`, `ml_itens_detalhe`) e cria/atualiza a linha do
  anúncio, ligando à ficha pelo SKU (`seller_custom_field`/SELLER_SKU, mesma leitura de `_promo_item_sku`,
  e o vínculo MLB↔SKU `ml_sku_mlb_links_v1`). Sem SKU reconhecido = lista "sem ficha" para o Admin ligar.
- Importa para a ficha só o que estiver vazio nela (atributos, peso/medidas, fotos, descrição) — nunca
  sobrescreve o digitado.
- Anúncio puxado começa como **"Alephee"** (não controlado): a plataforma **só lê**. Controle começa no
  "Passar para o Noromak".

### 4.6 Estoque

- Só para anúncios **controlados pela plataforma**, não-Full.
- Quantidade = regra do TikTok generalizada por canal e filial: disponível no Apollo da filial − unidades
  pendentes na fila daquela filial (vendido e ainda não baixado), kit pela peça que dá menos.
  Hoje está presa ao TikTok (`tiktok_estoque_itens`, `tiktok_apollo_ler_proximo`, `_tiktok_pendentes_bauru`)
  — extrair para uma versão por (canal, filial) e fazer o TikTok usar a mesma.
- **Contar a venda uma vez só** (decisão do Claude — confirmar antes de ligar o Lote): o "disponível" do
  Apollo já desconta a "Negociação" (`INTEGRACAO_ESTOQUE_APOLLO.md` §6). Se a peça presa em atendimento
  aberto entra na Negociação, a venda que **já virou atendimento** sai do disponível pelo Apollo e não pode
  ser descontada de novo pela fila. Regra: desconta da fila **só as vendas que ainda não têm atendimento**
  no Apollo (as da Alephee e as da plataforma antes do `atendimento/criar`). Confirmar com o Guilherme ou
  numa venda real (disponível antes e depois do atendimento) — a Simulação mostra a diferença.
- Apollo sem resposta (`disponivel: null`) → **não envia nada**, nunca zero.
- **Produto misto** (decisão do Claude): se um produto tem anúncios da mesma filial ainda na Alephee e outros
  na plataforma, cada ferramenta mostra o estoque inteiro — risco de vender a mais durante a migração. A
  ficha e a lista de anúncios avisam "produto misto: Alephee também mexe neste estoque". O risco já existe
  hoje (Alephee × canais fora dela) e diminui conforme a migração avança.
- Reação imediata: quando a versão da fila muda (venda nova em qualquer canal), recalcula e envia na hora
  só o que mudou para os anúncios da mesma filial.
- Envio ao ML: conta com um depósito = `PUT /items/{id}` `available_quantity`; conta com vários depósitos
  ou User Products = `/user-products/{id}/stock` com `x-version` (409 = reler e repetir). Detectar qual
  por anúncio. O PUT antigo pode responder 200 sem mudar: **reler e conferir** antes de marcar "ok".
- Cada envio vai para um histórico (anúncio, de, para, motivo, resposta, hora).
- Modos como o resto do sistema: **Simulação** (calcula e mostra, não envia) → **Lote** (clique) →
  **Automático** (ligar exige OWNER), por canal e conta.
- Leitura do Apollo: hoje um código por chamada, ~18/min dividido com `/custo`. Enquanto não houver
  leitura em lote, usar o rodízio do TikTok (nunca lido > perto de zerar > mais antigo) e o limite atual.

### 4.7 Vendas → Apollo

Para venda de anúncio controlado pela plataforma (os outros continuam com a Alephee):
1. Venda paga entra na fila como hoje (vigias/aviso do ML). Um worker próprio (decisão do Claude) leva ao
   Apollo as vendas de anúncios da plataforma que ainda não têm atendimento. Se o nome/CPF da NF-e ainda
   não chegou (o ML bloqueia o billing-info; vem pela extensão, `cliente_fiscal`), espera como o cadastro
   automático da V19.41: tenta de 10 em 10 min por até 1 h, depois vai para a lista de falhas.
2. Cliente: `cliente/buscar` pelo CPF/CNPJ → `cliente/criar` se não existe (mesmas regras de
   `exp_cadastro_atualizar`: endereço do envio, e-mail/celular padrão, cidade como o Apollo grafa).
3. Atendimento: `POST /api/v1/expedicao/atendimento/criar` (liberado pelo Guilherme em 05/11; contrato em
   `PEDIDO_PORTA_CRIAR_ATENDIMENTO.md`): `revenda` da filial, `pedido_externo` = marketplace + nº da venda
   (chamar de novo não duplica — `ja_existia`), `cliente`, itens **com o kit aberto em peças** e o preço da
   venda, `valor_esperado`. Conferir `valor`/`itens` relidos na resposta.
4. Falha → lista "**Venda não chegou no Apollo**" com o motivo (`sem_credito`, `item_nao_encontrado`,
   `sem_estoque`, `valor_divergente`, `apollo_fora`...) e botão "Tentar de novo". Erro de rede = **incerto**:
   não repete sozinho até alguém conferir (mesma regra das outras portas).
5. Daí em diante: romaneio, caixa e Faturamento como já existem (`faturamento_v1943`).
6. Venda cancelada com atendimento criado → aviso "cancelada com atendimento aberto (nº)"; cancelamento
   manual no Apollo.

A porta de **movimentação de estoque** que o dono disse estar liberada **não está no contrato que temos**
(`atendimento/criar` não tem movimentação). Antes de usar: pegar o contrato com o Guilherme e decidir para
que serve aqui (provavelmente nada no piloto: o atendimento já compromete as peças).

## 5. Piloto (primeira entrega)

Só **Mercado Livre · Araçatuba**, perfil Admin, Simulação → Lote antes de Automático.
1. Ficha do produto (peça e kit), aba Kits editando o Cadastro de Kits, aba Anúncios.
2. Puxar os anúncios da conta de Araçatuba (só leitura, todos como "Alephee").
3. Criar **um anúncio novo** de **um produto** pela plataforma, do começo ao fim.
4. Estoque desse anúncio controlado pela plataforma (Simulação, depois Lote, depois Automático).
5. Primeira venda desse anúncio chegando no Apollo pelo `atendimento/criar`.

Conferências com o dono: fotos subiram certas, campos obrigatórios aceitos, preço/taxas batendo com o
cobrado, estoque certo depois de venda em outro canal, atendimento com as peças e o valor certos.

Depois: migrar alguns anúncios antigos ("Passar para o Noromak"), ML Bauru, Shopee, Magalu, TikTok
(trocar a extensão pelo motor comum quando houver API), Amazon.

## 6. Dependências externas

| Com quem | O quê | Bloqueia |
|---|---|---|
| Guilherme | `atendimento/criar` — **liberado 05/11**, Noromak ainda não chama | item 5 do piloto |
| Guilherme | Contrato da porta de **movimentação de estoque** | nada no piloto |
| Guilherme | Leitura do **cadastro da peça** (descrição, NCM, EAN, marca) | NCM/EAN automáticos (dá para digitar) |
| Guilherme | **Estoque em lote** (muitos códigos por chamada) | escala além do piloto |
| Dono | Exportar da Alephee peso/medidas | peso/medidas automáticos (dá para digitar) |
| Dono | Como desativar um anúncio na Alephee | migração de anúncio antigo |
| Mercado Livre | 1ª publicação real (corpo User Products, fotos, atributos) | — conferir na amostra |

## 7. Regras do sistema que valem aqui

- ADMIN > Noromak > integrações: o que o Admin digitou na ficha ou no anúncio não é sobrescrito por
  importação, nem pela leitura do ML. Anúncio removido da plataforma pelo Admin não volta por importação
  (tombstone, como `promo_removidos_v1876`).
- Cache e polling nunca são fonte de verdade: antes de gravar estoque/preço, relê o dado ao vivo.
- Valor desconhecido fica em branco/nulo, nunca zero (estoque, custo, taxa).
- Uma escrita no ML por anúncio por vez (trava por anúncio, como `PROMO_RENOVACAO_LOCK`).
- Toda tela nova: `nk-ui.css`/`nk-ui.js`, item no `nk-menu.js` (menu "Vendas e anúncios"), rota no
  servidor/`web-auth.js`/`shared-storage.js`/`sw.js` (V18.82). Ver = módulo admin (Admin, Supervisor,
  Consulta só lê); **gravar = só perfil Admin** (criar anúncio, enviar estoque, "Passar para o Noromak",
  "Tentar de novo", editar ficha/kit) — o dono disse "usuários Admin"; abrir para outros é decisão dele depois. Worker novo entra em
  `SAUDE_WORKERS_INFO`. Teste novo em `TESTES/`.
- Nenhuma chamada pesada no banco de produção (VPS de 1 GB dividida com o CRM).

## 8. Deixado para o construtor

- Modelo das tabelas (ficha, atributo, foto, anúncio por canal, histórico de envio, venda→atendimento) e
  nomes, seguindo o padrão `_v20xx` do código.
- Se a ficha usa o `precificador_online_v1`/`conf_produtos` como base ou tabela própria ligada pelo código
  Apollo (sem duplicar custo/kit: esses continuam vindo de onde vêm).
- Detalhe das seções Variações e Aplicações (compatibilidade de veículos): só aparecem no índice no
  piloto.
- Divisão em versões (`V20.xx`) e ordem interna das partes do piloto.
