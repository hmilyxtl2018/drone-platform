/* Agents 工作台 UI 适配层：默认连接运行服务；?demo 使用本地可重放回放。 */
const params = new URLSearchParams(location.search);
const API_ENABLED = window.AGENTS_API_ENABLED === true || (window.AGENTS_API_ENABLED !== false && !params.has("demo"));
const API_ROOT = "/api";
const now = () => new Date().toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
const uid = (prefix) => prefix + "-" + Math.random().toString(36).slice(2, 8);
const sessionRunId = () => {
  const explicit = params.get("runId");
  if (explicit) return explicit;
  if (!API_ENABLED) return "run-demo";
  try {
    const key = "agents.runtime.runId";
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const created = `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    sessionStorage.setItem(key, created);
    return created;
  } catch (error) {
    return `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  }
};
const RUN_ID = sessionRunId();
const REVIEW_ID = `${RUN_ID}-review-1`;
const ACTION_REQUEST_IRI = `${RUN_ID}:action-request-1`;
const RECOMMENDATION_IRI = `${RUN_ID}:decision-draft-1`;
const AUTHORIZATION_IRI = `${RUN_ID}:authorization-1`;
const AGENTS = [
  { id: "world", name: "World / 世界", role: "观测与信念", mark: "W", state: "ok" },
  { id: "self", name: "Self / 己方", role: "状态与链路", mark: "S", state: "ok" },
  { id: "options", name: "Options / 方案", role: "推荐与推演", mark: "O", state: "ok" },
  { id: "gate", name: "Gate / 门控", role: "约束与授权", mark: "G", state: "warn" },
  { id: "effectors", name: "Effectors / 执行", role: "动作与效果", mark: "E", state: "ok" },
  { id: "learn", name: "Learn / 复盘", role: "轨迹与审计", mark: "L", state: "ok" },
];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const MOCK_EVENTS = [
  { eventId: "evt-001", eventType: "RunStarted", actor: "orchestrator", time: "14:32:08", title: "Run 已启动", detail: "coast-raid-k@0.9.4 · engagement-rules@24", tone: "success", evidence: [RUN_ID, "manifest:sha256:2b43…"] },
  { eventId: "evt-002", eventType: "ObservationCreated", actor: "sensor.registry.01", time: "14:36:41", title: "收到来源观测", detail: "record-17 · 版本 v3 · 来源可信度 0.91", evidence: ["obs-20260924-173", "sensor.registry.01"] },
  { eventId: "evt-003", eventType: "BeliefUpdated", actor: "agent.fusion.c2", time: "14:36:44", title: "信念状态已更新", detail: "来源完整度：0.63 · 仍有字段待确认", evidence: ["belief-record-17-v3", "rule.evidence.threshold@24"] },
  { eventId: "evt-004", eventType: "DecisionDraft", actor: "agent.options.planner", time: "14:36:51", title: "Agent 生成推荐", detail: "推荐采集补充证据并更新记录；范围受限且可撤销。", evidence: ["decision-draft-17-v1", "model:deterministic-1.2"] },
  { eventId: "evt-005", eventType: "ActionRequest", actor: "agent.options.planner", time: "14:36:53", title: "提交动作请求", detail: "collect-evidence · record-17 · 作用范围：知识库运行上下文", evidence: ["action-request-17-v1", "policy:evidence-collection"] },
  { eventId: "evt-006", eventType: "ApprovalRequest", actor: "agent.monitor.gate", time: "14:36:54", title: "等待人工确认", detail: "人机模式：in-the-loop · 证据仍需人工确认", tone: "warning", evidence: [REVIEW_ID, "humanControlMode:in-the-loop"] },
];
const state = {
  status: "running", events: [...MOCK_EVENTS], filter: "all", selectedEvent: "evt-006",
  recommendation: { status: "pending", selected: "A", confidence: 0.86 },
  review: { id: REVIEW_ID, status: "pending", action: "采集补充证据并更新记录", requester: "Options Agent", age: "刚刚", reason: "当前证据不足，需要人工确认是否继续采集。", actionRequestIri: ACTION_REQUEST_IRI, recommendationIri: RECOMMENDATION_IRI },
  authorization: null,
  messages: [{ from: "assistant", text: `已读取 ${RUN_ID} 的当前上下文。Options Agent 推荐为 record-17 采集补充证据并更新记录。该请求需要人在环确认。`, meta: API_ENABLED ? "API 模式 · 引用 3 条证据 · Gate 预检查完成" : "Demo 模式 · 本地回放" }],
};
const els = {
  eventStream: document.getElementById("event-stream"), eventCount: document.getElementById("event-count"), attentionCount: document.getElementById("attention-count"), agentList: document.getElementById("agent-list"), runStatus: document.getElementById("run-status"), runIdChip: document.getElementById("run-id-chip"), pauseRun: document.getElementById("pause-run"), recommendationState: document.getElementById("recommendation-state"), recommendationCard: document.getElementById("recommendation-card"), reviewCard: document.getElementById("review-card"), queueCount: document.getElementById("queue-count"), evidenceBody: document.getElementById("evidence-body"), copilotMessages: document.getElementById("copilot-messages"), copilotForm: document.getElementById("copilot-form"), copilotInput: document.getElementById("copilot-input"), toastStack: document.getElementById("toast-stack"),
};
const eventLabel = { RunStarted: "Run 启动", ObservationCreated: "观测创建", BeliefUpdated: "信念更新", DecisionDraft: "决策草案", ActionRequest: "动作请求", ApprovalRequest: "人工确认", HumanDecision: "人工决策", Authorization: "授权", AuthorizationRevoked: "授权撤销", ActionExecution: "动作执行", ActionExecutionStarted: "动作开始", ActionExecutionCompleted: "动作完成", ActionExecutionAborted: "动作中止", Effect: "效果", EffectRecorded: "效果记录", SafeFallbackTriggered: "安全回退", ReplayRequested: "请求回放", ObservationRequested: "请求补证", CopilotCommand: "Copilot 命令" };

function renderAgents() {
  els.agentList.innerHTML = AGENTS.map((agent) => `<li class="agent-item ${agent.id === "gate" ? "active" : ""}">
    <span class="agent-avatar">${agent.mark}</span><span class="agent-meta"><span class="agent-name">${esc(agent.name)}</span><span class="agent-sub">${esc(agent.role)}</span></span><i class="agent-state ${agent.state}"></i>
  </li>`).join("");
}

function renderTimeline() {
  const list = state.filter === "attention" ? state.events.filter((event) => ["warning", "danger"].includes(event.tone) || event.eventType === "ApprovalRequest") : state.events;
  els.eventCount.textContent = state.events.length;
  els.attentionCount.textContent = state.events.filter((event) => event.tone === "warning" || event.tone === "danger" || event.eventType === "ApprovalRequest").length;
  els.eventStream.innerHTML = list.map((event) => `<li class="event-item" data-tone="${event.tone || "default"}" data-event-id="${esc(event.eventId)}">
    <time class="event-time">${esc(event.time)}</time><i class="event-marker"></i><div class="event-body"><div class="event-title-row"><span class="event-title">${esc(event.title || eventLabel[event.eventType] || event.eventType)}</span><span class="event-kind">${esc(event.eventType)}</span></div><p class="event-detail">${esc(event.detail || "")}</p><button class="event-link" type="button" data-event="${esc(event.eventId)}">查看证据 →</button></div>
  </li>`).join("");
  els.eventStream.querySelectorAll("[data-event]").forEach((button) => button.addEventListener("click", () => selectEvent(button.dataset.event)));
}

function renderRecommendation() {
  const status = state.recommendation.status;
  const pill = { pending: ["待人工确认", "pending"], approved: ["已授权执行", "approved"], rejected: ["已拒绝", "rejected"], evidence: ["补证中", "muted"] }[status] || ["待处理", "muted"];
  els.recommendationState.textContent = pill[0];
  els.recommendationState.className = `state-pill ${pill[1]}`;
  const canReview = status === "pending" && state.review?.status === "pending";
  const auth = state.authorization;
  els.recommendationCard.innerHTML = `<div class="recommendation-content">
    <div class="rec-label"><span>DecisionDraft · v1</span><span class="mono">14:36:51</span></div>
    <h3 class="rec-title">采集补充证据并更新记录</h3>
    <p class="rec-summary">对象 <b>record-17</b> 的来源证据仍不完整。建议执行可逆的证据采集动作，并在新证据到达后重新评估。</p>
    <div class="confidence"><span>方案置信度</span><div class="confidence-bar"><i style="width:${Math.round(state.recommendation.confidence * 100)}%"></i></div><b>${Math.round(state.recommendation.confidence * 100)}%</b></div>
    <div class="option-list"><div class="option-row selected"><span class="option-index">A</span><span class="option-label">采集补充证据</span><span class="option-risk">可逆</span></div><div class="option-row"><span class="option-index">B</span><span class="option-label">请求来源复核</span><span class="option-risk">低风险</span></div><div class="option-row"><span class="option-index">C</span><span class="option-label">暂缓处理</span><span class="option-risk">无外部效果</span></div></div>
    <div class="rec-actions">${canReview ? `<button class="btn btn-primary" id="approve-recommendation" type="button">批准推荐</button><button class="btn btn-quiet" id="request-evidence" type="button">要求补充证据</button><button class="btn btn-quiet" id="revise-recommendation" type="button">修改后重新评估</button><button class="btn btn-quiet" id="reject-recommendation" type="button">拒绝</button>` : auth?.status === "active" ? `<button class="btn btn-quiet" id="revoke-authorization" type="button">撤销授权</button>` : `<button class="btn btn-quiet" id="open-trace" type="button">查看完整 Trace</button>`}</div>
  </div>`;
  document.getElementById("approve-recommendation")?.addEventListener("click", () => resolveReview("approved"));
  document.getElementById("reject-recommendation")?.addEventListener("click", () => resolveReview("rejected"));
  document.getElementById("request-evidence")?.addEventListener("click", () => resolveReview("request-more-evidence"));
  document.getElementById("revise-recommendation")?.addEventListener("click", reviseRecommendation);
  document.getElementById("revoke-authorization")?.addEventListener("click", revokeAuthorization);
  document.getElementById("open-trace")?.addEventListener("click", () => showToast("已定位到本次 Run 的完整 Trace", "success"));
}

function renderReview() {
  if (!state.review || state.review.status !== "pending") {
    const text = state.review?.status === "approved" ? "已批准，Gate 正在生成短时授权。" : state.review?.status === "rejected" ? "已拒绝，动作不会进入执行层。" : "等待新的人工确认请求。";
    els.reviewCard.innerHTML = `<div class="empty-review">${esc(text)}</div>`;
    els.queueCount.textContent = "0";
    return;
  }
  els.queueCount.textContent = "1";
  els.reviewCard.innerHTML = `<div class="review-card"><div class="review-title">${esc(state.review.action)}</div><div class="review-meta"><span>${esc(state.review.requester)}</span><i class="dot"></i><span>${esc(state.review.age)}</span><span class="state-pill pending">in-the-loop</span></div><div class="review-reason">${esc(state.review.reason)}</div><div class="review-buttons"><button class="btn btn-primary" id="review-approve" type="button">批准推荐</button><button class="btn btn-quiet" id="review-reject" type="button">拒绝</button></div><button class="event-link" id="review-more" type="button">查看请求与证据 →</button></div>`;
  document.getElementById("review-approve").addEventListener("click", () => resolveReview("approved"));
  document.getElementById("review-reject").addEventListener("click", () => resolveReview("rejected"));
  document.getElementById("review-more").addEventListener("click", () => selectEvent("evt-006"));
}

function renderEvidence() {
  const selected = state.events.find((event) => event.eventId === state.selectedEvent) || state.events[state.events.length - 1];
  const refs = selected?.evidence || [RUN_ID];
  els.evidenceBody.innerHTML = `<div class="evidence-body"><p class="evidence-intro">${esc(selected?.eventType || "RuntimeEnvelope")} · ${esc(selected?.eventId || "")}</p><ul class="evidence-list">${refs.map((ref, index) => `<li><span class="evidence-icon">${index === 0 ? "◇" : "#"}</span><span><b>${index === 0 ? "来源证据" : "关联引用"}</b><span>${esc(ref)}</span></span></li>`).join("")}</ul><div class="rule-check"><i>✓</i><span>schema.agent.extension@0.9.4 校验通过</span></div>${selected?.eventType === "ApprovalRequest" ? `<div class="rule-check fail"><i>!</i><span>范围受限规则待人工确认，未授权前不会执行</span></div>` : ""}</div>`;
}

function renderMessages() {
  els.copilotMessages.innerHTML = state.messages.map((message) => `<div class="message ${message.from === "user" ? "user" : "assistant"}"><span class="message-avatar">${message.from === "user" ? "我" : "✦"}</span><div class="message-bubble">${esc(message.text)}${message.meta ? `<small>${esc(message.meta)}</small>` : ""}</div></div>`).join("");
  els.copilotMessages.scrollTop = els.copilotMessages.scrollHeight;
}
function render() { renderAgents(); renderTimeline(); renderRecommendation(); renderReview(); renderEvidence(); renderMessages(); els.runStatus.textContent = state.status === "paused" ? "已暂停" : API_ENABLED ? "API 运行中" : "Demo 回放"; if (els.runIdChip) els.runIdChip.textContent = RUN_ID; els.pauseRun.textContent = state.status === "paused" ? "恢复 Run" : "暂停 Run"; }
function selectEvent(eventId) { state.selectedEvent = eventId; renderEvidence(); }
function appendEvent(type, title, detail, tone = "default", evidence = [], { persist = true } = {}) {
  const event = { eventId: uid("evt"), eventType: type, actor: "operator:hmily", time: now(), title, detail, tone, evidence };
  state.events.push(event); state.selectedEvent = event.eventId; renderTimeline(); renderEvidence();
  if (API_ENABLED && persist) {
    void apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/events`, { method: "POST", body: JSON.stringify({
      eventId: event.eventId, eventType: type, actorIri: event.actor, subjectIri: RUN_ID,
      payload: { title, detail, tone, evidenceUris: evidence }, evidence: evidence.map((sourceIri) => ({ sourceIri, relation: "supports" }))
    }) }).catch(() => undefined);
  }
  return event;
}

async function apiRequest(path, options = {}) {
  if (!API_ENABLED) return null;
  const response = await fetch(`${API_ROOT}${path}`, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.status === 204 ? null : response.json();
}

async function resolveReview(decision) {
  if (!state.review || state.review.status !== "pending") return;
  const reviewId = state.review.id;
  let persistedDecision = false;
  try {
    const response = await apiRequest(`/human-reviews/${encodeURIComponent(reviewId)}/decision`, { method: "POST", body: JSON.stringify({
      reviewId, runId: RUN_ID, decision, decidedBy: "operator:hmily",
      reason: decision === "approved" ? "批准低风险证据采集。" : decision === "rejected" ? "当前证据不足，拒绝本次请求。" : "需要补充来源证据后再评估。",
      actionRequestIri: state.review.actionRequestIri, recommendationIri: state.review.recommendationIri,
      decidedAt: new Date().toISOString(), evidence: ["kb:coast-raid-k@0.9.4"]
    }) });
    persistedDecision = Boolean(response?.review);
  } catch (error) {
    if (API_ENABLED) showToast("服务端暂不可用，已保留本地演示状态", "warn");
  }
  state.review.status = decision === "approved" ? "approved" : decision === "rejected" ? "rejected" : "evidence";
  if (decision === "approved") {
    state.recommendation.status = "approved";
    appendEvent("HumanDecision", "人工批准推荐", "操作员批准 DecisionDraft v1；Gate 开始最终规则复核。", "success", ["operator:hmily", REVIEW_ID, "decision:approved"], { persist: !persistedDecision });
    appendEvent("Authorization", "Gate 发布短时授权", "allow · evidence-collection · TTL 90s · 可撤销 · 作用范围：知识库运行上下文", "success", [AUTHORIZATION_IRI, "policy:evidence-collection", "ttl:90s"]);
    state.authorization = { id: AUTHORIZATION_IRI, status: "active" };
    appendEvent("ActionExecutionStarted", "Effectors 开始执行", "collect-evidence · 对象 record-17 · 执行器已确认接收", "success", [`${RUN_ID}:execution-1`, "effectors.evidence-collector"]);
    appendEvent("EffectRecorded", "证据效果已记录", "record-17 · 版本 v4 · 仅写入运行库读模型", "success", [`${RUN_ID}:effect-1`, "runtime-entity:record-17"]);
    appendEvent("ActionExecutionCompleted", "执行完成", "证据采集完成，结果已写入可回放 Trace", "success", [`${RUN_ID}:execution-1`, `${RUN_ID}:effect-1`]);
    showToast("已批准。Gate 发布 90 秒短时授权，执行已开始。", "success");
  } else if (decision === "rejected") {
    state.recommendation.status = "rejected";
    appendEvent("HumanDecision", "人工拒绝推荐", "操作员拒绝该 ActionRequest；不会产生可执行授权。", "danger", ["operator:hmily", "decision:rejected"], { persist: !persistedDecision });
    appendEvent("Authorization", "Gate 拒绝授权", "deny · action-request · 原因：人工拒绝", "danger", [`${RUN_ID}:authorization-deny-1`, "policy:human-decision"]);
    showToast("已拒绝，动作请求留存于审计轨迹。", "danger");
  } else {
    state.recommendation.status = "evidence";
    appendEvent("HumanDecision", "请求补充证据", "操作员要求先获取来源证据，再重新评估。", "warning", ["operator:hmily", "decision:request-more-evidence"], { persist: !persistedDecision });
    appendEvent("ObservationRequested", "已创建补证任务", "等待 World Agent 返回来源与版本证据。", "warning", ["task:collect-evidence-17"]);
    showToast("已创建补证任务，完成后会生成新的推荐版本。", "warn");
  }
  renderRecommendation(); renderReview(); renderTimeline(); renderEvidence();
}

function reviseRecommendation() {
  state.messages.push({ from: "assistant", text: "可以修改作用范围、观察窗口或备选方案。请在下方告诉我希望调整的字段；修改将生成 ActionRequest v2，并重新经过 Gate 评估。", meta: "原请求 v1 将保留在 Trace 中" });
  renderMessages();
  document.querySelector('[data-view="copilot"]')?.click();
  els.copilotInput.focus();
}

function revokeAuthorization() {
  if (!state.authorization || state.authorization.status !== "active") return;
  state.authorization.status = "revoked";
  appendEvent("AuthorizationRevoked", "授权已撤销", `操作员撤销 ${AUTHORIZATION_IRI}；执行器收到中止信号。`, "danger", [AUTHORIZATION_IRI, "operator:hmily"]);
  appendEvent("ActionExecutionAborted", "执行已中止", "当前证据采集安全停止，未产生不可逆效果。", "danger", [`${RUN_ID}:execution-1`, "abort:operator-revoke"]);
  appendEvent("SafeFallbackTriggered", "进入安全回退", "保持观察，等待下一次证据更新。", "warning", ["fallback:observe-only"]);
  state.recommendation.status = "rejected";
  render();
  showToast("授权已撤销，执行已进入安全回退。", "warn");
}

function toggleRun() {
  state.status = state.status === "paused" ? "running" : "paused";
  appendEvent(state.status === "paused" ? "RunPaused" : "RunResumed", state.status === "paused" ? "Run 已暂停" : "Run 已恢复", state.status === "paused" ? "新动作请求将暂缓，已有授权仍受 TTL 与撤销条件约束。" : "事件分发器恢复接收新事件。", state.status === "paused" ? "warning" : "success", ["operator:hmily", `run:${state.status}`]);
  render();
  showToast(state.status === "paused" ? "Run 已暂停" : "Run 已恢复", state.status === "paused" ? "warn" : "success");
}
function requestReplay() { appendEvent("ReplayRequested", "已请求 Trace 回放", "将按 causationId 重建 Observation → Belief → Recommendation → HumanDecision → Execution。", "default", [RUN_ID, "replay:causal-trace"]); renderTimeline(); renderEvidence(); showToast("回放请求已加入事件链", "success"); }
function showToast(text, tone = "success") { const toast = document.createElement("div"); toast.className = `toast ${tone}`; toast.textContent = text; els.toastStack.appendChild(toast); setTimeout(() => toast.remove(), 3800); }

function sendCopilot(event) {
  event.preventDefault();
  const input = els.copilotInput.value.trim();
  if (!input) return;
  state.messages.push({ from: "user", text: input });
  const isStatus = /状态|进度|发生|当前/.test(input);
  const reply = isStatus ? "当前 Run 正在等待人工确认。Gate 已完成预检查：证据采集方案可逆且范围受限，其他未满足证据要求的动作不会获得授权。" : "我会基于当前知识包和事件 Trace 分析这项请求。推荐仍需经过 Gate，并在需要时进入人工确认队列。";
  state.messages.push({ from: "assistant", text: reply, meta: `上下文：${RUN_ID} · coast-raid-k@0.9.4 · 引用当前 Trace` });
  appendEvent("CopilotCommand", "Copilot 收到操作员请求", input, "default", [`assistant-session:${RUN_ID}`, "contextHash:runtime"]);
  els.copilotInput.value = "";
  renderMessages(); renderTimeline();
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => { item.classList.toggle("active", item === tab); item.setAttribute("aria-selected", item === tab ? "true" : "false"); });
  document.querySelectorAll(".view-pane").forEach((pane) => { const active = pane.id === `${tab.dataset.view}-view`; pane.classList.toggle("active", active); pane.hidden = !active; });
}));
document.querySelectorAll(".filter-chip").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".filter-chip").forEach((item) => item.classList.toggle("active", item === button)); state.filter = button.dataset.filter; renderTimeline();
}));
els.pauseRun.addEventListener("click", toggleRun);
document.getElementById("replay-run").addEventListener("click", requestReplay);
document.getElementById("refresh-run").addEventListener("click", () => { showToast("运行状态已刷新", "success"); render(); });
document.getElementById("queue-refresh").addEventListener("click", () => { showToast("人工队列已刷新", "success"); renderReview(); });
document.getElementById("close-evidence").addEventListener("click", () => { els.evidenceBody.innerHTML = `<div class="empty-review">选择时间线事件查看其来源、规则与上下文引用。</div>`; });
document.getElementById("new-task").addEventListener("click", () => {
  state.messages.push({ from: "assistant", text: "新任务草案已创建。请描述目标、范围和期望的人机控制模式。", meta: "TaskDraft · 尚未提交" });
  document.querySelector('[data-view="copilot"]')?.click(); renderMessages(); els.copilotInput.focus();
});
els.copilotForm.addEventListener("submit", sendCopilot);
els.copilotInput.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); els.copilotForm.requestSubmit(); } });

async function bootstrap() {
  render();
  if (!API_ENABLED) return;
  try {
    let trace;
    try { trace = await apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/trace`); }
    catch (error) {
      await apiRequest("/runs", { method: "POST", body: JSON.stringify({ runId: RUN_ID, packageId: "coast-raid-k", packageVersion: "0.9.4", modelId: "workbench-demo", modelRevision: "demo-v1", scenario: "evidence-governance", status: "running" }) });
      await apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/events`, { method: "POST", body: JSON.stringify({ eventType: "RunStarted", payload: { title: "Run 已启动", detail: "工作台运行上下文已创建", tone: "success", evidenceUris: ["kb:coast-raid-k@0.9.4"] }, evidence: ["kb:coast-raid-k@0.9.4"] }) });
      await apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/human-reviews`, { method: "POST", body: JSON.stringify({ reviewId: state.review.id, actionRequestIri: state.review.actionRequestIri, recommendationIri: state.review.recommendationIri, requestedBy: "agent:options", reason: state.review.reason, payload: { action: state.review.action } }) });
      trace = await apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/trace`);
    }
    const queue = await apiRequest(`/runs/${encodeURIComponent(RUN_ID)}/human-queue`);
    if (Array.isArray(trace?.events)) state.events = trace.events.map((event) => ({
      eventId: event.eventId, eventType: event.eventType, actor: event.actorIri || "runtime",
      time: event.occurredAt ? new Date(event.occurredAt).toLocaleTimeString("zh-CN", { hour12: false }) : now(),
      title: event.payload?.title || event.eventType, detail: event.payload?.detail || event.payload?.reason || "运行事件已记录",
      tone: event.payload?.tone || "default", evidence: (event.payload?.evidenceUris || []).concat(event.payloadSha256 ? [`sha256:${event.payloadSha256.slice(0, 12)}…`] : [])
    }));
    const remoteReview = queue?.reviews?.[0];
    if (remoteReview) state.review = { ...state.review, ...remoteReview, id: remoteReview.reviewId, action: remoteReview.payload?.action || remoteReview.actionRequestIri, requester: remoteReview.requestedBy || "runtime", age: "刚刚" };
    render();
  } catch (error) { showToast("运行服务未连接，已切换到本地演示数据", "warn"); }
}
bootstrap();
