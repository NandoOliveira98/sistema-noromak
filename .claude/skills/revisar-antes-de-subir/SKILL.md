---
name: revisar-antes-de-subir
description: Revisão de código com 4 revisores especializados sobre o que vai subir para produção (diferença entre a versão no ar e o HEAD). Use antes de pedir o "pode subir" ao dono, junto do ./testar.sh, ou quando pedirem /revisar-antes-de-subir.
---

# Revisar antes de subir (V19.68)

Adaptado do prompt 03 (multi-agent code review) e do 13 (security review) do
vibe-coding-toolkit. Objetivo: **menos bugs em produção**. Só lê: não corrige,
não publica, não mexe no `publicar.sh`.

## 1. Fixar o alvo

1. `git fetch producao` (só leitura; atualiza o `producao/master`) — sem acesso,
   use o `producao/master` local e avise que pode estar desatualizado.
2. Alvo = `producao/master..HEAD`. Árvore suja: revise também o `git diff` não
   commitado e diga que ele ainda não está no commit.
3. Nada entre os dois: diga "nada a revisar" e pare.
4. Liste os commits e os arquivos tocados. Diff de `servidor_web.py`/`admin.html`
   é grande: passe aos revisores só os trechos alterados (`git diff -U30`) e deixe
   cada um abrir o resto que precisar.

## 2. Despachar os 4 revisores em paralelo

Um `Agent` por foco, todos na mesma mensagem, **somente leitura** (não editam,
não rodam o servidor, não chamam ML/Shopee/Apollo). Cada um recebe: o intervalo
de commits, a lista de arquivos, o seu foco abaixo e a ordem de ler o
`CLAUDE.md` (seções 1, 4 e as do assunto do diff). Pedir de volta, por achado:
gravidade (crítico/alto/médio/baixo), `arquivo:linha`, **cenário concreto de
falha** (entrada/estado → o que dá errado na operação) e a evidência no código.
Sem cenário concreto, não é achado.

- **Regras do ADMIN e dados** — exclusão/override do ADMIN desfeito por sync,
  import, merge, cache ou cofre; concluído voltando à Conferência; bip que
  diminui; tombstone faltando (excluir sem tombstone = integração recria);
  override em `__ROOT__` de banco agregado; filial errada ou fila filtrada
  gravada por cima da outra filial; `null`/sem resposta virando zero; ordem do
  `get_storage`/`save_storage` alterada; KIT multiplicando contagem.
- **Concorrência e travas** — escrita fora do `DB_LOCK` ou resposta enviada
  dentro dele; `refresh_token` de uso único renovado sem a trava da casa/
  marketplace e sem reler a linha; worker novo fora do `_worker_protegido` ou do
  `SAUDE_WORKERS_INFO`; dois PCs/abas gravando (tela desatualizada, `oldValue`);
  rodada longa segurando trava que o clique precisa (504); thread nova sem
  `ml_na_casa`.
- **Segurança e permissões** — rota nova sem checar módulo/perfil; POST que grava
  liberado ao Consulta (`CONSULTA_POST_LIVRES`); chave nova fora das listas de
  leitura do storage (403) ou segredo saindo por `/api/storage`; token/senha em
  log, resposta ou URL; entrada externa (webhook, planilha, extensão,
  `postMessage`) sem validação; OWNER exigido onde a regra pede.
- **Integrações externas** — chamada nova por ciclo de worker (custo/limite:
  Apollo 20/min compartilhado, ML 429); erro da API esvaziando a fila ou
  apagando dado (falha = não grava); paginação/janela cortando registros;
  ação que grava no marketplace sem passar por Simulação → Lote → Automático;
  formato "deduzido da doc" sem amostra guardada; contrato das extensões Chrome
  quebrado.

## 3. Conferir e juntar (eu, não os revisores)

- Abrir o código de cada achado e confirmar o cenário. Descartar o que não se
  sustenta, duplicados (mesma causa em focos diferentes vira um) e estilo.
- Ver se algum teste de `TESTES/` já cobre o caso — se cobre e passa, rebaixar
  ou descartar.

## 4. Mostrar ao dono

Junto do resultado do `./testar.sh`, em português:

- Intervalo revisado (commits) e o que ficou de fora (ex.: árvore suja, sem fetch).
- Achados confirmados, mais grave primeiro: gravidade, `arquivo:linha`, cenário
  de falha em linguagem da operação, sugestão de correção.
- Contagem de descartados.
- Nenhum achado ≠ "sem bugs": diga o que a revisão não cobre (comportamento em
  produção, respostas reais das APIs).

**Não corrigir sozinho.** O dono escolhe o que corrigir; cada correção começa
pelo teste que reproduz o bug (seção 8 do `CLAUDE.md`), roda `./testar.sh` e a
revisão de novo só sobre a correção. Achado crítico aberto: recomendar não subir.
