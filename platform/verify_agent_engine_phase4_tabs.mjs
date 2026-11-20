// Authenticated read-only browser sweep against the live local Agent Admin.
// The temporary credential arrives through stdin and is never persisted.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token=''; for await(const chunk of process.stdin) token+=chunk; token=token.trim();
const report={scope:'live local console, read-only tab sweep; temporary test identity; no providers, reviews, publications or writes',runId:'run-beb348292ff36dac',passed:false,tabs:[]};
const specs=[
  ['workspaces','研究工作区',[/\/workspaces$/]],
  ['report','运行报告',[]],
  ['plans','不可变计划历史',[/\/runs\/[^/]+\/plans$/]],
  ['evidence','证据审核工作台',[/\/review-workbench\/runs\/[^/]+$/]],
  ['budget','共享预算与实际模型调用',[/\/runs\/[^/]+\/budget$/]],
  ['events','运行事件',[/\/runs\/[^/]+\/events\?/]],
  ['engineering','需求与概念工程计算',[/\/runs\/[^/]+\/engineering$/]],
  ['traceability','需求与验证追溯',[/\/runs\/[^/]+\/traceability$/]],
  ['knowledge','知识变更与发布',[/\/review-workbench\/runs\/[^/]+$/]],
  ['evaluation','持续评测',[/\/evaluations\/datasets$/]],
  ['operations','运行健康',[/\/operations$/]],
  ['config','Agent Profile 配置与发布',[/\/config\/agents$/]],
  ['identities','用户与访问权限',[/\/identities$/]],
];
async function waitForExpectedApi(rows,start,patterns,tab){
  if(!patterns.length)return;
  const deadline=Date.now()+15000;
  while(Date.now()<deadline){
    if(rows.slice(start).some(row=>row.status>=200&&row.status<400&&patterns.some(pattern=>pattern.test(row.path))))return;
    await new Promise(resolve=>setTimeout(resolve,50));
  }
  throw Error('expected API did not load for tab '+tab+'; observed '+JSON.stringify(rows.slice(start)));
}
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const page=await context.newPage(), errors=[], apiResponses=[];
  page.on('pageerror',error=>errors.push(error.name+': '+error.message));
  page.on('response',response=>{if(response.url().includes('/api/agent-admin/')) apiResponses.push({path:new URL(response.url()).pathname+new URL(response.url()).search,status:response.status()});});
  await page.goto('http://127.0.0.1:8090/agent-admin.html#run='+report.runId);
  await page.locator('#token').fill(token); token='';
  await page.locator('#login-form button').click();
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.getByText(report.runId,{exact:false}).waitFor();
  for (const [tab,title,apiPatterns] of specs) {
    const start=apiResponses.length;
    await page.locator('[data-tab="'+tab+'"]').click();
    await page.waitForFunction(({tab,title})=>{
      const selected=document.querySelector('[data-tab="'+tab+'"]')?.classList.contains('selected');
      const heading=document.querySelector('#panel h2')?.textContent?.trim();
      return selected && heading===title;
    },{tab,title},{timeout:15000});
    await waitForExpectedApi(apiResponses,start,apiPatterns,tab);
    const text=await page.locator('#panel').innerText();
    report.tabs.push({tab,heading:title,loaded:text.includes(title),textLength:text.length,expectedApiPatterns:apiPatterns.map(p=>p.source)});
    if(tab==='evidence'){
      const candidate=page.locator('.candidate-row').filter({hasText:'8661b19a277bbc8172977273'});
      await candidate.getByRole('button',{name:'定位保存的原文',exact:true}).click();
      await page.locator('.review-source-text').waitFor();
      const sourceText=await page.locator('.review-source-text').innerText();
      report.payloadEvidenceVisible={
        internalPayloadCandidate:text.includes('internal-only')&&text.includes('261 kg'),
        externalPayloadCandidate:text.includes('external-only')&&text.includes('227 kg'),
        combinedPayloadCandidate:text.includes('internal-and-external')&&text.includes('488 kg'),
        fuelCapacitySeparatelyLabeled:sourceText.includes('Fuel Capacity: | 575 lb (261 kg) |'),
        payloadCapacitySeparatelyLabeled:sourceText.includes('Payload Capacity: | 575 lb int. (261 kg) <br> 500 lb ext. (227 kg) |'),
        combinedPayloadClaimVisible:sourceText.includes('carries 1,075 lb (488 kg) of internal and external payload'),
        unresolvedDecisionVisible:text.includes('request-evidence')&&text.includes('要求补证'),
        noVerifiedClaim:!text.includes('truthVerified": true')
      };
      await page.screenshot({path:'docs/verification/phase4/payload-review-console.png',fullPage:true});
    }
  }
  report.browserErrors=errors;
  report.failedApiResponses=apiResponses.filter(r=>r.status>=500);
  report.authenticatedApiResponseCount=apiResponses.length;
  report.reportEvidenceVisible=report.tabs.find(t=>t.tab==='evidence').loaded;
  report.engineeringApiMounted=apiResponses.some(r=>r.path.endsWith('/runs/'+report.runId+'/engineering')&&r.status===200);
  report.traceabilityApiMounted=apiResponses.some(r=>r.path.endsWith('/runs/'+report.runId+'/traceability')&&r.status===200);
  report.evaluationApiMounted=apiResponses.some(r=>r.path==='/api/agent-admin/evaluations/datasets'&&r.status===200);
  report.knowledgeApiMounted=apiResponses.some(r=>r.path==='/api/agent-admin/review-workbench/runs/'+report.runId&&r.status===200);
  report.readOnlyRunStillRequiresHumanReview=true;
  report.passed=errors.length===0&&report.failedApiResponses.length===0&&report.tabs.length===specs.length&&report.tabs.every(t=>t.loaded)&&report.engineeringApiMounted&&report.traceabilityApiMounted&&report.evaluationApiMounted&&report.knowledgeApiMounted&&Object.values(report.payloadEvidenceVisible||{}).every(Boolean);
  fs.mkdirSync('docs/verification/phase4',{recursive:true});
  await page.locator('[data-tab="engineering"]').click();
  await page.waitForFunction(()=>document.querySelector('#panel h2')?.textContent?.trim()==='需求与概念工程计算');
  await page.screenshot({path:'docs/verification/phase4/engineering-console.png',fullPage:true});
  if(!report.passed) process.exitCode=1;
  console.log(JSON.stringify({passed:report.passed,tabs:report.tabs.length,browserErrors:errors.length,apiResponses:apiResponses.length}));
} catch(error) {
  report.error=error.message; console.log(JSON.stringify({passed:false,error:error.message})); process.exitCode=1;
} finally {
  fs.writeFileSync('docs/verification/phase4/console-tabs-live.json',JSON.stringify(report,null,2));
  await browser.close();
}
