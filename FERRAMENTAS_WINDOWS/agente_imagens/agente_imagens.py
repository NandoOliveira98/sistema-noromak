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
import json
import os
import platform
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

VERSAO = "0.1"
INTERVALO = 10
LOG = PASTA / "agente.log"


def ler_config(caminho: Path = PASTA / "config.env") -> dict[str, str]:
    cfg = {"NOROMAK_URL": "https://noromakweb.com.br/expedicao",
           "REALESRGAN_DIR": str(Path.home() / ".claude/skills/imagem-anuncio/ferramentas/realesrgan")}
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
        self._abrir = abrir or urllib.request.urlopen

    def _pedir(self, metodo: str, rota: str, corpo: dict | None = None, bruto: bool = False):
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(self.url + rota, data=dados, method=metodo)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "noromak-agente-imagens/" + VERSAO)
        try:
            with self._abrir(req, timeout=120) as r:
                texto = r.read()
                return texto if bruto else json.loads(texto or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error") or ""
            except Exception:
                msg = ""
            raise NoromakErro(e.code, msg or f"HTTP {e.code}") from None

    def ping(self) -> None:
        self._pedir("POST", "/api/imagens/agente/ping", {"versao": VERSAO, "computador": platform.node()})

    def proximo(self) -> dict | None:
        return self._pedir("GET", "/api/imagens/agente/proximo").get("trabalho")

    def arquivo(self, id_: int, nome: str) -> bytes:
        return self._pedir("GET", f"/api/imagens/agente/arquivo?id={id_}&nome={urllib.request.quote(nome)}", bruto=True)

    def analise(self, corpo: dict) -> None:
        self._pedir("POST", "/api/imagens/agente/analise", corpo)

    def resultado(self, corpo: dict) -> None:
        self._pedir("POST", "/api/imagens/agente/resultado", corpo)


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

    def uma_volta(self) -> bool:
        """Processa um trabalho, se houver. Devolve True se processou."""
        trabalho = self.noromak.proximo()
        if not trabalho:
            return False
        claude = self.criar_claude(trabalho.get("modelo") or "claude-haiku-5-5")
        inicio = time.time()
        etapa = trabalho.get("etapa")
        try:
            if etapa == "analise":
                corpo = self.etapa_analise(trabalho, claude)
                self._entregar(self.noromak.analise, corpo, claude)
            else:
                corpo = self.etapa_arte(trabalho, claude) if etapa == "arte" else self.etapa_principal(trabalho, claude)
                self._entregar(self.noromak.resultado, corpo, claude)
            situacao = corpo["situacao"]
        except NoromakErro as e:
            situacao = f"não entregue ({e.status}: {e})"
        except Exception as e:  # o trabalho não pode ficar preso: volta como erro com o motivo
            traceback.print_exc()
            msg = _mensagem_erro(e)
            entregar = self.noromak.analise if etapa == "analise" else self.noromak.resultado
            try:
                self._entregar(entregar, {"id": trabalho["id"], "situacao": "erro", "mensagem": msg}, claude)
            except Exception:
                pass
            situacao = "erro: " + msg
        u = claude.uso
        registrar(f"#{trabalho['id']} {trabalho.get('tipo')}/{etapa} sku={trabalho.get('sku') or '-'} -> {situacao} | "
                  f"{u.modelo} {u.entrada}+{u.saida} tokens ~US$ {u.custo():.4f} | {time.time() - inicio:.0f}s")
        return True

    @staticmethod
    def _entregar(funcao: Callable, corpo: dict, claude: ClaudeImagens) -> None:
        corpo.update({"modelo": claude.modelo, "tokens_entrada": claude.uso.entrada, "tokens_saida": claude.uso.saida,
                      "buscas_web": claude.uso.buscas})
        funcao(corpo)

    def _originais(self, trabalho: dict) -> list[tuple[dict, Any]]:
        """Fotos do produto (a imagem de referência fica de fora: ela é de OUTRO anúncio)."""
        return [(a, P.abrir(self.noromak.arquivo(trabalho["id"], a["nome"]))) for a in trabalho.get("arquivos") or []
                if a.get("papel") != "referencia"]

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
                pesquisa = claude.pesquisar(str(produto.get("nome") or ""), str(produto.get("link") or ""))
                relatorio = {"pesquisa": pesquisa["texto"], "fontes": pesquisa["fontes"]}
            except Exception as e:
                relatorio = {"aviso_pesquisa": "A pesquisa na internet falhou; os textos vieram só do rótulo. " + _mensagem_erro(e)}
        dados = (claude.propor_textos_referencia(trabalho["tipo"], imagens, referencia, pesquisa) if referencia is not None
                 else claude.propor_textos(trabalho["tipo"], imagens, pesquisa))
        if not dados.get("serve", True):
            return {"id": trabalho["id"], "situacao": "outra_foto",
                    "mensagem": dados.get("motivo") or "Não dá para ler o rótulo: mande uma foto mais nítida."}
        return {"id": trabalho["id"], "situacao": "textos_para_aprovar", "textos": {"campos": dados["campos"]},
                "relatorio": relatorio}

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
                html = artes.limpar_html(claude.gerar_html(referencia, P.abrir(produto), campos, sombra, problemas_antes))
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


def main() -> None:
    p = argparse.ArgumentParser(description="Agente de imagens do Noromak")
    p.add_argument("--uma-vez", action="store_true", help="processa o que houver na fila e sai")
    a = p.parse_args()
    cfg = ler_config()
    faltam = [k for k in ("ANTHROPIC_API_KEY", "NOROMAK_AGENTE_TOKEN") if not cfg.get(k)]
    if faltam:
        sys.exit(f"Falta no config.env: {', '.join(faltam)} (veja config.env.exemplo).")
    agente = Agente(Noromak(cfg["NOROMAK_URL"], cfg["NOROMAK_AGENTE_TOKEN"]), criar_cliente_claude(cfg["ANTHROPIC_API_KEY"]),
                    cfg["REALESRGAN_DIR"])
    registrar(f"Agente de imagens {VERSAO} ligado ({cfg['NOROMAK_URL']}).")
    ultimo_ping = 0.0
    while True:
        try:
            if time.time() - ultimo_ping > 30:
                agente.noromak.ping()
                ultimo_ping = time.time()
            processou = agente.uma_volta()
            if processou:
                continue
            if a.uma_vez:
                return
        except NoromakErro as e:
            registrar(f"Noromak recusou ({e.status}): {e}" + (" — confira o NOROMAK_AGENTE_TOKEN." if e.status == 401 else ""))
            if a.uma_vez:
                return
        except (urllib.error.URLError, OSError) as e:
            registrar(f"Sem conexão com o Noromak: {e}")
            if a.uma_vez:
                return
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
