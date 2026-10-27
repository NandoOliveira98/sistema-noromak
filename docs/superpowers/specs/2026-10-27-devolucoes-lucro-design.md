# Devoluções no lucro (V19.95) — desenho

Decisões do dono, 27/10/2026 (entrevista). A partir de **01/11/2026** as devoluções entram no cálculo do lucro.

## O que é perda

Perda de uma devolução = **custo (Araçatuba) das peças com destino "perda"** + **frete da devolução**.

- Peça boa volta ao estoque: não é perda.
- O valor da venda **não** entra: a venda devolvida com reembolso é cancelada e sai sozinha do faturamento
  (`vendas_v1799` é regravada). Contar de novo seria em dobro.
- Valor desconhecido nunca vira zero: sem custo ou sem frete a perda fica **incompleta** e visível.

## Destino da peça (admin)

Ao tratar no Apollo o admin confirma o destino de cada peça: `estoque`, `perda`, `garantia` (garantia com o fornecedor:
recupera, não é perda) ou `desconto` (venda com desconto: não é perda por ora). Vem preenchido pelo estado do
operador (bom = estoque, ruim = perda). Só `perda` entra no cálculo. Mudança de destino vai para o histórico.

## Custo da peça

Regra do Financeiro (V19.77/V19.79): custo de **Araçatuba**, congelado na venda quando existe; senão o atual de
Araçatuba. Kit: cada peça já está aberta na devolução. Gravado na devolução quando é tratada (fica fixo); antes disso,
calculado na leitura. Sem custo = incompleta; o admin pode digitar o custo da peça.

## Frete da devolução

Mercado Livre: `ml_reclamacao_v1888.frete_devolucao` da reclamação da venda (rotina do Pós-venda, conta principal).
Shopee, Bauru ou sem informação: o admin digita. Digitado vence o do ML. Sem valor = "frete não informado" e a perda
fica incompleta. Zero só quando digitado zero.

## Mês

A perda conta no mês em que a peça **chegou** (`recebido_em`). Enquanto a devolução não foi tratada o valor é
**"a confirmar"**; tratada, **confirmado**. Devoluções antes de 01/11 ficam registradas mas fora do Financeiro.

## Financeiro

Linha nova **Devoluções** (perda do período), descontada do lucro líquido. `cobertura.devolucoes_incompletas` conta
as que estão sem custo ou sem frete; `devolucoes_a_confirmar` mostra quanto ainda não foi tratado.

## Tela do admin

- Cabeçalho padrão, filtro de período (mês) e filial.
- Resumo do mês: devoluções, peças, perda (confirmada + a confirmar), a tratar, taxa de devolução (peças devolvidas ÷
  unidades vendidas no período), cada um comparado ao mês anterior.
- Ranking por peça: devolvidas, vendidas, taxa, perda.
- Tabela com coluna de perda por devolução; gaveta com destino, custo e frete por peça.
