import assert from 'node:assert/strict';
import { buildExecutionModel, renderExecutionWorkspace, executionMethodForCalls } from './execution-workspace.js';

assert.equal(executionMethodForCalls([]).category, 'unknown');
assert.equal(executionMethodForCalls([], true).category, 'human');
assert.equal(executionMethodForCalls([{provider:'deepseek',operation:'generate-assessment'}]).category, 'fixed');
assert.equal(executionMethodForCalls([{provider:'deepseek',operation:'chat-completions',details:{actualModel:'release-1'}}]).category, 'llm');
assert.match(executionMethodForCalls([{operation:'chat-completions',model:'requested-only'}]).text, /实际模型未返回/);

function contextFixture(metrics = []) {
  const task = {
    id: 'task-a',
    status: 'completed',
    sourceIds: ['official', 'evaluation'],
    sourceResults: [
      { sourceId: 'official', status: 'ok', itemCount: 2 },
      { sourceId: 'evaluation', status: 'ok', itemCount: 0 },
    ],
    items: [{ id: 'item-a', provenance: {} }, { id: 'item-b', provenance: {} }],
    automation: { autoStartAssessment: true },
    brief: { collectionBudget: { targetReached: false, maxItems: 10 } },
  };
  const assessment = {
    id: 'assessment-a',
    analysisType: 'model-assessment',
    summary: { findings: [{ title: '趋势' }], capabilityAssessment: { metrics } },
  };
  return {
    task, assessment, trace: { executionTrace: [] }, sources: [], selectedIds: ['item-a', 'item-b'],
    records: { nodes: [], edges: [] }, rounds: [], admissions: [],
  };
}
function fixture(metrics = []) { return buildExecutionModel(contextFixture(metrics)); }

const model = fixture();
const collect = model.phases.find(phase => phase.id === 'collect');
const analysis = model.phases.find(phase => phase.id === 'analysis');
assert.equal(collect.status, 'done');
assert.match(collect.result, /目标未满足/);
assert.equal(analysis.status, 'partial');
assert.match(analysis.result, /0 项公开指标/);
assert.match(analysis.progress, /待补证/);

const retryTrace = [
  { id: 'assessment-call', stage: 'trend-analysis', operation: 'chat-completions', status: 'started',
    provider: 'deepseek', assessmentId: 'assessment-a', timestamp: '2026-10-11T00:00:00Z' },
  { id: 'assessment-call', stage: 'trend-analysis', operation: 'chat-completions', status: 'failed',
    provider: 'deepseek', assessmentId: 'assessment-a', timestamp: '2026-10-11T00:00:01Z' },
];
const persistedAfterRetry = buildExecutionModel({
  ...contextFixture(), trace: { executionTrace: retryTrace },
});
assert.equal(persistedAfterRetry.phases.find(phase => phase.id === 'analysis').status, 'partial');

const quantificationFailure = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [{ id: 'quant-a', stage: 'trend-analysis', operation: 'quantify-public-observations',
    status: 'failed', provider: 'deepseek', assessmentId: 'assessment-a',
    timestamp: '2026-10-11T00:00:02Z' }] },
});
assert.equal(quantificationFailure.phases.find(phase => phase.id === 'analysis').status, 'failed');

assert.equal(fixture([{ id: 'metric-a', value: 12 }]).phases.find(phase => phase.id === 'analysis').status, 'done');

const partialSourceTask = {
  ...contextFixture().task,
  status: 'partial',
  sourceResults: [
    { sourceId: 'official', status: 'partial', itemCount: 2 },
    { sourceId: 'evaluation', status: 'partial', itemCount: 0 },
  ],
};
const completedAfterRetry = buildExecutionModel({
  ...contextFixture(),
  task: partialSourceTask,
  trace: { executionTrace: [
    { id: 'old-source', runId: 'old-run', stage: 'collection', operation: 'search-source', status: 'failed', provider: 'firecrawl', details: { sourceId: 'official' }, timestamp: '2026-10-11T00:00:00Z' },
    { id: 'new-source', runId: 'new-run', stage: 'collection', operation: 'search-source', status: 'succeeded', provider: 'firecrawl', details: { sourceId: 'official' }, timestamp: '2026-10-11T00:00:02Z' },
  ] },
});
assert.equal(completedAfterRetry.phases.find(phase => phase.id === 'collect').status, 'done');
assert.deepEqual(completedAfterRetry.sourceRows.map(row => row.status), ['done', 'done']);

const quantificationRetry = buildExecutionModel({
  ...contextFixture([{ id: 'metric-a', value: 12 }]),
  trace: { executionTrace: [
    { id: 'old-quant', runId: 'old-quant-run', stage: 'trend-analysis', operation: 'quantify-public-observations', status: 'failed', assessmentId: 'assessment-a', timestamp: '2026-10-11T00:00:00Z' },
    { id: 'new-quant', runId: 'new-quant-run', stage: 'trend-analysis', operation: 'quantify-public-observations', status: 'succeeded', assessmentId: 'assessment-a', timestamp: '2026-10-11T00:00:02Z' },
  ] },
});
assert.equal(quantificationRetry.phases.find(phase => phase.id === 'analysis').status, 'done');

// Provider transport events are counted once and separated by execution
// stage. Coordinator wrappers must not inflate the DeepSeek total.
const callStats = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id: 'trend-1', runId: 'trend-run', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:00Z' },
    { id: 'quant-1', runId: 'quant-run', stage: 'capability-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'failed', details: { retryCount: 2 }, timestamp: '2026-10-11T00:00:01Z' },
    { id: 'attr-1', runId: 'attr-run', stage: 'object-attributes', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:00:02Z' },
    { id: 'wrapper', runId: 'trend-run', stage: 'trend-analysis', operation: 'generate-assessment', provider: 'assessment-engine', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:03Z' },
  ] },
});
assert.equal(callStats.deepseekStats.actual, 5);
assert.equal(callStats.deepseekStats.stages.find(row => row.name === 'trend').total, 1);
assert.equal(callStats.deepseekStats.stages.find(row => row.name === 'quantification').failed, 1);
assert.equal(callStats.deepseekStats.stages.find(row => row.name === 'quantification').total, 3);
assert.equal(callStats.deepseekStats.stages.find(row => row.name === 'objectAttributes').open, 1);

// The deterministic evidence check is not a DeepSeek request; model critique
// is source-batched and the trend-to-attribute linker is a separate request.
const fullProcessStats = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, automation: { autoRunModelCritique: true } },
  records: { nodes: [{ id: 'equipment:1', type: 'EquipmentModel', payload: { extractionMethod: 'llm' } }], edges: [] },
  trace: { executionTrace: [
    { id: 'critique-1', runId: 'verify-run', stage: 'evidence-verification', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:00Z' },
    { id: 'link-1', runId: 'extract-run', stage: 'finding-attribute-links', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:01Z' },
  ] },
}).deepseekStats;
assert.equal(fullProcessStats.plannedStages.find(row => row.name === 'verification').planned, 1);
assert.equal(fullProcessStats.stages.find(row => row.name === 'findingLinks').total, 1);
assert.equal(fullProcessStats.plannedStages.find(row => row.name === 'findingLinks').planned, 1);

// A continuation job may report only its current eight-object slice. The
// overview still plans against all objects already present in the graph.
const continuationObjects = Array.from({ length: 9 }, (_, index) => ({
  id: `equipment:${index}`,
  type: 'EquipmentModel',
  payload: { extractionMethod: 'llm' },
}));
const continuationStats = buildExecutionModel({
  ...contextFixture(),
  jobs: [{ status: 'running', progress: { totalObjects: 1, processedObjects: 0 } }],
  records: { nodes: continuationObjects, edges: [] },
}).deepseekStats;
assert.equal(continuationStats.plannedStages.find(row => row.name === 'objectAttributes').planned, 9);

const timingModel = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id: 'parent', runId: 'run-time', stage: 'trend-analysis', operation: 'generate-assessment', provider: 'assessment-engine', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:00:00.000Z' },
    { id: 'leaf', runId: 'run-time', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:00:02.000Z' },
    { id: 'leaf', runId: 'run-time', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:05.000Z' },
    { id: 'parent', runId: 'run-time', stage: 'trend-analysis', operation: 'generate-assessment', provider: 'assessment-engine', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:10.000Z' },
  ] },
});
assert.equal(timingModel.collectionClock.duration, 10000);
assert.equal(timingModel.collectionClock.activeDuration, 3000);

// Saving automatic validation/candidates is part of the workflow even after
// transport finishes. Later reviews, edits and out-of-session creations are
// excluded, and do not change the provider's cumulative work time.
const savedOutputsTiming = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, automation: { autoRunEvidenceCheck: true, autoCreateAdmissionCandidates: true } },
  trace: { executionTrace: [
    { id: 'saved-leaf', runId: 'saved-run', stage: 'evidence-verification', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-11-10T06:00:00Z' },
    { id: 'saved-leaf', runId: 'saved-run', stage: 'evidence-verification', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-11-10T06:01:00Z' },
  ] },
  rounds: [{ createdAt: '2026-11-10T06:02:00Z', updatedAt: '2026-11-11T06:00:00Z' }],
  admissions: [
    { createdAt: '2026-11-10T06:05:00Z', updatedAt: '2026-11-12T06:00:00Z', review: { reviewedAt: '2026-11-12T06:00:00Z' } },
    { createdAt: '2026-11-11T06:00:00Z' },
  ],
});
assert.equal(savedOutputsTiming.collectionClock.duration, 5*60000);
assert.equal(savedOutputsTiming.collectionClock.activeDuration, 60000);

const attributeParentTiming = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id: 'attribute-parent', runId: 'attr-time', stage: 'object-extraction', operation: 'extract-object-attributes', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:00:00.000Z' },
    { id: 'attribute-transport', runId: 'attr-time', stage: 'object-attributes', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:00:01.000Z' },
    { id: 'attribute-transport', runId: 'attr-time', stage: 'object-attributes', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:04.000Z' },
    { id: 'attribute-parent', runId: 'attr-time', stage: 'object-extraction', operation: 'extract-object-attributes', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', timestamp: '2026-10-11T00:00:09.000Z' },
  ] },
});
assert.equal(attributeParentTiming.collectionClock.activeDuration, 3000);

// Historical open calls stay unknown. The current page clock must not turn a
// stale `started` record into an hour-long run; an explicit task completion
// boundary is the only valid way to close the workflow clock.
const historicalOpen = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, createdAt: '2026-10-11T00:00:00.000Z', startedAt: '2026-10-11T00:00:00.000Z', completedAt: '2026-10-11T00:05:00.000Z' },
  assessment: { ...contextFixture().assessment, createdAt: '2026-10-11T00:00:00.000Z', updatedAt: '2026-10-11T01:00:00.000Z' },
  trace: { executionTrace: [
    { id: 'stale-open', runId: 'stale-run', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:01:00.000Z' },
  ] },
});
assert.equal(historicalOpen.collectionClock.duration, 300000);
assert.equal(historicalOpen.collectionClock.activeDuration, 0);

const openAfterCollection = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, createdAt: '2026-10-11T00:00:00.000Z', completedAt: '2026-10-11T00:01:00.000Z' },
  trace: { executionTrace: [
    { id: 'post-collection-open', runId: 'post-run', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:02:00.000Z' },
  ] },
});
assert.equal(openAfterCollection.collectionClock.duration, null);

const completedJobAfterOpen = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, createdAt: '2026-10-11T00:00:00.000Z', completedAt: '2026-10-11T00:01:00.000Z' },
  jobs: [{ status: 'completed', createdAt: '2026-10-11T00:02:30.000Z', completedAt: '2026-10-11T00:05:00.000Z' }],
  trace: { executionTrace: [
    { id: 'job-closed-open', runId: 'job-run', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:02:00.000Z' },
  ] },
});
// The task creation date is not an execution start. This attempt begins at
// the first recorded call (00:02), and its job completes at 00:05.
assert.equal(completedJobAfterOpen.collectionClock.duration, 180000);

const noReliableEnd = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, status: 'completed', createdAt: '2026-10-11T00:00:00.000Z' },
  trace: { executionTrace: [
    { id: 'unknown-end', runId: 'unknown-run', stage: 'trend-analysis', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'started', timestamp: '2026-10-11T00:01:00.000Z' },
  ] },
});
assert.equal(noReliableEnd.collectionClock.duration, null);
assert.equal(noReliableEnd.collectionClock.activeDuration, 0);

// Retrying an old task starts a new continuous workflow attempt. Neither
// the original creation date nor a delayed reaper event is real work time.
const restartedTask = buildExecutionModel({
  ...contextFixture(),
  task: { ...contextFixture().task, createdAt:'2026-10-16T04:42:00Z', startedAt:'2026-10-16T04:42:00Z', completedAt:'2026-10-16T04:46:00Z' },
  trace: { executionTrace: [
    { id:'old-search', runId:'old-collection', stage:'collection', operation:'search-source', provider:'firecrawl', status:'started', timestamp:'2026-10-16T04:42:00Z' },
    { id:'old-search', runId:'old-collection', stage:'collection', operation:'search-source', provider:'firecrawl', status:'succeeded', timestamp:'2026-10-16T04:46:00Z' },
    { id:'orphan-quant', runId:'old-analysis', stage:'capability-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'started', timestamp:'2026-10-16T04:55:00Z', details:{timeoutSeconds:240} },
    { id:'orphan-quant', runId:'old-analysis', stage:'capability-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'failed', timestamp:'2026-10-21T12:37:16Z', details:{recovery:'stale-call-reaper',ageSeconds:459741}, error:{code:'abandoned-call'} },
    { id:'new-parent', runId:'current-analysis', stage:'trend-analysis', operation:'generate-assessment', provider:'assessment-engine', assessmentId:'assessment-a', status:'started', timestamp:'2026-11-10T06:27:00Z' },
    { id:'new-leaf', runId:'current-analysis', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'started', timestamp:'2026-11-10T06:28:00Z' },
    { id:'new-leaf', runId:'current-analysis', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'succeeded', timestamp:'2026-11-10T06:30:00Z' },
    { id:'new-parent', runId:'current-analysis', stage:'trend-analysis', operation:'generate-assessment', provider:'assessment-engine', assessmentId:'assessment-a', status:'succeeded', timestamp:'2026-11-10T06:32:00Z' },
  ] },
});
assert.equal(restartedTask.collectionClock.start, Date.parse('2026-11-10T06:27:00Z'));
assert.equal(restartedTask.collectionClock.duration, 300000);
assert.equal(restartedTask.collectionClock.activeDuration, 120000);
assert.equal(restartedTask.collectionClock.previousCalls, 2);

const abandonedEnd = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id:'lost-call', runId:'lost-run', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'started', timestamp:'2026-10-16T04:55:00Z', details:{timeoutSeconds:240} },
    { id:'lost-call', runId:'lost-run', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'failed', timestamp:'2026-10-21T12:37:16Z', details:{recovery:'stale-call-reaper',ageSeconds:459741}, error:{code:'abandoned-call'} },
  ] },
});
assert.equal(abandonedEnd.collectionClock.duration, null);
assert.equal(abandonedEnd.collectionClock.activeDuration, 0);
assert.equal(abandonedEnd.collectionClock.recoveredCalls, 1);

// Known long-running provider work is retained, and stage transitions count
// once in the continuous wall clock even when they have separate run IDs.
const continuousWorkflow = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id:'long-call', runId:'long-run', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'started', timestamp:'2026-11-10T06:00:00Z' },
    { id:'long-call', runId:'long-run', stage:'trend-analysis', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'succeeded', timestamp:'2026-11-10T06:20:00Z' },
    { id:'next-call', runId:'next-run', stage:'object-extraction', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'started', timestamp:'2026-11-10T06:21:00Z' },
    { id:'next-call', runId:'next-run', stage:'object-extraction', operation:'chat-completions', provider:'deepseek', assessmentId:'assessment-a', status:'succeeded', timestamp:'2026-11-10T06:22:00Z' },
  ] },
});
assert.equal(continuousWorkflow.collectionClock.duration, 22*60000);
assert.equal(continuousWorkflow.collectionClock.activeDuration, 21*60000);

// A task can contain an October failure, a failed retry earlier today and a
// new live retry. Main counts must use the same latest session as the clock;
// all earlier attempts remain available through the audit totals and logs.
const multiRerunContext={
  ...contextFixture(),
  busy:true,
  task:{...contextFixture().task,items:Array.from({length:25},(_,index)=>({id:'m'+index,provenance:{}}))},
  trace:{executionTrace:[
    {id:'october-source',runId:'october-collection',taskId:'task-a',stage:'collection',operation:'search-source',provider:'firecrawl',status:'started',timestamp:'2026-10-16T04:42:00Z'},
    {id:'october-source',runId:'october-collection',taskId:'task-a',stage:'collection',operation:'search-source',provider:'firecrawl',status:'succeeded',timestamp:'2026-10-16T04:46:00Z'},
    {id:'october-model',runId:'october-analysis',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'chat-completions',provider:'deepseek',status:'started',timestamp:'2026-10-16T04:55:00Z'},
    {id:'october-model',runId:'october-analysis',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'chat-completions',provider:'deepseek',status:'failed',timestamp:'2026-10-21T12:37:16Z',details:{recovery:'stale-call-reaper'},error:{code:'abandoned-call'}},
    {id:'earlier-parent',runId:'earlier-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'generate-assessment',provider:'ontology-engine',status:'started',timestamp:'2026-11-10T06:27:00Z'},
    {id:'earlier-leaf',runId:'earlier-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'chat-completions',provider:'deepseek',status:'started',timestamp:'2026-11-10T06:28:00Z'},
    {id:'earlier-leaf',runId:'earlier-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'chat-completions',provider:'deepseek',status:'failed',timestamp:'2026-11-10T06:35:00Z',details:{retryCount:2}},
    {id:'earlier-parent',runId:'earlier-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'generate-assessment',provider:'ontology-engine',status:'failed',timestamp:'2026-11-10T06:35:01Z'},
    {id:'latest-parent',runId:'latest-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'generate-assessment',provider:'ontology-engine',status:'started',timestamp:'2026-11-10T06:56:52Z'},
    {id:'latest-leaf',runId:'latest-today',taskId:'task-a',assessmentId:'assessment-a',stage:'trend-analysis',operation:'chat-completions',provider:'deepseek',status:'started',timestamp:'2026-11-10T06:56:53Z'},
  ]},
};
const multiRerun=buildExecutionModel(multiRerunContext,Date.parse('2026-11-10T06:57:06Z'));
assert.equal(multiRerun.collectionClock.duration,14000);
assert.equal(multiRerun.sessionCalls.length,2);
assert.equal(multiRerun.historyCalls.length,4);
assert.equal(multiRerun.calls.length,6);
assert.equal(multiRerun.deepseekStats.actual,1);
assert.equal(multiRerun.historyDeepseekStats.actual,4);
assert.equal(multiRerun.modelCalls.length,1);
assert.equal(multiRerun.historyModelCalls.length,2);
assert.equal(multiRerun.reusesMaterials,true);
assert.equal(multiRerun.sessionStartPhase,'analysis');
assert.equal(multiRerun.sessionCalls.filter(call=>call.status==='failed').length,0);
assert.equal(multiRerun.historyCalls.filter(call=>call.status==='failed').length,3);
assert.equal(multiRerun.deepseekStats.planned,12);
const fakeHost={dataset:{},querySelectorAll:()=>[],closest:()=>null,querySelector:()=>({addEventListener(){}}),innerHTML:''};
globalThis.document={getElementById:()=>null};
renderExecutionWorkspace(fakeHost,multiRerunContext);
assert.match(fakeHost.innerHTML,/本轮工具调用<\/span><b>2/);
assert.match(fakeHost.innerHTML,/本轮成功 \/ 失败<\/span><b>0 <small>\/<\/small> 0/);
assert.match(fakeHost.innerHTML,/本轮 DeepSeek（实际 \/ 全流程预计）<\/span><b>1 <small>\/ 12 次/);
assert.match(fakeHost.innerHTML,/本任务累计 · 工具 6 次 · DeepSeek 5 次/);
assert.match(fakeHost.innerHTML,/本轮从趋势研判开始，复用既有 25 条正文材料/);
assert.match(fakeHost.innerHTML,/october-model/);
assert.match(fakeHost.innerHTML,/earlier-leaf/);
delete globalThis.document;

// Legacy extraction traces used object-extraction for both identity and
// per-object attribute transports. Infer the latter from its 3072-token
// bounded request contract so the overview does not inflate recognition.
const legacyExtractionStats = buildExecutionModel({
  ...contextFixture(),
  trace: { executionTrace: [
    { id: 'identity-legacy', runId: 'extract-run', stage: 'object-extraction', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', details: { outputTokenLimit: 8192 }, timestamp: '2026-10-11T00:00:00Z' },
    { id: 'attribute-legacy', runId: 'extract-run', stage: 'object-extraction', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', details: { outputTokenLimit: 3072 }, timestamp: '2026-10-11T00:00:01Z' },
    { id: 'linker-legacy', runId: 'extract-run', stage: 'object-extraction', operation: 'chat-completions', provider: 'deepseek', assessmentId: 'assessment-a', status: 'succeeded', details: { outputTokenLimit: 4096 }, timestamp: '2026-10-11T00:00:02Z' },
  ] },
}).deepseekStats;
assert.equal(legacyExtractionStats.stages.find(row => row.name === 'objectRecognition').total, 1);
assert.equal(legacyExtractionStats.stages.find(row => row.name === 'objectAttributes').total, 1);
assert.equal(legacyExtractionStats.stages.find(row => row.name === 'other').total, 1);
console.log('Execution semantics checks passed.');
