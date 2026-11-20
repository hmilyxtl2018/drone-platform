import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

// Run only the workspace's pure helpers and async handlers in isolated contexts.
// No DOM browser, HTTP request, provider call, or persisted project data is used.
const folder = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(folder, 'osint.js'), 'utf8');
const section = (start, end) => {
  const position = source.indexOf(start);
  assert.ok(position >= 0, `Missing source marker: ${start}`);
  const boundary = source.indexOf(end, position);
  assert.ok(boundary > position, `Missing end marker: ${end}`);
  return source.slice(position, boundary);
};
const tests = [];
const test = (name, run) => tests.push({ name, run });
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));
const asArray = value => Array.isArray(value) ? value : [];
const first = (object, ...keys) => keys.map(key => object?.[key]).find(value => value !== undefined);
const evaluate = (code, context) => {
  vm.createContext(context);
  vm.runInContext(section('function workbenchMessageMarkup(', 'function notify(') + code, context);
  return context;
};

test('Collection settings exposes a new-task action and resets the form flow', () => {
  const html = fs.readFileSync(path.join(folder, 'osint.html'), 'utf8');
  assert.match(html, /id="new-collection-task"/);
  assert.match(source, /function resetCollectionForm\(\)/);
  assert.match(source, /function startNewCollectionTask\(\)/);
  assert.match(source, /\$\('new-collection-task'\)\.addEventListener\('click',startNewCollectionTask\)/);
});

test('Graph view preserves pan while zooming and resets both together', () => {
  const state = { graphView: {} };
  const context = evaluate(section('function graphViewport(', 'function graphCanvasMarkup('), { state, Math, Number });
  assert.deepEqual(Array.from(['scale', 'panX', 'panY'], key => context.graphViewport('report-a')[key]), [1, 0, 0]);
  context.setGraphZoom('report-a', 0.2);
  state.graphView['report-a'].panX = -86;
  state.graphView['report-a'].panY = 42;
  assert.equal(context.graphViewBoxMarkup('report-a', 1080, 320), '-86.00 42.00 900.00 266.67');
  context.setGraphZoom('report-a', 0.15);
  assert.deepEqual(Array.from(['scale', 'panX', 'panY'], key => state.graphView['report-a'][key]), [1.35, -86, 42]);
  context.setGraphZoom('report-a', 'reset');
  assert.deepEqual(Array.from(['scale', 'panX', 'panY'], key => state.graphView['report-a'][key]), [1, 0, 0]);
});

test('Evidence association entry opens a compact relation list with node targets', () => {
  const state = { selectedGraphNodeId: 'source-1', selectedGraphEdgeId: null };
  const report = { id: 'report-a' };
  const graph = {
    nodes: [
      { id: 'source-1', type: 'EvidenceSource', label: '试验报告' },
      { id: 'equipment-1', type: 'EquipmentModel', label: '协同无人机' },
      { id: 'event-1', type: 'TestEvent', label: '编队测试' },
    ],
    edges: [
      { id: 'edge-1', from: 'source-1', to: 'equipment-1', relation: 'supports' },
      { id: 'edge-2', from: 'event-1', to: 'source-1', relation: 'cites' },
    ],
  };
  const context = evaluate(section('function graphSourceOrigin(', 'function graphNodeTypeMarkup('), {
    state, Map, Set, String, Array, asArray, first, esc, graphData: () => graph,
    assessmentId: value => value.id, relationLabel: value => ({ supports: '支撑', cites: '引用' }[value] || value),
    graphCategory: node => ({ id: node.type === 'EquipmentModel' ? 'equipment' : node.type === 'TestEvent' ? 'event' : 'evidence', label: node.type === 'EquipmentModel' ? '装备' : node.type === 'TestEvent' ? '事件' : '来源证据', symbol: node.type === 'EquipmentModel' ? '▣' : node.type === 'TestEvent' ? '●' : '▤' }),
    graphNodeLabel: node => node.label,
  });
  const rows = context.graphAssociationRows(report, 'source-1');
  assert.deepEqual(rows.map(row => [row.node.id, row.direction, row.relation]), [
    ['equipment-1', '出发', '支撑'], ['event-1', '指向', '引用'],
  ]);
  const markup = context.graphAssociationMarkup(report, 'source-1');
  assert.match(markup, /data-graph-association-item="equipment-1"/);
  assert.match(markup, /graph-association-panel/);
});

function extractionFixture() {
  const state = {
    task: { id: 'task-a' }, assessments: [{ id: 'report-a', revision: 4, status: 'under-review' }],
    selectedAssessmentId: 'report-a', augmentationBusy: {}, extractionJobs: {},
    extractionJobTimers: {}, extractionJobBusy: {}, extractionJobErrors: {},
    extractionJobErrorCounts: {}, handledExtractionJobs: {},
    graphByAssessment: { 'report-a': { id: 'old-graph' } }, validationByAssessment: {},
    admissionsByAssessment: {}, objectSelections: {}, selectedGraphNodeId: 'object-a',
    selectedGraphProperty: { nodeId: 'object-a' },
  };
  const requests = [], calls = [], scheduled = [];
  let status = 'running', fail = false;
  const context = evaluate(section('function extractionJobActive(', 'function objectVerificationControls('), {
    state, Set, Promise, asArray, first, assessmentId: value => value.id,
    renderAssessments: () => calls.push('assessments'), renderReportPanels: () => calls.push('panels'),
    renderGraphOverlay: () => calls.push('overlay'), loadImageryEvidence: async () => calls.push('imagery'),
    loadExecutionTrace: async () => calls.push('trace'), notify() {},
    setTimeout(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    async requestIntelligence(requestPath, options) {
      requests.push({ path: requestPath, options });
      if (fail) throw new Error('Temporary endpoint failure');
      if (options) return { job: { jobId: 'job-a', assessmentId: 'report-a', status: 'queued' } };
      if (requestPath.endsWith('/extraction-jobs')) {
        return { jobs: [{ jobId: 'restored-job', assessmentId: 'report-a', status: 'running' }] };
      }
      if (requestPath.includes('/extraction-jobs/')) {
        return { job: { jobId: 'job-a', assessmentId: 'report-a', status,
          resultGraphRevisionId: status === 'completed' ? 'new-graph' : null,
          progress: { processedObjects: 2, totalObjects: 2 } } };
      }
      if (requestPath.endsWith('/graph')) return { graph: { id: 'new-graph', nodes: [] } };
      if (requestPath.endsWith('/validation')) return { rounds: [] };
      if (requestPath.endsWith('/admissions')) return { admissions: [] };
      throw new Error(`Unexpected request: ${requestPath}`);
    },
  });
  return { context, state, requests, calls, scheduled,
    setStatus(value) { status = value; }, setFailure(value) { fail = value; } };
}

test('Background extraction creates one bounded request and blocks duplicates', async () => {
  const fixture = extractionFixture();
  await fixture.context.startExtractionJob('report-a', { extractAttributes: true, objectIds: ['a', 'b'] });
  await fixture.context.startExtractionJob('report-a', { extractAttributes: true });
  assert.equal(fixture.requests.length, 1);
  assert.deepEqual(JSON.parse(fixture.requests[0].options.body), {
    expectedRevision: 4, extractAttributes: true, objectIds: ['a', 'b'],
  });
  assert.equal(fixture.context.reportBusy('report-a'), true);
});

test('Reload restores active task polling without starting another extraction', async () => {
  const fixture = extractionFixture();
  await fixture.context.loadExtractionJobs('report-a');
  assert.equal(fixture.context.activeExtractionJob('report-a').jobId, 'restored-job');
  assert.equal(fixture.scheduled.length, 1);
  assert.equal(fixture.requests.some(request => request.options), false);
});

test('Reload resumes post-extraction stages even when the terminal graph is already loaded', async () => {
  const fixture = extractionFixture();
  fixture.state.task.workflowMode = 'auto';
  fixture.state.graphByAssessment['report-a'] = { graphRevisionId: 'graph-a' };
  fixture.context.requestIntelligence = async () => ({ jobs: [{ jobId: 'completed-job', assessmentId: 'report-a',
    status: 'completed', resultGraphRevisionId: 'graph-a' }] });
  const resumes = [];
  fixture.context.resumeAutomaticChain = async (id, taskId) => resumes.push([id, taskId]);
  await fixture.context.loadExtractionJobs('report-a');
  assert.equal(fixture.scheduled.length, 1);
  await fixture.scheduled[0].callback();
  assert.deepEqual(resumes, [['report-a', 'task-a']]);
});

test('Partial-job polling waits for the next attribute job to enqueue before automation', async () => {
  const fixture = extractionFixture();
  fixture.state.task.workflowMode = 'auto';
  fixture.state.autoExtractionRequested = {};
  let releaseEnqueue, enqueued = false;
  fixture.context.requestIntelligence = async (requestPath, options) => {
    if (options) {
      await new Promise(resolve => { releaseEnqueue = resolve; });
      enqueued = true;
      return { job: { jobId: 'next-job', assessmentId: 'report-a', status: 'queued' } };
    }
    if (requestPath.includes('/extraction-jobs/')) return { job: { jobId: 'partial-job', assessmentId: 'report-a', status: 'partial', resultGraphRevisionId: 'graph-a' } };
    if (requestPath.endsWith('/graph')) return { graph: { graphRevisionId: 'graph-a', nodes: [{ id: 'object-a', payload: {
      extractionMethod: 'llm', attributeExtraction: { status: 'budget-skipped' },
    } }] } };
    return {};
  };
  const automation = [];
  fixture.context.runConfiguredAutomation = async () => automation.push({ enqueued, active: !!fixture.context.activeExtractionJob('report-a') });
  const polling = fixture.context.pollExtractionJob('partial-job', 'report-a', 'task-a');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof releaseEnqueue, 'function');
  assert.deepEqual(automation, []);
  releaseEnqueue();
  await polling;
  assert.deepEqual(automation, [{ enqueued: true, active: true }]);
});

test('Completed extraction clears obsolete property selections and refreshes sources', async () => {
  const fixture = extractionFixture();
  fixture.setStatus('completed');
  await fixture.context.pollExtractionJob('job-a', 'report-a', 'task-a');
  assert.equal(fixture.state.graphByAssessment['report-a'].id, 'new-graph');
  assert.equal(fixture.state.selectedGraphProperty, null);
  assert.equal(fixture.state.objectSelections['report-a'].properties.size, 0);
  assert.ok(fixture.calls.includes('imagery'));
  assert.ok(fixture.calls.includes('trace'));
});

test('Switching tasks preserves the active report selection on background completion', async () => {
  const fixture = extractionFixture();
  fixture.state.task = { id: 'task-b' };
  fixture.state.selectedAssessmentId = 'report-b';
  fixture.state.selectedGraphProperty = { nodeId: 'object-b' };
  fixture.setStatus('completed');
  await fixture.context.pollExtractionJob('job-a', 'report-a', 'task-a');
  assert.equal(fixture.state.selectedGraphProperty.nodeId, 'object-b');
  assert.equal(fixture.state.graphByAssessment['report-a'].id, 'new-graph');
  assert.equal(fixture.calls.includes('panels'), false);
  assert.equal(fixture.calls.includes('imagery'), false);
});

test('Switching reports within the same task preserves the new property selection', async () => {
  const fixture = extractionFixture();
  fixture.state.selectedAssessmentId = 'report-b';
  fixture.state.selectedGraphProperty = { nodeId: 'object-b' };
  fixture.setStatus('completed');
  await fixture.context.pollExtractionJob('job-a', 'report-a', 'task-a');
  assert.equal(fixture.state.selectedGraphProperty.nodeId, 'object-b');
  assert.equal(fixture.calls.includes('panels'), false);
});

test('A failed job retains the old graph and selected property', async () => {
  const fixture = extractionFixture();
  fixture.setStatus('failed');
  await fixture.context.pollExtractionJob('job-a', 'report-a', 'task-a');
  assert.equal(fixture.state.graphByAssessment['report-a'].id, 'old-graph');
  assert.equal(fixture.state.selectedGraphProperty.nodeId, 'object-a');
});

test('Three endpoint failures stop automatic progress retries', async () => {
  const fixture = extractionFixture();
  fixture.setFailure(true);
  for (let index = 0; index < 3; index++) {
    delete fixture.state.extractionJobTimers['job-a'];
    await fixture.context.pollExtractionJob('job-a', 'report-a', 'task-a');
  }
  assert.equal(fixture.state.extractionJobErrorCounts['job-a'], 3);
  assert.equal(fixture.scheduled.length, 2);
  assert.equal(fixture.state.graphByAssessment['report-a'].id, 'old-graph');
});

function automaticAnalysisFixture(result) {
  const state = { task: { id: 'task-a', status: 'completed' }, assessments: [], busy: false };
  const requests = [], jobs = [], polls = [], notifications = [];
  const context = evaluate(section('async function generateAssessment(', 'async function reviewAssessment('), {
    state, Promise, Number, String, encodeURIComponent,
    ui: { generate: {}, horizon: { value: '5' } }, selectedItemIds: () => ['source-a'],
    renderAssessments() {}, renderReportPanels() {}, renderTask() {}, updateDeepLink() {},
    rememberExtractionJob(job) { jobs.push(job); },
    scheduleExtractionJob(job, taskId, delay) { polls.push({ job, taskId, delay }); },
    extractionJobActive: job => ['queued', 'running', 'interrupted'].includes(job.status),
    loadReportAugmentations: async () => {}, loadExtractionJobs: async () => {},
    loadLinkedImageryEvidence: async () => {}, loadExecutionTrace() {},
    notify(message, tone) { notifications.push({ message, tone }); },
    async request(requestPath, options) { requests.push({ requestPath, options }); return result; },
  });
  return { context, state, requests, jobs, polls, notifications };
}

test('Analysis restores the server-created extraction job without submitting a second model task', async () => {
  const fixture = automaticAnalysisFixture({ assessment: { assessmentId: 'report-a', analysisType: 'model-assessment' },
    extractionJob: { jobId: 'auto-job-a', assessmentId: 'report-a', status: 'queued' } });
  await fixture.context.generateAssessment();
  assert.equal(fixture.requests.length, 1);
  assert.equal(fixture.requests[0].requestPath, '/tasks/task-a/assessments');
  assert.equal(fixture.jobs[0].jobId, 'auto-job-a');
  assert.equal(fixture.polls.length, 1);
  assert.equal(fixture.polls[0].taskId, 'task-a');
  assert.equal(fixture.state.selectedAssessmentId, 'report-a');
  assert.equal(fixture.state.busy, false);
});

test('Automatic enqueue failure keeps the saved report and exposes a manual recovery reason', async () => {
  const fixture = automaticAnalysisFixture({ assessment: { assessmentId: 'report-a', analysisType: 'model-assessment' },
    extractionError: 'Queue unavailable; retry extraction' });
  await fixture.context.generateAssessment();
  assert.equal(fixture.state.assessments[0].assessmentId, 'report-a');
  assert.equal(fixture.state.extractionStartErrors['report-a'], 'Queue unavailable; retry extraction');
  assert.equal(fixture.jobs.length, 0);
  assert.equal(fixture.polls.length, 0);
  assert.equal(fixture.notifications.at(-1).tone, 'warn');
  assert.equal(fixture.notifications.at(-1).message.includes('分析版本已保存'), true);
});

test('Attribute recovery targets unprocessed and failed objects rather than unsupported or completed values', () => {
  const context = evaluate(section('function extractionJobActive(', 'function objectVerificationControls('), {
    extractedRecords: () => ({ nodes: ['completed', 'no-explicit-attributes', 'no-supported-attributes',
      'budget-skipped', 'pending', 'failed', null].map((status, index) => ({ id: String(index),
      payload: status ? { attributeExtraction: { status } } : {} })) }),
  });
  assert.deepEqual(Array.from(context.pendingAttributeNodes({}), node => node.id), ['3', '4', '5', '6']);
});

test('Automatic resume skips durable validation and critique rounds after reload', async () => {
  const state = {
    task: { id: 'task-a', workflowMode: 'auto', automation: {
      autoRunEvidenceCheck: true, autoRunModelCritique: true, autoCreateAdmissionCandidates: true,
    }},
    assessments: [{ id: 'report-a', status: 'under-review', summary: {} }],
    selectedAssessmentId: 'report-a', extractionJobs: { 'report-a': [{ jobId: 'job-a', status: 'completed', resultGraphRevisionId: 'graph-a' }] },
    validationByAssessment: { 'report-a': { rounds: ['deterministic', 'model-critique'].map(kind => ({ kind, status: 'needs-evidence', confidenceFactors: { objectVerificationCoverage: { graphRevisionId: 'graph-a' } } })) } },
    admissionsByAssessment: { 'report-a': { admissions: [{ id: 'candidate-a' }] } },
    augmentationLoaded: { 'report-a': true }, augmentationErrors: {}, augmentationBusy: {}, autoActions: {}, busy: false,
  };
  const calls = [], notifications = [];
  const context = evaluate(section('function objectVerificationControls(', 'function linkedObjectChecks('), {
    state, Promise, String, Set, asArray, assessmentId: value => value.id,
    graphData: () => ({ graphRevisionId: 'graph-a', nodes: [] }),
    extractionJobActive: job => ['queued', 'running', 'interrupted'].includes(job.status),
    validationRounds: report => state.validationByAssessment[report.id].rounds,
    ontologyAdmissions: report => state.admissionsByAssessment[report.id].admissions,
    reportBusy: () => false, renderAssessments() {}, renderReview() {}, loadReportAugmentations: async () => {},
    buildOntologyAdmissions: async () => { calls.push('admissions'); return { ok: true }; },
    notify(message) { notifications.push(message); },
  });
  assert.equal(await context.runConfiguredAutomation('report-a'), true);
  assert.deepEqual(calls, ['admissions']);
  assert.equal(state.autoActions['report-a'], 'done');
  assert.equal(notifications.some(message => message.includes('执行完毕')), true);
});

test('Automatic resume leaves an active extraction job waiting instead of validating', async () => {
  const state = {
    task: { id: 'task-a', workflowMode: 'auto', automation: { autoRunEvidenceCheck: true } },
    assessments: [{ id: 'report-a', summary: {} }], selectedAssessmentId: 'report-a',
    extractionJobs: { 'report-a': [{ jobId: 'job-a', status: 'running' }] }, validationByAssessment: {},
    admissionsByAssessment: {}, augmentationLoaded: { 'report-a': true }, augmentationErrors: {}, autoActions: {},
  };
  const context = evaluate(section('function objectVerificationControls(', 'function linkedObjectChecks('), {
    state, Promise, String, asArray, assessmentId: value => value.id,
    graphData: () => ({ graphRevisionId: 'graph-a', nodes: [] }),
    extractionJobActive: job => ['queued', 'running', 'interrupted'].includes(job.status),
  });
  assert.equal(await context.runConfiguredAutomation('report-a'), false);
  assert.equal(state.autoActions['report-a'], 'waiting-extraction');
});

function automationFixture() {
  const graph = { graphRevisionId: 'graph-a', nodes: [] };
  const state = { task: { id: 'task-a', workflowMode: 'auto', automation: {
    autoRunEvidenceCheck: true, autoRunModelCritique: true, autoCreateAdmissionCandidates: true,
  } }, assessments: [{ id: 'report-a', summary: {} }], selectedAssessmentId: 'report-a',
  extractionJobs: { 'report-a': [{ jobId: 'job-a', status: 'completed', resultGraphRevisionId: 'graph-a' }] },
  augmentationLoaded: { 'report-a': true }, augmentationBusy: {}, augmentationErrors: {},
  validationByAssessment: { 'report-a': { rounds: [] } }, autoActions: {} };
  const calls = [], notifications = [];
  const context = evaluate(section('function objectVerificationControls(', 'function linkedObjectChecks('), {
    state, Promise, String, Set, asArray, assessmentId: value => value.id, graphData: () => graph,
    validationRounds: report => state.validationByAssessment[report.id].rounds,
    extractionJobActive: job => ['queued', 'running', 'interrupted'].includes(job.status),
    loadReportAugmentations: async () => {}, renderAssessments() {},
    startExtractionJob: async (id, options) => { calls.push({ kind: 'extraction', options }); },
    runReportValidation: async (id, kind) => { calls.push({ kind: kind || 'deterministic' }); return true; },
    buildOntologyAdmissions: async () => { calls.push({ kind: 'admissions' }); return { ok: true }; },
    notify(message) { notifications.push(message); },
    setTimeout(callback) { callback(); },
  });
  return { graph, state, calls, notifications, context };
}

test('Failed attribute or link calls interrupt automatic completion before validation', async () => {
  for (const payload of [{ attributeExtraction: { status: 'failed' } }, { attributeLinkMetadata: { status: 'failed' } }]) {
    const fixture = automationFixture();
    fixture.graph.nodes.push({ id: 'object-a', payload });
    assert.equal(await fixture.context.runConfiguredAutomation('report-a'), false);
    assert.equal(fixture.state.autoActions['report-a'], 'error');
    assert.match(fixture.state.autoActionErrors['report-a'], /异常中断/);
    assert.deepEqual(fixture.calls, []);
  }
});

test('Latest failed extraction does not resume an older completed graph', async () => {
  const fixture = automationFixture();
  fixture.state.extractionJobs['report-a'].unshift({ jobId: 'failed-job', status: 'failed', errorCode: 'MODEL_TIMEOUT' });
  assert.equal(await fixture.context.resumeAutomaticChain('report-a', 'task-a'), false);
  assert.equal(fixture.state.autoActions['report-a'], 'error');
  assert.deepEqual(fixture.calls, []);
});

test('Validation persistence requires the current graph revision', async () => {
  const fixture = automationFixture();
  fixture.state.validationByAssessment['report-a'].rounds = ['deterministic', 'model-critique'].map(kind => ({
    kind, confidenceFactors: { objectVerificationCoverage: { graphRevisionId: 'old-graph' } },
  }));
  assert.equal(await fixture.context.runConfiguredAutomation('report-a'), true);
  assert.deepEqual(fixture.calls.map(call => call.kind), ['deterministic', 'model-critique', 'admissions']);
});

test('Hydration lock prevents simultaneous resume from repeating paid critique', async () => {
  const fixture = automationFixture();
  delete fixture.state.augmentationLoaded['report-a'];
  let finishHydration;
  fixture.context.loadReportAugmentations = () => new Promise(resolve => { finishHydration = resolve; });
  const firstRun = fixture.context.runConfiguredAutomation('report-a');
  assert.equal(await fixture.context.runConfiguredAutomation('report-a'), false);
  assert.deepEqual(fixture.calls, []);
  finishHydration();
  assert.equal(await firstRun, true);
  assert.deepEqual(fixture.calls.map(call => call.kind), ['deterministic', 'model-critique', 'admissions']);
  assert.equal(fixture.state.autoChainBusy['report-a'], undefined);
});

test('Attribute continuation processes all 25 objects across more than two batches', async () => {
  const fixture = automationFixture();
  fixture.graph.nodes = Array.from({ length: 25 }, (_, index) => ({ id: 'object-' + index,
    payload: { extractionMethod: 'llm', attributeExtraction: { status: 'budget-skipped' } } }));
  const batches = [];
  fixture.context.startExtractionJob = async (id, options) => {
    batches.push([...options.objectIds]);
    for (const node of fixture.graph.nodes) if (options.objectIds.includes(node.id)) node.payload.attributeExtraction.status = 'completed';
  };
  for (let index = 0; index < 4; index++) assert.equal(await fixture.context.runConfiguredAutomation('report-a'), false);
  assert.deepEqual(batches.map(batch => batch.length), [8, 8, 8, 1]);
  assert.deepEqual(fixture.calls, []);
  assert.equal(await fixture.context.runConfiguredAutomation('report-a'), true);
  assert.deepEqual(fixture.calls.map(call => call.kind), ['deterministic', 'model-critique', 'admissions']);
});

test('Admission failure propagates and never marks automatic completion done', async () => {
  const fixture = automationFixture();
  fixture.context.buildOntologyAdmissions = async () => false;
  assert.equal(await fixture.context.runConfiguredAutomation('report-a'), false);
  assert.equal(fixture.state.autoActions['report-a'], 'error');
  assert.match(fixture.state.autoActionErrors['report-a'], /准入候选生成未闭合/);
});

function automaticAdmissionFixture() {
  const graph = { graphRevisionId: 'graph-a', nodes: [], edges: [] }, report = { id: 'report-a', summary: { capabilityAssessment: { metrics: [] } }, metrics: [] };
  const state = { assessments: [report], augmentationBusy: {}, admissionsByAssessment: { 'report-a': { admissions: [] } },
    attributePredicates: [{ id: 'reportedArchitecture', domains: ['EquipmentModel'], aliases: ['architecture'] },
      { id: 'reportedMaturity', domains: ['EquipmentModel'], aliases: ['产业成熟度'] },
      { id: 'reportedContractStage', domains: ['EquipmentModel', 'ProcurementEvent'], aliases: ['合同阶段'] },
      { id: 'reportedEventDate', domains: ['TestEvent'], aliases: ['eventDate'] }] };
  const requests = [], notifications = [];
  const context = evaluate(section('function automaticPropertyPredicate(', 'async function buildGraphAdmissions('), {
    state, Set, Array, String, JSON, asArray, first, assessmentId: value => value.id,
    graphData: () => graph, reportBusy: () => false, capabilityMetrics: value => value.metrics,
    ontologyAdmissions: () => state.admissionsByAssessment['report-a'].admissions,
    graphAdmissionEligibility: element => ({ eligible: element.eligible !== false }),
    groundedObjectProperties: node => node.properties || [],
    linkedObjectChecks: (a, id) => [{ propertyEvidence: graph.nodes.find(node => node.id === id)?.verification || [], machineStatus: graph.edges.find(edge => edge.id === id)?.machineStatus }],
    predicateRecommendation: attribute => attribute.ontologyPredicateSuggestion,
    renderAssessments() {}, renderReview() {}, loadExecutionTrace() {},
    notify(message) { notifications.push(message); },
    async requestIntelligence(requestPath, options) {
      requests.push({ path: requestPath, options });
      if (options) {
        const selection = JSON.parse(options.body), current = state.admissionsByAssessment['report-a'].admissions;
        for (const id of selection.metricIds || []) current.push({ kind: 'capability-metric', metricId: id });
        for (const id of selection.graphNodeIds || []) current.push({ kind: 'graph-node', mapping: { graphRevisionId: 'graph-a', graphElementId: id } });
        for (const id of selection.graphEdgeIds || []) current.push({ kind: 'graph-relation', mapping: { graphRevisionId: 'graph-a', graphElementId: id } });
        for (const property of selection.graphProperties || []) current.push({ kind: 'graph-property', mapping: { graphRevisionId: 'graph-a', graphElementId: property.graphNodeId, propertyPath: property.propertyPath } });
      }
      return state.admissionsByAssessment['report-a'];
    },
  });
  return { context, graph, report, state, requests, notifications };
}

test('Zero metrics and no supported graph candidates make admission explicitly not applicable', async () => {
  const fixture = automaticAdmissionFixture();
  const result = await fixture.context.buildOntologyAdmissions('report-a', { automatic: true });
  assert.equal(result.notApplicable, true);
  assert.equal(fixture.requests.some(request => request.options), false);
});

test('Metric admission uses only saved summary IDs while graph observations use verified properties', async () => {
  const fixture = automaticAdmissionFixture();
  fixture.report.summary.capabilityAssessment.metrics = [{ id: 'summary-metric' }];
  fixture.report.metrics = [{ id: 'summary-metric' }, { id: 'graph-derived-metric' }];
  fixture.graph.nodes.push({ id: 'object-a', type: 'EquipmentModel', properties: [{
    propertyPath: 'payload.attributes[0].value', attribute: { name: 'architecture', value: 'open' },
  }], verification: [{ propertyPath: 'payload.attributes[0].value', machineStatus: 'passed' }] });
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).ok, true);
  const posts = fixture.requests.filter(request => request.options).map(request => JSON.parse(request.options.body));
  assert.deepEqual(posts.filter(post => post.metricIds).map(post => post.metricIds), [['summary-metric']]);
  assert.equal(posts.some(post => post.graphProperties?.[0]?.graphNodeId === 'object-a'), true);
  assert.equal(JSON.stringify(posts).includes('graph-derived-metric'), false);
  await fixture.context.buildOntologyAdmissions('report-a', { automatic: true });
  assert.equal(fixture.requests.filter(request => request.options).length, 2);
});

test('Graph-only displayed metrics never create unknown capability metric requests', async () => {
  const fixture = automaticAdmissionFixture();
  fixture.report.metrics = [{ id: 'graph-derived-metric' }];
  const result = await fixture.context.buildOntologyAdmissions('report-a', { automatic: true });
  assert.equal(result.notApplicable, true);
  assert.equal(fixture.requests.some(request => request.options), false);
});

test('Automatic graph admissions save supported properties once without publishing', async () => {
  const fixture = automaticAdmissionFixture();
  fixture.graph.nodes.push({ id: 'object-a', type: 'EquipmentModel', properties: [0, 1].map(index => ({
    propertyPath: 'payload.attributes[' + index + '].value', attribute: { name: 'architecture' },
  })), verification: [0, 1].map(index => ({ propertyPath: 'payload.attributes[' + index + '].value', machineStatus: index ? 'failed' : 'passed' })) });
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).notApplicable, false);
  const posts = fixture.requests.filter(request => request.options);
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body).graphProperties, [{ graphNodeId: 'object-a', propertyPath: 'payload.attributes[0].value', ontologyPredicate: 'reportedArchitecture' }]);
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).ok, true);
  assert.equal(fixture.requests.filter(request => request.options).length, 1);
  assert.equal(fixture.requests.every(request => request.path.endsWith('/admissions')), true);
});

test('Automatic property admission resolves a conflicting model predicate through the backend category catalog', async () => {
  const fixture = automaticAdmissionFixture();
  const attribute = { name: '产业成熟度', value: 'public test', ontologyPredicateSuggestion: { id: 'reportedContractStage' } };
  fixture.graph.nodes.push({ id: 'object-a', type: 'EquipmentModel', properties: [{ propertyPath: 'payload.attributes[0].value', attribute }],
    verification: [{ propertyPath: 'payload.attributes[0].value', machineStatus: 'passed' }] });
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).ok, true);
  const post = fixture.requests.find(request => request.options);
  assert.deepEqual(JSON.parse(post.options.body).graphProperties, [{ graphNodeId: 'object-a', propertyPath: 'payload.attributes[0].value', ontologyPredicate: 'reportedMaturity' }]);
  assert.equal(attribute.ontologyPredicateSuggestion.id, 'reportedContractStage');
});

test('Automatic property admission rejects undeclared, incompatible-domain and wrong event-date predicates', async () => {
  const fixture = automaticAdmissionFixture();
  const row = (name, id, propertyPath = 'payload.attributes[0].value') => ({ propertyPath, attribute: { name, ontologyPredicateSuggestion: { id } } });
  assert.equal(fixture.context.automaticPropertyPredicate({ type: 'EquipmentModel' }, row('unknown', 'notDeclared')), '');
  assert.equal(fixture.context.automaticPropertyPredicate({ type: 'Organization' }, row('architecture', 'reportedArchitecture')), '');
  assert.equal(fixture.context.automaticPropertyPredicate({ type: 'EventRecord', payload: { eventKind: 'TestEvent' } }, row('eventDate', 'reportedEventDate')), '');
  assert.equal(fixture.context.automaticPropertyPredicate({ type: 'EventRecord', payload: { eventKind: 'TestEvent' } }, row('eventDate', 'reportedEventDate', 'payload.date')), 'reportedEventDate');
});

test('Automatic graph admissions consume more than one API selection budget', async () => {
  const fixture = automaticAdmissionFixture();
  fixture.graph.nodes = Array.from({ length: 205 }, (_, index) => ({ id: 'object-' + index }));
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).ok, true);
  assert.deepEqual(fixture.requests.filter(request => request.options).map(request => JSON.parse(request.options.body).graphNodeIds.length), [200, 5]);
  assert.equal(fixture.state.admissionsByAssessment['report-a'].admissions.length, 205);
});

test('Automatic relation admissions require eligible mapping and a passed current graph check', async () => {
  const fixture = automaticAdmissionFixture();
  fixture.graph.edges = [
    { id: 'passed-edge', machineStatus: 'passed' },
    { id: 'failed-edge', machineStatus: 'failed' },
    { id: 'unmapped-edge', machineStatus: 'needs-evidence' },
    { id: 'unverified-edge' },
    { id: 'invalid-domain-edge', machineStatus: 'passed', eligible: false },
  ];
  assert.equal((await fixture.context.buildOntologyAdmissions('report-a', { automatic: true })).ok, true);
  const posts = fixture.requests.filter(request => request.options).map(request => JSON.parse(request.options.body));
  assert.deepEqual(posts.map(post => post.graphEdgeIds), [['passed-edge']]);
});

function propertyFixture() {
  const candidate = { admissionId: 'property-a', assessmentId: 'report-a', kind: 'graph-property',
    revision: 2, assertion: { rawValue: 'Open architecture' }, review: {} };
  const graph = { graphRevisionId: 'graph-a', nodes: [{ id: 'model-a', payload: {
    attributes: [{ name: 'architecture', value: 'Open architecture' }] } }] };
  const state = { task: { id: 'task-a' }, assessments: [{ id: 'report-a' }], selectedAssessmentId: 'report-a',
    augmentationBusy: {}, admissionForms: { 'report-a': { reviewer: 'Reviewer', reason: 'Explicit quote checked' } },
    admissionConfidence: { 'property-a': 'high' }, propertyEdits: {}, admissionsByAssessment: {}, propertyMappings: {} };
  const requests = [], notifications = [];
  const selection = { nodes: new Set(), edges: new Set(),
    properties: new Set([JSON.stringify(['model-a', 'payload.attributes[0].value'])]) };
  const context = evaluate(section('async function buildGraphAdmissions(', 'function renderReportPanels('), {
    state, Set, asArray, first, reportBusy: () => false, assessmentId: value => value.id,
    graphData: () => graph, objectSelection: () => selection,
    groundedObjectProperties: node => [{ attribute: node.payload.attributes[0], propertyPath: 'payload.attributes[0].value' }],
    propertyPredicate: () => 'reportedArchitecture', ontologyAdmissions: () => [candidate],
    renderAssessments() {}, renderEvidence() {}, renderReportPanels() {}, updateDeepLink() {},
    notify(message) { notifications.push(message); },
    async requestIntelligence(requestPath, options) {
      requests.push({ path: requestPath, options });
      return options ? { candidate: { admissionId: 'property-new' } } : { admissions: [] };
    },
  });
  return { context, state, candidate, graph, requests, notifications };
}

test('Property admission includes the owner identity and the exact mapped property', async () => {
  const fixture = propertyFixture();
  await fixture.context.buildGraphAdmissions('report-a');
  const body = JSON.parse(fixture.requests[0].options.body);
  assert.deepEqual(body.graphNodeIds, ['model-a']);
  assert.deepEqual(body.graphProperties, [{ graphNodeId: 'model-a',
    propertyPath: 'payload.attributes[0].value', ontologyPredicate: 'reportedArchitecture' }]);
});

test('Property admission forwards a model predicate recommendation without a manual selection', async () => {
  const fixture = propertyFixture();
  fixture.graph.nodes[0].payload.attributes[0].ontologyPredicateSuggestion = {
    id: 'reportedInterface', confidence: 'high', reason: 'The source names an open interface', alternatives: [],
  };
  await fixture.context.buildGraphAdmissions('report-a');
  const body = JSON.parse(fixture.requests[0].options.body);
  assert.deepEqual(body.graphProperties, [{ graphNodeId: 'model-a',
    propertyPath: 'payload.attributes[0].value', ontologyPredicate: 'reportedInterface' }]);
});

test('High confidence must be explicitly confirmed before property acceptance', async () => {
  const fixture = propertyFixture();
  delete fixture.state.admissionConfidence['property-a'];
  await fixture.context.reviewOntologyAdmission('accept', 'property-a', 'report-a');
  assert.equal(fixture.requests.length, 0);
  fixture.state.admissionConfidence['property-a'] = 'high';
  await fixture.context.reviewOntologyAdmission('accept', 'property-a', 'report-a');
  assert.equal(JSON.parse(fixture.requests[0].options.body).verifiedConfidence, 'high');
});

test('Modifying a property opens a new candidate without automatically approving it', async () => {
  const fixture = propertyFixture();
  fixture.state.propertyEdits['property-a'] = {
    rawValue: 'Open architecture', normalizedValue: '开放架构', ontologyPredicate: 'reportedArchitecture',
  };
  await fixture.context.reviewOntologyAdmission('modify', 'property-a', 'report-a');
  const body = JSON.parse(fixture.requests[0].options.body);
  assert.equal(body.action, 'modify');
  assert.equal(body.editedProperty.normalizedValue, '开放架构');
  assert.equal(body.verifiedConfidence, undefined);
  assert.equal(fixture.state.selectedAdmissionId, 'property-new');
  assert.equal(fixture.candidate.admissionId, 'property-a');
});

test('Superseded candidates cannot be published and invalid numeric changes are blocked', async () => {
  const fixture = propertyFixture();
  fixture.candidate.supersededByAdmissionId = 'property-new';
  await fixture.context.reviewOntologyAdmission('publish', 'property-a', 'report-a');
  assert.equal(fixture.requests.length, 0);
  delete fixture.candidate.supersededByAdmissionId;
  fixture.candidate.assertion.rawValue = 10;
  fixture.state.propertyEdits['property-a'] = { rawValue: 'invalid' };
  await fixture.context.reviewOntologyAdmission('modify', 'property-a', 'report-a');
  assert.equal(fixture.requests.length, 0);
});

function imageryFixture() {
  const parent = { assessmentId: 'report-parent', graphRevisionId: 'frozen-graph', graphElementId: 'model-parent',
    objectLabel: 'Model Parent', purpose: 'public-event-imagery', parentTaskId: 'task-parent' };
  const requests = [], button = { disabled: false };
  const form = { dataset: { taskId: 'task-child', assessmentId: 'report-parent', graphRevisionId: 'frozen-graph' },
    querySelector: () => button };
  const elements = { 'imagery-parent-target': { hidden: true, innerHTML: '' },
    'imagery-property': { value: 'payload.attributes[0].value', innerHTML: '' },
    'imagery-target': { value: 'model-parent' } };
  const state = { task: { id: 'task-child' }, parentFollowups: { 'task-child': parent },
    parentTargetGraphs: {}, parentFollowupLoading: {}, linkedImageryByAssessment: {}, linkedImageryCoverage: {},
    imageryByTask: { 'task-parent': [{ id: 'old-image', taskId: 'task-parent', assessmentId: 'report-parent',
      graphRevisionId: 'old-graph', graphElementId: 'model-parent', title: 'Old image', sourceUrl: 'https://example.org',
      supportStatement: 'Older context evidence', mediaKind: 'satellite-image' }] } };
  const context = evaluate(
    section('function imageryParentTarget(', 'function renderImageryEvidence(')
      + section('function renderImageryProperties(', 'function resetImageryForm(')
      + section('async function saveImageryEvidence(', 'function renderHistory('), {
      state, Set, Promise, asArray, esc, safeUrl: value => value || '',
      FormData: class { constructor() { return [
        ['title', 'Image'], ['graphElementId', ''], ['sourceUrl', 'https://example.org'],
        ['propertyPath', 'payload.attributes[0].value'],
      ][Symbol.iterator](); } },
      $: id => elements[id], assessmentId: value => value.id, graphData: () => ({ graphRevisionId: 'current-graph' }),
      selectedAssessment: () => null, resetImageryForm() {}, loadImageryEvidence: async () => {},
      renderImageryEvidence() {}, renderAssessments() {}, notify() {},
      groundedObjectProperties: () => [], propertyNameLabel: name => name,
      async request(requestPath, options) {
        requests.push({ path: requestPath, options });
        if (options) return { imageryEvidence: { id: 'new-image' }, duplicate: false };
        if (requestPath.endsWith('/parent-followup')) return { followup: parent };
        return { imageryEvidence: [{ id: 'new-image', taskId: 'task-child', assessmentId: 'report-parent',
          graphRevisionId: 'frozen-graph', graphElementId: 'model-parent', title: 'Child image',
          sourceUrl: 'https://example.org', supportStatement: 'Supports publication context' }] };
      },
      async requestIntelligence(requestPath) {
        return requestPath.endsWith('/followups')
          ? { followups: [{ purpose: 'public-event-imagery', followupTaskId: 'task-child' }] }
          : { graph: { graphRevisionId: 'new-graph', nodes: [] } };
      },
    });
  return { context, state, parent, requests, form, button, elements };
}

test('Image child with no assessment submits the exact frozen parent target', async () => {
  const fixture = imageryFixture();
  await fixture.context.saveImageryEvidence({ preventDefault() {}, currentTarget: fixture.form });
  const body = JSON.parse(fixture.requests[0].options.body);
  assert.equal(body.assessmentId, 'report-parent');
  assert.equal(body.graphRevisionId, 'frozen-graph');
  assert.equal(body.graphElementId, 'model-parent');
  assert.equal(body.propertyPath, null);
});

test('Refresh restores frozen imagery context and excludes property paths from changed graphs', async () => {
  const fixture = imageryFixture();
  await fixture.context.loadParentFollowup('task-child');
  assert.equal(fixture.state.parentFollowups['task-child'].graphRevisionId, 'frozen-graph');
  assert.equal(fixture.state.parentTargetGraphs['task-child'], undefined);
  fixture.context.renderImageryProperties();
  assert.equal(fixture.elements['imagery-property'].innerHTML.includes('payload.attributes['), false);
});

test('The parent object reads image child records while retaining stale version labels', async () => {
  const fixture = imageryFixture();
  await fixture.context.loadLinkedImageryEvidence('report-parent');
  fixture.state.task = { id: 'task-parent' };
  const html = fixture.context.objectImageryMarkup({ id: 'report-parent' }, 'model-parent');
  assert.ok(html.includes('Child image'));
  assert.ok(html.includes('Old image'));
  assert.ok(html.includes('旧图谱版本引用'));
  assert.ok(html.includes('task-child'));
});

function followUpFixture() {
  const requests = [], openedTasks = [];
  const state = { sources: [], task: { id: 'parent' }, selectedAssessmentId: 'report-a',
    followUpObject: { 'report-a': 'model-a' }, followUpPlans: { 'report-a': {
      graphRevisionId: 'graph-a', budget: { maxTasks: 4, maxItems: 32, remainingTasks: 1, remainingItems: 8 },
      targets: [{ targetId: 'target-architecture', objectId: 'model-a', objectLabel: 'Model A', targetType: 'property',
        attributeLabel: '架构', rawValue: 'Open architecture', reason: 'Need independent evidence', maxItems: 8,
        sourceIds: ['defense-official'], executable: true,
        sourceContext: [{ publishedAt: null, contentNature: 'manufacturer-claim' }] }],
    } } };
  const context = evaluate(
    section('function sourceNatureLabel(', 'function sourceProvenance(')
      + section('function followUpPlanMarkup(', 'async function loadExtractionJobs('), {
      state, asArray, esc, fieldText: value => String(value ?? ''), dayLabel: value => value,
      statusLabel: value => [value], listText: value => value || [], assessmentId: value => value.id,
      graphData: () => ({ graphRevisionId: 'graph-a' }), renderAssessments() {}, notify() {},
      loadTask: async id => openedTasks.push(id),
      async requestIntelligence(requestPath, options) {
        requests.push({ path: requestPath, options });
        return { task: { id: 'new-child' }, followup: { purpose: 'attribute-gap' } };
      },
    });
  return { context, state, requests, openedTasks, target: state.followUpPlans['report-a'].targets[0] };
}

test('A follow-up request selects the exact attribute target ID', async () => {
  const fixture = followUpFixture();
  await fixture.context.runFollowUpTarget('report-a', 0);
  assert.equal(JSON.parse(fixture.requests[0].options.body).targetId, 'target-architecture');
});

test('Follow-up cards expose dates, remaining budgets and deterministic planning', () => {
  const fixture = followUpFixture();
  const html = fixture.context.followUpPlanMarkup({ id: 'report-a' });
  assert.ok(html.includes('架构'));
  assert.ok(html.includes('资料发布日期缺失'));
  assert.ok(html.includes('剩余 1 项任务 / 8 条材料'));
  assert.ok(html.includes('程序按对象'));
});

test('Budget-exhausted targets are blocked while executed targets reuse the saved task', async () => {
  const fixture = followUpFixture();
  fixture.target.executable = false;
  await fixture.context.runFollowUpTarget('report-a', 0);
  assert.equal(fixture.requests.length, 0);
  fixture.target.followupTaskId = 'saved-child';
  await fixture.context.runFollowUpTarget('report-a', 0);
  assert.equal(fixture.requests.length, 0);
  assert.deepEqual(fixture.openedTasks, ['saved-child']);
});

test('Attribute rows show actual validity times, unknown legacy fields and restored-quote review status', () => {
  const attribute = { name: 'architecture', value: 'Open architecture', validTime: null,
    evidenceRefs: [{ quote: 'Model A has an Open architecture.' }],
    draftMetadata: { quoteRecoveryMethod: 'explicit-acronym-same-paragraph', humanReviewRequired: true } };
  const node = { id: 'model-a', label: 'Model A', payload: { attributes: [attribute] } };
  const context = evaluate(section('function propertyNameLabel(', 'function objectAttributeExtractionMarkup('), {
    state: { propertyMappings: {}, propertyEdits: {} }, Set, asArray, esc, first,
    fieldText: value => String(value ?? ''), graphTypeLabel: value => value,
    assessmentId: value => value.id, linkedObjectChecks: () => [],
    groundedObjectProperties: () => [{ attribute, index: 0, propertyPath: 'payload.attributes[0].value' }],
    objectSelection: () => ({ properties: new Set() }), reportBusy: () => false,
    graphAdmissionEligibility: () => ({ eligible: true }), ontologyAdmissions: () => [],
    admissionRecommendationMarkup: () => '',
  });
  const row = ['架构', 'Open architecture', 0, '', 'payload.attributes[0].value'];
  let html = context.objectAttributeMarkup(row, node, { id: 'report-a' });
  assert.ok(html.includes('有效时间：未知'));
  assert.ok(html.includes('已补全连续引文，属性归属待审核'));
  assert.equal(html.includes('explicit-acronym-same-paragraph'), false);
  attribute.validTime = 'during the 2024 public demonstration';
  html = context.objectAttributeMarkup(row, node, { id: 'report-a' });
  assert.ok(html.includes('有效时间：during the 2024 public demonstration'));
  delete attribute.validTime;
  assert.ok(context.objectAttributeMarkup(row, node, { id: 'report-a' }).includes('有效时间：未知'));
  assert.equal(context.propertyNameLabel('interface-standard'), '标准与接口');
  assert.equal(context.propertyNameLabel('human-oversight'), '人工监督');
  assert.equal(context.propertyNameLabel('public-test-status'), '公开试验状态');
  assert.equal(context.propertyNameLabel('industrial-maturity'), '产业成熟度');
});

test('Job progress labels the checkpoint as the most recently processed object', () => {
  const context = evaluate(section('function extractionJobActive(', 'function followUpPlanMarkup('), {
    state: { extractionJobs: { 'report-a': [{ jobId: 'job-a', status: 'running',
      progress: { processedObjects: 1, totalObjects: 2, currentObjectId: 'model-a' } }] },
      extractionJobErrors: {}, augmentationBusy: {} },
    asArray, esc, assessmentId: value => value.id,
    extractedRecords: () => ({ nodes: [{ id: 'model-a', label: 'Model A', payload: {} }] }),
  });
  const html = context.extractionJobsMarkup({ id: 'report-a' });
  assert.ok(html.includes('最近处理对象：Model A'));
  assert.equal(html.includes('当前对象：'), false);
});

function attributeVerificationFixture() {
  const node = { id: 'model-a', label: 'Model A', type: 'Technology', payload: { attributes: [
    { name: 'human-oversight', value: 'OODA loop', evidenceRefs: [{ quote: 'Model A accelerates the OODA loop.' }] },
    { name: 'architecture', value: 'Open architecture', evidenceRefs: [{ quote: 'Model A has Open architecture.' }] },
  ] } };
  const check = { graphRevisionId: 'graph-a', machineStatus: 'failed', status: 'needs-evidence',
    round: { createdAt: '2026-10-08' }, propertyEvidence: [
      { propertyPath: 'payload.attributes[0].value', machineStatus: 'failed' },
      { propertyPath: 'payload.attributes[1].value', machineStatus: 'passed' },
    ], checks: [
      { code: 'identity-quote', status: 'passed' },
      { code: 'ontology-class', status: 'passed' },
      { code: 'attribute-field-context', status: 'failed', propertyPath: 'payload.attributes[0].value',
        detail: '原文缺少人工监督线索，保留历史草稿，请修正分类或补证。' },
      { code: 'attribute-value-quote', status: 'passed', propertyPath: 'payload.attributes[1].value' },
    ] };
  const context = evaluate(
    section('function verificationCheckLabel(', 'function objectNeighborhoodMarkup(')
      + section('function propertyNameLabel(', 'function objectAttributeExtractionMarkup('), {
      state: { propertyMappings: {}, propertyEdits: {} }, Set, asArray, esc, first,
      fieldText: value => String(value ?? ''), graphTypeLabel: value => value,
      validationStatusLabel: value => ({ passed: '机器检查通过', failed: '检查失败', 'needs-evidence': '需要补证' })[value] || value,
      shortTime: value => value, assessmentId: value => value.id, linkedObjectChecks: () => [check],
      groundedObjectProperties: value => value.payload.attributes.map((attribute, index) => ({
        attribute, index, propertyPath: `payload.attributes[${index}].value`,
      })), objectSelection: () => ({ properties: new Set() }), reportBusy: () => false,
      graphAdmissionEligibility: () => ({ eligible: true }), ontologyAdmissions: () => [],
      admissionRecommendationMarkup: () => '',
    });
  return { context, node, check, assessment: { id: 'report-a' } };
}

test('Object verification distinguishes passed identity from a failed attribute collection', () => {
  const fixture = attributeVerificationFixture();
  const html = fixture.context.objectVerificationMarkup(fixture.assessment, fixture.node.id);
  assert.ok(html.includes('对象与属性核验汇总：检查失败'));
  assert.ok(html.includes('对象身份检查：机器检查通过'));
  assert.ok(html.includes('属性类别线索'));
  assert.ok(html.includes('属性路径：payload.attributes[0].value'));
});

test('A failed sibling attribute shows its reason without disabling the valid architecture property', () => {
  const fixture = attributeVerificationFixture();
  const bad = fixture.context.objectAttributeMarkup(['人工监督', 'OODA loop', 0, '',
    'payload.attributes[0].value'], fixture.node, fixture.assessment);
  const good = fixture.context.objectAttributeMarkup(['架构', 'Open architecture', 1, '',
    'payload.attributes[1].value'], fixture.node, fixture.assessment);
  assert.ok(bad.includes('本项检查：检查失败'));
  assert.ok(bad.includes('保留历史草稿，请修正分类或补证'));
  assert.ok(good.includes('本项检查：机器检查通过'));
  assert.equal(good.includes('保留历史草稿'), false);
  assert.equal(good.match(/<input[^>]*data-property-select[^>]*>/)[0].includes('disabled'), false);
});

test('Valid property candidates retain modify, evidence and rejection actions despite a sibling failure', () => {
  const fixture = propertyFixture();
  fixture.candidate.status = 'under-review';
  fixture.candidate.mapping = {};
  const context = evaluate(section('function ontologyMarkup(', 'function renderExecution('), {
    state: fixture.state, asArray, esc, first, assessmentId: value => value.id,
    ontologyAdmissions: () => [fixture.candidate], reportBusy: () => false, capabilityMetrics: () => [],
    admissionKind: () => 'property', propertyCandidateFields: () => '', admissionLabel: () => 'Model A · Architecture',
    admissionEvidence: () => [{ quote: 'Model A has an open architecture.' }],
    confidenceLabel: () => '待核对', admissionRecommendationMarkup: () => '',
    propertyEditMarkup: (_candidate, locked) => `<button data-admission-action="modify" ${locked ? 'disabled' : ''}>修改</button>`,
    linkedObjectChecks: () => [{ machineStatus: 'failed' }],
  });
  const html = context.ontologyMarkup({ id: 'report-a' });
  for (const action of ['modify', 'request-more-evidence', 'reject', 'accept']) {
    const button = html.match(new RegExp(`<button[^>]*data-admission-action="${action}"[^>]*>`))[0];
    assert.equal(button.includes('disabled'), false, `${action} remains available for the valid property`);
  }
});

function graphFixture() {
  const graph = { graphRevisionId: 'graph-a', nodes: [
    { id: 'model-a', label: 'Model A', type: 'EquipmentModel', payload: { extractionMethod: 'llm' } },
    { id: 'org-a', label: 'Organization A', type: 'Organization' },
    { id: 'source-a', label: 'Source A', type: 'EvidenceSource' },
    { id: 'trend-a', label: 'Trend A', type: 'TrendFinding' },
  ], edges: [
    { id: 'made', from: 'org-a', to: 'model-a', relation: 'manufactures' },
    { id: 'cited', from: 'source-a', to: 'model-a', relation: 'supports' },
    { id: 'inferred', from: 'model-a', to: 'trend-a', relation: 'supports' },
  ] };
  const state = { graphFilters: { 'report-a': {} } };
  const context = evaluate(section('const GRAPH_CATEGORIES=', 'function graphCanvasMarkup('), {
    state, Set, asArray, graphData: () => graph, assessmentId: value => value.id,
    graphNodeLabel: value => value.label, ontologyAdmissions: () => [],
  });
  return { context, state, graph, assessment: { id: 'report-a' } };
}

test('Partial report reads retain saved results and remain retryable until every endpoint succeeds', async () => {
  const state = { augmentationBusy: {}, graphByAssessment: { 'report-a': { id: 'saved-graph' } },
    validationByAssessment: {}, admissionsByAssessment: { 'report-a': { admissions: [{ id: 'saved-candidate' }] } },
    selectedAssessmentId: 'report-a' };
  let fail = true;
  const context = evaluate(section('async function loadReportAugmentations(', 'async function buildReportGraph('), {
    state, Promise, renderAssessments() {}, renderReview() {}, renderEvidence() {},
    renderExecution() {}, renderImageryEvidence() {},
    setTimeout(callback) { callback(); return 1; },
    maybeAutoStartExtraction() {},
    async requestIntelligence(requestPath) {
      if (requestPath.endsWith('/admissions') && fail) throw new Error('Candidate endpoint unavailable');
      if (requestPath.endsWith('/graph')) return { graph: { id: 'current-graph' } };
      if (requestPath.endsWith('/validation')) return { rounds: [] };
      return { admissions: [{ id: 'current-candidate' }] };
    },
  });
  await context.loadReportAugmentations('report-a');
  assert.equal(state.graphByAssessment['report-a'].id, 'current-graph');
  assert.equal(state.admissionsByAssessment['report-a'].admissions[0].id, 'saved-candidate');
  assert.equal(state.augmentationErrors['report-a'].admissions, 'Candidate endpoint unavailable');
  assert.equal(state.augmentationLoaded['report-a'], undefined);
  fail = false;
  await context.loadReportAugmentations('report-a');
  assert.equal(state.admissionsByAssessment['report-a'].admissions[0].id, 'current-candidate');
  assert.equal(state.augmentationErrors['report-a'].admissions, undefined);
  assert.equal(state.augmentationLoaded['report-a'], true);
});

test('Relation filters show only endpoints and edges for the selected relationship', () => {
  const fixture = graphFixture();
  fixture.state.graphFilters['report-a'].relation = 'manufactures';
  const view = fixture.context.graphView(fixture.assessment);
  assert.deepEqual(Array.from(view.nodes, node => node.id).sort(), ['model-a', 'org-a']);
  assert.deepEqual(Array.from(view.edges, edge => edge.id), ['made']);
});

test('Category and relation filters combine while preserving only matching neighbor context', () => {
  const fixture = graphFixture();
  fixture.state.graphFilters['report-a'] = { type: 'equipment', relation: 'supports' };
  let view = fixture.context.graphView(fixture.assessment);
  assert.deepEqual(Array.from(view.directIds), ['model-a']);
  assert.deepEqual(Array.from(view.nodes, node => node.id), ['model-a', 'source-a', 'trend-a']);
  assert.deepEqual(Array.from(view.edges, edge => edge.id).sort(), ['cited', 'inferred']);
  fixture.state.graphFilters['report-a'].relation = 'missing-relation';
  view = fixture.context.graphView(fixture.assessment);
  assert.equal(view.nodes.length, 0);
  assert.equal(view.edges.length, 0);
});

function inspectorFixture() {
  const attribute = { name: 'test-duration', value: '<30', unit: 'min', validTime: '2024', evidenceRefs: [{ quote: 'test-duration <30 min' }] };
  const node = { id: 'model-a', payload: { attributes: [attribute] } };
  const graph = { graphRevisionId: 'graph-a', nodes: [node] };
  const assessment = { id: 'report-a' }, warnings = [], calls = [];
  const state = { assessments: [assessment], selectedGraphEdgeId: 'old-edge', activeStage: 'graph' };
  const dialog = { open: true, close() { this.open = false; calls.push('close'); } };
  const button = { dataset: { inspectorPropertyIndex: '0', inspectorPropertyNode: 'model-a',
    inspectorPropertyReport: 'report-a', inspectorPropertyRevision: 'graph-a' }, closest: () => dialog };
  const event = { target: { closest: () => button } };
  const context = evaluate(section('function graphObjectAttributeDetailsMarkup(', 'function graphEvidenceMarkup('), {
    state, asArray, esc, assessmentId: value => value.id, graphData: () => graph,
    propertyNameLabel: value => value, fieldText: value => String(value ?? ''),
    objectAttributeRows: value => asArray(value.payload?.attributes).map((attribute, index) => ({ attribute, index })),
    objectAttributeSummary: value => {
      const rows = asArray(value.payload?.attributes).map((attribute, index) => ({ attribute, index }));
      const grounded = rows.filter(row => asArray(row.attribute?.evidenceRefs).some(ref => ref?.quote));
      return { rows, rawCount: rows.length, groundedCount: grounded.length, label: grounded.length ? '属性已保存' : '已提取草案', sourceRefCount: 0 };
    },
    objectAttributeEvidenceCount: row => asArray(row.attribute?.evidenceRefs).filter(ref => ref?.quote).length,
    groundedObjectProperties: value => asArray(value.payload?.attributes).map((attribute, index) => ({ attribute, index })),
    notify: message => warnings.push(message), updateDeepLink: () => calls.push('link'),
    renderReportPanels: () => calls.push('render'), revealEvidence: () => calls.push('evidence'),
  });
  return { context, state, graph, assessment, node, button, dialog, event, warnings, calls };
}

test('Graph node details expose saved attribute values and revision-bound evidence jumps', () => {
  const fixture = inspectorFixture();
  const html = fixture.context.graphObjectAttributeDetailsMarkup(fixture.assessment, fixture.node);
  assert.ok(html.includes('&lt;30 min'));
  assert.ok(html.includes('有效时间：2024'));
  assert.ok(html.includes('data-inspector-property-revision="graph-a"'));
  assert.ok(html.includes('核对属性引文'));
  assert.ok(html.includes('属性状态：已保存原文引文'));
});

test('Graph attribute evidence jump selects the exact property and closes the graph overlay', () => {
  const fixture = inspectorFixture();
  assert.equal(fixture.context.handleInspectorPropertyInteraction(fixture.event), true);
  assert.equal(fixture.state.activeStage, 'extract');
  assert.equal(fixture.state.selectedGraphNodeId, 'model-a');
  assert.equal(fixture.state.selectedGraphEdgeId, null);
  assert.equal(fixture.state.selectedGraphProperty.graphRevisionId, 'graph-a');
  assert.equal(fixture.state.selectedGraphProperty.nodeId, 'model-a');
  assert.equal(fixture.state.selectedGraphProperty.index, 0);
  assert.equal(fixture.dialog.open, false);
  assert.deepEqual(fixture.calls, ['close', 'link', 'render', 'evidence']);
});

test('A stale graph or missing attribute blocks an inspector evidence jump', () => {
  const fixture = inspectorFixture();
  fixture.graph.graphRevisionId = 'graph-b';
  fixture.context.handleInspectorPropertyInteraction(fixture.event);
  assert.equal(fixture.state.activeStage, 'graph');
  assert.equal(fixture.warnings.length, 1);
  fixture.button.dataset.inspectorPropertyRevision = 'graph-b';
  fixture.node.payload.attributes = [];
  fixture.context.handleInspectorPropertyInteraction(fixture.event);
  assert.equal(fixture.state.activeStage, 'graph');
  assert.equal(fixture.warnings.length, 2);
  assert.deepEqual(fixture.calls, []);
});

test('Archived task deep links clear the workspace and remembered task without loading old reports', async () => {
  const archived = { id: 'task-old', title: 'Saved research settings', archivedAt: '2026-10-08T10:00:00Z' };
  const state = { task: { id: archived.id }, tasks: [archived], assessments: [{ id: 'report-old' }],
    selectedAssessmentId: 'report-old', selectedMaterialId: 'material-old', selectedGraphProperty: { nodeId: 'object-old' },
    selectedItems: new Set(['material-old']), activeStage: 'extract', extractionJobTimers: { 'job-old': 42 }, taskLoadToken: 0 };
  const calls = [], requests = [], restored = [];
  const location = { href: 'http://localhost/osint.html?taskId=task-old&assessmentId=report-old&stage=extract' };
  const context = evaluate(section('function resetTaskWorkspace(', 'function stopPolling(')
    + section('function updateDeepLink(', 'function selectAssessment('), {
    state, URL, Set, Object, location, history: { replaceState(_state, _title, url) { location.href = String(url); } },
    unwrapTask: value => value.task, stopPolling() { calls.push('stop'); },
    clearTimeout: timer => calls.push(timer), saveLastTask: id => calls.push(['remember', id]),
    resetImageryForm() {}, renderHistory() {}, renderTask() {}, renderReportPanels() {}, notify() {},
    restoreTaskSettings: task => restored.push(task.title),
    async request(path) { requests.push(path); return { task: archived }; },
    async loadAssessments() { throw new Error('Archived reports must not be hydrated'); },
  });
  await context.loadTask('task-old', 'report-old');
  assert.deepEqual(requests, ['/tasks/task-old']);
  assert.equal(state.task, null);
  assert.equal(state.assessments.length, 0);
  assert.equal(state.tasks.length, 0);
  assert.equal(state.selectedItems.size, 0);
  assert.equal(state.selectedGraphProperty, null);
  assert.equal(state.selectedAssessmentId, null);
  assert.equal(Object.keys(state.extractionJobTimers).length, 0);
  assert.ok(calls.includes(42));
  assert.ok(calls.some(call => Array.isArray(call) && call[0] === 'remember' && call[1] === null));
  assert.equal(location.href, 'http://localhost/osint.html?stage=materials');
  assert.deepEqual(restored, ['Saved research settings']);
});

let passed = 0;
for (const entry of tests) {
  try {
    await entry.run();
    passed++;
    process.stdout.write(`PASS ${entry.name}\n`);
  } catch (error) {
    process.stderr.write(`FAIL ${entry.name}\n${error.stack}\n`);
    process.exitCode = 1;
  }
}
process.stdout.write(`${passed}/${tests.length} workbench UI checks passed.\n`);
