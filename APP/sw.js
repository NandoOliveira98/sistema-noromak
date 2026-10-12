const CACHE = "noromak-expedicao-pwa-19.03";
// V18.82: telas do Admin com endereço próprio — sem rede, caem na cópia do Admin.
const ROTAS_ADMIN_V1882 = /\/(dashboard|pedidos-pendentes|pedidos-dia|coleta|etiquetas|historico|historico-sku|produtos|kits|integracoes|usuarios|seguranca)\/?$/i;
const SHELL = [
  "/expedicao/",
  "/expedicao/acesso",
  "/expedicao/admin",
  "/expedicao/conferencia",
  "/expedicao/estacao-impressao",
  "/expedicao/shared-storage.js",
  "/expedicao/web-auth.js",
  "/expedicao/pwa-install.js?v=14.9",
  "/expedicao/pwa-install.css?v=14.9"
];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => Promise.allSettled(SHELL.map(url => cache.add(url)))).then(()=>self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("noromak-expedicao-pwa-") && k !== CACHE).map(k => caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith("/expedicao/")) return;
  if (url.pathname.includes("/api/") || url.pathname.endsWith("/sw.js")) return;
  const alwaysFresh = url.pathname.endsWith(".webmanifest") || url.pathname.includes("/icons/") || url.pathname.endsWith("/pwa-install.js") || url.pathname.endsWith("/pwa-install.css");
  if (alwaysFresh) {
    event.respondWith(fetch(req, {cache:"no-store"}));
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).then(res => { const copy=res.clone(); caches.open(CACHE).then(c=>c.put(req,copy)); return res; }).catch(async()=> (await caches.match(req)) || ((url.pathname.includes("admin") || ROTAS_ADMIN_V1882.test(url.pathname)) ? caches.match("/expedicao/admin") : url.pathname.includes("conferencia") ? caches.match("/expedicao/conferencia") : caches.match("/expedicao/"))));
    return;
  }
  event.respondWith(caches.match(req).then(cached => {
    const fresh = fetch(req).then(res => { if(res.ok){const copy=res.clone(); caches.open(CACHE).then(c=>c.put(req,copy));} return res; }).catch(()=>cached);
    return cached || fresh;
  }));
});

// V18.93 — avisos de venda (Web Push). O push chega VAZIO; o aparelho pede ao Noromak o
// que chegou. Com uma tela do Noromak à vista, ela mostra o popup com som (nk-avisos.js);
// sem tela à vista (site fechado, celular bloqueado), vira notificação do sistema.
const AVISO_ICONE = "/expedicao/icons/admin-192.png?v=18.94";
const AVISO_ABRIR = "/expedicao/dashboard";
// Conferência, Estação e login não carregam o nk-avisos.js: não servem para o popup.
const SEM_POPUP = /\/(conferencia|estacao-impressao|acesso)(\.html)?\/?$/i;
const ehSafari = () => { const ua = (self.navigator && self.navigator.userAgent) || ""; return /Safari/.test(ua) && !/Chrome|Chromium|CriOS|Edg|Android/.test(ua); };
self.addEventListener("push", event => {
  event.waitUntil((async () => {
    let vendas = [], total = 0;
    try {
      const sub = await self.registration.pushManager.getSubscription();
      const r = await fetch("/expedicao/api/avisos-venda/entregar", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({endpoint: sub ? sub.endpoint : ""}),
      });
      const d = await r.json();
      vendas = Array.isArray(d.vendas) ? d.vendas : [];
      total = Number(d.total) || vendas.length;
    } catch (_) {}
    const janelas = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const aVista = janelas.filter(c => c.visibilityState === "visible" && !SEM_POPUP.test(new URL(c.url).pathname));
    const tela = aVista.find(c => c.focused) || aVista[0];
    if (tela && vendas.length) {
      tela.postMessage({tipo: "nk-avisos-venda", vendas});
      // O Safari (iPhone) exige notificação a cada push; os outros dispensam com a tela à vista.
      if (!ehSafari()) return;
    }
    if (!vendas.length) {
      return self.registration.showNotification("Noromak", {body: "Nova venda. Abra o Noromak para ver.", tag: "nk-venda", icon: AVISO_ICONE, badge: AVISO_ICONE, data: {url: AVISO_ABRIR}});
    }
    const mostrar = vendas.slice(-3);
    for (const v of mostrar) {
      await self.registration.showNotification(v.titulo || "Nova venda", {
        body: v.corpo || "", tag: `nk-venda-${v.id}`, icon: AVISO_ICONE, badge: AVISO_ICONE,
        vibrate: [200, 100, 200], timestamp: Date.parse(v.venda_em) || Date.now(), data: {url: v.url || AVISO_ABRIR},
      });
    }
    if (total > mostrar.length) {
      await self.registration.showNotification(`+${total - mostrar.length} venda(s)`, {body: "Veja todas no Dashboard do Noromak.", tag: "nk-venda-mais", icon: AVISO_ICONE, badge: AVISO_ICONE, data: {url: AVISO_ABRIR}});
    }
  })());
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || AVISO_ABRIR;
  event.waitUntil((async () => {
    const janelas = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const aberta = janelas.find(c => new URL(c.url).pathname.startsWith("/expedicao") && !SEM_POPUP.test(new URL(c.url).pathname));
    if (aberta) { try { return await aberta.focus(); } catch (_) {} }
    return self.clients.openWindow(url);
  })());
});
