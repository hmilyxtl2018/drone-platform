"use strict";

/*
 * Manual concept calculations are submitted as separate, parent-linked Runs.
 * This view only projects frozen parent requirements/candidates and displays
 * server results; it never sets a review, verification or certification flag.
 */
async function renderEngineering(panel, runId) {
  panel.append(
    el("h2", "需求与概念工程计算"),
    el("p", "计算使用当前 Profile 中已启用的只读公式工具，并在独立子 Run 中记录预算、调用审计和不可变产物。缺少输入时结果保持 unknown；数学结果不等于人审、试验或工程认证。", "help-note help-caution")
  );
  if (!hasRole("operator")) {
    const status = el("p", "当前身份没有计算权限。", "muted");
    status.append(typeof miniTip === "function" ? miniTip("需要操作员或管理员权限才能发起计算。当前页面仅接受已登录的本地身份。", "help", "计算权限") : el("span", "需要操作员或管理员权限才能发起计算。当前页面仅接受已登录的本地身份。", "muted")); panel.append(status);
  }
  if (!runId) {
    panel.append(el("p", "尚未选择来源 Run。", "muted"), el("p", "选择一个已保存的来源 Run，查看其冻结需求与候选，并从中创建独立计算 Run。", "help-note help-help"));
    return;
  }

  const enc = encodeURIComponent;
  const section = (title, description) => {
    const node = el("section", undefined, "engineering-section");
    node.append(el("h3", title));
    if (description) node.append(el("p", description, "help-note help-help"));
    panel.append(node);
    return node;
  };
  const detail = (title, value) => {
    const node = el("details"), body = el("pre", JSON.stringify(value, null, 2));
    node.append(el("summary", title), body);
    return node;
  };
  const button = (parent, title, callback) => {
    const node = el("button", title);
    node.type = "button";
    node.onclick = async () => {
      node.disabled = true;
      try { await callback(); }
      catch (error) { notice(error.message || "工程计算请求失败。", true); }
      finally { node.disabled = false; }
    };
    parent.append(node);
    return node;
  };
  const input = (parent, title, value = "", type = "text") => {
    const label = el("label", title), node = el("input");
    node.type = type;
    node.value = value;
    label.append(node);
    parent.append(label);
    return node;
  };
  const select = (parent, title, options) => {
    const label = el("label", title), node = el("select");
    for (const option of options) node.append(new Option(option.label, option.value));
    label.append(node);
    parent.append(label);
    return node;
  };
  const sourceRun = enc(runId);
  const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === "object" ?
    Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
  const data = await api("/runs/" + sourceRun + "/engineering");
  let lifecycle = null;
  try { lifecycle = await api("/runs/" + sourceRun + "/engineering-lifecycle"); }
  catch (error) {
    // A lifecycle is created explicitly for a research Run.  A missing row is
    // rendered as an actionable setup state; other failures stay visible.
    if (!/404|not found|lifecycle/i.test(error.message || "")) panel.append(el("p", "生命周期状态暂时无法读取：" + error.message, "help-note help-caution"));
  }
  const identity = el("p", "来源 Run：" + data.parentRunId + " · 实体：" + (data.entityId || "unknown"));
  panel.append(identity);
  const lifecycleBox = section("工程生命周期门禁", "研判结果必须逐级经过人工审批、证据和验证门禁；任何阶段都保持 truthVerified=false，制造需求保持 proposed-human-review。");
  const lifecycleStages = ["exploration", "decision-support", "engineering-input", "prototype", "test", "qualification", "production-readiness"];
  if (!lifecycle) {
    lifecycleBox.append(el("p", "此 Run 尚未建立生命周期记录。", "muted"));
    if (hasRole("operator")) button(lifecycleBox, "建立 exploration 门禁", async () => {
      lifecycle = await api("/runs/" + sourceRun + "/engineering-lifecycle", {method: "POST", body: "{}"});
      notice("生命周期已建立；仍需人工审批才能推进。", false);
      panel.replaceChildren(); await renderEngineering(panel, runId);
    });
  } else {
    const state = lifecycle.state || {};
    lifecycleBox.append(el("p", "当前阶段：" + lifecycle.stage + " · 修订 " + lifecycle.revision, "lifecycle-stage"));
    lifecycleBox.append(el("p", "真值核验：" + lifecycle.truthVerified + " · 工程认证：" + lifecycle.engineeringCertification + " · 制造状态：" + lifecycle.manufacturingStatus, "muted"));
    const history = Array.isArray(lifecycle.transitions) ? lifecycle.transitions : [];
    if (history.length) lifecycleBox.append(detail("不可变门禁历史", history));
    if (Array.isArray(lifecycle.verificationRequirements) && lifecycle.verificationRequirements.length) {
      lifecycleBox.append(detail("验证矩阵（仅保存，不代表验收）", lifecycle.verificationRequirements));
    }
    const currentIndex = lifecycleStages.indexOf(lifecycle.stage);
    const next = lifecycleStages[currentIndex + 1];
    if (next && hasRole("operator")) {
      const advance = el("details");
      advance.append(el("summary", "提交下一阶段门禁（必须提供已批准的人审引用）"));
      const target = el("p", "目标阶段：" + next, "muted");
      const gateInput = el("textarea"); gateInput.rows = 8; gateInput.placeholder = JSON.stringify({humanReview: {status: "approved", approvalId: "…", reviewerId: "…", reportHash: "…", reason: "…"}}, null, 2);
      const submit = el("button", "提交门禁"); submit.type = "button";
      submit.onclick = async () => {
        submit.disabled = true;
        try {
          const gate = JSON.parse(gateInput.value);
          lifecycle = await api("/runs/" + sourceRun + "/engineering-lifecycle/advance", {method: "POST", body: JSON.stringify({targetStage: next, gate})});
          notice("生命周期门禁已保存。", false);
          panel.replaceChildren(); await renderEngineering(panel, runId);
        } catch (error) { notice(error.message || "门禁提交失败；请检查审批 ID、报告哈希和门禁字段。", true); }
        finally { submit.disabled = false; }
      };
      advance.append(target, el("p", "门禁 JSON", "muted"), gateInput, submit); lifecycleBox.append(advance);
    }
    if (state.history?.length) lifecycleBox.append(el("p", "生命周期历史修订数：" + state.history.length, "muted"));
  }
  if (data.workspace) panel.append(detail("冻结工作区版本", data.workspace));
  panel.append(el("p", "事实核验：false · 审核符合性：unknown · 工程认证：false", "muted"));

  const requirementsBox = section("冻结需求", "需求来自本体分析 Run 或其绑定的工作区修订。修改需求请回到工作区创建新修订，不在计算页面覆盖。 ");
  const requirements = data.requirements && typeof data.requirements === "object" ? data.requirements : {};
  const metrics = Object.keys(requirements).sort();
  if (!metrics.length) { const empty = el("p", "父 Run 没有保存需求。", "muted"); if (typeof miniTip === "function") empty.append(miniTip("仍可执行显式输入的质量/能源概念预算；需求判断将缺少目标量时返回 unknown。", "help", "补充需求")); requirementsBox.append(empty); }
  else for (const metric of metrics) requirementsBox.append(detail(metric, requirements[metric]));

  const candidateBox = section("父 Run 的冻结证据候选", "来源绑定只接受下列父 Run 候选；提交时服务端会在子 Run 中重新绑定来源、引文、内容哈希和候选指纹。父候选的审核状态不会复制成子 Run 的接受状态。");
  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  if (!candidates.length) { const empty = el("p", "没有可用候选。", "muted"); if (typeof miniTip === "function") empty.append(miniTip("可以使用明确标注的概念假设；来源型输入会保持 unknown，不能伪造来源。", "caution", "输入依据")); candidateBox.append(empty); }
  for (const candidate of candidates) {
    const row = el("div", undefined, "candidate");
    row.append(
      el("h4", candidate.entityId + " · " + candidate.metric),
      el("p", JSON.stringify(candidate.value ?? candidate.range ?? null) + " " + (candidate.unit || "") + " · " + (candidate.qualifier || "unknown")),
      el("blockquote", candidate.quote || "(没有保存引文)"),
      el("p", "候选指纹：" + candidate.fingerprint + " · 审核状态：子 Run 重新待审", "muted")
    );
    candidateBox.append(row);
  }

  const available = new Set(Array.isArray(data.availableTools) ? data.availableTools : []);
  const requirementsChoices = metrics.length ? metrics : [...new Set(candidates.map(candidate => candidate.metric))];
  const requirementCandidates = metric => candidates.filter(candidate => candidate.metric === metric);
  const candidateOption = (candidate, index) => ({
    label: candidate.metric + " · " + JSON.stringify(candidate.value ?? candidate.range) + " " + candidate.unit + " · " + (candidate.quote || "无引文").slice(0, 90),
    value: String(index)
  });
  const asQuantity = candidate => {
    const quantity = {unit: candidate.unit};
    if (typeof candidate.value === "number") quantity.value = candidate.value;
    else if (candidate.range && typeof candidate.range.min === "number" && typeof candidate.range.max === "number") {
      quantity.min = candidate.range.min;
      quantity.max = candidate.range.max;
    } else throw new Error("所选候选没有精确值或闭区间数量。");
    return quantity;
  };
  const candidateConditions = candidate => candidate.conditions === undefined ? null : candidate.conditions;
  const createCalculation = async (toolKey, payload, suffix) => {
    if (!hasRole("operator")) throw new Error("需要操作员或管理员权限。");
    const result = await api("/runs/" + sourceRun + "/engineering-calculations", {
      method: "POST",
      body: JSON.stringify({toolKey, payload, idempotencyKey: "engineering-" + crypto.randomUUID() + "-" + suffix})
    });
    notice("工程计算已在独立 Run 中执行并保存；人审符合性仍为 unknown。");
    await showResult(result.runId, true);
    await refreshCalculations();
  };
  const candidatePicker = (parent, title, choices) => select(parent, title, [
    {label: "显式概念假设（不作为来源证据）", value: ""},
    ...choices.map(({candidate, index}) => candidateOption(candidate, index))
  ]);
  const sourcePayload = (quantity, path, candidate, bindings) => {
    if (candidate) {
      bindings.push({path, candidateId: candidate.candidateId});
      return asQuantity(candidate);
    }
    return quantity;
  };
  const sourceOptions = allowed => candidates.map((candidate, index) => ({candidate, index}))
    .filter(({candidate}) => allowed.has(candidate.metric) && (candidate.value !== undefined || candidate.range));

  if (available.has("engineering.evaluate_requirement")) {
    const box = section("需求符合性计算", "结果只表示当前输入下的数学比较。缺观测、工况、来源或一致性证据时返回 unknown；不会自动接受候选。");
    const metric = select(box, "需求指标", requirementsChoices.map(value => ({label: value, value})));
    const candidate = select(box, "观测候选", []);
    const updateCandidates = () => {
      const rows = requirementCandidates(metric.value);
      candidate.replaceChildren(new Option("无观测（计算为 unknown）", ""));
      for (const row of rows) candidate.append(new Option(row.metric + " · " + JSON.stringify(row.value ?? row.range) + " " + (row.unit || "") + " · " + (row.quote || "").slice(0, 70), row.candidateId));
    };
    metric.onchange = updateCandidates;
    updateCandidates();
    button(box, "执行需求判断", () => {
      if (!metric.value) throw new Error("没有可比较的指标；请先保存需求或候选。");
      return createCalculation("engineering.evaluate_requirement", {
        requirementMetric: metric.value,
        ...(candidate.value ? {candidateId: candidate.value} : {})
      }, "requirement");
    });
  }

  if (available.has("engineering.mass_budget")) {
    const allowedLimit = new Set(["mtowKg", "massLimitKg", "massLimit"]);
    const allowedComponent = new Set(["massKg", "componentMassKg", "mass"]);
    const box = section("概念质量预算", "每个没有来源候选的数量都会附带明确的概念假设说明。已选来源数量直接采用候选原值；来源条件必须与本次计算条件一致。");
    const limitSource = candidatePicker(box, "最大起飞质量/质量上限来源候选", sourceOptions(allowedLimit));
    const limitValue = input(box, "质量上限（假设或未选来源时使用）", "", "number");
    const limitUnit = select(box, "上限单位", ["kg", "g", "lb"].map(value => ({label: value, value})));
    const componentId = input(box, "部件 ID", "component-1");
    const componentSource = candidatePicker(box, "部件质量来源候选", sourceOptions(allowedComponent));
    const componentValue = input(box, "部件质量（假设或未选来源时使用）", "", "number");
    const componentUnit = select(box, "部件质量单位", ["kg", "g", "lb"].map(value => ({label: value, value})));
    const contingency = input(box, "余量比例（0–1）", "0.1", "number");
    const conditionText = input(box, "计算工况 JSON（假设输入使用；来源输入自动采用来源工况）", "{}", "text");
    const missing = button(box, "按缺失输入执行并保存 unknown", () => createCalculation("engineering.mass_budget", {calculation: {}}, "mass-missing"));
    missing.title = "在无输入时保存一条明确标记 unknown 的预算结果。";
    button(box, "执行质量预算", () => {
      const bindings = [], selectedLimit = limitSource.value ? candidates[Number(limitSource.value)] : null;
      const selectedComponent = componentSource.value ? candidates[Number(componentSource.value)] : null;
      if (!selectedLimit && !limitValue.value.trim()) throw new Error("请填写质量上限或选择来源候选。");
      if (!selectedComponent && !componentValue.value.trim()) throw new Error("请填写部件质量或选择来源候选。");
      const limit = sourcePayload({value: Number(limitValue.value), unit: limitUnit.value,
        basis: {kind: "assumption", description: "Operator-entered concept mass limit; not independently verified."}}, "massLimit", selectedLimit, bindings);
      const mass = sourcePayload({value: Number(componentValue.value), unit: componentUnit.value,
        basis: {kind: "assumption", description: "Operator-entered component mass assumption; not a measured value."}}, "components.0.mass", selectedComponent, bindings);
      const conditions = selectedLimit || selectedComponent ? candidateConditions(selectedLimit || selectedComponent) : JSON.parse(conditionText.value || "{}");
      if (selectedLimit && selectedComponent && JSON.stringify(stable(candidateConditions(selectedLimit))) !== JSON.stringify(stable(candidateConditions(selectedComponent))))
        throw new Error("所选来源候选工况不一致，请分开分析或补充同工况证据。");
      const fraction = Number(contingency.value);
      if (!Number.isFinite(fraction)) throw new Error("余量比例必须是 0 到 1 之间的数值。");
      return createCalculation("engineering.mass_budget", {calculation: {
        conditions, components: [{componentId: componentId.value.trim() || "component-1", mass}],
        massLimit: limit, contingencyFraction: fraction
      }, candidateBindings: bindings}, "mass");
    });
  }

  if (available.has("engineering.endurance_budget")) {
    const allowedEnergy = new Set(["energyWh", "batteryEnergyWh", "energy"]);
    const allowedPower = new Set(["averagePowerW", "averagePower"]);
    const box = section("概念能源与续航预算", "公式只使用显式能源、平均功率、可用比例和预留比例，不推算推进效率、气动或环境影响。系统边界必须具体；输入缺失时可保存 unknown。");
    const energySource = candidatePicker(box, "能源来源候选", sourceOptions(allowedEnergy));
    const energyValue = input(box, "总能源（假设或未选来源时使用）", "", "number");
    const energyUnit = select(box, "能源单位", ["Wh", "kWh", "J", "kJ"].map(value => ({label: value, value})));
    const powerSource = candidatePicker(box, "平均功率来源候选", sourceOptions(allowedPower));
    const powerValue = input(box, "平均功率（假设或未选来源时使用）", "", "number");
    const powerUnit = select(box, "功率单位", ["W", "kW"].map(value => ({label: value, value})));
    const usable = input(box, "可用能源比例（0–1）", "", "number");
    const reserve = input(box, "预留能源比例（0–1）", "", "number");
    const boundary = input(box, "系统边界（例如 battery-terminal-electrical）");
    const conditionText = input(box, "计算工况 JSON（假设输入使用；来源输入自动采用来源工况）", "{}", "text");
    const missing = button(box, "按缺失输入执行并保存 unknown", () => createCalculation("engineering.endurance_budget", {calculation: {}}, "endurance-missing"));
    missing.title = "在无输入时保存一条明确标记 unknown 的预算结果。";
    button(box, "执行能源/续航预算", () => {
      const bindings = [], selectedEnergy = energySource.value ? candidates[Number(energySource.value)] : null;
      const selectedPower = powerSource.value ? candidates[Number(powerSource.value)] : null;
      if (!selectedEnergy && !energyValue.value.trim()) throw new Error("请填写能源或选择来源候选。");
      if (!selectedPower && !powerValue.value.trim()) throw new Error("请填写平均功率或选择来源候选。");
      const energy = sourcePayload({value: Number(energyValue.value), unit: energyUnit.value,
        basis: {kind: "assumption", description: "Operator-entered concept energy assumption; not independently verified."}}, "energy", selectedEnergy, bindings);
      const power = sourcePayload({value: Number(powerValue.value), unit: powerUnit.value,
        basis: {kind: "assumption", description: "Operator-entered concept average-power assumption; not independently verified."}}, "averagePower", selectedPower, bindings);
      if (usable.value === "" || reserve.value === "") throw new Error("可用比例和预留比例都必须显式填写；缺失时可保存 unknown。");
      const conditions = selectedEnergy || selectedPower ? candidateConditions(selectedEnergy || selectedPower) : JSON.parse(conditionText.value || "{}");
      if (selectedEnergy && selectedPower && JSON.stringify(stable(candidateConditions(selectedEnergy))) !== JSON.stringify(stable(candidateConditions(selectedPower))))
        throw new Error("能源与功率来源候选工况不一致，请分开分析或补充同工况证据。");
      return createCalculation("engineering.endurance_budget", {calculation: {
        conditions, energy, averagePower: power, usableFraction: Number(usable.value),
        reserveFraction: Number(reserve.value), ...(boundary.value.trim() ? {systemBoundary: boundary.value.trim()} : {})
      }, candidateBindings: bindings}, "endurance");
    });
  }

  const resultBox = section("已保存计算 Run", "可回看服务端保存的冻结输入、实际 ToolGateway 调用、预算和工程计算产物。");
  async function showResult(calculationRunId, append = false) {
    const value = await api("/engineering-calculations/" + enc(calculationRunId));
    const row = el("div", undefined, "candidate");
    row.append(el("h4", value.toolKey + " · " + value.status), el("p", "计算 Run：" + value.runId + " · 父 Run：" + value.parentRunId));
    row.append(el("p", "预算使用：" + JSON.stringify(value.budget?.used || {}) + " · 数学结果与否：见不可变产物 · 人审符合性：unknown · 工程认证：false", "muted"));
    row.append(detail("冻结计算输入", value.frozenInput), detail("实际工具调用审计", value.toolCalls), detail("概念计算产物及哈希", value.artifacts), detail("Run 输出与候选投影", {output: value.output, candidateProjection: value.candidateProjection, projectionHash: value.projectionHash}));
    if (append) resultBox.prepend(row); else resultBox.append(row);
  }
  async function refreshCalculations() {
    const latest = await api("/runs/" + sourceRun + "/engineering");
    const calculations = Array.isArray(latest.calculations) ? latest.calculations : [];
    resultBox.replaceChildren(el("h3", "已保存计算 Run"));
    if (!calculations.length) resultBox.append(el("p", "此来源 Run 尚无独立工程计算。", "muted"));
    for (const item of calculations) {
      const line = el("div", undefined, "engineering-calculation-row");
      line.append(el("span", item.run_id + " · " + item.tool_key + " · " + item.created_at));
      button(line, "查看计算结果", () => showResult(item.run_id));
      resultBox.append(line);
    }
  }
  button(resultBox, "刷新计算记录", refreshCalculations);
  await refreshCalculations();
}
