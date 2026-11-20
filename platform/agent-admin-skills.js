/* Skill configuration and receipts are deliberately separate projections. */
(() => {
  const make = (tag, text, cls) => { const e=document.createElement(tag); if(text!=null)e.textContent=text; if(cls)e.className=cls; return e; };
  const statuses = {succeeded:'调用成功',failed:'调用失败',running:'调用中',cancelled:'已取消',unknown:'结果未知'};
  const fold = (label, value) => { const d=make('details'); d.append(make('summary',label),make('pre',typeof value==='string'?value:JSON.stringify(value,null,2))); return d; };
  function packageView(root, binding) {
    root.append(make('p',`${binding.name||binding.skillKey} · v${binding.version}`),make('code',binding.contentHash));
    if(binding.package) {
      root.append(fold('方法正文',binding.package.instructions.join('\n\n')),fold('输入契约',binding.package.inputContract),fold('输出契约',binding.package.outputContract),fold('验证器',binding.package.manifest.validatorRefs));
    }
  }
  function configuration(root, data) {
    const card=make('section',null,'profile-card skill-panel'); card.append(make('h3','Skill · 分析方法'));
    let count=0;
    function add(group, delegated=false) {
      for(const b of group.bindings||[]) {
        count++; const d=make('details',null,'skill-method');d.append(make('summary',`${b.name||b.skillKey} · v${b.version} · ${delegated?'子 Agent 配置':'直接配置'}`));
        d.append(make('p',`${group.agentKey}@${group.profileVersion} / ${b.stepKey} / ${b.stage}`)); packageView(d,b);card.append(d);
      }
      for(const child of group.delegated||[])add(child,true);
    }
    add(data); if(!count)card.append(make('p','此 Profile 及其组合子 Agent 未配置 Skill。','muted'));
    root.append(card);
  }
  function marker(bindings, invocations, pos, configured=false) {
    if(!bindings?.length&&!invocations?.length)return null;
    const el=make('span','S','skill-mini');el.setAttribute('role','img');
    const label=configured?'Skill 配置；不代表执行':invocations?.length?`Skill · ${invocations.length} 条实际调用记录`:'Skill 已配置；未记录调用';
    el.title=label;el.setAttribute('aria-label',label);
    if(pos)Object.assign(el.style,{position:'absolute',left:pos.x+pos.width-20+'px',top:pos.y+pos.height-20+'px'});
    return el;
  }
  function inspect(root, {runId,bindings=[],invocations=[],api,cutoff=Infinity}) {
    const section=make('section',null,'skill-panel');section.append(make('h4','Skill · 方法与执行'));
    for(const b of bindings){const d=make('details'); d.append(make('summary',`已配置 · ${b.skillKey}@${b.version}`));packageView(d,b);section.append(d);}
    if(!invocations.length)section.append(make('p','未记录本节点的 Skill 调用。','muted'));
    for(const i of invocations){
      const d=make('details',null,'skill-receipt'); const done=i.completed_at&&Date.parse(i.completed_at)<=cutoff;
      d.append(make('summary',`${i.skill_key}@${i.skill_version} · ${statuses[i.status]||i.status}`),make('code',i.invocation_id),make('p',`阶段 ${i.stage} · ${i.step_run_id||'无节点记录'}`),make('code',i.content_hash));
      window.AgentAdminVerification?.mount(d,{subject:{kind:'invocation',run_id:runId,invocation_kind:'skill',invocation_id:i.invocation_id},api});
      if(i.error_code)d.append(make('p',i.error_code));
      const load=make('button','读取输入、输出与模型凭据');load.type='button';load.disabled=!done;
      if(!done)load.title='此回放时点没有已完成调用凭据';
      let loaded=false;
      load.onclick=async()=>{if(loaded)return;load.disabled=true;try{
        const value=await api('/runs/'+encodeURIComponent(runId)+'/skills/'+encodeURIComponent(i.invocation_id));
        packageView(d,value.binding||{skillKey:i.skill_key,version:i.skill_version,contentHash:i.content_hash});
        d.append(fold('校验与失败',value.validation));
        for(const c of value.calls){d.append(make('p',`${c.actual_model||c.requested_model||'模型未知'} · ${statuses[c.status]||c.status} · 输入 ${c.input_tokens??'未知'} / 输出 ${c.output_tokens??'未知'} Token · ${c.latency_ms??'未知'} ms`),fold('实际输入（已脱敏）',c.input),fold('实际输出',c.output));}
        d.append(make('p',value.billing,'muted'));loaded=true;load.remove();
        if(value.validation.receiptVerified&&value.binding.skillKey==='citation-support-review') {
          expertForm(d,runId,i,value,api);
          trialForm(d,runId,i,value.binding,api);
        }
      }catch(e){load.disabled=false;load.textContent='读取失败，点击重试：'+e.message;}};
      d.append(load);section.append(d);
    }
    root.append(section);
    if(runId&&api&&cutoff===Infinity&&(bindings.length||invocations.length)) trials(section,runId,api);
  }
  function trialForm(root,runId,invocation,binding,api) {
    const form=make('details',null,'skill-trial-form');form.append(make('summary','用此材料比较 Skill 版本'));
    const version=make('input');version.placeholder='候选版本，例如 1.0.2';version.setAttribute('aria-label','候选 Skill 版本');
    const hypothesis=make('textarea');hypothesis.placeholder='观察到什么问题，预期如何验证';hypothesis.setAttribute('aria-label','方法改进假设');
    const methods=make('textarea');methods.value=binding.package.instructions.join('\n\n');methods.rows=6;methods.setAttribute('aria-label','候选方法正文');
    const samples=make('textarea');samples.placeholder='可选：附加真实材料，每行填写 Run ID, Skill 调用 ID（最多 7 组）';samples.setAttribute('aria-label','附加已保存材料');
    const go=make('button','创建对照执行（2 次模型调用上限）');go.type='button';const status=make('p',null,'muted');let operation;
    go.onclick=async()=>{go.disabled=true;try{
      operation??='skill-ui-'+crypto.randomUUID();
      const extra=samples.value.trim()?samples.value.trim().split('\n').map(line=>{const [rid,iid,...rest]=line.split(',').map(s=>s.trim());if(!rid||!iid||rest.length)throw Error('每行需要 Run ID, Skill 调用 ID');return {runId:rid,invocationId:iid}}):[];
      const result=await api('/runs/'+encodeURIComponent(runId)+'/skill-trials',{method:'POST',body:JSON.stringify({invocationId:invocation.invocation_id,samples:[{runId,invocationId:invocation.invocation_id},...extra],candidateVersion:version.value.trim(),instructions:methods.value.split(/\n\s*\n/).map(s=>s.trim()).filter(Boolean),hypothesis:hypothesis.value.trim(),idempotencyKey:operation})});
      status.textContent='已入队 · '+result.trialId+'；完成后刷新本节点查看比较。';
    }catch(e){status.textContent=e.message;operation=null;go.disabled=false;}};
    samples.oninput=()=>{go.textContent=`创建对照执行（${2*(1+(samples.value.trim()?samples.value.trim().split('\n').length:0))} 次模型调用上限）`};
    form.append(version,hypothesis,methods,samples,go,status);root.append(form);
  }
  const categories={sufficient_data:'证据充分',missing_data:'证据不足',conflicting_data:'证据冲突',out_of_scope:'范围外'};
  const verdict=v=>({supported:'支持',unsupported:'不支持'}[v]||'未返回');
  function expertForm(root,runId,invocation,data,api) {
    const block=make('details',null,'skill-trial-form');block.append(make('summary','专家标注此材料'));
    const category=make('select');category.setAttribute('aria-label','专家案例分类');
    category.append(new Option('选择真实案例分类',''));for(const [k,v] of Object.entries(categories))category.append(new Option(v,k));
    const expected={},expectedRelations={},expectedScenarios={};
    const verdictSelect=labelText=>{const label=make('label',labelText),select=make('select');select.setAttribute('aria-label','专家判断 '+labelText);select.append(new Option('选择判断',''),new Option('支持','supported'),new Option('不支持','unsupported'));label.append(select);block.append(label);return select};
    for(const call of data.calls)for(const m of call.input?.request?.messages||[]) {
      if(m.role!=='user')continue;let material;try{material=JSON.parse(m.content)}catch{continue}
      for(const p of material.proposals||[]){const key=p.proposal?.perspectiveKey;if(!key||expected[key])continue;
        expected[key]=verdictSelect(p.perspective?.name||key);
        for(const [field,target] of [['relations',expectedRelations],['scenarioAssessments',expectedScenarios]]) for(const row of p.proposal?.[field]||[]) {
          target[key]??={};target[key][row.id]=verdictSelect((field==='relations'?'关系 · ':'条件判断 · ')+(row.statement||row.id));
        }
      }
    }
    const reason=make('textarea');reason.placeholder='专家依据及适用范围（必填）';reason.setAttribute('aria-label','专家标注依据');
    const save=make('button','保存专家标注');save.type='button';const status=make('p',null,'muted');
    const values=map=>Object.fromEntries(Object.entries(map).map(([k,v])=>[k,v.value]));
    const nested=map=>Object.fromEntries(Object.entries(map).map(([k,v])=>[k,values(v)]));
    save.onclick=async()=>{save.disabled=true;try{await api('/runs/'+runId+'/skills/'+invocation.invocation_id+'/expert-label',{method:'POST',body:JSON.stringify({caseType:category.value,expected:values(expected),expectedRelations:nested(expectedRelations),expectedScenarios:nested(expectedScenarios),rationale:reason.value.trim()})});status.textContent='标注已保存；请重新评测相关对照。';}catch(e){status.textContent=e.message}finally{save.disabled=false}};
    block.append(category,reason,save,status);root.append(block);
  }
  function comparison(root,result) {
    const view=make('div',null,'skill-comparison');root.append(view);
    view.append(make('p',result.publicationEligible?'已具备人工审阅资格':'验证不足或未改善 · 保留当前方法','muted'));
    if(result.missingCoverage?.length)view.append(make('p','缺少专家分类：'+result.missingCoverage.map(k=>categories[k]||k).join('、'),'muted'));
    for(const c of result.results||[]) {
      const table=make('table');const head=make('tr');for(const s of ['指标','基线','候选'])head.append(make('th',s));table.append(head);
      const rows=[['支持 / 弃权',...['baseline','candidate'].map(r=>c.metrics[r].supported+' / '+c.metrics[r].abstentions)],
        ['专家错误',...['baseline','candidate'].map(r=>c.metrics[r].expertErrors??'未标注')],
        ['模型调用',...['baseline','candidate'].map(r=>c.usage[r].modelCalls)],
        ['输入 / 输出 Token',...['baseline','candidate'].map(r=>(c.usage[r].inputTokens??'未知')+' / '+(c.usage[r].outputTokens??'未知'))],
        ['耗时 ms',...['baseline','candidate'].map(r=>c.usage[r].latencyMs??'未知')],
        ['费用',...['baseline','candidate'].map(r=>c.usage[r].cost??'未知')]];
      for(const cells of rows){const tr=make('tr');cells.forEach(s=>tr.append(make('td',s)));table.append(tr)}view.append(table);
      view.append(make('p',`同材料、模型及预算：${c.sameModelAndBudget?'通过':'未通过'} · 凭据：${Object.values(c.receiptsVerified||{}).every(Boolean)?'已核验':'未通过'}`,'muted'));
      const outputs=make('details');outputs.append(make('summary','逐项判断与依据'));
      const keys=new Set(['baseline','candidate'].flatMap(r=>(c.outputs?.[r]?.reviews||[]).map(v=>v.perspectiveKey)));
      for(const key of keys){outputs.append(make('strong',key));for(const [r,label] of [['baseline','基线'],['candidate','候选']]){
        const review=c.outputs[r].reviews.find(v=>v.perspectiveKey===key);outputs.append(make('p',label+' · '+verdict(review?.verdict)+'：'+(review?.reason||'未返回依据')));
        const checks=make('details');checks.append(make('summary',label+' · 关系与条件判断'));
        for(const field of ['relations','scenarioAssessments'])for(const check of review?.[field]||[])checks.append(make('p',check.id+' · '+verdict(check.verdict)+'：'+check.reason));outputs.append(checks);
      }}view.append(outputs);
    }
    view.append(fold('完整评测与校验记录',result));return view;
  }
  function releaseForm(root,runId,id,api,{rollback=false,onDraft}={}) {
    const form=make('details',null,'skill-trial-form');form.append(make('summary',rollback?'回滚到此发布之前的方法':'创建方法切换草稿'));
    const version=make('input');version.placeholder='新的 Profile 版本';version.setAttribute('aria-label','方法切换 Profile 版本');
    const reason=make('textarea');reason.placeholder='变更理由';reason.setAttribute('aria-label','方法切换理由');
    const go=make('button','创建受控草稿');go.type='button';const status=make('p',null,'muted');
    go.onclick=async()=>{go.disabled=true;try{
      const result=await api('/runs/'+runId+(rollback?'/skill-releases/':'/skill-trials/')+id+(rollback?'/rollback-draft':'/release-draft'),{method:'POST',body:JSON.stringify({profileVersion:version.value.trim(),reason:reason.value.trim()})});
      status.textContent='已创建草稿；只影响明确发布后的新运行。应用 Agent 的子版本引用须另行更新。';
      const open=make('button','审阅草稿与发布差异');open.type='button';open.onclick=async()=>{
        if(onDraft){await onDraft(result);return;}
        const dialog=make('dialog',null,'skill-draft-dialog'),close=make('button','关闭');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();
        try{await window.AgentProfileConfig.mount(dialog,{api,agentKey:result.agentKey,draftId:result.draftId,canEdit:true})}catch(e){dialog.append(make('p',e.message))}
      };form.append(open);
    }catch(e){status.textContent=e.message;go.disabled=false}};
    form.append(version,reason,go,status);root.append(form);
  }
  async function trials(root,runId,api) {
    const block=make('details',null,'skill-trial-history');block.append(make('summary','Skill 版本对照'));
    root.append(block);
    try {
      const data=await api('/runs/'+encodeURIComponent(runId)+'/skill-trials');
      if(!data.trials.length){block.remove();return;}
      for(const t of data.trials){
        const outcome={ 'eligible-for-human-review':'待人工审阅','insufficient-validation':'验证不足','not-improved':'未达到改进门槛' };
        const value=t.content_json,row=make('details');row.append(make('summary',`${value.baseline.version} → ${value.candidate.version} · ${outcome[t.assessment?.outcome]||t.runs.map(r=>({completed:'完成',failed:'失败',created:'排队中',running:'运行中'}[r.status]||r.status)).join(' / ')}`),make('p',value.hypothesis),make('code',t.artifact_id));
        for(const r of t.runs){const link=make('a',r.runId+' · '+r.status);link.href='/agent-admin.html#run='+encodeURIComponent(r.runId);row.append(link,make('br'));}
        let comparisonView=t.assessment?comparison(row,t.assessment):null;
        if(t.assessment?.publicationEligible)releaseForm(row,runId,t.artifact_id,api);
        const evaluate=make('button','读取结果并评测');evaluate.type='button';
        evaluate.disabled=t.runs.some(r=>!['completed','failed','blocked','cancelled'].includes(r.status));
        evaluate.onclick=async()=>{evaluate.disabled=true;try{const result=await api('/runs/'+runId+'/skill-trials/'+t.artifact_id+'/evaluate',{method:'POST',body:'{}'});comparisonView?.remove();comparisonView=comparison(row,result);if(result.publicationEligible&&!row.querySelector('.skill-trial-form'))releaseForm(row,runId,t.artifact_id,api);}catch(e){row.append(make('p',e.message));}finally{evaluate.disabled=false}};
        row.append(evaluate);block.append(row);
      }
    }catch(e){block.append(make('p','读取失败：'+e.message));}
  }
  window.AgentSkillUI={configuration,marker,inspect,trials,releaseForm};
})();
