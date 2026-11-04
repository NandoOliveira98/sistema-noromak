# Estúdio de Imagens — desenho (rascunho 1)

Decisões do dono, 04/11/2026 (entrevista). Nada construído ainda.

## Objetivo

Uma tela do Noromak onde o operador sobe a foto do produto e recebe, pronta para baixar:

- a **foto principal** (1200×1200, fundo branco puro, produto inteiro, ~85% do lado maior);
- as **artes secundárias**: benefícios, modo de uso, kit juntos e aplicação.

É o fluxo das skills `/melhorar`, `/beneficios`, `/modo`, `/juntos` e `/aplicacao`, testado em
04/11 com o Hidratante de Couro Würth, rodando sem o Claude Code.

**Fora do escopo:** guardar acervo de fotos, enviar foto ao ML/Shopee e criar o que não está na
foto (cenário, embalagem nova). O produto nunca é alterado: cor, rótulo e formato ficam como
estão.

## Decisões

| # | Decisão |
|---|---|
| 1 | O agente roda **no PC do dono** (Windows, com placa de vídeo; o Real-ESRGAN já funciona lá). PC desligado = a fila espera e a tela mostra "agente offline". |
| 2 | O SKU é só **etiqueta** (nome do arquivo, busca na lista). Nada fica guardado de vez. |
| 3 | **O operador apaga** depois de usar (botão por foto e "Apagar selecionadas"). Nada é apagado sozinho; a tela mostra o espaço usado e fica amarela acima de 500 MB. |
| 4 | Módulo próprio **"Imagens"** (`app_users.can_imagens`), ligado por usuário. Ao criar a coluna: perfil Admin ligado, demais desligados. Consulta só vê; Compras não tem. |
| 5 | A conferência usa a **API do Claude**, com a conta e o saldo do dono. A chave fica **só no PC do agente**, num arquivo que o dono preenche. Nunca vai ao servidor, à tela ou ao chat. Limite de gasto mensal no Console. |
| 6 | Modelo inicial: **Claude Haiku 5.5** (o mais barato). Seletor na tela (só perfil Admin) para trocar pelo Opus 5.5 se ele deixar passar erro. |
| 7 | Primeira versão: **foto principal e as 4 artes secundárias**. |
| 8 | Textos das artes: **o Claude propõe, o operador aprova.** Ele lê o rótulo (frente e verso, se houver); o que não está no rótulo vem em branco, nunca inventado. A arte só é gerada depois do "Gerar arte". |

## Arquitetura

O servidor não faz trabalho pesado: a VPS tem 1 GB de memória dividida com o CRM, não tem placa
de vídeo, e o `servidor_web.py` é só biblioteca padrão. O modelo é o mesmo do agente da Zebra:
**fila no servidor e agente que busca trabalho.**

```
Tela /imagens                   Servidor (VPS)                       Agente (PC do dono)
 envia fotos + SKU + tipo ──►  grava arquivos em disco        ◄──  GET próximo trabalho (10 s)
                               imagem_trabalho_v2017                 baixa as originais
                                                                     Claude: analisa / propõe textos
 edita e aprova textos   ◄──►  textos_para_aprovar           ◄──   devolve análise + textos
                               aguardando_arte               ◄──   GET próximo trabalho
                                                                     amplia (Real-ESRGAN) se < 1000 px
                                                                     monta 1200×1200 / renderiza HTML
                                                                     Claude: confere textos (zoom)
                                                                     corrige trecho deformado
 antes × depois, baixar ◄──    pronta / revisar / outra_foto ◄──   envia resultado + recortes + relatório
 apagar                  ──►   apaga a pasta e marca apagado
```

### Servidor (`servidor_web.py`)

- **Arquivos** em `/home/expedicao/dados/imagens/<id>/` (`original_N.*`, `resultado.jpg`,
  `zoom_N.jpg`), **nunca no banco**. Até 10 MB por foto, até 20 fotos por envio; jpg, png e webp.
  O tipo é conferido pelo conteúdo, não pela extensão.
- **Tabela `imagem_trabalho_v2017`:** id, sku, tipo (`principal`, `beneficios`, `modo`, `juntos`,
  `aplicacao`), situação, filial, quem enviou, criado_em, textos_json (proposta e aprovado),
  relatorio_json (o que o Claude achou), modelo, tokens, bytes, `reservado_ate` (vez do agente),
  versao, apagado_em. Sem imagem dentro.
- **Situações:** `aguardando` → `analisando` → (artes) `textos_para_aprovar` → `aguardando_arte` →
  `gerando` → `pronta` | `revisar` | `outra_foto` (a foto não serve: cena, fundo colorido, tremida,
  com o motivo) | `erro`. `apagado` só tira a pasta; a linha fica como histórico.
- **Vez do agente:** o `próximo` reserva o trabalho por 10 min (`reservado_ate`); reserva vencida
  volta para a fila. Um trabalho por vez.
- **Token do agente** em `imagem_agente_token_v2017`, igual ao da Zebra (`print_agent_auth`), e
  incluído em `STORAGE_SEGREDOS`. Trocar o token exige OWNER.
- **Rotas da tela** (módulo `imagens`): `GET /api/imagens`, `POST /api/imagens/{enviar,textos,
  refazer,apagar}`, `GET /api/imagens/arquivo?id=&nome=` (download), `POST /api/imagens/config`
  (modelo, só perfil Admin).
- **Rotas do agente** (token): `GET /api/imagens/agente/proximo`, `GET /api/imagens/agente/arquivo`,
  `POST /api/imagens/agente/{analise,resultado,ping}`.
- **Painel de saúde:** "Agente de imagens" com o último ping; atenção se há trabalho na fila e o
  agente está sem ping há > 2 min. Sem e-mail (o PC desligado à noite é normal).
- **Edição com `versao`:** aprovar textos numa versão antiga = 409 `CONFLITO`.

### Agente (fora do servidor, no PC)

Pasta `FERRAMENTAS_WINDOWS/agente_imagens/` (código no repo; chave e config **fora** do Git).

- Python com `anthropic`, `Pillow`, o Real-ESRGAN (`realesrgan-ncnn-vulkan.exe`) e o Chrome/Edge
  sem janela para as artes HTML (os `templates/` da skill `imagem-anuncio` vão junto).
- `config.env`: `ANTHROPIC_API_KEY` (o dono cola), `NOROMAK_URL`, `NOROMAK_AGENTE_TOKEN`,
  `PASTA_TRABALHO`.
- Sobe com o Windows (atalho na pasta Inicializar) e grava um log com uma linha por trabalho
  (tokens, custo estimado e resultado).

### Chamadas ao Claude (Haiku 5.5, saída em JSON com esquema fixo)

1. **Análise** (fotos originais, ~1.600 tokens por foto): o produto está em fundo branco liso? é
   claro (recorte proibido)? tem cena ou fundo colorido (`outra_foto`)? tamanho? Para as artes,
   também propõe os textos, só com o que está legível no rótulo, e marca cada campo como "lido no
   rótulo" ou "não encontrado".
2. **Conferência** (original × resultado, com recortes em zoom de cada texto): para cada recorte,
   OK ou deformado, com a região. Deformado → o agente refaz o trecho pela ampliação comum
   (Lanczos + UnsharpMask, máscara esfumada) e confere de novo, no máximo 2 voltas; depois disso
   `revisar`.

Custo estimado (não medido): frações de centavo de dólar por foto no Haiku. O log do agente mede
o custo real.

### Fluxos

- **Principal:** análise → ampliar se < 1000 px → produto claro em fundo branco: corte pelo que não
  é branco, sem recorte; os outros: `preparar.py` → 1200×1200 → conferência → `pronta`/`revisar`.
- **Artes:** análise + textos → **operador aprova na tela** → agente recorta o produto, preenche o
  template HTML com os textos aprovados, renderiza → conferência → `pronta`/`revisar`.
- **Kit juntos:** várias fotos num trabalho só (uma por item). Decidido na construção: sai **sem texto**, itens lado
  a lado em fundo branco (serve de foto principal do kit no ML); segue o caminho da foto principal, sem aprovação de textos.
- **Modo de uso / aplicação:** a foto do verso ajuda; sem ela, o operador digita os passos.

## Tela `/imagens`

`APP/imagens.html`, menu **Vendas e anúncios → Imagens**, peças padrão (`nk-ui`, modo escuro).
Entra no `MENU` do `nk-menu.js`, no `web-auth.js`, no `shared-storage.js` e no `sw.js`
(`test_rotas_v1882`).

- **Enviar:** arrastar fotos, SKU (busca no Precificador; opcional), tipo, "foto do verso" opcional.
- **Lista:** miniatura, SKU, tipo, situação, quem enviou, data. Filtro por situação. Espaço usado.
- **Textos para aprovar:** campos editáveis com a marca "lido no rótulo" / "não encontrado".
- **Gaveta antes × depois:** sempre mostra os recortes em zoom de cada texto, mesmo com OK do
  Claude, porque o Haiku pode deixar passar. Botões Baixar, Refazer e Apagar.
- **Selo "agente offline"** quando não há ping há > 2 min.

## Testes (`TESTES/test_imagens_v2017.py`)

Envio grava em disco e não no banco; tipo falso recusado; limite de tamanho; módulo `imagens`
obrigatório (Consulta só lê, Compras não); token do agente obrigatório e fora do `GET
/api/storage`; reserva única e vencida volta à fila; textos só viram arte depois de aprovados;
versão antiga = 409; apagar remove a pasta e mantém a linha; espaço usado; saúde do agente;
rota nova nas quatro listas.

## Em aberto

- Nome final da tela ("Imagens" ou "Estúdio de Imagens").
- Medir se o Haiku acha letra deformada; se não achar, trocar para o Opus no seletor.
- Fundo de cor da arte: a cor do rótulo (regra da skill) ou a cor da Noromak.
