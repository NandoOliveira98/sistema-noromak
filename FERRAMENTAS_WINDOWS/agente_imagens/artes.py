"""V20.17 — Agente de imagens: HTML das artes (benefícios, modo de uso, aplicação).

Mesmo visual dos modelos da skill imagem-anuncio (templates/*.html), montado a partir dos textos que
o operador APROVOU no Noromak. Campo vazio não aparece (nada é inventado). O produto entra como
"produto.png": em fundo branco liso vai com mix-blend-mode:multiply (sem sombra), senão recortado com
sombra (processar.produto_para_arte).
"""
from __future__ import annotations

import html
import re

BASE_CSS = """@import url('https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@400;600;800&display=block');
:root{--c1:#14c2a3;--tinta:#141414;--suave:#5b6470;--fundo:#f4f7fa}
*{box-sizing:border-box}
html,body{margin:0;width:1200px;height:1200px;overflow:hidden;font-family:Inter,Arial,sans-serif;color:var(--tinta)}
body{background:radial-gradient(ellipse at 50% 35%,#fff 0%,var(--fundo) 70%,#e6ebf0 100%)}
.titulo{font-family:Anton,Impact,'Arial Black',sans-serif;text-transform:uppercase;letter-spacing:.5px;line-height:.95;margin:0}
.sombra{filter:drop-shadow(0 28px 28px rgba(0,0,0,.22))}
.multiplica{mix-blend-mode:multiply}
.ico{width:64px;height:64px;flex:none;border-radius:50%;display:flex;align-items:center;justify-content:center}
.ico svg{width:36px;height:36px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
"""

ICONES = [
    '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>',                      # brilho
    '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',  # escudo
    '<path d="M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11z"/>',            # gota
    '<path d="M5 12l5 5 9-10"/>',                                           # check
    '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',               # relógio
    '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',  # sol
]
ICONES_PASSO = [
    '<path d="M9 7h5v4H9zM10 11v10h7v-6l-3-4M14 8h3M19 5v.01M21 7v.01M19 9v.01"/>',  # spray
    '<path d="M4 20l7-7M10 14l6-6 3 3-6 6zM15 7l2-2M18 10l2-2"/>',          # escova
    '<path d="M20 12a8 8 0 0 1-14 5M4 12a8 8 0 0 1 14-5M18 3v4h-4M6 21v-4h4"/>',  # circular
    '<path d="M4 8l8-4 8 4-8 4zM4 8v8l8 4 8-4V8"/>',                        # pano
    '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',               # relógio
    '<path d="M5 12l5 5 9-10"/>',                                           # check
]
ICONE_AREA = '<path d="M3 16v-3l2-5h14l2 5v3zM5 16v2M19 16v2M3 13h18M7 13.5v.01M17 13.5v.01"/>'
ICONE_PROIBIDO = '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>'


def _e(t: str) -> str:
    return html.escape(str(t or ""), quote=True)


def _cor(campos: dict[str, str]) -> str:
    c = (campos.get("cor") or "").strip()
    return c if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else "#1f57a5"


def _svg(d: str) -> str:
    return f'<svg viewBox="0 0 24 24">{d}</svg>'


def _produto(classe: str, sombra: bool) -> str:
    return f'<img class="prod {classe} {"sombra" if sombra else "multiplica"}" src="produto.png">'


def _pagina(cor: str, css: str, corpo: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}:root{{--c1:{cor}}}\n{css}</style>'
            f'</head><body>{corpo}</body></html>')


def _titulo(texto: str, tamanho: int) -> str:
    """Título longo encolhe para caber numa linha ou duas."""
    n = len(texto or "")
    t = tamanho if n <= 18 else (int(tamanho * 0.8) if n <= 28 else int(tamanho * 0.62))
    return f'<h1 class="titulo" style="font-size:{t}px">{_e(texto)}</h1>'


def beneficios(campos: dict[str, str], sombra: bool) -> str:
    itens = []
    for i in range(1, 7):
        v = (campos.get(f"beneficio_{i}") or "").strip()
        if v:
            tit, _, det = v.partition("|")
            itens.append((tit.strip(), det.strip()))
    posicoes = ["e1", "d1", "e2", "d2", "e3", "d3"]
    cartoes = "".join(
        f'<div class="card {posicoes[k]}"><div class="ico">{_svg(ICONES[k % len(ICONES)])}</div>'
        f'<h3>{_e(t)}</h3>{f"<p>{_e(d)}</p>" if d else ""}</div>' for k, (t, d) in enumerate(itens[:6]))
    css = """.topo{position:absolute;top:40px;left:40px;right:40px;text-align:center}
.topo .sub{margin-top:10px;font-size:32px;font-weight:600;color:var(--suave)}
.prod{position:absolute;left:50%;transform:translateX(-50%);bottom:60px;height:820px;max-width:440px;object-fit:contain}
.card{position:absolute;width:330px;background:#fff;border-radius:24px;padding:22px 24px;box-shadow:0 12px 30px rgba(0,0,0,.09);border-left:8px solid var(--c1)}
.card .ico{background:color-mix(in srgb,var(--c1) 14%,#fff);color:var(--c1);margin-bottom:12px}
.card h3{margin:0 0 6px;font-family:Anton,Impact;font-weight:400;font-size:34px;text-transform:uppercase;line-height:1}
.card p{margin:0;font-size:22px;line-height:1.3;color:var(--suave)}
.e1{left:40px;top:300px}.e2{left:40px;top:580px}.e3{left:40px;top:860px}
.d1{right:40px;top:300px}.d2{right:40px;top:580px}.d3{right:40px;top:860px}
.card.d1,.card.d2,.card.d3{border-left:none;border-right:8px solid var(--c1)}"""
    sub = campos.get("subtitulo") or ""
    corpo = (f'<div class="topo">{_titulo(campos.get("titulo", ""), 88)}{f"<div class=sub>{_e(sub)}</div>" if sub else ""}</div>'
             f'{_produto("", sombra)}{cartoes}')
    return _pagina(_cor(campos), css, corpo)


def modo(campos: dict[str, str], sombra: bool) -> str:
    passos = [(campos.get(f"passo_{i}") or "").strip() for i in range(1, 7)]
    passos = [p for p in passos if p]
    lista = "".join(f'<div class="passo"><span class="n">{k + 1}</span><div class="ico">{_svg(ICONES_PASSO[k % len(ICONES_PASSO)])}</div>'
                    f'<span>{_e(p)}</span></div>' for k, p in enumerate(passos))
    pode, nao = campos.get("pode") or "", campos.get("nao_usar") or ""
    css = """.topo{position:absolute;top:36px;left:40px;right:40px;text-align:center}
.col{position:absolute;left:40px;top:57%;transform:translateY(-50%);width:640px;max-height:940px;background:#fff;border-radius:26px;box-shadow:0 10px 30px rgba(0,0,0,.08);padding:18px 0 26px}
.passo{display:flex;align-items:center;gap:18px;padding:20px 28px;font-size:30px;line-height:1.25}
.passo .n{font-family:Anton,Impact;font-size:52px;color:var(--c1);width:38px;text-align:center}
.passo .ico{background:color-mix(in srgb,var(--c1) 14%,#fff);color:var(--c1)}
.obs{margin:14px 22px 0;padding:12px 18px;border-radius:14px;font-size:23px;font-weight:700}
.ok{background:color-mix(in srgb,var(--c1) 14%,#fff)}
.nao{background:#fde8e8;color:#c62828}
.prod{position:absolute;right:50px;bottom:60px;height:860px;max-width:440px;object-fit:contain}"""
    corpo = (f'<div class="topo">{_titulo(campos.get("titulo") or "Modo de uso", 92)}</div>'
             f'<div class="col">{lista}{f"<div class=\"obs ok\">✔ {_e(pode)}</div>" if pode else ""}'
             f'{f"<div class=\"obs nao\">⦸ {_e(nao)}</div>" if nao else ""}</div>{_produto("", sombra)}')
    return _pagina(_cor(campos), css, corpo)


def aplicacao(campos: dict[str, str], sombra: bool) -> str:
    areas = [(campos.get(f"area_{i}") or "").strip() for i in range(1, 5)]
    areas = [a for a in areas if a]
    grade = "".join(f'<div class="area"><div class="ico">{_svg(ICONE_AREA)}</div><h3>{_e(a)}</h3></div>' for a in areas)
    nao = campos.get("nao_usar") or ""
    css = """.topo{position:absolute;top:40px;left:40px;right:40px;text-align:center}
.prod{position:absolute;left:50px;bottom:60px;height:880px;max-width:400px;object-fit:contain}
.grade{position:absolute;left:470px;right:40px;top:260px;display:grid;grid-template-columns:1fr 1fr;gap:22px}
.area{background:#fff;border-radius:24px;padding:50px 20px;box-shadow:0 12px 28px rgba(0,0,0,.08);text-align:center}
.area .ico{width:110px;height:110px;margin:0 auto 14px;background:color-mix(in srgb,var(--c1) 16%,#fff);color:var(--c1)}
.area .ico svg{width:62px;height:62px;stroke-width:1.6}
.area h3{margin:0;font-family:Anton,Impact;font-weight:400;font-size:32px;text-transform:uppercase;line-height:1.05}
.nao{position:absolute;left:470px;right:40px;bottom:50px;background:#fde8e8;color:#c62828;border-radius:20px;padding:20px 26px;
 font-size:27px;font-weight:800;display:flex;align-items:center;gap:16px}
.nao .ico{background:#fff;color:#c62828}"""
    corpo = (f'<div class="topo">{_titulo(campos.get("titulo") or "Onde usar", 88)}</div>{_produto("", sombra)}'
             f'<div class="grade">{grade}</div>'
             f'{f"<div class=nao><div class=ico>{_svg(ICONE_PROIBIDO)}</div>{_e(nao)}</div>" if nao else ""}')
    return _pagina(_cor(campos), css, corpo)


MONTADORES = {"beneficios": beneficios, "modo": modo, "aplicacao": aplicacao}


def montar(tipo: str, campos_lista: list[dict], sombra: bool) -> str:
    campos = {str(c.get("chave")): str(c.get("valor") or "").strip() for c in campos_lista}
    return MONTADORES[tipo](campos, sombra)
