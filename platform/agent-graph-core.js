/* Shared by Agent Admin and the DSH browser bundle. No network or Run writes. */
(function (host) {
  "use strict";
  const VERSION = "1.0.0", SCHEMA = "agent-graph-view.v1";
  const MIN_ZOOM = .1, MAX_ZOOM = 2;
  const finite = (value, fallback = 0) => Number.isFinite(value) ? value : fallback;
  const clampZoom = value => Math.round(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, finite(value, 1))) * 10000) / 10000;
  const kinds = new Set(["baseline", "execution", "ontology"]);

  const methodPaths = {
    'sub-agent':"M5 2h6v4H5ZM2 11h5v4H2ZM10 11h5v4h-5ZM8 6v3M4.5 11V9h8v2",
    llm:"M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5ZM6 1v2.5M10 1v2.5M6 12.5V15M10 12.5V15M1 6h2.5M1 10h2.5M12.5 6H15M12.5 10H15M6 6h4v4H6Z",
    fixed:"M6 1h4v2l1.5.9 1.8-1 2 3.4-1.8 1V9l1.8 1-2 3.4-1.8-1L10 13v2H6v-2l-1.5-.9-1.8 1-2-3.4 1.8-1V7l-1.8-1 2-3.4 1.8 1L6 3V1ZM10.5 8a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z",
    input:"M10.5 4a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM3 14v-2a5 5 0 0 1 10 0v2",
    unknown:"M6 5a2 2 0 1 1 3 1.7C8 7.3 8 8 8 9M8 11v1",
  };
  function executionMethod(item) {
    if (item.kind === "input") return {category:"input",label:"输入",description:"任务输入与冻结配置；来源以任务记录为准。"};
    if ((item.status || item.execution?.status) === "waiting-human" || item.executionMethod?.category === "human") return {category:"human",label:"人工",description:"真实人工操作或审核门禁；是否完成以审批记录为准。"};
    if (item.delegations?.length || item.children?.some(child => child.child_run_id || child.childRunId)) return {category:"sub-agent",label:"Sub Agent",description:"已委派子 Agent；展开子运行可查看其实际模型、工具与内部处理。"};
    if (item.llmCalls?.length) return {category:"llm",label:"LLM",description:"本节点有直接模型调用记录。"};
    if (item.toolCalls?.length || item.dispatches?.length || item.delegations?.length || ["result","plan-validation"].includes(item.kind)) return {category:"fixed",label:"固定",description:"固定执行：工具 / API、内部处理或子任务调度。"};
    return {category:"unknown",label:item.observed === false ? "待执行" : "未记录",description:item.observed !== false ? "执行方式未记录；不能仅凭没有模型调用记录认定为固定执行。" : "配置节点尚未执行，调用发生后显示实际执行方式。"};
  }

  // Presentation adapters keep source records and relation meanings intact.
  // In particular, an ontology graph is not required to be acyclic, and a
  // planned model/tool budget is never evidence of an actual invocation.
  function adapt(kind, source, identity = {}) {
    if (!kinds.has(kind)) throw new TypeError("Unknown graph view kind");
    const graph = kind === "baseline" ? source.graph || source : source;
    if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new TypeError("Graph nodes and edges are required");
    const ids = new Set(), edgeIds = new Set();
    const nodes = graph.nodes.map(data => {
      if (typeof data.id !== "string" || !data.id || ids.has(data.id)) throw new TypeError("Graph node identity must be unique");
      ids.add(data.id);
      return Object.freeze({id:data.id, type:data.nodeType || data.type || data.kind || "unknown", data});
    });
    const edges = graph.edges.map((data, index) => {
      const from = data.from ?? data.source ?? data.subject, to = data.to ?? data.target ?? data.object;
      if (!ids.has(from) || !ids.has(to)) throw new TypeError("Graph edge endpoint is missing");
      const id = data.id || `${kind}:edge:${index}`;
      if (edgeIds.has(id)) throw new TypeError("Graph edge identity must be unique");
      edgeIds.add(id);
      const semantic = kind === "ontology" ? data.relation || data.predicate || data.kind : data.kind;
      return Object.freeze({id, from, to, semantic:semantic || "unrecorded", data});
    });
    return Object.freeze({schemaVersion:SCHEMA, kind, identity:Object.freeze({...identity}),
      nodes:Object.freeze(nodes), edges:Object.freeze(edges)});
  }

  // One controller owns camera math, DOM/SVG transforms, pointer capture,
  // wheel/keyboard semantics, resize behaviour and teardown for all hosts.
  function createViewport(options) {
    const {viewport, sizer, board} = options;
    if (!viewport || !sizer || !board) throw new TypeError("A viewport, sizer and board are required");
    viewport.agentGraphViewport?.destroy();
    const state = options.state || {};
    state.zoom = clampZoom(state.zoom ?? options.zoom ?? .85);
    state.panX = finite(state.panX); state.panY = finite(state.panY);
    let size = {width:1, height:1}, drag = null, suppressClick = false, clickTimer = null, destroyed = false;
    let fitMode = options.autoFit || false;
    const svg = options.svg === true, listeners = [], cleanups = [];
    const padding = options.padding ?? 12;
    const style = (element, key, value) => { if (element.style[key] !== value) element.style[key] = value; };
    const attribute = (element, key, value) => { if (element.getAttribute(key) !== String(value)) element.setAttribute(key, value); };
    let lastChange;
    const offset = () => ({
      x:(options.centerX ? Math.max(padding, (viewport.clientWidth - size.width * state.zoom) / 2) : 0) + state.panX,
      y:(options.centerY ? Math.max(padding, (viewport.clientHeight - size.height * state.zoom) / 2) : 0) + state.panY,
    });
    const snapshot = () => ({...state, ...offset(), width:size.width, height:size.height});
    function apply() {
      if (destroyed) return;
      const {x, y} = offset();
      const width = Math.max(viewport.clientWidth, Math.ceil(size.width * state.zoom + Math.max(0, x) + padding));
      const height = Math.max(viewport.clientHeight, Math.ceil(size.height * state.zoom + Math.max(0, y) + padding));
      if (svg) {
        attribute(board, "transform", `translate(${x},${y}) scale(${state.zoom})`);
        attribute(sizer, "width", width); attribute(sizer, "height", height);
        if (sizer.hasAttribute("viewBox")) attribute(sizer, "viewBox", `0 0 ${width} ${height}`);
      } else {
        style(board, "left", "0px"); style(board, "top", "0px");
        style(board, "transformOrigin", "0px 0px");
        style(board, "transform", `translate(${x}px, ${y}px) scale(${state.zoom})`);
      }
      style(sizer, "width", width + "px"); style(sizer, "height", height + "px");
      if (viewport.dataset.graphCore !== VERSION) viewport.dataset.graphCore = VERSION;
      if (viewport.dataset.graphZoom !== String(state.zoom)) viewport.dataset.graphZoom = String(state.zoom);
      const signature = [x,y,width,height,size.width,size.height,state.zoom,state.panX,state.panY,state.manualZoom].join(":");
      if (signature !== lastChange) { lastChange = signature; options.onChange?.(snapshot()); }
    }
    function setZoom(value, {manual = true, anchor = null} = {}) {
      if (destroyed || !Number.isFinite(value)) return;
      const point = anchor || {x:viewport.clientWidth / 2, y:viewport.clientHeight / 2};
      const old = offset(), bx = (viewport.scrollLeft + point.x - old.x) / state.zoom;
      const by = (viewport.scrollTop + point.y - old.y) / state.zoom;
      if (manual) { fitMode = false; state.manualZoom = true; }
      state.zoom = clampZoom(value); apply();
      if (manual) {
        const next = offset();
        viewport.scrollLeft = Math.max(0, bx * state.zoom + next.x - point.x);
        viewport.scrollTop = Math.max(0, by * state.zoom + next.y - point.y);
      }
      rememberScroll();
    }
    function fit({axis = "both", maxZoom = 1, manual = true} = {}) {
      fitMode = axis;
      if (manual) state.manualZoom = true;
      if (!viewport.clientWidth || !viewport.clientHeight) return;
      state.panX = state.panY = 0;
      const width = (viewport.clientWidth - padding * 2) / size.width;
      const height = (viewport.clientHeight - padding * 2) / size.height;
      setZoom(Math.min(width, axis === "width" ? Infinity : height, maxZoom), {manual:false});
      viewport.scrollLeft = viewport.scrollTop = 0; rememberScroll();
    }
    function setSize(next) {
      if (!(Number.isFinite(next.width) && next.width > 0 && Number.isFinite(next.height) && next.height > 0)) throw new TypeError("Invalid graph dimensions");
      size = {width:next.width, height:next.height};
      if (fitMode) fit({axis:fitMode,manual:false}); else apply();
    }
    function reset() {
      fitMode = false; state.manualZoom = true; state.panX = state.panY = 0;
      setZoom(1, {manual:false});
      viewport.scrollLeft = viewport.scrollTop = 0; rememberScroll();
    }
    function rememberScroll() { state.scrollLeft = viewport.scrollLeft; state.scrollTop = viewport.scrollTop; }
    function listen(type, callback, config) {
      viewport.addEventListener(type, callback, config);
      listeners.push(() => viewport.removeEventListener(type, callback, config));
    }
    const editable = target => target?.closest?.("input,textarea,select,[contenteditable=true],[data-graph-no-pan]");
    function finish(event) {
      if (!drag || event.pointerId !== drag.id) return;
      const moved = drag.moved; drag = null;
      viewport.classList.remove("is-panning");
      if (viewport.hasPointerCapture?.(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      if (moved && event.type === "pointerup") {
        suppressClick = true;
        clearTimeout(clickTimer); clickTimer = setTimeout(() => { suppressClick = false; }, 0);
      }
    }
    listen("pointerdown", event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || editable(event.target)) return;
      drag = {id:event.pointerId, x:event.clientX, y:event.clientY, panX:state.panX, panY:state.panY, moved:false};
    });
    listen("pointermove", event => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true; fitMode = false; state.manualZoom = true;
      viewport.setPointerCapture?.(event.pointerId); viewport.classList.add("is-panning"); event.preventDefault();
      state.panX = drag.panX + dx; state.panY = drag.panY + dy; apply();
    }, {passive:false});
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) listen(type, finish);
    listen("click", event => {
      if (!suppressClick) return;
      suppressClick = false; event.preventDefault(); event.stopPropagation();
    }, true);
    listen("wheel", event => {
      if (!(event.ctrlKey || event.metaKey) || !event.deltaY || editable(event.target)) return;
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1);
      setZoom(state.zoom * Math.exp(-Math.max(-160, Math.min(160, delta)) * .002), {anchor:{
        x:event.clientX - rect.left - viewport.clientLeft, y:event.clientY - rect.top - viewport.clientTop}});
    }, {passive:false});
    listen("keydown", event => {
      if (event.ctrlKey || event.metaKey || event.altKey || editable(event.target)) return;
      if (["+", "="].includes(event.key)) setZoom(state.zoom + .1);
      else if (["-", "_"].includes(event.key)) setZoom(state.zoom - .1);
      else if (event.key === "0") reset();
      else if (event.key.toLowerCase() === "f") fit();
      else return;
      event.preventDefault();
    });
    listen("scroll", rememberScroll);
    if (typeof ResizeObserver !== "undefined") {
      let previousSize;
      const observer = new ResizeObserver(() => {
        // Duplicate observer deliveries must not rewrite the camera controls
        // or feed another layout pass when the viewport has not changed.
        const nextSize = viewport.clientWidth + ":" + viewport.clientHeight;
        if (destroyed || nextSize === previousSize) return;
        previousSize = nextSize;
        if (fitMode) fit({axis:fitMode,manual:false}); else apply();
      });
      observer.observe(viewport); cleanups.push(() => observer.disconnect());
    }
    function bindControls({minus, plus, value, fit:fitButton, reset} = {}) {
      const bindings = [[minus, () => setZoom(state.zoom - .1)], [plus, () => setZoom(state.zoom + .1)],
        [value, () => controller.reset()], [fitButton, () => fit()], [reset, () => controller.reset()]];
      for (const [element, handler] of bindings) if (element) {
        element.addEventListener("click", handler); cleanups.push(() => element.removeEventListener("click", handler));
      }
      const update = () => {
        if (value) { value.textContent = Math.round(state.zoom * 100) + "%"; value.setAttribute("aria-label", "当前比例 " + value.textContent + "，点击恢复 100%"); }
        if (minus) minus.disabled = state.zoom <= MIN_ZOOM;
        if (plus) plus.disabled = state.zoom >= MAX_ZOOM;
      };
      const previous = options.onChange;
      options.onChange = camera => { previous?.(camera); update(); }; update();
    }
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (drag && viewport.hasPointerCapture?.(drag.id)) viewport.releasePointerCapture(drag.id);
      drag = null; clearTimeout(clickTimer);
      for (const remove of [...listeners, ...cleanups]) remove();
      viewport.classList.remove("is-panning");
      if (viewport.agentGraphViewport === controller) delete viewport.agentGraphViewport;
      active.delete(controller);
    }
    const controller = {viewport, setSize, setZoom, fit, reset, bindControls, apply, snapshot, destroy,
      zoomBy:factor => setZoom(state.zoom * factor),
      get state() { return snapshot(); }};
    viewport.agentGraphViewport = controller; active.add(controller); watchRemoval();
    return controller;
  }
  function layoutGraph(graph, options = {}) {
    // Read execution top-to-bottom: ranks are stages, parallel nodes occupy
    // columns on the same row. This keeps branches visibly parallel and makes
    // convergent nodes appear below all of their producers.
    const width = 176, height = 70, colGap = 42, rowGap = 18, top = 48, margin = 16;
    const positions = new Map(), bands = [];
    let x = margin, maxParallelRows = 1;
    for (const group of graph.groups) {
      const members = graph.nodes.filter(item => item.group === group.id);
      const ids = new Set(members.map(item => item.id)), rank = new Map(members.map(item => [item.id, 0]));
      const incoming = new Map(members.map(item => [item.id, 0])), outgoing = new Map(members.map(item => [item.id, []]));
      const localEdges = graph.edges.filter(item => ids.has(item.from) && ids.has(item.to));
      for (const edge of localEdges) { incoming.set(edge.to, incoming.get(edge.to) + 1); outgoing.get(edge.from).push(edge.to); }
      const queue = members.filter(item => incoming.get(item.id) === 0).map(item => item.id);
      for (let index = 0; index < queue.length; index++) {
        const source = queue[index];
        for (const target of outgoing.get(source)) {
          rank.set(target, Math.max(rank.get(target), rank.get(source) + 1));
          incoming.set(target, incoming.get(target) - 1);
          if (!incoming.get(target)) queue.push(target);
        }
      }
      // Malformed imported plans remain inspectable rather than hanging layout.
      for (const item of members.filter(item => !queue.includes(item.id))) rank.set(item.id, Math.max(...rank.values()) + 1);
      const columns = new Map();
      for (const item of members) { const r = rank.get(item.id); if (!columns.has(r)) columns.set(r, []); columns.get(r).push(item); }
      const parallel = Math.max(1, ...[...columns.values()].map(items => items.length));
      const rankValues = [...columns.keys()];
      const rankCount = Math.max(0, ...rankValues) + 1;
      const groupWidth = parallel * (width + colGap) - colGap + margin * 2;
      const groupHeight = rankCount * (height + rowGap) - rowGap + margin * 2;
      for (const [r, items] of columns) {
        maxParallelRows = Math.max(maxParallelRows, rankCount);
        items.forEach((item, index) => positions.set(item.id, {x:x + margin + index * (width + colGap), y:top + margin + r * (height + rowGap), width, height}));
      }
      bands.push({...group, x, width:groupWidth, height:groupHeight}); x += groupWidth + 20;
    }
    const result = {positions, bands, width:x, height:Math.max(320, top + maxParallelRows * (height + rowGap) + margin + 20)};
    if (options.direction !== "horizontal") return result;
    // Baseline profiles read as a left-to-right plan.  The execution monitor
    // keeps its vertical reading order; callers opt into this transposition
    // explicitly so replay and runtime graph semantics stay unchanged.
    const horizontalPositions = new Map();
    // The vertical algorithm stores rank in y and parallel lanes in x. For a
    // left-to-right baseline, transpose those logical coordinates and rebuild
    // the spacing from the card dimensions. Reusing the raw vertical y value
    // would put cards 106px apart while they are 194px wide, causing overlap
    // and truncating node titles.
    const ranks = [...new Set([...positions.values()].map(position => position.y))].sort((a, b) => a - b);
    const lanes = [...new Set([...positions.values()].map(position => position.x))].sort((a, b) => a - b);
    const rankX = new Map(ranks.map((value, index) => [value, margin + index * (width + colGap)]));
    const laneY = new Map(lanes.map((value, index) => [value, margin + index * (height + rowGap)]));
    positions.forEach((position, id) => horizontalPositions.set(id, {
      ...position, x: rankX.get(position.y), y: laneY.get(position.x),
    }));
    const horizontalWidth = Math.max(320, ...[...horizontalPositions.values()].map(position => position.x + position.width + margin));
    const horizontalHeight = Math.max(320, ...[...horizontalPositions.values()].map(position => position.y + position.height + margin));
    return {
      ...result,
      positions: horizontalPositions,
      width: horizontalWidth,
      height: horizontalHeight,
    };
  }

  function edgePath(source, target) {
    if (source.x === target.x && target.y >= source.y + source.height) {
      return `M${source.x + source.width / 2},${source.y + source.height} V${target.y}`;
    }
    const a = {x:source.x + source.width, y:source.y + source.height / 2}, b = {x:target.x, y:target.y + target.height / 2};
    if (b.x > a.x + 16) {
      const mid = a.x + (b.x - a.x) / 2;
      return `M${a.x},${a.y} H${mid - 8} Q${mid},${a.y} ${mid},${a.y + Math.sign(b.y - a.y) * Math.min(8, Math.abs(b.y - a.y) / 2)} V${b.y - Math.sign(b.y - a.y) * Math.min(8, Math.abs(b.y - a.y) / 2)} Q${mid},${b.y} ${mid + 8},${b.y} H${b.x}`;
    }
    return `M${a.x},${a.y} C${a.x + 25},${a.y} ${b.x - 25},${b.y} ${b.x},${b.y}`;
  }

  const active = new Set();
  let removalObserver;
  function watchRemoval() {
    if (removalObserver || !host.document || typeof MutationObserver === "undefined") return;
    removalObserver = new MutationObserver(() => {
      for (const item of active) if (!item.viewport.isConnected) item.destroy();
      if (!active.size) { removalObserver.disconnect(); removalObserver = null; }
    });
    removalObserver.observe(host.document.documentElement, {childList:true, subtree:true});
  }
  host.AgentGraph = Object.freeze({VERSION, SCHEMA, MIN_ZOOM, MAX_ZOOM, clampZoom, adapt, createViewport, layoutGraph, edgePath, methodPaths:Object.freeze(methodPaths), executionMethod});
})(typeof window === "undefined" ? globalThis : window);
