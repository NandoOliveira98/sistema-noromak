# Agente de anúncios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No rascunho do anúncio do ML, o Admin pede ao agente do Estúdio (Claude no PC do dono) uma proposta de 3 títulos, descrição e atributos obrigatórios, revisa e aceita; o aceito vai ao rascunho e aos campos vazios da ficha.

**Architecture:** Reaproveita a fila do Estúdio (`imagem_trabalho_v2017`) com `tipo='anuncio'`: o agente já busca `/api/imagens/agente/proximo` e baixa `/agente/arquivo` pelo túnel, então nenhuma rota nova precisa passar pelo nginx a não ser a da entrega. Duas etapas automáticas: `anuncio_textos` (títulos + descrição) → o servidor pede a categoria ao ML pelo título recomendado e os atributos da categoria → `anuncio_atributos`. O servidor nunca chama o Claude; o agente nunca chama o ML.

**Tech Stack:** Python só biblioteca padrão (servidor); agente em `FERRAMENTAS_WINDOWS/agente_imagens/` (Python + Pillow + SDK `anthropic`); HTML/JS puro (`APP/anuncios.html`); `unittest` com `TESTES/base.py`.

**Spec:** `docs/superpowers/specs/2026-11-06-agente-anuncios-design.md`

## Global Constraints

- Servidor sem dependência externa; o Claude só roda no agente.
- O agente e esta função **nunca** escrevem no ML: Conferir e Publicar continuam só pelo clique (`rascunho/validar`, `rascunho/publicar`).
- Pedir e aceitar = **perfil Admin** (`app_role == "admin"`); Supervisor/Consulta só veem (GET com módulo admin).
- Desconhecido = **em branco**, nunca inventado; descrição usa `[CONFIRMAR]` no que faltar.
- Título ≤ **60** caracteres; 3 opções, uma `recomendado: true`.
- Atributo com lista de valores do ML só aceita um `value_id`/nome da lista que o ML devolveu; fora da lista = em branco.
- Ficha: o aceito só preenche campo **vazio** de `ANUNCIO_CAMPOS_FICHA` que a proposta cobre (`nome`, `marca`, `modelo`, `descricao`, `categoria_ml`), pelo `ficha_salvar` (origem admin). Campo com valor ou "apagado pelo Admin" nunca muda.
- Modelo = `_imagem_cfg("modelo", IMAGEM_MODELO_PADRAO)`; gasto em `imagem_gasto_v2017` com `origem='anuncio'` (coluna nova; o que existe = `'imagem'`).
- Só Mercado Livre.
- Versão: **V20.31** (a V20.30 é de outra sessão; conferir `APP_VERSION` antes da Task 6). Commits `V20.31 - <descricao sem acentos>`, comentários `# V20.31: ...`.
- Nunca publicar (CLAUDE.md §0).

## Review Focus

1. **Rascunho editado enquanto o agente trabalha** — a proposta nunca sobrescreve; aceitar manda a `versao` do rascunho e 409 `CONFLITO` se mudou. Teste na Task 3.
2. **ML sem resposta entre as duas etapas** (categoria ou atributos) — a proposta fica pronta só com títulos e descrição e `mensagem` explica; nada vira erro do trabalho. Teste na Task 2.
3. **Proposta com atributo fora da lista do ML ou id que a categoria não tem** — descartado na entrega, nunca chega à tela. Teste na Task 2.
4. **Trabalho apagado/refeito ou rascunho publicado enquanto o agente entrega** — 409 `ImagemConflito`, nada gravado; aceitar proposta de rascunho que não está em `ANUNCIO_RASCUNHO_EDITAVEL` = 400. Testes nas Tasks 2 e 3.
5. **Kit com peça sem ficha nem foto** — vai só com código e descrição do Precificador; nenhum erro. Teste na Task 1.

---

### Task 1: Pedido do Admin entra na fila

**Files:**
- Modify: `servidor_web.py` (schema em `ensure_imagens_schema` ~40555; funções novas perto de `imagens_agente_proximo` ~41036; rota POST perto de `/api/anuncios/rascunho` e GET perto de `/api/anuncios/rascunhos`)
- Test: `TESTES/test_agente_anuncios_v2031.py` (novo)

**Interfaces:**
- Produces:
  - Colunas novas: `imagem_trabalho_v2017.rascunho_id INTEGER` (NULL nos trabalhos de imagem) e `imagem_gasto_v2017.origem TEXT NOT NULL DEFAULT 'imagem'` (ALTER no `ensure_imagens_schema`).
  - `IMAGEM_TIPOS["anuncio"] = "Texto do anúncio"`.
  - `anuncio_agente_pedir(rascunho_id: int, ator: str) -> dict` — lê o rascunho (precisa estar em `ANUNCIO_RASCUNHO_EDITAVEL`, senão `ValueError`) e a ficha (`ficha_ler`); com trabalho do mesmo rascunho em `aguardando|analisando|aguardando_atributos` → `ImagemConflito("Já tem um pedido ao agente para este rascunho.")`. Copia para `_imagem_pasta(id)` até 12 fotos da ficha (`anuncio_fotos` → arquivo em `ANUNCIO_FOTOS_DIR`) como `original_N.ext` com `papel: "original"`; ficha de kit: componentes do Cadastro de Kits (`sku`, `quantidade`) e, por peça, os campos da ficha e até 3 fotos (`papel: "peca"`, `peca: sku`); peça sem ficha = `{"sku", "quantidade", "descricao"}` do Precificador. `produto_json` = `{"rascunho_id", "conta", "codigo", "ficha": campos, "tipo_ficha", "kit": [...], "nome", "link"}`. Situação `aguardando`; devolve `{"ok": True, "trabalho_id": id}`.
  - `anuncio_agente_estado(rascunho_id: int) -> dict | None` — o trabalho mais novo do rascunho (`id`, `situacao`, `mensagem`, `proposta` = `textos_json` decodificado, `atualizado_em`).
  - `POST /api/anuncios/agente/pedir {rascunho_id, nome?, link?}` — perfil Admin, audit `anuncios.agente.pedir`; 409 no conflito.
  - `GET /api/anuncios/agente?rascunho=` — módulo admin.

- [ ] **Step 1: Write the failing tests** (`class Pedido(TesteNoromak)`; criar ficha com `S.ficha_salvar("ABC123", {"nome": "Limpador X", "marca": "Vonixx"}, "admin", None)`, uma foto gravando direto em `anuncio_foto_v2024` + arquivo em `S.ANUNCIO_FOTOS_DIR`, e um rascunho com `S.anuncio_rascunho_salvar({"ficha_codigo": "ABC123", "conta": "aracatuba"}, "admin", None)`):
  - `test_pedido_entra_na_fila_com_as_fotos_da_ficha`: `S.anuncio_agente_pedir(rid, "admin")` → linha `tipo='anuncio'`, `situacao='aguardando'`, `rascunho_id=rid`; `arquivos_json` com 1 item `papel="original"`; o arquivo existe na pasta do trabalho; `produto_json.ficha.marca == "Vonixx"`.
  - `test_segundo_pedido_do_mesmo_rascunho_e_recusado`: 2ª chamada → `ImagemConflito`.
  - `test_rascunho_publicado_nao_pede`: rascunho com `situacao='publicado'` → `ValueError`.
  - `test_kit_leva_as_pecas_e_peca_sem_ficha_vai_so_com_codigo`: ficha "KITX" tipo kit com peças "ABC123" (com ficha) e "ZZZ999" (sem ficha, no Precificador com descrição "Pano"); `produto_json.kit` tem as duas, a 2ª `{"sku": "ZZZ999", "quantidade": 1, "descricao": "Pano"}` sem fotos.
  - `test_so_perfil_admin_pede`: Supervisor (`permissions={"conferencia": True, "admin": True}`) `POST /api/anuncios/agente/pedir` → 403; Admin → 200.
- [ ] **Step 2: Run** `python -m unittest test_agente_anuncios_v2031` (em `TESTES/`) → FAIL (`AttributeError`).
- [ ] **Step 3: Implement** as interfaces acima. A cópia das fotos é feita **fora** do `DB_LOCK` (como o `imagens_enviar`); falha no meio apaga a pasta e não deixa linha.
- [ ] **Step 4: Run** os testes → PASS; `python -m unittest test_imagens_v2017` → PASS (a fila de imagens não muda).
- [ ] **Step 5: Commit** `V20.31 - Agente de anuncios: pedido do Admin entra na fila do agente`

### Task 2: Etapas do agente e categoria pelo ML

**Files:**
- Modify: `servidor_web.py` (`imagens_agente_proximo`, rota POST do agente ~43031, funções novas)
- Test: `TESTES/test_agente_anuncios_v2031.py` (`class Etapas`)

**Interfaces:**
- Consumes: Task 1 (`rascunho_id`, `produto_json`), `ml_categoria_sugerir(titulo, conta)`, `ml_categoria_atributos(categoria, conta)`.
- Produces:
  - `imagens_agente_proximo`: `tipo='anuncio'` em `aguardando` → `etapa: "anuncio_textos"`, situação `analisando`; em `aguardando_atributos` → `etapa: "anuncio_atributos"`, situação `analisando`, e o trabalho leva `atributos` (lista de `ml_categoria_atributos(...)["atributos"]` só com `obrigatorio` ou `condicional`) e `proposta` (títulos/descrição já aceitos na etapa 1). Reserva vencida de `anuncio` volta para a situação de onde saiu (`aguardando` ou `aguardando_atributos`).
  - `POST /api/imagens/agente/anuncio` (token do agente, mesmo `imagem_agente_auth`) → `anuncio_agente_entregar(req: dict) -> dict`:
    - etapa `anuncio_textos`: `{"id", "titulos": [{"texto", "recomendado"}], "descricao", "fontes": {...}, tokens...}`. Valida: 1 a 3 títulos, cada ≤ 60 (maiores são cortados fora da lista, não truncados), exatamente um recomendado (senão o 1º). Grava em `textos_json` = `{"titulos", "descricao", "fontes"}`. Depois, **fora do DB_LOCK**: `ml_categoria_sugerir(recomendado, conta)` → 1ª categoria → `ml_categoria_atributos`; sucesso = grava `categoria`, `categoria_nome`, `categorias` (as sugeridas) e `situacao='aguardando_atributos'`; erro do ML ou sem categoria = `situacao='proposta'` com `mensagem` "Categoria não lida no ML: …" (nada vira erro).
    - etapa `anuncio_atributos`: `{"id", "atributos": [{"id", "valor", "valor_id", "origem", "fonte"}]}`. Mantém só id presente na lista da categoria; com lista de valores, só `valor_id`/nome que está nela (normaliza o nome do valor da lista); `origem` ∈ `ficha|rotulo|catalogo|internet`. Grava em `textos_json.atributos`, `situacao='proposta'`.
    - `situacao: "erro"` + `mensagem` = trabalho em erro (como no Estúdio).
    - Trabalho fora de `analisando` ou apagado = `ImagemConflito` (409). Gasto: `_imagem_registrar_gasto(..., origem="anuncio")` (a função ganha `origem: str = "imagem"`).
  - `anuncio_agente_trocar_categoria(trabalho_id: int, categoria: str, ator: str) -> dict` + `POST /api/anuncios/agente/atributos {trabalho_id, categoria}` (perfil Admin): trabalho em `proposta` → lê os atributos da categoria (fora do lock), grava `categoria` e volta a `aguardando_atributos` (só os atributos são pedidos de novo); outra situação = 409.
  - `imagens_saldo` passa a devolver `por_origem: {"imagem": usd, "anuncio": usd}` do ciclo.

- [ ] **Step 1: Write the failing tests** (`mock.patch.object(S, "ml_categoria_sugerir", return_value=[{"categoria": "MLB1234", "categoria_nome": "Limpadores"}])` e `ml_categoria_atributos` devolvendo `{"atributos": [{"id": "BRAND", "obrigatorio": True, "valores": []}, {"id": "VOLUME", "obrigatorio": True, "valores": []}, {"id": "FORMAT", "obrigatorio": True, "valores": [{"id": "1", "nome": "Líquido"}]}, {"id": "COLOR", "obrigatorio": False, "valores": []}]}`; o agente é simulado chamando `S.imagens_agente_proximo()` e `S.anuncio_agente_entregar(...)`):
  - `test_proximo_entrega_a_etapa_de_textos`: `etapa == "anuncio_textos"`, `produto.ficha.marca == "Vonixx"`.
  - `test_textos_viram_categoria_e_pedem_os_atributos`: entrega com 3 títulos → `situacao='aguardando_atributos'`, `textos.categoria == "MLB1234"`; próximo `proximo()` → `etapa == "anuncio_atributos"` e `atributos` com BRAND, VOLUME, FORMAT (sem COLOR).
  - `test_titulo_acima_de_60_sai_da_lista`: um título de 61 caracteres não fica em `textos.titulos`.
  - `test_ml_sem_resposta_deixa_a_proposta_so_com_textos`: `ml_categoria_sugerir` levanta `RuntimeError` → `situacao='proposta'`, `mensagem` começa com "Categoria não lida no ML", títulos e descrição gravados.
  - `test_atributo_fora_da_lista_e_descartado`: entrega com `FORMAT` valor "Gel" (fora da lista) e `COLOR` (fora da categoria) → nenhum dos dois em `textos.atributos`; `BRAND` "Vonixx" origem `ficha` fica.
  - `test_entrega_de_trabalho_apagado_e_409`: apagar o trabalho antes de entregar → `ImagemConflito`.
  - `test_gasto_marcado_como_anuncio`: entrega com `tokens_entrada=1000` → linha em `imagem_gasto_v2017` com `origem='anuncio'`; `S.imagens_saldo()["por_origem"]["anuncio"] > 0`.
  - `test_trocar_categoria_pede_so_os_atributos`: em `proposta`, `anuncio_agente_trocar_categoria(id, "MLB999", "admin")` → `aguardando_atributos` com títulos intactos.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `test_agente_anuncios_v2031 test_imagens_v2017` → PASS.
- [ ] **Step 5: Commit** `V20.31 - Agente de anuncios: etapas de textos e atributos com a categoria do ML`

### Task 3: Aceitar a proposta

**Files:**
- Modify: `servidor_web.py`
- Test: `TESTES/test_agente_anuncios_v2031.py` (`class Aceitar`)

**Interfaces:**
- Consumes: Task 2 (`textos_json`), `anuncio_rascunho_salvar(dados, ator, versao)`, `ficha_salvar(codigo, campos, ator, versao)`, `ficha_ler`.
- Produces: `anuncio_agente_aceitar(trabalho_id: int, escolha: dict, ator: str, versao_rascunho: int) -> dict` + `POST /api/anuncios/agente/aceitar {trabalho_id, titulo, descricao: bool, atributos: [ids], versao_rascunho}` (perfil Admin, audit `anuncios.agente.aceitar`). `titulo` tem de ser um dos títulos da proposta (sem edição aqui: o Admin edita no rascunho depois); grava no rascunho `titulo`, `categoria` (da proposta), `descricao` (se `descricao: true`) e só os atributos escolhidos (junto dos que o rascunho já tem; o escolhido vence) pelo `anuncio_rascunho_salvar` com a `versao` recebida (conflito = 409 `CONFLITO` vindo dele). Depois preenche na ficha, só se vazio e não "apagado pelo Admin": `descricao`, `marca` (atributo `BRAND`), `modelo` (`MODEL`), `categoria_ml`, `nome` (título escolhido); usa a `versao` atual da ficha. Marca o trabalho `situacao='aceita'`. Rascunho fora de `ANUNCIO_RASCUNHO_EDITAVEL` = `ValueError` (400).

- [ ] **Step 1: Write the failing tests:**
  - `test_aceitar_grava_no_rascunho`: titulo, categoria, descricao e BRAND no rascunho; `situacao` do trabalho `aceita`.
  - `test_rascunho_editado_depois_do_pedido_da_409`: salvar o rascunho (versão sobe) e aceitar com a versão antiga → `FichaConflito` (é o que `anuncio_rascunho_salvar` levanta; a rota devolve o mesmo 409 `CONFLITO` da tela); rascunho intacto e trabalho continua `proposta`.
  - `test_ficha_so_ganha_o_que_esta_vazio`: ficha com `marca="Vonixx"` (admin) e sem `descricao`; proposta com BRAND "Outra" → ficha continua "Vonixx" e ganha a descrição.
  - `test_campo_apagado_pelo_admin_nao_volta`: ficha com `descricao` apagada pelo Admin (origem admin, valor vazio) → continua vazia.
  - `test_titulo_que_nao_esta_na_proposta_e_recusado`: `ValueError`.
  - `test_rascunho_publicado_nao_aceita`: `ValueError`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `V20.31 - Agente de anuncios: aceitar grava no rascunho e nos vazios da ficha`

### Task 4: Agente no PC

**Files:**
- Modify: `FERRAMENTAS_WINDOWS/agente_imagens/agente_imagens.py` (`_processar`, `Noromak.anuncio`, etapas novas), `FERRAMENTAS_WINDOWS/agente_imagens/claude_imagens.py` (dois métodos novos), `FERRAMENTAS_WINDOWS/agente_imagens/LEIA-ME.md`
- Test: `FERRAMENTAS_WINDOWS/agente_imagens/test_agente.py` (`class AnuncioDoAgente`, com o `ClaudeFalso` existente)

**Interfaces:**
- Consumes: Task 2 (trabalho com `etapa` `anuncio_textos|anuncio_atributos`, rota `/api/imagens/agente/anuncio`).
- Produces:
  - `Noromak.anuncio(corpo: dict) -> None` → `POST /api/imagens/agente/anuncio`.
  - `ClaudeImagens.propor_anuncio_textos(fotos: list[Image], produto: dict, catalogo, pesquisa) -> dict` — esquema JSON fixo `{"titulos": [{"texto", "recomendado"}] (exatamente 3), "descricao": str, "fontes": {"<campo>": {"origem", "fonte"}}}`; o prompt traz literalmente as regras de `/titulo` e `/descricao` (as de `C:\Users\user40\.claude\skills\titulo\SKILL.md` e `descricao\SKILL.md`: ordem produto+marca+modelo+característica+volume, ≤ 60, kit "Kit A + B Marca", sem preço/frete/promoção/emoji/caixa alta/sinais/outras marcas; descrição com abertura, BENEFÍCIOS, COMO USAR, ONDE APLICAR / NÃO APLICAR, ESPECIFICAÇÕES, CONTEÚDO DA EMBALAGEM, fechamento, texto puro, `[CONFIRMAR]` no que faltar) e a ordem de confiança ficha > rótulo/catálogo > internet.
  - `ClaudeImagens.propor_anuncio_atributos(fotos, produto, proposta, atributos, catalogo, pesquisa) -> dict` — `{"atributos": [{"id", "valor", "valor_id", "origem", "fonte"}]}`; para atributo com `valores`, o esquema restringe `valor_id` ao `enum` dos ids da lista; sem dado = não devolve o atributo.
  - Etapas: `etapa_anuncio_textos` reaproveita `_originais`, `_catalogo`, `_pesquisar(claude, nome, link)` (a pesquisa só roda se `produto.nome` ou `produto.link`); `etapa_anuncio_atributos` reaproveita a pesquisa guardada 6 h. Erro vira entrega `situacao: "erro"` pela mesma `Noromak.anuncio`.
- [ ] **Step 1: Write the failing tests:** `test_textos_do_anuncio_seguem_o_esquema_e_vao_para_a_rota_certa` (o falso devolve 3 títulos; o agente chama `anuncio` com `id`, `titulos` e tokens); `test_atributos_so_com_valor_da_lista` (o esquema enviado ao Claude tem `enum` com os ids da lista de FORMAT); `test_erro_do_claude_entrega_erro_e_nao_prende` (exceção → entrega `situacao: "erro"`).
- [ ] **Step 2: Run** `python -m unittest test_agente` (na pasta do agente) → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `V20.31 - Agente de anuncios: etapas no agente do PC`

### Task 5: Tela

**Files:**
- Modify: `APP/anuncios.html` (painel no rascunho, perto de `formDoRascunho` ~1412 e `abrirRascunho` ~1434)
- Test: `TESTES/test_agente_anuncios_v2031.py` (`class Tela`, lendo o HTML)

**Interfaces:**
- Consumes: rotas das Tasks 1–3.
- Produces: no rascunho aberto, cartão "Agente": botão **"Pedir ao agente"** (só perfil Admin; campos opcionais "Nome do produto" e "Link do fornecedor"); enquanto o pedido anda, "Aguardando o agente" (consulta `GET /api/anuncios/agente?rascunho=` a cada 5 s, para em 401/403 e com a aba escondida); com `proposta`: os 3 títulos em radio (recomendado marcado, contagem de caracteres), descrição com caixa "usar", lista de atributos com selo da origem ("da ficha", "lido no rótulo", "lido no catálogo — confira", "da internet — confira" + link), categoria com o nome e o seletor das sugeridas ("Trocar categoria" chama `/agente/atributos`), e **"Aceitar no rascunho"** com `nkConfirmar`. A resposta 409 recarrega o rascunho e avisa "o rascunho mudou; confira e aceite de novo". Peças só `--nk-*`.
- [ ] **Step 1: Write the failing test** `test_tela_tem_o_agente`: o HTML contém `"/expedicao/api/anuncios/agente/pedir"`, `"/expedicao/api/anuncios/agente/aceitar"`, `"/expedicao/api/anuncios/agente/atributos"`, o texto `Pedir ao agente` e `nkConfirmar(` no aceitar.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; conferir na prévia local (`FERRAMENTAS_WINDOWS/previa_local`, porta 8158) com o agente simulado da prévia, se existir, senão só o pedido e o estado "Aguardando o agente".
- [ ] **Step 5: Commit** `V20.31 - Agente de anuncios: painel no rascunho`

### Task 6: Versão, documentação e suíte

**Files:**
- Modify: `servidor_web.py` (`APP_VERSION`), `VERSAO.txt`, `CLAUDE.md` (parágrafo **V20.31** na seção da Plataforma de anúncios, apontando o desenho e os testes)
- [ ] **Step 1:** conferir que `APP_VERSION` no HEAD é V20.30 (ou anterior); `APP_VERSION = "20.31-AGENTE-ANUNCIOS"` e `VERSAO.txt` igual.
- [ ] **Step 2: Run** `./testar.sh` → `TUDO VERDE`; na pasta do agente `python -m unittest test_agente` → OK.
- [ ] **Step 3: Commit** `V20.31 - Agente de anuncios: versao e CLAUDE.md`

Depois: `revisar-antes-de-subir` e o "pode subir" (CLAUDE.md §0). O agente do PC precisa ser reiniciado com a versão nova; a rota `/api/imagens/agente/anuncio` vai pelo mesmo túnel das outras.
