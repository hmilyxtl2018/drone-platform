// Execution is described from recorded calls and saved outputs, never simulated.
const list = value => Array.isArray(value) ? value : [];
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timestamp = value => value ? Date.parse(value) : NaN;
const terminal = value => ['completed','succeeded','partial','failed','cancelled'].includes(value);
const labels = {done:'已完成',running:'执行中',partial:'部分完成',failed:'失败',waiting:'自动排队',manual:'待人工确认',blocked:'前置未就绪',unknown:'上次未完成',skipped:'预算跳过'};
const operations = {'collect-materials':'采集协调','search-source':'来源检索','search-images':'图片线索检索','fetch-feed':'读取公开订阅','deduplicate-materials':'落库前内容去重','generate-assessment':'生成趋势研判','chat-completions':'模型推理','extract-objects':'对象与关系提取','extract-entities':'对象与事件提取','extract-objects-and-attributes':'对象与属性提取','extract-object-attributes':'逐对象属性提取','build-graph':'构建证据图谱','verify-source-quotes':'证据完整性检查','model-critique':'模型反证复核','quantify-public-observations':'公开指标量化','propose-source-claims':'材料断言提取'};
const clocks = value => Number.isFinite(value) ? new Date(value).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '—';
  const seconds = Math.max(0,ms)/1000;
  if (seconds < .1) return '< 0.1 秒';
  if (seconds < 60) return seconds.toFixed(1)+' 秒';
  return Math.floor(seconds/60)+' 分 '+Math.floor(seconds%60)+' 秒';
}

export function pairExecutionCalls(events) {
  const calls = new Map();
  [...list(events)].sort((a,b) => (timestamp(a.timestamp)||0)-(timestamp(b.timestamp)||0)).forEach(event => {
    const id = event.callId || event.id;
    if (!id) return;
    const previous = calls.get(id) || {};
    const start = event.status === 'started' ? timestamp(event.timestamp) : previous.start;
    calls.set(id,{...previous,...event,id,details:{...previous.details,...event.details},start,
      end:event.status === 'started' ? undefined : timestamp(event.timestamp)});
  });
  return [...calls.values()].sort((a,b) => (a.start||a.end||0)-(b.start||b.end||0));
}

// Only compare peer tool calls in the same run. Parent coordinators overlap
// their children by definition and are not evidence of parallel scheduling.
export function concurrentToolPairs(calls) {
  const leaf = list(calls).filter(call => ['firecrawl','rss','deepseek'].includes(call.provider) && ['chat-completions','search-source','search-images','fetch-feed'].includes(call.operation) && Number.isFinite(call.start) && Number.isFinite(call.end));
  const pairs=[];
  leaf.forEach((a,index) => leaf.slice(index+1).forEach(b => {
    if (a.runId && a.runId===b.runId && a.stage===b.stage && Math.max(a.start,b.start)<Math.min(a.end,b.end)) pairs.push([a.id,b.id]);
  }));
  return pairs;
}

function phaseId(call) {
  if(call.operation==='deduplicate-materials') return 'fusion';
  if(call.stage==='collection') return 'collect';
  if(['trend-analysis','capability-analysis'].includes(call.stage)) return 'analysis';
  if(['object-extraction','object-attributes','graph-linking','finding-attribute-links'].includes(call.stage)) return 'extract';
  if(call.stage==='evidence-verification') return 'verify';
  return 'other';
}
function latestRun(calls) {
  const latest=[...calls].sort((a,b)=>(b.start||b.end||0)-(a.start||a.end||0))[0];
  return latest?.runId ? calls.filter(call=>call.runId===latest.runId) : latest?[latest]:[];
}
const WORKFLOW_IDLE_GAP_MS = 15 * 60 * 1000;
const recoveredCall = call => call.error?.code==='abandoned-call'||call.details?.recovery==='stale-call-reaper';
const observedCallEnd = call => recoveredCall(call)?NaN:call.end;
function currentWorkflowCalls(calls) {
  // Legacy traces have a run per stage, rather than a shared full-process ID.
  // Reconstruct continuous attempts from observed work, leaving a long idle
  // gap outside the latest attempt. Recovery timestamps are audit timestamps,
  // so they cannot bridge a stopped service to a later retry.
  const sessions=[];
  const callTime=call=>Number.isFinite(call.start)?call.start:observedCallEnd(call);
  calls.filter(call=>Number.isFinite(callTime(call))).sort((a,b)=>callTime(a)-callTime(b)).forEach(call=>{
    const start=callTime(call),end=observedCallEnd(call),boundary=Number.isFinite(end)?Math.max(start,end):start;
    const previous=sessions.at(-1);
    if(!previous||start-previous.end>WORKFLOW_IDLE_GAP_MS)sessions.push({start,end:boundary,calls:[call]});
    else {previous.calls.push(call);previous.end=Math.max(previous.end,boundary);}
  });
  return sessions.at(-1)||{calls:[],start:NaN,end:NaN};
}
function timing(calls,live,now) {
  if(!calls.length) return {duration:null};
  const starts=calls.map(call=>call.start).filter(Number.isFinite),ends=calls.map(observedCallEnd).filter(Number.isFinite),open=calls.some(call=>call.status==='started');
  const latestOpenStart=Math.max(...calls.filter(call=>call.status==='started').map(call=>call.start).filter(Number.isFinite),-Infinity);
  // `duration` is the wall-clock span users experience.  Keep the sum of
  // individual call durations separately: parent/coordinator calls overlap
  // their children and must never be added to the wall-clock value.
  // Coordinator records wrap the provider transport in the same run. They
  // describe orchestration, not additional elapsed work, so cumulative call
  // time only sums external leaf calls. This prevents a parent DB/queue
  // span from being added on top of the DeepSeek/Firecrawl request it owns.
  const leafOperations=new Set(['chat-completions','search-source','search-images','fetch-feed']);
  const leafCalls=calls.filter(call => leafOperations.has(call.operation));
  const activeDuration=leafCalls.reduce((sum,call)=>{
    // A historical trace with only a `started` record is interrupted/unknown.
    // Do not turn it into elapsed time with the page's current clock. Only a
    // live run may use `now` as the temporary end boundary.
    const recordedEnd=observedCallEnd(call);
    const callEnd=Number.isFinite(recordedEnd)?recordedEnd:(call.status==='started'&&live&&Number.isFinite(now)?now:NaN);
    return Number.isFinite(call.start)&&Number.isFinite(callEnd)?sum+Math.max(0,callEnd-call.start):sum;
  },0);
  if(starts.length!==calls.length) return {duration:null,activeDuration,incomplete:true};
  const start=Math.min(...starts),end=open?(live&&Number.isFinite(now)?now:NaN):ends.length?Math.max(...ends):NaN;
  return {start,end,duration:Number.isFinite(end)?Math.max(0,end-start):null,activeDuration,live:open&&live,interrupted:open&&!live,
    latestOpenStart:Number.isFinite(latestOpenStart)?latestOpenStart:null};
}
function workflowTiming(base,{task,assessment,job,rounds=[],admissions=[],live=false,now=Date.now()}={}) {
  // A task can be retried weeks after it was created. Only execution starts
  // near this attempt can extend its boundary; creation is never execution.
  const nearAttempt=value=>Number.isFinite(value)&&(!Number.isFinite(base.start)||Math.abs(value-base.start)<=WORKFLOW_IDLE_GAP_MS);
  const starts=[base.start,timestamp(task?.startedAt),timestamp(job?.startedAt),timestamp(job?.createdAt)].filter(nearAttempt);
  // Automatic validation and candidate creation finish work after the last
  // provider response. Count their original creation, never later edits.
  // Review/publish updates are lifecycle metadata, not execution boundaries.
  // They can occur long after collection and would inflate the reported wall
  // clock. A closed run must have a real completion timestamp; otherwise the
  // duration remains unknown until the trace is closed or the task/job marks
  // completion.
  const ends=[base.end,timestamp(task?.completedAt),timestamp(job?.completedAt),timestamp(assessment?.completedAt),
    ...rounds.map(row=>timestamp(row.createdAt)),...admissions.map(row=>timestamp(row.createdAt))]
    .filter(end=>Number.isFinite(end)&&(!Number.isFinite(base.start)||end>=base.start)&&(Number.isFinite(base.end)?end-base.end<=WORKFLOW_IDLE_GAP_MS:end-base.start<=WORKFLOW_IDLE_GAP_MS));
  // A persisted completion marker is usable only when it occurs after the
  // last unclosed transport started. This prevents an old/early task marker
  // from falsely closing a later model call; until a valid boundary exists,
  // the workflow duration remains unknown.
  const latestOpenStart=Number.isFinite(base.latestOpenStart)?base.latestOpenStart:NaN;
  const viableEnds=ends.filter(end=>!Number.isFinite(latestOpenStart)||end>=latestOpenStart);
  const start=starts.length?Math.min(...starts):NaN;
  const end=live?now:viableEnds.length?Math.max(...viableEnds):NaN;
  return {...base,start,end,duration:Number.isFinite(start)&&Number.isFinite(end)?Math.max(0,end-start):null,live:base.live||live};
}
function manualEndTime(phase,assessment,rounds,admissions,job) {
  if (phase==='snapshot'||phase==='analysis') return timestamp(assessment?.createdAt);
  if (phase==='extract') return timestamp(job?.completedAt||job?.updatedAt||job?.createdAt);
  if (phase==='verify') return timestamp(rounds[0]?.createdAt||rounds[0]?.completedAt);
  if (phase==='admit') return timestamp(admissions[0]?.review?.reviewedAt||admissions[0]?.updatedAt||admissions[0]?.createdAt);
  return NaN;
}
const DEEPSEEK_BATCH_SIZE = 8;
const ceilBatches = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Math.ceil(Number(value) / DEEPSEEK_BATCH_SIZE) : 0;
function deepseekCallStats(calls, {textCount=0, objectCount=0, findingCount=0, automation={}} = {}) {
  // A chat-completions event is emitted by the provider for every real
  // DeepSeek request.  Count these transport records only; coordinator
  // events (generate-assessment, extract-object-attributes, etc.) are not
  // additional model calls and would otherwise double-count the run.
  const records = list(calls).filter(call => call.provider === 'deepseek' && call.operation === 'chat-completions');
  const attemptsFor = row => 1 + (Number.isFinite(Number(row.details?.retryCount)) ? Math.max(0, Number(row.details.retryCount)) : 0);
  const bucket = (name, predicate) => {
    const rows = records.filter(predicate);
    return {name, total: rows.reduce((sum,row) => sum + attemptsFor(row), 0), requests: rows.length, succeeded: rows.filter(row => row.status === 'succeeded').length,
      failed: rows.filter(row => row.status === 'failed').length,
      open: rows.filter(row => row.status === 'started').length};
  };
  // Older extraction runs emitted every transport request under
  // `object-extraction`, even for the per-object attribute pass and the
  // finding/attribute linker.  Preserve explicit stage names, while
  // classifying those legacy records from their bounded output contract:
  // 3072 tokens is the attribute request, 8192 is identity extraction, and
  // 4096 is the finding linker (reported as other work).
  const extractionStage = call => {
    if (call.stage === 'object-attributes') return 'objectAttributes';
    if (call.stage !== 'object-extraction') return null;
    const limit = Number(call.details?.outputTokenLimit);
    if (limit === 3072) return 'objectAttributes';
    if (limit === 4096) return 'other';
    return 'objectRecognition';
  };
  const stages = [
    bucket('trend', call => call.stage === 'trend-analysis'),
    bucket('quantification', call => call.stage === 'capability-analysis'),
    bucket('objectRecognition', call => extractionStage(call) === 'objectRecognition'),
    bucket('objectAttributes', call => extractionStage(call) === 'objectAttributes'),
    bucket('verification', call => call.stage === 'evidence-verification'),
    bucket('findingLinks', call => call.stage === 'finding-attribute-links'),
    bucket('other', call => extractionStage(call) === 'other' || !['trend-analysis','capability-analysis','object-extraction','object-attributes','evidence-verification','finding-attribute-links'].includes(call.stage)),
  ];
  const auto = automation && typeof automation === 'object' ? automation : {};
  // These are a transparent workload estimate, not a fabricated result. The
  // estimate uses the configured eight-material prompt batch and the actual
  // one-object-per-request attribute contract. Retries remain visible in the
  // actual count and are never silently folded into the estimate.
  const plannedStages = [
    {name:'trend', planned:ceilBatches(textCount), basis:textCount ? `ceil(${textCount}/8)` : '无正文'},
    {name:'quantification', planned:ceilBatches(textCount), basis:textCount ? `ceil(${textCount}/8)` : '无正文'},
    {name:'objectRecognition', planned:ceilBatches(textCount), basis:textCount ? `ceil(${textCount}/8)` : '无正文'},
    {name:'objectAttributes', planned:Number(objectCount) > 0 ? Number(objectCount) : 0, basis:Number(objectCount) > 0 ? `${objectCount} 个对象 × 1 次` : '对象数待识别'},
    {name:'verification', planned:auto.autoRunModelCritique ? ceilBatches(textCount) : 0, basis:auto.autoRunModelCritique ? `反证复核 ceil(${textCount}/8)` : '未启用 DeepSeek 反证复核'},
    {name:'findingLinks', planned:Number(objectCount) > 0 && Number(findingCount) > 0 ? 1 : 0, basis:Number(objectCount) > 0 && Number(findingCount) > 0 ? '对象属性完成后 1 次趋势-属性语义关联' : '无对象或趋势判断'},
  ];
  const planned = plannedStages.reduce((sum, row) => sum + row.planned, 0);
  const actual = records.reduce((sum,row) => sum + attemptsFor(row), 0);
  return {records, stages, plannedStages, actual, planned, delta: actual - planned,
    succeeded: records.filter(row => row.status === 'succeeded').length,
    failed: records.filter(row => row.status === 'failed').length,
    open: records.filter(row => row.status === 'started').length,
    batchSize: DEEPSEEK_BATCH_SIZE};
}
function callStatus(calls,live,fallback) {
  const recent=latestRun(calls);
  if(!recent.length) return fallback;
  if(recent.some(call=>call.status==='started')) return live?'running':'unknown';
  if(recent.some(call=>call.status==='failed')) return recent.some(call=>call.status==='succeeded')?'partial':'failed';
  return 'done';
}

function phaseProgress(id,{task,assessment,items,textItems,selected,findings,nodes,edges,job,rounds,admissions,phaseCalls}) {
  const selectedText=selected.length+' / '+textItems.length+' 条正文';
  if(id==='collect') {
    const total=list(task?.sourceIds).length,done=list(task?.sourceResults).filter(row=>['ok','partial','error','failed','skipped-budget'].includes(row.status)).length;
    return task ? '来源组 '+done+' / '+total+' · 已落库 '+items.length+' 条材料' : '尚未创建任务';
  }
  if(id==='fusion') return task&&terminal(task.status) ? '已落库 '+items.length+' 条材料 · 可分析正文 '+textItems.length+' 条' : '等待来源收集完成';
  if(id==='snapshot') {
    if(assessment) return '快照已固化 · '+list(assessment.sourceSnapshot).length+' 条正文';
    if(selected.length===textItems.length&&textItems.length) return '进度 '+selectedText+' · 等待固化快照';
    if(selected.length) return '进度 '+selectedText+' · 继续选择材料';
    return textItems.length ? '进度 0 / '+textItems.length+' 条正文 · 等待选择' : '等待正文材料';
  }
  if(id==='analysis') {
    if(assessment) {
      const metricCount=list(assessment.summary?.capabilityAssessment?.metrics).length;
      return '分析版本已生成 · '+findings.length+' 项趋势判断 · '+metricCount+' 项公开指标'+(metricCount?'':' · 待补证');
    }
    if(phaseCalls.some(call=>call.operation==='chat-completions'&&call.status==='started')) return '进度：已提交 '+selectedText+' · 等待模型返回';
    if(phaseCalls.some(call=>call.operation==='generate-assessment'&&call.status==='started')) return '快照固化中 · 等待模型调用';
    return textItems.length ? '进度：已选 '+selectedText+' · 等待自动启动' : '等待快照材料';
  }
  if(id==='extract') {
    const total=job?.progress?.totalObjects,processed=job?.progress?.processedObjects;
    if(total!=null) return '对象进度 '+(processed||0)+' / '+total+' · '+nodes.length+' 个对象已保存';
    if(nodes.length) return '已保存 '+nodes.length+' 个对象 · '+edges.length+' 条关系';
    return assessment ? '等待自动启动对象与属性提取' : '等待研判版本';
  }
  if(id==='verify') return rounds.length ? '已完成 '+rounds.length+' 轮核验 · 待处理问题 '+rounds.reduce((sum,row)=>sum+list(row.issues).length,0) : assessment ? '等待核验证据完整性' : '等待研判版本';
  if(id==='admit') return admissions.length ? '准入候选 '+admissions.length+' 项 · 已发布 '+admissions.filter(row=>['published','released'].includes(row.status)).length+' 项' : assessment ? '等待生成准入候选' : '等待研判版本';
  return '';
}

export function buildExecutionModel(context,now=Date.now()) {
  const task=context.task,assessment=context.assessment,assessmentId=assessment?.id||assessment?.assessmentId;
  const allCalls=pairExecutionCalls(context.trace?.executionTrace);
  // A full automatic run can create a base trend assessment and then a
  // derived capability assessment. The task execution endpoint returns both
  // runs; keep them together here so the overview reports the complete
  // process instead of silently dropping the base trend calls when the
  // derived assessment is selected.
  const calls=allCalls.filter(call=>call.stage==='collection'||(task?.id&&(call.taskId===task.id||(!call.taskId&&call.assessmentId===assessmentId)))||(assessmentId&&!task?.id&&call.assessmentId===assessmentId));
  const verificationFailedCalls=calls.filter(call=>call.stage==='evidence-verification'&&call.status==='failed');
  const unbound=allCalls.filter(call=>call.stage!=='collection'&&(!call.assessmentId|| (task?.id&&call.taskId!==task.id)));
  const items=list(task?.items),textItems=items.filter(item=>item.provenance?.retrievalKind!=='search-discovery-only'&&item.provenance?.contentKind!=='image-search-metadata');
  const selected=list(context.selectedIds).filter(id=>textItems.some(item=>item.id===id));
  const nodes=list(context.records?.nodes),edges=list(context.records?.edges),rounds=list(context.rounds),admissions=list(context.admissions);
  const job=list(context.jobs).find(job=>['queued','running','interrupted'].includes(job.status))||list(context.jobs)[0];
  const taskLive=!!task&&!terminal(task.status),jobLive=['queued','running'].includes(job?.status);
  const snapshot=list(assessment?.sourceSnapshot),findings=list(assessment?.summary?.findings),budget=task?.brief?.collectionBudget||{};
  const automation=task?.automation||{};
  const autoSelect=automation.autoSelectMaterials===true;
  const autoAssessment=automation.autoStartAssessment===true;
  const autoVerification=automation.autoRunEvidenceCheck===true||automation.autoRunModelCritique===true;
  const autoAdmission=automation.autoCreateAdmissionCandidates===true;
  const autoSelectionComplete=autoSelect&&textItems.length>0&&selected.length===textItems.length;
  const sourceIds=list(task?.sourceIds),sourceResults=list(task?.sourceResults);
  // A source result marked `partial` is a terminal collection result: the
  // scope ran to its retry/round limit and returned what it could collect.
  // The shortfall belongs to the collection budget, not to the execution
  // state of the source branch.
  const sourceResultsComplete=sourceIds.length>0&&sourceIds.every(sourceId=>sourceResults.some(row=>row.sourceId===sourceId&&['ok','partial','error','failed','skipped-budget'].includes(row.status)));
  const sourceFailures=sourceResults.some(row=>['error','failed'].includes(row.status));
  const collectionStatus=taskLive?'running':!task?'blocked':sourceResultsComplete?(sourceFailures?'partial':'done'):task.status==='failed'?'failed':'partial';
  // Assessment creation has two observable subphases in the trace: the
  // coordinator creates the immutable snapshot first, then the model call
  // starts. Use those records instead of the page-wide busy flag so the
  // journey cannot show "waiting" and "running" for the same work.
  const assessmentCalls=allCalls.filter(call=>call.stage==='trend-analysis'&&(!assessmentId||call.assessmentId===assessmentId||!call.assessmentId));
  const quantificationCalls=allCalls.filter(call=>call.operation==='quantify-public-observations'&&(!assessmentId||call.assessmentId===assessmentId));
  // Only an explicit public-metric operation can fail this sub-step. A
  // failed trend-model call may be followed by a deterministic report
  // fallback, and a report with no metric is a coverage gap rather than a
  // failed quantification request.
  const quantificationRun=latestRun(quantificationCalls);
  const quantificationFailed=quantificationRun.some(call=>call.status==='failed');
  const quantificationAttempted=quantificationRun.length>0;
  const assessmentCoordinatorOpen=assessmentCalls.some(call=>call.operation==='generate-assessment'&&call.status==='started');
  const modelCallOpen=assessmentCalls.some(call=>call.operation==='chat-completions'&&call.status==='started');
  const modelCallStarted=assessmentCalls.some(call=>call.operation==='chat-completions'&&['started','succeeded','failed'].includes(call.status));
  const snapshotStatus=assessment?'done':assessmentCoordinatorOpen?(modelCallStarted?'done':context.busy?'running':'unknown'):autoSelectionComplete?'waiting':textItems.length?(autoSelect?'waiting':'manual'):'blocked';
  const states=nodes.map(node=>node.payload?.attributeExtraction?.status);
  const attributeCount=nodes.reduce((sum,node)=>sum+list(node.payload?.attributes).length,0);
  const published=admissions.filter(row=>['published','released'].includes(row.status)).length;
  const reviewIssues=rounds.length && (rounds[0].status!=='passed'||list(rounds[0].issues).length>0);
  const definitions=[
    {id:'plan',title:'任务校验与预算分配',dependency:'研究问题、来源与范围',mode:'自动',status:task?'done':'waiting',result:task?list(task.sourceIds).length+' 个来源组 · 最多 '+task.maxItems+' 条材料':'等待创建采编任务',
      input:['title · question · keywords[]','sourceIds[] · sinceDays · maxItems'],output:['合法采编任务 taskId','来源配额 queryPlan[] · collectionBudget'],rules:'服务端校验允许来源、时间窗和材料上限。Firecrawl 总预算分配至各来源组，余额按官方来源优先分配；零配额组跳过。',failure:'参数校验失败则不启动采集。排队和校验暂未单独计时。'},
    {id:'collect',title:'来源检索与正文收集',dependency:'01 任务校验',mode:'自动 · 来源组串行',status:collectionStatus,result:task?sourceResults.length+' / '+sourceIds.length+' 个来源组已完成'+(budget.targetReached===false?' · 材料目标未满足（'+items.length+' / '+(budget.maxItems||task.maxItems||'—')+'）':''):'等待来源采集',
      input:['taskId · 来源配额 · scoped query','允许域名 · 时间窗 · 主题表达式'],output:['sourceResults[] · items[] · errors[]','来源正文、URL、日期与检索溯源'],rules:'当前按来源组逐组调用 Firecrawl / RSS。各来源逻辑上独立，但尚未采用并发调度；每组返回后再处理下一组。',failure:'单一来源失败继续其他来源；保留可用结果并标为部分完成。无可用材料时不能推进模型研判。'},
    {id:'fusion',title:'筛选、去重与材料落库',dependency:'02 来源采集结果',mode:'自动 · 串行汇合',status:terminal(task?.status)&&(task.status!=='failed'||task.steps?.[2]?.status==='done')?'done':'blocked',result:items.length+' 条材料 · '+textItems.length+' 条可分析正文 · '+(items.length-textItems.length)+' 条发现线索',
      input:['各来源 items[] · sourceResults[]','itemUri · contentHash · provenance'],output:['持久化材料 itemId · sourceVersion','contentHash · body · provenance.retrievedInScopes[]'],rules:'检索内按规范化 URL 或内容哈希合并重复结果，保留命中来源组；落库前再按内容哈希去重。正文与图片发现线索分开，结果不超过任务材料上限。',failure:'重复项合并溯源；过滤域外、时间窗外、无效 URL、无正文或主题不符材料。失败来源及过滤计数保留，不视为独立证据。',timingNote:'本步骤仅记录落库前内容去重的耗时；来源内筛选计入来源调用，数据库保存尚未独立计时。'},
    {id:'snapshot',title:'选材与证据快照',dependency:'03 可用正文材料',mode:autoSelect?'自动选择正文':'人工选择正文',status:snapshotStatus,result:assessment?snapshot.length+' 条材料已固化到当前分析版本':assessmentCoordinatorOpen?(modelCallStarted?'快照已固化 · '+selected.length+' / '+textItems.length+' 条正文':context.busy?'已选择 '+selected.length+' / '+textItems.length+' 条正文 · 正在固化快照':'快照固化请求未闭合 · 请刷新记录或重新触发'):autoSelect?'已自动选择 '+selected.length+' / '+textItems.length+' 条可分析正文':'已选择 '+selected.length+' / '+textItems.length+' 条可分析正文',
      input:['用户选择 itemIds[] · taskId','研究问题 · horizonYears'],output:['assessmentId · sourceSnapshot[]','itemId · sourceVersion · contentHash · body · URL'],rules:'点击“分析所选材料”后，服务端生成所选材料的不可变快照。后续分析、提取、核验引用同一快照，切换材料需要生成新的分析版本。',failure:'当前未勾选材料时会默认使用全部正文；没有正文不能分析。当前版本的快照与页面正在勾选的材料分开记录。',action:!assessment&&textItems.length?'materials':null,actionLabel:'查看并选择材料'},
    {id:'analysis',title:'趋势研判 → 公开指标量化',dependency:'04 已固化的材料快照',mode:autoAssessment?'自动启动 · 内部串行':'人工启动 · 内部串行',status:assessment?(quantificationFailed?'failed':findings.length&&list(assessment.summary?.capabilityAssessment?.metrics).length?'done':'partial'):modelCallOpen?(context.busy?'running':'unknown'):modelCallStarted?'unknown':assessmentCoordinatorOpen?'waiting':autoAssessment&&textItems.length?'waiting':textItems.length?'manual':'blocked',result:assessment?(assessment.analysisType==='model-assessment'?'模型草稿 · ':'材料统计 · ')+findings.length+' 项趋势判断 · '+list(assessment.summary?.capabilityAssessment?.metrics).length+' 项公开指标'+(quantificationFailed?' · 量化调用失败 · 可重试':list(assessment.summary?.capabilityAssessment?.metrics).length?'':quantificationAttempted?' · 量化已执行但无可用指标':' · 量化尚未执行'):modelCallOpen?(context.busy?'正在调用模型 · 已提交 '+selected.length+' 条正文':'模型调用请求未闭合 · 请刷新记录或重新触发'):modelCallStarted?'模型调用已开始但尚未收到结束记录':assessmentCoordinatorOpen?'快照固化完成 · 等待模型调用':autoAssessment&&textItems.length?'已选 '+selected.length+' 条正文 · 等待自动启动':'等待分析所选材料',
      input:['sourceSnapshot[] · question · horizonYears','每条材料的稳定 itemId 与有界正文窗口'],output:['研判草稿 summary.findings[]','evidenceIds[] · confidence · uncertainty · 公开指标'],rules:'当前先生成趋势判断，再提取公开量化观测。DeepSeek 按每批最多 8 条正文分批读取完整快照，材料数量不设固定上限；单批上下文超限时压缩该批并重试，仍保留其余批次。正文不足两条或模型不可用时可能返回材料统计与证据不足。模型调用成功不等于结论核验通过。',failure:'未知引用或无证据数字不能进入有效结论。模型降级、正文窗口与覆盖缺口以报告结果为准；重复推理不提高置信度。',action:assessment?'assessment':null,actionLabel:'查看研判结果'},
    {id:'extract',title:'对象识别 → 逐对象属性 → 证据图谱',dependency:'04 快照 + 05 模型分析版本',mode:'自动衔接 · 单任务串行',status:job?.status==='interrupted'?'manual':jobLive?'running':job?.status==='partial'?'partial':job?.status==='failed'?'failed':nodes.length?(states.some(s=>['failed','budget-skipped','skipped-budget'].includes(s))?'partial':'done'):assessment?(autoAssessment?'waiting':'manual'):'blocked',result:nodes.length+' 个对象 · '+attributeCount+' 项属性 · '+edges.length+' 条关系'+(job?.progress?.totalObjects!=null?' · 本批 '+(job.progress.processedObjects||0)+' / '+job.progress.totalObjects+' 对象':''),
      input:['assessmentId · sourceSnapshot · 图谱基础版本','提取选项 · objectIds[]（补提批次可选）'],output:['nodes[] · edges[] · graphRevisionId','属性与关系引文、evidenceIds、逐对象检查点'],rules:'模型研判版本保存后，由服务端自动提交对象与属性提取任务；先识别对象与关系，再逐对象提取属性、保存检查点并构建图谱。重复分析复用已有任务，重开页面只恢复进度。单报告最多一个活动任务，全局最多两个并发；每批最多处理 8 个对象的属性。',failure:'任务未启动时可重试；失败或预算跳过的对象可补提。属性无原文支持时先补证，不能靠重复模型调用补造。既有产出保留，人工审核后才可入库。',action:assessment?'extract':null,actionLabel:jobLive?'查看逐对象进度':'查看对象与属性'},
    {id:'verify',title:'证据完整性与反证复核',dependency:'05 研判；关联对象需 06 图谱',mode:autoVerification?'自动按选项 · 两类检查独立':'人工触发 · 两类检查独立',status:rounds.length?(rounds.some(round=>round.status==='failed')||verificationFailedCalls.length?'partial':'done'):assessment?(autoVerification?'waiting':'manual'):'blocked',result:rounds.length+' 轮核验 · '+rounds.filter(round=>round.kind==='model-critique').length+' 轮模型反证复核'+(rounds.some(round=>round.status==='failed')?' · '+rounds.filter(round=>round.status==='failed').length+' 轮检查失败，需重试':'')+(verificationFailedCalls.length?' · '+verificationFailedCalls.length+' 次模型/核验调用失败，需重试':'')+(reviewIssues?' · '+rounds.reduce((sum,row)=>sum+list(row.issues).length,0)+' 项需补证/人工确认':''),
      input:['当前分析版本 · sourceSnapshot','图谱对象/属性/关系与待核验断言'],output:['validationRound · checks[] · issues[]','对象相关核验结果 · confidenceFactors'],rules:'证据完整性检查与 DeepSeek 反证复核分别手动启动，当前没有自动并行验证编排。核验回指当前版本及相关对象；同模型复核最多 3 轮，不等价于独立来源验证。',failure:'核验有缺口显示需补证；机器检查通过仍需人工审核，不能自动升为高置信度。',action:assessment?'admission':null,actionLabel:'查看核验与结论'},
    {id:'admit',title:'人工审核 → 本体准入与发布',dependency:'06 对象候选 + 准入证据校验',mode:autoAdmission?'自动生成候选 · 人工发布':'人工审核 · 独立发布',status:published?'done':admissions.length?'manual':assessment?(autoAdmission?'waiting':'manual'):'blocked',result:admissions.length+' 项准入候选 · '+published+' 项已发布'+(admissions.length&&!published?' · 等待逐条人工确认':'') ,
      input:['graphRevisionId · 选定对象/属性/关系','准入候选版本 · 审核理由 · 证据引用'],output:['人工审核记录 · 发布结果','本体库中可追溯的对象与断言'],rules:'候选对象、属性、关系与事件经逐项人工审核，满足准入校验后执行独立发布。本体发布与研判报告发布是不同动作。',failure:'被退回、证据不足或版本冲突的候选保持待处理，补证后重审。高置信度由人工核验决定。',action:assessment?'admission':null,actionLabel:'查看准入候选'}
  ];
  // Task validation and budget allocation are internal admission checks. Keep
  // them in the server trace, but start the user-facing journey at collection.
  const phases=definitions.filter(phase=>phase.id!=='plan').map(phase=>{
    const phaseCalls=calls.filter(call=>phaseId(call)===phase.id),statusCalls=phase.id==='analysis'?phaseCalls.filter(call=>call.operation!=='generate-assessment'):phaseCalls,live=phase.id==='collect'?taskLive:phase.id==='extract'?jobLive||!!context.busy:!!context.busy;
    const callOpen=statusCalls.some(call=>call.status==='started');
    const observed=callStatus(statusCalls,live,phase.status);
    // Saved semantic results outrank transport history. A later retry or an
    // orphaned failed call must not turn a persisted result red; the phase
    // status already reflects the saved result and its evidence gaps.
    const status=['done','partial','failed'].includes(phase.status)?phase.status:observed;
    let phaseTiming=timing(latestRun(statusCalls),live,now);
    if(phase.id==='snapshot'&&assessmentCoordinatorOpen){
      const coordinator=latestRun(assessmentCalls.filter(call=>call.operation==='generate-assessment'));
      const start=coordinator.map(call=>call.start).filter(Number.isFinite).sort((a,b)=>a-b)[0];
      const modelStart=assessmentCalls.filter(call=>call.operation==='chat-completions').map(call=>call.start).filter(Number.isFinite).sort((a,b)=>a-b)[0];
      const end=modelCallStarted&&Number.isFinite(modelStart)?modelStart:undefined;
      phaseTiming={start,end,duration:Number.isFinite(start)&&Number.isFinite(end)?Math.max(0,end-start):null,live:!Number.isFinite(end)&&context.busy,interrupted:!Number.isFinite(end)&&!context.busy};
    }
    // A phase is human only when it is actually waiting for a human action,
    // or when the final ontology admission gate has produced candidates.
    // Phrases such as "人工发布" describe a later gate and must not make an
    // upstream automatic phase look manual while it is still waiting.
    const manual=phase.status==='manual'||(phase.id==='admit'&&phase.status==='done');
    return {...phase,status,calls:phaseCalls,timing:phaseTiming,manual,manualEnd:manualEndTime(phase.id,assessment,rounds,admissions,job),runs:new Set(phaseCalls.map(call=>call.runId||call.id)).size};
  });
  phases.forEach(phase=>{
    phase.progress=phaseProgress(phase.id,{task,assessment,items,textItems,selected,findings,nodes,edges,job,rounds,admissions,phaseCalls:phase.calls});
    if(phase.id==='snapshot'&&phase.status==='done'&&!assessment)phase.progress='快照已固化 · '+selected.length+' / '+textItems.length+' 条正文';
    if(phase.id==='snapshot'&&phase.status==='unknown')phase.progress='快照固化已开始但未完成 · 请刷新记录或重新触发';
    if(phase.id==='analysis'&&phase.status==='unknown')phase.progress='模型调用已开始但未结束 · 请刷新记录或重新触发';
  });
  const sourceCalls=calls.filter(call=>['firecrawl','rss'].includes(call.provider)&&call.stage==='collection');
  const sourceRows=list(task?.sourceIds).map(sourceId=>{
    const result=list(task.sourceResults).find(row=>row.sourceId===sourceId),scope=list(context.sources).find(row=>row.id===sourceId);
    const branchCalls=sourceCalls.filter(call=>call.details?.sourceId===sourceId),recent=latestRun(branchCalls);
    // Historical failed attempts are retained for audit, but a later
    // successful/partial terminal result supersedes them. Treating any old
    // failed retry as the branch status made completed source groups appear
    // failed in the execution journey.
    const branchFailed=!!result?.error||['error','failed'].includes(result?.status);
    const status=result?.status==='skipped-budget'?'skipped':branchFailed?'failed':['ok','partial'].includes(result?.status)?'done':callStatus(recent,taskLive,taskLive?'waiting':'unknown');
    return {sourceId,name:result?.sourceName||scope?.name||sourceId,result,observed:recent.at(-1)?.details||{},calls:branchCalls,status,gap:result?.status==='partial',timing:timing(recent,taskLive,now)};
  });
  const coverage=sourceRows.filter(row=>row.result?.rawResultCount!=null);
  const removed=list(task?.sourceResults).reduce((sum,row)=>sum+(Number(row.duplicateCount)||0),0);
  const dedup=calls.filter(call=>call.operation==='deduplicate-materials').at(-1)?.details;
  const duplicateKnown=list(task?.sourceResults).some(row=>row.duplicateCount!=null)||dedup?.removedDuplicateCount!=null;
  // The headline clock represents the full automated run (collection,
  // analysis and extraction), rather than only the collector coordinator.
  // Users compare it with the end-to-end wall clock shown by the workflow.
  const workflowSession=currentWorkflowCalls(calls);
  const sessionCalls=workflowSession.calls;
  const sessionCallIds=new Set(sessionCalls.map(call=>call.id));
  const historyCalls=calls.filter(call=>!sessionCallIds.has(call.id));
  const traceClock=timing(sessionCalls.filter(call=>call.stage!=='other'),taskLive||jobLive||!!context.busy,now);
  // Some queue/DB work is persisted on the task or extraction job without a
  // provider call of its own. Include those lifecycle boundaries so the
  // headline covers the full automated workflow shown to the user.
  const collectionClock=workflowTiming(traceClock,{task,assessment,job,rounds:autoVerification?rounds:[],admissions:autoAdmission?admissions:[],live:taskLive||jobLive||!!context.busy,now});
  collectionClock.previousCalls=historyCalls.length;
  collectionClock.recoveredCalls=sessionCalls.filter(recoveredCall).length;
  const modelCalls=sessionCalls.filter(call=>call.provider==='deepseek'&&call.operation==='chat-completions');
  const historyModelCalls=historyCalls.filter(call=>call.provider==='deepseek'&&call.operation==='chat-completions');
  const attributeObjectCount=nodes.filter(node=>node?.payload?.extractionMethod==='llm'||node?.type==='EquipmentModel'||node?.type==='Technology'||node?.type==='Organization'||node?.type==='Program'||node?.type==='EventRecord').length;
  // Once an assessment exists, prefer its immutable source snapshot. Legacy
  // fixtures may omit that field; fall back only when the snapshot is empty.
  const immutableSnapshotCount=snapshot.filter(item=>item?.provenance?.retrievalKind!=='search-discovery-only'&&item?.provenance?.contentKind!=='image-search-metadata').length;
  const immutableTextCount=immutableSnapshotCount|| (selected.length||textItems.length);
  // A continuation job can report only its selected eight-object batch. Keep
  // the full report workload visible by taking the larger of that progress
  // value and the object set already present in the immutable graph.
  const plannedObjectCount=Math.max(Number(job?.progress?.totalObjects)||0,attributeObjectCount);
  const deepseekStats=deepseekCallStats(sessionCalls,{textCount:immutableTextCount,objectCount:plannedObjectCount,findingCount:findings.length,automation});
  const historyDeepseekStats=deepseekCallStats(historyCalls,{textCount:0,objectCount:0,findingCount:0,automation:{}});
  const reusesMaterials=textItems.length>0&&!sessionCalls.some(call=>call.stage==='collection')&&sessionCalls.length>0;
  const sessionStartPhase=sessionCalls.length?phaseId(sessionCalls[0]):null;
  return {task,assessment,assessmentId,calls,sessionCalls,historyCalls,reusesMaterials,sessionStartPhase,unbound,phases,sourceRows,budget,items,textItems,selected,collectionClock,modelCalls,historyModelCalls,modelCallGroups:new Set(modelCalls.map(call=>call.runId||call.id)).size,historyModelCallGroups:new Set(historyModelCalls.map(call=>call.runId||call.id)).size,deepseekStats,historyDeepseekStats,
    overlap:concurrentToolPairs(sourceCalls),rawCount:coverage.length===sourceRows.filter(row=>row.status!=='skipped').length&&coverage.length?coverage.reduce((sum,row)=>sum+Number(row.result.rawResultCount),0):null,
    duplicateCount:duplicateKnown?removed+(Number(dedup?.removedDuplicateCount)||0):null,failedSources:sourceRows.filter(row=>row.status==='failed').length,
    unknownCalls:sessionCalls.filter(call=>call.status==='started').length,historyUnknownCalls:historyCalls.filter(call=>call.status==='started').length,now};
}

const badge = (status,label) => '<span class="exec-status '+escape(status)+'">'+escape(label||labels[status]||status)+'</span>';
const fields = values => values.map(value=>'<code>'+escape(value)+'</code>').join('');
export function executionMethodForCalls(calls, manual=false) {
  const models=list(calls).filter(c=>c.operation==='chat-completions');
  if(models.length)return {category:'llm',label:'LLM',text:'直接模型调用\n'+models.map(c=>(c.details?.actualModel||'实际模型未返回')+(c.model?'\n请求模型：'+c.model:'')+'\n'+(c.provider||'提供方未记录')+' · '+c.status).join('\n\n')+'\n\n名称与版本按调用记录显示；具体发布版本未另行记录。'};
  if(manual)return {category:'human',label:'人工',text:'真实人工操作或确认阶段；完成情况以人工操作记录为准。'};
  if(calls?.length)return {category:'fixed',label:'固定',text:'固定执行：工具 / API 或内部处理。\n'+[...new Set(calls.map(c=>(c.provider||'内部')+' / '+(c.operation||'操作未记录')))].join('\n')+'\n子调用不冒充本节点直接模型调用。'};
  return {category:'unknown',label:'未记录',text:'执行方式未记录；不能仅凭没有模型调用记录认定为固定执行。'};
}
const methodPaths={llm:'M4 3h8v10H4ZM6 0v3M10 0v3M6 13v3M10 13v3M0 6h4M0 10h4M12 6h4M12 10h4M6 6h4v4H6Z',fixed:'M6 1h4v2l1.5.9 1.8-1 2 3.4-1.8 1V9l1.8 1-2 3.4-1.8-1L10 13v2H6v-2l-1.5-.9-1.8 1-2-3.4 1.8-1V7l-1.8-1 2-3.4 1.8 1L6 3V1ZM10.5 8a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z',human:'M10.5 4a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM3 14v-2a5 5 0 0 1 10 0v2',unknown:'M6 5a2 2 0 1 1 3 1.7C8 7.3 8 8 8 9M8 11v1'};
let methodTipId=0;
function methodMarkup(calls,manual=false) {
  const method=executionMethodForCalls(calls,manual),id='exec-method-'+(++methodTipId);
  return '<span class="exec-method"><button type="button" data-exec-method="'+method.category+'" aria-expanded="false" aria-controls="'+id+'" aria-label="'+escape(method.label+'：'+method.text)+'"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="'+methodPaths[method.category]+'"/></svg>'+method.label+'</button><span hidden id="'+id+'" role="tooltip" class="exec-method-popup">'+escape(method.text)+'</span></span>';
}
function callMarkup(call,model) {
  const phase=model.phases.find(phase=>phase.calls.includes(call)),live=phase?.status==='running'&&model.sessionCalls.includes(call);
  const observedEnd=observedCallEnd(call);
  const elapsed=Number.isFinite(call.start)&&Number.isFinite(observedEnd)?observedEnd-call.start:call.status==='started'&&live&&Number.isFinite(call.start)?model.now-call.start:null;
  const status=call.status==='succeeded'?'done':call.status==='failed'?'failed':live?'running':'unknown';
  const data=call.details||{},stats=[['sourceId','来源'],['objectId','对象'],['sourceCount','输入材料'],['rawResultCount','原始结果'],['retainedItemCount','保留材料'],['findingCount','判断'],['nodeCount','节点'],['edgeCount','关系'],['proposedAttributeCount','提议属性'],['retainedAttributeCount','保留属性'],['rejectedAttributeCount','拒绝属性'],['inputTokens','输入 tokens'],['outputTokens','输出 tokens']].filter(([key])=>data[key]!=null).map(([key,label])=>label+' '+data[key]);
  return '<article class="exec-call"><div><b>'+escape(operations[call.operation]||call.operation||call.stage)+'</b>'+methodMarkup([call])+badge(status)+'<time>'+escape(formatDuration(elapsed))+(call.status==='started'&&live?' · 已运行':'')+'</time></div><p>'+escape([call.provider,data.actualModel||call.model,clocks(call.start)+' → '+clocks(call.end)].filter(Boolean).join(' · '))+'</p>'+(stats.length?'<p>'+escape(stats.join(' · '))+'</p>':'')+(call.error?'<p class="exec-error">'+escape(call.error.code||'调用失败')+(call.error.httpStatus?' · HTTP '+escape(call.error.httpStatus):'')+'</p>':'')+'<small>call '+escape(call.id)+' · run '+escape(call.runId||'—')+'</small></article>';
}
function branchMarkup(model) {
  const starts=model.sourceRows.map(row=>row.timing.start).filter(Number.isFinite),ends=model.sourceRows.map(row=>row.timing.end).filter(Number.isFinite),start=starts.length?Math.min(...starts):0,span=ends.length?Math.max(1,Math.max(...ends)-start):1;
  return '<section class="exec-branches"><div class="exec-subheading"><b>来源分支与实际调度</b><span>'+(model.overlap.length?'记录中有 '+model.overlap.length+' 对工具调用重叠，需核对调度':'当前实现：逐组串行')+'</span></div><p class="exec-note">各来源检索可独立并行；当前按预算顺序逐组执行。时间条与起止时间均来自真实调用记录，未开始的分支显示为“—”。</p>'+model.sourceRows.map((row,index)=>{
    const time=row.timing,offset=Number.isFinite(time.start)?Math.max(0,(time.start-start)/span*100):0,width=Number.isFinite(time.duration)?Math.min(100-offset,Math.max(.5,time.duration/span*100)):0;
    return '<article class="exec-branch"><div><span class="exec-branch-number">'+String(index+1).padStart(2,'0')+'</span><b>'+escape(row.name)+'</b>'+methodMarkup(row.calls)+badge(row.status,row.gap?'已完成 · 目标未满足':undefined)+'</div><div class="exec-branch-detail"><span>'+(row.result?'最终保留 ':'初筛保留 ')+escape(row.result?.itemCount??row.observed.retainedItemCount??'—')+' / 配额 '+escape(row.result?.requestedLimit??row.observed.requestedLimit??'—')+'</span><time>'+escape(formatDuration(time.duration))+(time.live?' · 已运行':'')+'</time></div><div class="exec-time-track" aria-hidden="true"><i class="'+escape(row.status)+'" style="left:'+offset.toFixed(2)+'%;width:'+width.toFixed(2)+'%"></i></div><small>'+escape(clocks(time.start)+' → '+clocks(time.end))+'</small>'+(row.result?.error?'<p class="exec-error">'+escape(row.result.error)+'</p>':'')+'</article>';
  }).join('')+(!model.sourceRows.length?'<p class="exec-note">创建任务后显示来源分支。</p>':'')+'<p class="exec-note">'+escape(model.budget.timeoutSecondsPerRequest!=null?'每次请求超时 '+model.budget.timeoutSecondsPerRequest+' 秒；请求预算 '+(model.budget.plannedRequests??'—')+' 次。':'请求预算尚未设置。')+' 耗时来自调用记录，超时预算不是实际耗时。</p></section>';
}
function fusionMarkup(model) {
  return '<div class="exec-fusion"><span>检索返回 <b>'+escape(model.rawCount??'—')+'</b></span><span aria-hidden="true">→</span><span>过滤与去重 <b>'+(model.duplicateCount==null?'重复数待确认':model.duplicateCount+' 项重复')+'</b></span><span aria-hidden="true">→</span><span>材料落库 <b>'+model.items.length+' 条</b></span><span aria-hidden="true">→</span><span>可分析正文 <b>'+model.textItems.length+' 条</b></span></div><p class="exec-note">以上是不同处理口径，重复数不包含其他过滤项。'+(model.failedSources?model.failedSources+' 个来源失败，可用材料已保留；缺失来源不会用模型生成内容补齐。':'同一内容命中多个来源组仅保留一份材料；转载与多次命中不算独立证据。')+'</p>';
}
function phaseMarkup(phase,index,model,options={}) {
  const timelineStatus=['done','running','failed','unknown'].includes(phase.status)?phase.status:'pending';
  const humanDone=phase.manual&&phase.status==='done';
  const nodeClass=humanDone?'manual-done':timelineStatus;
  const displayTime=humanDone&&Number.isFinite(phase.manualEnd)?phase.manualEnd:phase.timing.start;
  const timeTitle=humanDone&&Number.isFinite(phase.manualEnd)?'人工操作执行结束时间':'步骤开始时间';
  let controls='';
  if(phase.id==='snapshot'&&phase.status==='manual') controls='<button type="button" class="filter-chip" data-exec-control="select-materials">查看并选择材料 →</button><button type="button" class="btn btn-primary" data-exec-control="select-all-materials">全选材料</button>';
  if(phase.id==='analysis'&&phase.status==='manual'&&!model.assessment&&model.textItems.length) controls='<button type="button" class="btn btn-primary" data-exec-control="generate-assessment">分析所选材料 →</button>';
  if(phase.id==='analysis'&&['partial','failed'].includes(phase.status)&&model.assessment) controls='<button type="button" class="btn btn-primary" data-exec-control="quantify-capability">补充公开指标量化 →</button>';
  if(phase.id==='extract'&&phase.status==='manual'&&model.assessment) controls='<button type="button" class="filter-chip" data-exec-control="extract">启动对象与属性提取 →</button>';
  if(phase.action&&!(phase.id==='snapshot'||phase.id==='analysis'||phase.id==='extract')) controls='<button type="button" class="filter-chip" data-exec-action="'+phase.action+'">'+escape(phase.actionLabel)+' →</button>';
  const record=humanDone&&Number.isFinite(phase.manualEnd)?'<p class="exec-manual-record"><span>人工操作记录</span><b>已完成</b><time>执行结束 '+escape(clocks(phase.manualEnd))+'</time></p>':'';
  const phaseTime=Number.isFinite(displayTime)?clocks(displayTime):phase.status==='running'?'刚刚启动':phase.status==='waiting'?'尚未开始':phase.status==='manual'?'等待操作':phase.status==='unknown'?'上次未完成':'等待前置';
  const phaseDuration=Number.isFinite(phase.timing.duration)?(phase.timing.interrupted?'上次运行 '+formatDuration(phase.timing.duration):formatDuration(phase.timing.duration)):phase.status==='running'?'进行中':phase.status==='waiting'?'尚未开始':phase.status==='manual'?'等待操作':phase.status==='unknown'?'需恢复':'等待前置';
  const progress=phase.progress||phase.result;
  const phaseStatusLabel=phase.id==='collect'&&phase.status==='done'&&model.budget.targetReached===false?'已完成 · 目标未满足':phase.id==='analysis'&&phase.status==='partial'?(phase.result.includes('量化尚未执行')?'已生成 · 待执行量化':'已生成 · 量化待补'):phase.status==='blocked'&&phase.id==='snapshot'?(model.textItems.length?'等待自动选择正文':'等待正文材料'):undefined;
  const stateNote=phase.id==='analysis'&&phase.status==='failed'?'公开指标量化有明确失败记录；已生成的趋势判断保留，点击“补充公开指标量化”重试，成功后生成独立待审核版本。':phase.id==='analysis'&&phase.status==='partial'&&phase.result.includes('量化尚未执行')?'趋势判断已经生成，但公开指标量化尚未产生调用记录；这是自动编排尚未推进到量化步骤，请点击“补充公开指标量化”继续。':phase.id==='analysis'&&phase.status==='partial'?'公开指标量化已经执行，但当前没有足够的可核验指标。请补充参数表、测试报告或项目文件，再运行公开指标量化。':phase.status==='unknown'?'检测到开始记录但没有结束记录；当前页面没有正在执行的请求，请刷新记录或重新触发该步骤。':phase.status==='waiting'?'前置条件已满足，系统将在自动流程到达此步骤后开始执行。':phase.status==='running'&&!phase.timing.live?'已提交执行请求，正在等待服务返回。':'';
  const progressNote=(progress&&progress!==phase.result?'<p class="exec-progress-note"><b>当前进度</b>'+escape(progress)+'</p>':'')+(stateNote?'<p class="exec-state-note">'+escape(stateNote)+'</p>':'');
  return '<details class="exec-phase '+(options.tree?'exec-tree-phase ':'')+(phase.manual?'human':'automatic')+'" data-exec-detail="'+phase.id+'"'+(phase.status==='running'?' open':'')+'><summary><time class="exec-phase-time" title="'+timeTitle+'">'+escape(phaseTime)+'</time><span class="exec-timeline-node '+nodeClass+'" aria-label="'+escape(labels[phase.status]||phase.status)+'"><span>'+String(index+1).padStart(2,'0')+'</span></span><div class="exec-step-copy"><b title="'+escape(phase.title)+'">'+escape(phase.title)+'</b><small title="'+escape(phase.mode)+'">'+escape(phase.mode)+'</small><span title="'+escape(progress)+'">'+escape(progress)+'</span></div><div class="exec-step-meta">'+methodMarkup(phase.calls,phase.manual)+badge(phase.status,phaseStatusLabel)+'<time>'+escape(phaseDuration)+(phase.timing.live?' · 已运行':'')+'</time></div><span class="exec-chevron" aria-hidden="true">⌄</span></summary><div class="exec-phase-body"><p class="exec-note"><b>'+escape(phase.title)+'</b> · '+escape(phase.result)+'</p>'+progressNote+record+(phase.timingNote?'<p class="exec-note">'+escape(phase.timingNote)+'</p>':'')+'<div class="exec-dependency"><span>前置：'+escape(phase.dependency)+'</span><b>'+escape(phase.mode)+'</b></div>'+(phase.id==='collect'?branchMarkup(model):phase.id==='fusion'?fusionMarkup(model):'')+'<div class="exec-contract"><div><h5>输入契约</h5>'+fields(phase.input)+'</div><span class="exec-contract-arrow" aria-hidden="true">→</span><div><h5>输出契约</h5>'+fields(phase.output)+'</div></div><p class="exec-rule"><b>流转规则</b>'+escape(phase.rules)+'</p><p class="exec-rule"><b>异常与控制</b>'+escape(phase.failure)+'</p>'+controls+'<details class="exec-tool-log" data-exec-detail="'+phase.id+'-calls"><summary>真实调用记录 · '+phase.calls.length+' 次'+(phase.runs>1?' / '+phase.runs+' 次运行':'')+'</summary><p class="exec-note">步骤耗时取最近一次运行的记录跨度，不累加父子调用。多次运行的明细保留在此；未产生独立调用记录时显示“—”。</p>'+[...phase.calls].reverse().map(call=>callMarkup(call,model)).join('')+(!phase.calls.length?'<p class="exec-note">暂无独立调用记录；当前状态依据任务及保存结果。</p>':'')+'</details></div></details>';
}

function treeStatus(phases) {
  const statuses = phases.map(phase => phase.status);
  for (const status of ['failed','unknown','running','partial','manual','waiting','blocked']) {
    if (statuses.includes(status)) return status;
  }
  return statuses.length && statuses.every(status => status === 'done') ? 'done' : 'waiting';
}

function treeNode(status, root = false) {
  const nodeStatus = ['done','running','failed','unknown'].includes(status) ? status : 'pending';
  return '<span class="exec-tree-node '+(root ? 'exec-tree-node-root ' : '')+nodeStatus+'" aria-label="'+escape(labels[status] || status)+'"></span>';
}

function executionTreeMarkup(model) {
  const groups = [
    {title:'材料采编', caption:'任务校验 · 来源检索 · 过滤去重 · 证据快照', phaseIds:['plan','collect','fusion','snapshot']},
    {title:'趋势研判与对象提取', caption:'模型研判 · 对象属性 · 证据图谱', phaseIds:['analysis','extract']},
    {title:'证据核验与本体准入', caption:'完整性核验 · 人工审核 · 发布入库', phaseIds:['verify','admit']}
  ];
  const rootStatus = treeStatus(model.phases);
  const done = model.phases.filter(phase => phase.status === 'done').length;
  const automaticPhases = model.phases.filter(phase => !phase.manual);
  const automaticDone = automaticPhases.filter(phase => phase.status === 'done').length;
  const humanPending = model.phases.filter(phase => phase.manual || phase.status === 'manual').length;
  const rootTitle = model.task?.title || '装备情报采编任务';
  const groupMarkup = groups.map(group => {
    const phases = group.phaseIds.map(id => model.phases.find(phase => phase.id === id)).filter(Boolean);
    const status = treeStatus(phases);
    const complete = phases.filter(phase => phase.status === 'done').length;
    return '<section class="exec-tree-group">'+
      '<div class="exec-tree-group-head">'+treeNode(status)+
      '<div class="exec-tree-group-copy"><b>'+escape(group.title)+'</b><small>'+escape(group.caption)+'</small></div>'+
      '<span class="exec-tree-group-progress">'+complete+' / '+phases.length+' 步'+(phases.some(phase=>phase.manual||phase.status==='manual')?' · 待人工确认':'')+'</span></div>'+
      '<div class="exec-tree-children">'+
      phases.map(phase => phaseMarkup(phase, model.phases.indexOf(phase), model, {tree:true})).join('')+
      '</div></section>';
  }).join('');
  return '<div class="exec-task-tree">'+
    '<div class="exec-tree-label">Tasks</div>'+
    '<div class="exec-tree-root">'+treeNode(rootStatus,true)+
    '<div class="exec-tree-root-copy"><b>'+escape(rootTitle)+'</b><small>采编执行 · 自动链路 '+automaticDone+' / '+automaticPhases.length+' 步'+(humanPending?' · '+humanPending+' 个治理门待人工确认':' · '+done+' / '+model.phases.length+' 步已记录')+'</small></div>'+
    '<div class="exec-tree-root-meta">'+badge(rootStatus,rootStatus==='manual'?'自动流程完成 · 待人工确认':rootStatus==='blocked'?(model.textItems.length?'自动排队中':'等待可分析正文'):rootStatus==='partial'?'自动流程有缺口':undefined)+'</div></div>'+
    '<div class="exec-tree-groups">'+groupMarkup+'</div></div>';
}

function deepseekStatsMarkup(stats) {
  if (!stats) return '';
  const labels = {trend:'趋势研判',quantification:'公开指标量化',objectRecognition:'对象识别',objectAttributes:'逐对象属性',verification:'证据复核',findingLinks:'趋势-属性关联',other:'其他'};
  const rows = stats.stages.filter(row => row.total || row.name !== 'other').map(row => {
    const planned = stats.plannedStages.find(item => item.name === row.name);
    const actual = row.total + ' 次';
    const estimate = planned ? planned.planned + ' 次' : '—';
    const requestNote = '逻辑请求 '+row.requests;
    const basis = planned?.basis ? ' · 依据 '+planned.basis : '';
    return '<span><b>'+escape(labels[row.name]||row.name)+'</b> '+escape(actual)+' / 计划 '+escape(estimate)+'<small>（'+requestNote+'，成功 '+row.succeeded+'，失败 '+row.failed+(row.open?'，未闭合 '+row.open:'')+escape(basis)+'）</small></span>';
  }).join('');
  return '<details class="exec-orchestration exec-deepseek-stats" data-exec-detail="deepseek-stats"><summary>本轮 DeepSeek 调用核算 · 实际 '+stats.actual+' 次 / 全流程预计 '+stats.planned+' 次</summary><div class="exec-deepseek-breakdown">'+rows+'</div><p class="exec-note">全流程预计值按当前完整工作量计算：每批最多 8 条正文计算趋势研判、公开指标量化、对象识别和启用的反证复核（ceil(正文数 / 8)）；逐对象属性按每个对象 1 次，趋势-属性语义关联在有对象与趋势判断时计 1 次。证据完整性检查本身是确定性核验，不计入 DeepSeek。实际值只统计本轮执行记录中 provider=deepseek、operation=chat-completions 的真实请求；“成功/失败/逻辑请求”按请求记录计数，重试次数计入实际尝试数，未闭合请求继续保留。历史尝试计入下方任务累计；预计值不是已完成次数或剩余调用额度。</p></details>';
}

function historyStatsMarkup(model) {
  const allSucceeded=model.calls.filter(call=>call.status==='succeeded').length;
  const allFailed=model.calls.filter(call=>call.status==='failed').length;
  const totalDeepseek=model.deepseekStats.actual+model.historyDeepseekStats.actual;
  return '<details class="exec-orchestration" data-exec-detail="history-calls"><summary>本任务累计 · 工具 '+model.calls.length+' 次 · DeepSeek '+totalDeepseek+' 次（含历史重跑）</summary><p class="exec-note">任务全部记录：成功 '+allSucceeded+' 次、失败 '+allFailed+' 次。本轮以外保留 '+model.historyCalls.length+' 条工具记录、'+model.historyDeepseekStats.actual+' 次 DeepSeek 尝试'+(model.historyUnknownCalls?'、'+model.historyUnknownCalls+' 条未闭合记录':'')+'；历史记录用于审计，不累加到本轮耗时、工具调用或成功/失败数。</p>'+[...model.historyCalls].reverse().map(call=>callMarkup(call,model)).join('')+(!model.historyCalls.length?'<p class="exec-note">没有本轮以外的调用记录。</p>':'')+'</details>';
}

const viewStates=new Map();
export function renderExecutionWorkspace(host,context,actions={}) {
  const key=(context.task?.id||'empty')+':'+(context.assessment?.id||context.assessment?.assessmentId||'none');
  const oldKey=host.dataset.executionKey,oldState=oldKey?viewStates.get(oldKey)||{}:{};
  const scrollBody=host.closest('.execution-workspace-body'),scrollTop=scrollBody?.scrollTop;
  host.querySelectorAll('[data-exec-detail]').forEach(detail=>oldState[detail.dataset.execDetail]=detail.open);
  if(oldKey)viewStates.set(oldKey,oldState);
  host.dataset.executionKey=key;
  const model=buildExecutionModel(context),saved=viewStates.get(key)||{};
  const unfinished=['running','failed','unknown','partial','manual','waiting'].map(status=>model.phases.find(phase=>phase.status===status)).find(Boolean);
  const counts={done:model.sessionCalls.filter(call=>call.status==='succeeded').length,failed:model.sessionCalls.filter(call=>call.status==='failed').length};
  const sessionStartLabel=({analysis:'趋势研判',extract:'对象与属性提取',verify:'证据核验'})[model.sessionStartPhase]||'后续分析';
  const sessionNote='<p class="exec-note">上方耗时、工具调用、成功/失败和 DeepSeek 实际值均属于本轮连续执行。'+(model.reusesMaterials?'本轮从'+escape(sessionStartLabel)+'开始，复用既有 '+model.textItems.length+' 条正文材料；材料采集记录与先前失败尝试保留在任务累计中。':'全流程预计值按当前材料与对象规模估算，历史重跑记录可在任务累计中查看。')+'</p>';
  const roundPolicy=model.budget?.roundPolicy||{};
  const roundFormula=roundPolicy.formula||'服务端未返回轮次公式';
  const roundPlan=roundPolicy.plannedRounds??model.budget?.maxCollectionRounds??'—';
  const roundActual=model.budget?.searchRounds??'—';
  const message=(label,body,tone='info')=>'<details class="workbench-message workbench-message-'+tone+'"><summary class="workbench-message-summary"><span class="workbench-message-symbol" aria-hidden="true">'+(tone==='info'?'i':'!')+'</span><span>'+escape(label)+'</span></summary><div class="workbench-message-body">'+escape(body)+'</div></details>';
  host.innerHTML='<div class="exec-heading"><div class="exec-heading-copy"><h3>采编执行总览</h3><span class="exec-context">'+escape(model.assessmentId?'分析版本 '+model.assessmentId.slice(-8):'尚无分析版本')+'</span></div><div class="exec-heading-actions">'+(context.trace?.unavailable?message('执行记录读取失败','暂显示已有记录与保存结果。可点击刷新记录重试。','error'):'')+(model.unknownCalls?message(model.unknownCalls+' 次调用状态未知','这些调用只有开始记录；历史未闭合调用保留为未知，不能判为成功。','warning'):'')+message(unfinished?'当前关注：'+unfinished.title:model.task?'记录已整理':'等待创建任务',unfinished?.result||'在左侧设置采编范围后启动任务',unfinished&&['failed','unknown','partial'].includes(unfinished.status)?'warning':'info')+'<button type="button" class="filter-chip" data-exec-refresh'+(!model.task||context.traceBusy?' disabled':'')+'>'+(context.traceBusy?'读取中…':'刷新记录')+'</button></div></div><div class="exec-metrics"><div title="本轮连续执行的墙钟耗时；超过15分钟的闲置另计一轮，恢复器补写的结束时间不计入"><span>本轮整编实际耗时（墙钟）</span><b>'+escape(formatDuration(model.collectionClock.duration))+'<small> · 调用累计 '+escape(formatDuration(model.collectionClock.activeDuration))+'</small></b></div><div><span>本轮工具调用</span><b>'+model.sessionCalls.length+'<small> 次</small></b></div><div><span>本轮 DeepSeek（实际 / 全流程预计）</span><b>'+model.deepseekStats.actual+' <small>/ '+model.deepseekStats.planned+' 次</small></b></div><div><span>本轮成功 / 失败</span><b>'+counts.done+' <small>/</small> '+counts.failed+'</b></div><div><span>正文 / 全部材料</span><b>'+model.textItems.length+' <small>/</small> '+model.items.length+'</b></div></div>'+sessionNote+executionTreeMarkup(model)+deepseekStatsMarkup(model.deepseekStats)+historyStatsMarkup(model)+'<details class="exec-orchestration" data-exec-detail="orchestration"><summary>串并行关系与汇合约束</summary><p><b>当前串行：</b>来源组逐组采集 → 过滤去重并落库 → 自动或人工选择材料固化快照 → 趋势研判再量化 → 服务端自动启动对象识别 → 逐对象属性 → 图谱保存。</p><p><b>轮次与调用预算：</b>自动采编轮次由服务端按目标与来源收敛策略计算：'+escape(roundFormula)+'；本次计划上限 '+escape(roundPlan)+' 轮，实际执行 '+escape(roundActual)+' 轮。每轮按来源组分配剩余目标，达到目标或预算耗尽即停止。DeepSeek 调用核算见上方“DeepSeek 调用核算”，实际值来自执行记录；计划值按每批最多 8 条正文、每个对象 1 次属性请求和已启用核验步骤计算，失败与重试单独计入实际值。</p><p><b>当前可并发：</b>不同报告的后台提取全局最多 2 个任务，同一报告最多 1 个。当前页的来源组与逐对象属性尚未并行；实际重叠需以同次运行的工具记录为准。</p><p><b>独立触发：</b>属性补提与对象重新提取用于恢复或重试；证据完整性检查与模型反证复核可在研判后分别启动。对象关联的核验需已有对象图谱；核验与发布之间保留人工审核。</p><p><b>自动与人工边界：</b>自动开关只推进采编、研判、对象属性提取、机器核验和准入候选生成；只有候选发布前的事实确认由人工完成，避免模型未经核验写入本体。</p><p><b>融合约束：</b>所有分支沿用当前 taskId；分析之后引用同一 assessmentId 和不可变 sourceSnapshot。重复材料合并溯源；对象按图谱 ID 关联，断言保留原文依据与条件，冲突与缺失交由核验及人工审核。</p><p class="exec-note">OpenViking 尚未接入本采编链，不列作已执行步骤；轮次与调用统计均来自后端预算和真实执行记录。</p></details>'+(model.unbound.length?'<details class="exec-orchestration" data-exec-detail="unbound"><summary>未绑定分析版本的调用 · '+model.unbound.length+' 次</summary><p class="exec-note">这些记录尚未绑定分析版本；具有本任务标识的调用仍按执行会话计入本轮或任务累计，完整记录保留在此。</p>'+model.unbound.map(call=>callMarkup(call,model)).join('')+'</details>':'');
  host.querySelectorAll('[data-exec-detail]').forEach(detail=>{if(Object.hasOwn(saved,detail.dataset.execDetail))detail.open=saved[detail.dataset.execDetail]});
  if(scrollBody&&oldKey===key)scrollBody.scrollTop=scrollTop;
  host.querySelector('[data-exec-refresh]').addEventListener('click',()=>actions.refresh?.());
  host.querySelectorAll('[data-exec-action]').forEach(button=>button.addEventListener('click',()=>actions.navigate?.(button.dataset.execAction)));
  host.querySelectorAll('[data-exec-control]').forEach(button=>button.addEventListener('click',()=>actions.manualControl?.(button.dataset.execControl)));
  host.querySelectorAll('[data-exec-method]').forEach(button=>{
    const popup=button.nextElementSibling;
    const show=()=>{popup.hidden=false;button.setAttribute('aria-expanded','true')};
    const hide=()=>{popup.hidden=true;button.setAttribute('aria-expanded','false')};
    button.addEventListener('pointerenter',show);button.addEventListener('pointerleave',hide);
    button.addEventListener('focus',show);button.addEventListener('blur',hide);
    button.addEventListener('click',event=>{event.stopPropagation();show()});
    button.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();hide()}});
  });
  const count=document.getElementById('execution-material-total');
  if(count)count.textContent=model.items.length+' 条材料 · '+model.selected.length+' 条正文已选择';
  return model;
}

