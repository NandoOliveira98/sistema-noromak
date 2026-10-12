# Campanhas do ML na linha do anúncio (V19.04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Na tela Promoções automáticas, cada anúncio mostra na própria linha a campanha do ML que dá mais lucro por venda que a promoção dele (com Aceitar), e as demais num "ver outras"; a lista separada de convites some.

**Architecture:** As campanhas passam a ser lidas **por anúncio** (`GET /seller-promotions/items/{MLB}?app_version=v2`), porque a lista da conta (`/seller-promotions/users/{id}`) não traz todas (12/10: 8 campanhas na lista; SMART, PRICE_MATCHING e outra SMART só apareciam por anúncio). Gravam em `ml_convite_v1889` como hoje. O `promo_renovacao_payload` passa a anexar a cada linha as campanhas dela com lucro por venda e a melhor; a tela desenha uma coluna nova.

**Tech Stack:** Python stdlib, SQLite, HTML/JS puro (`APP/promocoes-auto.html`), unittest.

**Spec (decisões do dono, 12/10, entrevista + protótipo 3 aprovado):**
1. Layout atual da tela **mantido**; entra uma coluna **"Campanha do ML"** entre Margem e Situação, um cartão "Campanha do ML dá mais lucro" no fim dos cartões e um filtro de mesmo nome no fim dos filtros. A seção separada de convites (lista) **sai**.
2. A coluna mostra só a **melhor** campanha: tipo, preço ao comprador, lucro por venda e "+R$ X" sobre a sua promoção, botão **Aceitar**. Sem melhor: "Nenhuma campanha dá mais lucro que a sua promoção". As outras ficam em "ver outras N campanhas" (tipo · preço · lucro, vermelho/verde; "abaixo da mín." quando for o caso).
3. Na coluna Promoção, linha a mais: "lucro R$ X/venda" da promoção atual.
4. Critério = **lucro por venda em R$** (o preço da promoção atual, que vem do Precificador/renovação, contra cada campanha). "Mais vantajosa" = lucro maior que o da promoção **e** margem ≥ `margem_min_promo` **e** situação "pronto".
5. "Redução de tarifas" = `meli_percentage`: o que você recebe = preço ao comprador + meli% × preço original (= original × (1 − seller%)), como `_promo_convite_receita` já faz; tarifa e frete do anúncio (`_promo_prod_com_ml`). Conferido 12/10 contra a tela do ML: diferença de R$ 0,05–1,50, sempre para menos (conservador).
6. **Só sugere**: o aceite é manual (botão). O modo automático dos convites continua existindo como hoje (o controle de modo vai para o bloco de Regras), sem mudança de regra.
7. **Oferta relâmpago (LIGHTNING)**: entra na comparação, **sem botão Aceitar** (exige escolher estoque; aceita no ML) — texto "aceitar no ML".
8. Fora: cupom (SELLER_COUPON_CAMPAIGN), PRICE_DISCOUNT (é a própria promoção), UNHEALTHY_STOCK.

## Global Constraints
- Comentários `# V19.04:`; `APP_VERSION = "19.04-CAMPANHAS-NA-LINHA"` + `VERSAO.txt`.
- Leitura do ML só GET; aceitar continua por `promo_convite_aceitar` (reconfere com o anúncio lido agora).
- Por conta (Araçatuba/Bauru): cada anúncio lido com o token da conta dele (`ml_na_casa`), como `_promo_convites_rodada_conta`.
- Valor desconhecido (sem custo, ML sem resposta) fica **nulo** e visível ("sem custo"), nunca zero.
- Removido pelo ADMIN (`promo_removidos_v1876`) nunca aparece.
- Telas: peças `nk-*`/`--nk-*`; edição cirúrgica no HTML; texto em português.

---

### Task 1: Leitura das campanhas por anúncio
**Files:** `servidor_web.py` (perto de `_promo_convites_rodada_conta`, ~22186), `TESTES/test_promo_convites_v1889.py` (estender) ou `TESTES/test_campanhas_linha_v1904.py` (novo).
- Nova `_promo_campanhas_do_anuncio(mlb) -> list[dict]`: `GET /seller-promotions/items/{mlb}?app_version=v2` via `_ml_promo_http`.
- Na rodada de convites de cada conta: além do que já faz, percorre os anúncios da lista da renovação **daquela conta** (`promo_renovacao_v1873` + monitor, menos removidos), com `PROMO_CONVITE_PAUSA` entre chamadas, e grava cada item de tipo em `PROMO_CONVITE_TIPOS ∪ {"LIGHTNING"}` com status `candidate|pending|started` em `ml_convite_v1889` (mesmo upsert; `promotion_id` = `id`; campos `price`, `original_price`, `meli_percentage`, `seller_percentage`, `min/max/suggested_discounted_price`, `start_date`/`finish_date`, `ref_id`→`offer_id`, `stock` não precisa).
- Limpeza: convite da conta cujo anúncio foi lido nesta rodada e o par (promotion_id, mlb) não veio mais → apaga (aceito fica, como hoje). Anúncio que deu erro na leitura não limpa nada.
- `_promo_convite_avaliar`: tipo LIGHTNING → situação `so_ver` (motivo "Oferta relâmpago: aceitar no ML"), receita = `price` ou `suggested_discounted_price`, margem calculada; nunca "pronto". `promo_convite_aceitar` recusa LIGHTNING.
- Testes (ML simulado): item SMART só visto por anúncio é gravado; PRICE_MATCHING gravado; LIGHTNING gravado e nunca "pronto"/aceito; cupom e PRICE_DISCOUNT ignorados; limpeza só do anúncio lido; erro de um anúncio não apaga; removido não entra; cada conta com o seu token.

### Task 2: Comparação na linha (payload)
**Files:** `servidor_web.py` (`promo_renovacao_payload` ~22453 e helpers), testes.
- Para cada linha da lista: `lucro_promo` = `promo_conta(prod, preco_da_promocao)["lucro"]` com o mesmo `prod` da coluna Margem (`_promo_prod_com_ml`); nulo sem custo.
- `campanhas`: convites do anúncio/conta (não expirados), cada um `{promotion_id, tipo, nome_tipo, preco, recebe, lucro, margem, situacao, motivo, pode_aceitar}`; lucro = `promo_conta(prod, receita)["lucro"]` (receita já avaliada).
- `campanha_melhor` = maior lucro com `lucro > lucro_promo`, margem ≥ `margem_min_promo`, situação `pronto`; com `ganho` = lucro − lucro_promo. Sem `lucro_promo` (sem promoção ativa), compara com o preço do anúncio.
- Contador para o cartão/filtro: `resumo.campanha_mais_lucro`.
- Testes: melhor escolhida pelo lucro, não pela margem; abaixo da mínima nunca é melhor; LIGHTNING pode ser melhor mas `pode_aceitar=False`; sem custo → lucros nulos e nenhuma melhor; contador.

### Task 3: Tela, versão e documentação
**Files:** `APP/promocoes-auto.html`, `servidor_web.py:47`, `VERSAO.txt`, `CLAUDE.md` (seção Promoções automáticas: parágrafo V19.04), teste estático.
- Coluna, cartão, filtro e "lucro R$/venda" na Promoção conforme spec (referência visual: protótipo 3 aprovado). Aceitar chama `/api/promocoes-auto/convites/aceitar` com o convite e recarrega. Seção `#convitesBox` (lista) removida; o controle de modo dos convites vai para o bloco de Regras (mesmos ids/comportamento, inclusive OWNER no automático).
- `./testar.sh` inteiro; conferir no navegador com dados de exemplo.
