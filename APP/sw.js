const CACHE = "noromak-expedicao-pwa-8.8";
const SHELL = [
  "/expedicao/",
  "/expedicao/acesso",
  "/expedicao/admin",
  "/expedicao/conferencia",
  "/expedicao/shared-storage.js",
  "/expedicao/web-auth.js",
  "/expedicao/pwa-install.js?v=8.8",
  "/expedicao/pwa-install.css?v=8.8"
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
    event.respondWith(fetch(req).then(res => { const copy=res.clone(); caches.open(CACHE).then(c=>c.put(req,copy)); return res; }).catch(async()=> (await caches.match(req)) || (url.pathname.includes("admin") ? caches.match("/expedicao/admin") : url.pathname.includes("conferencia") ? caches.match("/expedicao/conferencia") : caches.match("/expedicao/"))));
    return;
  }
  event.respondWith(caches.match(req).then(cached => {
    const fresh = fetch(req).then(res => { if(res.ok){const copy=res.clone(); caches.open(CACHE).then(c=>c.put(req,copy));} return res; }).catch(()=>cached);
    return cached || fresh;
  }));
});
