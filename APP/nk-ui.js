/* V18.83 — peças comuns do Sistema Web Noromak, iguais em todas as telas:
   1) tema claro/escuro (escolha guardada neste navegador, vale para todas as telas e abas);
   2) avisos (nkToast) no lugar da janelinha do navegador — opcional por tela, com
      <script src="nk-ui.js" data-alertas="toast">. A Conferência NÃO usa: lá o alert()
      trava o leitor de código de barras de propósito;
   3) confirmação padrão (await nkConfirmar("Excluir...?"));
   4) tabela padrão (table[data-nk-tabela]): clicar no título ordena, botão Exportar gera
      planilha (.csv que abre no Excel), título fica fixo ao rolar.
   Carregado no <head> SEM defer: o tema entra antes de a página desenhar (sem piscar branco).
   Só apresentação: nada aqui grava dado no servidor. */
(function () {
  "use strict";
  if (window.nkUI) return;
  const doc = document.documentElement;

  /* ---------- 1. Tema ---------- */
  const CHAVE_TEMA = "nk_tema_v1883";
  const lerTema = () => { try { return localStorage.getItem(CHAVE_TEMA) === "escuro" ? "escuro" : "claro"; } catch (_) { return "claro"; } };
  function aplicarTema(t) {
    const escuro = t === "escuro";
    doc.dataset.tema = escuro ? "escuro" : "claro";
    doc.style.colorScheme = escuro ? "dark" : "light";
    document.dispatchEvent(new CustomEvent("nk-tema", { detail: doc.dataset.tema }));
  }
  aplicarTema(lerTema());
  // Outra aba trocou o tema: acompanha.
  window.addEventListener("storage", e => { if (e.key === CHAVE_TEMA) aplicarTema(lerTema()); });
  const nkTema = {
    atual: () => doc.dataset.tema,
    definir(t) { try { localStorage.setItem(CHAVE_TEMA, t === "escuro" ? "escuro" : "claro"); } catch (_) {} aplicarTema(t); },
    alternar() { this.definir(this.atual() === "escuro" ? "claro" : "escuro"); },
  };

  const quandoPronto = fn => { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once: true }); else fn(); };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));

  /* ---------- 2. Avisos ---------- */
  const ICONE = { ok: "nk-i-check", erro: "nk-i-alert", aviso: "nk-i-alert", info: "nk-i-bell" };
  const DURACAO = { ok: 4500, info: 6000, aviso: 8000, erro: 10000 };
  const PENDENTES = "nk_toasts_v1883";
  const vivos = new Set();

  function classificar(msg) {
    const t = String(msg || "").toLowerCase();
    if (/(erro|falh|não foi possível|nao foi possivel|inválid|invalid|negad|recusad|não autorizado|sem permiss|exclusiva do|não encontrad|expirad|bloquead)/.test(t)) return "erro";
    if (/(atenç|atenc|cuidado|aviso|pendente|preencha|informe|selecione|nenhum|não há|nao ha)/.test(t)) return "aviso";
    if (/(salv|conclu|sucesso|atualizad|enviad|criad|removid|excluíd|excluid|aprovad|importad|conectad|copiad|restaurad|✅)/.test(t)) return "ok";
    return "info";
  }

  function areaToasts() {
    let a = document.getElementById("nkToasts");
    if (!a) {
      a = document.createElement("div");
      a.id = "nkToasts";
      a.className = "nk-toasts";
      a.setAttribute("aria-live", "polite");
      document.body.append(a);
    }
    return a;
  }

  function nkToast(msg, tipo, opcoes = {}) {
    tipo = ["ok", "erro", "aviso", "info"].includes(tipo) ? tipo : classificar(msg);
    if (!document.body) { quandoPronto(() => nkToast(msg, tipo, opcoes)); return; }
    const dur = opcoes.duracao || DURACAO[tipo];
    const el = document.createElement("div");
    el.className = `nk-toast ${tipo}`;
    el.setAttribute("role", tipo === "erro" ? "alert" : "status");
    el.innerHTML = `<i class=${ICONE[tipo]}></i><div class="nk-toast-txt"></div><button type="button" class="nk-toast-x" aria-label="Fechar aviso">×</button>`;
    el.querySelector(".nk-toast-txt").textContent = String(msg ?? "");
    const registro = { msg: String(msg ?? ""), tipo, ate: Date.now() + dur };
    vivos.add(registro);
    let timer = null, restante = dur, inicio = Date.now();
    const fechar = () => { clearTimeout(timer); vivos.delete(registro); el.classList.add("saindo"); setTimeout(() => el.remove(), 180); };
    const agendar = () => { inicio = Date.now(); timer = setTimeout(fechar, restante); };
    el.addEventListener("mouseenter", () => { clearTimeout(timer); restante -= Date.now() - inicio; });
    el.addEventListener("mouseleave", agendar);
    el.querySelector(".nk-toast-x").onclick = fechar;
    const area = areaToasts();
    area.append(el);
    while (area.children.length > 5) area.firstElementChild.remove();
    agendar();
    return fechar;
  }
  // Aviso mostrado logo antes de recarregar/trocar de página continua na próxima (alert(); location.reload()).
  window.addEventListener("pagehide", () => {
    try {
      const lista = [...vivos].filter(v => v.ate > Date.now());
      if (lista.length) sessionStorage.setItem(PENDENTES, JSON.stringify(lista));
    } catch (_) {}
  });
  quandoPronto(() => {
    try {
      const lista = JSON.parse(sessionStorage.getItem(PENDENTES) || "[]");
      sessionStorage.removeItem(PENDENTES);
      for (const v of lista) if (v.ate > Date.now()) nkToast(v.msg, v.tipo, { duracao: Math.max(2500, v.ate - Date.now()) });
    } catch (_) {}
  });

  const eu = document.currentScript;
  if (eu && eu.dataset.alertas === "toast") {
    const alertaNativo = window.alert.bind(window);
    window.nkAlertaNativo = alertaNativo;
    window.alert = msg => { nkToast(msg); };
  }

  /* ---------- 3. Confirmação ---------- */
  const VERBOS = ["Excluir", "Remover", "Apagar", "Desconectar", "Renovar", "Aprovar", "Imprimir", "Atualizar", "Restaurar", "Mandar", "Enviar", "Salvar", "Aplicar", "Limpar", "Cancelar", "Substituir", "Importar"];
  const PERIGO = /(^|[^A-Za-zÀ-ú])(excluir|remover|apagar|desconectar|limpar|substituir)(?![A-Za-zÀ-ú])|não volta atrás|nao volta atras|não pode ser desfeit/i;
  const ACAO = new RegExp("(?:^|[^A-Za-zÀ-ú])(" + VERBOS.join("|") + ")(?![A-Za-zÀ-ú])", "i");

  function nkConfirmar(texto, opcoes = {}) {
    return new Promise(resolver => {
      quandoPronto(() => {
        const tudo = String(texto ?? "");
        const linhas = tudo.split("\n");
        // 1ª linha vira o título (a pergunta) e o resto o detalhe; pergunta curta sozinha também é título.
        const titulo = opcoes.titulo || (linhas.length > 1 || tudo.length <= 120 ? linhas.shift().trim() : "Confirmar");
        const corpo = (opcoes.titulo ? tudo : linhas.join("\n")).trim();
        const achado = (titulo.match(ACAO) || tudo.match(ACAO) || [])[1] || "";
        const verbo = opcoes.ok || (achado ? achado[0].toUpperCase() + achado.slice(1).toLowerCase() : "Confirmar");
        const perigo = opcoes.perigo ?? PERIGO.test(tudo);
        const anterior = document.activeElement;
        const fundo = document.createElement("div");
        fundo.className = "nk-dialogo-fundo";
        fundo.innerHTML = `<div class="nk-dialogo" role="alertdialog" aria-modal="true" aria-labelledby="nkDlgTit">
          <h2 id="nkDlgTit">${esc(titulo)}</h2>${corpo ? `<div class="nk-dialogo-txt">${esc(corpo)}</div>` : ""}
          <div class="nk-dialogo-acoes"><button type="button" class="nk-btn" data-r="0">${esc(opcoes.cancelar || "Cancelar")}</button>
          <button type="button" class="nk-btn ${perigo ? "perigo-cheio" : "pri"}" data-r="1">${esc(verbo)}</button></div></div>`;
        const fim = r => {
          document.removeEventListener("keydown", teclas, true);
          fundo.remove();
          try { anterior && anterior.focus && anterior.focus(); } catch (_) {}
          resolver(r);
        };
        const teclas = e => {
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); fim(false); }
          else if (e.key === "Tab") {
            const bts = [...fundo.querySelectorAll("button")];
            const i = bts.indexOf(document.activeElement);
            e.preventDefault();
            bts[(i + (e.shiftKey ? bts.length - 1 : 1)) % bts.length].focus();
          }
        };
        fundo.addEventListener("click", e => { const b = e.target.closest("button[data-r]"); if (b) fim(b.dataset.r === "1"); else if (e.target === fundo) fim(false); });
        document.addEventListener("keydown", teclas, true);
        document.body.append(fundo);
        // Ação que apaga começa com o foco em Cancelar: Enter sem querer não exclui nada.
        fundo.querySelector(`button[data-r="${perigo ? 0 : 1}"]`).focus();
      });
    });
  }

  /* ---------- 4. Tabela padrão ---------- */
  function valorCelula(td) {
    if (!td) return "";
    if (td.dataset && td.dataset.ordem !== undefined) return td.dataset.ordem;
    const campo = td.querySelector("input:not([type=checkbox]),select,textarea");
    if (campo) return campo.value;
    return (td.innerText || td.textContent || "").replace(/\s+/g, " ").trim();
  }
  function chaveOrdem(v) {
    const t = String(v ?? "").trim();
    if (!t || t === "—" || t === "-") return { tipo: 2, v: "" };
    const data = t.match(/^(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
    if (data) {
      let ano = data[3] ? +data[3] : new Date().getFullYear();
      if (ano < 100) ano += 2000;
      return { tipo: 0, v: new Date(ano, +data[2] - 1, +data[1], +(data[4] || 0), +(data[5] || 0), +(data[6] || 0)).getTime() };
    }
    const num = t.replace(/^R\$\s*/, "").replace(/\s*(%|un\.?|unid\.?|dias?|h|min|MB|GB)$/i, "");
    if (/^[-+]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$/.test(num)) return { tipo: 0, v: parseFloat(num.replace(/\./g, "").replace(",", ".")) };
    if (/^[-+]?\d+\.\d+$/.test(num)) return { tipo: 0, v: parseFloat(num) };
    return { tipo: 1, v: t.toLocaleLowerCase("pt-BR") };
  }
  function comparar(a, b) {
    if (a.tipo !== b.tipo) return a.tipo - b.tipo; // números/datas, depois texto, vazios por último
    if (a.tipo === 1) return a.v.localeCompare(b.v, "pt-BR", { numeric: true });
    return a.v - b.v;
  }
  function linhasDados(tabela) {
    const corpo = tabela.tBodies[0];
    return corpo ? [...corpo.rows].filter(tr => !(tr.cells.length === 1 && tr.cells[0].colSpan > 1)) : [];
  }
  function cabecalho(tabela) {
    const linha = tabela.tHead ? tabela.tHead.rows[tabela.tHead.rows.length - 1] : null;
    return linha ? [...linha.cells] : [];
  }
  function ordenar(tabela) {
    const col = Number(tabela.dataset.nkOrdCol), dir = tabela.dataset.nkOrdDir === "desc" ? -1 : 1;
    if (!Number.isFinite(col) || tabela.dataset.nkOrdCol === undefined) return;
    const corpo = tabela.tBodies[0];
    if (!corpo) return;
    const linhas = linhasDados(tabela);
    // Guarda a posição que a tela deu a cada linha: o 3º clique volta para ela.
    linhas.forEach((tr, i) => { if (tr._nkPos === undefined) tr._nkPos = i; });
    const chaves = new Map(linhas.map(tr => [tr, chaveOrdem(valorCelula(tr.cells[col]))]));
    const ordenadas = [...linhas].sort((a, b) => {
      const ka = chaves.get(a), kb = chaves.get(b);
      if (ka.tipo === 2 || kb.tipo === 2) return ka.tipo - kb.tipo; // vazio sempre no fim
      return comparar(ka, kb) * dir;
    });
    if (ordenadas.every((tr, i) => tr === linhas[i])) return;
    const obs = tabela._nkObs;
    if (obs) obs.disconnect();
    const frag = document.createDocumentFragment();
    ordenadas.forEach(tr => frag.append(tr));
    corpo.append(frag);
    if (obs) obs.observe(tabela, { childList: true, subtree: true });
  }
  function marcarCabecalho(tabela) {
    const col = tabela.dataset.nkOrdCol;
    cabecalho(tabela).forEach((th, i) => {
      th.classList.toggle("nk-ord-asc", String(i) === col && tabela.dataset.nkOrdDir !== "desc");
      th.classList.toggle("nk-ord-desc", String(i) === col && tabela.dataset.nkOrdDir === "desc");
      if (!th.hasAttribute("data-nk-sem-ordem") && !th.querySelector("input,select")) th.classList.add("nk-ord");
    });
  }
  function exportar(tabela, nome) {
    const ths = cabecalho(tabela);
    const titulos = ths.map(th => (th.innerText || th.textContent || "").replace(/\s+/g, " ").trim());
    // Coluna só de botões ("Ações") e coluna marcada data-nk-sem-exportar ficam fora da planilha.
    const usar = titulos.map((t, i) => !/^(a[çc][õo]es?|a[çc][aã]o)?$/i.test(t) && !ths[i].hasAttribute("data-nk-sem-exportar"));
    const so = lista => lista.filter((_, i) => usar[i] !== false);
    const linhas = linhasDados(tabela).filter(tr => tr.offsetParent !== null || tr.closest("[hidden]") === null);
    const celula = v => { const t = String(v ?? "").replace(/\s+/g, " ").trim(); return /[";\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const csv = [so(titulos).map(celula).join(";")].concat(linhas.map(tr => so([...tr.cells]).map(td => celula(valorCelula(td))).join(";"))).join("\r\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    const dia = new Date().toLocaleDateString("pt-BR").split("/").reverse().join("-");
    a.href = URL.createObjectURL(blob);
    a.download = `${(nome || "tabela").replace(/[^\w\-]+/g, "_")}_${dia}.csv`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    nkToast(`${linhas.length} linha(s) exportada(s).`, "ok");
  }
  function nkTabela(tabela, opcoes = {}) {
    if (!tabela || tabela._nkTabela) return;
    tabela._nkTabela = true;
    tabela.classList.add("nk-tabela");
    const nome = opcoes.nome || tabela.dataset.nkTabela || tabela.id || document.title;
    const ordena = opcoes.ordenar !== false && tabela.dataset.nkOrdenar !== "0";
    // Clique no título ordena (1º clique crescente, 2º decrescente, 3º volta à ordem da tela).
    if (ordena) tabela.addEventListener("click", e => {
      const th = e.target.closest("thead th");
      if (!th || !tabela.contains(th) || th.hasAttribute("data-nk-sem-ordem") || e.target.closest("input,select,button,a")) return;
      const i = cabecalho(tabela).indexOf(th);
      if (i < 0) return;
      if (tabela.dataset.nkOrdCol === String(i)) {
        if (tabela.dataset.nkOrdDir === "desc") {
          delete tabela.dataset.nkOrdCol; delete tabela.dataset.nkOrdDir; marcarCabecalho(tabela);
          const corpo = tabela.tBodies[0];
          if (corpo) {
            const obs = tabela._nkObs; if (obs) obs.disconnect();
            linhasDados(tabela).sort((a, b) => (a._nkPos ?? 1e9) - (b._nkPos ?? 1e9)).forEach(tr => corpo.append(tr));
            if (obs) obs.observe(tabela, { childList: true, subtree: true });
          }
          return;
        }
        tabela.dataset.nkOrdDir = "desc";
      } else { tabela.dataset.nkOrdCol = String(i); tabela.dataset.nkOrdDir = "asc"; }
      marcarCabecalho(tabela);
      ordenar(tabela);
    });
    // A tela redesenha o corpo (filtro, polling): mantém a ordem escolhida.
    if (ordena) {
      tabela._nkObs = new MutationObserver(() => { marcarCabecalho(tabela); ordenar(tabela); });
      tabela._nkObs.observe(tabela, { childList: true, subtree: true });
      marcarCabecalho(tabela);
    }
    if (opcoes.exportar !== false && tabela.dataset.nkExportar !== "0") {
      const barra = document.createElement("div");
      barra.className = "nk-tabela-barra";
      barra.innerHTML = `<button type="button" class="nk-btn mini"><i class=nk-i-download></i> Exportar</button>`;
      barra.querySelector("button").onclick = () => exportar(tabela, nome);
      const alvo = tabela.closest(".nk-tw,.tw,.excel-wrap,.rolar,[data-nk-tabela-caixa]") || tabela;
      alvo.parentNode.insertBefore(barra, alvo);
    }
  }
  function iniciarTabelas(raiz) {
    (raiz || document).querySelectorAll("table[data-nk-tabela]").forEach(t => nkTabela(t));
  }
  quandoPronto(() => {
    iniciarTabelas();
    // Tabelas que a tela cria depois (abas, gavetas).
    new MutationObserver(lista => {
      for (const m of lista) for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches && n.matches("table[data-nk-tabela]")) nkTabela(n);
        else if (n.querySelector && n.querySelector("table[data-nk-tabela]")) iniciarTabelas(n);
      }
    }).observe(document.body, { childList: true, subtree: true });
  });

  window.nkUI = { versao: "18.83", tema: nkTema, toast: nkToast, confirmar: nkConfirmar, tabela: nkTabela, classificar };
  window.nkTema = nkTema;
  window.nkToast = nkToast;
  window.nkConfirmar = nkConfirmar;
  window.nkTabela = nkTabela;
})();
