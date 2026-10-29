/* V18.93 — avisos de venda: popup com som no canto da tela e notificação no celular ou
   no computador mesmo com o site fechado (Web Push).
   Carregado pelo nk-menu.js (todas as telas com menu; a Conferência e a Estação ficam sem,
   de propósito: lá o barulho atrapalha a bipagem). Liga-se POR APARELHO, no botão
   "Avisos de venda" do rodapé do menu; só quem tem o módulo Admin vê o botão.
   - Push ativo: o service worker (sw.js) recebe o aviso; com esta tela à vista, ele manda
     as vendas para cá (popup + som); sem tela à vista, vira notificação do sistema.
   - Sem push (navegador sem suporte ou notificação bloqueada): enquanto a tela está
     aberta, consulta /api/avisos-venda/recentes a cada 30 s.
   Só avisa: nada aqui grava pedido ou storage. */
(function () {
  "use strict";
  if (window.nkAvisos) return;
  const B = "/expedicao";
  const SW_URL = `${B}/sw.js?v=14.5`; // o MESMO endereço do pwa-install.js (outro endereço reinstala o worker)
  const CHAVE_LIGADO = "nk_avisos_venda_v1893";
  const CHAVE_ULTIMO = "nk_avisos_ultimo_v1893";
  const CHAVE_SOM = "nk_avisos_som_v1893";
  const POLL_MS = 30000;
  const ABRIR = `${B}/dashboard`;

  const ler = (k, padrao) => { try { const v = localStorage.getItem(k); return v === null ? padrao : v; } catch (_) { return padrao; } };
  const gravar = (k, v) => { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, String(v)); } catch (_) {} };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  const avisar = (msg, tipo) => { if (window.nkToast) window.nkToast(msg, tipo); else console.info(msg); };

  const ehIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const instalado = window.matchMedia && (matchMedia("(display-mode: standalone)").matches || navigator.standalone === true);
  const temPush = () => window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const ligado = () => ler(CHAVE_LIGADO, "0") === "1";
  const somLigado = () => ler(CHAVE_SOM, "1") === "1";

  /* ---------- som (gerado na hora: sem arquivo de áudio) ---------- */
  let audio = null;
  function destravarSom() {
    try {
      if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
    } catch (_) {}
  }
  // O navegador só deixa tocar som depois de um clique/tecla na página.
  ["pointerdown", "keydown", "touchstart"].forEach(ev => window.addEventListener(ev, destravarSom, { passive: true }));
  function tocar() {
    if (!somLigado() || !audio || audio.state !== "running") return;
    const t0 = audio.currentTime + 0.02;
    [[988, 0], [1319, 0.13], [1760, 0.26]].forEach(([freq, atraso]) => {
      const o = audio.createOscillator();
      const g = audio.createGain();
      o.type = "sine";
      o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t0 + atraso);
      g.gain.exponentialRampToValueAtTime(0.35, t0 + atraso + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + atraso + 0.45);
      o.connect(g).connect(audio.destination);
      o.start(t0 + atraso);
      o.stop(t0 + atraso + 0.5);
    });
  }

  /* ---------- popup no canto ---------- */
  function estilo() {
    if (document.getElementById("nk-avisos-css")) return;
    const st = document.createElement("style");
    st.id = "nk-avisos-css";
    st.textContent = `
.nk-vendas{position:fixed;top:16px;right:16px;z-index:2147483001;display:grid;gap:8px;width:min(360px,calc(100vw - 32px));pointer-events:none}
.nk-venda{pointer-events:auto;cursor:pointer;display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:start;background:var(--nk-tile,#fff);color:var(--nk-ink,#18324A);border:1px solid var(--nk-line,#DCE5EF);border-left:4px solid var(--nk-ok,#1B7A3E);border-radius:12px;padding:12px 10px 12px 12px;box-shadow:var(--nk-sombra2,0 12px 32px rgba(18,52,91,.16));font:13px/1.45 var(--nk-font,system-ui,sans-serif);animation:nk-venda-entra .25s ease-out}
.nk-venda.ml{border-left-color:#E6B800}.nk-venda.shopee{border-left-color:#EE4D2D}
.nk-venda-selo{font:700 11px/1 var(--nk-font,system-ui,sans-serif);padding:6px 7px;border-radius:7px;background:var(--nk-ok-bg,#E7F4EC);color:var(--nk-ok,#1B7A3E);margin-top:1px}
.nk-venda.ml .nk-venda-selo{background:#FFF6CC;color:#6B5400}.nk-venda.shopee .nk-venda-selo{background:#FDE7E2;color:#A3311B}
.nk-venda b{display:block;font-size:14px;color:var(--nk-head,#16344F)}
.nk-venda span{display:block;color:var(--nk-ink2,#586A7D);overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.nk-venda button{border:0;background:none;color:var(--nk-ink3,#7A8C9E);font-size:18px;line-height:1;cursor:pointer;padding:0 2px}
.nk-venda.sai{opacity:0;transform:translateX(16px);transition:.25s}
@keyframes nk-venda-entra{from{opacity:0;transform:translateY(-10px) scale(.98)}}
@media(max-width:640px){.nk-vendas{top:10px;right:10px;left:10px;width:auto}}
.nk-avisos-menu{position:fixed;z-index:2147483002;background:var(--nk-tile,#fff);color:var(--nk-ink,#18324A);border:1px solid var(--nk-line,#DCE5EF);border-radius:12px;box-shadow:var(--nk-sombra2,0 12px 32px rgba(18,52,91,.16));padding:8px;display:grid;gap:4px;min-width:230px;font:13px/1.4 var(--nk-font,system-ui,sans-serif)}
.nk-avisos-menu p{margin:4px 8px 6px;color:var(--nk-ink2,#586A7D);font-size:12px}
.nk-avisos-menu button{all:unset;cursor:pointer;padding:8px 10px;border-radius:8px}
.nk-avisos-menu button:hover{background:var(--nk-hover,#FAFCFE)}
.nk-avisos-menu button.perigo{color:var(--nk-bad,#B42318)}
.nk-avisos-on .v15-nav-icon{color:var(--nk-ok,#1B7A3E)}`;
    document.head.append(st);
  }

  function area() {
    let a = document.querySelector(".nk-vendas");
    if (!a) { a = document.createElement("div"); a.className = "nk-vendas"; a.setAttribute("aria-live", "polite"); document.body.append(a); }
    return a;
  }

  function mostrar(vendas) {
    // Várias abas abertas: cada venda aparece uma vez só (o último id mostrado é comum às abas).
    const ultimo = Number(ler(CHAVE_ULTIMO, "0")) || 0;
    const novas = (vendas || []).filter(v => !v.id || Number(v.id) > ultimo);
    if (!novas.length) return;
    const maior = Math.max(ultimo, ...novas.map(v => Number(v.id) || 0));
    gravar(CHAVE_ULTIMO, maior);
    estilo();
    const a = area();
    novas.slice(-4).forEach(v => {
      const canal = v.marketplace === "ml" ? "ML" : v.marketplace === "shopee" ? "Shopee" : v.marketplace === "full" ? "Full" : v.marketplace === "catalogo" ? "Catálogo" : v.marketplace === "pergunta" ? "Pergunta" : "Teste";
      const el = document.createElement("div");
      el.className = `nk-venda ${v.marketplace || ""}`;
      el.setAttribute("role", "status");
      el.innerHTML = `<div class="nk-venda-selo">${esc(canal)}</div><div><b>${esc(v.titulo)}</b><span>${esc(v.corpo)}</span></div><button type="button" aria-label="Fechar">×</button>`;
      const fechar = () => { el.classList.add("sai"); setTimeout(() => el.remove(), 260); };
      el.querySelector("button").addEventListener("click", e => { e.stopPropagation(); fechar(); });
      // V20.01: o aviso que traz o próprio endereço (pergunta → Pós-venda) abre nele.
      el.addEventListener("click", () => window.open(v.url ? v.url : v.marketplace === "full" ? `${B}/analise-full` : v.marketplace === "catalogo" ? `${B}/catalogo-ml` : ABRIR, "_blank"));
      a.append(el);
      setTimeout(fechar, 15000);
    });
    while (a.children.length > 5) a.firstElementChild.remove();
    tocar();
  }

  /* ---------- push ---------- */
  async function registro() {
    let reg = await navigator.serviceWorker.getRegistration(`${B}/`);
    if (!reg) reg = await navigator.serviceWorker.register(SW_URL, { scope: `${B}/`, updateViaCache: "none" });
    await navigator.serviceWorker.ready;
    return reg;
  }
  const bytesDaChave = b64 => {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const bin = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(bin, c => c.charCodeAt(0));
  };
  async function api(caminho, corpo) {
    const r = await fetch(`${B}${caminho}`, corpo === undefined ? { cache: "no-store", credentials: "same-origin" } : {
      method: "POST", cache: "no-store", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
    });
    let d = {};
    try { d = await r.json(); } catch (_) {}
    if (!r.ok || d.ok === false) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }
  function aparelho() {
    const ua = navigator.userAgent;
    const so = /Android/.test(ua) ? "Android" : ehIOS ? "iPhone/iPad" : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : "Outro";
    const nav = /Edg\//.test(ua) ? "Edge" : /Firefox/.test(ua) ? "Firefox" : /Chrome|CriOS/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "Navegador";
    return `${nav} · ${so}${instalado ? " (app)" : ""}`;
  }
  async function inscricaoAtual() {
    if (!temPush()) return null;
    try { const reg = await navigator.serviceWorker.getRegistration(`${B}/`); return reg ? await reg.pushManager.getSubscription() : null; } catch (_) { return null; }
  }

  async function ligar() {
    destravarSom();
    if (ehIOS && !instalado) {
      avisar("No iPhone o aviso só funciona com o Noromak instalado: toque em Compartilhar → \"Adicionar à Tela de Início\", abra o Noromak pelo ícone e ligue os avisos por lá.", "aviso");
      return;
    }
    const { chave, ultimo_id } = await api("/api/avisos-venda/chave");
    if (!(Number(ler(CHAVE_ULTIMO, "0")) > 0)) gravar(CHAVE_ULTIMO, ultimo_id || 0);
    if (!temPush()) {
      gravar(CHAVE_LIGADO, "1");
      avisar("Este navegador não recebe aviso com o site fechado. O popup com som vai aparecer enquanto o Noromak estiver aberto.", "aviso");
      return;
    }
    const permissao = await Notification.requestPermission();
    if (permissao !== "granted") {
      gravar(CHAVE_LIGADO, "1");
      avisar("O navegador bloqueou as notificações deste site. O popup com som vai aparecer só com o Noromak aberto. Para receber com o site fechado, libere as notificações no cadeado da barra de endereço.", "aviso");
      return;
    }
    const reg = await registro();
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      // Inscrição antiga com outra chave do servidor não recebe nada: refaz.
      const atual = sub.options && sub.options.applicationServerKey;
      const nova = bytesDaChave(chave);
      if (atual && new Uint8Array(atual).join() !== nova.join()) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytesDaChave(chave) });
    await api("/api/avisos-venda/inscrever", { inscricao: sub.toJSON(), aparelho: aparelho() });
    gravar(CHAVE_LIGADO, "1");
    avisar("Avisos de venda ligados neste aparelho. Use \"Enviar teste\" para conferir.", "ok");
  }

  async function desligar() {
    const sub = await inscricaoAtual();
    if (sub) {
      try { await api("/api/avisos-venda/desinscrever", { endpoint: sub.endpoint, aparelho: aparelho() }); } catch (_) {}
      try { await sub.unsubscribe(); } catch (_) {}
    }
    gravar(CHAVE_LIGADO, "0");
    avisar("Avisos de venda desligados neste aparelho.", "ok");
  }

  async function testar() {
    destravarSom();
    const sub = await inscricaoAtual();
    if (!sub) {
      mostrar([{ id: 0, marketplace: "teste", titulo: "Teste de aviso de venda", corpo: "Só nesta tela: este navegador não recebe aviso com o site fechado." }]);
      return;
    }
    await api("/api/avisos-venda/testar", { endpoint: sub.endpoint });
    avisar("Teste enviado. Deve chegar em alguns segundos.", "info");
  }

  /* ---------- tela aberta sem push: consulta periódica ---------- */
  let timer = null;
  async function consultar() {
    if (!ligado() || document.hidden) return;
    if (await inscricaoAtual()) return; // o push já entrega
    try {
      const desde = Number(ler(CHAVE_ULTIMO, "0")) || 0;
      const d = await api(`/api/avisos-venda/recentes?desde=${desde}`);
      if (!desde) { gravar(CHAVE_ULTIMO, d.ultimo_id || 0); return; }
      mostrar(d.vendas || []);
    } catch (_) {}
  }
  function iniciarConsulta() {
    if (timer) return;
    timer = setInterval(consultar, POLL_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) consultar(); });
    consultar();
  }

  /* ---------- botão do menu ---------- */
  const SINO = "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0";
  const icone = d => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
  function desenharBotoes() {
    document.querySelectorAll("[data-nk-avisos]").forEach(btn => {
      const on = ligado();
      btn.classList.toggle("nk-avisos-on", on);
      btn.querySelector(".v15-nav-icon").innerHTML = icone(SINO);
      btn.querySelector(".v15-nav-label").textContent = on ? "Avisos de venda: ligados" : "Ligar avisos de venda";
      btn.title = on ? "Avisos de venda ligados neste aparelho" : "Receber um aviso com som a cada venda do ML e da Shopee";
    });
  }
  function fecharMenu() { document.querySelector(".nk-avisos-menu")?.remove(); }
  function abrirMenu(btn) {
    fecharMenu();
    estilo();
    const m = document.createElement("div");
    m.className = "nk-avisos-menu";
    m.innerHTML = `<p>Avisos de venda neste aparelho</p><button type="button" data-a="testar">Enviar teste</button>` +
      `<button type="button" data-a="som">${somLigado() ? "Som do popup: ligado" : "Som do popup: desligado"}</button>` +
      `<button type="button" data-a="desligar" class="perigo">Desligar neste aparelho</button>`;
    document.body.append(m);
    const r = btn.getBoundingClientRect();
    m.style.left = `${Math.min(r.right + 8, innerWidth - m.offsetWidth - 8)}px`;
    m.style.top = `${Math.max(8, Math.min(r.top, innerHeight - m.offsetHeight - 8))}px`;
    if (r.right + 8 + m.offsetWidth > innerWidth) { m.style.left = `${Math.max(8, r.left)}px`; m.style.top = `${Math.max(8, r.top - m.offsetHeight - 8)}px`; }
    m.addEventListener("click", async e => {
      const a = e.target.closest("button")?.dataset.a;
      if (!a) return;
      fecharMenu();
      try {
        if (a === "testar") await testar();
        if (a === "som") { gravar(CHAVE_SOM, somLigado() ? "0" : "1"); destravarSom(); if (somLigado()) tocar(); avisar(somLigado() ? "Som do popup ligado." : "Som do popup desligado.", "ok"); }
        if (a === "desligar") { await desligar(); desenharBotoes(); }
      } catch (err) { avisar(`Não foi possível: ${err.message}`, "erro"); }
    });
    setTimeout(() => document.addEventListener("click", function fora(e) { if (!m.contains(e.target)) { fecharMenu(); document.removeEventListener("click", fora); } }), 0);
  }
  function ligarBotoes() {
    document.querySelectorAll("[data-nk-avisos]").forEach(btn => {
      if (btn.dataset.nkAvisosPronto) return;
      btn.dataset.nkAvisosPronto = "1";
      btn.addEventListener("click", async e => {
        e.preventDefault();
        e.stopPropagation();
        if (ligado()) { abrirMenu(btn); return; }
        btn.disabled = true;
        try { await ligar(); if (ligado()) iniciarConsulta(); }
        catch (err) { avisar(`Não foi possível ligar os avisos: ${err.message}`, "erro"); }
        finally { btn.disabled = false; desenharBotoes(); }
      });
    });
    desenharBotoes();
  }

  /* ---------- início ---------- */
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", e => {
      if (e.data && e.data.tipo === "nk-avisos-venda") mostrar(e.data.vendas);
    });
  }
  window.addEventListener("storage", e => { if (e.key === CHAVE_LIGADO) desenharBotoes(); });
  const iniciar = async () => {
    ligarBotoes();
    if (!ligado()) return;
    // Ligado aqui, mas a inscrição sumiu (navegador limpou ou bloqueou): segue só com a tela aberta.
    iniciarConsulta();
    // Renova a inscrição no servidor (o aparelho pode ter sido removido por falha de envio).
    const sub = await inscricaoAtual();
    if (sub && Notification.permission === "granted") {
      try { await api("/api/avisos-venda/inscrever", { inscricao: sub.toJSON(), aparelho: aparelho(), renovar: true }); } catch (_) {}
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar, { once: true }); else iniciar();

  window.nkAvisos = { ligar, desligar, testar, mostrar, ligarBotoes };
})();
