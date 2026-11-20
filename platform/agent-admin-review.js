"use strict";
/* Shared projections and version-bound human opinions keep execution and release distinct. */
(() => {
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const count = value => Number.isFinite(value) ? String(value) : "未记录";
  const label = (value, fallback) => value?.label || fallback;
  const time = value => {
    if (!value) return "未记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {hour12:false});
  };
  const row = (root, name, value) => {
    const line = make("div", undefined, "review-summary-detail-row");
    line.append(make("span", name), make("span", value ?? "未记录")); root.append(line);
  };
  const status = (root, value, fallback) => root.append(make("span", label(value, fallback), "review-summary-state"));

  function goalDetails(root, view) {
    if (!view?.goal) { row(root, "目标验收", view?.label || "目标未记录"); return; }
    const goal = view.goal, assessment = view.assessment;
    const box = make("details", undefined, "goal-assessment-details");
    box.append(make("summary", (view.label || "目标待验收") + " · 查看目标与验收"));
    const body = make("div", undefined, "review-summary-detail-body");
    row(body, "职责目标", goal.roleObjective); row(body, "本次问题", goal.objective);
    if (goal.localGoal) row(body, "受托局部目标", goal.localGoal);
    if (goal.parentRunId) {
      const line = make("div", undefined, "review-summary-detail-row"), link = make("a", "查看父目标所属运行");
      link.href = "/agent-admin.html#run=" + encodeURIComponent(goal.parentRunId);
      line.append(make("span", "贡献关系"), link); body.append(line);
    }
    row(body, "契约版本", "v" + goal.contractRevision + " / " + goal.contractHash.slice(0,12));
    row(body, "基础交付验收", {met:"通过",unmet:"尚未通过","not-evaluable":"暂不可评估"}[assessment?.deliveryStatus] || "待验收");
    const labels = {pass:"满足",fail:"未满足",unknown:"未评估"};
    for (const check of assessment?.checks || []) {
      row(body, check.label, labels[check.status] || "未评估");
      if (check.reason) row(body, "依据", check.reason);
      if (check.proof?.artifactId) row(body, "产物依据", check.proof.artifactId + " / " + check.proof.contentHash.slice(0,12));
      if (Array.isArray(check.proof) && check.proof.length) {
        const proof = make("details"), list = make("div", undefined, "review-summary-detail-body"); proof.append(make("summary", "查看依据（" + check.proof.length + "）"));
        for (const item of check.proof) row(list, item.evidenceId || item.runId, item.reason || [labels[item.status] || item.status, item.artifactId, item.contentHash?.slice(0,12)].filter(Boolean).join(" / "));
        proof.append(list); body.append(proof);
      }
      if (check.gapCount) row(body, "已声明缺口", check.gapCount + " 项");
    }
    const childDetails = make("details"), childList = make("div",undefined,"review-summary-detail-body");
    childDetails.append(make("summary","子目标（" + (view.children || []).length + "）"));
    for (const child of view.children || []) {
      const line = make("div", undefined, "review-summary-detail-row"), link = make("a", child.localGoal || child.agentKey);
      link.href = "/agent-admin.html#run=" + encodeURIComponent(child.runId);
      line.append(link, make("span", child.assessment?.label || "待验收")); childList.append(line);
    }
    if (view.children?.length) {childDetails.append(childList);body.append(childDetails);}
    if (assessment?.limitations?.length) row(body, "验收范围", assessment.limitations.join(" "));
    box.append(body); root.append(box);
  }

  function mountGoalContract(root, value, {api, canEdit, notice, onPublished, profileScope = {}, profileDescription, formatValue, tip} = {}) {
    const doc = value?.document;
    if (value?.contractId) root.dataset.goalContract = value.contractId;
    const display = input => formatValue ? formatValue(input) : input ?? "未登记";
    const list = (parent, items) => {
      const values = [...new Set((Array.isArray(items) ? items : []).filter(Boolean))];
      if (!values.length) { parent.append(make("p", "未登记", "muted")); return; }
      const ul = make("ul"); values.forEach(item => ul.append(make("li", item))); parent.append(ul);
    };
    const group = (title, description) => {
      const details = make("details", undefined, "agent-definition-group"), summary = make("summary", title);
      const icon = make("span", undefined, "future-war-stage-icon " + ({"输入与依赖":"data-model-icon","权限与限制":"scope-icon","执行准则":"loop-icon"}[title] || "info-icon"));
      icon.setAttribute("aria-hidden", "true"); summary.prepend(icon);
      if (tip && description) summary.append(tip(description, "info", title));
      const body = make("div", undefined, "goal-contract-body"); details.append(summary, body); root.append(details); return body;
    };
    const facts = make("dl", undefined, "agent-definition-facts");
    const fact = (name, text, description) => {
      const dt = make("dt", name);
      if (tip && description) dt.append(tip(description, "info", name));
      facts.append(dt, make("dd", text));
    };
    fact("专业范围", profileDescription || doc?.scope || "尚未登记职责范围");
    fact("长期目标", doc?.objective || "尚未登记职责目标", "Agent 持续承担的目标；本次要研究的具体问题在新建任务时填写。" + (doc?.scope ? "\n目标适用范围：" + doc.scope : ""));
    root.append(facts);
    if (doc) root.querySelector("h3")?.append(make("span", "目标 v" + value.revision, "agent-definition-version"));

    const inputs = group("输入与依赖", "输入类型来自 Profile；依赖条件来自目标约定。输出与交付要求统一见「交付与验收」。");
    row(inputs, "输入类型", display(profileScope.input_entities_json));
    inputs.append(make("h4", "依赖条件")); list(inputs, doc?.dependencies);

    const limits = group("权限与限制", "系统权限与目标约定分别保存。目标约束不会授予工具、写入或发布权限；运行时仍按实际授权检查。");
    row(limits, "允许写入", display(profileScope.allowed_write_json));
    row(limits, "系统禁止行为", display(profileScope.forbidden_actions_json));
    limits.append(make("h4", "目标约束")); list(limits, doc?.exclusions);

    const rules = group("执行准则", "说明处理证据、形成判断和补充研究时应如何行动；展开单条规则可查看触发条件、失败处理和依据要求。");
    if (!doc) rules.append(make("p", "尚未登记执行准则", "muted"));
    for (const rule of doc?.behaviorRules || []) {
      const item = make("details"); item.append(make("summary", ({hard:"强制约束",quality:"质量要求",strategy:"研究策略"}[rule.category] || rule.category || "规则") + " · " + rule.check));
      const text = make("div", undefined, "review-summary-detail-body");
      const failureLabels = {"deny-dispatch":"拒绝调用","reject-write":"拒绝写入","deny-publication":"拒绝发布","retain-gap-or-reject-claim":"保留缺口或拒绝断言","replan-within-budget":"在预算内重新规划"};
      row(text,"触发条件",rule.trigger); row(text,"失败处理",failureLabels[rule.failureAction] || rule.failureAction); row(text,"依据要求",rule.proofRequired); item.append(text); rules.append(item);
    }
    if (doc) { rules.append(make("h4", "停止条件")); list(rules, doc.stopConditions); }
    if (!doc || !canEdit) return;
    const edit = make("details", undefined, "goal-contract-editor"); edit.append(make("summary","编辑目标约定"));
    const form = make("form"), fields = {};
    for (const [key,title] of [["objective","长期目标"],["scope","目标适用范围"],["deliverables","交付要求（每行一项）"],["dependencies","依赖条件（每行一项）"],["exclusions","目标约束（每行一项）"],["reason","变更理由"]]) {
      const label = make("label",title), input = make("textarea"); input.rows = key==="scope"?3:2; input.required = true; input.maxLength=key==="reason"?2000:15000;
      input.value = Array.isArray(doc[key]) ? doc[key].join("\n") : doc[key] || ""; label.append(input); fields[key]=input; form.append(label);
    }
    form.append(make("p","新版本只用于后续运行；本次发布记录管理员身份与变更理由。","muted"));
    const save = make("button","发布目标新版本"); save.type="submit"; const result=make("p"); result.setAttribute("role","status"); form.append(save,result);
    let key = null, fingerprint = null;
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled=true;
      const document = structuredClone(doc);
      for (const name of ["objective","scope"]) document[name]=fields[name].value.trim();
      for (const name of ["deliverables","dependencies","exclusions"]) document[name]=fields[name].value.split("\n").map(s=>s.trim()).filter(Boolean);
      const body={document,expectedHash:value.contentHash,reason:fields.reason.value.trim()};
      if(fingerprint!==JSON.stringify(body)){fingerprint=JSON.stringify(body);key=crypto.randomUUID();}
      try { await api("/agents/"+encodeURIComponent(value.agentKey)+"/goal-contract",{method:"POST",body:JSON.stringify({...body,idempotencyKey:key})}); notice?.("目标新版本已发布，后续运行将使用此版本"); await onPublished?.(); }
      catch(error){result.textContent=error.message;} finally {save.disabled=false;}
    };
    edit.append(form); root.append(edit);
  }

  function mountHeader(root, summary, {error, queueStatus, children = [], tip} = {}) {
    const signature = JSON.stringify([summary, error || null, queueStatus, children]);
    if (root.dataset.reviewSignature === signature) return;
    const wasOpen = root.querySelector(".review-summary-details")?.open;
    const goalWasOpen = root.querySelector(".goal-assessment-details")?.open;
    const focusedDetails = root.querySelector(".review-summary-details > summary") === document.activeElement;
    root.replaceChildren(); root.dataset.reviewSignature = signature;
    root.classList.add("review-summary-host");
    if (!summary) {
      root.append(make("span", error ? "结果摘要读取失败；证据、审阅和共享调用量尚未取得" : "正在读取结果摘要", "review-summary-unavailable"));
      if (error && tip) root.append(tip(error, "info", "摘要读取失败"));
      return;
    }
    const body = make("div", undefined, "review-summary-body");
    const states = make("div", undefined, "review-summary-line review-summary-states");
    status(states, summary.goalAssessment, "目标未记录");
    status(states, summary.factVerification, "事实核验未记录");
    status(states, summary.humanReview, "人工审阅未记录");
    status(states, summary.publication?.report, "报告发布未记录");
    status(states, summary.evidence?.independence, "来源独立性未记录");
    status(states, summary.evidence?.citationReview, "引文支持复核未记录");
    if (tip) states.append(tip("研究执行完成、引文支持复核、事实核验、人工意见与发布分别记录。", "info", "状态含义"));
    const totals = summary.totalCounts;
    const counts = make("div", undefined, "review-summary-line review-summary-counts");
    counts.append(make("span", (summary.scope?.label || "整个任务") + "：判断 " + count(totals?.assertions) + " · 证据缺口 " + count(totals?.gaps) + (summary.qualityIssues?.length ? " · 质量检查 " + summary.qualityIssues.length + " 类待办" : "") + " · 摘录 " + count(totals?.evidenceExcerpts) + " · 来源组 " + count(totals?.sourceGroups)));
    if (summary.qualityIssues?.length && tip) counts.append(tip(summary.qualityIssues.map(i => i.label).join("\n"), "info", "未解决的质量检查"));
    const shared = summary.usage?.shared;
    counts.append(make("span", "共享调用：模型 " + count(shared?.modelCalls) + " · 工具 " + count(shared?.toolCalls), "review-summary-usage"));
    if (tip) counts.append(tip(summary.usage?.countingPolicy || "共享账本已包含子任务；不能重复相加。", "info", "共享调用口径"));
    body.append(states, counts);
    const details = make("details", undefined, "review-summary-details"); details.open = Boolean(wasOpen);
    const disclosure = make("summary", "详情"); details.append(disclosure);
    const detailBody = make("div", undefined, "review-summary-detail-body");
    goalDetails(detailBody, summary.goalAssessment);
    const goalDisclosure = detailBody.querySelector(".goal-assessment-details");
    if (goalDisclosure) goalDisclosure.open = Boolean(goalWasOpen);
    row(detailBody, "任务目标", summary.identity?.taskGoal);
    row(detailBody, "Agent / 版本", [summary.identity?.agentName || summary.identity?.agentKey, summary.identity?.agentVersion].filter(Boolean).join(" / "));
    row(detailBody, "报告保存时间", time(summary.identity?.reportTime));
    row(detailBody, "当前阶段", summary.execution?.currentStepLabel);
    row(detailBody, "子任务", summary.execution?.childrenLabel);
    row(detailBody, "关系发布", label(summary.publication?.relations, "未记录"));
    row(detailBody, "Profile 发布", label(summary.publication?.profile, "未记录"));
    row(detailBody, "根任务直接调用", "模型 " + count(summary.usage?.direct?.modelCalls) + " · 工具 " + count(summary.usage?.direct?.toolCalls));
    row(detailBody, "共享调用上限", "模型 " + count(shared?.modelLimit) + " · 工具 " + count(shared?.toolLimit));
    row(detailBody, "结构关系 / 视角", count(totals?.structuralEdges) + " / " + count(totals?.perspectives));
    row(detailBody, "Run", summary.identity?.runId);
    row(detailBody, "审阅版本指纹", summary.identity?.reportHash);
    row(detailBody, "源图哈希", summary.identity?.sourceGraphHash);
    if (!summary.humanReview?.writeAvailable) row(detailBody, "审阅记录", summary.humanReview?.unavailableReason || "当前不可写入审阅记录");
    if (queueStatus) row(detailBody, "队列", queueStatus);
    for (const child of children) {
      const line = make("div", undefined, "review-summary-detail-row");
      const link = make("a", child.targetAgentKey || child.childRunId);
      link.href = "/agent-admin.html#run=" + encodeURIComponent(child.childRunId);
      line.append(make("span", "协作任务"), link); detailBody.append(line);
    }
    details.append(detailBody); root.append(body, details);
    if (focusedDetails) disclosure.focus({preventScroll:true});
  }

  function mountResult(root, summary, {error, tip} = {}) {
    let holder = root.querySelector(":scope > .report-review-result");
    if (!holder) { holder = make("section", undefined, "report-review-result"); root.prepend(holder); }
    const signature = JSON.stringify([summary?.identity, summary?.totalCounts, summary?.items, summary?.limitations, error || null]);
    if (holder.dataset.signature === signature) return;
    const wasOpen = holder.querySelector("details")?.open;
    holder.dataset.signature = signature; holder.replaceChildren();
    holder.setAttribute("aria-label", "本次判断与缺口");
    if (!summary) {
      holder.append(make("p", error ? "统一结果摘要暂不可用；下方为已保存报告。" : "结果摘要正在读取。", "review-summary-unavailable"));
      return;
    }
    const items = (Array.isArray(summary.items) ? [...summary.items] : []).sort((a,b)=>Number(b.kind==="gap"||b.priority==="attention")-Number(a.kind==="gap"||a.priority==="attention"));
    const assertions = items.filter(item => item.kind === "assertion" || item.kind === "judgment");
    const gaps = items.filter(item => item.kind === "gap");
    const details = make("details", undefined, "review-result-items"); details.open = Boolean(wasOpen);
    const summaryLine = make("summary", undefined, "review-result-summary");
    const hasAssertions = Number.isFinite(summary.totalCounts?.assertions) && summary.totalCounts.assertions > 0;
    summaryLine.append(make("strong", hasAssertions ? "本体判断 " + summary.totalCounts.assertions + " 条" : summary.totalCounts?.assertions === 0 ? "尚未形成本体判断" : "判断数量未记录"));
    const lead = assertions[0] || gaps[0] || items[0];
    summaryLine.append(make("span", lead?.text || lead?.title || (summary.execution?.reportAvailable ? "展开查看当前报告的已记录条目。" : "报告尚未交付。"), "review-result-lead"));
    details.append(summaryLine);
    const list = make("div", undefined, "review-result-list");
    for (const item of items) {
      const article = make("article", undefined, "review-result-item");
      article.dataset.itemId = item.id || "";
      const heading = make("div", undefined, "review-result-item-heading");
      heading.append(make("span", item.kind === "gap" ? "缺口" : "判断", "muted"), make("strong", item.title || item.perspectiveKey || "已保存条目"));
      const locate=make("button",item.kind==="gap"?"查看缺口":"定位判断","review-result-locate");locate.type="button";
      locate.onclick=()=>article.dispatchEvent(new CustomEvent("report-item-select",{bubbles:true,detail:{item}}));
      heading.append(locate);
      article.append(heading);
      if (item.text) article.append(make("p", item.text));
      if (item.reason && item.reason !== item.text) article.append(make("p", "原因：" + item.reason, "muted"));
      if (Array.isArray(item.conditions) && item.conditions.length) article.append(make("p", "适用条件：" + item.conditions.join("；"), "muted"));
      if (item.perspectiveKey) article.append(make("span", "视角：" + item.perspectiveKey, "muted"));
      const refs = Array.isArray(item.evidenceIds) ? item.evidenceIds : [];
      if (refs.length) {
        const citations=make("div",undefined,"review-result-evidence");citations.append(make("span","引用："));
        for(const evidenceId of refs){const button=make("button",evidenceId);button.type="button";button.onclick=()=>article.dispatchEvent(new CustomEvent("report-item-select",{bubbles:true,detail:{item,focusEvidenceId:evidenceId}}));citations.append(button);}
        article.append(citations);
      }
      list.append(article);
    }
    if (!items.length) list.append(make("p", "本次报告尚无可列出的判断或缺口条目。", "muted"));
    details.append(list); holder.append(details);
    const limitations = [...new Set(summary.limitations || [])];
    if (limitations.length) {
      const line = make("p", limitations.join("；"), "review-result-limitations");
      if (tip) line.append(tip("这些是当前报告已记录的限制，不构成综合可信度评分。", "info", "报告限制"));
      holder.append(line);
    }
  }

  function mountGapDetail(root,detail){
    root.replaceChildren();
    if(!detail){root.append(make("p","未保存可读取的缺口详情。","review-summary-unavailable"));return;}
    const text=value=>value===null||value===undefined||value===""?"未记录":typeof value==="object"?"结构化记录见原始详情":String(value);
    const reasons={"no-relevant-evidence":"未取得直接相关引文"};
    const reasonText=value=>Object.hasOwn(reasons,value)?reasons[value]:(typeof value==="string"&&/^[a-z][a-z0-9_:-]*$/i.test(value)?"原因代码见原始记录":text(value));
    const statuses={found:"已返回资料",completed:"执行完成",succeeded:"执行完成",failed:"执行失败",error:"执行失败","no-results":"未返回资料",empty:"未返回资料",skipped:"未执行",blocked:"执行受阻",running:"执行中"};
    const statusText=value=>value===null||value===undefined||value===""?"未记录":Object.hasOwn(statuses,value)?statuses[value]:"已记录状态，见原始记录";
    if(detail.reason)row(root,"缺口原因",reasonText(detail.reason));
    row(root,"排除理由",reasonText(detail.exclusionReason));row(root,"排除规则",text(detail.exclusionRule));
    const rawDetail=make("details",undefined,"report-gap-raw");rawDetail.append(make("summary","原始缺口记录"),make("pre",JSON.stringify(detail,null,2)));root.append(rawDetail);
    const attempts=Array.isArray(detail.attempts)?detail.attempts:[];
    if(Array.isArray(detail.attemptedQueries)&&detail.attemptedQueries.length)row(root,"已存查询",detail.attemptedQueries.map(text).join("；"));
    if(Array.isArray(detail.attemptedUrls)&&detail.attemptedUrls.length)row(root,"已存来源",detail.attemptedUrls.map(text).join("；"));
    root.append(make("h4","已保存检索与采集尝试"));
    if(!attempts.length)root.append(make("p","未记录检索/采集尝试，不能据此认定未执行研究。","muted"));
    for(const savedAttempt of attempts){
      const attempt=savedAttempt&&typeof savedAttempt==="object"?savedAttempt:{query:typeof savedAttempt==="string"?savedAttempt:null};
      const block=make("article",undefined,"report-gap-attempt");
      const result=attempt.result&&typeof attempt.result==="object"&&!Array.isArray(attempt.result)?attempt.result:{};
      row(block,"查询 / 来源",text(attempt.query||result.query||attempt.sourceUri||attempt.url));
      row(block,"执行状态",statusText(result.status||attempt.status));
      if(attempt.reason||result.reason)row(block,"原因",reasonText(attempt.reason||result.reason));
      const results=Array.isArray(result.results)?result.results:Array.isArray(attempt.results)?attempt.results:[];
      if(results.length){
        row(block,"返回资料",results.length+" 项（已保存）");
        const list=make("ul",undefined,"report-gap-search-results");
        for(const entry of results){
          if(!entry||typeof entry!=="object")continue;
          const item=make("li");item.append(make("strong",entry.title||"标题未记录"));
          const uri=entry.sourceUri||entry.url;
          if(typeof uri==="string"&&/^https?:\/\//.test(uri)){const link=make("a",uri);link.href=uri;link.target="_blank";link.rel="noopener noreferrer";item.append(link);}
          const snippet=typeof entry.snippet==="string"?entry.snippet:"";
          const content=[entry.markdown,entry.content,entry.text].find(value=>typeof value==="string"&&value)||snippet;
          const preview=(snippet||content).replace(/\s+/g," ").trim();
          if(preview)item.append(make("p",preview.slice(0,240)+(preview.length>240?"…":"")));
          if(content&&(preview.length>240||content!==snippet||content.includes("\n"))){const full=make("details");full.append(make("summary","已保存摘录全文"),make("pre",content));item.append(full);}
          list.append(item);
        }
        block.append(list);
      }else if(typeof attempt.result==="string")row(block,"已保存结果",reasonText(attempt.result));
      else row(block,"返回资料","该次记录未列出资料条目");
      const raw=make("details",undefined,"report-gap-raw");raw.append(make("summary","原始检索记录"),make("pre",JSON.stringify(savedAttempt,null,2)));block.append(raw);
      root.append(block);
    }
    const missing=Array.isArray(detail.missingRecords)?detail.missingRecords:[];
    if(missing.length)root.append(make("p","缺少记录："+missing.join("、"),"review-summary-unavailable"));
    root.append(make("p","访问或检索到的资料不自动等于支持当前判断的引文。","muted"));
  }
  const opinionActions=[{code:"comment",label:"评论"},{code:"objection",label:"保留异议"},{code:"request-evidence",label:"要求补证"},{code:"scope-confirmation",label:"确认表述范围"},{code:"correction",label:"更正本人意见"}];
  function mountOpinions(destination,{runId,identity,objectKind="report",objectId="report",objectLabel,api,actorId,tip,onSaved}={}){
    const holder=make("details",undefined,"report-opinions");
    holder.dataset.objectKind=objectKind;holder.dataset.objectId=objectId;
    const heading=make("summary","审阅意见");
    if(tip)heading.append(tip("只追加当前版本、当前对象的人工意见；不创建任务，不调用模型，不发布报告或本体关系。", "info","意见的作用范围"));
    holder.append(heading);destination.append(holder);
    const content=make("div",undefined,"report-opinions-body");holder.append(content);
    const version=identity?.reportHash||null,sourceHash=identity?.sourceGraphHash??null;
    const storageEnabled=typeof actorId==="string"&&actorId.length>0;
    const key="ontology-report-opinion:"+JSON.stringify([actorId,runId,version,sourceHash,objectKind,objectId]);
    let draft={action:"comment",reason:"",evidenceIds:[],supersedesEventId:null,expectedRevision:null,idempotencyKey:crypto.randomUUID()};
    try{const saved=storageEnabled?JSON.parse(sessionStorage.getItem(key)||"null"):null;if(saved&&typeof saved.reason==="string")draft={...draft,action:typeof saved.action==="string"?saved.action:"comment",reason:saved.reason,evidenceIds:Array.isArray(saved.evidenceIds)?saved.evidenceIds.filter(value=>typeof value==="string"):[],supersedesEventId:typeof saved.supersedesEventId==="string"?saved.supersedesEventId:null,expectedRevision:Number.isInteger(saved.expectedRevision)&&saved.expectedRevision>=0?saved.expectedRevision:null,idempotencyKey:typeof saved.idempotencyKey==="string"?saved.idempotencyKey:draft.idempotencyKey,uncertain:saved.uncertain===true};}catch{}
    let loaded=false,busy=false,data=null,form=null,message=null,submit=null,reason=null,action=null,correction=null,evidence=null,rebase=null,refreshButton=null;
    const persist=()=>{if(!storageEnabled)return;try{sessionStorage.setItem(key,JSON.stringify(draft));}catch{}};
    const edit=()=>{draft.idempotencyKey=crypto.randomUUID();draft.uncertain=false;persist();updateControls();};
    const sameVersion=value=>value?.versionCurrent!==false&&value?.identity?.runId===runId&&value.identity?.reportHash===version&&(value.identity?.sourceGraphHash??null)===sourceHash&&value.object?.kind===objectKind&&value.object?.id===objectId;
    const isStale=()=>Number.isInteger(draft.expectedRevision)&&Number.isInteger(data?.objectRevision)&&draft.expectedRevision!==data.objectRevision;
    function updateControls(){
      if(!submit)return;
      submit.disabled=busy||!data?.writeAvailable||!sameVersion(data)||!(data.actions||opinionActions).some(entry=>entry.code===draft.action)||!Number.isInteger(draft.expectedRevision)||(isStale()&&!draft.uncertain)||!draft.reason.trim()||draft.evidenceIds.length>100||(draft.action==="correction"&&!draft.supersedesEventId);
      submit.textContent=draft.uncertain?"重试同一意见":"保存本对象意见";
      for(const field of [reason,action,correction,refreshButton])if(field)field.disabled=busy;
      for(const input of evidence?.querySelectorAll("input")||[])input.disabled=busy;
      if(rebase){rebase.hidden=!isStale()||!sameVersion(data)||draft.uncertain;rebase.disabled=busy;}
      if(form)form.setAttribute("aria-busy",String(busy));
    }
    function editFromFields(){
      draft.action=action.value;draft.reason=reason.value;
      draft.evidenceIds=[...evidence.querySelectorAll("input:checked")].map(input=>input.value);
      draft.supersedesEventId=draft.action==="correction"?(correction.value||null):null;
      correction.parentElement.hidden=draft.action!=="correction";edit();
    }
    function render(){
      content.replaceChildren();
      const context=make("div",undefined,"report-opinion-context");
      row(context,"作用对象",data?.object?.label||objectLabel||objectId);row(context,"对象类型",{report:"整份报告",node:"节点",edge:"关系",evidence:"引文"}[objectKind]||objectKind);
      row(context,"审阅版本",version?version.slice(0,12)+"…":"未取得版本指纹");row(context,"对象修订",count(data?.objectRevision));
      content.append(context);
      const versions=make("details",undefined,"report-opinion-version");versions.append(make("summary","精确对象与版本"));row(versions,"Run",runId);row(versions,"对象ID",objectId);row(versions,"审阅版本指纹",version);row(versions,"源图哈希",sourceHash);content.append(versions);
      const eventList=make("section",undefined,"report-opinion-events");eventList.setAttribute("aria-label","已保存的审阅意见");
      const events=Array.isArray(data?.events)?data.events:[];
      if(!events.length)eventList.append(make("p",loaded?"此版本、此对象暂无审阅意见。":"意见记录尚未取得。","muted"));
      for(const event of events){
        const item=make("article",undefined,"report-opinion-event");
        const actionName=(data?.actions||opinionActions).find(value=>value.code===event.action)?.label||"已保存意见";
        item.append(make("p",actionName+" · "+(event.displayName||event.reviewerId||"审阅人未记录")+" · "+time(event.createdAt),"report-opinion-event-meta"),make("p",event.reason));
        if(event.supersedesEventId)item.append(make("p","追加更正，原意见保留："+event.supersedesEventId,"muted"));
        if(Array.isArray(event.evidenceIds)&&event.evidenceIds.length)item.append(make("p","引用："+event.evidenceIds.join("、"),"report-opinion-event-refs"));
        row(item,"修订 / 事件",count(event.revision)+" / "+event.eventId);eventList.append(item);
      }
      content.append(eventList);
      if(!data?.writeAvailable)content.append(make("p",data?.unavailableReason||"当前仅可读取意见。","review-summary-unavailable"));
      if(data&&!sameVersion(data))content.append(make("p","报告版本已变化。草稿仍属于原版本，请重新打开报告后核对；意见不会自动移到新版本。","review-summary-unavailable"));
      form=make("form",undefined,"report-opinion-form");
      const actionLabel=make("label","意见类型");action=make("select");action.setAttribute("aria-label","意见类型");
      for(const entry of data?.actions||opinionActions){const option=make("option",entry.label);option.value=entry.code;action.append(option);}action.value=draft.action;actionLabel.append(action);
      const reasonLabel=make("label","理由与适用范围");reason=make("textarea");reason.rows=3;reason.maxLength=8000;reason.required=true;reason.value=draft.reason;reason.setAttribute("aria-label","理由与适用范围");reasonLabel.append(reason);
      const correctLabel=make("label","更正本人哪条意见");correction=make("select");correction.append(make("option","请选择本人已有意见"));correction.firstElementChild.value="";
      const corrected=new Set(events.map(event=>event.supersedesEventId).filter(Boolean));
      for(const event of events.filter(value=>value.reviewerId===data?.currentActor?.reviewerId&&!corrected.has(value.eventId))){const option=make("option",(event.reason||"").slice(0,75)+" · 修订 "+event.revision);option.value=event.eventId;correction.append(option);}
      if(draft.supersedesEventId&&![...correction.options].some(option=>option.value===draft.supersedesEventId)){const option=make("option","草稿引用的原意见（待复核记录）");option.value=draft.supersedesEventId;correction.append(option);}correction.value=draft.supersedesEventId||"";correctLabel.append(correction);correctLabel.hidden=draft.action!=="correction";
      const refs=make("details",undefined,"report-opinion-reference-picker");refs.append(make("summary","绑定本次已有引文（"+draft.evidenceIds.length+"）"));
      evidence=make("div",undefined,"report-opinion-evidence-options");
      const available=[...(data?.availableEvidence||[])];
      for(const id of draft.evidenceIds)if(!available.some(entry=>entry.evidenceId===id))available.push({evidenceId:id,label:"草稿中已选引文，当前列表未返回："+id});
      for(const entry of available){const label=make("label",undefined,"report-opinion-evidence-option"),input=make("input");input.type="checkbox";input.value=entry.evidenceId;input.checked=draft.evidenceIds.includes(entry.evidenceId);label.append(input,make("span",entry.label||entry.evidenceId));label.title=entry.evidenceId;evidence.append(label);}
      if(!data?.availableEvidence?.length)evidence.append(make("p","当前版本未提供可选择的引文记录。","muted"));refs.append(evidence);
      const actions=make("div",undefined,"report-opinion-form-actions");submit=make("button","保存本对象意见");submit.type="submit";
      refreshButton=make("button","重新读取当前记录");refreshButton.type="button";refreshButton.onclick=()=>load(true);
      rebase=make("button","已核对，按当前修订继续编辑");rebase.type="button";rebase.onclick=()=>{draft.expectedRevision=data.objectRevision;edit();render();};
      actions.append(submit,refreshButton,rebase);message=make("p",undefined,"report-opinion-message");message.setAttribute("role","status");message.setAttribute("aria-live","polite");
      form.append(actionLabel,correctLabel,reasonLabel,refs,make("p","只保存意见 · 不启动任务 · 不调用模型 · 不发布","report-opinion-cost"),actions,message);
      action.onchange=reason.oninput=correction.onchange=()=>editFromFields();evidence.onchange=()=>{editFromFields();refs.querySelector("summary").textContent="绑定本次已有引文（"+draft.evidenceIds.length+"）";};
      form.onsubmit=async event=>{
        event.preventDefault();if(submit.disabled)return;
        busy=true;updateControls();message.textContent="正在保存意见…";
        const body={reportHash:version,sourceGraphHash:sourceHash,objectKind,objectId,action:draft.action,reason:draft.reason.trim(),evidenceIds:[...draft.evidenceIds],expectedRevision:draft.expectedRevision,idempotencyKey:draft.idempotencyKey,supersedesEventId:draft.supersedesEventId};
        try{
          const saved=await api("/report-review/runs/"+encodeURIComponent(runId)+"/events",{method:"POST",body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
          if(saved.versionCurrent===false){
            data=saved;draft.uncertain=false;persist();busy=false;render();message.textContent="此意见已记录在旧版报告；当前输出已变化。原草稿已保留，不会移到新版本，请重新打开当前报告核对。";
            onSaved?.(saved);return;
          }
          if(!sameVersion(saved))throw Error("保存响应未匹配当前对象与版本，请重读记录核对。");
          data=saved;draft={action:"comment",reason:"",evidenceIds:[],supersedesEventId:null,expectedRevision:saved.objectRevision,idempotencyKey:crypto.randomUUID()};persist();busy=false;render();message.textContent=saved.idempotent?"该意见已保存，未重复追加。":"意见已保存；未创建任务或发布。";
          if(holder.isConnected)heading.focus({preventScroll:true});
          onSaved?.(saved);
        }catch(error){draft.uncertain=![400,401,403,404,409,422].includes(error.status);message.textContent=(error.status===409?"版本或对象修订发生冲突，请重新读取当前记录并核对。":draft.uncertain?"尚未确认保存结果，请重试同一意见核对，不会重复追加。":"保存失败："+error.message)+" 草稿与重试标识已保留。";persist();}
        finally{busy=false;updateControls();}
      };
      content.append(form);updateControls();
      if(draft.uncertain)message.textContent="上次保存结果未确认；请重试同一意见核对，复用原提交标识。";
      else if(isStale())message.textContent="当前对象已有新意见。草稿保留原修订，请核对记录后再继续。";
    }
    async function load(explicit=false){
      if(busy)return;
      busy=true;
      if(!loaded)content.replaceChildren(make("p","读取此版本、此对象的意见…","muted"));
      updateControls();
      try{
        const params=new URLSearchParams({objectKind,objectId});
        data=await api("/report-review/runs/"+encodeURIComponent(runId)+"/events?"+params,{signal:AbortSignal.timeout(15000)});
        if(draft.expectedRevision===null&&Number.isInteger(data.objectRevision)){draft.expectedRevision=data.objectRevision;persist();}
        loaded=true;busy=false;render();if(explicit){if(!isStale()&&!draft.uncertain)message.textContent="已读取当前对象记录；草稿已保留。";if(holder.isConnected)refreshButton.focus({preventScroll:true});}
      }catch(error){loaded=false;busy=false;data=null;render();message.textContent="意见记录读取失败："+error.message+"。草稿已保留，可重新读取。";}
      finally{busy=false;updateControls();}
    }
    holder.addEventListener("toggle",()=>{if(holder.open&&!loaded)load();});
    return holder;
  }
  window.AgentAdminReviewSummary = Object.freeze({mountHeader, mountResult,mountGapDetail,mountOpinions,mountGoalContract});
})();
