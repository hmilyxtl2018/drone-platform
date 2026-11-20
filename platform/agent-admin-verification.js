"use strict";
(() => {
  const labels = {passed:"通过",failed:"不通过",inconclusive:"无法核验","not-applicable":"已恢复 / 不适用"};
  const reasons = {
    "profile-version-reference-valid":"当前版本引用有效", "goal-reference-and-hash-match":"目标契约引用与哈希一致",
    "allowed-modes-match":"允许模式一致", "effective-budget-match":"有效资源预算一致", "public-projection-match":"网关公开契约投影一致",
    "frozen-profile-valid":"冻结 Profile 有效", "frozen-baseline-valid":"冻结执行基线有效",
    "frozen-skill-bindings-valid":"冻结 Skill 绑定一致", "frozen-goal-valid":"冻结目标契约有效",
    "frozen-harness-valid":"冻结 Harness 契约有效", "frozen-intent-chain-valid":"父子任务意图约束一致",
    "invocation-run-ownership-match":"调用归属一致", "invocation-succeeded":"调用已成功",
    "frozen-step-ownership-match":"调用节点与冻结配置一致", "model-input-output-hashes-match":"模型输入输出哈希一致",
    "model-budget-record-match":"模型预算记账一致", "frozen-tool-binding-match":"工具版本与绑定一致",
    "tool-budget-ledger-match":"工具调用与预算账本一致", "tool-input-output-recorded":"工具输入输出记录齐全",
    "frozen-skill-binding-match":"Skill 版本与绑定一致", "skill-model-output-proof-match":"Skill 与模型输出凭据一致",
    "report-artifact-hash-match":"保存报告内容哈希一致", "graph-references-and-projection-valid":"图谱引用与投影有效",
    "assertion-evidence-references-match":"关系断言的证据引用有效", "evidence-source-hash-and-quote-match":"来源哈希与原文引文定位一致",
    "required-node-recorded":"必需节点已执行成功", "required-delegation-recorded":"必需子 Agent 委托已记录",
    "delegation-binding-match":"子 Agent 版本与授权绑定一致",
    "required-node-not-completed":"必需节点未完成", "required-node-has-gaps":"节点已执行，仍有缺口",
    "execution-completed-with-gaps":"运行结束，仍有缺口", "execution-in-progress":"运行尚未结束",
    "invocation-not-successful":"调用未成功", "unsuccessful-attempt-recovered":"此失败尝试已重试恢复",
    "skill-applicability-or-receipt-unrecorded":"缺少 Skill 适用性或调用记录",
    "verification-read-coverage-limit":"已达到读取上限，检查未覆盖全部记录",
    "invocation-coverage-limit":"调用记录超过本次检查上限", "required-delegation-missing":"缺少必需的子 Agent 委托记录",
    "model-budget-record-mismatch":"模型支出记录归属或额度不符", "model-budget-record-unavailable":"缺少模型支出记录",
    "model-input-output-unverified":"模型输入输出摘要未匹配", "report-artifact-hash-mismatch":"报告内容摘要不一致",
    "evidence-source-or-quote-mismatch":"来源摘要或引文定位不一致", "assertion-evidence-references-unverified":"关系断言缺少有效证据引用",
    "graph-references-or-projection-unverified":"图谱引用或投影未通过检查", "historical-intent-unrecorded":"历史任务未保存完整意图契约",
    "required-model-receipt-missing":"缺少冻结配置要求的模型调用记录"
    ,"expected-invocation-requirements-unrecorded":"历史基线未明确冻结各节点的调用要求"
  };
  const make = (tag, text, cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function mount(holder,{subject,api,label="核验",onReference}={}) {
    if(!holder||!subject||!api)return;
    const key=JSON.stringify(subject);
    let entry=holder.querySelector(":scope > .verification-entry");
    if(entry?.dataset.subject===key)return entry;
    entry?.remove();entry=make("button",label,"verification-entry");entry.type="button";
    entry.dataset.subject=key;entry.title="只读核验 · 检查数据库记录与追溯链";
    entry.setAttribute("aria-label",label+" · 只读核验");
    entry.onclick=()=>open({subject,api,onReference});holder.append(entry);return entry;
  }
  async function open({subject,api,onReference}) {
    const dialog=make("dialog",undefined,"verification-dialog"),heading=make("div",undefined,"verification-heading");
    const title=make("h2","记录核验"),close=make("button","关闭");close.type="button";
    title.id="verification-title";dialog.setAttribute("aria-labelledby",title.id);
    close.onclick=()=>dialog.close();heading.append(title,close);
    const body=make("div",undefined,"verification-body");body.setAttribute("aria-live","polite");
    dialog.append(heading,body);document.body.append(dialog);
    dialog.addEventListener("close",()=>dialog.remove(),{once:true});dialog.showModal();
    const load=async()=>{
      body.replaceChildren(make("p","正在读取数据库快照…"));
      try {
        const result=await api("/verifications/check",{method:"POST",body:JSON.stringify({subject})});
        if(!dialog.isConnected)return;
        body.replaceChildren();const cov=result.coverage;
        const state=make("p",`核验${labels[result.status]} · 通过 ${cov.mandatoryPassed} · 不通过 ${cov.mandatoryFailed} · 无法核验 ${cov.mandatoryUnknown}`,"verification-result");
        state.dataset.status=result.status;body.append(state);
        body.append(make("p","专业语义：未评审", "verification-note"));
        const filter=make("select");filter.setAttribute("aria-label","核验检查筛选");
        for(const [value,text] of [["attention","差异与无法核验项"],["all","全部检查"]]){const o=make("option",text);o.value=value;filter.append(o);}
        if(!cov.mandatoryFailed&&!cov.mandatoryUnknown)filter.value="all";
        const listing=make("div",undefined,"verification-checks"),pager=make("div",undefined,"verification-pager");let page=0;
        const draw=()=>{
          const items=result.checks.filter(x=>filter.value==="all"||["failed","inconclusive"].includes(x.status));
          const pages=Math.max(1,Math.ceil(items.length/40));page=Math.min(page,pages-1);listing.replaceChildren();pager.replaceChildren();
          for(const item of items.slice(page*40,(page+1)*40)){
            const row=make("details",undefined,"verification-check"),summary=make("summary");
            summary.append(make("span",labels[item.status],"verification-state"),make("span",reasons[item.reasonCode]||({passed:"记录匹配","not-applicable":"已恢复 / 不适用"}[item.status])||"记录存在差异，展开查看依据"));
            row.dataset.status=item.status;row.append(summary,make("code",item.checkId+" · "+item.reasonCode));
            for(const ref of item.evidenceRefs||[]){
              if(/^run-[A-Za-z0-9-]+$/.test(ref)){const a=make("a",ref);a.href="/agent-admin.html#run="+encodeURIComponent(ref);row.append(a);}
              else if(onReference&&!ref.startsWith("artifact-")){const b=make("button","定位证据 · "+ref);b.type="button";b.onclick=()=>{dialog.close();onReference(ref);};row.append(b);}
              else row.append(make("code",ref));
            }
            listing.append(row);
          }
          if(!items.length)listing.append(make("p","没有差异或无法核验项。"));
          const previous=make("button","上一页"),next=make("button","下一页");previous.disabled=page===0;next.disabled=page===pages-1;
          previous.onclick=()=>{page--;draw();};next.onclick=()=>{page++;draw();};
          pager.append(previous,make("span",`${items.length} 项 · ${page+1} / ${pages}`),next);
        };
        filter.onchange=()=>{page=0;draw();};body.append(filter,listing,pager);draw();
        const metadata=make("details",undefined,"verification-meta");metadata.append(make("summary","范围、版本与审计"));
        for(const text of [result.subject.id,result.queriedAt,"当前性："+({current:"当前版本",superseded:"历史冻结版本",unknown:"未确认"}[result.currentness]||"当前注册配置"),
          result.verifiedScope.join(" · "),"规则："+result.checker.policyId+" / "+result.checker.ruleVersion,
          "审计："+result.audit.recordId,"构建："+result.checker.buildHash])metadata.append(make("p",text));
        metadata.append(make("pre",JSON.stringify(result.authority.references,null,2)));body.append(metadata);
        for(const limitation of result.limitations||[])metadata.append(make("p",limitation));
      } catch(error){
        if(!dialog.isConnected)return;
        body.replaceChildren(make("p","核验未完成 · "+(error.detail?.code||error.detail?.detail?.code||error.message)));
        const retry=make("button","重试");retry.onclick=load;body.append(retry);
      }
    };await load();
  }
  window.AgentAdminVerification={mount,open};
})();
