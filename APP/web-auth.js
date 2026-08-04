(() => {
  "use strict";

  // Na edição Web, a identidade vem exclusivamente do NoromakWeb.
  // As senhas e sessões locais da versão portátil nunca autorizam acesso.
  window.EXPEDICAO_WEB_AUTH = true;

  const BASE = location.pathname.startsWith("/expedicao") ? "/expedicao" : "";
  const API_ME = `${BASE}/api/me`;
  const API_ROLES = `${BASE}/api/roles`;

  const txt = value => String(value ?? "").trim();
  const profileLabel = role => role === "admin" ? "Admin" : "Operador";

  function clearLegacySession() {
    for (const key of ["conf_sessao_ativa", "conf_admin_logado", "conf_admin_sessao"]) {
      try { window.localStorage.removeItem(key); } catch (_) {}
      try { window.sessionStorage.removeItem(key); } catch (_) {}
    }
  }

  function pageKind() {
    const path = location.pathname.replace(/\/+$/, "");
    if (/\/(admin|administracao)(\.html)?$/i.test(path)) return "admin";
    if (/\/(conferencia|pedidos)(\.html)?$/i.test(path)) return "conference";
    return "access";
  }

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
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function showFullScreen(title, detail, kind = "error") {
    const old = document.getElementById("webAuthMessage");
    if (old) old.remove();
    const overlay = document.createElement("div");
    overlay.id = "webAuthMessage";
    overlay.style.cssText = "position:fixed;inset:0;z-index:20000;background:linear-gradient(135deg,#2B4E75,#15263a);display:flex;align-items:center;justify-content:center;padding:24px;font-family:Segoe UI,Arial";
    const card = document.createElement("div");
    card.style.cssText = `max-width:600px;width:100%;background:#fff;border-radius:14px;padding:30px;box-shadow:0 18px 50px rgba(0,0,0,.35);border-top:6px solid ${kind === "loading" ? "#2B4E75" : "#F28C18"};text-align:center`;
    card.innerHTML = `<h2 style="color:#2B4E75;margin:0 0 14px">${title}</h2><div style="color:#526475;line-height:1.55;white-space:pre-line">${detail}</div>`;
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    return overlay;
  }

  function accessConfirmation(user, target) {
    return new Promise(resolve => {
      const overlay = document.createElement("div");
      overlay.id = "webAccessConfirmation";
      overlay.style.cssText = "position:fixed;inset:0;z-index:21000;background:linear-gradient(135deg,#1F57A5,#17395f);display:flex;align-items:center;justify-content:center;padding:24px;font-family:'Plus Jakarta Sans',Segoe UI,Arial";
      const name = user.nome_expedicao || user.nome || user.username;
      const isAdmin = user.app_role === "admin";
      const title = target === "admin" ? "Acesso administrativo" : "Acesso à conferência";
      const action = target === "admin" ? "ENTRAR NO PAINEL ADMINISTRATIVO" : "ENTRAR NA CONFERÊNCIA";
      overlay.innerHTML = `
        <div style="width:min(560px,100%);background:#fff;border-radius:18px;padding:30px;box-shadow:0 22px 65px rgba(0,0,0,.32);border-top:7px solid #F7941D;text-align:center">
          <img src="/nk-logo.svg" alt="Noromak" style="max-width:210px;max-height:64px;object-fit:contain" onerror="this.style.display='none'">
          <h2 style="margin:12px 0 8px;color:#1F57A5">${title}</h2>
          <p style="margin:0;color:#607080;line-height:1.55">Confirme a conta do NoromakWeb que será usada nesta sessão.</p>
          <div style="margin:22px 0;padding:16px;border-radius:12px;background:#eef5fd;border:1px solid #c8dcf2;text-align:left">
            <div style="font-size:12px;color:#66788a">Usuário conectado</div>
            <div style="font-size:20px;font-weight:800;color:#17395f;margin-top:3px">${escapeHtml(name)}</div>
            <div style="font-size:13px;color:#51677d;margin-top:4px">Username: <b>${escapeHtml(user.username)}</b> · Perfil: <b>${isAdmin ? "Admin" : "Operador"}</b></div>
          </div>
          <button id="webConfirmAccess" type="button" style="width:100%;border:0;border-radius:9px;padding:13px 16px;background:#1F57A5;color:#fff;font-weight:800;cursor:pointer">${action}</button>
          <button id="webCancelAccess" type="button" style="width:100%;border:1px solid #cbd7e3;border-radius:9px;padding:12px 16px;background:#fff;color:#284b70;font-weight:750;cursor:pointer;margin-top:9px">VOLTAR AO NOROMAKWEB / TROCAR USUÁRIO</button>
          <p style="font-size:11px;color:#7a8793;line-height:1.45;margin:16px 0 0">A senha não é repetida aqui: o login é protegido pelo NoromakWeb. Para usar outra conta, saia do NoromakWeb e entre com o usuário correto.</p>
        </div>`;
      document.body.appendChild(overlay);
      overlay.querySelector("#webConfirmAccess")?.addEventListener("click", () => { overlay.remove(); resolve(true); });
      overlay.querySelector("#webCancelAccess")?.addEventListener("click", () => { window.location.href = "/"; });
    });
  }

  function renderAccessPortal(user) {
    const host = document.getElementById("webAccessPortal");
    if (!host) return;
    const name = user.nome_expedicao || user.nome || user.username;
    const role = user.app_role;
    host.innerHTML = `
      <div class="access-user">
        <span>Conta NoromakWeb identificada</span>
        <strong>${escapeHtml(name)}</strong>
        <small>Username: ${escapeHtml(user.username)}${role ? ` · Perfil: ${role === "admin" ? "Admin" : "Operador"}` : " · Sem acesso à Expedição"}</small>
      </div>
      ${role ? `<div class="access-actions">
        <a class="primary" href="${BASE}/conferencia">Abrir Conferência</a>
        ${role === "admin" ? `<a class="secondary" href="${BASE}/admin">Abrir Painel Administrativo</a>` : ""}
      </div>` : `<div class="access-denied">Seu usuário ainda não foi cadastrado na Expedição. Solicite a um administrador que libere o username <b>${escapeHtml(user.username)}</b>.</div>`}
      <a class="back-link" href="/">Voltar ao NoromakWeb para sair ou trocar de usuário</a>`;
  }

  function addUserPill(user) {
    if (document.getElementById("webUserPill")) return;
    const target = document.querySelector(".header-topo > div:last-child") || document.querySelector(".top-bar") || document.body;
    const pill = document.createElement("span");
    pill.id = "webUserPill";
    pill.style.cssText = "display:inline-flex;align-items:center;padding:7px 10px;border-radius:999px;background:#e8f1fb;color:#2B4E75;border:1px solid #b9cce0;font:700 11px Segoe UI,Arial";
    pill.textContent = `${user.nome_expedicao || user.nome || user.username} · ${profileLabel(user.app_role)}`;
    if (target === document.body) {
      pill.style.position = "fixed";
      pill.style.top = "10px";
      pill.style.right = "10px";
      pill.style.zIndex = "10000";
    } else {
      target.prepend(pill);
    }
  }

  function setLocalSession(user) {
    const session = {
      Nome: user.nome_expedicao || user.nome || user.username,
      Login: user.username,
      Perfil: profileLabel(user.app_role)
    };
    window.EXPEDICAO_USER = user;
    try {
      appStorage.setItem("conf_sessao_ativa", JSON.stringify(session));
      if (user.app_role === "admin") {
        appStorage.setItem("conf_admin_logado", "true");
        appStorage.setItem("conf_admin_sessao", JSON.stringify(session));
      } else {
        appStorage.removeItem("conf_admin_logado");
        appStorage.removeItem("conf_admin_sessao");
      }
    } catch (_) {}
    return session;
  }

  function activateConference(user) {
    const session = setLocalSession(user);
    window.fazerLogin = function() { alert("O acesso é feito pela conta do NoromakWeb."); };
    window.usuarioLogado = session;
    const login = document.getElementById("telaLogin");
    if (login) login.style.display = "none";
    if (typeof window.iniciarSessao === "function") window.iniciarSessao();
    addUserPill(user);
    const logoff = document.querySelector(".btn-logoff");
    if (logoff) logoff.textContent = "VOLTAR AO NOROMAKWEB";
    window.fazerLogoff = function() {
      try {
        appStorage.removeItem("conf_sessao_ativa");
        if (typeof window.liberarBloqueioPedidoAtual === "function") window.liberarBloqueioPedidoAtual();
      } catch (_) {}
      window.location.href = "/";
    };
  }

  function activateAdmin(user) {
    setLocalSession(user);
    window.autenticarAdmin = function() { alert("O acesso administrativo é feito pela conta do NoromakWeb."); };
    if (user.app_role !== "admin") {
      window.location.replace(`${BASE}/conferencia`);
      return;
    }
    const login = document.getElementById("loginAdmin");
    const panel = document.getElementById("painelConteudo");
    if (login) login.style.display = "none";
    if (panel) panel.style.display = "block";
    if (typeof window.inicializarBancoInternoAdmin === "function") window.inicializarBancoInternoAdmin();
    else if (typeof window.carregarLogs === "function") window.carregarLogs();
    addUserPill(user);
    const exitButton = document.querySelector(".btn-sair");
    if (exitButton) exitButton.textContent = "Voltar ao NoromakWeb";
    window.sairAdmin = function() { window.location.href = "/"; };
    installRoleManager();
  }

  function installRoleManager() {
    const section = document.getElementById("aba-usuarios");
    if (!section) return;
    section.innerHTML = `
      <div class="card-admin" id="webRolesCard">
        <h2>Usuários da Expedição</h2>
        <div class="aviso-banco"><b>Login integrado:</b> use o mesmo <b>username do NoromakWeb</b>. Não existe senha separada neste sistema. O usuário precisa entrar primeiro no NoromakWeb e ter um perfil ativo abaixo.</div>
        <div style="display:grid;grid-template-columns:1.1fr 1.4fr .8fr .65fr auto;gap:9px;align-items:end;margin:15px 0">
          <div><label>Username NoromakWeb</label><input id="webRoleUsername" placeholder="ex.: maria"></div>
          <div><label>Nome para exibição</label><input id="webRoleName" placeholder="Nome da pessoa"></div>
          <div><label>Perfil</label><select id="webRoleProfile"><option value="operador">Operador</option><option value="admin">Admin</option></select></div>
          <label style="display:flex;align-items:center;gap:7px;padding-bottom:10px"><input id="webRoleActive" type="checkbox" checked style="width:auto"> Ativo</label>
          <button type="button" class="btn-sucesso" id="webRoleSave" style="height:39px">Salvar acesso</button>
        </div>
        <div id="webRoleStatus" style="min-height:20px;font-size:12px;font-weight:700"></div>
        <div class="excel-wrap"><table class="excel-grid" style="min-width:760px"><thead><tr><th>Nome</th><th>Username</th><th>Perfil</th><th>Status</th><th>Atualizado por</th><th>Ações</th></tr></thead><tbody id="webRolesBody"></tbody></table></div>
      </div>`;

    const style = document.createElement("style");
    style.textContent = "#webRolesCard input,#webRolesCard select{width:100%;padding:9px;border:1px solid #c7d2dc;border-radius:6px;background:#fff} @media(max-width:850px){#webRolesCard>div:nth-of-type(2){grid-template-columns:1fr!important}}";
    document.head.appendChild(style);

    document.getElementById("webRoleSave")?.addEventListener("click", saveRole);
    loadRoles();
  }

  function roleStatus(message, error = false) {
    const el = document.getElementById("webRoleStatus");
    if (!el) return;
    el.style.color = error ? "#b42318" : "#24732f";
    el.textContent = message;
  }

  async function loadRoles() {
    try {
      const data = await jsonFetch(API_ROLES);
      const body = document.getElementById("webRolesBody");
      if (!body) return;
      body.innerHTML = "";
      for (const user of data.users || []) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHtml(user.display_name || user.username)}</td>
          <td><b>${escapeHtml(user.username)}</b></td>
          <td>${user.role === "admin" ? "Admin" : "Operador"}</td>
          <td>${user.active ? "✅ Ativo" : "⛔ Inativo"}</td>
          <td>${escapeHtml(user.updated_by || "—")}</td>
          <td><button type="button" class="btn-secundario web-edit-role">Editar</button> <button type="button" class="btn-limpar web-delete-role">Remover</button></td>`;
        tr.querySelector(".web-edit-role")?.addEventListener("click", () => {
          document.getElementById("webRoleUsername").value = user.username;
          document.getElementById("webRoleName").value = user.display_name || "";
          document.getElementById("webRoleProfile").value = user.role;
          document.getElementById("webRoleActive").checked = !!user.active;
          document.getElementById("webRoleUsername").focus();
        });
        tr.querySelector(".web-delete-role")?.addEventListener("click", () => deleteRole(user.username));
        body.appendChild(tr);
      }
      roleStatus(`${(data.users || []).length} usuário(s) configurado(s).`);
    } catch (error) {
      roleStatus(error.message || String(error), true);
    }
  }

  async function saveRole() {
    const username = txt(document.getElementById("webRoleUsername")?.value).toLowerCase();
    const display_name = txt(document.getElementById("webRoleName")?.value) || username;
    const role = txt(document.getElementById("webRoleProfile")?.value) || "operador";
    const active = !!document.getElementById("webRoleActive")?.checked;
    if (!username || /\s/.test(username)) {
      roleStatus("Informe o username do NoromakWeb sem espaços.", true);
      return;
    }
    try {
      roleStatus("Salvando...");
      await jsonFetch(API_ROLES, { method: "POST", body: JSON.stringify({ username, display_name, role, active }) });
      document.getElementById("webRoleUsername").value = "";
      document.getElementById("webRoleName").value = "";
      document.getElementById("webRoleProfile").value = "operador";
      document.getElementById("webRoleActive").checked = true;
      await loadRoles();
      roleStatus(`Acesso de ${username} salvo.`);
    } catch (error) {
      roleStatus(error.message || String(error), true);
    }
  }

  async function deleteRole(username) {
    if (!confirm(`Remover o acesso de ${username} à Expedição?`)) return;
    try {
      await jsonFetch(`${API_ROLES}?username=${encodeURIComponent(username)}`, { method: "DELETE" });
      await loadRoles();
      roleStatus(`Acesso de ${username} removido.`);
    } catch (error) {
      roleStatus(error.message || String(error), true);
    }
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
  }

  async function initialize() {
    // Esta limpeza acontece antes do window.onload legado, impedindo que uma
    // sessão antiga ou a senha fixa "admin" abra qualquer tela.
    clearLegacySession();
    const kind = pageKind();
    const loading = showFullScreen("Conectando ao NoromakWeb…", "Validando sua sessão e suas permissões na Expedição.", "loading");
    try {
      const user = await jsonFetch(API_ME);
      loading.remove();

      if (kind === "access") {
        renderAccessPortal(user);
        return;
      }

      if (!user.app_role) {
        showFullScreen(
          "Acesso à Expedição ainda não liberado",
          `Usuário identificado: ${user.username}\n\nPeça a um administrador da Expedição para cadastrar este username na aba Usuários. Não é necessário criar outra senha.`
        );
        return;
      }

      if (kind === "admin" && user.app_role !== "admin") {
        showFullScreen("Acesso negado", `O usuário ${user.username} possui perfil Operador e não pode abrir o painel administrativo.`);
        return;
      }

      await accessConfirmation(user, kind);
      if (kind === "admin") activateAdmin(user);
      else activateConference(user);
    } catch (error) {
      loading.remove();
      showFullScreen("Não foi possível abrir a Expedição", `${error.message || error}\n\nEntre novamente no NoromakWeb e tente abrir /expedicao.`);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
