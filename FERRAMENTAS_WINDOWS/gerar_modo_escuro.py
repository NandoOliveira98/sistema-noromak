"""V18.83 — gera o bloco do modo escuro de uma tela a partir das cores fixas do CSS dela.

As telas antigas escrevem cor direto no CSS (background:#fff, color:#18324A...). Em vez de
reescrever cada uma, este script lê os <style> da página e, para cada regra com cor clara de
fundo ou cor escura de texto, cria a mesma regra prefixada com html[data-tema="escuro"] e a cor
convertida mantendo o tom (verde continua verde, vermelho continua vermelho, só que escuro).
Também converte as cores escritas em style="..." dentro do HTML/JS da tela.

Uso (na raiz do projeto):
    python FERRAMENTAS_WINDOWS/gerar_modo_escuro.py APP/precificador.html [APP/outra.html ...]

O resultado é gravado na própria página, entre os marcadores
<style id="escuro-auto-v1883"> ... </style> (refeito a cada execução). Ajustes à mão ficam em
<style id="escuro-v1883">, que vem depois e vence. Sem dependência externa.
"""

from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

PREFIXO = 'html[data-tema="escuro"]'
MARCA_INI = '<style id="escuro-auto-v1883">'
COR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)|\bwhite\b", re.I)


def _rgb(txt: str) -> tuple[float, float, float, float] | None:
    t = txt.strip().lower()
    if t == "white":
        return 1.0, 1.0, 1.0, 1.0
    if t.startswith("#"):
        h = t[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, 1.0
    m = re.match(r"rgba?\(([^)]*)\)", t)
    if m:
        p = [float(x) for x in m.group(1).split(",")]
        return p[0] / 255, p[1] / 255, p[2] / 255, (p[3] if len(p) > 3 else 1.0)
    return None


def _hex(r: float, g: float, b: float, a: float = 1.0) -> str:
    if a < 0.999:
        return f"rgba({round(r * 255)},{round(g * 255)},{round(b * 255)},{a:g})"
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def _lum(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def fundo_escuro(txt: str) -> str | None:
    """Fundo claro vira escuro no mesmo tom. Fundo médio/saturado (botão azul, selo amarelo) fica."""
    c = _rgb(txt)
    if not c:
        return None
    r, g, b, a = c
    if _lum(r, g, b) < 0.80:
        return None
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.12 or l > 0.985:  # branco/cinza: superfície do sistema
        nl, ns, h = 0.135 + (1 - l) * 0.35, 0.30, 0.59
    else:  # tingido (verde, vermelho, âmbar, azul claro): mesmo tom, escuro
        nl, ns = 0.16, min(0.55, s * 0.6 + 0.15)
    r, g, b = colorsys.hls_to_rgb(h, nl, ns)
    return _hex(r, g, b, a)


def texto_claro(txt: str) -> str | None:
    """Texto escuro vira claro no mesmo tom (título navy -> quase branco; vermelho -> vermelho claro)."""
    c = _rgb(txt)
    if not c:
        return None
    r, g, b, a = c
    if _lum(r, g, b) > 0.42:
        return None
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.35 or (0.5 <= h <= 0.7 and l < 0.30):  # cinza/navy de texto comum: quase branco
        nl, ns = 0.90 - l * 0.45, min(s, 0.25)
    else:  # texto colorido (status)
        nl, ns = 0.72, min(0.75, s)
    r, g, b = colorsys.hls_to_rgb(h, nl, ns)
    return _hex(r, g, b, a)


def borda_escura(txt: str) -> str | None:
    c = _rgb(txt)
    if not c:
        return None
    r, g, b, a = c
    if _lum(r, g, b) < 0.70:
        return None
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h if s >= 0.12 else 0.59, 0.25, min(0.45, s * 0.6 + 0.12))
    return _hex(r, g, b, a)


def _trocar(valor: str, fn) -> tuple[str, bool]:
    mudou = False

    def sub(m):
        nonlocal mudou
        novo = fn(m.group(0))
        if novo:
            mudou = True
            return novo
        return m.group(0)

    return COR.sub(sub, valor), mudou


def _dividir_seletores(sel: str) -> list[str]:
    partes, nivel, atual = [], 0, ""
    for ch in sel:
        if ch in "([":
            nivel += 1
        elif ch in ")]":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append(atual.strip())
            atual = ""
        else:
            atual += ch
    if atual.strip():
        partes.append(atual.strip())
    return partes


def _prefixar(sel: str) -> str | None:
    s = sel.strip()
    # Pseudo-elementos (bolinha do interruptor, setas) ficam como estão.
    if not s or s.startswith(":root") or "::" in s or ":after" in s or ":before" in s or s.startswith("@"):
        return None
    if s.startswith("html"):
        resto = s[4:]
        if resto.startswith("[data-tema"):
            return None
        return PREFIXO + resto
    return f"{PREFIXO} {s}"


def _regras(css: str):
    """Percorre o CSS devolvendo (contexto_media, seletor, corpo). Ignora @keyframes/@font-face."""
    i, n = 0, len(css)
    pilha: list[str] = []
    buf = ""
    while i < n:
        ch = css[i]
        if css.startswith("/*", i):
            j = css.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == "{":
            cab = buf.strip()
            buf = ""
            if cab.startswith("@media") or cab.startswith("@supports"):
                pilha.append(cab)
                i += 1
                continue
            if cab.startswith("@"):
                prof, j = 1, i + 1
                while j < n and prof:
                    prof += {"{": 1, "}": -1}.get(css[j], 0)
                    j += 1
                i = j
                continue
            j = css.find("}", i)
            if j < 0:
                break
            yield (pilha[-1] if pilha else ""), cab, css[i + 1:j]
            i = j + 1
            continue
        if ch == "}":
            if pilha:
                pilha.pop()
            buf = ""
            i += 1
            continue
        buf += ch
        i += 1


def gerar_css(html: str) -> str:
    estilos = re.findall(r"<style(?![^>]*escuro)[^>]*>(.*?)</style>", html, re.S | re.I)
    saida: dict[str, list[str]] = {}
    for css in estilos:
        for media, sel, corpo in _regras(css):
            decls = []
            tem_fundo_mantido = False
            for prop, val in re.findall(r"([\w-]+)\s*:\s*([^;]+)", corpo):
                p = prop.strip().lower()
                v = val.replace("!important", "").strip()
                if p in ("background", "background-color"):
                    novo, mudou = _trocar(v, fundo_escuro)
                    if mudou:
                        decls.append(f"{'background' if 'gradient' in v else 'background-color'}:{novo}!important")
                    elif COR.search(v) and not re.search(r"transparent|none|currentcolor", v, re.I):
                        tem_fundo_mantido = True
            for prop, val in re.findall(r"([\w-]+)\s*:\s*([^;]+)", corpo):
                p = prop.strip().lower()
                v = val.replace("!important", "").strip()
                if p == "color" and not tem_fundo_mantido:
                    novo, mudou = _trocar(v, texto_claro)
                    if mudou:
                        decls.append(f"color:{novo}!important")
                elif p in ("border", "border-color", "border-top", "border-bottom", "border-left", "border-right",
                           "border-top-color", "border-bottom-color", "border-left-color", "border-right-color", "outline-color"):
                    novo, mudou = _trocar(v, borda_escura)
                    if mudou:
                        decls.append(f"{p}:{novo}!important")
            if not decls:
                continue
            seletores = [x for x in (_prefixar(s) for s in _dividir_seletores(sel)) if x]
            if not seletores:
                continue
            saida.setdefault(media, []).append(f"{','.join(seletores)}{{{';'.join(decls)}}}")
    # Cores escritas em style="..." no HTML e nos modelos do JS.
    inline = []
    vistos = set()
    for m in re.finditer(r"(background(?:-color)?|(?<![-\w])color)\s*:\s*(#[0-9a-fA-F]{3,6}\b)", "\n".join(re.findall(r"style=[\"'`]([^\"'`]*)", html))):
        prop, cor = m.group(1).lower(), m.group(2)
        chave = (prop, cor.lower())
        if chave in vistos:
            continue
        vistos.add(chave)
        if prop.startswith("background"):
            novo = fundo_escuro(cor)
            alvo = "background-color"
        else:
            novo = texto_claro(cor)
            alvo = "color"
        if novo:
            for forma in (f"{prop}:{cor}", f"{prop}: {cor}"):
                inline.append(f'{PREFIXO} [style*="{forma}" i]{{{alvo}:{novo}!important}}')
    partes = []
    for media, regras in saida.items():
        corpo = "\n".join(regras)
        partes.append(f"{media}{{\n{corpo}\n}}" if media else corpo)
    if inline:
        partes.append("/* cores em style=\"...\" */\n" + "\n".join(inline))
    return "\n".join(partes)


def aplicar(caminho: Path) -> int:
    with open(caminho, encoding="utf-8", newline="") as fh:  # mantém o fim de linha do arquivo
        html = fh.read()
    css = gerar_css(html)
    bloco = (f"{MARCA_INI}\n/* V18.83 — GERADO por FERRAMENTAS_WINDOWS/gerar_modo_escuro.py a partir das cores fixas desta tela.\n"
             f"   Não editar aqui (é refeito); ajuste à mão no <style id=\"escuro-v1883\">. */\n{css}\n</style>\n")
    ini = html.find(MARCA_INI)
    if ini >= 0:
        fim = html.find("</style>", ini) + len("</style>\n")
        html = html[:ini] + bloco + html[fim:]
    else:
        manual = html.find('<style id="escuro-v1883">')
        pos = manual if manual >= 0 else html.find("</head>")
        html = html[:pos] + bloco + html[pos:]
    with open(caminho, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)
    return css.count("{")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(arg, aplicar(Path(arg)), "regras")
