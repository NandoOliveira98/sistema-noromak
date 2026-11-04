"""V20.17 — monta o pacote para instalar o agente de imagens em outro PC (sem nenhum segredo).

Uso:  python empacotar.py [destino.zip]   (padrão: Desktop\\Instalador_Agente_Imagens_Noromak.zip)

Leva o código, o modelo do config.env, os .bat e o Real-ESRGAN (só o modelo realesrgan-x4plus, o que o agente usa).
NUNCA leva o config.env (chave da API e token), o agente.log nem os testes.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVOS = ["agente_imagens.py", "artes.py", "processar.py", "claude_imagens.py", "LEIA-ME.md",
            "config.env.exemplo", "instalar.bat", "iniciar_agente.bat"]
ESRGAN_ARQUIVOS = ["realesrgan-ncnn-vulkan.exe", "vcomp140.dll", "vcomp140d.dll",
                   "models/realesrgan-x4plus.bin", "models/realesrgan-x4plus.param"]
PROIBIDOS = ("config.env", "agente.log")


def achar_esrgan() -> Path:
    for p in (PASTA / "realesrgan", Path.home() / ".claude/skills/imagem-anuncio/ferramentas/realesrgan"):
        if (p / "realesrgan-ncnn-vulkan.exe").is_file():
            return p
    sys.exit("Real-ESRGAN não encontrado para pôr no pacote.")


def main() -> None:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "Instalador_Agente_Imagens_Noromak.zip"
    esrgan = achar_esrgan()
    raiz = "AgenteImagensNoromak"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for nome in ARQUIVOS:
            assert nome not in PROIBIDOS
            z.write(PASTA / nome, f"{raiz}/{nome}")
        for nome in ESRGAN_ARQUIVOS:
            origem = esrgan / nome
            if origem.is_file():
                z.write(origem, f"{raiz}/realesrgan/{nome}")
        nomes = z.namelist()
    if any(Path(n).name in PROIBIDOS for n in nomes):
        destino.unlink()
        sys.exit("Pacote recusado: tinha arquivo com segredo.")
    print(f"OK {destino} ({destino.stat().st_size / 1_048_576:.1f} MB, {len(nomes)} arquivos)")


if __name__ == "__main__":
    main()
