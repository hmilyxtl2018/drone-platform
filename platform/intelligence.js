const API_ENABLED = !new URLSearchParams(location.search).has("demo");
const API_ROOT = "/api/intelligence";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const uid = (prefix) => `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
const isoNow = () => new Date().toISOString();
const shortTime = (value) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "刚刚";
const hash = (value) => {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) h = Math.imul(h ^ value.charCodeAt(i), 16777619);
  return `fnv1a:${(h >>> 0).toString(16).padStart(8, "0")}`;
};
const normalizeCandidate = (candidate) => candidate ? { ...candidate, id: candidate.candidateId || candidate.id } : candidate;
const uncertaintyPercent = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) return `${Math.round(value * 100)}%`;
  if (value && typeof value === "object" && Number.isFinite(Number(value.value))) return `${Math.round(Number(value.value) * 100)}%`;
  return "待评估";
};
const normalizeDocument = (document) => document ? { ...document, id: document.documentId || document.id } : document;
const normalizeProvider = (provider) => typeof provider === "string" ? { id: provider } : provider && typeof provider === "object" ? { ...provider } : null;
const displayValue = (value) => value == null || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
const uncertaintyFlags = (uncertainty) => {
  const flags = Array.isArray(uncertainty) ? uncertainty : uncertainty?.flags;
  return Array.isArray(flags) ? flags : typeof flags === "string" ? [flags] : [];
};
const uncertaintySummary = (value) => uncertaintyFlags(value).length ? `${uncertaintyFlags(value).length} 项待核验标记` : uncertaintyPercent(value);
function providerLabel(provider) {
  if (!provider?.id) return "状态未读取";
  const id = String(provider.id);
  if (id === "deepseek-claim-provider") return `DeepSeek · ${provider.model || "模型未记录"} · ${provider.configured === true ? "已配置" : "配置待确认"}`;
  if (id.startsWith("deterministic-")) return "规则模式 · 确定性提取";
  return `${id}${provider.model ? ` · ${provider.model}` : ""}`;
}

const demoDocument = {
  id: "doc-demo-runbook-09",
  title: "知识库运行规范更新",
  sourceUri: "demo://governance/runbook-2026-09",
  sourceVersion: "2026.09",
  accessBasis: "public",
  sourceReliability: "primary",
  status: "extracted",
  candidateCount: 3,
  createdAt: "2026-09-28T08:42:00Z",
};
const demoCandidates = [
  { id: "candidate-retention", documentId: demoDocument.id, claimId: "claim-retention", revision: 1, statement: "文档归档的保留期限为 30 天。", originalStatement: "文档归档的保留期限为 30 天。", subject: "文档归档", predicate: "保留期限", value: "30 天", status: "pending-review", uncertainty: 0.08, concepts: [{ iri: "crk:DocumentArchive", label: "文档归档", keyword: "文档" }], conflictIds: ["conflict-retention"], conflicts: [{ id: "conflict-retention", existingStatement: "文档归档的保留期限为 90 天。", source: "运行规范 v0.8.2", severity: "high" }], recommendation: { action: "review-conflict", reason: "与已发布版本中的保留期限不同，请确认适用范围。" }, evidence: { documentId: demoDocument.id, sourceUri: demoDocument.sourceUri, sourceVersion: demoDocument.sourceVersion, lineStart: 1, lineEnd: 1, quote: "文档归档 | 保留期限 | 30 天", contentHash: "demo-sha256:retention-30d" }, createdAt: demoDocument.createdAt },
  { id: "candidate-audit", documentId: demoDocument.id, claimId: "claim-audit", revision: 1, statement: "运行事件采用 append-only 审计方式。", originalStatement: "运行事件采用 append-only 审计方式。", subject: "运行事件", predicate: "审计方式", value: "append-only", status: "pending-review", uncertainty: 0.04, concepts: [{ iri: "crk:RuntimeEvent", label: "运行事件", keyword: "事件" }], conflictIds: [], conflicts: [], recommendation: { action: "accept", reason: "与运行契约一致，证据片段明确。" }, evidence: { documentId: demoDocument.id, sourceUri: demoDocument.sourceUri, sourceVersion: demoDocument.sourceVersion, lineStart: 2, lineEnd: 2, quote: "运行事件 | 审计方式 | append-only", contentHash: "demo-sha256:audit-append-only" }, createdAt: demoDocument.createdAt },
  { id: "candidate-human", documentId: demoDocument.id, claimId: "claim-human", revision: 1, statement: "人工审核完成逐条确认后才可发布。", originalStatement: "人工审核完成逐条确认后才可发布。", subject: "人工审核", predicate: "发布条件", value: "逐条确认", status: "pending-review", uncertainty: 0.03, concepts: [{ iri: "crk:HumanReview", label: "人工审核", keyword: "审核" }], conflictIds: [], conflicts: [], recommendation: { action: "accept", reason: "符合人在回路约束，建议纳入规则说明。" }, evidence: { documentId: demoDocument.id, sourceUri: demoDocument.sourceUri, sourceVersion: demoDocument.sourceVersion, lineStart: 3, lineEnd: 3, quote: "人工审核 | 发布条件 | 逐条确认", contentHash: "demo-sha256:human-review" }, createdAt: demoDocument.createdAt },
];
const state = {
  documents: [demoDocument], candidates: demoCandidates, claims: [], conflicts: demoCandidates.flatMap((item) => item.conflicts || []), reviews: [], versions: [{ id: "kb-v0.9.4", version: "0.9.4", title: "知识库运行规范基线", candidateIds: [], reviewer: "system", createdAt: "2026-09-27T10:20:00Z" }], events: [], provider: API_ENABLED ? null : { id: "deterministic-extractor-1.0", model: null, configured: false, llmMayProposeOnly: true }, filter: "all", selectedId: demoCandidates[0].id, selectedDocumentId: demoDocument.id, selectedIds: new Set(), evidenceOpen: true, source: "demo",
};
const el = (id) => document.getElementById(id);
const els = { service: el("service-status"), provider: el("provider-label"), form: el("source-form"), docs: el("document-list"), candidates: el("candidate-list"), empty: el("empty-state"), pending: el("pending-count"), conflict: el("conflict-count"), reviewed: el("reviewed-count"), docCount: el("document-count"), claimStatus: el("claim-status"), claim: el("claim-detail"), conflictBody: el("conflict-detail"), conflictSummary: el("conflict-summary"), evidence: el("evidence-detail"), versions: el("version-list"), publish: el("publish-version"), toast: el("toast-stack") };

function notify(message, tone = "success") {
  const node = document.createElement("div"); node.className = `toast ${tone}`; node.textContent = message; els.toast.append(node);
  window.setTimeout(() => node.remove(), 3400);
}

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function normalizeState(payload) {
  if (!payload) return;
  if (Array.isArray(payload.documents)) state.documents = payload.documents.map(normalizeDocument);
  for (const key of ["claims", "conflicts", "reviews", "versions", "events"]) if (Array.isArray(payload[key])) state[key] = payload[key];
  if (Array.isArray(payload.candidates)) state.candidates = payload.candidates.map(normalizeCandidate);
  state.provider = normalizeProvider(payload.provider);
  state.source = "api";

  if (!state.documents.some((document) => document.id === state.selectedDocumentId)) state.selectedDocumentId = state.documents[0]?.id || null;
  if (!state.candidates.some((candidate) => candidate.id === state.selectedId && candidate.documentId === state.selectedDocumentId)) state.selectedId = state.candidates.find((candidate) => candidate.documentId === state.selectedDocumentId)?.id || null;
}

function statusLabel(status) {
  return { "pending-review": ["待审核", "pending"], "under-review": ["待审核", "pending"], new: ["待审核", "pending"], accepted: ["已采纳", "approved"], edited: ["已编辑", "approved"], rejected: ["已拒绝", "rejected"], "needs-evidence": ["待补证", "muted"], published: ["已发布", "approved"], superseded: ["已替代", "muted"] }[status] || [status || "未知", "muted"];
}
function activeConflicts(candidate) {
  const conflicts = candidate.conflicts || state.conflicts.filter((item) => (candidate.conflictIds || []).includes(item.id || item.conflictId));
  return conflicts.filter((conflict) => !conflict.status || ["open", "unresolved"].includes(conflict.status));
}

function filteredCandidates() {
  return state.candidates.filter((candidate) => {
    if (state.selectedDocumentId && candidate.documentId && candidate.documentId !== state.selectedDocumentId) return false;
    if (state.filter === "conflict") return (candidate.conflictIds || []).length > 0;
    if (state.filter === "reviewed") return ["accepted", "edited", "rejected", "needs-evidence", "published", "superseded"].includes(candidate.status);
    return !["rejected", "published", "superseded"].includes(candidate.status);
  });
}

function renderDocuments() {
  els.docCount.textContent = state.documents.length;
  els.docs.innerHTML = state.documents.length ? state.documents.map((doc) => `<button type="button" class="document-card ${doc.id === state.selectedDocumentId ? "active" : ""}" data-document-id="${esc(doc.id)}"><span class="document-title">${esc(doc.title || "未命名资料")}</span><span class="document-meta"><span>${esc(doc.sourceVersion || "未标版本")}</span><span>${doc.candidateCount ?? state.candidates.filter((c) => c.documentId === doc.id).length} 条候选</span></span><span class="document-meta"><span class="document-state">${esc(doc.status || "已接收")}</span><span>${shortTime(doc.createdAt)}</span></span></button>`).join("") : `<div class="placeholder">还没有资料。提交一份资料开始。</div>`;
  els.docs.querySelectorAll("[data-document-id]").forEach((node) => node.addEventListener("click", () => { state.selectedDocumentId = node.dataset.documentId; const first = state.candidates.find((candidate) => candidate.documentId === state.selectedDocumentId); state.selectedId = first?.id || null; render(); }));
}

function renderCandidates() {
  const candidates = filteredCandidates();
  const allCurrent = state.candidates.filter((candidate) => !["rejected", "published", "superseded"].includes(candidate.status));
  const conflicts = state.candidates.filter((candidate) => activeConflicts(candidate).length > 0 && candidate.status !== "published");
  const reviewed = state.candidates.filter((candidate) => ["accepted", "edited", "rejected", "needs-evidence", "published"].includes(candidate.status));
  els.pending.textContent = allCurrent.filter((candidate) => ["pending-review", "under-review", "new"].includes(candidate.status)).length;
  els.conflict.textContent = conflicts.length;
  els.reviewed.textContent = reviewed.length;
  els.empty.hidden = candidates.length > 0;
  els.candidates.innerHTML = candidates.map((candidate) => {
    const [label, tone] = statusLabel(candidate.status);
    const checked = state.selectedIds.has(candidate.id) ? "checked" : "";
    const hasConflict = activeConflicts(candidate).length > 0;
    const canReview = ["pending-review", "under-review", "new", "needs-evidence"].includes(candidate.status);
    return `<article class="candidate-card ${candidate.id === state.selectedId ? "selected" : ""}" data-candidate-id="${esc(candidate.id)}"><label class="candidate-check" title="选择发布"><input type="checkbox" data-select-id="${esc(candidate.id)}" ${checked} ${candidate.status === "rejected" ? "disabled" : ""} /></label><div class="candidate-body"><div class="candidate-top"><span class="candidate-subject">${esc(candidate.subject || "未标注主题")}</span><span class="candidate-arrow">/</span><span class="candidate-value">${esc(candidate.predicate || "")}: ${esc(candidate.value || "")}</span>${hasConflict ? `<span class="conflict-flag">⚠ 冲突 ${(candidate.conflictIds || []).length}</span>` : ""}</div><p class="candidate-statement">${esc(candidate.statement || "")}</p><div class="candidate-meta"><span>v${candidate.revision || 1}</span><span class="match">${esc(candidate.concepts?.[0]?.label || "本体待匹配")}${candidate.concepts?.length > 1 ? ` 等 ${candidate.concepts.length} 项` : ""}</span><span>不确定性 ${esc(uncertaintySummary(candidate.uncertainty))}</span><span>${shortTime(candidate.createdAt)}</span></div></div><div class="candidate-side"><span class="state-pill ${tone}">${label}</span><span class="mono">${esc(candidate.id)}</span></div>${canReview ? `<div class="candidate-actions"><button type="button" class="btn btn-quiet" data-action="accept" data-candidate="${esc(candidate.id)}">采纳</button><button type="button" class="btn btn-quiet" data-action="edit" data-candidate="${esc(candidate.id)}">编辑</button><button type="button" class="btn btn-quiet" data-action="evidence" data-candidate="${esc(candidate.id)}">要求补证</button><button type="button" class="btn btn-quiet" data-action="reject" data-candidate="${esc(candidate.id)}">拒绝</button></div>` : ""}</article>`;
  }).join("");
  els.candidates.querySelectorAll("[data-candidate-id]").forEach((node) => node.addEventListener("click", (event) => { if (event.target.closest("button,input")) return; state.selectedId = node.dataset.candidateId; render(); }));
  els.candidates.querySelectorAll("[data-select-id]").forEach((node) => node.addEventListener("click", (event) => { event.stopPropagation(); node.checked ? state.selectedIds.add(node.dataset.selectId) : state.selectedIds.delete(node.dataset.selectId); updatePublishButton(); }));
  els.candidates.querySelectorAll("[data-action]").forEach((node) => node.addEventListener("click", async (event) => { event.stopPropagation(); state.selectedId = node.dataset.candidate; renderClaim(); el("review-reason")?.focus(); notify("请核对右侧声明并填写审核理由，再提交决定。", "warn"); }));
  updatePublishButton();
}

function renderClaim() {
  const candidate = state.candidates.find((item) => item.id === state.selectedId);
  if (!candidate) { els.claimStatus.textContent = "待选择"; els.claimStatus.className = "state-pill pending"; els.claim.innerHTML = `<div class="placeholder">从中间列表选择一条候选声明。</div>`; els.conflictSummary.textContent = "—"; els.conflictBody.innerHTML = `<div class="placeholder">没有选中的冲突。</div>`; els.evidence.innerHTML = `<div class="placeholder">选择候选声明后查看来源、行号和哈希。</div>`; return; }
  const [label, tone] = statusLabel(candidate.status); els.claimStatus.textContent = label; els.claimStatus.className = `state-pill ${tone}`;
  const editable = ["pending-review", "under-review", "new", "needs-evidence", "accepted"].includes(candidate.status);
  const recommendation = candidate.recommendation || {};
  const flags = uncertaintyFlags(candidate.uncertainty);
  const fallback = flags.includes("llm-fallback");
  const concepts = (candidate.concepts || []).map((concept) => `<div><b>${esc(concept.label || concept.iri || displayValue(concept))}</b>${concept.iri ? `<br><span class="mono">${esc(concept.iri)}</span>` : ""}${concept.keyword || concept.matchMethod ? `<br><span>${esc([concept.keyword, concept.matchMethod].filter(Boolean).join(" · "))}</span>` : ""}</div>`).join("<br>");
  const actualProvider = recommendation.provider || "未记录";
  const actualModel = recommendation.model || (String(actualProvider).startsWith("deterministic-") ? "不适用（规则提取）" : "未记录");
  els.claim.innerHTML = `<div class="claim-statement">${esc(candidate.statement || "")}</div><dl class="claim-fields"><dt>关系主体</dt><dd>${esc(displayValue(candidate.subject))}</dd><dt>关系谓词</dt><dd>${esc(displayValue(candidate.predicate))}</dd><dt>关系值</dt><dd>${esc(displayValue(candidate.value))}</dd><dt>全部本体匹配</dt><dd style="overflow-wrap:anywhere">${concepts || "待匹配"}</dd><dt>实际提取器</dt><dd style="overflow-wrap:anywhere">${esc(actualProvider)}${fallback ? "<br><b>已回退到规则提取</b>" : ""}</dd><dt>实际模型</dt><dd style="overflow-wrap:anywhere">${esc(actualModel)}</dd><dt>不确定性</dt><dd>${esc(uncertaintySummary(candidate.uncertainty))}</dd><dt>核验标记</dt><dd style="overflow-wrap:anywhere">${flags.length ? flags.map((flag) => `<div>${esc(displayValue(flag))}</div>`).join("") : "未记录标记"}</dd><dt>推荐</dt><dd>${esc(typeof recommendation === "string" ? recommendation : recommendation.reason || "—")}</dd></dl>${editable ? `<div class="review-editor"><label>声明（可编辑自然语言；关系修改用 主题 | 谓词 | 值）<textarea id="review-text">${esc(candidate.statement || "")}</textarea></label><label>审核理由<textarea id="review-reason" placeholder="填写证据判断、修改或冲突处理的依据"></textarea></label><div class="review-actions"><button class="btn btn-quiet" type="button" data-detail-action="accept">采纳</button><button class="btn btn-quiet" type="button" data-detail-action="edit">保存修改</button><button class="btn btn-quiet" type="button" data-detail-action="evidence">要求补证</button><button class="btn btn-quiet" type="button" data-detail-action="reject">拒绝</button></div></div>` : `<div class="review-outcome">${candidate.latestReviewId ? `最近审核：<span class="mono">${esc(candidate.latestReviewId)}</span>` : "本条已完成审核。"}</div>`}`;
  els.claim.querySelectorAll("[data-detail-action]").forEach((node) => node.addEventListener("click", () => reviewCandidate(candidate.id, node.dataset.detailAction, el("review-text")?.value)));
  const conflicts = activeConflicts(candidate);
  els.conflictSummary.textContent = conflicts.length ? `${conflicts.length} 条待决` : "无冲突";
  els.conflictBody.innerHTML = conflicts.length ? conflicts.map((conflict) => { const known = conflict.existingStatement || conflict.statement; return `<div class="conflict-card"><strong>${known ? "现有知识" : "冲突说明"}：${esc(known || conflict.reason || "")}</strong><p>${esc(conflict.source || (known ? "已发布版本" : "检测到同主题/谓词存在不同值"))} · 严重度 ${esc(conflict.severity || "medium")}</p><span class="old-value">${esc(conflict.id || conflict.conflictId || "conflict")}</span>${editable ? `<div class="conflict-actions"><button class="btn btn-quiet" type="button" data-conflict-action="replace-existing">采纳并替换现有</button><button class="btn btn-quiet" type="button" data-conflict-action="keep-both">保留两者</button></div>` : ""}</div>`; }).join("") : `<div class="placeholder">未发现当前情报库中同主题、同谓词的未解决差异。</div>`;
  els.conflictBody.querySelectorAll("[data-conflict-action]").forEach((node) => node.addEventListener("click", () => reviewCandidate(candidate.id, "accept", el("review-text")?.value, node.dataset.conflictAction)));
  const evidence = candidate.evidence || {};
  els.evidence.innerHTML = `<div class="evidence-row"><span class="evidence-icon">↗</span><div><b>来源文档</b><span>${esc(evidence.sourceUri || candidate.documentId || "—")}</span></div></div><div class="evidence-row"><span class="evidence-icon">#</span><div><b>版本与行号</b><span>${esc(evidence.sourceVersion || "—")} · L${evidence.lineStart || "?"}–${evidence.lineEnd || evidence.lineStart || "?"}</span></div></div><div class="evidence-row"><span class="evidence-icon">◇</span><div><b>内容哈希</b><span>${esc(evidence.contentHash || "not-computed")}</span></div></div><div class="evidence-quote">${esc(evidence.quote || candidate.originalStatement || "没有引用片段")}</div>`;
}

function renderVersions() {
  els.versions.innerHTML = state.versions.length ? state.versions.slice().reverse().map((version) => `<div class="version-item"><div><strong>${esc(version.version || version.id || "未命名版本")}</strong><span>${esc(version.title || "知识治理版本")} · ${esc(version.publishedBy || version.reviewer || "system")}</span></div><time>${shortTime(version.publishedAt || version.createdAt)}</time></div>`).join("") : `<div class="placeholder">尚未发布新版本。</div>`;
}

function updatePublishButton() {
  const publishable = [...state.selectedIds].filter((id) => ["accepted"].includes(state.candidates.find((candidate) => candidate.id === id)?.status));
  els.publish.disabled = publishable.length === 0;
  els.publish.textContent = publishable.length ? `发布知识版本 · ${publishable.length}` : "发布知识版本";
}

function render() {
  els.provider.textContent = providerLabel(state.provider);
  els.provider.title = state.provider ? `${state.provider.id}${state.provider.fallback ? `；失败时回退：${state.provider.fallback}` : ""}；候选的实际提取器和模型见声明详情` : "尚未读取提取服务状态";
  renderDocuments(); renderCandidates(); renderClaim(); renderVersions();
}

function parseCandidates(body, documentId, documentInfo) {
  return body.split(/\r?\n/).map((line, index) => line.trim()).filter(Boolean).map((line, index) => {
    const bits = line.split("|").map((part) => part.trim()); const subject = bits[0] || "未标注主题"; const predicate = bits[1] || "资料内容"; const value = bits.slice(2).join(" | ") || bits[1] || line; const text = bits.length >= 3 ? `${subject}的${predicate}为${value}。` : line;
    return { id: uid("candidate"), documentId, claimId: uid("claim"), revision: 1, statement: text, originalStatement: text, subject, predicate, value, status: "pending-review", uncertainty: bits.length >= 3 ? 0.08 : 0.35, concepts: [], conflictIds: [], conflicts: [], recommendation: { action: "review", reason: "Agent 已提取结构化候选，等待人工确认。" }, evidence: { documentId, sourceUri: documentInfo.sourceUri, sourceVersion: documentInfo.sourceVersion, lineStart: index + 1, lineEnd: index + 1, quote: line, contentHash: `demo-hash:${hash(line)}` }, createdAt: isoNow() };
  });
}

async function submitDocument(event) {
  event.preventDefault(); const button = els.form.querySelector("button[type=submit]"); button.disabled = true; button.firstElementChild.textContent = "提取中…";
  const payload = Object.fromEntries(new FormData(els.form).entries()); payload.accessBasis = payload.accessBasis || "public"; payload.sourceReliability = payload.sourceReliability || "unknown";
  try {
    if (API_ENABLED) {
      const result = await request("/documents", { method: "POST", body: JSON.stringify(payload) });
      state.selectedDocumentId = result.document?.documentId || result.document?.id;
      state.selectedId = result.candidates?.[0]?.candidateId || result.candidates?.[0]?.id;
      normalizeState(await request("/state"));
    }
    else { const documentId = uid("doc"); const documentInfo = { id: documentId, title: payload.title, sourceUri: payload.sourceUri, sourceVersion: payload.sourceVersion, accessBasis: payload.accessBasis, sourceReliability: payload.sourceReliability, status: "extracted", candidateCount: 0, createdAt: isoNow() }; const candidates = parseCandidates(payload.body, documentId, documentInfo); documentInfo.candidateCount = candidates.length; state.documents.unshift(documentInfo); state.candidates = [...candidates, ...state.candidates]; state.selectedDocumentId = documentId; state.selectedId = candidates[0]?.id || null; }
    notify("资料已留档，候选声明进入人工审核队列。"); render();
  } catch (error) { notify(`资料提交失败：${error.message}`, "danger"); }
  finally { button.disabled = false; button.firstElementChild.textContent = "提取候选声明"; }
}

async function reviewCandidate(id, action, editedStatement, resolveConflict = null) {
  const candidate = state.candidates.find((item) => item.id === id); if (!candidate) return; const actionMap = { accept: "accept", reject: "reject", evidence: "request-more-evidence", edit: "edit" }; const normalized = actionMap[action] || action; const statement = editedStatement?.trim();
  const reason = el("review-reason")?.value.trim();
  if (!reason) { notify("请填写审核理由。", "warn"); el("review-reason")?.focus(); return; }
  try {
    if (API_ENABLED) { const result = await request(`/candidates/${encodeURIComponent(id)}/review`, { method: "POST", body: JSON.stringify({ action: normalized, reviewer: "operator.demo", reason, expectedRevision: candidate.revision, ...(statement ? { editedStatement: statement } : {}), ...(resolveConflict ? { resolveConflict } : {}) }) }); const updated = normalizeCandidate(result.candidate); if (updated) state.candidates = state.candidates.map((item) => item.id === id ? updated : item); if (result.review) state.reviews.unshift(result.review); normalizeState(await request("/state")); }
    else { candidate.status = normalized === "accept" ? "accepted" : normalized === "edit" ? "under-review" : normalized === "reject" ? "rejected" : "needs-evidence"; if (statement && normalized === "edit") candidate.statement = statement; if (resolveConflict) { candidate.conflictIds = []; candidate.conflicts = []; state.conflicts = state.conflicts.filter((conflict) => !(conflict.id === id || conflict.conflictId === id)); } candidate.latestReviewId = uid("review"); state.reviews.unshift({ id: candidate.latestReviewId, candidateId: id, action: normalized, resolveConflict, reviewer: "operator.demo", createdAt: isoNow() }); }
    if (normalized === "accept") state.selectedIds.add(id); else state.selectedIds.delete(id); notify(normalized === "accept" ? "声明已采纳，可加入知识版本发布。" : normalized === "edit" ? "新修订已保存，请核对后再次采纳。" : normalized === "reject" ? "声明已拒绝，原始资料仍保留。" : "已请求补充证据。", normalized === "reject" ? "warn" : "success"); render();
  } catch (error) { notify(`审核操作失败：${error.message}`, "danger"); }
}

async function publishVersion() {
  const ids = [...state.selectedIds].filter((id) => ["accepted"].includes(state.candidates.find((candidate) => candidate.id === id)?.status)); if (!ids.length) return;
  try {
    if (API_ENABLED) { const result = await request("/publish", { method: "POST", body: JSON.stringify({ reviewer: "operator.demo", candidateIds: ids, title: "情报采集 Agent 发布版本" }) }); if (result.version) state.versions.push(result.version); if (Array.isArray(result.candidates)) { const byId = new Map(result.candidates.map((candidate) => [candidate.id, candidate])); state.candidates = state.candidates.map((candidate) => byId.get(candidate.id) || candidate); } else state.candidates = state.candidates.map((candidate) => ids.includes(candidate.id) ? { ...candidate, status: "published" } : candidate); }
    else { const version = { id: uid("kb-v"), version: `0.9.${state.versions.length + 4}`, title: "情报采集 Agent 发布版本", candidateIds: ids, reviewer: "operator.demo", createdAt: isoNow() }; state.versions.push(version); state.candidates = state.candidates.map((candidate) => ids.includes(candidate.id) ? { ...candidate, status: "published" } : candidate); }
    if (API_ENABLED) normalizeState(await request("/state"));
    state.selectedIds.clear(); notify("知识版本已发布，声明与证据链已写入版本记录。", "success"); render();
  } catch (error) { notify(`发布失败：${error.message}`, "danger"); }
}

async function loadState() {
  if (!API_ENABLED) { els.service.textContent = "演示数据 · 本地回放"; render(); return; }
  els.service.textContent = "连接治理 API…";
  try { const payload = await request("/state"); normalizeState(payload); els.service.textContent = "治理链路已同步"; render(); }
  catch (error) { els.service.textContent = "服务未连接 · 请刷新重试"; notify(`无法读取情报 API：${error.message}`, "warn"); render(); }
}

els.form.addEventListener("submit", submitDocument); el("refresh-state").addEventListener("click", loadState); els.publish.addEventListener("click", publishVersion); el("select-accepted").addEventListener("click", () => { state.candidates.filter((candidate) => ["accepted"].includes(candidate.status)).forEach((candidate) => state.selectedIds.add(candidate.id)); render(); }); el("clear-selection").addEventListener("click", () => { state.selectedIds.clear(); render(); }); el("toggle-evidence").addEventListener("click", () => { els.evidence.closest(".evidence-panel").classList.toggle("collapsed"); }); document.querySelectorAll("[data-filter]").forEach((tab) => tab.addEventListener("click", () => { state.filter = tab.dataset.filter; document.querySelectorAll("[data-filter]").forEach((item) => { const active = item === tab; item.classList.toggle("active", active); item.setAttribute("aria-selected", active ? "true" : "false"); }); renderCandidates(); }));
if (API_ENABLED) { for (const key of ["documents", "candidates", "claims", "conflicts", "reviews", "versions", "events"]) state[key] = []; state.selectedId = null; state.selectedDocumentId = null; }
loadState();
