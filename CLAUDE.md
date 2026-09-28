# Sistema Web Noromak — guia para o Claude

Sistema interno de e-commerce da Noromak: entrada e controle de pedidos (Mercado Livre,
Shopee, manuais), expedição, separação, conferência por código de barras, etiquetas,
produtos/SKUs/EANs, kits, precificação, promoções, rentabilidade, estoque, Central de
Pendências e histórico.

Versão de referência deste documento: **V17.82 — Conclusão e Reinclusão Canônicas**.

---

## 0. NUNCA PUBLICAR SEM AUTORIZAÇÃO

**Não execute `./publicar.sh`, `git push producao`, nem nada que reinicie o serviço em
produção, por iniciativa própria. Nunca.**

O fluxo obrigatório é sempre:

1. Fazer a alteração.
2. Rodar **`./testar.sh`** e **mostrar o resultado real ao dono do sistema** — inclusive o
   que não foi possível testar.
3. Pedir autorização e **esperar** o "pode subir".
4. Só então publicar.

Commit local é permitido quando a alteração foi pedida. **Publicar não é.**
A autorização vale para **aquela** publicação — não vale para a próxima.

Motivo: este sistema roda a operação real de expedição da empresa. O `publicar.sh`
reinicia o serviço em produção na hora; um erro só aparece quando a operação já parou.

---

## 1. Regra arquitetural absoluta

**ADMIN > Sistema Noromak > Integrações externas.**

Ordem de prioridade ao resolver qualquer conflito de dados:

1. Ação ADMIN explícita
2. Tombstone / override ADMIN
3. Pedido concluído (`completed_orders`)
4. Dados canônicos Noromak
5. Alteração operacional humana
6. Mercado Livre / Shopee / Apollo
7. Imports automáticos
8. Cache / polling

Consequências práticas, não negociáveis:

- Se o ADMIN excluir um pedido, ele **permanece excluído** até nova inclusão explícita do
  próprio ADMIN. Nenhum sync, import, merge, polling, cache ou cofre manual pode reverter.
- Se o ADMIN alterar SKU, quantidade, Nome Conferência ou outro campo, integrações podem
  atualizar *outros* dados, mas **nunca** sobrescrever o campo protegido.
- Pedido concluído não reaparece na Conferência nem em Pedidos Pendentes.
- Cache e polling **nunca** são fonte de verdade.

---

## 2. Stack e execução

- Backend: `servidor_web.py` — ~13.9k linhas, **apenas biblioteca padrão do Python**
  (sem dependências externas). `ThreadingHTTPServer` em `127.0.0.1:8100`.
- O nginx **não** remove o prefixo: todas as rotas vivem sob `BASE_PATH = "/expedicao"`.
- Persistência: SQLite em `/home/expedicao/dados/expedicao.db` (fora da pasta do código).
  Backup diário automático em `/home/expedicao/backups`.
- Front-end: HTML/CSS/JS puro em `APP/`, sem build step. Arquivos monolíticos.
- Segredos reais só em `/home/expedicao/.env` no servidor. Ver `.env.example`.

### Estrutura

```
servidor_web.py              backend inteiro (rotas, integrações, workers, regras)
APP/
  acesso.html                login
  admin.html         ~830KB  abas: pedidos, pedidos-dia, produtos, kits, etiquetas,
                             usuarios, historico, historico-sku, integracoes, seguranca
  conferencia.html   ~199KB  fila de conferência e bipagem
  precificador.html  ~632KB  custo, margem, preço ideal, curva ABC
  estacao-impressao.html     estação Zebra / QZ Tray
  financeiro.html            faturamento, gastos e lucro (V18.30; substituiu a
                             Rentabilidade ML na V18.33)
  analise-full.html, compras.html (Demanda de Compra)
                             (Central de Pendências e Promoções ML removidas na V17.99;
                             /pendencias e /promocoes-ml abrem o Admin)
  shared-storage.js          cache versionado + polling (CRÍTICO)
  web-auth.js, user-permissions.js, sw.js, pwa-install.js
DADOS/                       seed ml_sku_mlb_links_seed.json (fora do Git)
FERRAMENTAS_WINDOWS/         agente de impressão Zebra, 2FA do owner
publicar.sh                  publicação em produção
testar.sh                    validação obrigatória antes de publicar
verificar_servidor.sh        diagnóstico remoto do serviço
TESTES/                      suíte de invariantes críticas
IDEIA_INTEGRACAO_CLAUDE.md   ideia registrada, ainda não decidida
```

> **Ideia em aberto:** integrar o sistema com o Claude para apoiar a operação
> (resumo diário, explicar pedido travado, diagnosticar erro). Nada decidido —
> ver [IDEIA_INTEGRACAO_CLAUDE.md](IDEIA_INTEGRACAO_CLAUDE.md), que já registra as
> duas restrições: a regra do ADMIN vale inteira, e o servidor não tem dependência
> externa.

---

## 3. Publicação

> Só depois de testar e receber autorização explícita. Ver a seção 0.

```bash
git add -A
git commit -m "V17.xx - descricao da alteracao"
./publicar.sh
```

`publicar.sh` exige árvore limpa, faz `git push producao HEAD:master`, reinicia o serviço
via ssh e valida `/api/health` interno **e** o endereço público.

Em divergência com o remoto:

```bash
git fetch producao master
git rebase producao/master
```

**Nunca** usar `git push --force`. Conflitos são resolvidos antes de publicar.

Ao subir versão, atualizar `APP_VERSION` em `servidor_web.py:41` **e** `VERSAO.txt`.
Mensagem de commit no padrão `V17.xx - Descricao sem acentos`.

---

## 4. Como os dados fluem (leia antes de tocar em qualquer persistência)

Tudo passa por `storage` (chave/valor JSON versionado). As chaves canônicas estão em
`KNOWN_KEYS` (`servidor_web.py:179`).

### Leitura — `get_storage()` (`servidor_web.py:2546`)

O servidor reaplica as decisões do ADMIN **na leitura**, mesmo que algum job legado tenha
escrito direto no banco:

`filter_completed_orders` → `_enrich_rows_with_completion_history` →
`_admin_override_apply` → `_admin_manual_order_vault_apply` →
`_pending_filter_manual_deleted_orders_value` → **`filter_completed_orders_v1772`**

A última etapa foi adicionada na V17.82 e **precisa continuar sendo a última**. O override
`__ENTITY__` e o cofre manual rodam *depois* do `filter_completed_orders` do começo e
reinjetavam pedidos já concluídos, que voltavam para a Conferência. A escrita já fazia
nessa ordem; a leitura não fazia. Conclusão é a barreira final nas duas pontas.

### Escrita — `save_storage()` (`servidor_web.py:3727`)

Tudo dentro de `BEGIN IMMEDIATE`, e as barreiras do ADMIN ficam **por último**, depois do
merge, para que nenhuma integração passe por cima:

1. `reactivate_completed_orders_from_delta` (só com `allow_completed_reimport`, ADMIN)
2. `_admin_manual_order_vault_sync` — cofre de pedidos manuais
3. `_admin_override_record_delta` — registra o delta manual antes de qualquer merge
4. `filter_completed_orders_v1772` — concluído nunca volta
5. `preserve_conference_progress_v1770` — bip é monotônico
6. `_preserve_admin_manual_pending` + `preserve_pending_records_v1714`
7. `preserve_conference_names`
8. `merge_value` / `merge_arrays` / `merge_label_banks`
9. `_dedupe_ml_legacy_sku_rows`, `_propagate_previous_completion_metadata`
10. **`_admin_override_apply`** → `_admin_manual_order_vault_apply` →
    `_pending_filter_manual_deleted_orders_value` → `filter_completed_orders_v1772`

### Tabelas e chaves que guardam decisão do ADMIN

| Onde | O quê |
|---|---|
| `admin_manual_overrides` | override por `(storage_key, entity_id, field_name)` |
| `admin_manual_orders_v1752` | cofre persistente de pedidos manuais |
| `completed_orders` | barreira de reentrada de pedido concluído |
| `order_completion_history` | histórico permanente, sobrevive à reabertura |
| `conf_pedidos_exclusoes_v1766` | tombstone de pedido inteiro |
| `conf_shopee_exclusoes_v1` | tombstone Shopee por pedido/linha |
| `ml_sku_mlb_manual_tombstones` | vínculo MLB↔SKU removido à mão |

Só `_pending_admin_restore_orders()` e `allow_completed_reimport` (reinclusão ADMIN
explícita) desfazem um tombstone.

### Sincronização no navegador — `APP/shared-storage.js`

Cache local versionado. Polling: **10 s** com a aba ativa, **30 s** em segundo plano,
consulta imediata ao voltar para a aba. As chaves monitoradas são **por tela**
(Conferência observa só 5). Bip, conclusão, exclusão e alterações locais importantes
persistem imediatamente, sem esperar o ciclo do polling.

Não criar timers duplicados nem ampliar a lista de chaves monitoradas sem necessidade —
a V17.34 tentou otimização global e causou regressões graves em produção.

---

## 5. Rotas e permissões

Prefixo `/expedicao`. Páginas: `/acesso`, `/conferencia`, `/admin`, `/precificador`,
`/pendencias`, `/estacao-impressao`.

APIs principais: `/api/storage`, `/api/order-complete`, `/api/order-lock`,
`/api/order-kit-delete`, `/api/order-line-delete`, `/api/order-conference-name`,
`/api/order-sku`, `/api/order-ean`, `/api/admin-order-upsert`, `/api/roles`,
`/api/integracoes/{mercadolivre,shopee}/*`, `/api/precificador/*`, `/api/print/*`,
`/api/qz/*`, `/api/security/owner/*`, `/api/full/apollo-stock`,
`/api/financeiro/{resumo,ads,sincronizar}`.

Perfis: **Admin**, **Supervisor**, **Operador**. Supervisor é intermediário e **não**
recebe automaticamente as áreas sensíveis. Módulos por usuário: `conferencia`, `admin`,
`estacao`, `precificador` (`module_allowed()`, `servidor_web.py:1293`).

`OPERATOR_WRITE_KEYS` (`servidor_web.py:202`) lista o que um usuário autenticado sem perfil
Admin pode gravar — inclui `conf_etiquetas`, `conf_fotos` e `conf_pedidos`. Isso é
funcional, não administrativo: **não transforma o usuário em Admin**.

Alterar `conf_usuarios` exige reautenticação do OWNER (senha + TOTP).

---

## 6. Integrações

### Mercado Livre
OAuth com `offline_access`. Cinco workers em background rodam 24/7 no servidor:
novos pedidos (~20 s), etiquetas pendentes (~15 s), reconcile de pedidos manuais (~60 s),
keepalive do token (~300 s), catch-up de segurança (~900 s).
Etiquetas: `_ml_fetch_shipping_zpl` / `_ml_store_linked_label`.
Rentabilidade via billing API com cache em `ml_profitability_billing_cache`.
Tratar expiração do token: já houve cenário de `access_token` sem `refresh_token`.

### Shopee
Fluxo de etiqueta: `get_shipping_parameter` → `ship_order` → `get_tracking_number` →
`create_shipping_document` → `get_shipping_document_result` → download/vínculo.
Para UNSPLIT existe fallback quando a API devolve `ship_order_not_need_package_number`:
repete `ship_order` sem `package_number`.
**Etiqueta = uma imagem (V17.99):** o ZPL da Shopee traz `~DGR` (a imagem) **antes** do `^XA`,
que só imprime a imagem (`^XGR`). Cortar o texto no `^XA` gera etiqueta em branco — foi o
bug das etiquetas automáticas da V17.89 a V17.98. `shopee_reparar_etiquetas_sem_grafico` roda no boot.
Quantidade da API é a autoridade automática **exceto** onde houver alteração explícita do
ADMIN (V17.78). Linhas são identificadas por IDs da API, não por posição. KITs não devem
gerar SKU artificial concatenado quando os componentes reais já existem.

### Apollo (ERP)
Consultas de custo e estoque via `CRM_COST_API_URL` / `CRM_STOCK_API_URL`, cache de 180 s.
Alimenta Precificador e Central de Pendências.
**Estoque disponível (V17.94):** rota `/api/v1/expedicao/estoque` (contrato em
`INTEGRACAO_ESTOQUE_APOLLO.md`), já líquida do FULL. `disponivel: null` é casa que não
respondeu — **nunca** tratar como zero. `/custo` e `/estoque` dividem o limite de 20
chamadas/min: `crm_rate_acquire`; em `/api/full/apollo-stock` com `nao_esperar` o que não
cabe volta `pending` + `retry_after` e a Demanda Full reenvia sozinha.
A Demanda Full (V17.95) considera **somente Araçatuba** (revenda 1, de onde sai o FULL) e
divide o estoque entre anúncios do mesmo código Apollo, mais urgente primeiro.
V17.96: kits consomem cada componente (`POST /api/full/kits`, mesma regra dos pedidos ML),
sugestão de transferência Ribeirão/Bauru e Lista de separação por prateleira (localização do Apollo).
**Pedido de rota de demanda (V17.99):** `PEDIDO_ROTA_DEMANDA_APOLLO.md` — vendas por período,
compra em aberto, custo e marca em lote. A chave do Noromak **só** tem `/custo` e `/estoque`;
não usar outras rotas do Apollo sem acordo com a equipe dele.

### Financeiro — faturamento, gastos e lucro (V18.30 a V18.38)
`APP/financeiro.html` (`/financeiro`, menu Comercial, só **admin**; leitura no padrão Power BI —
canvas com tiles, slicer de filtros, cards de KPI, waterfall, combo por dia, rosca e abas de página). Junta o que já existia:
faturamento de `vendas_v1799` (ML + Shopee), custo do produto pela mesma regra da Demanda de
Compra (`_compras_produtos_precificador` + `_compras_resolver_custo`, kit vira componentes),
tarifa e frete do ML **no ato da venda** (V18.31) e pela **fatura oficial** (`ml_billing_period_detail`, regra única em
`_ml_billing_consolidar_lancamentos`, que na V18.30 passou a devolver também `shipping` e
`publicidade`), tarifa e frete da Shopee pelo **escrow** (`shopee_escrow_v1810`) e o gasto de
Ads pela API de Publicidade. Imposto **fixo de 12%** sobre a venda (`FINANCEIRO_IMPOSTO_PERCENT`).
`lucro bruto = faturamento − CMV`; `líquido = bruto − taxa − frete − outros − ads − imposto`.
A tarifa é **do pedido** e rateada entre as linhas pelo valor — nunca somada por linha.
**V18.31 — taxa no ato da venda:** o ML já mostra "Tarifa de venda total" e "Tarifa do Mercado
Envios (por sua conta)" no detalhe do pedido. A tarifa vem em `order_items[].sale_fee` (por
unidade × qtd, `_ml_item_fee`) e é gravada em `ml_venda_custo_v1830` **dentro da sincronização
de vendas, sem chamada extra**; o frete vem de `/shipments/{id}/costs` (`senders[].cost`,
`_ml_shipping_cost_parse`) num worker próprio (`financeiro_frete_loop`, 5 min) e fica em
`ml_frete_envio_v1830` — um envio que cobre vários pedidos tem o custo **dividido** entre eles.
Ordem de autoridade: **fatura quando existe** (é o que foi cobrado), senão a tarifa da venda;
as duas **nunca somam**, e a diferença entre elas aparece na tela (`cobertura.divergencias`).
O que não é conhecido fica **nulo e visível** (`cobertura.faturamento_sem_custo` /
`sem_taxa`): nada é estimado. `GET /api/financeiro/resumo`, `POST /api/financeiro/sincronizar`
(fatura + escrow pendente + Ads, em segundo plano).
**V18.36 — o Billing devolve os MESMOS lançamentos em `BILL` e em `CREDIT_NOTE`.** Os dois
sincronismos de setembro voltaram com 5.773 itens idênticos. Marcar crédito pelo `document_type`
**pedido** fazia cada cobrança ganhar uma cópia negativa que a anulava: R$ 132 mil de faturamento
ML apareciam com **R$ 59,42** de tarifa. Agora crédito vem do **dado**
(`_ml_billing_credito_do_lancamento`: `detail_type=BONUS` ou sub_type `B*`) e a leitura deduplica
por `detail_id` (`_ml_billing_dedupe`). `ensure_ml_billing_credito_schema()` conserta as linhas
antigas. Com isso a tarifa fecha em **11,99% do faturamento** — a "Tarifa de 12%" que o ML mostra
na venda — em 994 de 994 pedidos. Confere com o Mercado Turbo, que o dono usa:
`faturamento − custo − imposto − tarifa − frete = margem de contribuição`.

**V18.35 — na Shopee o `escrow_amount` é a autoridade** (o que de fato cai na conta, como a fatura
é no ML). `order_ams_commission_fee` (comissão de afiliados/AMS) estava fora da conta: 3,5% do preço
em setembro. Agora entra como **Ads da Shopee** (coluna `ams_commission`, migrada por
`ensure_shopee_escrow_schema()` a partir do `order_income_json` já guardado — sem refazer chamada).
O que a Shopee reteve e as colunas não explicam vira **outros**, nunca margem: o resíduo caiu de
3,5% para 0,4% do preço.

**V18.34 — sub_types REAIS da fatura MLB** (`ML_FATURA_TARIFA/FRETE/ADS/NEUTRO`, levantados na
conta 224625106): `CVVML` (Custo por vender), `CVVPRC` (Custo por cobrar no MP) e `CVVFNU` (Taxa de
recebimento) são **tarifa de venda**; `CXDE`/`CFFE` são **frete**; **`PADS` é o gasto com Ads**,
lançado **por pedido** — quando a API de Publicidade não responde, é ela que alimenta o Ads (uma
fonte OU a outra, nunca somadas); `CFONPN` (taxa de parcelamento) é **neutra**, porque o acréscimo
que a cobre é pago pelo comprador e não entra no nosso faturamento. Antes disso o código procurava
`CV`/`BV` e a tarifa inteira caía em "outros", inflando a margem. O que não é conhecido virou
pendência explícita por linha (`sem_taxa`, `sem_frete`): **R$ 0,00 só quando é zero de verdade**.
O sync da fatura ganhou backoff no HTTP 429 e retoma do `last_id`.

**V18.33:** substituiu a **Rentabilidade ML** — `ml_profitability_payload()`, a rota
`/api/rentabilidade-ml` e `APP/rentabilidade-ml.html` foram removidas, e o endereço antigo
abre o Financeiro. `ml_profitability_snapshot` **não é mais alimentada**, mas continua sendo
lida (custo de reserva da Demanda de Compra e histórico da gaveta do Dashboard, via
`_ml_custo_venda_por_pedido`, que prefere o custo capturado no ato da venda).

### Publicidade / Mercado Ads (V18.30)
`GET /advertising/advertisers?product_id=PADS` (`Api-Version: 1`) → `advertiser_id`;
campanhas e métricas em `/advertising/{MLB}/advertisers/{id}/product_ads/campaigns/search`
(`api-version: 2`, `metrics=cost,...`), gasto por anúncio em
`/product_ads/campaigns/{id}/ad_groups/metrics` + `/product_ads/ad_groups/{id}`.
Exige a permissão funcional **Publicidade** no DevCenter (e reconexão do ML) **e** Publicidade
habilitada na conta — sem isso a API responde 404 "No permissions found for user_id" e a tela
mostra "Ads não conectado" com o gasto **em branco**, nunca estimado.
As métricas só voltam **90 dias** e consolidam às **10:00 GMT-3**: o worker `ml_ads_sync_loop`
(1 h) regrava a janela recente em `ml_ads_dia_v1830` / `ml_ads_campanha_dia_v1830` /
`ml_ads_item_dia_v1830` e completa o histórico de trás para frente. Os endpoints antigos de
`product_ads/items` e `ads/metrics` foram desativados pelo ML em maio/2026 — só `ad_groups`.

### Dashboard de vendas (V17.99)
Worker `vendas_sync_loop` copia as vendas do **ML** (`/orders/search`: valor, qtd, data real,
inclui Full) e da **Shopee** (`get_order_list` + `get_order_detail`: preço com desconto × qtd,
status pagos, janela máx. 15 dias) para `vendas_v1799` (coluna `marketplace`): últimos 3 dias a
cada 10 min, 30 dias 1x/dia, e uma janela de histórico por minuto até 365 dias. A janela é
**regravada** (venda cancelada some). `GET /api/dashboard/vendas?marketplace=ml|shopee`.

### Demanda de Compra (V18.00, provisória)
`APP/compras.html` (`/compras`, menu lateral). Vendas = ML + Shopee de `vendas_v1799` (kit vira
componentes; SKU `prefixo-código` e `A/B` viram código Apollo); custo e curva = lista do
Precificador (`precificador_online_v1`, cujo SKU é o código Apollo); estoque = `/api/full/apollo-stock`
no navegador. Marca não existe no cadastro: fornecedor = regras (começo do código, palavra na
descrição, lista de códigos) em `compras_fornecedores_v1800`. "A caminho" é digitado e fica no
navegador. Trocar as vendas pela rota do Apollo quando ela existir (PEDIDO_ROTA_DEMANDA_APOLLO.md).
**Custo de reserva (V18.07):** o custo médio do Apollo vai a 0 quando o estoque zera (245 peças em
21/09). A demanda usa, nesta ordem: custo atual do Apollo > custo **digitado** na tela > o mais
recente entre o **último custo > 0 visto** (`custos_conhecidos_v1807`, alimentada pelo Precificador
e por todo `crm_cost_lookup`) e o **custo da venda** do ML (`ml_profitability_snapshot`). Nunca grava
custo zero. `POST /api/compras/custo`. Definitivo: custo da última venda pela Linx Smart API.

### Linx Smart API (levantamento 21/09/2026)
API oficial da Linx para o Linx DMS, na nuvem: gateway `https://auto-gwsmartapi.linx.com.br/<api>`,
chave em `Ocp-Apim-Subscription-Key` + token de `POST /api-seguranca/token`. Assinadas: Segurança,
Gerencial Peças, Faturamento, Peças Balcão Premium, Veículos CRM. Tudo em
`INTEGRACAO_LINX_SMART_API.md` — inclusive quais operações **gravam/movimentam estoque** (nunca testar).
`OrdemCompra/Historico` (demanda mensal balcão/oficina/telemarketing) e `DetalhesPedidos`
(compra em aberto) são o caminho para a Demanda de Compra definitiva.

**Botão Cliente (V17.85):** atualiza o cadastro no **Linx DMS** (ERP em `10.1.15.1`, rede
do escritório — a VPS não alcança). O servidor só entrega CPF/CNPJ e nome de "Dados para
a NF-e" em `GET /api/cliente-fiscal?pedido=` (somente leitura; relê o billing-info no ML
para pedidos anteriores à V17.84). Quem grava no Linx é a extensão
`NOROMAK_LINX_CLIENTE` (terceira extensão, fora do repo), que recebe
`window.postMessage({tipo:"noromak-linx-atualizar-cliente",...})` e abre o painel com
confirmação. Regras e formato em `INTEGRACAO_LINX_CLIENTE.md`.
**Nome/CPF da NF-e na Central (V17.86):** a API do ML **bloqueia** o billing-info para o
app do Noromak (HTTP 403 PolicyAgent) — `NomeFiscalMercadoLivre`/`CPFMercadoLivre` ficam
vazios e a sincronização reescreve esses campos a cada poucos minutos. A extensão lê
"Dados para a NF-e" na página da venda (janela minimizada, sessão do usuário) e o Noromak
guarda na tabela **`cliente_fiscal`** (fora de `conf_pedidos`, de propósito): `POST
/api/cliente-fiscal`, `GET /api/cliente-fiscal/lista`. Depois de gravar no Linx, guarda
também o código do cliente (coluna Cód.Cliente).
O botão **Romaneio** continua só visual — não deve executar operação falsa.

### Vínculo MLB ↔ SKU
Banco canônico `ml_sku_mlb_links_v1`, com seed em `DADOS/ml_sku_mlb_links_seed.json`.
Um snapshot parcial do navegador **não** é autoridade: inclusão/remoção explícita usa
`/api/precificador/ml-vinculos/manual`. Em erro 403 PolicyAgent na consulta em lote, há
fallback para `/items/MLB...` individual.

---

## 7. Extensões Chrome (projetos separados)

Ficam **fora** deste repositório, no Desktop do usuário:

- `EXTENSAO CHROME CONFERÊNCIA` — MV3 v1.6.4, "Importador de Marketplaces"
  (ML / Shopee / Magalu). Na Shopee detecta ID do pedido e destinatário abaixo de
  "Endereço de entrega", remove só o sufixo mascarado (`,******41`), localiza o pedido pelo
  **ID exato — nunca pelo SKU** — e envia o Nome Conferência ao Noromak. Protegida contra
  loops de MutationObserver/SPA. Botão 👤 é reprocessamento manual.
- `NOROMAK_PRECIFICADOR_ML_EXTENS` — MV3 v1.105.0, Precificador na listagem de anúncios do
  ML: preço atual, preço ideal, margem, custo, margem disponível para promoção, badges,
  filtros, distinção Clássico/Premium e comparador por SKU.

**Qualquer mudança de endpoint ou de contrato de payload precisa considerar
compatibilidade com as duas extensões.**

---

## 8. Como trabalhar neste código

- O código existente é a fonte de verdade. Antes de alterar uma função, rastrear o fluxo
  completo: leitura → escrita → merge → cache → override → tombstone.
- **Não** remover funcionalidade para simplificar.
- **Não** reescrever módulos inteiros sem necessidade.
- Mudanças incrementais e testadas. Otimizações de performance, especialmente.
- Nos HTMLs monolíticos (`admin.html`, `precificador.html`), fazer edições cirúrgicas —
  nunca regenerar o arquivo.
- Comentar mudanças críticas com a versão, como o resto do código já faz
  (`# V17.78: ...`), para que a próxima pessoa saiba por que aquela linha existe.

### Validação antes de entregar

```bash
./testar.sh
```

Compila o backend e roda a suíte de invariantes críticas em um banco SQLite temporário.
Não toca no banco de produção e não faz chamada para ML, Shopee ou Apollo.

O `publicar.sh` chama `./testar.sh` antes do `git push` e **cancela a publicação** se
qualquer teste falhar — nada é enviado e a operação continua no ar.

Os testes ficam em `TESTES/`:

| Arquivo | Cobre |
|---|---|
| `test_admin_soberano.py` | exclusão soberana, tombstone, KIT inteiro, reinclusão ADMIN, campos protegidos, pedido manual |
| `test_conferencia.py` | bip monotônico, metadados de conferência, conclusão canônica, cofre manual |
| `test_financeiro.py` | imposto de 12%, lucro bruto/líquido, tarifa por pedido (não por linha), venda sem custo não vira lucro, Ads sem duplicidade, escrow Shopee |
| `test_instalacao.py` | instalação nova, idempotência do `init_db`, versionamento do storage, autenticação |
| `base.py` | infraestrutura: banco temporário, servidor real em porta livre, cliente HTTP |

`base.py` define as variáveis de ambiente **antes** de importar `servidor_web`, porque o
módulo abre a conexão SQLite já no import (`DB = connect_db()`).

**Ao corrigir qualquer bug, escreva primeiro o teste que o reproduz.** Cada teste da suíte
representa um problema que já chegou em produção.

Para o front-end, se o Node estiver disponível: `node --check APP/shared-storage.js`.

### Checklist obrigatório

- **Pedidos**: inclusão manual, exclusão ADMIN, pedido simples, KIT, quantidade.
- **Conferência**: bip, múltiplas unidades, conclusão, ausência de reaparecimento.
- **Shopee**: pedido, KIT, quantidade, etiqueta, Nome Conferência.
- **Mercado Livre**: pedidos, etiquetas, SKU, MLB, promoções.
- **Persistência**: F5, outro computador, polling, reinício do servidor, sync posterior.
- **ADMIN**: alteração não pode ser sobrescrita; exclusão não pode reaparecer.

### Armadilhas conhecidas

- `conf_etiquetas` é banco **agregado**. Nunca congelar o snapshot inteiro (`__ROOT__`) com
  override ADMIN — overrides devem ser por entidade/registro.
- Excluir fisicamente uma linha **não basta**: sem tombstone, a integração recria.
- Quantidade já conferida nunca diminui por atualização antiga.
- `completed_orders` não é informação visual, é barreira operacional de reentrada.
- Dashboard "Produtividade hoje" conta cada pedido **uma única vez** por marketplace;
  linhas e componentes de KIT não podem multiplicar a contagem.
- A Central de Pedidos do Dia mostra também os já conferidos — ela **não** se comporta
  como Pedidos Pendentes.
