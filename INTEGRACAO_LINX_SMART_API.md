# Linx Smart API — o que existe e o que podemos testar

Levantamento feito em 21/09/2026 no portal de desenvolvedor da Linx
(`https://auto-smartapi.linx.com.br`, logado como a conta da Noromak). Só leitura do
catálogo: nenhuma chamada foi feita às APIs e nenhuma chave foi copiada para cá.

É a API oficial da Linx para o **Linx DMS** (o ERP). Roda na nuvem da Linx — diferente do
Linx DMS local (`10.1.15.1`), que a VPS não alcança. A VPS provavelmente alcança esta; é uma
das primeiras coisas a testar.

---

## 1. Como chamar

| O quê | Valor |
|---|---|
| Endereço base (gateway) | `https://auto-gwsmartapi.linx.com.br/<caminho-da-api>` — ex.: `https://auto-gwsmartapi.linx.com.br/api-seguranca/token` |
| Chave da assinatura | cabeçalho **`Ocp-Apim-Subscription-Key`** (ou `?subscription-key=`). A chave fica em Perfil → Assinaturas no portal. **Nunca** no código nem no Git — vai para o `.env` do servidor. |
| Token do usuário | `POST /api-seguranca/token` (formulário `multipart/form-data`): `grant_type=client_credentials`, `username` (login do Linx DMS, até 15 caracteres), `password` (até 15), `cnpjEmpresa` (opcional; vazio = empresa padrão do usuário). Devolve `access_token`, `token_type`, `expires_in`. |
| Renovar token | `POST /api-seguranca/RefreshToken?token=<token atual>` |
| Envio do token | as demais chamadas mandam o token (provavelmente `Authorization: Bearer <access_token>`) **e** a chave da assinatura. Confirmar no primeiro teste. |
| Empresa / Revenda | quase todo corpo leva `Empresa` e `Revenda` (números) ou `empresaRevenda` (texto) e `codigoUsuario`. Os códigos saem do login (`EfetuarLogin` → `Filiais`). |

**Permissões do usuário do Linx.** Várias consultas exigem funções do usuário que gera o
token: 14 (info gerencial do item), 15 (notas do item), 16 (alocações), 17 (pedidos do item),
71 (custos). O ideal é um **usuário de integração só-leitura** com essas funções.

---

## 2. O que a Noromak tem assinado (dá para testar hoje)

Assinaturas ativas desde 13/08/2026, em nome de *14.346.930/0001-06 — NOROMAK CAMINHÕES E
ÔNIBUS LTDA*:

| Produto | API | Caminho | Operações |
|---|---|---|---|
| S01 Segurança | Segurança | `/api-seguranca` | 5 |
| G03 Gerencial Peças | Gerencial Peças | `/api-gerencial-pecas` | 30 |
| F01 Faturamento | Faturamento | `/api-faturamento` | 6 |
| P02 Peças Balcão Premium | Peças Balcão Premium | `/api-pecas-balcao-premium` | 38 |
| V01 Veículos CRM | Veículos CRM | `/api-veiculos-crm` | 8 |

As outras 14 APIs (seção 6) pedem assinatura **com aprovação da Linx**.

Legenda das tabelas: **L** = só leitura (seguro testar) · **E** = grava algo no Linx
(**não testar sem combinar**) · **E!** = movimenta estoque, emite/importa nota ou mexe em
dinheiro (**nunca testar em produção**).

---

## 3. O que interessa primeiro: Demanda de Compra

Tudo abaixo está em **P02 Peças Balcão Premium** (já assinada) e resolve exatamente o que a
Demanda de Compra provisória (V18.00) não tem:

| Precisamos de | Operação | O que devolve |
|---|---|---|
| Venda de **todos os canais** por mês | `POST /ConsultaPecaGerencial/InfoGerencial/OrdemCompra/Historico/{codigoItem}` (perm. 14) | por mês: `DemandaTotal`, **`DemandaBalcao`, `DemandaOficina`, `DemandaTelemark`**, `OcorrenciaVendas`, `ClassAbc`, `ClassXyz`, `TaxaEsgotamento` |
| **Compra em aberto** (o que está a caminho) | `POST /ConsultaPecaGerencial/DetalhesPedidos` (perm. 17) com `ApenasPendentes: true`, `MostrarTodasFiliais: true` | por pedido: `CodigoPedido`, `QuantidadePedida`, `QuantidadeBaixada`, `SituacaoPedido`, **`DataProvavelRecebimento`**, nota |
| **Marca, fornecedor, embalagem**, custo, estoque | `POST /ConsultaPecaGerencial` (busca por `Marcas`, `NomeMarcaParcial`, `CodigoItemParcial`, `GruposPecas`…) | por item: `Marca`, `NomeMarca`, **`Fornecedor`**, **`QuantidadeEmbalagem`**, **`LoteEconomico`**, `CustoMedio`, `QuantidadeDisponivel`, `ClassificacaoABC/XYZ/ABC123`, `Localizacao`, `ItemEstoqueSubstituto`, preços |
| Transferência entre filiais a caminho | `POST /ConsultaPecaGerencial/Transito/{codigoItem}` | notas de transferência que ainda não entraram na filial |
| Última compra, datas | `POST /ConsultaPecaGerencial/InfoGerencial/Cadastro/{codigoItem}` (perm. 14) e `.../DadosMovimento/{codigoItem}` (perm. 14 e 71) | `DataUltimaCompra`, `QuantidadeEmbalagem`, `ClassificacaoABC`; `DiasSemEntrada`, `DiasSemSaida` |

E em **G03 Gerencial Peças**: `POST /ConsultaGerencial/Pecas/Abc/Atual` (e `/Anterior`),
filtrável por **`marca`** e período (`dataInicio`, `dataFim`), devolve por item `vendas`,
`quantidadeSaida`, `compras`, `estoque`, `giroEstoque`, `class_abc`, `suprimento` — uma
consulta **por marca inteira**, sem precisar chamar item por item.

Com isso a demanda poderia ser: **venda real de todos os canais** + **compra em aberto** +
**marca/fornecedor do próprio ERP** + **múltiplo de embalagem** — sem as regras manuais de
fornecedor e sem o campo "A caminho" digitado.

---

## 4. Operações das APIs assinadas

### S01 Segurança — `/api-seguranca`
| | Operação | Para quê |
|---|---|---|
| L | `POST /token` | gera o token (ver seção 1) |
| L | `POST /RefreshToken?token=` | renova o token |
| L | `POST /Geral/Cadastros/Usuarios/EfetuarLogin` | corpo `login`, `senha`, `CodigoFilial`, `Encrypted`, `dataProcessamento`. Devolve usuário, **`Filiais`** (código, nome, funcionalidades) e o token. Útil para descobrir Empresa/Revenda. Retornos 423 (bloqueado), 500 |
| L | `POST /Geral/Cadastros/Usuarios/EfetuarLoginAutenticacaoFederada` | login federado (`TokenAutenticacaoFederada`) |
| L | `POST /RetornaDataProcessamento` | data de processamento do sistema |

### G03 Gerencial Peças — `/api-gerencial-pecas`
Todas as consultas usam o mesmo corpo base: `codigoUsuario`, `codigoFilial`, `dataInicio`,
`dataFim`, `marca`, `empresaRevenda`. Várias aceitam `?mock=` (provável resposta de teste —
bom para o primeiro contato).

| | Operação | Devolve |
|---|---|---|
| L | `POST /ConsultaGerencial/Pecas/Abc/Atual` | curva ABC do mês por item: `itemEstoquePub`, `class_abc`, `vendas`, `quantidade`, `quantidadeSaida`, `quantidadeEntrada`, `compras`, `estoque`, `giroEstoque`, `suprimento`, transferências |
| L | `POST /ConsultaGerencial/Pecas/Abc/Anterior` | o mesmo para o mês anterior (`vendasMa`, `comprasMa`, `estoqueMA`…) |
| L | `POST /ConsultaGerencial/Pecas/Grupo` | venda/custo/rentabilidade por **grupo de peças**: dia, mês, mês anterior, frete, impostos, `marca` |
| L | `POST /ConsultaGerencial/Pecas/Compras` | compras por grupo: fábrica, rede, loja, outros (dia e mês) |
| L | `POST /ConsultaGerencial/Pecas/Vendedor/ConsultaPecasVendedor` | vendas por vendedor: valor, custo, impostos, rentabilidade, objetivo |
| L | `POST /ConsultaGerencial/Pecas/Vendedor/Analitico` (+`Vendedor`) | notas do vendedor: cliente, valor, custo, frete, impostos, rentabilidade |
| L | `POST /ConsultaGerencial/Pecas/VendedorExterno` e `/Analitico` | o mesmo para vendedor externo |
| L | `POST /ConsultaGerencial/Pecas/AtendimentosAbertos` e `/Analitico` | atendimentos de balcão em aberto (cliente, vendedor, valor; itens com desconto e rentabilidade) |
| L | `.../ObterFiltros` (Abc, Compras, Grupo, Vendedor, VendedorExterno, AtendimentosAbertos) | filtros salvos do usuário (departamentos, transações de devolução, "ignorar vendas internas", opções de custo) |
| L | `.../SelecionarDepartamento`, `SelecionarTransacaoCompra`, `SelecionarTransacaoDevolucao(Venda)`, `Abc/GetSelRegistro` | listas de opções para os filtros |
| E | `.../GravaFiltros` (Abc, Compras, Grupo, Vendedor, VendedorExterno, AtendimentosAbertos) | **grava** os filtros do relatório do usuário no Linx. Baixo risco, mas altera o que o usuário vê na tela dele |

### P02 Peças Balcão Premium — `/api-pecas-balcao-premium`
**Consultas (L):**

| Operação | Devolve |
|---|---|
| `POST /ConsultaPecaGerencial` (`?codigoDepartamento`, `?dataProcessamento`) | busca de itens (ver seção 3). Corpo com `ConfiguracaoBase{Empresa, Revenda, Usuario…}`, `TextoPesquisa`, `CodigoItemParcial`, `CodigoEanGtin`, `DescricaoItemParcial`, `NomeMarcaParcial`, `Marcas`, `GruposPecas`, `ListaFornecedoresDSH`, `SomenteDisponiveis`, `Movimentados`, `ListaCodigoEstoque`… |
| `POST /ConsultaPecaGerencial/InfoGerencial/OrdemCompra/Historico/{codigoItem}` | demanda mensal por canal (seção 3) |
| `POST /ConsultaPecaGerencial/DetalhesPedidos` | pedidos de compra do item (seção 3) |
| `POST /ConsultaPecaGerencial/Transito/{codigoItem}` | transferências a caminho |
| `POST /ConsultaPecaGerencial/InfoGerencial/Cadastro/{codigoItem}` | grupo, embalagem, categoria, IPI, data do cadastro, **data da última compra**, ABC, 123 |
| `POST /ConsultaPecaGerencial/InfoGerencial/Custo/{codigoItem}` (perm. 14 e 71) | dados de custo do item |
| `POST /ConsultaPecaGerencial/InfoGerencial/DadosMovimento/{codigoItem}` | primeira entrada, última saída, dias sem entrada/saída |
| `POST /ConsultaPecaGerencial/InfoGerencial/UltimoMovimento/{tipoMovimento}/{codigoItem}` | últimas movimentações |
| `POST /ConsultaPecaGerencial/DetalhesMovimentacao/Transacoes/{codigoItem}/{ano}/{mes}` (perm. 15) | notas do item no mês (empresa e filiais): tipo, data, quantidade, custo, cliente |
| `POST /ConsultaPecaGerencial/ConsultaAlocacoes` (perm. 16) | itens orçados/alocados em OS, comprometidos em balcão e pedidos Cummins |
| `POST /ConsultaPecaGerencial/ItensSubstituidos/{itemEstoquePublico}` | cadeia de itens substituídos |
| `GET /VendaExternaPecas/Contatos/Agenda/{empresa}/{revenda}/{usuario}` | agenda/providências de contatos |
| `POST /Pecas/AtendimentoBalcao/Atendimento/ListaTransacoesAtendimento`, `ValidaTransacaoDefaultNovoAtendimento` | transações disponíveis/validação |

**Atendimento de balcão, romaneio e pedido (E / E!) — não testar sem combinar:**
`InserirContato`, `InserirItem`, `ComprometerItens`, `DescomprometerUmItem`,
`DescomprometerTodosOsItens`, `PoliticaPreco`, `AlterarCaixa`, `PrecoMarkup`,
`AlteraDepartamento`, `DescontoGlobalPercentual`, `AprovaAtendimento`, `ReprovaAtendimento`,
`Confirmar/SelecionaCliente`, `Confirmar/Validacoes`, `Confirmar/VerificaAtualizacaoIpi`,
`Confirmar/GravarMensagem`, **`InserirOuReenviarItensFrenteCaixaOuExpedicao`**,
**`ContinuaItensFrenteCaixaOuExpedicao`** (fecham o atendimento/nota),
`SelecionaPecas/Prepara`, `GerenciamentoDeEstoque/Compras/ManutencaoPedido/SelecionaPecas/Prepara`,
e o romaneio — `PecRomaneio/GeraListaRomaneio`, `AtualizaListaRomaneio/AtualizaQuantidades`,
`GeraArquivoEtiquetaPDF` e **`ConfirmaRomaneio`** (grava `PEC_ROMANEIO` e **movimenta
estoque** — E!). É o mesmo fluxo que já mapeamos pela extensão do Linx.

### F01 Faturamento — `/api-faturamento`
| | Operação | Para quê |
|---|---|---|
| L | `GET /Faturamento/Cadastros/CondicoesPagamento/ObterListaCondicoesPagamento/{empresa}` | condições de pagamento |
| L | `POST /VendaExternaPecas/ObterNotaFiscalFaturamento` | **baixa o XML da nota** (Empresa, Revenda, Usuario, NumeroNotaFiscal, SerieNotaFiscal, TipoTransacao, EncodingXml) — útil para a NF dos pedidos |
| L | `GET /Veiculos/ConsultasRelatorios/FaturamentoVeiculo?DataEntrada=&DataSaida=` | faturamento de veículos |
| E! | `POST /Faturamento/NotasFiscais/ImportaNotaEntradaXml` | importa NF-e de entrada (XML em Base64), **dá entrada no estoque** e pode contabilizar |
| E! | `POST /NotasDespesas/AdicionarNotaDespesa/CadastrarMovimentoCapa` e `.../CadastrarMovimentoDescLivre` | lança nota de despesa (capa e itens) |

### V01 Veículos CRM — `/api-veiculos-crm`
| | Operação | Para quê |
|---|---|---|
| L | `POST /Geral/ConsultaClientes/ConsultaClientes` | busca cliente (balcão de peças; precisa informar pessoa física/jurídica) |
| L | `POST /Geral/ConsultaClientes/ConsultaClientesPaginado` | lista paginada de clientes (Empresa e Revenda obrigatórias) |
| L | `GET /FluxoAtendimento/ResponderQuestionario/NovaRespostaQuestionario/{empresa}/{revenda}/{questionario}/{cliente}/{contato}` | questionário a responder |
| E | `POST /Geral/ManutencaoClienteSimplificado/CadastrarClienteSimplificado` | **inclui cliente** — é o que o botão Cliente faz hoje pela extensão |
| E | `POST .../AtualizarCadastroClienteSimplificado`, `.../AtualizarCadastroClienteAAR` | atualiza cliente (exige CPF/CNPJ, categoria…) |
| E | `POST/PUT /FluxoAtendimento/ResponderQuestionario/...` | grava respostas de questionário |

---

## 5. Plano de teste sugerido (tudo só-leitura)

1. **Acesso da VPS:** da VPS, `POST /api-seguranca/token` com o usuário de integração.
   Confirma que o gateway responde de lá e como o token volta.
2. **Descobrir Empresa/Revenda:** `EfetuarLogin` → `Filiais`.
3. **Item conhecido (4045):** `ConsultaPecaGerencial` com `CodigoItemParcial: "4045"` —
   conferir `QuantidadeDisponivel` contra o `/estoque` do Apollo (34 em Araçatuba em 18/09),
   e ver `Marca`/`Fornecedor`/`QuantidadeEmbalagem`.
4. **Demanda do 4045:** `OrdemCompra/Historico/4045` — conferir se `DemandaBalcao +
   DemandaOficina + DemandaTelemark` bate com o que se vendeu.
5. **Compra em aberto:** `DetalhesPedidos` com `ApenasPendentes: true` para um item que
   sabidamente tem pedido.
6. **Marca inteira:** `Abc/Atual` com `marca` (ex.: SCHULZ) e o mês — ver quantas peças e
   quanto tempo leva.
7. **Limites:** medir tempo de resposta e se há limite de chamadas (o portal não informa).

Nada da seção "E"/"E!" entra no teste.

---

## 6. APIs sem assinatura (precisam de aprovação da Linx)

| Produto | Caminho | Ops | Principais recursos |
|---|---|---|---|
| E01 E-Commerce | `/api-e-commerce` | 13 | consulta/cadastro de cliente, `ConsultaPecaGerencial`, atendimento (inserir contato/item) |
| E02 E-Commerce Premium | `/api-e-commerce-premium` | 19 | o de cima + condições de pagamento, lista de pagamentos, XML da nota |
| F02 Financeiro | `/api-financeiro` | 19 | títulos a pagar/receber (consultar, incluir), alçadas, consórcio |
| G01 Gerencial Fiscal | `/api-gerencial-fiscal` | 19 | NF emitidas, NF em atraso, quantidade de NF por usuário/transação |
| G02 Gerencial Oficina | `/api-gerencial-oficina` | 37 | OS encerradas, passagens, consultor técnico, mecânico, simulador de custos |
| G04 Gerencial Veículos | `/api-gerencial-veiculos` | 60 | análise de vendas de veículos, propostas, bônus |
| G05 Gestão Administrativa | `/api-gestao-administrativa` | 14 | usuários, grupos, funções, **lista de Empresa/Revenda** |
| O01 Oficina | `/api-oficina` | 10 | check-in, agendas, geração de OS, `ConsultaPecaGerencial` |
| O02 Oficina Premium | `/api-oficina-premium` | 41 | o de cima + OS, solicitações, links de pagamento (Pagar.me/Payhub) |
| — E-Commerce Básico / P01 Peças Balcão | `/api-pecas-balcao` | 11 | atendimento de balcão básico, `ConsultaPecaGerencial` |
| P03 Peças Balcão Premium Plus | `/api-pecas-balcao-premium-plus` | 38 | igual à P02 (mesmas consultas gerenciais) |
| V02 Gestão Show Room | `/api-veiculos-gestao-show-room` | 9 | estoque de veículos, avaliações, propostas |
| V03 Gestão Show Room Premium | `/api-veiculos-gestao-show-room-premium` | 90 | negociação, test drive, propostas, simulação |
| V04 Leads Veículos | `/api-leads-veiculos` | 7 | leads e distribuição de contatos |

Para a Noromak, as que podem valer a pena depois: **F02 Financeiro** (títulos),
**G01 Gerencial Fiscal** (notas emitidas/atrasadas) e **G05 Gestão Administrativa**
(lista Empresa/Revenda). Para peças e compras, **o que já está assinado basta**.

Contato da Linx no portal: automotivo.distribuicao@linx.com.br.
