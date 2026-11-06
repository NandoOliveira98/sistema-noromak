# TikTok Shop (fases 3 e 4 + cancelamento) e Amazon — desenho

Data: 06/11/2026. Entrevista com o dono (grill-me) depois de ler, só leitura, as integrações
TikTok Shop e Amazon do Bling (conta de outra empresa, usada apenas para ver as telas).

## O que o Bling faz (referência)

**TikTok Shop** — conecta por Shop ID + token (o Bling é parceiro/ISV no TikTok Partner Center).
Importa pedidos `AWAITING_SHIPMENT` e `AWAITING_COLLECTION` como "Em aberto"; envia o estoque do
depósito escolhido; envio pela plataforma (rastreio gerado pelo TikTok); NF-e automática opcional
("emitir ao disponibilizar para envio", "enviar dados fiscais ao emitir NF"); natureza e preço
próprios para amostras/presentes; taxa do marketplace em %.

**Amazon** — conecta pelo ID do vendedor (SP-API). Importa pedidos (status: Pendente, Pendente
disponibilidade, Enviado – Aguardando NF-e, Pronto para envio, Enviado, Parcialmente enviado,
Incumplível, Cancelado); importa as taxas da Amazon sozinho; envia estoque; NF-e só quando a
etiqueta **DBA** estiver disponível; liga cada forma de pagamento (CreditCard, Installments,
Rewards, Boleto, Pix, Other).

## Decisões do dono (06/11)

1. **Nada passa pelo Bling.** O Noromak liga direto no TikTok e na Amazon.
2. **TikTok pelo cadastro de parceiro** no TikTok Partner Center (o mesmo caminho do Bling), em vez
   de esperar o gerente de contas. O código da V19.63/64 já usa o login de parceiro
   (`services.tiktokshop.com/open/authorize?service_id=`): quando o app for aprovado, trocar
   `TIKTOK_APP_KEY/SECRET/SERVICE_ID` no `.env` e conectar. Até lá o estoque segue pela extensão (V19.81).
3. **TikTok — o que falta:** etiqueta (fase 3), vendas (fase 4) e cancelamento. Amostras/presentes
   ficam fora.
4. **Construir agora**, pela documentação, tudo desligado e com a 1ª resposta real de cada chamada em
   `tiktok_amostra_v1963` para conferir ao ligar.
5. **Etiqueta: automático + PDF.** O servidor organiza o envio do pacote quando o pedido entra e baixa
   a etiqueta em PDF; a Conferência de Bauru abre a impressão pelo mesmo caminho do PDF do ML (V18.95).
6. **NF-e: esperar.** No Brasil o TikTok só libera a etiqueta depois de receber a NF-e. A nota sai pelo
   Linx e o Noromak não tem o XML: a coluna mostra **AGUARDANDO NF** (como a Magalu) e, quando o TikTok
   aceitar a nota (enviada pela equipe no Seller Center), o servidor baixa a etiqueta sozinho.
7. **Vendas:** entram nas vendas de Bauru (`vendas_conta_v1898`, `marketplace='tiktok'`,
   `conta='bauru'`), aparecem no Dashboard com o filtro Bauru. Tarifa = **acerto real** do TikTok (o que
   caiu na conta, como o escrow da Shopee); sem acerto = em branco, nunca estimada. O Financeiro continua
   só Araçatuba; Financeiro por filial fica para depois.
8. **Cancelamento:** pedido cancelado no TikTok sai da fila de Bauru, com a mesma regra do ML: a linha
   da integração sai, a linha protegida pelo ADMIN fica; concluído não volta.
9. **Amazon só no papel** até a conta existir (a Noromak ainda não vende na Amazon). Envio pretendido:
   **DBA** (a Amazon coleta, etiqueta da Amazon). Código só depois de abrir a conta e o cadastro de
   desenvolvedor na SP-API.

## TikTok — chamadas a confirmar na 1ª resposta real

Fontes de terceiros, ainda não conferidas na documentação oficial do Partner Center:

| Para quê | Chamada provável |
|---|---|
| Pedidos | `POST /order/202309/orders/search` (já usada na V19.64) |
| Organizar envio | `POST /fulfillment/202309/packages/{package_id}/ship` |
| Etiqueta | `GET /fulfillment/202309/packages/{package_id}/shipping_documents?document_type=SHIPPING_LABEL` (PDF; ZPL só BR) — há referência também a `/logistics/202309/orders/{order_id}/shipping_documents` |
| Cancelamento | `POST /return_refund/202309/cancellations/search` + status do pedido `CANCELLED` |
| Acerto | `GET /finance/202309/statements/{id}/statement_transactions` ou `/finance/202501/orders/{order_id}/statement_transactions` |
| NF-e (Brasil) | sem endpoint de envio confirmado; o Noromak só lê se a nota foi aceita |

## Amazon — fases (quando a conta abrir)

0. Cadastro de desenvolvedor na SP-API (app privado do próprio vendedor), LWA OAuth, conexão em
   Integrações, amostra da 1ª resposta. 1. Pedidos na fila (filial a decidir; chave = AmazonOrderId,
   kit pela regra da Shopee V18.71, cancelado sai). 2. Etiqueta DBA (aguardando NF, como a Magalu).
   3. Vendas e taxas (Finances API). 4. Estoque (decisão à parte: exceção à regra "o Noromak nunca grava
   estoque", como TikTok e anúncios ML). Pagamentos e Pix ficam com o Linx.

## Fora deste desenho

Amostras/presentes do TikTok; Financeiro por filial; envio da NF-e ao TikTok pelo Noromak (exigiria
uma porta do NoromakWeb com o XML do Linx).
