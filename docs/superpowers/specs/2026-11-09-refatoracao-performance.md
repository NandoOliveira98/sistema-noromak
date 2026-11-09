# Refatoração de desempenho — sugestão (09/11/2026)

Sugestão, não decisão. Nada aqui foi feito além da V20.46 (memória). Cada item entra como versão
própria, com teste primeiro, `testar.sh`, `revisar-antes-de-subir` e o "pode subir" do dono.

## Por que agora

Medido em produção em 09/11, entre 13:30 e 14:00 (Brasília):

- **A VPS:** 954 MB de memória e 2 núcleos, e o provedor tomando cerca de 65% da CPU. Ela é
  dividida com o NoromakWeb: porta ~210 MB e site ~115 MB.
- **O Noromak:** 410 MB (memória + swap) com 54 min no ar. Até o `/api/health` levou 16 s.
- **O sintoma:** o cadastro e o romaneio da extensão passaram de 60 s, o nginx cortou e a extensão
  mostrou "erro HTTP". Foram 152 cortes em 3 h, com pico das 09:30 às 11:00.
- **O movimento:** baixo, cerca de 35 chamadas por minuto. O peso vem de **cada chamada fazer
  trabalho demais**, não da quantidade de chamadas.

## O que não pode mudar (vale para todos os itens)

- A ordem ADMIN > Noromak > integrações, os tombstones, o `completed_orders` como barreira final
  (na leitura e na escrita), o bip que só sobe e o cofre manual.
- O que fica guardado em memória serve **só para ler**. Ele nunca é fonte de verdade e nunca grava.
  Quando qualquer coisa de que o resultado depende muda, ele é refeito (o mesmo princípio da
  V20.04).
- O formato das rotas não muda (as extensões dependem dele).
- Dado desconhecido continua em branco, nunca zero.

---

## Fase 0 — feita (V20.46, ainda não publicada)

A memória presa do malloc agora volta ao sistema. São duas medidas: limite de 2 arenas e um
`malloc_trim` a cada 10 min. O Painel de saúde mostra quanto o Noromak usa e quanto devolveu.
**É a primeira medição real de quanto dos 410 MB era sobra.** Ver o painel no dia seguinte antes
de decidir o resto.

## Fase 1 — ganhos rápidos, risco baixo

| # | O quê | Onde | Ganho esperado | Risco |
|---|---|---|---|---|
| 1.1 | **Financeiro: tirar `dict.fromkeys(conhecidos)` de dentro do laço.** Hoje o dicionário de todos os códigos é refeito a cada linha de `ml_profitability_snapshot`, ou seja, linhas × códigos. | `_financeiro_custos_aracatuba` (~linha 32789) | Provavelmente boa parte dos mais de 60 s do resumo. | Muito baixo: o resultado é idêntico. |
| 1.2 | **Sessão sem gravar a cada chamada.** Toda chamada à API faz `UPDATE app_sessions` + `COMMIT`, de 2 a 3 vezes, sob o `DB_LOCK`; o `/api/auth/session` sozinho são 546 por hora. A proposta é gravar o `last_seen` no máximo 1 vez por minuto por sessão e usar a mesma leitura nas duas conferências da mesma requisição. | `session_context`, `consulta_bloqueada` + `self.auth()` | Corta de 1.000 a 1.500 gravações por hora no banco. | Baixo. A expiração continua valendo. **Decisão do dono:** guardar a sessão 30 s em memória atrasaria em até 30 s o efeito de desativar um usuário. A proposta é apagar a sessão guardada sempre que um usuário for alterado, e aí não há atraso. |
| 1.3 | **Índices que faltam:** `completed_orders(completed_at)` (o `conclusoes-dia`, 195 por hora, lê a tabela inteira e ordena) e `audit_log(action, created_at)` (as consultas por ação leem a tabela inteira, que nunca é limpa). | `init_db` | Consultas de segundos viram milissegundos. | Criar o índice numa tabela grande leva tempo no boot: **publicar fora do expediente**. |
| 1.4 | **`conclusoes-dia` guardado por 10 s** e pela "assinatura" de `completed_orders` (quantidade + última conclusão). Hoje abre o JSON de cada pedido concluído do dia, de 1 a 2 vezes, a cada chamada. | `conferencia_conclusoes_do_dia` | ~195 cálculos por hora viram poucos. | Baixo: a conclusão nova muda a assinatura e o cálculo é refeito. |
| 1.5 | **Limitar o `ThreadPoolExecutor(max_workers=len(tarefas))`**, que hoje não tem teto. | ~linha 21251 | Evita picos de threads (e de memória). | Muito baixo. |

## Fase 2 — leitura da fila de pedidos (o maior ganho, risco médio)

**O problema.** Cada leitura de `conf_pedidos` (Conferência, Admin, `cliente-fiscal/pendentes`, os
workers) repete o caminho inteiro **sob o `DB_LOCK`**:

`filter_completed_orders` → enriquecer com o histórico → override do ADMIN → cofre manual →
exclusões → `filter_completed_orders_v1772` → filtro da filial.

São cerca de **6 `json.loads` e 5 `json.dumps` do maior valor**, e `completed_orders` é lido duas
vezes, a cada chamada. Enquanto isso acontece, ninguém mais usa o banco: nem a bipagem, nem os
workers.

**A proposta, em dois passos independentes:**

1. **Guardar a leitura pronta pela assinatura de tudo de que ela depende.** A assinatura junta a
   versão da chave, a assinatura de `completed_orders`, de `admin_manual_overrides`, do cofre e das
   exclusões, e a filial. Ela é feita com consultas baratas (`MAX(rowid)`, `COUNT(*)`, `MAX(updated_at)`)
   em vez de um contador que cada gravação teria de lembrar de subir: isso protege contra quem
   grava fora do `save_storage` (o risco anotado na V20.04).
   - Guarda o **texto JSON** (imutável), nunca a lista, para ninguém alterar a cópia guardada por engano.
   - Padrão já usado e testado: `precificador_dados` (versão + `updated_at` + tamanho) e
     `_ml_fila_leitura` (V20.04).
   - Teste obrigatório: cada barreira do ADMIN alterada (exclusão, override, conclusão, cofre,
     reinclusão) faz a leitura seguinte já sair diferente.
2. **Abrir o JSON uma vez só no caminho.** As etapas passam a receber e devolver a lista em vez de
   texto. É uma mudança mecânica, e a ordem das etapas **não muda** (a `filter_completed_orders_v1772`
   continua a última).

**Ganho esperado:** a maior parte do tempo de `DB_LOCK` das telas e da Conferência. Também cai a
memória dos picos, porque o texto de vários MB deixa de ser montado de novo a cada chamada.

## Fase 3 — escrita da fila e rotinas (risco médio)

- **O `save_storage` da fila refaz a leitura inteira dentro do `BEGIN IMMEDIATE`, e depois a refaz
  de novo** com as barreiras finais. Com a Fase 2, a primeira leitura sai da memória guardada.
- **Shopee a cada 60 s:** `shopee_manual_sync` lê `conf_produtos` e `conf_pedidos` inteiros e monta
  a assinatura de cada linha duas vezes, mesmo sem nada novo. A proposta é comparar primeiro o que
  veio da Shopee com o que veio da última vez e só ler a fila quando algo mudou.
- **Aviso do ML (208 por hora):** cada `ml_import_order` faz leitura e gravação completas da fila. A
  proposta é juntar os avisos do mesmo minuto numa gravação só (a fila de 3 tarefas da V20.18 já
  existe).
- **Ruptura e Conversão:** seis consultas pesadas (uma com auto-junção sobre toda a
  `ml_estoque_dia`) dentro de um `DB_LOCK` só. Os dados são diários, então o resultado pode ser
  guardado pelo dia e pela versão das tabelas, e cada consulta pode soltar a trava entre uma e
  outra.
- **Financeiro:** `full_kit_compositions` lê e abre o `conf_kits` a cada chamada. A proposta é
  guardar pela versão.

## Fase 4 — rotinas e threads (risco maior, ganho menor depois da Fase 0)

- São cerca de 45 workers, alguns de 20 s (`tiktok_estoque_loop`, `anuncios_estoque_loop`). Quem já
  confere a versão antes de trabalhar está bem. Os que não conferem passam a conferir.
- Threads criadas por clique (publicar, Sincronizar, separação, atendimento TikTok): uma fila única
  de tarefas de fundo com 2 a 3 executores, no lugar de uma thread nova por clique.
- Juntar workers num agendador único só vale se a medição da Fase 0 mostrar que as threads ainda
  pesam. Com o limite de arenas, cada thread a mais quase não custa memória.

## Fase 5 — banco e máquina (decisão do dono e do Guilherme)

- **`VACUUM`** fora do expediente: o arquivo tem 1,6 GB e os logs apagados não devolvem espaço. Com
  isso o backup do deploy, o cache do disco e o swap ficam menores.
- **`audit_log` nunca é limpo** (por regra). Uma opção é mover o que tem mais de 12 meses para um
  arquivo à parte, sem apagar. **Decisão do dono.**
- `PRAGMA cache_size`: hoje está no padrão (~2 MB). Só aumentar depois da Fase 0, e medindo, porque
  a memória é o recurso que falta.
- **VPS maior (2 a 4 GB, CPU dedicada) ou tirar o site Next.js do NoromakWeb desta máquina.** É, de
  longe, o que mais resolve: as fases acima reduzem o consumo, mas não criam folga numa máquina de
  1 GB dividida.

## Como medir cada fase

- Antes e depois, com a mesma janela de horário:
  - Painel de saúde: memória do Noromak e CPU;
  - `/api/saude/medicao` (quem segura o banco e por quanto tempo);
  - cortes "Broken pipe" no `journalctl -u expedicao`;
  - tempo do resumo do Financeiro.
- Uma fase por publicação, fora do expediente, agrupada com o que já estiver pronto (seção 0 do CLAUDE.md).

## Ordem recomendada

1. Publicar a V20.46 e ler o painel por 1 dia.
2. Fase 1 inteira (uma versão só): é pequena e quase sem risco, e corta o Financeiro e as gravações da sessão.
3. Fase 2, passo 1 (leitura guardada), com a bateria de testes do ADMIN.
4. Medir de novo e decidir entre a Fase 2, passo 2, a Fase 3 e a VPS maior.
