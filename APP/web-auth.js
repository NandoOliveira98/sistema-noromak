(() => {
  "use strict";

  // O NoromakWeb continua protegendo o endereço externo, mas a Expedição usa
  // um segundo login próprio para identificar quem realmente está no estoque.
  window.EXPEDICAO_WEB_AUTH = true;

  const BASE = location.pathname.startsWith("/expedicao") ? "/expedicao" : "";
  const API_SESSION = `${BASE}/api/auth/session`;
  const API_LOGIN = `${BASE}/api/auth/login`;
  const API_LOGOUT = `${BASE}/api/auth/logout`;

  const txt = value => String(value ?? "").trim();
  const profileLabel = role => role === "admin" ? "Admin" : "Operador";
  const permission = (user, module) => {
    const p = user?.permissions || {};
    if (Object.prototype.hasOwnProperty.call(p, module)) return !!p[module];
    if (user?.app_role === "admin") return true; // compatibilidade durante a migração
    return module === "conferencia";
  };

  function pageKind() {
    const path = location.pathname.replace(/\/+$/, "");
    if (/\/(admin|administracao)(\.html)?$/i.test(path)) return "admin";
    if (/\/(conferencia|pedidos)(\.html)?$/i.test(path)) return "conference";
    return "access";
  }

  function clearLegacySession() {
    for (const key of ["conf_sessao_ativa", "conf_admin_logado", "conf_admin_sessao"]) {
      try { localStorage.removeItem(key); } catch (_) {}
      try { sessionStorage.removeItem(key); } catch (_) {}
    }
  }

  // Executa imediatamente, antes dos scripts antigos tentarem restaurar um
  // administrador salvo no navegador.
  clearLegacySession();

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json; charset=utf-8" } : {}),
        ...(options.headers || {})
      }
    });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[char]));
  }

  function setError(id, message) {
    const el = document.getElementById(id);
    if (el) el.textContent = message || "";
  }

  function setLocalSession(user) {
    const session = {
      Nome: user.nome || user.username,
      Login: user.username,
      Perfil: profileLabel(user.app_role)
    };
    window.EXPEDICAO_USER = user;
    window.usuarioLogado = session;
    try {
      localStorage.setItem("conf_sessao_ativa", JSON.stringify(session));
      if (permission(user, "admin")) {
        localStorage.setItem("conf_admin_logado", "true");
        localStorage.setItem("conf_admin_sessao", JSON.stringify(session));
      }
    } catch (_) {}
    return session;
  }

  function addUserPill(user) {
    document.getElementById("webUserPill")?.remove();
    const target = document.querySelector(".header-topo > div:last-child") || document.querySelector(".top-bar") || document.body;
    const pill = document.createElement("span");
    pill.id = "webUserPill";
    pill.style.cssText = "display:inline-flex;align-items:center;padding:7px 10px;border-radius:999px;background:#e8f1fb;color:#2B4E75;border:1px solid #b9cce0;font:700 11px Segoe UI,Arial";
    pill.textContent = `${user.nome || user.username} · ${profileLabel(user.app_role)}`;
    if (target === document.body) {
      pill.style.position = "fixed";
      pill.style.top = "10px";
      pill.style.right = "10px";
      pill.style.zIndex = "10000";
    } else {
      target.prepend(pill);
    }
  }

  async function logout(reloadTo = `${BASE}/`) {
    try { await jsonFetch(API_LOGOUT, { method: "POST", body: "{}" }); } catch (_) {}
    clearLegacySession();
    location.href = reloadTo;
  }

  async function performLogin(username, password, area, errorId, button) {
    username = txt(username).toLowerCase();
    password = String(password ?? "");
    setError(errorId, "");
    if (!username || !password) {
      setError(errorId, "Preencha o usuário e a senha.");
      return;
    }
    const oldText = button?.textContent;
    try {
      if (button) { button.disabled = true; button.textContent = "ENTRANDO..."; }
      await jsonFetch(API_LOGIN, {
        method: "POST",
        body: JSON.stringify({ username, password, area })
      });
      // Recarrega para que todos os dados compartilhados sejam buscados já com
      // a sessão autenticada no servidor.
      location.reload();
    } catch (error) {
      setError(errorId, error.message || String(error));
      if (button) { button.disabled = false; button.textContent = oldText || "ENTRAR"; }
    }
  }

  function activateConference(user) {
    const session = setLocalSession(user);
    const login = document.getElementById("telaLogin");
    if (login) login.style.display = "none";
    window.usuarioLogado = session;
    // A tela legada declara `usuarioLogado` com `let`, portanto essa variável
    // não é a mesma propriedade de window. Use a ponte exposta pela própria
    // tela para atualizar a variável interna antes de iniciar a sessão.
    if (typeof window.ativarSessaoWebConferencia === "function") {
      window.ativarSessaoWebConferencia(session);
    } else if (typeof window.iniciarSessao === "function") {
      window.iniciarSessao();
    }
    addUserPill(user);
    const logoff = document.querySelector(".btn-logoff");
    if (logoff) logoff.textContent = "SAIR DO USUÁRIO";
    window.fazerLogoff = async function() {
      try {
        if (typeof window.liberarBloqueioPedidoAtual === "function") window.liberarBloqueioPedidoAtual();
      } catch (_) {}
      await logout(`${BASE}/conferencia`);
    };
  }

  function activateAdmin(user) {
    if (!permission(user, "admin")) {
      showAdminLogin("Este usuário não possui acesso ao Painel Administrativo.");
      return;
    }
    setLocalSession(user);
    const login = document.getElementById("loginAdmin");
    const panel = document.getElementById("painelConteudo");
    if (login) login.style.display = "none";
    if (panel) panel.style.display = "block";
    if (typeof window.inicializarBancoInternoAdmin === "function") window.inicializarBancoInternoAdmin();
    else if (typeof window.carregarLogs === "function") window.carregarLogs();
    addUserPill(user);
    const exitButton = document.querySelector(".btn-sair");
    if (exitButton) exitButton.textContent = "Sair do usuário";
    window.sairAdmin = async function() { await logout(`${BASE}/admin`); };
  }

  function showAdminLogin(message = "") {
    const login = document.getElementById("loginAdmin");
    const panel = document.getElementById("painelConteudo");
    if (panel) panel.style.display = "none";
    if (login) login.style.display = "flex";
    setError("erroLoginAdmin", message);
    document.querySelectorAll("#loginAdmin .btn-solicitar-acesso").forEach(el => el.style.display = "none");
  }

  function showConferenceLogin(message = "") {
    const login = document.getElementById("telaLogin");
    if (login) login.style.display = "flex";
    setError("erroLogin", message);
    document.querySelectorAll("#telaLogin .btn-solicitar-acesso").forEach(el => el.style.display = "none");
  }

  function installAdminLogin() {
    showAdminLogin();
    window.autenticarAdmin = function() {
      const button = document.querySelector("#loginAdmin button[onclick*='autenticarAdmin']");
      performLogin(
        document.getElementById("usuarioAdmin")?.value,
        document.getElementById("senhaAdmin")?.value,
        "admin",
        "erroLoginAdmin",
        button
      );
    };
  }

  function installConferenceLogin() {
    showConferenceLogin();
    window.fazerLogin = function() {
      const button = document.querySelector("#telaLogin .btn-entrar");
      performLogin(
        document.getElementById("usuarioInput")?.value,
        document.getElementById("senhaInput")?.value,
        "conferencia",
        "erroLogin",
        button
      );
    };
  }

  function renderAccessLogin(message = "") {
    const host = document.getElementById("webAccessPortal");
    if (!host) return;
    host.innerHTML = `
      <form id="stockAccessForm" class="stock-login" autocomplete="off">
        <h2>Login dos usuários do estoque</h2>
        <p>Use o usuário e a senha cadastrados no Painel Administrativo da Expedição.</p>
        <label>Usuário</label>
        <input id="stockAccessUser" type="text" autocomplete="username" placeholder="Digite seu usuário" autofocus>
        <label>Senha</label>
        <input id="stockAccessPassword" type="password" autocomplete="current-password" placeholder="Digite sua senha">
        <div id="stockAccessError" class="stock-error">${escapeHtml(message)}</div>
        <button id="stockAccessButton" type="submit">ENTRAR NO SISTEMA</button>
      </form>`;
    host.querySelector("#stockAccessForm")?.addEventListener("submit", event => {
      event.preventDefault();
      performLogin(
        host.querySelector("#stockAccessUser")?.value,
        host.querySelector("#stockAccessPassword")?.value,
        "portal",
        "stockAccessError",
        host.querySelector("#stockAccessButton")
      );
    });
  }

  function renderAccessMenu(user) {
    const host = document.getElementById("webAccessPortal");
    if (!host) return;
    host.innerHTML = `
      <div class="access-user">
        <span>Usuário do estoque conectado</span>
        <strong>${escapeHtml(user.nome || user.username)}</strong>
        <small>Login: ${escapeHtml(user.username)} · Perfil: ${profileLabel(user.app_role)}</small>
      </div>
      <div class="access-actions">
        ${permission(user,"conferencia") ? `<a class="module-action" href="${BASE}/conferencia"><span class="module-icon">✓</span><span>Abrir Conferência</span></a>` : ""}
        ${permission(user,"admin") ? `<a class="module-action" href="${BASE}/admin"><span class="module-icon">⚙</span><span>Abrir Painel Administrativo</span></a>` : ""}
        ${permission(user,"estacao") ? `<a class="module-action" href="${BASE}/estacao-impressao"><span class="module-icon">▣</span><span>Abrir Estação de Impressão</span></a>` : ""}
        ${permission(user,"precificador") ? `<a class="module-action" href="${BASE}/precificador"><span class="module-icon">$</span><span>Abrir Precificador</span></a>` : ""}
        ${!["conferencia","admin","estacao","precificador"].some(m=>permission(user,m)) ? `<div class="access-denied">Este usuário não possui módulos liberados. Peça ao administrador para marcar os acessos necessários.</div>` : ""}
        <button id="stockAccessLogout" type="button" class="logout-access">Sair e trocar usuário</button>
      </div>`;
    host.querySelector("#stockAccessLogout")?.addEventListener("click", () => logout(`${BASE}/`));
  }

  async function initialize() {
    const kind = pageKind();

    // Instala os manipuladores imediatamente. Assim, mesmo quando não existe
    // sessão, as telas antigas nunca validam senha pelo localStorage.
    if (kind === "admin") installAdminLogin();
    if (kind === "conference") installConferenceLogin();

    try {
      const data = await jsonFetch(API_SESSION);
      const user = data.authenticated ? data.user : null;
      if (kind === "access") {
        if (user) renderAccessMenu(user);
        else renderAccessLogin();
        return;
      }
      if (!user) return;
      if (kind === "admin") activateAdmin(user);
      else activateConference(user);
    } catch (error) {
      const message = `Não foi possível validar o login do estoque: ${error.message || error}`;
      if (kind === "access") renderAccessLogin(message);
      else if (kind === "admin") showAdminLogin(message);
      else showConferenceLogin(message);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
