"""V20.17 — Agente de imagens: a parte de imagem (Pillow + Real-ESRGAN + Chrome headless).

Nada aqui chama o Claude nem o Noromak: recebe imagens, devolve imagens. As regras vêm da skill
imagem-anuncio (testadas em 04/11 com o Hidratante de Couro Würth):
- foto JÁ em fundo branco liso: NÃO recortar (produto branco some junto com o fundo; o logo virou
  "WÜR"); corta pelo que não é branco e cola na tela branca;
- foto pequena (< 1000 px): amplia 4x com Real-ESRGAN, que deforma letra pequena; por isso a
  comparação por faixas (ampliada × ampliação comum) e a troca da faixa deformada pela comum;
- o produto nunca é alterado: só fundo, enquadramento, contraste e nitidez leves.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

TAMANHO = 1200
OCUPACAO = 0.85
LIMITE_PEQUENA = 1000        # lado maior abaixo disto vai para a IA
FAIXAS = 6                   # faixas horizontais comparadas na conferência
LIMIAR_BRANCO = 12           # diferença do branco que conta como produto


# ---------- básicos ----------

def abrir(dados: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(dados))
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        fundo = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        fundo.paste(rgba, mask=rgba.split()[-1])
        return fundo
    return img.convert("RGB")


def jpg(img: Image.Image, qualidade: int = 94) -> bytes:
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=qualidade)
    return b.getvalue()


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def reduzir(img: Image.Image, lado: int) -> Image.Image:
    if max(img.size) <= lado:
        return img
    esc = lado / max(img.size)
    return img.resize((max(1, round(img.width * esc)), max(1, round(img.height * esc))), Image.LANCZOS)


def caixa_do_produto(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Retângulo do que não é branco (foto em fundo branco)."""
    fundo = Image.new("RGB", img.size, (255, 255, 255))
    mascara = ImageChops.difference(img.convert("RGB"), fundo).convert("L").point(lambda v: 255 if v > LIMIAR_BRANCO else 0)
    return mascara.getbbox()


def fundo_branco_liso(img: Image.Image) -> bool:
    """Bordas da foto quase brancas: dá para usar a caixa do produto sem recortar."""
    w, h = img.size
    rgb = img.convert("RGB")
    pontos = [(x, y) for x in (0, w // 4, w // 2, 3 * w // 4, w - 1) for y in (0, h - 1)]
    pontos += [(x, y) for x in (0, w - 1) for y in (h // 4, h // 2, 3 * h // 4)]
    return all(min(rgb.getpixel(p)) >= 245 for p in pontos)


# ---------- ampliação ----------

@dataclass
class Ampliacao:
    imagem: Image.Image           # a que segue (IA, ou a original se não precisou/falhou)
    comum: Image.Image | None     # ampliação comum do mesmo tamanho (referência e reserva)
    usou_ia: bool
    aviso: str = ""


def ampliacao_comum(img: Image.Image, tamanho: tuple[int, int]) -> Image.Image:
    out = img.resize(tamanho, Image.LANCZOS)
    return out.filter(ImageFilter.UnsharpMask(radius=3, percent=80, threshold=2))


def ampliar(img: Image.Image, pasta_esrgan: str | Path | None) -> Ampliacao:
    """Lado maior < LIMITE_PEQUENA: amplia 4x com o Real-ESRGAN. Placa fraca (Intel HD): blocos de 64
    (-t 64); bloco maior derruba o driver (vkQueueSubmit failed -4). Falhou = segue com a comum."""
    if max(img.size) >= LIMITE_PEQUENA:
        return Ampliacao(img, None, False)
    alvo = (img.width * 4, img.height * 4)
    comum = ampliacao_comum(img, alvo)
    exe = Path(pasta_esrgan or "") / "realesrgan-ncnn-vulkan.exe"
    if not exe.is_file():
        return Ampliacao(comum, comum, False, "Real-ESRGAN não encontrado: ampliada do jeito comum.")
    with tempfile.TemporaryDirectory() as d:
        ent, sai = os.path.join(d, "in.png"), os.path.join(d, "out.png")
        img.save(ent)
        try:
            r = subprocess.run([str(exe), "-i", ent, "-o", sai, "-n", "realesrgan-x4plus", "-s", "4",
                                "-t", "64", "-j", "1:1:1", "-m", str(exe.parent / "models")],
                               cwd=str(exe.parent), capture_output=True, text=True, timeout=1800,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            falhou = "failed" in (r.stderr or "").lower() or not os.path.isfile(sai)
        except (subprocess.TimeoutExpired, OSError):
            falhou = True
        if falhou:
            return Ampliacao(comum, comum, False, "A ampliação por IA falhou: ampliada do jeito comum.")
        ia = Image.open(sai).convert("RGB")
        ia.load()
    if ia.size != alvo:
        ia = ia.resize(alvo, Image.LANCZOS)
    return Ampliacao(ia, comum, True)


# ---------- faixas (conferência das letras) ----------

def faixas(altura: int, n: int = FAIXAS, sobra: float = 0.12) -> list[tuple[int, int]]:
    """Divide a altura em n faixas com um pouco de sobreposição (texto cortado ao meio aparece inteiro
    em uma das duas)."""
    passo = altura / n
    extra = int(passo * sobra)
    return [(max(0, int(i * passo) - extra), min(altura, int((i + 1) * passo) + extra)) for i in range(n)]


def par_da_faixa(referencia: Image.Image, ampliada: Image.Image, y0: int, y1: int, caixa: tuple[int, int, int, int] | None,
                 largura: int = 1000) -> Image.Image:
    """Imagem com a faixa da referência (em cima) e a da ampliada (embaixo), para o Claude comparar."""
    x0, x1 = (caixa[0], caixa[2]) if caixa else (0, referencia.width)
    a = reduzir(referencia.crop((x0, y0, x1, y1)), largura)
    b = reduzir(ampliada.crop((x0, y0, x1, y1)), largura)
    tela = Image.new("RGB", (max(a.width, b.width), a.height + b.height + 24), (255, 255, 255))
    tela.paste(a, (0, 0))
    ImageDraw.Draw(tela).rectangle((0, a.height + 10, tela.width, a.height + 14), fill=(220, 30, 30))
    tela.paste(b, (0, a.height + 24))
    return tela


def trocar_faixas(ampliada: Image.Image, comum: Image.Image, trechos: list[tuple[int, int]]) -> Image.Image:
    """Volta as faixas deformadas para a ampliação comum, com a borda esfumada (sem emenda)."""
    if not trechos:
        return ampliada
    mascara = Image.new("L", ampliada.size, 0)
    for y0, y1 in trechos:
        mascara.paste(255, (0, y0, ampliada.width, y1))
    mascara = mascara.filter(ImageFilter.GaussianBlur(10))
    return Image.composite(comum, ampliada, mascara)


# ---------- foto principal ----------

def montar_principal(img: Image.Image) -> Image.Image:
    """1200×1200, fundo branco, produto inteiro ocupando 85% do lado maior."""
    if fundo_branco_liso(img):
        caixa = caixa_do_produto(img)
        prod = img.crop(caixa) if caixa else img
        prod = ImageEnhance.Contrast(prod).enhance(1.03)
        mascara = None
    else:
        recorte = recortar(img)
        prod, mascara = recorte.convert("RGB"), recorte.split()[-1]
    lado = int(TAMANHO * OCUPACAO)
    esc = lado / max(prod.size)
    tam = (max(1, round(prod.width * esc)), max(1, round(prod.height * esc)))
    prod = prod.resize(tam, Image.LANCZOS)
    tela = Image.new("RGB", (TAMANHO, TAMANHO), (255, 255, 255))
    pos = ((TAMANHO - tam[0]) // 2, (TAMANHO - tam[1]) // 2)
    if mascara is not None:
        tela.paste(prod, pos, mascara.resize(tam, Image.LANCZOS))
    else:
        tela.paste(prod, pos)
    return tela


def montar_kit(itens: list[Image.Image]) -> Image.Image:
    """Itens lado a lado, mesma altura, em fundo branco (foto principal do kit, sem texto)."""
    pecas = []
    for img in itens:
        caixa = caixa_do_produto(img) if fundo_branco_liso(img) else None
        pecas.append(img.crop(caixa) if caixa else recortar(img).convert("RGB"))
    altura = 1000
    pecas = [p.resize((max(1, round(p.width * altura / p.height)), altura), Image.LANCZOS) for p in pecas]
    vao = 40
    largura = sum(p.width for p in pecas) + vao * (len(pecas) - 1)
    linha = Image.new("RGB", (largura, altura), (255, 255, 255))
    x = 0
    for p in pecas:
        linha.paste(p, (x, 0))
        x += p.width + vao
    return montar_principal_de_linha(linha)


def montar_principal_de_linha(linha: Image.Image) -> Image.Image:
    lado = int(TAMANHO * OCUPACAO)
    esc = lado / max(linha.size)
    tam = (max(1, round(linha.width * esc)), max(1, round(linha.height * esc)))
    tela = Image.new("RGB", (TAMANHO, TAMANHO), (255, 255, 255))
    tela.paste(linha.resize(tam, Image.LANCZOS), ((TAMANHO - tam[0]) // 2, (TAMANHO - tam[1]) // 2))
    return tela


def recortar(img: Image.Image, limiar: int = 243) -> Image.Image:
    """Remove o fundo claro LIGADO À BORDA (flood fill) — mesma regra do preparar.py da skill.
    Só para foto que NÃO está em fundo branco liso."""
    rgb = img.convert("RGB")
    r, g, b = rgb.split()
    minimo = ImageChops.darker(ImageChops.darker(r, g), b)
    mascara = minimo.point(lambda v: 255 if v >= limiar else 0)
    w, h = mascara.size
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if mascara.getpixel((x, y)) == 255:
            ImageDraw.floodfill(mascara, (x, y), 128)
    fundo = mascara.point(lambda v: 255 if v == 128 else 0)
    alfa = ImageOps.invert(fundo).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    out = rgb.convert("RGBA")
    out.putalpha(alfa)
    caixa = alfa.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(caixa) if caixa else out


# ---------- artes (HTML → Chrome headless) ----------

NAVEGADORES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def achar_navegador() -> str | None:
    for c in NAVEGADORES:
        if os.path.isfile(c):
            return c
    for nome in ("chrome", "msedge", "google-chrome", "chromium"):
        if shutil.which(nome):
            return shutil.which(nome)
    return None


def renderizar(html: str, arquivos: dict[str, bytes]) -> Image.Image:
    """Grava o HTML e as imagens numa pasta temporária e tira o 'print' 1200×1200 no Chrome/Edge.

    Revisão antes de subir (04/11): o HTML pode ter sido escrito pelo Claude a partir de uma referência ou de
    páginas da internet. O Chrome roda trancado: a página vem de um servidor local só desta pasta, com regra de
    segurança (CSP) que proíbe script e qualquer imagem/conexão de fora (página http também não lê file:// do PC); e
    toda a rede passa por um proxy que não existe, menos esse servidor e as fontes do Google. (A opção do Chrome que
    desliga o JavaScript impede o print no modo sem janela.)"""
    nav = achar_navegador()
    if not nav:
        raise RuntimeError("Chrome ou Edge não encontrado neste PC (precisa para montar as artes).")
    import functools
    import http.server
    import threading
    with tempfile.TemporaryDirectory() as d:
        for nome, dados in arquivos.items():
            Path(d, Path(nome).name).write_bytes(dados)
        Path(d, "arte.html").write_text(html, encoding="utf-8")
        saida = Path(tempfile.mkdtemp()) / "arte.png"

        class _Silencioso(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def end_headers(self):
                # Sem script, sem iframe/objeto, e só imagem desta pasta / fonte do Google.
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'none'; object-src 'none'; frame-src 'none'; "
                                 "base-uri 'none'; form-action 'none'; connect-src 'none'; img-src 'self' data:; "
                                 "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                 "font-src 'self' data: https://fonts.gstatic.com")
                super().end_headers()
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=d))
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        porta = servidor.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as perfil:
                subprocess.run([nav, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                                f"--user-data-dir={perfil}", "--virtual-time-budget=4000", f"--window-size={TAMANHO},{TAMANHO}",
                                "--disable-extensions", "--no-first-run",
                                "--proxy-server=http://127.0.0.1:9",
                                f"--proxy-bypass-list=<-loopback>;127.0.0.1:{porta};fonts.googleapis.com;fonts.gstatic.com",
                                f"--screenshot={saida}", f"http://127.0.0.1:{porta}/arte.html"],
                               check=True, capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            servidor.shutdown()
            servidor.server_close()
    img = Image.open(saida).convert("RGB")
    img.load()
    shutil.rmtree(saida.parent, ignore_errors=True)
    return img


SEM_FUNDO_MIN_OCUPACAO = 0.35   # o recorte tem de manter ao menos isto da caixa do produto
FURO_MIN = 0.004                # branco fechado (furo) maior que isto da caixa é candidato a fundo
FURO_MAX_DESVIO = 4.0           # e tem de ser liso (fundo de estúdio), não plástico branco com sombra


def _furos_do_fundo(rgb: Image.Image, mascara: Image.Image, area_caixa: int) -> Image.Image:
    """Branco FECHADO pelo produto que é o fundo aparecendo (furo do rolamento, alça, vão de kit): grande,
    liso e da cor do fundo. Devolve a máscara (255) desses buracos. Plástico branco tem sombra e não entra."""
    furos = Image.new("L", mascara.size, 0)
    candidatos = mascara.point(lambda v: 255 if v == 255 else 0).filter(ImageFilter.MinFilter(7))
    trabalho = mascara.copy()
    for _ in range(40):
        caixa = candidatos.getbbox()
        if not caixa:
            break
        x0, y0, x1, y1 = caixa
        semente = next(((x, y0) for x in range(x0, x1) if candidatos.getpixel((x, y0)) == 255), None)
        if semente is None:
            break
        ImageDraw.floodfill(trabalho, semente, 64)
        regiao = trabalho.point(lambda v: 255 if v == 64 else 0)
        area = regiao.histogram()[255]
        trabalho.paste(32, mask=regiao)                     # já visto
        candidatos.paste(0, mask=regiao)
        if area < FURO_MIN * area_caixa:
            continue
        est = ImageStat.Stat(rgb, mask=regiao)
        if max(est.stddev) <= FURO_MAX_DESVIO and min(est.mean) >= 250:
            furos.paste(255, mask=regiao)
    return furos


SEM_FUNDO_MAX_VAZAMENTO = 0.02   # fundo da borda que entrou no meio do produto (frasco branco da Würth: 5,5%; rolamento: 0)


def _vazou(produto: Image.Image, fundo_borda: Image.Image) -> float:
    """Quanto do fundo LIGADO À BORDA ficou entre partes do produto, linha a linha e coluna a coluna (em relação
    à área do produto). Rolamento: ~0 (o furo é "furo", não borda). Frasco branco sem contorno: o fundo entra
    pelos lados do frasco e o logo fica solto — passa do limite e o recorte é recusado."""
    lado = 300
    esc = lado / max(produto.size)
    tam = (max(1, round(produto.width * esc)), max(1, round(produto.height * esc)))
    pm = produto.resize(tam, Image.NEAREST).point(lambda v: 1 if v > 127 else 0)
    fb = fundo_borda.resize(tam, Image.NEAREST).point(lambda v: 1 if v > 127 else 0)
    w, h = tam
    p, f = list(pm.getdata()), list(fb.getdata())
    total = sum(p) or 1

    def dentro(indices) -> int:
        idx = [i for i in indices if p[i]]
        if len(idx) < 2:
            return 0
        a, z = indices.index(idx[0]), indices.index(idx[-1])
        return sum(f[i] for i in indices[a:z + 1])
    linhas = sum(dentro(list(range(y * w, y * w + w))) for y in range(h))
    colunas = sum(dentro(list(range(x, w * h, w))) for x in range(w))
    return max(linhas, colunas) / total


def recortar_fundo_branco(img: Image.Image, limiar: int = 246) -> Image.Image | None:
    """V20.21 (dono, 04/11: "a foto do produto tem que ser sem fundo"): tira o fundo branco liso, inclusive o
    que aparece por dentro do produto (furo do rolamento). Se o recorte levar o produto junto (produto branco
    sem contorno — o "WÜR" da skill), devolve None e a arte usa o retângulo com multiply, como antes."""
    rgb = reduzir(img.convert("RGB"), 1400)
    r, g, b = rgb.split()
    minimo = ImageChops.darker(ImageChops.darker(r, g), b)
    mascara = minimo.point(lambda v: 255 if v >= limiar else 0)
    w, h = mascara.size
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if mascara.getpixel((x, y)) == 255:
            ImageDraw.floodfill(mascara, (x, y), 128)
    # Caixa de TUDO o que não é o fundo, medida pela cor da borda (margem apertada): o frasco branco 251 em fundo
    # 255 entra na caixa, e o recorte que o comeu é recusado. A caixa_do_produto (margem 12) só via o rótulo.
    borda = Image.new("L", (w, h), 0)
    ImageDraw.Draw(borda).rectangle((0, 0, w - 1, h - 1), outline=255, width=max(2, min(w, h) // 100))
    nivel = ImageStat.Stat(minimo, mask=borda).median[0]
    caixa = minimo.point(lambda v: 255 if v < nivel - 3 else 0).getbbox()
    if not caixa:
        return None
    area_caixa = (caixa[2] - caixa[0]) * (caixa[3] - caixa[1])
    fundo_borda = mascara.point(lambda v: 255 if v == 128 else 0)
    fundo = ImageChops.lighter(fundo_borda, _furos_do_fundo(rgb, mascara, area_caixa))
    produto = ImageOps.invert(fundo)
    if produto.crop(caixa).histogram()[255] < SEM_FUNDO_MIN_OCUPACAO * area_caixa:
        return None
    if _vazou(produto, fundo_borda) > SEM_FUNDO_MAX_VAZAMENTO:
        return None
    alfa = produto.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
    out = rgb.convert("RGBA")
    out.putalpha(alfa)
    corte = alfa.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(corte) if corte else None


def produto_para_arte(img: Image.Image) -> tuple[bytes, bool]:
    """PNG do produto para a arte e se ele está SEM FUNDO (transparente).
    V20.21: em fundo branco liso o fundo também sai (antes ia o retângulo branco com multiply, que brigava com o
    fundo da arte); só se o recorte comer o produto volta ao retângulo (False)."""
    if fundo_branco_liso(img):
        sem_fundo = recortar_fundo_branco(img)
        if sem_fundo is not None:
            return png(sem_fundo), True
        caixa = caixa_do_produto(img)
        return png(reduzir(img.crop(caixa) if caixa else img, 1400)), False
    return png(reduzir(recortar(img), 1400)), True
