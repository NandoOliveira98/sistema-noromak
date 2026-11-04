# Agente de imagens do Noromak (V20.17)

Programa que roda **no PC do dono** e faz o trabalho pesado do Estúdio de Imagens (`/expedicao/imagens`):
busca a fila a cada 10 s, amplia fotos pequenas com o Real-ESRGAN, monta a foto principal ou a arte e
manda o Claude conferir. O servidor do Noromak só guarda os arquivos e a fila.

## Instalar (uma vez)

1. Python 3.12 com o Pillow e o SDK da Anthropic:
   ```
   pip install pillow anthropic
   ```
2. Real-ESRGAN: já está em `%USERPROFILE%\.claude\skills\imagem-anuncio\ferramentas\realesrgan`
   (o mesmo da skill `/melhorar`). Outro lugar: `REALESRGAN_DIR` no config.env.
3. Chrome ou Edge instalado (monta as artes).
4. Copie `config.env.exemplo` para `config.env` e preencha:
   - `ANTHROPIC_API_KEY`: chave criada em console.anthropic.com (cole você mesmo, nunca pelo chat).
     Deixe um **limite de gasto mensal** no Console.
   - `NOROMAK_AGENTE_TOKEN`: logado como perfil Admin, abra
     `https://noromakweb.com.br/expedicao/api/imagens/agente/config` e copie o `token`.
   O `config.env` não vai para o Git.
5. Para subir com o Windows: atalho do `iniciar_agente.bat` em `shell:startup` (Win+R).

## Usar

- `iniciar_agente.bat` (fica minimizado) ou `python agente_imagens.py`.
- `python agente_imagens.py --uma-vez` processa o que houver e sai (bom para testar).
- `agente.log`: uma linha por imagem com tokens e custo estimado.
- O Noromak mostra "Agente ligado" enquanto ele estiver rodando.

## O que ele faz

| Tipo | Etapas | Claude |
|---|---|---|
| Foto principal | analisa → amplia se < 1000 px → 1200×1200 fundo branco → confere letras | 1 + 1 a 3 chamadas |
| Kit juntos | o mesmo para cada foto → itens lado a lado em fundo branco (sem texto) | 1 por foto + conferência |
| Benefícios / Modo de uso / Aplicação | lê o rótulo e **propõe** os textos → operador aprova no Noromak → monta a arte → confere | 1 + (1 a 3) + 1 |

Regras (da skill imagem-anuncio): produto nunca é alterado; foto já em fundo branco liso não é
recortada (produto branco sumia junto com o fundo); produto claro em outro fundo = "precisa de outra
foto"; a IA deforma letra pequena, então cada faixa do rótulo é comparada com a ampliação comum e a
faixa deformada volta para a comum (2 tentativas; depois "revisar").

## Testar

```
python -m unittest test_agente -v
```
Sobe o servidor do Noromak com banco temporário e usa um Claude **simulado** (sem chamada, sem custo).
