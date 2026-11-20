"use strict";
/* Task objects come from the selected Agent's registered, read-only source. */
(() => {
  const sessions = new Map();
  let epoch = 0, current = null, questionListenerController = null;
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const action = (text, handler) => { const node = make("button", text); node.type = "button"; node.onclick = handler; return node; };
  const tip = (text, title) => window.AgentAdminTips.create(text, "info", title);
  const field = (text, control, help) => {
    const node = make("label"), heading = make("span", text, "target-field-heading");
    if (help) heading.append(tip(help, text));
    node.append(heading, control); return node;
  };
  const option = (value, text) => { const node = make("option", text); node.value = value; return node; };
  const purposes = {overview:"能力概览", requirements:"需求符合性", evidence:"证据补全"};
  const valueText = value => {
    const raw = value.normalizedValue ?? value.value;
    return (raw == null ? "未提供" : typeof raw === "object" ? JSON.stringify(raw) : String(raw)) + (value.unit ? " " + value.unit : "");
  };

  function mountFutureBattlefieldSettings(picker, preview, {agentKey, canOperate, saved}) {
    const submit = document.querySelector("#create-form > button[type=submit]");
    const box = make("section", undefined, "agent-specific-panel future-battlefield-panel");
    const heading = make("div", undefined, "task-settings-heading");
    heading.append(make("h3", "未来战场研判设置"), tip("此 Agent 研究战争形态与体系趋势，不选择无人机型号，也不输出实时目标指示、武器控制或作战命令。", "本 Agent 的专属设置"));
    box.append(heading);
    picker.replaceChildren(box); preview.replaceChildren(); preview.hidden = true;
    document.getElementById("entity").value = "";
    const questions = [
      {key:"war-system",name:"作战体系",question:"指挥、编组、跨域协同和决策链如何变化？",enabled:true},
      {key:"unmanned-autonomy",name:"无人化与自主化",question:"无人平台、自主决策和人机协同如何发展？",enabled:true},
      {key:"sensing-data-link",name:"传感器与数据链",question:"感知、通信、融合和决策周期如何变化？",enabled:true},
      {key:"electromagnetic-cyber",name:"电磁与网络对抗",question:"干扰、欺骗和网络攻击如何改变体系运作？",enabled:true},
      {key:"industrial-logistics",name:"工业与后勤",question:"生产、补充、维修和保障约束如何影响持续性？",enabled:true},
      {key:"countermeasure-iteration",name:"反制与技术迭代",question:"攻防反制和技术迭代如何相互塑造？",enabled:true},
      {key:"cognitive-information",name:"认知与信息影响",question:"信息可信度、欺骗与认知影响如何改变判断？",enabled:false},
      {key:"law-escalation",name:"法律与升级风险",question:"自主化、误判和责任问题带来哪些治理约束？",enabled:false}
    ];
    if (!Array.isArray(saved.perspectives)) saved.perspectives = questions.map(item => ({...item}));
    const horizon = make("select");
    for (const [value,label] of [["1","未来 1 年"],["3","未来 3 年"],["5","未来 3–5 年"],["10","未来 10 年"]]) horizon.append(option(value,label));
    horizon.value = saved.horizon || "5";
    const asOf = make("input"); asOf.type = "date"; asOf.value = saved.asOf || new Date().toISOString().slice(0,10);
    const theater = make("input"); theater.type = "text"; theater.maxLength = 500; theater.value = saved.theater || "全球；重点关注印太与高强度对抗环境";
    asOf.required = theater.required = true;
    const timeRow = make("div", undefined, "specific-two-col");
    timeRow.append(field("基准时点", asOf, "研判时点用来约束材料有效期和未来时间窗。"), field("展望期限", horizon, "选择报告需要覆盖的趋势周期。"));
    const subheading = (title, description, iconClass) => {
      const row = make("h4", undefined, "specific-subheading");
      const icon = make("span", undefined, "future-war-stage-icon " + iconClass); icon.setAttribute("aria-hidden", "true");
      row.append(icon, make("strong", title));
      if (description) row.append(tip(description, title)); return row;
    };
    const frameworkSection = make("section", undefined, "future-settings-block future-settings-framework");
    frameworkSection.append(subheading("研究框架", "先固定时间、空间和研究边界，所有来源与结论都在这个框架内解释。", "plan-icon"), timeRow,
      field("地域 / 作战空间", theater, "明确地理尺度，避免将局部结论外推为普遍规律。"));

    const scopeNote = make("textarea"); scopeNote.rows = 3; scopeNote.maxLength = 4000;
    scopeNote.required = true;
    scopeNote.value = saved.scopeNote || "关注战争形态、体系协同、无人化与智能化、跨域对抗和保障韧性；以公开材料支持判断。";
    const analysisSection = make("section", undefined, "future-settings-block future-settings-analysis");
    analysisSection.append(subheading("分析范围", "勾选本次覆盖的战争层级与证据类型；未勾选范围不会被模型暗中补入。", "scope-icon"),
      field("研究范围与排除项", scopeNote, "定义分析边界。实时目标指示、武器控制和作战命令不属于本 Agent 的输出范围。"));
    const makeChecks = (title, values, selected) => {
      const fieldset = make("fieldset", undefined, "specific-check-group");
      const legend = make("legend", title); legend.append(tip("本次 Run 只冻结已勾选的研究范围。", title)); fieldset.append(legend);
      const grid = make("div", undefined, "specific-check-grid"), controls = [];
      for (const item of values) {
        const check = make("input"); check.type = "checkbox"; check.value = item.key; check.checked = selected.includes(item.key);
        const label = make("label", undefined, "check specific-check"); label.append(check, make("span", item.name));
        if (item.help) label.append(tip(item.help, item.name));
        grid.append(label); controls.push(check);
      }
      fieldset.append(grid); return {fieldset, controls};
    };
    const layerOptions = [
      {key:"strategic",name:"战略层",help:"战争目的、力量结构与长期趋势"},
      {key:"operational",name:"战役层",help:"体系编成、行动方式和跨域协同"},
      {key:"tactical",name:"战术层",help:"交战样式和任务执行层面的公开趋势"},
      {key:"technical",name:"技术层",help:"关键技术、数据链和系统可靠性"}
    ];
    const layers = makeChecks("研判层级", layerOptions, saved.layers || layerOptions.map(item => item.key));
    saved.layers = layers.controls.filter(item => item.checked).map(item => item.value);
    analysisSection.append(layers.fieldset);
    const sources = makeChecks("期望证据类型", [
      {key:"official",name:"官方 / 机构",help:"政策、战略、预算和正式评估"},
      {key:"research",name:"研究 / 评估",help:"学术研究、智库分析和独立评估"},
      {key:"industry",name:"产业 / 技术",help:"公开标准、产业与技术资料"},
      {key:"events",name:"公开事件 / 演训",help:"有时间、地点和原始出处的公开记录"}
    ], saved.sourceTypes || ["official", "research", "industry", "events"]);
    saved.sourceTypes = sources.controls.filter(item => item.checked).map(item => item.value);
    const evidenceSection = make("section", undefined, "future-settings-block future-settings-evidence");
    evidenceSection.append(subheading("证据架构", "来源类型决定采集计划，不代表来源已经独立或事实已经核验。", "data-model-icon"), sources.fieldset);

    const perspectiveBox = make("fieldset", undefined, "specific-check-group perspective-editor");
    const perspectiveLegend = make("legend", "研判视角与核心问题");
    perspectiveLegend.append(tip("每个启用视角会形成一个独立结论或明确缺口。可按本次问题调整其核心问题。", "视角设计"));
    perspectiveBox.append(perspectiveLegend);
    const perspectiveRows = make("div", undefined, "perspective-editor-rows"), perspectiveControls = [];
    const stored = new Map(saved.perspectives.map(item => [item.key, item]));
    const defaultLayerByPerspective = {
      "war-system":"operational", "unmanned-autonomy":"operational",
      "sensing-data-link":"technical", "electromagnetic-cyber":"technical",
      "industrial-logistics":"operational", "countermeasure-iteration":"tactical",
      "cognitive-information":"strategic", "law-escalation":"strategic"
    };
    function populateLayerOptions(select, preferred) {
      const available = saved.layers || [];
      select.replaceChildren(...layerOptions.filter(layer => available.includes(layer.key)).map(layer => option(layer.key, layer.name)));
      if (available.includes(preferred)) select.value = preferred;
      else select.value = available[0] || "";
    }
    for (const item of questions) {
      const value = stored.get(item.key) || item;
      const row = make("div", undefined, "perspective-editor-row"), label = make("label", undefined, "check perspective-toggle");
      const enabled = make("input"); enabled.type = "checkbox"; enabled.checked = value.enabled !== false; enabled.value = item.key;
      label.append(enabled, make("span", item.name));
      const question = make("input"); question.type = "text"; question.maxLength = 500; question.value = value.question || item.question; question.setAttribute("aria-label", item.name + "核心问题");
      const layer = make("select"); layer.setAttribute("aria-label", item.name + "分析层级");
      populateLayerOptions(layer, value.layer || defaultLayerByPerspective[item.key]);
      row.append(label, question, layer); perspectiveRows.append(row); perspectiveControls.push({key:item.key,name:item.name,enabled,question,layer,defaultItem:item});
    }
    const conclusionSection = make("section", undefined, "future-settings-block future-settings-conclusion");
    perspectiveBox.append(perspectiveRows);
    conclusionSection.append(subheading("结论组织", "每个启用视角都必须形成判断、证据缺口和未知项，避免只给一段泛化摘要。", "imagine-icon"), perspectiveBox);
    const constraints = make("textarea"); constraints.rows = 3; constraints.maxLength = 4000;
    constraints.required = true;
    constraints.value = saved.constraints || "区分结构性趋势与短期事件；检视对手反制、欺骗、通信退化、保障与工业约束；关键判断保留反方证据和未知项。";
    const qualitySection = make("section", undefined, "future-settings-block future-settings-quality");
    qualitySection.append(subheading("质量与边界", "用反方检验、对手反制和保障约束限制过度推断。", "scope-icon"), field("必须回答的问题 / 研判约束", constraints, "用于明确本次的比较问题、反方检验和结论边界。"));
    const decisionDetails = make('details'); decisionDetails.append(make('summary', '决策说明（可选）'));
    const decisionControls = [];
    const decisionFields = [['decisionToSupport','支持什么判断',false],['decisionOwner','报告给谁审阅',false],
      ['objectsOfInterest','研究对象',true],['alternatives','比较哪些候选解释',true],
      ['criteria','判断标准',true],['completionCriteria','研究完成条件',true]];
    for (const [key,label,multiple] of decisionFields) {
      const input = make('textarea'); input.rows = 2; input.maxLength = 2000;
      input.value = multiple ? (saved.decisionBrief?.[key] || []).join('\n') : saved.decisionBrief?.[key] || '';
      decisionDetails.append(field(label, input, multiple ? '每行一项。留空将记录为未指定或明确的系统默认。' : '留空将记录为未指定或明确的系统默认。'));
      decisionControls.push({key,input,multiple});
    }
    qualitySection.append(decisionDetails);

    const settingsGrid = make("div", undefined, "future-settings-grid");
    settingsGrid.append(frameworkSection, analysisSection, evidenceSection, qualitySection, conclusionSection);
    box.append(settingsGrid);

    let sending = false;
    const sync = () => {
      saved.asOf = asOf.value; saved.horizon = horizon.value; saved.theater = theater.value; saved.scopeNote = scopeNote.value;
      saved.layers = layers.controls.filter(item => item.checked).map(item => item.value);
      saved.sourceTypes = sources.controls.filter(item => item.checked).map(item => item.value);
      saved.perspectives = perspectiveControls.map(item => ({key:item.key,name:item.name,question:item.question.value,layer:item.layer.value,enabled:item.enabled.checked}));
      for (const item of perspectiveControls) { item.question.disabled = item.layer.disabled = !item.enabled.checked; item.question.required = item.enabled.checked; }
      saved.constraints = constraints.value;
      saved.decisionBrief = Object.fromEntries(decisionControls.filter(({input}) => input.value.trim()).map(({key,input,multiple}) =>
        [key, multiple ? [...new Set(input.value.split('\n').map(value => value.trim()).filter(Boolean))] : input.value.trim()]));
      submit.disabled = sending || !canOperate || !document.getElementById("question").value.trim() || !saved.layers.length || !saved.sourceTypes.length || !saved.perspectives.some(item => item.enabled && item.question.trim());
    };
    for (const control of [asOf,horizon,theater,scopeNote,constraints,...decisionControls.map(item => item.input),...layers.controls,...sources.controls,...perspectiveControls.flatMap(item => [item.enabled,item.question,item.layer])]) {
      control.addEventListener("input", sync); control.addEventListener("change", sync);
    }
    for (const check of layers.controls) check.addEventListener("change", () => {
      for (const item of perspectiveControls) populateLayerOptions(item.layer, item.layer.value || defaultLayerByPerspective[item.key]);
      sync();
    });
    document.getElementById("question")?.addEventListener("input", sync, {signal:questionListenerController.signal});
    current = {setSubmitting(value) { sending = value; sync(); }, getInput() {
      if (!document.getElementById("question").value.trim()) throw Error("请填写任务目标");
      const enabled = saved.perspectives.filter(item => item.enabled && item.question.trim());
      if (!enabled.length) throw Error("至少启用一个含核心问题的研判视角");
      if (!saved.layers.length) throw Error("至少选择一个研判层级");
      if (!saved.sourceTypes.length) throw Error("至少选择一种期望证据类型");
      if (!saved.asOf || !saved.theater.trim() || !saved.scopeNote.trim() || !saved.constraints.trim()) throw Error("请补齐基准时点、地域、研究范围与研判约束");
      const specificSettings = {
        schemaVersion:"1.0",
        kind:"future-battlefield-judgment",
        asOf:saved.asOf,
        horizonYears:Number(saved.horizon),
        theater:saved.theater,
        scopeNote:saved.scopeNote,
        layers:[...saved.layers],
        sourceTypes:[...saved.sourceTypes],
        perspectives:enabled.map(item => ({key:item.key,name:item.name,question:item.question,layer:item.layer})),
        constraints:saved.constraints
      };
      return {
        analysisPurpose:"future-battlefield-judgment",
        asOf:saved.asOf,
        scope:{timeWindow:{asOf:saved.asOf,horizonYears:Number(saved.horizon)},geography:saved.theater,summary:saved.scopeNote,layers:saved.layers,sourceTypes:saved.sourceTypes,constraints:saved.constraints},
        perspectives:enabled.map(item => ({key:item.key,name:item.name,question:item.question,layer:item.layer})),
        analysisContext:{asOf:saved.asOf,horizonYears:Number(saved.horizon),theater:saved.theater,layers:saved.layers,sourceTypes:saved.sourceTypes,constraints:saved.constraints},
        specificSettings
        ,decisionBrief:{...saved.decisionBrief}
      };
    }};
    preview.replaceChildren(); preview.hidden = true; sync();
  }

  function mountActionSettings(picker, preview, {agentKey, canOperate, saved, displayName, taskConfig = {}}) {
    const submit = document.querySelector("#create-form > button[type=submit]");
    const box = make("section", undefined, "agent-specific-panel action-agent-panel");
    const heading = make("div", undefined, "task-settings-heading");
    heading.append(make("h3", "Action 专属设置"), tip("Action Agent 执行 Profile 已发布的固定工序，通常由上游应用级 Agent 委派。这里不选择无人机本体，也不修改执行图。", "Action Agent 输入契约"));
    box.append(heading);
    const delegated = taskConfig.dataContractVersion === "agent-data.v1" && ["evidence-extraction-action", "scenario-analysis-action"].includes(agentKey);
    if (delegated || agentKey.startsWith("research-")) {
      const link = make("a", "打开应用任务"); link.href = "#task=future-battlefield-analyst";
      box.append(make("p", agentKey.startsWith("research-") ? "由已完成报告的质量评审流程调用。" : "由应用执行图调用，自动传入冻结的问题、证据和交接契约。"), link);
      picker.replaceChildren(box); preview.replaceChildren(); preview.hidden = true;
      submit.disabled = true;
      current = {setSubmitting() { submit.disabled = true; }, getInput() { throw Error("请通过应用任务的正式交接调用此 Agent"); }};
      return;
    }
    heading.append(tip((displayName || agentKey) + " 接收上游任务材料并返回结构化 Action 产物。", "上游交接"));
    const note = make("textarea"); note.rows = 3; note.maxLength = 4000;
    note.placeholder = "可选：说明本次材料处理的关注点、排除项或交接备注";
    note.value = saved.actionNote || "";
    box.append(field("交接备注（可选）", note, "父 Agent 通过正式交接传入的材料优先；此处只补充本次任务的操作说明。"));
    const actionInputs = {
      "evidence-collection-action": {sources:[]},
      "evidence-extraction-action": {claims:[],events:[]},
      "trend-analysis-action": taskConfig.dataContractVersion === "agent-data.v1" ? {observations:[],parameters:{}} : {values:[]},
      "scenario-analysis-action": {judgments:[]},
      "red-team-action": {claims:[],judgments:[],conflicts:[]}
    };
    const template = actionInputs[agentKey];
    const material = make("textarea"); material.rows = 8; material.maxLength = 250000;
    material.className = "specific-json-input"; material.spellcheck = false;
    material.value = saved.actionInput || JSON.stringify(template || {}, null, 2);
    box.append(field("输入材料（JSON）", material, "字段按当前 Action 区分：来源包含 sourceUri/body，声明包含 quote/body 与来源时间，判断包含 statement/supportingClaims/conditions/counterArguments/unknowns；完整字段由服务端工具契约校验。"));
    const inputStatus = make("p", "", "specific-input-status"); inputStatus.setAttribute("aria-live", "polite"); box.append(inputStatus);
    function readMaterial() {
      let data;
      try { data = JSON.parse(material.value); } catch { throw Error("输入材料不是有效 JSON"); }
      if (!data || Array.isArray(data) || typeof data !== "object" || !template || Object.keys(data).some(key => !Object.hasOwn(template,key))) throw Error("输入材料字段与当前 Action 不匹配");
      if (Object.keys(template).some(key => Array.isArray(template[key]) ? !Array.isArray(data[key]) : !data[key] || Array.isArray(data[key]) || typeof data[key] !== "object")) throw Error("材料类型与当前契约不一致");
      const count = Object.values(data).filter(Array.isArray).reduce((sum, rows) => sum + rows.length, 0);
      if (!count) throw Error("请补充至少一条待处理材料");
      if (agentKey === "trend-analysis-action" && data.values && (data.values.length < 2 || data.values.some(value => typeof value !== "number" || !Number.isFinite(value)))) throw Error("趋势分析需要至少两个有限数值");
      return data;
    }
    const output = make("div", undefined, "specific-readonly-summary");
    output.append(make("span", "输出契约"), make("strong", "按 Profile 固定定义"), make("span", "工具结果、证据引用、缺口和状态"));
    box.append(output);
    picker.replaceChildren(box); preview.replaceChildren(); preview.hidden = true;
    document.getElementById("entity").value = "";
    let sending = false;
    const sync = () => {
      saved.actionNote = note.value;
      saved.actionInput = material.value;
      let valid = false;
      try { const data = readMaterial(); valid = true; inputStatus.textContent = Object.values(data).filter(Array.isArray).reduce((sum,rows) => sum + rows.length, 0) + " 条材料 · 提交时校验工具契约"; }
      catch (error) { inputStatus.textContent = error.message; }
      submit.disabled = sending || !canOperate || !valid || !document.getElementById("question").value.trim();
    };
    note.addEventListener("input", sync); material.addEventListener("input", sync); document.getElementById("question")?.addEventListener("input", sync, {signal:questionListenerController.signal}); sync();
    current = {setSubmitting(value) { sending = value; sync(); }, getInput() {
      if (!document.getElementById("question").value.trim()) throw Error("请填写任务目标");
      const actionInput = readMaterial();
      return {...actionInput, analysisPurpose:"action-task", analysisContext:{actionNote:saved.actionNote || ""},
        specificSettings:{schemaVersion:"1.0",kind:"action", actionNote:saved.actionNote || "", input:actionInput}};
    }};
  }

  function mountGenericSettings(picker, preview, {agentKey, canOperate, saved, displayName}) {
    const submit = document.querySelector("#create-form > button[type=submit]");
    const box = make("section", undefined, "agent-specific-panel generic-agent-panel");
    const heading = make("div", undefined, "task-settings-heading");
    heading.append(make("h3", "Profile 专属设置"), tip("该 Agent 尚未声明可视化输入表单；通用目标和预算仍然有效，专属字段由已发布 Profile 约束。", "Profile 输入契约"));
    box.append(heading);
    const note = make("textarea"); note.rows = 3; note.maxLength = 4000; note.value = saved.profileNote || "";
    note.placeholder = "可选：补充本次任务的范围、输入材料或判断约束";
    const contractNote = make("div", undefined, "specific-contract-note");
    box.append(contractNote);
    contractNote.append(make("strong", "由 Profile 定义"), make("span", (displayName || agentKey) + " 的专属输入未在控制台注册为可编辑字段。"));
    box.append(field("补充说明（可选）", note, "只作为本次任务上下文保存，不能扩大 Profile 的工具、数据或权限边界。"));
    picker.replaceChildren(box); preview.replaceChildren(); preview.hidden = true;
    document.getElementById("entity").value = "";
    let sending = false;
    const sync = () => { saved.profileNote = note.value; submit.disabled = true; };
    note.addEventListener("input", sync); document.getElementById("question")?.addEventListener("input", sync, {signal:questionListenerController.signal}); sync();
    current = {setSubmitting(value) { sending = value; sync(); }, getInput() {
      throw Error("请先为该 Agent 注册专属任务输入契约");
    }};
  }

  async function mount(picker, preview, {agentKey, api, notice, canOperate, agentLayer, displayName, taskConfig}) {
    questionListenerController?.abort();
    questionListenerController = new AbortController();
    const generation = ++epoch;
    current = null;
    if (!sessions.has(agentKey)) sessions.set(agentKey, {scope:"UnmannedAircraft", category:"", query:"", entityId:"", offset:0,
      metrics:["enduranceHours", "payloadKg"], purpose:"overview", configuration:"", requirementText:""});
    const saved = sessions.get(agentKey);
    const isDesignAgent = agentKey === "uav-design-engineer";
    const isReviewer = agentKey === "uav-evidence-reviewer";
    if (isDesignAgent) saved.purpose = "requirements";
    if (isReviewer) saved.purpose = "evidence";
    const isAction = agentLayer === "action" || agentKey.endsWith("-action");
    if (["future-battlefield-analyst", "future-battlefield-codex"].includes(agentKey)) {
      mountFutureBattlefieldSettings(picker, preview, {agentKey, canOperate, saved});
      return;
    }
    if (isAction) {
      mountActionSettings(picker, preview, {agentKey, canOperate, saved, displayName, taskConfig});
      return;
    }
    if (agentKey !== "uav-capability-analyst" && !isDesignAgent && !isReviewer) {
      mountGenericSettings(picker, preview, {agentKey, canOperate, saved, displayName});
      return;
    }
    let result = null, loading = false, sending = false, listGeneration = 0, checkGeneration = 0, timer;
    const live = () => generation === epoch;
    const submit = document.querySelector("#create-form > button[type=submit]");
    const ready = () => { if (live()) submit.disabled = !canOperate || !result?.canSubmit || loading || sending || !saved.metrics.length || !document.getElementById("question").value.trim() || (saved.purpose === "requirements" && !saved.requirementText.trim()); };
    document.getElementById("question")?.addEventListener("input", ready, {signal:questionListenerController.signal});
    picker.replaceChildren(); preview.replaceChildren(); preview.hidden = false;
    const panel = make("section", undefined, "agent-specific-panel uav-agent-panel");
    const panelTitle = isDesignAgent ? "设计制造设置" : isReviewer ? "证据审查设置" : "能力分析设置";
    const panelHelp = isDesignAgent ? "选择经过预检的设计基线，定义性能目标、硬约束和验证关注面。" : isReviewer ? "选择经过预检的审查对象，限定属性范围、证据维度和待核问题。" : "选择机型、目标属性与分析目的。";
    const panelHeading = make("div", undefined, "task-settings-heading"); panelHeading.append(make("h3", panelTitle), tip(panelHelp, "本 Agent 的专属设置"));
    panel.append(panelHeading); picker.append(panel);
    document.getElementById("entity").value = saved.entityId;
    const purpose = make("select"); purpose.id = "analysis-purpose";
    for (const [key, label] of Object.entries(purposes)) purpose.append(option(key, label));
    purpose.value = saved.purpose;
    const requirement = make("textarea"); requirement.id = "task-requirement-text"; requirement.rows = 2; requirement.maxLength = 4000;
    requirement.placeholder = "例如：指定构型下续航不少于 20 h；注明任务载荷和工况。"; requirement.value = saved.requirementText;
    const requirementField = field(isDesignAgent ? "性能目标与验收工况" : "需求阈值与适用工况", requirement,
      isDesignAgent ? "写出性能目标、任务条件和验收标准；这些要求会随本次 Run 冻结。" : undefined);
    if (isDesignAgent) requirement.placeholder = "例如：指定任务载荷和工况下续航不低于 20 h；注明验收条件。";
    if (isReviewer) requirementField.hidden = true;
    const purposeField = field("分析目的", purpose);
    if (isDesignAgent || isReviewer) purposeField.hidden = true;
    const updatePurpose = () => { requirementField.hidden = isReviewer || saved.purpose !== "requirements"; requirement.required = !requirementField.hidden; };
    updatePurpose();
    const scope = make("select"); scope.id = "object-scope";
    const category = make("select"); category.id = "object-category";
    const search = make("input"); search.type = "search"; search.id = "object-query"; search.maxLength = 200;
    search.placeholder = "搜索名称 / 别名 / ID"; search.autocomplete = "off"; search.value = saved.query;
    const objects = make("select"); objects.id = "object-choice"; objects.setAttribute("aria-label", "选择具体机型");
    const count = make("span", "读取本体目录…", "target-result-count"); count.setAttribute("role", "status");
    const previous = action("上一页", () => { saved.offset = Math.max(0, saved.offset - 30); clearSelection(); loadList().catch(failure); });
    const next = action("下一页", () => { saved.offset += 30; clearSelection(); loadList().catch(failure); });
    const pagination = make("div", undefined, "target-pagination"); pagination.append(previous, count, next);
    const filters = make("div", undefined, "target-filter-grid");
    filters.append(field("对象范围", scope, "飞行器、系统和巡飞弹分别选择，参数含义分别核对。组织与项目作为关联背景，不作为本次单机分析对象。"),
      field("展示分类", category, "分类沿用本体工作台按名称与原始类别归并的展示结果，不等同于经过审核的任务能力。同一机型可承担多种用途。"));
    const metricBox = make("fieldset", undefined, "target-metrics");
    const metricTitle = isDesignAgent ? "设计目标属性" : isReviewer ? "复核属性范围" : "目标属性";
    const legend = make("legend", metricTitle); legend.append(tip("选择属性可限定本次对象预检和后续分析范围；缺失值保留为未知，不按 0 处理。", metricTitle)); metricBox.append(legend);
    const metricControls = make("div", undefined, "target-metric-options"); metricBox.append(metricControls);
    const configuration = make("input"); configuration.id = "task-configuration"; configuration.maxLength = 4000;
    configuration.placeholder = isDesignAgent ? "可填写型号变型、载荷与运行构型" : isReviewer ? "可限定型号变型、来源或时间口径" : "可填写型号变型、载荷及使用工况";
    configuration.value = saved.configuration;
    panel.append(purposeField, requirementField, filters, field("搜索机型", search),
      field("分析对象", objects), pagination, metricBox, field(isDesignAgent ? "设计构型 / 工况" : isReviewer ? "审查构型 / 口径" : "构型 / 工况（可选）", configuration));
    let specializedField = null, specializedChecks = [];
    if (isDesignAgent || isReviewer) {
      specializedField = make("textarea"); specializedField.rows = 3; specializedField.maxLength = 4000;
      specializedField.value = saved.specializedNotes || "";
      panel.append(field(isDesignAgent ? "设计硬约束与优先级" : "审查问题与关注点", specializedField,
        isDesignAgent ? "重量、能源、成本、制造、材料和验证约束。" : "数值口径、来源独立性、引文定位或追溯缺口。"));
      const choices = isDesignAgent ? [["airframe","总体与结构"],["propulsion","动力与能源"],["payload","任务载荷"],["manufacturing","制造工艺"],["verification","试验验证"]] : [["citation","引文与来源"],["entity","实体与构型归属"],["conditions","单位与工况"],["independence","来源独立性"],["traceability","需求到验证追溯"]];
      const group = make("fieldset", undefined, "specific-check-group"), groupTitle = make("legend", isDesignAgent ? "设计关注面" : "审查维度"); group.append(groupTitle);
      const grid = make("div", undefined, "specific-check-grid"), selectedChecks = saved.specializedFocus || choices.map(([key]) => key);
      for (const [key,label] of choices) { const check=make("input"); check.type="checkbox"; check.value=key; check.checked=selectedChecks.includes(key); const item=make("label",undefined,"check specific-check"); item.append(check,make("span",label)); grid.append(item); specializedChecks.push(check); }
      group.append(grid); panel.append(group);
    }
    let collaboration = null, collaborate = null, includeDesign = null;
    if (agentKey === "uav-capability-analyst") {
      collaboration = make("fieldset", undefined, "specific-check-group task-agent-collaboration");
      const collaborationLegend = make("legend", "协同选项"); collaborationLegend.append(tip("下游证据审查和设计建议由本次能力分析 Run 按共享预算委派。", "Agent 协同边界")); collaboration.append(collaborationLegend);
      collaborate = make("input"); collaborate.type = "checkbox"; collaborate.id = "collaborate"; collaborate.checked = true;
      const collaborateLabel = make("label", undefined, "check specific-check"); collaborateLabel.append(collaborate, make("span", "联合证据审查 Agent")); collaboration.append(collaborateLabel);
      includeDesign = make("input"); includeDesign.type = "checkbox"; includeDesign.id = "include-design"; includeDesign.checked = true;
      const includeDesignLabel = make("label", undefined, "check specific-check"); includeDesignLabel.append(includeDesign, make("span", "包含设计与验证建议")); collaboration.append(includeDesignLabel);
      panel.append(collaboration);
    }

    function empty(message = "选择具体机型后，查看对象摘要与目标属性资料。") {
      preview.replaceChildren(make("h3", "分析对象与资料预检"), make("p", message, "muted"));
    }
    function clearSelection() {
      ++listGeneration; ++checkGeneration; saved.entityId = ""; result = null; loading = false;
      document.getElementById("entity").value = ""; objects.value = ""; empty(); ready();
    }
    function failure(error) {
      if (!live()) return;
      result = null; loading = false; ready();
      preview.replaceChildren(make("h3", "分析对象与资料预检"), make("p", error.message, "target-blocked"),
        action("重新读取", () => loadList().then(loaded => loaded && check()).catch(failure)));
      notice(error.message, true);
    }
    function metricFields(options) {
      saved.metrics = saved.metrics.filter(key => options.some(item => item.key === key));
      document.getElementById("metrics").value = saved.metrics.join(",");
      metricControls.replaceChildren();
      for (const item of options) {
        const input = make("input"); input.type = "checkbox"; input.value = item.key; input.checked = saved.metrics.includes(item.key);
        input.onchange = () => {
          saved.metrics = [...metricControls.querySelectorAll("input:checked")].map(node => node.value);
          document.getElementById("metrics").value = saved.metrics.join(","); check().catch(failure);
        };
        const label = make("label", undefined, "check"); label.append(input, make("span", item.label)); metricControls.append(label);
      }
    }
    async function loadList() {
      if (!live()) return;
      const request = ++listGeneration;
      objects.disabled = true; previous.disabled = true; next.disabled = true;
      const params = new URLSearchParams({agentKey, objectClass:saved.scope, category:saved.category, q:saved.query, offset:String(saved.offset)});
      let data;
      try { data = await api("/ontology/objects?" + params); }
      catch (error) { if (live() && request === listGeneration) failure(error); return; }
      if (!live() || request !== listGeneration) return;
      scope.replaceChildren(...data.classes.map(item => option(item.key, item.label + "（" + item.count + "）"))); scope.value = saved.scope;
      category.replaceChildren(option("", "全部分类"), ...data.categories.map(item => option(item.key, item.label + "（" + item.count + "）"))); category.value = saved.category;
      objects.replaceChildren(option("", data.total ? "请选择具体机型" : "没有匹配对象"));
      for (const item of data.objects) {
        const label = item.name + " · " + item.country + " · " + (item.selectable ? "v" + item.revision : item.unavailableReason);
        const row = option(item.entityId, label); row.title = [item.name, item.englishName, item.manufacturer, item.categoryLabel, item.unavailableReason].filter(Boolean).join(" · ");
        objects.append(row);
      }
      if (saved.entityId && !data.objects.some(item => item.entityId === saved.entityId)) {
        saved.entityId = ""; result = null; loading = false; ++checkGeneration;
        document.getElementById("entity").value = ""; empty();
      }
      objects.value = saved.entityId; objects.disabled = !data.objects.length;
      count.textContent = data.total + " 个对象 · " + data.readable + " 份属性资料";
      count.title = data.total ? "第 " + (saved.offset + 1) + "–" + Math.min(saved.offset + 30, data.total) + " 项；有属性资料不等于已核验" : "请调整分类或搜索词";
      previous.disabled = saved.offset === 0; next.disabled = !data.hasMore;
      metricFields(data.metrics); ready(); return true;
    }
    async function check() {
      if (!live()) return;
      const request = ++checkGeneration; result = null;
      document.getElementById("entity").value = saved.entityId;
      if (!saved.entityId || !saved.metrics.length) { empty(saved.entityId ? "请至少选择一个目标属性。" : undefined); loading = false; ready(); return; }
      loading = true; ready(); empty("正在读取所选机型的属性版本与证据…");
      const params = new URLSearchParams({agentKey, metrics:saved.metrics.join(",")});
      let data;
      try { data = await api("/ontology/objects/" + encodeURIComponent(saved.entityId) + "/preflight?" + params); }
      catch (error) { if (live() && request === checkGeneration) failure(error); return; }
      if (!live() || request !== checkGeneration) return;
      result = data; loading = false; drawPreview(data); ready();
    }
    function drawPreview(data) {
      const obj = data.object, heading = make("div", undefined, "target-preview-heading");
      heading.append(make("h3", "分析对象"), tip("对象类别为展示分类，不能直接证明其具备某项能力。任务提交前会复核机型、属性版本和数据源；新增版本不会替换本次选择的基线。", "分析对象与版本"));
      const name = make("div", undefined, "target-object-name"); name.append(make("strong", obj.name), make("span", obj.revision ? "本体 v" + obj.revision : "未建属性版本", "badge"));
      const meta = make("dl", undefined, "target-object-meta");
      for (const [label, value] of [["国家 / 厂商", obj.country + " · " + obj.manufacturer], ["范围 / 分类", obj.objectClassLabel + " · " + obj.categoryLabel], ["原始分类", obj.rawCategory || "待补"], ["生命周期", obj.lifecycle]]) {
        const row = make("div"); row.append(make("dt", label), make("dd", value)); meta.append(row);
      }
      const purposeLabel = isDesignAgent ? "设计基线与需求" : isReviewer ? "证据审查" : purposes[saved.purpose];
      const reason = make("p", "本次按“" + purposeLabel + "”分析指定机型；" + data.targetCount + " 项目标中 " + data.availableCount + " 项有候选记录。", "target-selection-reason");
      const precheckTitle = make("div", undefined, "target-preview-heading"); precheckTitle.append(make("h4", "目标属性预检"), tip("候选值不等于已验证事实。有原文、已填写工况、人审接受和独立事实核验分别展示；预检不代替人工审核。范围、多值和冲突保留原口径。", "如何阅读预检"));
      const table = make("table", undefined, "target-preflight-table"), head = make("thead"), headRow = make("tr"), body = make("tbody");
      for (const label of ["属性", "现有候选", "出处", "工况", "人审", "事实核验"]) headRow.append(make("th", label)); head.append(headRow);
      for (const item of data.metrics) {
        const row = make("tr"); row.dataset.metric = item.key; row.dataset.state = item.state;
        const states = {missing:"缺失", conflicting:"多值 / 冲突", qualified:"范围 / 口径待核", reported:"候选值"};
        const value = make("td");
        if (item.values.length) {
          value.append(make("span", item.values.map(valueText).join("；")));
          value.append(tip(JSON.stringify(item.values, null, 2), item.label + " · 原始口径"));
        } else value.append(make("span", "缺失", "target-missing"));
        if (item.state !== "missing") value.append(make("small", states[item.state], item.state === "reported" ? "muted" : "target-missing"));
        const evidence = make("td", item.hasEvidence ? "已关联" : "待补");
        if (item.evidence?.length) evidence.append(tip(JSON.stringify(item.evidence, null, 2), item.label + " · 出处与原文"));
        row.append(make("td", item.label), value, evidence, make("td", item.conditionsDeclared ? "已填写" : "待补"),
          make("td", item.reviewed ? "已接受" : "待审核"), make("td", item.truthVerified ? "已核验" : "待核验")); body.append(row);
      }
      table.append(head, body);
      const tableWrap = make("div", undefined, "target-preflight-scroll"); tableWrap.append(table);
      const details = make("details", undefined, "target-identifiers"); details.append(make("summary", "对象标识与版本"), make("pre", JSON.stringify({entityId:obj.entityId, sourceKey:data.sourceKey, selection:data.selection}, null, 2)));
      const actions = make("div", undefined, "target-preview-actions"); actions.append(action("重新预检", () => check().catch(failure)));
      preview.replaceChildren(heading, name, meta, reason, precheckTitle, tableWrap);
      if (!data.canSubmit) preview.append(make("p", obj.unavailableReason + "；请先补齐本体资料后再提交。", "target-blocked"));
      preview.append(details, actions);
    }
    purpose.onchange = () => { saved.purpose = purpose.value; updatePurpose(); if (result) drawPreview(result); ready(); };
    requirement.oninput = () => { saved.requirementText = requirement.value; ready(); };
    configuration.oninput = () => { saved.configuration = configuration.value; };
    if (specializedField) specializedField.oninput = () => { saved.specializedNotes = specializedField.value; };
    for (const check of specializedChecks) check.onchange = () => { saved.specializedFocus = specializedChecks.filter(item => item.checked).map(item => item.value); };
    scope.onchange = () => { saved.scope = scope.value; saved.category = ""; saved.offset = 0; clearSelection(); loadList().catch(failure); };
    category.onchange = () => { saved.category = category.value; saved.offset = 0; clearSelection(); loadList().catch(failure); };
    search.oninput = () => {
      saved.query = search.value; saved.offset = 0; clearSelection(); objects.disabled = true;
      clearTimeout(timer); timer = setTimeout(() => loadList().catch(failure), 220);
    };
    objects.onchange = () => { saved.entityId = objects.value; check().catch(failure); };
    // These controls are created only in the UAV capability panel above.
    if (collaborate) {
      const updateCollaboration = () => { includeDesign.disabled = !collaborate.checked; };
      collaborate.onchange = updateCollaboration; updateCollaboration();
    }
    current = {check, setSubmitting(value) { sending = value; ready(); }, getInput() {
      if (sending) throw Error("任务正在提交，请稍候");
      if (!live() || loading || !result?.canSubmit || !saved.entityId || !saved.metrics.length) throw Error("请先选择具体机型并完成资料预检");
      if ((saved.purpose === "requirements" || isDesignAgent) && !saved.requirementText.trim()) throw Error(isDesignAgent ? "请填写性能目标与验收工况" : "请填写需求阈值与适用工况");
      if (isDesignAgent || isReviewer) saved.specializedFocus = specializedChecks.filter(item => item.checked).map(item => item.value);
      const focusLabels = Object.fromEntries([...(isDesignAgent ? [["airframe","总体与结构"],["propulsion","动力与能源"],["payload","任务载荷"],["manufacturing","制造工艺"],["verification","试验验证"]] : [["citation","引文与来源"],["entity","实体与构型归属"],["conditions","单位与工况"],["independence","来源独立性"],["traceability","需求到验证追溯"]])]);
      const agentSpecificContext = isDesignAgent || isReviewer ? [(isDesignAgent ? "设计硬约束" : "审查关注点") + "：" + (saved.specializedNotes || "未补充"), (isDesignAgent ? "设计关注面" : "审查维度") + "：" + saved.specializedFocus.map(key => focusLabels[key]).filter(Boolean).join("、")].join("\n") : "";
      const specificSettings = {schemaVersion:"1.0", kind:isDesignAgent ? "uav-design" : isReviewer ? "uav-evidence-review" : "uav-capability-analysis",
        entityId:saved.entityId, entitySelection:result.selection, purpose:saved.purpose,
        metrics:[...saved.metrics], configuration:saved.configuration, requirementText:saved.requirementText,
        specializedNotes:saved.specializedNotes || "", specializedFocus:[...(saved.specializedFocus || [])]};
      return {entityId:saved.entityId, entitySelection:result.selection, analysisPurpose:saved.purpose, agentSpecificContext,
        analysisContext:{configuration:saved.configuration, requirementText:saved.requirementText},
        requirements:Object.fromEntries(saved.metrics.map(key => [key, null])), specificSettings};
    }};
    empty(); ready();
    try { const loaded = await loadList(); if (loaded && live() && saved.entityId) await check(); } catch (error) { failure(error); }
  }
  window.AgentAdminTargets = {mount, getInput:() => {
    if (!current) throw Error("请先选择 Agent 并完成专属设置");
    return current.getInput();
  }, refresh:() => current?.check(), setSubmitting:value => current?.setSubmitting(value)};
})();
