"use strict";
/* Profile baselines share graph geometry with the observed-run viewer. */
(() => {
  const make = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  };
  const copy = value => JSON.parse(JSON.stringify(value));
  const counters = [["model_calls", "模型调用 / 次"], ["search_calls", "Firecrawl 搜索 / 次"],
    ["scrape_calls", "Firecrawl 抓取 / 次"], ["http_calls", "HTTP 请求 / 次"],
    ["tool_calls", "工具调用 / 次"], ["input_tokens", "输入 Token"], ["output_tokens", "输出 Token"],
    ["max_rounds", "最大轮次"], ["max_parallelism", "并行上限"], ["max_runtime_seconds", "超时 / 秒"]];
  const kindNames = {stage:"阶段", action:"Action", tool:"工具", gate:"门控", human_review:"人工审核"};
  const stateNames = {published:"已发布", testing:"测试基线", retired:"已停用", unregistered:"待登记"};
  const action = (label, callback, className) => {
    const button = make("button", label, className); button.type = "button"; button.onclick = callback; return button;
  };
  const tip = (text, title) => window.AgentAdminTips.create(text, "info", title);

  function compactGraph(graph) {
    const nodes = graph.nodes.map(node => ({...node, members:[node.id]})), edges = graph.edges.map(copy);
    // Collapse only unbranched chains; forks and joins retain their exact topology.
    let changed = true;
    while (changed) {
      changed = false;
      for (const source of [...nodes]) {
        const outgoing = edges.filter(edge => edge.from === source.id);
        if (outgoing.length !== 1) continue;
        const target = nodes.find(node => node.id === outgoing[0].to);
        if (!target || edges.filter(edge => edge.to === target.id).length !== 1) continue;
        if (edges.filter(edge => edge.to === source.id).length > 1 || edges.filter(edge => edge.from === target.id).length > 1) continue;
        source.members.push(...target.members);
        source.lastName = target.lastName || target.name;
        nodes.splice(nodes.indexOf(target), 1);
        edges.splice(edges.indexOf(outgoing[0]), 1);
        edges.forEach(edge => { if (edge.from === target.id) edge.from = source.id; });
        changed = true; break;
      }
    }
    return {nodes, edges};
  }

  function mount(holder, options) {
    let {baseline, documentValue, canEdit, api, notice, onPublished} = options;
    if (!baseline?.graph?.nodes?.length) {
      holder.append(make("p", "执行图基线暂未读取。", "profile-empty-state")); return;
    }
    const compact = options.compact === true;
    const proposed = baseline.viewState === "proposed";
    const root = make("div", undefined, "baseline-view" + (compact ? " baseline-view--compact" : ""));
    root.dataset.graphType = "baseline";
    root.dataset.baselineState = proposed ? "proposed" : "registered";
    const graph = copy(baseline.graph);
    root.graphView = window.AgentGraph.adapt("baseline", baseline, {baselineId:baseline.baselineId, graphHash:baseline.graphHash});
    root.dataset.graphKind = "baseline";
    const meta = make("div", undefined, "baseline-meta");
    meta.append(make("span", baseline.baselineVersion ? "基线 v" + baseline.baselineVersion : "基线未登记", "baseline-version"),
      make("span", proposed ? "草案 · 未发布" : stateNames[baseline.status] || baseline.status, "baseline-state"),
      make("span", graph.nodes.length + " 节点 · " + graph.edges.length + " 依赖"),
      make("span", graph.engineMode + " · " + (graph.researchPolicy?.enabled ? "支持受限研究" : "仅固定执行"), "baseline-mode"));
    if (baseline.graphHash) {
      const hash = make("span", "hash " + baseline.graphHash.slice(0, 10) + "…", "baseline-hash");
      hash.title = baseline.graphHash; meta.append(hash);
    }
    if (proposed) meta.append(make("span", "目标 Profile " + graph.profileVersion, "baseline-mode"));
    const readiness = baseline.readiness || options.readiness;
    const validation = make("span", proposed ? "执行器待实现" : readiness ? (readiness.ready ? "基线已就绪" : "基线未就绪") :
      (baseline.validation.accepted ? "结构校验通过" : "结构校验失败"), "baseline-validation");
    validation.dataset.valid = String(readiness ? readiness.ready : baseline.validation.accepted); meta.append(validation);
    meta.append(tip("固定模式按节点依赖执行，编号仅用于排列。节点配额是共享总预算内的上限，不相加为新预算。研究模式另按冻结的研究授权生成计划。", "执行基线"));
    root.append(meta);
    if (readiness && !readiness.ready) {
      meta.append(tip("正式运行已阻止。结构校验通过不能证明角色阶段和执行器已实现。", "基线未就绪"));
      if (!compact) {
        const details = make("details"), summary = make("summary", "查看未就绪项"), list = make("ul", undefined, "baseline-errors");
        const grouped = new Map();
        (readiness.issues || []).forEach(issue => {
          if (!grouped.has(issue.code)) grouped.set(issue.code, {message:issue.message, names:[]});
          if (issue.nodeName) grouped.get(issue.code).names.push(issue.nodeName);
        });
        grouped.forEach(issue => list.append(make("li", issue.message + (issue.names.length ? " " + issue.names.join("、") : ""))));
        details.append(summary, list); root.append(details);
      }
    }
    if (!baseline.validation.accepted) {
      const errors = make("ul", undefined, "baseline-errors");
      baseline.validation.errors.forEach(error => errors.append(make("li", error.message))); root.append(errors);
    }
    const toolbar = make("div", undefined, "baseline-toolbar"), zoomTools = make("div", undefined, "baseline-zoom-tools");
    const viewport = make("div", undefined, "baseline-viewport"), sizer = make("div", undefined, "baseline-sizer"), board = make("div", undefined, "baseline-board");
    viewport.tabIndex = 0; viewport.setAttribute("aria-label", compact ? "执行基线缩略图；按住鼠标左键拖动画布平移" : "执行基线 DAG 画布；按住鼠标左键拖动画布平移");
    viewport.title = "拖动画布平移；Ctrl / ⌘ + 滚轮缩放；普通滚轮滚动";
    sizer.append(board); viewport.append(sizer);
    const workspace = make("div", undefined, "baseline-workspace");
    const inspector = make("aside", undefined, "baseline-inspector"); inspector.hidden = true;
    inspector.setAttribute("aria-label", "基线节点配置");
    workspace.append(viewport, inspector);
    const footer = make("div", undefined, "baseline-footer");
    footer.append(make("span", "全部前置完成后放行 · 并行分支按配额执行"),
      tip("固定模式与任务中的动态计划、实际运行记录分别保存。补证按轮次记录，不向 DAG 添加回边。", "依赖与迭代"));
    const status = make("p", undefined, "baseline-editor-status"); status.setAttribute("role", "status"); status.hidden = true;
    let selectedId = null, editing = false, draftDocument = null, savedDraft = null, checked = null, dirty = false;
    // The compact overview should fit its preview automatically. The full
    // baseline opens at a readable scale and keeps horizontal scrolling
    // available; fitting all stages into one viewport makes an 11-node plan
    // too small to read.
    let zoom = compact ? 1 : .8, layout = null;
    const camera = window.AgentGraph.createViewport({viewport,sizer,board,zoom,centerY:true,padding:28,autoFit:compact ? "width" : false,
      onChange:value => { zoom = value.zoom; zoomValue.textContent = Math.round(zoom * 100) + "%"; minus.disabled = zoom <= .1; plus.disabled = zoom >= 2; }});
    const zoomValue = action("100%", () => camera.reset()); zoomValue.setAttribute("aria-label", "恢复原始比例");
    const minus = action("−", () => setZoom(zoom - .1)), plus = action("+", () => setZoom(zoom + .1));
    minus.setAttribute("aria-label", "缩小基线图"); plus.setAttribute("aria-label", "放大基线图");
    zoomTools.append(minus, zoomValue, plus, action("适应宽度", () => fit()));
    if (!compact) toolbar.append(zoomTools);
    const edit = action("编辑基线", () => {
      if (editing && dirty) { showStatus("请先保存草稿；草稿可在管理设置中继续编辑。", true); return; }
      editing = !editing;
      if (editing) {
        draftDocument = copy(documentValue);
        const parts = draftDocument.definition.current_version.split(".");
        draftDocument.definition.current_version = parts.length === 3 && parts.every(part => /^\d+$/.test(part))
          ? `${parts[0]}.${parts[1]}.${Number(parts[2]) + 1}` : draftDocument.definition.current_version + ".1";
      }
      edit.textContent = editing ? "结束编辑" : "编辑基线";
      save.hidden = !editing; verify.hidden = !editing; publish.hidden = true;
      if (selectedId) inspect(graph.nodes.find(node => node.id === selectedId));
      else inspect(graph.nodes[0]);
    });
    const save = action("保存草稿", () => runAction(saveDraft));
    const verify = action("校验与差异", () => runAction(validateDraft));
    const publish = action("发布新版本", () => runAction(publishDraft), "primary-action");
    save.hidden = verify.hidden = publish.hidden = true;
    if (canEdit && !compact && documentValue?.models?.config_json) toolbar.append(edit, save, verify, publish);
    if (!compact) root.append(toolbar);
    root.append(workspace, footer, status); holder.append(root);
    const detailSection = (title, value) => {
      const details = make("details", undefined, "baseline-contract"); details.append(make("summary", title), make("pre", JSON.stringify(value, null, 2))); return details;
    };
    function showStatus(text, error = false) { status.hidden = false; status.textContent = text; status.dataset.error = String(error); }
    async function runAction(callback) {
      for (const button of [save, verify, publish]) button.disabled = true;
      try { await callback(); } catch (error) { showStatus(error.message, true); notice?.(error.message, true); }
      finally { for (const button of [save, verify, publish]) button.disabled = false; }
    }
    function markDirty() { dirty = true; checked = null; publish.hidden = true; showStatus("本地修改尚未保存；当前生效基线继续使用原版本。"); }
    function policyFor(node) {
      const config = draftDocument.models.config_json;
      config.executionBaseline ??= {nodePolicies:{}};
      config.executionBaseline.nodePolicies ??= {};
      return config.executionBaseline.nodePolicies[node.id] ??= {};
    }
    function inspect(node) {
      if (!node || compact) return;
      selectedId = node.id; inspector.hidden = false; workspace.classList.add("has-inspector");
      board.querySelectorAll(".baseline-node").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.nodeId === node.id)));
      inspector.replaceChildren();
      const heading = make("div", undefined, "baseline-inspector-heading");
      const close = action("×", () => { inspector.hidden = true; workspace.classList.remove("has-inspector"); selectedId = null; });
      close.setAttribute("aria-label", "关闭节点配置");
      heading.append(make("h4", node.name), close); inspector.append(heading);
      const detail = make("dl", undefined, "baseline-node-details");
      for (const [label, value] of [["类型", kindNames[node.nodeType] || node.nodeType], ["执行者", graph.agentKey],
        ...(node.executionKind ? [["配置方式", {llm:"LLM 推理",tool:"工具调用",api:"固定 API / 内部处理"}[node.executionKind] || node.executionKind],
          ["实现状态", readiness?.issues?.some(issue => issue.code === "executor-not-implemented" && issue.nodeKey === node.id) ? "待实现" : "以运行准入检查为准"]] : []),
        ["规划", node.plannerMode === "expandable" ? "受限展开" : "固定节点"], ["要求", node.required ? "必需" : "可选"],
        ["前置", graph.edges.filter(edge => edge.to === node.id).map(edge => graph.nodes.find(item => item.id === edge.from)?.name || edge.from).join("、") || "入口"],
        ["工具", node.tools.join("、") || "未绑定"], ["重试", node.retryLimit + " 次"]]) {
        const row = make("div"); row.append(make("dt", label), make("dd", value)); detail.append(row);
      }
      inspector.append(detail);
      if (node.executionKind === "llm" && graph.modelConfiguration?.modelName) {
        inspector.append(make("p", "配置模型：" + graph.modelConfiguration.modelName + " · 尚未执行", "muted"));
      }
      if(node.skillBindings?.length) window.AgentSkillUI.inspect(inspector,{bindings:node.skillBindings});
      if (node.description) { const line = make("div", undefined, "baseline-node-description"); line.append(make("span", "职责"), tip(node.description, node.name)); inspector.append(line); }
      inspector.append(detailSection("输入契约", node.inputContract), detailSection("输出与验收契约", node.outputContract));
      if (node.completionCriteria?.length) {
        const criteria = make("ul"); node.completionCriteria.forEach(text => criteria.append(make("li", text)));
        inspector.append(make("h5", "完成标准"), criteria);
      }
      if (node.condition && node.condition !== "always") inspector.append(make("p", "触发条件：" + node.condition, "muted"));
      const budgetTitle = make("h5", "资源上限");
      budgetTitle.append(tip("按工具和模型的实际调用计数。搜索、抓取和 HTTP 分别扣减；这里不把调用次数当作供应商计费 credits。节点共享 Agent 总预算。", "资源计量")); inspector.append(budgetTitle);
      const budgets = make("div", undefined, "baseline-budget-grid");
      const localPolicy = editing ? policyFor(node) : {};
      for (const [key, label] of counters) {
        const row = make("label", label);
        const value = localPolicy.budget?.[key] ?? node.budget[key];
        if (editing) {
          const input = make("input"); input.type = "number"; input.min = ["max_parallelism", "max_runtime_seconds"].includes(key) ? "1" : "0"; input.step = "1";
          input.value = value ?? 0; input.dataset.budgetKey = key; input.setAttribute("aria-label", label);
          input.oninput = () => {
            const parsed = input.value === "" ? null : Number(input.value);
            if (parsed === null || (Number.isInteger(parsed) && parsed >= Number(input.min || 0))) {
              localPolicy.budget ??= {};
              if (parsed === null) delete localPolicy.budget[key]; else localPolicy.budget[key] = parsed;
              markDirty();
            }
          };
          row.append(input);
        } else row.append(make("strong", value === undefined ? "未配置" : value));
        budgets.append(row);
      }
      inspector.append(budgets);
      if (editing) {
        const step = (draftDocument.steps || []).find(value => value.step_key === node.id);
        const dependencies = make("fieldset", undefined, "baseline-dependency-picker"); dependencies.append(make("legend", "前置依赖"));
        if (!step) {
          dependencies.append(make("p", "该节点来自研究授权域，计划节点由运行时按冻结的研究策略生成。", "muted"));
        }
        for (const candidate of step ? graph.nodes.filter(item => item.id !== node.id) : []) {
          const label = make("label", undefined, "check"), input = make("input"); input.type = "checkbox"; input.value = candidate.id;
          input.checked = step.dependencies.includes(candidate.id);
          input.onchange = () => {
            step.dependencies = [...dependencies.querySelectorAll("input:checked")].map(input => input.value);
            graph.edges = graph.edges.filter(edge => edge.to !== node.id);
            graph.edges.push(...step.dependencies.map(from => ({from,to:node.id,kind:"dependency"})));
            markDirty(); draw();
          };
          label.append(input, document.createTextNode(candidate.name)); dependencies.append(label);
        }
        inspector.append(dependencies);
      }
      const loops = graph.loopPolicies.filter(loop => loop.return_step_key === node.id);
      if (loops.length) inspector.append(detailSection("补证与收敛规则", loops));
    }
    function setZoom(value, manual = true, anchor = null) { camera.setZoom(value,{manual,anchor}); }
    function fit() { camera.fit({axis:"width"}); }
    function draw() {
      const display = compact ? compactGraph(graph) : graph;
      const data = {nodes:display.nodes.map(node => ({...node,group:"baseline"})),edges:display.edges,groups:[{id:"baseline"}]};
      layout = window.AgentGraph.layoutGraph(data, {direction:"horizontal"}); board.replaceChildren();
      // Baselines use a compact rail; keep a small top breathing room for the
      // reading cue instead of reserving the full lifecycle heading band.
      layout.positions.forEach(position => {position.y -= 16;}); layout.height -= 16;
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.classList.add("baseline-edges");
      svg.setAttribute("width", layout.width); svg.setAttribute("height", layout.height);
      const defs = document.createElementNS(svg.namespaceURI, "defs");
      const marker = document.createElementNS(svg.namespaceURI, "marker");
      const markerId = "baseline-arrow-" + Math.random().toString(36).slice(2);
      marker.id = markerId; marker.setAttribute("viewBox", "0 0 8 8"); marker.setAttribute("refX", "7");
      marker.setAttribute("refY", "4"); marker.setAttribute("markerWidth", "6"); marker.setAttribute("markerHeight", "6");
      marker.setAttribute("orient", "auto-start-reverse");
      const arrow = document.createElementNS(svg.namespaceURI, "path"); arrow.setAttribute("d", "M 0 0 L 8 4 L 0 8 z");
      arrow.setAttribute("fill", "#a6b7bc"); marker.append(arrow); defs.append(marker); svg.append(defs);
      for (const edge of display.edges) {
        const source = layout.positions.get(edge.from), target = layout.positions.get(edge.to); if (!source || !target) continue;
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("d", window.AgentGraph.edgePath(source, target)); path.dataset.from = edge.from; path.dataset.to = edge.to;
        path.setAttribute("marker-end", `url(#${markerId})`);
        svg.append(path);
      }
      board.append(svg); board.style.width = layout.width + "px"; board.style.height = layout.height + "px";
      for (const node of display.nodes) {
        const pos = layout.positions.get(node.id);
        const item = action("", () => compact ? options.onOpen?.(node.id) : inspect(graph.nodes.find(value => value.id === node.id)), "baseline-node");
        item.dataset.nodeId = node.id; item.dataset.nodeType = node.nodeType; item.setAttribute("aria-pressed", String(selectedId === node.id));
        Object.assign(item.style,{left:pos.x+"px",top:pos.y+"px",width:pos.width+"px",height:pos.height+"px"});
        const icon = make("span", undefined, "future-war-stage-icon " + (node.nodeType === "human_review" ? "scope-icon" : node.plannerMode === "expandable" ? "imagine-icon" : "execute-icon"));
        icon.setAttribute("aria-hidden","true");
        const label = make("span", node.members?.length > 1 ? node.name + " … " + node.lastName : node.name, "baseline-node-label");
        const title = make("span", undefined, "baseline-node-heading"); title.append(icon, label);
        const members = (node.members || [node.id]).map(id => graph.nodes.find(n => n.id === id)).filter(Boolean);
        const content = "基线配置，尚不代表已执行。模型预算是上限，不代表节点使用了大模型。\n\n" + members.map(n => n.name + "\n" +
          (n.nodeType === "human_review" ? "配置审核门禁；自动草稿模式下可仅做发布准备，不代表人工审阅完成。" :
           n.plannerMode === "expandable" ? "受限研究编排；实际调用以运行记录为准。" : "固定编排节点；实际调用以运行记录为准。") +
          (n.executionKind ? "\n配置方式：" + ({llm:"LLM 推理",tool:"工具调用",api:"固定 API / 内部处理"}[n.executionKind] || n.executionKind) : "") +
          (n.executionKind === "llm" && graph.modelConfiguration?.modelName ? "\n配置模型：" + graph.modelConfiguration.modelName : "") +
          (n.executorKey ? "\n配置处理器：" + n.executorKey : "") +
          (proposed ? "\n状态：草案未发布，执行器待实现。" : "") +
          (n.childAgentKey ? "\n配置子 Agent：" + n.childAgentKey : "") +
          (n.tools?.length ? "\n配置工具：" + n.tools.join("、") : "")).join("\n\n");
        const methodTip = tip(content, "执行方式配置 · " + node.name);
        methodTip.classList.add("dag-call-tip", "baseline-method-tip"); methodTip.dataset.baselineMethod = node.id;
        const methodSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        methodSvg.setAttribute("viewBox", "0 0 16 16"); methodSvg.setAttribute("aria-hidden", "true");
        const methodPath = document.createElementNS(methodSvg.namespaceURI, "path");
        const delegated = members.every(n => n.childAgentKey);
        const plannedKind = members.every(n => n.executionKind === members[0].executionKind) ? members[0].executionKind : null;
        const methodLabel = delegated ? "Sub Agent" : plannedKind ? ({llm:"LLM",tool:"Tool",api:"API"}[plannedKind] || "配置") : "配置";
        methodPath.setAttribute("d", window.AgentAdminGraph.methodPaths[delegated ? "sub-agent" : plannedKind === "llm" ? "llm" : members.every(n => n.nodeType === "human_review") ? "input" : "fixed"]); methodSvg.append(methodPath);
        methodTip.replaceChildren(methodSvg, make("span", methodLabel));
        const tipWidth = delegated ? 66 : plannedKind ? 42 : 38;
        title.style.paddingRight = tipWidth + "px";
        Object.assign(methodTip.style, {width:tipWidth+"px",left:pos.x + pos.width - tipWidth - 5 + "px", top:pos.y + 5 + "px"});
        methodTip.addEventListener("pointerdown", event => event.stopPropagation());
        const meta = make("span", node.members?.length > 1 ? node.members.length + " 个连续节点" :
          proposed && node.executionKind ? ({llm:"推理",tool:"工具",api:"接口"}[node.executionKind] || node.executionKind) + " · 待实现" :
          (kindNames[node.nodeType] || node.nodeType) + " · " + (node.plannerMode === "expandable" ? "受限展开" : "固定"), "baseline-node-meta");
        item.append(title,meta); board.append(item, methodTip);
        const skillMark=window.AgentSkillUI.marker(members.flatMap(n=>n.skillBindings||[]),[],pos,true); if(skillMark)board.append(skillMark);
      }
      camera.setSize(layout);
      if (!compact) {
        // The graph is a reading rail, not a full-page canvas. Size the rail
        // to the number of lanes so a one-line plan does not create an empty
        // half-screen panel while branched plans still have room to breathe.
        const railHeight = Math.max(240, Math.min(320, layout.height * .58 + 60));
        viewport.style.height = railHeight + "px";
        setZoom(zoom,false);
        camera.apply();
      }
    }
    async function saveDraft() {
      if (!editing || !draftDocument) return;
      if (!dirty && savedDraft) { showStatus("草稿已保存。"); return; }
      const body = {document:draftDocument,reason:"更新执行基线节点依赖与资源配额"};
      savedDraft = savedDraft ? await api("/config-drafts/" + encodeURIComponent(savedDraft.draftId),
        {method:"PUT",body:JSON.stringify({...body,expectedRevision:savedDraft.revision})}) :
        await api("/config-profiles/" + encodeURIComponent(graph.agentKey) + "/drafts", {method:"POST",body:JSON.stringify(body)});
      dirty = false; checked = null; publish.hidden = true;
      showStatus("草稿 r" + savedDraft.revision + " 已保存 · 目标 Profile " + draftDocument.definition.current_version);
    }
    async function validateDraft() {
      if (dirty || !savedDraft) await saveDraft();
      checked = await api("/config-drafts/" + encodeURIComponent(savedDraft.draftId) + "/validate",
        {method:"POST",body:JSON.stringify({revision:savedDraft.revision})});
      publish.hidden = !checked.valid || checked.liveChanged || checked.dependenciesChanged;
      showStatus(checked.valid ? "校验通过 · " + checked.diff.length + " 项变更；发布后仅影响新运行。" :
        checked.errors.map(error => error.path + "：" + error.message).join("\n"), !checked.valid);
      root.querySelector(".baseline-draft-diff")?.remove();
      const diff = make("details", undefined, "baseline-draft-diff"); diff.open = true; diff.append(make("summary", "发布差异"));
      const list = make("dl"); for (const item of checked.diff) { const row = make("div"); row.append(make("dt",item.path), make("dd",JSON.stringify(item.before) + " → " + JSON.stringify(item.after))); list.append(row); }
      diff.append(list); root.append(diff);
    }
    async function publishDraft() {
      if (dirty || !checked?.valid || checked.liveChanged || checked.dependenciesChanged) throw Error("请重新保存并校验当前草稿。");
      await api("/config-drafts/" + encodeURIComponent(savedDraft.draftId) + "/publish", {method:"POST",body:JSON.stringify({
        expectedRevision:savedDraft.revision,idempotencyKey:"baseline-" + crypto.randomUUID(),reason:"发布已审阅的执行图基线与节点配额"})});
      dirty = false; showStatus("执行基线已发布，新任务将冻结此版本。"); await onPublished?.();
    }
    if (!compact) root.addEventListener("keydown",event => {if(event.key === "Escape" && !editing){inspector.hidden=true;workspace.classList.remove("has-inspector");selectedId=null;}});
    draw();
    if (compact) { viewport.style.maxHeight = "290px"; viewport.style.height = "260px"; }
    root.selectNode = id => inspect(graph.nodes.find(node => node.id === id));
    return root;
  }
  window.AgentExecutionBaseline = {mount, compactGraph};
})();
