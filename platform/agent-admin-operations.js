"use strict";
async function renderOperations(panel){
  panel.append(el("h2","运行健康"));
  const health=await api("/operations");
  panel.append(el("p","数据库："+health.database.status+" · Worker："+health.workers.status+" · 整体："+health.status));
  if(hasRole("admin")){const connectors=el("a","查看连接器与调用状态");connectors.href="#area=settings&tab=connectors";connectors.className="connector-health-link";panel.append(connectors);}
  panel.append(el("p","费用：unknown","muted"),el("p","尚未配置价格表。预算包含预留及已结算用量。","help-note help-info"));
  panel.append(json({workers:health.workers,queue:health.queue,budgets:health.budgets,cost:health.cost}));
  const actions={"worker-heartbeat-expired":"确认进程状态；使用统一启动命令恢复 Worker。", "no-fresh-worker":"启动 Worker，检查数据库连接与心跳。", "queue-execution-failed":"查看对应 Run 的事件和调用失败原因，修复后创建后续任务或恢复可重试任务。", "lease-expired-before-finish":"恢复 Worker 以接管过期租约；旧进程不得继续写入。", "actual-usage-over-budget":"查看实际用量和不确定调用；补充预算需创建独立后续任务。", "shared-budget-exhausted":"共享预算已耗尽，查看调用记录和数据缺口。"};
  panel.append(el("h3","当前异常"));
  if(!health.alerts.length)panel.append(el("p","当前默认队列没有检测到异常。"));
  for(const alert of health.alerts){const block=el("section",undefined,"config-row"),title=el("strong",alert.reason_code);title.append(miniTip(actions[alert.reason_code]||"查看运行事件与数据库状态。","caution","异常处理建议"));block.append(title,json(alert));panel.append(block);}
  const refresh=el("button","重新检测");refresh.onclick=async()=>{try{await api("/operations/scan",{method:"POST"});panel.replaceChildren();await renderOperations(panel);}catch(error){notice(error.message,true);}};refresh.disabled=!hasRole("operator");panel.append(refresh);
  const history=el("details");history.append(el("summary","告警历史（重复异常只保存一次）"),json(await api("/operations/alerts")));panel.append(history);
}

async function renderTraceability(panel,runId){
  panel.append(el("h2","需求与验证追溯"),el("p","规划追溯用于列出材料需求、责任和通过标准。缺少真实测量时保持阻断；制造、试飞与工程认证状态需真实工程资料。", "help-note help-caution"));
  if(!runId){panel.append(el("p","先选择运行。"));return;}
  const prefix="/runs/"+encodeURIComponent(runId),items=await api(prefix+"/traceability"),[artifacts,evidence]=await Promise.all([api(prefix+"/artifacts"),api(prefix+"/evidence")]);
  const list=el("div");panel.append(list);
  function detail(value){
    const matrix=value.matrix,block=el("section",undefined,"config-row");
    block.append(el("h3",matrix.title+" · "+value.status),el("p",matrix.purpose,"help-note help-info"));
    block.append(el("p","制造："+matrix.manufacturingStatus+" · 试飞："+matrix.flightTestStatus+" · 工程认证："+(matrix.engineeringCertification?"是":"否")));
    block.append(el("p","运行 Profile 哈希："+value.runProfileHash+" · 矩阵哈希："+value.matrixHash,"muted"));
    if(value.workspaceId)block.append(el("p","冻结工作区："+value.workspaceId+" · 修订 "+value.workspaceRevision+" · "+value.workspaceRevisionHash,"muted"));
    if(matrix.dataRequests?.length){const gaps=el("ul",undefined,"traceability-gaps");for(const request of matrix.dataRequests)gaps.append(el("li",request));block.append(el("h4","阻断数据需求"),gaps);}
    if(matrix.assumptions?.length){const assumptions=el("ul");for(const item of matrix.assumptions)assumptions.append(el("li",item));block.append(el("h4","假设"),assumptions);}
    const table=el("table"),head=el("tr");
    for(const title of ["ID / 类型 / 状态","项目与输入","责任","方法","通过标准","材料需求","证据引用"])head.append(el("th",title));
    table.append(head);
    for(const item of matrix.items){
      const row=el("tr"),cells=[item.id+" · "+item.type+" · "+item.status,item.title+"\n输入："+(item.inputs||[]).join(", ")+"\n关联："+(item.links||[]).join(", "),item.responsibility,item.method,item.passCriteria,[...(item.requiresData||[]),...(item.dataRequest?[item.dataRequest]:[])].join("\n")||"未声明",(item.evidenceRefs||[]).map(ref=>ref.sourceId?"source "+ref.sourceId+" · "+ref.sourceVersion+" · "+ref.contentHash:"artifact "+ref.artifactId+" · "+ref.contentHash).join("\n")||"无"];
      for(const content of cells)row.append(el("td",content));table.append(row);
    }
    block.append(table,json({matrixId:value.matrixId,createdBy:value.createdBy,createdAt:value.createdAt,items:value.items}));list.append(block);
  }
  for(const row of items){const b=el("button",row.matrix_json.title+" · "+row.status);b.onclick=async()=>{try{const value=await api(prefix+"/traceability/"+encodeURIComponent(row.matrix_id));detail(value);}catch(error){notice(error.message,true);}};list.append(b);}
  const form=el("form"),title=el("input"),purpose=el("textarea");title.required=true;purpose.required=true;
  function field(labelText,input){const l=el("label",labelText);l.append(input);form.append(l);}
  field("验证计划名称",title);field("计划目的",purpose);
  const requests=el("input"),assumptions=el("input");field("总体所需材料或数据（逗号分隔）",requests);field("假设与限制（逗号分隔）",assumptions);
  const rows=el("div");form.append(rows);const entries=[];
  const types=[["requirement","需求"],["constraint","约束"],["calculation","计算"],["verification","验证"],["material","材料"],["data-request","数据需求"]];
  function add(){const block=el("section",undefined,"config-row"),entry={};const type=el("select");for(const [key,label]of types){const option=el("option",label);option.value=key;type.append(option);}const typeLabel=el("label","项目类型");typeLabel.append(type);block.append(typeLabel);entry.type=type;
    for(const [key,labelText]of[["id","项目 ID"],["title","项目名称"],["responsibility","责任角色"],["method","方法/工序"],["passCriteria","通过标准"],["requiresData","所需材料或数据（逗号分隔）"],["inputs","输入项目 ID（逗号分隔）"],["links","关联项目 ID（逗号分隔）"]]){const l=el("label",labelText),input=el("input");input.required=!["links","inputs"].includes(key);if(key==="id")input.value="trace-"+(entries.length+1);l.append(input);block.append(l);entry[key]=input;}
    const evidenceField=el("fieldset"),legend=el("legend","引用此运行中的持久化证据");evidenceField.append(legend);
    const choices=[...artifacts.map(a=>({ref:{artifactId:a.artifact_id,contentHash:a.content_hash},label:"产物 · "+a.artifact_type+" · "+a.artifact_id})),...(evidence.sources||[]).map(s=>({ref:{sourceId:s.sourceId,artifactId:s.artifactId,contentHash:s.contentHash,sourceVersion:s.metadata?.sourceVersion||s.artifactHash,locator:s.locator},label:"来源 · "+(s.metadata?.title||s.sourceUri||s.sourceId)+" · "+s.sourceId}))];
    entry.evidence=[];if(!choices.length)evidenceField.append(el("p","此运行暂无可引用的持久化产物或来源。","muted"));
    for(const choice of choices){const l=el("label",choice.label+" · "+choice.ref.contentHash),box=el("input");box.type="checkbox";l.prepend(box);evidenceField.append(l);entry.evidence.push({box,choice});}block.append(evidenceField);entries.push(entry);rows.append(block);}
  add();const addButton=el("button","增加验证项目");addButton.type="button";addButton.onclick=add;const submit=el("button","保存不可变验证计划");submit.disabled=!hasRole("operator");form.append(addButton,submit);panel.append(form);
  form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{const split=value=>value.split(/[,，]/).map(v=>v.trim()).filter(Boolean);const matrix={title:title.value,purpose:purpose.value,dataRequests:split(requests.value),assumptions:split(assumptions.value),items:entries.map(e=>({id:e.id.value,type:e.type.value,title:e.title.value,responsibility:e.responsibility.value,method:e.method.value,passCriteria:e.passCriteria.value,requiresData:split(e.requiresData.value),inputs:split(e.inputs.value),links:split(e.links.value),status:e.type.value==="verification"?"not-tested":"planned",evidenceRefs:e.evidence.filter(x=>x.box.checked).map(x=>x.choice.ref)}))};const result=await api(prefix+"/traceability",{method:"POST",body:JSON.stringify({matrix})});detail(result);notice("追溯计划已保存；尚无可信测量验收机制，不能将验证项标为通过。 ");}catch(error){notice(error.message,true);}finally{submit.disabled=false;}};
}
