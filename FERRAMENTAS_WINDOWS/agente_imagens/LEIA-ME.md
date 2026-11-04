# Agente de imagens do Noromak (V20.17)

Programa que roda **no PC do dono** e faz o trabalho pesado do Estúdio de Imagens (`/expedicao/imagens`):
busca a fila a cada 10 s, amplia fotos pequenas com o Real-ESRGAN, monta a foto principal ou a arte e
manda o Claude conferir. O servidor do Noromak só guarda os arquivos e a fila.

## Instalar em outro PC (uma vez)

1. Extraia o `Instalador_Agente_Imagens_Noromak.zip` numa pasta fixa, por exemplo `C:\AgenteImagensNoromak`
   (não deixe em Downloads: o atalho de início aponta para essa pasta).
2. Dê dois cliques em **`instalar.bat`**. Ele instala o Python (se faltar, pelo winget) e as bibliotecas, abre o
   `config.env` para você colar:
   - `ANTHROPIC_API_KEY`: chave da API (console.anthropic.com → API Keys; de preferência **uma chave por PC**);
   - `NOROMAK_AGENTE_TOKEN`: logado como perfil Admin, abra
     `https://noromakweb.com.br/expedicao/api/imagens/agente/config` e copie o `token`;
   pergunta se liga o agente junto com o Windows e roda o diagnóstico.
3. Tudo `[OK]` no diagnóstico? Dê dois cliques em `iniciar_agente.bat` (fica minimizado).

**Atenção:** enquanto o portão de login do NoromakWeb no nginx não liberar `/expedicao/api/imagens/agente/`
(`PEDIDO_NGINX_AGENTE_IMAGENS.md`), o diagnóstico mostra "O portão de login do NoromakWeb barrou o agente" e só o
PC do dono funciona (pelo túnel SSH, `NOROMAK_TUNEL=expedicao`). Deixe **um agente ligado por vez** na empresa.

Diagnóstico a qualquer hora: `python agente_imagens.py --testar` (não processa foto nem gasta crédito).
Gerar o pacote de novo (no PC do dono): `python empacotar.py`.

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

## Crédito, referência e pesquisa

- **Crédito:** cada entrega informa ao Noromak os tokens e as buscas; a tela mostra o gasto do ciclo (US$ 100 que
  renova todo dia 19) e quanto falta.
- **Imagem de referência** (artes): o Claude propõe os textos na estrutura da referência e escreve o HTML da arte
  imitando o layout (só com a foto real e os textos aprovados; fotos de veículos da referência viram ícone).
- **Nome do produto / link do fornecedor** (artes): pesquisa na internet antes dos textos (até 3 buscas, US$ 0,01
  cada); o que vier de lá aparece "da internet", com a fonte, para o operador conferir.

## Testar

```
python -m unittest test_agente -v
```
Sobe o servidor do Noromak com banco temporário e usa um Claude **simulado** (sem chamada, sem custo).
