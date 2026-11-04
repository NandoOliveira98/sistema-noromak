"""V20.17 — Agente de imagens: as chamadas ao Claude (API da Anthropic, SDK oficial).

O Claude não edita pixels: ele OLHA e DECIDE. Quatro chamadas, todas com resposta em JSON de
esquema fixo (output_config.format), uma mensagem só (sem histórico):
1. analisar_foto   — a foto serve? fundo branco liso? produto claro?
2. propor_textos   — lê o rótulo e propõe os textos da arte (só o que está escrito; o resto vazio)
3. conferir_faixas — compara faixa a faixa a ampliação por IA com a comum: letra deformada?
4. conferir_arte   — a arte pronta tem texto cortado/encostado/sobreposto?

Modelo vem do Noromak (padrão claude-haiku-5-5, decisão do dono 04/11: o mais barato). Esforço
"low": é tarefa de olhar e responder, não de raciocínio longo. A chave fica só neste PC.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any

from PIL import Image

# US$ por milhão de tokens (entrada, saída) — só para o log de custo estimado.
PRECOS = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}

CAMPOS_ARTE: dict[str, list[tuple[str, str]]] = {
    "beneficios": [("titulo", "Título (nome do produto)"), ("subtitulo", "Subtítulo (o que é, em poucas palavras)")]
                  + [(f"beneficio_{i}", f"Benefício {i} (título | detalhe)") for i in range(1, 7)]
                  + [("cor", "Cor de destaque (cor principal do rótulo, #rrggbb)")],
    "modo": [("titulo", "Título")] + [(f"passo_{i}", f"Passo {i}") for i in range(1, 7)]
            + [("pode", "Observação positiva (ex.: pronto uso)"), ("nao_usar", "Onde NÃO usar / cuidado"),
               ("cor", "Cor de destaque (cor principal do rótulo, #rrggbb)")],
    "aplicacao": [("titulo", "Título")] + [(f"area_{i}", f"Onde usar {i}") for i in range(1, 5)]
                 + [("nao_usar", "Onde NÃO usar"), ("cor", "Cor de destaque (cor principal do rótulo, #rrggbb)")],
}

INSTRUCAO_TEXTOS = {
    "beneficios": "Benefícios do produto para os cartões da arte: título curto (2 a 3 palavras) | detalhe curto "
                  "(até 6 palavras), separados por ' | '. Use 4 a 6. Volume/peso do rótulo pode ser um benefício.",
    "modo": "Passos do modo de uso, na ordem, frases curtas (até 9 palavras) começando com verbo. Use o modo de uso "
            "escrito no rótulo (normalmente no verso). 'pode' e 'nao_usar' só se o rótulo disser.",
    "aplicacao": "Onde usar: superfícies ou partes (1 a 3 palavras cada), só as que o rótulo cita. 'nao_usar' só se o "
                 "rótulo disser onde não aplicar.",
}


class ClaudeRecusou(Exception):
    pass


@dataclass
class Uso:
    entrada: int = 0
    saida: int = 0
    modelo: str = ""

    def somar(self, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.entrada += int(getattr(u, "input_tokens", 0) or 0)
            self.saida += int(getattr(u, "output_tokens", 0) or 0)

    def custo(self) -> float:
        pe, ps = PRECOS.get(self.modelo, (0.0, 0.0))
        return (self.entrada * pe + self.saida * ps) / 1_000_000


def bloco_imagem(img: Image.Image, lado: int = 1568) -> dict[str, Any]:
    """Imagem para a API: JPEG, lado maior até 1568 px (o tamanho que a visão aproveita)."""
    if max(img.size) > lado:
        esc = lado / max(img.size)
        img = img.resize((max(1, round(img.width * esc)), max(1, round(img.height * esc))), Image.LANCZOS)
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=90)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(b.getvalue()).decode("ascii")}}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SISTEMA = ("Você trabalha no estúdio de imagens de uma loja de autopeças e produtos automotivos que vende no Mercado "
           "Livre e na Shopee. Você olha fotos de produtos e responde só o que vê. Nunca invente informação que não "
           "está escrita no produto. Responda em português do Brasil.")


class ClaudeImagens:
    def __init__(self, cliente: Any, modelo: str):
        self.cliente = cliente
        self.modelo = modelo
        self.uso = Uso(modelo=modelo)

    def _chamar(self, conteudo: list[dict[str, Any]], esquema: dict[str, Any], max_tokens: int = 4000) -> dict[str, Any]:
        resp = self.cliente.messages.create(
            model=self.modelo,
            max_tokens=max_tokens,
            system=SISTEMA,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}},
            messages=[{"role": "user", "content": conteudo}],
        )
        self.uso.somar(resp)
        if getattr(resp, "stop_reason", "") == "refusal":
            raise ClaudeRecusou("O Claude recusou analisar esta imagem.")
        if getattr(resp, "stop_reason", "") == "max_tokens":
            raise RuntimeError("A resposta do Claude veio cortada (limite de tamanho).")
        texto = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return json.loads(texto)

    # 1 -------------------------------------------------------------------------------------
    def analisar_foto(self, img: Image.Image) -> dict[str, Any]:
        esquema = _obj({"serve": {"type": "boolean"}, "motivo": {"type": "string"},
                        "fundo_branco": {"type": "boolean"}, "produto_claro": {"type": "boolean"}})
        return self._chamar([bloco_imagem(img), {"type": "text", "text": (
            "Esta foto vai virar a foto principal de um anúncio (produto inteiro em fundo branco).\n"
            "- serve: false se o produto está cortado, desfocado/tremido, em cena (mão, mesa, carro), com mais de um "
            "produto que não é kit, ou se não dá para ver o produto. Fundo colorido ou cinza NÃO impede (true).\n"
            "- motivo: se não serve, diga em uma frase o que mandar no lugar; se serve, deixe vazio.\n"
            "- fundo_branco: o fundo é branco liso.\n"
            "- produto_claro: o produto (ou a embalagem) é branco ou muito claro.")}], esquema, 1500)

    # 2 -------------------------------------------------------------------------------------
    def propor_textos(self, tipo: str, imagens: list[Image.Image]) -> dict[str, Any]:
        campos = CAMPOS_ARTE[tipo]
        chaves = [c for c, _ in campos]
        esquema = _obj({
            "serve": {"type": "boolean"}, "motivo": {"type": "string"},
            "campos": {"type": "array", "items": _obj({
                "chave": {"type": "string", "enum": chaves}, "valor": {"type": "string"},
                "origem": {"type": "string", "enum": ["rotulo", "nao_encontrado"]}})},
        })
        lista = "\n".join(f"- {c}: {r}" for c, r in campos)
        conteudo = [bloco_imagem(im) for im in imagens] + [{"type": "text", "text": (
            f"Fotos do produto ({'frente e verso' if len(imagens) > 1 else 'frente'}). Vamos montar uma arte de anúncio.\n"
            f"{INSTRUCAO_TEXTOS[tipo]}\n\nPreencha um item para CADA campo abaixo (todos, na ordem):\n{lista}\n\n"
            "Regras: use só o que está escrito ou é visível no produto. Se não estiver, valor vazio e origem "
            "'nao_encontrado' — nunca invente número, tempo de proteção ou promessa. 'cor' é a cor principal do rótulo "
            "em #rrggbb (origem 'rotulo'). 'serve' false só se não dá para ler o rótulo (foto ruim), com o motivo.")}]
        dados = self._chamar(conteudo, esquema, 3000)
        # Garante todos os campos, na ordem, com o rótulo da tela.
        por_chave = {c.get("chave"): c for c in dados.get("campos") or [] if isinstance(c, dict)}
        dados["campos"] = [{"chave": c, "rotulo": r, "valor": str((por_chave.get(c) or {}).get("valor") or "").strip(),
                            "origem": (por_chave.get(c) or {}).get("origem") or "nao_encontrado"} for c, r in campos]
        for c in dados["campos"]:
            if not c["valor"]:
                c["origem"] = "nao_encontrado"
        return dados

    # 3 -------------------------------------------------------------------------------------
    def conferir_faixas(self, pares: list[Image.Image]) -> list[dict[str, Any]]:
        esquema = _obj({"faixas": {"type": "array", "items": _obj({
            "indice": {"type": "integer"}, "tem_texto": {"type": "boolean"}, "deformado": {"type": "boolean"},
            "trecho": {"type": "string"}, "obs": {"type": "string"}})}})
        conteudo: list[dict[str, Any]] = []
        for i, par in enumerate(pares):
            conteudo.append({"type": "text", "text": f"Faixa {i}:"})
            conteudo.append(bloco_imagem(par, 1200))
        conteudo.append({"type": "text", "text": (
            "Cada faixa mostra o mesmo pedaço do rótulo duas vezes, separadas por uma linha vermelha: EM CIMA a "
            "referência (ampliação comum, um pouco borrada mas com as letras certas) e EMBAIXO a ampliação por IA "
            "(mais nítida, mas a IA às vezes deforma letras). Para cada faixa:\n"
            "- tem_texto: há letras ou números nela.\n"
            "- deformado: alguma letra/número de BAIXO está diferente, torta, borrada em garrancho ou ilegível em "
            "relação à de CIMA. Nitidez maior embaixo NÃO é defeito; letra trocada ou desmanchada é.\n"
            "- trecho: o texto da faixa (como está na referência), curto.\n- obs: o que está deformado, se houver.\n"
            "Responda todas as faixas, com o mesmo índice.")})
        return list(self._chamar(conteudo, esquema, 4000).get("faixas") or [])

    # 4 -------------------------------------------------------------------------------------
    def conferir_arte(self, arte: Image.Image, campos: list[dict[str, Any]]) -> dict[str, Any]:
        esquema = _obj({"ok": {"type": "boolean"}, "problemas": {"type": "array", "items": {"type": "string"}}})
        textos = "\n".join(f"- {c['valor']}" for c in campos if c.get("valor") and c.get("chave") != "cor")
        return self._chamar([bloco_imagem(arte), {"type": "text", "text": (
            f"Esta é uma arte de anúncio 1200×1200. Os textos aprovados foram:\n{textos}\n\n"
            "ok = true se todos aparecem inteiros e legíveis, nada cortado na borda, nenhum texto em cima de outro "
            "ou em cima do produto, e o produto aparece inteiro. Senão ok = false e liste cada problema em uma frase.")}],
            esquema, 1500)
