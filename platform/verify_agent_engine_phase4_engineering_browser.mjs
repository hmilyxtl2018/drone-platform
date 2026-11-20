// Authenticated browser verification of a real engineering calculation on a
// persisted Run. The calculation deliberately uses missing inputs so the
// server must persist an explicit unknown result without model/provider calls.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');

let token=''; for await (const chunk of process.stdin) token+=chunk; token=token.trim();
const runId='run-beb348292ff36dac';
const report={
  schemaVersion:'phase4-engineering-browser.v1',
  scope:'live local authenticated browser; real persisted Run; missing-input calculation only; no model/provider call',
  runId, passed:false, browserErrors:[], apiResponses:[]
};

const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const page=await context.newPage();
  page.on('pageerror',error=>report.browserErrors.push(error.name+': '+error.message));
  page.on('response',response=>{
    const url=response.url();
    if(url.includes('/api/agent-admin/')) report.apiResponses.push({path:new URL(url).pathname+new URL(url).search,status:response.status()});
  });
  await page.goto('http://127.0.0.1:8090/agent-admin.html#run='+runId);
  await page.locator('#token').fill(token); token='';
  await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state:'visible',timeout:15000});
  await page.getByText(runId,{exact:false}).waitFor({timeout:15000});
  await page.locator('[data-tab="engineering"]').click();
  await page.waitForFunction(()=>document.querySelector('#panel h2')?.textContent?.trim()==='需求与概念工程计算',{timeout:15000});
  const initialText=await page.locator('#panel').innerText();
  report.initial={
    heading:'需求与概念工程计算',
    runVisible:initialText.includes(runId),
    operatorActionVisible:initialText.includes('按缺失输入执行并保存 unknown'),
    unknownBoundaryVisible:initialText.includes('审核符合性：unknown') && initialText.includes('工程认证：false')
  };
  const action=page.getByRole('button',{name:'按缺失输入执行并保存 unknown'}).first();
  if(await action.count()===0) throw new Error('missing-input engineering action is not available');
  const responsePromise=page.waitForResponse(response=>{
    const url=new URL(response.url());
    return response.request().method()==='POST' && url.pathname.endsWith('/engineering-calculations');
  },{timeout:15000});
  await action.click();
  const response=await responsePromise;
  const responseJson=await response.json();
  report.submission={status:response.status(),runId:responseJson.runId,toolKey:responseJson.toolKey,responseStatus:responseJson.status};
  await page.getByText(responseJson.runId,{exact:false}).first().waitFor({timeout:15000});
  // Open every persisted-result disclosure so the browser evidence covers the
  // frozen input, actual Gateway audit, artifact hash and output projection.
  await page.locator('#panel details').evaluateAll(nodes=>nodes.forEach(node=>{node.open=true;}));
  const resultText=await page.locator('#panel').innerText();
  report.result={
    childRunVisible:resultText.includes(responseJson.runId),
    unknownResultVisible:resultText.includes('"status": "unknown"') || resultText.includes('"status":"unknown"') || resultText.includes('unknown'),
    toolAuditVisible:resultText.includes('实际工具调用审计'),
    artifactHashVisible:resultText.includes('概念计算产物及哈希'),
    certificationFalseVisible:resultText.includes('工程认证：false'),
    humanComplianceUnknownVisible:resultText.includes('审核符合性：unknown')
  };
  await page.screenshot({path:'docs/verification/phase4/engineering-browser-console.png',fullPage:true});
  report.apiEvidence={
    parentEngineeringGet:report.apiResponses.some(r=>r.status===200&&r.path.endsWith('/runs/'+runId+'/engineering')),
    calculationPost:report.apiResponses.some(r=>r.status===201&&r.path.endsWith('/runs/'+runId+'/engineering-calculations')),
    calculationRead:report.apiResponses.some(r=>r.status===200&&r.path.endsWith('/engineering-calculations/'+responseJson.runId))
  };
  report.noBrowserErrors=report.browserErrors.length===0;
  report.failedApiResponses=report.apiResponses.filter(r=>r.status>=500);
  report.passed=report.initial.runVisible && report.initial.operatorActionVisible && report.initial.unknownBoundaryVisible &&
    report.submission.status===201 && report.submission.responseStatus==='completed' && report.result.childRunVisible &&
    report.result.unknownResultVisible && report.result.toolAuditVisible && report.result.artifactHashVisible &&
    report.result.certificationFalseVisible && report.result.humanComplianceUnknownVisible && report.apiEvidence.parentEngineeringGet &&
    report.apiEvidence.calculationPost && report.apiEvidence.calculationRead && report.noBrowserErrors && report.failedApiResponses.length===0;
} catch(error) {
  report.error=error.message; process.exitCode=1;
} finally {
  fs.mkdirSync('docs/verification/phase4',{recursive:true});
  fs.writeFileSync('docs/verification/phase4/engineering-browser.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:report.passed,childRunId:report.submission?.runId,browserErrors:report.browserErrors.length,apiResponses:report.apiResponses.length,error:report.error||null}));
  await browser.close();
}
if(!report.passed) process.exitCode=1;
