# Plataforma de anúncios — Fase 3: controle de estoque dos anúncios (Manual · Automático)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cada anúncio do ML controlado pelo Noromak ganha **"Controle de estoque: Manual · Automático"**. Manual = o Admin digita e envia ao ML na hora. Automático = o Noromak calcula pelo Apollo da filial (menos o que vendeu e não baixou; kit pela peça que dá menos) e manda na hora quando muda. Tudo registrado.

**Architecture:** Bloco novo "V20.26 — Estoque dos anúncios" em `servidor_web.py`, reaproveitando a fase 1 (`kit_composicao`, `kit_da_para_montar`, `pendentes_por_filial`, `apollo_disponivel_filial`/`crm_stock_lookup`) e o modelo do TikTok (`tiktok_apollo_ler_proximo` = rodízio do Apollo; `tiktok_estoque_loop` = refaz quando a versão da fila muda). Uma função única grava no ML (`ml_estoque_gravar`), relê para confirmar e registra. Worker novo `anuncios_estoque_loop` (20 s) dentro do `_worker_protegido` e em `SAUDE_WORKERS_INFO`.

**Tech Stack:** Python stdlib + SQLite; HTML/JS puro; `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-05-hub-anuncios-design.md` §4.6 + CLAUDE.md "Plataforma de anúncios" (V20.23–V20.25).

## Decisões do dono (05/11)

- Seletor por anúncio **"Controle de estoque — Manual / Automático"**.
- Manual: alterar o estoque do anúncio **vai na hora** para o ML.
- Automático: pelo Apollo da filial (Araçatuba revenda 1, Bauru revenda 3), menos o vendido e não baixado, **sem folga** (até zerar); venda em qualquer canal da filial muda na hora.
- Apollo sem resposta = **não mexe** (nunca zero).
- Exceção à regra "Noromak nunca grava estoque": agora grava no **ML**, só em anúncio com `controle='noromak'` (como já é no TikTok).

## Decisões do Claude (conferir)

- Só anúncios com `controle='noromak'` têm seletor; Alephee e Full não (Full = estoque do ML).
- Padrão de anúncio recém-passado/publicado: **Manual**.
- Automático tem um interruptor **geral por conta**: `simulacao` (padrão; calcula e mostra, não envia) → `lote` (clique "Enviar agora") → `automatico` (envia sozinho; ligar = senha + 2FA do OWNER). Anúncio em Manual nunca é tocado pelo automático.
- Rodízio do Apollo próprio: ≤ 3 leituras/min (a cota de 20/min é dividida com Faturamento/Separação/TikTok), prioridade: nunca lido > perto de zerar (≤ 3) lido há > 10 min > mais antigo.

## Global Constraints

- **Nunca publicar** sem o "pode subir"; commit local no master. Comentários `# V20.26:`; commits `V20.26 - ...` + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. `APP_VERSION = "20.26-ANUNCIOS-ESTOQUE"`.
- Valor desconhecido = nulo, **nunca zero**: Apollo sem resposta, fila não lida, componente sem leitura → não envia.
- Chamadas ao ML e ao Apollo **fora do `DB_LOCK`**; uma gravação de estoque por anúncio por vez (trava por anúncio).
- Gravar no ML = **só** `ml_estoque_gravar`; sempre relê o anúncio depois e só marca "ok" se o ML mostrar o número enviado (o PUT antigo pode dar 200 sem mudar).
- Full nunca recebe estoque. Anúncio removido/`alephee` nunca recebe.
- Perfil Admin para Manual, trocar modo do anúncio e "Enviar agora"; ligar o Automático da conta = OWNER. Supervisor/Consulta só veem.
- Toda 1ª resposta real de chamada nova vai para `anuncio_amostra_v2024` (`estoque_put_item`, `estoque_put_up`, `estoque_up_ler`).
- Worker novo no `_worker_protegido` e em `SAUDE_WORKERS_INFO` (o teste confere). Testar na prévia antes de subir (`FERRAMENTAS_WINDOWS/previa_local`).

## Review Focus

1. **Venda chega no meio de um envio** — o automático recalcula com a fila nova e nunca manda o número velho por cima do novo (versão da fila conferida antes de enviar). Teste na Task 4.
2. **Apollo sem resposta / limite** — anúncio fica como estava, sem envio e sem zero. Teste na Task 4.
3. **ML responde 200 mas não muda** (conta com vários depósitos) — não marca ok; tenta o caminho do user-product; registra erro. Teste na Task 2.
4. **Admin troca para Manual enquanto o automático vai enviar** — o automático relê o modo sob a trava e desiste. Teste na Task 4.
5. **Kit com peça sem leitura** — kit não recebe envio. Teste na Task 3.

---

### Task 1: Esquema, modo por anúncio e registro

**Files:** `servidor_web.py` (bloco V20.26; colunas no `ensure_anuncios_schema` de forma idempotente), `TESTES/test_anuncios_estoque_v2026.py`

**Produces:**
- Colunas em `anuncio_canal_v2023`: `estoque_modo TEXT NOT NULL DEFAULT 'manual'`, `estoque_enviado INTEGER`, `estoque_enviado_em TEXT DEFAULT ''`, `estoque_calculado INTEGER`, `estoque_calculado_em TEXT DEFAULT ''`.
- Tabela `anuncio_estoque_log_v2026(id INTEGER PRIMARY KEY AUTOINCREMENT, criado_em TEXT, canal TEXT, conta TEXT, anuncio_id TEXT, modo TEXT, de INTEGER, para INTEGER, motivo TEXT, resultado TEXT, detalhe TEXT, ator TEXT)` (resultado ∈ `ok|erro|simulado|ignorado`).
- Tabela `anuncio_estoque_config_v2026(conta TEXT PRIMARY KEY, modo TEXT NOT NULL DEFAULT 'simulacao', atualizado_em TEXT, atualizado_por TEXT)`.
- `anuncio_estoque_modo_definir(canal, conta, anuncio_id, modo, ator) -> dict` (`manual|automatico`; só `controle='noromak'` e não-Full, senão `ValueError`; audit).
- `anuncio_estoque_config(conta) -> dict`, `anuncio_estoque_config_definir(conta, modo, ator) -> dict`.
- `anuncio_estoque_log(canal, conta, anuncio_id, limite=50) -> list[dict]`.
- `_anuncio_estoque_registrar(...)` (insere no log; guarda só os últimos 20.000).

- [ ] Testes: modo padrão manual; trocar para automático só em `noromak` (alephee/full = ValueError); config padrão `simulacao`; log grava e lista. Implementar. Rodar. Commit `V20.26 - Anuncios: modo de estoque por anuncio e registro`.

### Task 2: Gravar estoque no ML (uma função só)

**Produces:** `ml_estoque_gravar(conta: str, mlb: str, quantidade: int, *, ator: str, modo: str, motivo: str) -> dict` → `{ok, antes, depois, metodo, erro}`:
1. Dentro de `ml_na_casa(conta)`, fora do `DB_LOCK`, com trava por anúncio (`_ANUNCIO_ESTOQUE_TRAVAS[mlb]`): lê o anúncio (`/items/{id}?attributes=id,available_quantity,user_product_id,shipping,status`).
2. Full (`logistic_type=fulfillment`) → recusa, nada enviado.
3. Sem `user_product_id`, ou user-product com só um local `selling_address`: `PUT /items/{id}` `{"available_quantity": n}` (por `_ml_promo_http`, JSON).
4. User-product com depósitos (`/user-products/{id}/stock` tem `locations` de tipo `store`/`seller_warehouse`): `PUT /user-products/{id}/stock/type/selling_address` com o cabeçalho `x-version` lido no GET (409 = reler e tentar uma vez). Se não houver `selling_address`, erro claro ("anúncio com depósito do vendedor: ajuste no ML") — não adivinha o local.
5. Relê o anúncio; `ok` só se `available_quantity == n` (senão `erro` "o ML não mudou o estoque").
6. Registra no log e atualiza `estoque_enviado(_em)` só se ok. Amostras das respostas.
- Quantidade: inteiro 0..99999 (0 é permitido no Manual se o Admin digitar 0 — é decisão humana explícita); no Automático 0 só se o cálculo deu 0 com tudo lido.

- [ ] Testes (ML falso por `_ml_promo_http`/`ml_api_get`): item simples PUT + releitura ok; 200 sem mudar = erro (**RF3**); user-product com depósito vai pelo stock com `x-version`; 409 reler 1x; Full recusado sem chamada; conta Bauru usa `ml_na_casa("bauru")`. Commit `V20.26 - Anuncios: gravar estoque no ML com releitura`.

### Task 3: Cálculo do automático

**Produces:**
- Tabela `anuncio_apollo_v2026(codigo TEXT, filial TEXT, disponivel REAL, respondeu INTEGER, lido_em TEXT, lido_epoch INTEGER, PRIMARY KEY(codigo, filial))`.
- `anuncios_apollo_ler_proximo(filial: str) -> str | None` — rodízio (≤ `ANUNCIO_APOLLO_POR_MINUTO = 3`), prioridade: nunca lido > lido com disponível ≤ 3 há > 10 min > mais antigo; só códigos de anúncios em Automático daquela filial (peças do kit abertas).
- `anuncios_estoque_calcular(conta: str) -> list[dict]` — para cada anúncio `noromak` + `automatico` + não-Full da conta: componentes da ficha (kit pela composição; peça = ela mesma), disponível das leituras guardadas, pendentes da fila (`pendentes_por_filial`), `kit_da_para_montar`; `None` = sem envio (motivo "sem leitura do Apollo"/"fila não lida"). Devolve `{anuncio_id, ficha, atual_ml, calculado, motivo}`.

- [ ] Testes: peça; kit pela peça que dá menos (**RF5**: peça sem leitura → None); pendente desconta; Apollo sem resposta → None (**RF2**); rodízio respeita 3/min e a prioridade. Commit `V20.26 - Anuncios: calculo do estoque automatico pelo Apollo`.

### Task 4: Rodada e worker

**Produces:**
- `anuncios_estoque_rodada(conta: str, *, ator: str, forcar: bool = False) -> dict` — calcula; para cada anúncio com `calculado` diferente de `estoque_enviado` (ou do lido no ML): `simulacao` → registra `simulado`; `lote` → só envia com `forcar=True` (clique); `automatico` → envia. Antes de enviar, **relê sob o `DB_LOCK`** o modo do anúncio (Manual = desiste, **RF4**) e a versão da fila (mudou = recalcula esse anúncio, **RF1**).
- Worker `anuncios_estoque_loop(stop_event)` (20 s): lê 1 código do Apollo pelo rodízio por filial com anúncio em Automático; quando a versão de `conf_pedidos` ou uma leitura do Apollo muda, roda `anuncios_estoque_rodada` das contas afetadas. Registrar no `main()` com `_worker_protegido` e em `SAUDE_WORKERS_INFO`.
- Rotas: `GET /api/anuncios/estoque?conta=` (cálculo + config + últimos envios; módulo admin), `POST /api/anuncios/estoque/{modo,manual,config,enviar}` — perfil Admin; `config` para `automatico` = `require_owner_reauth`; `manual` = `{conta, anuncio_id, quantidade}` chama `ml_estoque_gravar` na hora; `enviar` = rodada com `forcar=True`.

- [ ] Testes: simulação não chama o ML; lote só no clique; automático envia só o que mudou; RF1 (fila mudou no meio) e RF4 (virou Manual); Manual envia na hora e registra; permissões (Supervisor 403, Consulta bloqueado, Automático sem OWNER = recusado); worker em `SAUDE_WORKERS_INFO`. Commit `V20.26 - Anuncios: rodada e worker do estoque automatico`.

### Task 5: Tela

- Na aba "Anúncios em todos os canais", em cada anúncio `noromak` não-Full: **"Controle de estoque: Manual · Automático"** (seletor; perfil Admin). Manual mostra campo de quantidade + **"Enviar ao ML"** (`nkConfirmar` com "de X para Y") e o resultado. Automático mostra o calculado ("Apollo X − a baixar Y = Z") ou o motivo de não enviar, e o último envio.
- Topo da lista `/anuncios`: painel **"Estoque automático"** por conta (Araçatuba/Bauru, segue o seletor de casa): modo (Simulação/Lote/Automático; Automático pede senha + 2FA), "Enviar agora" (no Lote), quantos anúncios em Automático, quantos mudariam, e o registro dos últimos envios (`data-nk-tabela`).
- Prévia: simulador do ML aceita PUT de estoque e responde à releitura; o Apollo simulado continua.
- [ ] Teste `test_tela_tem_controle_de_estoque`; `node --check`. Conferir na prévia. Commit `V20.26 - Anuncios: tela do controle de estoque`.

### Task 6: Versão, CLAUDE.md, `./testar.sh`

- [ ] `APP_VERSION`, `VERSAO.txt`, parágrafo V20.26 no CLAUDE.md (inclui a exceção "Noromak grava estoque no ML só em anúncio `noromak`"). `./testar.sh`. Commit `V20.26 - Anuncios fase 3: versao e documentacao`.
