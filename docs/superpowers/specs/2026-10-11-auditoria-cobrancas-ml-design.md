# V19.01 — Auditor de cobranças do Mercado Livre (design)

**Data:** 11/10/2026 · **Status:** aprovado na conversa, aguardando revisão do spec escrito.
**Origem:** ideia 11 do brainstorming de 11/10 ("dinheiro que escapa"). Entrevista feita com o
dono, uma pergunta por vez; as decisões abaixo são dele, salvo as marcadas *(técnica)*.

---

## 1. Objetivo

Encontrar o dinheiro que o Mercado Livre cobrou indevidamente na fatura, transformar cada
ocorrência num **caso** acompanhado até o fim (contestado → recuperado ou negado) e mostrar
quanto voltou. Não existe API para contestar cobrança: a contestação continua sendo feita pela
equipe na Central de Ajuda do ML. O sistema **só lê** o ML; nunca escreve lá.

**Sucesso:** a lista de "A contestar" tem poucos alarmes falsos, cada caso sai com o texto e a
planilha prontos para a contestação, e o cartão "Recuperado no mês" mostra dinheiro voltando.

Primeira versão deliberadamente simples: as regras serão afinadas com o uso do dia a dia
(decisão do dono).

## 2. Decisões

| Ponto | Decisão |
|---|---|
| Tipos | estorno que não veio · frete de devolução indevido · cobrado acima do esperado · ajuste de peso/medidas |
| Acompanhamento | caso com situação até o fim; o crédito na fatura marca Recuperado sozinho |
| Carência | 30 dias para estorno de cancelamento/devolução (antes disso, "Aguardando estorno") |
| Valor mínimo | R$ 2,00 por pedido só em "acima do esperado"; diferenças menores somam num cartão do mês. Os outros tipos entram com qualquer valor |
| Conta | só Araçatuba (`CASA_PRINCIPAL`); a tabela de casos já nasce com a coluna `conta` |
| Histórico | 6 meses na primeira carga, depois mês a mês |
| Saída | planilha `.csv` dos casos selecionados + "Copiar texto da contestação" por caso |
| Acesso | igual ao Financeiro: módulo `admin` (Admin e Supervisor) vê e trabalha os casos; toda mudança vai para o `audit_log` |
| Aviso | uma linha no relatório diário das 08:00 |
| Dispensar | "Não contestar" pede motivo; o caso vira Ignorado e **nunca é recriado** pela rotina; pode ser reaberto à mão |
| Abordagem | casos persistidos em tabela própria, alimentada por rotina noturna (opção A) |

## 3. Dados

### 3.1 `ml_cobranca_caso_v1901` (nova)

| Coluna | Conteúdo |
|---|---|
| `caso_id` TEXT PK | chave determinística: `<tipo>:<order_id>` (frete acima do esperado: `frete_acima:<shipment_id>`; peso: `peso:<detail_id>`) |
| `conta` | `aracatuba` por enquanto |
| `tipo` | `estorno`, `devolucao`, `tarifa_acima`, `frete_acima`, `peso` |
| `order_id`, `pack_id`, `shipment_id`, `claim_id` | vínculos (vazios quando não se aplicam) |
| `sale_day` | dia da venda |
| `evento_em` | data que dispara a carência (cancelamento, reembolso, fechamento da reclamação) ou da cobrança |
| `periodo_fatura` | mês da fatura do lançamento principal (`AAAA-MM`) |
| `lancamentos_json` | lista `[{detail_id, sub_type, descricao, valor, data, credito}]` que prova o caso |
| `cobrado`, `esperado`, `diferenca`, `recuperado` | REAL; `diferenca = cobrado − esperado` |
| `situacao` | `aguardando`, `a_contestar`, `contestado`, `recuperado`, `recuperado_parcial`, `negado`, `ignorado` |
| `contestado_em`, `contestado_por`, `protocolo` | preenchidos pelo "Contestei" |
| `ignorado_em`, `ignorado_por`, `motivo_ignorado` | preenchidos pelo "Não contestar" |
| `prova_json` | dados extras para o texto (ex.: medidas e peso do anúncio no tipo peso) |
| `criado_em`, `atualizado_em`, `avisado_em` | `avisado_em` = quando entrou na linha do e-mail (um aviso por caso) |

**Regra de ouro:** a rotina pode atualizar **números e lançamentos** de um caso existente, mas
**nunca** a situação marcada por uma pessoa (`contestado`, `negado`, `ignorado`) nem os campos
humanos. A única transição automática sobre situação humana é para `recuperado` /
`recuperado_parcial`, porque vem do dado da fatura. Um caso `ignorado` continua na tabela — é a
presença dele que impede a recriação (mesma lógica de tombstone da regra do ADMIN).

Transições automáticas permitidas: `aguardando → a_contestar` (venceu a carência);
`aguardando → recuperado` (o estorno chegou dentro da carência); qualquer situação exceto
`ignorado` → `recuperado`/`recuperado_parcial` quando há crédito. Em `ignorado`, o crédito só
atualiza o campo `recuperado`.

### 3.2 `ml_pedido_status_v1901` (nova)

`order_id` PK, `status`, `cancelado_em`, `motivo`, `consultado_em`, `erro`. Memória de
`GET /orders/{id}` para os pedidos com cobrança na fatura que **não estão** em `vendas_v1799`
(venda cancelada some de lá). Consultado uma vez; erro é tentado de novo em outra noite.

### 3.3 `ml_frete_envio_v1830` (coluna nova) *(técnica)*

`bruto REAL` — valor cheio do envio, lido da **mesma** resposta de `/shipments/{id}/costs` que
já é buscada (`gross_amount`). Motivo: a fatura lança o frete cheio (comprador + vendedor,
V18.53); comparar com `senders[].cost` acusaria quase toda venda. Envios antigos sem `bruto` são
relidos pela rotina, com limite por noite. Migração em `ensure_...` idempotente.

## 4. Rotina `ml_auditoria_loop`

Só entre **02:00 e 06:00 (Brasília)**, uma rodada por noite, embrulhada em `_worker_protegido`
e registrada em `SAUDE_WORKERS_INFO`. Roda na casa principal.

1. **Fatura:** `_ml_billing_v1513_sync_period` para `BILL` e `CREDIT_NOTE` do mês atual e do
   anterior (`force`), mais **um mês antigo por noite** até cobrir 6 meses. Usa o
   `FINANCEIRO_SYNC_LOCK` (não bloqueante: se o Sincronizar do Financeiro estiver rodando, pula
   esta etapa nesta noite).
2. **Status dos candidatos:** pedidos com lançamento na fatura (6 meses) que não estão em
   `vendas_v1799` e não estão em `ml_pedido_status_v1901` → `GET /orders/{id}`, limite por
   rodada (`AUDITORIA_STATUS_POR_RODADA`, 300) com 0,25 s entre chamadas.
3. **Valor cheio do frete** dos envios sem `bruto`, com limite por rodada.
4. **Detecção:** `auditoria_detectar(hoje) -> list[dict]` — função **pura**: só lê o banco,
   não chama o ML, devolve os casos calculados. Depois `auditoria_gravar(casos)` faz o upsert
   respeitando a regra de ouro.
5. **Recuperação:** para cada caso aberto, soma os créditos (`is_credit=1`) daquele pedido/envio,
   do mesmo grupo de cobrança, com data posterior a `criado_em`.

Botão **"Auditar agora"** (`POST /api/auditoria-ml/rodar`): mesma rodada em segundo plano,
fora da janela horária, com trava própria (`AUDITORIA_LOCK`) — dois cliques não rodam duas.

## 5. Regras de detecção

**Gerais**
- Valor de cada lado = cobranças − créditos do mesmo grupo (tarifa: `ML_FATURA_TARIFA`; frete:
  `ML_FATURA_FRETE`), pela regra de leitura já existente (`_ml_billing_dedupe`, crédito pelo
  dado — V18.36).
- **Um lançamento pertence a um caso só.** Prioridade: estorno > devolução > peso > acima do
  esperado.
- `recuperado ≥ diferenca − 0,01` → `recuperado`; `0 < recuperado` → `recuperado_parcial`.
- **Fora do prazo:** config `prazo_contestacao_dias` (vazio por padrão). Vazio = nada é
  marcado; preenchido = caso mais velho que isso ganha o selo "provavelmente fora do prazo"
  (só visual).

**1. Estorno que não veio (`estorno`)**
- Pedido com `ml_pedido_status_v1901.status = cancelled`: espera-se de volta **tarifa e frete**.
- Pedido com reclamação de **reembolso total** (`ml_reclamacao_v1888.reembolsado ≥ pedido_total`):
  espera-se de volta **só a tarifa** (o frete da ida fica fora até haver casos reais que mostrem
  a regra do ML).
- Reembolso parcial: fora nesta versão.
- `evento_em` = cancelamento/reembolso. Menos de 30 dias → `aguardando`; depois → `a_contestar`.
- Caso só existe se `cobrado − créditos > 0,01`.

**2. Frete de devolução indevido (`devolucao`)**
- Reclamação com `frete_devolucao > 0` **e** (vendedor beneficiado **ou** `cobertura = 1`).
  "Vendedor beneficiado" = a mesma regra do Pós-venda (`respondent` em `beneficiado` e
  `complainant` não), para as duas telas nunca discordarem.
  `esperado = 0`, `cobrado = frete_devolucao`.
- `evento_em` = `fechada_em`; carência de 30 dias como no estorno.

**3. Cobrado acima do esperado (`tarifa_acima`, `frete_acima`)**
- **Tarifa:** tarifa líquida da fatura × soma de `ml_venda_custo_v1830.sale_fee` do pedido. Só
  venda não cancelada com os dois valores.
- **Frete:** frete líquido da fatura **somado por `shipment_id`** (todos os pedidos do carrinho)
  × `ml_frete_envio_v1830.bruto`.
- Diferença ≥ R$ 2,00 → caso, direto em `a_contestar`. Diferença entre 0,01 e 2,00 → só entra na
  soma do cartão "Diferenças pequenas do mês", não vira caso.

**4. Ajuste de peso/medidas (`peso`)**
- **Desligado** até o código do lançamento ser descoberto (seção 7). Ligado: cada lançamento com
  sub_type em `ML_FATURA_AJUSTE_PESO` (constante preenchida depois da descoberta) vira caso
  `a_contestar`, `esperado = 0`. `prova_json` traz peso e medidas do anúncio lidos do ML.
- Sem nenhum lançamento desse código nos 6 meses, a tela diz isso explicitamente.

## 6. Tela, APIs, planilha e e-mail

**Tela** `APP/auditoria-ml.html`, endereço `/auditoria-ml`, menu **Visão geral** abaixo do
Financeiro (`nk-menu.js`, `modulo: "admin"`). Padrão V18.83 (`.nk-pagina`, `.nk-cab`, `.nk-kpi`,
`table[data-nk-tabela]`, `nkConfirmar`, `nkToast`), modo escuro pelo `gerar_modo_escuro.py`.
Nova rota entra nos quatro lugares que `TESTES/test_rotas_v1882.py` confere (`nk-menu.js`,
`web-auth.js`, `shared-storage.js`, `sw.js`); subir `?v=` e o `CACHE` do `sw.js`.

- **Cartões:** A contestar (qtd + R$) · Aguardando estorno · Contestado em aberto · Recuperado no
  mês · Diferenças pequenas do mês.
- **Filtros:** situação (padrão `a_contestar`), tipo, mês da fatura, busca por pedido.
- **Tabela:** seleção, pedido (link da venda no ML), data, tipo, cobrado, esperado, diferença,
  recuperado, situação (+ selo "fora do prazo").
- **Gaveta do caso:** lançamentos (código, descrição, valor, data); texto da contestação +
  **Copiar**; protocolo; botões **Contestei**, **Negado**, **Não contestar** (motivo obrigatório)
  e **Reabrir** (volta para `a_contestar`).
- **Baixar planilha:** `.csv` (`;`, UTF-8 com BOM, como o Exportar do `nk-ui.js`) dos casos
  selecionados: pedido, data da venda, mês da fatura, código e nº do lançamento, cobrado,
  esperado, diferença, motivo, protocolo.
- **Rodapé:** "Auditar agora", hora da última rodada e meses da fatura cobertos.

**Texto da contestação** (montado no servidor, por tipo). Exemplo do estorno:
"Pedido 2000…, venda em 12/09/2026, cancelado em 13/09/2026. Na fatura de 09/2026 foi cobrada a
tarifa de venda de R$ 18,40 (lançamento CVVML nº …), sem o estorno correspondente até hoje.
Solicito a devolução de R$ 18,40."

**APIs** (todas exigem o módulo `admin`, como `/financeiro`):
- `GET /api/auditoria-ml` → `{casos, resumo, rodada, cobertura, config}`.
- `POST /api/auditoria-ml/caso` → `{caso_id, acao: contestar|negar|ignorar|reabrir, protocolo?, motivo?}`;
  `audit(usuario, "ml_cobranca_<acao>", ...)`.
- `POST /api/auditoria-ml/rodar` → dispara a rodada em segundo plano.
- `POST /api/auditoria-ml/config` → `{prazo_contestacao_dias}`.
- `GET /api/auditoria-ml/codigos` (**só perfil Admin**, seção 7).

**E-mail das 08:00:** `relatorio_diario_dados` ganha o bloco; a linha "Cobranças do ML: N casos
novos a contestar (R$ X) · R$ Y recuperados no mês" só aparece com N > 0 ou Y > 0. "Novo" =
`a_contestar` com `avisado_em` vazio; ao enviar, marca `avisado_em`.

## 7. Descoberta do código do ajuste de peso

`GET /api/auditoria-ml/codigos`: lista os `detail_sub_type` distintos de
`ml_billing_period_detail` com quantidade, soma, um `transaction_detail` de exemplo e se o
código já é classificado (tarifa/frete/ads/neutro). Só leitura, só perfil Admin, nunca devolve
dados de cliente. Depois da publicação, abrir essa rota com o dono, achar o código e ligar o
tipo 4 numa versão seguinte.

## 8. Testes — `TESTES/test_auditoria_cobrancas_v1901.py`

Fixtures com lançamentos de fatura, vendas, status de pedido e reclamações, sem rede:

1. Cancelado com tarifa+frete sem crédito → caso `estorno`; com crédito → nenhum caso.
2. Carência: 29 dias → `aguardando`; 31 dias → `a_contestar`; crédito dentro da carência →
   `recuperado`.
3. Reembolso total por reclamação → espera só a tarifa; reembolso parcial → nenhum caso.
4. Frete de devolução com vendedor beneficiado / com cobertura → caso; sem os dois → nenhum.
5. Tarifa acima: diferença 2,50 → caso; 1,50 → só na soma do cartão.
6. Frete acima comparado **por envio** num carrinho de 3 pedidos (não acusa 3×).
7. Um lançamento nunca em dois casos (frete de cancelado não vira também `frete_acima`).
8. A rodada não desfaz `contestado`/`negado`/`ignorado`; caso `ignorado` não é recriado.
9. Crédito total → `recuperado`; parcial → `recuperado_parcial`; `negado` + crédito → `recuperado`.
10. `auditoria_detectar` não chama o ML (rede bloqueada no teste).
11. Operador sem módulo admin → 403 nas rotas; `/codigos` → 403 para Supervisor.
12. Worker em `SAUDE_WORKERS_INFO`; rota nos quatro lugares do teste de rotas.
13. Linha do e-mail aparece uma vez por caso.

## 9. Fora desta versão

Conta de Bauru · Shopee · reembolso parcial · frete da ida na devolução · tipo peso ligado
(depende da seção 7) · contestação automática (não há API).

## 10. Publicação

Seção 0 do CLAUDE.md: `./testar.sh` com o resultado mostrado ao dono, "pode subir" explícito,
publicar antes das 08:00 ou depois das 18:00. Atualizar `APP_VERSION` e `VERSAO.txt` para
V19.01 e acrescentar a seção do auditor ao CLAUDE.md.
