"use strict";
/* Domain assertions are the report graph. Runtime dependencies are not facts. */
(() => {
  const types = {system:"体系", capability:"能力", technology:"技术", constraint:"约束", context:"情境", outcome:"结果"};
  const predicates = {"depends-on":"依赖", constrains:"约束", enables:"支持形成", changes:"影响", "part-of":"组成", "associated-with":"相关于"};
  const epistemicLabels = {"source-claim":"来源声称", "analytical-inference":"分析推断", correlation:"相关关系", "causal-hypothesis":"因果假设", "conditional-hypothesis":"条件性假设"};
  const epistemicLabel = row => epistemicLabels[row?.epistemicKind || row?.semantics?.kind] || "条件性假设";
  const sessions = new WeakMap();
  const arr = value => Array.isArray(value) ? value : [];
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    return node;
  };
  const uniqueMap = (rows, key) => {
    const map = new Map(), duplicates = new Set();
    for (const row of arr(rows)) {
      const id = key(row);
      if (!id) continue;
      if (map.has(id)) duplicates.add(id);
      map.set(id, row);
    }
    for (const id of duplicates) map.delete(id);
    return map;
  };

  function modelFor(report) {
    const ontology = report.ontology;
    const nodes = uniqueMap(ontology?.nodes, row => row.id);
    const evidence = uniqueMap(report.evidence, row => String(row.evidenceId || row.claimId || ""));
    const findings = uniqueMap(report.findings, row => row.findingId || row.perspectiveKey);
    const raw = arr(ontology?.assertions), identities = uniqueMap(raw, row => row.id);
    const relations = raw.filter(row => {
      const finding = findings.get(row.findingId || row.perspectiveKey);
      const refs = new Set(arr(finding?.supportingEvidence).map(item => String(item.evidenceId)));
      return ontology.schemaVersion === "1.0" && identities.has(row.id) && nodes.has(row.id) &&
        Object.hasOwn(predicates, row.predicate) &&
        Object.hasOwn(types, nodes.get(row.subject)?.type) && Object.hasOwn(types, nodes.get(row.object)?.type) &&
        finding?.status === "candidate" && row.truthStatus === "unverified" &&
        row.supportReview?.verdict === "supported" && arr(row.evidenceIds).length > 0 &&
        row.evidenceIds.every(id => evidence.has(String(id)) && refs.has(String(id)));
    });
    return {nodes, evidence, findings, relations, omitted:raw.length - relations.length};
  }

  function layoutFor(nodes, relations) {
    // Collapse strongly connected components for layout only. Cyclic domain
    // relations remain visible; the analysis graph is not required to be a DAG.
    const adjacent = new Map(nodes.map(node => [node.id, []]));
    for (const row of relations) adjacent.get(row.subject).push(row.object);
    const indexes = new Map(), low = new Map(), stack = [], onStack = new Set(), components = [];
    let next = 0;
    function visit(id) {
      indexes.set(id, next); low.set(id, next++); stack.push(id); onStack.add(id);
      for (const target of adjacent.get(id)) {
        if (!indexes.has(target)) { visit(target); low.set(id, Math.min(low.get(id), low.get(target))); }
        else if (onStack.has(target)) low.set(id, Math.min(low.get(id), indexes.get(target)));
      }
      if (low.get(id) === indexes.get(id)) {
        const group = []; let member;
        do { member = stack.pop(); onStack.delete(member); group.push(member); } while (member !== id);
        components.push(group.reverse());
      }
    }
    for (const node of nodes) if (!indexes.has(node.id)) visit(node.id);
    const componentOf = new Map();
    components.forEach((group, index) => group.forEach(id => componentOf.set(id, index)));
    const edges = components.map(() => new Set()), incoming = components.map(() => 0), rank = components.map(() => 0);
    for (const row of relations) {
      const from = componentOf.get(row.subject), to = componentOf.get(row.object);
      if (from !== to && !edges[from].has(to)) { edges[from].add(to); incoming[to]++; }
    }
    const queue = incoming.flatMap((count, index) => count === 0 ? [index] : []);
    for (let index = 0; index < queue.length; index++) {
      const from = queue[index];
      for (const to of edges[from]) {
        rank[to] = Math.max(rank[to], rank[from] + 1);
        if (--incoming[to] === 0) queue.push(to);
      }
    }
    // Give cyclic components an internal shape instead of stacking every
    // member in one vertical line. This keeps a cycle readable while the
    // condensation graph still supplies the left-to-right ordering.
    const groupsByRank = new Map();
    components.forEach((group, component) => {
      const column = rank[component];
      if (!groupsByRank.has(column)) groupsByRank.set(column, []);
      groupsByRank.get(column).push({component, group});
    });
    const componentSize = group => {
      const count = group.length;
      if (count <= 1) return {width:168, height:76, rows:1};
      if (count === 3) return {width:608, height:196, rows:2};
      const cols = count === 3 ? 3 : Math.min(3, Math.ceil(Math.sqrt(count)));
      const rows = Math.ceil(count / cols);
      return {width:168 + (cols - 1) * 220, height:76 + (rows - 1) * 120, rows};
    };
    const columnSize = new Map();
    for (const [column, groups] of groupsByRank) columnSize.set(column, Math.max(...groups.map(item => componentSize(item.group).width)));
    const columnX = new Map();
    let cursorX = 44;
    for (const column of [...groupsByRank.keys()].sort((a, b) => a - b)) {
      columnX.set(column, cursorX);
      cursorX += columnSize.get(column) + 104;
    }
    const positions = new Map();
    let maxHeight = 0;
    for (const [column, groups] of [...groupsByRank.entries()].sort((a, b) => a[0] - b[0])) {
      let cursorY = 42;
      const colWidth = columnSize.get(column);
      for (const {group} of groups) {
        const size = componentSize(group), baseX = columnX.get(column) + (colWidth - size.width) / 2;
        group.forEach((id, index) => {
          let col = 0, row = 0;
          if (group.length === 3) {
            // Triangle: one node above, two below. It makes the common
            // three-node feedback loop legible without inventing a direction.
            [col, row] = [[1, 0], [0, 1], [2, 1]][index];
          } else {
            const cols = group.length <= 1 ? 1 : Math.min(3, Math.ceil(Math.sqrt(group.length)));
            col = index % cols; row = Math.floor(index / cols);
          }
          positions.set(id, {x:baseX + col * 220, y:cursorY + row * 120});
        });
        cursorY += size.height + 62;
      }
      maxHeight = Math.max(maxHeight, cursorY);
    }
    const width = Math.max(300, cursorX - 104 + 44), height = Math.max(190, maxHeight + 12);
    return {positions, width, height};
  }

  function mount(destination, {report, sourceBlock,onReviewObject}) {
    const model = modelFor(report), root = make("section", undefined, "report-analysis-graph");
    root.setAttribute("aria-label", "本体图谱分析");
    root.dataset.graphKind = "ontology";
    root.graphView = window.AgentGraph.adapt("ontology", {nodes:[...model.nodes.values()],edges:model.relations}, {runId:report.runId,contentHash:report.contentHash});
    const header = make("div", undefined, "report-graph-heading"), title = make("h3", "本体图谱分析");
    title.append(window.AgentAdminTips.create("节点表示本次报告中的本体对象，箭头表示有引文复核记录的关系。点击关系可区分来源声称、分析推断与假设；引文支持不等于事实核验。图上的位置不表示重要性。超出画布的内容可滚动或拖动查看。", "info", "图谱阅读方法"));
    header.append(title);
    root.append(header); destination.append(root);
    if (!model.relations.length) {
      const text = !report.ontology ? "这份历史报告未保存本体对象和关系，无法还原分析图。重新研判后，报告可展示有证据绑定的关系。" : "本次尚无满足证据引用与关系复核要求的本体关系。请查看下方的候选判断和证据缺口。";
      root.append(make("p", text, "report-graph-unavailable"));
      if (model.omitted) root.append(make("p", model.omitted + " 条关系未通过展示校验，未绘入图谱。", "muted"));
      return {hasGraph:false, relationIds:new Set()};
    }

    const perspectives = uniqueMap(report.perspectives, row => row.key);
    for (const row of model.relations) if (!perspectives.has(row.perspectiveKey)) perspectives.set(row.perspectiveKey, {key:row.perspectiveKey, name:row.perspectiveKey});
    const filter = make("select"); filter.setAttribute("aria-label", "图谱研判视角");
    const all = make("option", "全部视角"); all.value = "all"; filter.append(all);
    for (const item of perspectives.values()) { const opt = make("option", item.name || item.key); opt.value = item.key; filter.append(opt); }
    filter.value = model.relations[0].perspectiveKey;
    const count = make("span", undefined, "report-graph-count"); count.setAttribute("aria-live", "polite");
    const tools = make("div", undefined, "report-graph-tools"), fit = make("button", "适应画布"), minus = make("button", "−"), plus = make("button", "+");
    for (const button of [fit, minus, plus]) button.type = "button";
    minus.setAttribute("aria-label", "缩小本体图谱"); plus.setAttribute("aria-label", "放大本体图谱");
    tools.append(fit, minus, plus); header.append(filter, count, tools);
    const workspace = make("div", undefined, "report-graph-workspace");
    const viewport = make("div", undefined, "report-graph-viewport"); viewport.tabIndex = 0; viewport.setAttribute("aria-label", "本体对象与关系画布，可拖动空白区域或滚动查看");
    const sizer = make("div", undefined, "report-graph-sizer"), board = make("div", undefined, "report-graph-board");
    sizer.append(board); viewport.append(sizer);
    const inspector = make("aside", undefined, "report-graph-inspector"); inspector.setAttribute("aria-label", "关系判断与证据详情");
    workspace.append(viewport, inspector); root.append(workspace);
    const note = make("div", undefined, "report-graph-note");
    note.append(make("span", "虚线：引文已复核，事实尚未核验"), make("span", "同名对象未经身份核对不合并")); root.append(note);
    if (model.omitted) note.append(make("span", model.omitted + " 条关系未通过展示校验"));
    let visible = [], selected = null, layout = null;
    const camera = window.AgentGraph.createViewport({viewport,sizer,board,centerX:true,centerY:true,autoFit:"both"});
    const edgeElements = new Map(), nodeElements = new Map();
    const labelFor = id => model.nodes.get(id)?.label || id;
    const relationLabel = row => labelFor(row.subject) + " · " + predicates[row.predicate] + " · " + labelFor(row.object);

    function addList(label, values) {
      const entries = arr(values).filter(value => typeof value === "string" && value.trim());
      if (!entries.length) return;
      inspector.append(make("h4", label)); const list = make("ul");
      for (const value of [...new Set(entries)]) list.append(make("li", value));
      inspector.append(list);
    }
    function addScope(scope) {
      if (!scope || typeof scope !== "object") return;
      const entries = [];
      if (scope.geography) entries.push(["地域", scope.geography]);
      if (scope.timeWindow?.asOf) entries.push(["基准时点", scope.timeWindow.asOf]);
      if (scope.timeWindow?.horizonYears) entries.push(["展望期限", scope.timeWindow.horizonYears + " 年"]);
      if (!entries.length) return;
      inspector.append(make("h4", "研究范围")); const dl = make("dl", undefined, "report-graph-scope");
      for (const [label, value] of entries) dl.append(make("dt", label), make("dd", value));
      inspector.append(dl);
    }
    function highlight() {
      for (const [id, node] of nodeElements) {
        const active = selected?.kind === "node" && selected.id === id;
        const relation = selected?.kind === "relation" ? visible.find(row => row.id === selected.id) : null;
        node.classList.toggle("is-selected", active || !!relation && (relation.subject === id || relation.object === id));
        node.setAttribute("aria-pressed", String(active));
      }
      for (const [id, edge] of edgeElements) {
        edge.classList.toggle("is-selected", selected?.kind === "relation" ? selected.id === id : selected?.kind === "node" && visible.some(row => row.id === id && (row.subject === selected.id || row.object === selected.id)));
        edge.setAttribute("aria-pressed", String(selected?.kind === "relation" && selected.id === id));
      }
    }
    function selectRelation(row) {
      selected = {kind:"relation", id:row.id}; highlight(); inspector.replaceChildren();
      inspector.append(make("h4", "关系判断"), make("p", relationLabel(row), "report-graph-selected-title"), make("p", epistemicLabel(row) + " · 事实尚未核验", "report-graph-status"));
      const finding = model.findings.get(row.findingId || row.perspectiveKey);
      if (finding?.statement) { inspector.append(make("h4", "关联结论"), make("p", finding.statement)); }
      if (row.supportReview?.reason) inspector.append(make("p", "引文相关性复核：" + row.supportReview.reason, "muted"));
      destination.dispatchEvent(new CustomEvent("report-graph-selection",{detail:{kind:"assertion",id:row.id}}));
      inspector.append(make("h4", "支持证据")); sourceBlock(row.evidenceIds, inspector);
      const perspectiveOnly=row.conditionScope==="perspective"||(!row.conditionScope&&JSON.stringify(row.conditions||[])===JSON.stringify(finding?.conditions||[]));
      if(!perspectiveOnly)addList("关系适用条件",arr(row.conditions));
      addList("视角适用条件",arr(finding?.conditions));
      addList("反方假设", [...arr(row.counterArguments), ...arr(finding?.counterArguments)]);
      addList("未知项", [...arr(row.unknowns), ...arr(finding?.unknowns)]);
      addScope(row.scope || report.taskDefinition?.scope);
      const contrary = [...new Set([...arr(row.counterEvidenceIds), ...arr(finding?.counterEvidenceIds)])].filter(id => model.evidence.has(String(id)));
      if (contrary.length) { inspector.append(make("h4", "反方证据")); sourceBlock(contrary, inspector); }
      inspector.append(make("p", "模型相关性复核不等于事实核验；来源独立性尚未确认。", "muted"));
      onReviewObject?.(inspector,{kind:"edge",id:"domain:"+row.id},relationLabel(row));
      inspector.scrollTop = 0;
    }
    function selectNode(id) {
      selected = {kind:"node", id}; highlight(); inspector.replaceChildren();
      const node = model.nodes.get(id);
      destination.dispatchEvent(new CustomEvent("report-graph-selection",{detail:{kind:"node",id}}));
      inspector.append(make("h4", "本体对象"), make("p", node.label, "report-graph-selected-title"), make("p", types[node.type] + " · 本次报告对象", "muted"));
      const related = visible.filter(row => row.subject === id || row.object === id);
      inspector.append(make("h4", "关联关系（" + related.length + "）"));
      for (const row of related) {
        const button = make("button", relationLabel(row), "report-graph-related"); button.type = "button";
        button.onclick = () => selectRelation(row); inspector.append(button);
      }
      onReviewObject?.(inspector,{kind:"node",id},node.label);
      inspector.scrollTop = 0;
    }
    function fitBoard() { camera.fit(); }
    fit.onclick = fitBoard; minus.onclick = () => camera.zoomBy(1 / 1.2); plus.onclick = () => camera.zoomBy(1.2);

    function draw() {
      visible = model.relations.filter(row => filter.value === "all" || row.perspectiveKey === filter.value);
      const ids = new Set(visible.flatMap(row => [row.subject, row.object]));
      const nodes = [...ids].map(id => model.nodes.get(id));
      count.textContent = nodes.length + " 个对象 / " + visible.length + " 条关系";
      board.replaceChildren(); edgeElements.clear(); nodeElements.clear(); selected = null; layout = null;
      if (!visible.length) {
        sizer.style.width = "100%"; sizer.style.height = "100%"; board.style.transform = "none";
        viewport.classList.add("is-empty");
        const gap = arr(report.evidenceGaps).find(item => item.perspectiveKey === filter.value);
        const finding = [...model.findings.values()].find(row => row.perspectiveKey === filter.value);
        board.append(make("p", "该视角尚无可绘制的本体关系。", "report-graph-unavailable"));
        inspector.replaceChildren(make("h4", "证据缺口"), make("p", gap?.summary || "未保存通过证据引用与关系复核校验的关系。"));
        if (gap?.neededEvidence) inspector.append(make("p", gap.neededEvidence));
        if (finding?.statement && finding.status === "candidate") inspector.append(make("h4", "候选文字判断"), make("p", finding.statement));
        return;
      }
      viewport.classList.remove("is-empty");
      layout = layoutFor(nodes, visible);
      viewport.style.height=Math.min(490,Math.max(220,layout.height)) + "px";
      inspector.style.maxHeight=Math.min(490,Math.max(260,layout.height)) + "px";
      board.style.width = layout.width + "px"; board.style.height = layout.height + "px";
      const canvas = svg("svg", {width:layout.width, height:layout.height, class:"report-graph-edges"});
      const markerId = "report-arrow-" + (++mount.serial);
      const defs = svg("defs"), marker = svg("marker", {id:markerId, viewBox:"0 0 10 10", refX:9, refY:5, markerWidth:7, markerHeight:7, orient:"auto"});
      marker.append(svg("path", {d:"M0,0 L10,5 L0,10 z", fill:"currentColor"})); defs.append(marker); canvas.append(defs);
      const parallels = new Map(), edgeIndex = new Map();
      for (const row of visible) {
        const pair = JSON.stringify([row.subject, row.object].sort());
        if (!parallels.has(pair)) parallels.set(pair, []);
        edgeIndex.set(row.id, parallels.get(pair).length); parallels.get(pair).push(row);
      }
      const boundary = (node, toward) => {
        const cx = node.x + 84, cy = node.y + 38, dx = toward.x - cx, dy = toward.y - cy;
        const reach = Math.min(dx ? 84 / Math.abs(dx) : Infinity, dy ? 38 / Math.abs(dy) : Infinity);
        return {x:cx + dx * reach, y:cy + dy * reach};
      };
      visible.forEach(row => {
        const from = layout.positions.get(row.subject), to = layout.positions.get(row.object);
        const pair = JSON.stringify([row.subject, row.object].sort()), index = edgeIndex.get(row.id), count = parallels.get(pair).length;
        let d, lx, ly;
        if (row.subject === row.object) {
          const sx = from.x + 168, sy = from.y + 38, reach = 55 + index * 24;
          d = "M " + sx + " " + (sy - 18) + " C " + (sx + reach) + " " + (sy - 60) + " " + (sx + reach) + " " + (sy + 60) + " " + sx + " " + (sy + 18);
          lx = sx + reach * .75; ly = sy;
        } else {
          // Use the same normal for both directions of an object pair.
          // Parallel and reciprocal relations receive separate curved lanes.
          const first = row.subject < row.object ? from : to, second = first === from ? to : from;
          const dx = second.x - first.x, dy = second.y - first.y, distance = Math.hypot(dx, dy);
          const offset = (index - (count - 1) / 2) * 64;
          const control = {x:(from.x + to.x) / 2 + 84 - dy / distance * offset, y:(from.y + to.y) / 2 + 38 + dx / distance * offset};
          const start = boundary(from, control), end = boundary(to, control);
          d = "M " + start.x + " " + start.y + " Q " + control.x + " " + control.y + " " + end.x + " " + end.y;
          lx = (start.x + end.x) / 4 + control.x / 2; ly = (start.y + end.y) / 4 + control.y / 2;
        }
        const group = svg("g", {class:"report-graph-edge", tabindex:0, role:"button", "aria-label":relationLabel(row) + "，查看判断依据", "data-assertion-id":row.id});
        const edge = svg("path", {d, class:"report-graph-edge-line", "marker-end":"url(#" + markerId + ")"});
        const hit = svg("path", {d, class:"report-graph-edge-hit"});
        const label = svg("text", {x:lx, y:ly - 5, "text-anchor":"middle", class:"report-graph-edge-label"}); label.textContent = predicates[row.predicate];
        group.append(edge, hit, label); group.onclick = () => selectRelation(row);
        group.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRelation(row); } };
        edgeElements.set(row.id, group); canvas.append(group);
      });
      board.append(canvas);
      for (const node of nodes) {
        const pos = layout.positions.get(node.id), button = make("button", undefined, "report-graph-node");
        button.type = "button"; button.dataset.nodeId = node.id; button.title = node.label;
        button.setAttribute("aria-label", types[node.type] + "：" + node.label + "，查看关联关系");
        button.style.left = pos.x + "px"; button.style.top = pos.y + "px";
        button.append(make("span", types[node.type], "report-graph-node-type"), make("strong", node.label));
        button.onclick = () => selectNode(node.id); board.append(button); nodeElements.set(node.id, button);
      }
      camera.setSize(layout); selectRelation(visible[0]); fitBoard();
    }
    filter.onchange = draw;
    draw();
    sessions.set(root, {viewport, camera, fitBoard, selectItem(item){
      const relation=model.relations.find(row=>row.id===item.id||"domain:"+row.id===item.objectRef?.id);
      if(relation){if(filter.value!==relation.perspectiveKey){filter.value=relation.perspectiveKey;draw();}selectRelation(relation);const element=edgeElements.get(relation.id);element?.focus({preventScroll:true});return true;}
      const id=item.objectRef?.kind==="node"?item.objectRef.id:null;
      if(id&&model.nodes.has(id)){const related=model.relations.find(row=>row.subject===id||row.object===id);if(related&&filter.value!==related.perspectiveKey){filter.value=related.perspectiveKey;draw();}if(nodeElements.has(id)){selectNode(id);nodeElements.get(id).focus({preventScroll:true});return true;}}
      return false;
    }});
    return {hasGraph:true, relationIds:new Set(model.relations.map(row => row.id))};
  }
  mount.serial = 0;
  function activate(destination) {
    for (const root of destination.querySelectorAll(".report-analysis-graph")) {
      const session = sessions.get(root);
      if (session) { session.camera.apply(); }
    }
  }
  function dispose(destination) {
    for (const root of destination.querySelectorAll(".report-analysis-graph")) sessions.get(root)?.camera.destroy();
  }
  function selectItem(destination,item){for(const root of destination.querySelectorAll(".report-analysis-graph")){if(sessions.get(root)?.selectItem(item)){root.scrollIntoView({block:"nearest",behavior:"auto"});return true;}}return false;}
  window.AgentAdminReportGraph = Object.freeze({mount, activate, dispose, selectItem, epistemicLabel});
})();
