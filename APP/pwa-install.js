(() => {
  "use strict";
  const BASE = "/expedicao";
  let installPrompt = null;
  const standalone = () => window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const buttons = () => [...document.querySelectorAll("[data-pwa-install]")];
  function appLabel(){ return document.documentElement.dataset.pwaApp === "admin" ? "Administração" : "Conferência"; }
  function updateButtons(){
    const installed = standalone();
    buttons().forEach(btn => {
      btn.hidden = installed;
      btn.title = installed ? "Aplicativo já está aberto em modo instalado" : `Instalar ${appLabel()} no computador`;
      btn.setAttribute("aria-label", btn.title);
    });
    document.documentElement.classList.toggle("pwa-standalone", installed);
  }
  function toast(message, action){
    document.querySelector(".pwa-toast")?.remove();
    const el=document.createElement("div"); el.className="pwa-toast"; el.textContent=message;
    if(action){ const b=document.createElement("button"); b.type="button"; b.textContent=action.label; b.onclick=action.run; el.appendChild(document.createElement("br")); el.appendChild(b); }
    document.body.appendChild(el); setTimeout(()=>el.remove(),9000);
  }
  async function install(){
    if (standalone()) return;
    if (!installPrompt){
      toast(`Para instalar ${appLabel()}, abra o menu do Chrome/Edge e escolha “Instalar aplicativo” ou “Adicionar à área de trabalho”.`);
      return;
    }
    const prompt = installPrompt; installPrompt = null;
    await prompt.prompt();
    const choice = await prompt.userChoice.catch(()=>({outcome:"dismissed"}));
    if(choice.outcome === "accepted") toast(`${appLabel()} foi instalado. Um ícone foi criado no Windows.`);
    updateButtons();
  }
  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault(); installPrompt = event; updateButtons();
  });
  window.addEventListener("appinstalled", () => { installPrompt=null; updateButtons(); toast(`${appLabel()} instalado com sucesso.`); });
  document.addEventListener("DOMContentLoaded", () => {
    buttons().forEach(btn => btn.addEventListener("click", install)); updateButtons();
    if ("serviceWorker" in navigator && location.protocol === "https:") {
      navigator.serviceWorker.register(`${BASE}/sw.js`, {scope:`${BASE}/`}).catch(err => console.warn("PWA: service worker não registrado", err));
    }
  });
})();
