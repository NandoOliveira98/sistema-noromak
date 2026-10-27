# Devoluções (V19.88) — desenho

Decisões do dono em 27/10 (entrevista). O objetivo é registrar o lado **físico** da devolução: a peça
chegou, bate com a venda, está boa ou ruim. Depois disso, o admin trata a devolução no Apollo.

## Fluxo

**Operador** — tela cheia `/devolucoes` (`APP/devolucoes.html`), módulo `conferencia`, sem menu lateral
(como a Conferência). O `alert()` fica, porque trava o leitor de código de barras de propósito.

1. Bipa o EAN/SKU da peça e/ou digita o nome do cliente (ou o nº do pedido, ou bipa a etiqueta de
   devolução do ML). Aparece a lista dos pedidos **concluídos** dos últimos 180 dias que batem
   (todos os marketplaces), filtrada pela filial de quem está usando.
2. Escolhe o pedido e a tela abre a **venda**: marketplace, nº, data, cliente e itens com quantidade.
   O kit aparece em peças (`_separacao_partes`). A nota fiscal não é usada: a venda tem os mesmos dados
   para conferir.
3. Bipa cada peça que voltou (+1 na linha) ou digita a quantidade. Uma peça que não está na venda entra
   como "não bate com a venda". O que não for bipado fica como "não voltou".
4. Para cada peça que voltou: **bom** ou **ruim**. Ruim exige o motivo (`DEVOLUCAO_MOTIVOS`: avariado,
   usado, faltando peça/acessório, embalagem violada, veio outra peça). A observação é opcional.
5. Conclui e a devolução fica "a tratar" para o admin.

**Pedido não encontrado** (venda antiga, balcão): o operador registra "sem pedido" com SKU + cliente e
segue igual. A devolução chega ao admin marcada com esse aviso.

**Admin** — `/devolucoes-admin` (`APP/devolucoes-admin.html`), menu Expedição, módulo `admin`.
Lista com os filtros a tratar / tratadas, bom / ruim e marketplace. "Tratada" exige o **nº do documento
do Apollo**; ficam registrados quem marcou e quando. O admin pode **corrigir** a quantidade, o estado e
o motivo: a correção vai para o histórico (`devolucao_evento_v1988`) com o valor anterior, sem apagar o
original. A devolução tratada **só reabre pelo admin**. Edições mandam `versao`: se outra mais nova já
foi gravada, o servidor responde 409.

## Regras

- Registrar a devolução **nunca** mexe em `completed_orders`, `conf_pedidos` nem nas exclusões: o pedido
  não volta para a Conferência (regra do pedido concluído).
- O Noromak não grava nada no Apollo nem no marketplace. É só registro.
- O mesmo pedido pode ter várias devoluções. A soma das quantidades que voltaram de cada peça nunca
  passa da quantidade vendida; isso é conferido sob o `DB_LOCK` na hora de gravar, então dois operadores
  ao mesmo tempo não ultrapassam o vendido.
- Filial = a do pedido (`Casa` da linha concluída). O operador vê e registra só a filial dele; quem tem
  "todas" escolhe no seletor (`X-Noromak-Filial`). Tentar registrar devolução de pedido de outra filial
  responde 403.

## Busca sem pesar

Varrer o `payload` de `order_completion_history` a cada bip seria lento e seguraria o `DB_LOCK`, o mesmo
do bip. Por isso existe o índice `devolucao_busca_v1988` (uma linha por peça de pedido concluído:
pedido, filial, marketplace, SKU, código de barras, cliente, nº da venda/envio do ML, data), preenchido
aos poucos por `devolucao_indexar()`: lotes de 200 ids novos do histórico (`id > último`, só os últimos
180 dias), com o JSON lido fora do lock. Ele roda no começo de cada busca e no worker
`devolucoes_indice_loop` (5 min). O pedido reconcluído é reindexado: as linhas antigas daquele pedido
saem.

O EAN bipado também passa pelo cadastro de produtos (`conf_produtos`): código de barras → SKU. Assim, uma
peça cujo EAN não estava na linha do pedido ainda é encontrada pelo SKU.

## Etiqueta de devolução do ML

A rotina do Pós-venda já lê `/post-purchase/v2/claims/{id}/returns`. Os envios de volta
(`shipments[]` com `type=return`: `shipment_id` e o rastreio, quando vem) passam a ser guardados em
`ml_reclamacao_v1888.devolucao_envios`, **sem chamada a mais**. Bipar esse código leva ao `order_id` e
daí ao pedido concluído (`VendaMercadoLivre` / `Pedido` / `PackIdMercadoLivre`). **A confirmar com a
1ª etiqueta real** qual número o código de barras traz. As reclamações antigas só ganham o envio quando a
rotina as detalhar de novo. O Pós-venda roda só na conta principal (Araçatuba). Shopee: depois de
conferir uma resposta real da API de devoluções.

## Tabelas

- `devolucao_v1988`: id, filial, marketplace, pedido, order_key, cliente, sem_pedido, situacao
  (`a_tratar`/`tratada`), recebido_por/em, obs, documento_apollo, tratada_por/em, versao.
- `devolucao_item_v1988`: devolucao_id, sku, descricao, ean, qtd_vendida, qtd, estado (`bom`/`ruim`),
  motivo, obs, fora_da_venda.
- `devolucao_evento_v1988`: histórico (registrada, corrigida, tratada, reaberta) com o detalhe.
- `devolucao_busca_v1988` + `devolucao_indice_v1988` (último id indexado).

## Rotas

| Rota | Quem |
|---|---|
| `GET /api/devolucoes/buscar?q=` | módulo conferencia ou admin |
| `GET /api/devolucoes/pedido?chave=` | módulo conferencia ou admin |
| `POST /api/devolucoes/registrar` | módulo conferencia ou admin (o Consulta é barrado pela trava geral) |
| `GET /api/devolucoes?situacao=&estado=&marketplace=` | módulo admin |
| `POST /api/devolucoes/{tratar,corrigir,reabrir}` | módulo admin |

## Fica para depois

Foto da peça ruim, destino por peça (estoque / perda / garantia), etiqueta de devolução da Shopee,
contestação automática no marketplace.
