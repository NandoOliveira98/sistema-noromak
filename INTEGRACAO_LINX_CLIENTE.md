# Linx DMS — atualizar cadastro de cliente

Mapeado em 17/09/2026 a partir de uma captura de rede de uma alteração real.
Base: `http://10.1.15.1:81/LinxDMS` — **rede local do escritório**.

> A VPS de produção **não alcança** esse endereço (testado: HTTP 000). Qualquer
> automação precisa rodar num PC do escritório, no mesmo padrão do agente Zebra.

---

## Autenticação

Todas as chamadas levam os mesmos cabeçalhos:

```
Authorization: bearer <JWT>
CodUser: 210
NomeAplicativo: LinxDMSPrincipal
IdSeq: 0
Monitor: false
Accept: application/json, text/plain, */*
Content-Type: application/json      (nos POST com corpo)
```

O JWT carrega `CodigoUsuario`, `Empresa`, `Revenda`, `IdSessaoAtiva` e os papéis.
**Validade observada: 15 minutos** (`exp - iat` = 900 s). Um agente precisa
renovar o token sozinho — ver "Pendências" no fim.

Nunca gravar token em arquivo, log ou repositório.

---

## O fluxo, em 5 chamadas

### 1. Achar o cliente pelo CPF/CNPJ

```
POST /Faturamento/Cadastros/MntCliente/ProcurarCliente
{"Empresa":1,"Revenda":1,"CnpjCpf":"46414894168", ...demais campos null ou "N"}
```

Devolve o `Cliente` (código interno). No exemplo: **81510**.

### 2. Ler o cadastro atual

```
GET  /Faturamento/Cadastros/MntCliente/DetalhesCliente/{cliente}
POST /Faturamento/Cadastros/MntCliente/DetalhesClienteCredito
     {"Empresa":1,"Revenda":1,"Cliente":81510}
POST /Faturamento/Cadastros/MntCliente/InicializaTelaFatCliente
     {"Empresa":1,"Revenda":1,"Usuario":"210","Cliente":81510}
```

### 3. Gravar

```
POST /Faturamento/Cadastros/MntCliente/AlteraRegistro
{
  "IdentificadorRegistro": {"Cliente": 81510},
  "NovosDadosRegistro": {
    "Empresa": 1, "Revenda": 1, "Usuario": "210", "ClienteFabricante": true,
    "Registro":      { ...o registro INTEIRO... },
    "PessoaFisica":  { ...ou null... },
    "PessoaJuridica": null
  }
}
```

> **Atenção, e isto é o mais importante deste documento:** `AlteraRegistro` recebe
> o **registro inteiro**, não um patch. Campo que não for enviado é apagado.
> O agente tem de ler o cadastro no passo 2, alterar **somente** os campos da
> regra, e devolver todo o resto exatamente como veio.

---

## Os campos da regra

| Regra combinada | Campo no payload | Valor |
|---|---|---|
| **Nome do cliente** | `Registro.Nome` | **o nome de "Dados para a NF-e"** |
| Telefone padrão | `DDDTelefone` / `Telefone` | `"11"` / `"111111111"` |
| Celular padrão | `DDDCelular` / `Celular` | `"11"` / `"111111111"` |
| E-mail padrão | `EMailCasa` / `EMailTrabalho` | `lojavirtual@noromak.com.br` |
| Cadastro **(421) = Aprovado** | `SituacaoCredito` | `"1"` |
| **Crédito normal** | `BloqueioCredito` | `"1"` |
| Validade = data de hoje | `DataLimiteCredito` | `"AAAA-MM-DDT00:00:00"` |
| Endereço sem acento | `LogradouroEntrega`, `BairroEntrega`, `MunicipioEntrega` e os `*Cobranca` equivalentes | texto sem acentuação |

### Tabelas de domínio (confirmadas na API)

`GET /Faturamento/Cadastros/MntCliente/SituacaoCredito`
```
1 = Aprovado        0 = Não Aprovado
```

`GET /Faturamento/Cadastros/MntCliente/BloqueioCredito`
```
0 = Sem bloqueio    1 = Crédito normal    2 = Consultar antes
3 = Crédito suspenso  4 = Advogado        5 = Venda suspensa
```

`GET /Faturamento/Cadastros/MntCliente/Conceito`
```
0 = Sem conceito  1 = Ótimo  2 = Bom  3 = Regular  4 = Ruim
5 = Péssimo       6 = Faturamento suspenso
```

Isto resolve a dúvida das "funções 420/421/430": na tela os rótulos são
**Cadastro (421)** e **Situação de Crédito (420)**; na API os campos são
`SituacaoCredito` e `BloqueioCredito`, nessa ordem.

---

## O nome tem de ser o da nota

O `Registro.Nome` recebe **obrigatoriamente** o nome que o marketplace mostra em
**"Dados para a NF-e"** — nunca o apelido da conta, nunca o destinatário da
entrega. Na tela do Mercado Livre é a linha que traz nome e CPF juntos:

```
Dados do comprador
Gabriel Arruda de Oliveira - CPF 08186845771
```

O sistema já separa isso corretamente: `_ml_billing_details()` lê só os Dados
para NF-e e guarda em `NomeFiscalMercadoLivre`; a Shopee usa `NomeFiscalShopee`.
O campo `Cliente` **não serve** — ele cai para o nome do comprador da conta
quando o fiscal ainda não chegou, e gravar isso no ERP geraria nota no nome
errado.

No servidor, quem lê é `nome_fiscal_do_pedido(row)`, que devolve vazio de
propósito quando não há nome fiscal, em vez de inventar um.

## Como reconhecer um cliente de marketplace

O registro do exemplo trazia:

```json
"ParecerCredito": "Origem via Marketplace MERCADOLIBRE"
```

Esse é o marcador que separa o cliente vindo de marketplace do cliente de balcão.
**Importante para não destruir dado real:** aplicar a padronização de telefone e
e-mail apenas onde esse marcador existir, para não apagar o contato verdadeiro de
uma oficina que também compra presencialmente.

---

## Outros endpoints úteis mapeados

| Para quê | Rota |
|---|---|
| Buscar endereço pelo CEP | `GET /Faturamento/CEP/GetCEP/{cep}` |
| Lista de UF | `POST /Faturamento/Cadastros/MntCliente/SelRegistroUF` |
| Tipo de via | `POST /Faturamento/Cadastros/MntCliente/SelRegistroTipoVia` |
| Categoria do cliente | `POST /Faturamento/Cadastros/MntCliente/SelRegistroCategoriaClientePorUsuariosLiberados` |
| Permissão de alterar e-mail | `POST /Faturamento/Cadastros/MntCliente/PermissaoAlterarEmail?usuario=210` |

---

## Decisões do dono (17/09/2026)

1. **Autenticação: a sessão do próprio Luis, no navegador dele.** Sem usuário de
   serviço e sem agente headless. Portanto a automação é uma **extensão Chrome**:
   o navegador já está na rede do escritório e já tem a sessão do Linx ativa.
   O token não é lido do storage — a extensão observa o cabeçalho `Authorization`
   que o próprio Linx envia e reaproveita. Assim ela acompanha de graça a
   renovação de 15 em 15 minutos.
2. **E-mail: sempre `lojavirtual@noromak.com.br`** em `EMailCasa` e `EMailTrabalho`.
3. **Alcance: todo pedido em que o Luis clicar para atualizar.** Não filtra por
   `ParecerCredito`. A decisão é do clique, não de uma regra automática — o que
   mantém a lógica do ADMIN soberano: nada acontece sem ação explícita.

## Pendência técnica conhecida

**O Mercado Livre não guarda o CPF/CNPJ do comprador hoje.**

- Shopee: já grava `CPFShopee` e `CNPJShopee` na linha do pedido.
- Mercado Livre: grava só `NomeFiscalMercadoLivre`.

Mas o dado **já é buscado**: `_ml_billing_details()` lê
`buyer.billing_info.identification` da API do ML para decidir o nome, usa o
`type` (CPF/CNPJ) e descarta o `number`. Basta passar a guardar o número na linha
do pedido — mudança pequena, em código que já funciona.

Sem isso, o `ProcurarCliente` não tem por onde buscar o cliente de venda ML.

> **Resolvido na V17.84:** o ML passou a guardar `CPFMercadoLivre`/`CNPJMercadoLivre`.
> Pedidos anteriores são relidos na API do ML pela rota `/api/cliente-fiscal` (V17.85).

## O que a primeira gravação real ensinou (18/09/2026)

Extensão v0.2 → v0.4. Clientes 81528, 81263 e 81282.

- **Formato da leitura** (`DetalhesCliente/{id}`): `{DadosCliente, DadosPessoaFisica,
  DadosPessoaJuridica}`. `DadosCliente` tem 316 campos, com grafia `DddTelefone`.
  `DadosPessoaFisica.Cpf` vem **numérico** (perde zero à esquerda).
- **Formato da gravação da tela**: 202 campos no `Registro`, grafia `DDDTelefone`, e 46
  na `PessoaFisica`, com `Cpf` em texto. `Bairro` = `BairroEntrega`; `Uf`, `Cep`,
  `Tipovia` e `Complemento` vão nulos. A lista exata está em `linx-formato.js`.
- **O Linx ESVAZIA todo campo que não vem no `AlteraRegistro`.** A própria tela não envia
  119 campos. Na primeira gravação, o cliente 81528 perdeu 21 valores (`NumeroEntrega`,
  `NumeroCobranca`, `TranspAereo`, `ContatoSegunda`, `Cadeirante`...). A partir da v0.3, a
  extensão manda também todo campo do modelo de `InicializaTelaFatCliente.DadosCliente.
  Registro` que tem valor no cadastro atual, com esse mesmo valor (exceto `Usuario` e
  `DtaUltimaAlteracao`).
- **Nome** vai em maiúsculo, como o Linx guarda; acento no nome fica.
- **CNPJ** está bloqueado: ainda não há captura da tela gravando pessoa jurídica.
- Depois de gravar, a extensão relê o cadastro e confere campo a campo.

## Mercado Livre não entrega os Dados para a NF-e (18/09/2026)

`/orders/billing-info/MLB/{id}` e `/orders/{id}/billing_info` respondem **HTTP 403
PolicyAgent** ("At least one policy returned UNAUTHORIZED") para o app do Noromak, com
qualquer header. Resultado: 0 de 55 pedidos ML tinham nome/CPF da nota; a captura da
V17.84 nunca funcionou. Correção de raiz: permissão do app no DevCenter do ML.

Contorno (V17.86 + extensão 0.6): a extensão abre `/vendas/{id}/detalhe` numa janela
minimizada, com a sessão do usuário, lê o texto e devolve ao Noromak, que guarda em
`cliente_fiscal`. Travas: só aceita a página cujo endereço é o da venda pedida; nome
precisa de duas palavras e não pode ser texto de tela; o servidor recusa nome inválido.
