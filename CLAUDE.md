# Sistema Web Noromak — guia para o Claude

Sistema interno de e-commerce da Noromak: entrada e controle de pedidos (Mercado Livre,
Shopee, manuais), expedição, separação, conferência por código de barras, etiquetas,
produtos/SKUs/EANs, kits, precificação, promoções, rentabilidade, estoque, Central de
Pendências e histórico.

Versão de referência deste documento: **V17.82 — Conclusão e Reinclusão Canônicas** (seções novas até a V19.04).

---

## 0. NUNCA PUBLICAR SEM AUTORIZAÇÃO

**Não execute `./publicar.sh`, `./voltar_versao.sh`, `git push producao`, nem nada que
reinicie o serviço em produção, por iniciativa própria. Nunca.**

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

**Quando publicar (V18.78).** Cada deploy custa mais do que o restart: o backup pré-deploy
copia e comprime o banco inteiro (1,6 GB em 08/10) numa VPS de 1 GB dividida com o CRM — às
11:25 de 08/10 isso parou a bipagem e o CRM por minutos. Entre 02/10 e 08/10 foram 12
deploys, 5 só em 06/10, quase todos no expediente. Por isso:

- Publicar **antes das 08:00 ou depois das 18:00** (Brasília). Em dia útil nesse horário o
  `publicar.sh` pede confirmação (ou `PUBLICAR_NO_EXPEDIENTE=1`) — só com motivo operacional.
- **Agrupar** as versões prontas em uma publicação; não subir várias no mesmo dia.
- Ao pedir o "pode subir", dizer o horário e lembrar o custo do backup.

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

- Backend: `servidor_web.py` — ~19.5k linhas, **apenas biblioteca padrão do Python**
  (sem dependências externas). `ThreadingHTTPServer` em `127.0.0.1:8100`.
- O nginx **não** remove o prefixo: todas as rotas vivem sob `BASE_PATH = "/expedicao"`.
- Persistência: SQLite em `/home/expedicao/dados/expedicao.db` (fora da pasta do código).
  Backup diário automático em `/home/expedicao/backups` (V18.56: thread própria com
  `BACKUP_LOCK`, cópia por conexão de leitura separada — **sem** o `DB_LOCK` —, `.gz.tmp`
  renomeado no fim; a requisição só dispara `backup_daily_em_segundo_plano()`). **V18.78:**
  com um `expedicao_predeploy_*.db.gz` de menos de 2 h (o `publicar.sh` acabou de copiar
  tudo), o diário espera e fica **agendado** para 2 h depois (`_backup_agendar_depois_do_predeploy`)
  — antes o boot fazia um segundo backup completo logo após o deploy.
  `ml_event_log`/`shopee_event_log` guardam **30 dias** (era 90; `DELETE` não encolhe o
  arquivo — `VACUUM` manual, fora do expediente, devolve o espaço).
- Todos os workers sobem embrulhados em `_worker_protegido` (V18.56): erro fora do try
  é registrado e o worker reinicia em 60 s. `manutencao_diaria_loop` apaga
  `ml_event_log`/`shopee_event_log` com mais de 90 dias (`audit_log` **não** é limpo).
- Respostas HTTP são montadas sob o `DB_LOCK` e **enviadas fora dele** (V18.56).
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
  analise-full.html, compras.html (Demanda de Compra), analise-ads.html (Análise de ADS, V18.54),
  promocoes.html, kits-analise.html (Oportunidades de Kit, V18.47)
                             (Central de Pendências e Promoções ML removidas na V17.99;
                             /pendencias e /promocoes-ml abrem o Admin)
  shared-storage.js          cache versionado + polling (CRÍTICO)
  nk-menu.js                 menu lateral único + endereço de cada tela (V18.82)
  nk-ui.js / nk-ui.css       tema claro/escuro, avisos, confirmação, tabela padrão (V18.83)
  saude.html                 Painel de saúde + alertas por e-mail (V18.83)
  posvenda.html              Pós-venda ML: reclamações, perguntas, perdas, reputação (V18.88)
  nk-avisos.js               avisos de venda: popup com som + Web Push (V18.93)
  web-auth.js, user-permissions.js, sw.js, pwa-install.js
DADOS/                       seed ml_sku_mlb_links_seed.json (fora do Git)
FERRAMENTAS_WINDOWS/         agente de impressão Zebra, 2FA do owner, gerar_modo_escuro.py (V18.83)
publicar.sh                  publicação em produção
testar.sh                    validação obrigatória antes de publicar
verificar_servidor.sh        diagnóstico remoto do serviço
TESTES/                      suíte de invariantes críticas
IDEIA_INTEGRACAO_CLAUDE.md   ideia registrada, ainda não decidida
INTEGRACAO_AGENDADOR_FULL.md contrato postMessage com a extensão Agendador Full (V19.03)
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

`publicar.sh` exige árvore limpa, roda o `testar.sh`, faz backup do banco, faz
`git push producao HEAD:master`, reinicia o serviço via ssh e valida `/api/health` interno
**e** o endereço público.

**V18.63 — publicação segura:**
- Antes de enviar, lê o commit e a versão no ar. Recusa se o `APP_VERSION` for igual ao
  que já está no ar (é pela versão que confere se o código novo subiu).
- Lista quem está conferindo agora (`order_locks` com heartbeat ≤ 90 s) e só segue com
  confirmação; sem terminal, cancela, a menos que `PUBLICAR_COM_OPERACAO=1`.
- Depois do restart, exige o `/api/health` com **a versão nova** e `workers: true`. Se
  não vier em 90 s, **volta sozinho** para o commit anterior (`git reset --hard` no
  servidor + restart). O banco não volta — para isso existe o backup pré-deploy.
- Cada publicação fica registrada em `/home/expedicao/deploys.log`
  (data, versão, commit, resultado, quem).
- `./voltar_versao.sh [commit]` volta a produção para o deploy OK anterior quando a versão
  subiu mas tem bug. É ação em produção: **mesma regra da seção 0**, só com autorização.
- `EXP_WORKERS=0` sobe só o HTTP, sem nenhum worker (ML, Shopee, vendas, Ads, Linx,
  relatório, manutenção, backup do boot). Usado pelo `TESTES/test_boot.py`, que sobe o
  `servidor_web.py` de verdade pelo `main()`. **Nunca em produção**: uma cópia do banco
  tem os tokens reais e o `refresh_token` do ML é de uso único.

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
3. `_admin_override_record_delta` — registra o delta manual antes de qualquer merge.
   **V19.00:** o delta é contra o que a **tela** tinha (`oldValue`), não contra o banco
   (`base_admin`; sem `oldValue` ou com `force`, o banco). Contra o banco, um pedido que
   chegou depois de a tela carregar virava tombstone ADMIN e um campo atualizado pela
   integração virava "alteração do ADMIN" com o valor velho. O que a tela não tinha fica
   com o merge de três vias.
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

**Filial por página (V19.00):** o seletor de filial (`nk_filial_v1895`) é lido **uma vez**,
quando a página abre (`FILIAL_DA_PAGINA`), e é ele que vai no `X-Noromak-Filial`. Lido a cada
requisição, a troca numa aba fazia as outras gravarem a fila antiga com a filial nova (aba
de Bauru gravando como "Todas" apagava Araçatuba). No servidor, `storage_remontar_por_filial`
respeita a filial que a linha **já tem no banco** — a cópia de uma tela não remarca linha
existente. Mudou `shared-storage.js` ou `nk-menu.js`: suba o `?v=` em todas as telas e o
`CACHE` do `sw.js`.

---

## 5. Rotas e permissões

Prefixo `/expedicao`. Páginas: `/acesso`, `/conferencia`, `/admin`, `/precificador`,
`/pendencias`, `/estacao-impressao`.

APIs principais: `/api/storage`, `/api/order-complete`, `/api/order-lock`,
`/api/order-kit-delete`, `/api/order-line-delete`, `/api/order-conference-name`,
`/api/order-sku`, `/api/order-ean`, `/api/admin-order-upsert`, `/api/roles`,
`/api/integracoes/{mercadolivre,shopee}/*`, `/api/precificador/*`, `/api/print/*`,
`/api/qz/*`, `/api/security/owner/*`, `/api/full/apollo-stock`,
`/api/financeiro/{resumo,ads,sincronizar}`, `/api/ads-analise`, `/api/ads-analise/importar`.

Perfis: **Admin**, **Supervisor**, **Operador**. Supervisor é intermediário e **não**
recebe automaticamente as áreas sensíveis. Módulos por usuário: `conferencia`, `admin`,
`estacao`, `precificador` (`module_allowed()`, `servidor_web.py:1293`).

`OPERATOR_WRITE_KEYS` (`servidor_web.py:202`) lista o que um usuário autenticado sem perfil
Admin pode gravar — inclui `conf_etiquetas`, `conf_fotos` e `conf_pedidos`. Isso é
funcional, não administrativo: **não transforma o usuário em Admin**.

**Leitura (V19.00):** o `GET /api/storage` passa por `storage_leitura_permitida`. Os tokens do
agente Zebra e do robô Linx (`STORAGE_SEGREDOS`) não saem nem entram por ali para ninguém (cada
um tem a rota própria; trocar exige OWNER). Quem tem o módulo admin (Admin e Supervisor) lê o
resto; os demais leem só `STORAGE_LEITURA_CONFERENCIA` (com conferencia ou estacao) e
`STORAGE_LEITURA_PRECIFICADOR` (com precificador, inclusive a extensão). `?list=1` é só do perfil
Admin; `?versions=` (polling) não muda. **Tela nova de operador que lê outra chave: incluir na
lista**, senão ela recebe 403.

Alterar `conf_usuarios` exige reautenticação do OWNER (senha + TOTP).

**Migração de usuários (V19.00):** `migrate_app_users` só traz usuários legados
(`MIGRACAO_INICIAL`, `conf_usuarios` antigo, `user_roles`) na instalação **nova** (`app_users`
vazia), e senha legada com menos de 8 caracteres é trocada pela inicial. Antes rodava em todo
boot: o usuário excluído voltava a cada publicação (felipe/matheus com "123"). Excluir usuário
apaga também de `user_roles`.

**Supervisor = operador com `can_admin`** (`is_supervisor_user`). Por isso, no `POST /api/roles`, o
Supervisor tem o Admin sempre ligado e os **outros módulos valem o que foi marcado** (V18.68: antes
recebia tudo liberado e a caixa desmarcada "voltava"). Na tela (`user-permissions.js`) a caixa Admin
segue o perfil: travada ligada no Supervisor, travada desligada no Operador. A migração de módulos do
boot (`ensure_app_user_permissions_schema`) só roda quando as colunas acabam de ser criadas — antes
religava todos os módulos de todo Admin a cada reinício.

**Tela de Usuários (V18.69):** padrão da Visão geral (rótulo, título, cartões de resumo) e uma linha
por usuário (`.usr-linha`, renderizador da V17.57 no fim do `admin.html`); módulos são a mesma
célula do `user-permissions.js` (`nkCelulaModulosUsuario(u,'div')`) em formato de botão. Linha
alterada e não salva fica marcada e o "Salvar" mostra quantas (`__usrBaseV1869`, refeito a cada
carga do servidor). Solicitações só aparecem quando há pendente.

**Menu do Admin (V18.68):** Visão geral (Dashboard, Financeiro) fixa; Expedição, Vendas e anúncios,
Estoque e compras, Cadastros e Sistema são grupos recolhíveis (`.v15-grp`, estado no `localStorage`
`nk_menu_grupos_v1868`, padrão só Expedição aberta). O grupo da tela atual abre sozinho; fechado,
ganha ponto laranja. No modo só ícones (761–1000 px) os títulos somem e tudo aparece.

**Menu único e endereço por tela (V18.82):** a lista de telas vive só em `APP/nk-menu.js` (`MENU`).
O Admin desenha o menu dentro do próprio `<nav>` (`nkMenuDesenhar`); as outras telas carregam
`nk-menu.js` com `data-shell` + `nk-ui.css` e ganham o mesmo menu à esquerda (Conferência e Estação
Zebra ficam sem, de propósito: tela cheia de trabalho). Itens são links que abrem em **aba nova** (V18.87,
pedido do dono; dentro do Admin as telas do próprio Admin trocam no lugar),
Ctrl+clique também. Cada tela do Admin tem endereço (`/expedicao/pedidos-dia`, `/usuarios`...,
`ROTAS_ADMIN_V1882` no servidor), todos servem o mesmo `admin.html`; o script `rotas-admin-v1882` (o
último do arquivo) embrulha `abrirAbaAdmin`/`abrirDashboardV151` para o endereço acompanhar a tela
(F5, Voltar, link). `/admin` e `?aba=` continuam valendo. A mesma lista está no `web-auth.js` (senão a
página vira portal e o Admin não ativa), no `shared-storage.js` (senão a tela audita como "Sistema")
e no `sw.js`; `TESTES/test_rotas_v1882.py` prende os quatro. Os scripts compartilhados levam `?v=` porque
o service worker entrega `.js` do cache primeiro.

**Peças comuns e modo escuro (V18.83):** toda tela carrega `nk-ui.css` + `nk-ui.js` no `<head>`
(sem `defer`: o tema entra antes de desenhar). `nkTema` (claro/escuro, `localStorage` `nk_tema_v1883`,
botão no rodapé do menu, vale para todas as abas), `nkToast` (aviso), `await nkConfirmar(texto)` (janela
padrão; exclusão começa com foco em Cancelar) e `table[data-nk-tabela]` (clique no título ordena,
3º clique volta à ordem da tela; botão Exportar gera .csv para o Excel; `data-nk-ordenar="0"` = só
exportar). O Admin troca `alert()` por aviso (`data-alertas="toast"`) e usa `nkConfirmar` — exceto o
confirm do "atualizar o banco de produtos" (o retorno é usado na hora). **A Conferência e a Estação
NÃO trocam o `alert()`:** lá ele trava o leitor de código de barras de propósito. Peças padrão para
tela nova: `.nk-pagina`, `.nk-cab` (`.nk-rotulo` + `h1` + `p`), `.nk-kpi`, `.nk-painel`, `.nk-selo`,
`.nk-btn`, `.nk-tab` — todas só com as variáveis `--nk-*`, que o modo escuro troca. Telas com menu ganham
o cabeçalho padrão (rótulo do grupo posto pelo `nk-menu.js`, título 24px, sem faixa colorida) e o botão
«/» que recolhe o menu (o Precificador começa recolhido: `data-shell="recolhido"`).
**Modo escuro das telas antigas:** as cores fixas viram regras `html[data-tema="escuro"] ...` geradas por
`python FERRAMENTAS_WINDOWS/gerar_modo_escuro.py APP/<tela>.html` (bloco `escuro-auto-v1883`, refeito a
cada execução — rode de novo ao mexer em cor de uma tela); o ajuste à mão fica em `escuro-v1883` /
`escuro-admin-v1883`. Toda regra desses blocos começa com o prefixo do modo escuro: o claro não muda
(`TESTES/test_ui_v1883.py`).

---

## 6. Integrações

### Mercado Livre
OAuth com `offline_access`. Cinco workers em background rodam 24/7 no servidor:
novos pedidos (~20 s), etiquetas pendentes (~15 s), reconcile de pedidos manuais (~60 s),
keepalive do token (~300 s), catch-up de segurança (~900 s).
Etiquetas: `_ml_fetch_shipping_zpl` / `_ml_store_linked_label`.
Rentabilidade via billing API com cache em `ml_profitability_billing_cache`.
Tratar expiração do token: já houve cenário de `access_token` sem `refresh_token`.
**V18.56:** o `refresh_token` é de uso único — a renovação passa pelo `ML_TOKEN_LOCK` e relê
a linha sob a trava (outro já renovou? reaproveita), igual ao `SHOPEE_TOKEN_LOCK`.
**V18.78 — trabalho repetido (40% de CPU em produção, 08/10):** pedido **Full** nunca entrava
em `completed` nem em `linked_local`, então o vigia de 20 s reimportava cada Full da primeira
página a cada ciclo (16 pedidos × ~383 registros em 12 h). Agora `ml_import_order` grava o
pedido em `ml_full_ignorado_v1878` e o vigia e o catch-up pulam o que está lá (45 dias; o
webhook e o botão Atualizar continuam chamando `ml_import_order` direto). A reconciliação de
manuais (60 s) não reimporta mais linha que a API já reconheceu (venda **e** envio
preenchidos — quem acompanha a etiqueta é `_ml_all_pending_label_candidates`; envio **Full**
protegido pelo ADMIN fica fora dessa regra, senão ninguém o revisitaria). A memória de Full
tem cópia em RAM (relida a cada hora) e é **desfeita** pelo dado ao vivo: o caminho não-Full do
`ml_import_order` chama `_ml_full_ignorado_esquecer` — cache nunca é fonte de verdade. Os
workers (inclusive os `except`) usam `ml_log_event_se_mudou` / `shopee_log_event_se_mudou`
(`_log_se_mudou`): só grava quando (resultado, detalhe) mudou para aquele recurso. O backup
copia por lotes (`backup(pages=2000, sleep=0.05)`, também no `publicar.sh`): `ionice` só vale
com CFQ/BFQ. Ainda em aberto (maior ganho, mais delicado): `_ml_linked_order_ids` decodifica
o `conf_etiquetas` inteiro (com os ZPL) a cada ciclo — cachear por versão do storage.

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
**V18.71 — kit vira componentes no servidor:** a Shopee pode mandar só a linha do anúncio
do kit ("0893600022/0893764272"). `_shopee_expand_kit_lines` abre pelo Cadastro de Kits
(só kit cadastrado, ativo e com o SKU exato — como no ML), com o mesmo `__uidLinha` que o
Admin grava (`KITDB_<pedido>_<kit>_<sku>_<i>`). Antes disso o Admin abria o kit só na
**tela** (V16.46) e a Conferência via a linha colada até alguém salvar no Admin. Ordem na
sincronização: filtro da linha artificial → exclusões manuais → abrir kit → exclusões de
novo (a exclusão da linha do kit e a de um componente continuam valendo). A linha colada
antiga não é ressuscitada pelo `preserve_pending_records_v1714` quando os componentes já
estão no pedido e ela não tem bip.

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

### Financeiro — faturamento, gastos e lucro (V18.30 a V18.39)
`APP/financeiro.html` (`/financeiro`, menu Comercial, só **admin**; V18.39: acabamento — grid de 12 colunas,
filtros no cabeçalho, 3 KPIs + faixa de custos, waterfall com conectores, combo com eixos, rosca e
abas no próprio bloco de detalhe. Só apresentação; o cálculo e a API não mudaram.) Junta o que já existia:
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
**V18.53 — no frete a ordem é a inversa:** a fatura lança o envio **cheio** (parte do comprador +
parte do vendedor = `gross_amount`), e a parte do comprador não está no nosso faturamento. Venda
2000015151309807: fatura R$ 128,89, comprador pagou R$ 105,99, vendedor R$ 22,90. Frete = custo do
envio (`senders[].cost`) quando existe; a fatura só sem ele (`cobertura.pedidos_ml_frete_so_fatura`),
e a diferença fica em `pedidos[].divergencia_frete`.
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

### Auditor de cobranças do ML (V19.01)
`APP/auditoria-ml.html` (`/auditoria-ml`, menu Visão geral abaixo do Financeiro, módulo **admin**; spec em
`docs/superpowers/specs/2026-10-11-auditoria-cobrancas-ml-design.md`). Encontra na fatura do ML o que parece
cobrado indevidamente e acompanha cada **caso** (`ml_cobranca_caso_v1901`) até o fim. Não há API para contestar:
a equipe contesta na Central de Ajuda do ML (texto pronto + planilha .csv na tela); o auditor **só lê** o ML.
Tipos: **estorno que não veio** (pedido cancelado: tarifa e frete; reembolso total por reclamação: só a tarifa;
reembolso parcial fica fora), **frete de devolução** (reclamação fechada que o vendedor ganhou ou que o ML cobriu —
mesma regra do Pós-venda), **tarifa/frete acima do esperado** (tarifa da fatura × `ml_venda_custo_v1830`; frete da
fatura **por envio** × valor cheio do envio, `ml_frete_envio_v1830.bruto` = `gross_amount` da mesma resposta do
`/shipments/{id}/costs`; abaixo de R$ 2,00 só soma no cartão "Diferenças pequenas") e **ajuste de peso** (desligado:
`ML_FATURA_AJUSTE_PESO` vazio até achar o código em `GET /api/auditoria-ml/codigos`, só perfil Admin). Um
lançamento pertence a **um caso só** (estorno > devolução > peso > acima). Carência de 30 dias ("Aguardando
estorno") para estorno e devolução. **Regra de ouro:** a rodada atualiza números e lançamentos, **nunca** a situação
humana (contestado, negado, ignorado); só vira Recuperado/Recuperado em parte pelo crédito na fatura (recuperado =
maior diferença vista − diferença atual). Ignorado (motivo obrigatório) fica na tabela e por isso nunca é recriado.
`auditoria_detectar` é **pura** (só banco); a rodada `auditoria_rodada` (worker `ml_auditoria_loop`, uma vez entre
02:00 e 06:00, ou "Auditar agora") baixa a fatura (mês atual e anterior + um mês antigo por noite até 6, com o
`FINANCEIRO_SYNC_LOCK`), consulta `/orders/{id}` dos pedidos com cobrança que sumiram de `vendas_v1799`
(`ml_pedido_status_v1901`, uma vez cada) e completa o valor cheio do frete. Só Araçatuba por enquanto. Linha no
relatório das 08:00 (`auditoria_resumo_email`; cada caso avisado uma vez, `avisado_em`).

### Remessa Full automática (V19.03)
`APP/analise-full.html` (seção no topo da **Demanda Full**; spec em `docs/superpowers/specs/2026-10-11-remessa-full-automatica-design.md`).
Monta sozinha a remessa para o Full do ML e acompanha até o recebimento. Tabelas: `full_remessa_v1903` (cabeçalho, situação,
`fora_json`, `sem_apollo`), `full_remessa_item_v1903` (uma linha por anúncio) e `full_nao_aceito_v1903` (tombstone **por código**).
**Candidatos** (`full_remessa_candidatos`): anúncio Full = `ml_estoque_dia_v1872` com logística `fulfillment` (últimos 7 dias), SKU de
`ml_visitas_item_v1868`. **Cálculo** (`full_remessa_calcular`, puro, sem Apollo nem ML): venda de 30 dias, **kit conta 1** unidade,
estoque disponível do Apollo **só Araçatuba** (revenda 1) dividido entre anúncios do mesmo código, mais urgente primeiro;
`disponivel: null` (Apollo sem resposta) **nunca vira zero** e o item fica sem quantidade (conta em `sem_apollo`, aviso na tela).
"A caminho" só conta remessa **agendada**, item confirmado e com quantidade. **Rodada** (`full_remessa_rodada`): existe **uma** remessa
aberta; ajuste do ADMIN (`qtd_admin`, `removido`, `confirmado`) **nunca é desfeito** pela rodada; se o ADMIN agendou durante a leitura
do Apollo, a rodada é descartada (cálculo velho). **Novos** (código com ≥ 10 vendas no ML, sem anúncio Full, kit e "A/B" fora, máx. 10):
só sugeridos e **só entram com confirmação**. **Não aceito no Full** (ML recusou): tombstone por código, não volta como sugestão
nem como novo. Situações: `montando` → `pronta` (≥ **150** unidades confirmadas) → `agendada` (congela; a próxima rodada abre outra)
→ `recebida` (automática quando o estoque no Full subiu ≥ **50%** da quantidade; remessa **só de novos** só manual). Worker
`full_remessa_loop` (depois das 05:00 Brasília, 1x/dia; no `SAUDE_WORKERS_INFO`). Aviso 1x/dia pelo `aviso_venda_v1893` com
`marketplace='full'` (popup/push de remessa, não de venda); linha no e-mail das 08:00 quando há remessa pronta. Rotas
`/api/full/remessa*` e `/api/full/nao-aceito*` (módulo **admin**; ValueError vira 400). O botão **Procurar datas** manda
`postMessage` para a extensão **Agendador Full v0.3** (contrato em `INTEGRACAO_AGENDADOR_FULL.md`; extensão **ainda não feita**, fica
fora deste repo como as outras). Teste: `TESTES/test_remessa_full_v1903.py`.
**Revisão final:**
- Apollo parcial: fica `pronta` quando as unidades de itens com estoque do Apollo **conhecido** chegam a 150 (obs "Apollo não respondeu para N códigos"); sem resposta nenhuma, `montando`. O worker dá 3000 s ao Apollo; o "Montar agora", 600 s.
- "A caminho" vence: agendada há mais de **10 dias** da data agendada vira `recebida` ("recebida por prazo").
- Anúncio Full com SKU "A/B" = um de cada (conta 1); parte sem código Apollo = fora "SKU sem código Apollo".
- Novo sem estoque no Apollo (0) vai para fora "sem estoque no Apollo" (sem resposta: "Apollo não respondeu"), não para os novos.

### Painel de saúde e alertas (V18.83)
`APP/saude.html` (`/saude`, menu Sistema; ver = módulo admin, mudar quem recebe = só perfil Admin).
`GET /api/saude` (`saude_payload`): Mercado Livre (conexão, token, última busca de pedidos), Shopee,
fila da Zebra (job parado > 10/30 min) e estações, rotinas (workers), backup diário (> 26/30 h), disco,
banco/WAL, memória (`/proc/meminfo`), CPU do processo, erros das integrações na última hora, vendas,
relatório diário e promoções automáticas. Cada checagem que quebra vira "atenção", nunca derruba o painel.
**Pulso dos workers:** o `_worker_protegido` entrega ao worker um `_EventoComPulso` — cada
`stop_event.wait()` registra quando pausou, por quanto tempo e quando acordou (`SAUDE_WORKERS`). Sem
mexer nos workers dá para ver esperando / trabalhando / travado (limite por worker em
`SAUDE_WORKERS_INFO`). Quem espera por outro evento usa `_saude_esperar` (Shopee etiquetas). **Worker
novo no `main()` precisa entrar em `SAUDE_WORKERS_INFO`** (o teste confere).
**Alertas** (`saude_alertas_loop`, 1 min): e-mail quando um item fica Crítico por
`SAUDE_ALERTA_RODADAS` (3) rodadas seguidas, nada nos 10 min depois do boot, lembrete a cada
`lembrete_horas` (6) e e-mail de "resolvido". SMTP com falha: não marca como avisado e espera 15 min
antes de tentar de novo. Destinatários em `saude_config_v1883` (padrão: `RELATORIO_EMAILS`); estado em
`saude_alerta_estado_v1883`, histórico em `saude_alerta_log_v1883` (1000 últimos). Link do e-mail:
`EXP_PUBLIC_URL` (padrão https://noromakweb.com.br/expedicao). WhatsApp fica para depois (provedor a escolher).

### Vendas por conta no Dashboard (V18.98)
Decisão do dono (10/10): filtro **Todas / Araçatuba / Bauru** pela conta do ML que vendeu (Shopee = Araçatuba),
e por enquanto **só no Dashboard**. As vendas das outras contas ficam em `vendas_conta_v1898` (mesmas colunas
+ `conta`), preenchida pelo mesmo `vendas_sync_loop` rodando `vendas_sync_marketplace("ml")` dentro de
`ml_na_casa(casa)`; as marcas de sincronização ganham sufixo (`ml@bauru`, `_vendas_chave`). As 12 telas que
leem `vendas_v1799` (Financeiro, Demanda de Compra, relatório, Conversão, Ruptura...) continuam só com Araçatuba;
o custo da venda (`ml_venda_custo_v1830`) também. `vendas_dashboard_payload(..., conta=)` lê a fonte certa
(UNION em "todas") e devolve `por_conta`; `GET /api/dashboard/vendas?conta=`. A escolha fica no navegador
(`nk_dash_conta_v1898`).

### Conferência por filial (V18.95)
Decisões do dono (10/10, entrevista): cada usuário tem uma **filial** (`app_users.casa`: aracatuba, bauru ou
— só perfil Admin — todas); na migração, Admin = todas e os demais = Araçatuba. Quem tem "todas" escolhe a
filial no **seletor do rodapé do menu** (`nk-menu.js`, `localStorage nk_filial_v1895`); o `shared-storage.js`
manda `X-Noromak-Filial` em toda chamada à API (inclusive `fetch` das telas) e o servidor só aceita o
cabeçalho de quem tem "todas" (`filial_efetiva`). **O filtro é no servidor**: `storage_filtrar_por_filial` em
toda leitura e resposta de `conf_pedidos`, `conf_etiquetas` (filial do registro = a do pedido na fila, senão
`casa` do registro), `conf_historico_conferencias_v2`, `conf_logs` e `conf_coleta_dia_v1` (Bauru em
`porFilial.bauru`). **Na gravação** `storage_remontar_por_filial` junta de volta o que o usuário não via (no
novo e no oldValue) ANTES das regras de exclusão/merge/override — sem isso a fila filtrada gravada por um Admin
viraria tombstone ADMIN da outra filial — e marca com a filial o que nasce sem marca. Rotas de pedido
(concluir, travar, excluir linha/kit, Nome Conferência, SKU, EAN) passam por `pedido_de_outra_filial` (403
`OUTRA_FILIAL`) e devolvem a fila filtrada; o corpo da requisição fica guardado (`read_body`) e é zerado a cada
requisição (`handle_one_request`). Pedido manual (`admin-order-upsert`, planilha, Ler print) fica na filial de
quem incluiu. `Casa` = filial que confere; `ContaMercadoLivre` = conta do ML (etiqueta, token, reconciliação —
`ml_casa_da_linha`). **Mover de filial** (Pedidos pendentes, só perfil Admin com "todas";
`POST /api/pedido-mover-filial`) grava pelo `save_storage` como ADMIN: vira override do campo `Casa` e a
importação não desfaz. **Bauru imprime em PDF** (térmica não-Zebra): `GET /api/etiqueta-ml-pdf?pedido=` baixa
`/shipment_labels?response_type=pdf` com a conta do pedido e a Conferência abre a janela de impressão
(`imprimirEtiquetaPdfV1895`). Em Bauru o operador informa a coleta clicando na caixa da coleta. Usuários:
campo Filial na tela (via `POST /api/roles`, `filial`), na aprovação de solicitação e no `conf_usuarios`. **V18.96:**
o `carregarBancoInternoAdmin` (roda a cada mudança da fila) não troca mais a lista oficial de usuários
(`window.__usrCanonicoV1896`): antes a alteração não salva na tela de Usuários "voltava". Ainda só da principal: Dashboard,
Financeiro, vendas e demais telas de gestão (V18.96).

### Casas: Araçatuba e Bauru (V18.94)
Bauru tem **só conta do Mercado Livre** (a Shopee é só de Araçatuba) e expede **neste sistema, separada por
filial** (V18.95). **A V18.94 não sobe sem a V18.95** (sem ela os pedidos de Bauru apareceriam na fila de
Araçatuba).
Araçatuba (`CASA_PRINCIPAL`) continua na linha id=1 de `ml_integration`; as outras casas ficam em
`ml_integration_casa_v1894`. Todo o código do ML lê a conta da **casa atual** (`ml_casa_atual()`, por thread);
`with ml_na_casa("bauru"):` faz o mesmo código usar a conta de Bauru (thread nova precisa entrar na casa de novo).
Os 5 workers da expedição ML (pedidos novos, etiquetas, reconciliação de manuais, token, catch-up) rodam para
cada casa de `ml_casas_conectadas()`; os de vendas, Ads, visitas, promoções e pós-venda ainda só para a principal.
Cada casa tem a própria trava de renovação (`_ml_token_lock`). **V18.97:** venda cujo envio já saiu (`ML_ENVIO_JA_DESPACHADO`: shipped, delivered,
not_delivered) não entra na fila, a linha da integração sai (a protegida pelo ADMIN fica) e o pedido vai para a
memória do vigia (`ml_full_ignorado_v1878`); o vigia de etiquetas tira da fila quem esperava etiqueta e já saiu —
ao conectar Bauru (10/10) entraram as vendas entregues de setembro. A mesma conta do ML nunca fica em duas casas
(`ml_store_token` recusa). O OAuth leva a casa no `ml_oauth_state.casa`; o webhook vai para a casa do `user_id`
(`ml_casa_do_seller`). Pedido importado ganha `Casa` (linha sem `Casa` = principal: tudo o que existia, manual,
Shopee); `_ml_all_pending_label_candidates`, `_ml_manual_pending_numbers` e
`_ml_recover_local_pending_identifiers_once` só olham as linhas da casa atual. Log do ML marca `[Bauru]` no
tópico. Rotas `status`/`connect`/`disconnect` aceitam `?casa=bauru`; cartão "Mercado Livre · Bauru" na Central
de Integrações. Ainda falta: botões que reimportam um pedido específico (rodam na principal) e a separação da fila.

### Avisos de venda (V18.93)
Popup com som no canto da tela e notificação no celular/computador **com o site fechado** (Web Push).
Liga **por aparelho** no botão "Avisos de venda" do rodapé do menu (`APP/nk-avisos.js`, carregado pelo
`nk-menu.js`; só quem tem o módulo **admin**; Conferência e Estação ficam sem, de propósito). Quem descobre
a venda são os vigias que já existem — `ml_new_order_watch_once` (o mesmo `/orders/search` de 20 s) e a
sincronização da Shopee (o mesmo `get_order_detail` de 1 min): **nenhuma chamada a mais**. `avisos_venda_registrar`
grava em `aviso_venda_v1893` (carrinho do ML = uma venda, `pack:<id>`); a **1ª leitura de cada marketplace
só marca** o que existe (sem enxurrada no dia da publicação) e venda vista pela 1ª vez com mais de 48 h entra
calada. O worker `avisos_venda_loop` manda um push **vazio** a cada aparelho de `aviso_inscricao_v1893`; o
service worker (`sw.js`) pede as vendas em `POST /api/avisos-venda/entregar` (autoriza pelo endereço de push
inscrito — o cookie pode ter vencido no celular). Push vazio dispensa criptografar o conteúdo: só a assinatura
VAPID (ES256, P-256 em **Python puro**, `p256_assinar`, conferida com o vetor da RFC 6979 e com o OpenSSL).
O par de chaves fica em `aviso_config_v1893` e **nunca** é trocado (trocar invalida todos os aparelhos). Um
push por vez por aparelho (`pendente_desde`, 10 min) + `Topic`: sem pilha quando o celular volta a ficar
on-line; renovar a inscrição ao abrir a tela **não** adianta o ponto (perderia a venda a caminho). 404/410 do
serviço de push = aparelho sai. Com uma tela do Noromak à vista, o service worker manda as vendas para ela
(popup + som); sem, notificação do sistema (o Safari exige notificação sempre). Sem push (navegador sem
suporte ou notificação bloqueada), a tela aberta consulta `/api/avisos-venda/recentes` a cada 30 s. **V18.96:** a
Apple recusa o cabeçalho `Topic` (HTTP 400 `BadWebPushTopic`): ele só vai para os outros serviços; o "Enviar
teste" recusado volta 200 com o motivo (502 virava a página de erro do nginx). iPhone:
só com o Noromak instalado na Tela de Início (iOS 16.4+). Endereços de push aceitos: `AVISOS_PUSH_HOSTS`.

### Pós-venda do Mercado Livre (V18.88)
`APP/posvenda.html` (`/posvenda`, menu Vendas e anúncios, módulo **admin**; `GET /api/posvenda?dias=30`,
`posvenda_payload`, só lê o banco). Worker `posvenda_loop`: perguntas a cada 5 min, reclamações e
reputação a cada 15 min. **Reclamações** (`ml_reclamacao_v1888`): `/post-purchase/v1/claims/search` sempre
com `players.role=respondent` + `players.user_id` (a doc proíbe busca só por status); `status=opened` + `range=`
(1ª carga: `date_created` dos últimos 180 dias; depois `last_updated` desde a mais recente − 1 dia; data
**com milissegundos**). Nova/alterada (ou aberta lida há > 1 h) é detalhada: `/detail`, `/affects-reputation`,
`/orders/{id}` (total e `transaction_amount_refunded`), `/post-purchase/v2/claims/{id}/returns` (+ `/reviews`
da triagem) e `/charges/return-cost` (frete da devolução cobrado do vendedor). 150 por rodada. Prazo = ação
obrigatória do vendedor (`prazo`) ou o do `/detail` quando a vez é do vendedor (`prazo_detalhe`, coluna própria
para a busca não apagar). Responder reclamação continua **no ML** (decisão do dono, 09/10).
**Perda** (`_posvenda_valores`): frete da devolução + custo do produto que não voltou ou voltou inservível
(custo = regra do Financeiro, `_financeiro_custo_unitario`) + reembolso parcial com o produto ficando com o
comprador. Produto que voltou bom não é perda; cobertura do ML ou vendedor vencedor = 0. "Vendas desfeitas" é
mostrado à parte e **não** é perda. Valor que o ML não informou fica nulo e a reclamação vira "incompleta" —
nunca zero. **Perguntas** (`ml_pergunta_v1888`): `/questions/search?seller_id&status=UNANSWERED&api_version=4`;
a que sai da lista é relida (`/questions/{id}`). Responder: `POST /api/posvenda/responder` → `POST /answers`
(JSON, via `_ml_promo_http`), audit `ml_pergunta_responder`. **Reputação**: `/users/{id}` → `seller_reputation`,
um retrato por dia em `ml_reputacao_dia_v1888`. **Painel de saúde**: `posvenda_prazo` fica Crítico (e-mail)
com reclamação cujo prazo do vendedor vence em < 24 h. Sem permissão no DevCenter (403) aparece na tela e no
painel, nunca vira "nenhuma reclamação".

### Visitas dos anúncios (V18.68)
Base da futura análise de **conversão** (vendas ÷ visitas). Worker `ml_visitas_loop`: depois das
06:00 (Brasília), uma rodada por dia busca `/items/{MLB}/visits/time_window?last=30&unit=day` de
**todos** os anúncios (`ml_todos_os_anuncios`) — a API aceita **um** anúncio por chamada, então são
~1.600 chamadas espaçadas (0,25 s). Grava em `ml_visitas_dia_v1868` (item, dia, visitas): a janela
de 30 dias é regravada e o que é mais antigo fica como histórico. `ml_visitas_item_v1868` marca o
dia em que cada anúncio foi buscado, e a rodada **retoma** só o que falta (restart, erro). 404 =
anúncio sumiu, espera o dia seguinte; 5 erros seguidos (sem conexão/permissão) param a rodada.
Dia sem número na resposta **não** vira zero. Situação: `GET /api/ml-visitas/status` (admin).
**O ML só devolve os dias que tiveram visita** (visto em produção, 06/10): anúncio sem nenhuma visita
no período não tem linha nenhuma. A resposta inclui o dia de hoje (parcial); a tela usa até ontem.
A mesma rodada guarda o retrato do anúncio (título, SKU, preço, status, estoque, categoria — 1x por
dia, `/items?ids=` de 20 em 20) e o nome da categoria (`ml_categoria_v1868`), para a tela não chamar o ML.
**Tela Conversão** (`APP/conversao.html`, `/conversao`, menu Comercial, só **admin**;
`GET /api/conversao?dias=30`, `conversao_payload`): conversão = **pedidos** ÷ visitas (como o ML
mede), hoje fora (não fechou), comparado com o período anterior de mesmo tamanho. Referência =
mediana da **categoria** (5+ anúncios com 30+ visitas), senão da conta. Grupos: vitrine sem venda
(muito visto, < metade da referência), abaixo, joia escondida (pouco visto, ≥ 1,5× a referência e
3+ vendas), campeão, na média, poucas visitas (< 30, ruído). "Potencial" = visitas × (ref − conv) ×
ticket — **estimativa** para ordenar, marcada assim. Anúncio que vendeu sem visita coletada fica
**fora** da conta (`vendeu_sem_visita_coletada`), nunca como conversão infinita.
**Dicas** (só no front, `dicasDe`): por situação e com os números do anúncio — estoque (pausado,
zerado, dura < 10 dias), preço, compatibilidade, fotos/título, frete (abaixo de R$ 79 sugere kit),
Ads (reduzir quando ≥ 25% das visitas são pagas e não convertem; criar/aumentar na joia), exposição
e perguntas. Máximo 3 "Fazer primeiro"; anúncio parado (pausado/encerrado/sem estoque) não recebe
dica de escalar. A primeira dica vira a coluna "Próxima ação".

### Ruptura de estoque (V18.72)
`APP/ruptura.html` (`/ruptura`, menu Estoque e compras, só **admin**; `GET /api/ruptura?dias=30`,
`ruptura_payload`). Venda perdida por falta de estoque, **só ML** por enquanto. O retrato de
`ml_visitas_item_v1868` é sobrescrito todo dia, por isso o histórico fica em `ml_estoque_dia_v1872`
(item, dia, checagens, checagens sem estoque, estoque mínimo/último, status, logística), alimentada
pela rodada diária das visitas (todos os anúncios) e pelo worker `ml_estoque_loop` (a cada 2 h, só
os anúncios que venderam nos últimos 60 dias, 20 por chamada). **Sem estoque** = quantidade 0 ou
sub_status `out_of_stock`; pausa manual com estoque **não** é ruptura (grupo "pausado vendendo");
encerrado fica fora; estoque não informado **não** vira zero. Ritmo = unidades dos últimos 60 dias ÷
dias **com** estoque (dia sem checagem conta como com estoque: o ritmo fica mais baixo, nunca
inflado). Perdido = ritmo × dias sem estoque (fração das checagens) × ticket — **estimativa**.
Grupos: zerado agora (R$/dia perdido), vai zerar (≤ 7 dias), atenção (≤ 15), pausado, ok. Full vem
de `shipping.logistic_type` (`ml_itens_detalhe` passou a pedir `shipping`). O histórico só começa a
contar a partir da publicação: "zerado agora" e "vai zerar" valem desde o primeiro dia.

### Conversão e Ruptura da Shopee (V18.90)
As duas telas ganharam a escolha **Mercado Livre / Shopee** (`?mp=shopee`; APIs com `&marketplace=shopee`).
`conversao_payload` e `ruptura_payload` recebem `marketplace` e leem as tabelas de `ANUNCIO_FONTES` — as da
Shopee têm **as mesmas colunas** das do ML (`shopee_item_v1890`, `shopee_visitas_dia_v1890`,
`shopee_estoque_dia_v1890`), e o status é traduzido para o vocabulário do ML (`SHOPEE_STATUS_ML`: NORMAL=active,
UNLIST=paused, deletado=closed). Worker `shopee_anuncios_loop` (30 min): depois das 06:00, **retrato do dia**
(`get_item_list` NORMAL+UNLIST, `get_item_base_info` em lotes de 50 — título, SKU, preço, estoque
`stock_info_v2.summary_info.total_available_stock` — e `get_item_extra_info` — `views`); fora dele, a cada 2 h,
o estoque de quem vendeu nos últimos 60 dias. **A Shopee não tem visita por dia na API**: `views` é o total
acumulado; a visita do dia anterior = retrato de hoje − o retrato anterior (`shopee_views_v1890`, 10 dias).
Se o total **cair**, o dia não entra e o anúncio fica com `views_nao_acumulado=1` (aviso na tela). Sem categoria
nem Ads da Shopee: referência = mediana da conta e a tela não dá dica de Ads (`tem_ads`). Anúncio com variações
conta o estoque somado. `_ml_estoque_registrar(..., tabela=)` aceita só as duas tabelas de estoque.

### Promoções automáticas — renovação do desconto próprio (V18.73)
`APP/promocoes-auto.html` (`/promocoes-auto`, menu Vendas e anúncios, só **admin**). Quando o
desconto próprio (`PRICE_DISCOUNT`) de um anúncio **marcado pelo ADMIN** acaba, cria outro de
`duracao_dias` (30) com o **mesmo preço** da promoção que acabou (o preço é atualizado a cada
conferência enquanto a promoção vale; "alterar" na tela grava outro). Worker
`promo_renovacao_loop`: rodada completa de hora em hora e, perto do vencimento
(`_promo_proxima_espera`), acorda 1 min depois do `finish_date` (sem fuso = Brasília) e confere
**só** os vencidos a cada 2 min por até 4 h — a lacuna sem desconto caiu de até 1 h para ~1–2 min.
A criação passa por `ml_promocao_aplicar()` (trava do preço mínimo, piso/teto do ML, data do
monitor). **SKU** (`_promo_resolver_sku`, vale também para o `ml_promocao_aplicar`): o vínculo
MLB↔SKU manda; sem ele, o SKU do próprio anúncio (só 7% tinham vínculo). **Kit**
(`_promo_produtos`): custo = componentes com o custo de hoje (kit do Precificador, senão Cadastro
de Kits com as taxas do 1º componente); componente sem custo = kit sem custo. Antes de renovar
(`_promo_renovacao_avaliar`): anúncio ativo, custo, preço do anúncio acima do da promoção, ≥ preço
mínimo, piso/teto, margem ≥ **margem mínima da promoção** (`margem_min_promo`, padrão 10% — não a
margem-alvo, que desconto quase sempre fura) e estoque pela Ruptura (`estoque_min_dias`). Em
**campanha do ML** espera ela acabar; margem na campanha abaixo da mínima vira só **aviso**
(decisão do dono: não sai da campanha). Modos em
`promo_auto_config_v1873`: `simulacao` (padrão, não chama o ML) → `lote` (clique) → `automatico`
(ligar exige **senha + 2FA do OWNER**; `limite_dia`). Nunca renova o mesmo anúncio duas vezes no
dia (`renovado_dia`). Tudo em `promo_auto_log_v1873`. A lista junta os marcados e o monitor do
Precificador (`ml_promocoes_datas_v1`). **1ª renovação real OK em 07/10** (MLB1362876721, lote).
**V18.74 — o dado ao vivo manda:** MLB1050410219 aparecia "Sem estoque" com 10 un. no ML — a
Ruptura tinha checado às 08:14 (pausado, 0 un.) e o anúncio foi reativado depois. Agora o estoque
lido NA conferência decide (da Ruptura só vem o ritmo de venda) e a leitura entra no histórico
dela (`_ml_estoque_registrar`). Marcar já confere na hora (`apenas=[mlb]`, regras do worker); a
tela mostra o preço do anúncio lido na conferência (`preco_anuncio`, migrado por
`ensure_promo_renovacao_schema`) e, depois de conferido, **só a data do ML** — a do monitor só
antes, marcada "pelo monitor, não conferido" (`fim_origem`). **V18.75 — tela no padrão Noromak**
(rótulo + título + cartões com ícone da Visão geral): "Todos" é o filtro padrão, busca por MLB/SKU/título,
páginas de 25/50/100 (lista tem 1.100+ anúncios), cartão "Renovadas em 30 dias" abre a lista própria
(`renovado_em`), histórico e regras recolhíveis. **V18.76 — remover da lista** (anúncio apagado no ML):
`promo_renovacao_remover` apaga a linha de renovação e grava o tombstone `promo_removidos_v1876`
(some da lista mesmo vindo do monitor; a rodada e o clique Renovar nunca o tocam); **incluir de novo
desfaz** (reinclusão explícita do ADMIN). Não chama o ML. Seleção em lote em qualquer linha (Renovar só
vale para marcados sem promoção valendo); Incluir virou janela; Regras em 4 blocos com Salvar só quando
há alteração. **V18.77 — gaveta de preço e taxas do ML:** clicar na linha (ou "alterar") abre
uma gaveta com o Precificador reduzido: preço, régua mínimo/ideal, atalhos, margem e lucro. Custo
vem do **Apollo** como no Precificador (filial do produto, padrão Araçatuba; cartões por filial);
custo e taxas editáveis gravam **só aquele SKU** no `precificador_online_v1` pelo merge
(`POST /api/promocoes-auto/produto`, `precificador_produto_ml_atualizar`; kit não edita custo).
**Comissão e frete vêm do ML por anúncio** (`ml_tarifa_frete_anuncio`: `/sites/MLB/listing_prices`
+ `/users/{seller}/shipping_options/free`, teste real de 08/10 bateu centavo a centavo com o
cobrado; taxa fixa veio 0, a comissão varia por categoria). Decisão do dono: a **renovação, a
coluna Margem, o aviso de campanha e a trava do mínimo** (`ml_promocao_aplicar`) usam esses valores
(`_promo_prod_com_ml`); ML sem resposta = valor do Precificador, registrado no motivo. Comissão/frete
do ML **não** são gravados no Precificador (valor por anúncio ≠ valor por produto). Cache 3 h.
Próxima etapa: convites de campanha.
**V18.80 — custo de kit nas Promoções:** a tela Promoções (`promocoes_painel`) lia
`_precificador_produtos_ml()`, que pula os kits; agora usa `_promo_produtos()`. Kit fora do
Precificador e do Cadastro de Kits abre pelo SKU do anúncio (`_promo_kit_pelo_sku`, regra da Demanda de
Compra: `full_kit_compositions` e "A/B" = um de cada, com ou sem "KIT-"); todo lookup passa por
`_promo_produto`. **O custo do Precificador manda** (kit com itens = mesma conta da tela; 151 kits estão
lá como produto comum, "KIT2-PA18", com custo digitado). Só quando ele está **zerado** soma as peças
pelo SKU, com as taxas do cadastro do kit (zeradas = as da 1ª peça; decisão do dono, 08/10). A tela
Promoções diz de onde veio o custo (`precificador.kit`) e custo 0 não mostra mais margem (dava 88%). `_promo_resolver_sku` não deixa mais o `_compras_codigo` ler "KIT14-X" como o simples
"X" (custo de 1 peça, margem inflada). "KIT27200155" sem cadastro continua sem custo (rótulo, V18.19).
**V18.81 — título sem retrato:** título/SKU da lista vinham só do retrato diário
(`ml_visitas_item_v1868`, 06:00); anúncio criado depois aparecia "(título ainda não coletado)". A
conferência de hora em hora grava o título e o SKU lidos no ML (`promo_renovacao_v1873.titulo/sku_ml`,
migração em `ensure_promo_renovacao_schema`); o retrato do dia continua mandando quando existe.
**V18.84 — "Buscar novos no ML":** botão **manual** (sem worker, por decisão do dono) na lista;
`POST /api/promocoes-auto/renovacao/buscar-novos` → `promo_renovacao_buscar_novos`. Lê todos os
anúncios (`ml_todos_os_anuncios(forcar=True)`) e inclui **sem marcar** o que não está na lista
(nem na tabela nem no monitor), com título/SKU/preço do `ml_itens_detalhe` (só de quem falta).
Removido pelo ADMIN (`promo_removidos_v1876`) nunca volta por aqui (conferido de novo sob a trava);
encerrado e sem resposta do ML não entram. Fica no histórico como "Novos do ML".
**V18.89 — convites de campanha do ML** (seção própria na mesma tela; `GET /api/promocoes-auto/convites`,
`POST .../convites/aceitar` e `.../convites/buscar`). Tipos: `SMART`, `PRICE_MATCHING`, `MARKETPLACE_CAMPAIGN`
e `DEAL` (`PROMO_CONVITE_TIPOS`). Worker `promo_convites_loop` (3 h) lê `/seller-promotions/users/{id}` e
`/seller-promotions/promotions/{id}/items` (paginado por `search_after`) para `ml_campanha_v1889` /
`ml_convite_v1889`, e avalia cada convite (`_promo_convite_avaliar`) com a régua da renovação: custo
(`_promo_produto`), comissão/frete do anúncio (`_promo_prod_com_ml`), preço mínimo, `margem_min_promo` e
estoque (`_promo_estoque_curto`). Na cofinanciada a margem usa o que o **vendedor recebe** =
preço original × (1 − `seller_percentage`) — o ML paga a parte dele; o boost não entra (margem real ≥ a
mostrada). **DEAL** (decisão do dono, 09/10): preço sugerido se der margem, senão o maior que o ML aceita
(`max_discounted_price`), senão não aceita. Aceitar (`promo_convite_aceitar`) só o que está "pronto", **reconferido
com o anúncio lido agora**; corpo do POST por tipo (SMART/PRICE_MATCHING com `offer_id`; DEAL com `deal_price`;
cofinanciada só a campanha). Modos em `modo_convites` (simulação → lote → automático; ligar o automático exige
OWNER, `limite_dia` vale para os dois). Convite que sai da lista do ML é apagado; aceito fica. Histórico em
`promo_auto_log_v1873` com `tipo='convite'`. Removido pelo ADMIN não aparece. Em campanha, a renovação do
desconto próprio espera ela acabar (regra da V18.73).
**V18.99 — conta de Bauru** (decisões do dono, 10/10): as REGRAS (margem mínima da promoção, duração,
estoque) valem para as duas contas; o **modo** (renovação e convites) e o **limite do dia** são de cada conta
(`PROMO_AUTO_POR_CONTA`; na principal as chaves de sempre, nas outras `chave@conta`, e sem valor gravado Bauru
começa em Simulação). `promo_auto_config()` lê a conta da **casa atual** — dentro de `ml_na_casa("bauru")` o modo
é o de Bauru. `promo_renovacao_v1873.conta`, `promo_auto_log_v1873.conta` e `ml_convite_v1889.conta`;
`ml_campanha_v1889` foi refeita com PK `(conta, promotion_id)` (a mesma campanha do ML pode convidar as duas
contas). `promo_renovacao_rodada` e `promo_convites_rodada` dão uma volta por conta conectada
(`_promo_renovacao_rodada_conta` / `_promo_convites_rodada_conta`): cada conta só confere/aceita o que é dela,
com o token dela, e o limite do dia conta por conta; a limpeza dos convites é só da conta da volta. Aceitar
convite roda na conta do convite. "Buscar novos no ML" passa pelas duas contas (lista de anúncios em cache por
conta, `_ML_ANUNCIOS_CACHE_CASA`). Renovação de Bauru **não** atualiza o monitor do Precificador (é de
Araçatuba). Tela: filtro Todas / Araçatuba / Bauru (`nk_promo_conta_v1899`), selo da conta em cada anúncio e
convite, os botões de modo mexem na conta do filtro (Todas = Araçatuba) e o Incluir escolhe a conta. Bauru ainda
não tem ritmo de venda na Ruptura (as vendas dela não estão em `vendas_v1799`): o critério de estoque mínimo em
dias fica sem dado para ela.
**V18.92 — conferência rodando e dois computadores:** a rodada de hora em hora (~5 min para ~250
marcados) segurava a vez inteira: o Renovar clicado no meio esperava até 120 s e o nginx (60 s)
dava **HTTP 504**; o anúncio marcado no meio ficava "Ainda não conferido" até a hora seguinte
(a conferência imediata desistia em 0,01 s). Agora `PROMO_RENOVACAO_LOCK` é a vez de **um
anúncio** (ler o ML, decidir, gravar) e a linha é **relida sob ela** — desmarcar, remover ou mudar
o preço em outro computador não é desfeito por uma rodada que começou antes; dois cliques em
Renovar criam uma promoção só. `PROMO_RODADA_LOCK` só impede duas rodadas **completas** juntas.
Marcar, remover e o clique entram entre um anúncio e outro (a `pausa` fica fora da vez). O
Renovar tem prazo (`PROMO_MANUAL_PRAZO`, 40 s) e devolve `pendentes`, que a tela pede de novo.
A tela consulta `GET /api/promocoes-auto/renovacao/versao` a cada 10 s (impressão digital só do
que a lista mostra — sem `checado_em`, senão recarregaria a lista inteira durante a rodada) e
recarrega quando muda, sem apagar regra digitada e não salva. A gaveta manda `preco_antes`; preço
mudado em outro computador = **409** com o preço novo, nunca sobrescreve calado
(`TESTES/test_promo_concorrencia_v1892.py`).

**V19.04 — campanhas na linha** (decisões do dono, 12/10): as campanhas do ML passam a ser lidas **por anúncio**
(`GET /seller-promotions/items/{MLB}?app_version=v2`, com o token da conta do anúncio), porque a lista da conta
(`/seller-promotions/users/{id}`) não traz todas (SMART e PRICE_MATCHING só apareciam por anúncio); gravam em
`ml_convite_v1889` como antes. O `promo_renovacao_payload` anexa a cada linha `lucro_promo`, `campanhas` e
`campanha_melhor` (+ `resumo.campanha_mais_lucro`). Critério = **lucro por venda em R$**, numa base só por linha:
o mesmo `prod` calcula a promoção e todas as campanhas — comissão e taxa fixa+frete do ML **gravadas na conferência**
da renovação (`promo_renovacao_v1873.comissao_ml/taxa_fixa_ml/tarifa_em`, a mesma base da coluna Margem; ML sem
resposta não apaga), sem elas as do Precificador; o payload não chama o ML. Aproximação: ignora a troca de faixa de
comissão/frete grátis entre os dois preços. Relâmpago não é sugerida para anúncio parado/sem estoque. Melhor = lucro maior que o da
promoção, margem ≥ `margem_min_promo` e situação pronta; sem custo fica nulo ("sem custo"), nunca zero. Na tela: coluna
"Campanha do ML" (melhor + Aceitar, "ver outras N"), cartão e filtro "Campanha do ML dá mais lucro", "lucro R$/venda"
na Promoção. **Oferta relâmpago** entra na comparação mas só para ver ("aceitar no ML": exige escolher estoque). Só
sugere; aceitar continua por `promo_convite_aceitar`. A **lista separada de convites saiu**; o modo dos convites
(simulação/lote/automático com OWNER) e o "Buscar convites" foram para o bloco de Regras, mesmos ids.

### Análise de ADS (V18.54)
`APP/analise-ads.html` (`/analise-ads`, menu Comercial, só **admin**). O relatório "Anúncios
patrocinados" que o ML deixa baixar em Publicidade > Relatórios (.xlsx) é **importado à mão**
(`POST /api/ads-analise/importar`, base64) e lido só com zipfile + ElementTree
(`ads_relatorio_ler_xlsx`). Fica em `ads_relatorio_v1854` / `ads_relatorio_linha_v1854`; o mesmo
período importado de novo **substitui**. O lucro por anúncio (`ads_analise_payload`) vem do
`financeiro_payload` do período (cada item agora traz o `anuncio`): custo, tarifa, frete e imposto
de **todas** as vendas do anúncio. O Ads vem **só do relatório** — o PADS da fatura não entra,
senão o mesmo gasto contaria duas vezes. Margem nula quando falta custo, tarifa ou frete.
Uma recomendação por anúncio ("O que fazer", só no front): **rever preço/frete** (margem < 0 antes
do Ads), **reduzir verba** (lucro < 0), **pausar** (gastou R$ 5+ ou 20+ cliques sem nenhuma venda),
**pode escalar** (lucro, Ads < metade da margem, 5+ unidades — confira o estoque), **manter**,
**conferir custo** e **gasto pequeno sem venda** (ruído). Menos de 5 unidades = "poucas vendas".

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

**V18.91 — demanda pela Linx (construída sem resposta real; produção sem credenciais em 09/10):**
para os códigos dos fornecedores cadastrados (`_compras_codigos_dos_fornecedores`), o worker `linx_demanda_loop`
(21h–6h, 400 por rodada, retoma no dia) e o botão "Atualizar da Linx" chamam, na P02,
`OrdemCompra/Historico/{codigo}` (→ `linx_demanda_mes_v1891`) e `DetalhesPedidos` com `ApenasPendentes`
(→ `linx_compra_aberta_v1891`). Leitura tolerante (`_linx_ci`, `_linx_mes`); a **1ª resposta real** de cada
tipo fica em `linx_demanda_amostra_v1891` (`GET /api/compras/linx/amostra`, perfil Admin) — **conferir ao ligar
as credenciais** (corpo, código do item na URL, nomes de campo). O e-commerce é faturado no Linx (dono, 09/10):
a demanda da Linx já inclui ML + Shopee — **uma OU a outra, nunca somadas**. Na tela: coluna "Linx (total)" dos
últimos meses fechados (período ÷ 30), a média usa a Linx quando há histórico; "A caminho" vem da compra em
aberto, e o digitado continua mandando. Código sem histórico (412) ou com erro fica **nulo**: nunca vira zero por
cima da venda do e-commerce.

### Linx Smart API (levantamento 21/09/2026)
API oficial da Linx para o Linx DMS, na nuvem: gateway `https://auto-gwsmartapi.linx.com.br/<api>`,
chave em `Ocp-Apim-Subscription-Key` + token de `POST /api-seguranca/token`. Assinadas: Segurança,
Gerencial Peças, Faturamento, Peças Balcão Premium, Veículos CRM. Tudo em
`INTEGRACAO_LINX_SMART_API.md` — inclusive quais operações **gravam/movimentam estoque** (nunca testar).
`OrdemCompra/Historico` (demanda mensal balcão/oficina/telemarketing) e `DetalhesPedidos`
(compra em aberto) são o caminho para a Demanda de Compra definitiva.
**V18.85:** **F02 Financeiro** assinado em 08/10 (títulos, alçadas, consórcio — **não** faz cadastro,
crédito, romaneio, caixa nem nota; faturar no caixa não existe em nenhuma assinatura). Cada produto
tem a sua chave (`LINX_API_CHAVE_<S01|V01|P02|G03|F01|F02>`, vazia = a geral; a de um produto dá 401
em outro). Admin → Integrações → *Testar Linx* roda o diagnóstico só leitura (`LINX_DIAGNOSTICO`, lista
fechada; devolve contagens e nomes de campos, nunca valores). Mapa das etapas em
`INTEGRACAO_LINX_SMART_API.md` §4d.

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

Os testes ficam em `TESTES/` (são 30 arquivos; os principais):

| Arquivo | Cobre |
|---|---|
| `test_admin_soberano.py` | exclusão soberana, tombstone, KIT inteiro, reinclusão ADMIN, campos protegidos, pedido manual |
| `test_conferencia.py` | bip monotônico, metadados de conferência, conclusão canônica, cofre manual |
| `test_financeiro.py` | imposto de 12%, lucro bruto/líquido, tarifa por pedido (não por linha), venda sem custo não vira lucro, Ads sem duplicidade, escrow Shopee, frete só do vendedor |
| `test_ads_analise.py` | leitura do .xlsx do ML, planilha errada recusada, mesmo período substitui, lucro por anúncio sem Ads em dobro |
| `test_auditoria_v1856.py` | renovação do token ML serializada, backup sem `DB_LOCK` e sem `.gz` cortado, reinclusão sob lock, filtro de concluídos sempre aplicado, vínculo MLB manual preservado, `.xlsx` malicioso, worker protegido, limpeza de logs/caches |
| `test_boot.py` | sobe o `servidor_web.py` pelo `main()` como processo (EXP_WORKERS=0) e exige o `/api/health` com a versão do código; `VERSAO.txt` = `APP_VERSION` |
| `test_repeticao_v1878.py` | Full importado uma vez só (vigia e catch-up), reconciliação não repete linha já vinculada, log só quando muda, retenção ≤ 30 dias, boot não duplica o backup pré-deploy |
| `test_avisos_venda_v1893.py` | assinatura VAPID válida (RFC 6979), 1ª leitura não avisa a fila, venda repetida/antiga não avisa, carrinho = 1 venda, vigia do ML sem chamada a mais, um push por vez, renovar não pula venda, só módulo Admin, 404/410 remove o aparelho |
| `test_casas_ml_v1894.py` | conta ML por casa: Bauru não toca em Araçatuba, mesma conta nas duas casas recusada, token renovado por casa, OAuth grava na casa certa, webhook pela casa do seller, pedido de Bauru entra marcado, cada casa revê só os pedidos dela |
| `test_filial_v1895.py` | filial do usuário (Admin = todas), Bauru só recebe a fila/etiquetas/histórico/coleta de Bauru, gravação filtrada não apaga a outra filial nem vira tombstone ADMIN, concluir/travar pedido de outra filial = 403, Mover de filial resiste à importação, PDF com a conta do pedido |
| `test_venda_despachada_v1897.py` | venda com envio já despachado/entregue não entra, a da integração sai, a do ADMIN fica, vigia de etiquetas limpa |
| `test_vendas_conta_v1898.py` | vendas de Bauru em tabela própria, sincronizar uma conta não apaga a outra, Bauru fora do custo do Financeiro, Dashboard filtra Todas/Araçatuba/Bauru |
| `test_promo_bauru_v1899.py` | modo/limite por conta e regras iguais, cada anúncio renovado com a sua conta, limite do dia por conta, clique nas duas contas, buscar novos nas duas, convites de uma conta não apagam os da outra, mesma campanha nas duas, tela com filtro de conta |
| `test_tela_desatualizada_v1900.py` | excluir/salvar com a tela desatualizada não apaga pedido que chegou depois, campo que a integração mudou não vira override do ADMIN, aba de uma filial gravando com o cabeçalho de outra não apaga nem remarca a outra filial, operador com a filial trocada e a tela aberta, filial lida uma vez por página |
| `test_auditoria_v1900.py` | operador não lê token do agente Zebra/robô Linx, custos nem senha de pedido de acesso; Conferência, Estação e Precificador continuam lendo o que usam; segredo não é gravado pela rota comum; lista de chaves só do Admin; usuário excluído não volta no reinício e senha fraca do arquivo de migração não vale |
| `test_auditoria_cobrancas_v1901.py` | cada tipo de cobrança indevida, carência de 30 dias, R$ 2,00, frete do carrinho por envio, lançamento em um caso só, situação humana nunca desfeita, ignorado não volta, recuperado total/parcial (negado vira recuperado), detecção sem chamar o ML, permissões, linha do e-mail uma vez |
| `test_remessa_full_v1903.py` | cálculo (30 dias, kit conta 1, Apollo Araçatuba dividido, null nunca zero), uma remessa aberta, ajuste do ADMIN nunca desfeito, mínimo de 150, rodada descartada se agendaram durante a leitura, novos só com confirmação, não aceito por código, recebida automática (≥ 50%) e manual, aviso 1x/dia, linha do e-mail, rotas e permissões |
| `test_campanhas_linha_v1904.py` | campanhas lidas por anúncio e pela conta certa, pausa/interrupção sem limpeza, lucro da promoção e de cada campanha na mesma base, melhor só acima do lucro e da margem mínima, relâmpago sem Aceitar, sem custo = nulo, expirada/outra conta/removida fora, tela com a coluna nova e sem a lista de convites |
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
- Reinclusão pelo ADMIN (extensão **e** "Ler print", V18.86) grava por `appStorage.upsertPedidosAdmin`
  (`/api/admin-order-upsert`), que desfaz o tombstone do pedido. Pela rota comum (`/api/storage`) o
  tombstone continua valendo e o pedido some logo depois do "salvo" (`test_reinclusao_print_v1886.py`).
- A Central de Pedidos do Dia mostra também os já conferidos — ela **não** se comporta
  como Pedidos Pendentes.
