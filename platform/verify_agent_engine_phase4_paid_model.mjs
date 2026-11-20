// Real local browser acceptance for the one-call paid DeepSeek evaluation.
// The token is read from stdin only and is never written to disk or output.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token=''; for await(const chunk of process.stdin) token+=chunk; token=token.trim();
const runId=process.env.AGENT_EVALUATION_TEST_SOURCE_RUN || 'run-beb348292ff36dac';
const baseUrl=process.env.AGENT_ADMIN_TEST_BASE_URL || 'http://127.0.0.1:8090';
const report={scope:'real local API and browser; one fresh DeepSeek V4 Pro quality evaluation; source Run remains unmodified and no knowledge publication',sourceRunId:runId,passed:false,browserErrors:[],apiResponses:[]};
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const page=await context.newPage();
  page.on('pageerror',error=>report.browserErrors.push(error.name+': '+error.message));
  page.on('response',response=>{if(response.url().includes('/api/agent-admin/')) report.apiResponses.push({method:response.request().method(),path:new URL(response.url()).pathname,status:response.status()});});
  await page.goto(baseUrl+'/agent-admin.html#run='+encodeURIComponent(runId));
  await page.locator('#token').fill(token); token='';
  await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('[data-tab="evaluation"]').click();
  await page.waitForFunction(()=>document.querySelector('#panel h2')?.textContent?.trim()==='持续评测');
  await page.getByLabel('冻结配置与来源的终态 Run ID').fill(runId);
  const responsePromise=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/agent-admin/evaluations/paid-model');
  await page.getByRole('button',{name:/执行一次 DeepSeek V4 Pro 付费质量评测/}).click();
  const response=await responsePromise;
  report.httpStatus=response.status();
  const body=await response.json();
  report.evaluationRunId=body.evaluation_run_id;
  report.executionMode=body.execution_mode;
  report.evaluationMeaning=body.evaluationMeaning;
  report.providerCallsMade=body.providerCallsMade;
  report.actualModel=body.actualModel;
  report.inputTokens=body.inputTokens;
  report.outputTokens=body.outputTokens;
  report.factualQuality=body.factualQuality;
  report.engineeringCertification=body.engineeringCertification;
  report.resultCount=(body.results||[]).length;
  report.caseResults=(body.results||[]).map(item=>({caseId:item.case_id,deterministicStatus:item.deterministic_status,deterministicCorrect:item.deterministic_correct,citationStatus:item.citation_status,convergenceStatus:item.convergence_status,authorizationStatus:item.authorization_status}));
  report.deterministicCorrectCount=(body.results||[]).filter(item=>item.deterministic_correct===true).length;
  report.allDeterministicCorrect=report.deterministicCorrectCount===report.resultCount;
  report.qualityFinding=report.allDeterministicCorrect?'all frozen checks passed':'model output contains deterministic mismatches; quality result is retained for review and is not treated as factual or engineering certification';
  report.payloadDecision=(body.results||[]).find(item=>item.actual_output_json?.caseKey==='payload-definition-review')?.actual_output_json || null;
  if(response.status()!==201) throw Error('paid-model endpoint failed: '+response.status());
  await page.getByText(new RegExp('评测详情：'+body.evaluation_run_id.replace(/[.*+?^${}()|[\\]\\]/g,'\\$&'))).waitFor();
  report.detailText=await page.locator('#panel').innerText();
  await page.screenshot({path:'docs/verification/phase4/evaluation-paid-model-console.png',fullPage:true});
  report.passed=report.httpStatus===201&&report.executionMode==='paid_model'&&report.evaluationMeaning==='fresh-paid-deepseek-v4-pro-quality-evaluation'&&report.providerCallsMade===1&&report.actualModel==='deepseek-v4-pro'&&report.resultCount===6&&report.factualQuality==='unknown'&&report.engineeringCertification===false&&report.browserErrors.length===0;
  if(!report.passed) process.exitCode=1;
} catch(error) { report.error=error.message; process.exitCode=1; }
finally { fs.mkdirSync('docs/verification/phase4',{recursive:true}); fs.writeFileSync('docs/verification/phase4/evaluation-paid-model.json',JSON.stringify(report,null,2)); console.log(JSON.stringify({passed:report.passed,httpStatus:report.httpStatus,evaluationRunId:report.evaluationRunId,providerCallsMade:report.providerCallsMade,actualModel:report.actualModel,browserErrors:report.browserErrors.length})); await browser.close(); }
