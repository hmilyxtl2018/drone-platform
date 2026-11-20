/* Profile drafts are edited locally, validated by the server, then explicitly published. */
"use strict";
(function () {
  const copy = value => JSON.parse(JSON.stringify(value));
  const encode = encodeURIComponent;
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const terminalStates = ["completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"];
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  };
  const dump = value => node("pre", JSON.stringify(value, null, 2));
  const lines = value => Array.isArray(value) ? value.join("\n") : "";
  const list = value => value.split(/\n|,/).map(item => item.trim()).filter(Boolean);
  const get = (value, path) => path.reduce((result, key) => result?.[key], value);
  const set = (value, path, field) => {
    let current = value;
    for (const key of path.slice(0, -1)) current = current[key] ??= {};
    current[path.at(-1)] = field;
  };

  async function mount(panel, options = {}) {
    const request = options.api || api;
    const notify = options.notice || notice;
    const canEdit = options.canEdit ?? hasRole("admin");
    const root = node("div"); root.dataset.profileConfig = "true"; panel.append(root);
    root.append(node("h2", "Agent Profile 配置与发布"),
      node("p", "保存草稿、校验并审阅差异后，明确发布到新运行。已有运行继续使用原快照。", "help-note help-flow"));
    if (!canEdit) { root.append(node("p", "此页面需要管理员权限。", "muted")); return; }
    const [agents, tools, versions, providers, sources] = await Promise.all([
      request("/config/agents"), request("/config/tools"), request("/config/versions"),
      request("/config/providers"), request("/config/sources")
    ]);
    if (!agents.length) { root.append(node("p", "尚无可编辑的 Agent。")); return; }
    const selection = node("label", "选择 Agent Profile"), picker = node("select");
    picker.setAttribute("aria-label", "选择 Agent Profile");
    for (const agent of agents) picker.append(new Option((agent.display_name || agent.agent_key) + " · " + agent.agent_key, agent.agent_key));
    if (options.agentKey && agents.some(agent => agent.agent_key === options.agentKey)) picker.value = options.agentKey;
    selection.append(picker); root.append(selection);
    const editor = node("div"); root.append(editor);
    let generation = 0, activeKey = picker.value, dirty = () => false;

    const action = (title, fn, className) => {
      const button = node("button", title, className); button.type = "button";
      button.onclick = async () => {
        button.disabled = true;
        try { await fn(); } catch (error) { notify(error.message, true); }
        finally { button.disabled = button.dataset.disabled === "true"; }
      };
      return button;
    };
    function field(parent, title, kind, value, settings = {}) {
      const label = node("label", kind === "checkbox" ? undefined : title, kind === "checkbox" ? "check" : undefined);
      const input = node(kind === "textarea" || kind === "json" || kind === "list" ? "textarea" : kind === "select" ? "select" : "input");
      input.setAttribute("aria-label", title);
      if (input.tagName === "INPUT") input.type = kind === "checkbox" ? "checkbox" : kind === "number" ? "number" : "text";
      if (kind === "select") {
        const choices = [...(settings.choices || [])];
        if (value != null && !choices.some(item => (typeof item === "object" ? item.value : item) === value)) choices.push(value);
        for (const item of choices) input.append(new Option(typeof item === "object" ? item.label : item, typeof item === "object" ? item.value : item));
      }
      if (kind === "checkbox") input.checked = value === true;
      else input.value = kind === "json" ? JSON.stringify(value ?? {}, null, 2) : kind === "list" ? lines(value) : value ?? "";
      if (input.tagName === "TEXTAREA") input.rows = settings.rows || (kind === "json" ? 5 : 3);
      if (kind === "number") { input.step = settings.step || "1"; if (settings.min !== undefined) input.min = settings.min; if (settings.max !== undefined) input.max = settings.max; }
      if (settings.readOnly) input.disabled = true;
      if (settings.path) input.dataset.configPath = settings.path;
      label.append(input);
      if (kind === "checkbox") label.append(document.createTextNode(title));
      parent.append(label);
      return () => {
        if (kind === "checkbox") return input.checked;
        if (kind === "number") {
          if (!input.value.trim() || !Number.isFinite(Number(input.value))) throw Error(title + "应为有限数值");
          const result = Number(input.value);
          if (settings.step !== "any" && !Number.isSafeInteger(result)) throw Error(title + "应为安全整数");
          return result;
        }
        if (kind === "json") { try { return JSON.parse(input.value); } catch { throw Error(title + "包含无效 JSON"); } }
        if (kind === "list") return list(input.value);
        return settings.nullable && !input.value ? null : input.value;
      };
    }
    function section(parent, title, open = false) {
      const details = node("details"); details.open = open; details.append(node("summary", title)); parent.append(details); return details;
    }
    function diffs(parent, title, changes) {
      const details = section(parent, title + "（" + changes.length + " 项）", true);
      if (!changes.length) { details.append(node("p", "无差异。", "muted")); return; }
      const table = node("table"), header = node("tr");
      for (const text of ["字段 / 操作", "修改前", "修改后"]) header.append(node("th", text));
      table.append(header);
      for (const change of changes) {
        const row = node("tr"), before = node("td"), after = node("td");
        before.append(dump(change.before ?? null)); after.append(dump(change.after ?? null));
        row.append(node("td", change.path + " · " + change.operation), before, after); table.append(row);
      }
      details.append(table);
    }
    async function open(draftId = null, revision = null) {
      const epoch = ++generation, key = picker.value;
      editor.replaceChildren(node("p", "正在读取配置与不可变历史…", "muted"));
      const [live, drafts, publications, saved] = await Promise.all([
        request("/config-profiles/" + encode(key)), request("/config-profiles/" + encode(key) + "/drafts"),
        request("/config-profiles/" + encode(key) + "/publications"),
        draftId ? request("/config-drafts/" + encode(draftId) + (revision ? "?revision=" + revision : "")) : Promise.resolve(null)
      ]);
      if (epoch !== generation || key !== picker.value) return;
      activeKey = key; editor.replaceChildren();
      const documentValue = copy(saved?.document || live.document);
      const historical = !!saved && saved.revision !== saved.currentRevision;
      let validation = null, publishOperation = null;
      const published = !!saved && publications.some(item => item.draftId === saved.draftId && item.revision === saved.revision);
      const draftSelect = node("select"); draftSelect.setAttribute("aria-label", "选择配置草稿");
      draftSelect.append(new Option("从当前已生效配置新建草稿", ""));
      for (const item of drafts) draftSelect.append(new Option(item.draftId + " · r" + item.revision + " · " + item.reason, item.draftId));
      draftSelect.value = saved?.draftId || "";
      draftSelect.onchange = async () => {
        if (dirty() && !window.confirm("当前编辑尚未保存，切换后将丢失这些编辑。继续切换？")) { draftSelect.value = saved?.draftId || ""; return; }
        try { await open(draftSelect.value || null); } catch (error) { notify(error.message, true); }
      };
      editor.append(node("h3", "草稿与当前版本"), draftSelect,
        action("重新读取当前配置与草稿", async () => {
          if (dirty() && !window.confirm("当前编辑尚未保存，重新读取后将丢失这些编辑。继续读取？")) return;
          await open(saved?.draftId || null);
        }), node("p", "当前生效配置哈希：" + live.liveHash, "muted"));
      if (saved) {
        editor.append(node("p", "草稿 " + saved.draftId + " · 修订 r" + saved.revision + " / " + saved.currentRevision + " · " + saved.documentHash, "muted"));
        const revisions = node("select"); revisions.setAttribute("aria-label", "查看草稿历史修订");
        const start = Math.max(1, saved.currentRevision - 99);
        if (saved.revision < start) revisions.append(new Option("r" + saved.revision, saved.revision));
        for (let n = start; n <= saved.currentRevision; n++) revisions.append(new Option("r" + n + (n === saved.currentRevision ? "（最新）" : ""), n));
        revisions.value = saved.revision;
        revisions.onchange = async () => {
          if (dirty() && !window.confirm("当前编辑尚未保存，切换后将丢失这些编辑。继续切换？")) { revisions.value = saved.revision; return; }
          try { await open(saved.draftId, Number(revisions.value)); } catch (error) { notify(error.message, true); }
        };
        editor.append(revisions);
      }
      if (historical) { const status = node("p", "历史修订 · 只读", "muted"); status.append(miniTip("请选择最新修订继续编辑。", "help", "历史修订")); editor.append(status); }
      if (published) { const status = node("p", "此修订已发布", "muted"); status.append(miniTip("需要继续修改时，请从当前生效配置新建草稿。", "flow", "继续修改配置")); editor.append(status); }
      const form = node("form"); form.onsubmit = event => event.preventDefault(); editor.append(form);
      const readers = [], collections = [];
      function mapped(parent, title, path, kind = "text", settings = {}) {
        const read = field(parent, title, kind, get(documentValue, path) ?? settings.default, { ...settings, path: "/" + path.join("/") });
        readers.push(result => set(result, path, read())); return read;
      }
      let group = section(form, "Profile：身份、职责与提示词", true);
      for (const [name, key] of [["显示名称", "display_name"], ["业务领域", "domain"], ["Profile 版本", "current_version"]]) mapped(group, name, ["definition", key]);
      mapped(group, "Agent 状态", ["definition", "status"], "select", { choices: ["draft", "testing", "published", "deprecated", "retired", "disabled", "active"] });
      mapped(group, "职责说明", ["definition", "description"], "textarea");
      mapped(group, "System Prompt", ["definition", "system_prompt"], "textarea", { rows: 7 });
      for (const [name, key] of [["输入实体（每行一项）", "input_entities_json"], ["输出实体（每行一项）", "output_entities_json"], ["允许写入范围（每行一项）", "allowed_write_json"], ["禁止行为（每行一项）", "forbidden_actions_json"]]) mapped(group, name, ["scope", key], "list");
      mapped(section(group, "高级：职责对象"), "职责对象 JSON", ["scope", "responsibility_json"], "json");

      group = section(form, "模型、来源与共享预算", true);
      mapped(group, "模型 Provider", ["models", "model_provider"], "select", { choices: providers.filter(item => item.provider_type === "model").map(item => item.provider_key) });
      mapped(group, "模型名称", ["models", "model_name"]);
      mapped(group, "提示词版本", ["models", "prompt_version"]);
      mapped(group, "Temperature", ["models", "temperature"], "number", { step: "any", min: 0, max: 2 });
      mapped(group, "单次最大输出 Token", ["models", "max_output_tokens"], "number", { min: 1, max: 8192 });
      mapped(group, "要求结构化输出", ["models", "structured_output_required"], "checkbox");
      mapped(group, "启用 Planner", ["models", "planner_enabled"], "checkbox");
      mapped(group, "本体数据源", ["models", "config_json", "ontologySourceKey"], "select", { choices: sources.map(item => ({ value: item.source_key, label: item.source_key + (item.read_only ? "（只读）" : "（可写）") })), default: "uav-ontology" });
      mapped(group, "网页 Provider", ["models", "config_json", "webProviderKey"], "select", { choices: providers.filter(item => item.provider_type === "web").map(item => item.provider_key), default: "firecrawl" });
      mapped(group, "允许网页域名（每行一项）", ["models", "config_json", "sourcePolicy", "allowedDomains"], "list");
      mapped(group, "最多抓取页数", ["models", "config_json", "sourcePolicy", "maxPages"], "number", { min: 1, max: 100, default: 8 });
      mapped(group, "每次模型请求最多重试", ["models", "config_json", "retryLimit"], "number", { min: 0, max: 1, default: 1 });
      for (const [name, key] of [["共享模型调用上限", "model_calls"], ["共享输入 Token 上限", "input_tokens"], ["共享输出 Token 上限", "output_tokens"], ["共享工具调用上限", "tool_calls"], ["共享 HTTP 调用上限", "http_calls"], ["共享搜索上限", "search_calls"], ["共享抓取上限", "scrape_calls"]]) mapped(group, name, ["models", "config_json", "budgetLimits", key], "number", { min: 0 });
      const contextPolicy = documentValue.models.config_json.contextPolicy || {};
      mapped(group, "上下文最大字节数", ["models", "config_json", "contextPolicy", "maxBytes"], "number", { min: 4096, max: 2000000, default: 120000 });
      mapped(group, "允许历史记忆", ["models", "config_json", "contextPolicy", "allowHistoricalMemory"], "checkbox");
      mapped(group, "工具上下文投影", ["models", "config_json", "contextPolicy", "toolProjection"], "checkbox");
      mapped(group, "上下文新鲜度策略", ["models", "config_json", "contextPolicy", "freshness"], "select", { choices: ["pinned", "run", "live"], default: "pinned" });
      mapped(section(group, "高级：角色上下文投影"), "roleProjections JSON", ["models", "config_json", "contextPolicy", "roleProjections"], "json", { rows: 8, default: contextPolicy.roleProjections || {} });
      const extraModel = copy(documentValue.models.config_json);
      for (const key of ["ontologySourceKey", "webProviderKey", "sourcePolicy", "retryLimit", "budgetLimits", "contextPolicy"]) delete extraModel[key];
      const extraRead = field(section(group, "高级：其他模型策略"), "其他模型策略 JSON", "json", extraModel);
      readers.push(result => { const extra = extraRead(); if (!extra || Array.isArray(extra) || typeof extra !== "object") throw Error("其他模型策略应为对象"); for (const key of ["ontologySourceKey", "webProviderKey", "sourcePolicy", "retryLimit", "budgetLimits", "contextPolicy"]) if (Object.hasOwn(extra, key)) throw Error("常用模型字段请使用上方表单"); result.models.config_json = { ...extra, ...Object.fromEntries(["ontologySourceKey", "webProviderKey", "sourcePolicy", "retryLimit", "budgetLimits", "contextPolicy"].map(key => [key, result.models.config_json[key]])) }; });

      group = section(form, "工具版本绑定", true);
      const bindingSection = group, bindingRows = [];
      bindingSection.append(node("p", "每行代表一个已注册工具绑定；版本、启用状态、审批要求和运行参数可直接对照查看。", "help-note help-help"));
      const bindingTableWrap = node("div", undefined, "binding-table-wrap");
      const bindingTable = node("table", undefined, "binding-table");
      const bindingHead = node("thead"), bindingHeadRow = node("tr");
      for (const title of ["工具", "版本", "启用", "逐步审批", "超时（秒）", "重试", "其他参数", "操作"]) bindingHeadRow.append(node("th", title));
      bindingHead.append(bindingHeadRow);
      const bindingBody = node("tbody");
      bindingTable.append(bindingHead, bindingBody);
      bindingTableWrap.append(bindingTable);
      bindingSection.append(bindingTableWrap);
      const compactSelect = (label, value, choices) => {
        const input = node("select"); input.setAttribute("aria-label", label);
        const values = choices.map(item => typeof item === "object" ? item.value : item);
        const available = [...choices]; if (value != null && value !== "" && !values.includes(value)) available.push(value);
        for (const item of available) input.append(new Option(typeof item === "object" ? item.label : item, typeof item === "object" ? item.value : item));
        if (value != null) input.value = value;
        return input;
      };
      const compactInput = (label, value, type = "text") => {
        const input = node("input"); input.type = type; input.setAttribute("aria-label", label); input.value = value ?? "";
        if (type === "number") { input.min = "0"; input.step = "1"; }
        return input;
      };
      function addBinding(value = {}) {
        const row = node("tr"); bindingBody.append(row);
        const tool = compactSelect("绑定工具", value.tool_key || tools[0]?.tool_key || "", tools.map(item => ({ value: item.tool_key, label: item.tool_name && item.tool_name !== item.tool_key ? item.tool_name + " · " + item.tool_key : item.tool_key })));
        const version = compactSelect("工具版本", "", []);
        const populate = selected => { version.replaceChildren(); const available = versions.filter(item => item.tool_key === tool.value).map(item => item.version); if (selected && !available.includes(selected)) available.push(selected); for (const item of available) version.append(new Option(item, item)); if (selected) version.value = selected; };
        populate(value.tool_version); tool.addEventListener("change", () => { populate(null); changed(); });
        const enabled = node("input"); enabled.type = "checkbox"; enabled.checked = value.enabled ?? true; enabled.setAttribute("aria-label", "启用工具绑定");
        const approval = node("input"); approval.type = "checkbox"; approval.checked = value.approval_required ?? false; approval.setAttribute("aria-label", "此工具每步需要人工审批");
        const config = copy(value.config_json || {});
        const timeout = compactInput("工具超时秒数（留空沿用默认）", config.timeoutSeconds ?? "", "number");
        const retries = compactInput("工具重试次数（留空沿用默认）", config.retryLimit ?? "", "number");
        delete config.timeoutSeconds; delete config.retryLimit;
        const params = node("details"), paramsSummary = node("summary", "编辑 JSON"), extra = node("textarea");
        extra.setAttribute("aria-label", "其他绑定参数 JSON"); extra.rows = 3; extra.value = JSON.stringify(config, null, 2);
        params.append(paramsSummary, extra);
        for (const input of [version, enabled, approval, timeout, retries, extra]) input.addEventListener("change", changed);
        extra.addEventListener("input", changed);
        const cell = (content, className) => { const td = node("td", undefined, className); td.append(content); return td; };
        row.append(cell(tool, "binding-tool"), cell(version, "binding-version"), cell(enabled, "binding-check"), cell(approval, "binding-check"), cell(timeout, "binding-number"), cell(retries, "binding-number"), cell(params, "binding-params"));
        const entry = () => {
          let result; try { result = JSON.parse(extra.value || "{}"); } catch { throw Error("其他绑定参数包含无效 JSON"); }
          if (!result || Array.isArray(result) || typeof result !== "object") throw Error("工具参数应为对象");
          for (const [key, input] of [["timeoutSeconds", timeout], ["retryLimit", retries]]) { const raw = input.value.trim(); if (!raw) delete result[key]; else if (/^\d+$/.test(raw) && Number.isSafeInteger(Number(raw))) result[key] = Number(raw); else throw Error("工具超时与重试应为非负整数"); }
          return { tool_key: tool.value, tool_version: version.value, enabled: enabled.checked, approval_required: approval.checked, config_json: result };
        };
        bindingRows.push(entry); const remove = action("移除", () => { row.remove(); bindingRows.splice(bindingRows.indexOf(entry), 1); changed(); });
        row.append(cell(remove, "binding-actions"));
      }
      for (const value of documentValue.bindings) addBinding(value);
      group.append(action("添加工具绑定", () => { addBinding(); changed(); }));
      collections.push(result => { result.bindings = bindingRows.map(read => read()); });

      group = section(form, "计划 / Plan 与执行图", true);
      const stepSection = group, stepRows = [];
      group.append(node("p", "依赖填写前置节点 Key；工具必须具有启用的版本绑定。循环依赖和执行顺序由服务端校验。", "help-note help-help"));
      function addStep(value = {}) {
        const row = node("fieldset"); row.append(node("legend", "计划节点")); stepSection.append(row);
        const reads = {};
        for (const [name, key, kind, fallback] of [["计划节点 Key", "step_key", "text", ""], ["计划节点名称", "name", "text", ""], ["执行序号", "sequence_no", "number", stepRows.length + 1], ["计划节点说明", "description", "textarea", ""], ["前置节点（每行一个 Key）", "dependencies", "list", []], ["本节点工具（每行一个 Key）", "tools", "list", []], ["并行分组（可空）", "parallel_group", "text", null], ["必需节点", "required", "checkbox", true], ["本节点重试上限", "retry_limit", "number", 0]]) reads[key] = field(row, name, kind, value[key] ?? fallback, { nullable: key === "parallel_group", min: 0 });
        const advanced = section(row, "高级：节点输入 / 输出契约");
        reads.input_contract = field(advanced, "节点输入 JSON Schema", "json", value.input_contract || { type: "object" });
        reads.output_contract = field(advanced, "节点输出 JSON Schema", "json", value.output_contract || { type: "object" });
        const entry = () => Object.fromEntries(Object.entries(reads).map(([key, read]) => [key, read()]));
        stepRows.push(entry); row.append(action("移除此节点", () => { row.remove(); stepRows.splice(stepRows.indexOf(entry), 1); changed(); }));
      }
      for (const value of documentValue.steps) addStep(value);
      group.append(action("添加节点", () => { addStep(); changed(); }));
      collections.push(result => { result.steps = stepRows.map(read => read()); });

      group = section(form, "Loop 与机器收敛", true);
      for (const [name, key] of [["完成条件（每行一项）", "completion_conditions_json"], ["允许终态（每行一项）", "allowed_terminal_states_json"]]) mapped(group, name, ["convergence", key], "list");
      for (const [name, key] of [["收敛工具调用上限", "max_tool_calls"], ["收敛模型调用上限", "max_model_calls"], ["运行时间上限（秒）", "max_runtime_seconds"], ["无新发现停止轮数", "no_new_finding_rounds"]]) mapped(group, name, ["convergence", key], "number", { min: 1 });
      const loopSection = section(group, "补查循环规则", true), loopRows = [];
      function addLoop(value = {}) {
        const row = node("fieldset"); row.append(node("legend", "循环规则")); loopSection.append(row);
        const reads = {};
        for (const [name, key, kind, fallback] of [["Loop ID", "loop_id", "text", "loop-" + crypto.randomUUID()], ["触发条件", "trigger_condition", "text", "missing-evidence"], ["返回节点 Key", "return_step_key", "text", ""], ["最多循环轮数", "max_iterations", "number", 2], ["无进展停止轮数", "no_progress_limit", "number", 1], ["循环停止状态", "stop_action", "select", "requires-human-review"]]) reads[key] = field(row, name, kind, value[key] ?? fallback, { choices: terminalStates, min: 1, max: 100 });
        const entry = () => Object.fromEntries(Object.entries(reads).map(([key, read]) => [key, read()]));
        loopRows.push(entry); row.append(action("移除此循环", () => { row.remove(); loopRows.splice(loopRows.indexOf(entry), 1); changed(); }));
      }
      for (const value of documentValue.loops) addLoop(value);
      loopSection.append(action("添加循环", () => { addLoop(); changed(); }));
      collections.push(result => { result.loops = loopRows.map(read => read()); });

      group = section(form, "资源、证据与审核策略");
      mapped(group, "最大并行工具数", ["resources", "max_parallel_tools"], "number", { min: 1, max: 32 });
      mapped(group, "每个工具最多重试", ["resources", "max_retries_per_tool"], "number", { min: 0, max: 10 });
      mapped(group, "允许风险等级（每行一项）", ["resources", "permitted_risk_levels_json"], "list");
      mapped(group, "最低证据数", ["evidence", "minimum_evidence_count"], "number", { min: 1, max: 1000 });
      mapped(group, "冲突处理", ["evidence", "conflict_action"], "select", { choices: ["retain-and-escalate", "reject"] });
      for (const [name, key] of [["要求来源 URI", "require_source_uri"], ["要求数据时间", "require_data_timestamp"], ["允许推断", "allow_inference"], ["推断必须披露", "inference_disclosure_required"]]) mapped(group, name, ["evidence", key], "checkbox");
      mapped(group, "审批节点（每行一项）", ["approval", "approval_points_json"], "list");
      mapped(group, "审批角色（每行一项）", ["approval", "approver_roles_json"], "list");
      mapped(section(group, "高级：证据策略"), "证据策略 JSON", ["evidence", "policy_json"], "json");
      group = section(form, "输出契约与运行审计");
      for (const [name, key] of [["输出必须带证据", "required_evidence"], ["输出必须带假设", "required_assumptions"], ["输出必须声明置信度", "required_confidence"]]) mapped(group, name, ["output", key], "checkbox");
      mapped(section(group, "高级：输出 Schema"), "输出 JSON Schema", ["output", "schema_json"], "json");
      for (const [name, key] of [["启用审计 Trace", "trace_enabled"], ["保存工具输入", "capture_tool_inputs"], ["保存工具输出", "capture_tool_outputs"], ["记录节点事件", "emit_step_events"], ["记录运行指标", "emit_metrics"]]) mapped(group, name, ["observability", key], "checkbox");
      mapped(group, "审计保留天数", ["observability", "retain_days"], "number", { min: 1, max: 36500 });
      mapped(section(group, "高级：审计策略"), "审计策略 JSON", ["observability", "policy_json"], "json");
      mapped(section(group, "高级：验证规则"), "验证规则 JSON", ["validationRules"], "json");
      function collect() {
        const result = copy(documentValue); for (const read of readers) read(result); for (const read of collections) read(result); return result;
      }
      const initialForm = collect();
      dirty = () => { try { return !same(collect(), initialForm); } catch { return true; } };
      const reason = field(form, "本次草稿变更理由", "textarea", "");
      const status = node("p", "尚未校验。", "muted"), results = node("div");
      const commands = node("div", undefined, "review-actions"); form.append(commands, status, results);
      const save = action(saved ? "保存为新修订并校验" : "保存新草稿并校验", async () => {
        const document = collect(), why = reason().trim(); if (!why) throw Error("请填写本次草稿变更理由");
        const result = saved ? await request("/config-drafts/" + encode(saved.draftId), { method: "PUT", body: JSON.stringify({ document, expectedRevision: saved.currentRevision, reason: why }) }) : await request("/config-profiles/" + encode(key) + "/drafts", { method: "POST", body: JSON.stringify({ document, reason: why }) });
        notify("草稿已保存为 r" + result.revision + "；正在读取服务器校验结果。"); await open(result.draftId);
      });
      const validate = action("校验已保存修订并查看差异", async () => {
        if (!saved || dirty()) throw Error("请先保存当前编辑，再校验该修订");
        await validateSaved();
      });
      commands.append(save, validate);
      const publishBox = section(form, "发布已审阅修订", true);
      const publishReason = field(publishBox, "发布理由", "textarea", "");
      const consent = field(publishBox, "我已审阅本修订的校验结果与差异，确认发布后用于新运行", "checkbox", false);
      const publish = action("明确发布此配置修订", async () => {
        if (!publishable() || !consent()) throw Error("请先保存、通过校验并明确确认当前修订");
        const why = publishReason().trim(); if (!why) throw Error("请填写发布理由");
        const signature = saved.draftId + ":" + saved.revision + ":" + why;
        if (!publishOperation || publishOperation.signature !== signature) publishOperation = { signature, key: "config-" + crypto.randomUUID() };
        const result = await request("/config-drafts/" + encode(saved.draftId) + "/publish", { method: "POST", body: JSON.stringify({ expectedRevision: saved.revision, idempotencyKey: publishOperation.key, reason: why }) });
        notify("配置已发布为版本 " + result.publicationNo + "。新运行使用此配置。"); await open();
      });
      publishBox.append(publish);
      function publishable() { return !!saved && !historical && !published && !dirty() && validation?.valid === true && validation.revision === saved.revision && !validation.liveChanged && !validation.dependenciesChanged; }
      function refreshPublish() {
        publish.disabled = !publishable() || !consent() || !publishReason().trim(); publish.dataset.disabled = String(publish.disabled);
        validate.disabled = !saved || dirty(); validate.dataset.disabled = String(validate.disabled);
      }
      function changed() {
        if (validation && dirty()) status.textContent = "存在未保存编辑；请保存新修订并重新校验。";
        refreshPublish();
      }
      async function validateSaved() {
        validation = await request("/config-drafts/" + encode(saved.draftId) + "/validate", { method: "POST", body: JSON.stringify({ revision: saved.revision }) });
        results.replaceChildren();
        status.textContent = validation.valid ? "服务器校验通过：r" + validation.revision : "服务器校验未通过：r" + validation.revision;
        status.className = validation.valid ? "muted" : "error";
        if (validation.liveChanged) results.append(node("p", "当前生效配置已改变，请从当前配置新建草稿后重新审阅。", "error"));
        if (validation.dependenciesChanged) results.append(node("p", "引用的工具、Provider 或来源注册发生变化，请保存新修订并重新校验。", "error"));
        for (const issue of [...(validation.errors || []), ...(validation.warnings || [])]) results.append(node("p", issue.path + "：" + issue.message));
        diffs(results, "相对创建草稿时的差异", validation.diff || []); diffs(results, "相对当前生效配置的差异", validation.liveDiff || []);
        refreshPublish();
      }
      form.addEventListener("input", changed); form.addEventListener("change", changed);
      if (historical || published) { for (const input of form.querySelectorAll("input,select,textarea,button")) input.disabled = true; save.dataset.disabled = "true"; }
      refreshPublish();
      const history = section(editor, "不可变发布历史（" + publications.length + " 项）", true);
      if (!publications.length) { const empty = node("p", "尚无通过此入口发布的历史。", "muted"); empty.append(miniTip("现有 Profile 版本号不代表完整发布历史。", "info", "发布历史")); history.append(empty); }
      for (const item of publications) {
        const record = section(history, "版本 " + item.publicationNo + " · " + item.publishedAt + " · " + item.reason);
        record.append(node("p", "操作者：" + item.actorId + " · " + item.draftId + " r" + item.revision, "muted"), node("p", "发布哈希：" + item.liveHash, "muted"));
        diffs(record, "发布差异", item.diff || []);
        record.append(action("查看此发布的冻结文档", async () => {
          const frozen = await request("/config-publications/" + encode(item.publicationId));
          const prior = record.querySelector("[data-frozen-profile]"); if (prior) prior.remove();
          const content = dump(frozen.document); content.dataset.frozenProfile = "true"; record.append(content);
          const sourceRunId=frozen.document?.models?.config_json?.skillMigration?.sourceRunId;
          if(sourceRunId&&window.AgentSkillUI&&!record.querySelector('.skill-trial-form')) window.AgentSkillUI.releaseForm(record,sourceRunId,item.publicationId,request,{rollback:true,onDraft:result=>open(result.draftId)});
        }));
      }
      if (saved) await validateSaved();
    }
    picker.onchange = async () => {
      if (dirty() && !window.confirm("当前编辑尚未保存，切换后将丢失这些编辑。继续切换？")) { picker.value = activeKey; return; }
      try { await open(); } catch (error) { notify(error.message, true); }
    };
    await open(options.draftId || null);
  }
  window.AgentProfileConfig = { mount };
})();

async function renderProfileConfig(panel, options = {}) {
  return window.AgentProfileConfig.mount(panel, options);
}

