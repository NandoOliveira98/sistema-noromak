# Remessa Full automática — design (aprovado na conversa de 11/10/2026)

## Objetivo
O sistema monta sozinho a sugestão de remessa para o Full (Araçatuba), cruza com o estoque
do Apollo, avisa quando ela compensa (≥ 150 unidades) e a equipe dispara a procura de datas
pela extensão do Agendador Full.

## Decisões do dono
1. **Cálculo no servidor** (hoje está no navegador, `APP/analise-full.html`). Rotina de manhã
   cedo grava a remessa no banco; a tela Demanda Full passa a mostrar a remessa do servidor.
   Apollo pelo `crm_rate_acquire` (20/min), devagar, de madrugada.
2. **Quantidade por item** = max(0, venda diária × **30 dias** − unidades no Full), venda
   diária = vendas 30 dias ÷ 30; limitada ao disponível no Apollo de Araçatuba (dividido entre
   anúncios do mesmo código, mais urgente primeiro — regra da V17.95). URGENTE (< 7 dias de
   cobertura) entra primeiro.
3. **Contagem**: qualquer kit conta **1** unidade na remessa; no Apollo consome cada componente
   (`/api/full/kits`, regra da V17.96).
4. **Mínimo 150 unidades**. Abaixo disso, não monta nada: espera. Nunca completa à força.
5. **Novos sugeridos** (seção à parte, entram só com confirmação): produto com **≥ 10 vendas
   no ML fora do Full** nos últimos 30 dias, com estoque no Apollo de Araçatuba e sem anúncio
   no Full; quantidade = venda fora do Full em 30 dias, **no máximo 10**.
6. **Não aceito no Full**: botão por linha (remessa e novos), **por SKU/código**, motivo
   opcional; quem tem o módulo admin marca. Tombstone ADMIN: nenhuma rotina tira; só
   "Liberar de novo" do ADMIN. Item marcado nunca entra em remessa nem sugestão.
7. **Aviso** quando a remessa fica "Pronta para agendar": popup + push (mesmo caminho dos
   avisos de venda, mesmos destinatários) e linha no e-mail das 08:00. Sem agendar, lembra
   **uma vez por dia**.
8. **Procurar datas**: clique na tela passa a lista para a extensão do Agendador Full
   (`window.postMessage`, como a extensão Linx Cliente). Agendada, a remessa guarda a data e
   seus itens deixam de contar como necessidade até chegarem ao Full.

## Situações da remessa
`montando` → `pronta` (≥ 150) → `agendada` (data) → `recebida` (itens chegaram ao Full).
Ajuste manual na tela (quantidade, tirar item, confirmar novo) é decisão do ADMIN: a rodada
seguinte não desfaz.

## Fora deste escopo
Bauru (não tem Full), procura de datas sem clique, envio automático ao ML.
