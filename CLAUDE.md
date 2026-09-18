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
  central-pendencias.html    divergências ML x Apollo
  estacao-impressao.html     estação Zebra / QZ Tray
  promocoes-ml.html, rentabilidade-ml.html, analise-full.html
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
`/api/qz/*`, `/api/security/owner/*`, `/api/full/apollo-stock`.

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
Quantidade da API é a autoridade automática **exceto** onde houver alteração explícita do
ADMIN (V17.78). Linhas são identificadas por IDs da API, não por posição. KITs não devem
gerar SKU artificial concatenado quando os componentes reais já existem.

### Apollo (ERP)
Consultas de custo e estoque via `CRM_COST_API_URL` / `CRM_STOCK_API_URL`, cache de 180 s.
Alimenta Precificador e Central de Pendências.

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
