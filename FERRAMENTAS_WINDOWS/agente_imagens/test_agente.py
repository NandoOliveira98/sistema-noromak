"""V20.17 — testes do agente de imagens, de ponta a ponta contra o servidor do Noromak (banco temporário,
igual à suíte TESTES/) e com o Claude SIMULADO (nenhuma chamada à API, nenhum custo).

Rodar:  cd FERRAMENTAS_WINDOWS/agente_imagens && python -m unittest test_agente -v
Precisa do Pillow (o servidor não precisa). A arte usa o Chrome/Edge do PC; sem ele, o teste da arte pula.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
sys.path.insert(0, str(RAIZ / "TESTES"))
sys.path.insert(0, str(AQUI))

from base import S, TesteNoromak  # noqa: E402  (sobe o servidor com banco temporário)
from PIL import Image, ImageDraw  # noqa: E402

import agente_imagens as A  # noqa: E402
import processar as P  # noqa: E402
from claude_imagens import ClaudeImagens  # noqa: E402


# ---------- Claude simulado ----------

class ClaudeFalso:
    """Responde pelo esquema pedido. `faixas_deformadas` = índices (na ordem enviada) marcados deformados
    em cada chamada de conferência (lista de listas, uma por chamada)."""

    def __init__(self, serve=True, fundo_branco=True, produto_claro=True, faixas_deformadas=None, arte_ok=True, erro=None):
        self.serve, self.fundo_branco, self.produto_claro = serve, fundo_branco, produto_claro
        self.faixas_deformadas = list(faixas_deformadas or [])
        self.arte_ok, self.erro = arte_ok, erro
        self.chamadas: list[str] = []
        self.messages = self

    def create(self, **kw):
        if self.erro:
            raise self.erro
        props = kw["output_config"]["format"]["schema"]["properties"]
        assert kw["output_config"]["effort"] == "low"
        conteudo = kw["messages"][0]["content"]
        if "faixas" in props:
            self.chamadas.append("faixas")
            n = sum(1 for c in conteudo if c["type"] == "image")
            ruins = self.faixas_deformadas.pop(0) if self.faixas_deformadas else []
            dados = {"faixas": [{"indice": i, "tem_texto": True, "deformado": i in ruins, "trecho": f"texto {i}",
                                 "obs": "letra torta" if i in ruins else ""} for i in range(n)]}
        elif "campos" in props:
            self.chamadas.append("textos")
            dados = {"serve": True, "motivo": "", "campos": [
                {"chave": "titulo", "valor": "Hidratante de Couro", "origem": "rotulo"},
                {"chave": "beneficio_1", "valor": "Hidrata | o couro", "origem": "rotulo"},
                {"chave": "beneficio_2", "valor": "Protege", "origem": "rotulo"},
                {"chave": "cor", "valor": "#d0021b", "origem": "rotulo"}]}
        elif "problemas" in props:
            self.chamadas.append("arte")
            dados = {"ok": self.arte_ok, "problemas": [] if self.arte_ok else ["Título cortado na borda"]}
        else:
            self.chamadas.append("foto")
            dados = {"serve": self.serve, "motivo": "" if self.serve else "Produto cortado: mande a foto inteira.",
                     "fundo_branco": self.fundo_branco, "produto_claro": self.produto_claro}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type="text", text=json.dumps(dados))])


def frasco(tamanho=(300, 700), fundo=(255, 255, 255)) -> bytes:
    """Frasco branco com rótulo vermelho e 'letras' pretas, em fundo branco (o caso difícil do Würth)."""
    img = Image.new("RGB", tamanho, fundo)
    d = ImageDraw.Draw(img)
    w, h = tamanho
    d.rectangle((w * .3, h * .05, w * .7, h * .15), fill=(220, 30, 30))          # tampa
    d.rectangle((w * .15, h * .15, w * .85, h * .95), fill=(250, 250, 250), outline=(225, 225, 225))  # frasco branco
    d.rectangle((w * .15, h * .45, w * .85, h * .7), fill=(210, 20, 30))          # rótulo
    for i in range(6):
        d.rectangle((w * .2 + i * 25, h * .5, w * .2 + i * 25 + 15, h * .55), fill=(10, 10, 10))
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


class AgenteDeImagens(TesteNoromak):

    def setUp(self):
        super().setUp()
        import tempfile
        log_antigo = A.LOG
        A.LOG = Path(tempfile.mkdtemp()) / "agente.log"   # o log dos testes não vai para a pasta do agente
        self.addCleanup(setattr, A, "LOG", log_antigo)
        self.token = S.imagem_agente_token()
        self.noromak = A.Noromak(self.url_base, self.token)

    def agente(self, falso: ClaudeFalso, esrgan: str = "nao-existe") -> A.Agente:
        return A.Agente(self.noromak, lambda modelo: ClaudeImagens(falso, modelo), esrgan)

    def enviar(self, tipo="principal", fotos=None, sku="0893600022") -> int:
        fotos = fotos or [frasco()]
        st, corpo = self.cliente.pedir("POST", "/api/imagens/enviar", {
            "tipo": tipo, "sku": sku, "fotos": [{"dados": base64.b64encode(f).decode()} for f in fotos]})
        self.assertEqual(st, 200, corpo)
        return corpo["ids"][0]

    def item(self, id_: int) -> dict:
        return next(x for x in self.cliente.pedir("GET", "/api/imagens")[1]["itens"] if x["id"] == id_)

    def resultado(self, id_: int) -> Image.Image:
        it = self.item(id_)
        nome = next(a["nome"] for a in it["resultados"] if a["papel"] == "resultado")
        return P.abrir(S.imagens_arquivo(id_, nome)[0])

    # ---------- foto principal ----------

    def test_foto_principal_em_fundo_branco_nao_come_o_produto_branco(self):
        id_ = self.enviar()
        falso = ClaudeFalso()
        self.assertTrue(self.agente(falso).uma_volta())
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual((it["modelo"], it["tokens_entrada"]), ("claude-haiku-5-5", 1000))
        img = self.resultado(id_)
        self.assertEqual(img.size, (1200, 1200))
        self.assertEqual(img.getpixel((5, 5)), (255, 255, 255))
        caixa = P.caixa_do_produto(img)
        self.assertGreaterEqual(caixa[3] - caixa[1], 1000)  # produto inteiro, ~85% da altura
        # A tampa (topo) e o frasco branco continuam lá: o branco do frasco não foi comido.
        self.assertLess(caixa[1], 120)
        self.assertFalse(self.agente(falso).uma_volta())    # fila vazia

    def test_foto_que_nao_serve_volta_com_o_motivo(self):
        id_ = self.enviar()
        self.agente(ClaudeFalso(serve=False)).uma_volta()
        it = self.item(id_)
        self.assertEqual((it["situacao"], it["mensagem"]), ("outra_foto", "Produto cortado: mande a foto inteira."))

    def test_produto_claro_fora_do_fundo_branco_pede_outra_foto(self):
        id_ = self.enviar(fotos=[frasco(fundo=(200, 215, 230))])
        self.agente(ClaudeFalso(fundo_branco=False, produto_claro=True)).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "outra_foto")
        self.assertIn("fundo branco", it["mensagem"])

    # ---------- conferência da ampliação por IA ----------

    def _ia_com_defeito(self, faixa_ruim: int):
        """Simula o Real-ESRGAN: devolve a ampliação comum com uma 'letra deformada' (mancha verde)
        dentro da faixa indicada."""
        original = P.ampliar

        def falso(img, pasta):
            alvo = (img.width * 4, img.height * 4)
            comum = P.ampliacao_comum(img, alvo)
            ia = comum.copy()
            caixa = P.caixa_do_produto(comum)
            y0, y1 = [(caixa[1] + a, caixa[1] + b) for a, b in P.faixas(caixa[3] - caixa[1])][faixa_ruim]
            ImageDraw.Draw(ia).rectangle((caixa[0] + 40, y0 + 20, caixa[2] - 40, y1 - 20), fill=(0, 200, 0))
            return P.Ampliacao(ia, comum, True)
        P.ampliar = falso
        self.addCleanup(setattr, P, "ampliar", original)

    def test_faixa_deformada_volta_para_a_ampliacao_comum(self):
        self._ia_com_defeito(2)
        id_ = self.enviar()
        falso = ClaudeFalso(faixas_deformadas=[[2], []])  # 1ª conferência acha a faixa 2; a 2ª confere só ela, ok
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual(falso.chamadas, ["foto", "faixas", "faixas"])
        textos = it["relatorio"]["textos"]
        self.assertEqual(len(textos), P.FAIXAS)
        self.assertEqual(next(t for t in textos if t["trecho"] == "texto 2")["obs"], "refeito pela ampliação comum")
        # A mancha verde (o "defeito" da IA) sumiu do resultado.
        img = self.resultado(id_)
        verdes = sum(1 for p in img.convert('RGB').getdata() if p[1] > 150 and p[0] < 80 and p[2] < 80)
        self.assertLess(verdes, 50)
        zooms = [a for a in it["resultados"] if a["papel"] == "zoom"]
        self.assertEqual(len(zooms), P.FAIXAS)

    def test_continua_deformado_vira_revisar(self):
        self._ia_com_defeito(1)
        id_ = self.enviar()
        self.agente(ClaudeFalso(faixas_deformadas=[[1], [0], [0]])).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "revisar")
        self.assertIn("confira os recortes", it["mensagem"])

    def test_kit_juntos_sai_em_fundo_branco_sem_texto(self):
        id_ = self.enviar("juntos", [frasco(), frasco((250, 500))])
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual(falso.chamadas, ["foto", "foto"])
        img = self.resultado(id_)
        self.assertEqual((img.size, img.getpixel((5, 600))), ((1200, 1200), (255, 255, 255)))

    # ---------- artes ----------

    def test_arte_em_duas_etapas_com_aprovacao_no_meio(self):
        if not P.achar_navegador():
            self.skipTest("Sem Chrome/Edge neste PC")
        id_ = self.enviar("beneficios", [frasco(), frasco()])
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "textos_para_aprovar")
        campos = it["textos"]["campos"]
        self.assertEqual(campos[0]["rotulo"], "Título (nome do produto)")
        # O que o Claude não achou vem vazio e marcado (nunca inventado).
        b3 = next(c for c in campos if c["chave"] == "beneficio_3")
        self.assertEqual((b3["valor"], b3["origem"]), ("", "nao_encontrado"))
        self.assertFalse(self.agente(falso).uma_volta())  # nada a fazer até aprovar
        b3["valor"], b3["origem"] = "Revitaliza | o couro", "operador"
        st, r = self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": campos})
        self.assertEqual(st, 200, r)
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual(self.resultado(id_).size, (1200, 1200))
        self.assertEqual(falso.chamadas, ["textos", "arte"])

    def test_arte_com_problema_vira_revisar_com_o_motivo(self):
        if not P.achar_navegador():
            self.skipTest("Sem Chrome/Edge neste PC")
        id_ = self.enviar("aplicacao", [frasco()])
        falso = ClaudeFalso(arte_ok=False)
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": it["textos"]["campos"]})
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual((it["situacao"], it["mensagem"]), ("revisar", "Título cortado na borda"))

    # ---------- erros ----------

    def test_erro_da_api_nao_prende_o_trabalho(self):
        class AuthenticationError(Exception):
            pass
        id_ = self.enviar()
        self.agente(ClaudeFalso(erro=AuthenticationError("401"))).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "erro")
        self.assertIn("Chave da API do Claude inválida", it["mensagem"])

    def test_token_errado_nao_entra(self):
        ruim = A.Agente(A.Noromak(self.url_base, "errado"), lambda m: None, "")
        with self.assertRaises(A.NoromakErro) as c:
            ruim.uma_volta()
        self.assertEqual(c.exception.status, 401)


class ArtesHtml(unittest.TestCase):
    def test_campo_vazio_nao_aparece_e_texto_e_escapado(self):
        import artes
        html = artes.montar("modo", [{"chave": "titulo", "valor": "Modo <b>de</b> uso"},
                                     {"chave": "passo_1", "valor": "Agite"}, {"chave": "passo_2", "valor": ""},
                                     {"chave": "nao_usar", "valor": ""}, {"chave": "cor", "valor": "vermelho"}], True)
        self.assertIn("Modo &lt;b&gt;de&lt;/b&gt; uso", html)
        self.assertEqual(html.count('class="passo"'), 1)
        self.assertNotIn("obs nao", html)
        self.assertIn("--c1:#1f57a5", html)  # cor inválida = cor padrão


if __name__ == "__main__":
    unittest.main()
