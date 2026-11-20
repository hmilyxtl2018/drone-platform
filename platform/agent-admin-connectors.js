"use strict";
/* Connector administration projects safe catalog fields; it never probes services. */
(() => {
  const kinds = {model:"模型", web:"网络证据", "data-source":"本体数据", local:"本地工具", http:"远程工具"};
  const transports = {python:"本地 Python", sqlite:"SQLite 只读", files:"本地文档", http:"HTTP", https:"HTTPS", "in-process":"进程内", "builtin":"本地处理", "legacy-builtin":"本地兼容处理", in_process_compat:"本地兼容处理", unavailable:"暂不可用"};
  const configuration = {configured:"已配置", invalid:"配置待修正", disabled:"已停用", unknown:"未知"};
  const observations = {success:"最近调用成功", failed:"最近调用失败", unknown:"尚无匹配记录"};
  const issues = {"missing-local-handler":"尚无已注册的本地实现", "legacy-research-binding-invalid":"研究型 Agent 仍绑定旧 HTTP 版本；须发布并绑定明确的执行版本。", "configuration-invalid":"连接配置未通过核验"};
  const policyFields = [
    ["maxConcurrent", "最大并发", 1, 128], ["callsPerMinute", "每分钟调用上限", 1, 100000],
    ["leaseSeconds", "租约秒数", 5, 3600], ["failureThreshold", "连续故障阈值", 1, 100],
    ["cooldownSeconds", "冷却秒数", 1, 3600]
  ];
  const text = value => typeof value === "string" ? value : "";
  const finite = value => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const datetime = value => {
    if (!value || Number.isNaN(new Date(value).getTime())) return "时间未记录";
    return new Date(value).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
  };
  function projectItem(raw) {
    const metrics = raw?.metrics || {};
    return {
      ref:text(raw?.ref), name:text(raw?.name) || text(raw?.ref), kind:kinds[raw?.kind] ? raw.kind : "unknown",
      version:text(raw?.version), actualTransport:text(raw?.actualTransport), configuredTransport:text(raw?.configuredTransport),
      enabled:typeof raw?.enabled === "boolean" ? raw.enabled : null,
      configurationStatus:configuration[raw?.configurationStatus] ? raw.configurationStatus : "unknown",
      observationStatus:observations[raw?.observationStatus] ? raw.observationStatus : "unknown",
      lastObservedAt:text(raw?.lastObservedAt), legacyMismatch:raw?.legacyMismatch === true,
      configurationIssues:Array.isArray(raw?.configurationIssues) ? raw.configurationIssues.filter(v=>typeof v === "string") : [],
      operations:(Array.isArray(raw?.operations) ? raw.operations : []).map(item=>({ref:text(item.ref),toolKey:text(item.toolKey),toolVersion:text(item.toolVersion),name:text(item.name),actualTransport:text(item.actualTransport)})),
      agents:(Array.isArray(raw?.agents) ? raw.agents : []).map(item=>({agentKey:text(item.agentKey),displayName:text(item.displayName)})),
      metrics:{windowHours:finite(metrics.windowHours),calls:integer(metrics.calls),failed:integer(metrics.failed),p95LatencyMs:finite(metrics.p95LatencyMs)},
      policy:raw?.policy && typeof raw.policy === "object" ? Object.fromEntries(["policyVersion","enabled",...policyFields.map(([key])=>key)].map(key=>[key,raw.policy[key]])) : null
    };
  }
  function usageLabel(metrics) {
    if (metrics.calls === null) return "调用量未知";
    const failures = metrics.failed === null ? "失败未知" : "失败 " + metrics.failed;
    return metrics.calls + " 次 / " + failures;
  }
  function policyPayload(policy, values, reason) {
    if (!Number.isSafeInteger(policy?.policyVersion) || policy.policyVersion < 0) throw Error("策略版本未记录，刷新后重试。");
    const result = {expectedPolicyVersion:policy.policyVersion, reason:String(reason || "").trim()};
    if (!result.reason || result.reason.length > 1000) throw Error("请填写 1–1000 字的变更原因。");
    if (typeof values.enabled !== "boolean") throw Error("请选择是否启用调度。");
    result.enabled = values.enabled;
    for (const [key,label,min,max] of policyFields) {
      const value = values[key];
      if (!Number.isSafeInteger(value) || value < min || value > max) throw Error(label + "应为 " + min + "–" + max + " 的整数。");
      result[key] = value;
    }
    return result;
  }
  async function render(panel, {api, notice, canEdit=false, initialRef=""}={}) {
    const make = (tag, value, className) => { const node=document.createElement(tag); if(value!==undefined) node.textContent=value; if(className)node.className=className; return node; };
    const button = (label, action, className) => { const node=make("button",label,className);node.type="button";node.onclick=action;return node; };
    const tip = (copy,label) => window.AgentAdminTips?.create(copy,"info",label) || make("span","");
    const root=make("section",undefined,"connector-workspace"),heading=make("div",undefined,"connector-heading");
    const title=make("h2",undefined,"panel-heading-with-icon"),icon=make("span",undefined,"future-war-stage-icon tools-icon");icon.setAttribute("aria-hidden","true");
    title.append(icon,document.createTextNode("连接器"),tip("配置状态、最近调用和窗口用量分别展示。刷新只读取已保存记录，不发起模型或采集调用。","连接器状态"));
    const refresh=button("刷新记录",()=>load());heading.append(title,refresh);
    const toolbar=make("div",undefined,"connector-toolbar"),search=make("input"),statusFilter=make("select"),filter=make("select"),count=make("span",undefined,"connector-count");
    search.type="search";search.placeholder="搜索连接器、工具或 Agent";search.setAttribute("aria-label","搜索连接器、工具或 Agent");
    statusFilter.setAttribute("aria-label","连接器状态");
    for(const [value,label] of [["enabled","已启用"],["all","全部状态"],["disabled","已停用"]]){const option=make("option",label);option.value=value;statusFilter.append(option);}
    filter.setAttribute("aria-label","连接器类型");
    for(const [value,label] of [["all","全部类型"],...Object.entries(kinds)]){const option=make("option",label);option.value=value;filter.append(option);}
    toolbar.append(search,statusFilter,filter,count);
    const message=make("p",undefined,"connector-message");message.hidden=true;message.setAttribute("role","status");
    const content=make("div",undefined,"connector-content"),list=make("div",undefined,"connector-table-region"),detail=make("aside",undefined,"connector-detail");
    list.setAttribute("aria-label","连接器列表");detail.setAttribute("aria-label","连接器详情");detail.hidden=true;content.append(list,detail);
    root.append(heading,toolbar,message,content);panel.append(root);
    let items=[], selected=initialRef, selectionGeneration=0, busy=false;
    function setMessage(value,error=false){message.textContent=value;message.hidden=!value;message.dataset.error=String(error);}
    function fact(parent,label,value){if(value===undefined||value===null||value==="")return;const row=make("div",undefined,"connector-fact");row.append(make("dt",label),make("dd",String(value)));parent.append(row);}
    function viewRows(){
      const query=search.value.trim().toLocaleLowerCase();
      const visible=items.filter(item=>{
        const disabled=item.enabled===false;
        const matchesStatus=statusFilter.value==="all"||(statusFilter.value==="disabled"?disabled:!disabled);
        return matchesStatus
          &&(filter.value==="all"||item.kind===filter.value)
          &&(!query||[item.name,item.ref,...item.operations.map(op=>op.name+" "+op.toolKey),...item.agents.map(agent=>agent.displayName+" "+agent.agentKey)].join(" ").toLocaleLowerCase().includes(query));
      });
      count.textContent=visible.length+" / "+items.length;
      const table=make("table",undefined,"connector-table"),head=make("thead"),row=make("tr");
      for(const label of ["名称","类型 / 执行方式","启用","配置","最近观测","窗口调用","关联 Agent"])row.append(make("th",label));head.append(row);table.append(head);
      const body=make("tbody");
      for(const item of visible){
        const line=make("tr");line.dataset.ref=item.ref;line.classList.toggle("is-selected",item.ref===selected);
        const name=make("td"),open=button(item.name,()=>select(item.ref),"connector-open");open.setAttribute("aria-expanded",String(item.ref===selected&&!detail.hidden));name.append(open);
        if(item.version)name.append(make("small","v"+item.version));
        const type=make("td");type.append(make("span",kinds[item.kind]||"未知类型"),make("small",transports[item.actualTransport]||item.actualTransport||"执行方式未记录"));
        if(item.legacyMismatch)type.append(tip("登记的协议与当前本地兼容执行方式存在差异；以实际调用和冻结版本为准。","执行方式差异"));
        const enabled=make("td",item.enabled===null?"未知":item.enabled?"是":"否");
        const config=make("td",configuration[item.configurationStatus]);config.dataset.status=item.configurationStatus;
        const observed=make("td"),status=make("span",observations[item.observationStatus]);status.dataset.status=item.observationStatus;observed.append(status);
        if(item.lastObservedAt)observed.append(make("small",datetime(item.lastObservedAt)));
        const usage=make("td",usageLabel(item.metrics));usage.append(make("small",item.metrics.windowHours===null?"窗口未记录":"近 "+item.metrics.windowHours+" 小时"));
        const agents=make("td",item.agents.length?item.agents.map(agent=>agent.displayName||agent.agentKey).join("、"):"未关联");agents.className="connector-agent-cell";
        line.append(name,type,enabled,config,observed,usage,agents);body.append(line);
      }
      table.append(body);list.replaceChildren(table);
      if(!visible.length)list.append(make("p",items.length?"没有匹配的连接器。":"尚无连接器登记。","connector-empty"));
    }
    async function select(ref){
      selected=ref;const generation=++selectionGeneration;detail.hidden=false;root.classList.add("has-selection");viewRows();
      detail.replaceChildren(make("p","正在读取详情…","muted"));
      try{
        const response=await api("/connectors/"+encodeURIComponent(ref));
        if(generation!==selectionGeneration)return;
        const item=projectItem(response.item||response);
        if(item.ref!==ref)throw Error("连接器详情与所选记录不一致。");
        renderDetail(item);
      }catch(error){if(generation!==selectionGeneration)return;detail.replaceChildren(make("p","详情读取失败，请重试。","connector-error"),button("重试",()=>select(ref)));notice?.(error.message,true);}
    }
    function renderDetail(item){
      detail.replaceChildren();
      const top=make("div",undefined,"connector-detail-heading"),name=make("h3",item.name),close=button("关闭",()=>{selectionGeneration++;selected="";detail.hidden=true;root.classList.remove("has-selection");viewRows();});top.append(name,close);detail.append(top);
      const facts=make("dl",undefined,"connector-facts");
      fact(facts,"标识",item.ref);fact(facts,"版本",item.version||"未记录");fact(facts,"登记方式",transports[item.configuredTransport]||item.configuredTransport||"未记录");fact(facts,"实际方式",transports[item.actualTransport]||item.actualTransport||"未记录");fact(facts,"配置",configuration[item.configurationStatus]);fact(facts,"最近观测",observations[item.observationStatus]);fact(facts,"观测时间",item.lastObservedAt?datetime(item.lastObservedAt):"未记录");fact(facts,"窗口调用",usageLabel(item.metrics));
      fact(facts,"P95 延迟",item.metrics.p95LatencyMs===null?"未知":item.metrics.p95LatencyMs+" ms");detail.append(facts);
      if(item.configurationIssues.length){const list=make("ul",undefined,"connector-issues");for(const issue of item.configurationIssues)list.append(make("li",issues[issue]||issue));detail.append(list);}
      const agents=make("section",undefined,"connector-detail-section");agents.append(make("h4","关联 Agent"));
      for(const agent of item.agents){const link=make("a",agent.displayName||agent.agentKey);link.href="#agent="+encodeURIComponent(agent.agentKey);agents.append(link);}if(!item.agents.length)agents.append(make("p","未关联","muted"));detail.append(agents);
      const operations=make("details",undefined,"connector-operations");operations.append(make("summary","操作能力（"+item.operations.length+"）"));
      for(const operation of item.operations){const entry=make("div",undefined,"connector-operation");entry.append(make("strong",operation.name||operation.toolKey),make("small",[operation.toolKey,operation.toolVersion&&"v"+operation.toolVersion,transports[operation.actualTransport]||operation.actualTransport].filter(Boolean).join(" · ")));operations.append(entry);}detail.append(operations);
      if(item.policy)renderPolicy(item);
    }
    function renderPolicy(item){
      const policy=item.policy,section=make("details",undefined,"connector-policy");section.append(make("summary","调度策略"));
      const version=make("p","策略版本 "+(Number.isSafeInteger(policy.policyVersion)?policy.policyVersion:"未记录"),"muted");section.append(version);
      if(!canEdit){const facts=make("dl",undefined,"connector-facts");fact(facts,"允许调度",typeof policy.enabled==="boolean"?(policy.enabled?"是":"否"):"未知");for(const [key,label] of policyFields)fact(facts,label,Number.isSafeInteger(policy[key])?policy[key]:"未设置");section.append(facts);detail.append(section);return;}
      const form=make("form",undefined,"connector-policy-form"),fields={};
      const enabledLabel=make("label","允许调度","check"),enabled=make("input");enabled.type="checkbox";enabled.checked=policy.enabled===true;enabledLabel.prepend(enabled);form.append(enabledLabel);
      const grid=make("div",undefined,"connector-policy-grid");
      for(const [key,label,min,max] of policyFields){const field=make("label",label),input=make("input");input.type="number";input.min=String(min);input.max=String(max);input.step="1";input.required=true;input.value=Number.isSafeInteger(policy[key])?String(policy[key]):"";field.append(input);grid.append(field);fields[key]=input;}form.append(grid);
      const reasonLabel=make("label","变更原因"),reason=make("textarea");reason.rows=2;reason.maxLength=1000;reason.required=true;reasonLabel.append(reason);form.append(reasonLabel);
      const save=make("button","保存策略");save.type="submit";save.disabled=!Number.isSafeInteger(policy.policyVersion);form.append(save);
      const feedback=make("p",undefined,"connector-policy-feedback");feedback.setAttribute("role","status");form.append(feedback);
      form.onsubmit=async event=>{event.preventDefault();if(busy)return;
        try{const values={enabled:enabled.checked};for(const [key] of policyFields)values[key]=fields[key].value.trim()===""?null:Number(fields[key].value);const body=policyPayload(policy,values,reason.value);busy=true;save.disabled=true;feedback.textContent="正在保存…";
          await api("/connectors/"+encodeURIComponent(item.ref)+"/policy",{method:"PUT",body:JSON.stringify(body)});
          notice?.("调度策略已保存。");await load(item.ref);
        }catch(error){feedback.textContent=error.message;feedback.dataset.error="true";notice?.(error.message,true);}
        finally{busy=false;save.disabled=!Number.isSafeInteger(policy.policyVersion);}
      };section.append(form);detail.append(section);
    }
    async function load(ref=selected){
      refresh.disabled=true;setMessage("");
      try{const value=await api("/connectors");
        if(value.schemaVersion!=="connector-catalog.v1"||!Array.isArray(value.items))throw Error("连接器目录版本不匹配，请更新服务后刷新。");
        items=value.items.map(projectItem);viewRows();
        if(ref&&items.some(item=>item.ref===ref))await select(ref);
        else if(ref){selectionGeneration++;selected="";detail.hidden=true;root.classList.remove("has-selection");setMessage("所选连接器已不在当前目录。");}
      }catch(error){setMessage("连接器记录读取失败："+error.message,true);if(!items.length)list.replaceChildren(make("p","刷新记录以重试。","connector-empty"));}
      finally{refresh.disabled=false;}
    }
    search.oninput=viewRows;filter.onchange=viewRows;statusFilter.onchange=viewRows;
    await load();
    return root;
  }
  window.AgentAdminConnectors=Object.freeze({render,projectItem,usageLabel,policyPayload});
})();
