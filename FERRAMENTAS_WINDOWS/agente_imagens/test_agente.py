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
        # V20.17: arte pela referência — lista de respostas da conferência (True/False), uma por volta.
        self.artes_ok = None
        # V20.17: pesquisa na internet — quantas vezes a API devolve pause_turn e se recusa a 1ª versão das ferramentas.
        self.pausas = 0
        self.recusar_ferramentas = False
        self.ferramentas_usadas: list[str] = []
        self.ultimo_texto_pedido = ""
        self.chamadas: list[str] = []
        self.messages = self

    def create(self, **kw):
        if self.erro:
            raise self.erro
        if "tools" in kw:
            self.ferramentas_usadas.append(kw["tools"][0]["type"])
            if self.recusar_ferramentas:
                self.recusar_ferramentas = False
                BadRequestError = type("BadRequestError", (Exception,), {})
                raise BadRequestError("tool type not supported for this model")
            self.chamadas.append("pesquisa")
            uso = SimpleNamespace(input_tokens=20000, output_tokens=400,
                                  server_tool_use=SimpleNamespace(web_search_requests=2))
            if self.pausas:
                self.pausas -= 1
                return SimpleNamespace(stop_reason="pause_turn", usage=uso, content=[SimpleNamespace(type="text", text="")])
            cit = SimpleNamespace(url="https://fornecedor.com.br/43-272")
            return SimpleNamespace(stop_reason="end_turn", usage=uso, content=[SimpleNamespace(
                type="text", text="- Rosca da haste M16x1,5 — https://fornecedor.com.br/43-272\n"
                                  "- Curso de 64 mm — https://catalogo.exemplo.com/p/43272", citations=[cit])])
        self.ultimo_texto_pedido = " ".join(c.get("text", "") for c in kw["messages"][0]["content"]
                                            if isinstance(c, dict) and c.get("type") == "text")
        self.imagens_pedidas = sum(1 for c in kw["messages"][0]["content"] if isinstance(c, dict) and c.get("type") == "image")
        props = kw["output_config"]["format"]["schema"]["properties"]
        conteudo = kw["messages"][0]["content"]
        if "html" in props:
            assert kw["output_config"]["effort"] == "medium"
            self.chamadas.append("html")
            self.ultimo_html_pedido = " ".join(c.get("text", "") for c in conteudo if c["type"] == "text")
            dados = {"html": '<html><head><script>alert(1)</script><style>body{width:1200px;height:1200px;margin:0;'
                             'background:#f4f7fa}</style></head><body><h1>OBSERVACOES</h1>'
                             '<img src="produto.png" style="height:900px"><img src="https://fora.com/x.png"></body></html>'}
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=3000, output_tokens=2000),
                                   content=[SimpleNamespace(type="text", text=json.dumps(dados))])
        assert kw["output_config"]["effort"] == "low"
        if "itens" in props:
            self.chamadas.append("textos_ref")
            dados = {"serve": True, "motivo": "", "titulo": "Observações", "cor": "#e30613", "itens": [
                {"rotulo": "Conteúdo", "valor": "500 g", "origem": "rotulo"},
                {"rotulo": "Rosca", "valor": "", "origem": "nao_encontrado"}]}
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                                   content=[SimpleNamespace(type="text", text=json.dumps(dados))])
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
                {"chave": "subtitulo", "valor": "Curso de 64 mm", "origem": "internet",
                 "fonte": "https://catalogo.exemplo.com/p/43272"},
                {"chave": "beneficio_1", "valor": "Hidrata | o couro", "origem": "rotulo"},
                {"chave": "beneficio_2", "valor": "Protege", "origem": "rotulo"},
                {"chave": "cor", "valor": "#d0021b", "origem": "rotulo"}]}
        elif "problemas" in props:
            self.chamadas.append("arte")
            ok = self.artes_ok.pop(0) if self.artes_ok else self.arte_ok
            dados = {"ok": ok, "problemas": [] if ok else ["Título cortado na borda"]}
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


class _BaseAgente(TesteNoromak):
    """Auxiliares comuns (sem testes)."""

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


class AgenteDeImagens(_BaseAgente):

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


class CatalogoDoProduto(_BaseAgente):
    """V20.21 (dono, 04/11): print de catálogo / ficha técnica é fonte de DADOS. Entra só na proposta de textos
    (origem "catalogo"), nunca como foto do produto na arte nem como layout."""

    def enviar_com_catalogo(self, referencia: bool = False) -> int:
        corpo = {"tipo": "aplicacao", "sku": "816.4003-0", "fotos": [{"dados": base64.b64encode(frasco()).decode()}],
                 "catalogo": [{"dados": base64.b64encode(frasco((500, 300), fundo=(240, 240, 240))).decode()}]}
        if referencia:
            corpo["referencia"] = {"dados": base64.b64encode(frasco((400, 400), fundo=(20, 40, 90))).decode()}
        st, r = self.cliente.pedir("POST", "/api/imagens/enviar", corpo)
        self.assertEqual(st, 200, r)
        return r["ids"][0]

    def test_catalogo_vai_para_a_proposta_de_textos(self):
        self.enviar_com_catalogo()
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        self.assertEqual(falso.chamadas, ["textos"])
        self.assertEqual(falso.imagens_pedidas, 2)                       # a foto + o catálogo
        self.assertIn("CATÁLOGO", falso.ultimo_texto_pedido)
        self.assertIn("caractere por caractere", falso.ultimo_texto_pedido)

    def test_catalogo_com_referencia(self):
        self.enviar_com_catalogo(referencia=True)
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        self.assertEqual(falso.chamadas, ["textos_ref"])
        self.assertEqual(falso.imagens_pedidas, 3)                       # referência + foto + catálogo
        self.assertIn("CATÁLOGO", falso.ultimo_texto_pedido)

    def test_catalogo_nao_e_foto_do_produto(self):
        id_ = self.enviar_com_catalogo(referencia=True)
        t = self.noromak.proximo()
        ag = self.agente(ClaudeFalso())
        self.assertEqual([a["papel"] for a, _ in ag._originais(t)], ["frente"])
        self.assertEqual(len(ag._catalogo(t)), 1)
        self.assertEqual(t["id"], id_)

    def test_origem_catalogo_e_aceita(self):
        from claude_imagens import _campo
        c = _campo("area_1", "Área 1", {"valor": "Reboques", "origem": "catalogo"})
        self.assertEqual(c["origem"], "catalogo")


class ArteComReferencia(_BaseAgente):
    """V20.17 (dono, 04/11): imagem de referência = o agente copia o LAYOUT dela. Os textos seguem a estrutura
    da referência, mas só com o que está no nosso produto; o HTML que o Claude escreve passa pela limpeza."""

    def enviar_com_ref(self, tipo="beneficios") -> int:
        st, corpo = self.cliente.pedir("POST", "/api/imagens/enviar", {
            "tipo": tipo, "sku": "43-272", "fotos": [{"dados": base64.b64encode(frasco()).decode()}],
            "referencia": {"dados": base64.b64encode(frasco((400, 400), fundo=(20, 40, 90))).decode()}})
        self.assertEqual(st, 200, corpo)
        return corpo["ids"][0]

    def test_textos_na_estrutura_da_referencia_sem_inventar(self):
        id_ = self.enviar_com_ref()
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "textos_para_aprovar")
        self.assertEqual(falso.chamadas, ["textos_ref"])
        campos = {c["chave"]: c for c in it["textos"]["campos"]}
        self.assertEqual(campos["titulo"]["valor"], "Observações")
        self.assertEqual((campos["item_1"]["valor"], campos["item_1"]["rotulo"]), ("Conteúdo | 500 g", "Item 1 — Conteúdo"))
        self.assertEqual((campos["item_2"]["valor"], campos["item_2"]["origem"]), ("", "nao_encontrado"))
        self.assertEqual(campos["cor"]["valor"], "#e30613")

    def test_arte_pelo_layout_da_referencia(self):
        if not P.achar_navegador():
            self.skipTest("Sem Chrome/Edge neste PC")
        id_ = self.enviar_com_ref()
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        it = self.item(id_)
        campos = it["textos"]["campos"]
        next(c for c in campos if c["chave"] == "item_2")["valor"] = "Rosca | M16x1,5"
        self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": campos})
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual(falso.chamadas, ["textos_ref", "html", "arte"])
        self.assertIn("Rosca | M16x1,5", falso.ultimo_html_pedido)      # o texto aprovado vai para o Claude
        # Caso real 04/11: referência azul-escuro virou arte cinza (a regra "fundo claro atrás" brigava com as cores
        # da referência). Agora: cores da referência em tudo, a foto de fundo branco num cartão claro.
        self.assertIn("MESMAS cores", falso.ultimo_html_pedido)
        self.assertIn("cartão", falso.ultimo_html_pedido)
        self.assertNotIn("fundo claro atrás", falso.ultimo_html_pedido)
        self.assertEqual(self.resultado(id_).size, (1200, 1200))

    def test_arte_com_problema_e_refeita_uma_vez(self):
        if not P.achar_navegador():
            self.skipTest("Sem Chrome/Edge neste PC")
        id_ = self.enviar_com_ref("aplicacao")
        falso = ClaudeFalso()
        falso.artes_ok = [False, True]
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.cliente.pedir("POST", "/api/imagens/textos", {"id": id_, "versao": it["versao"], "campos": it["textos"]["campos"]})
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "pronta", it["mensagem"])
        self.assertEqual(falso.chamadas, ["textos_ref", "html", "arte", "html", "arte"])
        self.assertIn("Título cortado na borda", falso.ultimo_html_pedido)  # a 2ª volta recebe o problema
        self.assertGreater(self.cliente.pedir("GET", "/api/imagens")[1]["saldo"]["usado"], 0)  # gasto entra no crédito

    def test_sem_referencia_continua_com_os_modelos_fixos(self):
        id_ = self.enviar("modo", [frasco()])
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        self.assertEqual(falso.chamadas, ["textos"])


class PesquisaNaInternet(_BaseAgente):
    """V20.17 (dono, 04/11): nome do produto / link do fornecedor = o agente pesquisa na internet antes de propor
    os textos. O que vem de lá fica marcado "internet" com a fonte; o operador aprova; as buscas entram no crédito."""

    def enviar_com_produto(self, nome="Câmara de freio 43-272", link="https://fornecedor.com.br/43-272") -> int:
        st, corpo = self.cliente.pedir("POST", "/api/imagens/enviar", {
            "tipo": "beneficios", "fotos": [{"dados": base64.b64encode(frasco()).decode()}],
            "produto_nome": nome, "produto_link": link})
        self.assertEqual(st, 200, corpo)
        return corpo["ids"][0]

    def test_pesquisa_alimenta_os_textos_com_a_fonte(self):
        id_ = self.enviar_com_produto()
        falso = ClaudeFalso()
        falso.pausas = 1                                   # busca longa: a API devolve a vez uma vez
        self.agente(falso).uma_volta()
        self.assertEqual(falso.chamadas, ["pesquisa", "pesquisa", "textos"])
        self.assertIn("INFORMAÇÕES PESQUISADAS NA INTERNET", falso.ultimo_texto_pedido)
        self.assertIn("M16x1,5", falso.ultimo_texto_pedido)
        it = self.item(id_)
        sub = next(c for c in it["textos"]["campos"] if c["chave"] == "subtitulo")
        self.assertEqual((sub["origem"], sub["fonte"]), ("internet", "https://catalogo.exemplo.com/p/43272"))
        tit = next(c for c in it["textos"]["campos"] if c["chave"] == "titulo")
        self.assertEqual(tit["fonte"], "")                 # do rótulo: sem fonte
        self.assertIn("https://fornecedor.com.br/43-272", it["relatorio"]["fontes"])
        self.assertIn("M16x1,5", it["relatorio"]["pesquisa"])
        # 2 chamadas × 2 buscas = 4 buscas × US$ 0,01 no crédito, além dos tokens.
        self.assertGreaterEqual(self.cliente.pedir("GET", "/api/imagens")[1]["saldo"]["usado"], 0.04)

    def test_haiku_comeca_pela_versao_basica_e_troca_se_recusada(self):
        self.enviar_com_produto(link="")
        falso = ClaudeFalso()
        falso.recusar_ferramentas = True
        self.agente(falso).uma_volta()
        self.assertEqual(falso.ferramentas_usadas, ["web_search_20250305", "web_search_20260209"])
        self.assertEqual(falso.chamadas, ["pesquisa", "textos"])

    def test_pesquisa_que_falha_nao_trava_os_textos(self):
        id_ = self.enviar_com_produto()
        falso = ClaudeFalso()
        falso.recusar_ferramentas = True

        def sempre_recusa(**kw):
            if "tools" in kw:
                raise type("BadRequestError", (Exception,), {})("no")
            return ClaudeFalso.create(falso, **kw)
        falso.create = sempre_recusa
        self.agente(falso).uma_volta()
        it = self.item(id_)
        self.assertEqual(it["situacao"], "textos_para_aprovar")
        self.assertIn("pesquisa na internet falhou", it["relatorio"]["aviso_pesquisa"])
        self.assertNotIn("INFORMAÇÕES PESQUISADAS", falso.ultimo_texto_pedido)

    def test_sem_nome_nem_link_nao_pesquisa(self):
        self.enviar("modo", [frasco()])
        falso = ClaudeFalso()
        self.agente(falso).uma_volta()
        self.assertEqual(falso.chamadas, ["textos"])


class RobustezDaRede(_BaseAgente):
    """Revisão antes de subir (04/11): queda do túnel/rede no meio do trabalho perdia o resultado (a entrega e o "erro"
    falhavam calados) e o trabalho era refeito até 3 vezes, gastando crédito. Agora a entrega é tentada de novo e, se a
    rede não voltar, o trabalho NÃO vira "erro": a reserva vence e ele volta à fila."""

    def setUp(self):
        super().setUp()
        self._esperas = A.ESPERAS_REENTREGA
        A.ESPERAS_REENTREGA = [0, 0, 0]
        self.addCleanup(setattr, A, "ESPERAS_REENTREGA", self._esperas)

    def _noromak_que_cai(self, quedas: int):
        import urllib.error
        real = self.noromak
        estado = {"quedas": quedas}

        class Instavel(A.Noromak):
            def resultado(self, corpo):
                if estado["quedas"]:
                    estado["quedas"] -= 1
                    raise urllib.error.URLError("conexão recusada (túnel caiu)")
                return real.resultado(corpo)
        return Instavel(real.url, real.token)

    def test_queda_passageira_reentrega_sem_refazer(self):
        id_ = self.enviar()
        falso = ClaudeFalso()
        ag = A.Agente(self._noromak_que_cai(2), lambda m: ClaudeImagens(falso, m), "nao-existe")
        ag.uma_volta()
        self.assertEqual(self.item(id_)["situacao"], "pronta")
        self.assertEqual(falso.chamadas, ["foto"])                 # o Claude não foi chamado de novo

    def test_rede_que_nao_volta_nao_vira_erro(self):
        id_ = self.enviar()
        ag = A.Agente(self._noromak_que_cai(99), lambda m: ClaudeImagens(ClaudeFalso(), m), "nao-existe")
        ag.uma_volta()
        self.assertEqual(self.item(id_)["situacao"], "processando")  # a reserva vence e volta à fila

    def test_portao_de_login_nao_e_seguido(self):
        import http.server
        import threading
        visitas = []

        class Portao(http.server.BaseHTTPRequestHandler):
            def _responder(self):
                visitas.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/login"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<html>login</html>")
                    return
                self.send_response(302)
                self.send_header("Location", "/login?next=/expedicao/")
                self.end_headers()
            do_GET = do_POST = _responder

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Portao)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            n = A.Noromak(f"http://127.0.0.1:{srv.server_address[1]}/expedicao", "token-secreto")
            with self.assertRaises(A.NoromakErro) as c:
                n.ping()
        finally:
            srv.shutdown()
        self.assertIn("portão de login", str(c.exception))
        self.assertFalse(any(p.startswith("/login") for p, _ in visitas))   # o token não vai para a tela de login

    def test_pesquisa_reaproveitada_entre_referencias(self):
        # Várias referências (V20.19) = vários trabalhos com o mesmo nome/link: a pesquisa é feita uma vez só.
        for _ in range(2):
            self.cliente.pedir("POST", "/api/imagens/enviar", {
                "tipo": "beneficios", "fotos": [{"dados": base64.b64encode(frasco()).decode()}],
                "produto_nome": "Câmara de freio 43-272"})
        falso = ClaudeFalso()
        ag = self.agente(falso)
        ag.uma_volta()
        ag.uma_volta()
        self.assertEqual(falso.chamadas.count("pesquisa"), 1)
        self.assertEqual(falso.chamadas.count("textos"), 2)


class TunelEInstancia(unittest.TestCase):

    def test_porta_ja_aberta_e_reaproveitada_sem_novo_ssh(self):
        import socket
        ouvinte = socket.socket()
        ouvinte.bind(("127.0.0.1", 0))
        ouvinte.listen(16)   # as checagens conectam e ninguem aceita: fila folgada
        t = A.Tunel("expedicao", porta_local=ouvinte.getsockname()[1])
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia abrir outro ssh"))
        try:
            t.garantir()
            t.garantir()
        finally:
            A.subprocess.Popen = original
            ouvinte.close()

    def test_ssh_que_falha_espera_mais_a_cada_vez(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            porta = s.getsockname()[1]                    # porta livre: nada ouvindo
        chamadas = []

        class SshQueSai:
            def __init__(self, cmd, **k):
                chamadas.append(k)
                self.returncode = 255

            def poll(self):
                return 255
        t = A.Tunel("expedicao", porta_local=porta)
        original = A.subprocess.Popen
        A.subprocess.Popen = SshQueSai
        try:
            with self.assertRaises(OSError):
                t.garantir()
            with self.assertRaises(OSError):
                t.garantir()                              # dentro da espera: nem tenta (sem login repetido na VPS)
        finally:
            A.subprocess.Popen = original
        self.assertEqual(len(chamadas), 1)
        self.assertNotEqual(chamadas[0].get("stderr"), A.subprocess.PIPE)   # erro do ssh vai para arquivo, não pipe
        self.assertNotIn("creationflags", {k for k, v in chamadas[0].items() if v})  # morre junto com o console

    def test_uma_instancia_por_pc(self):
        import tempfile
        caminho = Path(tempfile.mkdtemp()) / "agente.lock"
        primeira = A.trava_instancia(caminho)
        self.assertIsNotNone(primeira)
        self.assertIsNone(A.trava_instancia(caminho))
        primeira.close()
        self.assertIsNotNone(A.trava_instancia(caminho))


class LimpezaDoHtml(unittest.TestCase):
    def test_tira_script_evento_e_endereco_de_fora(self):
        import artes
        h = artes.limpar_html('<html><head><script>x()</script><link rel="stylesheet" href="https://fora.com/a.css">'
                              '<style>@import url("https://fonts.googleapis.com/css2?family=Anton");@import url("https://fora.com/b.css");'
                              '.a{background:url(https://fora.com/c.png)}</style></head><body onload="x()">'
                              '<img src="produto.png"><img src="https://fora.com/d.png"><a href="javascript:x()">a</a></body></html>')
        self.assertNotIn("script", h.lower())
        self.assertNotIn("fora.com", h)
        self.assertNotIn("onload", h)
        self.assertNotIn("javascript", h)
        self.assertIn("fonts.googleapis.com", h)
        self.assertIn('src="produto.png"', h)

    def test_sem_a_foto_do_produto_e_recusado(self):
        import artes
        with self.assertRaises(ValueError):
            artes.limpar_html("<html><body><h1>Sem produto</h1></body></html>")


class LimpezaDoHtmlTruques(unittest.TestCase):
    """Revisão antes de subir (04/11): 5 formas de passar pela limpeza antiga, achadas rodando a função."""

    def limpar(self, html: str) -> str:
        import artes
        return artes.limpar_html('<html><body><img src="produto.png">' + html + '</body></html>')

    def test_evento_com_barra(self):
        h = self.limpar('<svg/onload=fetch("http://10.1.15.1/")></svg>')
        self.assertNotIn("onload", h.lower())

    def test_script_remontado(self):
        h = self.limpar('<scr<script>ipt>fetch(1)</script x>')
        self.assertNotIn("<script", h.lower())

    def test_atributo_sem_aspas_srcset_e_file(self):
        h = self.limpar('<img src=https://fora.com/t.png><img srcset="file:///C:/Users/x.png 1x">')
        self.assertNotIn("fora.com", h)
        self.assertNotIn("srcset", h.lower())
        self.assertNotIn("file:", h.lower())

    def test_image_set_no_css(self):
        h = self.limpar("<style>.a{background:image-set('https://fora.com/a.png' 1x)}</style>")
        self.assertNotIn("fora.com", h)

    def test_link_com_fontes_escondidas_em_outro_atributo(self):
        h = self.limpar('<link rel=stylesheet href=https://fora.com/a.css data-x=fonts.googleapis.com>')
        self.assertNotIn("fora.com", h)


@unittest.skipUnless(P.achar_navegador(), "Sem Chrome/Edge neste PC")
class ChromeTrancado(unittest.TestCase):
    """Mesmo que algo passe pela limpeza, o Chrome que monta a arte não roda JavaScript, não lê arquivo do PC
    e não acessa a rede (só as fontes do Google)."""

    def test_javascript_desligado(self):
        img = P.renderizar('<html><body style="margin:0;width:1200px;height:1200px;background:#fff">'
                           '<script>document.body.style.background="#f00"</script></body></html>', {})
        self.assertEqual(img.getpixel((600, 600)), (255, 255, 255))

    def test_nao_le_arquivo_do_pc(self):
        import tempfile
        pasta = Path(tempfile.mkdtemp())
        vermelho = pasta / "segredo.png"
        Image.new("RGB", (50, 50), (255, 0, 0)).save(vermelho)
        img = P.renderizar(f'<html><body style="margin:0;background:#fff"><img src="{vermelho.as_uri()}" '
                           'style="width:1200px;height:1200px"></body></html>', {})
        self.assertNotEqual(img.getpixel((600, 600)), (255, 0, 0))

    def test_nao_acessa_a_rede(self):
        import http.server
        import threading
        chamadas = []

        class Espiao(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                chamadas.append(self.path)
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Espiao)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            porta = srv.server_address[1]
            P.renderizar(f'<html><body><img src="http://127.0.0.1:{porta}/vazou.png">'
                         f'<img src="http://localhost:{porta}/vazou2.png"></body></html>', {})
        finally:
            srv.shutdown()
        self.assertEqual(chamadas, [])

    def test_imagem_do_produto_continua_aparecendo(self):
        b = io.BytesIO()
        Image.new("RGB", (100, 100), (0, 0, 255)).save(b, "PNG")
        img = P.renderizar('<html><body style="margin:0;background:#fff"><img src="produto.png" '
                           'style="width:1200px;height:1200px"></body></html>', {"produto.png": b.getvalue()})
        self.assertEqual(img.getpixel((600, 600)), (0, 0, 255))


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
