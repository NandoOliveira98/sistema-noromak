/* V18.82 — menu lateral ÚNICO do Sistema Web Noromak.
   Antes cada tela tinha (ou não tinha) o seu menu, e tudo do Admin morava em /expedicao/admin.
   Agora a lista de telas vive só aqui: o Admin desenha o menu com nkMenuDesenhar() dentro do
   próprio <nav>, e as outras telas chamam nkMenuShell(), que monta o mesmo menu à esquerda.
   Cada tela tem o seu endereço (ROTAS_ADMIN no servidor_web.py precisa bater com os itens
   que têm "aba" — o TESTES/test_rotas_v1882.py confere).
   Nada aqui grava dado: é só navegação. */
(function () {
  "use strict";
  const B = "/expedicao";
  const ic = d => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;

  // aba = tela interna do admin.html (abre sem recarregar). modulo = permissão exigida pelo
  // servidor (page_modules). soAdmin = escondido do Supervisor (V17.55).
  const MENU = [
    { grupo: "visao", titulo: "Visão geral", fixo: true, itens: [
      // V19.69: quadro de tarefas (planos de ação). Sem módulo: todo usuário logado vê.
      // V19.70: em destaque (pedido do dono) — 1º item do menu, com fundo laranja.
      { id: "tarefas", rotulo: "Quadro de tarefas", href: "/tarefas", destaque: true, icone: "M3 3h5v18H3zM10 3h5v12h-5zM17 3h4v8h-4z" },
      { id: "dashboard", rotulo: "Dashboard", href: "/dashboard", aba: "dashboard", modulo: "admin", alias: ["/admin", "/administracao"], icone: "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z" },
      { id: "financeiro", rotulo: "Financeiro", href: "/financeiro", modulo: "admin", alias: ["/rentabilidade-ml"], icone: "M3 7h18v13H3zM3 7l3-4h12l3 4M16 14h2" },
      // V19.01: Auditor de cobranças do ML (mesma permissão do Financeiro).
      { id: "auditoria-ml", rotulo: "Cobranças do ML", href: "/auditoria-ml", modulo: "admin", icone: "M6 2h12v20l-3-2-3 2-3-2-3 2zM9 7h6M9 11h6M9 15h3" },
    ] },
    { grupo: "expedicao", titulo: "Expedição", itens: [
      { id: "pedidos", rotulo: "Pedidos pendentes", href: "/pedidos-pendentes", aba: "pedidos", modulo: "admin", alias: ["/pendencias", "/central-pendencias"], icone: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" },
      { id: "pedidos-dia", rotulo: "Pedidos do dia", href: "/pedidos-dia", aba: "pedidos-dia", modulo: "admin", icone: "M3 5h18v16H3zM3 10h18M8 3v4M16 3v4" },
      { id: "coleta", rotulo: "Coleta do dia", href: "/coleta", aba: "coleta", modulo: "admin", icone: "M1 6h13v10H1zM14 10h4l3 3v3h-7zM5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" },
      { id: "etiquetas", rotulo: "Etiquetas / Importar TXT", href: "/etiquetas", aba: "etiquetas", modulo: "admin", oculto: true /* V19.43: botão em Pedidos pendentes */, icone: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01" },
      { id: "separacao", rotulo: "Lista de separação", href: "/separacao", modulo: "admin", icone: "M9 5h11M9 12h11M9 19h11M4 5l1 1 2-2M4 12l1 1 2-2M4 19l1 1 2-2" },
      { id: "faturamento", rotulo: "Faturamento", href: "/faturamento", modulo: "admin", icone: "M4 2h16v20l-4-2-4 2-4-2-4 2zM8 7h8M8 11h8M8 15h5" },
      // V19.88: Devoluções — o operador recebe (tela cheia) e o admin trata no Apollo.
      { id: "devolucoes-admin", rotulo: "Devoluções", href: "/devolucoes-admin", modulo: "admin", icone: "M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-4" },
      { id: "devolucoes", rotulo: "Receber devolução", href: "/devolucoes", modulo: "devolucao", icone: "M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8" },
      { id: "conferencia", rotulo: "Conferência", href: "/conferencia", modulo: "conferencia", icone: "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" },
      { id: "historico", rotulo: "Histórico", href: "/historico", aba: "historico", modulo: "admin", oculto: true /* V19.43: botão em Pedidos pendentes */, icone: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2" },
      // Sem item no menu (abre por botões dentro do Histórico), mas tem endereço próprio.
      { id: "historico-sku", rotulo: "Histórico de SKU", href: "/historico-sku", aba: "historico-sku", modulo: "admin", oculto: true },
    ] },
    { grupo: "vendas", titulo: "Vendas e anúncios", itens: [
      // V20.01: Pós-venda de volta — perguntas e reclamações do ML de Araçatuba e Bauru (selo = perguntas sem resposta).
      { id: "posvenda", rotulo: "Pós-venda", href: "/posvenda", modulo: "admin", icone: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2zM12 7v4M12 14h.01" },
      { id: "conversao", rotulo: "Conversão", href: "/conversao", modulo: "admin", icone: "M3 4h18l-7 8v6l-4 2v-8L3 4z" },
      // V19.74: monitor de catálogo do ML (vitrine, concorrentes, margem no preço para ganhar).
      { id: "catalogo-ml", rotulo: "Catálogo ML", href: "/catalogo-ml", modulo: "admin", icone: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM17.5 14v7M14 17.5h7" },
      { id: "analise-ads", rotulo: "Análise de ADS", href: "/analise-ads", modulo: "admin", icone: "M3 11v3l13 5V6L3 11zM16 9a3 3 0 0 1 0 6M7 15.5V20h3v-3.3" },
      // V20.17: Estúdio de Imagens (foto principal e artes do anúncio; agente no PC do dono).
      { id: "imagens", rotulo: "Imagens", href: "/imagens", modulo: "imagens", icone: "M3 3h18v18H3zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21" },
      { id: "promocoes-auto", rotulo: "Promoções automáticas", href: "/promocoes-auto", modulo: "admin", icone: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5M9 15l6-6M9.5 9.5h.01M14.5 14.5h.01" },
      { id: "precificador", rotulo: "Precificador", href: "/precificador", modulo: "precificador", alias: ["/precificacao"], icone: "M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" },
    ] },
    { grupo: "estoque", titulo: "Estoque e compras", itens: [
      { id: "ruptura", rotulo: "Ruptura de estoque", href: "/ruptura", modulo: "admin", alt: "compras", icone: "M3 3v18h18M7 8l4 4 3-3 6 6M20 11v4h-4" },
      { id: "analise-full", rotulo: "Demanda Full", href: "/analise-full", modulo: "precificador", alias: ["/full"], icone: "M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8" },
      // V19.64: estoque do Apollo de Bauru enviado ao TikTok Shop.
      { id: "tiktok-estoque", rotulo: "Estoque TikTok", href: "/tiktok-estoque", modulo: "admin", icone: "M9 12a4 4 0 1 0 4 4V3c.5 2.5 2.5 4 5 4" },
      { id: "compras", rotulo: "Demanda de Compra", href: "/compras", modulo: "admin", alt: "compras", icone: "M3 3h2l2.4 12.2a2 2 0 0 0 2 1.8h7.7a2 2 0 0 0 2-1.6L21 8H6M9 21h.01M18 21h.01" },
    ] },
    { grupo: "cadastros", titulo: "Cadastros", itens: [
      { id: "produtos", rotulo: "Produtos", href: "/produtos", aba: "produtos", modulo: "admin", icone: "M16.5 9.4 7.5 4.2M21 16V8l-9-5-9 5v8l9 5zM3.3 7 12 12l8.7-5M12 22V12" },
      { id: "kits", rotulo: "Kits", href: "/kits", aba: "kits", modulo: "admin", icone: "M20 12v10H4V12M2 7h20v5H2zM12 22V7" },
    ] },
    { grupo: "sistema", titulo: "Sistema", itens: [
      // V18.83: integrações, rotinas, backup, disco e alertas por e-mail.
      { id: "saude", rotulo: "Saúde do sistema", href: "/saude", modulo: "admin", icone: "M22 12h-4l-3 9L9 3l-3 9H2" },
      { id: "integracoes", rotulo: "Central de integrações", href: "/integracoes", aba: "integracoes", modulo: "admin", soAdmin: true, icone: "M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4" },
      { id: "estacao", rotulo: "Estação Zebra", href: "/estacao-impressao", modulo: "estacao", alias: ["/print-station"], icone: "M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z" },
      { id: "usuarios", rotulo: "Usuários", href: "/usuarios", aba: "usuarios", modulo: "admin", soAdmin: true, icone: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8" },
      { id: "seguranca", rotulo: "Segurança", href: "/seguranca", aba: "seguranca", modulo: "admin", soAdmin: true, icone: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" },
    ] },
  ];

  const ITENS = MENU.flatMap(g => g.itens.map(i => ({ ...i, grupo: g.grupo })));
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  const semBarra = p => (String(p || "").replace(/\.html$/i, "").replace(/\/+$/, "") || "/");
  const caminhoRelativo = () => semBarra(location.pathname.startsWith(B) ? location.pathname.slice(B.length) : location.pathname);

  function itemDoCaminho(caminho) {
    const p = semBarra(caminho);
    return ITENS.find(i => i.href === p || (i.alias || []).includes(p)) || null;
  }
  function abaDoCaminho(caminho) { const i = itemDoCaminho(caminho); return i && i.aba ? i.aba : null; }
  function itemDaAba(aba) { return ITENS.find(i => i.aba === aba) || null; }

  // V18.87: como antes da V18.82, cada item abre em aba nova (target=_blank). Dentro do Admin,
  // o clique simples numa tela do próprio Admin (data-nk-aba) é interceptado pelo script
  // rotas-admin-v1882 e troca a tela sem recarregar.
  function itemHTML(i) {
    return `<a class="v15-nav-item" href="${B}${i.href}" target="_blank" rel="noopener" data-v15-nav="${esc(i.aba || i.id)}" data-nk-id="${esc(i.id)}"` +
      `${i.aba ? ` data-nk-aba="${esc(i.aba)}"` : ""}${i.modulo ? ` data-nk-modulo="${esc(i.modulo)}"` : ""}${i.alt ? ` data-nk-alt="${esc(i.alt)}"` : ""}${i.destaque ? ` style="background:rgba(242,139,24,.18);box-shadow:inset 3px 0 0 #F28B18;font-weight:700"` : ""}${i.soAdmin ? " data-nk-so-admin=\"1\"" : ""} title="${esc(i.rotulo)}">` +
      `<span class="v15-nav-icon">${ic(i.icone)}</span><span class="v15-nav-label">${esc(i.rotulo)}</span></a>`;
  }

  function menuHTML() {
    // V18.83: «/» recolhe o menu para só ícones (no computador); a escolha fica neste navegador.
    let h = `<div class="v15-brand"><div class="v15-brand-badge">N</div><div><strong>Noromak</strong><small>Sistema de Expedição</small></div>` +
      `<button type="button" class="nk-recolher" data-nk-recolher aria-label="Recolher menu" title="Recolher menu">«</button></div>`;
    for (const g of MENU) {
      const visiveis = g.itens.filter(i => !i.oculto);
      if (g.fixo) {
        h += `<div class="v15-nav-section">${esc(g.titulo)}</div>` + visiveis.map(itemHTML).join("");
        continue;
      }
      h += `<div class="v15-grp" data-v15-grp="${g.grupo}"><button type="button" class="v15-nav-section v15-grp-h" aria-expanded="true">` +
        `<span>${esc(g.titulo)}</span><span class="v15-grp-n">${visiveis.length}</span><span class="v15-grp-seta" aria-hidden="true">▾</span></button>` +
        `<div class="v15-grp-b">${visiveis.map(itemHTML).join("")}</div></div>`;
    }
    // V18.83: tema claro/escuro (nk-ui.js); some quando a tela não carrega o nk-ui.js.
    h += `<div class="v15-sidebar-footer"><button type="button" class="v15-nav-item nk-tema-btn" data-nk-tema="1" hidden><span class="v15-nav-icon"></span><span class="v15-nav-label"></span></button>`;
    // V18.95: filial que as telas mostram — só para quem vê todas as filiais (perfil Admin).
    h += `<label class="v15-nav-item nk-filial-sel" data-nk-filial="1" hidden title="Filial mostrada nas telas (Pedidos, Conferência, Etiquetas...)"><span class="v15-nav-icon">${ic("M12 21s-7-6.3-7-11a7 7 0 0 1 14 0c0 4.7-7 11-7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5")}</span>` +
      `<select class="nk-filial-select" aria-label="Filial"><option value="todas">Todas as filiais</option><option value="aracatuba">Araçatuba</option><option value="bauru">Bauru</option></select></label>`;
    // V18.93: avisos de venda (nk-avisos.js); só para quem tem o módulo Admin.
    h += `<button type="button" class="v15-nav-item" data-nk-avisos="1" data-nk-modulo="admin" hidden><span class="v15-nav-icon"></span><span class="v15-nav-label">Avisos de venda</span></button>`;
    h += `<button type="button" class="v15-nav-item" data-nk-sair="1"><span class="v15-nav-icon">${ic("M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9")}</span><span class="v15-nav-label">Sair</span></button></div>`;
    return h;
  }

  /* V18.68: grupos recolhíveis. Começa com Expedição aberta; o que cada um abre/fecha fica
     guardado neste navegador (mesma chave em todas as telas). O grupo da tela atual abre
     sozinho; se for fechado depois, o título ganha um ponto laranja. */
  function ligarGrupos(nav) {
    const CHAVE = "nk_menu_grupos_v1868";
    const grupos = [...nav.querySelectorAll(".v15-grp")];
    let abertos;
    try { abertos = JSON.parse(localStorage.getItem(CHAVE) || "null"); } catch (_) { abertos = null; }
    if (!Array.isArray(abertos)) abertos = ["expedicao"];
    const guardar = () => { try { localStorage.setItem(CHAVE, JSON.stringify(grupos.filter(g => !g.classList.contains("fechado")).map(g => g.dataset.v15Grp))); } catch (_) {} };
    const marcar = (g, aberto) => { g.classList.toggle("fechado", !aberto); g.querySelector(".v15-grp-h").setAttribute("aria-expanded", String(aberto)); };
    grupos.forEach(g => {
      marcar(g, abertos.includes(g.dataset.v15Grp));
      g.querySelector(".v15-grp-h").addEventListener("click", () => { marcar(g, g.classList.contains("fechado")); guardar(); });
    });
    let ativoAntes = null;
    const ativo = () => {
      const el = nav.querySelector(".v15-nav-item.active");
      grupos.forEach(g => g.classList.toggle("tem-ativo", !!el && g.contains(el)));
      if (el && el !== ativoAntes) { const g = el.closest(".v15-grp"); if (g && g.classList.contains("fechado")) { marcar(g, true); guardar(); } }
      ativoAntes = el;
    };
    new MutationObserver(ativo).observe(nav, { subtree: true, attributes: true, attributeFilter: ["class"] });
    ativo();
  }

  function marcarAtivo(nav) {
    const i = itemDoCaminho(caminhoRelativo());
    nav.querySelectorAll(".v15-nav-item").forEach(a => a.classList.toggle("active", !!i && a.dataset.nkId === i.id));
  }

  // Esconde o que o usuário não pode abrir (o servidor já recusa com 403; aqui é só para o
  // menu não oferecer). Supervisor = sem perfil Admin, mas com o módulo admin (V17.55).
  async function aplicarPermissoes(nav) {
    let user = window.EXPEDICAO_USER || null;
    if (!user) {
      try {
        const r = await fetch(`${B}/api/auth/session`, { cache: "no-store", credentials: "same-origin" });
        const d = await r.json();
        user = d && d.authenticated ? d.user : null;
      } catch (_) { return; }
    }
    if (!user) return;
    ligarFilial(nav, user);
    contarTarefas(nav);
    const perm = user.permissions || {};
    const pode = m => Object.prototype.hasOwnProperty.call(perm, m) ? !!perm[m] : (user.app_role === "admin" || m === "conferencia");
    if (pode("admin")) contarPerguntas(nav);
    const supervisor = user.app_role !== "admin" && pode("admin");
    nav.querySelectorAll(".v15-nav-item[data-nk-modulo]").forEach(a => {
      // V19.70: data-nk-alt = outro módulo que também abre a tela (perfil Compras na Ruptura/Demanda).
      const ok = (pode(a.dataset.nkModulo) || (!!a.dataset.nkAlt && pode(a.dataset.nkAlt))) && !(supervisor && a.dataset.nkSoAdmin === "1");
      a.hidden = !ok;
      if (!ok) a.style.display = "none";
    });
    nav.querySelectorAll(".v15-grp").forEach(g => {
      const n = [...g.querySelectorAll(".v15-nav-item")].filter(a => !a.hidden).length;
      g.style.display = n ? "" : "none";
      const c = g.querySelector(".v15-grp-n"); if (c) c.textContent = n;
    });
    // V19.70: quem só tem um grupo (perfil Compras) já o vê aberto.
    const visiveis = [...nav.querySelectorAll(".v15-grp")].filter(g => g.style.display !== "none");
    if (visiveis.length === 1 && visiveis[0].classList.contains("fechado")) {
      visiveis[0].classList.remove("fechado");
      visiveis[0].querySelector(".v15-grp-h")?.setAttribute("aria-expanded", "true");
    }
  }

  // V19.69: selo no item "Quadro de tarefas" com as tarefas abertas do usuário (vermelho se há atrasada).
  async function contarTarefas(nav) {
    const a = nav.querySelector('.v15-nav-item[data-nk-id="tarefas"]');
    if (!a) return;
    try {
      const r = await fetch(`${B}/api/tarefas/resumo`, { cache: "no-store", credentials: "same-origin" });
      const d = await r.json();
      if (!r.ok || !d.abertas) return;
      const s = document.createElement("span");
      s.className = "nk-tarefas-n";
      s.textContent = d.abertas;
      s.title = `${d.abertas} tarefa(s) sua(s) aberta(s)` + (d.atrasadas ? `, ${d.atrasadas} atrasada(s)` : "");
      s.style.cssText = `margin-left:auto;min-width:18px;height:18px;padding:0 5px;border-radius:9px;font-size:11px;font-weight:800;display:inline-grid;place-items:center;color:#fff;background:${d.atrasadas ? "#d03b3b" : "#3b6fd0"}`;
      a.appendChild(s);
    } catch (_) {}
  }

  // V20.01: selo no item "Pós-venda" com as perguntas do ML sem resposta (vermelho se alguma passou de 1 h).
  // Relê a cada 2 min com a aba à vista (uma consulta pequena, só ao banco).
  async function contarPerguntas(nav) {
    const a = nav.querySelector('.v15-nav-item[data-nk-id="posvenda"]');
    if (!a) return;
    const pintar = async () => {
      if (document.hidden) return;
      try {
        const r = await fetch(`${B}/api/posvenda/resumo`, { cache: "no-store", credentials: "same-origin" });
        const d = await r.json();
        if (!r.ok) return;
        let s = a.querySelector(".nk-perguntas-n");
        if (!d.sem_resposta) { if (s) s.remove(); return; }
        if (!s) { s = document.createElement("span"); s.className = "nk-perguntas-n"; a.appendChild(s); }
        s.textContent = d.sem_resposta;
        s.title = `${d.sem_resposta} pergunta(s) sem resposta` + (d.atrasadas ? `, ${d.atrasadas} há mais de ${d.atraso_horas} h` : "");
        s.style.cssText = `margin-left:auto;min-width:18px;height:18px;padding:0 5px;border-radius:9px;font-size:11px;font-weight:800;display:inline-grid;place-items:center;color:#fff;background:${d.atrasadas ? "#d03b3b" : "#3b6fd0"}`;
      } catch (_) {}
    };
    pintar();
    setInterval(pintar, 120000);
  }

  // V18.95: seletor de filial (quem vê "todas"). A escolha vale para todas as telas e abas
  // deste navegador; o servidor filtra pelo cabeçalho que o shared-storage.js manda.
  function ligarFilial(nav, user) {
    const rot = nav.querySelector("[data-nk-filial]");
    if (!rot) return;
    if (String(user.casa || "") !== "todas") { rot.hidden = true; rot.style.display = "none"; return; }
    rot.hidden = false;
    const sel = rot.querySelector("select");
    // V19.00: mostra a filial com que ESTA página trabalha (lida uma vez ao abrir, no
    // shared-storage.js), não a última escolhida em outra aba.
    let atual = "";
    if (window.appStorage && typeof window.appStorage.filialEscolhida === "function") atual = window.appStorage.filialEscolhida() || "";
    else { try { atual = localStorage.getItem("nk_filial_v1895") || ""; } catch (_) {} }
    sel.value = atual || "todas";
    sel.addEventListener("change", () => {
      if (window.appStorage && typeof window.appStorage.trocarFilial === "function") { window.appStorage.trocarFilial(sel.value); return; }
      try { if (sel.value === "todas") localStorage.removeItem("nk_filial_v1895"); else localStorage.setItem("nk_filial_v1895", sel.value); } catch (_) {}
      location.reload();
    });
  }

  const LUA = "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z";
  const SOL = "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4";
  function ligarTema(nav) {
    const btn = nav.querySelector("[data-nk-tema]");
    if (!btn) return;
    const desenhar = () => {
      if (!window.nkTema) { btn.hidden = true; return; }
      const escuro = window.nkTema.atual() === "escuro";
      btn.hidden = false;
      btn.querySelector(".v15-nav-icon").innerHTML = ic(escuro ? SOL : LUA);
      btn.querySelector(".v15-nav-label").textContent = escuro ? "Modo claro" : "Modo escuro";
      btn.title = escuro ? "Voltar para o modo claro" : "Usar o modo escuro";
    };
    btn.addEventListener("click", () => { if (window.nkTema) { window.nkTema.alternar(); desenhar(); } });
    document.addEventListener("nk-tema", desenhar);
    desenhar();
  }

  const CHAVE_RECOLHIDO = "nk_menu_recolhido_v1883";
  function ligarRecolher(nav, padraoRecolhido) {
    const btn = nav.querySelector("[data-nk-recolher]");
    if (!btn) return;
    let salvo = null;
    try { salvo = localStorage.getItem(CHAVE_RECOLHIDO); } catch (_) {}
    const aplicar = recolhido => {
      document.body.classList.toggle("nk-menu-recolhido", recolhido);
      btn.textContent = recolhido ? "»" : "«";
      btn.title = btn.ariaLabel = recolhido ? "Abrir o menu" : "Recolher menu";
    };
    aplicar(salvo === null ? !!padraoRecolhido : salvo === "1");
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const recolhido = !document.body.classList.contains("nk-menu-recolhido");
      try { localStorage.setItem(CHAVE_RECOLHIDO, recolhido ? "1" : "0"); } catch (_) {}
      aplicar(recolhido);
    });
  }

  function ligarSair(nav) {
    nav.addEventListener("click", e => {
      if (!e.target.closest("[data-nk-sair]")) return;
      if (typeof window.sairAdmin === "function") window.sairAdmin();
      else if (typeof window.expedicaoSair === "function") window.expedicaoSair();
      else location.href = `${B}/`;
    });
  }

  // Admin: desenha o menu no lugar do <script> que chamou (dentro do <nav>), ainda durante a
  // leitura da página — os scripts do Admin que vêm depois já encontram os itens.
  function nkMenuDesenhar(scriptAtual) {
    const nav = scriptAtual.closest("nav");
    scriptAtual.insertAdjacentHTML("beforebegin", menuHTML());
    ligarGrupos(nav);
    ligarSair(nav);
    ligarTema(nav);
    ligarRecolher(nav, false);
    marcarAtivo(nav);
    carregarAvisos();
    const permissoes = () => aplicarPermissoes(nav);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", permissoes); else permissoes();
    return nav;
  }

  // V18.83 — padrão Noromak: acima do título vai o nome do grupo do menu (ex.: "Estoque e compras"),
  // quando a tela ainda não tem o rótulo dela.
  function rotuloDaTela() {
    const it = itemDoCaminho(caminhoRelativo());
    const g = it && MENU.find(gr => gr.itens.some(i => i.id === it.id));
    const h1 = document.querySelector("h1");
    if (!g || !h1) return;
    const antes = h1.previousElementSibling;
    if (antes && /rotulo|eyebrow/i.test(antes.className || "")) return;
    const d = document.createElement("div");
    d.className = "nk-rotulo nk-rotulo-auto";
    d.textContent = g.titulo;
    h1.before(d);
  }

  // Demais telas: monta o menu à esquerda, o botão ☰ do celular e o fundo escuro da gaveta.
  function nkMenuShell(padraoRecolhido = false) {
    if (document.querySelector("nav.v15-sidebar")) return;
    document.body.classList.add("nk-shell");
    const nav = document.createElement("nav");
    nav.className = "v15-sidebar";
    nav.setAttribute("aria-label", "Navegação principal");
    nav.innerHTML = menuHTML();
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "nk-menu-btn"; btn.setAttribute("aria-label", "Abrir menu"); btn.textContent = "☰";
    const fundo = document.createElement("div");
    fundo.className = "nk-menu-fundo";
    const fechar = () => document.body.classList.remove("nk-menu-aberto");
    btn.onclick = () => document.body.classList.toggle("nk-menu-aberto");
    fundo.onclick = fechar;
    document.addEventListener("keydown", e => { if (e.key === "Escape") fechar(); });
    nav.addEventListener("click", e => { if (e.target.closest(".v15-nav-item")) fechar(); });
    document.body.prepend(nav);
    document.body.append(btn, fundo);
    // O "← Central Noromak" / "← Voltar ao Sistema" de cada tela vira redundante com o menu.
    document.querySelectorAll(`a[href="${B}/admin"],a[href="${B}/"]`).forEach(a => { if (/central noromak|voltar ao sistema/i.test(a.textContent || "")) a.style.display = "none"; });
    rotuloDaTela();
    ligarGrupos(nav);
    ligarSair(nav);
    ligarTema(nav);
    ligarRecolher(nav, padraoRecolhido === true);
    marcarAtivo(nav);
    aplicarPermissoes(nav);
    carregarAvisos();
  }

  // V18.93: avisos de venda — vem junto com o menu (a Conferência e a Estação, sem menu, ficam sem).
  function carregarAvisos() {
    if (window.nkAvisos) { window.nkAvisos.ligarBotoes(); return; }
    if (document.querySelector('script[data-nk-avisos-js]')) return;
    const s = document.createElement("script");
    s.src = `${B}/nk-avisos.js?v=20.01`;
    s.defer = true;
    s.dataset.nkAvisosJs = "1";
    document.head.append(s);
  }

  window.nkMenu = { MENU, ITENS, itemDoCaminho, abaDoCaminho, itemDaAba, caminhoRelativo, base: B };
  window.nkMenuDesenhar = nkMenuDesenhar;
  window.nkMenuShell = nkMenuShell;

  // Telas que pedem o menu com <script src="nk-menu.js" data-shell>: monta sozinho.
  const eu = document.currentScript;
  // data-shell="recolhido": a tela começa com o menu só de ícones (Precificador, que precisa de largura).
  const PADRAO_RECOLHIDO = !!(eu && eu.getAttribute("data-shell") === "recolhido");
  if (eu && eu.hasAttribute("data-shell")) {
    const montar = () => nkMenuShell(PADRAO_RECOLHIDO);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", montar); else montar();
  }
})();
