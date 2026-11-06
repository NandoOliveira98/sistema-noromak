# TikTok Shop — etiqueta, vendas, acerto e cancelamento (V20.29) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completar a integração do TikTok Shop de Bauru: o servidor organiza o envio e vincula a etiqueta (PDF), a Conferência de Bauru imprime, o cancelado sai da fila, as vendas entram no Dashboard de Bauru e o acerto real de cada pedido fica guardado.

**Architecture:** Tudo em `servidor_web.py` ao lado do código TikTok da V19.63/64 (`tiktok_api`, `tiktok_importar_pedidos`, `_magalu_merge`). A etiqueta segue o modelo da Magalu (registro em `conf_etiquetas` vinculado ao pedido), mas o arquivo é PDF e **não** vai para o banco: o registro guarda só um marcador `TIKTOK_PDF:<pacote>` e a Conferência baixa o PDF na hora por uma rota própria, como o PDF do ML de Bauru (V18.95). Vendas vão para `vendas_conta_v1898` (`conta='bauru'`, `marketplace='tiktok'`); o acerto vai para tabela própria.

**Tech Stack:** Python só biblioteca padrão, SQLite, HTML/JS puro (`APP/conferencia.html`, `APP/admin.html`), `unittest` com `TESTES/base.py`.

**Spec:** `docs/superpowers/specs/2026-11-06-tiktok-amazon-design.md` (decisões 2 a 8; a parte da Amazon NÃO é deste plano).

## Global Constraints

- Só biblioteca padrão do Python; nada de dependência nova (CLAUDE.md §2).
- Regra do ADMIN: nada daqui desfaz override/tombstone/concluído; toda gravação da fila passa por `save_storage` com `remove_pending_identities` (V18.52).
- Endpoints do TikTok vieram de fontes de terceiros: toda chamada nova passa por `tiktok_api`, que já guarda a 1ª resposta em `tiktok_amostra_v1963`. Leitura tolerante (campo ausente = vazio/None, nunca zero).
- Desconhecido fica em branco, nunca zero (tarifa, valor do acerto).
- "Tudo desligado" (decisão 4): nada roda sem o app de parceiro conectado; a única ação que **escreve** no TikTok (organizar envio) tem chave própria `etiqueta` em `tiktok_config_v1964`, **desligada** por padrão; ligar = perfil Admin.
- Etiqueta em **PDF** (decisão 5); NF-e: o Noromak nunca envia nota, só mostra `AGUARDANDO NF` (decisão 6).
- Vendas só em `vendas_conta_v1898` com `conta='bauru'`; `vendas_v1799` e o Financeiro **não** mudam (decisão 7). `VENDAS_MARKETPLACES` não muda (o Financeiro itera sobre ele).
- Versão: `APP_VERSION = "20.29-TIKTOK-ETIQUETA-VENDAS"` em `servidor_web.py` e `VERSAO.txt`; commits `V20.29 - <descricao sem acentos>`; comentários `# V20.29: ...`.
- Conferência e Estação mantêm `alert()` (não trocar por aviso).
- Nunca publicar; só commit local (CLAUDE.md §0).

## Review Focus

1. **Pedido cancelado depois de o envio ser organizado** — o registro da etiqueta fica no banco, mas a linha sai da fila; a Conferência nunca acha o pedido, então nada é impresso. Teste em Task 3.
2. **`doc_url` vencido** — o link do documento do TikTok expira; a rota do PDF pede o link de novo a cada impressão, nunca guarda. Teste em Task 2.
3. **Link do documento fora do TikTok / não-https / não-PDF** — recusado sem baixar ou sem imprimir. Teste em Task 2.
4. **Organizar envio falhando sempre pelo mesmo motivo (sem NF)** — não pode virar uma chamada nova a cada 120 s com log a cada volta: `tiktok_log_event_se_mudou` + espera de `TIKTOK_ETIQUETA_REPETIR_S` (600 s) por pacote. Teste em Task 1.
5. **Venda de Bauru do TikTok somando no Financeiro ou em `vendas_v1799`** — não pode. Teste em Task 4.

---

### Task 1: Organizar envio e vincular a etiqueta (servidor)

**Files:**
- Modify: `servidor_web.py` (bloco TikTok perto de `tiktok_importar_pedidos`, ~9797-9905; constantes ~7247-7256)
- Test: `TESTES/test_tiktok_etiqueta_v2029.py` (novo; reaproveita `Base`, `_pedido`, `_conectar` copiando o padrão de `TESTES/test_tiktok_estoque_v1964.py`)

**Interfaces:**
- Produces:
  - `TIKTOK_ETIQUETA_REPETIR_S = 600`
  - `TIKTOK_CAMPOS_OPERACIONAIS` passa a incluir `"Etiqueta API TikTok"` e `"PacoteTikTok"`.
  - `tiktok_etiqueta_ligada() -> bool` (chave `etiqueta` em `tiktok_config_v1964`; sem linha = **False**) e `tiktok_etiqueta_definir(ligada: bool, usuario: str) -> None`.
  - `_tiktok_pacotes(pedido: dict) -> list[str]` — ids de `pedido["packages"][*]["id"]`, sem vazios, na ordem.
  - `tiktok_organizar_envio(pacote: str) -> None` — `tiktok_api("POST", f"/fulfillment/202309/packages/{pacote}/ship", body={})`.
  - `tiktok_documento_url(pacote: str) -> str` — `tiktok_api("GET", f"/fulfillment/202309/packages/{pacote}/shipping_documents", params={"document_type": "SHIPPING_LABEL", "document_size": "A6"})`, devolve `data.doc_url`; vazio = `RuntimeError`.
  - `class TikTokSemNota(RuntimeError)` — erro do TikTok cuja mensagem cita nota/invoice (`re.search(r"invoice|nota|nf-?e", msg, re.I)`).
  - `_tiktok_etiquetas_vinculadas() -> set[str]` (pacotes com registro `marketplace == "tiktok"`).
  - `_tiktok_guardar_etiqueta(pedido: str, pacote: str) -> None` — registro em `conf_etiquetas`: id `"etq_tiktok_api_" + sha1(pacote)[:18]`, `marketplace: "tiktok"`, `envio: f"TIKTOK_PDF:{pacote}"`, `metadados: {"pedido", "pacote"}`, `pedido`, `vinculadoA: [pedido]`, `chaves: [pedido, pacote]`, `origem/vinculo: "api_tiktok"` — mesma montagem de `_magalu_guardar_etiqueta` (`servidor_web.py:6996`).
  - `tiktok_etiquetas_rodada(pedidos: list[dict]) -> dict` → `{"novas": int, "aguardando_nf": int, "erros": int}`.

- [ ] **Step 1: Write the failing tests** em `TESTES/test_tiktok_etiqueta_v2029.py`, `class Etiqueta(Base)`; `_pedido` ganha `packages=[{"id": f"PK{numero}"}]`; o falso `tiktok_api` responde `/ship` com `{"code":0,"data":{}}` e `/shipping_documents` com `{"code":0,"data":{"doc_url":"https://p16-oec.tiktokcdn.com/x.pdf"}}`:
  - `test_comeca_desligada_e_desligada_nao_chama_o_ship`: `S.tiktok_etiqueta_ligada()` é False; `tiktok_rodada()` com `_pedido("TT1")` → nenhum `path` com `/ship` em `self.enviados`; a linha tem `"Etiqueta API TikTok" == ""`.
  - `test_ligada_organiza_e_vincula`: `tiktok_etiqueta_definir(True,"dono")`; rodada → `/fulfillment/202309/packages/PKTT1/ship` chamado 1 vez; registro em `conf_etiquetas` com `envio == "TIKTOK_PDF:PKTT1"`, `pedido == "TT1"`, `marketplace == "tiktok"`; linha com `"Etiqueta API TikTok" == "VINCULADA"` e `"PacoteTikTok" == "PKTT1"`.
  - `test_ja_vinculado_nao_organiza_de_novo`: duas rodadas → `/ship` 1 vez só.
  - `test_aguardando_collection_nao_chama_ship_so_documento`: pedido com status `AWAITING_COLLECTION` → nenhum `/ship`, um `/shipping_documents`, linha `VINCULADA`.
  - `test_sem_nota_mostra_aguardando_nf_e_nao_repete_antes_de_600s`: `/ship` levanta `RuntimeError("TikTok /ship: 21011001 invoice not uploaded")` → linha `"AGUARDANDO NF"`; 2ª rodada logo em seguida → `/ship` continua com 1 chamada; com `mock.patch.object(S.time, "time", return_value=time.time()+601)` → 2ª chamada.
  - `test_outro_erro_marca_erro_e_nao_trava_a_importacao`: `/ship` levanta `RuntimeError("TikTok HTTP 500")` → linha `"ERRO"`, pedido continua na fila, `resumo["erros"] == 1`.
  - `test_edicao_do_admin_fica_e_so_campos_operacionais_mudam`: linha importada; ADMIN troca `Nome Conferência` via `save_storage(..., actor_role="admin")`; rodada com etiqueta → `Nome Conferência` mantido e `Etiqueta API TikTok == "VINCULADA"`.

- [ ] **Step 2: Run to verify they fail**
Run: `python -m unittest TESTES.test_tiktok_etiqueta_v2029 -v`
Expected: FAIL/ERROR (`AttributeError: ... tiktok_etiqueta_ligada`).

- [ ] **Step 3: Implement** as interfaces acima. Em `tiktok_importar_pedidos`, depois de `tiktok_pedidos_abertos()` e antes de montar as linhas: `estado = tiktok_etiquetas_rodada(pedidos) if tiktok_etiqueta_ligada() else {}`; `_tiktok_linhas_do_pedido(pedido, products, estado_etiqueta)` preenche `PacoteTikTok` (1º pacote) e `Etiqueta API TikTok` (`VINCULADA` se o pacote está em `_tiktok_etiquetas_vinculadas()`, senão o que a rodada anotou: `AGUARDANDO NF` / `ERRO` / `AGUARDANDO`; desligada = `""`). Pedido só tem envio organizado se `status == "AWAITING_SHIPMENT"`; com `AWAITING_COLLECTION` só pede o documento. A espera por pacote fica em memória (`_TIKTOK_ETIQUETA_TENTATIVA: dict[str, float]`), erro de cada pacote em `tiktok_log_event_se_mudou("etiqueta", f"pedido={id} pacote={pk}", ...)`. As chamadas ao TikTok ficam **fora** do `DB_LOCK` (como a Magalu). Se `_tiktok_linhas_do_pedido` mudar de assinatura, ajustar a chamada existente com default `estado=None`.

- [ ] **Step 4: Run tests**
Run: `python -m unittest TESTES.test_tiktok_etiqueta_v2029 TESTES.test_tiktok_estoque_v1964 -v`
Expected: PASS (inclusive os testes antigos de pedidos).

- [ ] **Step 5: Commit**
```bash
git add servidor_web.py TESTES/test_tiktok_etiqueta_v2029.py
git commit -m "V20.29 - TikTok: organiza o envio e vincula a etiqueta (desligado por padrao)"
```

### Task 2: Rota do PDF e impressão na Conferência

**Files:**
- Modify: `servidor_web.py` (função perto de `ml_etiqueta_pdf`, ~12087; rota GET perto de `/api/etiqueta-ml-pdf`, ~40821)
- Modify: `APP/conferencia.html` (`normalizarMarketplace` ~1474, `nomeMarketplace`/`nomeMarketplacePlanilha` ~1497, `imprimirEtiquetaPdfV1895` ~2239, `prepararDadosImpressaoMarketplace` ~3486, `imprimirDadosComFallback` ~3584)
- Test: `TESTES/test_tiktok_etiqueta_v2029.py` (`class Pdf(Base)`, `class TelaConferencia(TesteNoromak)`)

**Interfaces:**
- Consumes: `tiktok_documento_url(pacote)`, `_filial_linhas_do_pedido(pedido)`, `filial_da_linha`, `FILIAL_TODAS`.
- Produces:
  - `_tiktok_baixar_pdf(url: str) -> bytes` (só a rede; isolada para teste; timeout 30 s).
  - `tiktok_etiqueta_pdf(pedido: str, filial: str) -> bytes` — mesmo contrato de `ml_etiqueta_pdf`: `ValueError` (fora da fila / sem pacote), `PermissionError` (outra filial), `RuntimeError` (link inválido / não é PDF). Pede `tiktok_documento_url` **a cada chamada** (nunca guarda o link). Aceita só `https://` com host terminando em `.tiktokcdn.com`, `.tiktokcdn-us.com`, `.ibytedtos.com` ou `.tiktokglobalshop.com` (`TIKTOK_PDF_HOSTS`, tupla); o resto = `RuntimeError("Link da etiqueta fora do TikTok")` sem baixar. Bytes sem `%PDF` = `RuntimeError`.
  - `GET /api/etiqueta-tiktok-pdf?pedido=` — mesmas permissões, códigos (403 `OUTRA_FILIAL`, 404, 502) e `audit(..., "etiqueta.tiktok.pdf", pedido)` da rota do ML.
  - JS: `normalizarMarketplace` devolve `"tiktok"` para texto com `tiktok`; `nomeMarketplace("tiktok") === "TikTok Shop"`, `nomeMarketplacePlanilha("tiktok") === "TIKTOK"`; `imprimirEtiquetaPdfV1895(pedido, data, rota = "/expedicao/api/etiqueta-ml-pdf")`.

- [ ] **Step 1: Write the failing tests**
  - `Pdf.test_baixa_o_link_novo_a_cada_impressao`: pedido TT1 vinculado na fila; `mock _tiktok_baixar_pdf` devolve `b"%PDF-1.4..."`; duas chamadas de `S.tiktok_etiqueta_pdf("TT1","bauru")` → `/shipping_documents` chamado 2 vezes.
  - `Pdf.test_link_fora_do_tiktok_recusado_sem_baixar`: `doc_url = "https://evil.example.com/x.pdf"` → `RuntimeError`, `_tiktok_baixar_pdf` não chamado; idem `http://p16-oec.tiktokcdn.com/x.pdf`.
  - `Pdf.test_nao_pdf_recusado`: bytes `b"<html>"` → `RuntimeError`.
  - `Pdf.test_outra_filial_403`: `S.tiktok_etiqueta_pdf("TT1","aracatuba")` → `PermissionError`; via HTTP (`self.cliente` do `base.py`, usuário de Araçatuba) `GET /expedicao/api/etiqueta-tiktok-pdf?pedido=TT1` → 403 com `code == "OUTRA_FILIAL"`.
  - `Pdf.test_fora_da_fila_404`: pedido inexistente → `ValueError`; HTTP → 404.
  - `TelaConferencia.test_conferencia_imprime_tiktok_pelo_pdf`: lê `APP/conferencia.html` e confere: `includes("tiktok")) return "tiktok"` dentro de `normalizarMarketplace`; a string `"/expedicao/api/etiqueta-tiktok-pdf"`; em `imprimirDadosComFallback`, o ramo `normalizarMarketplace(marketplace) === "tiktok"` aparece **antes** do teste de Bauru do ML; `prepararDadosImpressaoMarketplace` tem ramo `marketplace === "tiktok"`; nenhuma ocorrência nova de `nkToast` no arquivo (a Conferência fica com `alert()`).

- [ ] **Step 2: Run to verify they fail**
Run: `python -m unittest TESTES.test_tiktok_etiqueta_v2029 -v`
Expected: FAIL (`tiktok_etiqueta_pdf` não existe; strings ausentes).

- [ ] **Step 3: Implement** servidor (funções + rota, copiando a rota do ML e trocando a função/auditoria) e Conferência: `prepararDadosImpressaoMarketplace` com `tiktok` → `data = [registro.envio]` (exige `registro.envio` começando com `TIKTOK_PDF:`, senão `throw new Error("A etiqueta do TikTok não está disponível.")`); `imprimirDadosComFallback` com `tiktok` → `return await imprimirEtiquetaPdfV1895(pedido, data, "/expedicao/api/etiqueta-tiktok-pdf")` em qualquer filial; mensagem de erro do PDF continua "Etiqueta em PDF indisponível: ...".

- [ ] **Step 4: Run tests**
Run: `python -m unittest TESTES.test_tiktok_etiqueta_v2029 -v` e `node --check` não se aplica (HTML); Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add servidor_web.py APP/conferencia.html TESTES/test_tiktok_etiqueta_v2029.py
git commit -m "V20.29 - TikTok: etiqueta em PDF na Conferencia de Bauru"
```

### Task 3: Cancelamento

O cancelado já sai hoje: `tiktok_pedidos_abertos` só busca `AWAITING_SHIPMENT/COLLECTION` e `_magalu_merge` tira, por remoção nomeada, a linha da API cujo pedido sumiu. Este task **prende** o comportamento em teste e fecha o que falta: não organizar envio de pedido com cancelamento pedido pelo comprador.

**Files:**
- Modify: `servidor_web.py` (`tiktok_etiquetas_rodada`)
- Test: `TESTES/test_tiktok_etiqueta_v2029.py` (`class Cancelamento(Base)`)

**Interfaces:**
- Produces: `_tiktok_cancelamento_pedido(pedido: dict) -> bool` — True se `pedido.get("cancel_reason")`, `pedido.get("cancellation_initiator")` ou `pedido.get("is_on_hold")` vierem preenchidos (leitura tolerante; nomes a conferir na amostra).

- [ ] **Step 1: Write the failing tests**
  - `test_cancelado_sai_da_fila`: TT1 importado; próxima resposta sem TT1 → TT1 fora da fila.
  - `test_linha_protegida_pelo_admin_fica`: ADMIN altera a `Quantidade` da linha do TT1 (override); TT1 some da API → a linha do ADMIN continua na fila (mesma regra da V18.97).
  - `test_concluido_nao_volta`: TT1 concluído (`/api/order-complete` ou a função usada em `test_conferencia.py`); TT1 volta na API → não reaparece na fila.
  - `test_unidade_cancelada_baixa_a_quantidade`: dois `line_items` do mesmo SKU, um com `display_status: "CANCELLED"` → `Quantidade == "1"`.
  - `test_cancelado_depois_de_vinculado_nao_imprime`: TT1 vinculado (Task 1); TT1 some → `S.tiktok_etiqueta_pdf("TT1","bauru")` levanta `ValueError` (fora da fila).
  - `test_pedido_com_cancelamento_pedido_nao_organiza_envio` (falha hoje): etiqueta ligada; `_pedido("TT1")` com `"cancel_reason": "Buyer requested"` → nenhum `/ship`; linha continua na fila com `"Etiqueta API TikTok" == "AGUARDANDO"`.

- [ ] **Step 2: Run** `python -m unittest TESTES.test_tiktok_etiqueta_v2029.Cancelamento -v` → só o último falha.
- [ ] **Step 3: Implement** `_tiktok_cancelamento_pedido` e pular o `/ship` em `tiktok_etiquetas_rodada` quando True (o documento também não é pedido).
- [ ] **Step 4: Run** `python -m unittest TESTES.test_tiktok_etiqueta_v2029 -v` → PASS.
- [ ] **Step 5: Commit** `git commit -m "V20.29 - TikTok: pedido com cancelamento nao organiza envio; testes do cancelado"`

### Task 4: Vendas do TikTok no Dashboard de Bauru

**Files:**
- Modify: `servidor_web.py` (bloco de vendas ~24070-24630; worker novo; `SAUDE_WORKERS_INFO` ~46856; `main()` ~47887)
- Modify: `APP/admin.html` (Dashboard de vendas ~5581-5640: canal, legenda e cor `tiktok`)
- Test: `TESTES/test_tiktok_vendas_v2029.py` (novo)

**Interfaces:**
- Produces:
  - `TIKTOK_VENDAS_STATUS_FORA = ("UNPAID", "CANCELLED")`, `TIKTOK_VENDAS_INTERVALO = 600`.
  - `VENDAS_MARKETPLACES_DASHBOARD = {**VENDAS_MARKETPLACES, "tiktok": "TikTok Shop"}` — usado **só** em `vendas_dashboard_payload` (linhas 24541, 24596, 24599, 24625) e no `top()` (`un_tiktok` em `por_marketplace`).
  - `tiktok_vendas_janela(date_from: str, date_to: str) -> dict` — `POST /order/202309/orders/search` com `create_time_ge/lt` (epoch de Brasília), paginado como `tiktok_pedidos_abertos`; registros `(marketplace="tiktok", order_id, line_key=sku_id, pack_id="", sale_at, sale_day, sku=seller_sku, anuncio=product_id, title, qty, amount=soma de sale_price das unidades, status, updated_at)`; status em `TIKTOK_VENDAS_STATUS_FORA` fica fora; `complete = not truncou`.
  - `_tiktok_vendas_gravar(date_from, date_to, records, complete) -> None` — `vendas_conta_v1898` com `conta=TIKTOK_CASA`, `marketplace='tiktok'`; só apaga a janela (dessa conta + marketplace) quando `complete`.
  - `tiktok_vendas_sync() -> dict` — últimos 3 dias sempre; 30 dias uma vez por dia (marca `_vendas_meta("resync_30d_tiktok@bauru")`).
  - Worker `tiktok_vendas_loop(stop_event)` (600 s; só com `tiktok_status_payload()["connected"]`), em `SAUDE_WORKERS_INFO` como `("Vendas do TikTok Shop (Bauru)", False, 30)`, iniciado em `main()` com `_worker_protegido`.

- [ ] **Step 1: Write the failing tests**
  - `test_venda_entra_nas_vendas_de_bauru`: falso `orders/search` com 1 pedido `PAID`/`AWAITING_SHIPMENT`, 2 unidades de `sale_price "19.90"` → linha em `vendas_conta_v1898` com `conta='bauru'`, `marketplace='tiktok'`, `qty=2`, `amount=39.8`.
  - `test_nao_vai_para_vendas_v1799_nem_financeiro`: depois do sync, `SELECT COUNT(*) FROM vendas_v1799 WHERE marketplace='tiktok'` = 0; `financeiro_payload` (mesma chamada de `TESTES/test_financeiro.py`) sem chave `tiktok` em `por_mp`.
  - `test_cancelado_e_nao_pago_ficam_fora`: pedidos `CANCELLED` e `UNPAID` → 0 linhas.
  - `test_janela_incompleta_nao_apaga`: venda gravada; nova busca truncada (`complete=False`) sem ela → continua.
  - `test_dashboard_bauru_mostra_tiktok`: `vendas_dashboard_payload(hoje, hoje, conta="bauru")["por_marketplace"]["tiktok"]["unidades"] == 2`; `marketplace="tiktok"` filtra.
  - `test_sync_nao_mexe_nas_vendas_do_ml_de_bauru`: linha `('bauru','ml',...)` existente sobrevive a uma janela completa do TikTok.
  - `test_worker_no_painel_de_saude`: `"tiktok_vendas_loop" in S.SAUDE_WORKERS_INFO`.
  - `test_dashboard_tela_tem_o_canal`: `APP/admin.html` contém `"tiktok"` na ordem do gráfico empilhado (`const ordem=[`) e na legenda `v1799Legenda`.

- [ ] **Step 2: Run** `python -m unittest TESTES.test_tiktok_vendas_v2029 -v` → FAIL.
- [ ] **Step 3: Implement** servidor e tela (`ordem=["tiktok","shopee","ml"]`, legenda com um `<span>` TikTok, cor própria via variável `--nk-*` existente; o array de filtro da legenda `["ml","shopee"]` vira `["ml","shopee","tiktok"]`). Se `admin.html` tiver regra de cor nova, rodar `python FERRAMENTAS_WINDOWS/gerar_modo_escuro.py APP/admin.html`.
- [ ] **Step 4: Run** `python -m unittest TESTES.test_tiktok_vendas_v2029 TESTES.test_vendas_conta_v1898 TESTES.test_financeiro -v` → PASS.
- [ ] **Step 5: Commit** `git commit -m "V20.29 - TikTok: vendas de Bauru no Dashboard"`

### Task 5: Acerto real do pedido (tarifa)

**Files:**
- Modify: `servidor_web.py` (schema perto de `tiktok_amostra_v1963` ~2184; funções no bloco de vendas do Task 4)
- Test: `TESTES/test_tiktok_vendas_v2029.py` (`class Acerto`)

**Interfaces:**
- Produces:
  - Tabela `tiktok_acerto_v2029(order_id TEXT PRIMARY KEY, receita REAL, tarifa REAL, frete REAL, liquido REAL, bruto_json TEXT, lido_em TEXT, tentado_em TEXT, erro TEXT)` — colunas de valor **NULL** quando o TikTok não informa.
  - `tiktok_acerto_ler(order_id: str) -> dict` — `GET /finance/202501/orders/{order_id}/statement_transactions`; se falhar com 404/`path not found`, tenta `GET /finance/202309/orders/{order_id}/statement_transactions`. Soma tolerante de `data.statement_transactions[*]`: `revenue_amount`→receita, `fee_amount`/`fee_tax_amount`→tarifa (valor absoluto), `shipping_cost_amount`→frete, `settlement_amount`→liquido. Campo ausente em todas as transações = None.
  - `tiktok_acerto_pendentes(limite: int = 20) -> list[str]` — vendas TikTok de Bauru com `sale_day` ≤ hoje − 3 e ≥ hoje − 60, sem `lido_em`, com `tentado_em` vazio ou > 6 h.
  - Roda no fim de cada volta do `tiktok_vendas_loop`.

- [ ] **Step 1: Write the failing tests**
  - `test_grava_os_valores_do_acerto`: resposta com uma transação (`revenue_amount "39.80"`, `fee_amount "-4.38"`, `shipping_cost_amount "-6.00"`, `settlement_amount "29.42"`) → linha com `receita 39.8`, `tarifa 4.38`, `frete 6.0`, `liquido 29.42`.
  - `test_campo_ausente_fica_nulo_nunca_zero`: transação sem `shipping_cost_amount` → `frete IS NULL`.
  - `test_cai_para_202309_quando_202501_nao_existe`: 1ª chamada levanta `RuntimeError("TikTok HTTP 404: path not found")` → 2ª com `/finance/202309/...`, grava.
  - `test_erro_espera_6h`: erro de rede → `tentado_em` gravado, `lido_em` vazio; `tiktok_acerto_pendentes()` não traz o pedido antes de 6 h.
  - `test_venda_recente_ainda_nao_e_lida`: venda de hoje fora dos pendentes.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `python -m unittest TESTES.test_tiktok_vendas_v2029 -v` → PASS.
- [ ] **Step 5: Commit** `git commit -m "V20.29 - TikTok: acerto real do pedido guardado"`

### Task 6: Cartão em Integrações, versão e documentação

**Files:**
- Modify: `servidor_web.py` (rota POST junto de `/api/integracoes/tiktok/importacao`, ~43664; `tiktok_status_payload` ~6429 passa `etiqueta_ligada`; `APP_VERSION` linha 52)
- Modify: `APP/admin.html` (cartão TikTok, perto de ~9950)
- Modify: `VERSAO.txt`, `CLAUDE.md` (seção TikTok: parágrafo **V20.29**)
- Test: `TESTES/test_tiktok_etiqueta_v2029.py` (`class Rotas`)

**Interfaces:**
- Produces: `POST /api/integracoes/tiktok/etiqueta {ligada: bool}` — perfil Admin (mesma checagem da rota `importacao`), audit `tiktok.etiqueta`, `tiktok_log_event("etiqueta","", "LIGADA"/"DESLIGADA", f"por {usuario}")`; Consulta recebe 403 pelo `consulta_bloqueada`.

- [ ] **Step 1: Write the failing tests**
  - `test_ligar_etiqueta_so_perfil_admin`: Supervisor → 403; Admin → 200 e `tiktok_etiqueta_ligada()` True.
  - `test_status_mostra_a_chave`: `tiktok_status_payload()["etiqueta_ligada"]` reflete a chave.
  - `test_tela_tem_o_botao`: `APP/admin.html` contém `"/expedicao/api/integracoes/tiktok/etiqueta"`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** rota, payload, botão "Etiqueta automática: Ligar/Desligar" no cartão (igual ao da importação, com `nkConfirmar("Ligar faz o Noromak organizar o envio no TikTok ...")`), `APP_VERSION = "20.29-TIKTOK-ETIQUETA-VENDAS"`, `VERSAO.txt` igual, parágrafo V20.29 no CLAUDE.md (o que liga, PDF por rota, NF esperada, vendas em `vendas_conta_v1898`, acerto, testes).
- [ ] **Step 4: Run the whole suite**
Run: `./testar.sh`
Expected: todos os testes OK (incluindo `test_boot.py`, que confere `VERSAO.txt == APP_VERSION`).
- [ ] **Step 5: Commit** `git commit -m "V20.29 - TikTok: cartao com etiqueta automatica, versao e CLAUDE.md"`

Depois: skill `revisar-antes-de-subir` e pedir o "pode subir" (CLAUDE.md §0). A 1ª resposta real de `/ship`, `/shipping_documents`, `statement_transactions` e os campos de cancelamento ficam em `tiktok_amostra_v1963` para conferir quando o app de parceiro for aprovado.
