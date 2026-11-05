# Plataforma de anúncios — Fase 1: ficha do produto, kits e anúncios puxados do ML

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tela `/anuncios` com a ficha de cada produto (peça ou kit), a aba Kits editando o Cadastro de Kits que já existe e a aba "Anúncios em todos os canais" com os anúncios do Mercado Livre puxados das duas contas — **só leitura no ML**, nada é gravado em marketplace nesta fase.

**Architecture:** Tudo em `servidor_web.py` (biblioteca padrão), num bloco novo "V20.23 — Anúncios". Duas tabelas novas (`anuncio_ficha_v2023`, `anuncio_canal_v2023`); kits continuam no storage `conf_kits` (gravado pelo `save_storage` como ADMIN). Leitura do ML reaproveita `ml_todos_os_anuncios` + `ml_itens_detalhe` dentro de `ml_na_casa(conta)`. Tela nova `APP/anuncios.html` no padrão `nk-ui`.

**Tech Stack:** Python 3 stdlib + SQLite; HTML/CSS/JS puro; testes `unittest` em `TESTES/` com `base.py`.

**Spec:** `docs/superpowers/specs/2026-11-05-hub-anuncios-design.md`

### Onde esta fase fica no piloto

O piloto do desenho (§5) vira quatro planos, cada um entregável sozinho, nesta ordem:
1. **Este plano** — ficha, kits, anúncios puxados (só leitura), "Passar para o Noromak" (só marca).
2. Criar anúncio no ML pela ficha (categoria, atributos, fotos, validar, publicar).
3. Motor de estoque por canal/filial (Simulação → Lote → Automático), generalizando o do TikTok.
4. Vendas → Apollo pelo `atendimento/criar`, lista "não chegou no Apollo", aviso de cancelada.

Os planos 2–4 são escritos depois desta fase subir, porque dependem da 1ª resposta real do ML e do contrato do Guilherme.

## Global Constraints

- **Nunca publicar** (`./publicar.sh`, `git push producao`) sem o "pode subir" do dono (CLAUDE.md §0). Commit local pode.
- Só biblioteca padrão do Python; front sem build.
- Versão: `APP_VERSION = "20.23-ANUNCIOS-FICHA"` em `servidor_web.py` **e** `VERSAO.txt`. Commits `V20.23 - Descricao sem acentos`.
- Comentar o código novo com `# V20.23:`.
- ADMIN > Noromak > integrações: campo digitado pelo Admin na ficha **nunca** é sobrescrito por importação.
- Valor desconhecido = vazio/nulo, **nunca zero** (estoque, peso, custo).
- Esta fase **não grava nada** no ML, Shopee, Magalu, TikTok nem Apollo.
- Ver = módulo admin (`self.auth(admin_only=True)`); **gravar = só perfil Admin** (`ctx["app_role"] == "admin"`, senão 403 "Somente o perfil Admin."). Consulta já é barrado pelo `consulta_bloqueada`.
- Respostas montadas sob o `DB_LOCK` e chamadas ao ML **fora** dele.
- Telas: `nk-ui.css` + `nk-ui.js` no `<head>`, `nk-menu.js` com `data-shell`, só variáveis `--nk-*`; nada de visual "amador" (dono recusou o 1º protótipo). Referência visual: as telas `ficha-produto-v4` e `kits-v5` mostradas ao dono (descritas na Task 6).
- `./testar.sh` tem que passar inteiro.

## Review Focus

1. **Kit editado com pedido do kit já na fila** — mudar a quantidade não pode diminuir bip nem sumir linha da Conferência (bip monotônico). Teste na Task 3.
2. **Puxar anúncios com o ML fora / 403** — nada da tabela é apagado nem marcado como encerrado por falta de resposta. Teste na Task 4.
3. **Anúncio removido pelo Admin** — não volta na próxima puxada (tombstone). Teste na Task 4.
4. **SKU do anúncio que não é código conhecido** ("KIT2-X" sem cadastro, "A/B", vazio) — vai para "sem ficha" ou liga à ficha certa, nunca a "X" sozinho. Teste na Task 4.
5. **Dois Admins salvando a mesma ficha** — o segundo recebe 409 com a versão atual, nunca sobrescreve calado. Teste na Task 1.

---

### Task 1: Tabelas e ficha do produto

**Files:**
- Modify: `servidor_web.py` — bloco novo perto de `ensure_tiktok_estoque_schema` (≈7258); chamada em `init_db` ao lado de `ensure_tiktok_estoque_schema()  # V19.64` (≈2703)
- Test: `TESTES/test_anuncios_ficha_v2023.py`

**Interfaces:**
- Produces:
  - `ensure_anuncios_schema() -> None`
  - `ANUNCIO_CAMPOS_FICHA: tuple[str, ...] = ("nome", "marca", "modelo", "ean", "ncm", "peso_g", "altura_cm", "largura_cm", "comprimento_cm", "descricao", "categoria_ml")`
  - `ANUNCIO_ORIGENS: dict[str, int] = {"admin": 1, "alephee": 2, "anuncio_ml": 3, "apollo": 4}` (menor = mais autoridade)
  - `ficha_ler(codigo: str) -> dict | None` → `{"codigo", "tipo": "peca"|"kit", "campos": {campo: valor}, "origem": {campo: origem}, "atributos": dict, "versao": int, "atualizado_em", "atualizado_por"}`
  - `ficha_salvar(codigo: str, campos: dict, ator: str, versao: int | None) -> dict` — grava como origem `admin`; `versao` diferente da gravada → `FichaConflito` (exceção com `.atual`)
  - `ficha_importar(codigo: str, campos: dict, origem: str) -> list[str]` — preenche só campo vazio ou de origem com **menos** autoridade; nunca toca campo `admin`; devolve os campos alterados; cria a ficha se não existir
  - `class FichaConflito(Exception)` com atributo `atual: dict`

Tabelas:
- `anuncio_ficha_v2023(codigo TEXT PRIMARY KEY, tipo TEXT NOT NULL DEFAULT 'peca', campos_json TEXT NOT NULL DEFAULT '{}', origem_json TEXT NOT NULL DEFAULT '{}', atributos_json TEXT NOT NULL DEFAULT '{}', versao INTEGER NOT NULL DEFAULT 1, atualizado_em TEXT NOT NULL DEFAULT '', atualizado_por TEXT NOT NULL DEFAULT '')`
- `anuncio_canal_v2023(canal TEXT NOT NULL, conta TEXT NOT NULL, anuncio_id TEXT NOT NULL, ficha_codigo TEXT NOT NULL DEFAULT '', sku_anuncio TEXT NOT NULL DEFAULT '', controle TEXT NOT NULL DEFAULT 'alephee', titulo TEXT NOT NULL DEFAULT '', preco REAL, estoque INTEGER, status TEXT NOT NULL DEFAULT '', tipo_anuncio TEXT NOT NULL DEFAULT '', categoria TEXT NOT NULL DEFAULT '', logistica TEXT NOT NULL DEFAULT '', user_product_id TEXT NOT NULL DEFAULT '', lido_em TEXT NOT NULL DEFAULT '', removido_em TEXT NOT NULL DEFAULT '', removido_por TEXT NOT NULL DEFAULT '', controle_em TEXT NOT NULL DEFAULT '', controle_por TEXT NOT NULL DEFAULT '', PRIMARY KEY (canal, conta, anuncio_id))` + índice em `ficha_codigo`.
- `codigo` passa sempre por `_exp_norm_codigo` antes de gravar/ler.

- [ ] **Step 1: Escrever os testes**

`class FichaTeste(TesteNoromak)`:
- `test_salvar_e_ler`: `ficha_salvar("0893117732", {"marca": "Würth", "peso_g": 450}, "luis", None)` → `ficha_ler` devolve `campos["marca"] == "Würth"`, `origem["marca"] == "admin"`, `versao == 1`.
- `test_importacao_nao_passa_por_cima_do_admin`: depois do anterior, `ficha_importar("0893117732", {"marca": "WURTH", "ean": "4048962065124"}, "anuncio_ml")` → devolve `["ean"]`; marca continua "Würth"/admin; ean com origem `anuncio_ml`.
- `test_importacao_respeita_ordem`: importa `{"peso_g": 500}` como `anuncio_ml`, depois `{"peso_g": 450}` como `alephee` → fica 450/alephee; importar de novo `{"peso_g": 999}` como `apollo` → continua 450.
- `test_conflito_de_versao`: `ficha_salvar(..., versao=1)` duas vezes seguidas → a segunda levanta `FichaConflito` e `exc.atual["versao"] == 2`.
- `test_valor_vazio_nao_vira_zero`: `ficha_importar(c, {"peso_g": None, "ean": ""}, "anuncio_ml")` → nenhum campo gravado (devolve `[]`).
- `test_codigo_normalizado`: salvar com `" 0893117732 "` e ler com `"0893117732"` acha a mesma ficha.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest TESTES.test_anuncios_ficha_v2023 -v` (de dentro de `TESTES/`: `python -m unittest test_anuncios_ficha_v2023 -v`)
Expected: FAIL `AttributeError: module 'servidor_web' has no attribute 'ficha_salvar'`

- [ ] **Step 3: Implementar** `ensure_anuncios_schema`, `ficha_ler`, `ficha_salvar`, `ficha_importar`, `FichaConflito` em `servidor_web.py`; chamar `ensure_anuncios_schema()  # V20.23` no `init_db`. Leitura e escrita sob `DB_LOCK`; `ficha_salvar` relê a versão sob a trava antes de gravar. Campo com valor `None`/`""` não é importado.

- [ ] **Step 4: Rodar e ver passar** — mesmo comando, todos OK.

- [ ] **Step 5: Commit**

```bash
git add servidor_web.py TESTES/test_anuncios_ficha_v2023.py
git commit -m "V20.23 - Anuncios: tabelas e ficha do produto com origem de cada campo"
```

---

### Task 2: Estoque "dá para montar" (puro) e leitura do Apollo da filial

**Files:**
- Modify: `servidor_web.py` (bloco V20.23)
- Test: `TESTES/test_anuncios_ficha_v2023.py` (nova classe)

**Interfaces:**
- Consumes: `crm_stock_lookup(codigo, operador, wait_seconds)` (≈733), `filial_da_linha`, `_rows_from_storage_value`, `_tiktok_pecas`, `_tiktok_quantidade`, `_exp_norm_codigo`.
- Produces:
  - `ANUNCIO_REVENDA: dict[str, int] = {"aracatuba": 1, "bauru": 3}`
  - `kit_da_para_montar(componentes: list[dict], disponivel: dict[str, float | None], pendente: dict[str, int]) -> int | None` — menor `floor((disp − pend) / quantidade)` entre os componentes, mínimo 0; qualquer componente com `disponivel` ausente/`None` → `None`.
  - `pendentes_da_filial(filial: str) -> dict[str, int]` — mesma conta de `_tiktok_pendentes_bauru`, para qualquer filial. **Refatorar** `_tiktok_pendentes_bauru` para `return pendentes_da_filial(TIKTOK_CASA)` (o teste do TikTok continua passando).
  - `apollo_disponivel_filial(codigos: list[str], filial: str) -> dict[str, float | None]` — chama `crm_stock_lookup(c, operador="anuncios", wait_seconds=0)` por código; pega `estoques[]` com `revenda == ANUNCIO_REVENDA[filial]`; `respondeu` falso ou erro/limite (`CrmLimiteAtingido`) → `None`. No máximo 10 códigos por chamada (os demais ficam `None`).

- [ ] **Step 1: Testes** (`class EstoqueKitTeste(TesteNoromak)`):
  - `test_kit_de_3`: `kit_da_para_montar([{"sku":"A","quantidade":3}], {"A": 24}, {})` == 8.
  - `test_peca_que_da_menos_manda`: comps A×2, B×1; disp A=10, B=3 → 3.
  - `test_desconta_pendente`: A×3, disp 24, pend {"A": 6} → 6.
  - `test_sem_leitura_e_nulo`: disp {"A": None} → `None`; disp `{}` → `None`.
  - `test_nunca_negativo`: disp 2, pend 5 → 0.
  - `test_pendentes_por_filial`: grava pela rota do Admin (`self.salvar_admin`) uma linha `Casa=bauru` SKU "KIT2-X" qtd 1 e uma sem `Casa` SKU "X" qtd 1 → `pendentes_da_filial("bauru") == {"X": 2}`, `pendentes_da_filial("aracatuba") == {"X": 1}` (usar as chaves de `_exp_norm_codigo`).
  - `test_apollo_null_nao_vira_zero`: `mock.patch.object(S, "crm_stock_lookup", return_value={"ok": True, "estoques": [{"revenda": 1, "disponivel": None, "respondeu": False}]})` → `{"A": None}`.
- [ ] **Step 2:** rodar, ver falhar (`AttributeError`).
- [ ] **Step 3:** implementar as quatro funções e a refatoração do TikTok.
- [ ] **Step 4:** rodar esta classe **e** `python -m unittest test_tiktok_estoque_v1964 -v` — tudo OK.
- [ ] **Step 5: Commit** `V20.23 - Anuncios: conta do kit pela peca que da menos e pendentes por filial`

---

### Task 3: Kits — ler e gravar o Cadastro de Kits

**Files:**
- Modify: `servidor_web.py` (bloco V20.23)
- Test: `TESTES/test_anuncios_kits_v2023.py`

**Interfaces:**
- Consumes: `_load_kit_catalog()` (≈11862), `_kit_catalog_lookup`, `_infer_quantity_kit_from_sku` (≈11828), `get_storage("conf_kits")`, `save_storage(...)` (≈5512), `audit`.
- Produces:
  - `kit_composicao(sku_kit: str) -> dict | None` → `{"sku_kit", "nome", "componentes": [{"sku", "quantidade"}], "fonte": "cadastro"|"prefixo", "ativo": bool}` (mesma regra de `full_kit_compositions` para um SKU).
  - `kits_da_peca(codigo: str) -> list[dict]` → todo kit do cadastro com a peça entre os componentes, mais as fichas `tipo=kit` que a contêm; cada item `{"sku_kit", "nome", "quantidade_da_peca", "fonte"}`.
  - `kit_salvar(sku_kit: str, nome: str, componentes: list[dict], ator: str, versao_storage: int | None) -> dict` — grava/atualiza **uma** entrada em `conf_kits` (chave `sku_kit`, mantém as outras entradas e campos desconhecidos da linha), via `save_storage("conf_kits", old, new, False, actor=ator, actor_role="admin", source_screen="anuncios", change_method="anuncios.kit")`. Valida: `sku_kit` não vazio, ≥ 1 componente, quantidade inteira 1..999, componente ≠ o próprio kit. `ValueError` com mensagem em português. Versão do storage diferente da atual → `FichaConflito`. Também garante a ficha do kit com `tipo='kit'` (insere se não existe; se existe, só troca o `tipo`, sem mexer nos campos nem na versão). `audit(ator, "anuncios.kit", f"{sku_kit}|{componentes}")`.
  - Kit que vinha só pelo prefixo (`fonte == "prefixo"`) ganha entrada no cadastro ao ser salvo (decisão do dono).

- [ ] **Step 1: Testes** (`class KitsTeste(TesteNoromak)`):
  - `test_salvar_kit_novo_e_ler`: `kit_salvar("KIT32052153", "Texamatic 3L", [{"sku":"81794-32052153","quantidade":3}], "luis", None)` → `kit_composicao("KIT32052153")["componentes"] == [{"sku":"81794-32052153","quantidade":3}]`, `fonte == "cadastro"`.
  - `test_mudar_quantidade_de_1_para_3`: cadastro inicial com quantidade 1 (gravar `conf_kits` direto como no `test_shopee_kit_quantidade_v2022.py`), `kit_salvar(..., quantidade 3)` → 3; outras entradas do `conf_kits` intactas (inclusive campos extras da linha).
  - `test_kits_da_peca`: dois kits com a peça (×3 e ×4) → `kits_da_peca("81794-32052153")` traz os dois com `quantidade_da_peca` 3 e 4.
  - `test_prefixo_vira_cadastro`: `kit_composicao("KIT2-ABC")["fonte"] == "prefixo"`; depois de `kit_salvar("KIT2-ABC", ..., [{"sku":"ABC","quantidade":2}])` → `"cadastro"`.
  - `test_validacao`: quantidade 0, componente igual ao kit, lista vazia → `ValueError`.
  - `test_conflito`: `kit_salvar(..., versao_storage=<versão velha>)` → `FichaConflito`.
  - **Review Focus 1** `test_bip_da_fila_nao_diminui`: fila com o pedido do kit já aberto em componentes e `Bipado`/quantidade conferida > 0 (montar com `linha(...)` + `self.salvar_admin`); `kit_salvar` mudando a quantidade → a linha do pedido continua na fila com o mesmo bip (`pedidos()`).
- [ ] **Step 2:** rodar, ver falhar.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** rodar este arquivo e `test_shopee_kit_quantidade_v2022`, `test_conferencia` — OK.
- [ ] **Step 5: Commit** `V20.23 - Anuncios: kits lidos e gravados no mesmo Cadastro de Kits`

---

### Task 4: Puxar os anúncios do Mercado Livre (só leitura)

**Files:**
- Modify: `servidor_web.py` (bloco V20.23)
- Test: `TESTES/test_anuncios_puxar_v2023.py`

**Interfaces:**
- Consumes: `ml_na_casa(conta)` (≈9966), `ml_casas_conectadas()` (≈10320), `ml_todos_os_anuncios(forcar=True)` (≈30212), `ml_itens_detalhe(mlbs)` (≈30285), `_promo_item_sku(item)` (≈10914), vínculo `ml_sku_mlb_links_v1`, `kit_composicao`, `ficha_importar`.
- Produces:
  - `ANUNCIO_ATRIBUTOS_ML: dict[str, str] = {"BRAND": "marca", "MODEL": "modelo", "GTIN": "ean", "SELLER_PACKAGE_WEIGHT": "peso_g", "SELLER_PACKAGE_HEIGHT": "altura_cm", "SELLER_PACKAGE_WIDTH": "largura_cm", "SELLER_PACKAGE_LENGTH": "comprimento_cm"}` — conversão de unidade pelo `value_struct` (`g`/`kg`, `cm`/`mm`); sem número → não importa.
  - `anuncio_ficha_do_sku(sku: str) -> str` — código da ficha: SKU de kit (cadastro ou prefixo) = o próprio SKU do kit; SKU simples = `_exp_norm_codigo(sku)`; "A/B" sem cadastro = o próprio SKU (vira ficha tipo kit sem componentes, aparece como "sem composição"); vazio = `""` (lista "sem ficha"). Vínculo manual MLB↔SKU vence o SKU do anúncio.
  - `anuncios_ml_puxar(contas: list[str] | None = None) -> dict` → `{"ok": bool, "contas": {conta: {"lidos", "novos", "atualizados", "sem_ficha", "erro"}}}`. Para cada conta (padrão: `ml_casas_conectadas()`): dentro de `ml_na_casa(conta)`, lista e detalha **fora do `DB_LOCK`**; depois upsert em `anuncio_canal_v2023` (`canal='ml'`). Regras:
    - linha com `removido_em` preenchido **não** é atualizada nem recriada;
    - `controle` de linha existente **nunca** muda aqui; linha nova = `'full'` se `shipping.logistic_type == 'fulfillment'`, senão `'alephee'`;
    - ML que não respondeu (lista vazia por erro, exceção, 403) → `erro` na conta e **nada** alterado/apagado; anúncio que sumiu da lista não é apagado;
    - campos da ficha importados com origem `anuncio_ml` (só o que o anúncio trouxe).
  - `anuncio_remover(canal, conta, anuncio_id, ator) -> dict` e `anuncio_reincluir(canal, conta, anuncio_id, ator) -> dict` (tombstone; `audit`).

- [ ] **Step 1: Testes** — `mock.patch.object(S, "ml_todos_os_anuncios", ...)`, `mock.patch.object(S, "ml_itens_detalhe", ...)`, `mock.patch.object(S, "ml_casas_conectadas", return_value=["aracatuba"])`:
  - `test_puxa_e_liga_a_ficha`: item `MLB1` SKU "0893117732", atributos BRAND "Würth" e GTIN → linha com `controle='alephee'`, `ficha_codigo` normalizado; ficha com marca/ean origem `anuncio_ml`.
  - `test_full_fica_full`: `shipping.logistic_type='fulfillment'` → `controle='full'`.
  - `test_nao_muda_controle_existente`: linha já `controle='noromak'` → depois de puxar continua `'noromak'`.
  - **RF2** `test_ml_fora_nao_apaga`: 1ª puxada com 2 anúncios; 2ª com `ml_todos_os_anuncios` levantando `RuntimeError` → resposta com `erro`, as 2 linhas iguais; 3ª com lista só de 1 → a outra continua lá.
  - **RF3** `test_removido_nao_volta`: `anuncio_remover` e puxar de novo → linha continua com `removido_em`; `anuncio_reincluir` → volta a ser atualizada.
  - **RF4** `test_sku_de_kit_e_ab`: "KIT2-X" → ficha "KIT2-X" (nunca "X"); "A/B" sem cadastro → ficha "A/B"; SKU vazio → `ficha_codigo == ""` e conta em `sem_ficha`.
  - `test_admin_vence_importacao`: `ficha_salvar(c, {"marca": "Würth"})` antes → puxar com BRAND "WURTH" não muda.
  - `test_peso_em_kg`: `SELLER_PACKAGE_WEIGHT` `value_struct {"number": 0.45, "unit": "kg"}` → `peso_g == 450`.
- [ ] **Step 2:** rodar, ver falhar.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** rodar — OK.
- [ ] **Step 5: Commit** `V20.23 - Anuncios: puxar anuncios do ML das duas contas so leitura`

---

### Task 5: Controle do anúncio, payloads e rotas

**Files:**
- Modify: `servidor_web.py` — GET perto de `"/api/tiktok-estoque"` (≈37034); POST perto do bloco `/api/tiktok-estoque/` (≈39325); páginas no mapa de módulos (≈38049) e no mapa de arquivos (≈41843)
- Test: `TESTES/test_anuncios_rotas_v2023.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `anuncio_controle_definir(canal: str, conta: str, anuncio_id: str, controle: str, ator: str, confirmou_alephee: bool) -> dict` — `controle` ∈ `{"noromak", "alephee"}`; para `noromak` exige `confirmou_alephee=True` (senão `ValueError("Confirme que o anúncio foi desativado na Alephee.")`); linha `full` → `ValueError("Anúncio Full: o estoque é do Mercado Livre.")`; grava `controle_em/por`; `audit(ator, "anuncios.controle", ...)`.
  - `anuncios_lista_payload(busca: str = "", filtro: str = "") -> dict` → `{"fichas": [{"codigo","tipo","nome","anuncios": n,"canais": [...],"sem_ficha": bool}], "sem_ficha": [linhas de anúncio sem ficha], "resumo": {...}}`; `filtro` ∈ `""|"sem_ficha"|"misto"|"noromak"`; até 200 fichas.
  - `anuncio_ficha_payload(codigo: str, ler_apollo: bool = False) -> dict` → `{"ficha", "kits_da_peca", "composicao" (se kit), "anuncios": [...linhas, removidos à parte], "misto": bool, "estoque": {"aracatuba": {"disponivel", "pendente", "da_para_montar"}, "bauru": {...}}}`. `misto` = existe anúncio da mesma conta com `controle='alephee'` **e** outro com `'noromak'`. Apollo só é lido com `ler_apollo=True` (fora do `DB_LOCK`; `apollo_disponivel_filial`).
  - Rotas:
    - `GET /api/anuncios?busca=&filtro=` → `anuncios_lista_payload` (`auth(admin_only=True)`)
    - `GET /api/anuncios/ficha?codigo=&apollo=1` → `anuncio_ficha_payload` (404 se não há ficha nem anúncio)
    - `POST /api/anuncios/{ficha,kit,puxar,controle,remover,reincluir}` — perfil Admin; `ValueError` → 400; `FichaConflito` → 409 `{"ok": false, "error": "CONFLITO", "atual": ...}`. `puxar` roda direto na requisição (≈16 chamadas de lista + ≈80 de detalhe por conta); uma puxada por vez (`ANUNCIOS_PUXAR_LOCK`, 2ª chamada = 409 "Já está puxando"). Se em produção passar de 50 s (limite do nginx), vira segundo plano no plano 3.
  - Páginas: `"/anuncios": "admin"` (+ `/anuncios/`, `/anuncios.html`) e `"/anuncios": "anuncios.html"` (+ variantes).

- [ ] **Step 1: Testes** (HTTP real pelo `self.cliente`):
  - `test_lista_e_ficha_admin`: depois de `ficha_salvar` → `GET /api/anuncios` 200 com a ficha; `GET /api/anuncios/ficha?codigo=...` 200.
  - `test_supervisor_ve_mas_nao_grava`: sob `S.DB_LOCK`, `S.upsert_app_user("sup_2023", "Supervisor", "operador", True, SENHA, "teste", permissions={"conferencia": True, "admin": True})` + `S.DB.commit()` (como `test_auditoria_cobrancas_v1901.py:367-371`), login com outro `Cliente` → GET 200; `POST /api/anuncios/ficha` 403.
  - `test_puxar_um_por_vez`: com `ANUNCIOS_PUXAR_LOCK` segurado no teste → `POST /api/anuncios/puxar` 409.
  - `test_operador_sem_admin_403`: operador sem módulo admin → GET 403.
  - **RF5** `test_conflito_409`: dois POST `/api/anuncios/ficha` com `versao: 1` → 2º = 409 `CONFLITO`.
  - `test_passar_para_noromak_exige_confirmacao`: sem `confirmou_alephee` → 400; com → 200 e linha `noromak`; em linha `full` → 400.
  - `test_misto`: dois anúncios da mesma ficha/conta, um `alephee` e um `noromak` → `misto: true`.
  - `test_pagina_abre`: `GET /expedicao/anuncios` com sessão admin → 200 HTML.
- [ ] **Step 2:** rodar, ver falhar.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** rodar — OK.
- [ ] **Step 5: Commit** `V20.23 - Anuncios: rotas da ficha, kits, puxar e Passar para o Noromak`

---

### Task 6: Tela `/anuncios`

**Files:**
- Create: `APP/anuncios.html`
- Modify: `APP/nk-menu.js` (item no grupo "Vendas e anúncios", ao lado de `tiktok-estoque`, linha ≈56: `{ id: "anuncios", rotulo: "Anúncios", href: "/anuncios", modulo: "admin", icone: ... }`); subir o `?v=` do `nk-menu.js` em **todas** as telas que o carregam e o `CACHE` do `APP/sw.js` (CLAUDE.md §4)
- Test: `TESTES/test_anuncios_rotas_v2023.py` (`test_tela_tem_as_pecas_padrao`)

Conteúdo (padrão `nk-ui`, mesmo cabeçalho/cartões/painéis das telas de Devoluções e Tarefas):
- **Lista** (sem `?codigo=`): cabeçalho "Vendas e anúncios / Anúncios"; botões "Puxar anúncios do ML" (perfil Admin) e Atualizar; KPIs (fichas, anúncios, sem ficha, produto misto, controlados pelo Noromak); busca + filtro; tabela `data-nk-tabela` (código, nome, tipo, canais com selo por controle, nº anúncios). Aba "Sem ficha" lista os anúncios cujo SKU não ligou.
- **Ficha** (`?codigo=`; endereço muda com `history.pushState`, F5 reabre): título = nome; abas **Ficha do produto · Kits · Anúncios em todos os canais**.
  - Ficha: índice à esquerda com bolinha verde/amarela/vermelha (vermelha = falta nome, marca, EAN ou peso/medidas); campos de `ANUNCIO_CAMPOS_FICHA` com a origem ao lado ("da ficha", "do anúncio ML", "da Alephee", "do Apollo"); Salvar manda `versao`; 409 mostra "outra pessoa alterou" e recarrega sem perder o digitado (mostra os dois valores).
  - Kits: "Kits que usam esta peça" + componentes do kit aberto com **quantidade editável**, "+ Adicionar componente", "Remover"; quadro "Dá para montar / Custo do kit / Na venda compromete" (custo: deixar em branco nesta fase, rótulo "na fase de preço"); botão "Ler estoque do Apollo agora" (`apollo=1`); `null` aparece "sem leitura", nunca 0.
  - Anúncios: tabela por canal/conta (canal, anúncio com link para o ML, quem controla [selo Noromak/Alephee/Full], situação, preço, estoque no ML, tipo, lido em); "Passar para o Noromak" abre `nkConfirmar` com a pergunta "Você já desativou este anúncio na Alephee?" e manda `confirmou_alephee`; aviso amarelo "Produto misto: a Alephee também mexe neste estoque" quando `misto`; linhas dos canais sem anúncio (Shopee, Magalu, TikTok, Amazon) com "em breve".
- Consulta (`somente_leitura`) e Supervisor: botões de gravar escondidos.
- Rodar `python FERRAMENTAS_WINDOWS/gerar_modo_escuro.py APP/anuncios.html` se usar cor fixa.

- [ ] **Step 1: Teste** `test_tela_tem_as_pecas_padrao`: lê `APP/anuncios.html` e confere `nk-ui.css`, `nk-ui.js`, `nk-menu.js` com `data-shell`, `nk-pagina`, `nk-cab`; e `nk-menu.js` contém `href: "/anuncios"`.
- [ ] **Step 2:** rodar, ver falhar (arquivo não existe).
- [ ] **Step 3:** criar a tela e o item do menu; subir `?v=` e `CACHE`.
- [ ] **Step 4:** rodar o teste, `test_rotas_v1882`, `test_ui_v1883` e `node --check APP/nk-menu.js` — OK.
- [ ] **Step 5: Verificar no navegador** (preview local com banco de teste, `EXP_WORKERS=0`): lista, abrir ficha, salvar, editar kit, modo escuro. Screenshot para o dono.
- [ ] **Step 6: Commit** `V20.23 - Anuncios: tela /anuncios com ficha, kits e anuncios por canal`

---

### Task 7: Versão, documentação e validação

**Files:**
- Modify: `servidor_web.py:51` (`APP_VERSION = "20.23-ANUNCIOS-FICHA"`), `VERSAO.txt`, `CLAUDE.md` (seção nova em §6 "Plataforma de anúncios (V20.23, fase 1)" com tabelas, rotas, regras e o teste; linha do teste na tabela de §8 se couber)

- [ ] **Step 1:** atualizar versão e CLAUDE.md.
- [ ] **Step 2:** `./testar.sh` — Expected: todos os testes OK (mostrar a saída real ao dono, inclusive o que não foi testado: chamada real ao ML).
- [ ] **Step 3:** skill `revisar-antes-de-subir` sobre `producao/master..HEAD`; mostrar achados ao dono.
- [ ] **Step 4: Commit** `V20.23 - Anuncios fase 1: versao e documentacao`
- [ ] **Step 5:** pedir o "pode subir" com horário (antes das 08:00 ou depois das 18:00) e lembrar o custo do backup; lembrar `git push origin master`.

**Depois de subir (com o dono):** clicar "Puxar anúncios do ML", conferir 5 anúncios de cada conta (SKU ligado à ficha certa, Full marcado, kits) e a contagem de "sem ficha" — é o insumo do plano 2.
