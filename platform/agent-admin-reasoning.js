"use strict";
/* Recorded reasoning is inspectable evidence, never a reconstruction of hidden thoughts. */
(() => {
  const arr = value => Array.isArray(value) ? value : [];
  const object = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const labels = {
    passed:"通过", failed:"未通过", "not-applicable":"不适用", candidate:"候选", pending:"待检查",
    accepted:"已接受", "accepted-as-unverified":"已接受 · 尚未核验", rejected:"已拒绝", "needs-evidence":"需补证", "evidence-requested":"需补证",
    proposed:"已提出", "evidence-bound":"已绑定引文", "support-reviewed":"支持审核通过", "needs-more-evidence":"需补证", conflicted:"有冲突",
    conflict:"有冲突", conflicting:"有冲突", withdrawn:"已撤回", published:"已发布",
    supported:"有引文支持", unsupported:"缺少引文支持", unverified:"尚未核验", verified:"已核验",
    continue:"继续", retry:"重试", stop:"停止", defer:"保留缺口", complete:"完成",
    "mark-conflict":"标记冲突", "request-evidence":"要求补证", accept:"接受", reject:"拒绝",
    withdraw:"撤回", publish:"发布候选", "fail-run":"终止运行", "block-publication":"阻止发布",
    "complete-with-gaps":"保留缺口", "record-gap":"记录缺口", warning:"提醒", error:"错误", critical:"阻断"
  };
  const display = value => {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "string") return labels[value] || value;
    if (typeof value === "object") return value.label || value.name || value.statement || value.summary || value.reason || value.evidenceId || value.id || JSON.stringify(value);
    return String(value);
  };
  const button = (text, action, cls) => {
    const node = make("button", text, cls); node.type = "button"; node.onclick = action; return node;
  };
  const raw = (target, title, value) => {
    const detail = make("details", undefined, "reasoning-raw");
    detail.append(make("summary", title), make("pre", JSON.stringify(value, null, 2))); target.append(detail);
  };
  const empty = (target, text) => target.append(make("p", text, "reasoning-empty"));
  const fact = (target, label, value) => {
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
    const row = make("div", undefined, "reasoning-fact");
    row.append(make("dt", label), make("dd", Array.isArray(value) ? value.map(display).join("；") : display(value))); target.append(row);
  };
  const badge = (value, text) => make("span", text || display(value), "reasoning-status reasoning-status--" + String(value).replace(/[^a-z-]/gi,""));
  let tabSequence = 0;

  function tabset(definitions, onSelect, cls = "reasoning-tabs") {
    const root = make("div", undefined, cls), buttons = new Map(), sequence = ++tabSequence;
    root.setAttribute("role", "tablist"); root.setAttribute("aria-label", "研判信息视图");
    const select = key => {
      for (const [name, node] of buttons) { node.setAttribute("aria-selected", String(name === key)); node.tabIndex = name === key ? 0 : -1; }
      onSelect(key);
    };
    definitions.forEach(([key, label], index) => {
      const node = button(label, () => select(key)); node.setAttribute("role", "tab"); node.dataset.reasoningTab = key; node.id="reasoning-tab-"+sequence+"-"+key;
      node.onkeydown = event => {
        const next = event.key === "ArrowRight" ? (index + 1) % definitions.length : event.key === "ArrowLeft" ? (index + definitions.length - 1) % definitions.length : event.key === "Home" ? 0 : event.key === "End" ? definitions.length - 1 : -1;
        if (next < 0) return;
        event.preventDefault(); const key = definitions[next][0]; select(key); buttons.get(key).focus();
      };
      buttons.set(key,node); root.append(node);
    });
    return {root, select, buttons};
  }

  function mountRun(holder, {runId, api, notice = () => {}, triggerHost, canReview = false}) {
    const prefix = "/runs/" + encodeURIComponent(runId);
    const drawer = make("section", undefined, "reasoning-drawer"); drawer.hidden = true;
    drawer.id = "reasoning-drawer-" + runId; drawer.setAttribute("aria-label", "本次运行研判过程");
    const trigger = button("研判过程", () => {
      drawer.hidden = !drawer.hidden; trigger.setAttribute("aria-expanded", String(!drawer.hidden));
      if (!drawer.hidden) { draw(); refresh(); }
    }, "reasoning-toggle");
    trigger.setAttribute("aria-controls", drawer.id); trigger.setAttribute("aria-expanded", "false");
    trigger.title = "查看已保存的研判检查点、质量门和关系候选";
    (triggerHost || holder).append(trigger); holder.append(drawer);
    const bar = make("div", undefined, "reasoning-drawer-heading"), body = make("div", undefined, "reasoning-drawer-body");
    body.id="reasoning-body-"+runId;body.setAttribute("role","tabpanel");body.tabIndex=0;
    const policyMeta = make("span", "按需读取", "reasoning-policy-meta"), state = make("span", undefined, "reasoning-sync-state");
    state.setAttribute("role", "status");
    let selected = "checkpoints", snapshot = null, signature = "", disposed = false, inflight = null;
    const tabs = tabset([["checkpoints","检查点"],["gates","质量门"],["candidates","关系候选"]], key => { selected = key; body.setAttribute("aria-labelledby",tabs.buttons.get(key).id); draw(); });
    for(const node of tabs.buttons.values())node.setAttribute("aria-controls",body.id);
    const retry = button("刷新", () => refresh(), "reasoning-text-action");
    bar.append(tabs.root, policyMeta, state, retry); drawer.append(bar, body);
    function checkpointRows(rows) {
      if (!rows.length) return empty(body, "本次运行尚未保存研判检查点。历史运行不会补造过程记录。");
      for (const checkpoint of rows) {
        const item = make("details", undefined, "reasoning-record"); item.dataset.checkpointId = checkpoint.checkpointId || "";
        const head = make("summary"); head.append(make("strong", checkpoint.stageName || snapshot.policy?.stages?.find(stage=>stage.key===checkpoint.stageKey)?.name || checkpoint.stageKey || checkpoint.operationKey),
          make("span", "第 " + (checkpoint.iteration ?? 1) + " 轮", "reasoning-meta"), badge(checkpoint.decision || "recorded", checkpoint.decision ? display(checkpoint.decision) : "已记录"),
          make("time", checkpoint.createdAt || "", "reasoning-meta"));
        const facts = make("dl", undefined, "reasoning-facts");
        fact(facts,"已有证据",checkpoint.knownEvidence); fact(facts,"证据缺口",checkpoint.missingEvidence);
        fact(facts,"关系候选",checkpoint.candidateRelations); fact(facts,"不确定性",checkpoint.uncertainties);
        fact(facts,"下一步",checkpoint.nextAction); fact(facts,"停止原因",checkpoint.stopReason);
        item.append(head,facts); raw(item,"检查点与指纹",checkpoint); body.append(item);
      }
    }
    function gateRows(rows) {
      if (!rows.length) return empty(body, "本次运行尚无质量门记录。");
      for (const gate of rows) {
        const item = make("details", undefined, "reasoning-record"); item.dataset.gateId = gate.resultId || gate.gateId || gate.gateKey || "";
        const head = make("summary"); head.append(badge(gate.status),make("strong",gate.name || gate.gateId || gate.gateKey),make("span",gate.stageKey || "", "reasoning-meta"));
        const facts = make("dl", undefined, "reasoning-facts");
        fact(facts,"检查结果",gate.reason); fact(facts,"处理动作",gate.action); fact(facts,"发布约束",gate.affectsPublication === true ? "影响发布资格" : "不影响发布资格");
        fact(facts,"关联引文",gate.evidenceIds); item.append(head,facts); raw(item,"检查数据",gate.details || gate); body.append(item);
      }
    }
    function candidateRows(rows) {
      if (!rows.length) return empty(body, "本次运行尚未登记关系候选。");
      for (const candidate of rows) {
        const item = make("details", undefined, "reasoning-record"); item.dataset.candidateId = candidate.candidateId;
        const head = make("summary"); head.append(badge(candidate.status), make("strong",display(candidate.subject) + " → " + display(candidate.predicate) + " → " + display(candidate.object)));
        const facts = make("dl", undefined, "reasoning-facts");
        fact(facts,"引文支持",candidate.supportStatus); fact(facts,"事实核验",candidate.truthStatus); fact(facts,"证据引用",candidate.evidenceIds);
        fact(facts,"适用条件",candidate.conditions); item.append(head,facts);
        const history = arr(candidate.history);
        if (history.length) { const list = make("ol", undefined, "reasoning-history"); for (const event of history) list.append(make("li",[display(event.action || event.status),event.comment || event.reason,event.createdAt || event.at].filter(Boolean).join(" · "))); item.append(list); }
        const gates = snapshot.gates;
        const review = object(candidate.supportReview), ids = arr(candidate.evidenceIds);
        const reviewedIds = [...new Set(arr(review.evidenceIds))].sort();
        const boundIds = [...new Set(ids)].sort();
        const supportPassed = gates.some(gate => (gate.gateId || gate.gateKey) === "relation-support-reviewed" && gate.checkpointId === candidate.checkpointId && gate.status === "passed");
        const acceptReady = candidate.status === "support-reviewed" && review.verdict === "supported" && Boolean(review.method) && ids.length > 0 && JSON.stringify(reviewedIds) === JSON.stringify(boundIds) && supportPassed;
        const publishReady = candidate.status === "accepted-as-unverified" && gates.length > 0 && !gates.some(gate => gate.blocking && !["passed","skipped","not-applicable"].includes(gate.status));
        const availableActions = candidate.status === "published" ? ["withdraw"]
          : ["withdrawn","rejected"].includes(candidate.status) ? ["request-evidence"]
          : [...(acceptReady ? ["accept"] : []),"reject","request-evidence","mark-conflict","withdraw",...(publishReady ? ["publish"] : [])];
        if (canReview && availableActions.length) {
          const audit = make("details",undefined,"reasoning-review"); audit.append(make("summary","审阅关系候选"));
          const comment = make("textarea"); comment.rows=2; comment.maxLength=2000; comment.setAttribute("aria-label","关系审阅理由"); comment.placeholder="填写可追溯的审阅理由";
          const actions = make("div",undefined,"reasoning-review-actions");
          for (const action of availableActions) {
            const control = button(display(action),async () => {
              if (!comment.value.trim()) { comment.focus(); return; }
              for (const node of actions.querySelectorAll("button")) node.disabled = true;
              try {
                await api(prefix + "/relation-candidates/" + encodeURIComponent(candidate.candidateId) + "/review", {method:"POST",body:JSON.stringify({action,comment:comment.value.trim(),idempotencyKey:crypto.randomUUID()})});
                notice("关系审阅已保存"); await refresh();
              } catch(error) { notice(error.message,true); }
              finally { for (const node of actions.querySelectorAll("button")) node.disabled=false; }
            },"reasoning-text-action"); control.dataset.reviewAction=action;actions.append(control);
          }
          audit.append(comment,actions); item.append(audit);
        }
        raw(item,"候选与审计指纹",candidate); body.append(item);
      }
    }
    function draw() {
      if (drawer.hidden || !snapshot) return;
      const opened = new Set([...body.querySelectorAll("details[open]")].map(node=>node.dataset.checkpointId || node.dataset.gateId || node.dataset.candidateId).filter(Boolean));
      body.replaceChildren();
      if (snapshot.errors.length) empty(body,"部分记录读取失败：" + snapshot.errors.join("；") + "。点击刷新重试。");
      if (selected === "checkpoints") checkpointRows(snapshot.checkpoints);
      if (selected === "gates") gateRows(snapshot.gates);
      if (selected === "candidates") candidateRows(snapshot.candidates);
      for (const node of body.querySelectorAll("details")) if (opened.has(node.dataset.checkpointId || node.dataset.gateId || node.dataset.candidateId)) node.open=true;
    }
    async function refresh() {
      if (disposed || drawer.hidden) return;
      if (inflight) return inflight;
      inflight = (async () => {
        const requests = ["reasoning-policy","reasoning-checkpoints","quality-gates","relation-candidates"];
        const results = await Promise.allSettled(requests.map(path=>api(prefix + "/" + path)));
        if (disposed) return;
        const values = results.map(result=>result.status === "fulfilled" ? result.value : null);
        const errors = results.flatMap((result,index)=>result.status === "rejected" ? [requests[index] + "：" + result.reason.message] : []);
        const next = {policy:values[0],checkpoints:arr(values[1]?.items || values[1]),gates:arr(values[2]?.items || values[2]),candidates:arr(values[3]?.items || values[3]),errors};
        const failed = next.gates.filter(gate=>gate.status === "failed").length;
        trigger.textContent = "研判过程" + (failed ? " · " + failed + " 条未通过记录" : next.checkpoints.length ? " · " + next.checkpoints.length : "");
        trigger.classList.toggle("has-failures",failed > 0);
        policyMeta.textContent = next.policy?.version ? "策略 v" + next.policy.version : next.policy?.status === "unavailable" ? "历史运行 · 未冻结研判契约" : "研判契约未读取";
        policyMeta.title = next.policy?.policyHash || "";
        state.textContent = errors.length ? "读取未完成" : "";
        const nextSignature = JSON.stringify(next); snapshot=next;
        if (signature !== nextSignature) { signature=nextSignature; draw(); }
      })().finally(()=>{inflight=null;});
      return inflight;
    }
    tabs.select("checkpoints"); refresh();
    return {refresh,dispose:()=>{disposed=true;drawer.remove();trigger.remove();}};
  }

  async function mountProfile(holder,{agentKey,api,notice=()=>{},canEdit=false}) {
    const root = make("section",undefined,"profile-card reasoning-profile"); holder.append(root);
    const heading = make("h3",undefined,"reasoning-profile-heading"), icon=make("span",undefined,"future-war-stage-icon imagine-icon"); icon.setAttribute("aria-hidden","true");
    heading.append(icon,make("span","研判契约"));
    if (window.AgentAdminTips) heading.append(window.AgentAdminTips.create("策略按版本保存，只用于新运行；每次运行冻结自己的阶段、资源上限和质量门。数值上限也受 Agent 总预算约束。","info","研判契约"));
    const content=make("div"),message=make("p",undefined,"reasoning-save-state"); message.setAttribute("role","status"); root.append(heading,content,message);
    const path="/reasoning-policies/" + encodeURIComponent(agentKey);
    try {
      let policy=await api(path); policy=object(policy.policy || policy);
      if (!arr(policy.stages).length) { empty(content,"此 Agent 尚未登记研判契约。");return; }
      let draft=structuredClone(policy);
      const render = () => {
        content.replaceChildren();
        let editorDirty=false;
        const meta=make("div",undefined,"reasoning-policy-overview"); meta.append(make("span","版本 " + (policy.version ?? "—")),make("span",display(policy.mode)),make("span",arr(policy.stages).length + " 个阶段")); content.append(meta);
        const conditions=make("div",undefined,"reasoning-policy-requirements");
        for (const [key,label] of [["requireEvidenceBinding","引文绑定"],["requireCounterArgument","反方假设"],["requireUnknowns","未知项"],["requireIndependentSources","来源独立性"]]) {
          const labelNode=make("label",undefined,"reasoning-policy-check"),input=make("input"); input.type="checkbox";input.checked=draft[key] === true;input.disabled=!canEdit;
          input.onchange=()=>{draft[key]=input.checked;editorDirty=false;};labelNode.append(input,make("span",label));conditions.append(labelNode);
        }
        content.append(conditions);
        const budgets=make("div",undefined,"reasoning-global-budgets");
        for(const [key,label] of [["reasoning_rounds","研判轮次"],["gate_checks","质量门检查"],["relation_reviews","关系审阅"]]){
          const wrapper=make("label",label,"reasoning-global-budget"),input=make("input");input.type="number";input.min="0";input.step="1";input.value=String(draft.budgetLimits?.[key] ?? 0);input.disabled=!canEdit;
          input.setAttribute("aria-label",label);input.oninput=()=>{draft.budgetLimits=object(draft.budgetLimits);draft.budgetLimits[key]=input.value===""?null:Number(input.value);editorDirty=false;};wrapper.append(input);budgets.append(wrapper);
        }
        content.append(budgets);
        const scroll=make("div",undefined,"reasoning-policy-table-wrap"),table=make("table",undefined,"reasoning-policy-table");
        const quotaKeys=["model_calls","tool_calls","http_calls","search_calls","scrape_calls","input_tokens","output_tokens"];
        const stageKeys=[...new Set([...quotaKeys,...arr(draft.stages).flatMap(stage=>Object.keys(stage.budget || {}))])];
        const quotaNames={model_calls:"模型",tool_calls:"工具",http_calls:"HTTP",search_calls:"检索",scrape_calls:"抓取",input_tokens:"输入 Token",output_tokens:"输出 Token"};
        const head=make("tr"); for (const title of ["研判阶段",...stageKeys.map(key=>quotaNames[key] || key),"质量门"]) head.append(make("th",title)); table.append(head);
        for (const stage of draft.stages) {
          const row=make("tr");row.append(make("th",stage.name || stage.key));
          for (const key of stageKeys) {
            const cell=make("td"),input=make("input"); input.type="number";input.min="0";input.step="1";input.value=stage.budget?.[key] == null ? "" : String(stage.budget[key]);input.placeholder="继承";input.title="留空继承 Harness / 全局上限；0 表示不允许调用";input.disabled=!canEdit;
            input.setAttribute("aria-label",(stage.name || stage.key) + " " + key); input.oninput=()=>{stage.budget=object(stage.budget);if(input.value==="")delete stage.budget[key];else stage.budget[key]=Number(input.value);editorDirty=false;};cell.append(input);row.append(cell);
          }
          const gates=arr(stage.qualityGates).length ? arr(stage.qualityGates) : arr(draft.qualityGates).filter(gate=>arr(gate.stages).includes(stage.stepKey || stage.key));const cell=make("td",gates.map(gate=>typeof gate==="string"?gate:gate.gateId).join("、") || "—");cell.title=cell.textContent;row.append(cell);table.append(row);
        }
        scroll.append(table);content.append(scroll);
        const advanced=make("details",undefined,"reasoning-policy-advanced"); advanced.append(make("summary","阶段、质量门与收敛规则"));
        const limits=make("dl",undefined,"reasoning-facts");for(const [key,value] of Object.entries(draft.budgetLimits || {})) fact(limits,key,value);advanced.append(limits);
        for (const gate of arr(draft.qualityGates)) {const row=make("p",undefined,"reasoning-gate-policy");row.append(make("strong",typeof gate==="string"?gate:gate.gateId),make("span",typeof gate==="string"?"":display(gate.severity) + " · " + display(gate.onFail)));advanced.append(row);}
        const editor=make("textarea",undefined,"reasoning-policy-json");editor.rows=14;editor.spellcheck=false;editor.setAttribute("aria-label","完整研判策略 JSON");editor.value=JSON.stringify(draft,null,2);editor.readOnly=!canEdit;editor.oninput=()=>{editorDirty=true;};
        const sync=button("将上方配置同步到 JSON",()=>{editor.value=JSON.stringify(draft,null,2);editorDirty=false;},"reasoning-text-action");
        if(canEdit) advanced.append(sync);advanced.append(editor);content.append(advanced);
        const actions=make("div",undefined,"reasoning-policy-actions");
        if(canEdit){
          const save=button("保存新版本",async()=>{
            save.disabled=true;message.textContent="保存中";
            try {
              if (editorDirty) draft=JSON.parse(editor.value);
              for (const [key,value] of Object.entries(draft.budgetLimits || {})) if (!Number.isInteger(value) || value<0) throw Error("全局上限 " + key + " 必须为非负整数");
              for (const stage of arr(draft.stages)) for (const [key,value] of Object.entries(stage.budget || {})) if (!Number.isInteger(value) || value<0) throw Error("阶段 " + (stage.name || stage.key) + " 的 " + key + " 必须为非负整数");
              const updated=await api(path,{method:"PUT",body:JSON.stringify({policy:draft,expectedVersion:policy.version})});
              policy=object(updated.policy || updated);draft=structuredClone(policy);render();message.textContent="已保存 v" + policy.version + "，新运行生效";notice(message.textContent);
            } catch(error){message.textContent=error.message;notice(error.message,true);}finally{save.disabled=false;}
          });actions.append(save,button("重置修改",()=>{draft=structuredClone(policy);render();message.textContent="";},"reasoning-text-action"));
        }
        content.append(actions);
      };render();
    } catch(error) {empty(content,"研判契约未能读取：" + error.message);content.append(button("重试",()=>{root.remove();mountProfile(holder,{agentKey,api,notice,canEdit});},"reasoning-text-action"));}
  }

  function mountReport(root,{report,sourceBlock,runId,api}) {
    const graphRoot=root.querySelector(".report-analysis-graph"),heading=root.querySelector(".report-graph-heading");
    if(!graphRoot || !heading)return;
    const originalChildren=[...root.children],graphItems=[...graphRoot.children].filter(node=>node!==heading),headingItems=[...heading.children];
    const graphPanel=make("div",undefined,"reasoning-report-graph-panel");graphPanel.append(...graphItems);graphRoot.append(graphPanel);
    const evidence=arr(report.evidence),findings=arr(report.findings),assertions=arr(report.ontology?.assertions),perspectives=new Map(arr(report.perspectives).map(row=>[row.key,row.name]));
    const ledger=make("section",undefined,"reasoning-report-ledger"),uncertainty=make("section",undefined,"reasoning-report-uncertainty");ledger.hidden=true;uncertainty.hidden=true;
    ledger.setAttribute("aria-label","证据台账");uncertainty.setAttribute("aria-label","不确定性与反证");graphRoot.append(ledger,uncertainty);
    let loaded=false;
    const tabs=tabset([["graph","本体图谱"],["ledger","证据台账"],["uncertainty","不确定性与反证"]],key=>{
      const isGraph=key==="graph";
      graphPanel.hidden=!isGraph;
      for(const node of originalChildren)if(node!==graphRoot)node.hidden=!isGraph;
      for(const node of headingItems)if(!node.matches("h3,.report-meta"))node.hidden=!isGraph;
      ledger.hidden=key!=="ledger";uncertainty.hidden=key!=="uncertainty";
      if(key==="uncertainty"&&!loaded&&api&&runId){loaded=true;loadRecordedUncertainty();}
    },"reasoning-tabs reasoning-report-tabs");
    for(const [key,panel]of[["graph",graphPanel],["ledger",ledger],["uncertainty",uncertainty]]){
      panel.id=tabs.buttons.get(key).id+"-panel";panel.setAttribute("role","tabpanel");panel.setAttribute("aria-labelledby",tabs.buttons.get(key).id);panel.tabIndex=0;tabs.buttons.get(key).setAttribute("aria-controls",panel.id);
    }
    const oldTitle=heading.querySelector("h3"),tip=oldTitle?.querySelector(".mini-tip"),nav=make("div",undefined,"reasoning-report-nav");nav.append(tabs.root);if(tip)nav.append(tip);
    oldTitle?.replaceWith(nav);if(!oldTitle)heading.prepend(nav);
    const title=make("div",undefined,"reasoning-ledger-heading"),search=make("input");search.type="search";search.placeholder="按引文、来源或关系筛选";search.setAttribute("aria-label","筛选证据台账");
    const count=make("span",evidence.length + " 条引文","reasoning-meta"),list=make("div");title.append(search,count);ledger.append(title,list);
    const renderEvidence=()=>{
      list.replaceChildren();let visible=0;const term=search.value.trim().toLowerCase();
      for(const ref of evidence){
        const id=String(ref.evidenceId || ref.claimId),related=assertions.filter(row=>arr(row.evidenceIds).includes(id));
        if(term&&!JSON.stringify([ref,related]).toLowerCase().includes(term))continue;visible++;
        const detail=make("details",undefined,"reasoning-ledger-row"),head=make("summary");
        let host=ref.sourceUri || "来源未记录";try{host=new URL(ref.sourceUri).host;}catch{}
        head.append(make("strong",id),make("span",host,"reasoning-ledger-source"),make("span",related.length + " 条关系","reasoning-meta"));detail.append(head);
        sourceBlock([id],detail);if(related.length){const facts=make("dl",undefined,"reasoning-facts");fact(facts,"支撑关系",related.map(row=>row.id));fact(facts,"研判视角",[...new Set(related.map(row=>perspectives.get(row.perspectiveKey)||row.perspectiveKey))]);detail.append(facts);}list.append(detail);
      }
      count.textContent=visible + " / " + evidence.length + " 条引文";if(!visible)empty(list,term?"未找到匹配引文。":"本次报告尚未保存可定位引文。");
    };search.oninput=renderEvidence;renderEvidence();
    let unknownCount=0;
    for(const finding of findings){
      const entries=[["适用条件",finding.conditions],["反方假设",finding.counterArguments],["未知项",finding.unknowns]].filter(([,values])=>arr(values).length);
      if(!entries.length)continue;unknownCount++;
      const detail=make("details",undefined,"reasoning-record"),head=make("summary");head.append(make("strong",perspectives.get(finding.perspectiveKey)||finding.perspectiveKey),make("span",entries.map(([label])=>label).join(" · "),"reasoning-meta"));detail.append(head);
      const facts=make("dl",undefined,"reasoning-facts");for(const[label,values]of entries)fact(facts,label,values);detail.append(facts);uncertainty.append(detail);
    }
    for(const gap of arr(report.evidenceGaps)){unknownCount++;const detail=make("details",undefined,"reasoning-record");detail.append(make("summary",(gap.perspectiveName||perspectives.get(gap.perspectiveKey)||gap.perspectiveKey||"证据缺口") + " · " + display(gap.summary)));
      const facts=make("dl",undefined,"reasoning-facts");fact(facts,"需要补充",gap.neededEvidence);fact(facts,"下一步",gap.nextAction);fact(facts,"已尝试",gap.attemptedQueries);detail.append(facts);uncertainty.append(detail);}
    const redTeam=report.redTeam || report.redTeamReview;
    if(redTeam){unknownCount++;raw(uncertainty,"红队审查记录",redTeam);}
    if(!unknownCount)empty(uncertainty,"报告未登记反方假设或未知项；这不代表不存在不确定性。");
    const recorded=make("div",undefined,"reasoning-report-recorded");uncertainty.append(recorded);
    async function loadRecordedUncertainty(){
      const results=await Promise.allSettled([api("/runs/"+encodeURIComponent(runId)+"/reasoning-checkpoints"),api("/runs/"+encodeURIComponent(runId)+"/quality-gates")]);
      if(!root.isConnected)return;
      recorded.replaceChildren();
      const checkpoints=results[0].status==="fulfilled"?arr(results[0].value):[],gates=results[1].status==="fulfilled"?arr(results[1].value):[];
      const failed=gates.filter(gate=>gate.status==="failed");
      if(failed.length){recorded.append(make("h4","未通过的质量门记录"));for(const gate of failed){const item=make("details",undefined,"reasoning-record");item.append(make("summary",display(gate.gateId || gate.gateKey)+" · "+display(gate.reason)));const facts=make("dl",undefined,"reasoning-facts");fact(facts,"处理动作",gate.action);fact(facts,"证据引用",gate.evidenceIds);item.append(facts);recorded.append(item);}}
      const uncertainties=checkpoints.flatMap(row=>arr(row.uncertainties).map(value=>({stage:row.stageKey,value:display(value)})));
      if(uncertainties.length){recorded.append(make("h4","检查点记录的未知项"));for(const item of uncertainties)recorded.append(make("p",item.stage+"："+item.value));}
      if(results.some(result=>result.status==="rejected")){loaded=false;empty(recorded,"部分研判过程未能读取，切换到此视图可重试。");}
    }
    tabs.select("graph");
  }
  window.AgentAdminReasoning={mountRun,mountProfile,mountReport};
})();
