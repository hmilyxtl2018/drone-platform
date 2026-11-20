"use strict";
/* Fixed local benchmarks; importing a Run only reads its historical audits. */
async function renderEvaluations(panel, runId) {
  panel.append(el("h2", "持续评测"), el("p", "离线评测执行固定工程函数和工具权限检查；历史模型导入只读取已存产物与调用审计。单独的 DeepSeek V4 Pro 质量评测会发起一次真实付费模型调用，固定输入六个工程与证据治理案例，不提供工具，且不会自动核验事实或认证工程结论。", "help-note help-caution"));
  const detail = (title, value) => { const d = el("details"); d.append(el("summary", title), el("pre", JSON.stringify(value, null, 2))); return d; };
  const show = v => v === undefined || v === null ? "unknown" : typeof v === "object" ? JSON.stringify(v) : String(v);
  const field = (parent, title, value = "") => { const label = el("label", title), i = el("input"); i.value = value; label.append(i); parent.append(label); return i; };
  const button = (parent, title, action) => { const b = el("button", title); b.type = "button"; b.onclick = async () => { b.disabled = true; try { await action(); } catch (e) { notice(e.message, true); } finally { b.disabled = false; } }; parent.append(b); return b; };
  const enc = encodeURIComponent, source = field(panel, "冻结配置与来源的终态 Run ID", runId || "");
  panel.append(el("p", "Profile、提示词、来源版本、产物哈希和调用用量均由服务端读取；来源真值与工程认证保持 unknown / false。", "help-note help-info"));
  const datasets = el("section"), runs = el("section"), selected = el("section"), comparison = el("section");
  const filter = field(panel, "运行列表按来源 Run 筛选（留空显示全部）", "");
  let rows = [], loading = 0;
  async function openRun(id) {
    const value = await api("/evaluations/runs/" + enc(id));
    const usage = value.usage || [], model = usage.filter(u => u.source_type === "model_call");
    const isFreshPaid = value.evaluationMeaning === "fresh-paid-deepseek-v4-pro-quality-evaluation";
    const providerCalls = value.providerCallsMade ?? 0;
    const actualModel = value.actualModel || (new Set(model.map(u => u.model_name).filter(Boolean)).size === 1 ? model.find(u => u.model_name)?.model_name : "unknown");
    selected.replaceChildren(el("h3", "评测详情：" + id), el("p", "模式：" + (value.execution_mode === "deterministic" ? "实际执行固定离线案例" : isFreshPaid ? "本次付费模型质量评测" : "历史模型产物与审计导入")),
      el("p", "来源 Run：" + value.source_run_id), el("p", "Profile：" + value.profile_agent_key + " · " + value.profile_version + " · " + value.profile_hash),
      el("p", "提示词版本 / 哈希：" + value.prompt_version + " / " + value.prompt_hash), el("p", "数据集版本 / 哈希：" + value.dataset_version + " / " + value.dataset_hash),
      el("p", "来源版本哈希：" + value.source_versions_hash),
      el("p", (isFreshPaid ? "本次新增 provider 调用：" + providerCalls + "；实际模型：" + actualModel + "；输入 / 输出 tokens：" + show(value.inputTokens) + " / " + show(value.outputTokens) + "；成本：" + show(value.actualCost) : "本次新增 provider 调用：0；历史导入审计行：" + model.length) + "；事实质量：unknown；工程认证：false", "muted"));
    if (value.displayRedacted) selected.append(el("p", "显示文本已脱敏；保存的哈希对应原始不可变数据。", "help-note help-info"));
    const cases = el("table"), head = el("tr");
    for (const title of ["案例", "确定性检查", "引文", "收敛", "权限"]) head.append(el("th", title));
    cases.append(head);
    for (const result of value.results) {
      const tr = el("tr");
      for (const cell of [result.case_id, result.deterministic_status, result.citation_status, result.convergence_status, result.authorization_status]) tr.append(el("td", show(cell)));
      cases.append(tr);
      const output = detail("实际执行输出与溯源 · " + result.case_id, {output: result.actual_output_json, outputHash: result.actual_output_hash,
        references: result.actual_references_json, checks: result.details_json});
      selected.append(output);
    }
    selected.append(cases, detail("冻结来源版本", value.source_versions_json), detail("判定指标", value.metrics));
    const total = key => model.length && model.every(u => u[key] !== null && u[key] !== undefined) ? model.reduce((n, u) => n + Number(u[key]), 0) : "unknown";
    selected.append(el("h4", "已保存调用审计"), el("p", isFreshPaid ? "以下是本次评测的 provider 调用审计；token 和模型名取自服务端真实审计行。" : value.execution_mode === "deterministic" ? "本次离线案例未执行模型调用，未借用来源 Run 的历史消耗。" : "以下为原 Run 历史消耗，不能当作本次导入新发生的调用。", "help-note help-info"),
      el("p", "历史模型行数：" + model.length + "；输入 / 输出 tokens：" + total("input_tokens") + " / " + total("output_tokens") + "；实际成本：" + total("cost")), detail("逐条审计引用", usage));
  }
  async function refresh() {
    const version = ++loading;
    const [catalog, data] = await Promise.all([api("/evaluations/datasets"), api("/evaluations/runs" + (filter.value.trim() ? "?sourceRunId=" + enc(filter.value.trim()) : ""))]);
    if (version !== loading) return;
    rows = data;
    datasets.replaceChildren(el("h3", "版本化数据集"));
    if (!catalog.length) datasets.append(el("p", "暂无评测数据集。运行固定离线评测时由服务器注册。", "muted"));
    for (const item of catalog) button(datasets, item.dataset_key + " · " + item.dataset_version, async () => {
      const data = await api("/evaluations/datasets/" + enc(item.dataset_id));
      selected.replaceChildren(el("h3", "数据集：" + item.dataset_key), el("p", "版本：" + item.dataset_version + " · " + item.dataset_hash));
      for (const c of data.cases) selected.append(detail(c.case_key + " · " + c.case_type, {input: c.input_json, expected: c.expected_json,
        inputHash: c.input_hash, expectedHash: c.expected_hash, caseHash: c.case_hash}));
    });
    runs.replaceChildren(el("h3", "评测运行历史"));
    if (!rows.length) runs.append(el("p", "暂无匹配评测运行。", "muted"));
    for (const item of rows) button(runs, item.evaluation_run_id + " · " + item.execution_mode + " · " + item.result_count + " 项", () => openRun(item.evaluation_run_id));
    comparison.replaceChildren(el("h3", "同版本案例与来源的结果比较"));
    const left = el("select"), right = el("select"); left.setAttribute("aria-label", "左侧评测运行"); right.setAttribute("aria-label", "右侧评测运行");
    for (const row of rows) { left.append(new Option(row.evaluation_run_id, row.evaluation_run_id)); right.append(new Option(row.evaluation_run_id, row.evaluation_run_id)); }
    if (rows.length > 1) right.selectedIndex = 1;
    const answer = el("div"); comparison.append(left, right);
    button(comparison, "比较冻结版本", async () => {
      const a = rows.find(r => r.evaluation_run_id === left.value), b = rows.find(r => r.evaluation_run_id === right.value);
      if (!a || !b) throw Error("请先选择两个已完成评测。");
      for (const key of ["dataset_id", "dataset_version", "dataset_hash", "case_set_hash", "source_versions_hash", "execution_mode"])
        if (a[key] !== b[key]) throw Error("两次评测的数据集、案例集合、来源版本或执行模式不同，不能横向比较。");
      const result = await api("/evaluations/compare?leftRunId=" + enc(a.evaluation_run_id) + "&rightRunId=" + enc(b.evaluation_run_id));
      answer.replaceChildren(el("p", "固定检查通过率：" + show(result.left.correctness) + " → " + show(result.right.correctness) + "；变化：" + show(result.delta)),
        el("p", "此比较不证明某个提示词导致性能变化，也不证明事实核验通过。", "help-note help-caution"), detail("比较的准确版本与结果", result));
    }); comparison.append(answer);
  }
  if (hasRole("operator")) {
    button(panel, "实际执行五类离线案例并保存", async () => {
      if (!source.value.trim()) throw Error("请选择保存冻结 Profile 的终态来源 Run。");
      const result = await api("/evaluations/benchmarks", {method: "POST", body: JSON.stringify({sourceRunId: source.value.trim()})});
      notice("离线案例已实际执行并保存；新增 provider 调用为 0。"); await refresh(); await openRun(result.evaluation_run_id);
    });
    const variants = el("section");
    variants.append(el("h3", "固定 Profile / Prompt 变体比较"), el("p", "服务端仅允许 baseline 与 prompt-v2 两个固定变体。两者复用同一来源 Run、数据集和来源版本；Profile / Prompt 哈希由服务端冻结，执行固定本地案例且 provider 调用为 0。", "help-note help-info"));
    for (const variant of ["baseline", "prompt-v2"]) button(variants, "执行变体：" + variant, async () => {
      if (!source.value.trim()) throw Error("请填写已有终态来源 Run ID。");
      const result = await api("/evaluations/benchmarks/variants", {method: "POST", body: JSON.stringify({sourceRunId: source.value.trim(), variant})});
      notice(variant + " 变体已保存；provider 调用为 0。"); await refresh(); await openRun(result.evaluation_run_id);
    });
    panel.append(variants);
    button(panel, "执行一次 DeepSeek V4 Pro 付费质量评测（6 案例）", async () => {
      if (!source.value.trim()) throw Error("请选择保存冻结 Profile 与来源的终态 Run。");
      const result = await api("/evaluations/paid-model", {method: "POST", body: JSON.stringify({sourceRunId: source.value.trim()})});
      notice("付费评测已保存；实际模型：" + (result.actualModel || "unknown") + "；provider 调用：" + (result.providerCallsMade ?? "unknown") + "。"); await refresh(); await openRun(result.evaluation_run_id);
    });
    button(panel, "导入该 Run 的历史模型产物与审计", async () => {
      if (!source.value.trim()) throw Error("请填写已有真实模型调用与报告的终态 Run ID。");
      const result = await api("/evaluations/historical-imports", {method: "POST", body: JSON.stringify({sourceRunId: source.value.trim()})});
      notice("已导入历史产物与审计；未发起新的付费模型评测。"); await refresh(); await openRun(result.evaluation_run_id);
    });
  }
  button(panel, "刷新数据集与运行列表", refresh);
  panel.append(datasets, runs, comparison, selected);
  await refresh();
}
