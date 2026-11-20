/* Stored evidence only; the server authenticates reviewers and owns hashes. */
(() => {
  "use strict";
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  };
  const value = item => item === undefined || item === null ? "unknown" : typeof item === "object" ? JSON.stringify(item) : String(item);
  const detail = (title, item) => {
    const block = node("details");
    block.append(node("summary", title), node("pre", JSON.stringify(item, null, 2)));
    return block;
  };
  const field = (parent, title, item) => parent.append(node("p", title + "：" + value(item)));

  async function mount(container, options) {
    const {runId, api, notice = () => {}, onReviewSubject} = options;
    if (!container || !runId || typeof api !== "function") throw new Error("审核工作台需要 Run 与真实 API。");
    let active = true, selection = 0;
    container.replaceChildren(node("p", "正在读取存档证据与当前审核状态…"));
    const base = "/review-workbench/runs/" + encodeURIComponent(runId);
    try {
      const data = await api(base);
      // The main page builds into a detached fragment before committing it.
      if (!active) return;
      container.replaceChildren();
      container.append(node("h2", "证据审核工作台"), node("p", "引用定位只证明文字来自保存的快照。人审决定、来源可信性与工程认证分别记录。", "help-note help-caution"));
      field(container, "运行", runId);
      const independence = node("p", "来源独立性：unknown");
      independence.append(miniTip("不同域名不自动计为独立佐证。", "caution", "来源独立性"));
      container.append(independence);
      container.append(detail("当前运行与证据指纹", data.snapshot), detail("来源分组（转载与重复）", data.sourceGroups));
      const summary = node("section");
      summary.append(node("h3", "冲突与审核历史"));
      if (!data.conflicts.length) { const empty = node("p", "未检测到已存候选间的冲突。"); empty.append(miniTip("这不证明资料完整。", "caution", "冲突检测范围")); summary.append(empty); }
      for (const conflict of data.conflicts) summary.append(detail(conflict.metric + " · " + conflict.status, conflict));
      if (!data.reviews.length) summary.append(node("p", "暂无人工审核决定。"));
      for (const review of data.reviews) {
        const row = node("div", undefined, "review-row");
        row.append(node("strong", review.decision + " · " + review.status));
        field(row, "审核者", review.reviewer_id);
        field(row, "理由", review.reason);
        field(row, "产物哈希", review.subject_hash);
        if (review.staleReasons.length) field(row, "失效原因", review.staleReasons);
        row.append(detail("不可变审核记录", review));
        summary.append(row);
      }
      container.append(summary);
      const sourcePanel = node("section"), candidatePanel = node("section");
      sourcePanel.append(node("h3", "存档原文"));
      const controls = node("div"), sourceSelect = node("select"), textPanel = node("div");
      sourceSelect.setAttribute("aria-label", "选择已保存来源");
      const empty = node("option", "请选择来源"); empty.value = ""; sourceSelect.append(empty);
      for (const source of data.sources) {
        const option = node("option", source.metadata?.title || source.sourceUri || source.sourceId);
        option.value = source.sourceId; sourceSelect.append(option);
      }
      controls.append(sourceSelect); sourcePanel.append(controls, textPanel); container.append(sourcePanel);

      async function showSource(sourceId, candidateId) {
        if (!sourceId) { textPanel.replaceChildren(); return; }
        const current = ++selection;
        textPanel.replaceChildren(node("p", "正在核对原文哈希…"));
        try {
          const source = await api(base + "/sources/" + encodeURIComponent(sourceId));
          if (!active || current !== selection) return;
          sourceSelect.value = sourceId;
          textPanel.replaceChildren();
          field(textPanel, "来源 URI（仅作标识）", source.sourceUri);
          field(textPanel, "保存内容 SHA-256", source.contentHash);
          field(textPanel, "完整快照产物 SHA-256", source.artifactHash);
          field(textPanel, "引用范围", source.citationScope === "stored-ontology-excerpt-only" ? "仅本体保存的摘录；原始全文不可用" : "保存的完整 Markdown");
          if (source.displayRedacted) textPanel.append(node("p", "显示内容已脱敏；Unicode 位置和行结构保留，保存哈希与显示哈希分别记录。", "help-note help-info"));
          const pre = node("pre", undefined, "review-source-text");
          pre.style.whiteSpace = "pre-wrap";
          pre.style.maxHeight = "34rem";
          pre.style.overflow = "auto";
          const characters = Array.from(source.text);
          const citation = source.citations.find(item => item.candidateId === candidateId);
          if (citation) {
            pre.append(document.createTextNode(characters.slice(0, citation.startOffset).join("")));
            const mark = node("mark", characters.slice(citation.startOffset, citation.endOffset).join(""));
            pre.append(mark, document.createTextNode(characters.slice(citation.endOffset).join("")));
            field(textPanel, "Unicode 引文位置", "[" + citation.startOffset + ", " + citation.endOffset + ")");
            textPanel.append(pre);
            mark.scrollIntoView({block: "nearest"});
          } else { pre.textContent = source.text; textPanel.append(pre); }
          textPanel.append(detail("来源元数据与引文", {...source, text: undefined}));
        } catch (error) {
          if (active && current === selection) textPanel.replaceChildren(node("p", error.message));
          notice(error.message, true);
        }
      }
      sourceSelect.onchange = () => showSource(sourceSelect.value);
      candidatePanel.append(node("h3", "候选与上下文"));
      if (!data.candidates.length) { const empty = node("p", "当前 Run 没有已保存候选。"); empty.append(miniTip("不能据此形成符合性结论。", "caution", "候选证据不足")); candidatePanel.append(empty); }
      for (const candidate of data.candidates) {
        const row = node("div", undefined, "candidate-row");
        row.append(node("h4", candidate.metric + " · " + candidate.entityId));
        field(row, "值 / 区间 / 单位", value(candidate.value ?? candidate.range) + " " + value(candidate.unit));
        field(row, "型号 / 构型", value(candidate.variant) + " / " + value(candidate.configuration));
        field(row, "工况", candidate.conditions);
        field(row, "观测时间", candidate.observedAt);
        field(row, "原文引用", candidate.quote);
        field(row, "候选哈希", candidate.fingerprint);
        field(row, "冲突", candidate.conflictIds.length ? candidate.conflictIds : "未检测到；并非已证无冲突");
        const locate = node("button", "定位保存的原文");
        locate.type = "button";
        locate.onclick = () => showSource(candidate.sourceId, candidate.candidateId);
        row.append(locate);
        if (typeof onReviewSubject === "function") {
          const review = node("button", "打开人工审核"); review.type = "button";
          review.onclick = () => onReviewSubject({runId, subjectType: "candidate", subjectId: candidate.candidateId, expectedHash: candidate.fingerprint});
          row.append(review);
        }
        row.append(detail("候选、定位与审核记录", candidate));
        candidatePanel.append(row);
      }
      container.append(candidatePanel, detail("变更影响范围（保守提示）", data.impact));
      return {dispose() { active = false; selection += 1; }};
    } catch (error) {
      if (active) container.replaceChildren(node("p", error.message));
      notice(error.message, true);
      throw error;
    }
  }
  window.AgentReviewWorkbench = Object.freeze({mount});
})();
