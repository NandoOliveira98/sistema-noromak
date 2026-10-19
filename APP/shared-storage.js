(() => {
  "use strict";

  const BASE = location.pathname.startsWith("/expedicao") ? "/expedicao" : "";
  // V18.82: as telas do Admin ganharam endereço próprio (/expedicao/pedidos-dia...) e continuam
  // sendo o Admin — mesma lista do ROTAS_ADMIN do servidor_web.py (TESTES/test_rotas_v1882.py).
  const ROTAS_ADMIN_V1882 = /\/(dashboard|pedidos-pendentes|pedidos-dia|coleta|etiquetas|historico|historico-sku|produtos|kits|integracoes|usuarios|seguranca|pendencias|central-pendencias|promocoes-ml)\/?$/i;
  const TELA_ATUAL = (location.pathname.includes("/admin") || ROTAS_ADMIN_V1882.test(location.pathname)) ? "Admin" :
    (location.pathname.includes("/conferencia") ? "Conferência" :
    (location.pathname.includes("/estacao-impressao") ? "Estação de impressão" :
    (location.pathname.includes("/precificador") ? "Precificador" : "Sistema")));
  const API = BASE + "/api/storage";
  const API_LOCK = BASE + "/api/order-lock";
  const API_COMPLETE = BASE + "/api/order-complete";
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
  const CHAVES_CANONICAS_SERVIDOR = new Set([
    "conf_pedidos",
    "conf_pedidos_rascunhos",
    "conf_produtos",
    "conf_kits",
    "precificador_online_v1",
    "ml_promocoes_datas_v1",
    "ml_sku_mlb_links_v1",
    "conf_etiquetas"
  ]);
  const CHAVES_MONITORADAS_TODAS = [
    "conf_pedidos","conf_pedidos_rascunhos","conf_produtos","conf_kits",
    "conf_usuarios","conf_solicitacoes_usuarios","conf_etiquetas","conf_fotos",
    "conf_historico_conferencias_v2","conf_logs","conf_banco_atualizado_em",
    "precificador_online_v1","ml_promocoes_datas_v1","ml_sku_mlb_links_v1"
  ];
  // V17.77: polling enxuto por tela. A Conferência monitora somente bancos
  // operacionais que podem alterar a fila/tabela durante o trabalho. Usuários,
  // histórico e logs continuam sendo lidos sob demanda, sem polling contínuo.
  const CHAVES_MONITORADAS = TELA_ATUAL === "Conferência"
    ? ["conf_pedidos","conf_produtos","conf_kits","conf_etiquetas","conf_banco_atualizado_em"]
    : TELA_ATUAL === "Precificador"
      ? ["precificador_online_v1","ml_promocoes_datas_v1","ml_sku_mlb_links_v1"]
      : TELA_ATUAL === "Estação de impressão"
        ? ["conf_pedidos","conf_etiquetas","conf_banco_atualizado_em"]
        : CHAVES_MONITORADAS_TODAS;

  // V18.95: filial escolhida no seletor (só quem vê "todas"; o servidor ignora para os
  // demais). Vai em TODA chamada à API desta origem — inclusive nos fetch() das telas.
  const CHAVE_FILIAL = "nk_filial_v1895";
  // V19.00: lida UMA vez, quando a página abre. Antes era lida a cada requisição: trocada
  // numa aba, as outras continuavam com a fila da filial antiga e passavam a gravar com o
  // cabeçalho da nova (aba de Bauru gravando como "Todas" apagava Araçatuba). Cada aba
  // trabalha com a filial com que abriu; trocarFilial() recarrega a aba que trocou.
  const FILIAL_DA_PAGINA = (() => {
    try { return window.localStorage.getItem(CHAVE_FILIAL) || ""; } catch (_) { return ""; }
  })();
  function filialEscolhida() {
    return FILIAL_DA_PAGINA;
  }
  if (window.fetch && !window.fetch.__nkFilial) {
    const fetchOriginal = window.fetch.bind(window);
    const fetchComFilial = (entrada, opcoes) => {
      const filial = filialEscolhida();
      if (filial && typeof entrada === "string") {
        let caminho = "";
        try { caminho = new URL(entrada, location.href).pathname; } catch (_) {}
        if (caminho.startsWith(BASE + "/api/")) {
          const cab = new Headers((opcoes && opcoes.headers) || {});
          if (!cab.has("X-Noromak-Filial")) cab.set("X-Noromak-Filial", filial);
          opcoes = { ...(opcoes || {}), headers: cab };
        }
      }
      return fetchOriginal(entrada, opcoes);
    };
    fetchComFilial.__nkFilial = true;
    window.fetch = fetchComFilial;
  }

  const cache = new Map();
  const versoes = new Map();
  let conectado = true;
  let ultimoErro = "";
  let pollEmAndamento = false;
  let infoServidor = null;

  function criarIdCliente() {
    // V16.72: identificação persiste mesmo após fechar/reabrir o navegador.
    // Isso evita o próprio PC enxergar seu bloqueio anterior como "outra máquina".
    let id = "";
    try { id = window.localStorage.getItem("conf_cliente_portatil_id") || ""; } catch (_) {}
    if (!id) {
      try { id = window.sessionStorage.getItem("conf_cliente_portatil_id") || ""; } catch (_) {}
    }
    if (!id) {
      id = "PC-" + Date.now().toString(36).toUpperCase() + "-" + Math.random().toString(36).slice(2, 10).toUpperCase();
    }
    try { window.localStorage.setItem("conf_cliente_portatil_id", id); } catch (_) {}
    try { window.sessionStorage.setItem("conf_cliente_portatil_id", id); } catch (_) {}
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
    xhr.setRequestHeader("X-Expedicao-Tela", TELA_ATUAL);
    if (filialEscolhida()) xhr.setRequestHeader("X-Noromak-Filial", filialEscolhida());  // V18.95
    if (corpo !== undefined) xhr.setRequestHeader("Content-Type", "application/json; charset=utf-8");
    if (corpo && corpo.key === "conf_produtos") xhr.setRequestHeader("X-Expedicao-Forma", TELA_ATUAL === "Admin" ? "Banco de produtos" : "Sincronização de produtos");
    else if (corpo && (corpo.key === "conf_pedidos" || corpo.key === "conf_pedidos_rascunhos")) xhr.setRequestHeader("X-Expedicao-Forma", TELA_ATUAL === "Admin" ? "Pedidos pendentes / importação" : (TELA_ATUAL === "Conferência" ? "Tela de conferência" : "Sincronização de pedidos"));
    try {
      xhr.send(corpo === undefined ? null : JSON.stringify(corpo));
    } catch (erro) {
      conectado = false;
      ultimoErro = erro.message || String(erro);
      atualizarIndicadorRede();
      throw new Error("O servidor da Expedição não está respondendo. Atualize a página ou avise o administrador.");
    }
    let resposta = {};
    try { resposta = xhr.responseText ? JSON.parse(xhr.responseText) : {}; } catch (_) { resposta = { error: xhr.responseText || "Falha desconhecida" }; }
    const sucesso = xhr.status >= 200 && xhr.status < 300;
    // V19.40: perfil Consulta — recusa de gravação não é queda de rede. Descarta a cópia
    // local da chave (a tela volta ao que está no servidor) e avisa.
    if (xhr.status === 403 && resposta && resposta.code === "SOMENTE_LEITURA") {
      if (corpo && corpo.key) {
        cache.delete(String(corpo.key));
        try { window.sessionStorage.removeItem("rede_cache_" + String(corpo.key)); } catch (_) {}
      }
      try { if (typeof window.nkToast === "function") window.nkToast("Perfil Consulta: somente leitura. Nada foi alterado.", "aviso"); } catch (_) {}
      throw new Error(resposta.error || "Perfil Consulta: somente leitura.");
    }
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
    // V18.95: troca a filial vista (seletor de quem vê "todas"): limpa as cópias e recarrega.
    filialEscolhida,
    trocarFilial(filial) {
      try {
        if (filial && filial !== "todas") window.localStorage.setItem(CHAVE_FILIAL, filial);
        else window.localStorage.removeItem(CHAVE_FILIAL);
        Object.keys(window.sessionStorage).filter(k => k.startsWith("rede_cache_")).forEach(k => window.sessionStorage.removeItem(k));
      } catch (_) {}
      cache.clear();
      versoes.clear();
      location.reload();
    },
    // V18.95: descarta a cópia em memória de uma chave (a próxima leitura busca no servidor).
    recarregar(chave) {
      chave = String(chave);
      cache.delete(chave);
      try { window.sessionStorage.removeItem("rede_cache_" + chave); } catch (_) {}
    },
    getItem(chave) {
      chave = String(chave);
      if (ehLocal(chave)) return window.localStorage.getItem(chave);
      // V17.71: bases compartilhadas grandes são lidas da memória após a primeira
      // carga. O poller por versão abaixo é a autoridade que invalida/atualiza o
      // cache quando o servidor muda. Evita baixar e parsear conf_pedidos inteiro
      // dezenas de vezes durante um único render.
      if (cache.has(chave)) return cache.get(chave);
      try {
        const resposta = xhrJson("GET", `${API}?key=${encodeURIComponent(chave)}`);
        const valor = resposta.exists ? String(resposta.value ?? "") : null;
        cache.set(chave, valor);
        versoes.set(chave, Number(resposta.version || 0));
        try {
          if (valor === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, valor);
        } catch (_) {}
        return valor;
      } catch (erro) {
        // V17.50: para bancos canônicos, nunca ressuscitar um snapshot antigo da sessão
        // após F5. Se o servidor não responder, mantemos apenas o cache desta execução.
        if (CHAVES_CANONICAS_SERVIDOR.has(chave)) return cache.has(chave) ? cache.get(chave) : null;
        const salvo = window.sessionStorage.getItem("rede_cache_" + chave);
        if (salvo !== null) return salvo;
        return null;
      }
    },

    setItem(chave, valor, base) {
      chave = String(chave);
      valor = String(valor);
      if (ehLocal(chave)) {
        window.localStorage.setItem(chave, valor);
        return valor;
      }
      let anterior = cache.has(chave) ? cache.get(chave) : null;
      if (!cache.has(chave)) anterior = this.getItem(chave);
      // V18.49: a tela pode informar em qual versão o que ela está gravando se
      // baseia. O cache acima é a última versão que CHEGOU do servidor, que a tela
      // pode ainda não ter aplicado; usá-la fazia o merge achar que a tela
      // "desfez" a alteração de outro usuário. Sem o 3º argumento, nada muda.
      if (typeof base === "string") anterior = base;
      const resposta = xhrJson("POST", API, { key: chave, oldValue: anterior, newValue: valor });
      const consolidado = resposta.value === null || resposta.value === undefined ? valor : String(resposta.value);
      cache.set(chave, consolidado);
      versoes.set(chave, Number(resposta.version || 0));
      try { window.sessionStorage.setItem("rede_cache_" + chave, consolidado); } catch (_) {}
      dispararStorage(chave, anterior, consolidado);
      return consolidado;
    },

    // V19.56: mesma gravação do setItem, mas sem travar a página. O Banco de produtos
    // (milhares de linhas) gravado pelo XHR síncrono deixava o Chrome em "Página sem
    // resposta" a cada campo editado. Quem chama garante uma gravação por vez.
    async setItemAsync(chave, valor, base) {
      chave = String(chave);
      valor = String(valor);
      if (ehLocal(chave)) return this.setItem(chave, valor);
      let anterior = cache.has(chave) ? cache.get(chave) : null;
      if (!cache.has(chave)) anterior = this.getItem(chave);
      if (typeof base === "string") anterior = base;
      const cab = { "Accept": "application/json", "Content-Type": "application/json; charset=utf-8", "X-Expedicao-Tela": TELA_ATUAL };
      if (filialEscolhida()) cab["X-Noromak-Filial"] = filialEscolhida();
      if (chave === "conf_produtos") cab["X-Expedicao-Forma"] = TELA_ATUAL === "Admin" ? "Banco de produtos" : "Sincronização de produtos";
      let r;
      try {
        r = await fetch(API, { method: "POST", headers: cab, credentials: "same-origin", body: JSON.stringify({ key: chave, oldValue: anterior, newValue: valor }) });
      } catch (erro) {
        conectado = false; ultimoErro = erro.message || String(erro); atualizarIndicadorRede();
        throw new Error("O servidor da Expedição não está respondendo. Atualize a página ou avise o administrador.");
      }
      const texto = await r.text();
      let resposta = {};
      try { resposta = texto ? JSON.parse(texto) : {}; } catch (_) { resposta = { error: texto || "Falha desconhecida" }; }
      if (r.status === 403 && resposta && resposta.code === "SOMENTE_LEITURA") {
        cache.delete(chave);
        try { window.sessionStorage.removeItem("rede_cache_" + chave); } catch (_) {}
        try { if (typeof window.nkToast === "function") window.nkToast("Perfil Consulta: somente leitura. Nada foi alterado.", "aviso"); } catch (_) {}
        throw new Error(resposta.error || "Perfil Consulta: somente leitura.");
      }
      if (!r.ok) {
        conectado = false; ultimoErro = resposta.error || `HTTP ${r.status}: ${texto || "falha no aplicativo"}`; atualizarIndicadorRede();
        throw new Error(ultimoErro);
      }
      conectado = true; ultimoErro = ""; atualizarIndicadorRede();
      const consolidado = resposta.value === null || resposta.value === undefined ? valor : String(resposta.value);
      cache.set(chave, consolidado);
      versoes.set(chave, Number(resposta.version || 0));
      try { window.sessionStorage.setItem("rede_cache_" + chave, consolidado); } catch (_) {}
      dispararStorage(chave, anterior, consolidado);
      return consolidado;
    },

    setPedidos(valor, permitirRemocoes = false) {
      const chave="conf_pedidos"; valor=String(valor);
      let anterior=cache.has(chave)?cache.get(chave):null;
      if(!cache.has(chave)) anterior=this.getItem(chave);
      const resposta=xhrJson("POST",API,{key:chave,oldValue:anterior,newValue:valor,allowPendingDeletes:!!permitirRemocoes});
      const consolidado=resposta.value===null||resposta.value===undefined?valor:String(resposta.value);
      cache.set(chave,consolidado);versoes.set(chave,Number(resposta.version||0));
      try{window.sessionStorage.setItem("rede_cache_"+chave,consolidado);}catch(_){}
      dispararStorage(chave,anterior,consolidado);return consolidado;
    },

    setEtiquetas(valor, permitirRemocoes = false) {
      const chave = "conf_etiquetas";
      valor = String(valor);
      let anterior = cache.has(chave) ? cache.get(chave) : null;
      if (!cache.has(chave)) anterior = this.getItem(chave);
      const resposta = xhrJson("POST", API, {
        key: chave,
        oldValue: anterior,
        newValue: valor,
        allowLabelDeletes: !!permitirRemocoes
      });
      const consolidado = resposta.value === null || resposta.value === undefined ? valor : String(resposta.value);
      cache.set(chave, consolidado);
      versoes.set(chave, Number(resposta.version || 0));
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
      versoes.delete(chave);
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
        infoServidor = xhrJson("GET", BASE + "/api/health");
        return true;
      } catch (_) {
        return false;
      }
    },

    obterInfoRede() {
      try { infoServidor = xhrJson("GET", BASE + "/api/health"); } catch (_) {}
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

    forcarLiberacaoBloqueioPedido(pedido) {
      if (!pedido) return { ok: true };
      return xhrJson("POST", API_LOCK, {
        action: "release",
        pedido: String(pedido),
        clientId: CLIENT_ID,
        force: true
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
      // V17.72: /order-complete já devolve o banco canônico após a remoção.
      // Atualiza cache + versão atomicamente; evita a fila manter snapshot antigo.
      const novo = resultado.value === null || resultado.value === undefined
        ? "[]" : String(resultado.value);
      cache.set("conf_pedidos", novo);
      versoes.set("conf_pedidos", Number(resultado.version || 0));
      try { window.sessionStorage.setItem("rede_cache_conf_pedidos", novo); } catch (_) {}
      if (anterior !== novo) dispararStorage("conf_pedidos", anterior, novo);
      return resultado;
    },



    upsertPedidosAdmin(linhas, source = "PAINEL_ADMIN") {
      const chave = "conf_pedidos";
      const anterior = cache.has(chave) ? cache.get(chave) : this.getItem(chave);
      const resposta = xhrJson("POST", BASE + "/api/admin-order-upsert", { rows: Array.isArray(linhas) ? linhas : [], source: String(source || "PAINEL_ADMIN") });
      const novo = resposta.value === null || resposta.value === undefined ? (anterior || "[]") : String(resposta.value);
      cache.set(chave, novo);
      versoes.set(chave, Number(resposta.version || 0));
      try { window.sessionStorage.setItem("rede_cache_" + chave, novo); } catch (_) {}
      if (anterior !== novo) dispararStorage(chave, anterior, novo);
      return novo;
    },

    // V19.16: bip leve — manda só as linhas bipadas (menos de 1 KB) em vez da fila inteira
    // (~700 KB ida e volta). Atualiza a cópia local sem baixar nada; o polling traz a versão
    // do servidor depois, em segundo plano. Devolve false (409) para a tela usar o caminho antigo.
    salvarBip(pedido, linhas) {
      const resposta = xhrJson("POST", BASE + "/api/order-progress", { pedido: String(pedido || ""), linhas: linhas || [] }, [409]);
      if (resposta.__status === 409) return false;
      const chave = "conf_pedidos";
      if (cache.has(chave) && cache.get(chave)) {
        try {
          const lista = JSON.parse(cache.get(chave));
          const ped = String(pedido || "").trim().toUpperCase();
          for (const ln of linhas || []) {
            const alvo = lista.find(r => r && String(r.Pedido || "").trim().toUpperCase() === ped && (ln.uid
              ? [r.__uidLinha, r.__uidPedido].map(v => String(v || "").trim()).includes(ln.uid)
              : String(r.SKU || "").trim().toUpperCase() === String(ln.sku || "").trim().toUpperCase() && String(r["Código de Barras"] || "").trim() === String(ln.ean || "").trim()));
            if (alvo) alvo.Lido = Math.max(Number(alvo.Lido || 0), Number(ln.lido || 0));
          }
          cache.set(chave, JSON.stringify(lista));
        } catch (_) { cache.delete(chave); }
      }
      return true;
    },

    atualizarSkuPedido(referencia) {
      const resposta = xhrJson("POST", BASE + "/api/order-sku", referencia || {});
      for (const chave of resposta.updated_keys || ["conf_pedidos", "conf_pedidos_rascunhos"]) {
        const anterior = cache.has(chave) ? cache.get(chave) : null;
        const atual = xhrJson("GET", `${API}?key=${encodeURIComponent(chave)}`);
        const novo = atual.exists ? String(atual.value ?? "") : null;
        cache.set(chave, novo);
        versoes.set(chave, Number(atual.version || resposta.versions?.[chave] || 0));
        try {
          if (novo === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, novo);
        } catch (_) {}
        if (anterior !== novo) dispararStorage(chave, anterior, novo);
      }
      return resposta;
    },

    atualizarEanPedido(referencia) {
      const resposta = xhrJson("POST", BASE + "/api/order-ean", referencia || {});
      for (const chave of resposta.updated_keys || []) {
        const anterior = cache.has(chave) ? cache.get(chave) : null;
        const atual = xhrJson("GET", `${API}?key=${encodeURIComponent(chave)}`);
        const novo = atual.exists ? String(atual.value ?? "") : null;
        cache.set(chave, novo);
        versoes.set(chave, Number(atual.version || resposta.versions?.[chave] || 0));
        try {
          if (novo === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, novo);
        } catch (_) {}
        if (anterior !== novo) dispararStorage(chave, anterior, novo);
      }
      return resposta;
    },

    atualizarEanProduto(referencia) {
      const resposta = xhrJson("POST", BASE + "/api/product-ean", referencia || {});
      for (const chave of resposta.updated_keys || []) {
        const anterior = cache.has(chave) ? cache.get(chave) : null;
        const atual = xhrJson("GET", `${API}?key=${encodeURIComponent(chave)}`);
        const novo = atual.exists ? String(atual.value ?? "") : null;
        cache.set(chave, novo);
        versoes.set(chave, Number(atual.version || resposta.versions?.[chave] || 0));
        try {
          if (novo === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, novo);
        } catch (_) {}
        if (anterior !== novo) dispararStorage(chave, anterior, novo);
      }
      return resposta;
    },

    migrarDoNavegador() {
      const chaves = CHAVES_MONITORADAS_TODAS.filter(k => window.localStorage.getItem(k) !== null);
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

  // V18.56: o polling não tinha prazo. Se uma requisição travasse na rede,
  // pollEmAndamento ficava true e a tela parava de atualizar em silêncio.
  function fetchComPrazo(url, ms) {
    if (typeof AbortController === "undefined") return fetch(url, { cache: "no-store" });
    const controle = new AbortController();
    // O prazo cobre também a leitura do corpo (.json()); abortar depois que a
    // resposta já foi lida não tem efeito.
    setTimeout(() => controle.abort(), ms);
    return fetch(url, { cache: "no-store", signal: controle.signal });
  }

  async function consultarAlteracoes() {
    const conferenciaEmSegundoPlano = document.documentElement?.dataset?.pwaApp === "conferencia";
    if (pollEmAndamento || (document.hidden && !conferenciaEmSegundoPlano)) return;
    pollEmAndamento = true;
    try {
      const resposta = await fetchComPrazo(`${API}?versions=${encodeURIComponent(CHAVES_MONITORADAS.join(","))}`, 15000);
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const dados = await resposta.json();
      conectado = true;
      ultimoErro = "";
      for (const chave of CHAVES_MONITORADAS) {
        const versaoNova = Number(dados.versions?.[chave] || 0);
        const versaoAntiga = versoes.get(chave);
        if (versaoAntiga === undefined) {
          versoes.set(chave, versaoNova);
          continue;
        }
        if (versaoAntiga === versaoNova) continue;
        const antigo = cache.has(chave) ? cache.get(chave) : undefined;
        const atual = await fetchComPrazo(`${API}?key=${encodeURIComponent(chave)}`, 60000);
        if (!atual.ok) throw new Error(`HTTP ${atual.status}`);
        const detalhe = await atual.json();
        const novo = detalhe.exists ? String(detalhe.value ?? "") : null;
        cache.set(chave, novo);
        versoes.set(chave, Number(detalhe.version || versaoNova));
        try {
          if (novo === null) window.sessionStorage.removeItem("rede_cache_" + chave);
          else window.sessionStorage.setItem("rede_cache_" + chave, novo);
        } catch (_) {}
        if (antigo !== undefined && antigo !== novo) dispararStorage(chave, antigo, novo);
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
      box.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:10050;width:14px;height:14px;padding:0;border-radius:50%;box-sizing:border-box;box-shadow:0 1px 5px rgba(0,0,0,.22);cursor:pointer;font-size:0;line-height:0";
      box.title = "Clique para testar o acesso ao servidor Web.";
      box.addEventListener("click", () => {
        const ok = appStorage.testarConexao();
        const info = appStorage.obterInfoRede();
        alert(ok
          ? `Servidor Web conectado.\n\nVersão: ${info?.version || "---"}\nBanco: ${info?.database || "---"}`
          : `Não foi possível acessar o servidor da Expedição. ${ultimoErro}`);
      });
      document.body.appendChild(box);
    }
    if (!box) return;
    if (conectado) {
      box.textContent = "";
      box.setAttribute("aria-label", "Servidor Web conectado");
      box.title = "Servidor Web conectado — clique para detalhes";
      box.style.background = "#22c55e";
      box.style.border = "2px solid #dcfce7";
    } else {
      box.textContent = "";
      box.setAttribute("aria-label", "Servidor Web indisponível");
      box.title = "Servidor Web indisponível — clique para testar";
      box.style.background = "#ef4444";
      box.style.border = "2px solid #fee2e2";
    }
  }

  window.appStorage = appStorage;
  try { for (const chave of CHAVES_CANONICAS_SERVIDOR) window.sessionStorage.removeItem("rede_cache_" + chave); } catch (_) {}
  document.addEventListener("DOMContentLoaded", () => {
    atualizarIndicadorRede();
    appStorage.testarConexao();
    consultarAlteracoes();
    // V17.77: polling adaptativo conservador para reduzir carga no servidor.
    // Ações locais (bip, conclusão, edição) continuam salvando imediatamente;
    // este timer serve apenas para descobrir mudanças feitas em OUTROS PCs.
    const POLL_ATIVO_MS = 10000;
    const POLL_OCULTO_MS = 30000;
    let proximoPoll=null;
    const agendarPoll=()=>{
      clearTimeout(proximoPoll);
      proximoPoll=setTimeout(async()=>{
        await consultarAlteracoes();
        agendarPoll();
      }, document.hidden ? POLL_OCULTO_MS : POLL_ATIVO_MS);
    };
    document.addEventListener("visibilitychange",()=>{
      clearTimeout(proximoPoll);
      if(!document.hidden){
        // Uma consulta imediata ao retornar; o lock interno evita duplicidade.
        consultarAlteracoes().finally(agendarPoll);
      }else{
        agendarPoll();
      }
    });
    agendarPoll();
  });
})();
