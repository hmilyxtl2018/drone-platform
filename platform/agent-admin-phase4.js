"use strict";
// Identity management uses the existing HttpOnly session; credentials are never stored.
async function renderIdentities(panel){
  panel.append(el("h2","用户与访问权限"));
  if(!hasRole("admin")){panel.append(el("p","需要管理员角色维护用户与令牌。","muted"));return;}
  const issued=el("section",undefined,"card");issued.hidden=true;
  const form=el("form");
  function field(parent,labelText,type="text",value=""){
    const label=el("label",labelText),input=el("input");input.type=type;input.value=value;label.append(input);parent.append(label);return input;
  }
  const name=field(form,"新用户名称");name.required=true;name.maxLength=120;
  const role=el("select");for(const [key,label]of[["operator,reviewer","操作与审核"],["operator","仅操作"],["reviewer","仅审核"],["admin","管理员"]]){const option=el("option",label);option.value=key;role.append(option);}
  const roleLabel=el("label","权限角色");roleLabel.append(role);form.append(roleLabel);
  const days=field(form,"有效天数","number","1");days.min="1";days.max="365";days.required=true;
  const reason=field(form,"创建理由");reason.required=true;reason.maxLength=2000;
  const submit=el("button","创建用户并签发一次性可见令牌");form.append(submit);
  form.onsubmit=async event=>{
    event.preventDefault();submit.disabled=true;
    try{
      const result=await api("/identities",{method:"POST",body:JSON.stringify({displayName:name.value,roles:role.value.split(","),expiresDays:Number(days.value),reason:reason.value})});
      issued.replaceChildren(el("h3","请将令牌交给该操作者本人"),el("p","此处仅本次显示，关闭后不能从服务器找回。","help-note help-caution"),el("p","过期时间："+result.expiresAt));issued.hidden=false;
      const tokenField=field(issued,"访问令牌","password",result.token);tokenField.readOnly=true;tokenField.autocomplete="off";
      const copy=el("button","复制令牌"),clear=el("button","清除显示");
      copy.onclick=async()=>{try{await navigator.clipboard.writeText(tokenField.value);notice("令牌已复制");}catch{notice("浏览器未允许复制，可手动选择令牌复制。",true);}};
      clear.onclick=()=>{tokenField.value="";issued.replaceChildren();issued.hidden=true;};
      issued.append(copy,clear);form.reset();await refreshIdentityList();
    }catch(error){notice(error.message,true);}finally{submit.disabled=false;}
  };
  panel.append(form,issued);
  const list=el("div");panel.append(list);
  async function change(identity,patch){
    const reasonText=window.prompt("填写权限变更理由");if(!reasonText)return;
    try{await api("/identities/"+encodeURIComponent(identity.principalId),{method:"PATCH",body:JSON.stringify({expectedRevision:identity.revision,reason:reasonText,patch})});notice("用户权限已更新");await refreshIdentityList();}
    catch(error){notice(error.message,true);}
  }
  async function refreshIdentityList(){
    const [identities,tokens]=await Promise.all([api("/identities"),api("/identity-tokens")]);
    list.replaceChildren(el("h3","当前用户"));
    for(const identity of identities){
      const block=el("section",undefined,"config-row");
      block.append(el("strong",identity.displayName),el("p",identity.roles.join(" / ")+" · "+(identity.enabled?"启用":"停用")+" · 有效令牌 "+identity.activeTokenCount));
      const rolesLabel=el("label","角色"),roles=el("div");const choices=[];
      for(const [key,title]of[["operator","操作"],["reviewer","审核"],["admin","管理"]]){const label=el("label",title,"check"),box=el("input");box.type="checkbox";box.value=key;box.checked=identity.roles.includes(key);label.prepend(box);roles.append(label);choices.push(box);}
      rolesLabel.append(roles);block.append(rolesLabel);
      const save=el("button","保存角色"),toggle=el("button",identity.enabled?"停用用户":"启用用户");
      save.onclick=()=>change(identity,{roles:choices.filter(box=>box.checked).map(box=>box.value)});
      toggle.onclick=()=>change(identity,{enabled:!identity.enabled});block.append(save,toggle);
      for(const token of tokens.filter(t=>t.principalId===identity.principalId)){
        const row=el("p","令牌 "+token.tokenId+" · "+token.status+" · 到期 "+token.expiresAt);
        if(token.status==="active"){
          const revoke=el("button","撤销");revoke.onclick=async()=>{const reasonText=window.prompt("填写撤销理由");if(!reasonText)return;try{await api("/identity-tokens/"+encodeURIComponent(token.tokenId)+"/revoke",{method:"POST",body:JSON.stringify({reason:reasonText})});await refreshIdentityList();}catch(error){notice(error.message,true);}};row.append(revoke);
        }block.append(row);
      }list.append(block);
    }
    const history=await api("/identity-audit");const details=el("details");details.append(el("summary","查看权限变更审计"),json(history));list.append(details);
  }
  await refreshIdentityList();
}
