"use strict";
/* Human decisions are explicit. The service owns eligibility, hashes and roles. */
const knowledgeMiniTip = (...args) => typeof miniTip === "function" ? miniTip(...args) : el("span", " ⓘ " + (args[0] || ""), "mini-tip");

async function renderKnowledge(panel, runId) {
  runId = runId || state.runId;
  panel.append(el("h2", "知识变更与发布"), el("p", "知识版本保存经人审的材料和判断。来源真值、来源独立性与工程符合性仍为 unknown；知识发布不构成工程认证。", "help-note help-caution"));
  if (!hasRole("reviewer")) { panel.append(el("p", "需要审核者或管理员角色查看及维护知识变更。", "muted")); return; }
  if (!runId) { panel.append(el("p", "请选择运行，查看候选资格与相关知识版本。", "muted")); return; }
  const root = el("div"); panel.append(root);
  const show = value => value === undefined || value === null ? "unknown" : typeof value === "object" ? JSON.stringify(value) : String(value);
  const detail = (title, value) => { const d = el("details"); d.append(el("summary", title), el("pre", JSON.stringify(value, null, 2))); return d; };
  const input = (parent, title, type = "text") => { const label = el("label", title), node = el(type === "textarea" ? "textarea" : "input"); if (type !== "textarea") node.type = type; label.append(node); parent.append(label); return node; };
  const check = (parent, title) => { const label = el("label", undefined, "check"), node = el("input"); node.type = "checkbox"; label.append(node, document.createTextNode(title)); parent.append(label); return node; };
  const action = (parent, title, callback) => { const b = el("button", title); b.type = "button"; b.onclick = async () => { b.disabled = true; try { await callback(); } catch (e) { notice(e.message, true); } finally { b.disabled = false; } }; parent.append(b); return b; };
  const segment = value => encodeURIComponent(value), base = "/knowledge", selection = [], pendingKeys = new Map();
  const key = id => { if (!pendingKeys.has(id)) pendingKeys.set(id, "knowledge-" + crypto.randomUUID()); return pendingKeys.get(id); };
  const actorId = state.actor?.principal_id || state.actor?.principalId;
  const workbench = await api("/review-workbench/runs/" + segment(runId));
  root.append(el("p", "运行：" + runId), el("p", "来源独立性：unknown；工程认证：false", "muted"));
  const streamBox = el("div"), stream = input(streamBox, "知识流名称（同一主题跨运行沿用）");
  stream.value = workbench.candidates[0]?.entityId || "run:" + runId;
  const listings = el("section"), proposal = el("section"), inspect = el("section"); root.append(streamBox, proposal, listings, inspect);
  proposal.append(el("h3", "选择本次提案的候选"));
  for (const candidate of workbench.candidates || []) {
    const row = el("div", undefined, "candidate"), reviews = (candidate.humanReviews || []).filter(r => r.effective), review = reviews[reviews.length - 1];
    const unresolved = (candidate.conflictIds || []).length > 0 && (review?.decision !== "resolve-conflict" || candidate.conflictIds.some(id => !(workbench.conflicts || []).some(c => c.conflictId === id && c.humanResolutionCurrent)));
    const eligible = !!review && ["accept", "resolve-conflict"].includes(review.decision) && !unresolved;
    const selected = check(row, candidate.metric + " · " + show(candidate.value ?? candidate.range) + " " + show(candidate.unit)); selected.disabled = !eligible;
    const blocked = !review ? "缺少当前有效的人审决定" : !["accept", "resolve-conflict"].includes(review.decision) ? "当前决定为 " + review.decision : unresolved ? "冲突尚无当前有效处理" : "可选";
    const status = el("p", blocked, eligible ? "muted" : "error"); if (eligible) status.append(knowledgeMiniTip("提交时服务端再次验证候选资格。", "info", "候选资格"));
    row.append(status, el("blockquote", show(candidate.quote)), detail("候选、引文与审核记录", candidate)); selection.push({candidate, selected}); proposal.append(row);
  }
  if (!(workbench.candidates || []).length) { const empty = el("p", "当前运行没有已保存候选。"); empty.append(knowledgeMiniTip("缺少已保存候选时不能形成知识提案。", "help", "提案所需材料")); proposal.append(empty); }
  const reason = input(proposal, "提案理由", "textarea"), expiry = input(proposal, "到期时间（可选，ISO 格式及 UTC 偏移）"), preview = el("div"); proposal.append(preview);
  const selectedIds = () => selection.filter(item => item.selected.checked).map(item => item.candidate.candidateId);
  let previewHash = null;
  const currentProposal = () => ({streamKey: stream.value.trim(), candidateIds: selectedIds(), reason: reason.value.trim(), ...(expiry.value.trim() ? {expiresAt: expiry.value.trim()} : {})});
  const invalidate = () => { previewHash = null; preview.replaceChildren(el("p", "输入已变化，请重新预览。", "muted")); pendingKeys.delete("proposal"); };
  for (const item of selection) item.selected.onchange = invalidate; reason.oninput = expiry.oninput = stream.oninput = invalidate;
  action(proposal, "预览提案材料", () => { const body = currentProposal(); if (!body.streamKey || !body.reason || !body.candidateIds.length) throw Error("请填写知识流、提案理由，并选择至少一个合格候选。"); previewHash = JSON.stringify(body); const status = el("p", "待独立批准 · 尚未发布"); status.append(knowledgeMiniTip("确认后创建待独立批准的变更集；本操作尚未发布知识。", "flow", "提案预览")); preview.replaceChildren(status, detail("本次选择与理由", body), detail("拟纳入的候选和引文", selection.filter(item => item.selected.checked).map(item => item.candidate))); });
  action(proposal, "确认创建待批准提案", async () => { const body = currentProposal(); if (!previewHash || JSON.stringify(body) !== previewHash) throw Error("请先预览当前提案材料。"); const result = await api(base + "/runs/" + segment(runId) + "/change-sets", {method: "POST", body: JSON.stringify({...body, idempotencyKey: key("proposal")})}); notice("提案已保存，需另一位审核者查看具体变更并决定。"); await loadStream(); await inspectChange(result.changeSetId); });
  function differences(before, after, path = "$", result = []) { if (result.length >= 500 || JSON.stringify(before) === JSON.stringify(after)) return result; const object = x => x !== null && typeof x === "object" && !Array.isArray(x); if (object(before) && object(after)) for (const field of new Set([...Object.keys(before), ...Object.keys(after)])) differences(before[field], after[field], path + "." + field, result); else result.push({path, before: before === undefined ? "(absent)" : before, after: after === undefined ? "(absent)" : after}); return result; }
  async function inspectChange(id) { const data = await api(base + "/change-sets/" + segment(id)), versions = await api(base + "/publications?streamKey=" + segment(data.streamKey)), prior = versions.find(v => v.version === data.baseVersion), publishedVersion = versions.find(v => v.changeSetId === id); inspect.replaceChildren(el("h3", "审阅变更集：" + id), el("p", "状态：" + data.status + " · 基于版本：" + data.baseVersion), el("p", "提案者：" + data.proposedBy), el("p", "当前变更哈希：" + data.payloadHash), detail("完整变更内容（候选、来源和人审）", data.payload), detail("与基准版本的内容差异", differences(prior?.payload || {}, data.payload)), detail("不可变决定历史", data.events || [])); const reviewed = check(inspect, "我已审阅上述具体内容、差异和来源，并针对显示的哈希作出决定。"), decisionReason = input(inspect, "本次决定理由", "textarea"), independent = !!actorId && actorId !== data.proposedBy; if (!independent) inspect.append(el("p", "提案者需要由另一位审核者批准或拒绝；当前身份不能批准或发布自己的提案。", "help-note help-help")); async function decide(operation) { if (!reviewed.checked || !decisionReason.value.trim()) throw Error("请审阅当前变更，并填写具体决定理由。"); await api(base + "/change-sets/" + segment(id) + "/" + operation, {method: "POST", body: JSON.stringify({expectedHash: data.payloadHash, reason: decisionReason.value.trim(), idempotencyKey: key(id + ":" + operation + ":" + decisionReason.value.trim())})}); notice(operation === "publish" ? "知识版本已显式发布；来源真值与工程认证边界保持。" : "决定已追加保存。"); await loadStream(); await inspectChange(id); } if (data.status === "proposed") for (const [operation, title] of [["approve", "独立批准此变更"], ["reject", "拒绝此变更"]]) { const b = action(inspect, title, () => decide(operation)); b.disabled = !independent; } if (publishedVersion) { const status = el("p", "版本 " + publishedVersion.version + " · " + publishedVersion.status, "muted"); status.append(knowledgeMiniTip("保存内容与原哈希继续保留。", "info", "已发布版本")); inspect.append(status); } else if (data.status === "approved") { inspect.append(el("p", "已批准 · 等待发布", "muted")); const confirmation = input(inspect, "输入“发布”确认此变更的版本发布"); const publish = action(inspect, "明确发布知识版本", async () => { if (confirmation.value.trim() !== "发布") throw Error("请输入“发布”确认具体变更。"); await decide("publish"); }); publish.disabled = !independent; } }
  async function inspectPublication(id) {
    const data = await api(base + "/publications/" + segment(id));
    inspect.replaceChildren(el("h3", "知识版本 " + data.version + " · " + data.status),
      el("p", "发布者：" + (data.publishedBy || "unknown") + " · " + show(data.publishedAt)),
      el("p", "版本哈希：" + data.payloadHash), detail("完整保存内容", data.payload),
      detail("发布与撤回历史", data.events));
    if (data.status === "withdrawn") {
      const status = el("p", "该版本已撤回。", "muted");
      status.append(knowledgeMiniTip("历史和原哈希保留。", "info", "撤回记录")); inspect.append(status); return;
    }
    const reason = input(inspect, "撤回理由", "textarea");
    const confirmed = check(inspect, "确认撤回此版本；历史内容和审核记录继续保留。");
    action(inspect, "明确撤回此版本", async () => {
      if (!confirmed.checked || !reason.value.trim()) throw Error("请确认具体版本并填写撤回理由。");
      await api(base + "/publications/" + segment(id) + "/withdraw", {method: "POST",
        body: JSON.stringify({reason: reason.value.trim(), idempotencyKey: key(id + ":withdraw:" + reason.value.trim())})});
      notice("版本已撤回，历史仍可回查。"); await loadStream(); await inspectPublication(id);
    });
  }
  async function loadStream() { const value = stream.value.trim(), ticket = (loadStream.ticket = (loadStream.ticket || 0) + 1); if (!value) throw Error("请填写知识流名称。"); const [changes, versions] = await Promise.all([api(base + "/change-sets?streamKey=" + segment(value)), api(base + "/publications?streamKey=" + segment(value))]); if (ticket !== loadStream.ticket) return; listings.replaceChildren(el("h3", "变更集与版本历史 · " + value)); for (const change of changes || []) action(listings, change.changeSetId + " · " + change.status, () => inspectChange(change.changeSetId)); for (const version of versions || []) action(listings, "版本 " + version.version + " · " + version.status, () => inspectPublication(version.publicationId)); }
  action(streamBox, "查询此知识流", async () => { inspect.replaceChildren(); await loadStream(); });
  await loadStream();
}
