(() => {
  "use strict";
  // V18.68: ícones do tema (nk-i-*) no lugar dos emojis, como no menu.
  const labels = [
    ["AcessoConferencia","Conferência","conferencia","check"],
    ["AcessoAdmin","Admin","admin","settings"],
    ["AcessoEstacao","Estação","estacao","printer"],
    ["AcessoPrecificador","Precificador","precificador","dollar"]
  ];
  // V18.68: Supervisor = operador COM acesso ao Admin (é assim que o servidor o
  // reconhece). Por isso a caixa Admin segue o perfil: sempre ligada no Supervisor,
  // sempre desligada no Operador. Os outros módulos ficam livres.
  function perfilDe(u){
    const p=String(u?.Perfil||"").toLowerCase();
    return p.includes("admin")?"admin":(p.includes("supervisor")?"supervisor":"operador");
  }
  function travaAdmin(u){
    const p=perfilDe(u);
    if(p==="supervisor") return {valor:true,dica:"O Supervisor sempre tem o Admin. Para tirar, mude o perfil para Operador."};
    if(p==="operador") return {valor:false,dica:"Para dar acesso ao Admin, mude o perfil para Supervisor."};
    return null;
  }

  function getUsers(){
    try { return typeof usuariosEditorAdmin !== "undefined" && Array.isArray(usuariosEditorAdmin) ? usuariosEditorAdmin : []; }
    catch (_) { return []; }
  }
  function accessValue(u, field, key){
    if (typeof u?.[field] === "boolean") return u[field];
    if (u?.Acessos && Object.prototype.hasOwnProperty.call(u.Acessos,key)) return !!u.Acessos[key];
    const perfil = String(u?.Perfil||"").toLowerCase();
    const admin = perfil.includes("admin");
    const supervisor = perfil.includes("supervisor");
    return field==="AcessoConferencia" ? true : (admin || supervisor);
  }
  function applyAccess(u, field, key, value){
    u[field]=!!value;
    u.Acessos = {
      conferencia: accessValue(u,"AcessoConferencia","conferencia"),
      admin: accessValue(u,"AcessoAdmin","admin"),
      estacao: accessValue(u,"AcessoEstacao","estacao"),
      precificador: accessValue(u,"AcessoPrecificador","precificador")
    };
    u.Acessos[key]=!!value;
    if (typeof agendarSalvarUsuariosAdmin === "function") agendarSalvarUsuariosAdmin();
  }
  function buildCell(u){
    const td=document.createElement("td");
    td.className="acessos-modulos-v123";
    const wrap=document.createElement("div");
    wrap.className="usuario-acessos";
    const trava=travaAdmin(u);
    if(trava && accessValue(u,"AcessoAdmin","admin")!==trava.valor){
      u.AcessoAdmin=trava.valor;
      u.Acessos={...(u.Acessos||{}),admin:trava.valor};
    }
    labels.forEach(([field,label,key,icone])=>{
      const lab=document.createElement("label");
      lab.className="usuario-acesso-item";
      const input=document.createElement("input");
      input.type="checkbox";
      input.checked=accessValue(u,field,key);
      input.dataset.permissionField=field;
      if(key==="admin" && trava){
        input.disabled=true;
        lab.title=trava.dica;
        lab.classList.add("usuario-acesso-travado");
      }
      input.addEventListener("change",()=>applyAccess(u,field,key,input.checked));
      const ic=document.createElement("i");
      ic.className="nk-i-"+icone;
      ic.setAttribute("aria-hidden","true");
      const span=document.createElement("span");
      span.textContent=label;
      lab.append(input,ic,span);
      wrap.appendChild(lab);
    });
    td.appendChild(wrap);
    const small=document.createElement("span");
    small.className="usuario-acessos-legenda";
    small.textContent="Marque os módulos liberados para este usuário.";
    td.appendChild(small);
    return td;
  }
  function hydrate(){
    const tbody=document.getElementById("corpoUsuariosAdmin");
    if(!tbody)return;
    const users=getUsers();
    [...tbody.querySelectorAll(":scope > tr")].forEach((tr,index)=>{
      const u=users[index];
      if(!u)return;
      let td=tr.querySelector("td.acessos-modulos-v123");
      const existing=tr.cells[5];
      if(existing && !existing.classList.contains("acessos-modulos-v123")){
        existing.classList.add("acessos-modulos-v123");
        td=existing;
      }
      if(!td){
        td=buildCell(u);
        tr.appendChild(td);
      }else if(!td.querySelector("input[type=checkbox]")){
        const fresh=buildCell(u);
        td.replaceWith(fresh);
      }
    });
  }
  document.addEventListener("DOMContentLoaded",()=>{
    hydrate();
    const tbody=document.getElementById("corpoUsuariosAdmin");
    if(tbody){
      new MutationObserver(()=>hydrate()).observe(tbody,{childList:true,subtree:false});
      // V18.68: trocou o perfil → refaz as caixas da linha (a do Admin segue o perfil).
      tbody.addEventListener("change",ev=>{
        const sel=ev.target;
        if(!sel || sel.tagName!=="SELECT" || sel.dataset.campo!=="Perfil") return;
        const u=getUsers().find(x=>String(x.__uidUsuario)===String(sel.dataset.uidusuario));
        const td=sel.closest("tr")?.querySelector("td.acessos-modulos-v123");
        if(!u || !td) return;
        td.replaceWith(buildCell(u));
        if (typeof agendarSalvarUsuariosAdmin === "function") agendarSalvarUsuariosAdmin();
      });
    }
    setInterval(hydrate,1200);
  });
  window.hidratarPermissoesUsuariosV123=hydrate;
})();