"use strict";
/* Read-only ontology research views. Planned capabilities are never call receipts. */
(() => {
  const APP = "ontology-cognitive-analyst";
  const AGENTS = new Set([APP, "situation-assessment-action", "rule-applicability-action", "tactics-research-action", "ontology-consistency-action"]);
  const snapshots = new WeakMap(), taskSessions = new Map();
  const arr = value => Array.isArray(value) ? value : [];
  const copy = value => JSON.parse(JSON.stringify(value));
  const isAgent = key => AGENTS.has(key);
  const unwrap = value => value?.productType === "CognitiveResearchReport" ? value : value?.report?.productType === "CognitiveResearchReport" ? value.report : null;
  const isReport = value => unwrap(value)?.schemaVersion === "ontology-cognitive.v1";
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const button = (text, handler, cls) => { const node = make("button", text, cls); node.type = "button"; node.onclick = handler; return node; };
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };
  const unique = (rows, key) => {
    const map = new Map(), duplicate = new Set();
    for (const value of arr(rows)) {
      if (!value || typeof value[key] !== "string" || !value[key]) continue;
      if (map.has(value[key])) duplicate.add(value[key]);
      map.set(value[key], value);
    }
    duplicate.forEach(id => map.delete(id));
    return map;
  };
  const validRef = ref => !!ref && ["runId", "artifactId", "artifactType"].every(k => typeof ref[k] === "string" && ref[k].trim()) && /^[a-f0-9]{64}$/.test(ref.contentHash || "");
  const supportLabel = value => ({supported:"有引文支持", disputed:"存在分歧", unknown:"依据未知", rejected:"已否决"}[value] || "状态未记录");
  const predicateLabel = value => ({supports:"支持", contradicts:"反证", "applies-to":"适用于", requires:"需要", "derived-from":"源于", "limited-by":"受限于"}[value] || value);

  function modelFor(value) {
    const report = unwrap(value) || {};
    const nodes = unique(report.ontologyView?.entities, "entityId"), claims = unique(report.claims, "claimId");
    const identities = unique(report.ontologyView?.relations, "relationId");
    const sourceRows = arr(report.ontologyView?.relations);
    const relations = sourceRows.filter(row => {
      if (!row || typeof row !== "object") return false;
      if (!identities.has(row.relationId) || !nodes.has(row.subjectId) || !nodes.has(row.objectId) || typeof row.predicateId !== "string" || !row.predicateId.trim()) return false;
      if (!["supported", "disputed", "unknown", "rejected"].includes(row.support)) return false;
      const refs = arr(row.claimIds);
      if (!refs.every(id => claims.has(id))) return false;
      if (!arr(row.conditionClaimIds).every(id => claims.has(id))) return false;
      if (row.support === "supported") return refs.length > 0 && refs.every(id => {
        const claim = claims.get(id);
        return claim.support === "supported" && claim.subjectId === row.subjectId && claim.predicateId === row.predicateId && claim.objectId === row.objectId && arr(claim.evidence).length > 0 &&
          claim.evidence.every(e => validRef(e.sourceArtifactRef) && typeof e.quote === "string" && e.quote.trim() && e.locator?.value);
      });
      return true;
    });
    return {report, nodes, claims, relations, omitted:sourceRows.length - relations.length};
  }

  function toBaseline(profile, validation = {}) {
    const kinds = {"sub-agent":"action", llm:"stage", tool:"tool", api:"stage", gate:"gate"};
    return {viewState:"proposed", status:"unregistered", validation:{accepted:validation.accepted === true, errors:arr(validation.errors)},
      graph:{schemaVersion:"execution-baseline.v1", graphType:"baseline", agentKey:profile.agentKey, profileVersion:profile.targetProfileVersion,
        engineMode:"cognitive-suite-v1", supportedModes:["fixed"], mode:"fixed-graph", researchPolicy:{enabled:false},
        nodes:arr(profile.nodes).map((node, index) => ({...node, name:node.name, sequence:index + 1,
          nodeType:kinds[node.executionKind] || "stage", plannerMode:"fixed", tools:arr(node.toolKeys), retryLimit:node.max_retries,
          inputContract:{description:"由父任务显式委派的已验证产物引用"}, outputContract:{description:node.outputType},
          failurePolicy:{action:"stop"}, expansion:{}})),
        edges:arr(profile.nodes).flatMap(node => arr(node.dependencies).map(from => ({from,to:node.id,kind:"dependency"}))),
        limits:{counters:profile.budgetCeiling,maxParallelism:profile.maxParallelism || 1}}};
  }
  function mountDesign(host, {design, agentKey, readiness, validation, api, notice}) {
    const profile = arr(design.profiles).find(p => p.agentKey === agentKey);
    if (!profile) return false;
    return window.AgentExecutionBaseline.mount(host, {baseline:toBaseline(profile, validation), readiness,
      canEdit:false, api, notice});
  }

  function mountReport(host, {report:value, runId, api, notice = () => {}}) {
    const model = modelFor(value), report = model.report;
    const root = make("section", undefined, "cognitive-report"); root.dataset.runId = runId || "";
    root.setAttribute("aria-label", "认知研究报告与证据图谱");
    snapshots.set(root, JSON.stringify(value));
    const heading = make("div", undefined, "cognitive-report-heading"), title = make("strong", "本体关系与研究依据");
    title.append(window.AgentAdminTips.create("图谱来自本次研究产物；有引文支持不等于已独立核实。点击对象或关系查看条件、主张和原文。", "info", "报告图谱"));
    heading.append(title, make("span", model.nodes.size + " 个对象 · " + model.relations.length + " 条关系 · " + arr(report.gaps).length + " 项缺口", "muted")); root.append(heading);
    if (report.summary) root.append(make("p", report.summary, "cognitive-report-summary"));
    const work = make("div", undefined, "cognitive-report-workspace"), canvas = make("section", undefined, "cognitive-report-canvas"), inspector = make("aside", undefined, "cognitive-report-inspector");
    inspector.setAttribute("aria-live", "polite");
    const selectClaims = ids => arr(ids).map(id => model.claims.get(id)).filter(Boolean);
    let selectionGeneration = 0;
    const relationControls = new Map();
    function evidenceView(evidence, target) {
      const row = make("section", undefined, "cognitive-evidence");
      row.append(make("blockquote", evidence.quote));
      row.append(make("small", "定位：" + (evidence.locator?.value || "未记录") + " · 取得：" + (evidence.retrievedAt || "未记录"), "muted"));
      if (validRef(evidence.sourceArtifactRef)) {
        const ref = evidence.sourceArtifactRef, original = make("div", undefined, "cognitive-source-original"); original.hidden = true;
        const open = button("查看保存的原文", async () => {
          if (!original.hidden) { original.hidden = true; open.textContent = "查看保存的原文"; return; }
          open.disabled = true;
          try {
            const rows = await api("/runs/" + encodeURIComponent(ref.runId) + "/artifacts");
            const source = arr(rows).find(item => item.artifact_id === ref.artifactId && item.content_hash === ref.contentHash);
            if (!source) throw Error("来源版本不匹配或当前账号不可读取。");
            const body = source.content_json || {};
            const text = typeof body === "string" ? body : body.markdown || body.text || body.content || body.body;
            if (typeof text !== "string" || !text.trim()) throw Error("该来源没有可展示的原文，请查看来源归档。");
            original.replaceChildren(make("pre", text)); original.hidden = false; open.textContent = "收起原文";
          } catch (error) { original.replaceChildren(make("p", error.message, "cognitive-missing")); original.hidden = false; }
          finally { open.disabled = false; }
        }, "cognitive-text-action");
        row.append(open, original);
      }
      target.append(row);
    }
    function showClaims(ids, target) {
      const selected = selectClaims(ids);
      if (!selected.length) { target.append(make("p", "当前选区没有已绑定主张，不能据此确认关系。", "cognitive-missing")); return; }
      for (const claim of selected) {
        const group = make("section", undefined, "cognitive-claim");
        group.append(make("span", supportLabel(claim.support), "cognitive-support"), make("p", claim.statement));
        if (arr(claim.conditions).length) group.append(make("p", "适用条件：" + claim.conditions.join("；"), "muted"));
        if (arr(claim.assumptions).length) group.append(make("p", "研究假设：" + claim.assumptions.join("；"), "muted"));
        if (!arr(claim.evidence).length) group.append(make("p", "尚无可定位引文。", "cognitive-missing"));
        arr(claim.evidence).forEach(e => evidenceView(e, group)); target.append(group);
      }
    }
    function inspectNode(node) {
      ++selectionGeneration;
      inspector.dataset.selectionKind = "node"; inspector.dataset.selectionId = node.entityId;
      relationControls.forEach(control => control.setAttribute("aria-pressed", "false"));
      inspector.replaceChildren(make("h3", node.label || node.entityId), make("p", "类别：" + node.typeId, "muted"));
      showClaims(node.claimIds, inspector);
      const related = model.relations.filter(edge => edge.subjectId === node.entityId || edge.objectId === node.entityId);
      if (related.length) {
        const list = make("section", undefined, "cognitive-related"); list.append(make("h4", "相关关系"));
        related.forEach(edge => list.append(button(predicateLabel(edge.predicateId) + " · " +
          (model.nodes.get(edge.subjectId === node.entityId ? edge.objectId : edge.subjectId)?.label || "对象"), () => inspectRelation(edge), "cognitive-related-item")));
        inspector.append(list);
      }
    }
    function inspectRelation(edge) {
      ++selectionGeneration;
      inspector.dataset.selectionKind = "relation"; inspector.dataset.selectionId = edge.relationId;
      relationControls.forEach((control, id) => control.setAttribute("aria-pressed", String(id === edge.relationId)));
      inspector.replaceChildren(make("h3", (model.nodes.get(edge.subjectId)?.label || edge.subjectId) + " · " + predicateLabel(edge.predicateId) + " · " + (model.nodes.get(edge.objectId)?.label || edge.objectId)), make("span", supportLabel(edge.support), "cognitive-support"));
      if (arr(edge.conditionClaimIds).length) {
        const conditions = make("section"); conditions.append(make("h4", "关系成立的条件")); showClaims(edge.conditionClaimIds, conditions); inspector.append(conditions);
      }
      showClaims(edge.claimIds, inspector);
    }
    if (model.nodes.size) {
      const toolbar = make("div", undefined, "cognitive-graph-tools"), zoomOut = button("−", () => {}), zoomIn = button("+", () => {}), fit = button("适应", () => {}), reset = button("100%", () => {}), zoomLabel = make("span", undefined, "muted");
      zoomOut.setAttribute("aria-label", "缩小本体图谱"); zoomIn.setAttribute("aria-label", "放大本体图谱");
      toolbar.append(zoomOut, zoomLabel, zoomIn, fit, reset);
      const viewport = make("div", undefined, "cognitive-report-viewport"), sizer = make("div", undefined, "cognitive-graph-sizer"), board = make("div", undefined, "cognitive-graph-board");
      viewport.tabIndex = 0; viewport.setAttribute("aria-label", "本体关系图，可缩放和平移");
      sizer.append(board); viewport.append(sizer); canvas.append(toolbar, viewport);
      const graph = {nodes:[...model.nodes.values()].map(n => ({id:n.entityId,group:"ontology"})), edges:model.relations.map(e => ({id:e.relationId,from:e.subjectId,to:e.objectId,predicate:e.predicateId})), groups:[{id:"ontology"}]};
      root.graphView = window.AgentGraph.adapt("ontology", graph, {runId});
      const geometry = window.AgentGraph.layoutGraph(graph, {direction:"horizontal"});
      const edges = svg("svg", {width:geometry.width,height:geometry.height,"aria-label":"关系"}); edges.classList.add("cognitive-graph-edges");
      // Render every path first and every relation control afterwards.  A
      // routed path may share a horizontal segment with another relation
      // (especially when one subject has multiple objects); keeping paths and
      // controls in separate layers prevents a later transparent stroke from
      // intercepting an earlier relation's target.
      const edgeLines = svg("g", {"aria-hidden":"true"}); edgeLines.classList.add("cognitive-graph-edge-lines");
      const edgeControls = svg("g", {"aria-label":"关系选择"}); edgeControls.classList.add("cognitive-graph-edge-controls");
      edges.append(edgeLines, edgeControls);
      for (const edge of model.relations) {
        const from = geometry.positions.get(edge.subjectId), to = geometry.positions.get(edge.objectId);
        const path = window.AgentGraph.edgePath(from, to);
        const vertical = from.x === to.x && to.y >= from.y + from.height;
        const center = vertical ? {x:from.x + from.width / 2, y:(from.y + from.height + to.y) / 2} :
          {x:(from.x + from.width + to.x) / 2, y:(from.y + from.height / 2 + to.y + to.height / 2) / 2};
        const label = (model.nodes.get(edge.subjectId)?.label || edge.subjectId) + " · " + predicateLabel(edge.predicateId) + " · " + (model.nodes.get(edge.objectId)?.label || edge.objectId);
        const group = svg("g", {tabindex:"0",role:"button","data-relation-id":edge.relationId,"aria-pressed":"false","aria-label":label + "，" + supportLabel(edge.support) + "，查看关系与证据"});
        const title = svg("title"); title.textContent = label + "；点击查看条件、主张与原文";
        const hit = svg("path", {d:path,fill:"none",stroke:"transparent","stroke-width":14}), line = svg("path", {d:path,fill:"none","stroke-width":1.5});
        hit.classList.add("cognitive-relation-hit");
        line.classList.add("cognitive-relation-line"); if (edge.support !== "supported") line.setAttribute("stroke-dasharray", "4 4");
        // A horizontal SVG path has zero geometry height even with a wide
        // stroke. Give every relation a real, visible button target, which
        // remains clickable with a mouse and focusable from the keyboard.
        const target = svg("rect", {x:center.x - 14,y:center.y - 14,width:28,height:28,rx:5}); target.classList.add("cognitive-relation-target");
        const marker = svg("circle", {cx:center.x,cy:center.y,r:8}); marker.classList.add("cognitive-relation-marker");
        const icon = svg("path", {d:`M${center.x},${center.y - 1} v5 M${center.x},${center.y - 4} v.2`}); icon.classList.add("cognitive-relation-icon");
        const lineGroup = svg("g", {"aria-hidden":"true"}); lineGroup.classList.add("cognitive-relation-line-group"); lineGroup.append(hit, line); edgeLines.append(lineGroup);
        group.append(title, target, marker, icon); group.onclick = () => inspectRelation(edge); group.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inspectRelation(edge); } }; edgeControls.append(group);
        relationControls.set(edge.relationId, group);
      }
      board.append(edges);
      for (const node of model.nodes.values()) {
        const position = geometry.positions.get(node.entityId), item = button("", () => inspectNode(node), "cognitive-graph-node");
        item.style.left = position.x + "px"; item.style.top = position.y + "px";
        item.append(make("strong", node.label || node.entityId), make("small", node.typeId, "muted"));
        item.setAttribute("aria-label", (node.label || node.entityId) + "，查看主张和证据"); board.append(item);
      }
      const camera = window.AgentGraph.createViewport({viewport,sizer,board,zoom:.9,autoFit:true,
        onChange:value => { zoomLabel.textContent = Math.round(value.zoom * 100) + "%"; }});
      camera.setSize({width:geometry.width,height:geometry.height}); camera.bindControls({minus:zoomOut,plus:zoomIn,value:zoomLabel,fit,reset});
      requestAnimationFrame(() => camera.fit({manual:false}));
      inspectNode(model.nodes.values().next().value);
    } else {
      root.classList.add("cognitive-report-empty");
      const empty = make("p", "本次报告尚未形成可展示的本体对象关系；请查看已展开的缺口与研究问题。", "cognitive-missing");
      empty.setAttribute("role", "status"); canvas.append(empty);
    }
    if (!model.nodes.size) inspector.hidden = true;
    if (model.omitted) canvas.append(make("p", model.omitted + " 条关系因标识、证据绑定或端点不完整而未展示。", "cognitive-missing"));
    work.append(canvas, inspector); root.append(work);
    const attention = arr(report.gaps).length + arr(report.conflicts).length;
    if (attention) {
      const details = make("details", undefined, "cognitive-attention"); details.open = !model.relations.length;
      details.append(make("summary", "缺口与冲突（" + attention + "）"));
      for (const gap of arr(report.gaps)) {
        const line = make("section"); line.append(make("strong", gap.description), make("p", gap.impact, "muted"));
        if (gap.suggestedNextStep) line.append(make("p", "补证方向：" + gap.suggestedNextStep)); details.append(line);
      }
      for (const conflict of arr(report.conflicts)) details.append(make("p", conflict.description)); root.append(details);
    }
    if (arr(report.decisionSupport?.unresolvedQuestions).length) {
      const details = make("details", undefined, "cognitive-attention"); details.append(make("summary", "未解决的研究问题"));
      report.decisionSupport.unresolvedQuestions.forEach(q => details.append(make("p", q))); root.append(details);
    }
    const versions = make("details", undefined, "cognitive-audit"); versions.append(make("summary", "快照与运行版本"));
    const versionData = {runId, profileVersion:report.provenance?.profileVersion, goalId:report.provenance?.goalId,
      ontologySnapshotRef:report.context?.ontologySnapshotRef, ontologySchemaRef:report.context?.ontologySchemaRef, ruleSetRef:report.context?.ruleSetRef};
    versions.append(make("pre", JSON.stringify(versionData, null, 2))); root.append(versions); host.append(root); return root;
  }

  const MAX_QUESTION_LENGTH = 2000;
  let activeTask = null, mountEpoch = 0, questionController = null, questionLimitState = null;
  function limitQuestion(enabled) {
    if (!enabled) {
      if (questionLimitState) questionLimitState.node.maxLength = questionLimitState.maxLength;
      questionLimitState = null; return;
    }
    const question = document.getElementById("question");
    if (!questionLimitState || questionLimitState.node !== question) {
      if (questionLimitState) questionLimitState.node.maxLength = questionLimitState.maxLength;
      questionLimitState = {node:question,maxLength:question.maxLength};
    }
    question.maxLength = MAX_QUESTION_LENGTH;
  }
  async function mountTask(picker, preview, options) {
    limitQuestion(true);
    questionController?.abort(); questionController = new AbortController();
    const epoch = ++mountEpoch, {agentKey, api, canOperate, notice = () => {}} = options;
    const submit = document.querySelector("#create-form > button[type=submit]");
    submit.disabled = true; preview.replaceChildren(); preview.hidden = true;
    const box = make("section", undefined, "agent-specific-panel cognitive-task-settings");
    const heading = make("div", undefined, "task-settings-heading"), title = make("h3", "文献研究设置");
    title.append(window.AgentAdminTips.create("研究问题最多 2000 字符。研究对象、规则和本体版本由服务器解析并冻结。未选择可用上下文时不创建运行。", "info", "研究上下文")); heading.append(title); box.append(heading); picker.replaceChildren(box);
    if (agentKey !== APP) {
      const link = make("a", "打开认知方案综合任务"); link.href = "#task=" + APP;
      box.append(make("p", "此专业 Agent 接收应用执行图委派的冻结材料。"), link);
      activeTask = {agentKey,setSubmitting:() => {submit.disabled = true;},getInput:() => {throw Error("请通过应用执行图调用专业 Agent。");}}; return;
    }
    const saved = taskSessions.get(agentKey) || {}; taskSessions.set(agentKey, saved);
    const select = make("select"), scope = make("textarea"); scope.rows = 3; scope.maxLength = 4000; scope.required = true;
    const contextLabel = make("label", "研究上下文"), scopeLabel = make("label", "本次研究范围"); contextLabel.append(select); scopeLabel.append(scope);
    const status = make("p", "正在读取可用上下文…", "muted"), issues = make("div", undefined, "cognitive-admission-issues"); issues.setAttribute("role", "status");
    box.append(contextLabel, scopeLabel, status, issues);
    let contexts = [], readiness = null, sending = false;
    const selected = () => contexts.find(row => row.contextId === select.value);
    const sync = () => {
      saved.contextId = select.value; saved.researchPurpose = scope.value;
      const questionValue = document.getElementById("question").value;
      submit.disabled = sending || !canOperate || readiness?.ready !== true || !selected() || !scope.value.trim() || !questionValue.trim() || questionValue.length > MAX_QUESTION_LENGTH;
    };
    const showSelection = () => {
      const row = selected();
      if (row) {
        scope.value = saved.researchPurpose || row.context?.researchPurpose || "";
        status.textContent = "已选 " + row.label + " · 来源产物 " + arr(row.sourceArtifactRefs).length + " · 能力产物 " + arr(row.capabilityArtifactRefs).length;
      } else status.textContent = "当前没有可用上下文，请先接入获准的本体与文献资产。";
      sync();
    };
    select.onchange = () => { saved.researchPurpose = ""; showSelection(); }; scope.oninput = sync;
    const question = document.getElementById("question"); question.addEventListener("input", sync, {signal:questionController.signal});
    activeTask = {agentKey, api, selected, scope, issues, setSubmitting(value) {sending = value; if (epoch === mountEpoch) sync();},
      getInput() {if (!selected()) throw Error("请选择可用的研究上下文。"); return {contextId:select.value,researchPurpose:scope.value.trim()};}};
    try {
      const data = await api("/cognitive-suite/context?agentKey=" + encodeURIComponent(agentKey));
      if (epoch !== mountEpoch) {question.removeEventListener("input", sync); return;}
      contexts = arr(data.contexts); readiness = data.readiness;
      if (!question.value.trim() && typeof data.defaultQuestion === "string") question.value = data.defaultQuestion;
      select.replaceChildren(...contexts.map(row => {const node = make("option", row.label); node.value = row.contextId; return node;}));
      if (contexts.some(row => row.contextId === saved.contextId)) select.value = saved.contextId;
      if (readiness?.ready !== true) arr(readiness?.issues).forEach(issue => issues.append(make("p", issue.message || "执行基线未就绪。", "cognitive-missing")));
      showSelection();
    } catch (error) {if (epoch === mountEpoch) {status.textContent = "研究上下文读取失败。"; issues.append(make("p", error.message, "cognitive-missing")); notice(error.message, true);}}
  }
  function install() {
    const original = window.AgentAdminTargets;
    if (!original || original.cognitiveInstalled) return;
    window.AgentAdminTargets = {...original,cognitiveInstalled:true,
      async mount(picker, preview, options) {
        if (isAgent(options.agentKey)) return mountTask(picker, preview, options);
        ++mountEpoch; questionController?.abort(); activeTask = null; limitQuestion(false); return original.mount(picker, preview, options);
      },
      getInput:() => activeTask ? activeTask.getInput() : original.getInput(),
      setSubmitting:value => activeTask ? activeTask.setSubmitting(value) : original.setSubmitting(value),
      refresh:() => activeTask ? Promise.resolve() : original.refresh()};
    document.getElementById("create-form")?.addEventListener("submit", async event => {
      if (document.getElementById("agent")?.value !== APP || !activeTask || activeTask.agentKey !== APP) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const form = event.currentTarget, task = activeTask;
      if (form.dataset.submitting === "true") return;
      try {
        const input = task.getInput(), rawQuestion = document.getElementById("question").value, question = rawQuestion.trim();
        if (!question) throw Error("请填写任务目标。");
        if (rawQuestion.length > MAX_QUESTION_LENGTH) throw Error("研究问题最多 2000 字符，请精简后重试。");
        form.dataset.submitting = "true"; task.setSubmitting(true); task.issues.replaceChildren();
        const budgetLimits = Object.fromEntries([["model_calls","model-budget"],["search_calls","search-budget"],["scrape_calls","scrape-budget"]].map(([key,id]) => [key,Number(document.getElementById(id).value)]));
        const admission = await task.api("/cognitive-suite/admission", {method:"POST",body:JSON.stringify({agentKey:APP,question,...input,budgetLimits})});
        if (admission.admissionStatus !== "accepted" || admission.dispatchAllowed !== true || !admission.input) {
          for (const missing of arr(admission.missingFields)) task.issues.append(make("p", missing.reason || "缺少必要上下文。", "cognitive-missing"));
          for (const hint of arr(admission.resolutionHints)) task.issues.append(make("p", typeof hint === "string" ? hint : hint.message || "请补齐上下文。", "muted"));
          if (!task.issues.childNodes.length) task.issues.append(make("p", admission.reason || "当前任务未通过准入。", "cognitive-missing"));
          return;
        }
        const result = await task.api("/runs", {method:"POST",body:JSON.stringify({agentKey:APP,input:admission.input})});
        if (!result.runId) throw Error("服务未返回运行标识，请检查执行监控后再重试。");
        location.hash = "#run=" + encodeURIComponent(result.runId);
      } catch (error) {task.issues.append(make("p", error.message, "cognitive-missing"));}
      finally {delete form.dataset.submitting; task.setSubmitting(false);}
    }, true);
  }
  window.AgentAdminCognitive = Object.freeze({isAgent,isReport,modelFor,toBaseline,mountDesign,mountReport,
    sameReport:(node, value) => snapshots.get(node) === JSON.stringify(value), install});
  if (typeof document !== "undefined") install();
})();
