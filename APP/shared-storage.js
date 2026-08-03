(() => {
  "use strict";

  const API = "/api/storage";
  const API_LOCK = "/api/order-lock";
  const API_COMPLETE = "/api/order-complete";
  const CHAVES_LOCAIS = new Set([
    "conf_admin_logado",
    "conf_sessao_ativa",
    "conf_impressora_qz",
    "conf_ultimo_pedido_em_andamento",
    "conf_atalhos_visiveis",
    "conf_filtro_marketplace_pedidos",
    "conf_admin_sessao",
    "conf_cliente_portatil_id"
  ]);
  const CHAVES_MONITORADAS = [
    "conf_pedidos",
    "conf_pedidos_rascunhos",
    "conf_produtos",
    "conf_usuarios",
    "conf_solicitacoes_usuarios",
    "conf_etiquetas",
    "conf_fotos",
    "conf_historico_conferencias_v2",
    "conf_logs",
    "conf_banco_atualizado_em"
  ];

  const cache = new Map();
  let conectado = true;
  let ultimoErro = "";
  let pollEmAndamento = false;
  let infoServidor = null;

  function criarIdCliente() {
    let id = window.sessionStorage.getItem("conf_cliente_portatil_id");
    if (!id) {
      id = "PC-" + Date.now().toString(36).toUpperCase() + "-" + Math.random().toString(36).slice(2, 10).toUpperCase();
      window.sessionStorage.setItem("conf_cliente_portatil_id", id);
    }
    return id;
  }
  const CLIENT_ID = criarIdCliente();

  function ehLocal(chave) {
    return CHAVES_LOCAIS.has(String(chave));
  }

  function xhrJson(metodo, url, corpo, statusPermitidos = []) {
    const xhr = new XMLHttpRequest();
    xhr.open(metodo, url, false);
    xhr.setRequestHeader("Accept", "application/json");
    if (corpo !== undefined) xhr.setRequestHeader("Content-Type", "application/json; charset=utf-8");
    try {
      xhr.send(corpo === undefined ? null : JSON.stringify(corpo));
    } catch (erro) {
      conectado = false;
      ultimoErro = erro.message || String(erro);
      atualizarIndicadorRede();
      throw new Error("O aplicativo local não está respondendo. Feche esta aba e abra novamente pelo atalho do sistema.");
    }
    let resposta = {};
    try { resposta = xhr.responseText ? JSON.parse(xhr.responseText) : {}; } catch (_) { resposta = { error: xhr.responseText || "Falha desconhecida" }; }
    const sucesso = xhr.status >= 200 && xhr.status < 300;
    if (!sucesso && !statusPermitidos.includes(xhr.status)) {
      conectado = false;
      ultimoErro = resposta.error || `HTTP ${xhr.status}: ${xhr.responseText || "falha no aplicativo"}`;
      atualizarIndicadorRede();
      throw new Error(ultimoErro);
    }
    conectado = true;
    ultimoErro = "";
    atualizarIndicadorRede();
    resposta.__status = xhr.status;
    return resposta;
  }

  const appStorage = {
    getItem(chave) {
      chave = String(chave);
      if (ehLocal(chave)) return window.localStorage.getItem(chave);
      try {
        const resposta = xhrJson("GET", `${API}?key=${encodeURIComponent(chave)}`);
        const valor = resposta.exists ? String(resposta.value ?? "") : null;
        cache.set(chave, valor);
        try {
          if (valor === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, valor);
        } catch (_) {}
        return valor;
      } catch (erro) {
        const salvo = window.sessionStorage.getItem("rede_cache_" + chave);
        if (salvo !== null) return salvo;
        return null;
      }
    },

    setItem(chave, valor) {
      chave = String(chave);
      valor = String(valor);
      if (ehLocal(chave)) {
        window.localStorage.setItem(chave, valor);
        return valor;
      }
      let anterior = cache.has(chave) ? cache.get(chave) : null;
      if (!cache.has(chave)) anterior = this.getItem(chave);
      const resposta = xhrJson("POST", API, { key: chave, oldValue: anterior, newValue: valor });
      const consolidado = resposta.value === null || resposta.value === undefined ? valor : String(resposta.value);
      cache.set(chave, consolidado);
      try { window.sessionStorage.setItem("rede_cache_" + chave, consolidado); } catch (_) {}
      dispararStorage(chave, anterior, consolidado);
      return consolidado;
    },

    removeItem(chave) {
      chave = String(chave);
      if (ehLocal(chave)) {
        window.localStorage.removeItem(chave);
        return;
      }
      const anterior = cache.has(chave) ? cache.get(chave) : this.getItem(chave);
      xhrJson("DELETE", `${API}?key=${encodeURIComponent(chave)}`);
      cache.set(chave, null);
      try { window.sessionStorage.removeItem("rede_cache_" + chave); } catch (_) {}
      dispararStorage(chave, anterior, null);
    },

    clear() {
      throw new Error("A limpeza total dos arquivos compartilhados foi bloqueada por segurança.");
    },

    key(indice) {
      const resposta = xhrJson("GET", `${API}?list=1`);
      return (resposta.keys || [])[indice] || null;
    },

    get length() {
      try { return (xhrJson("GET", `${API}?list=1`).keys || []).length; }
      catch (_) { return 0; }
    },

    testarConexao() {
      try {
        infoServidor = xhrJson("GET", "/api/health");
        return true;
      } catch (_) {
        return false;
      }
    },

    obterInfoRede() {
      try { infoServidor = xhrJson("GET", "/api/health"); } catch (_) {}
      return infoServidor;
    },

    adquirirBloqueioPedido(pedido, operador) {
      const resposta = xhrJson("POST", API_LOCK, {
        action: "acquire",
        pedido: String(pedido || ""),
        clientId: CLIENT_ID,
        operador: String(operador || "")
      }, [409]);
      return resposta;
    },

    renovarBloqueioPedido(pedido) {
      return xhrJson("POST", API_LOCK, {
        action: "heartbeat",
        pedido: String(pedido || ""),
        clientId: CLIENT_ID
      }, [409]);
    },

    liberarBloqueioPedido(pedido) {
      if (!pedido) return { ok: true };
      return xhrJson("POST", API_LOCK, {
        action: "release",
        pedido: String(pedido),
        clientId: CLIENT_ID
      }, [409]);
    },

    consultarBloqueioPedido(pedido) {
      return xhrJson("POST", API_LOCK, {
        action: "status",
        pedido: String(pedido || ""),
        clientId: CLIENT_ID
      }, [409]);
    },

    concluirPedido(pedido) {
      pedido = String(pedido || "").trim();
      if (!pedido) throw new Error("Pedido não informado para conclusão.");
      const anterior = cache.has("conf_pedidos") ? cache.get("conf_pedidos") : this.getItem("conf_pedidos");
      const resultado = xhrJson("POST", API_COMPLETE, { pedido, clientId: CLIENT_ID });
      const respostaAtual = xhrJson("GET", `${API}?key=${encodeURIComponent("conf_pedidos")}`);
      const novo = respostaAtual.exists ? String(respostaAtual.value ?? "[]") : "[]";
      cache.set("conf_pedidos", novo);
      try { window.sessionStorage.setItem("rede_cache_conf_pedidos", novo); } catch (_) {}
      dispararStorage("conf_pedidos", anterior, novo);
      return resultado;
    },

    migrarDoNavegador() {
      const chaves = CHAVES_MONITORADAS.filter(k => window.localStorage.getItem(k) !== null);
      if (!chaves.length) {
        alert("Não foram encontrados dados antigos neste navegador.");
        return;
      }
      if (!confirm(`Copiar ${chaves.length} conjunto(s) de dados deste navegador para a pasta compartilhada?\n\nUse esta opção somente na máquina que possui os dados corretos.`)) return;
      let migradas = 0;
      for (const chave of chaves) {
        const valor = window.localStorage.getItem(chave);
        if (valor !== null) {
          const atual = this.getItem(chave);
          const resposta = xhrJson("POST", API, { key: chave, oldValue: atual, newValue: valor, force: true });
          cache.set(chave, resposta.value ?? valor);
          migradas++;
        }
      }
      alert(`${migradas} conjunto(s) copiado(s) para a pasta compartilhada. As outras máquinas serão atualizadas automaticamente.`);
      location.reload();
    }
  };

  function dispararStorage(chave, antigo, novo) {
    try {
      window.dispatchEvent(new StorageEvent("storage", {
        key: chave,
        oldValue: antigo,
        newValue: novo,
        url: location.href,
        storageArea: null
      }));
    } catch (_) {
      window.dispatchEvent(new Event("storage"));
    }
  }

  async function consultarAlteracoes() {
    if (pollEmAndamento || document.hidden) return;
    pollEmAndamento = true;
    try {
      const resposta = await fetch(`${API}?keys=${encodeURIComponent(CHAVES_MONITORADAS.join(","))}`, { cache: "no-store" });
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const dados = await resposta.json();
      conectado = true;
      ultimoErro = "";
      for (const chave of CHAVES_MONITORADAS) {
        const novo = Object.prototype.hasOwnProperty.call(dados.values || {}, chave) ? dados.values[chave] : null;
        const antigo = cache.has(chave) ? cache.get(chave) : undefined;
        if (antigo !== undefined && antigo !== novo) {
          cache.set(chave, novo);
          try {
            if (novo === null) window.sessionStorage.removeItem("rede_cache_" + chave);
            else window.sessionStorage.setItem("rede_cache_" + chave, novo);
          } catch (_) {}
          dispararStorage(chave, antigo, novo);
        } else if (antigo === undefined) {
          cache.set(chave, novo);
        }
      }
    } catch (erro) {
      conectado = false;
      ultimoErro = erro.message || String(erro);
    } finally {
      pollEmAndamento = false;
      atualizarIndicadorRede();
    }
  }

  function atualizarIndicadorRede() {
    let box = document.getElementById("indicadorRedeCentral");
    if (!box && document.body) {
      box = document.createElement("div");
      box.id = "indicadorRedeCentral";
      box.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:10050;padding:8px 12px;border-radius:8px;font:600 12px Segoe UI,Arial;box-shadow:0 2px 10px rgba(0,0,0,.18);cursor:pointer;max-width:390px";
      box.title = "Clique para testar o acesso à pasta compartilhada.";
      box.addEventListener("click", () => {
        const ok = appStorage.testarConexao();
        const info = appStorage.obterInfoRede();
        alert(ok
          ? `Pasta compartilhada conectada.\n\nComputador: ${info?.computer || "---"}\nDados: ${info?.shared_data_path || "---"}`
          : `Não foi possível acessar os dados compartilhados. ${ultimoErro}`);
      });
      document.body.appendChild(box);
    }
    if (!box) return;
    if (conectado) {
      box.textContent = "● Dados compartilhados conectados";
      box.style.background = "#dff5e2";
      box.style.color = "#1b5e20";
      box.style.border = "1px solid #81c784";
    } else {
      box.textContent = "● Pasta compartilhada indisponível";
      box.style.background = "#ffebee";
      box.style.color = "#b71c1c";
      box.style.border = "1px solid #ef9a9a";
    }
  }

  window.appStorage = appStorage;
  document.addEventListener("DOMContentLoaded", () => {
    atualizarIndicadorRede();
    appStorage.testarConexao();
    consultarAlteracoes();
    setInterval(consultarAlteracoes, 1500);
  });
})();
