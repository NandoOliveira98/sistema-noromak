# Remessa Full automática (V19.03) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O servidor monta toda manhã a remessa sugerida para o Full (Araçatuba), cruza com o
estoque do Apollo, avisa quando passa de 150 unidades e a tela dispara a procura de datas na
extensão do Agendador Full.

**Architecture:** Cálculo puro `full_remessa_calcular()` (só banco + estoque já lido) separado da
rodada `full_remessa_rodada()` (lê o Apollo pelo `crm_stock_lookup`, grava, avisa). Fontes que já
existem: anúncios Full e o estoque no Full = `ml_estoque_dia_v1872` (`logistica='fulfillment'`,
`estoque_ultimo` do dia mais recente) + SKU do anúncio em `ml_visitas_item_v1868.sku`; vendas =
`vendas_v1799` (marketplace `ml`); código Apollo = `_compras_codigo`; kit = `full_kit_compositions`.
Novas tabelas: `full_remessa_v1903` (cabeçalho), `full_remessa_item_v1903` (linhas, com ajuste
ADMIN), `full_nao_aceito_v1903` (tombstone por código). Aviso reaproveita
`aviso_venda_v1893` (marketplace `full`), e-mail das 08:00 ganha a linha. Tela: seção nova no topo de
`APP/analise-full.html`.

**Tech Stack:** Python stdlib (`servidor_web.py`), SQLite, HTML/JS puro, `unittest` em `TESTES/`.

**Spec:** `docs/superpowers/specs/2026-10-11-remessa-full-automatica-design.md`

## Global Constraints

- Só Araçatuba: Apollo revenda **1**; vendas só de `vendas_v1799` (é a conta principal).
- Prazo **30 dias**; venda diária = unidades 30 dias ÷ 30; necessidade = max(0, ceil(diária×30) − no Full − a caminho).
- Qualquer kit conta **1** unidade na remessa; no Apollo consome cada componente × quantidade.
- Mínimo **150** unidades para `pronta`. Abaixo, `montando` e sem aviso. Nunca completa à força.
- Novos: **≥ 10** unidades vendidas em 30 dias, código sem anúncio Full, máx. **10** unidades, `confirmado=0` até o ADMIN confirmar; não contam nos 150 enquanto não confirmados.
- Não aceito: por código Apollo (ou SKU quando não resolve), só ADMIN libera.
- `disponivel: null` do Apollo **nunca** vira zero: item fica com `estoque_apollo=None` e qtd 0, marcado "Apollo não respondeu".
- Ajuste humano (qtd, tirar, confirmar) nunca é desfeito pela rodada.
- Lembrete do aviso **uma vez por dia** enquanto `pronta`.
- Comentários de versão `# V19.03:`; `APP_VERSION = "19.03-REMESSA-FULL"` + `VERSAO.txt`.
- Sem dependência externa. Rotas sob `/expedicao/api/full/remessa*`, módulo **admin**.

## Review Focus

- Mesmo código Apollo em vários anúncios Full: estoque dividido, mais urgente primeiro — não oferecer o mesmo estoque duas vezes (teste em Task 2).
- Kit e o componente simples na mesma remessa disputando o mesmo estoque do Apollo (teste em Task 2).
- Itens de remessa `agendada` contam "a caminho" até o estoque no Full subir; não reaparecem (teste em Task 3).
- Anúncio Full sem SKU / SKU que não resolve código Apollo: fica fora com motivo, nunca quebra a rodada (teste em Task 2).
- Rodada sem chave do Apollo / Apollo caído: não marca `pronta` nem avisa (teste em Task 3).

---

### Task 1: Tabelas e lista "Não aceito no Full"

**Files:**
- Modify: `servidor_web.py` (bloco de `CREATE TABLE` perto da linha 2056; funções novas depois de `full_anuncios_vendas`, ~linha 9310)
- Test: `TESTES/test_remessa_full_v1903.py` (novo)

**Interfaces:**
- Produces: `full_nao_aceito_marcar(codigo: str, motivo: str, usuario: str) -> None`,
  `full_nao_aceito_liberar(codigo: str, usuario: str) -> bool`,
  `full_nao_aceito_lista() -> dict[str, dict]` (chave = código maiúsculo).

- [ ] **Step 1: Teste que falha**

```python
"""V19.03 — Remessa Full automática. Apollo e ML simulados."""
import unittest
from base import S, TesteNoromak


class NaoAceito(TesteNoromak):
    def test_marcar_e_liberar(self):
        S.full_nao_aceito_marcar("0893600022", "inflamável", "admin")
        self.assertIn("0893600022", S.full_nao_aceito_lista())
        self.assertEqual(S.full_nao_aceito_lista()["0893600022"]["motivo"], "inflamável")
        self.assertTrue(S.full_nao_aceito_liberar("0893600022", "admin"))
        self.assertNotIn("0893600022", S.full_nao_aceito_lista())

    def test_codigo_normalizado(self):
        S.full_nao_aceito_marcar("  abc-1 ", "", "admin")
        self.assertIn("ABC-1", S.full_nao_aceito_lista())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2:** `python -m unittest TESTES.test_remessa_full_v1903 -v` (rodar de dentro de `TESTES/` como os outros: `cd TESTES && python -m unittest test_remessa_full_v1903 -v`). Esperado: FAIL `AttributeError: full_nao_aceito_marcar`.

- [ ] **Step 3: Tabelas e funções**

No `init_db`, junto das tabelas da V18.72:

```sql
            -- V19.03: remessa Full automática (spec 2026-10-11-remessa-full-automatica-design.md).
            CREATE TABLE IF NOT EXISTS full_remessa_v1903 (
                remessa_id INTEGER PRIMARY KEY AUTOINCREMENT,
                situacao TEXT NOT NULL DEFAULT 'montando',   -- montando|pronta|agendada|recebida
                unidades INTEGER NOT NULL DEFAULT 0,
                itens INTEGER NOT NULL DEFAULT 0,
                montada_em TEXT NOT NULL DEFAULT '',
                avisado_dia TEXT NOT NULL DEFAULT '',
                agendada_para TEXT NOT NULL DEFAULT '',
                agendada_em TEXT NOT NULL DEFAULT '',
                agendada_por TEXT NOT NULL DEFAULT '',
                obs TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS full_remessa_item_v1903 (
                remessa_id INTEGER NOT NULL,
                anuncio TEXT NOT NULL,            -- MLB, ou 'NOVO:<codigo>' para novos
                codigo TEXT NOT NULL DEFAULT '',
                sku TEXT NOT NULL DEFAULT '',
                titulo TEXT NOT NULL DEFAULT '',
                novo INTEGER NOT NULL DEFAULT 0,
                confirmado INTEGER NOT NULL DEFAULT 1,
                venda_30d INTEGER NOT NULL DEFAULT 0,
                no_full INTEGER,
                a_caminho INTEGER NOT NULL DEFAULT 0,
                cobertura_dias REAL,
                necessidade INTEGER NOT NULL DEFAULT 0,
                estoque_apollo REAL,
                qtd_sugerida INTEGER NOT NULL DEFAULT 0,
                qtd_admin INTEGER,                -- ajuste humano: a rodada nunca mexe
                removido INTEGER NOT NULL DEFAULT 0,
                motivo TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(remessa_id, anuncio)
            );
            CREATE TABLE IF NOT EXISTS full_nao_aceito_v1903 (
                codigo TEXT PRIMARY KEY,
                motivo TEXT NOT NULL DEFAULT '',
                marcado_por TEXT NOT NULL DEFAULT '',
                marcado_em TEXT NOT NULL DEFAULT ''
            );
```

Funções:

```python
# V19.03: o ML recusa alguns produtos no Full. Tombstone do ADMIN por código:
# nenhuma rodada tira daqui, só full_nao_aceito_liberar.
def _full_codigo(valor: Any) -> str:
    return str(valor or "").strip().upper()


def full_nao_aceito_marcar(codigo: str, motivo: str, usuario: str) -> None:
    c = _full_codigo(codigo)
    if not c:
        raise ValueError("Informe o código.")
    with DB_LOCK:
        DB.execute("INSERT OR REPLACE INTO full_nao_aceito_v1903(codigo,motivo,marcado_por,marcado_em) VALUES(?,?,?,?)",
                   (c, str(motivo or "").strip()[:200], str(usuario or ""), now_iso()))
        DB.commit()
    audit_event("full_nao_aceito_marcar", usuario, {"codigo": c, "motivo": motivo})


def full_nao_aceito_liberar(codigo: str, usuario: str) -> bool:
    c = _full_codigo(codigo)
    with DB_LOCK:
        n = DB.execute("DELETE FROM full_nao_aceito_v1903 WHERE codigo=?", (c,)).rowcount
        DB.commit()
    if n:
        audit_event("full_nao_aceito_liberar", usuario, {"codigo": c})
    return bool(n)


def full_nao_aceito_lista() -> dict[str, dict[str, Any]]:
    with DB_LOCK:
        rows = DB.execute("SELECT * FROM full_nao_aceito_v1903").fetchall()
    return {r["codigo"]: dict(r) for r in rows}
```

> Antes de escrever, confirmar o nome real da função de auditoria (`grep -n "def audit" servidor_web.py`) e usar a mesma assinatura que as rotas de promoção usam.

- [ ] **Step 4:** rodar o teste — PASS.
- [ ] **Step 5:** `git add servidor_web.py TESTES/test_remessa_full_v1903.py && git commit -m "V19.03 - Remessa Full: tabelas e lista Nao aceito no Full"`

---

### Task 2: Cálculo puro da remessa

**Files:**
- Modify: `servidor_web.py` (depois das funções da Task 1)
- Test: `TESTES/test_remessa_full_v1903.py`

**Interfaces:**
- Consumes: `full_nao_aceito_lista()`, `full_kit_compositions(skus)`, `_compras_codigo(sku, conhecidos)`, `_compras_produtos_precificador()`.
- Produces:
  - `full_remessa_candidatos(hoje: date | None = None) -> dict` → `{"anuncios": [...], "novos": [...], "codigos": set[str]}`; cada anúncio: `{anuncio, sku, titulo, codigo, componentes: [{codigo, qtd}], venda_30d, no_full, a_caminho}`.
  - `full_remessa_calcular(cand: dict, estoque: dict[str, float | None]) -> dict` → `{"itens": [...], "novos": [...], "unidades": int, "fora": [...]}`; item com as colunas de `full_remessa_item_v1903`.
  - Constantes `FULL_REMESSA_PRAZO = 30`, `FULL_REMESSA_MINIMO = 150`, `FULL_NOVO_VENDAS_MIN = 10`, `FULL_NOVO_QTD_MAX = 10`, `FULL_REVENDA = 1`.

Regras de `full_remessa_candidatos`:
- Anúncio Full = item com linha mais recente em `ml_estoque_dia_v1872` com `logistica='fulfillment'` nos últimos 7 dias; `no_full = estoque_ultimo` dessa linha (None se nulo).
- SKU = `ml_visitas_item_v1868.sku`; vazio → vai para `fora` com motivo "anúncio sem SKU".
- Kit (`full_kit_compositions([sku])`) → componentes; senão `codigo = _compras_codigo(sku, conhecidos)` (vazio → `fora` "SKU sem código Apollo"), componente único qtd 1.
- `venda_30d` = soma `qty` de `vendas_v1799` com `marketplace='ml'` e `anuncio=MLB` nos últimos 30 dias.
- `a_caminho` = soma de `COALESCE(qtd_admin, qtd_sugerida)` dos itens não removidos das remessas `agendada` (a Task 3 zera isso ao virar `recebida`).
- Novos: código com `SUM(qty) >= 10` em `vendas_v1799` (ml, 30 dias, por código via `_compras_codigo`, kit fora) que não é código de nenhum anúncio Full.
- Código em `full_nao_aceito_lista()` sai de tudo (anúncio ou novo; kit sai se qualquer componente estiver lá) com motivo "não aceito no Full".

Regras de `full_remessa_calcular`:
- `diaria = venda_30d / 30`; `necessidade = max(0, ceil(diaria*30) - (no_full or 0) - a_caminho)`; `cobertura = (no_full or 0)/diaria` (None sem venda).
- Ordena por cobertura crescente (sem venda por último); para cada anúncio, `qtd = min(necessidade, min(floor(saldo[c]/q) for c,q in componentes))`; desconta `qtd*q` do saldo de cada componente (saldo compartilhado entre anúncios e kits).
- Saldo None (Apollo não respondeu) → `qtd=0`, `estoque_apollo=None`, motivo "Apollo não respondeu".
- Novos depois, mesmo saldo: `qtd = min(venda_30d, 10, saldo)`, `confirmado=0`.
- `unidades` = soma de `qtd` dos itens com `qtd>0` e `confirmado=1`.

- [ ] **Step 1: Testes que falham** (acrescentar ao arquivo)

```python
from datetime import date, timedelta

HOJE = date(2026, 10, 11)


class Calculo(TesteNoromak):
    def anuncio(self, mlb, sku, no_full, venda, dia=HOJE):
        d = (dia - timedelta(days=1)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_estoque_dia_v1872(item_id,dia,checagens,estoque_ultimo,logistica,updated_at) "
                         "VALUES(?,?,1,?,'fulfillment','')", (mlb, d, no_full))
            S.DB.execute("INSERT OR REPLACE INTO ml_visitas_item_v1868(item_id,sku,titulo) VALUES(?,?,?)", (mlb, sku, "T " + mlb))
            if venda:
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                             "VALUES('ml',?,'1',?,?,?,?,'t',?,10,'paid','')", (f"o{mlb}", d, d, sku, mlb, venda))
            S.DB.commit()

    def setUp(self):
        super().setUp()
        self.conhecidos = {"A": {}, "B": {}, "C": {}}
        self._orig = S._compras_produtos_precificador
        S._compras_produtos_precificador = lambda: self.conhecidos

    def tearDown(self):
        S._compras_produtos_precificador = self._orig
        super().tearDown()

    def calc(self, estoque):
        return S.full_remessa_calcular(S.full_remessa_candidatos(HOJE), estoque)

    def test_necessidade_30_dias_menos_o_full(self):
        self.anuncio("MLB1", "A", no_full=10, venda=60)
        r = self.calc({"A": 500})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 50)
        self.assertEqual(r["unidades"], 50)

    def test_limitado_ao_apollo_e_mais_urgente_primeiro(self):
        self.anuncio("MLB1", "A", no_full=50, venda=60)   # 25 dias
        self.anuncio("MLB2", "A", no_full=0, venda=30)    # urgente
        r = {i["anuncio"]: i for i in self.calc({"A": 35})["itens"]}
        self.assertEqual(r["MLB2"]["qtd_sugerida"], 30)
        self.assertEqual(r["MLB1"]["qtd_sugerida"], 5)

    def test_apollo_sem_resposta_nao_vira_zero_nem_estoque(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        i = self.calc({"A": None})["itens"][0]
        self.assertIsNone(i["estoque_apollo"])
        self.assertEqual(i["qtd_sugerida"], 0)
        self.assertIn("Apollo", i["motivo"])

    def test_kit_conta_1_e_consome_componentes(self):
        self.anuncio("MLB9", "KIT2-A", no_full=0, venda=30)
        r = self.calc({"A": 20})
        self.assertEqual(r["itens"][0]["qtd_sugerida"], 10)   # 20 peças / 2 por kit
        self.assertEqual(r["unidades"], 10)

    def test_nao_aceito_sai(self):
        self.anuncio("MLB1", "A", no_full=0, venda=30)
        S.full_nao_aceito_marcar("A", "recusado", "admin")
        r = self.calc({"A": 100})
        self.assertEqual(r["itens"], [])
        self.assertTrue(any("não aceito" in f["motivo"] for f in r["fora"]))

    def test_anuncio_sem_codigo_fica_fora_com_motivo(self):
        self.anuncio("MLB1", "ZZZ", no_full=0, venda=30)
        r = self.calc({})
        self.assertEqual(r["itens"], [])
        self.assertEqual(r["fora"][0]["anuncio"], "MLB1")

    def test_novo_sugerido_ate_10_sem_confirmar(self):
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = self.calc({"C": 100})
        self.assertEqual(r["novos"][0]["qtd_sugerida"], 10)
        self.assertEqual(r["novos"][0]["confirmado"], 0)
        self.assertEqual(r["unidades"], 0)
```

> Conferir em `_infer_quantity_kit_from_sku` que "KIT2-A" vira 2×"A"; se o componente sair como "A" com outra grafia, ajustar a fixture, não a regra.

- [ ] **Step 2:** rodar — FAIL (`full_remessa_candidatos` não existe).
- [ ] **Step 3:** implementar as duas funções seguindo as regras acima (ler `vendas_v1799` e `ml_estoque_dia_v1872` dentro de `with DB_LOCK:`; `estoque` vem pronto do chamador — a função **não** chama o Apollo).
- [ ] **Step 4:** rodar — PASS.
- [ ] **Step 5:** commit `V19.03 - Remessa Full: calculo da sugestao (30 dias, Apollo, kit conta 1, novos)`.

---

### Task 3: Rodada, gravação e ajustes do ADMIN

**Files:**
- Modify: `servidor_web.py`
- Test: `TESTES/test_remessa_full_v1903.py`

**Interfaces:**
- Consumes: Task 2; `crm_stock_lookup(codigo, operador, wait_seconds)` (levanta `CrmLimiteAtingido`; `estoques[].revenda/disponivel`, `casas_sem_resposta`).
- Produces:
  - `full_remessa_estoque_apollo(codigos: set[str], prazo_s: float = 600) -> dict[str, float | None]` — revenda 1; casa sem resposta = None; espera o limite (`wait_seconds` até o prazo).
  - `full_remessa_rodada(hoje: date | None = None, estoque: dict | None = None) -> dict` — grava/atualiza a remessa aberta (`montando`/`pronta`); `estoque` injetável nos testes.
  - `full_remessa_atual() -> dict | None` — cabeçalho + itens + novos + fora + lista não aceito.
  - `full_remessa_ajustar(remessa_id: int, anuncio: str, *, qtd: int | None = None, removido: bool | None = None, confirmado: bool | None = None, usuario: str) -> dict`.
  - `full_remessa_agendar(remessa_id: int, data: str, usuario: str) -> dict` e `full_remessa_situacao(remessa_id, situacao, usuario)` para `recebida`.

Regras:
- Existe no máximo **uma** remessa aberta (`montando`/`pronta`). A rodada recalcula as linhas dela: atualiza `qtd_sugerida`, números e motivo; **não toca** `qtd_admin`, `removido`, `confirmado` de linha que já existe (exceto `confirmado` de não-novo, sempre 1). Linha que sumiu do cálculo e não tem ajuste humano é apagada; com ajuste fica.
- Total = soma de `COALESCE(qtd_admin, qtd_sugerida)` das linhas não removidas e confirmadas. `pronta` se ≥ 150, senão `montando`. Se `estoque` tiver algum None para código que tem necessidade, a remessa **não** sobe para `pronta` naquela rodada (fica como estava) e `obs` diz "Apollo não respondeu para N códigos".
- `agendada`: cabeçalho congelado (rodada não mexe mais); a próxima rodada abre remessa nova; os itens dela entram como `a_caminho`.
- Virar `recebida`: manual pela tela, ou automático quando, para todos os anúncios da remessa, `no_full` atual ≥ `no_full` da hora do agendamento + 50% da qtd (gravar `no_full` na linha ao agendar já é o caso — a coluna guarda o valor da última rodada antes de congelar).
- Ajuste em remessa `agendada`/`recebida` → `ValueError("Remessa já agendada.")`.

- [ ] **Step 1: Testes que falham**

```python
class Rodada(Calculo):
    def test_pronta_com_150(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(r["situacao"], "pronta")
        self.assertEqual(r["unidades"], 160)

    def test_abaixo_de_150_espera(self):
        self.anuncio("MLB1", "A", no_full=0, venda=100)
        self.assertEqual(S.full_remessa_rodada(HOJE, estoque={"A": 1000})["situacao"], "montando")

    def test_ajuste_admin_sobrevive_a_rodada(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_ajustar(r["remessa_id"], "MLB1", qtd=40, usuario="admin")
        r2 = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertEqual(r2["remessa_id"], r["remessa_id"])
        item = S.full_remessa_atual()["itens"][0]
        self.assertEqual(item["qtd_admin"], 40)
        self.assertEqual(r2["situacao"], "montando")   # 40 < 150

    def test_agendada_vira_a_caminho_e_nao_repete(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        S.full_remessa_agendar(r["remessa_id"], "2026-10-20", "admin")
        r2 = S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertNotEqual(r2["remessa_id"], r["remessa_id"])
        self.assertEqual(r2["unidades"], 0)

    def test_apollo_caido_nao_marca_pronta(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        r = S.full_remessa_rodada(HOJE, estoque={"A": None})
        self.assertEqual(r["situacao"], "montando")
        self.assertIn("Apollo", r["obs"])

    def test_confirmar_novo_entra_na_conta(self):
        self.anuncio("MLB1", "A", no_full=0, venda=145)
        d = (HOJE - timedelta(days=2)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,anuncio,title,qty,amount,status,updated_at) "
                         "VALUES('ml','n1','1',?,?,'C','MLB77','t',14,10,'paid','')", (d, d))
            S.DB.commit()
        r = S.full_remessa_rodada(HOJE, estoque={"A": 1000, "C": 100})
        self.assertEqual(r["situacao"], "montando")
        S.full_remessa_ajustar(r["remessa_id"], "NOVO:C", confirmado=True, usuario="admin")
        self.assertEqual(S.full_remessa_rodada(HOJE, estoque={"A": 1000, "C": 100})["situacao"], "pronta")
```

- [ ] **Step 2:** rodar — FAIL.
- [ ] **Step 3:** implementar (tudo sob `DB_LOCK` na gravação; leitura do Apollo **fora** do lock).
- [ ] **Step 4:** rodar — PASS.
- [ ] **Step 5:** commit `V19.03 - Remessa Full: rodada, ajuste do ADMIN e agendamento`.

---

### Task 4: Worker, aviso e e-mail das 08:00

**Files:**
- Modify: `servidor_web.py` (worker ao lado de `ml_auditoria_loop`; `SAUDE_WORKERS_INFO` ~linha 27530; `main()`; `relatorio_diario_enviar` ~linha 19120; `nk-avisos.js`/`sw.js` para o canal "Full")
- Test: `TESTES/test_remessa_full_v1903.py`

**Interfaces:**
- Produces: `full_remessa_loop(stop_event)`, `full_remessa_avisar(hoje: date | None = None) -> bool`, `full_remessa_resumo_email() -> str`.

Regras:
- Worker: acorda a cada 30 min; roda **uma vez por dia** depois das 05:00 (Brasília) (o dia da última rodada fica em `montada_em[:10]` da remessa mais recente; se já é hoje, não roda de novo), e depois chama `full_remessa_avisar()`.
- `full_remessa_avisar`: remessa `pronta` com `avisado_dia != hoje` → `INSERT` em `aviso_venda_v1893` com `marketplace='full'`, `venda_id=f"remessa:{id}:{hoje}"`, `titulo=f"Remessa Full pronta: {unidades} un. em {itens} itens"`, `silencioso=0`; grava `avisado_dia=hoje`; `AVISOS_VENDA_EVENT.set()`. Uma vez por dia.
- E-mail: `dados["remessa_full"] = full_remessa_resumo_email()` ao lado de `dados["cobrancas"]`, e a linha entra no texto/HTML do mesmo jeito que a de cobranças (conferir `relatorio_diario_texto`/`html`).
- `SAUDE_WORKERS_INFO["full_remessa_loop"] = ("Remessa Full automática", False, 3600)`.
- Front: em `APP/nk-avisos.js` linha 102, `v.marketplace === "full" ? "Full"`; ao clicar, abrir `/expedicao/analise-full`. Em `sw.js`, notificação da venda `full` com `data.url` = `/expedicao/analise-full`. Subir o `CACHE` do `sw.js` e o `?v=` do `nk-avisos.js` onde é carregado (`nk-menu.js`).

- [ ] **Step 1: Testes que falham**

```python
class Aviso(Calculo):
    def test_avisa_uma_vez_por_dia(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertTrue(S.full_remessa_avisar(HOJE))
        self.assertFalse(S.full_remessa_avisar(HOJE))
        self.assertTrue(S.full_remessa_avisar(HOJE + timedelta(days=1)))
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM aviso_venda_v1893 WHERE marketplace='full'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_montando_nao_avisa(self):
        self.anuncio("MLB1", "A", no_full=0, venda=10)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertFalse(S.full_remessa_avisar(HOJE))

    def test_linha_do_email(self):
        self.anuncio("MLB1", "A", no_full=0, venda=160)
        S.full_remessa_rodada(HOJE, estoque={"A": 1000})
        self.assertIn("160", S.full_remessa_resumo_email())

    def test_worker_no_painel_de_saude(self):
        self.assertIn("full_remessa_loop", S.SAUDE_WORKERS_INFO)
```

- [ ] **Step 2–4:** FAIL → implementar → PASS. Rodar também `cd TESTES && python -m unittest test_saude_v1883 test_avisos_venda_v1893 test_relatorio_diario -v`.
- [ ] **Step 5:** commit `V19.03 - Remessa Full: rotina diaria, aviso e linha no e-mail`.

---

### Task 5: Rotas e permissões

**Files:**
- Modify: `servidor_web.py` (junto de `/api/full/apollo-stock`, ~linha 24516; GET no `do_GET`)
- Test: `TESTES/test_remessa_full_v1903.py` (usa o cliente HTTP de `base.py` — ver como `test_auditoria_cobrancas_v1901.py` faz login de admin e de operador)

Rotas (todas exigem `module_allowed(ctx, "admin")`; operador = 403):
- `GET  /api/full/remessa` → `full_remessa_atual()` + `historico` (últimas 10 agendadas/recebidas).
- `POST /api/full/remessa/ajustar` `{remessa_id, anuncio, qtd?|removido?|confirmado?}`.
- `POST /api/full/remessa/agendar` `{remessa_id, data}` (data `YYYY-MM-DD`).
- `POST /api/full/remessa/situacao` `{remessa_id, situacao:"recebida"}`.
- `POST /api/full/remessa/montar` → roda `full_remessa_rodada()` em thread (`threading.Thread(daemon=True)`), responde `{"ok":true,"rodando":true}` (Apollo lento passa dos 60 s do nginx).
- `POST /api/full/nao-aceito` `{codigo, motivo}` / `POST /api/full/nao-aceito/liberar` `{codigo}`.

- [ ] **Step 1:** testes: operador recebe 403 em `GET /api/full/remessa`; admin marca não aceito e a lista volta no GET; ajustar remessa agendada = 400.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5:** commit `V19.03 - Remessa Full: rotas`.

---

### Task 6: Tela (seção "Remessa sugerida" na Demanda Full) e extensão

**Files:**
- Modify: `APP/analise-full.html` (seção nova no topo; edição cirúrgica, não regenerar)
- Modify: `INTEGRACAO_AGENDADOR_FULL.md` (novo, contrato da mensagem)

Conteúdo da seção (peças `nk-painel`, `nk-kpi`, `nk-selo`, `nk-btn`, tabela `data-nk-tabela`):
- Cartões: situação (selo Montando / Pronta para agendar / Agendada dd/mm), unidades / 150, itens, montada em.
- Tabela da remessa: anúncio, título, código, venda 30 d, no Full, cobertura, necessidade, Apollo (vazio + "não respondeu" quando null), **Qtd** (input; muda → `ajustar` com `qtd`), botões "Tirar" e "Não aceito no Full" (abre `nkConfirmar` com campo de motivo).
- "Novos sugeridos" (recolhível): mesma tabela + botão "Confirmar".
- "Fora da remessa" (recolhível): motivo de cada um; e "Não aceitos no Full" com "Liberar de novo".
- Botões: "Montar agora" (`/montar`, depois recarrega em 20 s), "**Procurar datas**" (só em `pronta`), "Marcar agendada" (pede a data), "Marcar recebida".
- "Procurar datas":

```js
window.postMessage({tipo: "noromak-full-procurar-datas", versao: 1, remessa_id: r.remessa_id,
  itens: r.itens.filter(i => !i.removido && i.confirmado && (i.qtd_admin ?? i.qtd_sugerida) > 0)
    .map(i => ({anuncio: i.anuncio, sku: i.sku, titulo: i.titulo, quantidade: i.qtd_admin ?? i.qtd_sugerida}))}, location.origin);
```

  Sem resposta da extensão em 3 s (`noromak-full-procurar-datas-ok`), mostrar `nkToast("Extensão do Agendador Full não respondeu: instale a v0.3")`.
- `INTEGRACAO_AGENDADOR_FULL.md`: o formato acima, a resposta `ok`, e que a extensão v0.3 (fora do repo, `Desktop\NOROMAK_AGENDADOR_FULL`) precisa de content script em `/expedicao/analise-full`.
- Rodar `python FERRAMENTAS_WINDOWS/gerar_modo_escuro.py APP/analise-full.html` depois de mexer em cor.

- [ ] **Step 1:** teste estático (padrão de `test_ui_v1883.py`): `analise-full.html` contém `noromak-full-procurar-datas` e chama `/api/full/remessa`.
- [ ] **Step 2–4:** implementar; abrir no preview (`preview_start`) com servidor local de teste e conferir console sem erro e a seção desenhando com dados de exemplo.
- [ ] **Step 5:** commit `V19.03 - Remessa Full: secao na Demanda Full e contrato com o Agendador`.

---

### Task 7: Versão, documentação e suíte

**Files:** `servidor_web.py:47`, `VERSAO.txt`, `CLAUDE.md` (seção nova "Remessa Full automática (V19.03)" em §6 + linha na tabela de testes)

- [ ] `APP_VERSION = "19.03-REMESSA-FULL"` e `VERSAO.txt` igual.
- [ ] `./testar.sh` inteiro — mostrar o resultado real ao dono.
- [ ] Commit final `V19.03 - Remessa Full automatica: ...` (sem acentos). **Não publicar** — pedir o "pode subir" (seção 0), lembrar horário e custo do backup.
