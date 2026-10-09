"""V18.83 — peças iguais em todas as telas: tema claro/escuro, avisos, confirmação e tabela.

Só front-end, mas com regras que não podem quebrar sem ninguém ver:
- a Conferência NÃO troca o alert() por aviso que some sozinho: lá o alert() trava o leitor de
  código de barras até o operador ler (bip errado não pode passar batido);
- toda tela carrega o nk-ui.js no <head> sem defer (senão o modo escuro pisca branco);
- o bloco gerado do modo escuro é refeito pelo gerador sem duplicar e sem tocar no modo claro;
- os confirm() trocados no Admin estão dentro de função async (await fora de async derruba o
  script inteiro do Admin — a tela não abriria).
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from base import RAIZ

APP = RAIZ / "APP"


def _dividir(seletor: str) -> list[str]:
    """Divide "a, b:is(c, d)" nas vírgulas de fora dos parênteses."""
    partes, nivel, atual = [], 0, ""
    for ch in seletor:
        nivel += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and nivel == 0:
            partes.append(atual)
            atual = ""
        else:
            atual += ch
    return partes + [atual]
TELAS = sorted(p for p in APP.glob("*.html"))


class PecasComuns(unittest.TestCase):

    def test_toda_tela_carrega_o_nk_ui_no_head_sem_defer(self):
        for tela in TELAS:
            html = tela.read_text(encoding="utf-8")
            cabeca = html.split("</head>", 1)[0]
            m = re.search(r'<script([^>]*)src="/expedicao/nk-ui\.js\?v=[\d.]+"([^>]*)>', cabeca)
            self.assertIsNotNone(m, f"{tela.name} sem o nk-ui.js no <head>")
            self.assertNotIn("defer", m.group(1) + m.group(2), f"{tela.name}: nk-ui.js com defer faz o tema escuro piscar")
            self.assertIn("nk-ui.css?v=", cabeca, f"{tela.name} sem o nk-ui.css")

    def test_conferencia_mantem_o_alert_que_trava_o_leitor(self):
        html = (APP / "conferencia.html").read_text(encoding="utf-8")
        tag = re.search(r'<script[^>]*nk-ui\.js[^>]*>', html).group(0)
        self.assertNotIn("data-alertas", tag, "a Conferência não pode trocar o alert() por aviso que some sozinho")
        estacao = (APP / "estacao-impressao.html").read_text(encoding="utf-8")
        self.assertNotIn("data-alertas", re.search(r'<script[^>]*nk-ui\.js[^>]*>', estacao).group(0))

    def test_admin_usa_avisos_e_confirmacao_padrao(self):
        html = (APP / "admin.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'<script src="/expedicao/nk-ui\.js\?v=[\d.]+" data-alertas="toast"></script>')
        # Só o confirm do "atualizar o banco de produtos" fica nativo: o retorno dele é usado na hora.
        nativos = re.findall(r"(?<![\w.])confirm\(", html)
        self.assertEqual(len(nativos), 1, "apareceu confirm() nativo novo no Admin — use await nkConfirmar()")

    def test_await_nkconfirmar_sempre_dentro_de_funcao_async(self):
        html = (APP / "admin.html").read_text(encoding="utf-8")
        linhas = html.split("\n")
        for i, linha in enumerate(linhas):
            if "await nkConfirmar(" not in linha:
                continue
            cabecalho = ""
            for j in range(i, max(i - 80, -1), -1):
                m = re.search(r"(async\s+function\s*\w*\s*\(|=\s*async\s+function\s*\(|async\s*\([^)]*\)\s*=>|(?<!async )function\s+\w+\s*\(|=\s*function\s*\()", linhas[j])
                if m:
                    cabecalho = m.group(0)
                    break
            self.assertIn("async", cabecalho, f"admin.html linha {i + 1}: await nkConfirmar fora de função async")

    def test_paginas_com_menu_tem_modo_escuro(self):
        for nome in ("admin", "precificador", "conferencia", "analise-full", "promocoes-auto", "acesso"):
            html = (APP / f"{nome}.html").read_text(encoding="utf-8")
            self.assertIn('<style id="escuro-auto-v1883">', html, f"{nome}.html sem o bloco gerado do modo escuro")
        for nome in ("compras", "promocoes", "estacao-impressao", "financeiro", "kits-analise", "saude"):
            self.assertIn('<style id="escuro-v1883">', (APP / f"{nome}.html").read_text(encoding="utf-8"))

    def test_regras_escuras_so_valem_no_modo_escuro(self):
        """Nenhuma regra dos blocos de modo escuro pode mudar o modo claro (o padrão dos operadores)."""
        for tela in TELAS:
            html = tela.read_text(encoding="utf-8")
            for bloco in re.findall(r'<style id="escuro[^"]*">(.*?)</style>', html, re.S):
                sem_comentario = re.sub(r"/\*.*?\*/", "", bloco, flags=re.S)
                for sel in re.findall(r"(?:^|})\s*([^{}@]+)\{", sem_comentario):
                    for parte in _dividir(sel):
                        parte = parte.strip()
                        if parte:
                            self.assertTrue(parte.startswith('html[data-tema="escuro"]'),
                                            f"{tela.name}: regra do modo escuro sem o prefixo: {parte[:80]}")

    def test_gerador_e_idempotente(self):
        gerador = RAIZ / "FERRAMENTAS_WINDOWS" / "gerar_modo_escuro.py"
        with tempfile.TemporaryDirectory() as d:
            alvo = Path(d) / "tela.html"
            alvo.write_text('<html><head><style>.card{background:#fff;color:#18324A;border:1px solid #DCE5EF}'
                            '.ok{background:#E7F4EC;color:#1B7A3E}.btn{background:#1F57A5;color:#fff}</style></head>'
                            '<body><div style="background:#FDECEA;color:#B42318">x</div></body></html>', encoding="utf-8")
            for _ in range(2):
                subprocess.run([sys.executable, str(gerador), str(alvo)], check=True, capture_output=True)
            html = alvo.read_text(encoding="utf-8")
        self.assertEqual(html.count('<style id="escuro-auto-v1883">'), 1, "rodar o gerador duas vezes duplicou o bloco")
        self.assertIn('html[data-tema="escuro"] .card{', html)
        self.assertIn('html[data-tema="escuro"] .ok{', html)
        self.assertNotIn('html[data-tema="escuro"] .btn{', html, "botão azul com texto branco não deve ser convertido")
        self.assertIn('[style*="background:#FDECEA" i]', html)

    def test_quase_branco_com_tom_vira_superficie_neutra(self):
        """V18.87: o Precificador usa fundos quase brancos esverdeados (#F7FAF4...) que viravam
        verde-oliva no modo escuro. Quase branco é superfície; selo colorido mantém o tom."""
        sys.path.insert(0, str(RAIZ / "FERRAMENTAS_WINDOWS"))
        import gerar_modo_escuro as G
        import colorsys
        for clara in ("#F7FAF4", "#FBFDF7", "#F4F8EF", "#FFFFFF"):
            r, g, b = (int(G.fundo_escuro(clara)[i:i + 2], 16) / 255 for i in (1, 3, 5))
            _, _, s = colorsys.rgb_to_hls(r, g, b)
            self.assertFalse(g > r + 0.03 and g > b, f"{clara} virou verde no modo escuro: {G.fundo_escuro(clara)}")
        r, g, b = (int(G.fundo_escuro("#D8F0E0")[i:i + 2], 16) / 255 for i in (1, 3, 5))
        self.assertGreater(g, r, "selo verde de verdade deve continuar esverdeado")

    def test_menu_tem_tema_e_recolher(self):
        js = (APP / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn("data-nk-tema", js)
        self.assertIn("data-nk-recolher", js)
        self.assertIn('data-shell="recolhido"', (APP / "precificador.html").read_text(encoding="utf-8"),
                      "o Precificador precisa começar com o menu recolhido (largura)")

    def test_nk_ui_sem_caractere_invisivel(self):
        """O marcador de UTF-8 da planilha vai como \\uFEFF, não como caractere invisível no código."""
        self.assertNotIn(chr(0xFEFF), (APP / "nk-ui.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
