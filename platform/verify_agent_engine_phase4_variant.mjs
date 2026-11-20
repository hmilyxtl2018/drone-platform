// Browser evidence for the provider-free profile/prompt variant comparison.
// The temporary operator token is read from stdin and never written to disk.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require = createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium} = require('playwright');

let token = '';
for await (const chunk of process.stdin) token += chunk;
token = token.trim();
const sourceRunId = 'run-beb348292ff36dac';
const outputJson = 'docs/verification/phase4/evaluation-profile-variant.json';
const outputPng = 'docs/verification/phase4/evaluation-profile-variant-console.png';
const report = {
  scope: 'local Agent Admin evaluation console; deterministic server-defined variants only',
  sourceRunId,
  providerCallsRequired: 0,
  passed: false,
};
const browser = await chromium.launch({headless: true, channel: 'msedge'});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.name + ': ' + error.message));
  await page.goto('http://127.0.0.1:8090/agent-admin.html');
  await page.locator('#token').fill(token);
  token = '';
  await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state: 'visible'});
  await page.getByRole('button', {name: '版本评测'}).click();
  await page.getByText('固定 Profile / Prompt 变体比较', {exact: true}).waitFor();
  const source = page.locator('label', {hasText: '冻结配置与来源的终态 Run ID'}).locator('input');
  await source.fill(sourceRunId);

  const runs = {};
  for (const variant of ['baseline', 'prompt-v2']) {
    const call = page.getByRole('button', {name: '执行变体：' + variant, exact: true});
    const response = page.waitForResponse(r => r.url().endsWith('/api/agent-admin/evaluations/benchmarks/variants') && r.request().method() === 'POST');
    await call.click();
    const payload = await (await response).json();
    const id = payload.evaluation_run_id;
    if (!id) throw new Error('variant response did not include an evaluation Run ID for ' + variant);
    await page.getByText('评测详情：' + id, {exact: true}).waitFor({state: 'visible', timeout: 10000}).catch(() => {});
    runs[variant] = {evaluationRunId: id};
  }

  const filter = page.locator('label', {hasText: '运行列表按来源 Run 筛选'}).locator('input');
  await filter.fill(sourceRunId);
  const refreshResponse = page.waitForResponse(r => r.url().includes('/api/agent-admin/evaluations/runs') && r.request().method() === 'GET');
  await page.getByRole('button', {name: '刷新数据集与运行列表', exact: true}).click();
  await refreshResponse;
  await page.getByRole('heading', {name: '评测运行历史'}).waitFor();
  const left = page.locator('select[aria-label="左侧评测运行"]');
  const right = page.locator('select[aria-label="右侧评测运行"]');
  await left.selectOption(runs.baseline.evaluationRunId);
  await right.selectOption(runs['prompt-v2'].evaluationRunId);
  await page.getByRole('button', {name: '比较冻结版本', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#panel')?.innerText.includes('固定检查通过率：'), null, {timeout: 10000}).catch(async () => {
    report.uiDebugText = await page.locator('#panel').innerText();
    report.uiMessage = await page.locator('#message').innerText();
    throw new Error('UI compare result did not render');
  });
  const visibleText = await page.locator('#panel').innerText();
  await page.screenshot({path: outputPng, fullPage: true});

  const get = async path => page.evaluate(async path => {
    const response = await fetch('/api/agent-admin/evaluations' + path);
    if (!response.ok) throw new Error('view HTTP ' + response.status);
    return response.json();
  }, path);
  const values = {};
  for (const [variant, item] of Object.entries(runs)) values[variant] = await get('/runs/' + encodeURIComponent(item.evaluationRunId));
  const compare = await get('/compare?leftRunId=' + encodeURIComponent(runs.baseline.evaluationRunId) + '&rightRunId=' + encodeURIComponent(runs['prompt-v2'].evaluationRunId));
  report.variantRuns = Object.fromEntries(Object.entries(values).map(([variant, value]) => [variant, {
    evaluationRunId: value.evaluation_run_id,
    datasetId: value.dataset_id,
    datasetHash: value.dataset_hash,
    sourceVersionsHash: value.source_versions_hash,
    profileHash: value.profile_hash,
    promptVersion: value.prompt_version,
    promptHash: value.prompt_hash,
    providerCallsMade: value.providerCallsMade ?? 0,
    factualQuality: value.factualQuality,
    engineeringCertification: value.engineeringCertification,
    resultCount: value.results?.length ?? 0,
  }]));
  report.compare = compare;
  report.ui = {
    variantControlsVisible: visibleText.includes('执行变体：baseline') && visibleText.includes('执行变体：prompt-v2'),
    compareVisible: visibleText.includes('固定检查通过率：') && visibleText.includes('此比较不证明某个提示词导致性能变化'),
    hashesVisible: visibleText.includes('Profile：') && visibleText.includes('提示词版本 / 哈希：') && visibleText.includes('来源版本哈希：'),
    providerCallsZeroVisible: visibleText.includes('本次新增 provider 调用：0'),
    noBrowserErrors: errors.length === 0,
  };
  report.browserErrors = errors;
  const leftValue = report.variantRuns.baseline;
  const rightValue = report.variantRuns['prompt-v2'];
  report.checks = {
    sameDatasetHash: leftValue.datasetHash === rightValue.datasetHash,
    sameSourceVersionsHash: leftValue.sourceVersionsHash === rightValue.sourceVersionsHash,
    differentProfileHash: leftValue.profileHash !== rightValue.profileHash,
    differentPromptHash: leftValue.promptHash !== rightValue.promptHash,
    providerCallsZero: leftValue.providerCallsMade === 0 && rightValue.providerCallsMade === 0,
    sameCaseCount: leftValue.resultCount === 5 && rightValue.resultCount === 5,
    compareDeltaPresent: typeof compare.delta === 'number',
  };
  report.passed = Object.values(report.checks).every(Boolean) && Object.values(report.ui).every(Boolean);
} catch (error) {
  report.error = error.message;
} finally {
  fs.writeFileSync(outputJson, JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({passed: report.passed, outputJson, outputPng, error: report.error || null}));
if (!report.passed) process.exitCode = 1;
