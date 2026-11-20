import {refreshServiceStatus,startServiceStatus} from './service-status.js?v=3-first-call-status';
import {renderExecutionWorkspace} from './execution-workspace.js?v=20261113-execution-methods';
const API_ROOT="/api/osint";
const INTELLIGENCE_API_ROOT="/api/intelligence";
const esc=function(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
const safeUrl=function(v){try{var u=new URL(String(v));return["http:","https:"].indexOf(u.protocol)>=0?u.href:""}catch(e){return""}};
const shortTime=function(v){return v?new Date(v).toLocaleString("zh-CN",{hour12:false,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"—"};
const dayLabel=function(v){return v?new Date(v).toLocaleDateString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit"}):"未记录"};
const $=function(id){return document.getElementById(id)};
const state={sources:[],tasks:[],task:null,assessments:[],selectedItems:new Set(),selectedAssessmentId:null,selectedFindingIndex:null,selectedCapabilityIndex:null,selectedGraphNodeId:null,selectedGraphEdgeId:null,selectedGraphAssociationId:null,selectedGraphProperty:null,evidenceFilterId:null,reviewBusy:false,capabilityBusyId:null,taskLoadToken:0,poll:null,provider:null,busy:false,graphByAssessment:{},validationByAssessment:{},auditByAssessment:{},admissionsByAssessment:{},augmentationBusy:{},graphFilters:{},graphView:{},autoExtractionRequested:{},admissionForms:{},auditFindingForms:{},activeStage:new URL(location.href).searchParams.get("stage")||"materials",objectSelections:{},objectFilters:{},admissionConfidence:{},selectedMaterialId:null,selectedAdmissionId:null,reportReviewForms:{},chartEvidence:null,executionByTask:{},executionBusy:{},manualActions:{},extractionJobs:{},extractionJobTimers:{},extractionJobBusy:{},extractionJobErrors:{},extractionJobErrorCounts:{},handledExtractionJobs:{},propertyMappings:{},propertyEdits:{},autoActions:{}};
const ui={form:$("osint-form"),service:$("service-status"),dot:$("service-dot"),provider:$("osint-provider"),sources:$("source-list"),sourceCount:$("source-count"),sourceSelected:$("source-selected"),history:$("task-history"),title:$("task-title"),question:$("task-question"),keywords:$("task-keywords"),since:$("task-since"),horizon:$("task-horizon"),maxItems:$("task-max-items"),workflowMode:$("task-workflow-mode"),autoSelect:$("auto-select-materials"),autoAssessment:$("auto-start-assessment"),autoEvidence:$("auto-run-evidence-check"),autoCritique:$("auto-run-model-critique"),autoAdmissions:$("auto-create-admissions"),start:$("run-search"),runTitle:$("run-title"),runQuestion:$("run-question"),runStatus:$("run-status"),refresh:$("refresh-task"),summary:$("run-summary"),summaryText:$("run-summary-text"),materials:$("item-list"),materialCount:$("item-count"),materialAll:$("material-all"),generate:$("generate-assessment"),assessments:$("assessment-list"),assessmentCount:$("assessment-count"),reviewState:$("review-state"),review:$("review-body"),evidence:$("evidence-body"),evidenceCount:$("evidence-count"),toast:$("osint-toast")};
function workbenchMessageMarkup(label,body,tone,className){var severity=['warning','error'].includes(tone)?tone:'info';return '<details class="workbench-message workbench-message-'+severity+(className?' '+className:'')+'"><summary class="workbench-message-summary" aria-label="展开'+esc(label)+'"><span class="workbench-message-symbol" aria-hidden="true">'+(severity==='info'?'i':'!')+'</span><span>'+esc(label)+'</span></summary><div class="workbench-message-body">'+body+'</div></details>'}
function augmentationMessagesMarkup(a,kinds,compact){var id=assessmentId(a),errors=state.augmentationErrors?.[id]||{},labels={graph:'图谱与对象',validation:'核验记录',admissions:'准入候选'},failed=asArray(kinds).filter(function(kind){return errors[kind]});if(compact)return failed.length?'<button type="button" class="filter-chip workbench-action-error" data-retry-augmentation="'+esc(id)+'" title="'+esc(failed.map(function(kind){return labels[kind]+'读取失败：'+errors[kind]}).join('\n'))+'" '+(state.augmentationBusy[id]?'disabled':'')+'>读取失败 · 重试</button>':'';return failed.length?'<div class="workbench-message-group augmentation-read-status">'+failed.map(function(kind){return workbenchMessageMarkup(labels[kind]+'读取失败','<p>'+esc(errors[kind])+'</p><p>已保存结果保留；尚未读取的记录不能判断为空。</p>','error')}).join('')+'<button type="button" class="filter-chip" data-retry-augmentation="'+esc(id)+'" '+(state.augmentationBusy[id]?'disabled':'')+'>重试读取</button></div>':''}
function notify(message,tone){var n=document.createElement("div");n.className="toast "+(tone||"success");n.textContent=message;ui.toast.append(n);setTimeout(function(){n.remove()},4200)}
async function request(path,options){options=options||{};var r=await fetch(API_ROOT+path,{headers:Object.assign({"Content-Type":"application/json"},options.headers||{}),method:options.method||"GET",body:options.body});var b=await r.json().catch(function(){return{}});if(!r.ok||b.ok===false)throw new Error(b.error||("HTTP "+r.status));return b}
async function requestIntelligence(path,options){options=options||{};var r=await fetch(INTELLIGENCE_API_ROOT+path,{headers:Object.assign({"Content-Type":"application/json"},options.headers||{}),method:options.method||"GET",body:options.body});var b=await r.json().catch(function(){return{}});if(!r.ok||b.ok===false)throw new Error(b.error||("HTTP "+r.status));return b}
function first(o){for(var i=1;i<arguments.length;i++){var k=arguments[i];if(o&&o[k]!==undefined&&o[k]!==null&&o[k]!=="")return o[k]}return""}
function asArray(v){if(Array.isArray(v))return v;if(v===undefined||v===null||v==="")return[];return[v]}
function itemText(v){if(v===undefined||v===null)return"";if(typeof v==="string"||typeof v==="number")return String(v);return String(first(v,"label","name","title","text","summary","value","reason")||"")}
function listText(v){return asArray(v).map(itemText).filter(Boolean)}
function keywords(v){return String(v||"").split(/[\n,，、;；]+/).map(function(x){return x.trim()}).filter(Boolean).slice(0,30)}
function statusLabel(s){return({queued:["排队中","pending"],running:["采集中","pending"],processing:["处理中","pending"],generating:["生成研判","pending"],completed:["已完成","approved"],succeeded:["已完成","approved"],partial:["部分完成","pending"],failed:["失败","rejected"],cancelled:["已取消","rejected"]}[String(s||"").toLowerCase()]||[s||"未知","muted"])}
function assessmentStatus(a){
  var raw=String(first(a,"status","reviewStatus","state")||"draft").toLowerCase();
  if(raw==="withdrawn")return["已撤回","withdrawn"];
  if(["published","released"].includes(raw))return["已发布","approved"];
  if(["accepted","approved","accept"].includes(raw))return["审核通过","approved"];
  if(["rejected","returned"].includes(raw))return["已退回","rejected"];
  if(["request-more-evidence","more-evidence","needs-evidence"].includes(raw))return["补充证据","pending"];
  if(["pending-review","pending_human","in-review","under-review"].includes(raw))return["待人工审核","pending"];
  return["研判草稿","muted"];
}
function confidenceLabel(finding){
  var value=first(finding,"confidence","confidenceLevel");
  if(typeof value==="object")value=first(value,"level","value");
  return ({low:"低",medium:"中",high:"高"})[String(value).toLowerCase()]||"未提供";
}
function providerLabel(p){if(!p)return"材料统计 / 证据不足";var id=String(first(p,"id","provider","name")||"").toLowerCase();var model=first(p,"model","modelId","version");var configured=p.configured===true||p.apiKeyConfigured===true||p.enabled===true;if(id.indexOf("deepseek")>=0)return configured?"DeepSeek · "+(model||"模型研判草稿"):"材料统计 / 证据不足";if(configured&&model)return(first(p,"name","provider")||id)+" · "+model;return"材料统计 / 证据不足"}
function isModelDraft(a){var p=first(a,"provider","modelProvider","generator");return String(typeof p==="object"?first(p,"id","name"):p).toLowerCase().indexOf("deepseek")>=0}
function saveLastTask(id){try{if(id)localStorage.setItem("osint.lastTaskId",String(id));else localStorage.removeItem("osint.lastTaskId")}catch(e){}}
function loadLastTask(){try{return localStorage.getItem("osint.lastTaskId")||""}catch(e){return""}}
function unwrapTask(b){return b&&b.task||b&&b.run||b}
function unwrapAssessments(b){var a=b&&b.assessments!==undefined?b.assessments:b&&b.assessment!==undefined?b.assessment:[];return Array.isArray(a)?a:a?[a]:[]}
function selectedSourceIds(){return Array.from(document.querySelectorAll("[data-source-id]:checked")).map(function(n){return n.dataset.sourceId})}
function isTextEvidence(item){var p=item?.provenance||{};return p.retrievalKind!=='search-discovery-only'&&p.contentKind!=='image-search-metadata'}
function selectedItemIds(){return Array.from(state.selectedItems).filter(function(id){return asArray(state.task?.items).some(function(item){return item.id===id&&isTextEvidence(item)})})}
function setSummary(text,tone){ui.summary.className="run-summary "+(tone?"summary-"+tone:"muted-summary");ui.summaryText.textContent=text;var trigger=ui.summary.querySelector('summary');if(trigger)trigger.setAttribute('aria-label',(tone==='error'?'执行失败':tone==='warning'?'执行提醒':'任务状态')+'：'+text)}
function renderProvider(){refreshServiceStatus()}
function sourceRoleLabel(role){return ({'official-publication':'官方公开发布','evaluation-report':'试验 / 审计评估来源','procurement-record':'采购 / 预算来源','manufacturer-disclosure':'厂商披露 · 自报声明','secondary-report':'防务媒体 · 二手报道','research-analysis':'研究机构分析','public-imagery-archive':'公开影像档案 · 待核验'})[role]||'来源类别未记录'}
function sourceNatureLabel(nature){return ({'manufacturer-claim':'厂商宣称','manufacturer-disclosure':'厂商披露','procurement-record':'采购记录','evaluation-report':'试验评估记录','official-publication':'官方公开发布','reported-test':'报道试验','public-reported-test':'公开试验报道','source-report':'来源报道','secondary-report':'二手报道','research-analysis':'研究分析','project-goal':'项目目标'})[nature]||nature||'资料性质未分类'}
function sourceProvenance(item){var p=item&&item.provenance||{};return Object.assign({},p,{sourceRole:item&&item.sourceRole||p.sourceRole||'',publisherDomain:item&&item.publisherDomain||p.publisherDomain||'',contentNature:item&&item.contentNature||p.contentNature||''})}
function sourceBadgeMarkup(item){var p=sourceProvenance(item),known=!!p.sourceRole&&p.sourceRole!=='unclassified';return '<span class="source-role-badge '+(known?'classified':'unclassified')+'" title="检索来源类别；文献性质与事实仍需核验">'+esc(sourceRoleLabel(p.sourceRole))+'</span>'}
function renderSources(){
  ui.sourceCount.textContent=state.sources.length;
  var current=state.task&&state.task.sourceIds&&state.task.sourceIds.length?new Set(state.task.sourceIds):null,known=current&&state.sources.some(function(s){return current.has(s.id)}),isSearch=state.sources.some(function(s){return s.engine==='firecrawl'});
  if($('source-engine'))$('source-engine').textContent=isSearch?'采集工具：Firecrawl · 按勾选的站点范围搜索':'采集工具：RSS / Atom · 读取所选订阅源';
  if(!state.sources.length){ui.sources.innerHTML='<div class="placeholder">没有可用的公开来源。</div>';ui.sourceSelected.textContent='0 已选';return}
  ui.sources.innerHTML=state.sources.map(function(s){var checked=known?current.has(s.id):s.defaultSelected!==false,domains=asArray(s.domains);return '<label class="source-option"><input type="checkbox" data-source-id="'+esc(s.id)+'" '+(checked?'checked':'')+'><span><b>'+esc(s.name||s.title||s.id)+'</b><span>'+esc(s.description||s.category||'公开来源')+'</span><span class="source-domains">'+esc(domains.length?domains.join(' · '):(s.url||s.uri||''))+'</span></span></label>'}).join('');updateSourceCount();
}
function updateSourceCount(){var checked=selectedSourceIds(),total=state.sources.length;ui.sourceSelected.textContent=checked.length+' 已选';$('source-all').classList.toggle('active',total>0&&checked.length===total);$('source-default').classList.toggle('active',total>0&&state.sources.every(function(s){return checked.includes(s.id)===(s.defaultSelected!==false)}))}
function restoreTaskSettings(task){
  if(!task)return;
  ui.title.value=task.title||'';ui.question.value=task.question||'';ui.keywords.value=listText(task.keywords).join(', ');
  [[ui.since,task.sinceDays,function(v){return '过去 '+v+' 天'}],[ui.horizon,task.horizonYears,function(v){return '未来 '+v+' 年'}]].forEach(function(row){var value=row[1];if(value===undefined||value===null)return;value=String(value);if(!Array.from(row[0].options).some(function(option){return option.value===value})){var option=document.createElement('option');option.value=value;option.textContent=row[2](value);row[0].append(option)}row[0].value=value});
  if(task.maxItems!==undefined)ui.maxItems.value=task.maxItems;
  var automation=task.automation||{};if(ui.workflowMode)ui.workflowMode.value=task.workflowMode||task.triggerMode||'manual';if(ui.autoSelect)ui.autoSelect.checked=!!automation.autoSelectMaterials;if(ui.autoAssessment)ui.autoAssessment.checked=!!automation.autoStartAssessment;if(ui.autoEvidence)ui.autoEvidence.checked=!!automation.autoRunEvidenceCheck;if(ui.autoCritique)ui.autoCritique.checked=!!automation.autoRunModelCritique;if(ui.autoAdmissions)ui.autoAdmissions.checked=!!automation.autoCreateAdmissionCandidates;syncAutomationOptions();
  renderSources();if(Array.isArray(task.sourceIds)){var ids=new Set(task.sourceIds);ui.sources.querySelectorAll('[data-source-id]').forEach(function(input){input.checked=ids.has(input.dataset.sourceId)});updateSourceCount()}
}
function renderCollectionCoverage(){
  renderImageryEvidence();
  var el=$('collection-coverage'),t=state.task,results=t&&asArray(t.sourceResults)||[];if(!el)return;el.hidden=!t;if(!t)return;
  var roles={},items=asArray(t.items);items.forEach(function(item){var label=sourceRoleLabel(sourceProvenance(item).sourceRole);roles[label]=(roles[label]||0)+1});
  var statuses={ok:'已检索',error:'失败',failed:'失败','skipped-budget':'条数预算不足，未检索','skipped-target':'已达到目标，未继续',partial:'本来源未达目标'};
  var budget=t.brief?.collectionBudget||{},loopNote=budget.searchRounds?'<p class="collection-note">循环检索 '+esc(budget.searchRounds)+' 轮 · '+esc(budget.targetReached?'已达到材料目标':'已达到循环上限')+' · 保留 '+esc(items.length)+' / '+esc(budget.maxItems||'—')+' 条</p>':'';
  el.innerHTML='<summary>本次采编记录 · '+items.length+' 条去重材料 · '+results.length+' 项来源结果</summary><div class="collection-body"><p>条数去重不代表来源独立。以下记录属于当前任务，左侧选择用于下一次搜索。</p>'+loopNote+'<div class="source-composition">'+Object.keys(roles).map(function(label){return '<span>'+esc(label)+' <b>'+roles[label]+'</b></span>'}).join('')+'</div>'+results.map(function(r){return '<div class="collection-row"><b>'+esc(r.sourceName||r.sourceId||'来源')+'</b><span>'+esc(statuses[r.status]||r.status||'未记录')+' · 保留 '+esc(r.itemCount??0)+' 条'+(r.searchRounds?' · '+esc(r.searchRounds)+' 轮':'')+(r.requestedLimit!==undefined?' / 请求上限 '+esc(r.requestedLimit):'')+'</span>'+(r.excludedCounts&&Object.values(r.excludedCounts).some(function(n){return n>0})?'<p>过滤：域外 '+esc(r.excludedCounts.outsideScope||0)+' · 超出时间窗 '+esc(r.excludedCounts.outsideTimeWindow||0)+' · 无效链接 '+esc(r.excludedCounts.invalidUrl||0)+' · 无正文 '+esc(r.excludedCounts.emptyBody||0)+' · 对象不匹配 '+esc(r.excludedCounts.unrelatedSubject||0)+' · 泛化目录 '+esc(r.excludedCounts.genericSummary||0)+' · 无效图片链接 '+esc(r.excludedCounts.invalidImageUrl||0)+'</p>':'')+(r.error?'<p>'+esc(r.error)+'</p>':'')+'</div>'}).join('')+collectionQueryPlanMarkup(t)+(asArray(t.errors).length?'<p class="collection-error">'+asArray(t.errors).map(function(e){return esc(e.sourceId||'采集')+'：'+esc(e.error||e.message||'失败')}).join('<br>')+'</p>':'')+'</div>';
}
function collectionQueryPlanMarkup(task){var plan=asArray(task.brief?.queryPlan||task.queryPlan),budget=task.brief?.collectionBudget||{};if(!plan.length&&!budget.searchRounds)return '';return '<details><summary>实际定向检索计划 · '+plan.length+' 项'+(budget.searchRounds?' · '+esc(budget.searchRounds)+' 轮':'')+'</summary>'+(budget.targetReached!==undefined?'<p class="collection-note">循环停止条件：'+esc(budget.targetReached?'已达到材料数量目标':'已达到循环预算上限')+' · 已保留 '+esc(budget.maxItems-budget.remainingItems)+' / '+esc(budget.maxItems)+' 条</p>':'')+plan.map(function(row){return '<div class="collection-row"><b>'+esc(row.sourceName||row.sourceId)+(row.round?' · 第 '+esc(row.round)+' 轮':'')+'</b><p>'+esc(row.query||'')+'</p><small>'+esc(row.searchType==='images'?'图片检索 · 待核验':'正文检索')+' · 对象锚点 '+esc(asArray(row.targetAnchors).join(' / ')||'主题短语')+' · '+esc(row.status||'已执行')+'</small></div>'}).join('')+'</details>'}
function mediaCandidates(){return asArray(state.task?.items).flatMap(function(item){return asArray(item.provenance?.mediaReferences).map(function(ref){return Object.assign({sourceItemId:item.id,title:item.title,sourceName:item.sourceName},ref)})})}
function imageryParentTarget(taskId){var parent=state.parentFollowups?.[taskId||state.task?.id];return parent&&parent.purpose==='public-event-imagery'&&parent.assessmentId&&parent.graphRevisionId&&parent.graphElementId?parent:null}
async function loadParentFollowup(taskId){
  if(!taskId)return;state.parentFollowups=state.parentFollowups||{};state.parentTargetGraphs=state.parentTargetGraphs||{};state.parentFollowupLoading=state.parentFollowupLoading||{};state.parentFollowupLoading[taskId]=true;
  try{var result=await request('/tasks/'+encodeURIComponent(taskId)+'/parent-followup'),parent=result.followup;state.parentFollowups[taskId]=parent||null;if(parent?.purpose==='public-event-imagery'&&parent.assessmentId){try{var response=await requestIntelligence('/assessments/'+encodeURIComponent(parent.assessmentId)+'/graph'),graph=response.graph||response;if((graph.graphRevisionId||graph.id)===parent.graphRevisionId)state.parentTargetGraphs[taskId]=graph;else delete state.parentTargetGraphs[taskId]}catch(error){delete state.parentTargetGraphs[taskId]}}}catch(error){}finally{delete state.parentFollowupLoading[taskId];if(state.task?.id===taskId)renderImageryEvidence()}
}
function renderImageryParentTarget(){var host=$('imagery-parent-target'),parent=imageryParentTarget();if(!host)return;host.hidden=!parent;if(!parent){host.innerHTML='';return}host.innerHTML='<div class="workbench-message-group"><b>固定对象：'+esc(parent.objectLabel||parent.graphElementId)+'</b>'+workbenchMessageMarkup(parent.staleGraph?'父目标已过期':'关联范围','<p>影像引用将关联原报告中的此对象；旧图谱版本保留，不自动改绑到当前版本。</p><small>原报告 '+esc(parent.assessmentId)+' · 图谱 '+esc(parent.graphRevisionId)+(parent.staleGraph?' · 原图谱已更新，引用仍待重新核验':'')+'</small>',parent.staleGraph?'warning':'info')+(parent.parentTaskId?'<button type="button" class="filter-chip" data-open-parent-imagery="'+esc(parent.parentTaskId)+'" data-parent-assessment="'+esc(parent.assessmentId)+'">返回原对象分析 →</button>':'')+'</div>'}
async function loadLinkedImageryEvidence(id){
  if(!id)return;state.linkedImageryByAssessment=state.linkedImageryByAssessment||{};state.linkedImageryCoverage=state.linkedImageryCoverage||{};var taskId=state.task?.id;
  try{var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/followups'),followups=asArray(result.followups).filter(function(record){return record.purpose==='public-event-imagery'&&record.followupTaskId}),taskIds=Array.from(new Set(followups.map(function(record){return record.followupTaskId}))),selected=taskIds.slice(-20),responses=await Promise.allSettled(selected.map(function(childId){return request('/tasks/'+encodeURIComponent(childId)+'/imagery-evidence')})),records=[];
    responses.forEach(function(response,index){if(response.status==='fulfilled')asArray(response.value.imageryEvidence).forEach(function(record){if(record.assessmentId===id)records.push(Object.assign({followupTaskId:selected[index]},record))})});state.linkedImageryByAssessment[id]=records;state.linkedImageryCoverage[id]={taskCount:taskIds.length,loadedCount:responses.filter(function(response){return response.status==='fulfilled'}).length,limited:taskIds.length>20,failedCount:responses.filter(function(response){return response.status==='rejected'}).length};if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id))renderAssessments();
  }catch(error){state.linkedImageryCoverage[id]={error:error.message};if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id))renderAssessments()}
}
function renderImageryEvidence(){
  var host=$('imagery-source-list');if(!host)return;state.imageryByTask=state.imageryByTask||{};var taskId=state.task?.id,records=asArray(state.imageryByTask[taskId]),candidates=mediaCandidates();$('imagery-count').textContent=records.length+' 已登记 / '+candidates.length+' 图片线索';
  host.innerHTML=records.map(function(record){return imagerySourceRowMarkup(record)}).join('')+candidates.slice(0,12).map(function(record,index){return imagerySourceRowMarkup(record,index)}).join('')+(!records.length&&!candidates.length?workbenchMessageMarkup('影像证据 0','<p>尚无影像证据。可在采编设置选择公开影像档案，围绕具体对象定向搜索；检索图片须核对获取日期、原始提供方和许可后再关联断言。</p>','warning'):'');
  var select=$('imagery-target'),previous=select.value,a=selectedAssessment(),records=a?extractedRecords(a):{nodes:[],edges:[]},nodes=records.nodes.concat(records.edges),parent=imageryParentTarget(taskId),form=$('imagery-form');renderImageryParentTarget();select.disabled=!!parent;
  if(parent){select.innerHTML='<option value="'+esc(parent.graphElementId)+'">'+esc(parent.objectLabel||parent.graphElementId)+' · 原任务固定目标</option>';select.value=parent.graphElementId;form.dataset.assessmentId=parent.assessmentId;form.dataset.graphRevisionId=parent.graphRevisionId;form.dataset.taskId=taskId}else{select.innerHTML='<option value="">暂未绑定，等待对象核验</option>'+nodes.map(function(n){return '<option value="'+esc(n.id)+'">'+esc(n.type?graphTypeLabel(n.type)+' · '+n.label:relationLabel(n.relation)+' · '+n.from+' → '+n.to)+'</option>'}).join('');if(nodes.some(function(n){return n.id===previous}))select.value=previous}renderImageryProperties();
  $('imagery-form').querySelector('button[type="submit"]').disabled=!taskId||!!state.parentFollowupLoading?.[taskId]||$('imagery-form').dataset.saving==='true';
}
function syncAutomationOptions(){document.querySelectorAll('.automation-options input').forEach(function(input){input.disabled=false})}
function automationPayload(){var automation={autoSelectMaterials:!!ui.autoSelect?.checked,autoStartAssessment:!!ui.autoAssessment?.checked,autoRunEvidenceCheck:!!ui.autoEvidence?.checked,autoRunModelCritique:!!ui.autoCritique?.checked,autoCreateAdmissionCandidates:!!ui.autoAdmissions?.checked},enabled=Object.values(automation).some(Boolean);return {workflowMode:enabled?'auto':'manual',automation:automation}}
function evidenceKindIcon(image){return '<svg class="evidence-kind-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true">'+(image?'<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><circle cx="5.5" cy="6" r="1"/><path d="m3 12 4-4 2.5 2 2-2 1.5 1.5"/>':'<path d="M4 1.5h5l3 3v10H4zM9 1.5v3h3M6 7h4M6 9.5h4M6 12h3"/>')+'</svg>'}
function imagerySourceRowMarkup(record,index){
  var candidate=Number.isInteger(index),source=safeUrl(record.sourceUrl),image=safeUrl(record.imageUrl),title=record.caption||record.title||'未命名影像',kind=candidate?'图片线索':record.mediaKind==='satellite-image'?'卫星影像':'来源照片';
  var status=record.staleGraph?'旧版 · 待复核':candidate?'待确认':'待人工核验';
  return '<article class="imagery-source-row"><details class="imagery-source-detail"><summary>'+evidenceKindIcon(true)+'<b class="imagery-source-title" title="'+esc(title)+'">'+esc(title)+'</b><span class="imagery-source-kind">'+esc(kind)+'</span><span class="imagery-source-provider" title="'+esc(record.provider||record.sourceName||'提供方待确认')+'">'+esc(record.provider||record.sourceName||'提供方待确认')+'</span><time>'+esc(record.captureDate||'日期待核验')+'</time><span class="imagery-source-status'+(record.staleGraph?' stale':'')+'">'+esc(status)+'</span><span class="list-detail-label">详情⌄</span></summary><div class="imagery-source-body">'+(candidate?'<p>图片检索线索：类型、获取时间与原始提供方尚未确认；须核对原始出处和许可后再登记。</p>':'<p>'+esc(record.supportStatement||'支持范围尚未记录。')+'</p><p>'+esc(record.staleGraph?'关联图谱已更新，需重新核验':record.targetLabel?'关联对象：'+record.targetLabel:'尚未关联具体对象')+'</p><small>'+esc(record.mediaKind==='satellite-image'?'卫星影像类型待核验。':'')+'该引用不会自动提升对象或报告置信度。</small>')+'</div></details><div class="imagery-source-actions">'+(source?'<a href="'+esc(source)+'" target="_blank" rel="noopener noreferrer">来源 ↗</a>':'')+(image?'<a href="'+esc(image)+'" target="_blank" rel="noopener noreferrer">图片 ↗</a>':'')+(candidate?'<button class="filter-chip" type="button" data-register-media="'+index+'">核对后登记</button>':'')+'</div></article>';
}
async function loadImageryEvidence(){var taskId=state.task?.id;if(!taskId)return;try{var result=await request('/tasks/'+encodeURIComponent(taskId)+'/imagery-evidence');state.imageryByTask=state.imageryByTask||{};state.imageryByTask[taskId]=asArray(result.imageryEvidence);if(state.task?.id===taskId){renderImageryEvidence();renderAssessments()}}catch(error){if(state.task?.id===taskId)$('imagery-source-list').innerHTML=workbenchMessageMarkup('影像来源读取失败','<p>'+esc(error.message)+'</p>','error')}}
function renderImageryProperties(){
  var select=$('imagery-property'),previous=select.value,a=selectedAssessment(),parent=imageryParentTarget(),parentGraph=parent&&state.parentTargetGraphs?.[state.task?.id],records=parent?{nodes:asArray(parentGraph?.nodes),edges:asArray(parentGraph?.edges)}:a?extractedRecords(a):{nodes:[],edges:[]},id=$('imagery-target').value,element=records.nodes.concat(records.edges).find(function(r){return r.id===id});
  var options=element&&records.nodes.includes(element)?groundedObjectProperties(element).map(function(row){return {path:row.propertyPath,label:propertyNameLabel(row.attribute.name)}}):[];
  select.innerHTML='<option value="">对象或关系整体</option>'+options.map(function(row){return '<option value="'+esc(row.path)+'">'+esc(row.label)+'</option>'}).join('');if(options.some(function(row){return row.path===previous}))select.value=previous;
}
function resetImageryForm(){var form=$('imagery-form'),parent=imageryParentTarget();form.reset();form.elements.license.required=false;form.dataset.taskId=state.task?.id||'';form.dataset.assessmentId=parent?.assessmentId||'';form.dataset.graphRevisionId=parent?.graphRevisionId||'';if(parent)$('imagery-target').value=parent.graphElementId;$('imagery-source-binding').textContent='可从图片线索选择已保存的来源；单独登记限公开影像发布者。';renderImageryProperties()}
async function saveImageryEvidence(event){
  event.preventDefault();var form=event.currentTarget,taskId=state.task?.id;if(!taskId||form.dataset.saving==='true')return;if(state.parentFollowupLoading?.[taskId]){notify('正在恢复补采关联目标，请稍候再登记。','warn');return}
  if(form.dataset.taskId&&form.dataset.taskId!==taskId){resetImageryForm();notify('任务已切换，请重新选择图片来源。','warn');return}
  var body=Object.fromEntries(new FormData(form));['imageUrl','captureDate','publishedDate','sensor','resolution','license','sourceItemId','propertyPath'].forEach(function(key){if(!body[key]?.trim())body[key]=null});
  var parent=imageryParentTarget(taskId);if(parent){if(form.dataset.assessmentId!==parent.assessmentId||form.dataset.graphRevisionId!==parent.graphRevisionId){notify('影像补采的固定目标发生变化，请重新读取任务。','warn');return}body.graphElementId=parent.graphElementId;body.assessmentId=parent.assessmentId;body.graphRevisionId=parent.graphRevisionId;if(!state.parentTargetGraphs?.[taskId])body.propertyPath=null}else if(body.graphElementId){var a=selectedAssessment(),graph=a&&graphData(a),revision=graph&&(graph.graphRevisionId||graph.id);if(!a||!revision){notify('请先保存对象图谱版本。','warn');return}if(form.dataset.assessmentId!==assessmentId(a)||form.dataset.graphRevisionId!==revision){notify('报告或图谱已更新，请重新选择关联对象。','warn');return}body.assessmentId=assessmentId(a);body.graphRevisionId=revision}else{delete body.graphElementId;body.propertyPath=null}
  var button=form.querySelector('button[type="submit"]');form.dataset.saving='true';button.disabled=true;
  try{var result=await request('/tasks/'+encodeURIComponent(taskId)+'/imagery-evidence',{method:'POST',body:JSON.stringify(body)});if(state.task?.id===taskId){resetImageryForm();await loadImageryEvidence()}notify(result.duplicate?'已存在相同影像引用。':'已保存待核验引用，请继续核对来源及支持范围。')}catch(error){notify('影像登记失败：'+error.message,'warn')}finally{delete form.dataset.saving;button.disabled=!state.task?.id}
}
function objectImageryMarkup(a,elementId){
  var id=assessmentId(a),revision=graphData(a)?.graphRevisionId||graphData(a)?.id,seen=new Set(),records=asArray(state.imageryByTask?.[state.task?.id]).concat(asArray(state.linkedImageryByAssessment?.[id])).filter(function(record){if(record.assessmentId!==id||record.graphElementId!==elementId||seen.has(record.id))return false;seen.add(record.id);return true});if(!records.length)return '';
  return '<details class="object-verification"><summary>关联影像来源 · '+records.length+'</summary>'+records.map(function(record){var url=safeUrl(record.sourceUrl),stale=record.staleGraph||record.graphRevisionId!==revision;return '<p>'+esc(record.title)+' · '+esc(record.captureDate||'获取日期未知')+' · '+esc(stale?'旧图谱版本引用，待重新核验':'待人工核验')+'</p><small>'+esc(record.mediaKind==='satellite-image'?'卫星影像类型待核验':'来源照片')+' · 保存目标版本 '+esc(record.graphRevisionId)+(record.propertyPath?' · '+esc(record.propertyPath):'')+'</small><p>'+esc(record.supportStatement)+'</p>'+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">核对原始影像出处 ↗</a>':'')+(record.taskId?'<button type="button" class="evidence-ref-button" data-open-imagery-task="'+esc(record.taskId)+'">查看该影像采编任务 →</button>':'')}).join('')+'<p>影像支持范围按具体断言审核，旧版引用不会自动改绑，也不自动提高置信度。</p></details>';
}
function linkedImageryCoverageMarkup(a){var coverage=state.linkedImageryCoverage?.[assessmentId(a)];if(!coverage)return '';if(coverage.error)return workbenchMessageMarkup('影像关联读取失败','<p>'+esc(coverage.error)+'</p>','error');return coverage.taskCount?workbenchMessageMarkup('关联影像任务 '+coverage.loadedCount+' / '+coverage.taskCount+(coverage.failedCount?' · 读取失败':''),'<p>已读取 '+coverage.loadedCount+' / '+coverage.taskCount+' 项关联影像补采任务'+(coverage.failedCount?'；'+coverage.failedCount+' 项读取失败':'')+(coverage.limited?'；只显示最近 20 项任务，请打开较早补采任务核对':'')+'。</p>',coverage.failedCount||coverage.limited?'warning':'info'):''}
function renderHistory(){var sorted=state.tasks.slice().sort(function(a,b){return String(b.createdAt||"").localeCompare(String(a.createdAt||""))});ui.history.innerHTML='<option value="">选择历史任务…</option>'+sorted.map(function(t){return '<option value="'+esc(t.id)+'" '+(state.task&&state.task.id===t.id?"selected":"")+'>'+esc(t.title||"未命名任务")+" · "+esc(dayLabel(t.createdAt))+"</option>"}).join("")}
 function renderTask(){renderCollectionCoverage();renderWorkflow();var t=state.task;if(!t){ui.runTitle.textContent="等待开始研究";ui.runQuestion.textContent="搜索公开材料后，在这里生成趋势研判结论。";ui.runStatus.textContent="待开始";ui.runStatus.className="state-pill muted";ui.refresh.disabled=true;ui.generate.disabled=true;renderMaterials();renderExecution();return}var pair=statusLabel(t.status),completion=t.completion||{},rawStatus=String(t.status||"").toLowerCase(),targetGap=(completion.targetReached===false)||(t.brief?.collectionBudget?.targetReached===false),sourceIds=asArray(t.sourceIds),sourceResults=asArray(t.sourceResults),sourceTerminal=sourceIds.length>0&&sourceIds.every(function(sourceId){return sourceResults.some(function(row){return row.sourceId===sourceId&&['ok','partial','error','failed','skipped-budget'].includes(row.status)})}),sourceFailed=sourceResults.some(function(row){return ['error','failed'].includes(row.status)}),collectionComplete=sourceTerminal&&!sourceFailed,collectionTargetLabel=targetGap&&collectionComplete&&['completed','succeeded','partial'].includes(rawStatus);ui.runTitle.textContent=t.title||"装备趋势研究";ui.runQuestion.textContent=t.question||"未来 3–5 年装备发展应用趋势";ui.runStatus.textContent=collectionTargetLabel?"已完成 · 目标未满足":pair[0];ui.runStatus.className="state-pill "+(collectionTargetLabel?"warn":pair[1]);ui.refresh.disabled=false;var ready=["completed","succeeded","partial"].indexOf(rawStatus)>=0;var count=(t.items||[]).length;ui.generate.disabled=state.busy||!ready||!asArray(t.items).some(isTextEvidence);$('extract-from-execution').disabled=!state.selectedAssessmentId||state.busy;var budget=t.brief?.collectionBudget||{},partialReason=targetGap?'材料目标未满足（'+count+' / '+(budget.maxItems||t.maxItems||'—')+' 条，已循环 '+(budget.searchRounds||'—')+' 轮）':asArray(t.errors).length?'部分来源或请求失败':'';if(ready&&count>0)setSummary((targetGap?"自动采编已完成：":"采编完成：")+count+" 条材料可供研判，已保留原文摘要与来源链接。"+(partialReason?' '+partialReason+'。':''),targetGap?"warn":"success");else if(ready)setSummary("搜索完成但没有材料。请调整来源或关键词后重新搜索，暂不能生成趋势研判。","warn");else if(rawStatus==="failed")setSummary("采集失败：请检查来源或刷新任务后重试。","warn");else setSummary("Agent 正在读取允许的公开来源，完成后即可生成趋势研判。","info");renderHistory();renderMaterials();renderExecution()}
function renderMaterials(){
  var items=state.task&&state.task.items||[];ui.materialCount.textContent=items.length;
  if(!items.length){ui.materials.innerHTML=workbenchMessageMarkup('尚无材料','<p>搜索完成后，这里显示原文标题、来源、日期和摘要。先选择材料，再生成研判。</p>');ui.materialAll.disabled=true;return}
  ui.materialAll.disabled=false;var textual=items.filter(isTextEvidence),allSelected=textual.length&&textual.every(function(item){return state.selectedItems.has(item.id)});ui.materialAll.textContent=allSelected?'取消全选材料':'全选材料';
  ui.materials.innerHTML=items.map(function(item){
    var url=safeUrl(item.itemUri||item.sourceUri),checked=state.selectedItems.has(item.id),title=item.title||'未命名材料';
    return '<article class="material-row '+(checked?'selected':'')+(state.selectedMaterialId===item.id?' inspected':'')+'" data-material-id="'+esc(item.id)+'"><input type="checkbox" data-material-check="'+esc(item.id)+'" '+(!isTextEvidence(item)?'disabled title="图片线索须在影像来源中登记核验"':'')+' '+(checked?'checked':'')+' aria-label="选择材料：'+esc(title)+'">'+evidenceKindIcon(!isTextEvidence(item))+'<div class="material-title" title="'+esc(title)+'">'+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(title)+'</a>':esc(title))+'</div>'+sourceBadgeMarkup(item)+'<span class="material-source" title="'+esc(item.sourceName||item.sourceId||'公开来源')+'">'+esc(item.sourceName||item.sourceId||'公开来源')+'</span><time class="material-date">'+esc(shortTime(item.publishedAt))+'</time><button type="button" class="filter-chip material-view" aria-label="查看材料：'+esc(title)+'">查看原文</button></article>';
  }).join('');
  ui.materials.querySelectorAll('[data-material-id]').forEach(function(row){row.addEventListener('click',function(event){if(event.target.closest('a')||event.target.closest('input'))return;state.selectedMaterialId=row.dataset.materialId;renderMaterials();renderEvidence();revealEvidence()})});
  ui.materials.querySelectorAll('[data-material-check]').forEach(function(input){input.addEventListener('change',function(event){var id=event.target.dataset.materialCheck;if(event.target.checked)state.selectedItems.add(id);else state.selectedItems.delete(id);renderMaterials()})});
}
function assessmentId(a){return String(a.id||a.assessmentId||"")}
function reportFindings(a){return Array.isArray(a.summary?.findings)?a.summary.findings:[]}
function fieldText(value){
  if(Array.isArray(value))return value.map(fieldText).filter(Boolean).join("；");
  if(value&&typeof value==="object")return itemText(value)||Object.entries(value).map(function(entry){return entry[0]+"："+fieldText(entry[1])}).join("；");
  return value===undefined||value===null?"":String(value);
}
function reportMode(a){
  if(a.analysisType==="signal-summary")return "材料统计 / 证据不足";
  if(a.analysisType==="model-assessment"&&isModelDraft(a))return "DeepSeek · 模型研判草稿";
  if(a.analysisType==="model-assessment")return "模型研判草稿";
  return "分析类型未记录";
}
function selectedAssessment(){return state.assessments.find(function(a){return assessmentId(a)===String(state.selectedAssessmentId)})||null}
function assessmentEvidence(a){return Array.isArray(a.evidenceRefs)?a.evidenceRefs:[]}
function capabilityMetrics(a){
  var summary=Array.isArray(a.summary?.capabilityAssessment?.metrics)?a.summary.capabilityAssessment.metrics.filter(function(metric){return metric&&typeof metric==="object"}):[],graph=graphData(a)||{},derived=[];
  asArray(graph.nodes).forEach(function(node){if(node.type!=='EquipmentModel')return;asArray(node.payload?.attributes).forEach(function(attribute,index){var key=attribute.attributeKey||attribute.name;if(!['endurance-duration','flight-range','payload-capacity','datalink-range','mtow','service-ceiling','speed','swarm-coordination','multi-uav-coordination','autonomy-mode','navigation-resilience','sensor-suite','target-detection','tracking-capability','electronic-warfare','stealth-features'].includes(key))return;var ref=asArray(attribute.evidenceRefs)[0];derived.push({id:attribute.assertionId||node.id+'-attribute-'+index,equipment:node.label,metric:key,dimension:'装备对象属性',value:attribute.value,unit:attribute.unit||'',conditions:attribute.conditions||'',basis:attribute.claimKind||'reported-observation',evidence:{itemId:ref?.itemId,quote:ref?.quote,sourceUri:ref?.sourceUri}})})});
  var seen=new Set();return summary.concat(derived).filter(function(metric){var key=[metric.equipment,metric.metric,metric.value,metric.evidence?.itemId].join('|');if(seen.has(key))return false;seen.add(key);return true});
}
function capabilityEvidence(a,metric,index){
  var evidence=metric.evidence||{},source=assessmentEvidence(a).find(function(ref){return ref.itemId&&ref.itemId===evidence.itemId})||{};
  return Object.assign({},source,evidence,{id:metric.id||"capability-"+index,title:(metric.equipment||"未标注装备")+" · "+(metric.metric||"公开指标"),quote:evidence.quote||"",sourceUri:evidence.sourceUri||source.sourceUri});
}
function reportSectionSource(a,key,fallback){
  var s=a&&a.summary||{},sections=s.sections||{};
  var canonical=({scope:'researchTask',evidenceCoverage:'evidenceCollection',validation:'validationAndReview'})[key]||key;
  return sections[canonical]!==undefined?sections[canonical]:(sections[key]!==undefined?sections[key]:(s[key]!==undefined?s[key]:(a&&a[key]!==undefined?a[key]:fallback)));
}
function graphData(a){
  var id=assessmentId(a),loaded=state.graphByAssessment[id],fallback=reportSectionSource(a,"entityEventGraph",null)||reportSectionSource(a,"graph",null);
  return loaded&&loaded.graph?loaded.graph:(loaded&&loaded.nodes?loaded:((fallback&&fallback.graph)||fallback));
}
function validationRounds(a){
  var id=assessmentId(a),loaded=state.validationByAssessment[id],s=a&&a.summary||{},value=loaded&&loaded.rounds!==undefined?loaded.rounds:(loaded&&loaded.validationRounds!==undefined?loaded.validationRounds:(a.validationRounds||s.validationRounds||reportSectionSource(a,"validation",[])));
  return asArray(value).filter(function(v){return v&&typeof v==="object"}).sort(function(a,b){return (Number(b.roundNo)||0)-(Number(a.roundNo)||0)});
}
function ontologyAdmissions(a){
  var id=assessmentId(a),loaded=state.admissionsByAssessment[id],s=a&&a.summary||{},value=loaded&&loaded.admissions!==undefined?loaded.admissions:(loaded&&loaded.candidates!==undefined?loaded.candidates:(loaded&&loaded.proposals!==undefined?loaded.proposals:(a.ontologyAdmissions||a.ontologyAdmission||s.ontologyAdmissions||s.ontologyCandidates||reportSectionSource(a,"ontologyAdmission",[]))));
  return asArray(value).filter(function(v){return v&&typeof v==="object"});
}
function graphNodeLabel(node){return String(first(node,"label","name","title","id")||"未命名节点")}
function graphSourceOrigin(node){
  var payload=node&&node.payload||{},source=first(payload,"sourceName","publisherName","publisher","publisherDomain");
  if(source)return String(source);
  var uri=String(first(payload,"sourceUri","sourceUrl")||node&&node.iri||"").trim(),match=uri.match(/^[a-z][a-z0-9+.-]*:\/\/([^/]+)/i);
  return match?match[1]:"采编出处待记录";
}
function graphAssociationRows(a,sourceId){
  var graph=graphData(a)||{},nodes=asArray(graph.nodes),edges=asArray(graph.edges),nodeById=new Map(nodes.map(function(node){return [String(node.id),node]}));
  return edges.filter(function(edge){return String(edge.from)===String(sourceId)||String(edge.to)===String(sourceId)}).map(function(edge){
    var from=String(edge.from)===String(sourceId)?String(edge.to):String(edge.from),node=nodeById.get(from);
    return {edge:edge,node:node||{id:from,label:from,type:'Entity'},direction:String(edge.from)===String(sourceId)?'出发':'指向',relation:relationLabel(edge.relation||'relatedTo')};
  });
}
function graphAssociationMarkup(a,sourceId){
  var source=asArray(graphData(a)?.nodes).find(function(node){return String(node.id)===String(sourceId)}),rows=graphAssociationRows(a,sourceId),sourceLabel=source?graphNodeLabel(source):'来源证据';
  return '<section class="graph-association-panel" aria-label="'+esc(sourceLabel)+'的关联对象"><div class="graph-association-head"><div><p class="eyebrow">SOURCE ASSOCIATIONS</p><b>'+esc(sourceLabel)+'</b><span>关联 '+rows.length+' 项</span></div><button type="button" class="filter-chip" data-clear-graph-association>收起</button></div>'+
    (rows.length?'<ul class="graph-association-list">'+rows.map(function(row){var category=graphCategory(row.node),active=String(state.selectedGraphNodeId)===String(row.node.id)||String(state.selectedGraphEdgeId)===String(row.edge.id);return '<li><button type="button" class="graph-association-row '+(active?'active':'')+'" data-graph-association-item="'+esc(row.node.id)+'" data-association-edge="'+esc(row.edge.id)+'" data-association-source="'+esc(sourceId)+'" data-association-report="'+esc(assessmentId(a))+'"><span class="graph-association-icon graph-category-'+esc(category.id)+'" aria-hidden="true">'+esc(category.symbol)+'</span><span class="graph-association-main"><b>'+esc(graphNodeLabel(row.node))+'</b><small>'+esc(category.label)+' · '+esc(row.relation)+'</small></span><span class="graph-association-direction">'+esc(row.direction)+'</span><span class="graph-association-chevron" aria-hidden="true">›</span></button></li>';}).join('')+'</ul>':'<p class="graph-association-empty">该来源暂未建立可定位的对象、事件或趋势关联。</p>')+
    '<p class="graph-association-hint">点击关联项可在图谱中定位并高亮节点，同时查看其证据。</p></section>';
}
function graphSourceIcon(node){
  var payload=node&&node.payload&&typeof node.payload==='object'?node.payload:{},provenance=payload.provenance&&typeof payload.provenance==='object'?payload.provenance:{},nature=String(payload.contentNature||provenance.contentNature||'').toLowerCase(),role=String(payload.sourceRole||provenance.sourceRole||'').toLowerCase(),kind=String(payload.contentKind||provenance.contentKind||'').toLowerCase(),retrieval=String(payload.retrievalKind||provenance.retrievalKind||'').toLowerCase();
  if(nature.includes('image')||nature.includes('imagery')||kind.includes('image')||role.includes('imagery'))return {symbol:'▧',label:'公开影像或图片线索'};
  if(nature.includes('report')||nature.includes('evaluation')||role.includes('evaluation'))return {symbol:'▥',label:'评估报告或测试资料'};
  if(retrieval.includes('feed')||kind.includes('rss')||kind.includes('atom'))return {symbol:'◫',label:'公开订阅来源'};
  return {symbol:'▤',label:'正文来源'};
}
function graphNodeTypeMarkup(category,review,context,y,node,reportId){
  var label=category.symbol+' '+category.label+' · '+review.label+(context?' · 上下文':'');
  if(category.id==='evidence'){
    var iconY=Number(y)+9,sourceIcon=graphSourceIcon(node);
    return '<g class="graph-node-source-kind" aria-hidden="true"><text class="graph-node-source-icon" x="-103" y="'+(Number(y)+3)+'">'+esc(sourceIcon.symbol)+'</text><title>'+esc(sourceIcon.label)+'</title></g><g class="graph-node-association" data-graph-associations="'+esc(node.id)+'" data-graph-report="'+esc(reportId)+'" tabindex="0" role="button" aria-label="显示 '+esc(graphNodeLabel(node))+' 的关联对象"><title>'+esc(label+' · 点击显示关联')+'</title><circle cx="96" cy="'+iconY+'" r="8"></circle><text class="graph-node-association-glyph" x="96" y="'+(iconY+3.5)+'">↗</text></g>';
  }
  return '<text class="graph-node-type" x="-103" y="'+y+'">'+esc(label)+'</text>';
}
function graphTypeLabel(type){return ({Equipment:"装备",Technology:"技术",Organization:"组织",Program:"项目",Contract:"合同",Event:"事件",EventRecord:"事件记录",TestEvent:"测试",ProductionEvent:"生产事件",ProcurementEvent:"采购事件",CapabilityObservation:"能力观测",SourceDocument:"来源",EvidenceSource:"来源",EquipmentModel:"装备型号",TrendHypothesis:"趋势假设",TrendFinding:"趋势",ManufacturingProcess:"制造过程",DecisionAssertion:"决策断言",DecisionRecommendation:"决策断言",ManufacturingRequirement:"制造要求"})[String(type||"")]||String(type||"实体")}
const GRAPH_CATEGORIES=[
  {id:"evidence",label:"来源证据",symbol:"▤",column:0,types:["EvidenceSource","SourceDocument","Source","EvidenceDocument"]},
  {id:"organization",label:"组织",symbol:"▦",column:1,types:["Organization","Manufacturer","Supplier"]},
  {id:"equipment",label:"装备",symbol:"▣",column:1,types:["EquipmentModel","Equipment","System"]},
  {id:"technology",label:"技术",symbol:"◆",column:1,types:["Technology","ManufacturingProcess"]},
  {id:"project",label:"项目 / 合同",symbol:"⬡",column:1,types:["Program","Project","Contract"]},
  {id:"event",label:"事件",symbol:"●",column:2,types:["Event","EventRecord","TestEvent","ProcurementEvent","ProductionEvent"]},
  {id:"observation",label:"能力观测",symbol:"◉",column:2,types:["CapabilityObservation","CapabilityMetric","EvidenceObservation"]},
  {id:"trend",label:"趋势结论",symbol:"◇",column:3,types:["TrendHypothesis","TrendFinding","TrendConclusion","Finding"]},
  {id:"decision",label:"决策断言",symbol:"➜",column:3,types:["DecisionAssertion","DecisionRecommendation","DecisionDraft","ManufacturingDecision"]},
  {id:"requirement",label:"制造要求",symbol:"⚙",column:3,types:["ManufacturingRequirement"]},
  {id:"other",label:"其他实体",symbol:"□",column:1,types:[]}
];
function graphCategory(node){return GRAPH_CATEGORIES.find(function(category){return category.types.includes(String(node.type))})||GRAPH_CATEGORIES[GRAPH_CATEGORIES.length-1]}
function graphReviewStatus(a,node){var candidates=ontologyAdmissions(a).filter(function(c){return !c.staleGraph&&!c.supersededByAdmissionId&&c.graphElementId===node.id}),identity=candidates.find(function(c){return c.kind==='graph-node'}),status=identity?.status;if(status==='published')return {id:'published',label:'身份已入库'};if(status==='accepted')return {id:'accepted',label:'身份已审核'};if(status==='rejected')return {id:'rejected',label:'身份已退回'};if(status==='needs-evidence')return {id:'gap',label:'待补证'};if(node.payload?.extractionMethod==='llm'){var extraction=node.payload.attributeExtraction,summary=node.type==='EquipmentModel'?objectAttributeSummary(node):null;if(summary?.groundedCount)return {id:'pending',label:'属性已保存 · 待审核'};if(extraction&&['no-supported-attributes','no-explicit-attributes'].includes(extraction.status))return {id:'gap',label:'来源未披露明确属性'};if(summary?.rawCount)return {id:'gap',label:'属性草案待核'};if(extraction&&!['completed','attribute-limit'].includes(extraction.status))return {id:extraction.status==='failed'?'failed':'gap',label:extraction.status==='failed'?'属性提取失败':'属性待补证'};return {id:'pending',label:'待人工审核'}}return {id:'context',label:graphCategory(node).id==='trend'?'研判草稿':'引用上下文'}}
function graphView(a){
  var graph=graphData(a),id=assessmentId(a),filters=state.graphFilters[id]||{},selected=filters.type||"all",query=String(filters.query||"").trim().toLowerCase(),allNodes=graph&&Array.isArray(graph.nodes)?graph.nodes:[],allEdges=graph&&Array.isArray(graph.edges)?graph.edges:[];
  var relation=filters.relation||'all',relationEdges=allEdges.filter(function(edge){return relation==='all'||edge.relation===relation}),relationNodeIds=new Set(relationEdges.flatMap(function(edge){return [String(edge.from),String(edge.to)]}));
  var direct=allNodes.filter(function(node){return(selected==="all"||graphCategory(node).id===selected||String(node.type)===selected)&&(!query||graphNodeLabel(node).toLowerCase().includes(query))&&(relation==='all'||relationNodeIds.has(String(node.id)))&&(!filters.reviewStatus||filters.reviewStatus==='all'||graphReviewStatus(a,node).id===filters.reviewStatus)}),directIds=new Set(direct.map(function(node){return String(node.id)})),visibleIds=new Set(directIds),filtered=selected!=="all"||!!query||relation!=='all'||!!filters.reviewStatus&&filters.reviewStatus!=='all';
  if(filtered)relationEdges.forEach(function(edge){if(directIds.has(String(edge.from))||directIds.has(String(edge.to))){visibleIds.add(String(edge.from));visibleIds.add(String(edge.to))}});
  var matching=allNodes.filter(function(node){return visibleIds.has(String(node.id))}),ordered=filtered?direct.concat(matching.filter(function(node){return !directIds.has(String(node.id))})):matching;
  // The full evidence projection can be much larger than the readable canvas.
  // Keep the semantic core (organizations, equipment, technologies, events
  // and capability observations) ahead of decision/manufacturing detail so a
  // clipped default view never hides the actual objects being analyzed.
  if(!filtered){var priority={organization:0,equipment:1,technology:2,project:3,event:4,observation:5,trend:6,decision:7,requirement:8,evidence:9,other:10};ordered=ordered.map(function(node,index){return {node:node,index:index,rank:priority[graphCategory(node).id]??10}}).sort(function(a,b){return a.rank-b.rank||a.index-b.index}).map(function(row){return row.node})}
  var nodes=ordered.slice(0,60),nodeIds=new Set(nodes.map(function(node){return String(node.id)})),matchingEdges=relationEdges.filter(function(edge){return visibleIds.has(String(edge.from))&&visibleIds.has(String(edge.to))}),renderableEdges=matchingEdges.filter(function(edge){return nodeIds.has(String(edge.from))&&nodeIds.has(String(edge.to))}),edges=renderableEdges.slice(0,120);
  return {assessment:a,graph:graph,id:id,filters:filters,selected:selected,query:query,allNodes:allNodes,allEdges:allEdges,directIds:directIds,filtered:filtered,matching:matching,nodes:nodes,edges:edges,matchingEdges:matchingEdges,clipped:matching.length>nodes.length||matchingEdges.length>edges.length};
}
function graphViewport(id){
  var current=state.graphView[id]||{};
  return {scale:Math.max(.65,Math.min(1.8,Number(current.scale)||1)),panX:Number.isFinite(Number(current.panX))?Number(current.panX):0,panY:Number.isFinite(Number(current.panY))?Number(current.panY):0};
}
function setGraphZoom(id,delta){
  var current=graphViewport(id),reset=delta==='reset',next=reset?1:Math.max(.65,Math.min(1.8,current.scale+Number(delta||0)));
  state.graphView[id]={scale:Math.round(next*100)/100,panX:reset?0:current.panX,panY:reset?0:current.panY};
}
function graphViewBoxMarkup(id,width,height){
  var view=graphViewport(id);
  return [view.panX,view.panY,width/view.scale,height/view.scale].map(function(value){return Number(value).toFixed(2)}).join(' ');
}
function updateGraphCanvasView(canvas){
  if(!canvas)return;
  var svg=canvas.querySelector('svg');if(!svg)return;
  var width=Number(svg.dataset.graphWidth)||1080,height=Number(svg.dataset.graphHeight)||270;
  svg.setAttribute('viewBox',graphViewBoxMarkup(canvas.dataset.graphCanvas,width,height));
  var view=graphViewport(canvas.dataset.graphCanvas);
  canvas.dataset.graphPanX=String(view.panX);canvas.dataset.graphPanY=String(view.panY);
}
function graphPanBlockedTarget(target){
  return !!(target&&target.closest('[data-graph-node],[data-graph-edge],[data-graph-associations],button,a,input,select,textarea,label,summary'));
}
function handleGraphPointerDown(event){
  var canvas=event.target.closest('[data-graph-canvas]');
  if(!canvas||graphPanBlockedTarget(event.target)||(event.button!==undefined&&event.button!==0))return;
  var id=canvas.dataset.graphCanvas,view=graphViewport(id);
  state.graphPanGesture={id:id,canvas:canvas,pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,panX:view.panX,panY:view.panY,scale:view.scale,moved:false};
  canvas.classList.add('is-panning');
  if(canvas.setPointerCapture&&event.pointerId!==undefined){try{canvas.setPointerCapture(event.pointerId)}catch(error){}}
  event.preventDefault();
}
function handleGraphPointerMove(event){
  var gesture=state.graphPanGesture;if(!gesture||gesture.pointerId!==undefined&&event.pointerId!==gesture.pointerId)return;
  var svg=gesture.canvas.querySelector('svg'),width=Number(svg?.dataset.graphWidth)||1080,height=Number(svg?.dataset.graphHeight)||270,rect=svg?.getBoundingClientRect(),clientWidth=rect?.width||gesture.canvas.clientWidth||width,clientHeight=rect?.height||gesture.canvas.clientHeight||height,viewWidth=width/gesture.scale,viewHeight=height/gesture.scale;
  var dx=(event.clientX-gesture.startX)*(viewWidth/clientWidth),dy=(event.clientY-gesture.startY)*(viewHeight/clientHeight);
  if(Math.abs(dx)+Math.abs(dy)>2)gesture.moved=true;
  state.graphView[gesture.id]={scale:gesture.scale,panX:gesture.panX-dx,panY:gesture.panY-dy};
  updateGraphCanvasView(gesture.canvas);event.preventDefault();
}
function handleGraphPointerUp(event){
  var gesture=state.graphPanGesture;if(!gesture||gesture.pointerId!==undefined&&event.pointerId!==gesture.pointerId)return;
  gesture.canvas.classList.remove('is-panning');
  if(gesture.canvas.releasePointerCapture&&event.pointerId!==undefined){try{gesture.canvas.releasePointerCapture(event.pointerId)}catch(error){}}
  if(gesture.moved){state.graphSuppressClick=true;setTimeout(function(){state.graphSuppressClick=false},0)}
  state.graphPanGesture=null;
}
function graphNodeLayout(category,node){
  var hasLine2=Array.from(graphNodeLabel(node)).length>19;
  if(category.id==='equipment'){
    var height=hasLine2?64:50,typeY=-height/2+13,labelY=typeY+14,line2Y=labelY+13;
    return {height:height,typeY:typeY,labelY:labelY,line2Y:line2Y,metaY:(hasLine2?line2Y:labelY)+13};
  }
  var source=category.id==='evidence',labelY=source?-7:2,line2Y=source?7:17;
  return {height:58,typeY:source?-24:-15,labelY:labelY,line2Y:line2Y,metaY:hasLine2?line2Y+12:labelY+13};
}
function graphCanvasMarkup(view,expanded){
  if(!view.nodes.length)return workbenchMessageMarkup('筛选无节点','<p>当前筛选没有匹配节点，请调整对象类别、关系类型或名称条件。</p>');
  var markerId="graph-direction-"+encodeURIComponent(view.id)+(expanded?"-expanded":"-inline"),colWidth=264,width=1080,padding=12,columnY=[64,64,64,64],positions={},groups=[],activeNode=String(state.selectedGraphNodeId||""),activeEdge=String(state.selectedGraphEdgeId||""),nodeGroup={},zoom=graphViewport(view.id),scaledWidth=Math.round(width*zoom.scale),scaledHeight=Math.round(Math.max(270,...columnY)*zoom.scale);
  GRAPH_CATEGORIES.forEach(function(category){var nodes=view.nodes.filter(function(node){return graphCategory(node).id===category.id});if(!nodes.length)return;var equipment=category.id==='equipment',nodeGap=category.id==='evidence'?70:78,x=padding+category.column*colWidth,y=columnY[category.column],offset=38,height=equipment?38+nodes.reduce(function(total,node){return total+graphNodeLayout(category,node).height+10},0)+2:42+nodes.length*nodeGap+10;groups.push({category:category,x:x,y:y,height:height,count:nodes.length});nodes.forEach(function(node,index){var nodeHeight=graphNodeLayout(category,node).height;positions[String(node.id)]={x:x+colWidth/2-6,y:equipment?y+offset+nodeHeight/2:y+70+index*nodeGap};if(equipment)offset+=nodeHeight+10;nodeGroup[String(node.id)]=category});columnY[category.column]+=height+14;});
  var height=Math.max(270,...columnY)+12;scaledHeight=Math.round(height*zoom.scale);var columns=[['资料层','来源与原文'],['对象层','装备、组织、技术与项目'],['观测层','事件及附条件的能力'],['研判层','模型分析与趋势假设']];
  var columnsMarkup=columns.map(function(column,index){return '<g class="graph-stage '+(index===3?'graph-stage-inference':'')+'"><rect x="'+(padding+index*colWidth)+'" y="8" width="252" height="44" rx="5"></rect><text x="'+(padding+index*colWidth+12)+'" y="27" class="graph-stage-title">'+esc(column[0])+'</text><text x="'+(padding+index*colWidth+12)+'" y="43" class="graph-stage-description">'+esc(column[1])+'</text></g>'}).join('');
  var groupsMarkup=groups.map(function(group){var c=group.category;return '<g class="graph-category-region graph-category-'+c.id+'"><rect x="'+group.x+'" y="'+group.y+'" width="252" height="'+group.height+'" rx="7"></rect><text x="'+(group.x+13)+'" y="'+(group.y+24)+'" class="graph-category-heading">'+esc(c.symbol+' '+c.label)+' · '+group.count+'</text></g>'}).join('');
  var edgeMarkup=view.edges.map(function(edge){var p=positions[String(edge.from)],q=positions[String(edge.to)],active=activeEdge===String(edge.id)||activeNode===String(edge.from)||activeNode===String(edge.to),context=view.filtered&&!view.directIds.has(String(edge.from))&&!view.directIds.has(String(edge.to)),sameColumn=Math.abs(p.x-q.x)<1,startX=p.x+(sameColumn?114:(q.x>p.x?114:-114)),endX=q.x+(sameColumn?114:(q.x>p.x?-114:114)),bend=sameColumn?Math.max(startX,endX)+18+(view.edges.indexOf(edge)%3)*4:(startX+endX)/2,d='M '+startX+' '+p.y+' C '+bend+' '+p.y+', '+bend+' '+q.y+', '+endX+' '+q.y;return '<g class="graph-edge '+(active?'active ':'')+(context?'graph-context':'')+'" tabindex="0" role="button" aria-label="'+esc(edge.relation||"关联")+'" data-graph-edge="'+esc(edge.id)+'" data-graph-report="'+esc(view.id)+'"><path class="graph-edge-hit" d="'+d+'"></path><path class="graph-edge-line" d="'+d+'" marker-end="url(#'+esc(markerId)+')"></path><title>'+esc(edge.relation)+' · 点击查看引用</title></g>'}).join('');
  var nodeMarkup=view.nodes.map(function(node){var p=positions[String(node.id)],category=nodeGroup[String(node.id)],review=graphReviewStatus(view.assessment,node),active=activeNode===String(node.id),context=view.filtered&&!view.directIds.has(String(node.id)),label=graphNodeLabel(node),chars=Array.from(label),line1=chars.slice(0,19).join(''),line2=chars.slice(19,38).join('')+(chars.length>38?'…':''),hasLine2=!!line2,summary=category.id==='equipment'?objectAttributeSummary(node):null,origin=category.id==='evidence'?graphSourceOrigin(node):'',layout=graphNodeLayout(category,node),nodeHeight=layout.height,typeY=layout.typeY,labelY=layout.labelY,line2Y=layout.line2Y,metaY=layout.metaY,textX=category.id==='evidence'?-84:-103,shape=category.id==='trend'?'<path class="graph-node-shape" d="M -107 -29 L 101 -29 L 114 -16 L 114 29 L -114 29 L -114 -16 Z"></path>':category.id==='event'?'<rect class="graph-node-shape" x="-114" y="-29" width="228" height="58" rx="22"></rect>':'<rect class="graph-node-shape" x="-114" y="'+(-nodeHeight/2)+'" width="228" height="'+nodeHeight+'" rx="'+(category.id==='evidence'?2:7)+'"></rect>',meta=summary?'<text class="graph-node-meta graph-attribute-state-'+summary.state+'" x="'+textX+'" y="'+metaY+'">'+esc(summary.rawCount?'属性 '+summary.rawCount+' · 能力 '+summary.capabilityCount+' · '+summary.label:(summary.label+(summary.sourceRefCount?' · 来源 '+summary.sourceRefCount:'')))+'</text>':origin?'<text class="graph-node-meta" x="'+textX+'" y="'+metaY+'">采编出处 · '+esc(origin)+'</text>':'';return '<g class="graph-node graph-category-'+category.id+' graph-review-'+review.id+' '+(active?'active ':'')+(context?'graph-context':'')+'" tabindex="0" role="button" aria-label="'+esc(category.label+'：'+label+(origin?'，采编出处 '+origin:'')+(summary?'，'+summary.label+'，属性'+summary.rawCount+'项':'')+(context?'，关联上下文':''))+'" data-graph-node="'+esc(node.id)+'" data-graph-report="'+esc(view.id)+'" transform="translate('+p.x+','+p.y+')">'+shape+graphNodeTypeMarkup(category,review,context,typeY,node,view.id)+'<text class="graph-node-label" x="'+textX+'" y="'+labelY+'">'+esc(line1)+'</text>'+(hasLine2?'<text class="graph-node-label" x="'+textX+'" y="'+line2Y+'">'+esc(line2)+'</text>':'')+meta+'<title>'+esc(label+(origin?' · 采编出处 '+origin:'')+(summary?' · '+summary.label+' · 属性'+summary.rawCount+'项':''))+'</title></g>'}).join('');
  return '<div class="graph-canvas graph-semantic-canvas" data-graph-canvas="'+esc(view.id)+'" data-graph-scale="'+zoom.scale+'" data-graph-pan-x="'+zoom.panX+'" data-graph-pan-y="'+zoom.panY+'"><svg width="'+scaledWidth+'" height="'+scaledHeight+'" data-graph-width="'+width+'" data-graph-height="'+height+'" viewBox="'+graphViewBoxMarkup(view.id,width,height)+'" role="group" aria-label="按资料、对象、观测、研判分区的证据图谱"><defs><marker id="'+esc(markerId)+'" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M 0 0 L 10 5 L 0 10 Z" fill="#607e70"></path></marker></defs><g class="graph-viewport">'+columnsMarkup+groupsMarkup+'<g class="graph-edges">'+edgeMarkup+'</g><g class="graph-nodes">'+nodeMarkup+'</g></g></svg></div>';
}

function graphMarkup(a,expanded){
  var view=graphView(a),id=view.id,busy=reportBusy(id),heading='<div class="report-section-heading"><div><p class="eyebrow">03 / EVIDENCE RELATION GRAPH</p><h4>实体与证据关系图谱</h4></div>';
  if(!view.allNodes.length)return '<div class="graph-toolbar">'+augmentationMessagesMarkup(a,['graph'])+workbenchMessageMarkup(state.augmentationErrors?.[id]?.graph?'图谱尚未读取':'图谱待构建','<p>'+(state.augmentationErrors?.[id]?.graph?'本次图谱读取失败，请先重试读取，尚不能判断是否已有图谱。':'根据当前报告的装备型号、能力观测、趋势假设及来源建立引用关系。')+'</p>')+'<button type="button" class="filter-chip" data-build-graph="'+esc(id)+'" '+(busy?'disabled':'')+'>'+(busy?'正在读取…':'构建证据图谱')+'</button><button type="button" class="filter-chip" data-extract-graph="'+esc(id)+'" '+(busy?'disabled':'')+'>启动对象与属性提取</button></div>';
  var legend='<div class="graph-category-legend" aria-label="按类别筛选"><button type="button" class="graph-category-chip '+(view.selected==='all'?'active':'')+'" data-graph-category="all" data-graph-report="'+esc(id)+'" aria-pressed="'+(view.selected==='all')+'">全部 <b>'+view.allNodes.length+'</b></button>'+GRAPH_CATEGORIES.map(function(category){var count=view.allNodes.filter(function(node){return graphCategory(node).id===category.id}).length;if(category.id==='other'&&!count)return'';return '<button type="button" class="graph-category-chip graph-category-'+category.id+' '+(view.selected===category.id?'active':'')+'" data-graph-category="'+category.id+'" data-graph-report="'+esc(id)+'" aria-pressed="'+(view.selected===category.id)+'" '+(!count?'disabled':'')+'><span>'+esc(category.symbol)+'</span>'+esc(category.label)+' <b>'+count+'</b></button>'}).join('')+'</div>';
  var zoom=graphViewport(id),controls='<div class="graph-toolbar"><label>审核状态<select data-graph-review-status="'+esc(id)+'">'+[['all','全部状态'],['pending','待人工审核'],['gap','待补证'],['accepted','身份已审核'],['published','身份已入库'],['rejected','已退回'],['failed','属性提取失败'],['context','引用上下文']].map(function(pair){return '<option value="'+pair[0]+'" '+((view.filters.reviewStatus||'all')===pair[0]?'selected':'')+'>'+pair[1]+'</option>'}).join('')+'</select></label><label>关系类型<select data-graph-relation="'+esc(id)+'"><option value="all">全部关系</option>'+Array.from(new Set(view.allEdges.map(function(edge){return edge.relation}).filter(Boolean))).map(function(relation){return '<option value="'+esc(relation)+'" '+((view.filters.relation||'all')===relation?'selected':'')+'>'+esc(relationLabel(relation))+'</option>'}).join('')+'</select></label><label>查找 <input data-graph-query="'+esc(id)+'" value="'+esc(view.filters.query||'')+'" placeholder="名称，回车筛选"></label><span class="graph-zoom-controls" aria-label="图谱缩放"><button type="button" class="filter-chip" data-graph-zoom="-0.15" data-graph-report="'+esc(id)+'" aria-label="缩小图谱">−</button><b data-graph-zoom-value="'+esc(id)+'">'+Math.round(zoom.scale*100)+'%</b><button type="button" class="filter-chip" data-graph-zoom="0.15" data-graph-report="'+esc(id)+'" aria-label="放大图谱">＋</button><button type="button" class="filter-chip" data-graph-zoom="reset" data-graph-report="'+esc(id)+'">重置视图</button></span><button type="button" class="filter-chip" data-build-graph="'+esc(id)+'" '+(busy?'disabled':'')+'>'+(busy?'构建中…':'重建证据图谱')+'</button><button type="button" class="filter-chip" data-extract-graph="'+esc(id)+'" '+(busy?'disabled':'')+'>重新提取对象与属性</button><button type="button" class="filter-chip" data-refresh-graph="'+esc(id)+'" '+(busy?'disabled':'')+'>刷新</button>'+(expanded?'':'<button type="button" class="filter-chip graph-expand-button" data-expand-graph="'+esc(id)+'">展开图谱 ↗</button>')+'</div>';
  var equipmentNodes=view.allNodes.filter(function(node){return node.type==='EquipmentModel'}),equipmentSaved=equipmentNodes.filter(function(node){return objectAttributeSummary(node).groundedCount>0}).length,equipmentDraft=equipmentNodes.filter(function(node){var summary=objectAttributeSummary(node);return summary.rawCount>0&&!summary.groundedCount}).length,equipmentNoAttributes=equipmentNodes.filter(function(node){return objectAttributeSummary(node).rawCount===0}).length;
  var coreCounts={objects:view.allNodes.filter(function(node){return ['Organization','EquipmentModel','Technology','Program','Project','Contract'].includes(String(node.type))}).length,observations:view.allNodes.filter(function(node){return ['CapabilityObservation','CapabilityMetric','EvidenceObservation','Event','EventRecord','TestEvent','ProcurementEvent','ProductionEvent'].includes(String(node.type))}).length};
  var coverage='<p class="graph-coverage">显示 '+view.nodes.length+' / '+view.allNodes.length+' 节点，'+view.edges.length+' / '+view.allEdges.length+' 关系 · 对象层 '+coreCounts.objects+' · 观测层 '+coreCounts.observations+' · 装备属性：已保存 '+equipmentSaved+'，草案待核 '+equipmentDraft+'，未提取 '+equipmentNoAttributes+' · 修订 '+esc(view.graph.revision||'—')+(view.filtered?' · 直接匹配 '+view.directIds.size+' 个，淡色节点为相邻上下文':'')+(view.clipped?' · 已截断：默认优先显示对象与观测，其他节点请按类别筛选':'')+'</p>';
  return '<section class="report-section graph-section graph-semantic-section">'+heading+'<span class="section-status approved">'+view.allNodes.length+' 节点 · '+view.allEdges.length+' 关系</span>'+augmentationMessagesMarkup(a,['graph'])+workbenchMessageMarkup('图谱说明','<p>按类别分区：来源证据 → 抽取对象 → 事件与能力观测 → 趋势结论。研判与实体分开呈现；关联不代表因果。</p><p>来源证据节点右上角的 ↗ 图标用于查看该来源直接关联的对象、事件和趋势；点击关联项会回定位并高亮目标节点。</p><p>空白处按住拖拽可自由平移，滚轮缩放；点击节点或连线查看原文，Tab 聚焦后按 Enter / 空格打开证据。</p>')+'</div>'+legend+controls+coverage+graphCanvasMarkup(view,expanded)+'</section>';
}
function renderGraphOverlay(){
  var dialog=document.getElementById('osint-graph-dialog');if(!dialog||!dialog.open)return;var id=dialog.dataset.graphReport,a=state.assessments.find(function(item){return assessmentId(item)===id});if(!a)return;
  var graph=graphData(a),target=graph&&((graph.nodes||[]).find(function(node){return String(node.id)===String(state.selectedGraphNodeId)})||(graph.edges||[]).find(function(edge){return String(edge.id)===String(state.selectedGraphEdgeId)})),content=dialog.querySelector('.graph-overlay-view'),previous=content.querySelector('.graph-canvas'),left=previous?previous.scrollLeft:0,top=previous?previous.scrollTop:0;
  content.innerHTML=graphMarkup(a,true);var canvas=content.querySelector('.graph-canvas');if(canvas){canvas.scrollLeft=left;canvas.scrollTop=top}
  var association=state.selectedGraphAssociationId?graphAssociationMarkup(a,state.selectedGraphAssociationId):'',detail=target&&(!state.selectedGraphAssociationId||String(target.id)!==String(state.selectedGraphAssociationId))?graphEvidenceMarkup(a,target).replace('id="clear-evidence-filter"','id="overlay-clear-evidence-filter"'):'';
  dialog.querySelector('.graph-overlay-evidence').innerHTML=association+detail+(association||detail?'':'<div class="placeholder">选择节点或连线，在这里展开原文与来源。</div>');
}
function openGraphOverlay(id){
  var dialog=document.getElementById('osint-graph-dialog');if(!dialog){dialog=document.createElement('dialog');dialog.id='osint-graph-dialog';dialog.className='graph-overlay';dialog.setAttribute('aria-label','按类别查看证据关系图谱');dialog.innerHTML='<div class="graph-overlay-header"><div><b>图谱洞察</b><span>类别分区 · 证据溯源</span></div><button type="button" class="filter-chip" data-close-graph>收起图谱 ×</button></div><div class="graph-overlay-body"><div class="graph-overlay-view"></div><aside class="graph-overlay-evidence" aria-label="图谱证据详情"></aside></div>';document.body.append(dialog);dialog.addEventListener('click',function(event){if(handleInspectorPropertyInteraction(event))return;if(event.target.closest('[data-close-graph]')){dialog.close();return}if(event.target.closest('#overlay-clear-evidence-filter')){state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;renderGraphOverlay();renderEvidence();return}handleGraphInteraction(event)});dialog.addEventListener('wheel',handleGraphWheel,{passive:false});dialog.addEventListener('change',handleGraphFilterChange);dialog.addEventListener('keydown',handleGraphKeydown);dialog.addEventListener('close',function(){var trigger=Array.from(ui.assessments.querySelectorAll('[data-expand-graph]')).find(function(button){return button.dataset.expandGraph===dialog.dataset.graphReport});if(trigger)trigger.focus()})}
  dialog.dataset.graphReport=id;dialog.showModal();renderGraphOverlay();dialog.querySelector('[data-close-graph]').focus();
}
function renderGraphInteraction(){var top=ui.assessments.scrollTop;renderAssessments();ui.assessments.scrollTop=top;renderGraphOverlay()}
function handleGraphInteraction(event){
  if(state.graphSuppressClick){state.graphSuppressClick=false;return true}
  var association=event.target.closest('[data-graph-associations]');if(association){var associationReport=association.dataset.graphReport,sourceId=association.dataset.graphAssociations,changed=String(state.selectedAssessmentId)!==String(associationReport);state.selectedAssessmentId=associationReport;state.selectedGraphAssociationId=sourceId;state.selectedGraphNodeId=sourceId;state.selectedGraphEdgeId=null;state.selectedGraphProperty=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null;updateDeepLink();renderGraphInteraction();if(changed)renderReview();renderEvidence();if(!association.closest('dialog'))revealEvidence();return true}
  var associationItem=event.target.closest('[data-graph-association-item]');if(associationItem){var itemReport=associationItem.dataset.associationReport,source=associationItem.dataset.associationSource,nodeId=associationItem.dataset.graphAssociationItem,edgeId=associationItem.dataset.associationEdge,changedItem=String(state.selectedAssessmentId)!==String(itemReport);state.selectedAssessmentId=itemReport;state.selectedGraphAssociationId=source;state.selectedGraphNodeId=nodeId;state.selectedGraphEdgeId=edgeId;state.selectedGraphProperty=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null;updateDeepLink();renderGraphInteraction();if(changedItem)renderReview();renderEvidence();if(!associationItem.closest('dialog'))revealEvidence();return true}
  if(event.target.closest('[data-clear-graph-association]')){state.selectedGraphAssociationId=null;state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;renderGraphInteraction();renderEvidence();return true}
  var zoomButton=event.target.closest('[data-graph-zoom]');if(zoomButton){var zoomId=zoomButton.dataset.graphReport;setGraphZoom(zoomId,zoomButton.dataset.graphZoom);var inOverlay=!!zoomButton.closest('dialog');renderGraphInteraction();var surface=inOverlay?document.getElementById('osint-graph-dialog'):ui.assessments,focus=surface&&surface.querySelector('[data-graph-zoom="'+zoomButton.dataset.graphZoom+'"]');if(focus)focus.focus({preventScroll:true});return true}
  var retryAugmentation=event.target.closest('[data-retry-augmentation]');if(retryAugmentation){state.augmentationLoaded=state.augmentationLoaded||{};delete state.augmentationLoaded[retryAugmentation.dataset.retryAugmentation];loadReportAugmentations(retryAugmentation.dataset.retryAugmentation).finally(renderGraphOverlay);return true}
  var category=event.target.closest('[data-graph-category]');if(category){var inOverlay=!!category.closest('dialog'),categoryId=category.dataset.graphCategory,id=category.dataset.graphReport;state.graphFilters[id]=state.graphFilters[id]||{};state.graphFilters[id].type=categoryId;renderGraphInteraction();var surface=inOverlay?document.getElementById('osint-graph-dialog'):ui.assessments,button=Array.from(surface.querySelectorAll('[data-graph-category]')).find(function(item){return item.dataset.graphCategory===categoryId&&item.dataset.graphReport===id});if(button)button.focus();return true}
  var expand=event.target.closest('[data-expand-graph]');if(expand){openGraphOverlay(expand.dataset.expandGraph);return true}
  var target=event.target.closest('[data-graph-node],[data-graph-edge]');if(target){var inOverlay=!!target.closest('dialog'),nodeId=target.dataset.graphNode,edgeId=target.dataset.graphEdge,changed=String(state.selectedAssessmentId)!==target.dataset.graphReport;state.selectedAssessmentId=target.dataset.graphReport;state.chartEvidence=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;state.selectedGraphNodeId=target.dataset.graphNode||null;state.selectedGraphEdgeId=target.dataset.graphEdge||null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;updateDeepLink();renderGraphInteraction();if(changed)renderReview();renderEvidence();if(!inOverlay)revealEvidence();var surface=inOverlay?document.getElementById('osint-graph-dialog'):ui.assessments,focusTarget=Array.from(surface.querySelectorAll(nodeId?'[data-graph-node]':'[data-graph-edge]')).find(function(item){return nodeId?item.dataset.graphNode===nodeId:item.dataset.graphEdge===edgeId});if(focusTarget)focusTarget.focus({preventScroll:true});return true}
  var attributes=event.target.closest('[data-extract-attributes]');if(attributes){var attributeReport=state.assessments.find(function(a){return assessmentId(a)===attributes.dataset.extractAttributes}),pending=attributeReport?pendingAttributeNodes(attributeReport):[];if(pending.length)startExtractionJob(attributes.dataset.extractAttributes,{extractAttributes:true,objectIds:pending.slice(0,8).map(function(node){return node.id})});else notify('当前对象均已尝试提取。缺少原文支持的属性需要补证。','warn');return true}
  var build=event.target.closest('[data-build-graph],[data-extract-graph]');if(build){(build.dataset.extractGraph?startExtractionJob(build.dataset.extractGraph,{extractEntities:true}):buildReportGraph(build.dataset.buildGraph,false));return true}
  var refresh=event.target.closest('[data-refresh-graph]');if(refresh){state.augmentationLoaded=state.augmentationLoaded||{};delete state.augmentationLoaded[refresh.dataset.refreshGraph];loadReportAugmentations(refresh.dataset.refreshGraph).finally(renderGraphOverlay);return true}return false;
}
function handleGraphWheel(event){
  var canvas=event.target.closest('[data-graph-canvas]');if(!canvas)return;
  event.preventDefault();setGraphZoom(canvas.dataset.graphCanvas,event.deltaY>0?-0.08:0.08);renderGraphInteraction();
}
function handleGraphFilterChange(event){var type=event.target.closest('[data-graph-type]'),query=event.target.closest('[data-graph-query]'),review=event.target.closest('[data-graph-review-status]'),relation=event.target.closest('[data-graph-relation]'),id=type?type.dataset.graphType:query?query.dataset.graphQuery:review?review.dataset.graphReviewStatus:relation?relation.dataset.graphRelation:null;if(!id)return;state.graphFilters[id]=state.graphFilters[id]||{};state.graphFilters[id][type?'type':review?'reviewStatus':relation?'relation':'query']=event.target.value;renderGraphInteraction()}
function handleGraphKeydown(event){if(event.key!=='Enter'&&event.key!==' ')return;var target=event.target.closest('[data-graph-node],[data-graph-edge],[data-graph-associations],[data-graph-association-item],[data-clear-graph-association]');if(target){event.preventDefault();handleGraphInteraction(event)}else if(event.key==='Enter'&&event.target.matches('[data-graph-query]')){event.preventDefault();handleGraphFilterChange(event)}}
function reportScopeMarkup(a){
  var scope=reportSectionSource(a,"scope",{}),stats=(a.summary||{}).statistics||{},coverage=reportSectionSource(a,"evidenceCoverage",{}),round=validationRounds(a)[0]||{},factors=round.confidenceFactors||{};
  if(typeof scope!=="object"||Array.isArray(scope))scope={summary:scope};
  var task=state.task&&(!a.taskId||a.taskId===state.task.id)?state.task:null,windowText=fieldText(first(scope,"timeWindow","window","since"))||(task&&task.sinceDays?'采集过滤最近 '+task.sinceDays+' 天（不是完整历史）':'未记录'),horizonText=fieldText(first(scope,"horizon"))||((scope.horizonYears??a.horizonYears)?'未来 '+(scope.horizonYears??a.horizonYears)+' 年':'未记录'),independenceVerified=coverage.independenceVerified===true||factors.independenceVerified===true,independentCount=coverage.independenceVerified===true?coverage.independentPublisherCount:factors.independentPublisherCount;
  return workbenchMessageMarkup('研究范围','<dl class="section-grid"><div><dt>研究问题</dt><dd>'+esc(fieldText(first(scope,"question","researchQuestion"))||a.question||"未记录")+'</dd></div><div><dt>时间窗</dt><dd>'+esc(windowText)+'</dd></div><div><dt>展望</dt><dd>'+esc(horizonText)+'</dd></div><div><dt>范围说明</dt><dd>'+esc(fieldText(first(scope,"summary","scope","description"))||"仅限公开材料")+'</dd></div></dl><div class="section-stats"><span><b>'+esc(stats.selectedItems??stats.items??0)+'</b> 选定材料</span><span><b>'+esc(stats.citedItems??0)+'</b> 被引用</span><span><b>'+esc(stats.sourceCount??0)+'</b> 出版域名</span><span><b>'+esc(independenceVerified&&independentCount!==undefined?independentCount:'未核实')+'</b> 独立来源</span></div>');
}
function evidenceCoverageMarkup(a){
  var summary=a.summary||{},coverage=reportSectionSource(a,"evidenceCoverage",{});if(typeof coverage!=="object"||Array.isArray(coverage))coverage={summary:coverage};var gaps=first(coverage,"gaps","evidenceGaps")||summary.gaps,publisherCount=coverage.publisherCount??coverage.distinctPublisherCount??summary.statistics?.sourceCount;
  var composition={};asArray(a.sourceSnapshot).forEach(function(item){var role=sourceRoleLabel(sourceProvenance(item).sourceRole);composition[role]=(composition[role]||0)+1});var compositionHtml='<div class="source-composition">'+Object.keys(composition).map(function(role){return '<span>'+esc(role)+' <b>'+composition[role]+'</b></span>'}).join('')+'</div>';
  return workbenchMessageMarkup('证据覆盖'+(asArray(gaps).length?' · 需补证':''),'<dl class="section-grid"><div><dt>覆盖说明</dt><dd>'+esc(fieldText(first(coverage,"summary","coverage","description"))||"保留原文、发布日期、来源版本和引用区间。")+'</dd></div><div><dt>出版域数</dt><dd>'+esc(publisherCount??"未记录")+'</dd></div><div><dt>时间窗覆盖</dt><dd>'+esc(fieldText(first(coverage,"timeWindowCoverage","timeWindows"))||"未记录")+'</dd></div><div><dt>反证检查</dt><dd>'+esc(fieldText(first(coverage,"contraryEvidenceChecked","counterEvidenceChecked"))||"未记录")+'</dd></div></dl>'+compositionHtml+'<p class="section-note">来源类别反映采编记录；预算需求、官方声明与测试结果需分别核查。旧材料缺少类别时保留未分类。</p><div class="section-gap-list"><b>待补证</b>'+listMarkup(gaps,"暂未记录额外证据缺口。")+'</div>',asArray(gaps).length?'warning':'info');
}
function manufacturingMarkup(a){
  var m=reportSectionSource(a,"manufacturingImplications",null)||reportSectionSource(a,"manufacturingDecisionSupport",null)||reportSectionSource(a,"manufacturing",null)||{};
  if(typeof m!=="object"||Array.isArray(m))m={summary:m};
  var status=String(first(m,"status","recommendationStatus","decisionStatus")||"待补证");
  var recommendation=first(m,"recommendation","judgment","summary","decision")||"尚未形成制造选型建议。";
  var hasFindings=Object.keys(m).length>0;
  var factors=first(m,"factors","criteria","evidence","assessments");
  var actions=first(m,"nextActions","actions","followups","nextChecks");
  if(!hasFindings)return workbenchMessageMarkup('制造选型待补证','<p>尚未形成制造选型建议。进入工程验证前，需补充技术适配、成熟度、可靠性、良率、维护、成本、交付和供应链证据。</p>','warning');
  return '<section class="report-section manufacturing-section"><div class="report-section-heading"><div><p class="eyebrow">06 / MANUFACTURING DECISION SUPPORT</p><h4>制造选型与应用影响</h4></div><span class="section-status '+(status.toLowerCase().includes("不")||status.toLowerCase().includes("待")?'pending':'approved')+'">'+esc(status)+'</span>'+workbenchMessageMarkup('应用边界','<p>该建议只支持技术选型研究和制造验证，不自动形成采购、作战或批量定型决定。</p>')+'</div><div class="manufacturing-recommendation"><b>Agent 建议</b><p>'+esc(fieldText(recommendation))+'</p></div><div class="manufacturing-grid"><div><h5>评估维度</h5>'+listOrKeyValueMarkup(factors,"技术适配、成熟度、可靠性、维护、成本、交付和供应链")+'</div><div><h5>下一步验证</h5>'+listMarkup(actions,"进入工程验证前，补充良率、可靠性、维护和供应链证据。")+'</div></div></section>';
}
function listOrKeyValueMarkup(value,empty){
  if(Array.isArray(value))return listMarkup(value,empty);
  if(value&&typeof value==="object")return '<ul class="report-bullets">'+Object.entries(value).map(function(entry){return '<li><b>'+esc(entry[0])+'</b>：'+esc(fieldText(entry[1]))+'</li>'}).join('')+'</ul>';
  return '<p class="not-recorded">'+esc(fieldText(value)||empty)+'</p>';
}
function validationStatusLabel(value){return ({passed:"机器检查通过",failed:"检查失败","needs-evidence":"需要补证","human-confirmed":"人工确认","not-run":"未执行"})[value]||value||"未执行"}
function auditStatusLabel(value){return ({passed:"审计通过",failed:"审计失败","needs-review":"待人工审核","not-run":"未运行"})[String(value||"")]||value||"未运行"}
function ontologyAuditMarkup(a){
  var id=assessmentId(a),payload=state.auditByAssessment&&state.auditByAssessment[id]||{},runs=asArray(payload.audits||payload.auditRuns),latest=runs[0]||{},findings=asArray(payload.findings),open=findings.filter(function(f){return !["confirmed","dismissed"].includes(String(f.state||""))}).length,status=first(latest,"currentStatus","status")||"not-run",busy=reportBusy(id);
  var rows=findings.length?findings.map(function(f){var fid=first(f,"findingId","id"),form=state.auditFindingForms?.[fid]||{},stateLabel=String(f.state||"open");return '<div class="ontology-audit-finding '+(stateLabel==='open'?'is-open':'is-closed')+'"><span class="audit-finding-code">'+esc(f.code||f.targetType||"发现")+'</span><span class="audit-finding-state">'+esc(stateLabel)+'</span><span class="audit-finding-message" title="'+esc(f.message||"")+'">'+esc(f.message||"未提供问题说明")+'</span><input class="audit-reviewer" data-audit-field="reviewer" data-audit-finding="'+esc(fid)+'" value="'+esc(form.reviewer||"")+'" placeholder="审核人" aria-label="审核人"><input class="audit-reason" data-audit-field="reason" data-audit-finding="'+esc(fid)+'" value="'+esc(form.reason||"")+'" placeholder="审核理由" aria-label="审核理由"><span class="audit-finding-actions"><button type="button" class="filter-chip" data-audit-finding-action="confirm" data-audit-finding="'+esc(fid)+'" data-audit-report="'+esc(id)+'" '+(busy||stateLabel!=='open'?'disabled':'')+'>确认</button><button type="button" class="filter-chip" data-audit-finding-action="dismiss" data-audit-finding="'+esc(fid)+'" data-audit-report="'+esc(id)+'" '+(busy||stateLabel!=='open'?'disabled':'')+'>驳回</button><button type="button" class="filter-chip" data-audit-finding-action="request-more-evidence" data-audit-finding="'+esc(fid)+'" data-audit-report="'+esc(id)+'" '+(busy||stateLabel!=='open'?'disabled':'')+'>补证</button></span></div>'}).join(''):'<div class="ontology-audit-empty">'+(status==='not-run'?'尚未运行周期审计。':'当前审计未发现待处理问题。')+'</div>';
  return '<section class="ontology-audit-panel" aria-label="本体周期审计与人工审核"><div class="ontology-audit-head"><div><b>周期审计 · 人工审核</b><small>审计只记录治理状态；模型置信度不能替代人工确认。</small></div><span class="section-status '+(status==='passed'?'approved':'pending')+'">'+esc(auditStatusLabel(status))+'</span><button type="button" class="filter-chip" data-run-ontology-audit="'+esc(id)+'" '+(busy?'disabled':'')+'>'+(busy?'审计中…':'运行周期审计')+'</button></div><div class="ontology-audit-summary"><span>运行 <b>'+runs.length+'</b></span><span>发现 <b>'+findings.length+'</b></span><span>待处理 <b>'+open+'</b></span>'+(latest.createdAt?'<span>最近 '+esc(shortTime(latest.createdAt))+'</span>':'')+'</div><div class="ontology-audit-findings">'+rows+'</div></section>';
}
function validationMarkup(a){
  var rounds=validationRounds(a),id=assessmentId(a),latest=rounds[0]||{},status=first(latest,"status","validationStatus")||"not-run",modelCount=rounds.filter(function(round){return round.kind==="model-critique"}).length;
  return '<section class="report-section validation-section"><div class="report-section-heading"><div><p class="eyebrow">07 / VALIDATION &amp; HUMAN REVIEW</p><h4>验证记录与置信度依据</h4></div><span class="section-status '+(status==='passed'?'approved':'pending')+'">'+esc(validationStatusLabel(status))+'</span>'+workbenchMessageMarkup('验证范围','<p>完整性检查核对引用、数值、单位和来源快照。DeepSeek 反证复核需单独执行；同模型复核不构成独立来源验证，重复检查不会提高置信度。</p>')+'</div><div class="validation-summary"><span><b>'+rounds.length+'</b> 检查记录</span><span>报告审核 <b>'+esc(assessmentStatus(a)[0])+'</b></span><button type="button" class="filter-chip" data-run-validation="'+esc(id)+'" '+(reportBusy(id)?'disabled':'')+'>'+(reportBusy(id)?'读取中…':'检查证据完整性')+'</button><button type="button" class="filter-chip" data-model-validation="'+esc(id)+'" '+(reportBusy(id)||modelCount>=3?'disabled':'')+'>DeepSeek 反证复核（'+modelCount+'/3）</button></div>'+(modelCount>=3?workbenchMessageMarkup('模型复核已达上限','<p>本报告已达到 3 轮模型复核上限。请补充新证据并生成新报告后继续复核。</p>','warning'):'')+(rounds.length?'<ol class="validation-timeline">'+rounds.map(function(round,index){var checks=asArray(round.checks),factors=round.confidenceFactors||{};return '<li><div class="validation-dot '+(round.status==='passed'?'pass':'')+'"></div><div class="validation-round-head"><b>R'+esc(round.roundNo||index+1)+' · '+esc(round.kind==='model-critique'?'DeepSeek 反证复核':'证据完整性')+'</b><span class="section-status '+(round.status==='passed'?'approved':'pending')+'">'+esc(validationStatusLabel(round.status))+'</span></div><div class="validation-meta">'+esc(round.actor||"系统")+' · '+esc(shortTime(round.createdAt))+' · '+checks.length+' 项检查</div><ul class="validation-checks">'+checks.map(function(check){return '<li><span>'+esc(({"finding-evidence":"趋势证据引用","capability-provenance":"能力数值溯源","independent-publisher":"来源覆盖"})[check.code]||check.code)+'</span><b>'+esc(validationStatusLabel(check.status))+'</b><small>'+esc(check.detail||'')+'</small></li>'}).join('')+'</ul><p>出版域 '+esc(factors.publisherCount??factors.independentPublisherCount??'未记录')+' · 来源独立性 '+(factors.independenceVerified?'已验证':'未验证')+' · 反证核查 '+(factors.contraryEvidenceChecked?'已记录，待独立复核':'未完成')+'</p>'+listMarkup(round.issues,"本轮未发现结构性问题，仍需人工判断结论是否成立。")+'</li>'}).join('')+'</ol>':workbenchMessageMarkup('核验尚未执行','<p>执行检查后记录结果、缺口及来源快照；人工研判仍是独立环节。</p>'))+'</section>'+ontologyAuditMarkup(a);
}
function admissionKind(candidate){return candidate.kind==='graph-property'?'property':candidate.kind==='graph-relation'?'relation':candidate.kind==='graph-node'?(candidate.assertion?.type==='EventRecord'?'event':'object'):'capability'}
function admissionLabel(candidate){var assertion=candidate.assertion||{},graph=state.graphByAssessment[candidate.assessmentId]||{},nodes=asArray(graph.nodes),mapping=candidate.mapping||{},from=nodes.find(function(n){return n.id===mapping.fromGraphNodeId}),to=nodes.find(function(n){return n.id===mapping.toGraphNodeId});return candidate.kind==='graph-property'?(assertion.subjectLabel||'对象')+' · '+(assertion.propertyName||'属性'):candidate.kind==='graph-relation'?(from?.label||assertion.from?.label||'起点对象')+' → '+relationLabel(assertion.relation)+' → '+(to?.label||assertion.to?.label||'终点对象'):candidate.kind==='graph-node'?assertion.label||'对象候选':(assertion.equipment||'装备')+' · '+(assertion.metric||'能力观测')}
function admissionEvidence(candidate){return candidate.assertion?.evidenceRefs||asArray(candidate.assertion?.evidence)}
function propertyCandidateFields(candidate){var assertion=candidate.assertion||{},suggestion=assertion.predicateRecommendation||candidate.mapping?.predicateRecommendation||candidate.predicateRecommendation,recommendation=suggestion&&typeof suggestion==='object'?[suggestion.label||suggestion.id||'未命名',predicateConfidenceLabel(suggestion.confidence),suggestion.reason].filter(Boolean).join(' · '):null;return [['所属对象',assertion.subjectLabel||assertion.subject],['属性',assertion.propertyName],['原始值',assertion.rawValue],['显示值（待人工确认）',assertion.normalizedValue],['单位',assertion.unit],['条件',assertion.conditions],['有效时间',assertion.validTime],['模型推荐谓词',recommendation],['本体谓词',candidate.mapping?.predicate]].filter(function(row){return row[1]!==undefined&&row[1]!==null&&row[1]!==''}).map(function(row){return '<div><dt>'+esc(row[0])+'</dt><dd>'+esc(fieldText(row[1]))+'</dd></div>'}).join('')}
function admissionRecommendationMarkup(candidate){var recommendation=candidate.reviewRecommendation||candidate.assertion?.reviewRecommendation||candidate.recommendation||candidate.modelRecommendation;if(!recommendation||typeof recommendation!=='object')return '';var action=recommendation.action||recommendation.proposedAction,label=({consider:'建议人工审阅','more-evidence':'建议补证',accept:'建议接受',reject:'建议退回','request-more-evidence':'建议补证',modify:'建议修改'})[action]||'待审核建议';return workbenchMessageMarkup(label+' · '+(recommendation.source==='model'?'模型建议':'依据待核对'),'<p>'+esc(recommendation.reason||recommendation.rationale||'未提供建议理由')+'</p>'+((recommendation.gaps||recommendation.nextChecks)?listMarkup(recommendation.gaps||recommendation.nextChecks):'')+'<small>模型建议供审核参考；接受、修改、补证与入库由审核人分别确认。</small>',['reject','more-evidence','request-more-evidence'].includes(action)?'warning':'info','admission-recommendation')}
function propertyEditMarkup(candidate,locked){var assertion=candidate.assertion||{},id=first(candidate,'admissionId','id'),edits=state.propertyEdits[id]||{},fields=[['rawValue','原始值',assertion.rawValue],['normalizedValue','中文显示值',assertion.normalizedValue],['unit','单位',assertion.unit],['conditions','条件',assertion.conditions],['validTime','有效时间',assertion.validTime]],attrs=' data-admission-id="'+esc(id)+'" data-admission-report="'+esc(candidate.assessmentId)+'"';return '<details class="property-edit"><summary>修正属性或谓词，生成新审核版本</summary><div class="property-edit-fields">'+fields.map(function(field){return '<label>'+field[1]+'<input data-property-edit="'+field[0]+'" data-property-candidate="'+esc(id)+'" value="'+esc(edits[field[0]]!==undefined?edits[field[0]]:fieldText(field[2]))+'" '+(locked?'disabled':'')+'></label>'}).join('')+'<label>改为其他本体谓词<select data-property-edit="ontologyPredicate" data-property-candidate="'+esc(id)+'" '+(locked?'disabled':'')+'><option value="">保留模型推荐</option>'+PROPERTY_PREDICATES.map(function(pair){return '<option value="'+pair[0]+'" '+(edits.ontologyPredicate===pair[0]?'selected':'')+'>'+pair[1]+'</option>'}).join('')+'</select></label></div><p>默认采用模型推荐；只有语义不符或证据不足时才修正。填写审核理由后保存，原候选保留，新版本需重新人工确认。</p><button type="button" class="filter-chip" data-admission-action="modify"'+attrs+' '+(locked?'disabled':'')+'>保存修改版本</button></details>'}
function ontologyMarkup(a){
  var all=ontologyAdmissions(a),id=assessmentId(a),form=state.admissionForms[id]||{},withdrawn=a.status==='withdrawn',busy=reportBusy(id),filter=form.filter||'all',list=all.filter(function(c){return filter==='all'||filter==='high'&&c.review?.verifiedConfidence==='high'||admissionKind(c)===filter}),published=all.filter(function(c){return c.status==='published'}).length;
  var head='<section class="report-section ontology-section"><div class="report-section-heading"><div><p class="eyebrow">HUMAN REVIEW → ONTOLOGY</p><h4>逐条审核，确认后写入知识库</h4></div><span class="section-status">'+all.length+' 候选 · '+published+' 已入库</span>'+augmentationMessagesMarkup(a,['admissions','validation'])+workbenchMessageMarkup('准入规则','<p>对象身份、每条属性、关系和事件分别审核。模型先推荐本体谓词，机器检查词表与对象类别，用户只在冲突或低置信度时修正。属性保留原始值、显示值与独立引文。高置信度由审核人结合原文确认；关系入库须先发布两端对象。报告审核与本体断言准入分别记录。</p>')+'</div><div class="object-toolbar"><label>候选类别<select data-admission-field="filter" data-admission-form="'+esc(id)+'">'+[['all','全部候选'],['object','对象身份'],['property','对象属性'],['relation','关系'],['event','事件'],['capability','能力指标'],['high','人工确认高置信度']].map(function(r){return '<option value="'+r[0]+'" '+(filter===r[0]?'selected':'')+'>'+r[1]+'</option>'}).join('')+'</select></label><button type="button" class="filter-chip" data-go-stage="extract">从对象提取选择 →</button><button type="button" class="filter-chip" data-build-admissions="'+esc(id)+'" '+(busy||withdrawn||!capabilityMetrics(a).length?'disabled':'')+'>生成能力准入候选</button></div>';
  if(!all.length)return head+workbenchMessageMarkup(state.augmentationErrors?.[id]?.admissions?'准入候选尚未读取':'尚无准入候选','<p>'+(state.augmentationErrors?.[id]?.admissions?'准入候选读取失败，请先重试读取，尚不能判断候选是否为空。':'在对象分析中勾选具有精确证据的对象、属性、关系或事件，送入此处审核；也可生成能力指标准入候选。')+'</p>')+'</section>';
  var cards=list.map(function(c){var assertion=c.assertion||{},status=c.status||'under-review',cid=first(c,'admissionId','id'),revision=c.revision||1,generic=['graph-node','graph-relation','graph-property'].includes(c.kind),locked=withdrawn||c.staleGraph||['published','withdrawn'].includes(status)||!!c.supersededByAdmissionId||busy,prerequisites=['graph-relation','graph-property'].includes(c.kind)&&asArray(c.mapping?.requiredCandidateIris).some(function(iri){return !all.some(function(other){return other.kind==='graph-node'&&other.candidateIri===iri&&other.status==='published'})}),attrs=' data-admission-id="'+esc(cid)+'" data-admission-report="'+esc(id)+'"',statusText=({'under-review':'待审核',accepted:'审核通过',published:'已入库',rejected:'已退回','needs-evidence':'待补证',withdrawn:'已撤回'})[status]||status,ref=admissionEvidence(c)[0]||{},verified=c.review?.verifiedConfidence||'',confidence=state.admissionConfidence[cid]||verified||'',kind=admissionKind(c),attributes=assertion.attributes||{},properties=c.kind==='graph-property'?propertyCandidateFields(c):generic?Object.entries(c.kind==='graph-node'?Object.assign({label:assertion.label,type:graphTypeLabel(assertion.type)},attributes):{relation:relationLabel(assertion.relation)}).map(function(entry){return '<div><dt>'+esc(({eventKind:'事件类型',occurredAt:'事件日期',eventDate:'事件日期',label:'名称',type:'类别',relation:'关系'})[entry[0]]||entry[0])+'</dt><dd>'+esc(fieldText(entry[1]))+'</dd></div>'}).join(''):'<div><dt>指标值</dt><dd>'+esc(assertion.value)+' '+esc(assertion.unit||'')+'</dd></div><div><dt>数据性质</dt><dd>'+esc(assertion.basis||'未记录')+'</dd></div><div><dt>条件</dt><dd>'+esc(assertion.conditions||'未记录')+'</dd></div>';
    return '<article class="admission-card '+(state.selectedAdmissionId===cid?'active':'')+'"><div class="admission-head"><b>'+esc(admissionLabel(c))+'</b><span class="section-status '+(['accepted','published'].includes(status)?'approved':'pending')+'">'+esc(statusText)+'</span></div><div class="admission-meta"><span>'+esc(({object:'对象身份',property:'对象属性',relation:'关系',event:'事件',capability:'能力指标'})[kind])+'</span><span>提取置信度：'+esc(confidenceLabel({confidence:c.proposedConfidence||'unknown'}))+'</span><span>人工确认：'+(verified==='high'?'高置信度':'未确认')+'</span></div><dl class="object-attribute-grid">'+properties+'</dl>'+admissionRecommendationMarkup(c)+(c.kind==='graph-property'?propertyEditMarkup(c,locked):'')+(c.supersededByAdmissionId?workbenchMessageMarkup('已生成修改版本','<p>请审核新候选，原版本保留供追溯。</p>','warning'):'')+(c.staleGraph?workbenchMessageMarkup('候选版本已过期','<p>图谱版本已更新，此候选不能审核或入库，请返回对象提取生成当前版本候选。</p>','warning'):'')+'<blockquote>'+esc(ref.quote||'尚缺精确原文')+'</blockquote><button type="button" class="evidence-ref-button" data-view-admission="'+esc(cid)+'" data-admission-report="'+esc(id)+'">核对原文、属性与映射 →</button><details class="evidence-provenance"><summary>本体映射与版本</summary><span>类别：'+esc(c.ontologyClass||assertion.type||'关系断言')+'</span><span>候选 IRI：'+esc(c.candidateIri||'未记录')+'</span><span>关系：'+esc(c.mapping?.predicate||'对象身份断言')+'</span><span>图谱版本：'+esc(c.graphRevisionId||'能力证据账本')+'</span><span>候选修订：'+revision+'</span></details>'+(generic?'<label class="verified-confidence-label">人工核验置信度<select data-verified-confidence="'+esc(cid)+'" '+(locked?'disabled':'')+'><option value="">选择核验结论…</option><option value="high" '+(confidence==='high'?'selected':'')+'>高 · 证据充分且映射成立</option></select></label>':'')+(['graph-relation','graph-property'].includes(c.kind)?workbenchMessageMarkup(prerequisites?'所属对象待入库':'发布顺序','<p>先审核并发布所属对象，再发布属性或关系；每条断言独立核对。</p>',prerequisites?'warning':'info'):'')+(c.review?.reason?workbenchMessageMarkup('审核理由','<p>'+esc(c.review.reason)+'</p>'):'')+'<div class="admission-actions"><button type="button" class="filter-chip" data-admission-action="request-more-evidence"'+attrs+' '+(locked?'disabled':'')+'>补充证据</button><button type="button" class="filter-chip" data-admission-action="reject"'+attrs+' '+(locked?'disabled':'')+'>退回</button><button type="button" class="btn btn-primary" data-admission-action="accept"'+attrs+' '+(locked||generic&&confidence!=='high'?'disabled':'')+'>确认通过</button><button type="button" class="filter-chip" data-admission-action="publish"'+attrs+' '+(locked||prerequisites||status!=='accepted'?'disabled':'')+'>写入本体知识库</button>'+(status==='published'&&c.candidateIri?'<a class="filter-chip admission-kb-link" href="./kb.html#iri/'+encodeURIComponent(c.kind==='graph-property'?c.mapping?.subjectIri||assertion.subject:c.kind==='graph-relation'?assertion.from:c.candidateIri)+'">在知识库查看 ↗</a>':'')+'</div></article>';
  }).join('');
  return head+'<div class="admission-review-form"><label>审核人<input data-admission-field="reviewer" data-admission-form="'+esc(id)+'" value="'+esc(form.reviewer||'')+'" placeholder="填写实际审核人"></label><label>本次审核理由<textarea data-admission-field="reason" data-admission-form="'+esc(id)+'" placeholder="说明原文是否支持当前断言、身份和本体映射，记录限制条件与核验依据">'+esc(form.reason||'')+'</textarea></label></div><div class="admission-list">'+(cards||workbenchMessageMarkup('筛选无候选','<p>高置信度筛选只显示人工已明确确认的记录。</p>'))+'</div></section>';
}
function renderExecution(){
  var task=state.task,a=selectedAssessment(),id=a?assessmentId(a):null;
  renderExecutionWorkspace($('execution-summary'),{
    task:task,assessment:a,sources:state.sources,trace:task&&state.executionByTask[task.id],
    traceBusy:!!(task&&state.executionBusy[task.id]),selectedIds:selectedItemIds(),
    records:a?extractedRecords(a):null,rounds:a?validationRounds(a):[],
    admissions:a?ontologyAdmissions(a):[],jobs:id?state.extractionJobs[id]:[],
    busy:state.busy||!!(id&&state.augmentationBusy[id])
  },{refresh:loadExecutionTrace,navigate:function(stage){
    if(stage==='materials')$('execution-materials').scrollIntoView({block:'start',behavior:'smooth'});
    else setWorkflowStage(stage);
  },manualControl:function(action){
    if(action==='select-materials'){
      $('execution-materials')?.scrollIntoView({block:'start',behavior:'smooth'});
      return;
    }
    if(action==='select-all-materials'){
      ui.materialAll?.click();
      return;
    }
    if(action==='generate-assessment'){
      ui.generate?.click();
      return;
    }
    if(action==='quantify-capability'&&state.selectedAssessmentId){
      setWorkflowStage('assessment',true);
      enrichCapabilities(state.selectedAssessmentId);
      return;
    }
    if(action==='extract'&&state.selectedAssessmentId){
      startExtractionJob(state.selectedAssessmentId,{extractEntities:true});
    }
  }});
}
async function loadExecutionTrace(){
  var id=state.task?.id;if(!id||state.executionBusy[id])return;
  state.executionBusy[id]=true;renderExecution();
  try{state.executionByTask[id]=await request('/tasks/'+encodeURIComponent(id)+'/execution')}
  catch(e){state.executionByTask[id]=Object.assign({},state.executionByTask[id]||{executionTrace:[]},{unavailable:true})}
  finally{delete state.executionBusy[id];if(state.task?.id===id)renderExecution()}
}
function extractionJobActive(job){return job&&['queued','running','interrupted'].includes(job.status)}
function activeExtractionJob(id){return asArray(state.extractionJobs[id]).find(extractionJobActive)||null}
function reportBusy(id){return !!(state.augmentationBusy[id]||activeExtractionJob(id))}
function pendingAttributeNodes(a){return extractedRecords(a).nodes.filter(function(node){return !node.payload?.attributeExtraction||['budget-skipped','skipped-budget','pending','failed','not-configured'].includes(node.payload.attributeExtraction.status)})}
function rememberExtractionJob(job){if(!job?.jobId||!job.assessmentId)return;var id=job.assessmentId;state.extractionJobs[id]=[job].concat(asArray(state.extractionJobs[id]).filter(function(row){return row.jobId!==job.jobId})).sort(function(a,b){return String(b.createdAt).localeCompare(String(a.createdAt))})}
function extractionJobsMarkup(a,compact){
  var id=assessmentId(a),jobs=asArray(state.extractionJobs[id]),job=jobs.find(extractionJobActive)||jobs[0];if(!job)return '';
  var progress=job.progress||{},labels={queued:'等待提取',running:'逐对象提取中',interrupted:'等待恢复提取',completed:'提取完成',partial:'部分完成',failed:'提取失败'},busy=extractionJobActive(job),nodes=extractedRecords(a).nodes,remaining=pendingAttributeNodes(a),error=state.extractionJobErrors[job.jobId],label=(labels[job.status]||job.status)+' · '+(progress.processedObjects??0)+' / '+(progress.totalObjects??nodes.length)+' 对象';
  if(compact)return error||['failed','partial','interrupted'].includes(job.status)?'<button type="button" class="filter-chip '+(error||job.status==='failed'?'workbench-action-error':'workbench-action-warning')+'" data-refresh-extraction="'+esc(id)+'" title="'+esc(label+(job.errorCode?' · '+job.errorCode:'')+(error?' · '+error:''))+'">'+esc(error?'进度读取失败':labels[job.status])+' · 刷新进度</button>':'';
  return '<div class="workbench-message-group extraction-job-status">'+workbenchMessageMarkup(label,'<small>'+esc(job.jobId)+'</small>'+(progress.currentObjectId?'<p>最近处理对象：'+esc(nodes.find(function(node){return node.id===progress.currentObjectId})?.label||progress.currentObjectId)+'</p>':'')+(busy?'<p>可切换视图；重新打开报告后继续恢复进度。</p>':'')+(job.errorCode?'<p>'+esc(job.errorCode)+'；已有图谱与属性保留。</p>':'')+(error?'<p>进度读取失败：'+esc(error)+' <button type="button" class="filter-chip" data-refresh-extraction="'+esc(id)+'">重试读取</button></p>':''),error||job.status==='failed'?'error':job.status==='partial'||job.status==='interrupted'?'warning':'info')+(!busy&&remaining.length?'<button type="button" class="filter-chip" data-next-attribute-batch="'+esc(id)+'" '+(reportBusy(id)?'disabled':'')+'>继续提取未执行对象（'+remaining.length+'）</button>':'')+'</div>';
}
function followUpPlanMarkup(a){
  var id=assessmentId(a),plan=state.followUpPlans?.[id];if(!plan)return '';
  var revision=graphData(a)?.graphRevisionId||graphData(a)?.id,stale=revision!==plan.graphRevisionId,objectId=state.followUpObject?.[id],targets=asArray(plan.targets).map(function(target,index){return {target:target,index:index}}).filter(function(row){return !objectId||row.target.objectId===objectId}),budget=plan.budget||{},remaining=budget.remainingTasks!==undefined?' · 剩余 '+budget.remainingTasks+' 项任务 / '+budget.remainingItems+' 条材料':'';
  return '<details class="follow-up-plan '+(stale||budget.exhausted?'workbench-warning':'')+'"><summary>针对证据缺口的补采计划 · '+targets.length+' 项'+(stale?' · 图谱已过期':budget.exhausted?' · 预算已用完':'')+'</summary><p>程序按对象、属性与原文缺口生成检索模板，每次选择一项执行。总预算最多 '+esc(budget.maxTasks??4)+' 项任务 / '+esc(budget.maxItems??32)+' 条材料'+esc(remaining)+'。</p>'+(budget.exhausted?workbenchMessageMarkup('补采预算已用完','<p>当前图谱版本的补采预算已用完；已执行任务仍可打开核对。</p>','warning'):'')+(stale?workbenchMessageMarkup('补采目标已过期','<p>图谱版本已变化，请重新生成补采计划。</p>','warning'):'')+targets.map(function(row){
    var target=row.target,context=asArray(target.sourceContext),done=!!target.followupTaskId,blocked=target.executable===false||budget.exhausted;
    return '<article><b>'+esc(target.objectLabel)+' · '+esc(target.attributeLabel||({property:'属性补证',identity:'断言独立核验','public-event-imagery':'公开历史影像出处'})[target.targetType]||target.targetType)+'</b>'+(target.rawValue!==undefined?'<p>待核验原始值：'+esc(fieldText(target.rawValue))+'</p>':'')+'<p>'+esc(target.reason)+'</p>'+(target.propertyPath||target.assertionId?'<small>断言：'+esc(target.assertionId||target.propertyPath)+'</small>':'')+(context.length?'<div class="follow-up-source-context">'+context.map(function(source){return '<small>'+esc(source.sourceName||source.title||source.itemId||'保存来源')+' · '+esc(sourceNatureLabel(source.contentNature||source.sourceRole))+' · '+esc(source.publishedAt?dayLabel(source.publishedAt):'资料发布日期缺失，需核对原页面')+'</small>'}).join('')+'</div>':'<small>缺少具体出处与日期，需补证。</small>')+'<details><summary>检索任务与来源范围</summary><p>'+esc(target.question||'')+'</p><small>'+esc(target.query||listText(target.keywords).join(' · '))+' · 最多 '+esc(target.maxItems)+' 条</small><small>信源范围：'+esc(asArray(target.sourceIds).map(function(sourceId){return state.sources.find(function(source){return source.id===sourceId})?.name||sourceId}).join(' / '))+'</small>'+(target.stoppingCondition?'<small>停止条件：'+esc(fieldText(target.stoppingCondition))+'</small>':'')+'</details>'+(done?'<p>已执行 · '+esc(statusLabel(target.executionStatus)[0])+' · 已采集 '+esc(target.collectedItems??0)+' 条</p><button type="button" class="filter-chip" data-open-follow-up-task="'+esc(target.followupTaskId)+'">打开已有补采任务 →</button>':'<button type="button" class="filter-chip" data-run-follow-up="'+row.index+'" data-follow-report="'+esc(id)+'" '+(stale||blocked||state.followUpBusy?.[id]?'disabled':'')+'>'+(blocked?'预算不足，未执行':'执行此项补采')+'</button>')+'</article>';
  }).join('')+(!targets.length?'<p>此对象当前没有可执行补采目标。</p>':'')+'</details>';
}
async function loadFollowUpPlan(id,objectId){state.followUpPlans=state.followUpPlans||{};state.followUpObject=state.followUpObject||{};var taskId=state.task?.id;try{var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/follow-up-plan');state.followUpPlans[id]=result.plan;state.followUpObject[id]=objectId||null;if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id))renderAssessments()}catch(error){notify('补采计划读取失败：'+error.message,'warn')}}
async function runFollowUpTarget(id,index){var plan=state.followUpPlans?.[id],target=asArray(plan?.targets)[index],taskId=state.task?.id;if(!target)return;if(target.followupTaskId){state.activeStage='materials';await loadTask(target.followupTaskId);return}if(target.executable===false||plan.budget?.exhausted){notify('当前补采目标不可执行，请核对剩余预算。','warn');return}state.followUpBusy=state.followUpBusy||{};if(state.followUpBusy[id])return;state.followUpBusy[id]=true;renderAssessments();try{var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/follow-up-plan',{method:'POST',body:JSON.stringify({graphRevisionId:plan.graphRevisionId,objectId:target.objectId,targetType:target.targetType,targetId:target.targetId})});state.parentFollowups=state.parentFollowups||{};if(result.followup?.purpose==='public-event-imagery')state.parentFollowups[result.task.id]=result.followup;notify('已启动该对象的定向补采任务。');if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id)){state.activeStage='materials';await loadTask(result.task.id)}}catch(error){notify('定向补采启动失败：'+error.message,'warn')}finally{delete state.followUpBusy[id];renderAssessments()}}
async function loadExtractionJobs(id){
  if(!id)return;var taskId=state.task?.id;
  try{var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/extraction-jobs');state.extractionJobs[id]=asArray(result.jobs).sort(function(a,b){return String(b.createdAt).localeCompare(String(a.createdAt))});state.extractionJobs[id].filter(function(job,index){var current=state.graphByAssessment[id];return extractionJobActive(job)||index===0&&job.resultGraphRevisionId&&job.resultGraphRevisionId!==(current?.graphRevisionId||current?.id)&&!state.handledExtractionJobs[job.jobId]}).forEach(function(job){scheduleExtractionJob(job,taskId,1000)});if(String(state.selectedAssessmentId)===String(id)&&state.task?.id===taskId)renderAssessments();
    // A reload may see a terminal extraction job whose graph revision is
    // already current.  The extraction result is then intentionally not
    // reloaded, but the persisted validation/admission stages still need to
    // resume.  Defer until the current render and any active job polling have
    // settled; runConfiguredAutomation itself re-checks the durable records.
    if(String(state.selectedAssessmentId)===String(id)&&state.task?.id===taskId&&state.task.workflowMode==='auto')setTimeout(async function(){await resumeAutomaticChain(id,taskId);if(String(state.selectedAssessmentId)===String(id)&&state.task?.id===taskId)renderAssessments()},0);
  }catch(error){if(String(state.selectedAssessmentId)===String(id)&&state.task?.id===taskId)notify('提取任务进度暂不可用：'+error.message,'warn')}
}
function scheduleExtractionJob(job,taskId,delay){if(!job?.jobId||state.extractionJobTimers[job.jobId])return;state.extractionJobTimers[job.jobId]=setTimeout(function(){delete state.extractionJobTimers[job.jobId];pollExtractionJob(job.jobId,job.assessmentId,taskId)},delay||2500)}
async function startExtractionJob(id,options){
  id=String(id||'');if(!id||state.busy||reportBusy(id))return;
  var a=state.assessments.find(function(report){return assessmentId(report)===id}),taskId=state.task?.id;if(!a||a.status==='withdrawn')return;
  state.augmentationBusy[id]=true;renderAssessments();
  try{var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/extraction-jobs',{method:'POST',body:JSON.stringify(Object.assign({expectedRevision:first(a,'revision','version','expectedRevision')||1},options||{}))}),job=result.job;if(!job?.jobId)throw new Error('提取任务未返回任务编号');rememberExtractionJob(job);if(state.extractionStartErrors)delete state.extractionStartErrors[id];scheduleExtractionJob(job,taskId,100);notify('后台提取任务已开始，逐对象记录进度与证据。');return job}
  catch(error){state.extractionStartErrors=state.extractionStartErrors||{};state.extractionStartErrors[id]=error.message;delete state.autoExtractionRequested[id];notify('创建提取任务失败：'+error.message,'warn');return null}
  finally{delete state.augmentationBusy[id];if(state.task?.id===taskId&&String(state.selectedAssessmentId)===id)renderAssessments()}
}
async function pollExtractionJob(jobId,id,taskId,options){
  options=options||{};
  if(state.extractionJobBusy[jobId])return;state.extractionJobBusy[jobId]=true;
  try{var result=await requestIntelligence('/extraction-jobs/'+encodeURIComponent(jobId)),job=result.job;if(!job)throw new Error('提取任务未找到');rememberExtractionJob(job);delete state.extractionJobErrors[jobId];delete state.extractionJobErrorCounts[jobId];
    if(extractionJobActive(job))scheduleExtractionJob(job,taskId);
    else if(!state.handledExtractionJobs[jobId]){
      if(job.resultGraphRevisionId){var results=await Promise.allSettled(['graph','validation','admissions'].map(function(kind){return requestIntelligence('/assessments/'+encodeURIComponent(id)+'/'+kind)}));if(results[0].status!=='fulfilled')throw results[0].reason;state.graphByAssessment[id]=results[0].value.graph||results[0].value;if(results[1].status==='fulfilled')state.validationByAssessment[id]=results[1].value;if(results[2].status==='fulfilled')state.admissionsByAssessment[id]=results[2].value;else delete state.admissionsByAssessment[id];state.objectSelections[id]={nodes:new Set(),edges:new Set(),properties:new Set()};
        if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id)){state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;state.selectedAdmissionId=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null}
        // Identity extraction deliberately caps each attribute pass at eight
        // objects. Continue budget-skipped objects automatically so a report
        // with more than one batch reaches a terminal result without silently
        // requiring a manual click. Failed calls remain manual retry work.
        if(job.status==='partial'&&!options.suppressContinuation){
          var continuation=asArray(state.graphByAssessment[id]?.nodes).filter(function(node){
            return node?.payload?.extractionMethod==='llm' && node?.payload?.attributeExtraction?.status==='budget-skipped';
          }).slice(0,8);
          if(continuation.length){
            // Await enqueue before checking automation. The next batch is
            // now durably present and active, so the guard below will defer
            // validation until its final graph is saved. Normal job polling
            // advances any additional batches without holding this request.
            await startExtractionJob(id,{extractAttributes:true,objectIds:continuation.map(function(node){return node.id})});
          }
        }
      }
      state.handledExtractionJobs[jobId]=true;if(state.task?.id===taskId)await Promise.allSettled([loadImageryEvidence(),loadExecutionTrace()]);if(!options.suppressAutomation&&state.task?.id===taskId&&state.task.workflowMode==='auto')await runConfiguredAutomation(id);
    }
  }catch(error){state.extractionJobErrors[jobId]=error.message;state.extractionJobErrorCounts[jobId]=(state.extractionJobErrorCounts[jobId]||0)+1;if(state.extractionJobErrorCounts[jobId]<3)scheduleExtractionJob({jobId:jobId,assessmentId:id},taskId,10000)}
  finally{delete state.extractionJobBusy[jobId];if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id)){renderReportPanels();renderGraphOverlay()}}
}
function objectVerificationControls(a){var id=assessmentId(a);return '<button type="button" class="filter-chip" data-run-validation="'+esc(id)+'" title="核验当前版本对象、属性与来源证据" '+(reportBusy(id)?'disabled':'')+'>核验证据</button><button type="button" class="filter-chip" data-model-validation="'+esc(id)+'" title="DeepSeek 反证复核；同一报告最多 3 轮" '+(reportBusy(id)||validationRounds(a).filter(function(r){return r.kind==='model-critique'}).length>=3?'disabled':'')+'>反证复核</button>'}
function persistentValidation(id,kind){
  var a=state.assessments.find(function(report){return assessmentId(report)===String(id)}),graph=a&&graphData(a),revision=graph?.graphRevisionId||graph?.id;
  return !!a&&validationRounds(a).some(function(round){
    if(kind==='model-critique'?round.kind!==kind:round.kind==='model-critique')return false;
    var recorded=round.confidenceFactors?.objectVerificationCoverage?.graphRevisionId;
    return !revision||recorded===revision||asArray(round.checks).some(function(check){return check.graphRevisionId===revision});
  });
}
function activeExtractionFor(id){return asArray(state.extractionJobs[id]).find(extractionJobActive)||null}
async function runAutomationCheck(id,kind,attempts){
  // Validation rounds are immutable and durable.  Refreshing the page must
  // never buy another critique for a round already recorded.
  if(persistentValidation(id,kind))return true;
  if(activeExtractionFor(id))return false;
  var limit=Math.max(1,Number(attempts)||1);
  for(var attempt=1;attempt<=limit;attempt++){
    if(activeExtractionFor(id))return false;
    if(await runReportValidation(id,kind)!==false)return true;
    if(persistentValidation(id,kind))return true;
    if(attempt<limit)await new Promise(function(resolve){setTimeout(resolve,1200)});
  }
  return false;
}
async function resumeAutomaticChain(id,taskId){
  if(!state.task||state.task.id!==taskId||state.task.workflowMode!=='auto')return false;
  if(activeExtractionFor(id))return false;
  var latest=asArray(state.extractionJobs[id])[0];
  if(latest?.status==='failed'){state.autoActions[id]='error';state.autoActionErrors=state.autoActionErrors||{};state.autoActionErrors[id]='对象与属性提取异常中断：'+(latest.errorCode||'请通过重新提取或补提取属性恢复');return false}
  if(!latest?.resultGraphRevisionId||!['completed','partial'].includes(latest.status))return false;
  return await runConfiguredAutomation(id);
}
async function runConfiguredAutomation(id){
  var task=state.task,auto=task?.automation||{},a=state.assessments.find(function(report){return assessmentId(report)===String(id)}),graph=a&&graphData(a),revision=graph?.graphRevisionId||graph?.id;
  if(!id||!task||!a||a.status==='withdrawn'||task.workflowMode!=='auto'||state.autoActions[id]==='running'||state.autoChainBusy?.[id])return false;
  if(state.autoActions[id]==='done'&&state.autoCompletionGraph?.[id]===revision)return true;
  if(activeExtractionFor(id)){state.autoActions[id]='waiting-extraction';return false;}
  state.autoChainBusy=state.autoChainBusy||{};state.autoChainBusy[id]=true;
  try{
  // If this is a browser reload, hydrate durable validation/admission rows
  // before deciding whether a paid model critique or admission POST is due.
  if(!state.augmentationLoaded?.[id]&&!state.augmentationBusy?.[id])await loadReportAugmentations(id);
  if(state.augmentationBusy?.[id]||(auto.autoRunEvidenceCheck||auto.autoRunModelCritique)&&state.augmentationErrors?.[id]?.validation||(auto.autoCreateAdmissionCandidates&&state.augmentationErrors?.[id]?.admissions)){state.autoActions[id]='waiting-state-read';return false;}
  if(activeExtractionFor(id)||state.task?.id!==task.id)return false;
  graph=graphData(a);revision=graph?.graphRevisionId||graph?.id;
  if(!revision){state.autoActions[id]='waiting-extraction';return false;}
  var continuation=asArray(graph.nodes).filter(function(node){return node?.payload?.extractionMethod==='llm'&&['budget-skipped','skipped-budget','pending'].includes(node.payload?.attributeExtraction?.status)}).slice(0,8);
  if(continuation.length){state.autoActions[id]='waiting-extraction';await startExtractionJob(id,{extractAttributes:true,objectIds:continuation.map(function(node){return node.id})});return false;}
  var failed=asArray(graph.nodes).filter(function(node){return ['failed','not-configured'].includes(node.payload?.attributeExtraction?.status)||node.payload?.attributeLinkMetadata?.status==='failed'}),latest=asArray(state.extractionJobs[id])[0];
  if(failed.length||latest?.status==='failed'){state.autoActions[id]='error';state.autoActionErrors=state.autoActionErrors||{};state.autoActionErrors[id]=failed.length?'对象属性或趋势属性关联调用异常中断（'+failed.length+' 项），已保留结果；请使用补提取属性或重新提取恢复。':'对象与属性提取异常中断，已保留结果；请重新提取恢复。';notify(state.autoActionErrors[id],'warn');return false;}
  if(state.autoActionErrors)delete state.autoActionErrors[id];
  state.autoActions[id]='running';
    if(auto.autoRunEvidenceCheck){if(await runAutomationCheck(id,undefined,1)===false)throw new Error('证据完整性检查未闭合')}
    if(auto.autoRunModelCritique){if(await runAutomationCheck(id,'model-critique',2)===false)throw new Error('DeepSeek 反证复核未闭合（已自动重试 1 次）')}
    if(auto.autoCreateAdmissionCandidates){var admissionResult=await buildOntologyAdmissions(id,{automatic:true});if(admissionResult===false)throw new Error('准入候选生成未闭合')}
    state.autoActions[id]='done';state.autoCompletionGraph=state.autoCompletionGraph||{};state.autoCompletionGraph[id]=revision;
    notify(admissionResult?.notApplicable?'自动流程已执行完毕；准入候选不适用，保留证据缺口。':'自动流程已执行完毕：核验记录与准入候选已保存，证据缺口保留待核。',admissionResult?.notApplicable?'warn':'success');return true
  }catch(error){state.autoActions[id]='error';state.autoActionErrors=state.autoActionErrors||{};state.autoActionErrors[id]='自动流程异常中断：'+error.message+'；已保存结果保留，可使用对应步骤的重试入口恢复。';notify(state.autoActionErrors[id],'warn');return false}
  finally{delete state.autoChainBusy[id];if(state.task?.id===task.id&&String(state.selectedAssessmentId)===String(id))renderAssessments()}
}
function linkedObjectChecks(a,elementId){var graph=graphData(a),revision=graph?.graphRevisionId||graph?.id||null,rows=[];validationRounds(a).forEach(function(round){asArray(round.checks).forEach(function(check){if(check.graphElementId===elementId&&check.graphRevisionId===revision)rows.push(Object.assign({round:round},check))})});return rows}
function verificationCheckLabel(check){return check.label||({'snapshot-quote-locator':'引文与版本定位','ontology-class':'本体类别','identity-quote':'对象身份','relation-endpoints':'关系两端对象','ontology-predicate':'本体谓词','ontology-domain-range':'本体关系类别约束','capability-number-unit':'数值与单位','semantic-human-review':'断言含义人工核验','attribute-quote-locator':'属性引文定位','attribute-value-quote':'属性原值支持','attribute-field-context':'属性类别线索','attribute-object-context':'属性对象归属','attribute-schema':'属性结构'})[check.code]||check.code}
function objectVerificationMarkup(a,elementId){var rows=linkedObjectChecks(a,elementId),latest=rows[0],checks=asArray(latest?.checks),identity=checks.filter(function(check){return ['snapshot-quote-locator','ontology-class','identity-quote'].includes(check.code)&&!check.propertyPath}),identityStatus=identity.some(function(check){return check.status==='failed'})?'failed':identity.some(function(check){return check.status==='needs-evidence'})?'needs-evidence':identity.length?'passed':'not-run';return '<details class="object-verification"><summary>核验详情'+(latest?.machineStatus==='failed'?' · 检查失败':latest?.machineStatus==='needs-evidence'?' · 待补证':'')+'</summary><p>对象与属性核验汇总：'+esc(latest?validationStatusLabel(latest.machineStatus)+' · '+validationStatusLabel(latest.status):'尚未执行')+'</p><p>'+(latest&&identity.length?'对象身份检查：'+esc(validationStatusLabel(identityStatus))+'。':'')+'汇总包含各项属性；属性分别显示检查结果与失败原因。事实真实性与来源独立性仍需人工核验。</p>'+(latest?'<ul>'+checks.map(function(check){return '<li>'+esc(verificationCheckLabel(check))+' · '+esc(validationStatusLabel(check.status))+(check.propertyPath?'<small>属性路径：'+esc(check.propertyPath)+'</small>':'')+'<small>'+esc(check.detail||'')+'</small></li>'}).join('')+'</ul><span>图谱 '+esc(latest.graphRevisionId)+' · '+esc(shortTime(latest.round.createdAt))+'</span>':'<button type="button" class="filter-chip" data-run-validation="'+esc(assessmentId(a))+'">执行此版本对象核验</button>')+'</details>'}
function objectNeighborhoodMarkup(a,node){var records=extractedRecords(a),byId={};records.nodes.forEach(function(n){byId[n.id]=n});var edges=records.edges.filter(function(e){return e.from===node.id||e.to===node.id});if(!edges.length)return workbenchMessageMarkup('关联记录 0','<p>当前提取尚无关联记录。</p>');return '<div class="object-neighborhood"><b>关联关系与事件 · '+edges.length+'</b>'+(edges.length?edges.map(function(e){return '<button type="button" class="evidence-ref-button" data-graph-edge="'+esc(e.id)+'" data-graph-report="'+esc(assessmentId(a))+'">'+esc((byId[e.from]?.label||e.from)+' → '+relationLabel(e.relation)+' → '+(byId[e.to]?.label||e.to))+'</button>'}).join(''):'<span>当前提取尚无关联记录</span>')+'</div>'}
function findingPropertyLinksMarkup(a,finding){var graph=graphData(a),revision=graph?.graphRevisionId||graph?.id,findingNode=asArray(graph?.nodes).find(function(node){return node.type==='TrendHypothesis'&&(node.id==='finding:'+finding.id||node.payload?.findingId===finding.id||node.label===finding.title)}),refs=asArray(finding.objectPropertyRefs||finding.attributeRefs||findingNode?.payload?.objectPropertyRefs),links=[];refs.forEach(function(ref){if(!ref||ref.graphRevisionId&&ref.graphRevisionId!==revision)return;var node=asArray(graph?.nodes).find(function(n){return n.id===(ref.graphNodeId||ref.graphElementId||ref.objectId)}),row=node&&groundedObjectProperties(node).find(function(row){return row.propertyPath===ref.propertyPath});if(row)links.push('<button type="button" class="evidence-ref-button" data-object-property-index="'+row.index+'" data-object-property-node="'+esc(node.id)+'" data-graph-report="'+esc(assessmentId(a))+'">'+esc(node.label+' · '+propertyNameLabel(row.attribute.name))+' →</button>')});return links.length?'<div class="finding-property-links"><b>关联对象属性断言</b>'+links.join('')+'</div>':workbenchMessageMarkup('属性关联待补','<p>尚未绑定实际对象属性断言；共享出处不能作为该属性的验证依据。</p>','warning')}
 function ontologyPredicateLabel(predicate){return ({hasCapability:'具备能力',dependsOn:'依赖',constrainedBy:'受限于',adopts:'采用',drives:'推动',requires:'需要',implies:'意味着',supports:'支持',hasDecisionAssertion:'形成决策断言',impliesManufacturingRequirement:'对应制造要求'})[String(predicate||'')]||String(predicate||'关系未标注')}
 function findingOntologyClaims(finding){return asArray(finding?.ontologyClaims||finding?.decisionAssertions).filter(function(claim){return claim&&typeof claim==='object'&&String(claim.subject||'').trim()&&String(claim.predicate||claim.relation||'').trim()&&String(claim.object||claim.objectValue||'').trim()})}
 function decisionAssertionMarkup(finding){
   var claims=findingOntologyClaims(finding);
   if(!claims.length)return '<div class="decision-assertions decision-assertions-empty"><span class="decision-assertions-label">本体决策断言</span><span class="decision-assertions-gap">本分析版本没有结构化断言，请重新运行趋势研判生成主体—关系—客体结果</span></div>';
   return '<div class="decision-assertions"><div class="decision-assertions-head"><b>本体决策断言</b><span>'+claims.length+' 条 · 模型提出，待人工审核</span></div>'+claims.map(function(claim){
     var predicate=claim.predicate||claim.relation,metrics=asArray(claim.metrics),requirements=asArray(claim.manufacturingRequirements||claim.manufacturingImpact),gaps=asArray(claim.evidenceGaps);
     return '<article class="decision-assertion"><div class="decision-assertion-row"><strong>'+esc(claim.subject)+'</strong><span class="decision-arrow">→</span><b>'+esc(ontologyPredicateLabel(predicate))+'</b><span class="decision-arrow">→</span><strong>'+esc(claim.object||claim.objectValue)+'</strong><span class="confidence-label">'+esc(confidenceLabel(claim))+'</span></div>'+(claim.decisionImpact?'<p class="decision-assertion-impact">决策含义：'+esc(claim.decisionImpact)+'</p>':'')+((metrics.length||requirements.length||gaps.length)?'<details class="decision-assertion-details"><summary>指标、制造影响与证据缺口</summary>'+(metrics.length?'<div><b>量化指标</b>'+metrics.map(function(metric){return '<span>'+esc(metric.label||'指标')+'：'+esc(fieldText(metric.value))+(metric.unit?' '+esc(metric.unit):'')+(metric.conditions?' · '+esc(metric.conditions):'')+'</span>'}).join('')+'</div>':'')+(requirements.length?'<div><b>制造要求</b>'+requirements.map(function(item){return '<span>'+esc(fieldText(item))+'</span>'}).join('')+'</div>':'')+(gaps.length?'<div><b>证据缺口</b>'+gaps.map(function(item){return '<span>'+esc(fieldText(item))+'</span>'}).join('')+'</div>':'')+'</details>':'')+'</article>';
   }).join('')+'</div>';
 }
 function evidenceChartMarkup(a,title,kind,groups,note){var max=Math.max(1,...groups.map(function(g){return g.count}));return '<article class="evidence-chart"><div class="workbench-content-heading"><h5>'+esc(title)+'</h5>'+workbenchMessageMarkup('图表口径','<p>'+esc(note)+'</p>')+'</div><div class="evidence-chart-bars">'+groups.map(function(g){return '<button type="button" class="chart-bar" data-chart-kind="'+kind+'" data-chart-value="'+esc(g.value)+'" data-chart-report="'+esc(assessmentId(a))+'" aria-label="'+esc(g.label+'，'+g.count+' 条，查看证据')+'"><span>'+esc(g.label)+'</span><i style="width:'+Math.max(2,g.count/max*100)+'%"></i><b>'+g.count+'</b></button>'}).join('')+'</div></article>'}
function chartGroups(rows,key,label){var counts=new Map();rows.forEach(function(row){var k=key(row);counts.set(k,(counts.get(k)||0)+1)});return Array.from(counts.entries()).sort(function(a,b){return a[0].localeCompare(b[0])}).map(function(r){return {value:r[0],label:label?label(r[0]):r[0],count:r[1]}})}
function evidenceSufficiencyMarkup(a){
  var sources=asArray(a.sourceSnapshot),metrics=capabilityMetrics(a),roles=new Set(),domains=new Set(),hashes=new Set(),duplicateHashes=0;
  sources.forEach(function(s){var p=sourceProvenance(s),role=p.sourceRole||'unknown',domain=p.publisherDomain||'';roles.add(role);if(domain)domains.add(domain);var hash=String(s.contentHash||'');if(hash){if(hashes.has(hash))duplicateHashes++;else hashes.add(hash)}});
  var months=new Set(sources.map(function(s){return String(s.publishedAt||'').slice(0,7)}).filter(Boolean));
  var hasTest=metrics.some(function(m){return ['reported-test','reported-observation'].includes(m.basis)}),hasIndependentRole=['evaluation-report','procurement-record','secondary-report','research-analysis'].some(function(r){return roles.has(r)});
  var gates={volume:sources.length>=20,roles:roles.size>=3,domains:domains.size>=3,dates:months.size>=3,independent:hasIndependentRole,test:hasTest,duplicates:duplicateHashes===0};
  var passed=Object.keys(gates).filter(function(k){return gates[k]}).length,level=passed>=7?'A · 证据较充分':passed>=5?'B · 可形成方向性判断':passed>=3?'C · 仅有方向性线索':'D · 证据不足',tone=passed>=7?'info':passed>=3?'warning':'error',gaps=[];
  if(!gates.volume)gaps.push('分析材料少于 20 条');if(!gates.roles)gaps.push('来源类别少于 3 类');if(!gates.domains)gaps.push('独立出版域少于 3 个');if(!gates.dates)gaps.push('有效日期覆盖少于 3 个时间点');if(!gates.independent)gaps.push('缺少独立试验、采购或研究来源');if(!gates.test)gaps.push('缺少公开试验或独立观测依据');if(!gates.duplicates)gaps.push('存在重复内容哈希，材料数需按独立来源折算');
  return workbenchMessageMarkup('研判证据等级 · '+level,'<p>采编材料 '+sources.length+' 条 · 日期覆盖 '+months.size+' 个时间点 · 来源类别 '+roles.size+' 类 · 独立出版域 '+domains.size+' 个 · 可量化指标 '+metrics.length+' 项。</p>'+(gaps.length?'<p>当前缺口：'+esc(gaps.join('；'))+'。结论仅适合作为方向性研判，暂不宜形成能力排名或确定性增长曲线。</p>':'<p>已达到当前趋势研判门槛，仍需人工核验来源独立性、转载关系与指标可比性。</p>'),tone,'evidence-sufficiency')}function trendDashboardMarkup(a){var sources=asArray(a.sourceSnapshot),metrics=capabilityMetrics(a),dated=chartGroups(sources,function(s){return String(s.publishedAt||'').slice(0,7)||'未记录'}),roles=chartGroups(sources,function(s){return sourceProvenance(s).sourceRole||'unknown'},sourceRoleLabel),bases=chartGroups(metrics,function(m){return m.basis||'unknown'},function(v){return ({'reported-test':'报道测试','manufacturer-claim':'厂商声明','program-target':'项目目标','reported-observation':'报道记录',unknown:'未记录'})[v]||v});return '<section class="report-section trend-evidence-dashboard"><div class="report-section-heading"><h4>图表与分析证据</h4><span class="section-status muted">'+sources.length+' 条分析材料</span>'+evidenceSufficiencyMarkup(a)+workbenchMessageMarkup('指标说明','<p>指标数值、单位、试验条件与本体关联在能力证据账本中逐项显示。不同条件的指标不合成能力评分。</p>')+'</div><div class="evidence-chart-grid">'+evidenceChartMarkup(a,'材料发布日期分布','date',dated,'统计已保存资料的覆盖时间；材料数量不代表装备能力增长。')+evidenceChartMarkup(a,'采编来源性质','role',roles,'来源性质与事实核验分开，历史缺失分类按未记录处理。')+evidenceChartMarkup(a,'量化数据的依据','basis',bases,'设计目标、厂商声明与测试报道分别呈现。点击柱条查看原文。')+'</div></section>'}
function equipmentCapabilityCoverageMarkup(a){
  var nodes=extractedRecords(a).nodes.filter(function(node){return node.type==='EquipmentModel'}),required=['endurance-duration','flight-range','payload-capacity','communications-link','datalink-range','autonomy-mode','swarm-coordination','multi-uav-coordination','sensor-suite','navigation-resilience','electronic-warfare','stealth-features'],labels={'endurance-duration':'续航','flight-range':'航程','payload-capacity':'载荷','communications-link':'通信链路','datalink-range':'数据链距离','autonomy-mode':'自主模式','swarm-coordination':'蜂群协同','multi-uav-coordination':'多机协同','sensor-suite':'传感器','navigation-resilience':'导航抗干扰','electronic-warfare':'电子战','stealth-features':'隐身'};
  if(!nodes.length)return '';
   var rows=nodes.map(function(node){var summary=objectAttributeSummary(node),covered=required.filter(function(key){return summary.grounded.some(function(row){return String(row.attribute.attributeKey||row.attribute.name)===key})}),draft=required.filter(function(key){return summary.rows.some(function(row){return String(row.attribute.attributeKey||row.attribute.name)===key})&&!covered.includes(key)}),missing=required.filter(function(key){return !covered.includes(key)&&!draft.includes(key)});return '<tr><th>'+esc(node.label)+'</th><td>'+covered.length+' / '+required.length+'</td><td>'+esc(covered.map(function(key){return labels[key]}).join(' · ')||'尚无已核对能力字段')+'</td><td>'+esc(draft.map(function(key){return labels[key]}).join(' · ')||'—')+'</td><td>'+esc(missing.map(function(key){return labels[key]}).join(' · ')||'—')+'</td></tr>'}).join('');
   return '<section class="report-section equipment-capability-coverage"><div class="report-section-heading"><h4>装备对象能力覆盖</h4><span class="section-status pending">'+nodes.length+' 个装备对象</span>'+workbenchMessageMarkup('量化边界','<p>已保存只统计带原文引文的属性；草案表示模型提取到字段但引文校验尚未通过；未记录表示当前材料没有该能力字段。缺失字段不按 0 计入趋势。</p>')+'</div><div class="compact-capability-table"><table><thead><tr><th>装备对象</th><th>已保存 / '+required.length+'</th><th>已保存能力</th><th>草案待核</th><th>未记录</th></tr></thead><tbody>'+rows+'</tbody></table></div></section>';
}
function findingEvidenceGrade(f,a){var sources=asArray(a.sourceSnapshot),ids=new Set(listText(f.evidenceIds)),refs=sources.filter(function(s){return ids.has(String(s.itemId))}),roles=new Set(refs.map(function(s){return sourceProvenance(s).sourceRole||'unknown'})),domains=new Set(refs.map(function(s){return sourceProvenance(s).publisherDomain||s.sourceUri||s.itemId})),months=new Set(refs.map(function(s){return String(s.publishedAt||'').slice(0,7)}).filter(Boolean)),metrics=capabilityMetrics(a).filter(function(m){return ids.has(String(m.evidence?.itemId||''))}),score=(refs.length>=3?1:0)+(roles.size>=2?1:0)+(domains.size>=3?1:0)+(months.size>=2?1:0)+(metrics.length?1:0),label=score>=4?'A':score>=3?'B':score>=1?'C':'D',detail=refs.length+' 条支撑材料 · '+roles.size+' 类来源 · '+domains.size+' 个独立出版域 · '+months.size+' 个时间点'+(metrics.length?' · 含量化指标':' · 未关联量化指标'),action=label==='A'?'继续人工核验来源独立性与指标可比性':label==='B'?'补充一个独立来源或时间点后再确认':label==='C'?'优先执行对象定向补采，补齐独立来源与时间覆盖':'当前仅有线索，暂不应作为正式趋势结论';return {label:label,detail:detail,action:action,tone:label==='A'?'info':label==='B'?'warning':'error'} }
function insightLayerFor(a){
  var findings=reportFindings(a),layer=a.summary?.insightLayer;
  if(layer&&Array.isArray(layer.findings))return layer;
  return {schemaVersion:1,purpose:'decision-implication',executiveSummary:'洞察层用于表达决策含义、采用约束和补证优先级；趋势事实请查看“趋势研判”。',findings:findings.map(function(f){
    var constraints=asArray(f.manufacturingRequirements||f.manufacturingImpact).filter(Boolean);
    if(!constraints.length)constraints=['采购、制造与采用条件尚未从公开测试和交付证据中单独核验。'];
    return {id:f.id,title:f.title,decisionMeaning:'当前材料仅支持方向性判断，先用于补证与资源排序；不直接作为采用或能力结论。',adoptionConstraints:constraints,evidenceGaps:(f.nextChecks||[]).concat(f.contraryEvidence||[]),evidenceIds:f.evidenceIds,confidence:f.confidence,reviewGate:'趋势判断与决策含义均为模型草案，须逐条核对引文后确认。'};
  })};
}
function insightsMarkup(a){
  var trendFindings=reportFindings(a),layer=insightLayerFor(a),records=extractedRecords(a),rows=asArray(layer.findings);
  return '<section class="report-section insight-section"><div class="report-section-heading"><h4>综合洞察结论</h4><span class="section-status pending">'+esc(assessmentStatus(a)[0])+'</span><p class="section-note">本页呈现决策含义、采用约束与补证优先级；趋势事实和原始判断见“趋势研判”。</p></div><div class="report-executive"><p>'+esc(layer.executiveSummary||'当前材料不足以形成独立洞察。')+'</p></div><div class="insight-findings">'+rows.map(function(row){
    var f=trendFindings.find(function(item){return String(item.id)===String(row.id)})||row,ids=new Set(listText(row.evidenceIds||f.evidenceIds)),objects=records.nodes.filter(function(n){return asArray(n.payload?.evidenceRefs).some(function(ref){return ids.has(String(ref.itemId))})}),grade=findingEvidenceGrade(f,a);
    var backgroundFields=[['采购 / 产业',f.procurementIndustry],['应用影响',f.applicationImpact]].filter(function(field){return String(fieldText(field[1])||'').trim()});
    var background=backgroundFields.length?'<details class="insight-decision-details"><summary>采用与产业背景</summary><dl class="finding-fields">'+backgroundFields.map(function(field){return '<div><dt>'+field[0]+'</dt><dd>'+esc(fieldText(field[1]))+'</dd></div>'}).join('')+'</dl></details>':'';
    return '<article class="insight-finding"><div class="insight-finding-heading"><b>'+esc(row.title||f.title)+'</b><span class="confidence-label">'+esc(confidenceLabel(row))+' · 决策层</span></div>'+workbenchMessageMarkup('证据等级 · '+grade.label,'<p>'+esc(grade.detail)+'</p><p><b>补证动作：</b>'+esc(grade.action)+'</p>',''+grade.tone)+'<div class="insight-decision-field"><b>决策含义</b><p>'+esc(row.decisionMeaning||'尚未形成独立决策含义。')+'</p></div><div class="insight-decision-field"><b>采用 / 制造约束</b>'+listMarkup(row.adoptionConstraints,'尚未记录可核验约束')+'</div><div class="insight-decision-field"><b>证据缺口与复核问题</b>'+listMarkup(row.evidenceGaps,'尚未记录补证事项')+'</div><details class="insight-decision-details"><summary>结构化决策断言</summary>'+decisionAssertionMarkup(f)+'</details>'+background+workbenchMessageMarkup('审核门槛','<p>'+esc(row.reviewGate||'需逐条核对引文、来源独立性和适用条件后确认。')+'</p>','warning')+findingPropertyLinksMarkup(a,f)+'<div class="finding-evidence"><button type="button" class="evidence-ref-button" data-finding-report="'+esc(assessmentId(a))+'" data-finding-index="'+trendFindings.indexOf(f)+'">'+ids.size+' 条支撑材料 →</button>'+objects.slice(0,8).map(function(node){return '<button type="button" class="evidence-ref-button" data-object-insight="'+esc(node.id)+'" data-graph-report="'+esc(assessmentId(a))+'">'+esc(node.label)+'</button>'}).join('')+'</div></article>';
  }).join('')+'</div>'+workbenchMessageMarkup('证据边界与后续研究',listMarkup(a.summary?.gaps)+listMarkup(a.summary?.nextQuestions),'warning')+'</section>';
}
const WORKFLOW_STAGES=[
  {id:'materials',label:'采编执行',note:'采编过程、工具调用及来源、内容、证据结果。'},
  {id:'extract',label:'对象分析',note:'对象的属性、关联关系、事件与本体映射核验。'},
  {id:'graph',label:'图谱洞察',note:'全部提取对象、事件与证据的关联洞察。'},
  {id:'assessment',label:'趋势研判',note:'趋势事实、成熟度、公开指标与原始证据。'},
  {id:'admission',label:'洞察结论',note:'决策含义、采用约束、制造验证与本体知识准入。'}
];
function setWorkflowStage(stage,focus){
  if(stage==='validation')stage='extract';if(!WORKFLOW_STAGES.some(function(s){return s.id===stage}))stage='materials';
  var previous=state.activeStage;state.activeStage=stage;
  if(!['extract','graph'].includes(stage)||!['extract','graph'].includes(previous)){state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null}
  state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.selectedAdmissionId=null;state.chartEvidence=null;state.selectedGraphProperty=null;
  updateDeepLink();renderReportPanels();
  if(focus)$('workflow-tab-'+stage).focus();
}
function objectSelection(id){var selection=state.objectSelections[id]||(state.objectSelections[id]={nodes:new Set(),edges:new Set(),properties:new Set()});if(!selection.properties)selection.properties=new Set();return selection}
function extractedRecords(a){var graph=graphData(a)||{},nodes=asArray(graph.nodes).filter(function(n){return ['EquipmentModel','Organization','Technology','Program','EventRecord'].includes(n.type)&&n.payload?.extractionMethod==='llm'}),ids=new Set(nodes.map(function(n){return String(n.id)}));return {graph:graph,nodes:nodes,edges:asArray(graph.edges).filter(function(e){return e.payload?.extractionMethod==='llm'&&ids.has(String(e.from))&&ids.has(String(e.to))})}}
const GRAPH_ADMISSION_EVENT_KINDS=['TestEvent','ProcurementEvent','ProductionEvent'];
const GRAPH_ADMISSION_RELATIONS={
  manufactures:[['Organization'],['EquipmentModel']],develops:[['Organization'],['EquipmentModel','Technology','Program']],
  procures:[['Organization'],['EquipmentModel']],supplies:[['Organization'],['EquipmentModel']],
  testsEquipment:[['TestEvent'],['EquipmentModel']],usesTechnology:[['EquipmentModel','Program'],['Technology']],
  partOfProgram:[['EquipmentModel','Technology'],['Program']],participatesIn:[['Organization'],GRAPH_ADMISSION_EVENT_KINDS],
  announcedBy:[GRAPH_ADMISSION_EVENT_KINDS,['Organization']],involvesEquipment:[GRAPH_ADMISSION_EVENT_KINDS,['EquipmentModel']],
  involvesOrganization:[GRAPH_ADMISSION_EVENT_KINDS,['Organization']]
};
function graphAdmissionEvidenceIssue(element,a){
  var refs=element.payload?.evidenceRefs,sources=asArray(a.sourceSnapshot),evidenceIds=asArray(element.evidenceIds);
  if(!Array.isArray(refs)||!refs.length)return '缺少精确原文引用，需先补证。';
  for(var ref of refs){
    var source=ref&&sources.find(function(s){return s.itemId===ref.itemId});
    if(!source||!evidenceIds.includes(ref.itemId))return '引用未对应当前分析版本的来源快照。';
    if(ref.sourceUri!==source.sourceUri||ref.contentHash!==source.contentHash||ref.sourceVersion!==String(source.sourceVersion||source.contentHash||''))return '来源版本或内容指纹与快照不一致。';
    var quote=ref.quote,body=String(source.body||''),valid=false;
    if(typeof quote!=='string'||!quote.trim())return '缺少连续原文引用，需先补证。';
    if('startChar' in ref||'endChar' in ref){var chars=Array.from(body);valid=Number.isInteger(ref.startChar)&&Number.isInteger(ref.endChar)&&ref.startChar>=0&&ref.startChar<ref.endChar&&ref.endChar<=chars.length&&chars.slice(ref.startChar,ref.endChar).join('')===quote}
    else {var lines=body.split(/\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/);if(lines.length>1&&lines[lines.length-1]==='')lines.pop();valid=Number.isInteger(ref.lineStart)&&Number.isInteger(ref.lineEnd)&&ref.lineStart>=1&&ref.lineStart<=ref.lineEnd&&ref.lineEnd<=lines.length&&lines.slice(ref.lineStart-1,ref.lineEnd).join('\n')===quote}
    if(!valid)return '引用区间与保存的原文不一致，需先补证。';
  }
  return '';
}
function graphAdmissionEligibility(element,a,byId,isRelation){
  if(isRelation){
    var rule=GRAPH_ADMISSION_RELATIONS[element.relation];if(!rule)return {eligible:false,issue:'仅作研究关联：当前关系没有本体准入映射。'};
    var from=byId[element.from],to=byId[element.to];if(!from||!to)return {eligible:false,issue:'关系端点未对应当前对象记录。'};
    var fromCheck=graphAdmissionEligibility(from,a,byId,false),toCheck=graphAdmissionEligibility(to,a,byId,false);
    if(!fromCheck.eligible||!toCheck.eligible)return {eligible:false,issue:'关系端点需先具备有效类别与精确来源依据。'};
    if(!rule[0].includes(fromCheck.kind)||!rule[1].includes(toCheck.kind))return {eligible:false,issue:'仅作研究关联：端点类别与本体关系定义不匹配。'};
  }else{
    var kind=element.type==='EventRecord'?element.payload?.eventKind:element.type;
    if(!['EquipmentModel','Organization','Technology','Program'].includes(kind)&&!GRAPH_ADMISSION_EVENT_KINDS.includes(kind))return {eligible:false,issue:'事件须明确为公开测试、采购或生产活动。'};
    var label=String(element.label||'').trim();if(!label)return {eligible:false,issue:'对象名称尚未记录。'};
    if(!GRAPH_ADMISSION_EVENT_KINDS.includes(kind)&&!asArray(element.payload?.evidenceRefs).some(function(ref){return typeof ref?.quote==='string'&&ref.quote.toLowerCase().includes(label.toLowerCase())}))return {eligible:false,issue:'对象名称未出现在所引用的原文中。'};
  }
  var issue=graphAdmissionEvidenceIssue(element,a);return {eligible:!issue,issue:issue,kind:isRelation?'relation':kind};
}
function groundedObjectProperties(node){
  var attributes=asArray(node.payload?.attributes),rows=attributes.map(function(attribute,index){return {attribute:attribute,index:index,propertyPath:'payload.attributes['+index+'].value'}});
  if(node.payload?.date!==undefined)rows.push({attribute:{name:'eventDate',value:node.payload.date,evidenceRefs:node.payload.dateEvidenceRefs},index:attributes.length,propertyPath:'payload.date'});
  return rows.filter(function(row){var attribute=row.attribute;return attribute&&typeof attribute==='object'&&typeof attribute.name==='string'&&attribute.name.trim()&&attribute.value!==undefined&&attribute.value!==null&&String(attribute.value).trim()&&asArray(attribute.evidenceRefs).some(function(ref){return ref&&typeof ref==='object'&&typeof ref.quote==='string'&&ref.quote.trim()})});
}
// Keep the two states visible in the UI: an attribute can be persisted as a
// model draft while its immutable source quote is still missing or invalid.
function objectAttributeRows(node){
  var attributes=asArray(node?.payload?.attributes),rows=attributes.map(function(attribute,index){return {attribute:attribute,index:index,propertyPath:'payload.attributes['+index+'].value'}});
  if(node?.payload?.date!==undefined)rows.push({attribute:{name:'eventDate',value:node.payload.date,evidenceRefs:node.payload.dateEvidenceRefs},index:attributes.length,propertyPath:'payload.date'});
  return rows.filter(function(row){var attribute=row.attribute;return attribute&&typeof attribute==='object'&&typeof attribute.name==='string'&&attribute.name.trim()&&attribute.value!==undefined&&attribute.value!==null&&String(attribute.value).trim()});
}
function objectAttributeEvidenceCount(row){return asArray(row?.attribute?.evidenceRefs).filter(function(ref){return ref&&typeof ref==='object'&&typeof ref.quote==='string'&&ref.quote.trim()}).length}
function objectAttributeSummary(node){
  var rows=objectAttributeRows(node),grounded=rows.filter(function(row){return objectAttributeEvidenceCount(row)>0}),capabilityKeys=['endurance-duration','flight-range','payload-capacity','datalink-range','mtow','service-ceiling','speed','swarm-coordination','multi-uav-coordination','autonomy-mode','navigation-resilience','sensor-suite','target-detection','tracking-capability','electronic-warfare','stealth-features'],capability=grounded.filter(function(row){return capabilityKeys.includes(String(row.attribute.attributeKey||row.attribute.name))});
  var refs=new Set(asArray(node?.payload?.evidenceRefs).map(function(ref){return ref?.itemId||ref?.id}).concat(asArray(node?.evidenceIds))).size;
  var extraction=node?.payload?.attributeExtraction||{},status=String(extraction.status||'');
  var state=rows.length&&grounded.length?'saved':rows.length?'draft':'none';
  var label=state==='saved'?'属性已保存':state==='draft'?'已提取草案 · 引文待核':refs?'未提取属性':'未采集对象证据';
  return {rows:rows,grounded:grounded,rawCount:rows.length,groundedCount:grounded.length,capabilityCount:capability.length,sourceRefCount:refs,state:state,label:label,extractionStatus:status};
}
function propertyNameLabel(name){return ({occurredAt:'事件日期',eventDate:'事件日期',date:'日期',status:'状态',standard:'标准',interface:'接口',maturity:'成熟度',humanOversight:'人工监督',category:'类别',architecture:'架构','interface-standard':'标准与接口',interoperability:'互操作性','human-oversight':'人工监督','public-test-status':'公开试验状态','industrial-maturity':'产业成熟度','organization-role':'组织角色','public-program-role':'项目角色','program-goal':'项目目标','program-stage':'项目阶段','public-event-status':'事件状态','test-context':'试验条件','platform-type':'平台类型',generation:'代际',productionStage:'生产阶段','production-stage':'生产阶段','manufacturing-readiness':'制造准备度',mtow:'最大起飞重量',length:'机长',wingspan:'翼展','service-ceiling':'升限',speed:'速度','endurance-duration':'续航时间','flight-range':'飞行航程','payload-capacity':'载荷容量','payload-types':'载荷类型','sensor-suite':'传感器套件','communications-link':'通信链路','datalink-range':'数据链距离','satellite-communication':'卫星通信','autonomy-mode':'自主模式','navigation-resilience':'导航抗干扰','swarm-coordination':'蜂群协同','multi-uav-coordination':'多机协同','manned-unmanned-teaming':'有人-无人协同','target-detection':'目标探测','tracking-capability':'目标跟踪','electronic-warfare':'电子战能力','stealth-features':'隐身特征','launch-recovery':'发射 / 回收','operating-environment':'运行环境','procurement-stage':'采购阶段','test-status':'试验状态','evidence-date':'证据日期',participants:'参与方'})[name]||name}
function objectMediaEvidenceRecords(node,assessment){
  var stored=asArray(node?.payload?.mediaEvidence).filter(function(ref){return ref&&safeUrl(ref.imageUrl)});
  if(stored.length||!assessment)return stored;
  // Compatibility for graph revisions created before mediaEvidence was added.
  // This is response-only enrichment: it never mutates the immutable graph.
  var payload=node?.payload||{},label=String(node?.label||'').trim(),cited=new Set(asArray(node?.evidenceIds).map(String));
  asArray(payload.evidenceRefs).forEach(function(ref){if(ref?.itemId)cited.add(String(ref.itemId))});
  var byId=new Map();
  asArray(assessment.sourceSnapshot).concat(assessmentEvidence(assessment)).forEach(function(source){
    if(!source||typeof source!=='object')return;
    var id=source.itemId||source.id;if(id&&!byId.has(String(id)))byId.set(String(id),source);
  });
  var records=[],seen=new Set();
  byId.forEach(function(source,itemId){
    var provenance=source.provenance&&typeof source.provenance==='object'?source.provenance:{},refs=asArray(provenance.mediaReferences),retrievalKind=String(provenance.retrievalKind||''),metadataText=[source.title].concat(refs.map(function(ref){return ref?.caption||''})).join(' ').toLowerCase(),exactMatch=!!label&&metadataText.indexOf(label.toLowerCase())>=0;
    if(!cited.has(String(itemId))&&!(retrievalKind==='search-discovery-only'&&exactMatch))return;
    refs.forEach(function(ref){
      if(!ref||!safeUrl(ref.imageUrl))return;
      var image=safeUrl(ref.imageUrl),sourceUrl=safeUrl(ref.sourceUrl||source.sourceUri||source.itemUri||source.url);if(!sourceUrl)return;
      var key=image+'|'+sourceUrl;if(seen.has(key))return;seen.add(key);
      records.push({imageUrl:image,sourceUrl:sourceUrl,sourceItemId:String(itemId),sourceTitle:source.title||'',sourceName:source.sourceName||'',caption:ref.caption||'',mediaKind:ref.mediaKind||'unclassified-image',captureDate:ref.captureDate||null,publishedDate:ref.publishedDate||source.publishedAt||null,verificationStatus:ref.verificationStatus||'unverified',matchBasis:cited.has(String(itemId))?'cited-source-media':'exact-label-in-image-metadata',reviewStatus:'pending-review',supportScope:'对象图片候选；需人工核对对象身份、时间与出处'});
    });
  });
  return records.slice(0,24);
}
function objectMediaEvidenceMarkup(node,assessment){
  var records=objectMediaEvidenceRecords(node,assessment);
  if(!records.length)return '';
  return '<details class="object-media-evidence"><summary>对象图片 / 影像证据 · '+records.length+' 个候选</summary><div class="object-media-list">'+records.map(function(ref){
    var image=safeUrl(ref.imageUrl),source=safeUrl(ref.sourceUrl),status=ref.verificationStatus==='verified'?'已核验':'待人工核验';
    return '<figure class="object-media-item"><a href="'+esc(image)+'" target="_blank" rel="noopener noreferrer"><img src="'+esc(image)+'" loading="lazy" alt="'+esc(ref.caption||node.label||'对象图片候选')+'" referrerpolicy="no-referrer"></a><figcaption><b>'+esc(ref.caption||'未提供图注')+'</b><span>'+esc(ref.sourceName||ref.sourceTitle||'来源未记录')+'</span><span>'+esc(status)+' · '+esc(ref.mediaKind||'未分类图片')+'</span><span>'+esc(ref.matchBasis==='exact-label-in-image-metadata'?'图片检索元数据命中对象名':'来自对象引用来源')+'</span>'+(source?'<a href="'+esc(source)+'" target="_blank" rel="noopener noreferrer">打开来源页面 ↗</a>':'')+'</figcaption></figure>';
  }).join('')+'</div><p class="object-media-note">图片只作为对象识别线索和来源证据候选，不自动证明型号、属性或作战能力；核验后才能进入本体准入。</p></details>';
}
function objectAttributes(node){var payload=node.payload||{},rows=[];if(payload.eventKind)rows.push(['事件类型',payload.eventKind]);groundedObjectProperties(node).forEach(function(row){var attribute=row.attribute;rows.push([propertyNameLabel(attribute.name),fieldText(attribute.value)+(attribute.unit?' '+attribute.unit:''),row.index,attribute.conditions,row.propertyPath])});return rows}
let PROPERTY_PREDICATES=[['reportedStandard','公开标准'],['reportedInterface','公开接口'],['reportedArchitecture','公开架构'],['reportedHumanOversight','人工监督'],['reportedTestStatus','公开试验状态'],['reportedMaturity','公开成熟度'],['reportedContractStage','合同阶段'],['reportedEventDate','事件日期']];
async function loadAttributePredicates(){try{var result=await requestIntelligence('/attribute-predicates'),catalog=asArray(result.attributePredicates).filter(function(row){return row&&typeof row.id==='string'&&typeof row.label==='string'});if(catalog.length){state.attributePredicates=catalog;PROPERTY_PREDICATES=catalog.map(function(row){return [row.id,row.label]});renderAssessments()}}catch(error){}}
function graphPropertyKey(nodeId,path){return JSON.stringify([String(nodeId),String(path)])}
function propertyPredicate(name){var mapped=asArray(state.attributePredicates).find(function(row){return asArray(row.aliases).some(function(alias){return String(alias).toLowerCase()===String(name).toLowerCase()})});return mapped?mapped.id:({standard:'reportedStandard',标准:'reportedStandard',interface:'reportedInterface',接口:'reportedInterface',architecture:'reportedArchitecture',架构:'reportedArchitecture',humanOversight:'reportedHumanOversight',人工监督:'reportedHumanOversight',testStatus:'reportedTestStatus',公开试验状态:'reportedTestStatus',maturity:'reportedMaturity',成熟度:'reportedMaturity',contractStage:'reportedContractStage',合同阶段:'reportedContractStage',eventDate:'reportedEventDate',date:'reportedEventDate'})[name]||''}
function predicateRecommendation(attribute){
  var suggestion=attribute&&attribute.ontologyPredicateSuggestion;
  if(suggestion&&typeof suggestion==='object'&&typeof suggestion.id==='string'&&suggestion.id.trim()){
    var modelPair=PROPERTY_PREDICATES.find(function(pair){return pair[0]===suggestion.id});
    return {id:suggestion.id,label:suggestion.label||(modelPair&&modelPair[1])||suggestion.id,confidence:suggestion.confidence||'low',reason:suggestion.reason||'',alternatives:asArray(suggestion.alternatives),source:'model'};
  }
  var fallback=propertyPredicate(attribute?.name),pair=PROPERTY_PREDICATES.find(function(item){return item[0]===fallback});
  return fallback?{id:fallback,label:pair&&pair[1]||fallback,confidence:'low',reason:'按属性名称与已声明别名自动匹配，仍需核对原文语义。',alternatives:[],source:'catalog'}:null;
}
function predicateConfidenceLabel(value){return ({high:'高',medium:'中',low:'低'})[String(value||'').toLowerCase()]||'待定'}
function predicateRecommendationMarkup(recommendation,key,selected,busy,locked){
  if(!recommendation)return '<span class="property-predicate-empty">模型未给出可用谓词推荐</span>';
  var source=recommendation.source==='model'?'模型推荐':'规则匹配',alternatives=recommendation.alternatives.length?' · 备选 '+recommendation.alternatives.map(function(item){var pair=PROPERTY_PREDICATES.find(function(row){return row[0]===item});return pair?pair[1]:item}).join('、'):'';
  return '<span class="property-predicate-recommendation" title="依据：'+esc(recommendation.reason||'待人工核对')+'"><b>'+esc(source)+'：'+esc(recommendation.label)+'</b><small>置信度 '+esc(predicateConfidenceLabel(recommendation.confidence))+esc(alternatives)+' · <span aria-label="推荐理由">依据</span></small></span>'+(selected?'<details class="property-predicate-edit"><summary>修改</summary><label class="property-predicate-label">改为<select data-property-mapping="'+esc(key)+'" '+(busy||locked?'disabled':'')+'><option value="">保留模型推荐</option>'+PROPERTY_PREDICATES.map(function(pair){return '<option value="'+pair[0]+'">'+pair[1]+'</option>'}).join('')+'</select></label></details>':'');
}
function claimKindLabel(value){return ({'reported-test':'报道测试结果','public-reported-test':'报道公开试验','manufacturer-claim':'厂商宣称','program-target':'项目目标','project-goal':'项目目标','procurement-record':'采购记录','source-report':'来源报道','reported-observation':'报道观察',fact:'原文事实',claim:'来源宣称',plan:'规划目标'})[value]||value||'来源未标注'}
function objectAttributeMarkup(row,node,a){
  var targetCheck=linkedObjectChecks(a,node.id)[0],propertyCheck=asArray(targetCheck?.propertyEvidence).find(function(check){return check.propertyPath===row[4]}),propertyIssues=asArray(targetCheck?.checks).filter(function(check){return typeof row[4]==='string'&&(check.propertyPath===row[4]||row[4].endsWith('.value')&&String(check.propertyPath||'').startsWith(row[4].slice(0,-6)+'.'))&&['failed','needs-evidence'].includes(check.status)}),isProperty=Number.isInteger(row[2]),attribute=isProperty?groundedObjectProperties(node).find(function(value){return value.index===row[2]})?.attribute:null,id=assessmentId(a),key=graphPropertyKey(node.id,row[4]),selection=objectSelection(id),selected=isProperty&&selection.properties.has(key),busy=reportBusy(id)||state.busy,eligible=isProperty&&graphAdmissionEligibility(node,a,{},false).eligible,recommendation=predicateRecommendation(attribute),predicate=state.propertyMappings[key]||recommendation?.id||'',candidate=isProperty&&ontologyAdmissions(a).find(function(c){return c.kind==='graph-property'&&!c.staleGraph&&c.graphElementId===node.id&&c.mapping?.propertyPath===row[4]&&!c.supersededByAdmissionId}),status=candidate&&({published:'已入库',accepted:'待入库','needs-evidence':'待补证',rejected:'已退回'})[candidate.status]||'待审核';
  var metadata=isProperty?workbenchMessageMarkup(propertyCheck?'本项检查：'+validationStatusLabel(propertyCheck.machineStatus):'属性详情 · 待人工核验','<p>有效时间：'+esc(attribute?.validTime||'未知')+'</p>'+(attribute?.claimKind?'<p>数据性质：'+esc(claimKindLabel(attribute.claimKind))+'</p>':'')+(row[3]?'<p>条件：'+esc(row[3])+'</p>':'')+(attribute?.displayValue?'<p>中文显示值由模型翻译，待人工核对；原文值分别保留。</p>':'')+(attribute?.draftMetadata?.quoteRecoveryMethod==='explicit-acronym-same-paragraph'?'<p>已补全连续引文，属性归属待审核。</p>':'')+propertyIssues.map(function(check){return '<p>'+esc(verificationCheckLabel(check))+'：'+esc(check.detail||validationStatusLabel(check.status))+'</p>'}).join('')+'<p>原文和结构检查不替代人工核验。</p>',propertyCheck?.machineStatus==='failed'?'error':propertyIssues.length?'warning':'info','object-property-metadata'):'';
  return '<div class="'+(isProperty?'object-property-row':'')+'"><dt>'+(isProperty?'<label><input type="checkbox" data-property-select="'+esc(key)+'" data-property-node="'+esc(node.id)+'" data-property-path="'+esc(row[4])+'" data-report="'+esc(id)+'" '+(selected?'checked':'')+' '+(!eligible||busy||a.status==='withdrawn'?'disabled':'')+' aria-label="选择 '+esc(node.label+' · '+row[0])+'"> '+esc(row[0])+'</label>':esc(row[0]))+'</dt><dd><span class="object-property-value">'+esc(row[1])+'</span>'+(attribute?.displayValue?'<span class="object-property-translation">中文：'+esc(attribute.displayValue)+'</span>':'')+'<div class="object-property-actions">'+metadata+(isProperty?predicateRecommendationMarkup(recommendation,key,selected,busy,a.status==='withdrawn'):'')+(attribute?.reviewRecommendation?admissionRecommendationMarkup({reviewRecommendation:attribute.reviewRecommendation}):'')+(candidate?'<button type="button" class="evidence-ref-button" data-view-admission="'+esc(first(candidate,'admissionId','id'))+'" data-admission-report="'+esc(id)+'">'+esc(status)+'属性候选 →</button>':'')+(isProperty?'<button type="button" class="evidence-ref-button object-property-source" data-object-property-index="'+row[2]+'" data-object-property-node="'+esc(node.id)+'" data-graph-report="'+esc(id)+'">属性原文 →</button>':'')+'</div></dd></div>';
}
function objectAttributeExtractionMarkup(node,a){
  var metadata=node.payload?.attributeExtraction,count=groundedObjectProperties(node).length;
  if(!metadata||typeof metadata!=='object')return workbenchMessageMarkup('属性提取未记录','<p>已保存 '+count+' 项有引文属性；尚未记录逐对象属性提取结果。</p>','warning');
   var labels={'budget-skipped':'本轮预算不足，待继续提取','no-object-context':'缺少该对象的原文上下文','no-supported-attributes':'本轮未新增可核验属性','attribute-limit':'达到单对象属性上限','invalid-identity-evidence':'身份引用无效，未提取属性','invalid-existing-attributes':'原有属性结构无效',completed:'逐对象属性提取完成','no-explicit-attributes':'来源未明确给出可保留属性',failed:'逐对象属性提取失败','skipped-budget':'本轮预算不足，待继续提取','no-source-context':'缺少该对象的原文上下文','not-configured':'尚未配置属性提取模型'},status=String(metadata.status||''),label=labels[status]||'逐对象属性提取状态待核对',stats=[];
   if(status==='no-supported-attributes'&&count)label='本轮未新增属性 · 已保留 '+count+' 项已有属性';
  [['proposedCount','模型提出'],['retainedCount','通过出处与结构检查'],['rejectedCount','未通过检查']].forEach(function(pair){if(Number.isInteger(metadata[pair[0]])&&metadata[pair[0]]>=0)stats.push(pair[1]+' '+metadata[pair[0]])});
  var fieldLabels={category:'类别','platform-type':'平台类型',generation:'代际',architecture:'架构','interface-standard':'标准与接口',interoperability:'互操作性','human-oversight':'人工监督','public-test-status':'公开试验状态','industrial-maturity':'产业成熟度','production-stage':'生产阶段','manufacturing-readiness':'制造准备度',mtow:'最大起飞重量',length:'机长',wingspan:'翼展','service-ceiling':'升限',speed:'速度','endurance-duration':'续航时间','flight-range':'飞行航程','payload-capacity':'载荷容量','payload-types':'载荷类型','sensor-suite':'传感器套件','communications-link':'通信链路','datalink-range':'数据链距离','satellite-communication':'卫星通信','autonomy-mode':'自主模式','navigation-resilience':'导航抗干扰','swarm-coordination':'蜂群协同','multi-uav-coordination':'多机协同','manned-unmanned-teaming':'有人-无人协同','target-detection':'目标探测','tracking-capability':'目标跟踪','electronic-warfare':'电子战能力','stealth-features':'隐身特征','launch-recovery':'发射 / 回收','operating-environment':'运行环境','procurement-stage':'采购阶段','test-status':'试验状态','evidence-date':'证据日期',participants:'参与方'},coverage=metadata.coverage||{},states=asArray(coverage.fieldStates),rejections=asArray(metadata.rejectedProposals),reasonLabels={'invalid-schema':'结构不符合要求','unknown-source':'来源未在保存材料中','invalid-quote':'引文缺失或无效','quote-not-in-object-window':'引文不在该对象原文窗口','unsupported-object-context':'无法证明属性属于此对象','unsupported-name-or-value':'原始值缺少逐字支持','unsupported-qualifier':'单位或条件没有原文支持','snapshot-locator-mismatch':'引用与来源快照不一致','attribute-limit':'达到属性数量上限','output-limit':'输出超过本轮上限','unsupported-inference':'分析推断不能作为原文属性'};
  return workbenchMessageMarkup(label+(count?' · '+count+' 项属性':''),'<b>'+esc(label)+'</b>'+(stats.length?'<span>'+esc(stats.join(' · '))+'</span>':'')+(metadata.model?'<span>'+esc(metadata.model)+'</span>':'')+(states.length?'<details class="attribute-coverage"><summary>属性模板覆盖 · '+states.filter(function(field){return field.state==='retained-draft'}).length+' / '+states.length+'</summary><p>仅核对本次读取的对象正文；“未提取”不代表该属性不存在。</p><div>'+states.map(function(field){return '<span>'+esc(fieldLabels[field.attributeKey]||field.attributeKey)+'：'+esc(field.state==='retained-draft'?'保留草案':'尚未提取')+'</span>'}).join('')+'</div></details>':'')+(rejections.length?'<details class="attribute-rejections"><summary>未采纳的模型草案 · '+rejections.length+'</summary><p>以下内容用于解释拒绝原因，未作为对象事实保存。</p>'+rejections.map(function(proposal){return '<article><b>'+esc(proposal.name||proposal.attributeKey||'未命名属性')+'</b><p>提出值：'+esc(fieldText(proposal.value))+'</p><small>'+esc(reasonLabels[proposal.reason]||proposal.reason||'未通过检查')+'</small></article>'}).join('')+'</details>':'')+(listText(metadata.gaps).length?listMarkup(metadata.gaps):'')+'<span>检查通过仅表示原文与结构可核对，仍待人工确认。</span>'+(a&&((metadata.gaps&&metadata.gaps.length)||!['completed','attribute-limit'].includes(status))?'<button type="button" class="filter-chip" data-follow-up-plan="'+esc(assessmentId(a))+'" data-follow-up-object="'+esc(node.id)+'">针对该对象查看补采计划 →</button>':''),['completed','attribute-limit'].includes(status)?'info':status==='failed'?'error':'warning','object-extraction-status');
}
function selectedPropertyTarget(a,node){
  var selection=state.selectedGraphProperty,graph=graphData(a);if(!selection)return null;
  var row=node&&groundedObjectProperties(node).find(function(row){return row.index===selection.index});
  if(selection.assessmentId!==assessmentId(a)||selection.graphRevisionId!==(graph?.graphRevisionId||graph?.id||null)||selection.nodeId!==node?.id||state.activeStage!=='extract'||!row){state.selectedGraphProperty=null;return null}
  var attribute=row.attribute;return {id:node.id+':attribute:'+row.index,label:node.label+' · '+propertyNameLabel(attribute.name),type:node.type,iri:node.iri,payload:{attributeName:propertyNameLabel(attribute.name),attributeValue:attribute.value,displayValue:attribute.displayValue,claimKind:attribute.claimKind,validTime:attribute.validTime||'未知',propertyPath:row.propertyPath,unit:attribute.unit,conditions:attribute.conditions,evidenceRefs:attribute.evidenceRefs,ontologyClass:node.payload?.ontologyClass,graphRevisionId:selection.graphRevisionId}};
}
 function relationLabel(name){return ({manufactures:'制造',manufacturedBy:'制造方',develops:'研发',procures:'采购',supplies:'供应',participatesIn:'参与',participatesInEventRecord:'参与事件',participatesInProgram:'属于项目',testsEquipment:'测试装备',usesTechnology:'采用技术',partOfProgram:'属于项目',announcedBy:'发布者',involvesEquipment:'涉及装备',involvesOrganization:'涉及组织',relatedTo:'相关',hasDecisionAssertion:'形成决策断言',impliesManufacturingRequirement:'对应制造要求',dependsOn:'依赖',constrainedBy:'受限于',hasCapability:'具备能力'})[name]||name}
function objectCategoryIconMarkup(row){
  var category=row.kind==='relation'?{id:'relation',label:'关系'}:graphCategory(row.element),label=row.kind==='relation'?'关系':graphTypeLabel(row.element.type),paths={
    equipment:'<path d="m8 1.8 5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9L8 1.8Z M2.5 4.9 8 8l5.5-3.1 M8 8v6.2"/>',
    organization:'<path d="M3 14V3h7v11 M10 7h3v7 M1.5 14.5h13 M5 5h1 M8 5h.5 M5 8h1 M8 8h.5 M5 11h1 M8 11h.5"/>',
    technology:'<rect x="4" y="4" width="8" height="8" rx="1"/><path d="M6 1.5V4 M10 1.5V4 M6 12v2.5 M10 12v2.5 M1.5 6H4 M1.5 10H4 M12 6h2.5 M12 10h2.5"/><rect x="6.5" y="6.5" width="3" height="3"/>',
    project:'<path d="M2 13V3.5h4l1.5 2H14V13H2Z"/>',
    event:'<rect x="2.5" y="3.5" width="11" height="10" rx="1"/><path d="M5 1.5v4 M11 1.5v4 M2.5 7h11 M5 9.5h2 M9 9.5h2"/>',
    relation:'<circle cx="3" cy="8" r="2"/><circle cx="12.5" cy="3.5" r="2"/><circle cx="12.5" cy="12.5" r="2"/><path d="m5 7 5.5-2.5 M5 9l5.5 2.5"/>',
    other:'<circle cx="8" cy="8" r="5.5"/><path d="M5.5 8h5 M8 5.5v5"/>'
  };
  return '<span class="object-category-icon graph-category-'+esc(category.id)+'" role="img" aria-label="'+esc(label)+'" title="'+esc(label)+'"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(paths[category.id]||paths.other)+'</svg></span>';
}
function extractionMarkup(a){
  var id=assessmentId(a),records=extractedRecords(a),selection=objectSelection(id),filter=state.objectFilters[id]||{},type=filter.type||'all',confidence=filter.confidence||'all',query=(filter.query||'').toLowerCase(),busy=reportBusy(id)||state.busy,admissions=ontologyAdmissions(a),byId={};asArray(records.graph.nodes).forEach(function(n){byId[String(n.id)]=n});
  var rows=records.nodes.map(function(n){return {kind:n.type==='EventRecord'?'event':'object',element:n,label:n.label,detail:objectAttributes(n)}}).concat(records.edges.map(function(e){return {kind:'relation',element:e,label:(byId[e.from]?.label||e.from)+' → '+relationLabel(e.relation)+' → '+(byId[e.to]?.label||e.to),detail:[['关系',relationLabel(e.relation)],['起点',byId[e.from]?.label||e.from],['终点',byId[e.to]?.label||e.to]]}}));
  rows.forEach(function(row){row.admission=graphAdmissionEligibility(row.element,a,byId,row.kind==='relation')});
  var eligibleNodes=new Set(rows.filter(function(row){return row.kind!=='relation'&&row.admission.eligible}).map(function(row){return String(row.element.id)})),eligibleEdges=new Set(rows.filter(function(row){return row.kind==='relation'&&row.admission.eligible}).map(function(row){return String(row.element.id)}));
  selection.nodes.forEach(function(id){if(!eligibleNodes.has(id))selection.nodes.delete(id)});selection.edges.forEach(function(id){if(!eligibleEdges.has(id))selection.edges.delete(id)});
  var eligibleProperties=new Set();records.nodes.forEach(function(node){if(eligibleNodes.has(String(node.id)))groundedObjectProperties(node).forEach(function(row){eligibleProperties.add(graphPropertyKey(node.id,row.propertyPath))})});selection.properties.forEach(function(key){if(!eligibleProperties.has(key))selection.properties.delete(key)});
  var visible=rows.filter(function(r){return (type==='all'||r.kind===type)&&(confidence==='all'||r.element.payload?.confidence===confidence)&&(!query||r.label.toLowerCase().includes(query))}),selectable=visible.filter(function(r){return r.admission.eligible});
  var attributePending=pendingAttributeNodes(a),activeJob=activeExtractionJob(id),progress=activeJob?.progress||{},startError=state.extractionStartErrors?.[id],extractLabel=activeJob?(activeJob.status==='queued'?'等待自动提取':activeJob.status==='interrupted'?'等待恢复提取':'提取中 '+(progress.processedObjects??0)+' / '+(progress.totalObjects??'—')):busy?'读取中…':startError?'自动提取未启动 · 重试':records.nodes.length?'重新提取对象与属性':'启动对象与属性提取';
  var selectionActions='<span class="object-selection-actions" aria-label="对象准入操作"><button type="button" class="filter-chip" data-select-objects="'+esc(id)+'" '+(!selectable.length||busy||a.status==='withdrawn'?'disabled':'')+'>'+(selectable.length&&selectable.every(function(r){return (r.kind==='relation'?selection.edges:selection.nodes).has(String(r.element.id))})?'取消当前筛选选择':'选择当前筛选')+'</button><button type="button" class="btn btn-primary" data-propose-objects="'+esc(id)+'" '+(!selection.nodes.size&&!selection.edges.size&&!selection.properties.size||busy||a.status==='withdrawn'?'disabled':'')+'>送至审核入库 →</button></span>';
  var head='<section class="report-section extraction-section" aria-label="对象分析"><div class="object-toolbar extraction-toolbar"><label>记录类型<select data-object-filter="type" data-report="'+esc(id)+'"><option value="all">全部</option><option value="object" '+(type==='object'?'selected':'')+'>对象与属性</option><option value="event" '+(type==='event'?'selected':'')+'>事件</option><option value="relation" '+(type==='relation'?'selected':'')+'>关系</option></select></label><label>提取置信度<select data-object-filter="confidence" data-report="'+esc(id)+'">'+[['all','全部'],['high','高'],['medium','中'],['low','低']].map(function(r){return '<option value="'+r[0]+'" '+(confidence===r[0]?'selected':'')+'>'+r[1]+'</option>'}).join('')+'</select></label><label class="object-search-filter">查找<input data-object-filter="query" data-report="'+esc(id)+'" value="'+esc(filter.query||'')+'" placeholder="对象或关系名称"></label><div class="extraction-actions"><button type="button" class="filter-chip '+(startError?'workbench-action-error':'')+'" data-extract-graph="'+esc(id)+'" title="'+esc(startError||'识别对象后自动逐对象提取属性、关系与事件；此按钮用于重试或重新提取')+'" '+(busy||a.status==='withdrawn'?'disabled':'')+'>'+esc(extractLabel)+'</button>'+(attributePending.length?'<button type="button" class="filter-chip" data-extract-attributes="'+esc(id)+'" title="仅补提取尚未处理、预算跳过或调用失败的对象；每批最多 8 个对象" '+(busy||a.status==='withdrawn'?'disabled':'')+'>补提取属性（'+attributePending.length+'）</button>':'')+objectVerificationControls(a)+extractionJobsMarkup(a,true)+augmentationMessagesMarkup(a,['graph','validation','admissions'],true)+selectionActions+'</div></div>'+workbenchMessageMarkup('模型预审与人工核验边界','<p>DeepSeek 已完成对象身份、属性名称/原始值、单位、条件、有效时间、关系和引文定位，并为可入本体的属性提出谓词候选；机器检查可发现引文、格式、单位、类别和归属问题。</p><p>人工仍需确认对象是否确为该装备、来源是否独立可信、属性是否属于该对象、测试条件是否可比，以及是否允许写入正式本体。模型推荐只用于减少手工选择，只有人工确认后才可发布。</p>','info');
  if(state.autoActionErrors?.[id])head+=workbenchMessageMarkup('自动流程异常中断','<p>'+esc(state.autoActionErrors[id])+'</p>','error');
  if(!rows.length)return head+workbenchMessageMarkup(state.augmentationErrors?.[id]?.graph?'对象结果尚未读取':activeJob?'对象与属性提取中':'尚无对象提取','<p>'+(state.augmentationErrors?.[id]?.graph?'图谱与对象读取失败，请先重试读取，尚不能判断是否已有提取对象。':activeJob?'后台正在基于保存材料识别对象并逐项提取属性，完成后自动显示。':startError?'分析版本已保存，自动提取未启动；可使用上方重试按钮恢复。':'新模型分析会自动提取对象与属性；历史版本可使用上方按钮启动提取。')+'</p>',activeJob?'info':'warning')+'</section>';
  return head+followUpPlanMarkup(a)+'<div class="object-record-list">'+(visible.length?visible.map(function(row){var element=row.element,payload=element.payload||{},selected=(row.kind==='relation'?selection.edges:selection.nodes).has(String(element.id)),candidate=admissions.find(function(c){return c.graphElementId===element.id&&!c.staleGraph}),active=(state.selectedGraphNodeId||state.selectedGraphEdgeId)===element.id;return '<article class="object-record '+(active?'active':'')+'"><label class="object-record-check"><input type="checkbox" data-object-select="'+esc(element.id)+'" data-object-kind="'+row.kind+'" data-report="'+esc(id)+'" '+(selected?'checked':'')+' '+(!row.admission.eligible||busy||a.status==='withdrawn'?'disabled':'')+' aria-label="选择 '+esc(row.label)+'"></label><div><div class="object-record-heading"><b>'+objectCategoryIconMarkup(row)+'<span>'+esc(row.label)+'</span></b><span class="section-status muted">'+esc(confidenceLabel(payload))+' · 提取</span>'+(candidate?'<span class="section-status">'+esc(candidate.status==='published'?'已入库':'已有准入候选')+'</span>':'')+'</div>'+(row.detail.length?'<dl class="object-attribute-grid">'+row.detail.map(function(r){return objectAttributeMarkup(r,element,a)}).join('')+'</dl>':'')+(row.kind==='relation'?'':objectAttributeExtractionMarkup(element,a)+objectMediaEvidenceMarkup(element,a)+objectNeighborhoodMarkup(a,element))+objectVerificationMarkup(a,element.id)+objectImageryMarkup(a,element.id)+(!row.admission.eligible?workbenchMessageMarkup('当前不能准入','<p>'+esc(row.admission.issue)+'</p>','warning'):'')+workbenchMessageMarkup(row.kind==='relation'?'关系引文':'身份引文','<blockquote>'+esc(asArray(payload.evidenceRefs)[0]?.quote||'精确证据未记录')+'</blockquote>')+'<button type="button" class="evidence-ref-button" '+(row.kind==='relation'?'data-graph-edge':'data-graph-node')+'="'+esc(element.id)+'" data-graph-report="'+esc(id)+'">核对原文与来源 →</button></div></article>'}).join(''):workbenchMessageMarkup('筛选无匹配','<p>当前筛选没有匹配记录。若选择高置信度，须先有相应核验依据；可以查看全部候选并补证。</p>'))+'</div></section>';
}
function reportSectionsMarkup(a){
  if(state.activeStage==='extract')return extractionMarkup(a)+'<details class="linked-validation-history"><summary>本体相关核验记录与反证复核</summary>'+validationMarkup(a)+'</details>';
  if(state.activeStage==='graph')return graphMarkup(a);
  if(state.activeStage==='admission')return insightsMarkup(a)+'<details class="insight-admissions"><summary>制造选型与应用验证</summary>'+manufacturingMarkup(a)+'</details><details class="insight-admissions" open><summary>本体知识审核入库 · '+ontologyAdmissions(a).length+' 条候选</summary>'+ontologyMarkup(a)+'</details>';
  var findings=reportFindings(a);
  return '<section class="report-section"><div class="report-section-heading"><h4>趋势研判结论</h4><span class="section-status muted">'+findings.length+' 个方向</span>'+augmentationMessagesMarkup(a,['validation'])+reportScopeMarkup(a)+evidenceCoverageMarkup(a)+workbenchMessageMarkup('研究缺口与后续问题',listMarkup(a.summary?.gaps)+listMarkup(a.summary?.nextQuestions),'warning')+'</div><div class="report-executive"><b>总体研判</b><p>'+esc(a.summary?.executiveSummary||'尚无总体研判')+'</p></div><div class="report-findings">'+findings.map(function(f,i){return findingMarkup(a,f,i)}).join('')+'</div></section>'+trendDashboardMarkup(a)+equipmentCapabilityCoverageMarkup(a)+capabilityMarkup(a);
}
function renderWorkflow(){
  if(state.activeStage==='validation')state.activeStage='extract';var stage=WORKFLOW_STAGES.find(function(s){return s.id===state.activeStage})||WORKFLOW_STAGES[0];state.activeStage=stage.id;
  $('workflow-tabs').querySelectorAll('[data-workflow-tab]').forEach(function(tab){var selected=tab.dataset.workflowTab===stage.id;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1});
  $('workflow-materials').hidden=stage.id!=='materials';$('workflow-stage').hidden=stage.id==='materials';$('workflow-stage').setAttribute('aria-labelledby','workflow-tab-'+stage.id);
  $('collection-coverage').hidden=stage.id!=='materials'||!state.task;
  $('workflow-stage-heading').firstChild.nodeValue=stage.label+' ';
  $('workflow-stage-note').textContent=stage.note;$('workflow-context').textContent=stage.note;
  document.querySelector('.trend-inspector .review-panel').hidden=stage.id!=='admission';document.querySelector('.operating-panel').hidden=stage.id!=='admission';
  $('evidence-panel-title').textContent=stage.id==='materials'?'材料原文':'证据详情';
  var a=selectedAssessment(),records=a?extractedRecords(a):{nodes:[],edges:[]};document.querySelector('[data-stage-count="materials"]').textContent=asArray(state.task?.items).length;document.querySelector('[data-stage-count="extract"]').textContent=records.nodes.length+records.edges.length;document.querySelector('[data-stage-count="admission"]').textContent=a?reportFindings(a).length:0;
  var version=$('assessment-version');version.innerHTML=state.assessments.length?state.assessments.map(function(report,i){return '<option value="'+esc(assessmentId(report))+'" '+(assessmentId(report)===String(state.selectedAssessmentId)?'selected':'')+'>'+esc('版本 '+(state.assessments.length-i)+' · '+shortTime(report.createdAt)+' · '+assessmentStatus(report)[0])+'</option>'}).join(''):'<option value="">尚无分析版本</option>';
}
function updateDeepLink(){
  var url=new URL(location.href);
  if(state.task?.id)url.searchParams.set("taskId",state.task.id);else url.searchParams.delete("taskId");
  if(state.selectedAssessmentId)url.searchParams.set("assessmentId",state.selectedAssessmentId);else url.searchParams.delete("assessmentId");
  url.searchParams.set("stage",state.activeStage);
  history.replaceState(null,"",url);
}
function selectAssessment(id,findingIndex,evidenceId,capabilityIndex){state.chartEvidence=null;state.selectedGraphProperty=null;
  var changed=String(state.selectedAssessmentId)!==String(id);
  state.selectedAssessmentId=id;
  state.selectedFindingIndex=Number.isInteger(findingIndex)?findingIndex:null;
  state.selectedCapabilityIndex=Number.isInteger(capabilityIndex)?capabilityIndex:null;
  state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;
  state.evidenceFilterId=evidenceId||null;
  updateDeepLink();
  renderAssessments();
  if(changed){state.chartEvidence=null;renderReview()}
  renderEvidence();
  loadReportAugmentations(id);loadExtractionJobs(id);loadLinkedImageryEvidence(id);renderExecution();renderImageryEvidence();
}
function translateFlag(value){
  var text=fieldText(value);
  return ({"single-publisher-evidence":"单一出版域名证据，交叉验证不足","llm-proposed-human-review-required":"模型提出的草稿，必须经过人工审核","model-confidence-clamped":"模型置信度已按规则限制","source-independence-not-verified":"来源独立性尚未核验","repeated-source-text":"存在重复来源正文，不能当作独立佐证","unsubstantiated-maturity-rating-removed":"缺少原文支撑的成熟度等级已移除"}[text])||text;
}
function listMarkup(values,empty){
  var rows=asArray(values).map(translateFlag).filter(Boolean);
  return rows.length?'<ul class="report-bullets">'+rows.map(function(row){return "<li>"+esc(row)+"</li>"}).join("")+"</ul>":'<p class="not-recorded">'+esc(empty||"未提供")+"</p>";
}
function withdrawalMarkup(a){
  if(a.status!=="withdrawn")return "";
  var withdrawal=a.publicationWithdrawal||{},reason=first(withdrawal,"reason","note"),time=first(withdrawal,"withdrawnAt","at","createdAt");
  return workbenchMessageMarkup('测试发布已撤回','<p>'+esc(reason||"该报告的测试发布记录已撤回，保留原始内容供追溯。")+'</p>'+(time?'<small>'+esc(shortTime(time))+'</small>':''),'warning','withdrawal-notice');
}
function capabilityMarkup(a){
  var capability=a.summary?.capabilityAssessment||{},metrics=capabilityMetrics(a),id=assessmentId(a);
  var header='<section class="capability-section" aria-label="公开装备能力量化"><div class="capability-heading"><div><p class="eyebrow">PUBLIC CAPABILITY METRICS</p><h4>公开装备能力量化</h4></div><span class="capability-badge">证据账本</span>'+workbenchMessageMarkup('量化口径','<p>公开参数、测试与保障指标用于评估证据支持程度，不能直接等同实战效果。以下仅统计证据覆盖数量，不是战力评分。</p>')+'</div>';
  if(!metrics.length)return workbenchMessageMarkup('量化证据待补','<p>'+(a.summary?.capabilityAssessment?'当前材料没有可核验的能力数值。请补充公开参数表、测试报告或装备对象属性证据后重新生成研判。':'尚未提取能力指标。对象属性提取完成后，将把数值、单位、测试条件和精确原文带入这里；没有证据的指标保留为缺口。')+'</p>'+listMarkup(capability.gaps,'未记录额外缺口；仍需逐项核对数值、条件和来源。'),'warning','capability-status');
  var provider=typeof capability.provider==="object"?first(capability.provider,"name","id"):capability.provider;
  if(provider||capability.model)header+='<p class="capability-provider">指标提取：'+esc(String(provider||"模型").toLowerCase().includes("deepseek")?"DeepSeek":provider||"模型")+(capability.model?" · "+esc(capability.model):"")+"</p>";
  var stats=capability.statistics||{},bases={"reported-test":"报道测试结果","manufacturer-claim":"厂商宣称","program-target":"项目目标","reported-observation":"公开报道观察"};
  var count=function(key,fallback){return Number.isInteger(stats[key])&&stats[key]>=0?stats[key]:fallback};
  var coverage=[["指标",count("metricCount",metrics.length)],["装备对象",count("equipmentCount",new Set(metrics.map(function(m){return m.equipment}).filter(Boolean)).size)],["引用材料",count("citedSourceCount",new Set(metrics.map(function(m){return m.evidence?.itemId}).filter(Boolean)).size)]];
  var composition=[["报道测试结果","reportedTestCount","reported-test"],["厂商宣称","manufacturerClaimCount","manufacturer-claim"],["项目目标","programTargetCount","program-target"],["公开观察","reportedObservationCount","reported-observation"]];
  return header+(capability.scope?workbenchMessageMarkup('指标范围','<p>'+esc(capability.scope)+'</p>'):'')+'<div class="capability-coverage">'+coverage.map(function(entry){return '<span><strong>'+esc(entry[1])+'</strong>'+entry[0]+'</span>'}).join('')+'</div><div class="capability-composition">'+composition.map(function(entry){return '<span>'+entry[0]+' '+count(entry[1],metrics.filter(function(m){return m.basis===entry[2]}).length)+'</span>'}).join('')+'</div><div class="capability-metrics">'+
    (metrics.length?metrics.map(function(metric,index){
      var evidence=capabilityEvidence(a,metric,index),url=safeUrl(evidence.sourceUri),selected=String(state.selectedAssessmentId)===id&&state.selectedCapabilityIndex===index;
      var qualifier=({exact:"",approximately:"约 ","at-least":"至少 ","at-most":"至多 ","greater-than":"大于 ","less-than":"小于 "})[metric.qualifier]??"";
      var hasValue=typeof metric.value==="number"&&Number.isFinite(metric.value);
      return '<article class="capability-metric '+(selected?'active':'')+'"><div class="capability-metric-heading"><b>'+esc(metric.equipment||"未标注装备")+'</b><span class="capability-basis '+esc(metric.basis||'unknown')+'">'+esc(bases[metric.basis]||'数据性质未标注')+'</span></div><div class="capability-reading"><span>'+esc(metric.metric||"未标注指标")+'</span><strong>'+esc(hasValue?qualifier+String(metric.value):'未提供数值')+'</strong>'+(hasValue?'<span class="capability-unit">'+esc(metric.unit||'单位未标注')+'</span>':'')+'</div>'+(metric.dimension?'<p class="capability-dimension">维度：'+esc(metric.dimension)+'</p>':'')+'<dl class="capability-details"><div><dt>条件与口径</dt><dd>'+esc(metric.conditions||"来源未说明测试条件，暂不可直接比较。")+'</dd></div><div><dt>分析解读</dt><dd>'+esc(metric.assessment||"待补充解读；该数值只代表来源记录的公开参数。")+'</dd></div></dl><blockquote>'+esc(evidence.quote||"未提供精确原文，需补充证据。")+'</blockquote><div class="capability-evidence-actions"><button type="button" class="evidence-ref-button" data-capability-report="'+esc(id)+'" data-capability-index="'+index+'">查看指标证据与版本</button>'+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>':'<span>来源链接未记录</span>')+'<span>'+esc(dayLabel(evidence.publishedAt))+'</span></div>'+objectVerificationMarkup(a,'capability:'+metric.id)+'</article>';
    }).join(''):'<div class="capability-empty"><b>当前材料没有可核验的能力数值</b><p>请补充公开参数表、测试报告或保障数据后重新生成研判。</p></div>')+'</div>'+workbenchMessageMarkup('量化证据缺口',listMarkup(capability.gaps,"未记录额外缺口；仍需逐项核对数值、条件和来源。"),'warning')+'</section>';
}
function findingMarkup(a,finding,index){
  var id=assessmentId(a),refs=listText(finding.evidenceIds),allEvidence=assessmentEvidence(a);
  var selected=String(state.selectedAssessmentId)===id&&state.selectedFindingIndex===index;
  var fields=[["成熟度",finding.maturity]];
  var type=finding.analysisType==="signal"?"主题信号":finding.analysisType==="inference"?"推断":finding.analysisType==="analysis"?"分析":"未标注";
  return '<section class="finding-card '+(selected?"active":"")+'"><div class="finding-heading"><h4><span class="finding-number">'+(index+1)+
    "</span>"+esc(finding.title||"未命名方向")+'</h4><span class="finding-type">'+esc(type)+'</span><span class="confidence-label">置信度：'+esc(confidenceLabel(finding))+
     '</span></div><p class="finding-judgment">'+esc(finding.judgment||"此方向未提供判断。")+'</p>'+findingPropertyLinksMarkup(a,finding)+'<dl class="finding-fields">'+fields.map(function(field){return "<div><dt>"+field[0]+"</dt><dd>"+esc(fieldText(field[1])||"未提供")+"</dd></div>"}).join("")+ 
    '</dl>'+workbenchMessageMarkup('不确定性与后续核查','<b>不确定性</b>'+listMarkup(finding.uncertainty,"未提供不确定性说明")+'<b>下一步核查</b>'+listMarkup(finding.nextChecks,"未提供核查项")+(finding.contraryEvidence?'<b>反证 / 限制</b><p>'+esc(fieldText(finding.contraryEvidence))+'</p>':''),'warning')+'<div class="finding-evidence"><button type="button" class="filter-chip '+(selected&&!state.evidenceFilterId?"active":"")+'" data-finding-report="'+esc(id)+'" data-finding-index="'+index+'">查看本方向证据 ('+refs.length+')</button>'+
    refs.map(function(ref){
      var evidence=allEvidence.find(function(e){return String(e.id)===ref||String(e.itemId)===ref});
      var label=evidence?evidence.title||evidence.sourceName||ref:ref;
      return '<button type="button" class="evidence-ref-button" data-evidence-report="'+esc(id)+'" data-finding-index="'+index+'" data-evidence-ref="'+esc(ref)+'" title="'+esc(label)+'">'+esc(label)+"</button>";
    }).join("")+'<button type="button" class="evidence-ref-button" data-go-stage="admission">决策含义、采用约束与制造验证 → 洞察结论</button></div></section>';
}
function renderAssessments(){
  renderWorkflow();var current=selectedAssessment(),records=current?extractedRecords(current):{nodes:[],edges:[]},graph=current?graphData(current):null;$('extract-from-execution').disabled=!current||state.busy||reportBusy(current&&assessmentId(current));ui.assessmentCount.textContent=state.activeStage==='extract'?records.nodes.length+records.edges.length:state.activeStage==='graph'?asArray(graph?.nodes).length:state.activeStage==='admission'?(current?ontologyAdmissions(current).length:0):state.activeStage==='validation'?(current?validationRounds(current).length:0):state.assessments.length;
  var a=selectedAssessment();if(!a){ui.assessments.innerHTML='<div class="object-toolbar">'+workbenchMessageMarkup('尚无分析版本','<p>先在采集材料中选择来源材料并开始分析，然后逐步提取、核验和入库。</p>')+'<button type="button" class="filter-chip" data-go-stage="materials">前往采集材料 →</button></div>';return}
  var id=assessmentId(a),pair=assessmentStatus(a);
  ui.assessments.innerHTML='<article class="assessment-card selected '+pair[1]+'">'+withdrawalMarkup(a)+reportSectionsMarkup(a)+(state.activeStage==='assessment'?'<div class="report-controls"><button type="button" class="filter-chip" data-all-evidence="'+esc(id)+'">全部证据</button><button type="button" class="filter-chip capability-action" data-enrich-capability="'+esc(id)+'" '+(state.busy||a.status==='withdrawn'?'disabled':'')+'>'+(state.capabilityBusyId===id?'正在提取…':'补充能力量化')+'</button></div>':'')+'</article>';
}
ui.assessments.addEventListener("click",function(event){
  var retryAugmentation=event.target.closest('[data-retry-augmentation]');if(retryAugmentation){state.augmentationLoaded=state.augmentationLoaded||{};delete state.augmentationLoaded[retryAugmentation.dataset.retryAugmentation];loadReportAugmentations(retryAugmentation.dataset.retryAugmentation);return}
  var followTask=event.target.closest('[data-open-follow-up-task]');if(followTask){state.activeStage='materials';loadTask(followTask.dataset.openFollowUpTask);return}
  var imageryTask=event.target.closest('[data-open-imagery-task]');if(imageryTask){state.activeStage='materials';loadTask(imageryTask.dataset.openImageryTask);return}
  var follow=event.target.closest('[data-follow-up-plan]');if(follow){loadFollowUpPlan(follow.dataset.followUpPlan,follow.dataset.followUpObject);return}
  var runFollow=event.target.closest('[data-run-follow-up]');if(runFollow){runFollowUpTarget(runFollow.dataset.followReport,Number(runFollow.dataset.runFollowUp));return}
  var jobRefresh=event.target.closest('[data-refresh-extraction]');if(jobRefresh){asArray(state.extractionJobs[jobRefresh.dataset.refreshExtraction]).forEach(function(job){delete state.extractionJobErrorCounts[job.jobId]});loadExtractionJobs(jobRefresh.dataset.refreshExtraction);return}
  var nextBatch=event.target.closest('[data-next-attribute-batch]');if(nextBatch){var nextReport=state.assessments.find(function(a){return assessmentId(a)===nextBatch.dataset.nextAttributeBatch}),pending=nextReport&&pendingAttributeNodes(nextReport);if(pending?.length)startExtractionJob(assessmentId(nextReport),{extractAttributes:true,objectIds:pending.slice(0,8).map(function(node){return node.id})});return}
  var property=event.target.closest('[data-object-property-index]');if(property){
    var report=state.assessments.find(function(a){return assessmentId(a)===property.dataset.graphReport}),propertyGraph=report&&graphData(report),node=asArray(propertyGraph?.nodes).find(function(n){return n.id===property.dataset.objectPropertyNode}),index=Number(property.dataset.objectPropertyIndex);
    if(!node||!groundedObjectProperties(node).some(function(row){return row.index===index})){notify('当前属性或证据版本已变化，请重新选择。','warn');return}
    state.activeStage='extract';state.selectedAssessmentId=assessmentId(report);state.selectedGraphNodeId=node.id;state.selectedGraphEdgeId=null;state.selectedGraphProperty={assessmentId:assessmentId(report),graphRevisionId:propertyGraph.graphRevisionId||propertyGraph.id||null,nodeId:node.id,index:index};state.chartEvidence=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.selectedAdmissionId=null;
    updateDeepLink();renderAssessments();renderEvidence();revealEvidence();return;
  }
  var insightObject=event.target.closest('[data-object-insight]');if(insightObject){setWorkflowStage('extract');state.selectedGraphNodeId=insightObject.dataset.objectInsight;renderAssessments();renderEvidence();revealEvidence();return}
  var chart=event.target.closest('[data-chart-kind]');if(chart){var a=state.assessments.find(function(r){return assessmentId(r)===chart.dataset.chartReport}),kind=chart.dataset.chartKind,value=chart.dataset.chartValue,sources=asArray(a?.sourceSnapshot),ids;if(kind==='date')ids=sources.filter(function(s){return (String(s.publishedAt||'').slice(0,7)||'未记录')===value}).map(function(s){return s.itemId});else if(kind==='role')ids=sources.filter(function(s){return (sourceProvenance(s).sourceRole||'unknown')===value}).map(function(s){return s.itemId});else ids=capabilityMetrics(a).filter(function(m){return (m.basis||'unknown')===value}).map(function(m){return m.evidence?.itemId});var chartRefs=kind==='basis'?capabilityMetrics(a).map(function(m,i){return {metric:m,index:i}}).filter(function(r){return (r.metric.basis||'unknown')===value}).map(function(r){return capabilityEvidence(a,r.metric,r.index)}):sources.filter(function(source){return ids.includes(source.itemId)}).map(function(source){return Object.assign({id:source.itemId,quote:source.body||''},source)});state.chartEvidence={assessmentId:assessmentId(a),ids:ids,refs:chartRefs,label:chart.getAttribute('aria-label')};state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;renderEvidence();revealEvidence();return}
  var go=event.target.closest('[data-go-stage]');if(go){setWorkflowStage(go.dataset.goStage,true);return}
  var propose=event.target.closest('[data-propose-objects]');if(propose){buildGraphAdmissions(propose.dataset.proposeObjects);return}
  var selectObjects=event.target.closest('[data-select-objects]');if(selectObjects){var id=selectObjects.dataset.selectObjects,selection=objectSelection(id),visible=Array.from(ui.assessments.querySelectorAll('[data-object-select]:not(:disabled)')),all=visible.length&&visible.every(function(input){return input.checked});visible.forEach(function(input){var set=input.dataset.objectKind==='relation'?selection.edges:selection.nodes;if(all)set.delete(input.dataset.objectSelect);else set.add(input.dataset.objectSelect)});renderAssessments();return}
  var admissionView=event.target.closest('[data-view-admission]');if(admissionView){state.chartEvidence=null;state.selectedAdmissionId=admissionView.dataset.viewAdmission;state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;setWorkflowStage('admission');renderAssessments();renderEvidence();revealEvidence();return}
  if(handleGraphInteraction(event))return;
  var runValidation=event.target.closest("[data-run-validation]");
  if(runValidation){runReportValidation(runValidation.dataset.runValidation);return}
  var modelValidation=event.target.closest("[data-model-validation]");
  if(modelValidation){runReportValidation(modelValidation.dataset.modelValidation,"model-critique");return}
  var runAudit=event.target.closest("[data-run-ontology-audit]");
  if(runAudit){runOntologyAudit(runAudit.dataset.runOntologyAudit);return}
  var auditFindingAction=event.target.closest("[data-audit-finding-action]");
  if(auditFindingAction){reviewAuditFinding(auditFindingAction.dataset.auditFindingAction,auditFindingAction.dataset.auditFinding,auditFindingAction.dataset.auditReport);return}
  var buildAdmissions=event.target.closest("[data-build-admissions]");
  if(buildAdmissions){buildOntologyAdmissions(buildAdmissions.dataset.buildAdmissions);return}
  var admissionAction=event.target.closest("[data-admission-action]");
  if(admissionAction){reviewOntologyAdmission(admissionAction.dataset.admissionAction,admissionAction.dataset.admissionId,admissionAction.dataset.admissionReport);return}
  var enrich=event.target.closest("[data-enrich-capability]");
  if(enrich){enrichCapabilities(enrich.dataset.enrichCapability);return}
  var select=event.target.closest("[data-select-report]");
  if(select){selectAssessment(select.dataset.selectReport);return}
  var all=event.target.closest("[data-all-evidence]");
  if(all){selectAssessment(all.dataset.allEvidence);return}
  var capability=event.target.closest("[data-capability-report]");
  if(capability){selectAssessment(capability.dataset.capabilityReport,null,null,Number(capability.dataset.capabilityIndex));return}
  var finding=event.target.closest("[data-finding-report]");
  if(finding){selectAssessment(finding.dataset.findingReport,Number(finding.dataset.findingIndex));return}
  var ref=event.target.closest("[data-evidence-ref]");
  if(ref)selectAssessment(ref.dataset.evidenceReport,Number(ref.dataset.findingIndex),ref.dataset.evidenceRef);
});
ui.assessments.addEventListener("wheel",handleGraphWheel,{passive:false});
document.addEventListener("pointerdown",handleGraphPointerDown);
document.addEventListener("pointermove",handleGraphPointerMove,{passive:false});
document.addEventListener("pointerup",handleGraphPointerUp);
document.addEventListener("pointercancel",handleGraphPointerUp);
ui.assessments.addEventListener("change",function(event){
  var property=event.target.closest('[data-property-select]'),mapping=event.target.closest('[data-property-mapping]');
  if(property){var selected=objectSelection(property.dataset.report);if(property.checked)selected.properties.add(property.dataset.propertySelect);else selected.properties.delete(property.dataset.propertySelect);renderAssessments();return}
  if(mapping){state.propertyMappings[mapping.dataset.propertyMapping]=mapping.value;return}
  var input=event.target,object=input.closest('[data-object-select]'),filter=input.closest('[data-object-filter]'),confidence=input.closest('[data-verified-confidence]'),field=input.closest('[data-admission-field]');
  if(object){var selection=objectSelection(object.dataset.report),set=object.dataset.objectKind==='relation'?selection.edges:selection.nodes;if(object.checked)set.add(object.dataset.objectSelect);else set.delete(object.dataset.objectSelect);renderAssessments();return}
  if(filter){var id=filter.dataset.report;state.objectFilters[id]=state.objectFilters[id]||{};state.objectFilters[id][filter.dataset.objectFilter]=filter.value;renderAssessments();return}
  if(confidence){state.admissionConfidence[confidence.dataset.verifiedConfidence]=confidence.value;renderAssessments();return}
  if(field&&field.dataset.admissionField==='filter'){state.admissionForms[field.dataset.admissionForm]=state.admissionForms[field.dataset.admissionForm]||{};state.admissionForms[field.dataset.admissionForm].filter=field.value;renderAssessments();return}
  handleGraphFilterChange(event);
});
ui.assessments.addEventListener("input",function(event){var edit=event.target.closest('[data-property-edit]');if(edit){var candidateId=edit.dataset.propertyCandidate;state.propertyEdits[candidateId]=state.propertyEdits[candidateId]||{};state.propertyEdits[candidateId][edit.dataset.propertyEdit]=edit.value;return}var auditField=event.target.closest("[data-audit-field]");if(auditField){var findingId=auditField.dataset.auditFinding;state.auditFindingForms[findingId]=state.auditFindingForms[findingId]||{};state.auditFindingForms[findingId][auditField.dataset.auditField]=auditField.value;return}var field=event.target.closest("[data-admission-field]");if(!field)return;var id=field.dataset.admissionForm;state.admissionForms[id]=state.admissionForms[id]||{};state.admissionForms[id][field.dataset.admissionField]=field.value});
ui.assessments.addEventListener("keydown",handleGraphKeydown);
ui.evidence.addEventListener("click",handleInspectorPropertyInteraction);
function renderReview(){
  var a=selectedAssessment();
  if(!a){ui.reviewState.textContent="未选择";ui.reviewState.className="state-pill muted";ui.review.innerHTML='<div class="placeholder">选择一份研判报告后，在这里记录审核理由。</div>';return}
  var pair=assessmentStatus(a),rawStatus=String(first(a,"status","reviewStatus","state")||"").toLowerCase(),published=["published","released"].includes(rawStatus),withdrawn=rawStatus==="withdrawn",locked=published||withdrawn||state.reviewBusy,review=Object.assign({},a.review||a.latestReview||{},state.reportReviewForms[assessmentId(a)]||{}),revision=a.revision;
  ui.reviewState.textContent=pair[0];ui.reviewState.className="state-pill "+pair[1];
  ui.review.innerHTML='<div class="review-meta"><span>整份报告 · 修订 '+esc(revision)+'</span><span>'+esc(a.title||"趋势研判报告")+'</span></div>'+withdrawalMarkup(a)+
    '<label>审核人<input id="reviewer" value="'+esc(first(review,"reviewer")||"operator")+'" '+(locked?"disabled":"")+'></label>'+
    '<label>审核理由<textarea id="review-reason" '+(locked?"disabled":"")+' placeholder="说明整份报告的证据是否支持结论、缺少的材料及发布依据。">'+esc(first(review,"reason","comment")||"")+
    '</textarea></label><div class="review-actions"><button type="button" class="btn btn-primary" data-review-action="accept" '+(locked?"disabled":"")+'>确认通过</button>'+
    '<button type="button" class="btn btn-danger" data-review-action="reject" '+(locked?"disabled":"")+'>退回</button><button type="button" class="btn btn-quiet" data-review-action="request-more-evidence" '+(locked?"disabled":"")+
    '>要求补充证据</button></div><button type="button" class="btn btn-primary publish-action" data-publish-assessment '+(locked||rawStatus!=="accepted"?"disabled":"")+
    '>发布报告（不写入本体）</button><p class="review-hint">'+(withdrawn?"该测试发布已撤回，仅保留审计记录，不可再次审核或发布。":"审核与发布针对整份报告。本体可信入库需在知识库治理流程中另行完成。")+"</p>";
  ui.review.querySelectorAll('input,textarea').forEach(function(input){input.addEventListener('input',function(){var id=assessmentId(a);state.reportReviewForms[id]=state.reportReviewForms[id]||{};state.reportReviewForms[id][input.id==='reviewer'?'reviewer':'reason']=input.value})});
  ui.review.querySelectorAll("[data-review-action]").forEach(function(btn){btn.addEventListener("click",function(){reviewAssessment(btn.dataset.reviewAction)})});
  ui.review.querySelector("[data-publish-assessment]").addEventListener("click",publishAssessment);
}
function graphObjectAttributeDetailsMarkup(a,target){var rows=objectAttributeRows(target),summary=objectAttributeSummary(target),graph=graphData(a)||{},revision=graph.graphRevisionId||graph.id||'';return '<details class="graph-object-attributes" open><summary>该对象的属性 · '+summary.rawCount+'（已保存引文 '+summary.groundedCount+'）</summary>'+(rows.length?'<dl>'+rows.map(function(row){var evidenceCount=objectAttributeEvidenceCount(row),attribute=row.attribute;return '<div><dt>'+esc(propertyNameLabel(attribute.name))+'</dt><dd>'+esc(fieldText(attribute.value))+(attribute.unit?' '+esc(attribute.unit):'')+'<small>属性状态：'+(evidenceCount?'已保存原文引文 '+evidenceCount+' 条':'已提取草案，暂无可核对引文')+' · 有效时间：'+esc(attribute.validTime||'未知')+'</small>'+(evidenceCount?'<button type="button" class="evidence-ref-button" data-inspector-property-index="'+row.index+'" data-inspector-property-node="'+esc(target.id)+'" data-inspector-property-report="'+esc(assessmentId(a))+'" data-inspector-property-revision="'+esc(revision)+'">核对属性引文 →</button>':'')+'</dd></div>'}).join('')+'</dl>':'<p>尚未保存对象属性。'+(summary.sourceRefCount?'已采集对象来源，但逐对象属性提取没有形成可保存字段。':'当前对象没有关联的来源记录，先补采集该对象的原文材料。')+'</p>')+'<p>状态说明：已保存表示属性和原文引文均已入图；草案表示模型提出了字段但引文校验未通过；未提取表示有对象来源但尚未形成属性字段。</p></details>'}
function handleInspectorPropertyInteraction(event){
  var button=event.target.closest('[data-inspector-property-index]');if(!button)return false;
  var a=state.assessments.find(function(report){return assessmentId(report)===button.dataset.inspectorPropertyReport}),graph=a&&graphData(a),node=asArray(graph?.nodes).find(function(node){return node.id===button.dataset.inspectorPropertyNode}),index=Number(button.dataset.inspectorPropertyIndex);
  if(!node||String(graph.graphRevisionId||graph.id||'')!==String(button.dataset.inspectorPropertyRevision||'')||!groundedObjectProperties(node).some(function(row){return row.index===index})){notify('该属性或图谱版本已变化，请重新选择。','warn');return true}
  state.selectedAssessmentId=assessmentId(a);state.activeStage='extract';state.selectedGraphNodeId=node.id;state.selectedGraphEdgeId=null;state.selectedGraphProperty={assessmentId:assessmentId(a),graphRevisionId:graph.graphRevisionId||graph.id,nodeId:node.id,index:index};state.selectedAdmissionId=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.chartEvidence=null;state.evidenceFilterId=null;
  var dialog=button.closest('dialog');if(dialog?.open)dialog.close();updateDeepLink();renderReportPanels();revealEvidence();return true;
}
function graphEvidenceMarkup(a,target){
  var payload=target.payload||{},refs=asArray(payload.evidenceRefs||target.evidenceRefs).filter(function(ref){return ref&&typeof ref==='object'}),all=assessmentEvidence(a),snapshot=asArray(a.sourceSnapshot),metric=capabilityMetrics(a).find(function(m){return m.id===payload.metricId});
  if(metric&&metric.evidence)refs.push(metric.evidence);
  if(!refs.length)asArray(target.evidenceIds).forEach(function(id){var ref=all.find(function(item){return String(item.id)===String(id)||String(item.itemId)===String(id)})||snapshot.find(function(item){return String(item.itemId||item.id)===String(id)});refs.push(ref||{id:id,missing:true})});
  var label=target.label||target.relation||target.id,seen=new Set();refs=refs.filter(function(ref){var key=(ref.itemId||ref.id||ref.sourceUri||'')+':'+(ref.quote||'');if(seen.has(key))return false;seen.add(key);return true});
   var targetSummary=target.type==='EquipmentModel'?objectAttributeSummary(target):null,metadata=[['ID',target.id],['类型 / 关系',graphTypeLabel(target.type||target.relation)],['IRI',target.iri],['本体类别',payload.ontologyClass],['主体',payload.subject],['关系',payload.predicate||payload.relation],['客体',payload.object||payload.objectValue],['决策含义',payload.decisionImpact],['制造要求',asArray(payload.manufacturingRequirements).join('；')],['证据缺口',asArray(payload.evidenceGaps).join('；')],['断言状态',payload.status==='proposed-human-review'?'模型提出 · 待人工审核':payload.status],['属性',payload.attributeName],['原始属性值',payload.attributeValue],['显示值（模型翻译，待人工确认）',payload.displayValue],['数据性质',payload.claimKind?claimKindLabel(payload.claimKind):null],['属性路径',payload.propertyPath],['单位',payload.unit],['属性状态',targetSummary?(targetSummary.label+' · '+targetSummary.rawCount+' 项，原文引文 '+targetSummary.groundedCount+' 条'):null],['图谱版本',payload.graphRevisionId],['关系映射',payload.predicate],['报告来源快照指纹',payload.sourceSnapshotHash],['判断',payload.judgment||payload.assessment],['事件类型',payload.eventKind],['事件时间',payload.occurredAt||payload.eventDate],['条件',payload.conditions],['有效时间',payload.validTime],['数据性质',payload.basis],['来源版本',payload.sourceVersion],['发布日期',payload.publishedAt]];
  ui.evidenceCount.textContent=refs.length+(payload.attributeName?' 条属性证据':' 条图谱证据');
  return '<div class="evidence-filter"><b>'+esc(label)+'</b><button type="button" class="filter-chip" id="clear-evidence-filter">全部证据</button></div><div class="graph-detail-card"><dl>'+metadata.filter(function(row){return row[1]!==undefined&&row[1]!==null&&row[1]!==''}).map(function(row){return '<div><dt>'+esc(row[0])+'</dt><dd>'+esc(fieldText(row[1]))+'</dd></div>'}).join('')+'</dl>'+graphObjectAttributeDetailsMarkup(a,target)+objectMediaEvidenceMarkup(target,a)+'</div><ul class="evidence-list">'+(refs.length?refs.map(function(ref){if(ref.missing)return '<li class="evidence-item evidence-missing"><b>证据引用未找到</b><p>'+esc(ref.id)+'</p></li>';var source=all.find(function(item){return item.itemId&&item.itemId===ref.itemId})||snapshot.find(function(item){return item.itemId&&item.itemId===ref.itemId})||{},url=safeUrl(ref.sourceUri||ref.url||source.sourceUri),quote=ref.quote||ref.exactQuote||ref.body||source.quote||source.body||'';return '<li class="evidence-item"><span class="evidence-source">'+esc(ref.sourceName||source.sourceName||'公开来源')+' · '+esc(dayLabel(ref.publishedAt||source.publishedAt))+'</span>'+sourceBadgeMarkup(Object.assign({},source,ref))+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(ref.title||source.title||'打开原文')+' ↗</a>':'<b>'+esc(ref.title||source.title||'来源链接未记录')+'</b>')+'<blockquote>'+esc(quote||'缺少精确原文，需补证。')+'</blockquote><details class="evidence-provenance"><summary>版本与原文区间</summary><span>来源版本：'+esc(ref.sourceVersion||source.sourceVersion||'未记录')+'</span><span>内容哈希：'+esc(ref.contentHash||source.contentHash||'未记录')+'</span><span>字符区间：'+esc(Number.isInteger(ref.startChar)?'['+ref.startChar+', '+ref.endChar+')':'未记录')+'</span></details></li>'}).join(''):'<li class="evidence-item evidence-missing">当前关系没有可展开的引用，需补充证据。</li>')+'</ul>';
}
function revealEvidence(){if(window.matchMedia('(max-width:1000px)').matches)document.querySelector('.evidence-panel').scrollIntoView({behavior:'smooth',block:'start'})}
function renderEvidence(){
  if(state.activeStage==='materials'){
    var item=asArray(state.task?.items).find(function(i){return i.id===state.selectedMaterialId});
    if(!item){ui.evidenceCount.textContent='待选择';ui.evidence.innerHTML='<div class="placeholder">点击材料标题区域查看保存的原文；勾选框用于选择分析材料。</div>';return}
    ui.evidenceCount.textContent='1 条材料';var url=safeUrl(item.sourceUri||item.itemUri);
    ui.evidence.innerHTML='<div class="graph-detail-card"><b>'+esc(item.title)+'</b>'+sourceBadgeMarkup(item)+'<p>'+esc(item.sourceName)+' · '+esc(dayLabel(item.publishedAt))+'</p>'+(url?'<a class="admission-kb-link" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">打开来源 ↗</a>':'')+'</div><div class="material-full-body">'+esc(item.body)+'</div><details class="evidence-provenance"><summary>来源版本与指纹</summary><span>'+esc(item.sourceVersion)+'</span><span>'+esc(item.contentHash)+'</span></details>';return;
  }
  var a=selectedAssessment();
  if(a&&state.activeStage==='admission'&&state.selectedAdmissionId){var candidate=ontologyAdmissions(a).find(function(c){return first(c,'admissionId','id')===state.selectedAdmissionId});if(candidate){ui.evidence.innerHTML=graphEvidenceMarkup(a,{id:state.selectedAdmissionId,label:admissionLabel(candidate),type:candidate.assertion?.type||candidate.kind,iri:candidate.candidateIri,payload:Object.assign({},candidate.assertion?.attributes||{},{evidenceRefs:admissionEvidence(candidate),attributeName:candidate.assertion?.propertyName,attributeValue:candidate.assertion?.rawValue,displayValue:candidate.assertion?.normalizedValue,unit:candidate.assertion?.unit,conditions:candidate.assertion?.conditions,propertyPath:candidate.mapping?.propertyPath,ontologyClass:candidate.ontologyClass,predicate:candidate.mapping?.predicate,sourceSnapshotHash:candidate.sourceSnapshotHash})});var clear=$('clear-evidence-filter');if(clear)clear.addEventListener('click',function(){state.selectedAdmissionId=null;renderEvidence()});return}}

  if(!a){ui.evidenceCount.textContent="0 条";ui.evidence.innerHTML='<div class="placeholder">选择报告或研判方向查看对应的原文证据。</div>';return}
  var all=assessmentEvidence(a),finding=Number.isInteger(state.selectedFindingIndex)?reportFindings(a)[state.selectedFindingIndex]:null,graph=graphData(a),graphNode=graph&&Array.isArray(graph.nodes)?graph.nodes.find(function(node){return String(node.id)===String(state.selectedGraphNodeId)}):null,graphEdge=graph&&Array.isArray(graph.edges)?graph.edges.find(function(edge){return String(edge.id)===String(state.selectedGraphEdgeId)}):null;
  var metric=Number.isInteger(state.selectedCapabilityIndex)?capabilityMetrics(a)[state.selectedCapabilityIndex]:null;
  var ids=state.evidenceFilterId?[state.evidenceFilterId]:finding?listText(finding.evidenceIds):null;
  if(state.chartEvidence&&state.chartEvidence.assessmentId!==assessmentId(a))state.chartEvidence=null;if(state.chartEvidence&&!metric&&!finding)ids=state.chartEvidence.ids;
  var refs=state.chartEvidence&&!metric&&!finding?state.chartEvidence.refs:metric?[capabilityEvidence(a,metric,state.selectedCapabilityIndex)]:ids?ids.map(function(id){return all.find(function(ref){return String(ref.id)===id||String(ref.itemId)===id})||{id:id,missing:true}}):all;
  if(state.selectedGraphAssociationId){var associationHtml=graphAssociationMarkup(a,state.selectedGraphAssociationId),associationTarget=graphNode||graphEdge;ui.evidenceCount.textContent=graphAssociationRows(a,state.selectedGraphAssociationId).length+' 项关联';ui.evidence.innerHTML=associationHtml+(associationTarget&&String(associationTarget.id)!==String(state.selectedGraphAssociationId)?graphEvidenceMarkup(a,associationTarget):'');return}
  var propertyTarget=selectedPropertyTarget(a,graphNode);if(graphNode||graphEdge){ui.evidence.innerHTML=graphEvidenceMarkup(a,propertyTarget||graphNode||graphEdge);$("clear-evidence-filter").addEventListener("click",function(){state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;renderAssessments();renderEvidence()});return}
  ui.evidenceCount.textContent=state.chartEvidence?refs.length+" 条图表支撑引用":metric?"1 项指标证据":refs.length+" / "+all.length+" 条";
  ui.evidence.innerHTML='<div class="evidence-filter"><b>'+esc(metric?(metric.equipment||"装备")+" · "+(metric.metric||"公开指标"):finding?finding.title:state.chartEvidence?.label||"整份报告的来源")+'</b><button type="button" class="filter-chip" id="clear-evidence-filter">全部证据</button></div>'+
    '<ul class="evidence-list">'+(refs.length?refs.map(function(ref){
      if(ref.missing)return '<li class="evidence-item evidence-missing"><b>证据引用未找到</b><p>'+esc(ref.id)+"</p></li>";
      var url=safeUrl(ref.sourceUri),line=Number.isInteger(ref.startChar)&&Number.isInteger(ref.endChar)?"原文字符区间 ["+ref.startChar+", "+ref.endChar+")":ref.lineStart!==undefined?"原文行 "+ref.lineStart+(ref.lineEnd!==undefined?"–"+ref.lineEnd:""):"";
      if(!metric&&!finding&&!state.evidenceFilterId)return '<li class="evidence-item"><details class="evidence-full-text"><summary><b class="evidence-row-title" title="'+esc(ref.title||ref.id)+'">'+esc(ref.title||ref.id)+'</b><span class="evidence-source" title="'+esc(ref.sourceName||'公开来源')+'">'+esc(ref.sourceName||'公开来源')+'</span><span class="list-detail-label">详情⌄</span></summary><div class="evidence-expanded-body"><span class="evidence-source">'+esc(dayLabel(ref.publishedAt))+'</span>'+sourceBadgeMarkup(ref)+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>':'')+'<blockquote>'+esc(ref.quote||'未提供来源原文摘要。')+'</blockquote><details class="evidence-provenance"><summary>来源版本与引用</summary><span>'+esc(line)+'</span><span>引用 ID：'+esc(ref.id)+'</span><span>来源版本：'+esc(ref.sourceVersion||'未记录')+'</span><span>内容指纹：'+esc(ref.contentHash||'未记录')+'</span></details></div></details></li>';
      return '<li class="evidence-item"><span class="evidence-source">'+esc(ref.sourceName||"公开来源")+" · "+esc(dayLabel(ref.publishedAt))+
        '</span>'+sourceBadgeMarkup(ref)+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(ref.title||ref.id)+" ↗</a>":"<b>"+esc(ref.title||ref.id)+"</b>")+
        '<blockquote>'+esc(ref.quote||"未提供来源原文摘要。")+'</blockquote><details class="evidence-provenance"><summary>来源版本与引用</summary><span>'+esc(line)+'</span><span>引用 ID：'+esc(ref.id)+
        '</span><span>来源版本：'+esc(ref.sourceVersion||"未记录")+'</span><span>内容指纹：'+esc(ref.contentHash||"未记录")+"</span></details></li>";
    }).join(""):'<li class="evidence-item"><b>证据不足</b><p>当前方向未引用可核验来源。</p></li>')+
    '</ul>'+workbenchMessageMarkup(metric?"量化证据缺口":finding?"本方向不确定性":"报告证据缺口",listMarkup(metric?a.summary?.capabilityAssessment?.gaps:finding?finding.uncertainty:a.summary?.gaps),'warning');
  $("clear-evidence-filter").addEventListener("click",function(){state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null;renderAssessments();renderEvidence()});
}
async function maybeAutoStartExtraction(id){
  var task=state.task,a=state.assessments.find(function(report){return assessmentId(report)===String(id)}),graph=a&&graphData(a);
  if(!task||task.workflowMode!=='auto'||!a||a.status==='withdrawn'||state.autoExtractionRequested[id]||reportBusy(id))return;
  var nodes=extractedRecords(a).nodes,pending=nodes.filter(function(node){return ['budget-skipped','skipped-budget','pending'].includes(node.payload?.attributeExtraction?.status)}),jobs=asArray(state.extractionJobs[id]);
  // Existing terminal jobs are evidence of an extraction attempt. A saved
  // empty or failed result must not start another paid identity pass on each
  // refresh; only the unexecuted attribute budget is resumed automatically.
  if(!nodes.length&&!jobs.length||pending.length){state.autoExtractionRequested[id]=true;await startExtractionJob(id,{extractEntities:!nodes.length,extractAttributes:!!nodes.length,objectIds:nodes.length?pending.slice(0,8).map(function(node){return node.id}):undefined})}
  else await resumeAutomaticChain(id,task.id);
}
async function loadReportAugmentations(id){
  state.augmentationBusy=state.augmentationBusy||{};
  state.augmentationLoaded=state.augmentationLoaded||{};
  state.augmentationErrors=state.augmentationErrors||{};
  state.graphByAssessment=state.graphByAssessment||{};
  state.validationByAssessment=state.validationByAssessment||{};
  state.admissionsByAssessment=state.admissionsByAssessment||{};
  state.auditByAssessment=state.auditByAssessment||{};
  if(!id||state.augmentationBusy[id]||state.augmentationLoaded&&state.augmentationLoaded[id])return;
  state.augmentationBusy[id]=true;state.augmentationErrors[id]=state.augmentationErrors[id]||{};
  var paths=["/assessments/"+encodeURIComponent(id)+"/graph","/assessments/"+encodeURIComponent(id)+"/validation","/assessments/"+encodeURIComponent(id)+"/admissions","/assessments/"+encodeURIComponent(id)+"/audit"];
  var results=await Promise.allSettled(paths.map(function(path){return requestIntelligence(path)}));
  results.forEach(function(result,index){var kind=['graph','validation','admissions','audit'][index];if(result.status!=="fulfilled"){state.augmentationErrors[id][kind]=result.reason?.message||String(result.reason||'读取失败');return}delete state.augmentationErrors[id][kind];var value=result.value||{};if(index===0)state.graphByAssessment[id]=value.graph||value;else if(index===1)state.validationByAssessment[id]=value;else if(index===2)state.admissionsByAssessment[id]=value;else state.auditByAssessment[id]=value});
  if(results.every(function(result){return result.status==='fulfilled'}))state.augmentationLoaded[id]=true;else delete state.augmentationLoaded[id];delete state.augmentationBusy[id];
  if(String(state.selectedAssessmentId)===String(id))setTimeout(function(){maybeAutoStartExtraction(id)},0);
  if(String(state.selectedAssessmentId)===String(id)){renderAssessments();renderReview();renderEvidence();renderExecution();renderImageryEvidence()}
}
async function buildReportGraph(id,extractEntities){
  id=String(id||'');
  if(!id||state.busy||reportBusy(id))return;
  var a=state.assessments.find(function(item){return assessmentId(item)===id}),taskId=state.task?.id;
  if(!a||a.status==='withdrawn')return;
  state.augmentationBusy[id]=true;renderAssessments();
  try{
    var expectedRevision=first(a,'revision','version','expectedRevision')||1,result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/graph',{method:'POST',body:JSON.stringify({expectedRevision:expectedRevision,extractEntities:extractEntities===true})});
    state.graphByAssessment[id]=result.graph||result;state.objectSelections[id]={nodes:new Set(),edges:new Set()};
    state.augmentationLoaded=state.augmentationLoaded||{};state.augmentationLoaded[id]=true;
    if(state.task?.id===taskId&&String(state.selectedAssessmentId)===id){state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;state.selectedAdmissionId=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null}
    var admissions=await Promise.allSettled([requestIntelligence('/assessments/'+encodeURIComponent(id)+'/admissions')]);
    if(admissions[0].status==='fulfilled')state.admissionsByAssessment[id]=admissions[0].value;
    else{delete state.admissionsByAssessment[id];delete state.augmentationLoaded[id];notify('图谱已保存；准入候选刷新失败，请刷新此报告后审核。','warn')}
    notify(state.task?.id===taskId&&String(state.selectedAssessmentId)===id?'证据关系图谱已生成，可点击节点、属性或关系查看来源。':'证据关系图谱已保存到原报告，可返回查看。','success');
  }catch(error){notify('图谱构建失败：'+error.message,'warn')}
  finally{
    delete state.augmentationBusy[id];
    if(state.task?.id===taskId){if(String(state.selectedAssessmentId)===id){renderReportPanels();renderGraphOverlay()}await Promise.allSettled([loadExecutionTrace(),loadImageryEvidence()])}
  }
}
async function enrichObjectAttributes(id){
  id=String(id||'');
  if(!id||state.busy||reportBusy(id))return;
  var a=state.assessments.find(function(report){return assessmentId(report)===id}),graph=a&&graphData(a),taskId=state.task?.id;
  if(!a||a.status==='withdrawn'||!(graph?.graphRevisionId||graph?.id)||!extractedRecords(a).nodes.length)return;
  var expectedRevision=first(a,'revision','version','expectedRevision')||1;
  state.augmentationBusy[id]=true;state.attributeBusyId=id;renderAssessments();
  try{
    var result=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/graph',{method:'POST',body:JSON.stringify({expectedRevision:expectedRevision,extractAttributes:true})});
    state.graphByAssessment[id]=result.graph||result;state.objectSelections[id]={nodes:new Set(),edges:new Set()};
    state.augmentationLoaded=state.augmentationLoaded||{};state.augmentationLoaded[id]=true;
    if(state.task?.id===taskId&&String(state.selectedAssessmentId)===id){
      state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.selectedGraphProperty=null;state.selectedAdmissionId=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;state.chartEvidence=null;
    }
    var admissions=await Promise.allSettled([requestIntelligence('/assessments/'+encodeURIComponent(id)+'/admissions')]);
    if(admissions[0].status==='fulfilled')state.admissionsByAssessment[id]=admissions[0].value;
    else{delete state.admissionsByAssessment[id];delete state.augmentationLoaded[id];notify('属性提取结果已保存；准入候选刷新失败，请刷新此报告后审核。','warn')}
    notify(state.task?.id===taskId&&String(state.selectedAssessmentId)===id?'逐对象属性提取结果已保存，缺口与失败状态按对象显示，等待人工核验。':'属性提取结果已保存到原报告，可返回对象分析查看。','success');
  }catch(error){notify('逐对象属性提取失败：'+error.message,'warn')}
  finally{
    delete state.augmentationBusy[id];if(state.attributeBusyId===id)state.attributeBusyId=null;
    if(state.task?.id===taskId){
      if(String(state.selectedAssessmentId)===id){renderReportPanels();renderGraphOverlay()}
      await Promise.allSettled([loadImageryEvidence(),loadExecutionTrace()]);
    }
  }
}
async function runReportValidation(id,kind){
  if(!id||reportBusy(id))return false;state.augmentationBusy[id]=true;renderAssessments();
  try{var a=state.assessments.find(function(item){return assessmentId(item)===String(id)}),reviewer=$("reviewer")&&$("reviewer").value.trim()||"operator",expectedRevision=first(a||{},"revision","version","expectedRevision")||1,result=await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/validate",{method:"POST",body:JSON.stringify({kind:kind||"deterministic",actor:reviewer,expectedRevision:expectedRevision})});state.validationByAssessment[id]=await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/validation");notify("证据检查已记录，尚需独立检索与人工复核。","success");return true}catch(e){notify("验证接口暂不可用："+e.message,"warn");return false}finally{delete state.augmentationBusy[id];loadExecutionTrace();renderAssessments();renderReview()}
}
async function loadOntologyAudit(id){
  if(!id)return null;
  var value=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/audit');
  state.auditByAssessment[id]=value||{};
  return value;
}
async function runOntologyAudit(id){
  if(!id||reportBusy(id))return;
  state.augmentationBusy[id]=true;renderAssessments();
  try{
    var reviewer=$("reviewer")&&$("reviewer").value.trim()||"operator";
    await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/audit',{method:'POST',body:JSON.stringify({actor:reviewer,trigger:'periodic'})});
    await loadOntologyAudit(id);
    notify('周期审计已完成；请逐项处理开放发现。','success');
  }catch(error){notify('周期审计失败：'+error.message,'warn')}
  finally{delete state.augmentationBusy[id];renderAssessments();renderReview()}
}
async function reviewAuditFinding(action,findingId,reportId){
  if(!findingId||!reportId||reportBusy(reportId))return;
  var payload=state.auditByAssessment?.[reportId]||{},finding=asArray(payload.findings).find(function(row){return String(first(row,'findingId','id'))===String(findingId)}),form=state.auditFindingForms?.[findingId]||{},reviewer=(form.reviewer||'').trim()||($("reviewer")&&$("reviewer").value.trim())||'operator',reason=(form.reason||'').trim();
  if(!finding){notify('审计发现已变化，请刷新后重试。','warn');return}
  if(!reason){notify('请填写该发现的人工审核理由。','warn');return}
  state.augmentationBusy[reportId]=true;renderAssessments();
  try{
    await requestIntelligence('/audit-findings/'+encodeURIComponent(findingId)+'/review',{method:'POST',body:JSON.stringify({action:action,reviewer:reviewer,reason:reason,expectedRevision:finding.revision||1})});
    await loadOntologyAudit(reportId);
    notify('审计发现已记录人工处理结果。','success');
  }catch(error){notify('审计发现审核失败：'+error.message,'warn')}
  finally{delete state.augmentationBusy[reportId];renderAssessments();renderReview()}
}
function automaticPropertyPredicate(node,row){
  var attribute=row.attribute||{},catalog=asArray(state.attributePredicates),name=String(attribute.name||'').trim().toLowerCase(),aliases=catalog.filter(function(entry){return asArray(entry.aliases).some(function(alias){return String(alias).toLowerCase()===name})}),suggestion=predicateRecommendation(attribute)?.id||'',kind=node.type==='EventRecord'?node.payload?.eventKind:node.type;
  suggestion=String(suggestion).split('#').pop();
  // Catalog aliases are the backend's recognized categories. A model's
  // suggestion cannot override a declared category merely because the
  // property's source quote passed verification.
  var predicate=aliases.length===1?aliases[0].id:aliases.length?aliases.some(function(entry){return entry.id===suggestion})?suggestion:'':suggestion,entry=catalog.find(function(value){return value.id===predicate});
  if(!entry||!asArray(entry.domains).includes(kind)||(row.propertyPath==='payload.date')!==(predicate==='reportedEventDate'))return '';
  return predicate;
}
function automaticGraphAdmissionSelection(a,existing){
  var graph=graphData(a),revision=graph?.graphRevisionId||graph?.id;if(!graph||!revision)return null;
  var nodes=asArray(graph.nodes),edges=asArray(graph.edges),byId={};nodes.forEach(function(node){byId[node.id]=node});
  var current=asArray(existing),nodeExisting=new Set(current.filter(function(candidate){return candidate.kind==='graph-node'&&!candidate.staleGraph&&candidate.mapping?.graphRevisionId===revision}).map(function(candidate){return candidate.mapping?.graphElementId})),edgeExisting=new Set(current.filter(function(candidate){return candidate.kind==='graph-relation'&&!candidate.staleGraph&&candidate.mapping?.graphRevisionId===revision}).map(function(candidate){return candidate.mapping?.graphElementId})),propertyExisting=new Set(current.filter(function(candidate){return candidate.kind==='graph-property'&&!candidate.staleGraph&&candidate.mapping?.graphRevisionId===revision}).map(function(candidate){return String(candidate.mapping?.graphElementId)+'|'+String(candidate.mapping?.propertyPath)}));
  var graphNodeIds=nodes.filter(function(node){return !nodeExisting.has(node.id)&&graphAdmissionEligibility(node,a,byId,false).eligible}).map(function(node){return String(node.id)}),graphEdgeIds=edges.filter(function(edge){return !edgeExisting.has(edge.id)&&graphAdmissionEligibility(edge,a,byId,true).eligible&&linkedObjectChecks(a,edge.id)[0]?.machineStatus==='passed'}).map(function(edge){return String(edge.id)}),graphProperties=[];
  nodes.forEach(function(node){if(!graphNodeIds.includes(String(node.id))&&!nodeExisting.has(node.id))return;if(!graphAdmissionEligibility(node,a,byId,false).eligible)return;var objectCheck=linkedObjectChecks(a,node.id)[0];groundedObjectProperties(node).forEach(function(row){var key=String(node.id)+'|'+String(row.propertyPath),propertyCheck=asArray(objectCheck?.propertyEvidence).find(function(check){return check.propertyPath===row.propertyPath});if(propertyExisting.has(key)||propertyCheck?.machineStatus!=='passed')return;var predicate=automaticPropertyPredicate(node,row);if(predicate)graphProperties.push({graphNodeId:String(node.id),propertyPath:row.propertyPath,ontologyPredicate:predicate})})});
  graphNodeIds=graphNodeIds.slice(0,200);graphEdgeIds=graphEdgeIds.slice(0,Math.max(0,200-graphNodeIds.length));graphProperties=graphProperties.slice(0,Math.max(0,200-graphNodeIds.length-graphEdgeIds.length));
  if(!graphNodeIds.length&&!graphEdgeIds.length&&!graphProperties.length)return null;
  return {graphRevisionId:String(revision),graphNodeIds:graphNodeIds,graphEdgeIds:graphEdgeIds,graphProperties:graphProperties};
}
async function buildOntologyAdmissions(id,options){
  options=options||{};if(!id||reportBusy(id))return false;state.augmentationBusy[id]=true;renderAssessments();
  try{
    // The metric endpoint resolves IDs only from the saved assessment summary.
    // Display metrics also include graph-derived observations; those enter
    // through verified graph-property candidates below, never as metric IDs.
    var a=state.assessments.find(function(item){return assessmentId(item)===String(id)}),expectedRevision=first(a||{},"revision","version","expectedRevision")||1,current=ontologyAdmissions(a||{}),metrics=asArray(a?.summary?.capabilityAssessment?.metrics),metricIds=new Set(metrics.map(function(metric){return String(metric.id)})),existingMetricIds=new Set(current.filter(function(candidate){return candidate.kind!=='graph-node'&&candidate.kind!=='graph-relation'&&candidate.kind!=='graph-property'&&candidate.metricId}).map(function(candidate){return String(candidate.metricId)})),requestsMade=0;
    if(metrics.length){var missingMetrics=Array.from(metricIds).filter(function(metricId){return !existingMetricIds.has(metricId)});if(missingMetrics.length){await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/admissions",{method:"POST",body:JSON.stringify({expectedRevision:expectedRevision,metricIds:missingMetrics})});requestsMade++}}
    if(options.automatic){
      if(!asArray(state.attributePredicates).length){var catalog=await requestIntelligence('/attribute-predicates');state.attributePredicates=asArray(catalog.attributePredicates);if(!state.attributePredicates.length)throw new Error('属性本体谓词目录尚未读取，不能自动提交属性候选')}
      // The API accepts at most 200 selections. Consume each bounded group
      // from the durable candidate projection instead of silently truncating
      // a larger graph and calling the stage complete.
      var selection=automaticGraphAdmissionSelection(a,current);
      while(selection){
        await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/admissions",{method:"POST",body:JSON.stringify(selection)});requestsMade++;
        state.admissionsByAssessment[id]=await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/admissions");
        var nextCurrent=ontologyAdmissions(a||{}),nextSelection=automaticGraphAdmissionSelection(a,nextCurrent);
        if(nextSelection&&JSON.stringify(nextSelection)===JSON.stringify(selection))throw new Error('候选接口未保存所选图谱记录');
        current=nextCurrent;selection=nextSelection;
      }
    }
    state.admissionsByAssessment[id]=await requestIntelligence("/assessments/"+encodeURIComponent(id)+"/admissions");
    var recorded=ontologyAdmissions(a||{}),currentGraph=graphData(a),currentRevision=currentGraph?.graphRevisionId||currentGraph?.id,currentGraphCandidates=recorded.filter(function(candidate){return !candidate.staleGraph&&candidate.mapping?.graphRevisionId===currentRevision});
    if(!metrics.length&&!currentGraphCandidates.length){notify('自动准入不适用：当前没有可核验能力指标或图谱对象属性候选；证据缺口已保留。','warn');return {ok:true,notApplicable:true}};
    if(!options.automatic)notify('已生成本体准入候选；仍需逐条人工审核。','success');return {ok:true,notApplicable:false,created:requestsMade};
  }catch(e){notify('准入候选接口暂不可用：'+e.message,'warn');return false}
  finally{delete state.augmentationBusy[id];loadExecutionTrace();renderAssessments();renderReview()}
}
async function buildGraphAdmissions(id){
  if(!id||reportBusy(id))return;var a=state.assessments.find(function(r){return assessmentId(r)===id}),graph=graphData(a),selection=objectSelection(id),taskId=state.task?.id;if(!graph?.id&&!graph?.graphRevisionId){notify('请先提取对象并保存图谱版本。','warn');return}
  state.augmentationBusy[id]=true;renderAssessments();
  try{await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/admissions',{method:'POST',body:JSON.stringify({graphRevisionId:graph.graphRevisionId||graph.id,graphNodeIds:Array.from(new Set(Array.from(selection.nodes).concat(Array.from(selection.properties).map(function(key){return JSON.parse(key)[0]})))),graphEdgeIds:Array.from(selection.edges),graphProperties:Array.from(selection.properties).map(function(key){var pair=JSON.parse(key),node=asArray(graph.nodes).find(function(n){return n.id===pair[0]}),row=node&&groundedObjectProperties(node).find(function(r){return r.propertyPath===pair[1]}),attribute=row?.attribute||{},suggestion=attribute.ontologyPredicateSuggestion,predicate=state.propertyMappings[key]||(suggestion&&suggestion.id)||propertyPredicate(attribute.name)||'';return Object.assign({graphNodeId:pair[0],propertyPath:pair[1]},predicate?{ontologyPredicate:predicate}:{})})})});state.admissionsByAssessment[id]=await requestIntelligence('/assessments/'+encodeURIComponent(id)+'/admissions');if(state.task?.id===taskId&&String(state.selectedAssessmentId)===String(id)){state.activeStage='admission';updateDeepLink()}notify('已送入审核队列；谓词已由模型预推荐，存在冲突时再人工修正。')}catch(e){notify('创建对象准入候选失败：'+e.message,'warn')}finally{delete state.augmentationBusy[id];renderReportPanels()}
}
async function reviewOntologyAdmission(action,admissionId,reportId){
  if(!admissionId||!reportId||reportBusy(reportId))return;
  var form=state.admissionForms[reportId]||{},reviewer=(form.reviewer||'').trim(),reason=(form.reason||'').trim();
  if(!reviewer||(!reason&&action!=='publish')){notify("请填写本体审核人和准入理由后再提交。","warn");return}
  var report=state.assessments.find(function(item){return assessmentId(item)===String(reportId)}),candidate=ontologyAdmissions(report||{}).find(function(item){return String(first(item,"id","admissionId"))===String(admissionId)});
  if(!candidate){notify("候选已变化，请刷新报告。","warn");return}
  if(candidate.supersededByAdmissionId){notify('此候选已有修改版本，请审核新候选。','warn');return}
  if(action==='accept'&&['graph-node','graph-relation','graph-property'].includes(candidate.kind)&&(state.admissionConfidence[admissionId]||candidate.review?.verifiedConfidence)!=='high'){notify('请先核对原文，并明确选择人工核验置信度。','warn');return}
  var revision=candidate.revision||1,path="/admissions/"+encodeURIComponent(admissionId)+(action==='publish'?'/publish':'/review'),body={reviewer:reviewer,expectedRevision:revision};if(action!=='publish'){body.action=action;body.reason=reason;if(action==='accept'&&['graph-node','graph-relation','graph-property'].includes(candidate.kind))body.verifiedConfidence=state.admissionConfidence[admissionId]||candidate.review?.verifiedConfidence}
  if(action==='modify'){
    var edits=state.propertyEdits[admissionId]||{},edited={};
    if(candidate.kind!=='graph-property'||!Object.keys(edits).length){notify('先修改至少一个属性字段，再保存新版本。','warn');return}
    if(edits.rawValue!==undefined){var original=candidate.assertion?.rawValue;if(typeof original==='number'){edited.rawValue=Number(edits.rawValue);if(!edits.rawValue.trim()||!Number.isFinite(edited.rawValue)){notify('原始值应为有效数值。','warn');return}}else if(typeof original==='boolean'){if(!['true','false'].includes(edits.rawValue.trim())){notify('该原始值应为 true 或 false。','warn');return}edited.rawValue=edits.rawValue.trim()==='true'}else edited.rawValue=edits.rawValue}
    ['normalizedValue','unit','conditions','validTime','ontologyPredicate'].forEach(function(key){if(edits[key]!==undefined&&!(key==='ontologyPredicate'&&!edits[key]))edited[key]=edits[key]});body.editedProperty=edited;
  }
  state.augmentationBusy[reportId]=true;renderAssessments();
  try{var result=await requestIntelligence(path,{method:"POST",body:JSON.stringify(body)});state.admissionsByAssessment[reportId]=await requestIntelligence("/assessments/"+encodeURIComponent(reportId)+"/admissions");if(action==='modify'&&String(state.selectedAssessmentId)===String(reportId)){var next=result.admission||result.candidate||result;state.selectedAdmissionId=next.admissionId||next.id||null;delete state.propertyEdits[admissionId]}notify(action==='publish'?"本体准入已发布，独立保留版本记录。":action==='modify'?'修改版本已保存，请重新人工确认。':"本体准入审核已记录。","success")}catch(e){notify("本体准入操作失败："+e.message,"danger")}finally{delete state.augmentationBusy[reportId];renderAssessments();renderEvidence()}
}
function renderReportPanels(){renderAssessments();renderReview();renderEvidence();renderImageryEvidence()}
async function enrichCapabilities(id){
  if(state.busy)return;
  var a=state.assessments.find(function(report){return assessmentId(report)===id});
  if(!a||a.status==="withdrawn")return;
  var taskId=state.task?.id;
  state.busy=true;state.capabilityBusyId=id;ui.generate.disabled=true;renderAssessments();
  setSummary("正在提取公开能力指标，完成后将生成一份独立待审核草稿。");
  try{
    var result=await request("/assessments/"+encodeURIComponent(id)+"/capabilities",{method:"POST",body:JSON.stringify({})});
    if(state.task?.id===taskId)await loadAssessments(result.assessment&&assessmentId(result.assessment));
    notify(result.duplicate?"结果与已有版本相同，已打开原版本（"+assessmentStatus(result.assessment||{})[0]+"），保留该版本的审核状态。":"已生成含能力量化的新草稿，原报告已保留，等待人工审核。");
  }catch(error){notify("补充能力量化失败："+error.message,"danger")}
  finally{state.busy=false;state.capabilityBusyId=null;renderTask();renderAssessments()}
}
async function loadAssessments(preferredId){state.chartEvidence=null;
  if(!state.task?.id){state.assessments=[];renderReportPanels();return}
  var taskId=state.task.id;
  try{
    var b=await request("/tasks/"+encodeURIComponent(taskId)+"/assessments");
    if(state.task?.id!==taskId)return;
    state.assessments=unwrapAssessments(b).sort(function(a,b){return String(b.createdAt||"").localeCompare(String(a.createdAt||""))});
    var wanted=preferredId||state.selectedAssessmentId,selected=state.assessments.find(function(a){return assessmentId(a)===String(wanted)});
    if(preferredId&&!selected)notify("链接指定的研判报告不存在，已选择可用报告。","warn");
    if(!selected)selected=state.assessments.find(function(a){return a.status==="under-review"})||state.assessments[0];
    state.selectedAssessmentId=selected?assessmentId(selected):null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.evidenceFilterId=null;
    updateDeepLink();renderReportPanels();if(selected)await Promise.allSettled([loadReportAugmentations(assessmentId(selected)),loadExtractionJobs(assessmentId(selected)),loadLinkedImageryEvidence(assessmentId(selected))]);
  }catch(e){
    if(state.task?.id!==taskId)return;
    state.assessments=[];state.selectedAssessmentId=null;renderReportPanels();
    notify("读取研判失败："+e.message,"danger");
  }
}
 function resetCollectionForm(){
   if(ui.form)ui.form.reset();
   document.querySelectorAll('[data-source-id]').forEach(function(input){var source=state.sources.find(function(item){return item.id===input.dataset.sourceId});input.checked=!!source&&source.defaultSelected!==false});
   updateSourceCount();
   syncAutomationOptions();
 }
 function startNewCollectionTask(){
   if(state.busy){notify('当前任务仍在提交，请稍候再新建。','warn');return}
   resetTaskWorkspace();
   resetCollectionForm();
   setCollectionDrawerCollapsed(false);
   notify('已新建采编任务，请设置研究范围后启动。','success');
   ui.title.focus();
 }
 function resetTaskWorkspace(){
  stopPolling();state.taskLoadToken++;
  Object.values(state.extractionJobTimers).forEach(clearTimeout);state.extractionJobTimers={};
  state.task=null;state.assessments=[];state.selectedItems=new Set();state.activeStage='materials';
  ['selectedAssessmentId','selectedFindingIndex','selectedCapabilityIndex','selectedGraphNodeId','selectedGraphEdgeId','selectedGraphAssociationId','selectedGraphProperty','selectedMaterialId','selectedAdmissionId','evidenceFilterId','chartEvidence'].forEach(function(key){state[key]=null});
  saveLastTask(null);resetImageryForm();updateDeepLink();renderHistory();renderTask();renderReportPanels();
}
async function loadTask(id,assessmentId){state.chartEvidence=null;
  if(!id)return;
  stopPolling();
  var token=++state.taskLoadToken;
  try{
    var b=await request("/tasks/"+encodeURIComponent(id));
    if(token!==state.taskLoadToken)return;
    var task=unwrapTask(b);
    if(task.archivedAt){state.tasks=state.tasks.filter(function(item){return item.id!==task.id});resetTaskWorkspace();restoreTaskSettings(task);notify('历史任务已归档，可以重新开始采编。');return}
    state.chartEvidence=null;state.task=task;state.tasks=[state.task].concat(state.tasks.filter(function(t){return t.id!==state.task.id}));var parentFollowupPromise=loadParentFollowup(state.task.id);
    state.selectedItems=new Set((state.task.items||[]).filter(isTextEvidence).map(function(i){return i.id}));state.selectedMaterialId=null;state.selectedAdmissionId=null;
    state.assessments=[];state.selectedAssessmentId=null;state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.evidenceFilterId=null;
    resetImageryForm();restoreTaskSettings(state.task);saveLastTask(state.task.id);renderTask();renderReportPanels();await loadAssessments(assessmentId);await Promise.allSettled([loadExecutionTrace(),loadImageryEvidence(),parentFollowupPromise]);
    if(["queued","running","processing"].includes(state.task?.status))state.poll=setInterval(pollTask,2200);
  }catch(e){notify("任务读取失败："+e.message,"danger")}
}
function stopPolling(){if(state.poll){clearInterval(state.poll);state.poll=null}}
async function pollTask(){if(!state.task||!state.task.id)return;try{var b=await request("/tasks/"+encodeURIComponent(state.task.id));state.chartEvidence=null;state.task=unwrapTask(b);state.tasks=[state.task].concat(state.tasks.filter(function(t){return t.id!==state.task.id}));if(state.task.workflowMode==='auto'&&state.task.automation?.autoSelectMaterials)state.selectedItems=new Set(asArray(state.task.items).filter(isTextEvidence).map(function(item){return item.id}));renderTask();if(["completed","succeeded","partial","failed","cancelled"].indexOf(String(state.task.status||"").toLowerCase())>=0){stopPolling();await loadAssessments();await loadExecutionTrace();if(['completed','succeeded','partial'].includes(String(state.task.status||'').toLowerCase())&&state.task.automation?.autoStartAssessment&&!state.assessments.length&&!state.autoActions[state.task.id]){state.autoActions[state.task.id]='assessment';await generateAssessment()}}}catch(e){stopPolling();notify("任务状态读取失败："+e.message,"danger")}}
async function createTask(e){e.preventDefault();var sourceIds=selectedSourceIds(),title=ui.title.value.trim(),question=ui.question.value.trim(),maxItems=Number(ui.maxItems.value);if(!Number.isInteger(maxItems)||maxItems<1||maxItems>100){notify("本次最多材料应为 1–100 的整数。","warn");ui.maxItems.focus();return}if(!sourceIds.length){notify("至少选择一个公开来源。","warn");return}if(!title||!question){notify("请填写任务名称和研究问题。","warn");return}ui.start.disabled=true;ui.start.firstElementChild.textContent="搜索中…";state.busy=true;try{var b=await request("/tasks",{method:"POST",body:JSON.stringify(Object.assign({title:title,question:question,keywords:keywords(ui.keywords.value),sourceIds:sourceIds,maxItems:maxItems,sinceDays:Number(ui.since.value),horizonYears:Number(ui.horizon.value),topicPreset:"future-war-equipment-trends",scope:"global-public-equipment-trends"},automationPayload()))});state.chartEvidence=null;state.task=unwrapTask(b);state.tasks=[state.task].concat(state.tasks.filter(function(t){return t.id!==state.task.id}));state.assessments=[];state.selectedItems=new Set();state.selectedAssessmentId=null;state.activeStage="materials";state.selectedMaterialId=null;resetImageryForm();saveLastTask(state.task.id);updateDeepLink();renderTask();renderReportPanels();stopPolling();state.poll=setInterval(pollTask,2200);await pollTask();notify(state.task.workflowMode==='auto'?"自动采编任务已启动，完成后会按选项推进候选分析。":"研究任务已启动，Agent 正在读取公开材料。")}catch(e){notify("搜索任务创建失败："+e.message,"danger")}finally{state.busy=false;ui.start.disabled=false;ui.start.firstElementChild.textContent="搜索公开材料";renderTask()}}
async function generateAssessment(){
  if(!state.task||!state.task.id||state.busy)return;
  var taskId=state.task.id,ready=['completed','succeeded','partial'].includes(String(state.task.status||'').toLowerCase());
  if(!ready){notify('等待材料搜索完成后再分析。','warn');return}
  state.busy=true;ui.generate.disabled=true;ui.generate.textContent='分析中…';renderAssessments();
  var body={horizonYears:Number(ui.horizon.value)||5},ids=selectedItemIds();if(ids.length)body.itemIds=ids;
  try{
    var result=await request('/tasks/'+encodeURIComponent(taskId)+'/assessments',{method:'POST',body:JSON.stringify(body)}),a=result.assessment||result.result||result,id=a&&(a.id||a.assessmentId),job=result.extractionJob;
    if(a){
      state.assessments=[a].concat(state.assessments.filter(function(x){return String(x.id||x.assessmentId)!==String(id)}));
      state.chartEvidence=null;state.selectedAssessmentId=id||null;state.activeStage='extract';state.selectedFindingIndex=null;state.selectedCapabilityIndex=null;state.selectedGraphNodeId=null;state.selectedGraphEdgeId=null;state.selectedGraphAssociationId=null;state.evidenceFilterId=null;
      state.extractionStartErrors=state.extractionStartErrors||{};
      if(result.extractionError)state.extractionStartErrors[id]=String(result.extractionError);else delete state.extractionStartErrors[id];
      if(job?.jobId&&String(job.assessmentId)===String(id)){rememberExtractionJob(job);scheduleExtractionJob(job,taskId,100)}
      updateDeepLink();renderReportPanels();
      // Fully automatic runs must close the analysis sub-chain themselves.
      // A report may contain findings while public metrics are still absent;
      // schedule quantification before waiting for object extraction.
      if(id&&state.task?.workflowMode==='auto'&&state.task.automation?.autoStartAssessment){
        try{
          var capability=a.summary?.capabilityAssessment||{},currentMetrics=asArray(capability.metrics),embeddedQuantification=!!result.quantificationSkipped||(
            String(capability.provider||'').toLowerCase().indexOf('deepseek')===0&&String(capability.model||'').trim().length>0
          );
          // An embedded DeepSeek quantification may validly contain zero
          // metrics. Do not re-submit it merely because the result is empty;
          // only a deterministic/legacy draft without a real model result
          // needs the explicit capability follow-up.
          if(!currentMetrics.length&&!embeddedQuantification){
            await request('/assessments/'+encodeURIComponent(id)+'/capabilities',{method:'POST',body:'{}'});
            await loadAssessments(id);
          }
        }catch(error){notify('自动公开指标量化失败，将按证据缺口保留并继续后续任务：'+error.message,'warn')}
      }
      if(id)await Promise.allSettled([loadReportAugmentations(id),loadExtractionJobs(id),loadLinkedImageryEvidence(id)]);
    }
    if(result.extractionError)notify('分析版本已保存，自动提取未启动；可在对象分析中重试。','warn');
    else if(job?.jobId)notify(extractionJobActive(job)?'分析版本已保存，正在自动提取对象、属性与事件。':['failed','partial'].includes(job.status)?'已恢复提取任务状态，可查看原因后重试或补提。':'已恢复分析版本及对象属性提取结果。',['failed','partial'].includes(job.status)?'warn':'success');
    else notify(a&&a.analysisType==='model-assessment'?'已生成研判草稿，等待人工审核。':'本次返回材料统计或证据不足，未自动启动对象提取。');
  }catch(error){notify('分析材料失败：'+error.message,'danger')}
  finally{state.busy=false;ui.generate.textContent='分析所选材料';loadExecutionTrace();renderTask();renderAssessments()}
}
async function reviewAssessment(action){var a=selectedAssessment();if(!a||["withdrawn","published","released"].includes(String(first(a,"status","reviewStatus","state")||"").toLowerCase()))return;var reviewer=$("reviewer")&&$("reviewer").value.trim()||"operator",reason=$("review-reason")&&$("review-reason").value.trim()||"";if(!reason){notify("请填写审核理由后再提交。","warn");$("review-reason")&&$("review-reason").focus();return}try{localStorage.setItem("osint.reviewer",reviewer)}catch(e){}var expectedRevision=first(a,"revision","version","expectedRevision")||1;try{var b=await request("/assessments/"+encodeURIComponent(a.id||a.assessmentId)+"/review",{method:"POST",body:JSON.stringify({action:action,reviewer:reviewer,reason:reason,expectedRevision:expectedRevision})}),next=b.assessment||b.review&&b.review.assessment||b.review||b;state.assessments=state.assessments.map(function(x){return String(x.id||x.assessmentId)===String(a.id||a.assessmentId)?Object.assign({},x,next,{review:next.review||{reviewer:reviewer,reason:reason},status:next.status||action}):x});renderReportPanels();notify(action==="accept"?"研判已通过人工审核。":action==="reject"?"研判已退回。":"已要求补充证据。",action==="reject"?"warn":"success")}catch(e){notify("审核提交失败："+e.message,"danger")}}
async function publishAssessment(){var a=selectedAssessment();if(!a||a.status!=="accepted")return;var reviewer=$("reviewer")&&$("reviewer").value.trim()||"operator",expectedRevision=first(a,"revision","version","expectedRevision")||1;try{var b=await request("/assessments/"+encodeURIComponent(a.id||a.assessmentId)+"/publish",{method:"POST",body:JSON.stringify({reviewer:reviewer,expectedRevision:expectedRevision})}),next=b.assessment||b.report||b;state.assessments=state.assessments.map(function(x){return String(x.id||x.assessmentId)===String(a.id||a.assessmentId)?Object.assign({},x,next,{status:next.status||"published"}):x});renderReportPanels();notify("报告已发布。发布与本体可信入库保持分离。")}catch(e){notify("报告发布失败："+e.message,"danger")}}
async function loadState(){var results=await Promise.allSettled([request("/state"),requestIntelligence("/state")]),osintResult=results[0],intelligenceResult=results[1];if(osintResult.status==="fulfilled"){var b=osintResult.value;state.sources=b.sources||[];state.tasks=b.tasks||[];renderSources();renderHistory();var params=new URL(location.href).searchParams,wanted=params.get("taskId")||state.task?.id||loadLastTask(),candidate=wanted?state.tasks.find(function(t){return t.id===wanted}):null;candidate=candidate||state.tasks[0];if(candidate)await loadTask(candidate.id,params.get("taskId")===candidate.id?params.get("assessmentId"):null);else resetTaskWorkspace()}else{renderSources();renderTask();notify("无法读取公开来源："+(osintResult.reason&&osintResult.reason.message||osintResult.reason),"danger")}if(intelligenceResult.status==="fulfilled")state.provider=intelligenceResult.value.trendProvider||intelligenceResult.value.provider||intelligenceResult.value.modelProvider||null;else state.provider=null;renderProvider()}
function setCollectionDrawerCollapsed(collapsed){var shell=document.querySelector('.trend-shell'),content=$('collection-drawer-content'),toggle=$('collection-drawer-toggle');shell.classList.toggle('is-settings-collapsed',collapsed);content.hidden=collapsed;toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',collapsed?'展开装备情报采编设置':'收起采编设置');toggle.title=collapsed?'装备情报采编设置 · 点击展开':'收起采编设置';try{localStorage.setItem('osint.collectionDrawerCollapsed',String(collapsed))}catch(e){}}
var collectionDrawerCollapsed=false;try{collectionDrawerCollapsed=localStorage.getItem('osint.collectionDrawerCollapsed')==='true'}catch(e){}setCollectionDrawerCollapsed(collectionDrawerCollapsed);
$('new-collection-task').addEventListener('click',startNewCollectionTask);
$('collection-drawer-toggle').addEventListener('click',function(){setCollectionDrawerCollapsed(!document.querySelector('.trend-shell').classList.contains('is-settings-collapsed'))});
ui.form.addEventListener("submit",createTask);
syncAutomationOptions();
$('imagery-form').addEventListener('submit',saveImageryEvidence);
$('imagery-parent-target').addEventListener('click',function(event){var button=event.target.closest('[data-open-parent-imagery]');if(button){state.activeStage='extract';loadTask(button.dataset.openParentImagery,button.dataset.parentAssessment)}});
$('imagery-form').elements.referenceType.addEventListener('change',function(){this.form.elements.license.required=this.value==='licensed-publication'});
$('imagery-form').elements.sourceUrl.addEventListener('input',function(){this.form.elements.sourceItemId.value='';$('imagery-source-binding').textContent='来源地址已修改；作为独立公开影像来源重新核验。'});
$('imagery-target').addEventListener('change',function(){var form=$('imagery-form'),a=selectedAssessment(),graph=a&&graphData(a);form.dataset.assessmentId=a?assessmentId(a):'';form.dataset.graphRevisionId=graph&&(graph.graphRevisionId||graph.id)||'';renderImageryProperties()});
$('imagery-source-list').addEventListener('click',function(event){
  var button=event.target.closest('[data-register-media]');if(!button)return;var candidate=mediaCandidates()[Number(button.dataset.registerMedia)];if(!candidate)return;
  resetImageryForm();var form=$('imagery-form');form.elements.title.value=(candidate.caption||candidate.title||'公开图片引用').slice(0,300);form.elements.sourceUrl.value=candidate.sourceUrl||'';form.elements.imageUrl.value=candidate.imageUrl||'';form.elements.sourceItemId.value=candidate.sourceItemId||'';
  $('imagery-source-binding').textContent='已关联保存材料：'+(candidate.title||candidate.sourceName||candidate.sourceItemId)+'；原始影像提供方、类别和日期仍需核对。';
  $('collection-imagery').open=true;form.closest('details').open=true;form.elements.mediaKind.focus();
});
$('source-default').addEventListener('click',function(){document.querySelectorAll('[data-source-id]').forEach(function(n){var source=state.sources.find(function(s){return s.id===n.dataset.sourceId});n.checked=!!source&&source.defaultSelected!==false});updateSourceCount()});
$("source-all").addEventListener("click",function(){document.querySelectorAll("[data-source-id]").forEach(function(n){n.checked=true});updateSourceCount()});
$("source-none").addEventListener("click",function(){document.querySelectorAll("[data-source-id]").forEach(function(n){n.checked=false});updateSourceCount()});
ui.sources.addEventListener("change",updateSourceCount);
$("refresh-osint").addEventListener("click",function(event){event.preventDefault();event.stopPropagation();loadState()});
ui.refresh.addEventListener("click",pollTask);
$("material-all").addEventListener("click",function(){var items=asArray(state.task?.items).filter(isTextEvidence),all=items.length&&items.every(function(i){return state.selectedItems.has(i.id)});state.selectedItems=all?new Set():new Set(items.map(function(i){return i.id}));renderMaterials()});
ui.generate.addEventListener("click",generateAssessment);
ui.materials.addEventListener('change',function(event){if(event.target.matches('[data-material-check]'))renderExecution()});
ui.materialAll.addEventListener('click',renderExecution);
ui.history.addEventListener("change",function(e){if(e.target.value)loadTask(e.target.value)});
if($('clear-task-history'))$('clear-task-history').addEventListener('click',async function(){if(!confirm('永久删除全部采编任务、材料、执行记录、分析版本和提取任务？此操作不可恢复。'))return;try{var result=await request('/history/purge',{method:'POST',body:JSON.stringify({reason:'Operator requested permanent reset'})});state.tasks=[];resetTaskWorkspace();notify('已永久删除 '+(result.purgedTaskCount||0)+' 个任务及全部关联数据，可开始新的测试。','success')}catch(error){notify('永久清理失败：'+error.message,'danger')}});
$('workflow-tabs').addEventListener('click',function(event){var tab=event.target.closest('[data-workflow-tab]');if(tab)setWorkflowStage(tab.dataset.workflowTab)});
$('workflow-tabs').addEventListener('keydown',function(event){var tabs=Array.from(this.querySelectorAll('[role="tab"]')),index=tabs.indexOf(event.target),next;if(index<0)return;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();setWorkflowStage(tabs[next].dataset.workflowTab,true)});
$('extract-from-execution').addEventListener('click',function(){if(state.selectedAssessmentId)startExtractionJob(state.selectedAssessmentId,{extractEntities:true})});
$('load-research-preset').addEventListener('click',async function(){try{var response=await fetch('./research-presets.json',{cache:'no-store'}),data=await response.json(),preset=data.presets?.find(function(p){return p.id==='tactical-uav-cooperation'})||data;ui.title.value=preset.title;ui.question.value=preset.question;ui.keywords.value=preset.keywords.join(', ');ui.since.value=String(preset.sinceDays);ui.horizon.value=String(preset.horizonYears);ui.maxItems.value=preset.maxItems;document.querySelectorAll('[data-source-id]').forEach(function(input){input.checked=preset.sourceIds.includes(input.dataset.sourceId)});updateSourceCount();setCollectionDrawerCollapsed(false);notify('已加载规范任务提示词，核对设置后即可开始采编。')}catch(e){notify('示例提示词读取失败：'+e.message,'warn')}});
$('assessment-version').addEventListener('change',function(event){if(event.target.value){state.selectedAdmissionId=null;selectAssessment(event.target.value)}});
$('back-workflow').addEventListener('click',function(){document.querySelector('.workflow-tabs').scrollIntoView({behavior:'smooth',block:'start'})});
setInterval(function(){if(state.task?.id&&(state.busy||Object.keys(state.augmentationBusy).length||activeExtractionJob(state.selectedAssessmentId)||['queued','running'].includes(state.task.status)))loadExecutionTrace()},4000);
startServiceStatus();
loadState();
loadAttributePredicates();




