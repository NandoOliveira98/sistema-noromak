(() => {
  "use strict";
  const BASE = "/expedicao";
  let installPrompt = null;
  let installRequested = false;
  let waitTimer = null;

  const standalone = () =>
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const buttons = () => [...document.querySelectorAll("[data-pwa-install]")];
  const notes = () => [...document.querySelectorAll(".pwa-installed-note")];
  const appType = () => {
    const value = document.documentElement.dataset.pwaApp;
    return value === "admin" || value === "estacao" ? value : "conferencia";
  };
  const appLabel = () => appType() === "admin" ? "Administração" : appType() === "estacao" ? "Estação Zebra" : "Conferência";
  const readyLabel = () => appType() === "admin" ? "⬇ INSTALAR ADMIN" : appType() === "estacao" ? "⬇ INSTALAR ESTAÇÃO" : "⬇ INSTALAR CONFERÊNCIA";

  function setButtonState(state) {
    const installed = standalone();
    buttons().forEach(btn => {
      btn.hidden = installed;
      btn.disabled = state === "waiting";
      btn.classList.toggle("pwa-ready", state === "ready");
      btn.classList.toggle("pwa-waiting", state === "waiting");
      btn.textContent = state === "ready" ? readyLabel() : state === "waiting" ? "⏳ PREPARANDO INSTALAÇÃO…" : readyLabel();
      btn.title = installed ? "Aplicativo já instalado" : state === "ready" ? `Instalar ${appLabel()} no computador` : "Aguardando o Chrome liberar a instalação";
      btn.setAttribute("aria-label", btn.title);
    });
    document.documentElement.classList.toggle("pwa-standalone", installed);
  }

  function updateNotes(message) {
    notes().forEach(note => { if (message) note.textContent = message; });
  }

  function toast(message, action, persistent = false) {
    document.querySelector(".pwa-toast")?.remove();
    const el = document.createElement("div");
    el.className = "pwa-toast";
    const text = document.createElement("span");
    text.textContent = message;
    el.appendChild(text);
    if (action) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = action.label;
      b.addEventListener("click", action.run, {once:true});
      el.appendChild(b);
    }
    document.body.appendChild(el);
    if (!persistent) setTimeout(() => el.remove(), 11000);
    return el;
  }

  async function promptInstall() {
    if (!installPrompt || standalone()) return false;
    const prompt = installPrompt;
    installPrompt = null;
    installRequested = false;
    clearTimeout(waitTimer);
    await prompt.prompt();
    const choice = await prompt.userChoice.catch(() => ({outcome:"dismissed"}));
    if (choice.outcome === "accepted") {
      toast(`${appLabel()} foi instalado. O atalho aparecerá no menu Iniciar e poderá ser fixado na área de trabalho.`);
    } else {
      toast("Instalação cancelada. O botão será liberado novamente pelo navegador em uma próxima visita.");
    }
    setButtonState("idle");
    return choice.outcome === "accepted";
  }

  function waitForChrome() {
    installRequested = true;
    setButtonState("waiting");
    updateNotes("O Chrome precisa validar o aplicativo. Mantenha esta tela aberta por alguns segundos.");
    toast("Preparando a instalação. O Chrome exige uma interação e pode levar cerca de 30 segundos para liberar o instalador.", null, true);
    clearTimeout(waitTimer);
    waitTimer = setTimeout(() => {
      if (!installPrompt && !standalone()) {
        setButtonState("idle");
        updateNotes("Instalação ainda não liberada. Atualize com Ctrl+F5 e mantenha a tela aberta por 30 segundos.");
        toast("O Chrome ainda não liberou a instalação. Atualize com Ctrl+F5, permaneça nesta tela por 30 segundos e clique novamente. Se o app já estiver instalado, abra chrome://apps.");
        installRequested = false;
      }
    }, 45000);
  }

  async function installFromClick() {
    if (standalone()) return;
    if (installPrompt) {
      await promptInstall();
      return;
    }
    waitForChrome();
  }

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    installPrompt = event;
    clearTimeout(waitTimer);
    setButtonState("ready");
    updateNotes("Aplicativo pronto para instalar em uma janela separada.");
    if (installRequested) {
      document.querySelector(".pwa-toast")?.remove();
      toast(`${appLabel()} está pronto para instalar.`, {label:"INSTALAR AGORA", run:promptInstall}, true);
    }
  });

  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    installRequested = false;
    clearTimeout(waitTimer);
    setButtonState("idle");
    toast(`${appLabel()} instalado com sucesso.`);
  });

  document.addEventListener("DOMContentLoaded", () => {
    buttons().forEach(btn => btn.addEventListener("click", installFromClick));
    setButtonState("idle");
    if ("serviceWorker" in navigator && location.protocol === "https:") {
      navigator.serviceWorker.register(`${BASE}/sw.js?v=11.5`, {scope:`${BASE}/`, updateViaCache:"none"})
        .then(reg => reg.update().catch(() => {}))
        .catch(err => console.warn("PWA: service worker não registrado", err));
    }
  });
})();
