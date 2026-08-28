(() => {
  "use strict";
  const secure = location.protocol === "https:";
  const base = secure ? "https://localhost:9101/" : "http://localhost:9100/";
  const storageKey = "noromak_zebra_browser_print_uid";

  function xhr(method, path, body, timeout = 6500) {
    return new Promise((resolve, reject) => {
      let req;
      try {
        req = new XMLHttpRequest();
        req.open(method, base + path, true);
        req.timeout = timeout;
        req.onreadystatechange = () => {
          if (req.readyState !== XMLHttpRequest.DONE) return;
          if (req.status === 200) resolve(req.responseText || "");
          else reject(new Error((req.responseText || "").trim() || `Zebra Browser Print HTTP ${req.status || 0}`));
        };
        req.ontimeout = () => reject(new Error("Tempo esgotado ao conectar ao Zebra Browser Print."));
        req.onerror = () => reject(new Error(
          secure
            ? "Zebra Browser Print não respondeu em https://localhost:9101. Confirme se está aberto, autorizado para este site e com o certificado local aceito."
            : "Zebra Browser Print não respondeu em http://localhost:9100."
        ));
        if (body !== undefined) {
          req.setRequestHeader("Content-Type", "application/json");
          req.send(JSON.stringify(body));
        } else req.send();
      } catch (e) { reject(e); }
    });
  }

  function normalizeDevice(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      name: String(raw.name || ""),
      uid: String(raw.uid || ""),
      connection: String(raw.connection || ""),
      deviceType: String(raw.deviceType || "printer"),
      version: raw.version == null ? 2 : raw.version,
      provider: String(raw.provider || ""),
      manufacturer: String(raw.manufacturer || "")
    };
  }

  async function getDefault() {
    const raw = await xhr("GET", "default?type=printer");
    if (!raw.trim()) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.uid) return null;
    return normalizeDevice(obj);
  }

  async function list() {
    const raw = await xhr("GET", "available");
    const obj = raw.trim() ? JSON.parse(raw) : {};
    const arr = Array.isArray(obj) ? obj : (Array.isArray(obj.printer) ? obj.printer : (Array.isArray(obj.deviceList) ? obj.deviceList : []));
    return arr.map(normalizeDevice).filter(Boolean);
  }

  async function choose() {
    const devices = await list();
    const printers = devices.filter(d => d.deviceType === "printer" || !d.deviceType);
    const preferredUid = localStorage.getItem(storageKey) || "";
    let selected = printers.find(d => d.uid === preferredUid) || null;
    if (!selected) {
      try {
        const def = await getDefault();
        if (def) selected = printers.find(d => d.uid === def.uid) || def;
      } catch (_) {}
    }
    if (!selected && printers.length) selected = printers[0];
    if (selected && selected.uid) localStorage.setItem(storageKey, selected.uid);
    return { selected, devices: printers };
  }

  function setPreferred(uid) {
    if (uid) localStorage.setItem(storageKey, String(uid));
    else localStorage.removeItem(storageKey);
  }

  async function send(device, data) {
    if (!device || !device.uid) throw new Error("Nenhuma impressora Zebra foi selecionada.");
    const payload = { device: normalizeDevice(device), data: String(data == null ? "" : data) };
    return xhr("POST", "write", payload, 15000);
  }

  async function sendMany(device, documents, pauseMs = 120) {
    const docs = Array.isArray(documents) ? documents : [documents];
    for (let i = 0; i < docs.length; i++) {
      const text = String(docs[i] == null ? "" : docs[i]);
      if (!text.trim()) continue;
      // Browser Print document transfers are limited; sending each ZPL separately
      // also prevents one large job from blocking the whole queue.
      if (new Blob([text]).size > 2 * 1024 * 1024) {
        throw new Error(`Documento ${i + 1} excede 2 MB, limite do Zebra Browser Print.`);
      }
      await send(device, text);
      if (pauseMs && i < docs.length - 1) await new Promise(r => setTimeout(r, pauseMs));
    }
    return true;
  }

  async function probe() {
    const { selected, devices } = await choose();
    return {
      online: !!selected,
      selected,
      devices,
      base,
      secure,
      sslSupportUrl: "https://localhost:9101/ssl_support"
    };
  }

  window.NoromakZebraBrowserPrint = {
    base, secure, probe, list, getDefault, choose, setPreferred, send, sendMany,
    sslSupportUrl: "https://localhost:9101/ssl_support"
  };
})();