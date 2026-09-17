# Ideia registrada — integrar o Noromak com o Claude

**Registrado em 17/09/2026, na V17.82, a pedido do Luis.**
Ainda **não é decisão**, nem tem prazo. É um lugar para a ideia não se perder.

---

## A ideia

Ligar o Sistema Web Noromak à ferramenta de IA que já está sendo usada para
desenvolvê-lo (Claude), de modo que ela também ajude na **operação do dia a dia**,
e não só na programação.

O que torna isso viável agora: o sistema já **guarda tudo que precisaria ser lido**.
Não falta dado, falta ligação.

| Onde já está o dado | O que dá para responder com ele |
|---|---|
| `audit_log` | quem fez o quê, quando — toda ação já é registrada |
| `sku_change_history` | histórico de troca de SKU por pedido, usuário e tela |
| `order_completion_history` | conclusões, inclusive de pedidos reabertos |
| `ml_event_log` / `shopee_event_log` | erros e respostas das APIs dos marketplaces |
| `completed_orders` / `conf_pedidos` | estado real da fila |
| `print_jobs` / `print_stations` | fila e saúde das estações Zebra |
| `/api/health` | versão, workers e último ciclo de sincronização |

---

## Ideias concretas, do mais simples ao mais ambicioso

1. **Resumo diário da expedição.** Um texto curto toda manhã: quantos pedidos
   entraram por marketplace, quantos ficaram para trás, quais estão sem etiqueta há
   mais de X horas, quais estações Zebra não deram sinal. Hoje isso exige abrir
   várias telas.

2. **Explicar um pedido travado.** "Por que o pedido 2000017699807078 não sai da
   fila?" — a resposta está espalhada entre `conf_pedidos`, `conf_etiquetas`,
   `ml_event_log` e os tombstones. Juntar isso é trabalho de leitura, que a
   ferramenta faz bem.

3. **Central de Pendências assistida.** As divergências entre estoque do Mercado
   Livre e do Apollo já são detectadas; falta dizer **a provável causa** de cada uma.

4. **Diagnóstico de erro.** Quando o `journalctl` enche de erro, hoje alguém precisa
   ler. Podia chegar explicado.

5. **Consulta em linguagem natural, somente leitura.** "Quantos pedidos a Shopee
   cancelou essa semana?" sem precisar de tela nova para cada pergunta.

6. **Apoio ao operador na Conferência.** Explicar por que um bipe foi recusado —
   EAN não cadastrado, quantidade já completa, pedido concluído.

---

## Duas restrições que não podem ser esquecidas

### 1. A regra do ADMIN continua valendo, inteira

Qualquer integração entra **abaixo** de tudo na ordem de prioridade do sistema.
Concretamente:

- A IA **nunca grava direto** no banco. Nem em `storage`, nem em `conf_pedidos`,
  nem em tombstone, nem em override.
- Leitura é o caminho padrão. Se algum dia precisar escrever, é pelas **rotas que já
  existem**, com `actor`, `actor_role` e registro em `audit_log` — exatamente como
  qualquer outro cliente.
- Sugestão não é execução. O sistema propõe; **a pessoa decide e age**.

Isso não é formalidade. Foi exatamente "uma automação escreveu por cima do que o
ADMIN decidiu" que gerou as versões V17.44 até V17.82.

### 2. O servidor não tem dependência externa

`servidor_web.py` usa **só a biblioteca padrão do Python**. É uma das melhores
qualidades deste projeto: instala em qualquer lugar, não quebra por atualização de
pacote, não tem superfície de ataque de terceiros.

Integrar exige escolher entre três caminhos, e **essa escolha ainda não foi feita**:

- **(a) Processo separado.** Um serviço à parte lê o banco em modo somente leitura e
  fala com a API. O `servidor_web.py` não muda uma linha e continua sem dependência.
  *É a opção que eu recomendaria começar avaliando.*
- **(b) `urllib` na mão.** Chamar a API por HTTP cru, com a stdlib. Mantém a regra,
  mas significa manter na mão o que o SDK oficial já resolve.
- **(c) Adicionar o pacote `anthropic`.** Mais simples de escrever, mas quebra a
  regra de "zero dependência" do servidor.

Dados de referência para quando essa conversa acontecer: o modelo atual é
`claude-opus-5`, com janela de contexto de 1M tokens, a US$ 5,00 por milhão de
tokens de entrada e US$ 25,00 por milhão de saída. Para um resumo diário, o custo é
desprezível; para consulta livre em cima de log, precisa de medição antes.

---

## Próximo passo, quando houver interesse

Começar pela ideia 1 (resumo diário), pelo caminho (a), em **somente leitura**.
É a de menor risco: não toca no servidor, não escreve nada, e dá para medir o custo
real antes de decidir qualquer coisa maior.

Antes de escrever código, decidir:

- Onde a chave da API fica guardada (no servidor, o lugar é `/home/expedicao/.env`,
  que nunca entra no Git).
- Se o dado do pedido — que inclui **nome e endereço de cliente** — pode sair do
  servidor. Essa é uma decisão do dono do negócio, não técnica.

Enquanto nada disso for decidido, este arquivo é só um lembrete.
