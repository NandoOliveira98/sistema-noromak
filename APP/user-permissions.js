(() => {
  "use strict";
  const labels = [
    ["AcessoConferencia","📦 Conferência","conferencia"],
    ["AcessoAdmin","⚙ Admin","admin"],
    ["AcessoEstacao","🖨 Estação","estacao"],
    ["AcessoPrecificador","💲 Precificador","precificador"]
  ];

  function getUsers(){
    try { return typeof usuariosEditorAdmin !== "undefined" && Array.isArray(usuariosEditorAdmin) ? usuariosEditorAdmin : []; }
    catch (_) { return []; }
  }
  function accessValue(u, field, key){
    if (typeof u?.[field] === "boolean") return u[field];
    if (u?.Acessos && Object.prototype.hasOwnProperty.call(u.Acessos,key)) return !!u.Acessos[key];
    const admin = String(u?.Perfil||"").toLowerCase().includes("admin");
    return field==="AcessoConferencia" ? true : admin;
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
    labels.forEach(([field,label,key])=>{
      const lab=document.createElement("label");
      lab.className="usuario-acesso-item";
      const input=document.createElement("input");
      input.type="checkbox";
      input.checked=accessValue(u,field,key);
      input.dataset.permissionField=field;
      input.addEventListener("change",()=>applyAccess(u,field,key,input.checked));
      const span=document.createElement("span");
      span.textContent=label;
      lab.append(input,span);
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
    }
    setInterval(hydrate,1200);
  });
  window.hidratarPermissoesUsuariosV123=hydrate;
})();