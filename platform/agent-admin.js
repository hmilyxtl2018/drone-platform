"use strict";
/* Only explicitly marked help copy becomes a tip; status and audit data stay visible. */
(() => {
  const kinds = {
    info: { label: "信息" },
    help: { label: "帮助" },
    flow: { label: "流程" },
    caution: { label: "注意" }
  };
  const payloads = new WeakMap();
  const popup = document.createElement("div");
  popup.id = "agent-mini-tip";
  popup.className = "mini-tip-popup";
  popup.setAttribute("role", "tooltip");
  popup.hidden = true;
  document.body.append(popup);
  let active = null, pinned = false, closeTimer = 0, frame = 0;

  function create(content, kind = "info", title = "说明") {
    kind = Object.hasOwn(kinds, kind) ? kind : "info";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mini-tip";
    button.dataset.tipKind = kind;
    button.setAttribute("aria-label", title + " · " + kinds[kind].label);
    button.setAttribute("aria-expanded", "false");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    const circle = document.createElementNS(svg.namespaceURI, "circle");
    circle.setAttribute("cx", "8"); circle.setAttribute("cy", "8"); circle.setAttribute("r", "6");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", "M8 4.5v4M8 11v.1");
    svg.append(circle, path);
    button.append(svg);
    payloads.set(button, {content, kind, title});
    return button;
  }

  function anchorFor(note) {
    if (note.dataset.tipFor) return document.getElementById(note.dataset.tipFor);
    const selector = "h1,h2,h3,h4,strong,summary,.profile-title";
    for (let previous = note.previousElementSibling; previous; previous = previous.previousElementSibling) {
      if (previous.matches(selector)) return previous;
    }
    const section = note.closest(".profile-card,section,fieldset");
    return section?.querySelector(":scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > summary") || null;
  }

  function enhance(root) {
    const notes = root.matches?.(".help-note") ? [root] : [...(root.querySelectorAll?.(".help-note") || [])];
    for (const note of notes) {
      if (!note.isConnected || note.closest(".mini-tip-popup")) continue;
      const kind = note.dataset.tipKind || Object.keys(kinds).find(key => note.classList.contains("help-" + key)) || "info";
      const anchor = anchorFor(note);
      const summary = note.querySelector(":scope > summary");
      const title = note.dataset.tipTitle || summary?.textContent ||
        (anchor ? [...anchor.childNodes].filter(node => !node.classList?.contains("mini-tip")).map(node => node.textContent).join("").trim() : "说明");
      const content = document.createDocumentFragment();
      for (const child of note.childNodes) if (child !== summary) content.append(child.cloneNode(true));
      const tip = create(content, kind, title);
      if (anchor) { anchor.append(tip); note.remove(); } else note.replaceWith(tip);
    }
  }

  function hide() {
    clearTimeout(closeTimer);
    if (active) { active.setAttribute("aria-expanded", "false"); active.removeAttribute("aria-describedby"); }
    active = null; pinned = false; popup.hidden = true; popup.replaceChildren();
  }
  function position() {
    frame = 0;
    if (!active) return;
    if (!active.isConnected || !active.getClientRects().length) { hide(); return; }
    const anchor = active.getBoundingClientRect();
    const width = document.documentElement.clientWidth, height = window.innerHeight, gap = 8;
    if (anchor.bottom < 0 || anchor.top > height || anchor.right < 0 || anchor.left > width) { hide(); return; }
    const bounds = popup.getBoundingClientRect();
    const below = anchor.bottom + gap, above = anchor.top - bounds.height - gap;
    popup.style.left = Math.max(10, Math.min(anchor.left - 8, width - bounds.width - 10)) + "px";
    popup.style.top = Math.max(10, Math.min(below + bounds.height <= height - 10 ? below : above, height - bounds.height - 10)) + "px";
  }
  function show(button) {
    const value = payloads.get(button);
    if (!value) return;
    clearTimeout(closeTimer);
    if (active !== button) { hide(); active = button; }
    button.setAttribute("aria-describedby", popup.id);
    button.setAttribute("aria-expanded", "true");
    popup.dataset.tipKind = value.kind;
    const heading = document.createElement("div"), body = document.createElement("div");
    heading.className = "mini-tip-heading"; heading.textContent = value.title;
    body.className = "mini-tip-body";
    value.content instanceof Node ? body.append(value.content.cloneNode(true)) : body.textContent = String(value.content);
    popup.replaceChildren(heading, body);
    popup.hidden = false;
    position();
  }
  function dismissLater() {
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => {
      if (!pinned && active && document.activeElement !== active && !active.matches(":hover") && !popup.matches(":hover")) hide();
    }, 180);
  }
  document.addEventListener("pointerover", event => {
    const button = event.target.closest?.(".mini-tip");
    if (event.pointerType !== "touch" && button && !button.contains(event.relatedTarget) && (!pinned || active === button)) show(button);
    if (popup.contains(event.target)) clearTimeout(closeTimer);
  });
  document.addEventListener("pointerout", event => {
    if (event.target.closest?.(".mini-tip") || popup.contains(event.target)) dismissLater();
  });
  document.addEventListener("focusin", event => {
    if (event.target.matches?.(".mini-tip")) show(event.target);
  });
  document.addEventListener("focusout", event => {
    if (event.target.matches?.(".mini-tip")) dismissLater();
  });
  document.addEventListener("click", event => {
    const button = event.target.closest?.(".mini-tip");
    if (button) {
      event.preventDefault(); event.stopPropagation();
      if (active === button && pinned) hide(); else { show(button); pinned = true; }
    } else if (!popup.contains(event.target)) hide();
  }, true);
  document.addEventListener("keydown", event => { if (event.key === "Escape" && active) { event.preventDefault(); hide(); } });
  const reposition = () => { if (active && !frame) frame = requestAnimationFrame(position); };
  window.addEventListener("resize", reposition);
  document.addEventListener("scroll", reposition, true);
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if (node.nodeType === Node.ELEMENT_NODE && !popup.contains(node)) enhance(node);
    if (active && (!active.isConnected || !active.getClientRects().length)) hide();
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"]});
  window.AgentAdminTips = Object.freeze({create, enhance});
  enhance(document);
})();
function miniTip(content, kind = "info", title = "说明") { return window.AgentAdminTips.create(content, kind, title); }
const API = "/api/agent-admin";
const state = {actor:null, runId:null, area:"agents", tab:"graph", run:null, reviewSummary:null, reviewSummaryError:null, initialRunTabPending:false, cursor:0, events:[], agents:[], agentKey:null, refreshing:false, pendingRefresh:false, pendingRefreshForce:false, renderGeneration:0};
const runTabChoices = new Map();
const runTaskNames = new Map();
// Historical pauses and human gates are selectable history, not live execution.
const activeRunStates = new Set(["running","planning","queued","created","pending"]);
const savedRunKey = agentKey => "ontology-agent:last-run:" + agentKey;
function savedRun(agentKey) { try { return localStorage.getItem(savedRunKey(agentKey)); } catch { return null; } }
function rememberRun(run) { if (!run?.agent_key || !run?.run_id) return; try { localStorage.setItem(savedRunKey(run.agent_key), run.run_id); } catch {} }
function runTimeLabel(value) { const date=new Date(value); return !value || Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}); }
function runTaskTitle(run) {
  const goal=run.task_title||run.task_goal||runTaskNames.get(run.run_id);
  return goal ? String(goal).replace(/\s+/g," ").slice(0,64) : "未记录任务简称";
}
let runListGeneration = 0;
let runSelectionGeneration = 0;
let runHeaderSignature = null;
function setActiveAgent(agentKey) {
  if (!agentKey) return;
  const runAgentKey = state.run?.agent_key || state.run?.agentKey;
  if (agentKey !== state.agentKey || (runAgentKey && runAgentKey !== agentKey)) {
    state.renderGeneration++;
    runSelectionGeneration++;
    state.runId = null;
    state.run = null;
    state.reviewSummary = null;
    state.reviewSummaryError = null;
    state.cursor = 0;
    state.events = [];
  }
  state.agentKey = agentKey;
  if (state.agents.some(agent => agent.agent_key === agentKey)) $("agent").value = agentKey;
}
const RUN_NAVIGATION = {
  action: new Set(["graph", "plans", "budget", "events"]),
  application: new Set(["graph", "workspaces", "report", "plans", "evidence", "budget", "engineering", "traceability", "knowledge", "events"]),
  future: new Set(["graph", "workspaces", "report", "plans", "evidence", "budget", "traceability", "events"]),
};
function runLayer(run = state.run) {
  const key = run?.agent_key || run?.agentKey;
  if (["future-battlefield-analyst", "future-battlefield-codex"].includes(key)) return "future";
  return run?.agent_layer || run?.agentLayer || state.agents.find(item => item.agent_key === key)?.agent_layer || "application";
}
function applyRunNavigation(run = state.run) {
  const allowed = RUN_NAVIGATION[runLayer(run)] || RUN_NAVIGATION.application;
  document.querySelectorAll('[data-tab-group="runs"] [data-tab]').forEach(button => {
    const visible = allowed.has(button.dataset.tab);
    if (button.hidden !== !visible) button.hidden = !visible;
    if (button.getAttribute("aria-hidden") !== String(!visible)) button.setAttribute("aria-hidden", String(!visible));
  });
  if (!allowed.has(state.tab)) state.tab = "graph";
  const title = document.querySelector("#run-history-card h2");
  if (title) {
    const activeAgentKey = run?.agent_key || run?.agentKey || state.agentKey;
    const activeAgent = state.agents.find(item => item.agent_key === activeAgentKey);
    const label = activeAgentKey ? "最近运行 · " + (activeAgent?.display_name || activeAgentKey) : "最近运行";
    if (title.textContent !== label) title.textContent = label;
  }
}
function runStatusLabel(run) {
  const status = run?.status;
  if (status === "created") return "已创建 · 未启动";
  if (status === "waiting-human") return "等待人工操作";
  if (status === "completed-with-gaps") return "自动完成 · 有缺口";
  if (status === "requires-human-review") return "发布前审阅";
  if (status === "completed") return "自动完成";
  if (status === "failed") return "失败";
  if (status === "blocked") return "已阻断";
  if (status === "running") return "自动运行中";
  if (status === "planning") return "准备中";
  if (status === "queued") return "排队中";
  return status || "未知";
}
function runCurrentStepLabel(run) {
  const key = run?.current_step_key || run?.currentStepKey;
  if (!key) return "—";
  const mode = run?.automationMode || run?.input_json?.automationMode || (run?.status === "waiting-human" ? "human-gate" : "unattended-draft");
  const automatic = mode !== "human-gate";
  const labels = {
    "define-research-question": "定义研判问题",
    "generate-research-hypotheses": "生成研究假设",
    "plan-source-coverage": "规划来源覆盖",
    "collect-public-evidence": "采集公开证据",
    "extract-facts": "事实与事件抽取",
    "extract-war-system": "作战体系要素抽取",
    "fuse-evidence-and-conflicts": "证据融合与冲突识别",
    "analyze-by-perspective": "多视角趋势研判",
    "red-team-challenge": "红队反证与不确定性审查",
    "compose-judgment-report": "编排研判报告",
    "human-review": automatic ? "发布准备（运行外审阅）" : "人工审核与发布门控"
  };
  return labels[key] || key;
}
const TASK_GOAL_DEFAULTS = Object.freeze({
  "future-battlefield-analyst": [
    "请依据本次研究框架设定的基准时点、展望期限和地域，研判高强度对抗条件下的未来战场形态与关键特征，重点分析多域协同、有人/无人协同、智能化决策、低成本集群、数据链对抗、精确打击与体系韧性。",
    "请按本次已启用的研判视角分别分析，覆盖作战体系、技术演化、装备能力、工业制造与保障、对手适应、风险与反例，并明确各项判断的适用条件。",
    "输出必须区分事实、判断、假设、未知和待验证问题；每条事实绑定公开来源、原文引文、时间和定位；保留来源冲突，不把模型推测当作事实。",
    "最终给出未来战场形态判断、关键能力清单、装备发展方向、工程验证需求、主要不确定性、预警指标和红队反证意见。结论用于装备论证和能力规划，不直接形成作战行动指令。"
  ].join("\n\n")
});
const taskGoalSessions = new Map();
function syncTaskGoal(agentKey) {
  const field = $("question");
  if (!field) return;
  const previousAgent = field.dataset.taskGoalAgent;
  if (previousAgent && previousAgent !== agentKey) {
    taskGoalSessions.set(previousAgent, field.value);
  }
  if (previousAgent === agentKey) return;
  const saved = taskGoalSessions.get(agentKey);
  const defaultValue = TASK_GOAL_DEFAULTS[agentKey === "future-battlefield-codex" ? "future-battlefield-analyst" : agentKey] || "";
  field.value = saved ?? defaultValue;
  field.dataset.taskGoalAgent = agentKey || "";
  field.rows = defaultValue ? 8 : 3;
}
const $ = id => document.getElementById(id);
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(className)node.className=className;return node;}
function notice(text,error=false){$("message").textContent=text;$("message").className=error?"error":"";$("message").style.display="block";}
async function api(path,options={}){const response=await fetch(API+path,{credentials:"same-origin",...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});let data;try{data=await response.json();}catch{data={detail:"服务返回了无效响应"};}if(!response.ok){const error=Error(typeof data.detail==="string"?data.detail:data.detail?.message||JSON.stringify(data.detail));error.status=response.status;error.detail=data.detail;throw error;}return data;}
function json(value){return el("pre",JSON.stringify(value,null,2));}
function hasRole(role){return state.actor&&(state.actor.roles.includes(role)||state.actor.roles.includes("admin"));}
async function login() {
  try {
    state.actor = await api("/me");
    $("login").hidden = true;
    $("workspace").hidden = false;
    $("signout").hidden = state.actor.auth_mode === "local";
    $("identity").textContent = state.actor.auth_mode === "local" ? "本地操作员 · 免登录" : state.actor.display_name + " · " + state.actor.roles.join(" / ");
    await loadAgents();
    // Resolve the Profile before loading history, including after a reload.
    await applyAgentAdminRoute();
    if (!location.hash) {
      const legacy = new URLSearchParams(location.search).get("analysisRunId");
      if (legacy) {
        try {
          const link = await api("/legacy-runs/" + encodeURIComponent(legacy));
          await selectRun(link.run_id || link.runId);
        } catch { notice("此历史 AnalysisRun 尚无 Engine 关联；未重复启动任务。", false); }
      }
    }
  } catch (error) {
    const local = state.actor?.auth_mode === "local";
    notice(error.message, true);
    state.actor = null;
    $("login").hidden = local;
    $("workspace").hidden = true;
    $("identity").textContent = local ? "本地工作台加载失败" : "未登录";
  }
}
async function loadAgents(){
  const rows=await api("/agents");state.agents=rows;
  const previous=$("agent").value;$("agent").replaceChildren();
  for(const row of rows){const option=el("option",row.display_name||row.agent_key);option.value=row.agent_key;$("agent").append(option);}
  const preferred=previous||state.agentKey||"uav-capability-analyst";
  if(rows.some(row=>row.agent_key===preferred)){$("agent").value=preferred;state.agentKey=preferred;}
  $("create-form").querySelector("button[type=submit]").disabled=true;
  $("agent").onchange=()=>{
    setActiveAgent($("agent").value);window.AgentAdminJourney?.updateTaskProfileHint();
    if(state.area==="task"){
      history.replaceState(null,"","#task="+encodeURIComponent(state.agentKey));
      render().catch(e=>notice(e.message,true));
    }
  };
  window.AgentAdminJourney?.updateTaskProfileHint();
}
async function loadRuns(){
  const generation=++runListGeneration;
  const activeAgentKey=state.run?.agent_key || state.run?.agentKey || state.agentKey;
  const runs=await api("/runs?limit=50" + (activeAgentKey ? "&agentKey=" + encodeURIComponent(activeAgentKey) : ""));
  if (generation !== runListGeneration || activeAgentKey !== (state.run?.agent_key || state.run?.agentKey || state.agentKey)) return;
  // Keep the history rail scoped to the selected Agent Profile. Mixing
  // sibling application/action runs makes the active context hard to read.
  const filtered=activeAgentKey
    ? runs.filter(row=>row.agent_key===activeAgentKey)
    : runs;
  applyRunNavigation();
  $("runs").replaceChildren();
  $("runs").dataset.agentKey=activeAgentKey || "";
  if(!filtered.length){
    $("runs").append(el("p","当前 Agent 暂无运行记录。","muted"));
    return [];
  }
  const ordered=[...filtered].sort((a,b)=>Number(activeRunStates.has(b.status))-Number(activeRunStates.has(a.status))||Date.parse(b.created_at)-Date.parse(a.created_at));
  for(const row of ordered){
    const agent=state.agents.find(item=>item.agent_key===row.agent_key);
    const button=el("button",undefined,"run-item"+(row.run_id===state.runId?" active":""));
    button.dataset.agentKey=row.agent_key;
    button.dataset.runId=row.run_id;
    const fullGoal=row.task_goal||runTaskNames.get(row.run_id)||runTaskTitle(row);
    button.title=fullGoal+"\n"+(agent?.display_name||row.agent_key)+"\n"+row.run_id;
    button.append(el("strong",runTaskTitle(row),"run-item-task"));
    const role={application:"应用",action:"Action",atomic:"原子"}[row.agent_layer]||"Agent";
    const description=runTimeLabel(row.created_at)+" · "+role+" · "+runStatusLabel(row);
    button.append(el("small",description,"run-item-state"));
    button.setAttribute("aria-label",fullGoal+"，"+description+"，"+row.run_id);
    button.onclick=()=>selectRun(row.run_id).catch(e=>notice(e.message,true));
    $("runs").append(button);
  }
  return ordered;
}
async function restoreAgentRun(rows,agentKey){
  if(!agentKey||state.runId||state.area!=="runs")return false;
  const generation=runListGeneration,remembered=savedRun(agentKey);
  let chosen=(rows||[]).find(row=>row.run_id===remembered&&row.agent_key===agentKey);
  if(!chosen&&remembered&&/^run-[a-zA-Z0-9-]{1,100}$/.test(remembered)){
    try{const data=await api("/runs/"+encodeURIComponent(remembered));if(data.run?.agent_key===agentKey)chosen=data.run;}catch{}
  }
  if(generation!==runListGeneration||state.agentKey!==agentKey||state.runId||state.area!=="runs")return false;
  if(!chosen)chosen=(rows||[]).find(row=>row.agent_key===agentKey&&activeRunStates.has(row.status))||(rows||[]).find(row=>row.agent_key===agentKey);
  if(!chosen)return false;
  history.replaceState(null,"","#run="+encodeURIComponent(chosen.run_id));
  await selectRun(chosen.run_id,{updateHash:false});return true;
}
async function selectRun(id,{updateHash=true}={}){
  if(!id)return;
  if(id!==state.runId||state.area!=="runs"){
    runSelectionGeneration++;
    state.tab=runTabChoices.get(id)||"graph";
    state.initialRunTabPending=!runTabChoices.has(id);
    state.reviewSummary=null;state.reviewSummaryError=null;
    window.AgentAdminReportGraph.dispose($("panel"));
    $("panel").replaceChildren(el("p","正在读取所选运行…","muted"));
    $("panel").setAttribute("aria-busy","true");
    $("run-title").textContent="正在切换运行";
    $("run-status").textContent="读取中";
    $("controls").hidden=true;$("followup-section").hidden=true;$("run-link").replaceChildren();
    for(const row of $("runs").querySelectorAll(".run-item"))row.classList.toggle("active",row.dataset.runId===id);
    window.AgentAdminReviewSummary.mountHeader($("summary"),null);
  }
  state.area="runs";
  if(["config","connectors","evaluation","operations","identities"].includes(state.tab))state.tab="graph";
  state.renderGeneration++;state.runId=id;state.run=null;state.cursor=0;state.events=[];
  const nextHash="#run="+encodeURIComponent(id);if(updateHash&&location.hash!==nextHash)location.hash=nextHash;
  await refreshRun();await loadRuns();
}
async function refreshRun({poll=false}={}){
  if(!state.runId)return;
  if(state.refreshing){state.pendingRefresh=true;state.pendingRefreshForce ||= !poll;return;}
  state.refreshing=true;
  const runId=state.runId, selectionGeneration=runSelectionGeneration, prefix="/runs/"+encodeURIComponent(runId);
  try{
    const [data,followups,children,reviewResult]=await Promise.all([
      api(prefix),api(prefix+"/followups"),api(prefix+"/handoffs"),
      api("/report-review/runs/"+encodeURIComponent(runId)+"/summary",{signal:AbortSignal.timeout(10000)}).then(value=>value?.schemaVersion==="review-summary.v1"&&value?.identity?.runId===runId?{value}:{error:"摘要身份或版本不匹配，请重新读取。"},error=>({error:error.message}))
    ]);
    if(runId!==state.runId||selectionGeneration!==runSelectionGeneration)return;
    state.run=data.run;
    state.reviewSummary=reviewResult.value||null;
    state.reviewSummaryError=reviewResult.error||null;
    state.agentKey=data.run.agent_key;
    rememberRun(data.run);
    if(state.reviewSummary?.identity?.taskGoal)runTaskNames.set(runId,state.reviewSummary.identity.taskGoal);
    if(state.initialRunTabPending&&state.reviewSummary){
      const preferred=state.reviewSummary.execution?.terminal&&state.reviewSummary.execution?.reportAvailable?"report":"graph";
      const allowed=RUN_NAVIGATION[runLayer(data.run)]||RUN_NAVIGATION.application;
      state.tab=runTabChoices.get(runId)||(allowed.has(preferred)?preferred:"graph");
      state.initialRunTabPending=false;
    }
    if(poll&&state.area!=="runs")return;
    const nextHeaderSignature=JSON.stringify([runId,data,followups,children,reviewResult]);
    if(!poll||nextHeaderSignature!==runHeaderSignature){
      applyRunNavigation(data.run);
      renderFollowups(runId,data.run,followups);
      const title=state.reviewSummary?.identity?.taskTitle||state.reviewSummary?.identity?.agentName||data.run.agent_key;
      if($("run-title").textContent!==title)$("run-title").textContent=title;
      $("run-title").title=state.reviewSummary?.identity?.taskGoal||title;
      window.AgentAdminVerification?.mount($("run-title").parentElement,{subject:{kind:"run-integrity",run_id:runId},api,label:"核验"});
      const executionLabel=state.reviewSummary?.execution?.label||runStatusLabel(data.run);
      if($("run-status").textContent!==executionLabel)$("run-status").textContent=executionLabel;
      const terminalRun = ["completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"].includes(data.run.status);
      $("controls").hidden=!hasRole("operator") || terminalRun;
      window.AgentAdminReviewSummary.mountHeader($("summary"),state.reviewSummary,{error:state.reviewSummaryError,queueStatus:data.queue?.status,children,tip:miniTip});
      $("panel").removeAttribute("aria-busy");
      const href="/agent-admin.html#run="+encodeURIComponent(runId);
      if($("run-link").firstElementChild?.getAttribute("href")!==href){const link=el("a","运行链接");link.href=href;link.title=runId;$("run-link").replaceChildren(link);}
      runHeaderSignature=nextHeaderSignature;
    }
    // A steady heartbeat still reads the graph: child attempts and invocation
    // receipts may change while the parent Run and its summary remain equal.
    await render({poll});
  }finally{
    state.refreshing=false;
    if(state.pendingRefresh||runId!==state.runId||selectionGeneration!==runSelectionGeneration){
      const force=state.pendingRefreshForce||runId!==state.runId||selectionGeneration!==runSelectionGeneration;
      state.pendingRefresh=false;state.pendingRefreshForce=false;
      refreshRun({poll:!force}).catch(e=>notice(e.message,true));
    }
  }
}
async function render({poll=false}={}){
  if(window.AgentAdminJourney){
    window.AgentAdminJourney.sync();
    if(state.area==="agents"){await window.AgentAdminJourney.renderAgents();return;}
    if(state.area==="audit"){await window.AgentAdminJourney.renderAudit();return;}
    if(state.area==="task"){await window.AgentAdminJourney.renderTask();return;}
    if(state.area==="runs"&&!state.runId){window.AgentAdminJourney.renderExecutionEmpty();return;}
    if(state.area==="settings"&&!hasRole("admin")){state.area="agents";window.AgentAdminJourney.sync();notice("管理设置需要 admin 权限",true);await window.AgentAdminJourney.renderAgents();return;}
  }
  const generation=++state.renderGeneration;
  const view={runId:state.runId,tab:state.tab,run:state.run,reviewSummary:state.reviewSummary,reviewSummaryError:state.reviewSummaryError,cursor:state.cursor,events:[...state.events]};
  const mountedGraph=$("panel").querySelector(":scope > .dag-view");
  const mountedReport=$("panel").querySelector(":scope > .future-report");
  const mountedCognitive=$("panel").querySelector(":scope > .cognitive-report");
  if(poll&&view.tab==="report"&&mountedCognitive?.dataset.runId===view.runId&&view.run?.output_json){
    let output=view.run.output_json;
    if(typeof output==="string"){try{output=JSON.parse(output);}catch{}}
    if(window.AgentAdminCognitive?.sameReport(mountedCognitive,output))return;
  }
  if(poll&&view.tab==="report"&&mountedReport?.dataset.runId===view.runId&&view.run?.output_json){
    window.AgentAdminReviewSummary.mountResult(mountedReport,view.reviewSummary,{error:view.reviewSummaryError,tip:miniTip});
    let output=view.run.output_json;
    if(typeof output==="string"){try{output=JSON.parse(output);}catch{}}
    // Heartbeats must not reset graph selection, zoom or evidence inspection.
    if(futureReportSnapshots.get(mountedReport)===JSON.stringify(output))return;
  }
  if(view.tab==="graph"&&mountedGraph?.dataset.runId===view.runId){
    // Refresh the mounted graph in place; unchanged snapshots do not touch its DOM.
    await window.AgentAdminGraph.refresh(mountedGraph,{force:!poll,isCurrent:()=>generation===state.renderGeneration&&view.runId===state.runId&&view.tab===state.tab&&state.area==="runs"});
    return;
  }
  const content=document.createDocumentFragment();
  await renderContent(content,view);
  if(generation!==state.renderGeneration||view.runId!==state.runId||view.tab!==state.tab)return;
  window.AgentAdminReportGraph.dispose($("panel"));
  $("panel").replaceChildren(content);
  window.AgentAdminReportGraph.activate($("panel"));
  document.querySelectorAll("[data-tab]").forEach(button=>button.classList.toggle("selected",button.dataset.tab===view.tab));
  if(view.tab==="events"){state.events=view.events;state.cursor=view.cursor;}
}
async function renderFutureEvidence(panel, view, prefix) {
  const bundle = await api(prefix + "/evidence");
  const title = el("h2", "研判证据");
  title.append(miniTip("原文引用已经定位并保存哈希；事实真值与来源独立性仍待审核。", "info", "引用校验范围"));
  const references = bundle.futureEvidence || [];
  panel.append(title, el("p", "已保存来源 " + bundle.sources.length + " · 已定位引文 " + references.length, "muted"));
  if (!references.length) panel.append(el("p", "当前运行尚无通过原文定位校验的引文。", "muted"));
  for (const ref of references) {
    const block = el("article", undefined, "candidate");
    block.append(el("h3", ref.evidence_id), el("blockquote", ref.quote));
    if (/^https:\/\//.test(ref.source_uri || "")) {
      const link = el("a", ref.source_uri); link.href = ref.source_uri;
      link.target = "_blank"; link.rel = "noopener noreferrer"; block.append(link);
    }
    const detail = el("details");
    detail.append(el("summary", "来源、定位与哈希"), json({
      sourceArtifactId:ref.source_artifact_id, sourceArtifactHash:ref.source_artifact_hash,
      contentHash:ref.content_hash, locator:ref.locator_json,
      startOffset:ref.start_offset, endOffset:ref.end_offset,
      publishedAt:ref.published_at || "未知", truthStatus:ref.truth_status, reviewStatus:"pending"
    }));
    block.append(detail); panel.append(block);
  }
  for (const source of bundle.sources) {
    const detail = el("details");
    detail.append(el("summary", source.metadata?.title || source.sourceUri));
    const body = el("pre", source.markdown, "review-source-text");
    body.style.whiteSpace = "pre-wrap"; body.style.maxHeight = "28rem"; body.style.overflow = "auto";
    detail.append(body); panel.append(detail);
  }
}
const futureReportSnapshots = new WeakMap();
async function renderFutureReport(panel, out, runId, reviewSummary, reviewSummaryError) {
  const readPrefix="/report-review/runs/"+encodeURIComponent(runId);
  let explorerGraph=null,explorerError=null;
  try{explorerGraph=await api(readPrefix+"/graph",{signal:AbortSignal.timeout(10000)});}catch(error){explorerError=error.message;}
  const ontology = out.ontology;
  const evidence = new Map((out.evidence || []).map(item => [String(item.evidenceId || item.claimId), item]));
  const perspectives = new Map((out.perspectives || []).map(item => [item.key, item]));
  const nodeMap = new Map((ontology?.nodes || []).map(item => [item.id, item]));
  const relations = ontology?.assertions || [];
  const root = el("div", undefined, "future-report"); root.dataset.schema = ontology?.schemaVersion || "legacy";
  root.setAttribute("role", "region"); root.setAttribute("aria-label", "研判报告");
  root.dataset.runId = runId || ""; futureReportSnapshots.set(root,JSON.stringify(out));
  window.AgentAdminReviewSummary.mountResult(root,reviewSummary,{error:reviewSummaryError,tip:miniTip});
  const opinionIdentity=reviewSummary?.identity;
  const objectOpinions=(destination,ref,label)=>{
    if(!opinionIdentity?.reportHash){destination.append(el("p","未取得当前审阅版本，暂不可读取或提交意见。","review-summary-unavailable"));return;}
    window.AgentAdminReviewSummary.mountOpinions(destination,{runId,identity:opinionIdentity,objectKind:ref.kind,objectId:ref.id,objectLabel:label,api,actorId:state.actor?.principal_id,tip:miniTip,onSaved:saved=>{if(saved?.versionCurrent===false)notice("意见已记录在旧版报告；原草稿保留，未移到新版本。请核对当前报告。");if(state.runId===runId)refreshRun({poll:true}).catch(error=>notice(error.message,true));}});
  };
  objectOpinions(root,{kind:"report",id:"report"},"整份报告");
  const knownCount = value => Number.isFinite(value) ? String(value) : "未记录";
  const reportMeta = el("span", "整个任务 · 来源组 " + knownCount(reviewSummary?.totalCounts?.sourceGroups) + " · 摘录 " + knownCount(reviewSummary?.totalCounts?.evidenceExcerpts), "report-meta");
  const findings = (out.findings || []).filter(item => item.status === "candidate");
  const missing = (out.findings || []).filter(item => item.status === "insufficient-evidence");
  const gapReasons = {"web-search-no-results":"检索未返回可用来源", "web-acquisition-failed":"来源正文采集失败", "web-scrape-no-content":"未取得可读正文", "acquisition-budget-exhausted":"采集预算已用尽", "evidence-support-rejected":"引文不足以支撑判断"};
  const gaps = Array.isArray(out.evidenceGaps) ? [...out.evidenceGaps] : missing.map(item => {
    const diagnostic = (out.capabilityGaps || []).find(g => g.perspectiveKey === item.perspectiveKey && gapReasons[g.reason]);
    return {perspectiveKey:item.perspectiveKey, perspectiveName:perspectives.get(item.perspectiveKey)?.name || item.perspectiveKey,
      question:perspectives.get(item.perspectiveKey)?.question, summary:gapReasons[diagnostic?.reason] || "尚无直接相关的可定位引文",
      neededEvidence:"补充直接回答该视角问题的公开原文，并复核引文与判断的对应关系。", errorType:diagnostic?.errorType};
  });
  for(const saved of reviewSummary?.items||[]){if(saved.kind==="gap"&&!gaps.some(gap=>gap.id===saved.id||(gap.perspectiveKey&&gap.perspectiveKey===saved.perspectiveKey)))gaps.push({id:saved.id,perspectiveKey:saved.perspectiveKey,perspectiveName:saved.title,summary:saved.text,reason:saved.reason,neededEvidence:saved.neededEvidence,nextAction:saved.nextAction});}
  const showSnapshot=async(ref,holder,button)=>{
    button.disabled=true;
    try{
      if(!explorerGraph?.contentHash)throw Error("当前报告投影未取得，请重新打开报告后读取存档。");
      const excerpt=await api(readPrefix+"/source",{method:"POST",body:JSON.stringify({evidenceId:String(ref.evidenceId||ref.claimId),projectionHash:explorerGraph.contentHash})});
      holder.replaceChildren();
      const body=el("pre",undefined,"review-source-snapshot");
      const characters=Array.from(excerpt.text||""),start=excerpt.highlight?.start,end=excerpt.highlight?.end;
      if(Number.isInteger(start)&&Number.isInteger(end)&&start>=0&&end>start&&end<=characters.length){
        body.append(document.createTextNode(characters.slice(0,start).join("")),el("mark",characters.slice(start,end).join("")),document.createTextNode(characters.slice(end).join("")));
      }else body.textContent=excerpt.text||"未保存原文窗口";
      holder.append(el("p",(excerpt.title||"已保存原文")+" · 已定位摘录；事实尚未核验","muted"),body);
      body.querySelector("mark")?.scrollIntoView({block:"nearest",behavior:"auto"});
      button.textContent="重新读取原文存档";
    }catch(error){holder.replaceChildren(el("p","原文存档未取得："+error.message,"review-summary-unavailable"));}
    finally{button.disabled=false;}
  };
  const sourceBlock = (ids, destination, {library=false}={}) => {
    const refs = [...new Set(ids)].map(id => evidence.get(String(id))).filter(Boolean),requested=new Set(refs.map(ref=>String(ref.evidenceId||ref.claimId)));
    const groups=(explorerGraph?.sourceCoverage?.sourceGroups||[]).map(group=>({...group,refs:(group.evidenceIds||[]).filter(id=>requested.has(String(id))).map(id=>evidence.get(String(id))).filter(Boolean)})).filter(group=>group.refs.length);
    const assigned=new Set(groups.flatMap(group=>group.refs.map(ref=>String(ref.evidenceId||ref.claimId))));
    for(const ref of refs){const id=String(ref.evidenceId||ref.claimId);if(!assigned.has(id))groups.push({id:"excerpt:"+id,refs:[ref],sourceUris:[ref.sourceUri].filter(Boolean)});}
    for(const group of groups){
      const source=el("details",undefined,"report-source-group");source.dataset.sourceGroupId=group.id;
      source.open=!library&&groups.length===1&&group.refs.length<=3;
      const title=el("summary",undefined,"report-source-group-title");
      title.append(el("span",group.sourceUris?.[0]||"来源未命名"),el("span",group.refs.length+" 条摘录 · 独立性未建立","muted"));
      source.append(title);
      for (const ref of group.refs) {
      const block = el("article", undefined, "report-quote");
      block.dataset.evidenceId=String(ref.evidenceId||ref.claimId);
      block.append(el("blockquote", ref.quote || ""));
      const snapshot=el("button","查看原文存档","report-source-open");snapshot.type="button";
      const snapshotHolder=el("div",undefined,"report-source-window");snapshot.onclick=()=>showSnapshot(ref,snapshotHolder,snapshot);
      block.append(snapshot,snapshotHolder);
      if (/^https:\/\//.test(ref.sourceUri || "")) {
        const link = el("a", "打开原始来源"); link.href = ref.sourceUri; link.target = "_blank"; link.rel = "noopener noreferrer"; block.append(link);
      }
      block.append(el("p", "发表时间：" + (ref.publishedAt || "未知") + " · 引用 " + (ref.evidenceId || ref.claimId), "muted"));
      const detail = el("details"); detail.append(el("summary", "定位与存档"), json({locator:ref.locator, sourceArtifactId:ref.sourceArtifactId,
        sourceArtifactHash:ref.sourceArtifactHash, contentHash:ref.contentHash})); block.append(detail); source.append(block);
      objectOpinions(block,{kind:"evidence",id:String(ref.evidenceId||ref.claimId)},"引文 "+String(ref.evidenceId||ref.claimId));
      }
      destination.append(source);
    }
    if (!refs.length) destination.append(el("p", "尚无绑定的可定位引文。", "muted"));
  };
  const graph = window.AgentAdminReportGraph.mount(root, {report:out, sourceBlock,onReviewObject:(destination,ref,label)=>{
    const belongs=ref.kind==="edge"?explorerGraph?.edges?.some(item=>item.id===ref.id):explorerGraph?.nodes?.some(item=>item.id===ref.id);
    if(belongs)objectOpinions(destination,ref,label);
  }});
  root.addEventListener("report-graph-selection",event=>{
    const selected=event.detail||{};
    for(const row of root.querySelectorAll(".review-result-item"))row.classList.toggle("is-selected",selected.kind==="assertion"&&row.dataset.itemId===selected.id);
  });
  root.addEventListener("report-item-select",event=>{
    const {item,focusEvidenceId}=event.detail||{};if(!item)return;
    for(const row of root.querySelectorAll(".review-result-item"))row.classList.toggle("is-selected",row.dataset.itemId===item.id);
    const located=window.AgentAdminReportGraph.selectItem(root,item);
    if(!located&&item.kind==="gap"){
      const gap=[...root.querySelectorAll(".report-gap")].find(row=>row.dataset.itemId===item.id||row.dataset.perspectiveKey===item.perspectiveKey);
      if(gap){gap.open=true;gap.scrollIntoView({block:"nearest",behavior:"auto"});gap.querySelector("summary")?.focus({preventScroll:true});}
    }
    if(focusEvidenceId){
      const quote=[...root.querySelectorAll(".report-quote")].find(row=>row.dataset.evidenceId===String(focusEvidenceId));
      if(quote){for(let ancestor=quote.parentElement;ancestor&&ancestor!==root;ancestor=ancestor.parentElement)if(ancestor.tagName==="DETAILS")ancestor.open=true;quote.scrollIntoView({block:"nearest",behavior:"auto"});quote.querySelector(".report-source-open")?.focus({preventScroll:true});}
    }
  });
  const graphHeading = root.querySelector(".report-graph-heading");
  graphHeading.insertBefore(reportMeta, graphHeading.querySelector(".report-graph-tools"));
  root.classList.toggle("future-report--graph", graph.hasGraph);
  const section = el(graph.hasGraph ? "details" : "section", undefined, "report-findings");
  section.append(el(graph.hasGraph ? "summary" : "h3", graph.hasGraph ? "逐视角文字结论与完整关系" : "候选判断与本体关系"));
  if (!ontology && findings.length) section.append(el("p", "本次历史报告尚未生成本体关系，引用相关性也尚未按新规则复核。", "muted"));
  if (!findings.length) section.append(el("p", "本次未形成证据足够的候选判断，请查看证据缺口。", "muted"));
  for (const finding of findings) {
    const card = el("article", undefined, "ontology-perspective"); card.dataset.perspectiveKey = finding.perspectiveKey;
    const heading = el("div", undefined, "ontology-perspective-title");
    heading.append(el("h4", perspectives.get(finding.perspectiveKey)?.name || finding.perspectiveKey));
    heading.append(el("span", finding.supportReview?.verdict === "supported" ? window.AgentAdminReportGraph.epistemicLabel(finding) : finding.status === "insufficient-evidence" ? "尚待补证" : "引用待复核", "muted"));
    card.append(heading, el("p", finding.statement, "judgment-statement"));
    const assertions = relations.filter(item => (finding.findingId ? item.findingId === finding.findingId : item.perspectiveKey === finding.perspectiveKey) && graph.relationIds.has(item.id));
    for (const relation of assertions) {
      const detail = el("details", undefined, "ontology-relation"); detail.dataset.assertionId = relation.id;
      const row = el("summary", undefined, "ontology-triple");
      row.append(el("strong", nodeMap.get(relation.subject)?.label || relation.subject),
        el("span", "→ " + (ontology.predicates[relation.predicate] || relation.predicate) + " →", "ontology-predicate"),
        el("strong", nodeMap.get(relation.object)?.label || relation.object), el("span", "查看依据", "ontology-open"));
      detail.append(row);
      const body = el("div", undefined, "ontology-relation-detail");
      body.append(el("p", "关系性质：" + window.AgentAdminReportGraph.epistemicLabel(relation) + "；事实尚未核验", "muted"));
      const inherited=relation.conditionScope==="perspective"||(!relation.conditionScope&&JSON.stringify(relation.conditions||[])===JSON.stringify(finding.conditions||[]));
      if(!inherited&&relation.conditions?.length) body.append(el("p", "关系适用条件：" + relation.conditions.join("；")));
      if (relation.supportReview?.reason) body.append(el("p", "复核理由：" + relation.supportReview.reason));
      sourceBlock(relation.evidenceIds || [], body); detail.append(body); card.append(detail);
    }
    if (ontology && !assertions.length) card.append(el("p", "实体关系尚无充分依据。", "muted"));
    const detail = el("details", undefined, "ontology-context"); detail.append(el("summary", "适用条件、反方假设与引用"));
    for (const [key,label] of [["conditions","适用条件"],["counterArguments","反方假设"],["unknowns","未知项"]]) {
      if (finding[key]?.length) detail.append(el("p", label + "：" + finding[key].join("；")));
    }
    sourceBlock(finding.supportingClaims || [], detail); card.append(detail); section.append(card);
  }
  root.append(section);
  if (out.taskDefinition?.decisionSpec) {
    const spec = out.taskDefinition.decisionSpec;
    const detail = el('details', undefined, 'ontology-context');
    detail.append(el('summary', '决策问题与验证任务'));
    detail.append(el('p', spec.decision.decisionToSupport));
    for (const q of spec.questions) {
      const coverage = (out.questionCoverage || []).find(item => item.questionId === q.id);
      detail.append(el('p', q.text + ' · ' + (coverage?.status === 'supported-answer' ? '已有回答依据（机器复核，仍有待验证项）' : '尚待补证')));
    }
    for (const task of out.verificationTasks || []) {
      const item = el('details'); item.append(el('summary', task.target));
      item.append(el('p', task.reason), el('p', '责任角色：' + task.ownerRole + '；待安排'));
      item.append(el('p', '所需材料：' + task.requiredMaterial.join('；')), el('p', '完成条件：' + task.completionCriteria.join('；')));
      sourceBlock(task.evidenceIds, item); detail.append(item);
    }
    const assumptions = el('details'); assumptions.append(el('summary', '任务规范中的系统默认项'));
    for (const item of spec.assumptions) assumptions.append(el('p', (Array.isArray(item.value) ? item.value.join('；') : item.value)));
    detail.append(assumptions); root.append(detail);
  }
  const gapSection = el("section", undefined, "ontology-gap-summary");
  gapSection.append(el("h3", "证据缺口" + (gaps.length ? "（" + gaps.length + "）" : "")));
  if (!gaps.length) gapSection.append(el("p", "未记录未覆盖视角。来源独立性与其他研究限制见报告原始记录。", "muted"));
  for (const gap of gaps) {
    const item = el("details", undefined, "report-gap"); item.dataset.perspectiveKey = gap.perspectiveKey;
    const summaryItem=(reviewSummary?.items||[]).find(row=>row.kind==="gap"&&(row.perspectiveKey===gap.perspectiveKey||row.id===gap.id));
    item.dataset.itemId=summaryItem?.id||gap.id||"";
    const summary = el("summary", undefined, "gap-row");
    summary.append(el("strong", gap.perspectiveName || perspectives.get(gap.perspectiveKey)?.name || gap.perspectiveKey), el("span", gap.summary)); item.append(summary);
    if (gap.question) item.append(el("p", "研究问题：" + gap.question));
    if (gap.supportReview?.reason) item.append(el("p", "复核理由：" + gap.supportReview.reason));
    if (gap.attemptedQueries?.length) item.append(el("p", "已尝试检索：" + gap.attemptedQueries.join("；")));
    if (gap.attemptedUrls?.length) item.append(el("p", "已尝试来源：" + gap.attemptedUrls.join("；")));
    if (gap.errorType) item.append(el("p", "采集结果：" + gap.errorType, "muted"));
    if (gap.neededEvidence) item.append(el("p", "需要补充：" + gap.neededEvidence));
    if (gap.nextAction) item.append(el("p", "后续动作：" + gap.nextAction));
    const attempts=el("div",undefined,"report-gap-attempts");item.append(attempts);
    if(summaryItem?.objectRef)objectOpinions(item,summaryItem.objectRef,gap.perspectiveName||gap.summary||"证据缺口");
    let detailLoaded=false;
    item.addEventListener("toggle",async()=>{
      if(!item.open||detailLoaded)return;
      const ref=summaryItem?.objectRef;
      if(!ref||!explorerGraph?.contentHash){attempts.replaceChildren(el("p","检索尝试、排除理由与排除规则的详情未取得；当前未建立可读取的缺口对象引用。","review-summary-unavailable"));return;}
      detailLoaded=true;attempts.replaceChildren(el("p","读取已保存的缺口记录…","muted"));
      try{
        const selected=await api(readPrefix+"/selection",{method:"POST",body:JSON.stringify({...ref,projectionHash:explorerGraph.contentHash})});
        window.AgentAdminReviewSummary.mountGapDetail(attempts,selected.gapDetail);
      }catch(error){detailLoaded=false;attempts.replaceChildren(el("p","缺口详情未取得："+error.message,"review-summary-unavailable"));}
    });
    gapSection.append(item);
  }
  root.append(gapSection);
  if(out.professionalAnalysis){
    const methods=el("details",undefined,"report-methods");
    const summary=out.analysisSummary||{};
    methods.append(el("summary","分析方法 · 已评估 "+(summary.assessed||0)+" · 条件受限 "+(summary.limited||0)+" · 不适用 "+(summary.notApplicable||0)));
    const names={"analysis.source_dependencies":"来源依赖","analysis.entity_candidates":"实体候选","analysis.series_build":"观测序列","analysis.change_point":"CUSUM 变化监测","analysis.claim_relation":"主张关系","analysis.evidence_rank":"本地检索排序","analysis.evidence_coverage":"问题覆盖","analysis.ontology_validate":"本体约束","analysis.hypothesis_compare":"竞争假设","analysis.scenario_consistency":"情景一致性","analysis.robustness_check":"稳健性","analysis.maturity_assess":"成熟度证据","analysis.method_evaluate":"独立评测"};
    const statuses={assessed:"已评估",partial:"条件受限","not-applicable":"不适用","invalid-input":"输入无效"};
    const reasons={"comparable-observation-series":"没有带测量时间、单位和可比条件的观测序列","documented-cusum-parameters":"缺少有出处的 CUSUM 基线与阈值参数","atomic-claim-fields":"原子主张字段不足","uncovered-questions":"部分研究问题尚无通过支持复核的结论","uncovered-formal-targets":"部分本体约束没有可校验对象","versioned-authoritative-standard":"尚未采用有版本及原文存档的成熟度标准","criterion-evidence-and-validation-environment":"缺少逐项证据和验证环境","at-least-20-independent-labelled-holdout-clusters":"缺少足够的独立专家保留样本","semantic-reevaluation-not-performed":"仅检查引文覆盖敏感性，未重做语义判断","evidence-linked-hypothesis-assessments":"没有通过逐项复核的假设—证据评估","evidence-bound-cross-consistency-constraints":"没有证据支持的跨维度一致性约束","constraint-coverage-incomplete":"仅检查已有约束，尚未覆盖所有组合","independence-not-established":"来源与模型审查的独立性尚未建立"};
    for(const [key,value] of Object.entries(out.professionalAnalysis)){
      const row=el("details");row.append(el("summary",(names[key]||key)+" · "+(statuses[value.analysisStatus]||value.analysisStatus)+" · 样本 "+value.sampleCount));
      if(value.reasonCodes?.length)row.append(el("p",value.reasonCodes.map(code=>reasons[code]||code).join("；"),"muted"));
      if(key==="analysis.hypothesis_compare"){
        for(const h of value.hypotheses||[])row.append(el("p",h.id+" · "+h.statement));
        const matrix=el("table");const heading=el("tr");for(const text of ["假设","证据","关系","理由"])heading.append(el("th",text));matrix.append(heading);
        const relations={consistent:"相容",inconsistent:"不相容",neutral:"中性",unknown:"未知"};
        for(const a of value.matrix||[]){const line=el("tr");for(const text of [a.hypothesisId,a.evidenceId,relations[a.relation]||a.relation,a.rationale])line.append(el("td",text));matrix.append(line);}row.append(matrix);
      }
      if(key==="analysis.evidence_coverage")for(const q of value.matrix||[])row.append(el("p",(perspectives.get(q.questionKey)?.name||q.questionKey)+" · "+({supported:"已有支持",'not-supported':"材料尚未支持",'read-failed':"原文读取失败",'no-retrieval-hit':"未命中来源",'not-searched':"尚未检索",'budget-exhausted':"采集预算已用尽",unknown:"待核对"}[q.status]||q.status)));
      if(value.validation?.shacl)row.append(el("p","本体约束：已覆盖 "+value.validation.shacl.coveredShapeCount+" · 未覆盖 "+value.validation.shacl.uncoveredShapeCount+" · 违规 "+value.validation.shacl.violations.length,"muted"));
      const audit=el("details");audit.append(el("summary","参数、依据与调用记录"),json(value));
      row.append(el("p","方法版本："+value.methodVersion+"；调用完成不代表专业准确性已被验证。","muted"),audit);methods.append(row);
    }
    root.append(methods);
  }
  if(evidence.size){const library=el("details",undefined,"report-source-library");library.append(el("summary","任务已有来源与摘录（"+evidence.size+"）"));library.append(el("p","这些是本次任务已保存的材料；不自动构成当前缺口的支撑证据。","muted"));sourceBlock([...evidence.keys()],library,{library:true});root.append(library);}
  if(explorerError)root.append(el("p","共享来源/缺口投影读取失败："+explorerError,"review-summary-unavailable"));
  const raw = el("details", undefined, "report-raw"); raw.append(el("summary", "完整报告与审计数据"), json(out)); root.append(raw);
  window.AgentAdminReasoning?.mountReport(root, {report:out, sourceBlock, runId, api});
  panel.append(root);
}
async function renderContent(panel,view){
  if(view.tab==="connectors"){await window.AgentAdminConnectors.render(panel,{api,notice,canEdit:hasRole("admin"),initialRef:new URLSearchParams(location.hash.slice(1)).get("connector")||""});return;}
  if(view.tab==="graph"&&view.runId){await window.AgentAdminGraph.render(panel,{runId:view.runId,api,notice,canReview:hasRole("reviewer"),onTab:tab=>{if(state.runId!==view.runId)return;state.tab=tab;runTabChoices.set(state.runId,tab);state.initialRunTabPending=false;render().catch(e=>notice(e.message,true));}});return;}
  if(view.tab==="operations"){await renderOperations(panel);return;} if(view.tab==="knowledge"){await renderKnowledge(panel,view.runId);return;} if(view.tab==="engineering"){await renderEngineering(panel,view.runId);return;} if(view.tab==="traceability"){await renderTraceability(panel,view.runId);return;} if(view.tab==="evaluation"){await renderEvaluations(panel,view.runId);return;} if(view.tab==="workspaces"){await renderWorkspaces(panel);return;} if(view.tab==="identities"){await renderIdentities(panel);return;} if(view.tab==="config"){await renderConfig(panel);return;}
  if(!view.runId){panel.append(el("p","选择运行以查看真实数据。","muted"));return;}
  const prefix="/runs/"+encodeURIComponent(view.runId);
  if(view.tab==="report"){
    let out=view.run?.output_json;
    if(!out&&["future-battlefield-analyst","future-battlefield-codex"].includes(view.run?.agent_key)){const artifacts=await api(prefix+"/artifacts");out=artifacts.filter(a=>a.artifact_type==="future-candidate-report").at(-1)?.content_json;}
    if(!out){panel.append(el("h2","运行报告"),el("p","报告尚未生成。可查看计划和事件。","muted"));return;}
    if(typeof out === "string"){try{out=JSON.parse(out);}catch{}}
    if(window.AgentAdminCognitive?.isReport(out)){window.AgentAdminCognitive.mountReport(panel,{report:out,runId:view.runId,api,notice});return;}
    if(["future-battlefield-analyst","future-battlefield-codex"].includes(view.run?.agent_key) || out.ontology) await renderFutureReport(panel,out,view.runId,view.reviewSummary,view.reviewSummaryError);
    else {panel.append(el("h2","运行报告"),el("p","候选报告：来源独立性尚未确认；不同记录或域名不能直接算作独立佐证。","help-note help-caution"));panel.append(el("h3",out.summary||out.title||"研究结果"));if(Array.isArray(out.findings)){for(const finding of out.findings)panel.append(el("p",typeof finding==="string"?finding:finding.statement||JSON.stringify(finding)));}const details=el("details");details.append(el("summary","查看完整结构化报告"),json(out));panel.append(details);}
    if(out.stopReason)panel.append(el("p","停止原因："+out.stopReason,"muted"));
    const artifacts=await api(prefix+"/artifacts");
    // Runtime stop/gap summaries are appended after the immutable report is
    // saved. Compare every other field and verify that exact saved version.
    const canonical=value=>JSON.stringify(value,(key,v)=>["stopReason","unresolvedQuestions","capabilityGaps"].includes(key)?undefined:v&&typeof v==="object"&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
    const savedReport=artifacts.filter(a=>a.artifact_type?.endsWith("-report")&&canonical(a.content_json)===canonical(out)).at(-1);
    const reportHeading=panel.querySelector(".report-graph-tools")||panel.querySelector(".report-review-result");
    const verifiedReportRoot=panel.querySelector(".future-report");
    if(savedReport&&reportHeading)window.AgentAdminVerification?.mount(reportHeading,{
      subject:{kind:"report-evidence",run_id:view.runId,artifact_id:savedReport.artifact_id},api,label:"证据核验",
      onReference:evidenceId=>{
        const report=verifiedReportRoot;
        if(report)report.dispatchEvent(new CustomEvent("report-item-select",{detail:{item:{id:evidenceId},focusEvidenceId:evidenceId}}));
      }});
    const originalArtifacts=artifacts.filter(a=>a.artifact_type==="agent-output");
    if(originalArtifacts.length){
      const technical=el("details",undefined,"report-original-artifacts");technical.append(el("summary","原始运行产物 · 技术详情"));
      for(const a of originalArtifacts){
        technical.append(el("p","产物 ID："+a.artifact_id,"muted"),el("p","原始产物哈希："+a.content_hash,"muted"));
        if(hasRole("reviewer")&&["waiting-human","requires-human-review"].includes(view.run?.status)){
          technical.append(el("p","当前待处理项：此原始产物的既有审核；不会批准整份报告或发布本体关系。","report-legacy-review-context"),reviewButtons("artifact",a.artifact_id,a.content_hash,view.runId));
        }
      }
      panel.append(technical);
    }
    return;
  }
  if(view.tab==="plans"){const rows=await api(prefix+"/plans");panel.append(el("h2","不可变计划历史"));for(const p of rows){panel.append(el("h3","计划版本 "+p.plan_version),json({plan:p.plan_json,validation:p.validation_json}));}return;}
  if(view.tab==="budget"){const [budget,calls,toolcalls,approvals]=await Promise.all([api(prefix+"/budget"),api(prefix+"/model-calls"),api(prefix+"/tool-calls"),api(prefix+"/approvals")]);panel.append(el("h2","共享预算与实际模型调用"),json(budget));const table=el("table"),header=el("tr");for(const title of ["阶段","请求模型","实际模型","状态","输入 / 输出 Token","毫秒"])header.append(el("th",title));table.append(header);for(const c of calls){const row=el("tr");for(const v of [c.stage,c.requested_model,c.actual_model||"unknown",c.status,(c.input_tokens??"unknown")+" / "+(c.output_tokens??"unknown"),c.latency_ms])row.append(el("td",v));table.append(row);}panel.append(table,el("h3","实际工具调用"),json(toolcalls),el("h3","工具审批"));for(const approval of approvals){panel.append(json(approval));if(hasRole("reviewer")&&approval.status==="pending"){for(const approved of[true,false]){const decide=el("button",approved?"允许本步工具":"拒绝本步工具");decide.onclick=async()=>{try{await api(prefix+"/approvals/"+encodeURIComponent(approval.approval_id),{method:"POST",body:JSON.stringify({approved})});await refreshRun();}catch(error){notice(error.message,true);}};panel.append(decide);}}}return;}
  if(view.tab==="events"){const data=await api(prefix+"/events?after="+view.cursor+"&limit=300");view.events=[...new Map([...view.events,...data.events].map(event=>[event.event_id,event])).values()];view.cursor=data.nextCursor;panel.append(el("h2","运行事件"));for(const event of view.events.slice(-300)){panel.append(el("p",event.event_id+" · "+event.event_type),json(event.detail_json));}return;}
  if(view.tab==="evidence"){if(["future-battlefield-analyst","future-battlefield-codex"].includes(view.run?.agent_key)){await renderFutureEvidence(panel,view,prefix);return;}if(hasRole("reviewer")&&window.AgentReviewWorkbench){const holder=el("div");panel.append(holder);await window.AgentReviewWorkbench.mount(holder,{runId:view.runId,api,notice,onReviewSubject:subject=>{holder.append(reviewButtons(subject.subjectType,subject.subjectId,subject.expectedHash,subject.runId));}});return;}const bundle=await api(prefix+"/evidence");panel.append(el("h2","候选与原文引用"),el("p","人审接受表示本次候选与引文审核通过；独立事实核验状态另行保留。","help-note help-caution"));for(const c of bundle.candidates){const block=el("div",undefined,"candidate");block.append(el("h3",c.entityId+" · "+c.metric),el("p",JSON.stringify(c.value??c.range)+" "+(c.unit||"")+" · "+(bundle.trustedReviewStates[c.candidateId]?.reviewStatus||"pending")),el("blockquote",c.quote),el("p","候选指纹："+c.fingerprint,"muted"));if(c.sourceUri&&/^https:\/\//.test(c.sourceUri)){const link=el("a","原始来源");link.href=c.sourceUri;link.target="_blank";link.rel="noopener noreferrer";block.append(link);}if(hasRole("reviewer"))block.append(reviewButtons("candidate",c.candidateId,c.fingerprint,view.runId));panel.append(block);}panel.append(el("h3","来源分组（独立性未确认）"),json(bundle.sourceGroups||[]),el("h3","冲突与审核记录"),json({conflicts:bundle.conflicts,reviews:bundle.reviews}));}
}
function reviewButtons(subjectType,subjectId,expectedHash,runId=state.runId){
  const group=el("div",undefined,"review-actions");
  const object=subjectType==="artifact"?"本产物":"本候选";
  const decisions=[["accept","接受"+object],["reject","拒绝"+object],["request-evidence","要求"+object+"补证"]];
  if(subjectType==="candidate")decisions.push(["resolve-conflict","记录本候选冲突处理"]);
  for(const [decision,label]of decisions){const button=el("button",label);button.onclick=async()=>{const reason=window.prompt("填写"+object+"的审核理由；仅记录当前对象，不核验事实、不发布、不启动补证运行。");if(!reason)return;let conflictId;if(decision==="resolve-conflict"){conflictId=window.prompt("冲突 ID");if(!conflictId)return;}try{await api("/runs/"+encodeURIComponent(runId)+"/reviews",{method:"POST",body:JSON.stringify({subjectType,subjectId,expectedHash,decision,reason,...(conflictId?{conflictId}:{})})});notice(object+"审核记录已保存");await refreshRun();}catch(error){notice(error.message,true);}};group.append(button);}
  return group;
}
async function renderConfig(panel){if(hasRole("admin"))await renderProfileConfig(panel);panel.append(el("h2","配置管理"),el("p","配置变更只用于新运行。凭据仅保存服务端环境变量引用。", "help-note help-info"));for(const resource of["agents","models","tools","providers","sources","scope","resources","versions","bindings"]){const rows=await api("/config/"+resource);panel.append(el("h3",resource));if(hasRole("admin")){const create=el("button","新增 "+resource);create.onclick=async()=>{const key=window.prompt(resource==="versions"?"工具Key~新版本":resource==="bindings"?"AgentKey~工具Key":"配置Key");if(!key)return;const entered=window.prompt("输入新增配置JSON（仅凭据引用）","{}");if(!entered)return;try{await api("/config/"+resource+"/"+encodeURIComponent(key),{method:"POST",body:JSON.stringify(JSON.parse(entered))});notice("新配置已保存");await render();}catch(error){notice(error.message,true);}};panel.append(create);}for(const row of rows){const key=resource==="versions"?row.tool_key+"~"+row.version:resource==="bindings"?row.agent_key+"~"+row.tool_key:row.agent_key||row.tool_key||row.provider_key||row.source_key;const block=el("div",undefined,"config-row");block.append(el("strong",key),json(row));if(hasRole("admin")&&resource!=="versions"){const edit=el("button","编辑配置字段");edit.onclick=async()=>{const entered=window.prompt("输入需要更新的 JSON 字段；不要填写 API Key", "{}");if(!entered)return;try{await api("/config/"+resource+"/"+encodeURIComponent(key),{method:"PUT",body:JSON.stringify(JSON.parse(entered))});notice("配置已更新，新运行将使用新配置");await render();}catch(error){notice(error.message,true);}};block.append(edit);if(["agents","tools","providers","sources"].includes(resource)){const disable=el("button","停用");disable.onclick=async()=>{try{await api("/config/"+resource+"/"+encodeURIComponent(key),{method:"DELETE"});await render();}catch(error){notice(error.message,true);}};block.append(disable);}}panel.append(block);}}}
// Loopback access is authenticated by the server middleware; the browser never
// asks the operator to paste or store an access token.
if($("login-form")) $("login-form").onsubmit=event=>{event.preventDefault();notice("本机工作台使用免令牌访问。",false);};
$("signout").onclick=async()=>{await api("/session",{method:"DELETE"});state.actor=null;location.reload();};
$("create-form").onsubmit=async event=>{
  event.preventDefault();
  if($("create-form").dataset.submitting==="true")return;
  let submitting=false;
  try{
    const target=window.AgentAdminTargets.getInput();
    $("create-form").dataset.submitting="true";submitting=true;
    window.AgentAdminTargets.setSubmitting(true);
    const purpose={overview:"能力概览",requirements:"需求符合性",evidence:"证据补全", "future-battlefield-judgment":"未来战场形态研判", "conceptual-design":"概念设计与制造验证", "evidence-review":"证据链独立审查", "action-task":"上游材料处理"}[target.analysisPurpose] || target.analysisPurpose || "按 Agent Profile 执行";
    const question=[$("question").value,"分析目的："+purpose,target.analysisContext?.configuration&&"构型/工况："+target.analysisContext.configuration,
      target.analysisContext?.requirementText&&"需求："+target.analysisContext.requirementText,
      target.agentSpecificContext].filter(Boolean).join("\n");
    const agentKey = $("agent").value;
    const selectedAgent = state.agents.find(item=>item.agent_key===agentKey) || {};
    const fixedProfileRun = ["future-battlefield-analyst","future-battlefield-codex"].includes(agentKey);
    const executionMode = selectedAgent.agent_layer === "action" ? "fixed" : (fixedProfileRun ? "profile-run" : "research");
    const automationMode = fixedProfileRun ? "unattended-draft" : undefined;
    const collaborationEnabled = $("collaborate")?.checked === true;
    const designEnabled = $("include-design")?.checked === true;
    const commonSettings={schemaVersion:"1.0",agentKey,agentVersion:selectedAgent.current_version || null,
      taskGoal:$('question').value.trim(),executionMode,budgetLimits:{model_calls:Number($("model-budget").value),search_calls:Number($("search-budget").value),scrape_calls:Number($("scrape-budget").value)}};
    const result=await api("/runs",{method:"POST",body:JSON.stringify({agentKey,agentVersion:selectedAgent.current_version || undefined,input:{...target,commonSettings,question,...(automationMode?{automationMode}:{}),
      budgetLimits:{model_calls:Number($("model-budget").value),search_calls:Number($("search-budget").value),scrape_calls:Number($("scrape-budget").value)},
      executionMode,collaborate:fixedProfileRun||selectedAgent.agent_layer==="action"?false:collaborationEnabled,includeDesign:fixedProfileRun||selectedAgent.agent_layer==="action"?false:collaborationEnabled&&designEnabled}})});
    notice("已入队");await selectRun(result.runId);
  }catch(error){
    notice(error.message,true);
    if(error.message.includes("重新预检"))window.AgentAdminTargets.refresh().catch(e=>notice(e.message,true));
  }finally{if(submitting){delete $("create-form").dataset.submitting;window.AgentAdminTargets.setSubmitting(false);}}
};
$("refresh").onclick=()=>loadRuns().then(refreshRun).catch(e=>notice(e.message,true));document.querySelectorAll("[data-tab]").forEach(button=>button.onclick=()=>{state.tab=button.dataset.tab;if(state.area==="settings")history.replaceState(null,"","#area=settings&tab="+encodeURIComponent(state.tab));if(state.area==="runs"&&state.runId){runTabChoices.set(state.runId,state.tab);state.initialRunTabPending=false;}render().catch(e=>notice(e.message,true));});document.querySelectorAll("[data-area]").forEach(button=>button.onclick=()=>navigateAgentAdminArea(button.dataset.area));document.querySelectorAll("[data-control]").forEach(button=>button.onclick=async()=>{try{await api("/runs/"+encodeURIComponent(state.runId)+"/"+button.dataset.control,{method:"POST"});await refreshRun();}catch(error){notice(error.message,true);}});
function navigateAgentAdminArea(area) {
  if (area === "settings" && !hasRole("admin")) { notice("管理设置需要 admin 权限", true); return; }
  const hash = area === "runs" && state.runId ? "#run=" + encodeURIComponent(state.runId)
    : area === "runs" && state.agentKey ? "#area=runs&agentKey=" + encodeURIComponent(state.agentKey)
    : area === "agents" && state.agentKey ? "#agent=" + encodeURIComponent(state.agentKey)
    : area === "task" && state.agentKey ? "#task=" + encodeURIComponent(state.agentKey)
    : "#area=" + encodeURIComponent(area);
  if (location.hash !== hash) { location.hash = hash; return; }
  applyAgentAdminRoute().catch(error => notice(error.message, true));
}
window.navigateAgentAdminArea=navigateAgentAdminArea;
async function applyAgentAdminRoute() {
  if (!state.actor) return;
  const route = new URLSearchParams(location.hash.slice(1));
  const runId = route.get("run");
  if (runId) {
    if (runId !== state.runId || !state.run) { await selectRun(runId, {updateHash:false}); return; }
    state.area = "runs";
    await loadRuns();
    await render();
    return;
  }
  const agentKey = route.get("task") || route.get("agent") || route.get("agentKey");
  if (agentKey) setActiveAgent(agentKey);
  state.area = route.has("task") ? "task" : route.has("agent") ? "agents" : route.get("area") || "agents";
  if (state.area === "settings" && !hasRole("admin")) {
    state.area = "agents";
    notice("管理设置需要 admin 权限", true);
  }
  if (state.area === "settings") state.tab = ["config", "connectors", "evaluation", "operations", "identities"].includes(route.get("tab")) ? route.get("tab") : "config";
  if (state.area === "runs") {
    if (["config", "connectors", "evaluation", "operations", "identities"].includes(state.tab)) state.tab = "graph";
    if (!route.has("agentKey") && state.agentKey) {
      history.replaceState(null, "", "#area=runs&agentKey=" + encodeURIComponent(state.agentKey));
    }
    const rows=await loadRuns();
    if(!state.runId&&await restoreAgentRun(rows,state.agentKey))return;
  }
  await render();
}
window.addEventListener("hashchange", () => applyAgentAdminRoute().catch(error => notice(error.message, true)));
setInterval(()=>{if(state.actor&&state.area==="runs"&&state.runId&&!["config","connectors","identities","workspaces","evidence","knowledge","engineering","traceability","evaluation","operations"].includes(state.tab))refreshRun({poll:true}).catch(e=>notice(e.message,true));},5000);login();








function renderFollowups(runId,run,links){
  const section=$("followup-section");
  section.dataset.available=String(hasRole("operator")&&["completed","completed-with-gaps","requires-human-review","blocked","failed","cancelled"].includes(run.status));
  section.hidden=section.dataset.available!=="true";
  let holder=$("followup-links");
  if(!holder){holder=el("p");holder.id="followup-links";section.append(holder);}
  const signature=JSON.stringify([runId,links.map(item=>[item.run_id,item.previous_run_id])]);
  if(holder.dataset.signature===signature)return;
  holder.replaceChildren();
  for(const item of links){const link=el("a",item.run_id===runId?"查看前次任务":"查看后续任务");link.href="/agent-admin.html#run="+encodeURIComponent(item.run_id===runId?item.previous_run_id:item.run_id);link.style.marginRight="14px";holder.append(link);}
  holder.dataset.signature=signature;
}
$("followup-form").onsubmit=async event=>{
  event.preventDefault();const form=event.currentTarget;const runId=state.runId;const text=$("followup-material").value;
  const button=form.querySelector("button");button.disabled=true;
  // Keep this key for an uncertain network retry; a click must not duplicate a paid task.
  const key=(form.dataset.runId===runId&&form.dataset.operationKey)||("followup-"+crypto.randomUUID());form.dataset.operationKey=key;form.dataset.runId=runId;
  try{const result=await api("/runs/"+encodeURIComponent(runId)+"/followups",{method:"POST",body:JSON.stringify({idempotencyKey:key,reason:"User supplied additional research material",input:{supplementalMaterial:text}})});delete form.dataset.operationKey;$("followup-material").value="";notice("后续任务已入队，结论将重新审核");await selectRun(result.runId);}catch(error){notice(error.message,true);}finally{button.disabled=false;}
};


/* User-first navigation for Agent Admin. It composes existing APIs and run views. */
"use strict";
(function () {
  const text = value => value === undefined || value === null || value === "" ? "—" : String(value);
  const copy = value => value === undefined || value === null ? "—" : typeof value === "string" ? value : JSON.stringify(value);
  const make = (tag, value, className) => el(tag, value, className);
  const panelIcon = title => {
    const key = String(title);
    if (key.includes("职责") || key.includes("边界") || key.includes("行为准则") || key.includes("Agent 定义")) return "imagine-icon";
    if (key.includes("能力图谱")) return "data-model-icon";
    if (key.includes("计划")) return "plan-icon";
    if (key.includes("工具")) return "tools-icon";
    if (key.includes("Loop") || key.includes("收敛")) return "loop-icon";
    if (key.includes("模型") || key.includes("数据源")) return "data-model-icon";
    if (key.includes("生命周期") || key.includes("执行") || key.includes("运行")) return "execute-icon";
    return "scope-icon";
  };
  const panelHeading = (title, description = "") => { const heading = make("h3", undefined, "panel-heading-with-icon"); const icon = make("span", undefined, "future-war-stage-icon " + panelIcon(title)); icon.setAttribute("aria-hidden", "true"); heading.append(icon, document.createTextNode(title)); if (description) heading.append(miniTip(description, "info", title)); return heading; };
  const card = (title, body, className = "") => {
    const section = make("section", undefined, "profile-card " + className);
    section.append(panelHeading(title));
    if (body instanceof Node) section.append(body); else section.append(make("p", body));
    return section;
  };
  const table = (headers, rows, className = "") => {
    const result = make("table", undefined, className);
    const head = make("thead"), headRow = make("tr");
    headers.forEach(header => headRow.append(make("th", header)));
    head.append(headRow);
    const body = make("tbody");
    rows.forEach(values => {
      const row = make("tr");
      values.forEach(value => { const cell = make("td"); value instanceof Node ? cell.append(value) : cell.textContent = text(value); row.append(cell); });
      body.append(row);
    });
    result.append(head, body);
    return result;
  };
  const labelControl = (title, input) => { const label = make("label", title, "audit-filter"); label.append(input); return label; };
  const configObject = value => {
    if (typeof value === "string") { try { value = JSON.parse(value); } catch { return {}; } }
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  };
  const researchStepCopy = {
    "uav-design-engineer": {
      "read-frozen-source": ["核对设计需求与本体基线", "读取任务需求、目标机型与本体保存的来源候选，识别型号、构型、单位和工况；缺失的设计输入保留为待补数据。"],
      "inspect-upstream": ["检查上游产物与设计约束", "作为协作子任务时，核对上游产物哈希并继承其证据与约束；独立任务使用本次输入。来源候选不因交接变成已验证事实。"],
      "generate-deliverables": ["生成概念方案与验证计划", "整理设计简报、约束矩阵、待补数据清单和验证计划。重量与能源数值需有可复算的工程计算产物；当前阶段不代表已完成 CAD、BOM、制造工艺或试验验证。"],
      "validate-deliverables": ["校验交付物与未闭合项", "检查必需交付物、结论与候选证据的绑定及缺失材料。未闭合项进入补证或人工审核，模型不签发正式设计或制造放行。"]
    },
    "uav-evidence-reviewer": {
      "read-frozen-source": ["读取待审对象与来源快照", "读取任务机型、本体候选与保存的原文摘录，保留来源位置、单位、型号和工况；摘录与完整原文的可用范围分别记录。"],
      "inspect-upstream": ["核对上游哈希与证据归属", "作为协作子任务时，验证上游交接产物的哈希并读取其候选与证据；检查材料是否属于当前实体、型号和任务，原有审核不会自动变成新结论的批准。"],
      "generate-deliverables": ["审查引文、冲突与推断", "围绕引文、来源归属、单位、工况、来源独立性和过度推断形成审查包、阻断项与追溯矩阵。计算复核须有可复算输入和产物；缺少资料时明确保留缺口。"],
      "validate-deliverables": ["校验追溯与人工审核边界", "检查必需审查交付物和候选引用，保留冲突及补证项。Agent 审查意见交给人工决定，不代替人审接受、知识发布或工程认证。"]
    }
  };
  const workflowView = (documentValue, model, agentKey) => {
    const fixed = Array.isArray(documentValue.steps) ? documentValue.steps : [];
    if (fixed.length) return {mode:"fixed", steps:fixed};
    const config = configObject(documentValue.models?.config_json ?? model.config_json);
    const research = Array.isArray(documentValue.researchSteps) && documentValue.researchSteps.length
      ? documentValue.researchSteps : config.researchSteps;
    const steps = (Array.isArray(research) ? research : []).filter(step => step && typeof step === "object").map(step => {
      const localized = researchStepCopy[agentKey]?.[step.key];
      return {...step, name:step.name || localized?.[0] || step.key,
        description:step.description || localized?.[1] || step.executor || ""};
    });
    return {mode:"research", steps};
  };
  let processCatalogRequest = null, agentRenderGeneration = 0, taskRenderGeneration = 0;
  const taskBudgetSessions = new Map();
  const workflowDisclosureState = new Map();
  const directoryDisclosureState = new Map();
  const profileTabState = new Map();
  const loadProcessCatalog = () => {
    if (!processCatalogRequest) processCatalogRequest = api("/process-catalog").catch(() => {
      processCatalogRequest = null;
      return null;
    });
    return processCatalogRequest;
  };
  const processNotes = fields => fields.filter(([, value]) => Array.isArray(value) ? value.length : value)
    .map(([label, value]) => label + "：" + (Array.isArray(value) ? value.join("、") : value)).join("\n");
  const workflowRow = (tag, code, name, description, count = "") => {
    const row = make(tag, undefined, "workflow-tree-row");
    if (code) row.append(make("span", code, "workflow-node-code"));
    const label = make("span", name, "workflow-node-name");
    row.append(label);
    if (description) row.append(miniTip(description, "info", name));
    if (count) row.append(make("span", count, "workflow-node-count"));
    return row;
  };
  const workflowBranch = (agentKey, key, summary, children, initiallyOpen = false) => {
    const item = make("li", undefined, "workflow-node"), branch = make("details", undefined, "workflow-branch");
    const stateKey = agentKey + ":" + key;
    branch.dataset.nodeKey = key;
    branch.open = workflowDisclosureState.has(stateKey) ? workflowDisclosureState.get(stateKey) : initiallyOpen;
    branch.addEventListener("toggle", () => workflowDisclosureState.set(stateKey, branch.open));
    branch.append(summary, children); item.append(branch);
    return item;
  };
  const operationNode = (agentKey, operation) => {
    const steps = Array.isArray(operation.steps) ? operation.steps : [];
    const description = processNotes([
      ["状态", "参考计划草案，尚未绑定为 Engine 可执行节点"], ["适用范围", operation.applicability],
      ["输入", operation.inputs], ["产出", operation.outputs], ["放行条件", operation.release_criteria],
      ["责任角色", operation.owner_role], ["前置计划节点", operation.depends_on],
      ["所属阶段当前绑定工具", operation.available_tool_keys], ["待接入能力", operation.integration_gaps]
    ]);
    const children = make("ul", undefined, "workflow-children");
    steps.forEach(step => {
      const item = make("li", undefined, "workflow-step");
      item.dataset.stepKey = step.step_key;
      item.append(workflowRow("div", String(step.sequence_no).padStart(2, "0"), step.action || step.step_key,
        processNotes([["节点", step.step_key], ["动作", step.action], ["输入", step.input_refs],
          ["输出记录", step.output_record], ["验收标准", step.acceptance_criteria],
          ["执行责任", step.executor], ["异常处理", step.exception_action], ["前置节点", step.depends_on]])));
      children.append(item);
    });
    return workflowBranch(agentKey, operation.operation_key,
      workflowRow("summary", operation.operation_key, operation.name, description, steps.length + " 节点 · 草案"), children);
  };
  const workflowCard = (workflow, catalog, agentKey, profileLoaded) => {
    const section = make("section", undefined, "profile-card workflow-card");
    const heading = make("div", undefined, "profile-card-heading");
    const reference = catalog?.workflows?.find(item => item.agent_key === agentKey);
    const operations = Array.isArray(reference?.operations) ? reference.operations : [];
    const stepCount = operations.reduce((count, operation) => count + (operation.steps?.length || 0), 0);
    const title = make("h3", "计划 / Plan");
    heading.append(make("span", undefined, "future-war-stage-icon plan-icon"), title);
    if (workflow.steps.length) heading.append(make("span", workflow.mode === "research"
      ? "研究模式 · " + workflow.steps.length + " 阶段" : "固定节点 · " + workflow.steps.length + " 步", "badge"));
    title.append(miniTip([
      workflow.mode === "research" ? "第一层为当前 Profile 的研究阶段，点击箭头展开计划节点。" : "显示当前 Profile 登记的固定计划节点与工具。",
      operations.length ? "下级为参考草案：" + operations.length + " 计划 / " + stepCount + " 节点，内容版本 " + catalog.catalog_version + "。草案尚未发布为 Engine 执行配置。" : "",
      operations.length ? catalog.scope : "",
      "每次运行的实际工具调用与依赖，请在执行监控中查看。"
    ].filter(Boolean).join("\n"), "info", "计划配置"));
    section.append(heading);
    const tree = make("ul", undefined, "workflow-tree");
    tree.setAttribute("aria-label", "计划与执行节点层次");
    workflow.steps.forEach((step, index) => {
      const key = step.key || step.step_key, name = step.name || key || "节点";
      const description = processNotes([["说明", step.description || step.executor], ["必需交付物", step.requiredTypes],
        ["前置节点", step.dependencies], ["工具", step.tools]]);
      const mapped = operations.filter(operation => operation.research_stage_ref === key);
      const children = make("ul", undefined, "workflow-children");
      mapped.forEach(operation => children.append(operationNode(agentKey, operation)));
      if (mapped.length) {
        tree.append(workflowBranch(agentKey, "stage:" + key,
          workflowRow("summary", String(index + 1).padStart(2, "0"), name, description, mapped.length + " 计划节点"), children, true));
      } else {
        const item = make("li", undefined, "workflow-step workflow-stage");
        item.append(workflowRow("div", String(index + 1).padStart(2, "0"), name, description)); tree.append(item);
      }
    });
    // Keep unmapped reference operations visible if the current Profile stages change.
    const stageKeys = new Set(workflow.steps.map(step => step.key || step.step_key));
    const unmapped = operations.filter(operation => !stageKeys.has(operation.research_stage_ref));
    if (unmapped.length) {
      const children = make("ul", undefined, "workflow-children");
      unmapped.forEach(operation => children.append(operationNode(agentKey, operation)));
      tree.append(workflowBranch(agentKey, "unmapped-reference", workflowRow("summary", "", "待关联的参考计划",
        "这些草案尚未关联到当前 Profile 的研究阶段。", unmapped.length + " 计划节点"), children));
    }
    if (tree.childElementCount) section.append(tree);
    else section.append(make("p", profileLoaded ? "该 Agent 尚未登记具体节点。" : "当前会话暂时未加载节点详情，请刷新页面或联系管理员。", "muted"));
    if (!catalog) section.append(make("span", "参考计划暂未加载", "workflow-load-status"),
      miniTip("当前仅展示已读取的 Profile 节点；刷新页面可重试加载参考计划。", "caution", "参考内容加载状态"));
    return section;
  };
  const toolPurpose = tool => {
    const purpose = make("span", tool.category || "用途");
    if (tool.description) purpose.append(miniTip(tool.description, "info", (tool.tool_name || tool.tool_key || "工具") + " · 用途"));
    return purpose;
  };
  const agentDirectory = (agents, models, tools, bindings) => {
    const directory = make("aside", undefined, "agent-directory card");
    directory.setAttribute("aria-label", "按层级选择 Agent 与原子能力");
    const heading = make("h2", undefined, "agent-directory-heading");
    const headingIcon = make("span", undefined, "future-war-stage-icon data-model-icon");
    headingIcon.setAttribute("aria-hidden", "true");
    heading.append(headingIcon, document.createTextNode("Agent 能力目录"), miniTip("按层级选择 Agent，展开原子能力查看工具。", "info", "Agent 能力目录"));
    directory.append(make("div", "AGENT LAYERS", "journey-section-kicker"), heading);
    const modelConfig = new Map(models.map(item => [item.agent_key, configObject(item.config_json)]));
    const agentByKey = new Map(agents.map(item => [item.agent_key, item]));
    const layers = [
      {key:"application", name:"应用级 Agent", description:"负责完整业务任务", icon:"imagine-icon"},
      {key:"action", name:"Action Agent", description:"执行单项分析动作", icon:"execute-icon"},
      {key:"internal", name:"研究改进内部角色", description:"由研究改进流程调度的复盘、候选设计与基线验证角色；不是新的业务任务入口。", icon:"loop-icon"},
      {key:"atomic", name:"原子能力", description:"已注册工具与算法", icon:"tools-icon"}
    ];
    const grouped = new Map(layers.map(layer => [layer.key, []]));
    const internalRoles=new Set(["research-reflection-agent","research-candidate-designer-agent","research-report-quality-agent"]);
    agents.forEach(agent => {
      const layer = internalRoles.has(agent.agent_key)?"internal":modelConfig.get(agent.agent_key)?.agentLayer || "application";
      // Existing Profiles predate agentLayer and are application Agents.
      if (!grouped.has(layer)) {
        grouped.set(layer, []);
        layers.push({key:layer, name:layer + " Agent", description:"其他已配置层级", icon:"scope-icon"});
      }
      grouped.get(layer).push(agent);
    });
    const selectAgent = agent => {
      setActiveAgent(agent.agent_key);
      history.replaceState(null, "", "#agent=" + encodeURIComponent(agent.agent_key));
      render().catch(error => notice(error.message, true));
    };
    const agentButton = agent => {
      const selected = agent.agent_key === state.agentKey;
      const button = make("button", undefined, "agent-directory-item" + (selected ? " selected" : ""));
      button.type = "button";
      button.dataset.agentKey = agent.agent_key;
      if (selected) button.setAttribute("aria-current", "true");
      const titleRow = make("span", undefined, "agent-directory-title-row");
      titleRow.append(make("strong", agent.display_name || agent.agent_key),
        make("small", text(agent.current_version || "未发布"), "agent-directory-version"));
      button.append(titleRow);
      if (agent.description) button.title = agent.description;
      button.onclick = () => selectAgent(agent);
      return button;
    };
    layers.forEach(layer => {
      const members = grouped.get(layer.key);
      const atomicTools = layer.key === "atomic" ? tools : [];
      const group = make("details", undefined, "agent-directory-group");
      group.dataset.layer = layer.key;
      group.open = members.some(agent => agent.agent_key === state.agentKey) ||
        (directoryDisclosureState.get(layer.key) ?? !["atomic","internal"].includes(layer.key));
      group.addEventListener("toggle", () => {
        if (group.isConnected) directoryDisclosureState.set(layer.key, group.open);
      });
      const summary = make("summary", undefined, "agent-directory-group-title");
      const icon = make("span", undefined, "future-war-stage-icon " + layer.icon);
      icon.setAttribute("aria-hidden", "true");
      const label = make("span", undefined, "agent-directory-group-label");
      const layerTitle = make("strong", layer.name);
      layerTitle.append(miniTip(layer.description, "info", layer.name));
      label.append(layerTitle);
      const count = make("span", members.length + atomicTools.length, "agent-directory-count");
      count.setAttribute("aria-label", members.length + " 个 Agent" + (layer.key === "atomic" ? "，" + atomicTools.length + " 项工具" : ""));
      summary.append(icon, label, count);
      const list = make("div", undefined, "agent-directory-group-items");
      members.forEach(agent => list.append(agentButton(agent)));
      atomicTools.forEach(tool => {
        const item = make("details", undefined, "agent-directory-tool");
        item.dataset.toolKey = tool.tool_key;
        const title = make("summary", tool.tool_name || tool.tool_key, "agent-directory-tool-title");
        title.title = tool.tool_key;
        const body = make("div", undefined, "agent-directory-tool-body");
        if (tool.description) title.append(miniTip(tool.description, "info", tool.tool_name || tool.tool_key));
        body.append(make("code", tool.tool_key),
          make("p", "版本：" + text(tool.current_version || "未发布") + " · 注册状态：" + text(tool.status)));
        const boundAgents = [...new Set(bindings.filter(binding => binding.tool_key === tool.tool_key &&
          binding.enabled !== false && binding.enabled !== 0).map(binding => binding.agent_key))]
          .map(key => agentByKey.get(key)).filter(Boolean);
        if (boundAgents.length) {
          body.append(make("p", "已启用绑定："));
          boundAgents.forEach(agent => {
            const button = make("button", agent.display_name || agent.agent_key, "agent-directory-tool-agent");
            button.type = "button";
            button.onclick = () => selectAgent(agent);
            body.append(button);
          });
        } else body.append(make("p", "暂无启用的 Agent 绑定"));
        item.append(title, body);
        list.append(item);
      });
      if (!members.length && !atomicTools.length) list.append(make("p", "暂无已注册能力", "agent-directory-empty"));
      group.append(summary, list);
      directory.append(group);
    });
    return directory;
  };

  function sync() {
    const area = state.area || "agents";
    const hasRun = Boolean(state.runId);
    const isTask = area === "task";
    const isRuns = area === "runs";
    const isSettings = area === "settings";
    if (document.body.dataset.area !== area) document.body.dataset.area = area;
    const hidden = (node, value) => { if (node.hidden !== value) node.hidden = value; };
    const dedicated = area === "agents" || area === "audit";
    hidden($("journey-agent-view"), area !== "agents");
    hidden($("journey-audit-view"), area !== "audit");
    hidden($("task-run-shell"), dedicated);
    hidden($("task-card"), !isTask);
    hidden($("task-context-card"), !isTask);
    hidden($("run-history-card"), !isRuns);
    hidden($("run-status-card"), !isRuns || !hasRun);
    hidden($("tabs"), !(isSettings || (isRuns && hasRun)));
    hidden($("panel"), isTask);
    hidden($("followup-section"), !isRuns || !hasRun || $("followup-section").dataset.available !== "true");
    document.querySelectorAll("[data-tab-group]").forEach(group => {
      hidden(group, group.dataset.tabGroup === "settings" ? !isSettings : !isRuns);
    });
    const aside = document.querySelector("#task-run-shell .layout>aside");
    if (aside) hidden(aside, isSettings);
    document.querySelectorAll("[data-area]").forEach(button => button.classList.toggle("selected", button.dataset.area === area));
  }

  function updateTaskProfileHint() {
    const key = $("agent")?.value;
    syncTaskGoal(key);
    const hint = $("selected-profile-hint");
    if (!hint) return;
    const agent = (state.agents || []).find(item => item.agent_key === key);
    hint.textContent = agent ? "当前 Profile：" + text(agent.current_version || "未发布") + " · " + text(agent.status) : "";
  }

  async function renderTask() {
    const generation = ++taskRenderGeneration;
    const root = $("task-context-card");
    const picker = $("entity-picker");
    // Preserve the reusable form host before replacing the main task surface.
    picker?.remove();
    const agentKey = $("agent")?.value || state.agentKey;
    const selected = (state.agents || []).find(item => item.agent_key === agentKey) || {};
    updateTaskProfileHint();
    const title = selected.display_name || selected.agent_key || "尚未选择 Agent";
    root.replaceChildren();
    const intro = make("div", undefined, "task-profile-summary");
    const taskTitle = make("h2", title, "task-profile-title");
    taskTitle.append(miniTip(selected.description || "先选择一个 Agent，确认职责后再提交任务。", "info", "Agent 职责"));
    intro.append(taskTitle);
    const meta = make("div", undefined, "profile-meta");
    meta.append(make("span", "版本：" + text(selected.current_version || "未发布")), make("span", "状态：" + text(selected.status)), make("span", "领域：" + text(selected.domain)));
    intro.append(meta);
    root.append(intro);
    root.append(picker);

    const objectPreview = make("section", undefined, "profile-card task-flow-card target-object-preview");
    objectPreview.id = "task-object-preview";
    // All UAV application roles use a checked entity baseline; strategic
    // research and deterministic Actions have their own input contracts.
    objectPreview.hidden = !["uav-capability-analyst","uav-design-engineer","uav-evidence-reviewer"].includes(agentKey);
    root.append(objectPreview);
    const [taskModels, taskBindings] = await Promise.all([api("/config/models"),api("/config/bindings")]);
    if (generation !== taskRenderGeneration || state.area !== "task" || $("agent").value !== agentKey) return;
    const taskModel = taskModels.find(item => item.agent_key === agentKey) || {};
    const taskConfig = configObject(taskModel.config_json);
    const configuredLimits = taskConfig.budgetLimits || {};
    const boundTools = taskBindings.filter(item => item.agent_key === agentKey && item.enabled !== false && item.enabled !== 0);
    const modelEnabled = selected.agent_layer !== "action" && taskConfig.researchModeAvailable === true;
    if (!taskBudgetSessions.has(agentKey)) taskBudgetSessions.set(agentKey, {});
    const savedBudget = taskBudgetSessions.get(agentKey);
    for (const [id,key,fallback,enabled] of [
      ["model-budget","model_calls",12,modelEnabled],
      ["search-budget","search_calls",4,boundTools.some(item => item.tool_key === "web.search")],
      ["scrape-budget","scrape_calls",4,boundTools.some(item => item.tool_key === "web.scrape")]
    ]) {
      const control = $(id), maximum = Number.isInteger(configuredLimits[key]) ? configuredLimits[key] : 0;
      control.min = ["future-battlefield-analyst","future-battlefield-codex"].includes(agentKey) && enabled ? (key === "model_calls" ? "2" : "1") : key === "model_calls" && enabled ? "1" : "0";
      control.max = String(maximum); control.disabled = !enabled || maximum === 0;
      control.value = control.disabled ? "0" : String(Math.min(maximum,savedBudget[key] ?? fallback));
      control.oninput = () => { savedBudget[key] = Number(control.value); };
      control.parentElement.querySelector(".mini-tip")?.remove();
      control.parentElement.insertBefore(miniTip(control.disabled ? "当前 Profile 的执行路径未启用此类调用。" : "当前 Profile 允许的上限为 " + maximum + " 次；本次任务可缩小预算。", "info", "运行预算边界"), control);
    }
    await window.AgentAdminTargets.mount($("entity-picker"), objectPreview, {
      agentKey, agentLayer:selected.agent_layer, displayName:selected.display_name,
      taskConfig,
      api, notice, canOperate:hasRole("operator")
    });
    if (generation !== taskRenderGeneration || state.area !== "task" || $("agent").value !== agentKey) return;
    // Domain controls remain associated with the shared submit form even
    // though their visual surface is in the main column.
    picker.querySelectorAll("input,select,textarea,button").forEach(control => control.setAttribute("form", "create-form"));

    const stages = make("ol", undefined, "task-stages");
    [
      ["冻结配置", "本次运行记录所选 Agent 版本、任务输入和调用预算。"],
      ["排队执行", "Engine 按 Profile 的节点和已绑定工具推进研究。"],
      ["观察进度", "任务进入执行监控，可查看当前状态、报告、证据与调用记录。"],
      ["审计结果", "结束后从历史审计回看结论、证据、审核决定和运行事件。"]
    ].forEach(([name, description]) => { const item = make("li"); item.append(make("strong", name), miniTip(description, "flow", name)); stages.append(item); });
    const stageCard = make("details", undefined, "task-flow-disclosure");
    stageCard.append(make("summary", "任务生命周期"), stages);
    root.append(stageCard);

    try {
      const [scopes, bindings, tools] = await Promise.all([api("/config/scope"), api("/config/bindings"), api("/config/tools")]);
      if (generation !== taskRenderGeneration || state.area !== "task" || $("agent").value !== agentKey) return;
      const scope = scopes.find(item => item.agent_key === agentKey) || {};
      const toolMap = new Map(tools.map(item => [item.tool_key, item]));
      const profile = make("details", undefined, "task-flow-disclosure");
      profile.append(make("summary", "职责边界与工具"));
      profile.append(make("p", copy(scope.responsibility_json), "help-note help-info"));
      const bound = bindings.filter(item => item.agent_key === agentKey);
      profile.append(table(["已绑定工具", "用途", "状态"], bound.map(item => {
        const tool = toolMap.get(item.tool_key) || {};
        return [tool.tool_name || item.tool_key, toolPurpose(tool), item.enabled === false ? "停用" : "启用"];
      }), "profile-table binding-overview task-tool-bindings"));
      if (!bound.length) profile.append(make("p", "当前配置没有可显示的工具绑定。", "muted"));
      root.append(profile);
    } catch {
      root.append(make("p", "当前无法读取 Profile 摘要；提交前请回到 Agents 页面核对职责与工具绑定。", "muted"));
    }
  }

  function renderExecutionEmpty() {
    const root = $("panel");
    root.replaceChildren(make("div", "RUN MONITOR", "journey-section-kicker"), make("h2", "选择一次运行开始观察"), make("p", "左侧列出最近任务。选择一条后，这里会显示运行状态，并可切换到报告、证据、计划、预算和事件详情。", "help-note help-help"));
    const actions = make("div", undefined, "empty-state-actions");
    const task = make("button", "新建任务", "primary-action");
    task.onclick = () => window.navigateAgentAdminArea?.("task");
    const audit = make("button", "查看历史审计");
    audit.onclick = () => window.navigateAgentAdminArea?.("audit");
    actions.append(task, audit);
    root.append(actions);
  }

  async function renderAgents() {
    const generation = ++agentRenderGeneration;
    const root = $("journey-agent-view");
    root.replaceChildren();
    const agents = state.agents?.length ? state.agents : await api("/agents");
    if (generation !== agentRenderGeneration || state.area !== "agents") return;
    state.agents = agents;
    if (!state.agentKey || !agents.some(item => item.agent_key === state.agentKey)) state.agentKey = agents[0]?.agent_key;
    const agentKey = state.agentKey;
    const [models, tools, bindings, scopes, resources, processCatalog, goalContract] = await Promise.all([api("/config/models"), api("/config/tools"), api("/config/bindings"), api("/config/scope"), api("/config/resources"), loadProcessCatalog(), api("/agents/" + encodeURIComponent(agentKey) + "/goal-contract")]);
    const selected = agents.find(item => item.agent_key === agentKey) || agents[0] || {};
    let exported = null;
    try { exported = await api((hasRole("admin") ? "/config-profiles/" : "/agent-profile-views/") + encodeURIComponent(agentKey)); } catch { /* the remaining registry summaries still render */ }
    let executionReadiness = null;
    if (["uav-capability-analyst", "uav-design-engineer", "uav-evidence-reviewer"].includes(agentKey) || window.AgentAdminCognitive?.isAgent(agentKey)) {
      try { executionReadiness = await api("/agents/" + encodeURIComponent(agentKey) + "/execution-readiness"); }
      catch { executionReadiness = {ready:false, issues:[{code:"readiness-unavailable", message:"暂时无法核验执行基线，请刷新重试。"}]}; }
    }
    if (generation !== agentRenderGeneration || agentKey !== state.agentKey || state.area !== "agents") return;
    const documentValue = exported?.document || {};
    const registeredBaseline = exported?.executionBaseline;
    const executionBaseline = exported?.executionBaselineDraft || registeredBaseline;
    const showingBaselineDraft = executionBaseline?.viewState === "proposed";
    const model = models.find(item => item.agent_key === state.agentKey) || {};
    const scope = scopes.find(item => item.agent_key === state.agentKey) || {};
    const resource = resources.find(item => item.agent_key === state.agentKey) || {};
    const bound = documentValue.bindings || bindings.filter(item => item.agent_key === state.agentKey);
    const toolMap = new Map(tools.map(item => [item.tool_key, item]));

    const directory = agentDirectory(agents, models, tools, bindings);

    const workflow = workflowView(documentValue, model, agentKey);
    const capability = exported?.dependencies?.capabilityGraph?.graph || documentValue.capabilityGraph;
    const convergence = documentValue.convergence || {};
    const modelConfig = configObject(model.config_json);
    const effectiveCeiling = (counter, convergenceField) => {
      const limits = [modelConfig.budgetLimits?.[counter], convergence[convergenceField]].filter(Number.isInteger);
      return limits.length ? Math.min(...limits) : null;
    };
    const effectiveModelCalls = effectiveCeiling("model_calls", "max_model_calls");
    const effectiveToolCalls = effectiveCeiling("tool_calls", "max_tool_calls");
    const enabledBindings = bound.filter(item => item.enabled !== false && item.enabled !== 0);
    const layerLabels = { application: "应用级 Agent", action: "Action Agent", atomic: "原子能力" };
    const layerLabel = layerLabels[modelConfig.agentLayer] || "应用级 Agent";

    const content = make("section", undefined, "agent-profile-main agent-profile-main--baseline-first");
    const pageHeader = make("section", undefined, "agent-page-header");
    const pageHeaderTop = make("div", undefined, "agent-page-header-top");
    const pageIntro = make("div", undefined, "agent-page-intro");
    pageIntro.append(make("div", "Agent 工作台 / " + layerLabel, "agent-page-kicker"));
    const pageTitleRow = make("div", undefined, "agent-page-title-row");
    const avatar = make("span", undefined, "agent-profile-symbol future-war-stage-icon " + (modelConfig.agentLayer === "action" ? "execute-icon" : "imagine-icon"));
    avatar.setAttribute("aria-hidden", "true");
    const titleText = make("div", undefined, "agent-page-title-text");
    const pageMeta = make("div", undefined, "agent-page-meta");
    const statusLabels = {published:"已发布", draft:"草稿", disabled:"已停用", active:"已启用"};
    const status = make("span", "Profile · " + (statusLabels[selected.status] || text(selected.status)), "agent-meta-chip agent-meta-status");
    status.dataset.status = selected.status || "unknown";
    pageMeta.append(status, make("span", "版本 " + text(selected.current_version || "未发布"), "agent-meta-chip"), make("span", layerLabel, "agent-meta-chip"));
    const headingLine = make("div", undefined, "agent-page-heading-line");
    const pageTitle = make("h1", selected.display_name || selected.agent_key, "agent-page-title");
    pageTitle.append(miniTip(selected.description || "暂无职责说明", "info", "Agent 职责简介"));
    headingLine.append(pageTitle, pageMeta);
    if(["future-battlefield-analyst","future-battlefield-codex","uav-capability-analyst","uav-design-engineer","uav-evidence-reviewer"].includes(agentKey))
      window.AgentAdminVerification?.mount(headingLine,{subject:{kind:"agent-registration",agent_key:agentKey},api});
    titleText.append(headingLine);
    pageTitleRow.append(avatar, titleText);
    pageIntro.append(pageTitleRow);
    const actions = make("div", undefined, "profile-actions agent-page-actions");
    const runButton = make("button", "使用此 Agent", "primary-action"); runButton.onclick = () => { $("agent").value = state.agentKey; updateTaskProfileHint(); window.navigateAgentAdminArea?.("task"); };
    if (executionReadiness && !executionReadiness.ready) {
      runButton.disabled = true;
      runButton.textContent = "基线未就绪";
      runButton.title = "在执行基线中查看缺少的角色阶段与执行器。";
    }
    actions.append(runButton);
    if (hasRole("admin")) { const edit = make("button", "编辑 Profile"); edit.onclick = () => window.navigateAgentAdminArea?.("settings"); actions.append(edit); }
    pageHeaderTop.append(pageIntro, actions);
    pageHeader.append(pageHeaderTop);
    content.append(pageHeader);

    const summary = make("section", undefined, "agent-summary-strip");
    const summaryStat = (icon, label, value, detail) => {
      const stat = make("div", undefined, "agent-summary-stat");
      stat.append(make("span", undefined, "future-war-stage-icon " + icon));
      const copy = make("div", undefined, "agent-summary-stat-copy");
      const labelText = make("span", label, "agent-summary-label");
      if (detail) labelText.append(miniTip(detail, "info", label));
      copy.append(labelText, make("strong", value, "agent-summary-value"));
      stat.append(copy);
      return stat;
    };
    summary.append(summaryStat("plan-icon", showingBaselineDraft ? "草案节点" : "基线节点", text(executionBaseline?.graph?.nodes?.length || workflow.steps?.length || 0), showingBaselineDraft ? "已保存的角色 DAG 草案，尚未发布或运行" : "执行图基线中的固定节点与依赖"), summaryStat("tools-icon", "启用工具", text(enabledBindings.length), "共 " + bound.length + " 项绑定"), summaryStat("data-model-icon", "能力要求", text(capability?.capabilities?.length || 0), capability?.version ? "图谱 v" + capability.version : "尚未登记"), summaryStat("loop-icon", "模型调用上限", text(effectiveModelCalls), "资源配额与收敛上限的较小值 / 运行"));
    content.append(summary);

    const grid = make("div", undefined, "agent-profile-main-grid");
    const readable = value => {
      if (typeof value === "string") {
        const trimmed = value.trim();
        if ((trimmed.startsWith("[") && trimmed.endsWith("]")) || (trimmed.startsWith("{") && trimmed.endsWith("}"))) {
          try { return readable(JSON.parse(trimmed)); } catch { /* retain the original value */ }
        }
      }
      if (Array.isArray(value)) return value.length ? value.join("、") : "无";
      if (value && typeof value === "object") {
        if (Array.isArray(value.responsibilities)) return value.responsibilities.join("、");
        return Object.entries(value).map(([key, item]) => key + "：" + readable(item)).join("；");
      }
      return copy(value);
    };
    const planCard = make("section", undefined, "profile-card profile-card--plan");
    planCard.append(panelHeading(showingBaselineDraft ? "角色执行基线 · 草案" : "执行图基线", showingBaselineDraft ?
      "已保存到数据库的角色 DAG 草案，包含节点契约和配额。执行器尚未安装，未发布，不参与运行。当前登记配置可在下方展开。" :
      "当前 Profile 的节点、依赖、契约和资源上限。基线配置、任务计划和实际执行分别保存。"));
    grid.append(planCard);

    const bindingTable = table(["工具", "版本", "用途", "启用", "审批"], bound.map(binding => { const tool = toolMap.get(binding.tool_key) || {}; return [tool.tool_name && tool.tool_name !== binding.tool_key ? tool.tool_name + " · " + binding.tool_key : binding.tool_key, binding.tool_version, toolPurpose(tool), binding.enabled === false ? "停用" : "启用", binding.approval_required ? "需要" : "自动"]; }), "profile-table binding-overview");
    const bindingSummary = make("div", undefined, "binding-summary");
    [["启用工具", enabledBindings.length + " 项", "当前 Profile 已启用"], ["调用需审批", enabledBindings.filter(item => item.approval_required).length + " 项", "按调用审批策略执行"], ["未启用", Math.max(0, bound.length - enabledBindings.length) + " 项", "当前不参与调用"]].forEach(([label, value, detail]) => { const item = make("div", undefined, "binding-summary-item"); const name = make("span", label, "binding-summary-label"); name.append(miniTip(detail, "info", label)); item.append(name, make("strong", value, "binding-summary-value")); bindingSummary.append(item); });
    const bindingBody = make("div", undefined, "profile-copy");
    const toolFilter = make("div", undefined, "profile-tool-filter");
    const searchLabel = make("label", "搜索工具");
    const searchInput = make("input"); searchInput.type = "search"; searchInput.placeholder = "工具名称、标识或用途"; searchLabel.append(searchInput);
    const statusLabel = make("label", "绑定状态"), statusFilter = make("select");
    [["all", "全部状态"], ["enabled", "已启用"], ["disabled", "未启用"]].forEach(([key, label]) => statusFilter.append(new Option(label, key))); statusLabel.append(statusFilter);
    const filterCount = make("span", undefined, "profile-filter-count"); filterCount.setAttribute("aria-live", "polite");
    const noTools = make("p", "没有匹配的工具，请调整搜索条件。", "profile-empty-state");
    const filterTools = () => {
      const query = searchInput.value.trim().toLocaleLowerCase(); let shown = 0;
      [...bindingTable.tBodies[0].rows].forEach((row, index) => {
        const binding = bound[index], enabled = binding.enabled !== false && binding.enabled !== 0;
        row.hidden = !(row.textContent.toLocaleLowerCase().includes(query) && (statusFilter.value === "all" || enabled === (statusFilter.value === "enabled")));
        if (!row.hidden) shown++;
      });
      filterCount.textContent = shown + " / " + bound.length + " 项工具"; noTools.hidden = shown > 0;
    };
    searchInput.oninput = statusFilter.onchange = filterTools; filterTools();
    toolFilter.append(searchLabel, statusLabel, filterCount);
    const toolScroll = make("div", undefined, "profile-table-scroll"); toolScroll.tabIndex = 0; toolScroll.setAttribute("aria-label", "工具绑定明细"); toolScroll.append(bindingTable);
    bindingBody.append(bindingSummary, toolFilter, toolScroll, noTools);
    grid.append(card("工具与调用权限", bindingBody, "binding-card profile-card--bindings"));

    if (capability?.capabilities?.length) {
      const body = make("div", undefined, "profile-copy");
      body.append(make("p", "能力要求 · v" + text(capability.version), "muted"));
      body.append(table(["能力", "依赖动作", "验收要求"], capability.capabilities.map(item => [
        item.name, (item.requiredActions || []).map(key => agents.find(agent => agent.agent_key === key)?.display_name || toolMap.get(key)?.tool_name || key).join("、"),
        (item.acceptance || []).join("；")
      ]), "profile-table capability-requirements"));
      const details = make("details");
      details.append(make("summary", "查看证据契约要求"));
      capability.capabilities.forEach(item => details.append(make("p", item.name + "：" + (item.requiredEvidence || []).join("、"))));
      body.append(details);
      grid.append(card("能力图谱与验收要求", body, "binding-card profile-card--capability"));
    }

    const convergenceCopy = make("div", undefined, "profile-copy"); convergenceCopy.append(make("p", "允许终态：" + readable(convergence.allowed_terminal_states_json)), make("p", "完成条件：" + readable(convergence.completion_conditions_json)), make("p", "最大模型调用：" + text(convergence.max_model_calls)), make("p", "最大工具调用：" + text(convergence.max_tool_calls)), make("p", "无进展停止轮数：" + text(convergence.no_new_finding_rounds))); grid.append(card("Loop 与收敛", convergenceCopy, "profile-card--convergence"));
    const modelCopy = make("div", undefined, "profile-copy"); modelCopy.append(make("p", "模型：" + text(model.model_name)), make("p", "Provider：" + text(model.model_provider)), make("p", "数据源：" + text(documentValue.dataSourceKey || modelConfig.ontologySourceKey || "uav-ontology")), make("p", "最大并行工具：" + text(resource.max_parallel_tools))); grid.append(card("模型与数据源", modelCopy, "profile-card--model"));
    const tabs = make("div", undefined, "profile-view-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Agent 详情");
    const views = make("div", undefined, "profile-views");
    // The execution baseline is the operator's primary decision surface. Keep
    // the overview available, but open the full dependency DAG first so the
    // user can inspect what will run before reading secondary profile detail.
    const viewDefinitions = [["plan", "执行基线", "plan-icon"], ["overview", "Agent 概览", "imagine-icon"], ["capabilities", "能力与工具", "tools-icon"], ["runtime", "运行约束", "loop-icon"]];
    const viewMap = new Map(), tabMap = new Map();
    const selectView = key => {
      profileTabState.set(agentKey, key);
      viewMap.forEach((panel, name) => { panel.hidden = name !== key; });
      tabMap.forEach((button, name) => { button.setAttribute("aria-selected", String(name === key)); button.tabIndex = name === key ? 0 : -1; });
    };
    viewDefinitions.forEach(([key, label, iconClass], index) => {
      const button = make("button", undefined, "profile-view-tab"); button.type = "button"; button.dataset.profileTab = key; button.id = "profile-tab-" + key;
      button.setAttribute("role", "tab"); button.setAttribute("aria-controls", "profile-view-" + key);
      const icon = make("span", undefined, "future-war-stage-icon " + iconClass); icon.setAttribute("aria-hidden", "true"); button.append(icon, make("span", label));
      button.onclick = () => selectView(key);
      button.onkeydown = event => {
        const next = event.key === "ArrowRight" ? (index + 1) % viewDefinitions.length : event.key === "ArrowLeft" ? (index + viewDefinitions.length - 1) % viewDefinitions.length : event.key === "Home" ? 0 : event.key === "End" ? viewDefinitions.length - 1 : -1;
        if (next < 0) return; event.preventDefault(); const target = viewDefinitions[next][0]; selectView(target); tabMap.get(target).focus();
      };
      const panel = make("section", undefined, "profile-view profile-view--" + key); panel.id = "profile-view-" + key; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", button.id); panel.tabIndex = 0;
      viewMap.set(key, panel); tabMap.set(key, button); tabs.append(button); views.append(panel);
    });
    const overview = make("div", undefined, "profile-overview-layout"), overviewMain = make("div", undefined, "profile-overview-main"), overviewAside = make("aside", undefined, "profile-overview-aside");
    const goalSection = make("section", undefined, "profile-card agent-goal-contract agent-definition");
    goalSection.append(panelHeading("Agent 定义", "统一查看职责目标、输入依赖、权限限制和执行准则。职责目标按版本保存，本次任务目标在新建任务时填写。"));
    window.AgentAdminReviewSummary.mountGoalContract(goalSection, goalContract, {api, canEdit:hasRole("admin"), notice, onPublished:renderAgents, profileScope:scope, profileDescription:selected.description, formatValue:readable, tip:miniTip});
    overviewMain.append(goalSection);
    const planPreview = make("section", undefined, "profile-card profile-plan-preview");
    const previewHeading = make("div", undefined, "profile-preview-heading"), planLink = make("button", "查看执行基线 →", "profile-text-action"); planLink.type = "button"; planLink.onclick = () => { selectView("plan"); tabMap.get("plan").focus(); };
    previewHeading.append(panelHeading("计划 / Plan", "执行图基线缩略图折叠连续节点，保留并行与汇合关系。点击节点可查看完整基线。"), planLink); planPreview.append(previewHeading);
    let fullBaselineView;
    window.AgentExecutionBaseline.mount(planPreview, {baseline:executionBaseline,readiness:executionReadiness,compact:true,
      onOpen:id => {selectView("plan"); fullBaselineView?.selectNode(id);}});
    overviewMain.append(planPreview);
    const runtimePreview = make("section", undefined, "profile-card profile-runtime-preview"); runtimePreview.append(panelHeading("运行资源", "以下是当前 Profile 的运行约束；创建具体任务时可进一步设置本次运行的资源预算。"));
    const runtimeValues = make("dl", undefined, "profile-runtime-values");
    [["执行模型", model.model_name], ["模型调用上限", effectiveModelCalls == null ? "未读取" : effectiveModelCalls + " 次"], ["工具调用上限", effectiveToolCalls == null ? "未读取" : effectiveToolCalls + " 次"], ["最长运行时间", convergence.max_runtime_seconds == null ? "未读取" : convergence.max_runtime_seconds + " 秒"], ["工具并行上限", resource.max_parallel_tools == null ? "未读取" : resource.max_parallel_tools + " 项"]].forEach(([label, value]) => { const row = make("div"); row.append(make("dt", label), make("dd", text(value))); runtimeValues.append(row); });
    runtimePreview.append(runtimeValues); overviewAside.append(runtimePreview);
    const outputPreview = make("section", undefined, "profile-card profile-output-preview");
    const outputValue = typeof scope.output_entities_json === "string" ? (() => { try { return JSON.parse(scope.output_entities_json); } catch { return []; } })() : scope.output_entities_json;
    const outputs = Array.isArray(outputValue) ? outputValue : [];
    outputPreview.append(panelHeading("交付与验收", "集中列出目标约定的交付要求、Profile 输出类型及完成条件。结构化交付检查通过不等于专业正确性已被独立核验。"));
    const deliverables = goalContract?.document?.deliverables || [];
    if (deliverables.length) {
      outputPreview.append(make("h4", "交付要求")); const list = make("ul", undefined, "profile-acceptance-list");
      deliverables.forEach(value => list.append(make("li", value))); outputPreview.append(list);
    }
    outputPreview.append(make("h4", "输出类型"));
    const outputList = make("ul", undefined, "profile-output-list"); outputs.forEach(value => outputList.append(make("li", value))); outputPreview.append(outputList);
    if (!outputs.length) outputPreview.append(make("p", "尚未登记输出类型", "muted"));
    const completion = Array.isArray(convergence.completion_conditions_json) ? convergence.completion_conditions_json : [];
    if (completion.length) { outputPreview.append(make("h4", "完成条件")); const list = make("ul", undefined, "profile-acceptance-list"); completion.forEach(value => list.append(make("li", value))); outputPreview.append(list); }
    if (goalContract?.document?.evaluation) {
      const evaluation = make("details", undefined, "profile-acceptance-scope"); evaluation.append(make("summary", "验收范围"));
      evaluation.append(make("p", "结构化交付与追溯；完整覆盖和专业正确性需独立评估。", "muted"));
      evaluation.append(make("p", "评估器：" + text(goalContract.document.evaluation.evaluator), "muted")); outputPreview.append(evaluation);
    }
    overviewAside.append(outputPreview); overview.append(overviewMain, overviewAside); viewMap.get("overview").append(overview);
    const fullPlan = grid.querySelector(".profile-card--plan");
    fullBaselineView = window.AgentExecutionBaseline.mount(fullPlan, {baseline:executionBaseline,readiness:executionReadiness,
      documentValue,canEdit:hasRole("admin") && !showingBaselineDraft,api,notice,onPublished:renderAgents});
    if (showingBaselineDraft && registeredBaseline) {
      const previous = make("details", undefined, "profile-raw-details");
      previous.append(make("summary", "当前登记配置 · Profile " + registeredBaseline.graph.profileVersion + " · " + registeredBaseline.graph.nodes.length + " 节点"));
      const current = make("div"); previous.append(current);
      previous.addEventListener("toggle", () => {
        if (previous.open && !current.firstChild) window.AgentExecutionBaseline.mount(current,
          {baseline:registeredBaseline,readiness:executionReadiness,compact:true});
      });
      fullPlan.append(previous);
    }
    viewMap.get("plan").append(fullPlan);
    const capabilityCard = grid.querySelector(".profile-card--capability");
    if (capabilityCard) viewMap.get("capabilities").append(capabilityCard);
    viewMap.get("capabilities").append(grid.querySelector(".profile-card--bindings"));
    try { window.AgentSkillUI.configuration(viewMap.get("capabilities"), await api("/agents/"+encodeURIComponent(agentKey)+"/skills")); }
    catch(e) { viewMap.get("capabilities").append(make("p","Skill 读取失败："+e.message,"muted")); }
    viewMap.get("runtime").append(grid.querySelector(".profile-card--convergence"), grid.querySelector(".profile-card--model"));
    if (["future-battlefield-analyst","future-battlefield-codex"].includes(agentKey) && window.AgentAdminReasoning) await window.AgentAdminReasoning.mountProfile(viewMap.get("runtime"),{agentKey,api,notice,canEdit:hasRole("admin")});
    if (exported?.liveHash && hasRole("admin")) { const details = make("details", undefined, "profile-raw-details"); details.append(make("summary", "高级信息 · Profile 标识与原始快照"), make("p", "Profile Key：" + text(agentKey), "muted"), make("p", "Profile Hash：" + text(exported.liveHash), "muted"), json(documentValue)); viewMap.get("runtime").append(details); }
    if (!exported) content.append(make("p", "Profile 详情暂时无法读取，当前仅展示注册信息。请刷新重试。", "profile-load-warning"));
    content.append(tabs, views); selectView(profileTabState.get(agentKey) || "plan");
    const layout = make("div", undefined, "agent-center-grid"); layout.append(directory, content); root.append(layout);
  }

  async function renderAudit() {
    const root = $("journey-audit-view"); root.replaceChildren(make("div", "AUDIT CENTER", "journey-section-kicker"), make("h1", "历史任务与审计", "journey-page-title"), make("p", "每次运行都绑定不可变 Profile 快照，可按 Agent、状态和时间回看。", "help-note help-info"));
    const runs = await api("/runs?limit=200");
    const filters = make("div", undefined, "audit-filters"), agentFilter = make("select"), statusFilter = make("select"); agentFilter.append(new Option("全部 Agent", "")); (state.agents || []).forEach(agent => agentFilter.append(new Option(agent.display_name || agent.agent_key, agent.agent_key))); statusFilter.append(new Option("全部状态", "")); ["queued", "running", "paused", "completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"].forEach(status => statusFilter.append(new Option(status, status))); filters.append(labelControl("Agent", agentFilter), labelControl("状态", statusFilter));
    const tableCard = make("section", undefined, "card audit-table-card");
    const draw = () => { const rows = runs.filter(run => (!agentFilter.value || run.agent_key === agentFilter.value) && (!statusFilter.value || run.status === statusFilter.value)); tableCard.replaceChildren(table(["时间", "Agent", "运行 ID", "状态", "当前阶段", "调用量", "操作"], rows.map(run => { const open = make("button", "查看执行图"); open.onclick = () => selectRun(run.run_id); return [run.created_at, run.agent_key, run.run_id, runStatusLabel(run), runCurrentStepLabel(run), (run.model_call_count || 0) + " 模型 / " + (run.tool_call_count || 0) + " 工具", open]; }), "audit-table")); };
    agentFilter.onchange = draw; statusFilter.onchange = draw; root.append(filters, tableCard); draw();
  }

  window.AgentAdminJourney = { sync, renderAgents, renderAudit, renderTask, renderExecutionEmpty, updateTaskProfileHint };
})();



