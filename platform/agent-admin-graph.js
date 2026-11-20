"use strict";
/* Run DAG: declared dependencies, observed order, and unrolled attempts. */
(() => {
  const sessions = new Map();
  const mounted = new WeakMap();
  const labels = {created:"已创建", queued:"排队中", planning:"规划中", running:"执行中", validating:"校验中",
    "waiting-tool":"等待工具", "waiting-human":"等待人工", retrying:"重试中", looping:"迭代中", paused:"已暂停",
    succeeded:"已完成", completed:"已完成", "completed-with-gaps":"自动完成 · 有缺口", "requires-human-review":"需专家审阅",
    blocked:"已阻断", failed:"失败", cancelled:"已取消", skipped:"已跳过", dispatched:"已调度", pending:"待执行", proposed:"计划动作", unknown:"未知"};
  const terminal = new Set(["completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"]);
  const time = value => Date.parse(value) || 0;
  const statusLabel = status => labels[status] || status || "未知";
  const runMode = run => run?.automationMode || run?.input_json?.automationMode || (run?.status === "waiting-human" ? "human-gate" : "unattended-draft");
  const legacyHumanGate = run => run?.status === "waiting-human" && !run?.automationMode && !run?.input_json?.automationMode;
  const runMessage = run => {
    if (run?.status === "created") return "仅已创建 · 尚未启动";
    if (run?.status === "queued") return "已入队 · 等待执行";
    if (run?.status === "paused") return "运行已暂停";
    if (run?.status === "cancelled") return "运行已取消";
    if (run?.status === "waiting-human") return legacyHumanGate(run) ? "历史运行 · 人工发布门禁未处理" : "运行已暂停 · 等待人工操作";
    if (run?.status === "completed-with-gaps") return runMode(run) === "human-gate" ? "运行完成 · 证据有缺口" : "发布状态 · 草稿未发布 · 专家审阅";
    if (run?.status === "requires-human-review") return "运行完成 · 报告待专家审阅";
    if (run?.status === "completed") return "发布状态 · 草稿未发布 · 专家审阅";
    if (run?.status === "failed") return "运行失败 · 查看当前节点详情";
    if (run?.status === "blocked") return "运行阻断 · 需要补齐前置条件";
    return runMode(run) === "human-gate" ? "人工发布门禁已启用" : "自动运行 · 无需人工干预";
  };
  const tone = status => ["succeeded","completed","completed-with-gaps"].includes(status) ? "done" :
    ["running","planning","validating","retrying","looping"].includes(status) ? "running" :
    ["waiting-human","waiting-tool","requires-human-review","blocked","paused"].includes(status) ? "waiting" :
    status === "failed" ? "failed" : "pending";
  const stamp = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai", hour12:false}) : "—";
  const make = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  };
  const button = (text, action, className) => {
    const item = make("button", text, className); item.type = "button"; item.onclick = action; return item;
  };
  const summary = (holder, label, value) => {
    if (value === undefined || value === null || value === "") return;
    const row = make("div", undefined, "dag-detail-row"); row.append(make("dt", label), make("dd", value)); holder.append(row);
  };
  const structured = (holder, label, value) => {
    if (value === undefined || value === null) return;
    const details = make("details", undefined, "dag-data"); details.append(make("summary", label), make("pre", JSON.stringify(value, null, 2))); holder.append(details);
  };

  function llmDescription(calls) {
    const models = new Map();
    for (const call of calls) {
      const key = JSON.stringify([call.providerKey, call.actualModel, call.requestedModel, call.status, call.stage, call.errorCode]);
      if (!models.has(key)) models.set(key, {call, count:0});
      models.get(key).count++;
    }
    return [...models.values()].map(({call, count}) => {
      const identity = call.actualModel || "实际模型未返回";
      const requested = call.requestedModel && call.requestedModel !== call.actualModel ? "\n请求模型：" + call.requestedModel : "";
      return identity + " · " + count + " 次 · " + statusLabel(call.status) + requested + (call.providerKey ? "\n提供方：" + call.providerKey : "") + (call.stage ? "\n环节：" + call.stage : "") + (call.errorCode ? "\n原因：" + call.errorCode : "");
    }).join("\n\n") + "\n\n名称与版本按调用记录的模型标识显示；具体发布版本未另行记录。";
  }

  function toolDescription(calls) {
    const tools = new Map();
    for (const call of calls) {
      const key = JSON.stringify([call.toolKey, call.toolVersion, call.status]);
      if (!tools.has(key)) tools.set(key, {call, count:0});
      tools.get(key).count++;
    }
    return [...tools.values()].map(({call, count}) => (call.toolKey || "工具名称未记录") +
      (call.toolVersion ? " · v" + call.toolVersion : " · 版本未记录") + "\n" + count + " 次 · " + statusLabel(call.status)).join("\n\n");
  }

  function connectorDescription(call) {
    const binding=call?.connectorBinding;
    if (!binding || typeof binding.connectorRef!=="string" || !binding.connectorRef ||
        (binding.callId && call.call_id && binding.callId!==call.call_id)) return "";
    return ["连接器："+binding.connectorRef,
      binding.connectorVersion ? "版本："+binding.connectorVersion : "",
      binding.callId || call.call_id ? "调用："+(binding.callId||call.call_id) : "",
      binding.operationKey ? "操作："+binding.operationKey : ""].filter(Boolean).join("\n");
  }

  const methodPaths = window.AgentGraph.methodPaths;
  const executionMethod = item => window.AgentGraph.executionMethod(item);
  function methodDescription(item) {
    const sections = [executionMethod(item).description];
    if (item.llmCalls?.length) sections.push((item.delegations?.length ? "父节点" : "本节点") + "直接模型调用\n" + llmDescription(item.llmCalls));
    if (item.toolCalls?.length) sections.push("直接工具调用\n" + toolDescription(item.toolCalls));
    if (item.dispatches?.length) sections.push("已记录处理器\n" + [...new Set(item.dispatches.map(d => d.executorKey || d.handlerId || "处理器版本未记录"))].join("\n"));
    if (item.delegations?.length) sections.push("子 Agent 委派\n" + item.delegations.map(d => (d.displayName || d.agentKey || "子 Agent") + (d.agentVersion ? " · v" + d.agentVersion : "") + "\n" + (d.childRunId || d.child_run_id) + " · " + statusLabel(d.status)).join("\n") + "\n子任务的模型调用单独记录，不计为本节点直接推理。");
    if (childModelCalls(item).length) sections.push("子 Agent 内部模型调用\n" + item.delegations.filter(d => d.llmCalls?.length).map(d => (d.childRunId || d.child_run_id) + "\n" + llmDescription(d.llmCalls)).join("\n\n"));
    return sections.join("\n\n");
  }
  function childModelCalls(item) {
    return [...new Map((item.delegations || []).flatMap(d => d.llmCalls || []).map(c => [c.callId, c])).values()];
  }
  function callTip(item, pos, childModels = false) {
    const method = childModels ? {category:"llm",label:"LLM"} : executionMethod(item), isModel = method.category === "llm";
    const description = childModels ? [item.llmCalls?.length ? "父节点直接模型调用\n" + llmDescription(item.llmCalls) : "",
      childModelCalls(item).length ? "子 Agent 内部模型调用；按实际调用归属统计。\n\n" + item.delegations.filter(d => d.llmCalls?.length).map(d =>
      (d.displayName || d.agentKey || "子 Agent") + " · " + (d.childRunId || d.child_run_id) + "\n" + llmDescription(d.llmCalls)).join("\n\n") : ""].filter(Boolean).join("\n\n") : methodDescription(item);
    const tip = window.AgentAdminTips.create(description, "info", method.label + " · " + item.label);
    tip.classList.add("dag-call-tip", isModel ? "dag-llm-tip" : "dag-tool-tip"); tip.dataset.callNodeId = item.id;
    tip.dataset.executionCategory = method.category;
    if (childModels && childModelCalls(item).length) tip.dataset.childLlmNodeId = item.id;
    else if (childModels) tip.dataset.llmNodeId = item.id;
    else if (isModel) tip.dataset.llmNodeId = item.id;
    else if (item.toolCalls?.length) tip.dataset.toolNodeId = item.id;
    tip.setAttribute("aria-label", item.label + (childModels ? childModelCalls(item).length ? "的子 Agent 内部模型调用" : "的直接模型调用" : "的执行方式：" + method.label));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", methodPaths[method.category === "human" ? "input" : method.category]);
    svg.append(path); tip.replaceChildren(svg, make("span", method.category === "unknown" ? "" : method.label));
    const tipWidth = method.category === "sub-agent" ? 66 : 38;
    tip.style.width = tipWidth + "px";
    tip.style.left = pos.x + pos.width - tipWidth - 5 + "px"; tip.style.top = pos.y + (childModels ? 25 : 5) + "px";
    tip.addEventListener("pointerdown", event => event.stopPropagation());
    tip.addEventListener("keydown", event => { if (!["Escape", "Tab"].includes(event.key)) event.stopPropagation(); });
    return tip;
  }

  function project(data, eventId) {
    const event = data.events.find(item => item.id === eventId);
    const cutoff = event ? time(event.createdAt) : Infinity;
    const availableEvents = data.events.filter(item => !event || item.id <= event.id);
    const transitions = availableEvents.filter(item => item.type === "run.status" && item.status);
    const runStatus = event ? transitions.at(-1)?.status || "created" : data.run.status;
    const nodes = data.nodes.filter(item => {
      const available = time(item.availableAt || item.createdAt || data.run.createdAt);
      return available <= cutoff && !(event && item.attempt > 0 && time(item.createdAt) > cutoff);
    }).map(item => {
      let status = item.status;
      if (event) {
        if (item.id === "result") status = terminal.has(runStatus) ? runStatus : "pending";
        else if (item.observed && item.id !== "entry") {
          if (time(item.startedAt || item.finishedAt || item.createdAt) > cutoff) status = "pending";
          else if (item.finishedAt && time(item.finishedAt) > cutoff) status = "running";
          else if (item.pendingApprovalAt && time(item.pendingApprovalAt) > cutoff) status = item.observedStatus || "failed";
          else if (!item.finishedAt) status = "running";
        }
      }
      // Missing call timestamps are usable in the live view only. Do not
      // reveal a later model response when replaying an earlier event.
      const llmCalls = (item.llmCalls || []).filter(call => !event || (call.createdAt && time(call.createdAt) <= cutoff));
      const toolCalls = (item.toolCalls || []).filter(call => !event || (call.createdAt && time(call.createdAt) <= cutoff));
      const dispatches = (item.dispatches || []).filter(d => !event || (d.createdAt && time(d.createdAt) <= cutoff));
      const delegations = (item.delegations || []).filter(d => !event || (d.createdAt ? time(d.createdAt) <= cutoff : item.finishedAt && time(item.finishedAt) <= cutoff)).map(d => ({...d,
        status:event && (!item.finishedAt || time(item.finishedAt) > cutoff) ? "dispatched" : d.status,
        llmCalls:(d.llmCalls || []).filter(c => !event || (c.createdAt && time(c.createdAt) <= cutoff)),
        toolCalls:(d.toolCalls || []).filter(c => !event || (c.createdAt && time(c.createdAt) <= cutoff))}));
      const executionMethod = event ? item.humanGateAt && time(item.humanGateAt) <= cutoff ? {category:"human"} : null : item.executionMethod;
      const skillInvocations=(item.skillInvocations||[]).filter(i=>!event||time(i.created_at)<=cutoff).map(i=>event&&(!i.completed_at||time(i.completed_at)>cutoff)?{...i,status:'running',completed_at:null,model_call_ids:[],output_hash:null,error_code:null}:i);
      return {...item, skillInvocations, status, dispatches, delegations, executionMethod, llmCalls, modelCallIds:llmCalls.map(call => call.callId), modelCallCount:llmCalls.length,
        toolCalls, toolCallIds:toolCalls.map(call => call.callId), toolCallCount:toolCalls.length};
    });
    const ids = new Set(nodes.map(item => item.id));
    return {nodes, edges:data.edges.filter(item => ids.has(item.from) && ids.has(item.to)),
      groups:data.groups.filter(group => nodes.some(item => item.group === group.id)), cutoff, event, availableEvents, runStatus};
  }

  // The audit projection keeps every attempt. The default monitor view groups
  // those attempts into one phase card so retries and research loops do not
  // turn the canvas into a wall of duplicate nodes. Selecting a phase exposes
  // the exact attempts; no audit record is discarded.
  function summarizeGraph(graph) {
    const byKey = new Map(), originals = new Map(graph.nodes.map(node => [node.id, node]));
    for (const item of graph.nodes) {
      const key = item.executionKey || item.id;
      // During event replay, do not leave a future observed phase as a
      // misleading pending card. Unobserved frozen plan actions remain visible
      // when their definition is already available at the replay cutoff.
      if (graph.event && !item.observed) continue;
      if (graph.event && item.observed && time(item.startedAt || item.finishedAt || item.createdAt) > graph.cutoff) continue;
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(item);
    }
    const nodes = [];
    const idByKey = new Map();
    for (const [key, members] of byKey) {
      const ordered = [...members].sort((a,b) => (a.attempt ?? -1) - (b.attempt ?? -1) || time(a.createdAt) - time(b.createdAt));
      const latest = ordered.at(-1);
      const id = members.length > 1 ? "phase:" + key : latest.id;
      const llmCalls = [...new Map(ordered.flatMap(n => n.llmCalls || []).map(call => [call.callId, call])).values()];
      const toolCalls = [...new Map(ordered.flatMap(n => n.toolCalls || []).map(call => [call.callId, call])).values()];
      idByKey.set(key, id);
      const phase = latest.phaseSummary || {
        executionKey:key, attemptCount:members.length, latestAttempt:latest.attempt ?? 0,
        latestStatus:latest.status, failureCount:members.filter(n => n.failureKind === "execution-error").length,
        interruptionCount:members.filter(n => n.failureKind === "worker-interrupted").length,
        recoveredCount:members.filter(n => n.recoveredBy).length,
        gapCount:members.reduce((sum,n) => sum + (n.gapCount || 0), 0),
        latestOutputSummary:latest.outputSummary, latestElapsedMs:latest.elapsedMs,
        toolCallCount:members.reduce((sum,n) => sum + (n.toolCallCount || 0), 0),
        toolFailureCount:members.reduce((sum,n) => sum + (n.toolFailureCount || 0), 0),
      };
      nodes.push({...latest, id, attempt:undefined, phaseSummary:phase, llmCalls, toolCalls, skillInvocations:[...new Map(ordered.flatMap(n=>n.skillInvocations||[]).map(i=>[i.invocation_id,i])).values()],
        dispatches:ordered.flatMap(n => n.dispatches || []), delegations:[...new Map(ordered.flatMap(n => n.delegations || []).map(d => [d.invocationId || d.childRunId, d])).values()],
        toolCallIds:toolCalls.map(call => call.callId), toolCallCount:toolCalls.length,
        modelCallIds:llmCalls.map(call => call.callId), modelCallCount:llmCalls.length, members:ordered.map(n => n.id),
        memberAttempts:ordered.map(n => ({id:n.id, attempt:n.attempt, status:n.status, failureKind:n.failureKind,
          error:n.error, startedAt:n.startedAt, finishedAt:n.finishedAt, outputSummary:n.outputSummary})),
        failureKind: phase.failureCount ? "execution-error" : phase.interruptionCount ? "worker-interrupted" : null,
        status:phase.latestStatus});
    }
    const edgeKeys = new Set(), edges = [];
    const add = (from,to,kind,extra={}) => {
      if (!from || !to || from === to) return;
      const key = `${from}|${to}|${kind}`; if (edgeKeys.has(key)) return;
      edgeKeys.add(key); edges.push({from,to,kind,...extra});
    };
    for (const edge of graph.edges) {
      if (["retry", "observed-order"].includes(edge.kind)) continue;
      if (edge.kind === "context" && originals.get(edge.to)?.kind !== "result") continue;
      const from = idByKey.get(originals.get(edge.from)?.executionKey || edge.from);
      const to = idByKey.get(originals.get(edge.to)?.executionKey || edge.to);
      add(from,to,edge.kind,{contract:edge.contract,dependencyType:edge.dependencyType,contractStatus:edge.contractStatus});
    }
    return { ...graph, nodes, edges, groups:graph.groups.filter(group => nodes.some(node => node.group === group.id)) };
  }

  const layoutGraph = (...args) => window.AgentGraph.layoutGraph(...args);
  const edgePath = (...args) => window.AgentGraph.edgePath(...args);

  async function render(holder, {runId, api, notice, onTab, canReview = false}) {
    let data = await api("/runs/" + encodeURIComponent(runId) + "/graph");
    let signature = JSON.stringify(data);
    if (!sessions.has(runId)) sessions.set(runId, {selectedId:null, eventId:null, zoom:.85, panX:0, panY:0, manualZoom:false, scrollLeft:0, scrollTop:0, detailCache:null, compact:true});
    // Bound UI memory to the recent runs rather than retaining audit payloads indefinitely.
    if (sessions.size > 20) sessions.delete(sessions.keys().next().value);
    const saved = sessions.get(runId);
    const root = make("div", undefined, "dag-view");
    root.dataset.runId = runId;
    const heading = make("div", undefined, "dag-heading"), title = make("div", undefined, "dag-title");
    title.append(make("span", undefined, "future-war-stage-icon execute-icon"), make("h2", "执行图 / Execute"), window.AgentAdminTips.create(
      "从左到右阅读执行阶段。实线表示数据契约流转，虚线表示执行依赖；点划线表示已记录的时间先后，重试和循环展开为独立尝试。契约边可查看生产者、消费者、版本和字段映射。按住鼠标左键拖动画布可平移。",
      "info", "如何阅读执行图"));
    const tools = make("div", undefined, "dag-tools");
    const attemptMode = button("", () => { saved.compact = !saved.compact; saved.selectedId = null; draw(); }, "dag-attempt-mode");
    attemptMode.title = "切换阶段摘要与全部尝试";
    const zoomGroup = make("div", undefined, "dag-zoom-group");
    zoomGroup.setAttribute("role", "group"); zoomGroup.setAttribute("aria-label", "执行图缩放");
    const zoomOut = button("−", () => setZoom(saved.zoom - .1), "dag-zoom-step");
    const zoomValue = button("", () => camera.reset(), "dag-zoom-value");
    const zoomIn = button("+", () => setZoom(saved.zoom + .1), "dag-zoom-step");
    zoomOut.setAttribute("aria-label", "缩小执行图"); zoomOut.title = "缩小执行图（−）";
    zoomIn.setAttribute("aria-label", "放大执行图"); zoomIn.title = "放大执行图（+）";
    zoomValue.title = "点击恢复 100%（0）";
    zoomGroup.append(zoomOut, zoomValue, zoomIn);
    tools.append(attemptMode, zoomGroup, button("适应画布", fitCanvas), window.AgentAdminTips.create(
      "点击 − / + 缩放，点击比例恢复 100%；适应画布可查看整图。支持 10%–200%。在画布上按住鼠标左键拖动可平移，按 Ctrl / ⌘ + 滚轮可围绕鼠标缩放；聚焦画布后也可按 +、−、0。普通滚轮仍用于滚动。",
      "help", "画布缩放"));
    const meta = make("div", undefined, "dag-meta");
    meta.append(make("span", data.run.agentName + " · v" + data.run.agentVersion),
      make("span", data.run.mode === "research" ? "研究计划 DAG / Plan" : "固定计划 DAG / Plan"),
      make("span", data.plans.length ? data.plans.filter(p => p.accepted).length + " 个有效计划 / " + data.plans.length + " 个版本" : "冻结 Profile"));
    const runNote = make("div", runMessage(data.run), "dag-run-note dag-" + tone(data.run.status));
    const lifecycle = make("div", undefined, "dag-lifecycle"); lifecycle.setAttribute("aria-label", "任务生命周期");
    // Keep all run-level context in the same reading rail as the title. The
    // canvas below should start immediately after this single summary line.
    heading.append(title, meta, runNote, lifecycle, tools);
    const bar = make("div", undefined, "dag-replay");
    const mode = make("select"); mode.setAttribute("aria-label", "执行图观察模式");
    const liveLabel = () => terminal.has(data.run.status) ? "最终运行快照" : data.run.status === "created" ? "等待启动 · 5 秒同步" : "实时观察 · 5 秒同步";
    mode.append(new Option(liveLabel(), "live"), new Option("按事件回放", "replay"));
    mode.value = saved.eventId === null ? "live" : "replay";
    const previous = button("前一步", () => seek(-1)), next = button("后一步", () => seek(1));
    const slider = make("input"); slider.type = "range"; slider.min = "0"; slider.max = String(Math.max(0, data.events.length - 1)); slider.step = "1"; slider.setAttribute("aria-label", "事件回放进度");
    const position = make("span", undefined, "dag-replay-position"), eventText = make("span", undefined, "dag-replay-event");
    mode.onchange = () => { saved.eventId = mode.value === "live" ? null : data.events[0]?.id ?? null; draw(); };
    slider.oninput = () => { saved.eventId = data.events[Number(slider.value)]?.id ?? null; mode.value = "replay"; draw(); };
    bar.append(mode, previous, slider, next, position);
    const workspace = make("div", undefined, "dag-workspace"), canvasArea = make("div", undefined, "dag-canvas-area");
    const legend = make("div", undefined, "dag-legend");
    for (const [key, text] of [["done","已完成 / 有缺口"],["running","执行中"],["waiting","需处理"],["failed","失败"],["pending","待执行 / 计划动作"]]) legend.append(make("span", text, "dag-key dag-" + key));
    legend.append(make("span", "实线 · 数据契约", "dag-edge-key dag-edge-key-contract"), make("span", "虚线 · 执行依赖", "dag-edge-key dag-edge-key-dependency"), make("span", "点划线 · 执行先后", "dag-edge-key dag-edge-key-observed"));
    const viewport = make("div", undefined, "dag-viewport"); viewport.setAttribute("aria-label", "执行 DAG 画布，可滚动；按住鼠标左键拖动画布平移；Ctrl 或 Command 加滚轮缩放，按加减号缩放，0 恢复原始比例"); viewport.title = "按住鼠标左键拖动画布平移；Ctrl / Command + 滚轮缩放"; viewport.tabIndex = 0;
    const sizer = make("div", undefined, "dag-sizer"), board = make("div", undefined, "dag-board"); sizer.append(board); viewport.append(sizer);
    const inspector = make("aside", undefined, "dag-inspector"); inspector.setAttribute("aria-label", "节点详情"); inspector.setAttribute("aria-live", "polite");
    const footer = make("div", undefined, "dag-footer");
    canvasArea.append(legend, viewport, eventText, footer); workspace.append(canvasArea, inspector);
    root.append(heading, bar, workspace); holder.append(root);
    const reasoningView = window.AgentAdminReasoning && ["future-battlefield-analyst", "future-battlefield-codex"].includes(data.run.agentKey)
      ? window.AgentAdminReasoning.mountRun(root, {runId, api, notice, triggerHost:tools, canReview}) : null;
    let graph, displayGraph, layout, selected, detailGeneration = 0;
    function seek(delta) {
      const index = saved.eventId === null ? data.events.length - 1 : data.events.findIndex(e => e.id === saved.eventId);
      saved.eventId = data.events[Math.max(0, Math.min(data.events.length - 1, index + delta))]?.id ?? null;
      mode.value = "replay"; draw();
    }
    const camera = window.AgentGraph.createViewport({viewport, sizer, board, state:saved,
      onChange:() => {
        zoomValue.textContent = Math.round(saved.zoom * 100) + "%";
        zoomValue.setAttribute("aria-label", "当前比例 " + zoomValue.textContent + "，点击恢复 100%");
        zoomOut.disabled = saved.zoom <= window.AgentGraph.MIN_ZOOM;
        zoomIn.disabled = saved.zoom >= window.AgentGraph.MAX_ZOOM;
      }});
    function setZoom(value, manual = true, anchor = null) { camera.setZoom(value, {manual, anchor}); }
    function applyBoardTransform() { camera.apply(); }
    function fitCanvas() { camera.fit(); }
    function drawLifecycle() {
      lifecycle.replaceChildren();
      const history = [{status:"created", id:data.events.find(e => e.type === "run.created")?.id}];
      for (const event of graph.availableEvents.filter(e => e.type === "run.status" && e.status)) {
        if (history.at(-1)?.status !== event.status) history.push(event);
      }
      if (history.at(-1)?.status !== graph.runStatus) history.push({status:graph.runStatus});
      // The full lifecycle is available in event replay. The live canvas only
      // needs one current-state signal; a row of historical chips competes
      // with the graph for attention and made a normal run look blocked.
      const entry = history.at(-1) || {status:"unknown"};
      const chip = button(statusLabel(entry.status), () => { if (entry.id) { saved.eventId = entry.id; mode.value = "replay"; draw(); } }, "dag-life-step dag-" + tone(entry.status));
      chip.classList.add("current"); chip.disabled = !entry.id;
      chip.title = history.length > 1 ? "已经过 " + history.length + " 个生命周期状态；切换到事件回放查看" : "当前生命周期状态";
      lifecycle.append(chip);
    }
    function draw() {
      graph = project(data, saved.eventId);
      const message = runMessage({...data.run, status:graph.runStatus});
      if (runNote.textContent !== message) runNote.textContent = message;
      runNote.className = "dag-run-note dag-" + tone(graph.runStatus);
      const compactView = saved.compact && saved.eventId === null;
      displayGraph = saved.compact ? summarizeGraph(graph) : graph;
      root.graphView = window.AgentGraph.adapt("execution", displayGraph, {runId, eventId:saved.eventId});
      root.dataset.graphKind = "execution";
      // The compact phase rail is intentionally horizontal for scanability;
      // full attempts keep the vertical audit layout and event order.
      layout = layoutGraph(displayGraph, compactView ? {direction:"horizontal"} : {});
      attemptMode.textContent = compactView ? "阶段摘要" : "全部尝试";
      attemptMode.setAttribute("aria-label", compactView ? "当前为阶段摘要，点击查看全部尝试" : "当前为全部尝试，点击按阶段摘要");
      drawLifecycle(); board.replaceChildren();
      board.style.width = layout.width + "px"; board.style.height = layout.height + "px";
      for (const band of layout.bands) {
        const region = make("section", undefined, "dag-band");
        region.style.left = band.x + "px"; region.style.width = band.width + "px"; region.style.height = layout.height - 20 + "px";
        region.append(make("h3", band.label)); board.append(region);
      }
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.classList.add("dag-connections"); svg.setAttribute("width", layout.width); svg.setAttribute("height", layout.height); svg.setAttribute("aria-hidden", "true");
      const defs = document.createElementNS(svg.namespaceURI, "defs"), marker = document.createElementNS(svg.namespaceURI, "marker");
      marker.id = "dag-arrow-" + runId; marker.setAttribute("viewBox", "0 0 8 8"); marker.setAttribute("refX", "7"); marker.setAttribute("refY", "4"); marker.setAttribute("markerWidth", "5"); marker.setAttribute("markerHeight", "5"); marker.setAttribute("orient", "auto");
      const arrow = document.createElementNS(svg.namespaceURI, "path"); arrow.setAttribute("d", "M0 0 L8 4 L0 8Z"); arrow.setAttribute("fill", "#8d9eae"); marker.append(arrow); defs.append(marker); svg.append(defs);
      for (const item of displayGraph.edges) {
        const a = layout.positions.get(item.from), b = layout.positions.get(item.to);
        if (!a || !b) continue;
        const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", edgePath(a, b));
        path.setAttribute("marker-end", "url(#" + marker.id + ")"); path.dataset.from = item.from; path.dataset.to = item.to; path.dataset.kind = item.kind;
        path.classList.add("dag-edge", "dag-edge-" + item.kind);
        path.dataset.edgeType = item.kind;
        const description = document.createElementNS(svg.namespaceURI, "title");
        const contract = item.contract;
        description.textContent = item.kind === "data-contract"
          ? `数据契约：${contract?.name || "未命名"} · ${contract?.version || "未标版本"} · ${contract?.validationStatus || "未声明"}`
          : ({"execution-dependency":"执行依赖", dependency:"执行依赖", retry:"再次尝试 / 新一轮", "observed-order":"已记录的执行先后", context:"流程关联"}[item.kind] || item.kind);
        path.append(description); svg.append(path);
        if (item.kind === "data-contract") {
          const aLabel = contract?.name || "数据契约";
          const version = contract?.version && contract.version !== "inline" ? ` · ${contract.version}` : "";
          const label = document.createElementNS(svg.namespaceURI, "text");
          label.classList.add("dag-edge-label");
          label.setAttribute("x", String((a.x + a.width + b.x) / 2));
          label.setAttribute("y", String((a.y + a.height / 2 + b.y + b.height / 2) / 2 - 5));
          label.textContent = (aLabel + version).slice(0, 42);
          label.setAttribute("text-anchor", "middle");
          svg.append(label);
        }
      }
      board.append(svg);
      if (!displayGraph.nodes.some(n => n.id === saved.selectedId)) saved.selectedId = displayGraph.nodes.find(n => ["running","waiting-human","failed"].includes(n.status))?.id || "entry";
      for (const item of displayGraph.nodes) {
        const pos = layout.positions.get(item.id), box = button("", () => select(item.id), "dag-node dag-" + tone(item.status));
        box.dataset.nodeId = item.id; box.style.left = pos.x + "px"; box.style.top = pos.y + "px"; box.style.width = pos.width + "px"; box.style.height = pos.height + "px";
        box.setAttribute("aria-label", item.label + "，" + statusLabel(item.status)); box.setAttribute("aria-pressed", String(item.id === saved.selectedId));
        box.append(make("strong", item.label, "dag-node-label"));
        const invocationTip = callTip(item, pos);
        if (invocationTip) box.classList.add("has-call");
        if (executionMethod(item).category === "sub-agent") box.classList.add("has-sub-agent");
        const note = make("span", undefined, "dag-node-meta"); note.append(make("span", statusLabel(item.status), "dag-node-status"));
        const phase = item.phaseSummary;
        const extra = phase ? (phase.attemptCount > 1 ? phase.attemptCount + " 次尝试" : item.attempt !== undefined ? "第 " + (item.attempt + 1) + " 次" : "当前阶段") : item.kind === "model" ? "模型调用" : item.kind === "decision" ? "计划提议" : item.kind === "plan-validation" ? "计划校验" : item.kind === "result" ? "收敛状态" : "冻结配置";
        note.append(make("span", extra)); box.append(note); board.append(box);
        if (phase && (phase.latestOutputSummary || phase.gapCount || phase.interruptionCount || phase.toolFailureCount)) {
          const facts = [];
          if (phase.latestOutputSummary) facts.push(phase.latestOutputSummary);
          if (phase.gapCount) facts.push("缺口 " + phase.gapCount);
          if (phase.interruptionCount) facts.push("中断 " + phase.interruptionCount + " · 已恢复");
          if (phase.toolFailureCount) facts.push("工具失败 " + phase.toolFailureCount);
          box.append(make("span", facts.join(" · "), "dag-node-summary"));
        }
        if (invocationTip) board.append(invocationTip);
        const skillMark=window.AgentSkillUI.marker(item.skillBindings,item.skillInvocations,pos);if(skillMark)board.append(skillMark);
        if (childModelCalls(item).length || (executionMethod(item).category === "sub-agent" && item.llmCalls?.length)) board.append(callTip(item, pos, true));
      }
      const index = graph.event ? data.events.findIndex(e => e.id === graph.event.id) : data.events.length - 1;
      slider.value = String(Math.max(0, index)); slider.disabled = !data.events.length;
      previous.disabled = index <= 0; next.disabled = index >= data.events.length - 1;
      position.textContent = data.events.length ? (index + 1) + " / " + data.events.length : "暂无事件";
      eventText.textContent = graph.event ? stamp(graph.event.createdAt) + " · " + graph.event.type + (graph.event.stepKey ? " · " + graph.event.stepKey : "") : "当前快照 · " + stamp(data.run.updatedAt);
      const known = displayGraph.nodes.filter(n => n.kind === "step" || n.kind === "model");
      footer.replaceChildren(make("span", known.filter(n => ["succeeded","completed","completed-with-gaps"].includes(n.status)).length + " / " + known.length + " 阶段完成"));
      if (data.warnings.length || data.plans.some(p => !p.accepted)) footer.append(window.AgentAdminTips.create(
        [...data.warnings, ...data.plans.filter(p => !p.accepted).map(p => "计划 v" + p.version + " 校验未通过，未作为执行 DAG：" + (p.validation?.code || "查看计划记录"))].join("\n"), "caution", "计划与执行记录说明"));
      camera.setSize(layout); setZoom(saved.zoom, false); select(saved.selectedId);
    }

    async function detailsData() {
      if (saved.detailCache?.signature !== signature) {
        const prefix = "/runs/" + encodeURIComponent(runId);
        saved.detailCache = {signature, promise:Promise.allSettled([
          "steps?summary=true", "tool-calls?summary=true", "model-calls?summary=true", "artifacts?summary=true", "approvals", "evidence"
        ].map(key => api(prefix + "/" + key)))};
      }
      const results = await saved.detailCache.promise, bundle = {};
      ["steps","tools","models","artifacts","approvals","evidence"].forEach((key, index) => {
        const result = results[index]; bundle[key] = result.status === "fulfilled" ? result.value : key === "evidence" ? {} : [];
      });
      bundle.errors = results.filter(r => r.status === "rejected").length;
      if (bundle.errors) saved.detailCache = null;
      return bundle;
    }
    async function selectedDetails(item) {
      const prefix = "/runs/" + encodeURIComponent(runId);
      const requests = [];
      if (item.stepRunId) requests.push(api(prefix + "/steps/" + encodeURIComponent(item.stepRunId)));
      else requests.push(Promise.resolve(null));
      for (const id of item.toolCallIds || []) requests.push(api(prefix + "/tool-calls/" + encodeURIComponent(id)));
      for (const id of item.modelCallIds || []) requests.push(api(prefix + "/model-calls/" + encodeURIComponent(id)));
      for (const id of item.artifactIds || []) requests.push(api(prefix + "/artifacts/" + encodeURIComponent(id)));
      const result = await Promise.allSettled(requests);
      return {step:result[0]?.status === "fulfilled" ? result[0].value : null,
        tools:result.slice(1, 1 + (item.toolCallIds || []).length).filter(r => r.status === "fulfilled").map(r => r.value),
        models:result.slice(1 + (item.toolCallIds || []).length, 1 + (item.toolCallIds || []).length + (item.modelCallIds || []).length).filter(r => r.status === "fulfilled").map(r => r.value),
        artifacts:result.slice(1 + (item.toolCallIds || []).length + (item.modelCallIds || []).length).filter(r => r.status === "fulfilled").map(r => r.value)};
    }
    async function select(nodeId) {
      saved.selectedId = nodeId; selected = displayGraph.nodes.find(n => n.id === nodeId);
      if (!selected) return;
      const item = selected, generation = ++detailGeneration;
      for (const box of board.querySelectorAll(".dag-node")) box.setAttribute("aria-pressed", String(box.dataset.nodeId === nodeId));
      for (const path of board.querySelectorAll(".dag-edge")) path.classList.toggle("highlighted", path.dataset.from === nodeId || path.dataset.to === nodeId);
      inspector.replaceChildren(make("div", "节点详情", "dag-inspector-kicker"), make("h3", item.label));
      inspector.append(make("p", methodDescription(item), "dag-llm-details"));
      if(item.skillBindings?.length||item.skillInvocations?.length)window.AgentSkillUI.inspect(inspector,{runId,bindings:item.skillBindings,invocations:item.skillInvocations,api,cutoff:graph.cutoff});
      for (const child of item.delegations || []) {
        const open = button("查看子执行图 · " + (child.displayName || child.agentKey || child.childRunId), async () => {
          const returnToParent = button("返回父执行图", () => {
            childHolder.remove(); returnToParent.remove();
            for (const section of [heading,bar,workspace]) section.style.display = "";
          });
          const childHolder = make("div", undefined, "dag-child-view");
          for (const section of [heading,bar,workspace]) section.style.display = "none";
          root.append(returnToParent, childHolder);
          try { await render(childHolder, {runId:child.childRunId, api, notice, onTab, canReview}); }
          catch (error) { childHolder.replaceChildren(make("p", error.message)); }
        });
        open.dataset.childRunId = child.childRunId;
        inspector.append(open);
      }
      if (item.members?.length > 1) {
        const phase = item.phaseSummary || {};
        const info = make("dl", undefined, "dag-details");
        summary(info, "阶段状态", statusLabel(phase.latestStatus)); summary(info, "尝试次数", phase.attemptCount);
        summary(info, "恢复情况", `${phase.recoveredCount || 0} 次恢复 · ${phase.interruptionCount || 0} 次中断`);
        summary(info, "工具调用", `${phase.toolCallCount || 0} 次 · 失败 ${phase.toolFailureCount || 0} 次`);
        summary(info, "产出摘要", phase.latestOutputSummary || "—"); summary(info, "证据缺口", phase.gapCount || 0);
        inspector.append(info, make("h4", "全部尝试"));
        for (const attempt of item.memberAttempts || []) {
          const row = make("div", undefined, "dag-attempt-row");
          row.append(make("strong", "第 " + ((attempt.attempt ?? 0) + 1) + " 次 · " + statusLabel(attempt.status)), make("span", attempt.failureKind === "worker-interrupted" ? "Worker 中断 · 已恢复" : attempt.error || attempt.outputSummary || "无异常"));
          row.onclick = () => { saved.compact = false; draw(); select(attempt.id); };
          row.setAttribute("role", "button"); row.tabIndex = 0; inspector.append(row);
        }
        inspector.append(make("p", "阶段摘要用于快速阅读；点击某次尝试可查看完整输入、输出、工具调用和证据。", "dag-detail-note"));
        return;
      }
      const info = make("dl", undefined, "dag-details");
      summary(info, "状态", statusLabel(item.status)); summary(info, "节点", item.executionKey || item.id);
      if (item.contextId) {
        summary(info, "上下文", item.contextId);
        summary(info, "上下文状态", item.contextStatus || "冻结");
        summary(info, "上下文哈希", item.manifestHash);
        if (item.chainHash) summary(info, "上下文链哈希", item.chainHash);
        summary(info, "共享状态版本", item.wRevision === undefined || item.wRevision === null ? "—" : item.wRevision);
        if (item.invocationStatus) {
          summary(info, "调用槽", item.invocationStatus + " · " + (item.invocationAttemptCount ?? 0) + " 次尝试");
          if (item.invocationResultArtifactId) summary(info, "调用结果产物", item.invocationResultArtifactId);
        }
      }
      summary(info, item.startedAtEstimated ? "开始估算" : "开始", time(item.startedAt) <= graph.cutoff ? stamp(item.startedAt) : "尚未开始");
      summary(info, "结束", item.finishedAt && time(item.finishedAt) <= graph.cutoff ? stamp(item.finishedAt) : "—");
      if (item.attempt !== undefined) summary(info, "执行次数", "第 " + (item.attempt + 1) + " 次尝试");
      if (item.reason) summary(info, "计划理由", item.reason);
      if (item.error && time(item.finishedAt) <= graph.cutoff) summary(info, "异常", item.error);
      inspector.append(info);
      if (item.contextManifest) {
        inspector.append(make("h4", "实际上下文"));
        const manifest = item.contextManifest;
        structured(inspector, "角色与工步", {roleKey: manifest.roleKey, nodeKey: manifest.nodeKey,
          operationKey: manifest.operationKey, iteration: manifest.iteration});
        structured(inspector, "读取集与排除项", {dependencies: manifest.dependencies || [], excluded: manifest.excluded || []});
      }
      const dependencies = graph.edges.filter(e => e.to === nodeId && (e.kind === "execution-dependency" || e.kind === "dependency"));
      if (dependencies.length) {
        inspector.append(make("h4", "前置依赖"));
        for (const dep of dependencies) { const source = graph.nodes.find(n => n.id === dep.from); inspector.append(button(source?.label || dep.from, () => select(dep.from), "dag-related-node")); }
      }
      const contractEdges = graph.edges.filter(e => (e.from === nodeId || e.to === nodeId) && e.kind === "data-contract");
      if (contractEdges.length) {
        inspector.append(make("h4", "数据契约连接"));
        for (const edge of contractEdges) {
          const direction = edge.from === nodeId ? "输出 → 消费" : "输入 ← 产生";
          const contract = edge.contract || {};
          const item = make("div", undefined, "dag-contract-edge");
          item.append(make("strong", `${direction} · ${contract.name || "未命名契约"}`));
          item.append(make("span", `版本：${contract.version || "未标版本"} · 状态：${contract.validationStatus || "未声明"}`));
          const mapping = contract.fieldMapping || {};
          item.append(make("span", `字段映射：${(mapping.matchedFields || []).join("、") || "未声明"}`));
          inspector.append(item);
        }
      }
      if (item.inputContract) structured(inspector, "输入契约", item.inputContract);
      if (item.outputContract) structured(inspector, "输出契约", item.outputContract);
      if (item.kind === "input") {
        summary(info, "Agent", data.run.agentName + " · v" + data.run.agentVersion);
        summary(info, "执行模式", data.run.mode === "research" ? "模型规划 + 有界研究" : "固定计划");
        structured(inspector, "冻结计划版本", data.plans.filter(p => time(p.createdAt) <= graph.cutoff));
        structured(inspector, "循环与收敛规则", data.loopRules);
        return;
      }
      if (item.kind === "decision") {
        inspector.append(make("p", "该节点是计划中的动作提议；实际结果以运行记录和报告为准。", "dag-detail-note"));
        structured(inspector, "计划参数", item.arguments); return;
      }
      if (item.kind === "plan-validation") {
        structured(inspector, "计划校验记录", data.plans.find(p => p.id === item.planId));
        inspector.append(button("查看计划历史", () => onTab("plans")));
        return;
      }
      if (item.status === "pending" || !item.observed) { inspector.append(make("p", "此时尚无该节点的执行记录。", "dag-detail-note")); return; }
      const loading = make("p", "读取执行记录…", "muted"); inspector.append(loading);
      try {
        const bundle = await detailsData();
        if (generation !== detailGeneration) return;
        loading.remove();
        const occurred = record => time(record.finished_at || record.created_at || record.started_at) <= graph.cutoff;
        const selected = await selectedDetails(item);
        if (generation !== detailGeneration) return;
        const step = selected.step || bundle.steps.find(s => s.step_run_id === item.stepRunId);
        if (step) {
          structured(inspector, "输入", step.input_json);
          if (occurred(step)) structured(inspector, "输出", step.output_json);
        }
        const tools = (selected.tools.length ? selected.tools : bundle.tools.filter(c => item.toolCallIds.includes(c.call_id))).filter(occurred);
        const models = (selected.models.length ? selected.models : bundle.models.filter(c => item.modelCallIds.includes(c.call_id))).filter(occurred);
        if (tools.length || models.length) {
          inspector.append(make("h4", "工具与模型调用"));
          for (const call of [...tools, ...models]) {
            const line = make("div", undefined, "dag-call"); line.append(make("strong", call.tool_key || call.actual_model || call.requested_model || "模型"),
              make("span", statusLabel(call.status) + " · " + (call.latency_ms ?? "—") + " ms")); inspector.append(line);
            window.AgentAdminVerification?.mount(line,{subject:{kind:"invocation",run_id:runId,
              invocation_kind:call.tool_key?"tool":"model",invocation_id:call.call_id},api});
            const connector=connectorDescription(call);
            if(connector)inspector.append(make("p",connector,"dag-call-connector"));
            structured(inspector, "调用记录", call);
          }
        }
        const artifacts = (selected.artifacts.length ? selected.artifacts : bundle.artifacts.filter(a => item.artifactIds.includes(a.artifact_id))).filter(occurred);
        if (artifacts.length) {
          inspector.append(make("h4", "产物与原始记录"));
          for (const artifact of artifacts) structured(inspector, artifact.artifact_type + " · " + artifact.artifact_id, artifact);
        }
        const evidence = bundle.evidence.candidates || [];
        const candidates = item.kind === "result" ? evidence : evidence.filter(c => artifacts.some(a => a.artifact_id === c.sourceArtifactId));
        if (candidates.length && graph.cutoff === Infinity) {
          inspector.append(make("h4", "关联证据 · " + candidates.length));
          for (const candidate of candidates.slice(0, 8)) {
            const quote = make("blockquote", candidate.quote || candidate.candidateId);
            quote.title = candidate.sourceUri || ""; inspector.append(quote);
          }
        }
        const approvals = bundle.approvals.filter(a => item.approvalIds.includes(a.approval_id) && time(a.created_at) <= graph.cutoff).map(a => {
          if (graph.cutoff === Infinity || !a.decided_at || time(a.decided_at) <= graph.cutoff) return a;
          return {approval_id:a.approval_id, step_key:a.step_key, action:a.action, reason:a.reason, status:"pending", created_at:a.created_at};
        });
        if (approvals.length) { inspector.append(make("h4", "审批记录")); for (const approval of approvals) structured(inspector, statusLabel(approval.status) + " · " + approval.action, approval); }
        const links = make("div", undefined, "dag-detail-actions");
        links.append(button("证据与审核", () => onTab("evidence")), button(item.kind === "result" ? "查看报告" : "预算与调用", () => onTab(item.kind === "result" ? "report" : "budget")));
        inspector.append(links);
        if (bundle.errors) inspector.append(make("p", "部分执行明细暂时无法读取，重新选择节点可重试。", "dag-detail-note"));
      } catch (error) {
        if (generation === detailGeneration) { loading.textContent = "明细加载失败"; inspector.append(button("重试", () => select(nodeId))); notice(error.message, true); }
      }
    }
    mounted.set(root, async ({force = false, isCurrent = () => true} = {}) => {
      const childRoot = root.querySelector(".dag-child-view .dag-view");
      if (childRoot) { await refresh(childRoot, {force, isCurrent}); return; }
      const nextData = await api("/runs/" + encodeURIComponent(runId) + "/graph");
      if (!root.isConnected || !isCurrent()) return;
      await reasoningView?.refresh();
      const nextSignature = JSON.stringify(nextData);
      // Compare the full projection: plans, attempts, approvals and events can
      // change even when the run status and top-level counters stay the same.
      if (!force && nextSignature === signature) return;
      const scrollLeft = viewport.scrollLeft, scrollTop = viewport.scrollTop;
      data = nextData; signature = nextSignature; saved.detailCache = null;
      if (saved.eventId !== null && !data.events.some(event => event.id === saved.eventId)) saved.eventId = null;
      if (mode.options[0].text !== liveLabel()) mode.options[0].text = liveLabel();
      mode.value = saved.eventId === null ? "live" : "replay";
      slider.max = String(Math.max(0, data.events.length - 1));
      draw();
      // Keep the viewport mounted and restore in the same task, before paint.
      // Automatic updates also keep the user's current zoom instead of refitting.
      viewport.scrollLeft = scrollLeft; viewport.scrollTop = scrollTop;
      saved.scrollLeft = viewport.scrollLeft; saved.scrollTop = viewport.scrollTop;
    });
    draw();
    requestAnimationFrame(() => { if (root.isConnected) {
      if (!saved.manualZoom) setZoom(Math.max(.7, Math.min((viewport.clientWidth - 24) / layout.width, .9)), false);
      viewport.scrollLeft = saved.scrollLeft; viewport.scrollTop = saved.scrollTop;
    } });
  }
  const refresh = (root, options) => mounted.get(root)?.(options);
  window.AgentAdminGraph = {methodPaths, executionMethod, methodDescription, connectorDescription, render, refresh, project, summarizeGraph, layoutGraph, edgePath, statusLabel};
})();


