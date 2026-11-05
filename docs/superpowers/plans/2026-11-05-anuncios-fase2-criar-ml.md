# Plataforma de anúncios — Fase 2: criar anúncio no Mercado Livre pela ficha

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dentro da ficha do produto, criar um anúncio novo no Mercado Livre na ordem **título → categoria → obrigatórios da categoria → fotos → preço/tipo/envio/quantidade → conferir no ML → publicar**, e criar fichas novas pela lista ("+ Novo produto").

**Architecture:** Continua no bloco V20.23 de `servidor_web.py` (nova versão V20.24) e em `APP/anuncios.html`. Um **rascunho de anúncio** por tentativa (`anuncio_rascunho_v2024`) guarda tudo o que o Admin escolhe; uma função pura monta o corpo do `POST /items`; leituras do ML só por GET dentro de `ml_na_casa(conta)`; a **única** gravação no ML é o "Publicar" (fotos + item + descrição), por clique do perfil Admin. Fotos da ficha ficam em disco (`ANUNCIO_FOTOS_DIR`), nunca no banco.

**Tech Stack:** Python 3 stdlib + SQLite; HTML/CSS/JS puro; `unittest` em `TESTES/`.

**Spec:** `docs/superpowers/specs/2026-11-05-hub-anuncios-design.md` (§4.4) — e a fase 1 já construída no ramo `anuncios-v2023` (`CLAUDE.md` seção "Plataforma de anúncios (V20.23, fase 1)").

## Decisões do dono (05/11)

- Ordem da criação: **título → categoria (sugerida pelo título) → obrigatórios da categoria**. A tela não mostra categoria sem título, nem obrigatórios sem categoria.
- Fotos: **do computador e do Estúdio de Imagens**; o Noromak envia o arquivo ao ML (o Estúdio fica atrás do login, link não serve).
- Estoque inicial do anúncio: **o "dá para montar" da filial lido no Apollo na hora** (Apollo − a baixar, kit pela peça que dá menos). Apollo sem resposta = **não publica**. O Admin pode diminuir, nunca aumentar.
- Preço: o Admin define; a tela mostra o preço ideal (margem do Precificador com a comissão do ML) e "usar o ideal". Nunca muda sozinho.
- Piloto: conta **Araçatuba**; Bauru aparece na escolha da conta mas o 1º teste real é com o dono, num produto só.

## Global Constraints

- **Nunca publicar o sistema** (`./publicar.sh`, `git push`) sem o "pode subir" do dono. Commit local no ramo `anuncios-v2023`.
- Só biblioteca padrão do Python; front sem build. Comentar com `# V20.24:`; commits `V20.24 - Descricao sem acentos`.
- `APP_VERSION = "20.24-ANUNCIOS-CRIAR"` + `VERSAO.txt` (última tarefa).
- Ver = módulo admin; **gravar e tudo que chama o ML = só perfil Admin** (`ctx["app_role"] == "admin"`). Consulta/Compras barrados como na fase 1.
- Chamadas ao ML e ao Apollo **fora do `DB_LOCK`**; respostas montadas sob ele.
- A **única** escrita no ML é o Publicar (upload de fotos, `POST /items`, `POST /items/{id}/description`). Validar usa `POST /items/validate` (não cria nada).
- **Nunca dois anúncios para o mesmo rascunho**: rascunho publicado ou "incerto" (erro de rede depois de enviar o item) não publica de novo até o Admin conferir no ML e liberar.
- Valor desconhecido = vazio/nulo, nunca zero (estoque, frete, tarifa).
- Toda 1ª resposta real de cada chamada nova do ML vai para `anuncio_amostra_v2024` (tipo, resposta cortada em 6.000) — conferir com o dono na 1ª publicação.
- Chave da ficha = `normalize()` (fase 1). Telas no padrão `nk-ui` (fase 1).

## Review Focus

1. **Clique duplo / dois Admins no Publicar** — um anúncio só (trava por rascunho + situação relida sob o `DB_LOCK`). Teste na Task 5.
2. **Erro de rede depois do `POST /items`** — vira `incerto`, não repete sozinho, e a tela pede para conferir no ML. Teste na Task 5.
3. **Apollo sem resposta ou "dá para montar" 0** — não publica; quantidade acima do disponível é recusada. Teste na Task 5.
4. **Foto inválida** (não é imagem, > 10 MB, < 500 px) — recusada no envio para a ficha, nunca chega ao ML. Teste na Task 1.
5. **Mudar a categoria depois de preencher** — o que foi digitado nos atributos que continuam valendo não some. Teste na Task 4.

---

### Task 1: Fotos da ficha (computador e Estúdio)

**Files:** Modify `servidor_web.py` (bloco V20.23→V20.24); Test `TESTES/test_anuncios_fotos_v2024.py`

**Interfaces — Produces:**
- `ANUNCIO_FOTOS_DIR = Path(os.getenv("EXP_ANUNCIO_FOTOS_DIR", str(DB_PATH.parent / "anuncios_fotos")))`; `ANUNCIO_FOTO_MAX_BYTES = 10 * 1024 * 1024`; `ANUNCIO_FOTO_MIN_PX = 500`; `ANUNCIO_FOTOS_MAX = 12`.
- Tabela `anuncio_foto_v2024(id INTEGER PRIMARY KEY AUTOINCREMENT, ficha_codigo TEXT NOT NULL, arquivo TEXT NOT NULL, tipo TEXT NOT NULL, largura INTEGER, altura INTEGER, bytes INTEGER, origem TEXT NOT NULL DEFAULT 'pc', ordem INTEGER NOT NULL DEFAULT 0, criado_em TEXT, criado_por TEXT)`, criada no `ensure_anuncios_schema`.
- `_imagem_dimensoes(dados: bytes) -> tuple[str, int, int] | None` — tipo pelo conteúdo (`jpg`/`png`/`webp`) e largura/altura lidas do cabeçalho (stdlib; JPEG pelos marcadores SOF, PNG pelo IHDR, WEBP VP8/VP8L/VP8X). Não reconhecido = `None`.
- `anuncio_foto_adicionar(codigo: str, dados: bytes, ator: str, origem: str = "pc") -> dict` — valida (tipo, ≤ 10 MB, menor lado ≥ 500 px, ≤ 12 fotos por ficha; `ValueError` em português), grava o arquivo `ANUNCIO_FOTOS_DIR/<id>.<tipo>` (nome pelo id, nunca pelo nome enviado) e a linha; ordem = última + 1.
- `anuncio_foto_do_estudio(codigo: str, trabalho_id: int, nome: str, ator: str) -> dict` — lê os bytes por `imagens_arquivo(trabalho_id, nome)` (≈37057) e chama `anuncio_foto_adicionar(..., origem="estudio")`.
- `anuncio_fotos(codigo) -> list[dict]`, `anuncio_foto_remover(codigo, foto_id, ator)` (apaga linha e arquivo), `anuncio_foto_ordenar(codigo, ids: list[int], ator)`, `anuncio_foto_bytes(foto_id) -> tuple[bytes, str]`.
- Rotas: `POST /api/anuncios/foto` (`{codigo, base64}` ou `{codigo, estudio_id, nome}`), `POST /api/anuncios/foto/{remover,ordenar}`, `GET /api/anuncios/foto?id=` (módulo admin; bytes com `Content-Type` certo), `GET /api/anuncios/estudio?busca=` (lista trabalhos prontos do Estúdio com os nomes dos resultados, só leitura). Gravar = perfil Admin. `anuncio_ficha_payload` passa a trazer `fotos`.

- [ ] **Step 1: Testes** — `test_jpeg_png_webp_dimensoes` (gerar bytes mínimos válidos à mão: PNG 600×600 com IHDR; JPEG com SOF0 800×600; WEBP VP8X 1200×1200) · `test_recusa_nao_imagem` · `test_recusa_maior_que_10mb` · `test_recusa_menor_que_500px` (PNG 400×900) · `test_limite_12` · `test_arquivo_nomeado_pelo_id` (nome enviado com `../` não aparece no caminho) · `test_remover_apaga_arquivo` · `test_ordenar` · `test_do_estudio` (cria trabalho do Estúdio com um resultado em `IMAGENS_DIR` como `TESTES/test_imagens_v2017.py` faz e importa) · `test_rotas_permissao` (Supervisor GET 200, POST 403).
- [ ] **Step 2:** rodar e ver falhar. **Step 3:** implementar. **Step 4:** rodar e ver passar. **Step 5: Commit** `V20.24 - Anuncios: fotos da ficha pelo computador e pelo Estudio`

---

### Task 2: "+ Novo produto" (ficha nova pela lista)

**Files:** Modify `servidor_web.py`, `APP/anuncios.html`; Test `TESTES/test_anuncios_rotas_v2023.py` (nova classe)

**Interfaces — Produces:** `POST /api/anuncios/ficha` com `{codigo, campos, criar: true}` cria a ficha **só se não existir** (existe = 409 `JA_EXISTE` com a atual). Código obrigatório (até 60 caracteres, sem `/`), `campos.nome` obrigatório. Tipo `kit` se `kit_composicao(codigo)` existir, senão `peca`. Na lista, botão "+ Novo produto" (perfil Admin) abre janela com código e título; ao criar, abre a ficha.

- [ ] **Step 1: Testes** — `test_criar_ficha_nova` · `test_criar_existente_409` · `test_criar_sem_nome_400` · `test_criar_codigo_de_kit_vira_kit` · `test_supervisor_nao_cria`.
- [ ] **Step 2–4:** falhar, implementar (servidor + botão na lista), passar. **Step 5: Commit** `V20.24 - Anuncios: novo produto pela lista`

---

### Task 3: Leituras do ML para criar (categoria, atributos, tarifa) — só GET

**Files:** Modify `servidor_web.py`; Test `TESTES/test_anuncios_criar_v2024.py`

**Interfaces — Consumes:** `ml_api_get` (GET com token da casa atual), `ml_na_casa(conta)`, `_ml_tarifa_cache`, `_money_number`.
**Produces:**
- Tabela `anuncio_amostra_v2024(tipo TEXT PRIMARY KEY, resposta TEXT, criado_em TEXT)`; `_anuncio_amostra(tipo: str, resposta: Any) -> None` (grava só se ainda não existe; corta em 6.000).
- `ml_categoria_sugerir(titulo: str, conta: str) -> list[dict]` → `GET /sites/MLB/domain_discovery/search?limit=5&q=<título>` → `[{"categoria", "categoria_nome", "dominio", "dominio_nome"}]`. Título vazio = `ValueError`. Erro do ML = `RuntimeError` com a mensagem (a tela mostra; nada gravado).
- `ml_categoria_atributos(categoria: str, conta: str) -> dict` → `GET /categories/{id}` (nome/caminho) + `GET /categories/{id}/attributes`; devolve `{"categoria", "caminho": [...], "atributos": [{"id","nome","obrigatorio": bool,"condicional": bool,"tipo" (value_type),"valores": [{id,nome}] (até 200),"unidades": [...],"max": value_max_length,"dica": hint}]}`. Obrigatório = `tags.required` ou `tags.catalog_required`; condicional = `tags.conditional_required`. Fora: atributos com `tags.hidden`, `read_only` ou `fixed`. Cache 24 h por categoria (`_ml_tarifa_cache` ou dicionário com prazo próprio).
- `ANUNCIO_CAMPO_DO_ATRIBUTO = {"BRAND": "marca", "MODEL": "modelo", "GTIN": "ean", "SELLER_PACKAGE_WEIGHT": "peso_g", "SELLER_PACKAGE_HEIGHT": "altura_cm", "SELLER_PACKAGE_WIDTH": "largura_cm", "SELLER_PACKAGE_LENGTH": "comprimento_cm", "SELLER_SKU": "codigo"}`; `anuncio_atributos_da_ficha(ficha: dict, atributos: list[dict]) -> dict[str, Any]` (pura): preenche o que a ficha tem (peso em g → `"<n> g"`, medidas em `"<n> cm"`; `SELLER_SKU` = código da ficha). Kit sem GTIN → `EMPTY_GTIN_REASON` = "Kit" **se** o atributo existir na categoria.
- `ml_tarifa_nova(preco: float, tipo: str, categoria: str, conta: str) -> dict` → `GET /sites/MLB/listing_prices?price&listing_type_id&category_id` → `{"comissao","taxa_fixa","tarifa"}` (o que faltar = `None`); **frete = `None`** com o texto "o ML informa o frete depois de publicar" (sem MLB não há cotação).
- Rotas (perfil Admin, porque chamam o ML): `GET /api/anuncios/ml/categorias?titulo=&conta=`, `GET /api/anuncios/ml/atributos?categoria=&conta=`, `GET /api/anuncios/ml/tarifa?preco=&tipo=&categoria=&conta=`. `conta` ∈ `ml_casas_conectadas()` senão 400.

- [ ] **Step 1: Testes** (com `mock.patch.object(S, "ml_api_get", ...)` respondendo por caminho): `test_sugerir_categorias` · `test_titulo_vazio_400` · `test_atributos_obrigatorios_e_fora_os_ocultos` · `test_cache_de_atributos` (2ª leitura não chama o ML) · `test_atributos_da_ficha_peso_medidas_sku` · `test_kit_sem_gtin_usa_empty_gtin_reason` · `test_tarifa_nova_sem_frete` · `test_amostra_guardada_uma_vez` · `test_conta_desconhecida_400` · `test_supervisor_403`.
- [ ] **Step 2–4.** **Step 5: Commit** `V20.24 - Anuncios: categoria, obrigatorios e tarifa do ML para criar`

---

### Task 4: Rascunho do anúncio e corpo do item

**Files:** Modify `servidor_web.py`; Test `TESTES/test_anuncios_criar_v2024.py`

**Interfaces — Produces:**
- Tabela `anuncio_rascunho_v2024(id INTEGER PRIMARY KEY AUTOINCREMENT, ficha_codigo TEXT NOT NULL, conta TEXT NOT NULL, titulo TEXT, categoria TEXT, atributos_json TEXT DEFAULT '{}', fotos_json TEXT DEFAULT '[]', descricao TEXT, preco REAL, tipo_anuncio TEXT DEFAULT 'gold_special', frete_gratis INTEGER, garantia TEXT, quantidade INTEGER, situacao TEXT NOT NULL DEFAULT 'rascunho', mlb TEXT, erros_json TEXT DEFAULT '[]', versao INTEGER NOT NULL DEFAULT 1, criado_em, criado_por, atualizado_em, atualizado_por)`. Situações: `rascunho` → `validado` → `publicando` → `publicado` | `erro` | `incerto`.
- `anuncio_rascunho_salvar(dados: dict, ator: str, versao: int | None) -> dict` — cria (sem `id`) ou atualiza (com `id`, versão conferida → `FichaConflito`); só em `rascunho`/`validado`/`erro` (publicado/publicando/incerto = `ValueError`); qualquer mudança volta `validado` para `rascunho`. Ao trocar `categoria`, mantém em `atributos_json` só as chaves que existem na nova categoria **e** preserva o resto numa chave `_guardados` (volta se a categoria anterior voltar). `fotos_json` = lista de ids de `anuncio_foto_v2024` da mesma ficha (outra ficha = `ValueError`). Ordem obrigatória: sem `titulo` não aceita `categoria`; sem `categoria` não aceita atributos.
- `anuncio_rascunhos(codigo) -> list[dict]`, `anuncio_rascunho_ler(id) -> dict | None`.
- `anuncio_item_corpo(rascunho: dict, picture_ids: list[str]) -> dict` (**pura**): `{"title", "category_id", "price", "currency_id": "BRL", "available_quantity", "buying_mode": "buy_it_now", "listing_type_id", "condition": "new", "pictures": [{"id": ...}], "attributes": [{"id","value_name"} ou {"id","value_id"}], "sale_terms": [garantia como WARRANTY_TYPE/WARRANTY_TIME se informada], "shipping": {"mode": "me2", "free_shipping": bool}}`. Atributo vazio não vai. (O ML pode exigir `family_name` em categorias do modelo novo — ver Task 5.)
- Rotas: `GET /api/anuncios/rascunhos?codigo=`, `POST /api/anuncios/rascunho` (perfil Admin; 409 `CONFLITO`).

- [ ] **Step 1: Testes** — `test_criar_e_ler_rascunho` · `test_ordem_titulo_categoria_atributos` · `test_trocar_categoria_guarda_o_digitado` (**RF5**) · `test_publicado_nao_edita` · `test_conflito_409` · `test_foto_de_outra_ficha_recusada` · `test_corpo_do_item` (todos os campos, atributo vazio fora, value_id quando houver) · `test_mudar_volta_para_rascunho`.
- [ ] **Step 2–4.** **Step 5: Commit** `V20.24 - Anuncios: rascunho do anuncio e corpo do item`

---

### Task 5: Conferir no ML e Publicar

**Files:** Modify `servidor_web.py`; Test `TESTES/test_anuncios_publicar_v2024.py`

**Interfaces — Consumes:** Tasks 1–4; `_ml_promo_http(method, path, payload)` (JSON com renovação de token, ≈29695 — chamar dentro de `ml_na_casa(conta)`); `anuncio_ficha_payload(codigo, ler_apollo=True)` para o "dá para montar"; `audit`.
**Produces:**
- `_ml_foto_upload(dados: bytes, tipo: str) -> str` — `POST /pictures/items/upload` multipart (`file`), com o token de `ml_access_token()` da casa atual; devolve o `id` da foto; erro = `RuntimeError` com a mensagem do ML. 1ª resposta → amostra `foto_upload`.
- `anuncio_validar(rascunho_id: int, ator: str) -> dict` — confere localmente (título, categoria, obrigatórios preenchidos, ≥ 1 foto, preço > 0, quantidade ≥ 1) e chama `POST /items/validate` com o corpo **sem subir as fotos** (a foto só sobe no Publicar): o corpo da validação leva `pictures: []` e as causas do ML que falam só de foto/picture são ignoradas (a foto já foi conferida localmente). Resposta 204/200 = `validado`; 400 = `erros` com `cause[].message` traduzido para a tela; situação fica `rascunho`. Amostra `validate`.
- `anuncio_publicar(rascunho_id: int, ator: str) -> dict`:
  1. Sob `DB_LOCK`: relê; só `validado` publica (senão `ValueError`); marca `publicando` (ninguém mais publica).
  2. Fora do lock: lê o Apollo da conta (`anuncio_ficha_payload(..., ler_apollo=True)`); `da_para_montar` `None` = volta a `validado` com erro "Apollo não respondeu — não publicado"; `quantidade > da_para_montar` ou `da_para_montar == 0` = idem com a mensagem.
  3. Sobe as fotos (`_ml_foto_upload`); erro = volta a `validado` com o erro (nada criado).
  4. `POST /items` com `anuncio_item_corpo`. Se o ML recusar com causa sobre `title`/`family_name` (o modelo novo), repete **uma vez** trocando `title` por `family_name` — ainda é recusa, nada foi criado. HTTP de erro = `erro` com a mensagem. **Exceção de rede/timeout (sem resposta)** = `incerto` (**RF2**) — não repete. Amostra `item_criar`.
  5. Criado: `POST /items/{id}/description` (`plain_text`; falha = aviso, não desfaz); grava `mlb`, `publicado`, e insere em `anuncio_canal_v2023` a linha (`canal='ml'`, conta, `anuncio_id`, `ficha_codigo`, `controle='noromak'`, título, preço, estoque, status). `audit(ator, "anuncios.publicar", ...)`.
- `anuncio_incerto_liberar(rascunho_id, mlb_encontrado: str | "", ator)` — o Admin conferiu no ML: com MLB informado vira `publicado` (e entra no `anuncio_canal`); sem = volta a `validado`. Audit.
- Rotas: `POST /api/anuncios/rascunho/{validar,publicar,liberar}` (perfil Admin; `ANUNCIOS_PUBLICAR_LOCK` não-bloqueante: outra publicação em andamento = 409).

- [ ] **Step 1: Testes** (ML falso por `mock.patch.object(S, "_ml_promo_http", ...)` e `S._ml_foto_upload`; Apollo por `S.crm_stock_lookup`):
  `test_validar_ok` · `test_validar_erro_mostra_causas` · `test_validar_sem_foto_ou_obrigatorio_local` · `test_publicar_ok_cria_linha_noromak` · **RF1** `test_publicar_duas_vezes_um_anuncio` (2ª chamada = `ValueError`/409, `POST /items` chamado 1 vez) · **RF2** `test_rede_caiu_vira_incerto_e_nao_repete` · `test_liberar_incerto_com_e_sem_mlb` · **RF3** `test_apollo_sem_resposta_nao_publica` · `test_quantidade_acima_do_disponivel_recusada` · `test_foto_falhou_nada_criado` · `test_title_recusado_tenta_family_name_uma_vez` · `test_descricao_falhou_continua_publicado` · `test_supervisor_403`.
- [ ] **Step 2–4.** **Step 5: Commit** `V20.24 - Anuncios: conferir no ML e publicar o anuncio`

---

### Task 6: Tela — fotos, criar anúncio passo a passo, novo produto

**Files:** Modify `APP/anuncios.html`; Test `TESTES/test_anuncios_rotas_v2023.py` (`test_tela_tem_criar_anuncio`)

- **Aba nova "Fotos"** na ficha: enviar do computador (vários arquivos; cada um um POST base64), "Do Estúdio de Imagens" (janela com busca e miniaturas dos trabalhos prontos), arrastar ou setas para ordenar, remover (`nkConfirmar`, foco em Cancelar). Mostra tamanho e px; a 1ª é a principal.
- **Aba "Anúncios"**: botão "Criar anúncio" (perfil Admin) e a lista de rascunhos com a situação (selo). O criar abre um painel em passos, **na ordem do dono**, cada passo só libera o seguinte:
  1. **Conta** (Araçatuba padrão) e **Título** (da ficha, editável; contador de caracteres).
  2. **Categoria**: "Sugerir pelo título" mostra as sugestões do ML com o caminho; escolher uma. Mudar o título oferece sugerir de novo.
  3. **Obrigatórios da categoria**: lista com os obrigatórios em cima, condicionais depois, os opcionais recolhidos; o que veio da ficha marcado "da ficha"; lista de valores vira `<select>`; contador "N de M obrigatórios".
  4. **Fotos**: marcar quais fotos da ficha vão e a ordem.
  5. **Preço, tipo, envio, garantia, quantidade**: preço com "usar o ideal" e a conta (comissão do ML lida agora, imposto, custo, lucro; frete "o ML informa depois de publicar"); quantidade = "dá para montar" lido agora, editável só para baixo.
  6. **Conferir no ML**: mostra o que o ML apontou, campo a campo.
  7. **Publicar**: `nkConfirmar` com o resumo (conta, título, preço, quantidade, nº de fotos). Sucesso = link do anúncio no ML e a linha aparece na tabela como "Noromak". `incerto` = aviso vermelho "Confira no Mercado Livre se o anúncio foi criado" com os botões "Achei, é o MLB…" e "Não foi criado".
  Rascunho salvo a cada passo (manda `versao`; 409 mostra conflito sem perder o digitado).
- **Lista**: "+ Novo produto" (Task 2).
- Supervisor/Consulta: vê rascunhos, sem botões.

- [ ] **Step 1:** teste `test_tela_tem_criar_anuncio` (a página tem os 7 passos e os endpoints `/api/anuncios/ml/categorias`, `/rascunho/validar`, `/rascunho/publicar`, `/foto`).
- [ ] **Step 2–4:** falhar, implementar, passar; `node --check` do script.
- [ ] **Step 5:** conferir na prévia local (`dev_anuncios.py` com ML simulado: categorias, atributos, validate, upload, items) — criar um anúncio do começo ao fim, o caso `incerto` e o conflito. **Step 6: Commit** `V20.24 - Anuncios: tela de criar anuncio, fotos e novo produto`

---

### Task 7: Versão, documentação e validação

- [ ] `APP_VERSION = "20.24-ANUNCIOS-CRIAR"` + `VERSAO.txt`; seção do CLAUDE.md "Plataforma de anúncios" ganha a fase 2 (tabelas, rotas, regras: única escrita no ML, incerto, estoque inicial do Apollo, amostras a conferir).
- [ ] `./testar.sh` inteiro. Commit `V20.24 - Anuncios fase 2: versao e documentacao`.

**Depois de subir (com o dono):** criar **um** anúncio real em Araçatuba, conferir no ML (fotos, ficha técnica, preço, estoque) e ler as amostras (`anuncio_amostra_v2024`) — é aí que se confirma se a categoria pede `family_name`.
