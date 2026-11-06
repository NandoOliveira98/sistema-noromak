"""V20.17 — Agente de imagens do Noromak (roda no PC do dono; decisões de 04/11).

Busca a fila do Estúdio de Imagens (/expedicao/imagens) a cada 10 s, processa e devolve:
- principal / juntos: analisa a foto (Claude), amplia se pequena (Real-ESRGAN), monta 1200×1200 em
  fundo branco, confere faixa a faixa se a IA deformou alguma letra (Claude) e troca a faixa
  deformada pela ampliação comum (até 2 voltas; depois disso "revisar").
- benefícios / modo de uso / aplicação: 1ª etapa o Claude lê o rótulo e PROPÕE os textos (o operador
  aprova no Noromak); 2ª etapa monta a arte com os textos aprovados, renderiza no Chrome e o Claude
  confere se nada ficou cortado.

Uso:  python agente_imagens.py            (fica rodando)
      python agente_imagens.py --uma-vez  (processa o que houver e sai)
Configuração em config.env (copie de config.env.exemplo). A chave da API fica SÓ aqui.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))

import artes  # noqa: E402
import processar as P  # noqa: E402
from claude_imagens import ClaudeImagens, ClaudeRecusou  # noqa: E402

VERSAO = "0.4"  # V20.31: etapa de anúncio
INTERVALO = 10
LOG = PASTA / "agente.log"
TUNEL_LOG = PASTA / "tunel.log"
TRAVA = PASTA / "agente.lock"
# Revisão de 04/11: queda do túnel/rede na entrega. Tenta de novo depois de cada espera (s); não voltou = o trabalho
# NÃO vira "erro" (a reserva de 10 min vence e ele volta à fila), para não perder o resultado já pago à toa.
ESPERAS_REENTREGA = [5, 20, 60]
PESQUISA_VALIDADE = 6 * 3600   # várias referências (V20.19) = vários trabalhos do mesmo produto: pesquisa uma vez só


def erro_de_rede(e: BaseException) -> bool:
    """Falha de conexão (túnel caiu, internet fora), não recusa do Noromak nem erro do trabalho."""
    return isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException)) \
        and not isinstance(e, urllib.error.HTTPError)


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """O portão de login do nginx responde 302 para /login: seguir mandaria o token do agente para a tela de login."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ABRIR_SEM_REDIRECIONAR = urllib.request.build_opener(_SemRedirecionar).open


def ler_config(caminho: Path = PASTA / "config.env") -> dict[str, str]:
    # Instalado em outro PC (instalar.bat): o Real-ESRGAN vem na própria pasta; no PC do dono, na pasta da skill.
    esrgan = PASTA / "realesrgan"
    if not (esrgan / "realesrgan-ncnn-vulkan.exe").is_file():
        esrgan = Path.home() / ".claude/skills/imagem-anuncio/ferramentas/realesrgan"
    cfg = {"NOROMAK_URL": "https://noromakweb.com.br/expedicao", "REALESRGAN_DIR": str(esrgan)}
    if caminho.is_file():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ANTHROPIC_API_KEY", "NOROMAK_URL", "NOROMAK_AGENTE_TOKEN", "REALESRGAN_DIR"):
        if os.getenv(k):
            cfg[k] = os.environ[k]
    return cfg


def registrar(texto: str) -> None:
    linha = f"{datetime.now():%d/%m %H:%M:%S} {texto}"
    print(linha, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


# ---------- Noromak ----------

class Noromak:
    def __init__(self, url: str, token: str, abrir: Callable | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self._abrir = abrir or _ABRIR_SEM_REDIRECIONAR

    def _pedir(self, metodo: str, rota: str, corpo: dict | None = None, bruto: bool = False):
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url + rota, data=dados, method=metodo)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "noromak-agente-imagens/" + VERSAO)
        try:
            with self._abrir(req, timeout=120) as r:
                texto = r.read()
                # O portão de login do NoromakWeb (nginx) manda para /login: não é a Expedição respondendo.
                if "/login" in (r.geturl() or "") and "/expedicao/api/" not in (r.geturl() or ""):
                    raise NoromakErro(302, PORTAO_LOGIN)
                return texto if bruto else json.loads(texto or b"{}")
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                destino = e.headers.get("Location", "") if e.headers else ""
                raise NoromakErro(302, PORTAO_LOGIN if "/login" in destino else f"Redirecionado para {destino}") from None
            try:
                msg = json.loads(e.read() or b"{}").get("error") or ""
            except Exception:
                msg = ""
            raise NoromakErro(e.code, msg or f"HTTP {e.code}") from None

    def ping(self) -> None:
        self._pedir("POST", "/api/imagens/agente/ping", {"versao": VERSAO, "computador": platform.node()})

    def proximo(self) -> dict | None:
        # V20.31: "anuncio=1" avisa o servidor que este agente sabe tratar o pedido de anúncio.
        return self._pedir("GET", "/api/imagens/agente/proximo?anuncio=1").get("trabalho")

    def arquivo(self, id_: int, nome: str) -> bytes:
        return self._pedir("GET", f"/api/imagens/agente/arquivo?id={id_}&nome={urllib.request.quote(nome)}", bruto=True)

    def analise(self, corpo: dict) -> None:
        self._pedir("POST", "/api/imagens/agente/analise", corpo)

    def resultado(self, corpo: dict) -> None:
        self._pedir("POST", "/api/imagens/agente/resultado", corpo)

    def anuncio(self, corpo: dict) -> None:  # V20.31
        self._pedir("POST", "/api/imagens/agente/anuncio", corpo)


PORTAO_LOGIN = ("O portão de login do NoromakWeb barrou o agente. Peça para liberar /expedicao/api/imagens/agente/ no "
                "nginx (PEDIDO_NGINX_AGENTE_IMAGENS.md) ou, no PC do dono, use NOROMAK_TUNEL=expedicao.")


class NoromakErro(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


# ---------- trabalho ----------

class Agente:
    def __init__(self, noromak: Noromak, criar_claude: Callable[[str], ClaudeImagens], pasta_esrgan: str):
        self.noromak = noromak
        self.criar_claude = criar_claude
        self.pasta_esrgan = pasta_esrgan
        self.ao_reconectar: Callable[[], None] | None = None   # reabre o túnel antes de tentar entregar de novo
        self._pesquisas: dict[tuple[str, str], tuple[float, dict]] = {}

    def uma_volta(self) -> bool:
        """Processa um trabalho, se houver. Devolve True se processou."""
        trabalho = self.noromak.proximo()
        if not trabalho:
            return False
        claude = self.criar_claude(trabalho.get("modelo") or "claude-haiku-5-5")
        inicio = time.time()
        etapa = trabalho.get("etapa")
        try:
            situacao = self._processar(trabalho, claude, etapa)
        except NoromakErro as e:
            situacao = f"não entregue ({e.status}: {e})"
        except Exception as e:
            if not erro_de_rede(e):
                raise
            # Sem conexão: não vira "erro" (o "erro" também não chegaria). A reserva vence e o trabalho volta à fila.
            situacao = f"sem conexão com o Noromak, volta à fila quando a reserva vencer ({e})"
        u = claude.uso
        registrar(f"#{trabalho['id']} {trabalho.get('tipo')}/{etapa} sku={trabalho.get('sku') or '-'} -> {situacao} | "
                  f"{u.modelo} {u.entrada}+{u.saida} tokens ~US$ {u.custo():.4f} | {time.time() - inicio:.0f}s")
        return True

    def _processar(self, trabalho: dict, claude: ClaudeImagens, etapa: str | None) -> str:
        try:
            if str(etapa or "").startswith("anuncio_"):  # V20.31: textos/atributos do anúncio
                corpo = (self.etapa_anuncio_atributos(trabalho, claude) if etapa == "anuncio_atributos"
                         else self.etapa_anuncio_textos(trabalho, claude))
                self._entregar(self.noromak.anuncio, corpo, claude)
            elif etapa == "analise":
                corpo = self.etapa_analise(trabalho, claude)
                self._entregar(self.noromak.analise, corpo, claude)
            else:
                corpo = self.etapa_arte(trabalho, claude) if etapa == "arte" else self.etapa_principal(trabalho, claude)
                self._entregar(self.noromak.resultado, corpo, claude)
            return corpo["situacao"]
        except NoromakErro:
            raise
        except Exception as e:  # o trabalho não pode ficar preso: volta como erro com o motivo
            if erro_de_rede(e):
                raise
            traceback.print_exc()
            msg = _mensagem_erro(e)
            entregar = (self.noromak.anuncio if str(etapa or "").startswith("anuncio_")
                        else self.noromak.analise if etapa == "analise" else self.noromak.resultado)
            try:
                self._entregar(entregar, {"id": trabalho["id"], "etapa": etapa, "situacao": "erro", "mensagem": msg}, claude)
            except Exception:
                pass
            return "erro: " + msg

    def _entregar(self, funcao: Callable, corpo: dict, claude: ClaudeImagens) -> None:
        corpo.update({"modelo": claude.modelo, "tokens_entrada": claude.uso.entrada, "tokens_saida": claude.uso.saida,
                      "buscas_web": claude.uso.buscas})
        for espera in [*ESPERAS_REENTREGA, None]:
            try:
                funcao(corpo)
                return
            except Exception as e:
                if espera is None or not erro_de_rede(e):
                    raise
                registrar(f"#{corpo.get('id')} entrega falhou ({e}); tentando de novo em {espera} s.")
                time.sleep(espera)
                if self.ao_reconectar:
                    try:
                        self.ao_reconectar()
                    except Exception:
                        pass

    def _pesquisar(self, claude: ClaudeImagens, nome: str, link: str) -> dict:
        chave = (nome.strip().lower(), link.strip())
        guardada = self._pesquisas.get(chave)
        if guardada and time.time() - guardada[0] < PESQUISA_VALIDADE:
            return guardada[1]
        pesquisa = claude.pesquisar(nome, link)
        self._pesquisas[chave] = (time.time(), pesquisa)
        return pesquisa

    def _originais(self, trabalho: dict) -> list[tuple[dict, Any]]:
        """Fotos do produto (a referência fica de fora: é de OUTRO anúncio; o catálogo também: é fonte de dados)."""
        return [(a, P.abrir(self.noromak.arquivo(trabalho["id"], a["nome"]))) for a in trabalho.get("arquivos") or []
                if a.get("papel") not in ("referencia", "catalogo")]

    @staticmethod
    def _pedido(trabalho: dict) -> str:
        """V20.21: "Como você quer a arte" (envio) + "Pedido ao refazer". Antes o refazer só chegava na ampliação
        da foto e a arte o ignorava."""
        partes = [str((trabalho.get("produto") or {}).get("pedido") or "").strip()]
        obs = str(trabalho.get("obs") or "").strip()
        if obs:
            partes.append("Ao refazer, o operador pediu: " + obs)
        return "\n".join(p for p in partes if p)

    def _catalogo(self, trabalho: dict) -> list:
        """V20.21: prints de catálogo / ficha técnica — só para ler os dados dos textos."""
        return [P.abrir(self.noromak.arquivo(trabalho["id"], a["nome"])) for a in trabalho.get("arquivos") or []
                if a.get("papel") == "catalogo"]

    def _referencia(self, trabalho: dict):
        a = next((x for x in trabalho.get("arquivos") or [] if x.get("papel") == "referencia"), None)
        return P.abrir(self.noromak.arquivo(trabalho["id"], a["nome"])) if a else None

    # ----- foto principal / kit -----
    def etapa_principal(self, trabalho: dict, claude: ClaudeImagens) -> dict:
        originais = self._originais(trabalho)
        relatorio: dict[str, Any] = {"textos": [], "avisos": []}
        zooms: list[tuple[str, Any]] = []
        prontas = []
        revisar = False
        for papel, img in originais:
            analise = claude.analisar_foto(img)
            if not analise.get("serve", True):
                return {"id": trabalho["id"], "situacao": "outra_foto",
                        "mensagem": analise.get("motivo") or "A foto não serve para a foto principal."}
            if not P.fundo_branco_liso(img) and (analise.get("produto_claro") or not analise.get("fundo_branco")):
                # Produto claro fora de fundo branco: o recorte come o produto (caso do Würth, 04/11).
                # Fundo que não é branco nem liso: o recorte automático não serve.
                return {"id": trabalho["id"], "situacao": "outra_foto",
                        "mensagem": "Mande a foto do produto em fundo branco liso: com este fundo o recorte automático "
                                    "corta o produto."}
            ampliada, faixas_rel, faixas_zoom, deu_certo = self.ampliar_e_conferir(img, claude, trabalho.get("obs") or "")
            relatorio["textos"] += faixas_rel
            zooms += faixas_zoom
            revisar = revisar or not deu_certo
            prontas.append(ampliada)
        final = P.montar_kit(prontas) if trabalho.get("tipo") == "juntos" else P.montar_principal(prontas[0])
        arquivos = [{"papel": "resultado", "dados": b64(P.jpg(final))}]
        arquivos += [{"papel": "zoom", "legenda": leg[:120], "dados": b64(P.jpg(z, 88))} for leg, z in zooms[:20]]
        msg = relatorio["avisos"] and " ".join(relatorio["avisos"]) or ""
        return {"id": trabalho["id"], "situacao": "revisar" if revisar else "pronta", "arquivos": arquivos,
                "relatorio": relatorio, "mensagem": (msg or ("A IA deformou letras que não consegui corrigir: confira os "
                                                             "recortes." if revisar else ""))}

    def ampliar_e_conferir(self, img, claude: ClaudeImagens, obs: str) -> tuple[Any, list, list, bool]:
        """Amplia (se pequena) e confere faixa a faixa. Devolve a imagem (com as faixas deformadas trocadas
        pela ampliação comum), o relatório, os recortes para a tela e se ficou tudo certo."""
        amp = P.ampliar(img, self.pasta_esrgan)
        if not amp.usou_ia:
            return amp.imagem, [], [], True
        caixa = P.caixa_do_produto(amp.comum) if P.fundo_branco_liso(amp.comum) else None
        y_ini, y_fim = (caixa[1], caixa[3]) if caixa else (0, amp.imagem.height)
        trechos = [(y_ini + a, y_ini + b) for a, b in P.faixas(y_fim - y_ini)]
        atual = amp.imagem
        trocadas: set[int] = set()
        resultado: list[dict] = []
        for volta in range(3):  # 1ª conferência + até 2 voltas de correção
            pendentes = [i for i in range(len(trechos)) if i not in trocadas] if volta == 0 else sorted(trocadas_nesta)
            if not pendentes:
                break
            pares = [P.par_da_faixa(amp.comum, atual, *trechos[i], caixa) for i in pendentes]
            respostas = {int(r.get("indice", -1)): r for r in claude.conferir_faixas(pares)}
            trocadas_nesta: set[int] = set()
            for pos, i in enumerate(pendentes):
                r = respostas.get(pos) or {}
                if volta == 0:
                    resultado.append({"faixa": i, "trecho": r.get("trecho") or "", "tem_texto": bool(r.get("tem_texto")),
                                      "ok": not r.get("deformado"), "obs": r.get("obs") or ""})
                if r.get("deformado") and volta < 2:
                    trocadas_nesta.add(i)
                elif r.get("deformado"):
                    next(x for x in resultado if x["faixa"] == i)["obs"] = "continua deformado depois da troca"
                    return atual, _rel(resultado), self._zooms(atual, trechos, caixa, resultado), False
                elif volta > 0:
                    next(x for x in resultado if x["faixa"] == i)["obs"] = "refeito pela ampliação comum"
            if not trocadas_nesta:
                break
            trocadas |= trocadas_nesta
            atual = P.trocar_faixas(atual, amp.comum, [trechos[i] for i in trocadas])
        return atual, _rel(resultado), self._zooms(atual, trechos, caixa, resultado), True

    @staticmethod
    def _zooms(img, trechos, caixa, resultado) -> list[tuple[str, Any]]:
        x0, x1 = (caixa[0], caixa[2]) if caixa else (0, img.width)
        return [(r["trecho"] or f"Faixa {r['faixa'] + 1}", P.reduzir(img.crop((x0, trechos[r["faixa"]][0], x1, trechos[r["faixa"]][1])), 1000))
                for r in resultado if r["tem_texto"]]

    # ----- artes -----
    def etapa_analise(self, trabalho: dict, claude: ClaudeImagens) -> dict:
        imagens = [img for _, img in self._originais(trabalho)]
        referencia = self._referencia(trabalho)
        # V20.17 (dono, 04/11): com nome/link do produto, pesquisa na internet antes. Falhou = segue só com o rótulo.
        produto = trabalho.get("produto") or {}
        pesquisa, relatorio = None, {}
        if produto.get("nome") or produto.get("link"):
            try:
                pesquisa = self._pesquisar(claude, str(produto.get("nome") or ""), str(produto.get("link") or ""))
                relatorio = {"pesquisa": pesquisa["texto"], "fontes": pesquisa["fontes"]}
            except Exception as e:
                relatorio = {"aviso_pesquisa": "A pesquisa na internet falhou; os textos vieram só do rótulo. " + _mensagem_erro(e)}
        catalogo = self._catalogo(trabalho)
        pedido = self._pedido(trabalho)
        dados = (claude.propor_textos_referencia(trabalho["tipo"], imagens, referencia, pesquisa, catalogo, pedido)
                 if referencia is not None else claude.propor_textos(trabalho["tipo"], imagens, pesquisa, catalogo, pedido))
        if not dados.get("serve", True):
            return {"id": trabalho["id"], "situacao": "outra_foto",
                    "mensagem": dados.get("motivo") or "Não dá para ler o rótulo: mande uma foto mais nítida."}
        return {"id": trabalho["id"], "situacao": "textos_para_aprovar", "textos": {"campos": dados["campos"]},
                "relatorio": relatorio}

    # ----- V20.31: anúncio (textos, depois atributos da categoria) -----
    def _anuncio_fontes(self, trabalho: dict, claude: ClaudeImagens) -> tuple[list, list, dict | None]:
        imagens = [img for _, img in self._originais(trabalho)]
        catalogo = self._catalogo(trabalho)
        produto = trabalho.get("produto") or {}
        pesquisa = None
        if produto.get("nome") or produto.get("link"):
            try:
                pesquisa = self._pesquisar(claude, str(produto.get("nome") or ""), str(produto.get("link") or ""))
            except Exception as e:  # pesquisa que falha não trava o anúncio
                registrar(f"#{trabalho['id']} pesquisa falhou: {_mensagem_erro(e)}")
        return imagens, catalogo, pesquisa

    def etapa_anuncio_textos(self, trabalho: dict, claude: ClaudeImagens) -> dict:
        imagens, catalogo, pesquisa = self._anuncio_fontes(trabalho, claude)
        dados = claude.propor_anuncio_textos(imagens, trabalho.get("produto") or {}, catalogo, pesquisa)
        return {"id": trabalho["id"], "etapa": "anuncio_textos", "situacao": "anuncio_textos",
                "titulos": dados.get("titulos") or [], "descricao": dados.get("descricao") or ""}

    def etapa_anuncio_atributos(self, trabalho: dict, claude: ClaudeImagens) -> dict:
        imagens, catalogo, pesquisa = self._anuncio_fontes(trabalho, claude)
        dados = claude.propor_anuncio_atributos(imagens, trabalho.get("produto") or {}, trabalho.get("proposta") or {},
                                                trabalho.get("atributos") or [], catalogo, pesquisa)
        return {"id": trabalho["id"], "etapa": "anuncio_atributos", "situacao": "proposta",
                "atributos": dados.get("atributos") or []}

    def etapa_arte(self, trabalho: dict, claude: ClaudeImagens) -> dict:
        originais = self._originais(trabalho)
        frente = next((img for a, img in originais if a.get("papel") == "frente"), originais[0][1])
        ampliada, faixas_rel, zooms, deu_certo = self.ampliar_e_conferir(frente, claude, trabalho.get("obs") or "")
        produto, sombra = P.produto_para_arte(ampliada)
        campos = (trabalho.get("textos") or {}).get("campos") or []
        referencia = self._referencia(trabalho)
        if referencia is None:
            arte = P.renderizar(artes.montar(trabalho["tipo"], campos, sombra), {"produto.png": produto})
            revisao = claude.conferir_arte(arte, campos)
        else:
            # V20.17: layout da referência — o Claude escreve o HTML; se a conferência achar problema, ele
            # corrige uma vez com a lista de problemas.
            problemas_antes: list[str] = []
            for _ in range(2):
                html = artes.limpar_html(claude.gerar_html(referencia, P.abrir(produto), campos, sombra, problemas_antes,
                                                           self._pedido(trabalho)))
                arte = P.renderizar(html, {"produto.png": produto})
                revisao = claude.conferir_arte(arte, campos)
                problemas_antes = [str(p) for p in revisao.get("problemas") or []]
                if revisao.get("ok"):
                    break
        problemas = [str(p) for p in revisao.get("problemas") or []]
        ok = bool(revisao.get("ok")) and deu_certo
        arquivos = [{"papel": "resultado", "dados": b64(P.jpg(arte))}]
        arquivos += [{"papel": "zoom", "legenda": leg[:120], "dados": b64(P.jpg(z, 88))} for leg, z in zooms[:12]]
        msg = "; ".join(problemas) if problemas else ("" if deu_certo else "A IA deformou letras do produto: confira os recortes.")
        return {"id": trabalho["id"], "situacao": "pronta" if ok else "revisar", "arquivos": arquivos,
                "relatorio": {"textos": faixas_rel, "arte": problemas}, "mensagem": msg}


def _rel(resultado: list[dict]) -> list[dict]:
    return [{"trecho": r["trecho"], "ok": r["ok"] or "refeito" in r["obs"], "obs": r["obs"]} for r in resultado if r["tem_texto"]]


def _mensagem_erro(e: Exception) -> str:
    nome = type(e).__name__
    if isinstance(e, ClaudeRecusou):
        return str(e)
    if nome == "AuthenticationError":
        return "Chave da API do Claude inválida: confira o config.env do agente."
    if nome in ("PermissionDeniedError",):
        return "A chave da API não tem permissão para este modelo."
    if nome in ("RateLimitError", "InternalServerError", "APIConnectionError", "APITimeoutError", "OverloadedError"):
        return "O Claude não respondeu agora (limite ou instabilidade). Clique em Refazer daqui a pouco."
    if "saldo" in str(e).lower() or "credit" in str(e).lower():
        return "Sem saldo na conta da API do Claude."
    return f"Erro no agente: {str(e)[:300]}"


def criar_cliente_claude(chave: str) -> Callable[[str], ClaudeImagens]:
    import anthropic
    cliente = anthropic.Anthropic(api_key=chave, max_retries=3)
    return lambda modelo: ClaudeImagens(cliente, modelo)


class Tunel:
    """V20.17 (dono, 04/11): o portão de login do NoromakWeb no nginx barra o agente (302 para /login). Até liberarem
    /expedicao/api/imagens/agente/ no nginx, o agente fala com a Expedição por um túnel SSH até a VPS — o mesmo acesso
    da publicação (o "Host expedicao" do ~/.ssh/config deste PC). A Expedição continua exigindo o token do agente.
    config.env: NOROMAK_TUNEL=expedicao (vazio = sem túnel, pelo endereço público)."""

    def __init__(self, destino: str, porta_local: int = 18157, porta_remota: int = 8100):
        self.destino, self.porta_local, self.porta_remota = destino, porta_local, porta_remota
        self.proc: subprocess.Popen | None = None
        self._falhas = 0
        self._proxima = 0.0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.porta_local}/expedicao"

    def _porta_aberta(self) -> bool:
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", self.porta_local)) == 0

    def garantir(self) -> None:
        """Abre o túnel se a porta não estiver respondendo (e reabre se o ssh caiu).
        Revisão de 04/11: porta já aberta (túnel deste agente ou de outro) = reaproveita, sem novo login na VPS; ssh que
        falha espera mais a cada vez (15 s, 30 s... até 5 min); o ssh roda no mesmo console (fecha junto com o agente)
        e o erro dele vai para o tunel.log, não para um pipe que ninguém lê."""
        if self._porta_aberta():
            return
        if time.time() < self._proxima:
            raise OSError(f"Túnel SSH fora; nova tentativa em {self._proxima - time.time():.0f} s.")
        self.fechar()
        ssh = shutil.which("ssh") or "ssh"
        try:
            log = open(TUNEL_LOG, "ab")
        except OSError:
            log = subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(
                [ssh, "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                 "-o", "ServerAliveCountMax=4", "-L", f"127.0.0.1:{self.porta_local}:127.0.0.1:{self.porta_remota}",
                 self.destino],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        finally:
            if log is not subprocess.DEVNULL:
                log.close()
        for _ in range(30):
            if self._porta_aberta():
                self._falhas = 0
                registrar(f"Túnel SSH aberto ({self.destino} → porta {self.porta_local}).")
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self._falhas += 1
        self._proxima = time.time() + min(300, 15 * 2 ** (self._falhas - 1))
        saiu = self.proc.poll() is not None
        self.fechar()
        raise OSError(f"O túnel SSH não abriu ({'ssh saiu' if saiu else '15 s sem resposta'}; veja {TUNEL_LOG.name}).")

    def fechar(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def trava_instancia(caminho: Path = TRAVA):
    """Um agente por PC (revisão de 04/11): dois juntos abririam dois túneis e pegariam trabalhos em dobro. Devolve o
    arquivo travado (manter aberto enquanto roda) ou None se outro agente já está ligado. O Windows solta a trava
    sozinho quando o processo morre."""
    try:
        f = open(caminho, "a+b")
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def testar_instalacao(cfg: dict[str, str]) -> bool:
    """Confere tudo o que o agente precisa, sem processar foto e sem gastar crédito. Uma linha por item."""
    ok = True

    def linha(bom: bool, texto: str) -> None:
        nonlocal ok
        ok = ok and bom
        print(("  [OK]    " if bom else "  [FALTA] ") + texto, flush=True)

    print("Agente de imagens do Noromak — conferindo a instalação deste PC")
    try:
        import anthropic  # noqa: F401
        linha(True, "Bibliotecas do Python (Pillow e anthropic)")
    except ImportError:
        linha(False, "Biblioteca anthropic: rode o instalar.bat de novo")
    esrgan = Path(cfg["REALESRGAN_DIR"]) / "realesrgan-ncnn-vulkan.exe"
    linha(esrgan.is_file(), f"Real-ESRGAN ({esrgan.parent})" if esrgan.is_file()
          else "Real-ESRGAN não encontrado: as fotos pequenas serão ampliadas sem IA")
    nav = P.achar_navegador()
    linha(bool(nav), f"Chrome/Edge ({nav})" if nav else "Chrome ou Edge não encontrado (precisa para montar as artes)")
    chave = cfg.get("ANTHROPIC_API_KEY", "")
    linha(chave.startswith("sk-ant-"), "Chave da API do Claude no config.env" if chave.startswith("sk-ant-")
          else "Chave da API do Claude: cole em ANTHROPIC_API_KEY no config.env")
    if chave.startswith("sk-ant-"):
        try:
            import anthropic
            anthropic.Anthropic(api_key=chave, max_retries=1).models.retrieve("claude-haiku-5-5")
            linha(True, "Chave da API aceita pela Anthropic (sem gasto)")
        except Exception as e:
            linha(False, f"Chave da API recusada: {_mensagem_erro(e)}")
    token = cfg.get("NOROMAK_AGENTE_TOKEN", "")
    linha(bool(token), "Token do agente no config.env" if token else "Token do agente: cole em NOROMAK_AGENTE_TOKEN no config.env")
    if token:
        tunel = Tunel(cfg["NOROMAK_TUNEL"]) if cfg.get("NOROMAK_TUNEL") else None
        try:
            if tunel:
                tunel.garantir()
            Noromak(tunel.url if tunel else cfg["NOROMAK_URL"], token).ping()
            linha(True, "Conexão com o Noromak" + (" (pelo túnel)" if tunel else ""))
        except NoromakErro as e:
            linha(False, "Token do agente recusado pelo Noromak (confira o token)" if e.status == 401 else str(e))
        except Exception as e:
            linha(False, f"Sem conexão com o Noromak: {e}")
        finally:
            if tunel:
                tunel.fechar()
    print("Tudo certo: o agente pode ser ligado." if ok else "Corrija o que está marcado como FALTA e rode de novo.")
    return ok


def main() -> None:
    for fluxo in (sys.stdout, sys.stderr):   # console do Windows sem UTF-8 não pode derrubar o agente por um acento
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Agente de imagens do Noromak")
    p.add_argument("--uma-vez", action="store_true", help="processa o que houver na fila e sai")
    p.add_argument("--testar", action="store_true", help="confere a instalação (sem processar nenhuma foto) e sai")
    a = p.parse_args()
    cfg = ler_config()
    if a.testar:
        sys.exit(0 if testar_instalacao(cfg) else 1)
    faltam = [k for k in ("ANTHROPIC_API_KEY", "NOROMAK_AGENTE_TOKEN") if not cfg.get(k)]
    if faltam:
        sys.exit(f"Falta no config.env: {', '.join(faltam)} (veja config.env.exemplo).")
    trava = trava_instancia()
    if trava is None:
        sys.exit("Já tem um agente de imagens ligado neste PC. Feche o outro antes (um agente por vez).")
    tunel = Tunel(cfg["NOROMAK_TUNEL"]) if cfg.get("NOROMAK_TUNEL") else None
    url = tunel.url if tunel else cfg["NOROMAK_URL"]
    agente = Agente(Noromak(url, cfg["NOROMAK_AGENTE_TOKEN"]), criar_cliente_claude(cfg["ANTHROPIC_API_KEY"]),
                    cfg["REALESRGAN_DIR"])
    registrar(f"Agente de imagens {VERSAO} ligado ({url}{' pelo túnel ' + cfg['NOROMAK_TUNEL'] if tunel else ''}).")
    if tunel:
        agente.ao_reconectar = tunel.garantir
    try:
        _laco(agente, a.uma_vez, tunel)
    finally:
        if tunel:
            tunel.fechar()
        trava.close()


def _laco(agente: "Agente", uma_vez: bool, tunel: "Tunel | None") -> None:
    ultimo_ping = 0.0
    while True:
        try:
            if tunel:
                tunel.garantir()
            if time.time() - ultimo_ping > 30:
                agente.noromak.ping()
                ultimo_ping = time.time()
            processou = agente.uma_volta()
            if processou:
                continue
            if uma_vez:
                return
        except NoromakErro as e:
            registrar(f"Noromak recusou ({e.status}): {e}" + (" — confira o NOROMAK_AGENTE_TOKEN." if e.status == 401 else ""))
            if uma_vez:
                return
        except (urllib.error.URLError, OSError) as e:
            registrar(f"Sem conexão com o Noromak: {e}")
            if uma_vez:
                return
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
