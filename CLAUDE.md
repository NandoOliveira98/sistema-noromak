# Sistema Web Noromak — guia para o Claude

Sistema interno de e-commerce da Noromak: entrada e controle de pedidos (Mercado Livre,
Shopee, manuais), expedição, separação, conferência por código de barras, etiquetas,
produtos/SKUs/EANs, kits, precificação, promoções, rentabilidade, estoque, Central de
Pendências e histórico.

Versão de referência deste documento: **V17.82 — Conclusão e Reinclusão Canônicas** (seções novas até a V20.41).

---

## 0. NUNCA PUBLICAR SEM AUTORIZAÇÃO

**Não execute `./publicar.sh`, `./voltar_versao.sh`, `git push producao`, nem nada que
reinicie o serviço em produção, por iniciativa própria. Nunca.**

O fluxo obrigatório é sempre:

1. Fazer a alteração.
2. Rodar **`./testar.sh`** e **mostrar o resultado real ao dono do sistema** — inclusive o
   que não foi possível testar.
   **V19.68:** junto do `testar.sh`, rodar a skill **`revisar-antes-de-subir`**
   (`.claude/skills/`): 4 revisores só de leitura sobre `producao/master..HEAD` — regras
   do ADMIN, concorrência, segurança e integrações. Mostrar os achados confirmados; o
   dono escolhe o que corrigir (nada é corrigido sozinho).
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
- **V20.46 — memória presa:** em 09/11 o processo tinha 410 MB (RSS + swap) com 54 min no ar,
  ~200 MB em 20 arenas do malloc (uma por thread, ~100 threads) que não voltavam ao sistema; na
  VPS de 1 GB (dividida com o NoromakWeb) tudo ia para o swap e o cadastro/romaneio da extensão
  passavam de 60 s (nginx cortava = "erro HTTP"). No import, `memoria_limitar_arenas()` pede
  `mallopt(M_ARENA_MAX, 2)` (antes de qualquer thread); o worker `memoria_loop` chama
  `malloc_trim(0)` a cada 10 min e mede antes/depois (`_MEMORIA_ESTADO`, item "Memória do Noromak"
  no Painel de saúde, atenção acima de 400 MB — o systemd tem `MemoryMax` de 500 MB). Fora do
  Linux não faz nada. Teste: `TESTES/test_memoria_v2046.py`. **revisar-antes-de-subir (09/11, "corrigir todos"):**
  falha passageira (leitura do `/proc`, exceção no trim) pula só aquela volta (antes o worker parava de vez); a duração
  vai sempre ao Painel e ao log quando ≥ 0,5 s (o trim segura as arenas: acima de ~1 s, rever). Junto, achados da
  V20.45: categoria sempre em **código** (`apollo_categorias_codigo`: "FILTROS"/"111 FILTROS" → "111") ao salvar o
  fornecedor, na demanda, no Puxar, nos filtros dos fornecedores e na rodada da noite (antes o mesmo filtro tinha duas
  chaves e o nome ia para a porta); de dia, sem o Puxar do gestor, a demanda da Linx de uma categoria pergunta no máximo
  `APOLLO_DEMANDA_DIA_MAX` (200) peças e o resto vai à noite (`demanda_adiada`); na tela, o "a caminho" antigo por
  fornecedor é copiado **uma vez** (`compras_caminho_migrado_v2046`) e o erro de um pedido antigo não cobre a demanda certa.
  Testes: `RevisaoV2046` em `TESTES/test_demanda_grupo_v2044.py`.
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
  catalogo-ml.html (Monitor de catálogo ML, V19.74)
                             (Central de Pendências e Promoções ML removidas na V17.99;
                             /pendencias e /promocoes-ml abrem o Admin. V19.76: as telas
                             Promoções ML (promocoes.html), Oportunidades de Kit e Pós-venda
                             foram apagadas a pedido do dono — /promocoes abre as Promoções
                             automáticas; as APIs e a rotina do Pós-venda continuam.
                             V20.01: posvenda.html voltou, com Araçatuba e Bauru)
  shared-storage.js          cache versionado + polling (CRÍTICO)
  nk-menu.js                 menu lateral único + endereço de cada tela (V18.82)
  nk-ui.js / nk-ui.css       tema claro/escuro, avisos, confirmação, tabela padrão (V18.83)
  saude.html                 Painel de saúde + alertas por e-mail (V18.83)
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
11. **V20.07:** `conf_pedidos` igual ao que já está gravado (`_fila_mesmo_conteudo`, sem `force`) não grava nem sobe a
    versão; o `commit` continua, para o delta do ADMIN e o cofre ficarem gravados. Teste: `TESTES/test_gravacao_sem_mudanca_v2007.py`.

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

**Perfil Consulta (V19.40, decisões do dono 16/10):** vê tudo menos Usuários e Segurança, sempre em
**todas** as filiais, e **não altera nada**. Gravado como `role='operador'` + `app_users.somente_leitura=1`
(o CHECK da tabela só aceita admin/operador; recriar a tabela de produção não valia o risco) —
`role_efetivo()` devolve `consulta` e é o `app_role` da sessão (`somente_leitura: true`). Módulos fixos
(`CONSULTA_PERMISSOES`: Conferência só para ver a fila, Admin, Precificador; sem Estação). A trava é no
servidor, antes de qualquer rota (`consulta_bloqueada` no `do_GET/do_POST/do_DELETE`): todo POST/DELETE
recusado com 403 `SOMENTE_LEITURA`, menos os que só leem (`CONSULTA_POST_LIVRES`); GET que conecta
integração ou mostra Segurança também (`CONSULTA_GET_BLOQUEADOS`). **Rota nova de POST que só lê e que o
Consulta precisa: incluir em `CONSULTA_POST_LIVRES`.** Na tela: selo "Somente leitura" e aviso
(`web-auth.js`); o `shared-storage.js` não trata a recusa como queda de rede e descarta a cópia local.

**Perfil Compras (V19.70, decisões do dono 21/10):** vê **só** o Quadro de tarefas (1º item do menu, em destaque),
a Ruptura (só ver) e a Demanda de Compra (pode digitar). Gravado como `role='operador'` + `somente_leitura=2` (a coluna
guarda o perfil especial: 1 = Consulta, 2 = Compras; `PERFIL_ESPECIAL_CODIGO`); `role_efetivo()` devolve `compras`,
permissões fixas `COMPRAS_PERMISSOES` (nenhum dos 4 módulos + `compras: true`). A trava é no servidor, antes de qualquer
rota (`consulta_bloqueada`): fora de `COMPRAS_API_LIVRES` toda chamada à API volta 403 `PERFIL_COMPRAS` (as rotas de
pedido só pedem login). Rotas da Ruptura/Demanda usam `self.auth(admin_only=True, compras_ok=True)`; as páginas `/ruptura`
e `/compras` abrem com o módulo `compras`; no menu o item tem `alt: "compras"`. Ao entrar, o portal manda o Compras para
`/tarefas`. Na Demanda o Compras só digita custo (e "a caminho", que fica no navegador): e-mail/limite do comprador,
fornecedores, marca, envio ao comprador e Linx ficam com Admin/Supervisor (`COMPRAS_POST_SO_GESTOR`, 403).
**Rota nova da Demanda/Ruptura que o Compras usa: `compras_ok=True` e prefixo em `COMPRAS_API_LIVRES`.**
Teste: `TESTES/test_perfil_compras_v1970.py`.

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

### TikTok Shop · Bauru (V19.63, fase 0)
Pedido do dono (20/10): conta do TikTok Shop em **Bauru** com **controle de estoque** pelo Apollo de Bauru. A Open API
atende o Brasil (Bling/Olist já integram; o spec traz pt-BR e etiqueta ZPL/INVOICE_LABEL só BR). Fases: 0 conexão;
1 estoque Apollo de Bauru → TikTok (`POST /product/202309/products/{id}/inventory/update`, por SKU e **por depósito**;
Simulação → Lote → Automático; `disponivel: null` nunca vira zero; **quebra a regra "Noromak nunca grava estoque" só
para o TikTok — ainda a confirmar com o dono**); 2 pedidos na fila de Bauru; 3 etiqueta; 4 vendas/Financeiro.
**Fase 0:** `tiktok_connect_url` → `services.tiktokshop.com/open/authorize?service_id=&state=`; `tiktok_exchange_code`
→ `GET auth.tiktok-shops.com/api/v2/token/get` (`grant_type=authorized_code`; `*_expire_in` = data epoch);
`tiktok_access_token` renova (`/api/v2/token/refresh`) sob `TIKTOK_TOKEN_LOCK`, relendo a linha. Chamadas em
`open-api.tiktokglobalshop.com` (`tiktok_api`): `app_key` + `timestamp` + `sign` na URL, token em `x-tts-access-token`,
`shop_cipher` (de `/authorization/202309/shops`, gravado na conexão) nas rotas da loja. `tiktok_assinar`: caminho +
parâmetros em ordem (sem `sign`/`access_token`) + corpo, embrulhado no secret, HMAC-SHA256. Tabelas
`tiktok_integration` (id=1, `casa`='bauru'), `tiktok_oauth_state`, `tiktok_event_log` (30 dias), `tiktok_amostra_v1963`
(**feita pela documentação, sem resposta real — conferir a 1ª resposta ao conectar**). Rotas
`/api/integracoes/tiktok/{callback,status,connect,teste,amostra,logs,disconnect}`; cartão "TikTok Shop · Bauru" em
Integrações (Testar leitura = loja + depósitos). `.env`: `TIKTOK_APP_KEY/SECRET/SERVICE_ID`; o callback precisa estar
cadastrado como Redirect URL no app. Teste: `TESTES/test_tiktok_v1963.py`.
**V19.64 — fase 1 (decisões do dono, 20/10):** o Noromak **grava estoque só no TikTok** (exceção à regra; Simulação
padrão → Lote por clique → Automático, ligar = OWNER). Quantidade = **todo** o disponível de Bauru (revenda 3) no Apollo −
unidades das linhas da fila com `Casa=bauru` (TikTok + ML de Bauru + manuais: vendido e ainda não baixado no Apollo); kit =
peça que tem menos (`_tiktok_pecas`: "A/B", "KITn-X" = n×X, prefixo "151490-"). Apollo não lido/sem resposta = **não mexe**,
nunca zero. Vínculo pelo `seller_sku`; o vínculo à mão (`tiktok_vinculo_v1964`) vence. Anúncios criados no Seller Center
(nada é criado pelo Noromak); `tiktok_produtos_sincronizar` lê `/product/202502/products/search` de hora em hora
(`tiktok_sku_v1964`; variação com mais de um depósito não é enviada). "Tempo real sem pesar": worker `tiktok_estoque_loop`
(20 s) lê o Apollo em rodízio (`tiktok_apollo_ler_proximo`, ≤ `TIKTOK_APOLLO_POR_MINUTO`=3/min; nunca lido > estoque ≤ 3
lido há 10 min > mais antigo; `tiktok_apollo_v1964`) e refaz o estoque quando a versão da fila muda (venda do ML de Bauru
baixa na hora, sem Apollo). Envio: `POST /product/202309/products/{id}/inventory/update`, só o que mudou, erro por SKU não
marca como enviado; histórico em `tiktok_estoque_log_v1964`. **Pedidos:** `tiktok_pedidos_loop` (120 s), status
`AWAITING_SHIPMENT`/`AWAITING_COLLECTION`, importação **começa ligada** (`tiktok_config_v1964`); cada unidade é um
`line_item` → agrupa por SKU; linha `Marketplace=TIKTOK`, `Casa=bauru`, `OrigemCadastro=API_TIKTOK`, uid `TIKTOKAPI_<sha1>`;
mesma regra da Magalu (`_magalu_merge(..., origem=, campos=)`: outra origem não duplica, enviado sai pela remoção nomeada).
Tela `APP/tiktok-estoque.html` (`/tiktok-estoque`, menu Estoque e compras, módulo admin; mudar = perfil Admin). Rotas
`GET /api/tiktok-estoque`, `POST /api/tiktok-estoque/{config,vinculo,enviar,atualizar}`,
`POST /api/integracoes/tiktok/{importacao,sincronizar}`. Etiqueta = fase 3. Teste: `TESTES/test_tiktok_estoque_v1964.py`.
**V19.81 — estoque pela extensão do Seller Center** (a Open API exige gerente de contas; decisão do dono 22/10). Extensão
`NOROMAK_TIKTOK_ESTOQUE` (Edge, fora do repo, v0.2) usa as chamadas internas da página com a sessão do vendedor:
`POST /api/v1/product/stock/sku/list` (lista por SKU, `in_shop_stock`), `/stock/query/sku` (relê antes de gravar) e
`/sku/price/stocks/update` — grava **por diferença** (`quantity_variation`), por isso relê o atual na hora. Rotas
`POST /api/tiktok-estoque/extensao/{produtos,iniciar,resultado}` e `GET .../extensao/pendentes` (perfil Admin).
**Uma rodada de envio por vez** (`tiktok_extensao_iniciar`, `TIKTOK_RODADA`, 409; vence em 15 min; o `resultado` com
`fim` fecha): duas rodadas juntas somariam a diferença duas vezes. Iniciar em Simulação = 400. OK só com a quantidade
final confirmada e igual à pedida (senão ERRO; o final informado vira `qtd_tiktok`). `tiktok_produtos_da_extensao`:
só apaga quem não veio com `completo`; lista vazia recusada; leitura "completa" com < 70% do que existe não apaga.
O "Enviar" relê a loja antes. Extensão v0.3 roda só em content script isolado (sem postMessage com a página; os
parâmetros da sessão vêm do `performance`). Sem a API conectada o `tiktok_estoque_loop` continua o rodízio do Apollo
só se a extensão leu a loja nas últimas 6 h (`extensao_lida_epoch`). Produto inativo: o `status` do Seller Center é
guardado mas ainda não filtra (falta amostra de inativo).
**V19.84 — preço ideal do TikTok** (decisões do dono 23/10): `GET /api/tiktok-estoque/preco?sku=&margens=15,20&preco=&custo=`
(módulo admin, só lê; extensão v0.6, quadro "Preço ideal"). Afiliado fixo **5% em toda venda**, loja inteira no
**frete grátis** (6%, teto R$ 50), imposto = `FINANCEIRO_IMPOSTO_PERCENT`, tarifa `TIKTOK_FAIXAS_TARIFA` (< R$ 50 =
10% + R$ 4; ≥ R$ 50 = 6% + R$ 6; mudou no TikTok, trocar aqui). Afiliado/frete editáveis em `tiktok_config_v1964`
(`preco_afiliado_pct`, `preco_frete_pct`). Custo = Apollo de **Bauru** (revenda 3) lido agora, senão a última leitura
de Bauru (`custo_apollo_v1931`); **nunca** outra filial; kit soma as peças; custo digitado vence. Margem = lucro ÷ preço.
Teste: `TESTES/test_tiktok_preco_v1984.py` (exemplos conferidos com o dono: 2011004 → 65,71 / 72,16). **Falta:** pedidos do TikTok sem API não entram na fila (o vendido no TikTok só sai quando o Apollo baixar).
Teste: `TESTES/test_tiktok_extensao_v1981.py`.
**V20.08 — SKU cortado** (dono, 30/10): o SKU do vendedor chega no máximo com 50 caracteres (`TIKTOK_SKU_LIMITE`; 4 kits vieram com
o último código pela metade, "…/201104"). SKU no limite sem vínculo à mão vira `sku_cortado`: não envia e não gasta a vaga do Apollo
(`_tiktok_sku_cortado`), mesmo que o trecho exista como código, porque o fim pode ter perdido uma peça inteira. A tela (aba Sem
vínculo) oferece botões "Usar <código>" com os códigos do Precificador que começam com o trecho (`tiktok_sugerir_sku`, só no
payload da tela; até 6, nunca o próprio trecho, nunca grava sozinho; o clique confirma mostrando as peças). Teste: `TESTES/test_tiktok_sku_cortado_v2008.py`.
**V20.29 — etiqueta, cancelamento, vendas e acerto** (dono, 06/11; desenho em
`docs/superpowers/specs/2026-11-06-tiktok-amazon-design.md`). A API vai ser liberada pelo **cadastro de parceiro (ISV)** no
TikTok Partner Center, como o Bling: o código já usa esse login, basta trocar as chaves no `.env`. Construído pela documentação
de terceiros: a 1ª resposta de cada chamada nova fica em `tiktok_amostra_v1963` — **conferir ao conectar**. **Etiqueta** (chave
`etiqueta` em `tiktok_config_v1964`, **começa desligada**: é a única ação que grava no TikTok; ligar = perfil Admin,
`POST /api/integracoes/tiktok/etiqueta`): na importação, pacote de pedido `AWAITING_SHIPMENT` → `POST /fulfillment/202309/packages/{id}/ship`,
depois `GET .../shipping_documents` (SHIPPING_LABEL, A6) só para conferir que existe; registro em `conf_etiquetas` com
`marketplace: "tiktok"` e `envio: "TIKTOK_PDF:<pacote>"` (o PDF **não** vai para o banco). Colunas `PacoteTikTok` e `Etiqueta API TikTok`
(VINCULADA / AGUARDANDO / AGUARDANDO NF / ERRO). Recusa que cita nota/invoice = AGUARDANDO NF (a nota sai pelo Linx; o Noromak nunca
envia); pacote que falhou só é tentado de novo depois de `TIKTOK_ETIQUETA_REPETIR_S` (600 s). Pedido com `cancel_reason`/
`cancellation_initiator`/`is_on_hold` não tem envio organizado. **PDF:** `GET /api/etiqueta-tiktok-pdf?pedido=` (mesmo bloco e regras da
rota do ML de Bauru) pede o link ao TikTok **a cada impressão** e só baixa `https` de `TIKTOK_PDF_HOSTS`; a Conferência imprime todo
pedido TikTok em PDF, em qualquer filial. **Cancelado** sai pela remoção nomeada do `_magalu_merge` (a linha marcada como manual do ADMIN
fica; concluído não volta). **Vendas** (`tiktok_vendas_loop`, 10 min, só conectado): `orders/search` por data de criação, 3 dias sempre e
30 dias 1x/dia, sem UNPAID/CANCELLED, em `vendas_conta_v1898` (`conta='bauru'`, `marketplace='tiktok'`); `vendas_v1799`, o Financeiro e
`VENDAS_MARKETPLACES` não mudam — o Dashboard usa `VENDAS_MARKETPLACES_DASHBOARD` (canal e cor TikTok). **Acerto** (`tiktok_acerto_v2029`):
vendas de 3 a 60 dias, 20 por volta, `/finance/202501/orders/{id}/statement_transactions` (404 = 202309); receita, tarifa, frete e líquido
somados das transações, campo ausente = NULL; erro espera 6 h. **Revisão final (06/11):** o envio só é organizado para pedido que fica na
fila (`_tiktok_pedidos_que_ficam_na_fila`: tombstone, linha excluída e concluído do ADMIN valem antes da escrita no TikTok); `/ship` nunca
duas vezes no mesmo pacote (`_TIKTOK_ETIQUETA_ORGANIZADO`); acerto sem transação não conta como lido (relê); amostra uma por rota
(id do pacote/pedido vira `{id}`). **revisar-antes-de-subir (06/11):** as barreiras do ADMIN rodam sob o `DB_LOCK` e
também depois de abrir o kit (linha `KITDB_` excluída); falha ao lê-las = nenhuma escrita no TikTok naquela volta (a importação
segue); "Sincronizar agora" só inicia em segundo plano (`tiktok_sincronizar_iniciar`, 409 se já roda); a recusa vai para a amostra
`<rota> [erro]` e a 1ª resposta boa fica na `<rota>`; link do documento fora de `TIKTOK_PDF_HOSTS` já dá ERRO ao vincular (com o
host no log). Testes: `TESTES/test_tiktok_etiqueta_v2029.py`, `TESTES/test_tiktok_vendas_v2029.py`.
**V20.30 — pedidos pela extensão do Seller Center** (dono, 06/11: o TikTok confirmou que a API só sai com gerente de contas, e
cadastro ISV não vale para conta que já vende; "criar o nosso caminho"). Mapa das chamadas internas em
`docs/superpowers/specs/2026-11-06-tiktok-seller-center-mapa.md`. Extensão `NOROMAK_TIKTOK_ESTOQUE` **0.7**: alarme de 2 min lê a aba
"Para enviar" (`POST /api/fulfillment/order/list`, `search_tab 101`) e abre até 10 detalhes por volta (`/api/fulfillment/order/get`):
primeiro os da fila que sumiram da aba, depois os novos (nome e CPF da nota). Rotas `GET /api/tiktok-estoque/extensao/pedidos-estado` e
`POST /api/tiktok-estoque/extensao/pedidos` (perfil Admin; importação desligada ou outra rodada = 409). `tiktok_sc_pedido` traduz para o
formato da Open API (`sku_display_status` = códigos da Open API, conferido: 121 em pedido enviado; quantidade pelas `order_line_ids` não
canceladas) e reaproveita `_tiktok_linhas_do_pedido` (mesmo uid `TIKTOKAPI_`, origem `API_TIKTOK`) e o `_magalu_merge`. **Sai da fila só
com o TikTok confirmando** (detalhe IN_TRANSIT/DELIVERED/COMPLETED/CANCELLED); sumiu da aba sem confirmar, lista vazia ou situação
desconhecida = fica como está. Nome guardado em `tiktok_sc_pedido_v2030` (60 dias); CPF/nome da nota vão para `cliente_fiscal`
(fonte `tiktok_sc`) só se o pedido ainda não tem nada lá. Painel de saúde `tiktok_pedidos`: Crítico (e-mail) sem leitura completa há 30 min
entre 08:30 e 18:00 de dia útil, fora disso atenção; nunca lido = info. **Etiqueta continua pelo Seller Center** (a extensão não organiza
envio). **revisar-antes-de-subir (06/11, "corrigir todos"):** `TikTokAtualizadoEm` só anda com mudança real (a fila não é regravada a
cada 2 min); quantidade = unidades ainda a enviar (cancelada e já enviada fora) e muda na linha existente, nunca abaixo do bipado (fica o
bipado + `AvisoTikTok`); SKU sem unidade a enviar e cópia sem bip de pedido que o ADMIN incluiu por outra origem saem pela remoção nomeada;
pacote = o que tem unidade aberta; amostra sem comprador (`_tiktok_sc_sem_comprador`); detalhe reaberto até ter CPF (`cpf_ok`, até
`TIKTOK_SC_TENTATIVAS_CPF`=5); leitura anterior à saída confirmada (`saiu_epoch` × `lido_em`/`agora` da extensão) não reabre o pedido.
Extensão 0.7.1: vez própria para pedidos (`ocupadoPedidos`), rodízio dos detalhes (`detalheAberto`), até 500 pedidos, `completo` só com
`total_count`. **2ª revisão (só das correções):** aviso vai na `Observação` com o prefixo "TikTok: " (a Conferência mostra ⚠; a
Observação do operador nunca é trocada) e some quando resolve; Quantidade/Cliente com override do ADMIN não mudam (e não regravam a fila);
variação fora pelo uid exato (sku_id + SKU; peça de kit pelo `SkuKitOrigem`); linha fora **já bipada** fica com o aviso em vez de sair;
`saiu_epoch` lido e gravado dentro do `TIKTOK_SYNC_LOCK`; a extensão alterna "sumiram da aba" e "novos" nas 10 vagas.
Teste: `TESTES/test_tiktok_pedidos_extensao_v2030.py`.
**revisar-antes-de-subir da V20.30 + V20.31 (06/11, "corrija tudo"):** código de situação fora de `TIKTOK_SC_STATUS` ou
quantidade que não veio = pedido fica como está (nunca zero) e o Painel de saúde fica em atenção com os códigos
(`pedidos_desconhecidos` em `tiktok_config_v1964`; leitura completa sem eles limpa); pedido cancelado/enviado **inteiro** com peça
bipada fica com o aviso; Cliente: nome guardado > nome da nota > o que a linha já tem > apelido; nome/CPF da nota com
`salvar_cliente_fiscal(..., so_se_vazio=True)` (confere e grava na mesma vez); leitura, conta e gravação da fila num `DB_LOCK` só
(`_tiktok_sc_montar_fila`); amostra mascara dado pessoal em qualquer nível (`_tiktok_sc_mascarar`); a API (`tiktok_rodada`) não importa
pedidos enquanto a extensão leu nas últimas 6 h (`TIKTOK_SC_FONTE_UNICA_S`: uma fonte de cada vez). Teste: `TESTES/test_revisao_subida_v2031.py`.
**V20.33 — atendimento do TikTok no Apollo** (dono, 06/11: "a mesma rotina do ML: criar o cadastro do cliente e em
seguida o atendimento"). O TikTok não tem integração com o Apollo; o worker `tiktok_atendimento_loop` (2 min, 2 pedidos por volta)
faz o papel dela para os pedidos do TikTok na fila: `exp_cadastro_atualizar(..., criar=True)` com o endereço do detalhe (bairro
incluso) e depois `POST atendimento/criar` (contrato em `PEDIDO_PORTA_CRIAR_ATENDIMENTO.md`) na revenda de Bauru, origem `TIKTOK`,
`pedido_externo` `TIKTOK-<pedido>` (a porta nunca cria dois: `ja_existia`, por isso o incerto é repetido em 10 min), condição 973
(a porta ainda não aplica: o painel/extensão acerta antes do caixa). Interruptor `atendimento_auto` em `tiktok_config_v1964`
(**começa desligado**; `POST /api/integracoes/tiktok/atendimento`, perfil Admin; `GET` = estado; linha no cartão TikTok de Integrações).
Preço: o detalhe do Seller Center (`_tiktok_sc_venda`, guardado em `tiktok_sc_pedido_v2030.venda_json` com `endereco_json`):
`sku_unit_price` × unidades a enviar, com o desconto da LOJA rateado (o subsídio do TikTok não entra); a soma das unidades tem de
bater com `main_order_origin_sale_price` (ou com ele menos o desconto), senão `revisar`. Kit = `_anuncio_sku_partes` (Cadastro de
Kits/KITn-X/"A/B"), preço do kit dividido pelo **custo de Araçatuba** das peças (`_tiktok_atendimento_dividir`, centavo que sobra numa
peça de 1 unidade); peça sem custo ou SKU com cara de dois códigos sem Cadastro de Kits ("KIT-2050171-2011058") = `revisar`, nada criado.
Estado em `tiktok_atendimento_v2033` (criado/ja_existia/erro/incerto/revisar + próxima tentativa); cadastro feito vai para o
`cadastro_auto_v1941` (o painel pula a etapa). `_exp_valor_pedido` do TikTok = o valor enviado (senão o do detalhe).
**Revenda por filial** (dono, 06/11): `_exp_revenda`/`_exp_revenda_das_linhas` — pedido de Bauru (TikTok **e** ML de Bauru) usa a
revenda 3 no cadastro, busca de atendimento, romaneio, reemissão e caixa; o resto continua na 1. Extensão Linx Cliente 0.51 mostra a
situação do atendimento automático no painel. Teste: `TESTES/test_tiktok_atendimento_v2033.py`.
**revisar-antes-de-subir da V20.33 (06/11, "corrigir todos"):** ao ligar, o que já está na fila fica `anterior` (atendido à mão:
o atendimento manual não tem a chave `TIKTOK-`, a porta criaria outro); pedido com romaneio/caixa registrado também vira `anterior`;
só entra pedido que o TikTok diz "a enviar" (cancelado/enviado com peça bipada fica de fora). Logo antes de criar, relê a fila e
**confere as peças da fila** (com as exclusões/alterações do ADMIN) contra as do TikTok (`_tiktok_atendimento_conferir_fila`):
diferente = `revisar`. Vínculo à mão (`tiktok_vinculo_v1964`) vence o SKU; SKU cortado (V20.08) sem vínculo = `revisar`. Desconto da
loja negativo, ou desconto com unidade cancelada (não dá para saber de qual produto era) = `revisar`. `sem_credito` = `revisar`
("libere pelo painel"). Um pedido por volta (limite de escritas da porta). Pedido que saiu da fila sem atendimento fica
`fora_da_fila` no cartão. `_exp_valor_pedido` do TikTok = a venda do detalhe de AGORA (o enviado só vale até 1 centavo por unidade):
cancelamento depois do atendimento aparece como valor diferente. **Revenda pela conta** que vendeu (`_exp_revenda_das_linhas`: ML =
`ContaMercadoLivre`, TikTok = 3, Shopee/Magalu = 1, manual = filial), não pela filial que confere (Mover de filial); o caixa usa a
revenda gravada no romaneio; `_exp_revenda` lê a fila guardada (`_ml_fila_leitura`). Romaneio/caixa: o clique espera a porta no máximo
`EXP_CLIQUE_ESPERA_S` (40 s, "Apollo ocupado") e confere de novo, já na vez, que ninguém soltou/enviou. Estoque: `_anuncio_pedidos_com_atendimento`
inclui os atendimentos do TikTok (os anúncios de Bauru têm a trava de queda). **V20.37 — criar pelo painel** (dono, 07/11, pedido
587022850875164046: automático desligado e o painel sem como criar): `POST /api/expedicao-apollo/tiktok-atendimento {pedido}` (módulo
admin, recusa outra filial) → `tiktok_atendimento_manual_iniciar`: só pedido do TikTok (`tiktok_sc_pedido_v2030`) ainda sem
atendimento criado; grava `criando` e roda `tiktok_atendimento_pedido` numa thread (um por vez, `_TIKTOK_ATEND_MANUAL_LOCK`; vale
também para o `anterior` da hora de ligar); "aguardando" sem fazer nada vira `revisar` com o motivo. Extensão Linx Cliente **0.52**:
faixa "Este pedido do TikTok ainda não tem atendimento no Apollo" com o botão "Criar atendimento no Apollo" (confirma, acompanha 2 min)
e o crédito pelo Linx web preenche a **data de nascimento vazia** (aleatória 1970–2000, regra do robô do Apollo; Erro 2544 do Linx).
Teste: classe `BotaoDoPainel` em `TESTES/test_tiktok_atendimento_v2033.py`. Extensão **0.52.1**: a rota nova entrou na lista fechada de rotas
do `noromak-api` do service worker ("Rota não permitida"). **V20.38:** o pedido lido antes da V20.33 tinha o CPF e **não o preço**
(`venda_json`): "Não criado: sem o preço do detalhe". `tiktok_sc_estado` tira do `com_detalhe` o pedido "a enviar" sem preço lido
antes de `TIKTOK_SC_PRECO_DESDE` (06/11 22:56 UTC), e a extensão reabre o detalhe (o lido depois sem preço não volta a cada 2 min). **V20.39 — subsídio do TikTok** (1º pedido real): o Seller Center manda o `sku_unit_price` já SEM os dois descontos
(119,90 − 45,10 da loja − 5,05 do TikTok, `platform_discount_total` = 69,75). `_tiktok_sc_venda` reconhece esse 3º formato e volta ao
valor com o desconto da loja (74,80: o subsídio o TikTok repassa); unidade cancelada com desconto do TikTok = erro, como a da loja. O
erro do preço guardado leva `regra` (`TIKTOK_SC_VENDA_REGRA` = 2039): erro de regra mais velha faz o detalhe ser reaberto uma vez. **2ª revisão:** o estoque do TikTok continua descontando
todo pendente (sem a trava de queda, subiria antes de o Apollo reler); "já atendido" = qualquer romaneio/caixa registrado com número
de atendimento (inclusive erro/incerto); a conferência usa o SKU atual da linha (o ADMIN que troca o SKU deixa o do TikTok em
`SkuOrigemImportacao`) e todas as linhas do pedido, de qualquer origem; `fora_da_fila` só marca com a data de ligar, lê fila e tabela
na mesma vez e quem volta à fila volta a ser candidato; `_tiktok_venda_mudou` (atendimento criado e o pedido cancelado/saiu ou com
preço ilegível) barra o caixa (`_exp_caixa_valor_diferente`) e o romaneio do painel; o "Atualizar cadastro" do botão também espera a
porta no máximo 40 s (`_ExpVezDaPorta`; as rotinas esperam o quanto for); o lote do Faturamento tenta de novo o "ocupado".

**V20.40 — pronto para a API** (dono, 07/11: app "Noromak" criado no Partner Center como **Desenvolvedor de aplicativos →
ERP, serviço personalizado**, sem gerente de contas; chaves no `.env`; falta o TikTok aprovar e Publicar). **Uma fonte de cada vez**
(`tiktok_fonte()`, chave `fonte` em `tiktok_config_v1964`: `extensao` padrão | `api`; `POST /api/integracoes/tiktok/fonte`, perfil
Admin; virar para a API só conectado e com a loja lida; botão "Usar a API" no cartão). Com `api`: o `tiktok_estoque_loop` lê os anúncios
e grava o estoque pela API, o `tiktok_rodada` importa os pedidos, e a extensão é **recusada** (`TikTokFonteApi` → 409 em
`extensao/{produtos,pedidos,iniciar}`; o `resultado` de uma rodada aberta ainda fecha) — o estoque da extensão grava por diferença. Com
`extensao`: Sincronizar, `tiktok-estoque/{enviar,atualizar}` = 409 (antes valia "a extensão leu nas últimas 6 h"). **Atendimento pela
API:** `tiktok_importar_pedidos` grava em `tiktok_sc_pedido_v2030` a situação, o preço (`_tiktok_api_venda`: um line_item por unidade,
`original_price − seller_discount`, subsídio fora, cancelada fora, preço ausente = erro), o endereço (`_tiktok_api_endereco`:
`address_line1/2/3`, `district_info` L1 = UF, L2 = cidade, L3 = bairro) e o nome/CPF da nota (`cpf`/`cpf_name`, `so_se_vazio`);
pedido que sumiu da lista "a enviar" é relido (`GET /order/202309/orders?ids=`, até 50) e só sai com a situação confirmada. Formato pela
documentação — **conferir a amostra no 1º pedido real**. Painel de saúde segue a fonte (`pedidos_api_epoch`). **Plataforma de anúncios:**
cada variação de `tiktok_sku_v1964` (API ou extensão) vira linha `canal='tiktok'`, `conta='bauru'`, `anuncio_id` = id da variação,
ficha pelo SKU do vendedor (vínculo à mão vence), `controle='tiktok'` (o estoque é da tela Estoque TikTok; "Passar para o Noromak" recusa),
`user_product_id` = product_id (`_tiktok_anuncios_espelhar`, também no boot); removido pelo ADMIN não volta. Teste:
`TESTES/test_tiktok_fonte_api_v2040.py`.
**revisar-antes-de-subir da V20.40 (07/11, "corrigir todos"):** a importação pela API monta a fila pela **mesma regra da
extensão** (`_tiktok_api_para_sc` → `_tiktok_sc_montar_fila`): pedido que sumiu da lista (ou lista vazia) só sai com o TikTok
confirmando na releitura (`_tiktok_api_reler`, até 50 por volta, o relido há mais tempo primeiro); bipado fica com aviso;
quantidade nunca abaixo do bipado; `TikTokAtualizadoEm` só com mudança real; `PARTIALLY_SHIPPING` entra na busca (só as unidades
a enviar). Amostra da API passa por `_tiktok_sc_mascarar`. Virar para a API volta o estoque a **Simulação** e é recusado com
rodada da extensão aberta (`TIKTOK_RODADA`, 409); `tiktok_estoque_enviar` também espera a rodada fechar e o clique espera no máximo
20 s (`TikTokEstoqueOcupado`, 409). Catálogo pela API com as travas da extensão (vazio/cortado/< 70% não apaga). Endereço só com
`address_line2` numérico (senão o atendimento espera); níveis pelo `address_level_name` (state/city/district), senão L1/L2/L3.
Erro de preço não apaga o preço bom já lido; CPF ausente vai para o log. Com a API, `pedidos-estado` diz `importacao: false` e
`pendentes` volta vazio (a extensão nem lê); "Atualizar anúncios" em 2º plano (`tiktok_catalogo_iniciar`, `TIKTOK_CATALOGO_LOCK`,
também no worker); espelho só grava o que mudou e o estoque enviado aparece na hora (`_tiktok_anuncio_estoque`); o aviso de
situação desconhecida do Painel de saúde é só da extensão.
2ª revisão (só das correções): pacote com unidade a enviar vem primeiro (`_tiktok_api_pacote_aberto_primeiro`, pelo
`package_id` dos line_items; coluna e etiqueta do parcialmente enviado nunca são do pacote já despachado); o preço pela API
também tira as unidades já enviadas (senão o atendimento ia sempre a revisar); trocar a fonte espera no máximo 20 s pela trava.
**V20.43 — peças do atendimento comprometidas** (dono, 09/11: "o atendimento balcão precisa cair comprometido"). O
`atendimento/criar` só abre o atendimento; a porta nova do Guilherme `atendimento/comprometer {revenda, operador, contato}`
(idempotente; `ja_comprometido` sem chamar o Apollo; cada item com `comprometido`/`motivo`; `?ensaio=1`) é chamada logo depois
do criar (`tiktok_atendimento_comprometer`) e, se não comprometeu tudo, de novo **só ela** nas voltas seguintes
(`_tiktok_comp_candidatos`: pedido na fila, sem romaneio/caixa, até `TIKTOK_COMP_MAX` = 5, espera por situação em
`TIKTOK_COMP_ESPERA`), na mesma vaga de um pedido por volta. Colunas `comprometido` (sim/parcial/nao/erro/incerto/comprometendo),
`comp_detalhe`, `comp_itens_json`, `comp_tentativas`, `comp_proxima` em `tiktok_atendimento_v2033`. "sim" só com prova (todos os
itens `comprometido: true` ou `ja_comprometido`); falha no comprometer nunca desfaz o atendimento. `_anuncio_pedidos_com_atendimento`
só conta o TikTok com `comprometido='sim'` (sem isso o Apollo não reserva: desconta). O botão do painel (`POST
/api/expedicao-apollo/tiktok-atendimento`) com o atendimento já criado e sem as peças comprometidas só compromete; extensão Linx
Cliente **0.53**: faixa "Peças do atendimento N não comprometidas" com o botão "Comprometer peças". Teste:
`TESTES/test_tiktok_comprometer_v2043.py`. **revisar-antes-de-subir (09/11, "corrigir todos"):** revenda sempre a de Bauru
(`SEPARACAO_REVENDA[TIKTOK_CASA]`, não pela fila: pedido fora dela caía em Araçatuba); rotina e botão com as mesmas barreiras
(`_tiktok_comp_pode`: pedido na fila, TikTok ainda "a enviar" — venda cancelada não reserva —, sem romaneio/caixa, venda não
mudou); 429/409 não gastam tentativa; o comprometer roda **mesmo com o automático desligado** (só mexe em atendimento que já
existe; o criar continua desligado); "comprometendo" que sobrou de um reinício vira `erro` no boot (volta a ser tentado e o botão
reaparece), e sem atendimento não fica preso; item `comprometido` aceita `true` ou `"S"`/`"SIM"`/`1` (o Linx usa "S"); a amostra
guarda só os campos do contrato. 2ª revisão (só das correções): sem atendimento a comprometer a rotina não lê a fila; as barreiras são
conferidas de novo já na vez da porta (`EXP_PORTA_LOCK`: o romaneio pode ter saído enquanto esperava); 429/409 seguidos contam 1
tentativa a cada `TIKTOK_COMP_PASSAGEIROS` (10, `comp_passageiros`). Limite: romaneio solto direto no Linx (fora do painel) não fica em
`linx_operacoes_v1860`, então a barreira não o vê.

**V20.41 — TikTok igual aos outros na plataforma de anúncios** (dono, 07/11; decisões: estoque "plataforma, por anúncio",
preço digitado + Apollo de Bauru, promoções do TikTok depois). **Estoque:** conta de estoque `tiktok` (`ESTOQUE_CONTA_TIKTOK` em
`ESTOQUE_CONTAS_FORA`; `_estoque_alvo` = canal 'tiktok', conta e filial Bauru): com a fonte `api` os anúncios do TikTok ficam com
`controle='noromak'` (`_tiktok_controle_atual`, refeito pelo espelho e na troca de fonte) e ganham Manual/Automático em /anuncios
(Apollo de Bauru − pendentes de Bauru; interruptor próprio "TikTok · Bauru", Automático com OWNER); com a extensão o controle volta a
'tiktok' e o Automático vira Manual. Grava só por `tiktok_estoque_gravar` → `_tiktok_estoque_gravar_api` (lê o produto, SKU da ficha
ou vínculo à mão, um depósito, `inventory/update` absoluto, relê; só com a fonte api). O `tiktok_estoque_loop` com a API só lê o
catálogo; `/api/tiktok-estoque/enviar` com a API = 409 (é por anúncio). **Criar anúncio no TikTok** (painel na aba Anúncios em todos os
canais, Bauru): rascunho `anuncio_rascunho_tiktok_v2041`, categorias/sugestão/atributos/regras/marcas/depósito de venda (cache 24 h),
"Conferir no TikTok" = `products/listing_check` (sem fotos; falhou = conferência local), Publicar em 2º plano só com a fonte api
(`TikTokFonteNaoApi` 409), fotos por `images/upload` (multipart, assinatura sem o corpo), `POST /product/202309/products`; estoque
inicial ≤ Apollo de Bauru − pendentes (sem leitura = não publica); preço digitado com o preço ideal do TikTok como sugestão
(`tiktok_conta_preco`); sem resposta/erro interno = `incerto` (Liberar confere o seller_sku = código da ficha); já anunciado não publica
outro; publicado = linha `controle='noromak'`, Manual, + `tiktok_sku_v1964`. **Trazer dados do anúncio** (TikTok): título, descrição,
marca (origem `anuncio_tiktok`, abaixo de `anuncio_ml`) e fotos só com a ficha sem foto (`TIKTOK_FOTO_HOSTS`). Tudo pela documentação:
1ª resposta de cada chamada em `anuncio_amostra_v2024` (`tiktok_*`) — **conferir no 1º uso**. Rotas
`GET /api/anuncios/tiktok/{categorias,atributos,conta,rascunhos}`, `POST /api/anuncios/tiktok/{rascunho,conferir,publicar,liberar}`.
Testes: `TESTES/test_tiktok_anuncios_estoque_v2041.py`, `TESTES/test_anuncios_tiktok_v2041.py`.
**revisar-antes-de-subir da V20.41 (07/11):** SKU do TikTok cortado (V20.08) sem vínculo à mão não recebe estoque pela plataforma
(`_anuncio_estoque_elegivel`); o envio pela API (`_tiktok_estoque_gravar_api`) roda sob o `TIKTOK_ESTOQUE_LOCK` (a troca de fonte espera);
o Publicar já reservado confere a fonte de novo antes de criar; amostra uma por rota também em `/products/{id}` e `/categories/{id}`;
marcas até `TIKTOK_MARCAS_PAGINAS` (20) páginas com `incompleta` e, cortada, a marca da ficha é buscada pelo nome
(`tiktok_marca_buscar`, `brand_name`); `listing_check` FAILED sem motivo legível não conta como conferido.

### Magalu (V19.24, fase 0 de 5)
Plano decidido com o dono em 14/10 (integração completa "igual ao ML", por fases): 0 conexão; 1 pedidos na
fila de Araçatuba (chave = número do pedido Magalu, nunca SKU; kit pela regra da Shopee V18.71; a extensão
Importador continua como reserva e junta no mesmo pedido); 2 etiqueta Magalu Entregas (a NF-e vai pelo Linx —
o Noromak só espera e mostra "aguardando NF"); 3 vendas em `vendas_v1799` (`magalu`) e Financeiro (tarifa pela
API de análise financeira/DRE; sem ela, nula); 4 anúncios/preço/estoque **só leitura** (preço só por clique do
ADMIN com a trava do mínimo; estoque **nunca** gravado); 5 pós-venda (SAC/perguntas na tela Pós-venda) e promoções.
Uma conta só (Araçatuba). **Fase 0:** OAuth do ID Magalu (`magalu_connect_url` → `id.magalu.com/login` com
`choose_tenants=true`; `magalu_exchange_code`; token como **formulário** (o JSON do exemplo da doc dá 401 invalid_client — V19.25), com Basic de reserva; `magalu_access_token` renova sob `MAGALU_TOKEN_LOCK`, relendo a
linha), tabelas `magalu_integration` / `magalu_oauth_state` / `magalu_event_log` (30 dias), rotas
`/api/integracoes/magalu/{callback,status,connect,logs,disconnect}` e as páginas **públicas** `/termos` e
`/privacidade` (o `idm client create` exige as duas + o callback). Credenciais `MAGALU_CLIENT_ID/SECRET` no `.env`.
**Fase 1a (V19.26):** prévia **só leitura** — `GET /api/integracoes/magalu/previa` (`magalu_pedidos_previa`: `/seller/v1/orders?status=approved`, paginado; entrega `approved`/`invoiced` entra, kit aberto pelo `_shopee_expand_kit_lines`) e botão "Ver pedidos da Magalu" no cartão; **não grava em `conf_pedidos`**. A 1ª resposta com pedidos fica em `magalu_amostra_v1926` (`GET /api/integracoes/magalu/amostra`, perfil Admin) — conferir os campos antes da 1b (importação).
**Fases 1b + 2 (V19.27):** importação **começa ligada**, como nos outros marketplaces (decisão do dono, 14/10; `magalu_config_v1927`, sem linha = ligada, botão Ligar/Desligar no cartão, só perfil Admin). Worker `magalu_pedidos_loop` (120 s): mantém o token vivo sempre e, ligada, roda `magalu_importar_pedidos` (também `POST /api/integracoes/magalu/sincronizar`, 409 desligada). Linhas `OrigemCadastro=API_MAGALU`, `Marketplace=MAGALU`, uid `MAGALUAPI_<sha1>`; kit pelo `_shopee_expand_kit_lines`; pedido já na fila por outra origem (extensão/manual) **não duplica**; linha já importada só recebe os campos de `MAGALU_CAMPOS_OPERACIONAIS` (edição manual fica); entrega enviada/cancelada sai por `remove_pending_identities` (V18.52, remoção nomeada); API falhou = nada gravado. Etiqueta: entrega `invoiced` sem etiqueta → `POST /seller/v1/logistics/shipping-labels` (zpl/full, `channel.id` do pedido) → baixa o `signed_url` → `conf_etiquetas` (marketplace `magalu`, `metadados.entrega_id`) — a Conferência acha pelo par marketplace + pedido. Coluna `Etiqueta API Magalu`: AGUARDANDO NF / AGUARDANDO / VINCULADA. 1ª resposta da etiqueta também vai para a amostra.
**V19.29 — canal da etiqueta:** o pedido real **não traz `channel.id`** (só `marketplace.document`/`alias`); o canal vazio deu 404 DELIVERY_NOT_FOUND (15/10, pedido 1578270107775283). `_magalu_corpos_etiqueta`: canal = id do vendedor da entrega sem `GENPUB.` (como no exemplo da doc) + `branch` da entrega; em 404 (`MagaluNaoEncontrado`) tenta sem a filial, com o id cheio do vendedor e com o `channel.id` (se vier). Outro erro não repete. Na resposta real os status de entrega são os da doc (`approved`, `invoiced`, `shipped`).

### Mercado Livre
OAuth com `offline_access`. Cinco workers em background rodam 24/7 no servidor:
novos pedidos (~300 s), etiquetas pendentes (~300 s, todos os pendentes por volta), reconcile de pedidos manuais (~60 s),
keepalive do token (~300 s), catch-up de segurança (~900 s).
**V20.15/V20.16 — o aviso manda** (dono, 03/11: CPU em 62–85%). Pedido e etiqueta chegam pelo aviso do ML
(webhook `orders_v2`/`shipments`, nas duas contas); os dois vigias são rede de segurança de até 5 min (eram 15 s/20 s).
O vigia de pedidos novos não relê envio/etiqueta de quem já está na fila com venda + envio (é do vigia de etiquetas),
e o de etiquetas não reimporta envio `ready_to_ship` já postado (`ML_SUBSTATUS_JA_POSTADO`: dropped_off, picked_up,
in_hub — a etiqueta não imprime mais; o pedido fica na fila para o ADMIN). Painel de saúde conta o atraso a partir do
intervalo. Shopee pelo aviso (Push Mechanism) fica para outra fase. Testes: `TESTES/test_vigias_sem_repeticao_v2015.py`,
`TESTES/test_vigias_por_aviso_v2016.py`.
**V20.18 — aviso reforçado** (revisão de segurança de 08/10; dono, 04/11). O endereço do aviso é aberto e aceitava
qualquer coisa. `_ml_aviso_conferir`: só `orders_v2`/`orders`/`shipments` com recurso `/orders/<número>` ou
`/shipments/<número>` (`_ML_AVISO_RECURSO`), `application_id` igual ao `ML_CLIENT_ID` (vazio = recusado) e `user_id` de
uma conta conectada (antes, sem `user_id` ia para Araçatuba). Recusa não chama o ML e fica no log uma vez por motivo e
por hora (`_ml_aviso_recusado`). A rota responde "ok" na hora e põe o aviso numa fila de `ML_AVISO_FILA_MAX` (500) com
`ML_AVISO_TAREFAS` (3) tarefas (antes: uma thread por aviso, sem limite); o mesmo recurso ainda na fila não entra de
novo, fila cheia descarta (o vigia de 5 min cobre); corpo acima de `ML_AVISO_MAX_BYTES` (64 KB) = 413 sem ler.
Conferido em produção antes: os 2.244 avisos de pedido e envio das duas contas vieram com os dois campos.
Teste: `TESTES/test_webhook_ml_v2018.py`.
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
com CFQ/BFQ.
**V20.48 — rotinas do ML sem trabalho repetido** (10/11: alerta de expedição travada; banco ocupado 45% em 18 h, telas com
"Broken pipe"). Pelo `/api/saude/medicao`: vigia de etiquetas 2 h 56 min de banco e reconciliação 2 h 05 min. (1) A recuperação de
pedidos presos rodava em toda volta do vigia (`round(120 / 300)` = 0 → 1 desde a V20.15) e reimportava os ~60 pedidos que só esperam
etiqueta: agora `_ml_descoberta_devida` (por casa, `ML_DESCOBERTA_S` = 900 s) e `_ml_recover_local_pending_identifiers_once(automatica=True)`
pula a linha com venda E envio fora do Full (é do vigia; o botão manual continua completo). (2) Reconciliação: venda já reconhecida sem
envio (ou Full protegido) só a cada `ML_RECONC_COM_VENDA_S` (900 s, `_ML_RECONC_COM_VENDA` em memória); print/extensão sem venda segue a
cada minuto. (3) Precificador: abrir relia todos os anúncios no ML (174 s); `_ml_precificador_live` guarda a lista + detalhes 15 min por
casa (leitura com erro não fica; uma por vez); os vínculos gravados no banco valem na hora. Teste: `TESTES/test_ml_rotinas_leves_v2048.py`.
**revisar-antes-de-subir (10/11):** a recuperação automática só pula quem o vigia acompanha (venda + envio + etiqueta ainda não
VINCULADA): linha com o texto VINCULADA sem etiqueta válida (o ADMIN removeu) continua sendo revista. A lista do Precificador vem do
`ml_todos_os_anuncios` (scan; a paginação por offset estourava em ~1000 e a leitura "falhava" sempre, sem nunca ser guardada); leitura
parcial fica guardada com os erros, sem nenhum anúncio não fica; quem chega com outra leitura em andamento espera no máximo
`ML_PRECIFICADOR_LIVE_ESPERA_S` (5 s) e recebe a última leitura (sem nenhuma, lê sozinho). A reconciliação só marca a vez com a volta
completa (`_ml_reconc_marcar`) e a descoberta que falhou é tentada na volta seguinte. Teste: classe `RevisaoV2048`.
**V20.04 — vigias leves (40% de CPU e banco ocupado 39,7%, 30/10):** os vigias de etiquetas e de pedidos novos
remontavam a fila, decodificavam o `conf_etiquetas` e abriam o JSON de todos os concluídos a cada ciclo, por conta.
`_ml_fila_leitura` e `_ml_etiquetas_validas` guardam a leitura pela versão das chaves (a fila também pela do tombstone),
por no máximo `ML_CACHE_LEITURA_S` (60 s); **só para decidir o que olhar**: as linhas não podem ser alteradas e quem
grava continua no `get_storage`/`save_storage`. `_ml_completed_order_ids` lê sempre chave + data e abre o JSON só de quem
é novo. **Tela nova que grava a fila ou o tombstone fora do `save_storage` tem de subir a versão.** Shopee: `CriadoEm` não muda mais, `ShopeeAtualizadoEm`
só anda com mudança real e sincronização sem mudança não grava (a versão da fila não sobe e as telas não rebaixam tudo).
`/api/saude/medicao` mostra quem segura o banco. Teste: `TESTES/test_vigias_leves_v2004.py`.
**V19.85 — carrinho com kit (caso real 26/10, carrinho 2000015699983821):** `_ml_bind_manual_pending_rows`
remarcava TODA linha do carrinho com a venda importada na hora (`VendaMercadoLivre`); com duas vendas no carrinho, a
limpeza seguinte apagava a peça da outra venda e a busca Pedido + SKU fundia o pano avulso com o pano do kit. A venda
real da linha da API vem da chave `MercadoLivreLinhaKey` (`_ml_venda_da_linha`, nunca era remarcada): o vínculo, a
busca e a limpeza não tomam linha da API de outra venda (`_ml_linha_de_outra_venda`), o que também desfaz as filas já
embaralhadas na releitura. Linha sem a chave (print, extensão, TXT) segue como antes. Linha nova cujo UID já
está na fila (linha fundida antiga) ganha UID próprio; sem colisão o UID não muda. A mesma regra vale para o kit
corrigido pelo ADMIN (`KitManualOverride` só trava a venda dele) e para o cancelamento (sai só a venda cancelada). **Não mudar o `__uidLinha` das linhas ML** (o tombstone de linha do ADMIN é pelo UID) nem tirar
linha por remoção nomeada aqui (o kit fechado e a 1ª peça têm o mesmo UID; a revisão de 26/10 recusou as duas
ideias). Teste: `TESTES/test_carrinho_kit_ml_v1985.py`.

### Shopee
Fluxo de etiqueta: `get_shipping_parameter` → `ship_order` → `get_tracking_number` →
`create_shipping_document` → `get_shipping_document_result` → download/vínculo.
Para UNSPLIT existe fallback quando a API devolve `ship_order_not_need_package_number`:
repete `ship_order` sem `package_number`.
**V20.13 — lista de envio vazia não é "já organizado"** (03/11): desde 07:41 a `get_shipment_list` volta vazia, sem erro,
com pedidos READY_TO_SHIP e NF-e válida. `shopee_label_candidatos`: pendente sem etiqueta fora da lista consulta o
`get_order_detail`; pacote `LOGISTICS_READY` vai pelo `ship_order` (falta de NF-e volta a aparecer), o resto direto ao
rastreio; sem o detalhe, como antes. O vigia olha até 150 pendentes (era 40). A sincronização de pedidos ainda usa a
mesma lista. **V20.14:** a sincronização junta os READY_TO_SHIP/RETRY_SHIP da `get_order_list` (`shopee_pedidos_a_enviar`,
15 dias; o pacote vem do detalhe); falha nela = só a lista de envio. Teste: `TESTES/test_shopee_lista_vazia_v2013.py`.
**V20.22 — quantidade do kit "kitN-X"** (dono, 04/11: romaneio com uma quantidade, Conferência com outra). O reparo
de boot da V18.04 (`_kit_e_residuo_prefixo`, feito para o ML) tratava como resíduo a linha de componente aberta pelo
cadastro (`SkuOrigemImportacao` = SKU do componente, sem "KIT") e, **a cada publicação**, voltava o kit2 para 1 un. e
apagava o selo (261104EF1BKBAP). Linha `KITDB_` ou com `SkuReferenciaShopee`/`SkuPrincipalShopee` "KIT…" não é resíduo.
E kit "KITn-X" sem cadastro abre pelo prefixo (`_infer_quantity_kit_from_sku`), como no ML (261028SR23HUPK:
kit10-10396473 subia como 1 × o SKU do kit). Vale também para Magalu e TikTok (mesmo `_shopee_expand_kit_lines`).
**Transição (revisão de 05/11):** a linha antiga do kit (Shopee/Magalu/TikTok, SKU do kit como veio) que a integração
agora abre em `KITDB_` não volta pelo `preserve_pending_records_v1714` e o bip dela passa para os componentes
(`_kit_aberto_da_linha_v2022`, `_kit_passar_bip_v2022`: na proporção da quantidade da linha antiga, nunca diminui).
Linha antiga editada à mão (SKU trocado, ou override do ADMIN em SKU/quantidade, `_admin_linhas_editadas_v2022`; o bip
não conta) fica como o ADMIN deixou e os componentes daquele kit não entram (senão 20 unidades ou bip inflado). Na Magalu/TikTok a linha que o ADMIN excluiu (tombstone por UID) sai **antes** de abrir o kit
(`_sem_linhas_excluidas_admin_v2022`, sob o `DB_LOCK`; leitura falhou = a rodada para, nada gravado). Teste: `TESTES/test_shopee_kit_quantidade_v2022.py`.
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
**V19.77 — custo sempre de Araçatuba, congelado na venda** (dono, 22/10). 6007.003.315.00.8 foi vendido com
custo R$ 13,02; o estoque zerou e o Financeiro puxou R$ 39,41 — o último custo lido no Apollo de **Bauru**
(`custos_conhecidos_v1807.custo_auto` mistura as filiais e **não entra mais** no Financeiro). Custo = Araçatuba
(revenda 1): `custosFiliais` do Precificador > custo do Precificador com `filialCusto` 1 > `custo_apollo_v1931["1"]`.
`financeiro_custo_congelar` (na rotina `financeiro_frete_loop`) grava em `venda_custo_v1977` o custo das vendas de
até 3 dias; venda antiga sem custo congelado = "estimado" (o digitado na Demanda de Compra vence, até o congelado; ao congelar, o Apollo de Araçatuba vem antes do custo do Precificador sem filial; escrow com erro espera 6 h). SKU com barra que
é um código inteiro não vira duas peças; código só no Apollo também tem custo. O escrow da Shopee passou a ser
buscado pela mesma rotina (`financeiro_shopee_escrow_pendentes`, 30 dias): antes só pelo botão, e 237 de 292
pedidos de outubro estavam "parciais". A regra antiga (`_financeiro_custo_unitario`) segue no Pós-venda e na
análise de kits. Teste: `TESTES/test_financeiro_custo_v1977.py`.
**V19.79 — a planilha antiga do Precificador não vale** (dono, 22/10): os produtos com `_id` `source-N` que nunca
foram lidos do Apollo (`custosFiliais` vazio) têm em quase metade 30–42% a mais (WAP331 74,97 × 53,04 real; JFC217
91,00 × 64,38; 10256080 965,73 × 731,64). Sem leitura do Apollo ficam **sem custo** (o dono digita na Demanda). O
último custo visto (`custo_auto`) só vale de origem "Precificador" e de produto que não é só da planilha. A Demanda
de Compra usa a mesma regra (`compras_custo_aracatuba`). Venda sem custo de Araçatuba registrado fica **sem custo** (o custo de outra filial foi
descartado pelo dono: vendido por Araçatuba, Bauru 39,41 × 13,02 real). O dono digita o custo real **no próprio
Financeiro** (lápis na coluna Custo → `POST /api/compras/custo`, o mesmo custo digitado da Demanda, que vence
tudo); o payload manda `por_sku[].codigo_custo` (vazio em kit: edita-se cada peça). Kit "KITa - b" soma as peças.
**V20.06 — 12 meses** (dono, 30/10): três mini gráficos (faturamento, pedidos, ticket) abaixo da faixa de custos, sempre os últimos 12 meses e seguindo só o filtro de canal. `GET /api/financeiro/evolucao` (`financeiro_evolucao_payload`) é **à parte do resumo**, que passa de 60 s em produção: só soma `vendas_v1799`, sem custo, Precificador ou Apollo. Mês sem venda = em branco, nunca zero; mês atual = parcial (barra clara). Lucro não entra: exigiria o cálculo pesado mês a mês, e o custo congelado só existe desde 22/10. Teste: `TESTES/test_financeiro_evolucao_v2006.py`.

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
**V19.60 — "No Full" = armazém do ML.** O `available_quantity` do anúncio soma o depósito do vendedor: MLB6143159674
(20/10) tinha 23 no anúncio e **0 no Full**, e a remessa sugeria 2. Agora `ml_estoque_full_completar` lê
`/user-products/{user_product_id}/stock` (locais `meli_facility`) para os anúncios Full nas duas checagens (2 h e retrato
diário) e grava em `ml_estoque_dia_v1872.estoque_full_ultimo` (leitura sem número não apaga a do dia). Candidatos e a
"recebida automática" usam essa coluna; sem número = em branco, nunca zero. A Ruptura continua com o `available_quantity`
(o anúncio ainda vende do depósito). Teste: `TESTES/test_estoque_full_v1960.py`.
**V19.85 — "a caminho" também do ML** (dono, 26/10: o envio da NF 57.238 saiu sem "Marcar agendada" e a sugestão
repetiu os itens). Na rodada, fora do `DB_LOCK`, `full_ml_a_caminho` lê `/inventories/{inventory_id}/stock/fulfillment`
de cada anúncio Full candidato (`inventory_id` por `/items?ids=` de 20 em 20, guardado em `FULL_ML_INVENTARIO`):
`not_available_detail` com status `transfer` = a caminho do armazém (conferido com a nota: 48/48, 12/12, 6/6).
**A caminho = o MAIOR entre o agendado no sistema e o do ML, nunca a soma** (`_full_a_caminho_ml_aplicar`); ML sem
resposta = fica o do sistema (nunca zero); 5 erros seguidos param a leitura. Item guarda `a_caminho_sis` e
`a_caminho_ml`; coluna "A caminho" na tabela com a origem (ML/sistema). Teste: `TESTES/test_full_a_caminho_ml_v1985.py`.

### Plataforma de anúncios (V20.23, fase 1)
`APP/anuncios.html` (`/anuncios`, menu Vendas e anúncios, módulo **admin**; desenho em `docs/superpowers/specs/2026-11-05-hub-anuncios-design.md`).
Vai substituir o Alephee. Fase 1 = **ficha do produto + kits + anúncios puxados do ML**; o Noromak **só lê** o ML aqui (nada é criado,
alterado nem estoque gravado). Fases seguintes: 2 criar anúncio (título → categoria → atributos obrigatórios), 3 estoque, 4 vendas → Apollo.
**Tabelas:** `anuncio_ficha_v2023` (uma ficha por código; campos de `ANUNCIO_CAMPOS_FICHA` + origem de cada campo + `versao`) e
`anuncio_canal_v2023` (um anúncio por canal/conta/id; `controle` noromak|alephee|full — `full` = logística `fulfillment`, estoque do ML;
`removido_em` = tombstone do ADMIN).
**Chave da ficha = `normalize()`** (maiúsculas, sem espaço; o mesmo código do `conf_kits`/Precificador), **não** `_exp_norm_codigo`: este
tira tudo que não é 0-9/A-Z e juntaria códigos diferentes ("A/B" e "AB", "KIT2-X" e "KIT2X") e perderia os pontos dos códigos do Apollo
("6007.003.315.00.8"). `_exp_norm_codigo` só é chave para casar o estoque com os pendentes da fila; o Apollo é consultado com o código
como está no cadastro.
**Autoridade por campo** (`ANUNCIO_ORIGENS`): admin > alephee > anuncio_ml > apollo. A importação (`ficha_importar`) **nunca** toca campo
de origem admin — nem o que o Admin **apagou**: apagar deixa a origem `admin` sem valor ("apagado pelo Admin" na tela) e o puxar não
preenche de novo; o Admin digitando volta a valer. Desconhecido fica em branco (nunca zero); medida (peso/altura/largura/comprimento) <= 0
é ignorada.
**Concorrência:** a edição manda `versao`; outra mais nova = 409 `CONFLITO`; `versao` None numa ficha que já existe também é conflito.
**Kits:** editam o **mesmo** `conf_kits` pelo `save_storage` como ADMIN (a regra do ADMIN e o Cadastro de Kits continuam valendo); linha nova
ganha id `KIT_…`. **Tirar ou pôr peça** de um kit com pedido na fila da Conferência (linha com `SkuKitOrigem` do kit ou uid
`KITDB_<pedido>_<kit>_…`) é recusado (400): o índice do uid muda e a próxima sincronização da Shopee duplicava as linhas (a antiga, com
bip, voltava pelo `preserve_pending_records_v1714`); mudar a quantidade pode. Kit **novo** com o código de um produto (Precificador,
`conf_produtos` ou ficha de peça; "KIT…" não conta) é recusado: toda venda do produto viraria os componentes. Kit inativo aparece com o
selo "inativo". Estoque do kit = menor `floor((disponível − pendentes) / qtd)` entre as peças; pendentes da fila por `_exp_norm_codigo`;
Apollo sem resposta = em branco, nunca zero.
**Puxar do ML** (`anuncios_ml_puxar`): as duas contas (Araçatuba e Bauru), leitura fora do `DB_LOCK`, grava em lotes de `ANUNCIO_LOTE_GRAVACAO` (100);
falha do ML ou lista vazia **não muda nada**; um por vez (409). Anúncio novo entra `controle=alephee` (ou `full`, se a logística é `fulfillment`); **puxar nunca muda o controle**.
Anúncio que sumiu do ML **não** é marcado (fica como estava). `removido_em` só pelo ADMIN (`anuncio_remover`): o puxar não atualiza nem
recria o removido; `anuncio_reincluir` desfaz. **Passar para o Noromak** (`anuncio_controle_definir`) exige
`confirmou_alephee` (o anúncio ainda está no Alephee, risco de duas ferramentas mexendo) e **não vale para Full**; ficha com anúncios nos dois
controles aparece como "misto".
**V20.24 — puxar em segundo plano e um anúncio só** (dono, 05/11): `POST /api/anuncios/puxar` só confere e inicia a thread
(`anuncios_puxar_iniciar`; ocupado = 409) e responde na hora; o andamento fica em `ANUNCIOS_PUXAR_ESTADO` (conta atual,
resultado por conta) e sai em `GET /api/anuncios/puxar/situacao` (módulo admin; a tela consulta a cada 3 s, também ao abrir
com uma puxada rodando). A thread fecha o estado e solta a trava num `finally`; auditoria no fim. Erro no meio de uma conta
mantém os lotes já gravados (a tela diz isso). **Um anúncio:** `POST /api/anuncios/puxar/um {mlb_ou_link}` (`anuncio_puxar_um`;
`anuncio_mlb_do_texto` aceita MLB, MLB-, link do anúncio; link de catálogo `/p/MLB…` = 400): lê o item em cada conta
conectada (`ml_itens_detalhe` agora traz `seller_id` e, com `falhas=`, separa 404 de "sem resposta"), a dona é a do
`seller_id` (`ml_casa_do_seller`); grava pelo mesmo `_anuncio_gravar_itens` da puxada. Não é nosso / não existe = 404, ML sem
resposta = 502, nada gravado; removido pelo ADMIN continua removido. **Revisão:** item sem SKU no ML não apaga a ficha já ligada
(`COALESCE(NULLIF(?,''),ficha_codigo)`); kit com pedido na fila compara a **lista em ordem** (reordenar também muda o uid
`KITDB_`); 408 no `POST /items` = `incerto`. Teste: `TESTES/test_anuncios_puxar_fundo_v2024.py`.
**V20.25 — puxar traz a ficha** (dono, 05/11, CAM1019: "puxei um mas não veio as informações" — só marca, EAN, peso e medidas).
As duas puxadas importam também `nome` (título do anúncio) e `categoria_ml` (`anuncio_campos_do_detalhe`, origem `anuncio_ml`, mesma
regra do `ficha_importar`: Admin e "apagado pelo Admin" nunca mudam; vários anúncios na mesma ficha = o 1º lido vale). O **"Puxar um"** e o
botão **"Trazer dados do anúncio"** (linha ML da aba Anúncios em todos os canais; `POST /api/anuncios/trazer-dados {canal, conta,
anuncio_id}`, perfil Admin, token da conta da linha; removido pelo Admin = 400) trazem ainda a **descrição** (`GET /items/{id}/description`
→ `plain_text`) e as **fotos** (`ml_itens_detalhe` pede `pictures`, até 12; `_anuncio_fotos_do_ml`): só quando a ficha **não tem foto**
(nunca duplica, nunca apaga; `ANUNCIOS_FOTOS_ML_LOCK` + releitura antes de gravar), baixadas fora do `DB_LOCK`, 20 s por foto, **só https
do mlstatic.com** (também no redirecionamento), versão `-F` primeiro; falha é pulada e contada. A puxada em massa **não** lê descrição nem
fotos. A resposta traz `trazidos` e `trazido_texto` ("Título, categoria, descrição e 5 fotos vieram do anúncio."), que a tela mostra.
Prévia: anúncios simulados com fotos e descrição. Teste: `TESTES/test_anuncios_puxar_dados_v2025.py`.
**Rotas:** `GET /api/anuncios`, `GET /api/anuncios/ficha?codigo=&apollo=1`; `POST /api/anuncios/{ficha,kit,puxar,controle,remover,reincluir}`.
Ver = módulo admin; gravar = perfil Admin (Consulta bloqueado pelo `consulta_bloqueada`).
Testes: `TESTES/test_anuncios_{ficha,kits,puxar,rotas,kit_fila}_v2023.py`.

**V20.24 — fase 2: criar anúncio no ML** (aba nova na ficha; plano `docs/superpowers/plans/2026-11-05-anuncios-fase2-criar-ml.md`).
Ordem do dono: **título → categoria → atributos obrigatórios** (categoria e obrigatórios lidos do ML, cache de 24 h; o que a ficha
tem já vem preenchido, marcado "da ficha"; SELLER_SKU e medidas do pacote vão da ficha; desconhecido fica em branco, nunca zero).
**Fotos** em `ANUNCIO_FOTOS_DIR` (disco, nunca no banco; arquivo nomeado pelo id): do computador (`pc`) ou do Estúdio de Imagens
(trabalho pronto); tipo pelo conteúdo, ≤ 10 MB, ≥ 500 px, ≤ 12 por ficha; gravadas fora do `DB_LOCK`, sem órfãos em falha; ordem e
remoção pela tela. **"+ Novo produto"** na lista cria a ficha só se não existir (409 `JA_EXISTE` com a atual).
**Tabelas:** `anuncio_foto_v2024`, `anuncio_rascunho_v2024` (um por tentativa; vários por ficha/conta; `versao` → 409 `CONFLITO`) e
`anuncio_amostra_v2024` (1ª resposta real de cada chamada, 6.000 caracteres — **conferir na 1ª publicação**: se a categoria pede
`family_name` e o `value_id` do `EMPTY_GTIN_REASON`). Tarifa de anúncio que ainda não existe: só `listing_prices` (frete em branco, sem MLB não há cotação).
**O ML só é lido por GET, exceto no Publicar**, a ÚNICA escrita no ML da plataforma (perfil Admin; sobe as fotos, `POST /items`, descrição).
"Conferir no ML" usa `POST /items/validate` sem fotos e não cria nada. `ANUNCIOS_PUBLICAR_LOCK` + máquina de situação
`rascunho → validado → publicando → publicado | erro | incerto` (a troca de situação é feita **sob o `DB_LOCK`**, só se a situação
ainda é a esperada; as chamadas ao ML e ao Apollo ficam **fora** dele). Falha **antes do envio** (token, Apollo, quantidade, foto)
volta a `validado` com o motivo em `erros` (a tela mostra no Conferir e no Publicar); nada foi criado.
**Incerto** = erro de rede/timeout, 5xx, 2xx sem `id` ou `publicando` que sobrou no boot (processo caiu no meio): o ML pode ter criado,
então **nunca repete**; o Admin confere no ML e libera com `liberar` (com o MLB = `publicado`, entra na lista como `noromak`; sem MLB = volta
a `validado` (pode publicar de novo). MLB que já está na lista com outro produto, sem produto ou com controle que não é `noromak` é
recusado: nunca religa calado). Recusa 4xx = erro, nada foi feito. **Estoque inicial** = `da_para_montar` pela leitura do Apollo **agora** (kit = peça que
tem menos); desconhecido = não publica; só pode ser **menor** que isso, nunca maior. **Título → `family_name`:** se a recusa é a da categoria
User Products (causa com `family_name` ou `user_product`; recusa comum do título não repete), o Conferir **e** o Publicar tentam de
novo **uma vez** com `family_name` e sem `title`; o corpo que o ML aceitou no Conferir fica em `anuncio_rascunho_v2024.usa_family_name`
e o Publicar manda ele primeiro. Falhou nas duas = mostra as duas recusas. `POST /items` e a descrição com 90 s de espera
(`ANUNCIO_ITEM_TIMEOUT`; o `_ml_promo_http` ganhou `timeout` opcional, o padrão não mudou). O Publicar lê o Apollo só da filial da conta.
**Custo** da conta do preço = regra de **Araçatuba** (V19.77/V19.79, `compras_custo_aracatuba`) para as **duas** contas: planilha
antiga sem leitura do Apollo = sem custo; kit = soma das peças × quantidade do Cadastro de Kits (peça sem custo = kit sem custo); a
tela diz de onde veio (`custo_origem`). Margem-alvo do Precificador. **Kit sem GTIN:** `EMPTY_GTIN_REASON` = o único valor da lista do
ML que fala de "kit" (frase do ML, vai como `value_id`); nenhum = em branco para o Admin escolher (texto livre nunca vai).
**Garantia** só vai (WARRANTY_TYPE "Garantia do vendedor" + WARRANTY_TIME) no formato "N dias|meses|anos"; outro texto não vai.
**Rotas:** `GET /api/anuncios/{ml/categorias,ml/atributos,ml/tarifa,foto,estudio,rascunhos}`;
`POST /api/anuncios/{foto,foto/remover,foto/ordenar,rascunho,rascunho/validar,rascunho/publicar,rascunho/liberar}`; `ficha` aceita criar produto novo.
Testes: `TESTES/test_anuncios_{criar,fotos,publicar}_v2024.py`.

**V20.31 — agente de anúncios** (dono, 06/11; desenho `docs/superpowers/specs/2026-11-06-agente-anuncios-design.md`). No rascunho, o
Admin clica em **"Pedir ao agente"** (perfil Admin; `POST /api/anuncios/agente/pedir {rascunho_id, nome?, link?}`) e o agente do Estúdio
(Claude no PC do dono) propõe **3 títulos** (regras do `/titulo`: ≤ 60, produto+marca+modelo+característica+volume), a **descrição**
(regras do `/descricao`, `[CONFIRMAR]` no que faltar) e os **atributos obrigatórios/condicionais** da categoria. Usa a **fila do Estúdio**
(`imagem_trabalho_v2017`, `tipo='anuncio'`, coluna `rascunho_id`; a tela do Estúdio esconde esses trabalhos): fotos da ficha copiadas
para a pasta do trabalho (kit: as peças com fichas e até 3 fotos; peça sem ficha vai com código e descrição do Precificador).
**Duas etapas automáticas:** `anuncio_textos` → o servidor pede ao ML a categoria pelo título recomendado (1ª sugestão) e os atributos
dela (fora do `DB_LOCK`; ML sem resposta = proposta só com os textos e a mensagem) → `anuncio_atributos` → `proposta`. Entrega do agente
em `POST /api/imagens/agente/anuncio` (token do agente): título > 60 sai da lista; atributo fora dos pedidos ou valor fora da lista do ML
é descartado; origem `ficha|rotulo|catalogo|internet`. **Só o agente 0.4+ recebe** (`/agente/proximo?anuncio=1`): o antigo trataria o
pedido como foto principal; entrega cruzada imagem×anúncio = 400. Trocar a categoria (`POST /api/anuncios/agente/atributos`) pede só os
atributos de novo. **Aceitar** (`POST /api/anuncios/agente/aceitar {trabalho_id, titulo, descricao, atributos, versao_rascunho}`):
grava no rascunho pelo `anuncio_rascunho_salvar` (versão conferida → 409 `CONFLITO`; os atributos escolhidos vencem os que o rascunho
tinha) e preenche na ficha **só** `nome/descricao/marca/modelo/categoria_ml` vazios e não "apagados pelo Admin". Nada vai ao ML.
Gasto no mesmo crédito do Estúdio com `imagem_gasto_v2017.origem='anuncio'` (`imagens_saldo().por_origem`). Estado:
`GET /api/anuncios/agente?rascunho=` (módulo admin). Testes: `TESTES/test_agente_anuncios_v2031.py` e `AnuncioDoAgente` em
`FERRAMENTAS_WINDOWS/agente_imagens/test_agente.py`.
**revisar-antes-de-subir (06/11, "corrija tudo"):** a tela do Estúdio (módulo Imagens) não refaz, apaga nem baixa pedido de anúncio
(`imagens_arquivo(..., anuncio_ok=True)` só na rota do agente). Entrega dos textos → `buscando_categoria` e a busca no ML numa thread
(`_anuncio_agente_buscar_categoria`; a entrega espera até `ANUNCIO_AGENTE_ESPERA_ML` = 40 s, o nginx corta em 60): entrega repetida é
recusada e o gasto não conta duas vezes; busca que não terminou (reserva vencida) vira proposta com "Use Trocar categoria". Nenhum
título até 60 = `erro` com o motivo e o gasto (antes 400 e 3 voltas). Categoria sem atributo obrigatório/condicional = proposta direto.
Aceitar reserva a proposta (`aceitando`, pela versão; falha devolve `proposta`; Trocar categoria no meio = 409). Pedir de novo aposenta
a proposta antiga só depois de copiar as fotos; cancelado durante a cópia = 409 e sem pasta. Cancelado com o agente trabalhando: o gasto
entra uma vez. O que o agente preenche na ficha tem origem **`agente`** (`ANUNCIO_ORIGENS`: abaixo do anúncio do ML, via
`ficha_importar`; tela "do agente (confira)"). Fontes até 20 links http(s) de 300; valor de atributo sem limite do ML corta em 255.
Revisão das correções: `buscando_categoria` e `aceitando` (este com prazo `ANUNCIO_AGENTE_ACEITAR_S` = 120 s) que pararam no meio
voltam a proposta também quando a tela consulta (`_anuncio_agente_destravar`); falha na ficha depois de gravar o rascunho não derruba o
aceite; "Sincronizar agora" do TikTok também respeita a fonte única (`_tiktok_extensao_em_uso`); a amostra esconde os valores e mantém as chaves.
Teste: `TESTES/test_revisao_subida_v2031.py`.

**V20.32 — anúncios na Shopee (Araçatuba)** (dono, 06/11: "testar o envio desse mesmo produto" na Shopee e Magalu;
Shopee primeiro — o token da Magalu só lê produto/estoque; desenho `docs/superpowers/specs/2026-11-06-anuncios-shopee-design.md`).
Painel **"Criar anúncio na Shopee"** na aba Anúncios em todos os canais (só com Araçatuba visível): rascunho próprio
`anuncio_rascunho_shopee_v2032` (título, categoria, atributos `{id: {valor_id, valor, unidade}}`, marca, fotos da ficha, descrição,
preço, quantidade; `versao` → 409; situação como no ML: rascunho → validado → publicando → publicado | erro | incerto). Leituras com
cache de 24 h: `get_item_limit` (limites do título/descrição/fotos), `get_category` (pt-br) + `category_recommend` (pelo título; só
folha), `get_attribute_tree` (nível de cima: `mandatory`, lista/livre/unidade), `get_brand_list` (paginado, `is_mandatory`; marca da
ficha casada sem acento, senão "No Brand" `brand_id 0` só se não obrigatória), `logistics/get_channel_list` (ligados, sem
SIZE_SELECTION/CUSTOM_PRICE, que aceitam o peso/medidas). **A Shopee não tem "conferir sem criar"**: o Conferir é local contra essas
regras; **peso e medidas sempre lidos da ficha na hora** (nunca copiados para o rascunho). Publicar (perfil Admin,
`ANUNCIOS_PUBLICAR_LOCK`): Apollo de Araçatuba relido (quantidade só diminui), fotos por `media_space/upload_image` (multipart, só
JPG/PNG), `product/add_item` (`NORMAL`, `NEW`, `item_sku` = código, `gtin_code` = EAN ou "00", `tax_info.ncm` + `origin "0"` quando a
ficha tem NCM de 8 dígitos). `_shopee_chamar` separa recusa (nada feito → `erro`) de sem resposta (rede/timeout/5xx/sem `item_id` →
`incerto`, nunca repete; libera com o número do item). Publicado = linha `canal='shopee'`, `conta='aracatuba'`, `controle='noromak'`,
estoque **Manual**. **Estoque**: conta de estoque `'shopee'` (`_estoque_conta_valida`/`_estoque_alvo`) com interruptor próprio em
`anuncio_estoque_config_v2026` (Simulação → Lote → Automático com OWNER), o **mesmo disponível do ML** (Apollo de Araçatuba − pendentes;
decisão do dono: um estoque só para todos os canais), rodízio do Apollo único por filial; grava só por `shopee_estoque_gravar`
(`product/update_stock` absoluto, `failure_list` = erro, relê `get_item_base_info` e só é ok com o número; com variações não grava).
1ª resposta de cada chamada em `anuncio_amostra_v2024` (`shopee_*`, construído pelo SDK gerado da doc oficial — **conferir no 1º uso**).
Prévia: `FERRAMENTAS_WINDOWS/previa_local/dev_shopee_anuncio.py` (título com "recusar"/"sumir" simula recusa/incerto).
Testes: `TESTES/test_anuncios_shopee_v2032.py`, `TESTES/test_anuncios_shopee_estoque_v2032.py`.
**revisar-antes-de-subir (06/11, "corrija tudo"):** erro do lado da Shopee no `add_item` (interno/ocupado/tempo, HTTP 408/429,
`_shopee_talvez_fez`) = **incerto**, nunca "recusado"; produto que já tem anúncio Shopee do Noromak (ou rascunho publicando/incerto) não
publica outro (`_shopee_ja_anunciado`, sob o `DB_LOCK`); **Publicar em segundo plano** (`shopee_publicar_iniciar`: reserva na hora, a
thread solta o `ANUNCIOS_PUBLICAR_LOCK`; a tela acompanha os rascunhos); número do item nunca se perde (vai no `incerto`); foto que não
é JPG/PNG barra no Conferir; foto tenta a assinatura da loja e, com erro de assinatura, a pública (`publico=True`); **Liberar** só com
número e só se o `item_sku` lido na Shopee for o código da ficha; o envio de estoque confere o SKU, recusa item excluído/bloqueado e
item com mais de um depósito (com um, manda o `location_id` lido), e depósito sem número = sem leitura (nunca 0); só renova o token em
erro **de token** (403 de permissão não gasta o refresh_token); a rodada da Shopee roda numa thread e a volta do worker espera no
máximo `ANUNCIO_ESTOQUE_SHOPEE_ESPERA_S` (20 s): Shopee fora do ar não segura o ML; Conferir confere a versão (409); preço finito;
fotos sem repetir; marca zera com a categoria (e marca fora da lista da categoria = erro; a da ficha fora da lista = aviso "No Brand");
"misto" só no mesmo canal; ver os rascunhos = módulo admin. Teste: `TESTES/test_revisao_shopee_v2032.py`.
2ª revisão (só das correções): 429 = a Shopee recusou sem processar (não é incerto), `error_time_out` é; a foto só tenta a assinatura
pública pelo **código** de erro do corpo; renova também em 403 com código de token (inclui a grafia `invalid_acceess_token`); o Publicar
que não começa (thread) volta a `validado`; mudança que chega com a rodada da Shopee ocupada fica para a volta seguinte
(`estado["shopee_pendente"]`); trocar de ficha durante a publicação não trava o painel.

**V20.34 — anúncios na Magalu (Araçatuba)** (dono, 06/11: "vamos para o magalu agr"; desenho
`docs/superpowers/specs/2026-11-06-anuncios-magalu-design.md`). Escopos de gravação pedidos pelo `idm client add-scope` (AVAILABLE
em 06/11); o token só recebe extra se o link pedir: `MAGALU_OAUTH_SCOPE` pede os quatro — **reconectar a Magalu uma vez depois de
publicar**. Painel **"Criar anúncio na Magalu"** na aba Anúncios em todos os canais: rascunho `anuncio_rascunho_magalu_v2034`
(título ≤ 150, categoria por busca de nome, ficha técnica/atributos da categoria, marca, fotos, descrição ≤ 7000, preço, preço "de",
quantidade; `versao` → 409). **Tudo na Magalu é assíncrono (202)**: o SKU enviado = código da ficha, e `aguardando` só vira
`publicado` quando o `GET` do SKU mostra o produto — aí manda o preço (centavos, `currency: BRL` sempre) e o estoque e a linha entra
(`canal='magalu'`, `anuncio_id` = SKU, estoque Manual). Acompanhamento: worker do estoque a cada volta + a tela (no máximo 1 leitura
por rascunho a cada 20 s). Sem resposta no envio = `aguardando` (não incerto); trace com erro = `erro`; 30 min sem o SKU = `incerto`
("Conferir de novo" / "Não foi criado"); `publicando` no boot = `aguardando`. **SKU que já existe na Magalu nunca é sobrescrito**
(o Publicar recusa). **Fotos = endereços do anúncio do ML** (`mlstatic.com`, JPEG; a Magalu baixa pelo endereço e as fotos do Noromak
estão atrás do login); a página não mostra imagem de fora (CSP): link "ver". Estoque: conta `magalu` (`ESTOQUE_CONTAS_FORA`, com a
`shopee`), interruptor próprio, o mesmo disponível de Araçatuba; `magalu_estoque_gravar` lê o AVAILABLE do canal (RESERVED fora),
`PATCH`/`POST` absoluto com o `branch` único, dois CDs = recusa, relê até ~30 s. `shopee_estoque_gravar` e o da Magalu passam pelo
mesmo `_estoque_gravar_fora`. Rotas `GET /api/anuncios/magalu/{categorias,atributos,conta,fotos,rascunhos}` e
`POST /api/anuncios/magalu/{rascunho,conferir,publicar,acompanhar,liberar}`. 1ª resposta de cada chamada em `anuncio_amostra_v2024`
(`magalu_*`) — **conferir no 1º uso**. Prévia: `dev_magalu_anuncio.py` ("recusar"/"sumir" no título). Teste:
`TESTES/test_anuncios_magalu_v2034.py`.
**revisar-antes-de-subir (06/11, "corrija tudo"):** categorias no endereço da spec (**plural** `/portfolios/categories`, busca com 3+
letras, opções em `choices`, atributo inativo fora, subcategoria por `/categories/hierarchy?parent_id=` barra no Conferir). **Nunca toma
SKU alheio:** a marca "enviado" (`enviado_em`) é gravada antes do POST — no boot `publicando` sem ela volta a `validado`; o SKU só é
aceito como nosso com o **mesmo título** do envio (senão `erro`, nada enviado); "Conferir de novo" só de `incerto` ou de `erro` depois
de o SKU ser reconhecido (etapa preço/estoque); SKU nosso que apareceu tarde é reconhecido no Publicar seguinte (não cria de novo).
Etapas `sku → preco → estoque → confirmar` (`etapa`, `etapa_em`, `tentativas`): preço POST/PATCH conforme já existe, **estoque inicial =
o Apollo de AGORA** (menor entre o pedido e o que dá para montar; sem leitura não envia) por ler-e-escolher PATCH/POST
(`_magalu_estoque_enviar`), 5 recusas ou 30 min = `erro` (sem laço); `publicado` só quando a Magalu mostra preço e estoque (ou com aviso
depois de 10 min) e a linha nasce com o estoque **lido**. Prazo de 30 min conta também com a leitura falhando. Acompanhamento **numa
thread** (`magalu_acompanhar_pendentes_fundo`, a volta do estoque espera ≤ 15 s; 3 por vez) e a rota `acompanhar`/`liberar` só dispara
(nginx 60 s). Conferir: fotos em paralelo com prazo de 25 s (o resto confere no Publicar; foto JPEG guardada 24 h). Estoque aceito e
ainda não mostrado = `ignorado` "aguardando a Magalu" (não conta erro nem para o Automático), releitura 2+4+6 s. SKU só com letras,
números, `.`, `-`, `_`. Republicar limpa a remoção do Admin (reinclusão explícita). Trace com falha guardado na amostra. Token:
`magalu_access_token(force=True, recusado=)` não renova de novo se outro já renovou. Teste: classe `Revisao` do mesmo arquivo.
**2ª revisão (só das correções):** o SKU é reconhecido como nosso pela **marca do envio** gravada no próprio produto
(`extra_data` `noromak_envio` = `rid:quando`, que o GET devolve; sem `extra_data` na resposta vale o título igual a um rascunho que
enviou) — título mudado pela Magalu não perde o produto, título repetido por outra ferramenta não o toma; trace com erro zera o
`enviado_em`; boot só acompanha envio posterior à reserva (`reservado_em`); o estoque inicial faz POST quando o GET do estoque dá 404;
Apollo sem leitura não gasta tentativa (só o prazo); atributos paginados (`_limit` 100) e os que não são de variação vão para a ficha
técnica; estoque aceito e não mostrado 3 vezes seguidas = erro (`_MAGALU_ESTOQUE_SEM_MOSTRAR`); a trava/intervalo só sai quando o
rascunho sai de `aguardando`; o lote do worker vai pelos há mais tempo sem conferir; estoque com espera 12 s por chamada e releitura 2+3 s.
**V20.35 — 1º uso real (06/11, TUNAP109):** a Magalu **troca o `extra_data`** pelo dela (`IdProduct`, `update_only_front`) e a marca
do envio some; o SKU criado pelo Noromak foi recusado como "não é o que o Noromak enviou". A prova de que é nosso passa a ser o
`created_at` (UTC, sem fuso) do SKU logo depois de um envio nosso desta ficha (de 2 min antes a 3 dias depois,
`MAGALU_CRIADO_ANTES_S`/`_DEPOIS_S`); SKU criado antes do envio = de outra ferramenta. "Conferir de novo" vale também no `erro`
cujo envio saiu (`enviado_em`): o acompanhamento confere de novo. O SKU criado fica `DRAFT` na Magalu até receber preço e estoque.
**V20.36 — histórico de envios** (06/11 20:33–20:35): o 2º Publicar reconheceu o SKU como nosso mas **trocou o `enviado_em`** pela
hora do clique, e o 3º recusou o próprio SKU como "criado fora do Noromak". Agora cada POST real entra em `envios_json` (nunca
sobrescrito; até 50) e a prova compara o `created_at` com **todos** os envios da ficha; reconhecer sem enviar e o "Conferir de novo"
não mexem no `enviado_em` (o prazo recomeça pelo `etapa_em`, `_magalu_passou_envio`); envio recusado (4xx, 429, 423, trace com erro)
sai do histórico. Migração no boot: a coluna nova é preenchida pelo `audit_log` (`anuncios.magalu_publicar` "aguardando" sem mensagem
ou com "Sem resposta"/"Falha depois do envio").
**V20.55 — 409 "already exists" no estoque** (11/11, TUNAP109): a Magalu levou 7 min para mostrar o 24 aceito às 13:40 e o
Automático reenviava o mesmo número; cada reenvio voltava 409 "Stock cannot be saved because it already exists" e na 3ª vez
o anúncio parava. `_magalu_estoque_enviar` não grava quando a Magalu já mostra o número, e o 409 com "already exists" conta
como "aguardando a Magalu" (3 seguidas = erro), não como recusa. Teste: `test_v2055_*` em `TESTES/test_anuncios_magalu_v2034.py`.

**V20.26 — fase 3: controle de estoque dos anúncios do ML** (decisões do dono 05/11; plano
`docs/superpowers/plans/2026-11-05-anuncios-fase3-estoque.md`). **Exceção à regra "o Noromak nunca grava estoque": o Noromak grava
estoque no ML só em anúncio `controle='noromak'`** (nunca Full, Alephee ou removido pelo ADMIN; como já é no TikTok). Cada anúncio tem
**Controle de estoque: Manual · Automático** (`anuncio_canal_v2023.estoque_modo`, padrão **Manual**; trocar quem controla volta a Manual).
**Manual** = o Admin digita e vai na hora (`nkConfirmar` "de X para Y"; 0 só digitado). **Automático** = Apollo da filial (Araçatuba
revenda 1, Bauru 3) − vendido e ainda não baixado (`pendentes_por_filial`), **sem folga**; kit = a peça que dá menos
(`kit_da_para_montar`); peça sem leitura, Apollo sem resposta, leitura com mais de 6 h ou fila não lida = **não envia** (nunca zero).
Interruptor **por conta** (`anuncio_estoque_config_v2026`): `simulacao` (padrão; calcula e registra) → `lote` ("Enviar agora") →
`automatico` (envia sozinho; ligar = senha + 2FA do OWNER, `require_owner_reauth`). Anúncio em Manual nunca é tocado pelo automático.
**Gravar = só `ml_estoque_gravar`** (trava por anúncio, ML fora do `DB_LOCK`, dentro de `ml_na_casa`): Full recusado sem chamada;
`PUT /items/{id}` ou, com depósitos, `PUT /user-products/{id}/stock/type/selling_address` com `x-version` (409 = relê e tenta 1x;
sem `selling_address` = erro "ajuste no ML"); **relê o anúncio** e só é ok se o ML mostrar o número (RF3: 200 sem mudar = erro);
1ª resposta de cada chamada em `anuncio_amostra_v2024` (`estoque_put_item`, `estoque_put_up`, `estoque_up_ler`). Antes de enviar, sob a
trava, o Automático relê o modo do anúncio (virou Manual = desiste, RF4) e a versão da fila (mudou = recalcula, RF1).
**Apollo em rodízio** (`anuncios_apollo_ler_proximo`, `anuncio_apollo_v2026`): ≤ `ANUNCIO_APOLLO_POR_MINUTO` (3/min, a cota de 20/min
é dividida com Faturamento/Separação/TikTok); nunca lido > disponível ≤ 3 lido há > 10 min > mais antigo; só códigos de anúncios em
Automático. Worker **`anuncios_estoque_loop`** (20 s, `_worker_protegido`, em `SAUDE_WORKERS_INFO`): sem anúncio em Automático não lê
nada; refaz a conta quando a fila ou uma leitura muda (ou a cada 10 min); o mesmo número recusado pelo ML só é tentado de novo em 10 min.
Registro em `anuncio_estoque_log_v2026` (ok/erro/simulado/ignorado, últimos 20.000). Rotas: `GET /api/anuncios/estoque?conta=&anuncio_id=`
(módulo admin, só lê o banco); `POST /api/anuncios/estoque/{modo,manual,config,enviar}` (perfil Admin, `conta` obrigatória; Manual em
anúncio Automático = 400; Enviar em Simulação = 400, rodada em andamento = 409). Tela: cartão "Controle de estoque" na aba Anúncios em
todos os canais e painel "Estoque automático" (um cartão por conta, segue o seletor de casa; registro com `data-nk-tabela`).
Prévia: o ML simulado aceita o PUT e o MLB1129555578 tem depósito do vendedor (V20.27: multiorigem, envia).
Teste: `TESTES/test_anuncios_estoque_v2026.py`.
**Revisão final (05/11, "pode corrigir todos"):** (1) chave **por conta** `apollo_desconta_atendimento` em `anuncio_estoque_config_v2026`
(padrão 0 = desconta todo pendente). O Disponível do Apollo = Contábil − Negociação − Fulfillment; se a Negociação já inclui o atendimento
que a integração abre para cada venda, descontar a fila tira a venda duas vezes — **ligar só depois de o Guilherme confirmar** (V20.42: confirmado em 09/11 — o atendimento aberto entra na Negociação, 8 de 9 medidos; ligar depois de comparar os dois números por alguns dias). Ligada,
desconta só os pedidos sem atendimento: com atendimento = linha do `faturamento_v1943` com `atendimento` preenchido ou situação
`feito`/`pronto_caixa`; pedido que o Faturamento não conhece desconta (o lado seguro). Mudar = perfil Admin (sem OWNER), auditado
(`anuncios.estoque_desconta_atendimento`) e no registro; `POST /api/anuncios/estoque/config {conta, apollo_desconta_atendimento}`. Cada
item traz `calculado_tudo` e `calculado_sem_atendimento` (a Simulação mostra "descontando tudo: X · só sem atendimento: Y" e o registro
também, para comparar por alguns dias). (2) **Abertura do SKU única** (`_anuncio_sku_partes`: Cadastro de Kits/KITn-X, senão
`_separacao_partes`) no estoque do anúncio **e** no pendente (`pendentes_por_filial`): "WAP103/S", "PSH367/80" e
"KIT-1/10405594-1/10405569" passam a descontar. O TikTok continua com `pendentes_da_filial` (`_tiktok_pecas`, igual). (3) **Pedido
concluído não sobe antes do Apollo reler:** cada cálculo compara o pendente com o anterior (`_anuncio_pend_observar`); peça cujo pendente
caiu (`_ANUNCIO_PEND_QUEDA`) com leitura do Apollo mais velha que a queda segura o anúncio no último enviado (sem envio: o número do ML;
sem nenhum = não envia; `segurado` diz por quê) e vai para o começo do rodízio. Memória do processo, sem chamada a mais. (4) Pendente
calculado **uma vez por versão** da fila + tombstone + `conf_kits` (`_anuncio_pend_linhas`, sobre a leitura guardada `_ml_fila_leitura`
da V20.04; limpo junto em `_ml_cache_leitura_limpar`); o RF1 só recalcula quando a versão mudou. (5) Anúncio `closed`/`inactive`/
`under_review` (`ANUNCIO_ESTOQUE_STATUS_PARADO`) ou com variações (`anuncio_canal_v2023.variacoes`, lido no `GET /items` antes de gravar)
não entra (motivo na tela); `ANUNCIO_ESTOQUE_ERROS_PARAR` (3) erros seguidos no mesmo número param o Automático até o número ou o anúncio
(nova leitura) mudar, e aparecem no Painel de saúde (`anuncios_estoque`, "atenção", só memória). (6) **"Enviar agora" em segundo plano**
(`anuncios_estoque_envio_iniciar`, um por conta, 409 se já roda); o GET devolve `envio` e a tela pergunta a cada 3 s (para em 401/403).
(7) Volta a **Manual** (com linha no registro) quando a puxada ou o envio vê Full, ao remover/reincluir e ao trocar quem controla.
(8) Foto pelo computador/Estúdio pega a `ANUNCIOS_FOTOS_ML_LOCK` sem esperar (409 "Aguarde as fotos do anúncio…"). Prévia: o
PREVIA02 tem atendimento no Faturamento. Teste: `TESTES/test_anuncios_estoque_revisao_v2026.py`.
**V20.27 — depósito do vendedor (ML multiorigem)** (1º uso real, 05/11 20:28, recusado "ajuste no ML"). Formato REAL de
Araçatuba no `GET /user-products/{id}/stock`: sem `selling_address`, um local `{type: seller_warehouse, store_id, network_node_id,
quantity, status: active}` + cabeçalho `x-version`. Com **um** `seller_warehouse` ativo (o `meli_facility`/Full não conta) grava
`PUT /user-products/{id}/stock/type/seller_warehouse` com `x-version` e `{"locations": [{store_id, network_node_id, quantity}]}`
como vieram (`_ml_estoque_wh_gravar`; 409 = relê e tenta 1x, depósito trocado = recusa); relê o estoque do user-product
(`_ml_estoque_wh_reler`) e o anúncio, e só é ok se o depósito mostrar o número. Dois depósitos, depósito inativo ou sem
`store_id`/`network_node_id` = recusa listando cada um (nunca adivinha); `selling_address` + `seller_warehouse` juntos = recusa;
só `selling_address` = como na V20.26. 1ª resposta em `estoque_put_seller_warehouse`. Prévia: o MLB1129555578 tem o formato real e
agora dá certo. Teste: `TESTES/test_anuncios_estoque_deposito_v2027.py`.
**V20.28 — "Este produto é: Peça · Kit"** (dono, 05/11; caso "0893600022-0893764272": dois códigos do Apollo juntos por hífen,
lido como prefixo + código, virava ficha de peça e o "+ Novo kit" era recusado). Seletor no topo da ficha (perfil Admin; os
outros só veem). **Kit** abre a aba **"Composição do kit"** (peça por código ou nome — `GET /api/anuncios/produtos?busca=`,
Precificador + fichas + `conf_produtos` + códigos já lidos do Apollo; código digitado que o sistema não conhece = "código não
encontrado — confira"; quantidade 1..999; subir/descer/remover) e o Salvar grava no **mesmo** Cadastro de Kits com sku_kit = o
código da ficha (`POST /api/anuncios/ficha/tipo {codigo, tipo, componentes, nome, confirmar_produto, versao_storage}` →
`anuncio_ficha_tipo_definir` → `kit_salvar(..., admin_tipo=True)`). Ficha de peça que só a puxada criou vira kit sem pergunta;
código de produto real (Precificador/`conf_produtos`, `_kit_codigo_produto_real`) ou ficha criada como peça pelo Admin
(`criada_por_admin`, gravado no 1º `ficha_salvar`) só com `confirmar_produto: true` — sem ele 400 `codigo_erro:
CODIGO_DE_PRODUTO` (`KitCodigoDeProduto`) e a tela pergunta "Marcar como kit faz TODA venda dele abrir nestas peças...". O "+ Novo
kit" da aba Kits continua recusando (aponta para o seletor). A trava da fila para tirar/pôr/reordenar peça continua. **Peça** num kit
= `ativo: false` no Cadastro (nunca apaga; recusado com pedido do kit na fila, `_kit_desativar`); a ficha mostra "kit desativado" e
"Kit" de novo religa com as peças antigas. A escolha fica em `anuncio_ficha_v2023.tipo_confirmado_por/em`; a puxada
(`_ficha_garantir_kit` com ator `importacao:`) nunca desfaz. Mudou o tipo = anúncios da ficha em Automático voltam a Manual (linha no
registro). **Peça confirmada = 1 × o código INTEIRO** no estoque e no pendente (regra única em `_anuncio_sku_partes`, conjunto `_anuncio_pecas_confirmadas` na chave da leitura guardada do pendente): "0893600022-0893764272" vai assim ao Apollo e a fila desse SKU desconta dele, nunca a leitura antiga que fica só com "0893764272"; sem confirmação continua como antes (selo + sem Automático). **Selo "kit? confirme a composição"**
(`anuncio_sku_parece_kit`, heurística conservadora): exatamente um hífen, cada lado só 0-9/A-Z/ponto com **7+ caracteres e 5+
dígitos** (o prefixo de anúncio "151490-"/"157507-" tem 5–6 e fica fora; "KIT…" e "A/B" também), sem kit no Cadastro e tipo não
escolhido. Na lista, na ficha e na linha do anúncio; o **Automático é recusado** até o Admin escolher (e o anúncio que já estava em
Automático não envia: motivo "confirme na ficha se é Peça ou Kit"). **Revisão (05/11):** o código inteiro vale **só** para o SKU com cara de dois códigos (`anuncio_sku_parece_kit`): "151490-0893270212"
continua em 0893270212 mesmo confirmado, e "Peça" numa peça comum (sem selo, sem kit no Cadastro) não muda nem grava nada
(`sem_mudanca`). Criar ou religar o kit com venda do código na fila em linha comum (SKU = o kit, `_kit_codigo_na_fila`) é recusado
("marcar como kit só depois que o pedido sair"; KITn-X fica fora). Código que é peça de outros kits (`kits_da_peca`) também pede a
confirmação (`confirmar_produto`, lista os kits). A busca de peças guarda os candidatos 60 s (`_anuncio_produtos_candidatos`,
refeita quando muda Precificador, `conf_produtos`, ficha ou leitura do Apollo). Prévia: MLB8000000007 com esse SKU; `PREVIA_PORTA` sobe uma 2ª
cópia. Teste: `TESTES/test_anuncios_kit_ficha_v2028.py`.

**V20.42 — Apollo relido em lote** (Guilherme, 09/11: a Linx não tem "o que mudou desde" nem aviso; pedido em
`PEDIDO_PORTA_ESTOQUE_MUDANCAS.md`). `POST /api/v1/expedicao/estoque/lote {codigos (até 500, código até 30), revendas,
operador}` devolve `itens[{codigo, revenda, disponivel, tem_cadastro, erro}]`, lido na hora, o mesmo número da porta
`estoque`; limite de **6/min separado** dos 20/min. `anuncios_apollo_lote` (no começo de cada volta do
`anuncios_estoque_loop`, no máximo 1 por `ANUNCIO_APOLLO_LOTE_S` = 60 s, até `ANUNCIO_APOLLO_LOTE_POR_MINUTO` = 5
chamadas): todos os códigos em Automático das duas filiais (Shopee/Magalu = Araçatuba, TikTok = Bauru) **e** os do TikTok
pela extensão (`_tiktok_extensao_le_apollo`, mesmo sem anúncio em Automático), grava nas mesmas linhas do rodízio
(`anuncio_apollo_v2026`, `tiktok_apollo_v1964`) e marca a filial que mudou. `disponivel`/`tem_cadastro` null ou `erro`
= None (nunca zero); `tem_cadastro: false` = 0; código pedido que não voltou = None; `ok` diferente de true ou erro =
nada gravado daquela chamada. **O rodízio de 3/min virou reserva:** só roda sem lote completo nos últimos
`ANUNCIO_APOLLO_LOTE_RESERVA_S` (180 s) (`apollo_lote_em_dia`), e com o lote em dia só cobre o código maior que 30
(`apollo_lote_fora`); o mesmo no `tiktok_estoque_loop` (`apollo_lote_em_dia(tiktok=True)`). Porta 404 = tenta de novo
em 10 min; o mesmo erro vai ao log uma vez. A trava de queda (pedido concluído) solta na leitura do minuto seguinte.
Tela: linha "Apollo relido a cada minuto" ou o aviso de lote fora no painel Estoque automático (`apollo_lote` no
`GET /api/anuncios/estoque`). Teste: `TESTES/test_apollo_lote_v2042.py`.
**revisar-antes-de-subir (09/11, "corrigir os 5"):** (1) peça com `tem_cadastro: false` em **todas** as casas pedidas = None
(a porta `estoque` responde `ok: false` e o rodízio gravava None: SKU errado, Peça confirmada com dois códigos, peça errada
no kit) — nunca 0, que pausaria o anúncio; sem cadastro só numa casa continua 0. (2) Código que voltou sem número (ou não
voltou) entra no `apollo_lote_fora` e o rodízio cobre; o código da resposta casa pelo `normalize` e, sem isso, pelo
`_exp_norm_codigo` (sem pontos); sem número não apaga uma leitura boa dos últimos 180 s; TikTok com algum código sem
número não fica "em dia". (3) Tudo vai à porta pelo `normalize` (o SKU do TikTok tinha espaço); 422 divide a chamada ao meio
até achar o código recusado, que fica fora do lote por 1 h (`ANUNCIO_APOLLO_LOTE_RECUSADO_S`) e vai pelo rodízio;
`PortaErro.status` = código HTTP (o 404 não é mais achado no texto). (4) `lido_epoch` = a hora de **antes** da chamada
(senão a trava de queda soltava cedo) e o lote nunca grava por cima de leitura mais nova (o rodízio leu depois), também no
TikTok. (5) O lote roda numa thread (`anuncios_apollo_lote_fundo`, um por vez, `_APOLLO_LOTE_RODANDO`); a volta espera no
máximo `ANUNCIO_APOLLO_LOTE_ESPERA_S` (15 s) e a filial que mudou depois entra na volta seguinte (`_APOLLO_LOTE_MUDANCAS`).
**2ª revisão (só das correções, "corrigir os 3"):** a busca do código recusado continua no minuto seguinte
(`_APOLLO_LOTE_ESTADO["suspeitos"]`; antes, com mais de ~16 códigos, as 5 chamadas não bastavam e recomeçava do zero); com
só a busca pendente o lote conta como em dia e o aviso diz "procurando qual". O lote pede **sempre as três casas**
(`APOLLO_LOTE_REVENDAS` = 1, 2, 3): "não existe no Apollo" não depende de quem tem Automático (peça só em Bauru ou só em
Ribeirão anunciada em Araçatuba = 0). Sem número por cima de sem número não grava (a hora fica a da 1ª vez, o rodízio chega
nele); peça que não existe em nenhuma casa não vai para o rodízio nem tira o TikTok de "em dia". 3ª revisão: o 0 de "sem
cadastro nesta casa" só vale com outra casa confirmando o cadastro — nenhuma confirma e alguma não respondeu (Ribeirão fora)
= None em todas e vai para o rodízio, nunca 0; o pedaço da busca do recusado volta para a fila quando a chamada falha; no
TikTok o motivo ("não existe no Apollo") é atualizado mesmo quando a hora fica.

### Estúdio de Imagens (V20.17: servidor, tela e agente)
`APP/imagens.html` (`/imagens`, menu Vendas e anúncios, módulo próprio **`imagens`** = `app_users.can_imagens`, mesma
regra do Devolução: Admin nasce ligado, salvar sem a chave mantém; Consulta só vê; Compras não tem). Decisões do dono
(04/11; desenho em `docs/superpowers/specs/2026-11-04-estudio-imagens-design.md`): o operador sobe a foto e recebe a
**foto principal** (1200×1200, fundo branco) ou a **arte** (benefícios, modo de uso, kit juntos, aplicação). Quem
processa é o **agente no PC do dono** (Real-ESRGAN + Claude Haiku 5.5 pela API; fase 2, ainda não feito); a VPS só
guarda arquivos e a fila — **nada pesado roda aqui**. Fotos em `IMAGENS_DIR` (`/home/expedicao/dados/imagens/<id>/`),
**nunca no banco**; tipo pelo conteúdo (jpg/png/webp), 10 MB por foto, 20 por envio (principal = um trabalho por foto;
kit = 2 a 8 fotos num trabalho; artes = frente + verso). Tabela `imagem_trabalho_v2017` (situação, arquivos, resultados,
textos, relatório, tokens, `reservado_ate`, `versao`, `apagado_em`) e `imagem_config_v2017` (token do agente, modelo,
pulso). Situações: aguardando → processando (principal) | analisando → textos_para_aprovar → aguardando_arte → gerando →
pronta | revisar | outra_foto | erro. **Arte só é gerada depois que o operador aprova os textos** (o Claude propõe só o
que leu no rótulo; o resto vem em branco). O agente pega um trabalho por vez (`/api/imagens/agente/proximo`, reserva de
10 min; vencida volta à fila, 3 vezes = erro) e entrega por `/agente/{analise,resultado}`; resultado de trabalho
apagado/refeito = 409. Token do agente: `Authorization: Bearer`, tabela própria (não sai pelo `/api/storage`), lido em
`GET /api/imagens/agente/config` (perfil Admin), trocado em `/agente/token-novo` (OWNER). **Nada é apagado sozinho**: o
operador apaga (some a pasta, a linha fica de histórico); acima de `IMAGEM_AVISO_BYTES` (500 MB) a tela fica amarela.
Modelo (`claude-haiku-5-5` padrão, ou `claude-opus-5-5`) muda só o perfil Admin. A gaveta mostra **sempre** os recortes
de zoom dos textos (o Haiku pode deixar passar letra deformada). Painel de saúde `imagens_agente`: atenção só com
trabalho na fila e agente sem pulso há > 2 min (PC desligado à noite é normal; sem e-mail). Rotas
`GET /api/imagens[/arquivo]`, `POST /api/imagens/{enviar,textos,refazer,apagar,config}`. Teste:
`TESTES/test_imagens_v2017.py`.
**Agente** em `FERRAMENTAS_WINDOWS/agente_imagens/` (Python + Pillow + SDK `anthropic`; `config.env` fora do Git com
`ANTHROPIC_API_KEY` e `NOROMAK_AGENTE_TOKEN`; LEIA-ME lá). Principal: Claude analisa (serve? fundo branco? produto
claro?) → Real-ESRGAN se < 1000 px (`-t 64`: a placa daqui derruba o driver com bloco maior; ~40 s por foto) → foto em
fundo branco liso NÃO é recortada (caixa do que não é branco) → conferência em 6 faixas (ampliação comum em cima × IA
embaixo; faixa deformada volta para a comum, até 2 voltas, depois `revisar`). **Kit juntos sai sem texto** (itens lado a
lado em fundo branco, foto principal do kit): o servidor o manda pela etapa `principal`. Artes: `propor_textos` (campos
fixos por tipo em `CAMPOS_ARTE`; o que não está no rótulo vem vazio) → aprovação → `artes.py` monta o HTML (produto em
fundo branco com `mix-blend-mode:multiply`) → Chrome headless → `conferir_arte`. Todas as chamadas com
`output_config.format` (JSON de esquema fixo) e `effort: low`. Teste com o Claude simulado:
`python -m unittest test_agente` na pasta do agente (fora do `testar.sh`: precisa do Pillow). Conferido com a chave real
(04/11): o Haiku achou o "Hidrata" deformado e o agente refez a faixa (US$ 0,0022 a foto).
**Crédito da API** (dono, 04/11): US$ 100 que renova todo dia 19 (`saldo_valor`/`saldo_dia` em `imagem_config_v2017`,
perfil Admin muda pelo `POST /api/imagens/config`). A API não informa saldo para a chave comum: cada entrega do agente
grava uma linha em `imagem_gasto_v2017` (tokens × `IMAGEM_PRECOS` + `buscas` × `IMAGEM_PRECO_BUSCA` US$ 0,01); a barra
soma do último dia 19 até a renovação (`_imagem_ciclo`, dia 31 vira o último do mês). Só conta o uso do Noromak;
apagar a imagem não devolve crédito. **Imagem de referência** (só nas artes, `papel: referencia`): o Claude propõe os
textos na estrutura da referência (`propor_textos_referencia`: título + até 8 itens "rótulo | valor", nunca copia dados
da referência) e escreve o HTML imitando o layout (`gerar_html`, esforço medium); o HTML passa por `artes.limpar_html`
(sem script/evento/iframe/endereço de fora; só fontes do Google; tem de usar `produto.png`) e, se a conferência achar
problema, é refeito uma vez com a lista de problemas. **V20.19 — várias referências** (dono, 05/11: "uma arte por
referência"): a tela aceita até `MAX_REFERENCIAS` (10) e faz **um envio por referência**, com as mesmas fotos, nome e link;
o servidor e o agente não mudaram (uma referência por trabalho). Com mais de uma, confirma antes (cada arte gasta crédito);
a referência já enviada sai da lista na hora, e erro no meio deixa só as que faltam (tentar de novo não duplica).
**V20.21 — print de catálogo** (dono, 04/11; caso 816.4003-0: o print do catálogo da Schulz foi como 2ª referência e virou uma 2ª arte copiando a tela do catálogo, e a arte com referência azul saiu cinza): campo próprio "Print de catálogo / ficha técnica" (só artes, até `IMAGEM_MAX_CATALOGO` 4, entra na soma de 25 MB), papel `catalogo` no MESMO trabalho (não multiplica artes), vai em todos os envios das várias referências. O agente só lê os dados para os textos (origem `catalogo`, "lido no catálogo — confira os códigos"; vale igual ao rótulo, acima da internet) e nunca usa como foto do produto nem layout. `gerar_html`: cores da referência em tudo, foto de fundo branco num cartão claro (antes "fundo claro atrás" deixava a arte cinza); produto SEM FUNDO na arte (`recortar_fundo_branco`, inclusive o furo; recorte que vaza para dentro do produto branco, > `SEM_FUNDO_MAX_VAZAMENTO` 2%, volta ao retângulo com multiply); arte pela referência sem título por padrão. Campo **"Como você quer a arte"** (`pedido`, até 500, só artes, em `produto_json`) vai para os textos e o layout junto com o "Pedido ao refazer" (`obs`, que antes só chegava na ampliação e a arte ignorava); as regras (não inventar dado, não alterar o produto) valem mais que o pedido. **V20.20 (revisão de 04/11):** enquanto o envio anda (`enviando`), o botão Enviar fica desligado, tipo/SKU/nome/link
travados, e ×, Limpar e acrescentar fotos ou referências respondem "Aguarde terminar o envio" (`ocupado()`). Antes,
tirar a referência já enviada religava o botão no meio do laço e o 2º clique duplicava a arte. **Nome do produto / link do fornecedor** (só nas artes,
`produto_json`): antes dos textos o agente pesquisa na internet (`pesquisar`: web_search + web_fetch, até 3 buscas; a
versão `_20260209` é a documentada para o Opus, o Haiku começa pela básica e troca se a API recusar; `pause_turn`
continua) e a ficha de fatos com URL entra na proposta: campo da internet = `origem: internet` + `fonte` (tela: "da
internet — confira" com o link); rótulo vale mais que a internet; pesquisa que falha não trava os textos
(`aviso_pesquisa`). As fontes costumam ser anúncios de outros vendedores: o operador confere.

### Quadro de tarefas (V19.69)
`APP/tarefas.html` (`/tarefas`, menu Visão geral, **todo usuário logado**: a página só exige sessão, o item do menu
não tem `modulo`). Decisões do dono (21/10): 4 colunas fixas (`TAREFA_COLUNAS`: a_fazer, andamento, aguardando,
concluido; Iniciar grava `iniciado_em`, Concluir `concluido_em`/`concluido_por`); Plano (`tarefa_plano_v1969`) agrupa
cartões (`tarefa_cartao_v1969`: área, prioridade, prazo, responsável, filial só de filtro, checklist em JSON);
histórico/comentários em `tarefa_evento_v1969`. Criar/editar = módulo admin (Admin e Supervisor, `tarefa_gestor`);
mover, checklist = o responsável ou gestor; comentar = qualquer logado; excluir/restaurar (lixeira, `excluido_em`) só
perfil Admin; Consulta só lê (`consulta_bloqueada`). Edição manda `versao`: outra mais nova = 409 `CONFLITO` com o
cartão atual. Concluído há > 14 dias sai do quadro para Arquivados (calculado na leitura; mover para A fazer reabre).
Carga inicial uma vez (plano com chave única `faturamento-out26`, cartão excluído nunca volta). Selo no menu
(`/api/tarefas/resumo`, vermelho com atrasada) e linha das atrasadas no e-mail das 08:00 (`tarefas_resumo_email`).
Rotas `GET /api/tarefas[?arquivados=1|lixeira=1]`, `/api/tarefas/{resumo,cartao}`,
`POST /api/tarefas/{cartao,plano,mover,checklist,comentar,excluir,restaurar}`. V2 (a fazer): cartões automáticos de
Compras (zerados, vai zerar, demanda, projeção). Teste: `TESTES/test_tarefas_v1969.py`.

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
**V20.01 — a tela voltou, com Araçatuba e Bauru** (decisões do dono, 29/10; a V19.76 tinha apagado porque a
integração ainda dava 403). `APP/posvenda.html` (`/posvenda`, menu Vendas e anúncios, módulo **admin**; Consulta só vê).
Pergunta e reclamação guardam a `conta` (coluna nova, o que existia = Araçatuba); `posvenda_rodada_contas` roda
perguntas/reclamações/reputação **em cada conta conectada** dentro de `ml_na_casa` (o detalhe da reclamação e a releitura
da pergunta só na fila da conta, com o token dela); cada conta tem o seu ponto de partida da busca e o seu estado
(`_POSVENDA_ESTADO["contas"]`; "ultimo"/"perguntas" seguem sendo da principal). Reputação por conta em
`ml_reputacao_conta_v2001` (a antiga copiada como Araçatuba). **Responder** (`posvenda_responder`): pela conta dona da
pergunta, com saudação/assinatura da conta (`posvenda_config_v2001`, `saudacao@conta`/`assinatura@conta`, entram uma vez
só; o limite de 2.000 do ML vale para o texto final) e uma pessoa por vez (`_POSVENDA_RESPONDENDO`); respondida por aqui
não volta a "sem resposta" pela busca atrasada do ML. Respostas prontas em `posvenda_resposta_pronta_v2001`
(`POST /api/posvenda/respostas`, `/config`). Tela: filtro Todas/Araçatuba/Bauru (`nk_posvenda_conta_v2001`), anúncio pelo
retrato diário (sem chamar o ML; Bauru sem retrato), respostas anteriores do mesmo anúncio, conta sem permissão aparece
"sem permissão (403), reconectar", **devoluções a caminho** (reclamação com envio de volta, 60 dias) com "Chegou" quando a
peça está em `devolucao_v1988` pelo nº do pedido. O **relatório de perdas saiu da tela** (a perda fica no Financeiro,
V19.95; `?relatorio=1` ainda o devolve). **Avisos:** pergunta nova vira aviso `marketplace='pergunta'` (popup/push dos
Avisos de venda, abre `/posvenda`; 1ª leitura de cada conta só marca, `base_pergunta@conta`; > 48 h calada). Selo no
menu (`GET /api/posvenda/resumo`, vermelho com pergunta > 1 h). Painel de saúde: prazo < 24 h das duas contas, com o nome
da conta e o botão "Abrir Pós-venda". O Auditor de cobranças continua lendo só as reclamações de Araçatuba.
**Revisão 29/10:** a proteção da resposta dada por aqui vale só `POSVENDA_PROTEGE_RESPOSTA_MIN` (15 min): depois, o
"sem resposta" do ML (resposta apagada pela moderação) devolve a pergunta à fila; as perguntas de todas as contas são
lidas antes das reclamações; a trava das reclamações é por conta (`_posvenda_trava_conta`) e o "Buscar agora" é um por
vez (`POSVENDA_MANUAL_LOCK`, `ja_estava`); selo e Painel de saúde contam só as contas conectadas.
**V20.02 — botões de resposta** (dono, 29/10, pelo print de outra ferramenta): as respostas prontas viram botões de um
clique em cima da caixa (o seletor saiu; o texto entra onde está o cursor), botão "Bom dia/Boa tarde/Boa noite" pela
hora de Brasília e caixa "Salvar como resposta pronta" (guarda o texto digitado depois de enviar). Saudação/assinatura
automáticas continuam. Nome do comprador e link "Meus produtos" ficaram de fora (o ML só dá o apelido).
Teste: `TESTES/test_posvenda_contas_v2001.py`.
**V19.76: a tela foi apagada (dono, 22/10).** A rotina `posvenda_loop` e as tabelas continuam: o Auditor de
cobranças usa as reclamações (frete de devolução) e o Painel de saúde o prazo do vendedor (sem botão de tela).
Era `APP/posvenda.html` (`/posvenda`, menu Vendas e anúncios, módulo **admin**; `GET /api/posvenda?dias=30`,
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
**V19.59 — relatório semanal e mensal:** quadro "Venda perdida por semana e por mês" na mesma tela
(`GET /api/ruptura/relatorio?marketplace=`, `ruptura_relatorio_payload`): 8 semanas (segunda a domingo) e
6 meses, a mesma conta do perdido feita período a período, com o ritmo dos 60 dias que terminam no fim de
cada período. Semana/mês atuais vão até ontem (`parcial`); período que começa antes da 1ª checagem sai
`coleta_completa=False`. Encerrado fica fora. **Exportar Excel**: `GET /api/ruptura/relatorio.xlsx` (`ruptura_relatorio_xlsx`, `_xlsx_abas`): abas Semanal, Mensal e Anúncios (todo anúncio que perdeu em cada período). **V19.61:** clicar numa linha do quadro abre a gaveta com todos os anúncios que faltaram no período (`?completo=1` traz `itens` com SKU e link). **V20.03:** o Excel da gaveta é só daquele período (`relatorio.xlsx?tipo=meses|semanas&inicio=`; uma aba com anúncio, SKU, título, dias, unidades, perdido e link; o botão do quadro continua com o relatório geral). Teste: `TESTES/test_ruptura_relatorio_v1959.py`.
**V20.05 — relatório completo** (dono, 30/10): o "Exportar Excel" do quadro traz antes das abas Semanal/Mensal/Anúncios as 6 do relatório feito à mão em 30/10 (Resumo, Ações, Perto de acabar, Zerados, Maiores perdas, Marcas) e o botão "Folha A4" abre `/api/ruptura/a4` para imprimir. `ruptura_completo_payload`: ML + Shopee do mês até ontem, faturamento contra os mesmos dias do mês anterior, guardado 2 min. **Nunca consulta o Apollo** (a leitura em lote travou o sistema em 30/10): usa só `compras_estoque_v1912` (o `estoque_apollo` da remessa é saldo depois dos anúncios anteriores, não serve); kit = peça que tem menos; sem leitura = em branco e ação "Sem leitura". A caminho = maior entre `_full_a_caminho`, o `a_caminho_ml` da remessa aberta e o `a_caminho` do `fora_json` dela (item que o a caminho cobre não ganha linha). Ação: Comprar (0), A caminho (0 + a caminho, ou Full com a caminho), Ajustar anúncio ou Enviar ao Full (tem peça). Um cálculo por vez (`RUPTURA_COMPLETO_LOCK`, sempre antes do `DB_LOCK`). Perfil Compras: folha sem faturamento, pedidos e ticket; Excel sem "% do faturado". `_xlsx_abas` ganhou `opcoes` por aba (larguras, cabeçalho fixo, filtro) e os estilos 5–9. A folha "Zerados e meta do mês" não entrou. Teste: `TESTES/test_ruptura_completo_v2005.py`.
**V19.86 — filtro por marca** (dono, 26/10, para o Compras): marca = fornecedores da Demanda de Compra (regras +
marca escolhida à mão, `_compras_regras_marca`), pelo mesmo caminho da demanda (kit vira peças, "A/B" uma de cada; o
anúncio entra em cada marca das peças; sem fornecedor = "Sem marca"). `ruptura_payload` devolve `itens[].marcas` e
`marcas` (só as que têm anúncio). Na tela o seletor fica no topo; cartões e abas contam só a marca; a escolha fica no
navegador (`nk_ruptura_marca_v1986`). O relatório semanal/mensal não filtra. Revisão 26/10: SKU fora do cadastro de kits passa
por `_separacao_partes` ("PSH367/80" que é um produto fica inteiro); a marca só é calculada com `com_marcas=True`
(a rota da tela — as rotinas de promoção chamam `ruptura_payload` e não usam); falha na leitura volta `marcas_erro`
e a tela avisa. Teste: `TESTES/test_ruptura_marca_v1986.py`.

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

### Promoções automáticas da Shopee (V19.49)
Mesma tela `/promocoes-auto`, escolha **Mercado Livre / Shopee** no topo (`?mp=shopee`). Decisões do dono (16/10,
entrevista): o ADMIN **marca cada anúncio**; quando faltam `SHOPEE_PROMO_RENOVA_ANTES` (3 dias) para a promoção
acabar, o sistema cria a próxima pela API `v2.discount` (`add_discount` + `add_discount_item`), **começando no segundo
em que a atual termina** (a Shopee exige início ≥ 1 h no futuro, por isso a antecedência), com `duracao_dias`. Os
anúncios que vencem no mesmo horário vão para **uma promoção só** ("Noromak auto DD/MM"; reaproveita a que já
criamos com o mesmo início, `shopee_promo_criada_v1949`). Regras = as do ML (margem mínima, duração, estoque); **modo
e limite do dia próprios** (`modo_renovacao@shopee`/`limite_dia@shopee` em `promo_auto_config_v1873`, lidos por
`promo_auto_config("shopee")`), começa em Simulação, Automático exige OWNER. Margem pela tabela de faixas do
Precificador copiada para o servidor (`SHOPEE_FAIXAS_TAXA` = `SHOPEE_BANDS`) — **V19.50:** tabela atual da Shopee (16/10): até R$ 79,99 = 20% + R$ 4,50; R$ 80–99,99 = 14% + R$ 16; R$ 100–199,99 = 14% + R$ 20; ≥ R$ 200 = 14% + R$ 26 (mudou a Shopee: trocar nos TRÊS lugares — Precificador, servidor e `FAIXAS` da gaveta em `promocoes-auto.html`; `TESTES/test_shopee_taxa_adicional_v1998.py` prende os três). **V19.98:** + "Taxa de serviço adicional" de **3,5%** do preço em toda faixa (`SHOPEE_TAXA_ADICIONAL`, somada à comissão: 23,5% / 17,5%), conferida centavo a centavo em 5 pedidos de 28/10 (a Water Off pagou 5%; dono: 3,5% fixo). Os 20% da Shopee aparecem como comissão 18,6% + transação 1,4%; os R$ 4,50 são por unidade e a faixa é pelo preço unitário; cupom da loja reduz a base, frete Shopee do Precificador, imposto e
custo pela regra do ML (`_promo_produtos`/`_promo_produto`, sem o vínculo MLB↔SKU). **Nunca** altera nem encerra
promoção que não criou; anúncio com promoção **agendada** não ganha outra por cima. **Variação a variação**
(`shopee_promo_modelo_v1949`, model_id "0" = sem variação): entram as que estavam na promoção, cada uma com o seu preço
e a sua trava; sem custo fica de fora (nunca custo zero). Estoque lido ao vivo (entra no histórico da Ruptura da
Shopee). Recusa por campanha da Shopee fica "revisar" e é tentada de novo na rodada seguinte. Worker
`shopee_promo_loop` (1 h). Tabelas `shopee_promo_v1949`, `_modelo_`, `_removidos_` (tombstone; marcar desfaz),
`_criada_`, `_log_`, `_amostra_` (**1ª resposta real** de cada chamada, `GET /api/promocoes-auto/shopee/amostra`, perfil
Admin — **conferir antes de ligar o Lote**: a API foi construída pela documentação, sem resposta real). Rotas
`GET /api/promocoes-auto/shopee` e `POST /api/promocoes-auto/shopee/{marcar,precos,remover,buscar-novos,renovar,checar,config}`.
"Buscar novos" lê o retrato diário (`shopee_item_v1890`), sem chamar a Shopee. Convites de campanha da Shopee ficam
para depois. Teste: `TESTES/test_promo_shopee_v1949.py`.

### Promoções automáticas da Magalu (V19.53 a V19.55)
**V19.53/54 — teste de leitura** (botão "Testar promoções (só leitura)" no cartão Magalu, `GET /api/integracoes/magalu/teste-promocoes`,
perfil Admin, só GET; respostas em `magalu_amostra_v1926` tipo `promo_*`). Formato real lido em 18/10: promoção `type.id=absolute_discount`
("Preço Promocional"), `origin` self_service|channel, `validity.starts_at/ends_at` (UTC), `benefits.total` 100..6000 (desconto de 1% a 60%),
`channel.id`; SKU da promoção com `price.original/promotional.value` (centavos); preço do anúncio em `/portfolios/prices/{sku}`
(`list_price` = "de", `price` = "por"). **A lista `/seller/v1/promotions` sem filtro só traz as que COMEÇAM nos próximos 30 dias**: sempre
mandar `start_date_gte`/`start_date_lte` (`_magalu_janela_promos`). O token da loja já tem os escopos de promoção e de preço.
**V19.55 — aba Magalu** na mesma tela (`?mp=magalu`; a seção `#shp` e a gaveta servem Shopee e Magalu: `URLB()`, `NOME()`, `.mpn`,
`.so-shopee`/`.so-magalu`). Mesma régua (`shopee_promo_avaliar` com `perfil`: nome, início mínimo e a conta de taxas), mesmo agrupamento
por início ("Noromak auto DD/MM"), renova 3 dias antes. Taxas = Precificador `marketplaces.magalu` (comissão, taxa fixa, frete);
**comissão 0 = sem taxa cadastrada → não renova** (metade dos produtos tem 0 no Precificador). Campanha da Magalu (`origin=channel`,
ex. Feirão) = "em campanha": espera acabar e o preço dela **não** vira o nosso. Desconto fora de 1–60% = revisar. Estoque não vem do
catálogo: fica desconhecido (não trava). SKU `BLOCKED` = parado. Criação (`_magalu_promo_criar`): `POST /seller/v1/promotions`
(name, type, scope seller_choice, validity, channel) → `PUT /{id}/skus/{sku}` (`price.promotional`) → `POST /{id}/apply`; **corpo
deduzido da leitura, ainda não confirmado** — cada 1ª resposta/erro vai para a amostra (`promo_criar*`). A 1ª criação real é com o
dono, em 1 SKU, no Lote. Tabelas `magalu_promo_v1955` (`_removidos_`, `_criada_`, `_log_`), modo `modo_renovacao@magalu`, worker
`magalu_promo_loop` (1 h). Rotas `GET /api/promocoes-auto/magalu` e `POST .../magalu/{marcar,precos,remover,buscar-novos,renovar,checar,config}`
(o mesmo bloco da Shopee). "Buscar novos" lê o catálogo `/seller/v1/portfolios/skus`. Testes: `test_magalu_promo_teste_v1953.py`,
`test_promo_magalu_v1955.py`.

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
**V20.00 — demanda pela porta do NoromakWeb** (ligada pelo Guilherme em 28/10, `TEIL_EXPEDICAO_DEMANDA=1` no lado
dele). `GET /api/v1/expedicao/demanda?codigos=&meses=12&operador=` (`_exp_porta_get`), em lotes de 200, devolve uma
linha por (peça, filial, mês **fechado**) com `DemandaBalcao/Oficina/Telemark/Total` e `OcorrenciaVendas`, mais
`sem_historico`, `com_erro` e `pendentes` (ainda na fila de leitura dele: a rodada pergunta de novo a cada 20 s, até
9 vezes; o que sobrar fica para a próxima). Guardada em `linx_demanda_filial_v2000`; erro não apaga a leitura anterior.
Decisões do dono (29/10): **filial escolhida na tela** (`filial=1|2|3|todas`, padrão Araçatuba, `nk_compras_filial_v2000`)
e o **mês corrente entra pelo e-commerce** (ML + Shopee do mês até hoje, da conta da filial: Araçatuba `vendas_v1799`,
Bauru `vendas_conta_v1898`, Ribeirão nenhum), somado no total. **Revisão 29/10:** a média **nunca fica abaixo da dos
meses fechados** — `max(Linx ÷ dias dos meses, (Linx + e-com) ÷ (dias + corridos))`; sem isso a peça de balcão caía pela
metade (o mês atual não tem o balcão). Na **virada do mês** a âncora (`lido_dia`) é o mês seguinte ao **último mês que a
resposta da porta trouxe** (não o dia da leitura): até a Linx trazer o mês que fechou, ele entra pelo e-commerce (antes: demanda ~0 em todas as peças no dia 1º). A coluna/reserva
E-commerce também é da conta da filial. A amostra guarda a 1ª resposta **com linhas** (a 1ª vem toda em `pendentes`) e a
1ª com `com_erro`. A demanda da Linx **inclui a transferência
entre filiais** (em "Todas" conta duas vezes; aviso na tela). Telemarketing veio zero em tudo; o e-commerce cai em Balcão.
A compra em aberto continua pela Smart API (`linx_compra_aberta_item`, `caminho_ok`) só se ela estiver configurada; sem
ela "A caminho" é digitado (nunca 0 falso). Teste: `TESTES/test_linx_demanda_porta_v2000.py`.
**V20.02 — mês em andamento da Linx** (Guilherme, 29/10, tarefa #452). A rodada pede `incluir_mes_atual=1`: vem uma
linha por (peça, filial) do mês corrente com `parcial: true`, `ate` (último dia contado, normalmente ontem) e
`DemandaEcommerce` (ML Full M21 + balcão P21 dos vendedores 134006, 43 e 66), já **fora** do `DemandaBalcao`. Guardada
em `linx_demanda_filial_v2000` (`parcial`, `ate`, `ecommerce`); a peça guarda o mês coberto (`mes_atual`, `mes_atual_ate`;
resposta sem linha parcial = não coberto, peça coberta sem linha da filial = 0). Mês atual = balcão + oficina +
telemarketing da Linx + o **nosso** e-commerce do mês (a coluna `DemandaEcommerce` **nunca** é somada). Com o mês coberto,
a média é a conta honesta (mês fraco puxa para baixo; decisão do dono, 29/10); a trava "nunca abaixo dos meses
fechados" só vale sem ele. A âncora (`lido_dia`) sai só dos meses fechados; mês parcial de outro mês é ignorado. Peça
em `com_erro` com o parâmetro é perguntada de novo **sem** ele (vêm os fechados). O mês aberto vale só no dia da leitura
(no dia seguinte a peça volta uma vez em `pendentes`); dia 1º não vem. Amostra `porta_demanda_mes`. A conferir: o
vendedor 68 (contado como balcão). **Revisão 29/10:** o mês só conta como coberto com `mes_atual_ate` de até 2 dias
atrás (leitura velha = trava de volta; senão os dias sem leitura entravam na média com balcão zero); peças, meses
fechados e mês em andamento lidos num `DB_LOCK` só (o e-commerce fora dele); a amostra do mês guarda só as linhas do
mês (até 15), achadas na resposta crua (chave `parcial`/`ate`/`DemandaEcommerce` ou mês corrente) — a resposta inteira
cortada em 6.000 caracteres ficava só com os meses fechados.

**V20.44 — demanda por GRUPO do Apollo** (dono, 09/11; portas do Guilherme de 09/11). No Apollo **grupo = fabricante**
(TECFIL, WEGA), **marca = NOROMAK em tudo** (fica fora) e **categoria = tipo** (111 filtros, 15 óleos/graxas). A tela tem **dois
filtros**: o Fornecedor (como já era) e **Grupo + Categoria** (`filtroGrupo`/`filtroCategoria`, vários por vírgula; o fornecedor
guarda `grupos`/`categorias` e a tela abre com eles). Com grupo/categoria valem **só as peças do Apollo** + a lista de códigos do
fornecedor + as marcadas à mão para ele (começo de código/palavra ficam fora). **Só o que vendeu** (dono: "para não sobrecarregar"):
a tela mostra só quem tem venda na demanda lida (Linx, qualquer filial) ou no e-commerce; peça lida **sem venda** não é perguntada de
novo por `APOLLO_SEM_VENDA_REVER_DIAS` (30); o estoque (`POST estoque/lote`, ≤ 500 por chamada, revendas 1/2/3, gravado em
`compras_estoque_v1912` no formato da geral; sem resposta = desconhecido, nunca zero) só é lido de quem vendeu. Lista do filtro:
`GET pecas?grupo=&categoria=&pagina=&por_pagina=500` (cópia da madrugada, 60/min) em `apollo_peca_v2044`, trocada só com todas as
páginas (erro não apaga; > `APOLLO_PECAS_MAX` 5000 = recusado, "escolha a categoria"); `apollo_peca_filtro_v2044` guarda leitura,
`sem_categoria_lida` (> 0 com categoria = "lista incompleta" na tela), erro e uso. Filtro nunca lido começa a ler sozinho ao abrir;
"↻ Puxar do Apollo" (`POST /api/compras/apollo/puxar`, gestor; 409 se já roda) relê em 2º plano; à noite o `linx_demanda_loop` relê
lista+estoque dos filtros usados em 30 dias e a demanda deles. `GET /api/compras/apollo/filtros` (grupos e categorias, 1 h em
memória). Teste: `TESTES/test_demanda_grupo_v2044.py` (classe `Revisao`).
**revisar-antes-de-subir (09/11, "corrigir todos"):** só o gestor (não Compras/Consulta) marca o filtro em uso e dispara a 1ª
leitura (`pode_iniciar`); grupo tem de existir em `pecas/grupos` e categoria é código (`apollo_filtro_validar`, até 10 de cada;
também no puxar e ao salvar o fornecedor); filtro com erro há menos de 30 min não é relido sozinho (sem laço de 15 s). O estoque em
lote passa pela **mesma conta de chamadas da V20.42** (`_APOLLO_LOTE_CHAMADAS`, deixando 1 vaga por minuto aos anúncios; 1 lote
por minuto, espera a vaga ≤ 90 s), lê pela regra dela (`_apollo_lote_ler_itens`), código > 30 caracteres fica fora, 422 divide o
lote, resposta sem `itens` não grava nada e código sem nenhuma casa lida não apaga a leitura anterior. Lista vazia ou < 90% do
`total` não substitui a guardada. Marca à mão para outro fornecedor vence o grupo (sem compra dobrada). "Ainda sem leitura da
demanda" é contado à parte (`nao_lidas`), não como "sem venda"; Linx ocupada no Puxar = erro, não "lido". À noite o estoque lido há
menos de 20 h não é relido e a rodada da Linx para às 6h (`noite=True`, `_linx_noite_acabou`). Fornecedor com grupo tem as peças do
Apollo também na demanda geral, no e-mail, na Ruptura por marca e na rodada da Linx (`_compras_regras_marca`,
`_compras_codigos_dos_fornecedores`, `apollo_filtros_dos_fornecedores`); venda do e-commerce de peça do Apollo fora do Precificador conta.
**2ª revisão (09/11, "corrigir todos"):** a abertura da tela não chama a porta (`apollo_filtros(so_cache=True)`; a lista é lida pelo
`/api/compras/apollo/filtros` com 10 s por chamada, falha guardada 5 min, uma leitura por vez); salvar fornecedor só confere o grupo
quando ele mudou; grupo grande sem categoria é recusado já na validação; código recusado (422) fica fora por 1 h e as divisões têm teto
(`APOLLO_ESTOQUE_MAX_DIVISOES`); casa sem resposta mantém o número que ela tinha; o modo grupo nas outras telas só vale depois da 1ª
leitura (antes, as regras antigas) e a Marca do cadastro não conta com grupo; lista vazia sem lista anterior = 0 peças (não erro);
nome do grupo normalizado dos dois lados (`_apollo_nome`); o iniciar pega a vez (`APOLLO_GRUPO_LOCK`) antes de responder; outra
leitura rodando aparece na tela (`outra_rodando`, consulta a cada 30 s); a noite confere o fim da janela também no estoque e nas
perguntas da Linx, e interrompida não conta como ok. Teste: classe `Revisao2`.
**3ª revisão (09/11):** a tela pede a lista de grupos antes da 1ª demanda e, sem ela na memória (reinício), mostra "buscando" e
confere de novo (`aguardando_lista`, sem erro falso); quem chega com a lista sendo lida espera ela; grupo que a lista diz ter peças e
volta vazio é erro, e lista vazia não liga o modo grupo nas outras telas; o número antigo de uma casa sem resposta vale só 24 h e
guarda a data dele (`lido_em`/`antigo` em cada casa); código recusado (422) deixa a leitura incompleta (o `estoque_em` não é renovado)
e o teto de divisões desiste só daquele lote. Teste: classe `Revisao3`.
**V20.45 — categoria primeiro, fornecedor opcional, modo escuro** (dono, 09/11, com os prints do Apollo: a categoria da Manutenção de
Itens do Estoque, 111 FILTROS e 15 LUBRIFICANTES/ADITIV, é o filtro principal). Ordem na tela: **Categoria do Apollo** (aceita "111
FILTROS" ou só "111"; `categoriaCodigos()` traduz o nome; enquanto o Apollo não leu as categorias, a lista sugere as duas conhecidas),
**Grupo (fabricante)** opcional e **Fornecedor** opcional com a opção **Todos** (dono: "se deixar AGRALE não vai ter lubrificante").
Com Todos, o payload aceita `fornecedor` vazio desde que haja categoria/grupo (pedido mínimo 0, prazo 7, cobertura 30; o digitado fica
no navegador por filtro, `nk_compras_cfg_v2045_*`) e a marca à mão não tira peça. O último filtro usado abre de novo
(`nk_compras_filtro_v2045`). Modo escuro: a tela nunca tinha recebido o bloco `escuro-auto-v1883` (gerado agora) e ganhou ajustes à mão
(células e tabelas de transferência). Prévia: `FERRAMENTAS_WINDOWS/previa_local/previa_demanda.py` (porta 8162, portas simuladas).
Teste: classe `CategoriaPrincipal`.
**revisar-antes-de-subir da V20.45 (09/11, "corrija"):** o "a caminho" digitado é da **peça** (`compras_caminho_v2045`, vale em qualquer
visão; na 1ª vez junta o antigo por fornecedor, sem apagar; apagar grava); escolher um fornecedor usa a categoria/grupo dele (sem nenhum,
limpa e valem as regras); pedido mínimo/prazo do Todos guardados pela categoria em código (`chaveFiltro()`) e relidos ao trocar o
filtro; nome de categoria: exato primeiro, começo de nome só com um candidato; só a resposta da última recarga vale (`seqDemanda`);
categoria acima de `APOLLO_PECAS_MAX` pede "o grupo (fabricante)" (o limite de 5.000 vale rever quando o Apollo terminar de ler as
categorias, ~12/11). Teste: classe `RevisaoV2045`.

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

**V19.30 — Romaneio e caixa pelas portas da Expedição** (decisões do dono, 15/10). O NoromakWeb
(Guilherme, mesma VPS) abriu portas em `127.0.0.1:8000/api/v1/expedicao/*` (`X-Chave-Expedicao` =
`CHAVE_CRM`; contrato em `/openapi.json` dele): `cliente/{situacao,atualizar,credito}`,
`atendimentos/{abertos,itens}`, `romaneio/{previa,soltar}` e `caixa/{previa,enviar}`. A Expedição
nunca recebe credencial da Linx. `_exp_porta` (404 = "porta não liberada"; falha de rede numa escrita
= `incerto`). Rotas do Noromak `/api/expedicao-apollo/{romaneio, romaneio/soltar, caixa/previa,
caixa/enviar, cadastro}` (módulo **admin** = Admin e Supervisor). O ícone de documento da linha do
pedido abre a janela nova (`pdExpRomaneioV1930`); a extensão Linx Cliente **0.30** troca o 🧾 da venda
(ML e Shopee) por `romaneio-api.html`, que chama as mesmas rotas pelo service worker (login do Noromak
do Chrome). Regras dos testes reais: atendimento achado pelo **nome no Apollo** (`cliente/situacao` com
o código de `cliente_fiscal`); **V19.31:** sem código, busca pelo **nome da NF-e** (todas as palavras, sem acento) e, não achando, primeiro + último nome — pode trazer homônimo, então só escolhe sozinho com peças batendo (exato só quando a porta tiver `cliente/buscar` por CPF); o nº do atendimento digitado fica como reserva; escolhe sozinho só um
atendimento com peças batendo; **observação digitada** (vazia não sai, até 100); caixa manda **sempre o
código do cliente** (sem ele UF vazia → Erro 2619), a **condição de pagamento** é escolhida no Apollo
(sem ela 412), envio com `valor_visto`; romaneio e caixa nunca saem duas vezes (`linx_operacoes_v1860`);
`pergunta`/`incerto` trava o caixa até marcar "conferi no Apollo". **Cadastro** (`exp_cadastro_atualizar`)
fica **desligado** (`EXP_CADASTRO_API=0`) até a porta ter `cliente/buscar`, `cliente/criar`, nome e
Endereço 1; manda e-mail + telefone + celular juntos (sem telefone o Apollo recusa). O romaneio antigo
pela Smart API (`linx_romaneio_confirmar`) ficou sem botão. Teste: `TESTES/test_expedicao_apollo_v1930.py`.
**V19.32 — cadastro pela porta (ligado; `EXP_CADASTRO_API=0` desliga).** Contrato real do Guilherme (15/10):
`cliente/buscar` (CPF/CNPJ → `encontrado`/`nao_cadastrado`/`mais_de_um`; falha = `ok:false`, nunca "não
cadastrado"), `cliente/criar` (nome, e-mail, celular de **9 dígitos começando com 9**, `endereco` inteiro;
`ja_cadastrado` se já existe) e `cliente/atualizar` com `nome` + `endereco` (`EnderecoUm`: rua, numero,
complemento, bairro, cep, cidade, uf — **tudo ou nada**); campo desconhecido = 422. Decisões do dono:
endereço = **Dados do envio** (`exp_endereco_entrega`: `/shipments/{ShipmentIdMercadoLivre}` com a conta do
pedido; só ML por enquanto), e-mail e celular **padrão para todos** (`EXP_CELULAR_PADRAO` = 11 999999999, o
111111111 é recusado na criação; o fixo 11 111111111 só vai no atualizar). A **cidade** fica com a grafia
do cadastro de CEP do Apollo (ele recusa a nossa se diferir) — a resposta diz como ficou. O romaneio, sem o
código, usa o `cliente/buscar` (exato) e só depois o nome. Botão "Linx" do Admin = `pdExpCadastroV1932`
(sem nome/CPF da NF-e guardados cai na extensão); extensão **0.31**: o ✏️ do ML abre `romaneio-api.html?funcao=cadastro`
(nome/CPF lidos na venda vão por mensagem e o servidor guarda em `cliente_fiscal` antes).
**V19.33 — CNPJ:** o ML mostra a NF-e em pares rótulo/valor ("Razão social" / "59.398.838 NOME"); a extensão 0.31.1 lê o valor depois do rótulo (tira o prefixo do MEI) e a **IE** ("Inscrição estadual (IE)"). Antes lia o rótulo "Tipo de contribuinte" como nome (pedido 2000018994284906): o servidor recusa nome que é rótulo (`EXP_NOME_ROTULO`). A IE só vai no `cliente/criar` (CNPJ); cliente existente recebe aviso — o `atualizar` não tem IE.
**V19.34:** caixa com um botão só ("Enviar ao caixa" confere por trás, confirma o valor e envia). **V19.35 — endereço da Shopee:** `_exp_endereco_shopee` lê `recipient_address` do `get_order_detail` (rua/número/complemento do `full_address`, UF pelo nome do estado em `EXP_UF_POR_NOME`), sem acento como no ML; a Shopee **não informa o bairro** → `CENTRO` (`EXP_BAIRRO_PADRAO_SHOPEE`, decisão do dono 15/10, com aviso); texto mascarado (`*`) conta como faltando. Extensão 0.32: ✏️ também na Shopee. **V20.33 — TikTok** (06/11): extensão **0.49** põe o botão do painel único na página do pedido do TikTok (`botao-tiktok.js`, `seller-br.tiktok.com`): nome/CPF da nota e endereço pelo `/api/fulfillment/order/get` da página (reserva: o texto "Nome completo"/"Número do CPF"/"Endereço de envio"), observação TIKTOK e condição **973**. O TikTok traz o bairro: a extensão manda `bairro` junto do `endereco_texto` e `exp_endereco_do_texto(texto, bairro)` usa (mascarado/vazio = CENTRO, com aviso). Extensão **0.50**: o Linx web voltou (06/11) e o painel grava o **crédito** por ele depois do "Atualizar cadastro" (e no botão "Liberar pelo Linx web" da faixa de crédito bloqueado): só Cadastro 421 = Aprovado, Situação 420 = Crédito normal e validade hoje, com o mesmo registro preservado da gravação 0.3 e releitura; código do Noromak diferente do achado pelo CPF, CNPJ ou bloqueio do financeiro (`bloqueado`) = não grava. O cadastro automático do servidor continua só com a porta. **V19.36:** em produção a API da Shopee devolveu o endereço **mascarado**; a extensão 0.33 lê o endereço da página (linha depois do nome, abaixo de "Endereço de entrega") e manda em `endereco_texto`; `exp_endereco_do_texto` separa rua, número, complemento, cidade, estado e CEP (o fim é sempre cidade, estado, CEP), bairro CENTRO. **V19.37 — kit com peça faltando:** `_exp_componentes_do_pedido` abre o SKU em peças ("A/B", tira "KIT-" e o prefixo de anúncio "151490-") e cada atendimento ganha `faltam`; com peça faltando o botão não escolhe sozinho e mostra/confirma "Falta no atendimento" (Shopee 261015P6NPQQHV: kit 0893600022/0893764272, atendimento só com 0893764272). Extensão 0.35 (e 0.34: layout minimalista, painel com altura do conteúdo). **V19.38 — romaneio já solto no Apollo:** atendimento com as peças do pedido e nada a soltar (solto por fora do botão) vira `situacao: romaneio_fora` + `romaneio_fora` (o nº do atendimento); as telas mostram "Romaneio já solto no Apollo" e liberam o caixa com esse atendimento (antes caía em "peças que não batem"). Extensão 0.36–0.39: condição de pagamento pelo Linx web antes do caixa (só Shopee 971/Magalu 983; ML já nasce certo) e botão "Acertar condição", confirmação que não cancela no clique fora, painel preso pelo topo (crescia para cima e o clique errava). **V19.41 — cadastro automático** (decisões do dono, 16/10): worker `cadastro_auto_loop` (2 min) roda `exp_cadastro_atualizar(..., criar=False)` nos pedidos pendentes de ML e Shopee cujo nome/CPF da NF-e (`cliente_fiscal`) chegou **depois de ligar**; **nunca cria** cliente (a venda demora a cair no Apollo): não achou → tenta de 10 em 10 min por **1 h** contada da chegada do nome/CPF, depois `nao_encontrado` (só o botão resolve); `mais_de_um`/erro não mexe; cada pedido uma vez. Grava tudo igual ao botão. Estado em `cadastro_auto_v1941`, interruptor `cadastro_auto_config_v1941` (começa desligado; `POST /api/expedicao-apollo/cadastro-auto` só perfil Admin; cartão no Admin → Integrações). Painel da extensão (0.42) pula o cadastro quando o automático já fez; coluna Cód.Cliente mostra aguardando/não encontrado. Teste: `TESTES/test_cadastro_auto_v1941.py`. Extensão 0.41: **painel único** (um botão; cadastro → romaneio → caixa).
**V20.47 — mais tempo tentando, sem pesar** (dono, 10/11: a maioria dos clientes caía no Apollo depois de 1 h e o Faturamento
ficava em "aguardando cadastro"). Intervalo crescente (`_cadastro_auto_intervalo`: 10 min na 1ª hora, 30 min até 6 h, depois de
hora em hora) por até `CADASTRO_AUTO_JANELA` (3 dias) enquanto o pedido está na fila; "não encontrado" da regra antiga volta a ser
tentado dentro da janela. O automático (`criar=False`) procura o cliente pelo CPF **antes** de ler o envio no ML: tentativa sem
cliente = uma leitura na porta. O `detalhe` diz a próxima tentativa (hora de Brasília) e o Faturamento mostra.
**revisar-antes-de-subir (10/11, "corrigir todos"):** a rodada relê a situação antes de cada pedido e o registro da rotina
(`automatico=True`) nunca troca um feito (o botão pode fazer no meio da volta); candidatos em ordem de vencimento (nunca tentado
primeiro); "não encontrado" com `linx_cliente` já guardado (resolvido à mão) não reabre; erro passageiro da porta (429, sem
resposta) para a volta sem gastar a vez; feito sem endereço (Shopee mascara na API) = `feito_sem_endereco`
(`CADASTRO_AUTO_FEITOS`: vale como feito no Faturamento, e a extensão Linx, que só pula com "feito", oferece o cadastro com o
endereço da página); erro da porta aparece no Faturamento como erro, não como "cliente fora do Apollo". Teste: classe `RevisaoV2047`.
**V20.47 — criar cliente e atendimento de ML/Shopee pelo painel** (dono, 10/11: "mesma função do TikTok"). Depois de
`MKT_ATEND_APOS_S` (2 h) sem o cliente no Apollo, o painel do romaneio (extensão Linx Cliente **0.54**) mostra "Criar cliente e
atendimento no Apollo" → `POST /api/expedicao-apollo/criar-atendimento {pedido, endereco_texto, bairro}` (módulo admin, recusa outra
filial) → `mkt_atendimento_iniciar` (um por vez, em 2º plano) → `mkt_atendimento_pedido`: itens/preço da venda copiada
(`vendas_v1799`/`vendas_conta_v1898` por pedido ou carrinho; kit dividido pelo custo de Araçatuba, `_tiktok_atendimento_itens(canal=)`),
conferência com a fila (`_tiktok_atendimento_conferir_fila(canal=)`; diferente = `revisar`, nada gravado), cliente criado/atualizado
pelo botão (registra feito), **procura um atendimento aberto do cliente com todas as peças** (a integração pode ter trazido a venda:
achou = `ja_tinha`, nada criado), `atendimento/criar` (origem MERCADOLIVRE/SHOPEE, chave `ML-`/`SHOPEE-<pedido>`, Shopee condição 971)
e `atendimento/comprometer`. Estado em `mkt_atendimento_v2047` (painel: `atendimento_mkt` no `romaneio`); comprometido = sim conta em
`_anuncio_pedidos_com_atendimento`. Comprometer que falhou: o botão volta como "Comprometer peças" (sem rotina automática). Risco aceito
pelo dono: a integração trazer a venda depois do botão = atendimento a mais. Teste: `TESTES/test_atendimento_ml_shopee_v2047.py`.
**revisar-antes-de-subir do botão (10/11, "corrigir todos"):** a extensão acompanha pelo `GET /api/expedicao-apollo/criar-atendimento?pedido=`
(`mkt_atendimento_painel`, só o banco; o estado lê a fila guardada `_ml_fila_leitura`) e relê o painel uma vez no fim — antes relia o
painel a cada 4 s e gastava as 12 leituras/min que a criação precisava. O criar marca `enviado_em` antes de sair: depois de um incerto
(ou reinício) o clique vai direto à porta (a chave devolve `ja_existia` e compromete), em vez de achar o próprio atendimento e virar
`ja_tinha`; recusa limpa a marca. "Já existe" só com todas as peças, algo a soltar, valor da venda (±0,10) e atendimento não ligado a outro
pedido (`_mkt_atend_ligados`). Fila e venda relidas logo antes de criar (ADMIN mexeu = `revisar`) e antes de comprometer. Cadastro já
feito pelo botão não é regravado na repetição. Kit que não divide sem perder centavos = `revisar`. Trava: estado conferido de novo com a
trava, thread que não sobe devolve o estado anterior, a trava só é solta por quem a recebeu. ML: a porta não aplica a condição e o painel
só acerta Shopee/Magalu/TikTok — `condicao_aviso` no painel até o código da condição do ML entrar em `MKT_ATEND_CANAIS`. Cidade do CEP
vai por `_exp_limpo`. Teste: classe `RevisaoBotao`.
**2ª revisão (só das correções, "corrigir todos"):** na Shopee o "já existe" aceita também a diferença explicada por cupons/moedas
(`_faturamento_diferenca_explicada`, o atendimento da integração vem com o valor pago); itens comparados só por código e quantidade (o
preço da peça do kit muda com o custo de Araçatuba); "criado" já grava `comprometido='comprometendo'` (o acompanhamento não para antes do
comprometer); o Faturamento põe em atenção o ML criado pelo botão até a condição ser acertada (`_mkt_faturamento_barreira`: o lote não o
manda ao caixa); o GET do estado recusa pedido de outra filial (403 `OUTRA_FILIAL`). Teste: classe `Revisao2Botao`.
**V20.49 — botão com o cliente já cadastrado** (dono, 10/11, pedido 2000015964547311: o "Atualizar cadastro" do painel criou o
cliente e o botão sumiu, "o cliente já está no Apollo"). Decisão (revisão): **cliente CRIADO pelo Noromak** (`cadastro_auto_v1941.cliente_criado`,
gravado pelo botão quando o `cliente/criar` criou: ele não existia, então a integração ainda não trouxe a venda) = botão **na hora**; cliente que
**já existia** = espera de **2 h desde a venda** (`_mkt_atend_desde` lê o `sale_at` das vendas copiadas; o "Atualizar cadastro" regravava o
início do cadastro com a hora do clique). Com o cliente no Apollo vai `requer_sem_atendimento` (vale até o criar sair, `enviado_em`): a
extensão Linx Cliente **0.55** só mostra o botão na etapa do romaneio quando nenhum atendimento aberto cobre as peças e o painel leu o Apollo
(sem erro, ocupado ou `romaneio_fora`). No clique, `romaneio_fora` (romaneio solto direto no Apollo) conta como atendimento existente
(`ja_tinha`). Limite que já existia: o painel olha só os 4 primeiros atendimentos abertos (`EXP_MAX_ATENDIMENTOS`). Teste: `QuandoAparece` em
`TESTES/test_atendimento_ml_shopee_v2047.py`.
**V20.50 — endereço sem número = "SN"** (dono, 11/11, ML 2000019472156870 "Br 101 km 26, SN"): o número vai dentro do
Endereço do Apollo; vazio gravava sem a ", número" e o crédito pelo Linx web voltava 412 ("vírgula seguida de um espaço para
o número"). `_exp_porta_endereco` troca número vazio/"SN"/"S/N"/"0" por "SN" em todo criar/atualizar (ML, Shopee, texto,
TikTok, Full). Cliente já gravado sem número: "Atualizar cadastro" de novo. **revisar-antes-de-subir (11/11, "corrigir 1 e 3"):**
"sem número" de qualquer grafia (S N, S.N., S/Nº, SEM NUMERO, SEM Nº) vira "SN" (`_EXP_SEM_NUMERO_RE`) e número com espaço antes da
letra ("45 B", que o TikTok aceita) é número, não complemento — mesma regra no texto da página e na Shopee (`_exp_numero_lido`).
Número colado na rua sem vírgula ("Rua X 342, Casa…") continua virando "RUA X 342, SN" (aceito). **Envio do pedido** (11/11, ML
2000019449560734: "Cliente novo precisa do endereço inteiro: pedido sem número de envio do ML"): `exp_endereco_entrega` olhava só a
1ª linha; agora pega o envio de qualquer linha do pedido e, sem nenhum, lê `/orders/{venda}` → `shipping.id` com a conta do pedido
(a fila não muda). **Tipo de rua** (11/11, ML 2000015965127073 "Condomínio Algodoal 8B"): o Apollo lê a 1ª palavra como tipo de
rua e recusa a que não tem ("o tipo de rua "Condomínio" não está cadastrado"); decisão do dono: `_exp_porta_endereco` repete UMA
vez com "RUA " na frente (`_EXP_TIPO_RUA_RE`) e avisa; junto com a cidade do CEP, cada correção uma vez. Teste: `TESTES/test_endereco_sem_numero_v2050.py`.
**V20.51 — `manual: true`** (Guilherme, 11/11): a porta `atendimento/criar` só abre MERCADOLIVRE/SHOPEE com `manual: true`
("clique de gente no painel"), chave `ML-`/`SHOPEE-<venda>` e a origem ligada no NoromakWeb (Gestão › Configurações › Apollo /
LinxDMS › Parâmetros da concessionária › Marketplaces que abrem atendimento pela Expedição); senão `origem_recusada`.
`mkt_atendimento_pedido` (só o botão chama) manda `manual: true`; o TikTok não muda.
**V20.52 — o liga/desliga é do Noromak** (dono, 11/11: "essa porta de ligação tem que ser minha, eu controlo o e-commerce"; o
dono só acessa o /expedicao). O Guilherme deixa as duas origens ligadas uma vez; no dia a dia manda o interruptor
`mkt_atendimento_config_v2052` (por marketplace, sem linha = ligado), cartão "Atendimento de ML e Shopee pelo painel" em Admin ›
Integrações, `GET/POST /api/expedicao-apollo/atendimento-mkt` (ver = módulo admin, mudar = perfil Admin, audit
`expedicao.mkt_atendimento_config`). Desligado, `mkt_atendimento_estado` não oferece o criar (motivo "desligado", nada vai à porta);
comprometer atendimento já criado continua. Teste: classes `Interruptor`/`InterruptorRotas`. Origens ligadas pelo Guilherme em 11/11
(MERCADOLIVRE/SHOPEE/TIKTOK = usuário 210 do DMS, o mesmo vendedor da integração). Respostas novas da porta (contrato de 10/11):
`ja_aberto_no_apollo` (ela achou atendimento aberto do mesmo cliente com todas as peças) = `ja_tinha` com o número, nada criado;
`sem_estoque` (peça sem saldo na revenda) e `apollo_fora` = erro, o clique pode repetir. Teste: classe `RespostasNovasDaPorta`.
**V20.54 — a vez do painel na porta** (dono, 11/11: "Muitas chamadas ao Apollo neste minuto" nos 3 PCs). A porta aceita 12
leituras e 6 gravações por minuto **por rota**, e tudo sai deste servidor (os computadores e as rotinas dividem o teto); cada
painel aberto faz até 6 leituras (situação + abertos + até 4 prévias). `_exp_porta` conta cada chamada por rota e origem
(`exp_porta_uso`, em `/api/saude/medicao` → `porta_apollo`: painel/rotina no último minuto, total, 429, seguradas). Rotinas
automáticas rodam em `with exp_porta_rotina():` (Faturamento, cadastro automático, cadastro do Full, atendimento do TikTok; a
marca passa às tarefas do `_exp_em_paralelo`) e, nas rotas de leitura (`EXP_PORTA_LEITURAS`), usam no máximo
`EXP_PORTA_ROTINA_LEITURAS` (6) por minuto; passou, ou depois de um 429 na rota (1 min), recebem o PortaErro passageiro de um
429 sem chamar a porta. Gravação não é segurada. Teste: `TESTES/test_porta_vez_v2054.py`.
**Cidade do CEP** (pedido 2000019436248196): o Apollo recusa "O CEP é de CRISTALINA, não de CAMPOS LINDOS" (o ML manda o distrito);
`_exp_porta_endereco` tenta uma vez de novo com a cidade que o Apollo indica (criar e atualizar, também no Full) e avisa a troca.

**V19.43 — Fila de faturamento em lote** (`APP/faturamento.html`, `/faturamento`, menu Expedição, módulo **admin**).
Worker `faturamento_loop` confere no Apollo, de 10 em 10 min (4 por rodada), cada pedido ML/Shopee/Magalu da fila de
pendentes (`faturamento_v1943`). **Pronto** = cadastro `feito` (`cadastro_auto_v1941`, automático ou botão) + crédito
liberado + **um** atendimento com as peças do pedido + valor do atendimento **igual** ao da venda (`_exp_valor_pedido`:
soma de `amount` em `vendas_v1799`/`vendas_conta_v1898`; sem venda = atenção, nunca pronto). Romaneio já solto =
`pronto_caixa`. O lote (uma confirmação) chama `POST /api/faturamento/executar` por pedido, que **confere de novo**,
solta o romaneio com a observação do marketplace e envia ao caixa; Shopee/Magalu param em `etapa: condicao` e a tela
troca a condição pela extensão Linx 0.44 (`condicao-definir-lote`, sem confirmação por pedido) antes de reenviar com
`condicao_ok`. Etiquetas/TXT e Histórico saíram do menu (botões no "Importar pedidos"); Relatório ML/Shopee removidos.
**V20.12 — Apollo ocupado não é atenção** (dono, 03/11; Shopee 2610312SXXXGJE): 429 da porta ("Muitas chamadas") ou leitura
sem resposta (`PortaErro.passageiro`) vira `aguardando_apollo` (aba Aguardando), conferido de novo em 2 min
(`FATURAMENTO_REPETIR_APOLLO`), e a rodada para de ir ao Apollo no primeiro limite. 404/401 continuam atenção. No lote a tela
espera 1 min e tenta de novo. Sem resposta há 30 min seguidos (`apollo_desde`, `FATURAMENTO_APOLLO_ATENCAO_S`) = atenção
"Apollo sem resposta há N min" (continua conferido; a 1ª resposta zera a contagem). Teste: `TESTES/test_faturamento_apollo_ocupado_v2012.py`.
**V19.99 — corrigir o valor do atendimento** (decisões do dono, 28/10). A integração Shopee → Apollo grava o que o
comprador pagou (preço − cupom da loja − cupom da Shopee − moedas; conferido em 9 pedidos); o valor certo da nota é
o **preço da unidade × qtd** (a venda, que o Noromak já compara). No ML o kit sai com centavos a mais (132,93 × 132,90).
`_faturamento_diferenca_explicada`: Shopee = diferença igual a cupons + moedas do escrow (`shopee_escrow_v1810`,
± R$ 0,05; sem escrow = atenção); ML = até R$ 0,10. Explicada → situação `corrigir_valor` (aba Prontos, selo "Corrige
o valor"); sem explicação continua "Valor diferente". O `executar` para em `etapa: preco` **sem soltar nada**; a tela
chama a extensão Linx **0.45** (`preco-corrigir-lote` → `corrigirValorAtendimento`: relê o atendimento, recusa se não
está mais com o valor que o servidor viu, divide o valor da venda pelas peças na proporção do Apollo —
`dividirValorAtendimento`, centavo que sobra na peça de qtd 1 — e usa a correção do popup 0.28, que exclui e reinsere
o item com o preço certo no Linx web; gera "venda perdida" no Linx, como a correção à mão). Depois chama `executar` de
novo: a nova conferência é que libera o romaneio. 1º teste real com o dono: 261028RSB9AC4M, atend. 790955, 23,31 → 25,90.
Pedido de porta própria ao NoromakWeb e mensagem à Linx (corrigir na origem) em `PEDIDO_PORTA_PRECO_ATENDIMENTO.md`.
**Revisão 28/10:** uma correção por pedido — a etapa `preco` reserva o pedido com uma senha (`trava`) em
`faturamento_correcao_v1999`; sem ela o `executar` recusa sem ir ao Apollo; a tela devolve a senha (corrigido) ou
`liberar` (nada alterado, inclusive recusa da extensão antes de mexer). A reserva **não vence sozinha**: correção pela
metade (ou sem resposta da extensão) fica "em_correcao" na aba Atenção até alguém conferir no Linx e clicar em "Já
conferi no Linx — liberar" (`POST /api/faturamento/correcao/liberar`, audit). A extensão lê atendimento e valores em
`GET /api/faturamento/correcao?pedido=&trava=` (nunca os números da página), só aceita a ação da tela Faturamento
(content script e `sender.url`), faz uma correção por vez (quem espera > 4 min volta `NAO_ALTERADO`) e confere todas
as peças (seleção, kit, pesquisa) **antes** de excluir a primeira (`_preConferirCorrecao`, só lê); falha antes de mexer
= `NAO_ALTERADO`, mexeu e não fechou = `VERIFICAR` → a tela **para o lote**. Romaneio já solto com valor diferente da
venda ou sem valor = atenção (não vai ao caixa); atendimento sem valor = atenção. Prazo da tela: 10 min. Limite: a
extensão chama o Noromak sem `www` (a correção pela tela aberta em `www.` volta "nada alterado").
Teste: `TESTES/test_faturamento_valor_v1999.py`.
**V20.11 — correção pelo painel do pedido** (dono, 02/11, Shopee 2610314H4FMAE7: romaneio solto com valor errado sai da correção). `exp_romaneio_soltar_painel` confere o valor no servidor antes de soltar: com diferença explicada volta `valor_diferente` (o painel troca o botão por "Corrigir valor", `POST /api/expedicao-apollo/valor/{reservar,concluir}`, mesma reserva e mesma extensão do Faturamento, 0.48); sem explicação ou sem valor do Apollo só sai com `valor_conferido` (caixa "Conferi", audit `expedicao.romaneio_valor_conferido`); a explicada nunca sai com "Conferi"; reserva aberta bloqueia sempre. O caixa do painel/Admin tem a mesma barreira do lote (`exp_caixa_previa_painel`/`exp_caixa_enviar_painel`, audit `expedicao.caixa_valor_conferido`), e o painel libera a reserva (`/valor/liberar`), porque pedido fora da fila não aparece no Faturamento. Venda sem valor sincronizado não trava. O lote não usa essa rota. Teste: `TESTES/test_painel_valor_v2011.py`.

**V19.87 — Reemissão do romaneio na Conferência** (dono, 27/10). 247 de 247 romaneios soltos pela porta voltaram
"Impressão enviada ... impressora 1", mas às vezes a folha não sai no estoque (percebido só no faturamento). Soltar de
novo não reimprime (as peças já estão em romaneio) e a porta não tem reimpressão. `GET /api/romaneio-pedido?pedido=`
(`romaneio_do_pedido`; qualquer logado, recusa outra filial; só lê `linx_operacoes_v1860`): número do romaneio tirado
da mensagem do Apollo, atendimento, observação, hora. Na Conferência o layout não muda (dono, 27/10): só o botão
"Reimprimir romaneio" e a bolinha do Linx ao lado da etiqueta, quando há romaneio solto com número (confirmação antes). Quem reimprime é a extensão **NOROMAK_REEMISSAO_ROMANEIO** (Desktop, fora do
repo; `postMessage` `noromak-reemissao-romaneio`), no Linx web logado do mesmo Chrome, igual à tela Reimpressão de
Romaneio (menu 88100): `ProcuraRomaneio {NumeroRomaneio}` → confere o atendimento → `ReimprimirRomaneio {Contato,
Empresa, Revenda, Usuario, Romaneio, Reimpressao:true, Impressora, TipoRomaneio}` na **mesma impressora**; 206/208 =
o Linx quer PDF (usuário sem impressora) → avisa e não imprime; mesmo romaneio 1x por minuto. Nunca chama
GeraLista/AtualizaQuantidades/ConfirmaRomaneio. Teste: `TESTES/test_reemissao_romaneio_v1987.py`.
Revisão 27/10: `romaneios` (lista; um soltar pode gerar mais de um, um botão por romaneio; sem número = sem botão);
pedido fora da fila só para quem vê todas as filiais (404 `FORA_DA_FILA`); a resposta não traz o login de quem
soltou; Consulta (`data-somente-leitura`) não ganha o botão; o foco volta ao leitor logo depois do clique e a
resposta de outro pedido não cobre o aviso do aberto. Extensão 0.3.0: uma reimpressão por vez, prazo (busca 10 s,
reimpressão 25 s — a tela desiste em 40 s), sem resposta/prazo/5xx = INCERTO e trava 1 min, confere número e
atendimento devolvidos (teste `node teste-reemissao.mjs` na pasta da extensão).
Revisão da correção: com um romaneio pendente todos os botões travam (`reimpressaoPendenteV1987`); a rota lê a fila
uma vez só (`_pedido_filial_e_fila`, sob o DB_LOCK a cada pedido aberto); extensão 0.3.1: a leitura da sessão na aba
do Linx desiste em 5 s (aba travada não segura a fila).

**V19.85 — Lista de separação do dia** (`APP/separacao.html`, `/separacao`, menu Expedição + botão em Pedidos
pendentes, módulo **admin**; decisões do dono 26/10). Todos os pendentes da fila da filial do seletor do menu ("todas" =
400, escolher a filial), kit aberto em peças (`_tiktok_pecas`), somados por código, na ordem da prateleira do Apollo da
filial (Araçatuba revenda 1, Bauru 3; `_full_loc_chave`). Linha = prateleira, código, descrição, quantidade. A prateleira
fica 12 h em memória (`SEPARACAO_LOCAIS`); o que falta é lido em segundo plano — **uma leitura por vez para as duas
filiais, 1 a cada 12 s, sem esperar vaga**; limite do Apollo cheio = para 1 min (a operação vem antes) — e a tela
recarrega até completar; filial sem resposta (`respondeu: false`) vai para o fim e é tentada de novo depois de 5 min.
SKU com barra que é um produto do Precificador ("PSH367/80") fica inteiro (regra da V19.48); parte com menos de 3
caracteres é variação ("WAP103/S" = uma peça); "KIT-1/A-1/B" = quantidade/código de cada peça. Separada por
marketplace (`grupos`: Mercado Livre, Shopee, Magalu, TikTok; cada um em folha nova na impressão). Só entra despacho
de hoje e atrasados (`_faturamento_prazo_dia`, regra do Faturamento V19.73; sem prazo = Shopee entra); os de outro dia
contam em `futuros` (caso real 26/10: coleta de 28/10 aparecendo na lista). `GET /api/separacao`.
Começa em `SEPARACAO_INICIO` (27/10, dono: a de 26/10 foi emitida à mão); antes disso a rota devolve vazio com `inicio`
e não lê o Apollo. A tela relê a cada 30 s enquanto lê a prateleira e para com a aba escondida (revisão 26/10).
Antes de subir: `FERRAMENTAS_WINDOWS/separacao_hoje_leitura.py` (só leitura, via ssh) traz a fila para montar à mão.
Teste: `TESTES/test_separacao_v1985.py`.

**V19.88 — Devoluções** (decisões do dono, 27/10; desenho em `docs/superpowers/specs/2026-10-27-devolucoes-design.md`).
Só o lado físico, só registro: nunca mexe em `completed_orders`, na fila, no Apollo nem no marketplace. **Operador**
(`APP/devolucoes.html`, `/devolucoes`, módulo **conferencia**, tela cheia sem menu, `alert()` de propósito): bipa a peça /
a etiqueta de devolução do ML ou digita cliente / nº do pedido → `GET /api/devolucoes/buscar` (pedidos **concluídos** dos
últimos 180 dias, todos os marketplaces, filial de quem busca) → `GET /api/devolucoes/pedido` (venda com kit aberto em
peças, `_separacao_partes`; o que já voltou; EANs do `conf_produtos`) → bipa cada peça (+1) ou digita a quantidade →
**bom** ou **ruim** (ruim exige motivo de `DEVOLUCAO_MOTIVOS`) → `POST /api/devolucoes/registrar`. Peça fora da venda entra
marcada (`fora_da_venda`); "sem pedido" exige o cliente. Nunca volta mais que o vendido somando todas as devoluções do
pedido (conferido sob o `DB_LOCK`). Pedido de outra filial = 403 `OUTRA_FILIAL`. **Admin** (`APP/devolucoes-admin.html`,
`/devolucoes-admin`, menu Expedição, módulo admin): `GET /api/devolucoes`, `POST /api/devolucoes/{tratar,corrigir,reabrir}`
— tratar exige o nº do documento do Apollo; correção vai para `devolucao_evento_v1988` com o antes; tratada só reabre pelo
admin; edição manda `versao` (409 `CONFLITO`). **Busca sem pesar:** índice `devolucao_busca_v1988` (uma linha por peça),
preenchido por `devolucao_indexar` em lotes de 200 ids de `order_completion_history` (JSON lido fora do lock) — na busca e
no worker `devolucoes_indice_loop` (5 min); pedido reconcluído troca as linhas. **Etiqueta do ML:** o Pós-venda guarda os
envios de volta (`shipments[]` `type=return`: `shipment_id` + rastreio) em `ml_reclamacao_v1888.devolucao_envios`, sem
chamada a mais — **conferir com a 1ª etiqueta real** qual número o código de barras traz (o QR em JSON vale pelo `id`);
só a conta principal. **Revisão 27/10:** o índice anda do **mais novo para o mais antigo** (`piso_hist_id` +
`devolucao_indexado_v1988`), uma indexação por vez (`DEVOLUCAO_INDICE_LOCK`; a busca não espera e indexa 1 lote), só a
conclusão mais nova de cada pedido vale, o que passou de 180 dias sai; Precificador lido uma vez por lote; código da peça
passa pelo `normalize`; kit devolvido fechado (código do anúncio ou EAN da embalagem, `kits` na venda) soma as peças;
pedido reaberto (fora de `completed_orders`) não aparece; o admin zera peça lançada por engano (qtd 0) e não corrige
tratada sem reabrir; "sem pedido" de quem vê "todas" exige a filial; `token` da tela evita duplicar o "Concluir"
repetido (a resposta "repetida" diz o que foi gravado; o token é conferido antes da venda); conclusão "fantasma" do boot (`payload` "[]", recriada do `audit_log` pelo `migrate_completion_history`) nunca conta como a mais nova (`DEVOLUCAO_COM_LINHAS`); devolução com as peças zeradas trata sem documento e sai do filtro de estado; `%`/`_` não são curinga e nome só com palavras de 3+ letras; até 200 peças e código até 60. Depois: foto da
peça ruim, destino por peça, etiqueta da Shopee. Teste: `TESTES/test_devolucoes_v1988.py`.
**V19.89 — módulo Devolução** (dono, 27/10: hoje só Felipe e Matheus recebem). A tela Receber devolução e as rotas
`/api/devolucoes/{buscar,pedido,registrar}` pedem o módulo **`devolucao`** (`app_users.can_devolucao`), não mais
Conferência/Admin; a tela do admin (tratar no Apollo) continua no módulo Admin. Ao criar a coluna, perfil Admin ligado e
os demais desligados (só na criação). Consulta tem (vê; registrar é POST bloqueado), Compras não. **Salvar usuário sem a
chave `devolucao` mantém o valor gravado** (`upsert_app_user`, `/api/roles`, `conf_usuarios`): a tela antiga e a extensão
mandam só os 4 módulos de antes. Caixa "Devolução" na tela de Usuários e botão "Receber devolução" no portal de acesso.
Teste: `TESTES/test_modulo_devolucao_v1989.py`.
**V19.90 — busca "peça + nome"** (dono, 27/10: a 0893117732 só listava de 22/10 para cá). A busca devolve os 30 mais
recentes (`DEVOLUCAO_BUSCA_LIMITE`) e agora `mais: true` quando há mais; a tela pede o nome do cliente. Texto com código E
palavra de 3+ letras ("0893117732 rafael", qualquer ordem) exige os dois na mesma peça; sem resultado, tenta o texto
inteiro como antes (código com espaço). `codigo` na resposta = a peça bipada (o +1 automático vai nela).
**V19.92 — etiqueta de devolução pelo envio** (dono, 27/10). As reclamações estavam vazias (claims API 403 até a
permissão no DevCenter; Bauru ainda 403). O `/shipments/{id}` de um envio de devolução responde `type=return` com o
`order_id` (conferido em produção: 48308414462 → 2000019002481344). Sem resultado na busca, número de 10 a 12 dígitos
(`_devolucao_venda_do_envio_ml`) consulta cada conta conectada e busca pela venda; resposta e "não existe" guardados
30 min (`_DEVOLUCAO_ENVIO_CACHE`), erro do ML = não achou. EAN-13/EAN-8/nº de venda não chamam o ML. Resposta com
`etiqueta_ml` e `codigo` vazio (sem +1 automático). Etiqueta real 48319676132 (27/10) dá 404 nas duas contas: o ML não a conhece. **Pós-venda nunca tinha gravado nada:** com a permissão
liberada (27/10, Araçatuba; Bauru ainda 403) o `claims/search` só com `range` volta HTTP 400 `atLeastOneFilterProvided` e
derrubava a rodada inteira. Agora a busca por data vai uma vez por situação (`opened` e `closed`); o simulador do
`test_posvenda_v1888.py` recusa como o ML real.
**V19.93 — retorno do Full** (dono, 27/10). Venda Full nunca é concluída na Conferência. Sem resultado no índice, a busca
procura nas vendas do ML (`vendas_v1799` = Araçatuba, `vendas_conta_v1898` = outras contas) **só por número**: venda,
carrinho ou o `order_id` da etiqueta (`/shipments`, inclusive o **Ref. ID** da etiqueta de "não entregue" que volta do
armazém do ML com remetente MELI — o código de barras dessa etiqueta o ML não mostra). Chave `VENDAML<order_id>`
(`DEVOLUCAO_VENDA_PREFIXO`; nunca colide com a fila); venda conferida (pela venda ou pelo carrinho) nunca aparece por
aqui; `full` = anúncio com `fulfillment` em `ml_estoque_dia_v1872`; sem nome do cliente (nome/peça não acham Full).
**V19.94 — venda cancelada** (caso real 27/10: etiqueta 48323000393 → venda Full 2000019080231218, cancelada pelo
reembolso). Venda cancelada some de `vendas_v1799` (janela regravada). Para o nº vindo da etiqueta (ou 16 dígitos sem
resultado), `_devolucao_ler_venda_ml` lê `/orders/{id}` nas contas conectadas **uma vez** e guarda em
`devolucao_venda_ml_v1994` (a cópia das vendas manda quando existe); "não existe" fica 30 min em memória; erro = não
achou. Tela marca "cancelada".
**V19.95 — devoluções no lucro + NF de venda** (dono, 27/10; desenho em `docs/superpowers/specs/2026-10-27-devolucoes-lucro-design.md`).
Perda = custo de **Araçatuba** das peças com destino `perda` + frete da devolução; o valor da venda não entra (a cancelada
já sai do faturamento). Destino por peça (`devolucao_item_v1988.destino`: estoque/perda/garantia/desconto; vazio = do
estado). Custo: o do Financeiro (`_financeiro_custo_aracatuba`), **congelado ao tratar** (`custo_unit`), digitável
(`manual`). Frete: digitado vence; senão `ml_reclamacao_v1888.frete_devolucao` pelas vendas ligadas ao pedido; sem custo
ou frete = `incompleta` (perda None; o Financeiro soma só `perda_conhecida`). Conta no **mês em que a peça chegou**, "a
confirmar" até tratar, só **a partir de 01/11/2026** (`DEVOLUCAO_FINANCEIRO_INICIO`) e só Araçatuba no Financeiro:
`financeiro_devolucoes` → bloco `devolucoes` em totais/por_mp/por_dia, descontado do líquido; `cobertura.devolucoes_*`.
Rotas `GET /api/devolucoes/resumo?mes=` (KPIs do mês × anterior, taxa = peças devolvidas ÷ unidades vendidas, ranking por
peça) e `POST /api/devolucoes/valores` (frete, destino, custo; histórico `valores`). **NF de venda**:
`GET /api/devolucoes/nf?id=` (admin) / `?chave=` (operador) lê `/users/{seller}/invoices/orders/{order}` (número,
série, chave, data, valor; Full = série 2) uma vez e guarda em `devolucao_nf_v1995`; sem PDF da DANFE por essa API;
**V19.96:** a Shopee traz (`invoice_data` do `get_order_detail`, `_devolucao_nf_shopee`; conferido 27/10). **V19.97 — relatório no formato da equipe, exportado por LOTE** (dono, 27/10: sem duplicar). "Exportar Excel" → `POST /api/devolucoes/exportar` cria um lote (`devolucao_exportacao_v1997`) com as devoluções **tratadas** que ainda não foram exportadas, de qualquer mês, e marca cada uma (`devolucao_v1988.exportacao_id`, evento `exportada`); clicar de novo não duplica (409 `NADA_A_EXPORTAR`); sem desfazer. `GET /api/devolucoes/relatorio.xlsx?lote=` baixa (de novo) o lote; `GET /api/devolucoes/exportacoes` = histórico. Planilha própria (`devolucoes_relatorio_xlsx`, título, cabeçalho fixo com filtro, zebra, total por fórmula, aba "Resumo por motivo"): NF | NFD | NOME | SKU E QUANTIDADE | VALOR | MOTIVO, uma linha por peça. NFD = o documento do Apollo digitado ao tratar. VALOR = preço de venda × quantidade que voltou (`devolucao_valor_venda`: preço da reclamação do ML > vendas copiadas > venda lida no ML; kit dividido pelas peças; sem preço, o valor da NF). MOTIVO = `devolucao_v1988.motivo_dev` escolhido pelo admin (`DEVOLUCAO_MOTIVOS_DEV`), senão o da reclamação do ML (`_devolucao_motivo_do_ml`). Teste: `TESTES/test_devolucoes_relatorio_v1997.py`. Telas com `theme.css` (o padrão das outras). Teste: `TESTES/test_devolucoes_lucro_v1995.py`.
**V20.52 — aviso no Chat Seguro** (dono, 11/11): ao **tratar** uma devolução com peça **ruim** (qtd > 0), o `devolucao_tratar` dispara
em 2º plano `devolucao_avisar_chat` → porta `POST mensagens/enviar` do NoromakWeb (contrato do Guilherme, seção 7; chave
`expedicao_chatseguro` ligada por ele em 11/11): `para` = `DEVOLUCAO_AVISO_PARA` (`alejandro`, peças), título/texto com pedido,
peças ruins + motivo, documento do Apollo e link da tela; `operador` = quem tratou. Um aviso por devolução
(`devolucao_aviso_v2052`; "enviada"/"repetida" não sai de novo ao reabrir); `chat_seguro_fora`/`nao_tentada`/porta sem resposta =
mais 1 tentativa em 60 s; falha nunca desfaz o tratar (audit `devolucao.aviso_chat`). Sem `CHAVE_CRM` não tenta. Teste:
`TESTES/test_devolucao_aviso_v2052.py`.

**V19.45 — Cliente do Full pela NF-e do ML** (decisões do dono, 16/10). Venda Full não passa pela fila, e a integração
ML → Apollo cria o cliente com o nome da conta ("Premium Flex") em vez da razão social da nota (M2 SOLUCOES GRAFICAS).
No Full a NF-e é emitida pelo **ML** (série 2): `ml_nota_da_venda` lê `/users/{seller}/invoices/orders/{order}` (XML da
NF-e → `dest`; leitura tolerante, 1ª resposta em `cadastro_full_amostra_v1945`, **conferir ao ligar**). Worker
`cadastro_full_loop` (5 min, 4 por rodada): vendas de `vendas_v1799` com anúncio `fulfillment` (`ml_estoque_dia_v1872`) e
fora de `completed_orders`, 30 dias (até 2 dias a qualquer hora; mais antigas só 21h–6h). `full_cadastro_atualizar`:
**nunca cria** (busca exata pelo documento), atualiza nome, endereço sem acento, e-mail/telefones padrão, categoria e
I.E. da nota; sem crédito. Sem nota / cliente fora do Apollo: tenta de hora em hora por 1 dia, depois lista no cartão
"Cadastro dos clientes do Full" (Admin → Integrações; interruptor próprio, desligado; só perfil Admin). Só Araçatuba.

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
| `test_consulta_v1940.py` | perfil Consulta: lê fila e gestão, não grava fila/bip/conclusão/trava/impressão, não apaga nem conecta integração, não vê Usuários/Segurança, todas as filiais, sem Estação, perfil sobrevive ao salvar a lista de usuários |
| `test_promo_shopee_v1949.py` | promoção Shopee: próxima começa no fim da atual, uma por dia de vencimento, simulação/lote só leem, não renova 2x no dia, não cria por cima de agendada, nunca mexe em promoção manual, margem/estoque/pausado travam, variação a variação, recusa por campanha tenta de novo, modo da Shopee separado do ML, Automático com OWNER |
| `test_magalu_v1924.py` | páginas de termos/privacidade sem login, URL de consentimento, state de uso único, token não vaza no status, renovação serializada com o refresh novo, callback com erro volta ao Admin |
| `test_magalu_previa_v1926.py` | prévia não grava na fila, só entrega approved/invoiced, kit aberto, já-na-fila, paginação, 1ª resposta guardada, 401 renova o token uma vez |
| `test_magalu_importacao_v1927.py` | chave começa ligada, pedido entra como MAGALU, reimportar não duplica e mantém edição manual, kit, exclusão do ADMIN (pedido e linha) e concluído não voltam, pedido da extensão não duplica, enviado sai, falha da API não esvazia a fila, etiqueta da entrega faturada baixada uma vez e vinculada, erro na etiqueta não trava o pedido, rotas |
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
