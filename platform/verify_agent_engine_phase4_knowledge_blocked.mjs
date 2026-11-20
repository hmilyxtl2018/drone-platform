// Real local browser acceptance for the P4-05 evidence gate.
// The source Run is real; this script attempts no publication and writes no review.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token=''; for await(const chunk of process.stdin) token+=chunk; token=token.trim();
const runId=process.env.AGENT_EVALUATION_TEST_SOURCE_RUN || 'run-beb348292ff36dac';
const candidateId=process.env.AGENT_KNOWLEDGE_TEST_CANDIDATE || 'candidate-8661b19a277bbc8172977273';
const baseUrl=process.env.AGENT_ADMIN_TEST_BASE_URL || 'http://127.0.0.1:8090';
const report={scope:'real local source Run and browser; P4-05 unresolved-evidence gate; no review, change-set or publication writes',runId,candidateId,passed:false,browserErrors:[]};
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
 const context=await browser.newContext({viewport:{width:1440,height:1100}});
 const page=await context.newPage();
 page.on('pageerror',error=>report.browserErrors.push(error.name+': '+error.message));
 await page.goto(baseUrl+'/agent-admin.html#run='+encodeURIComponent(runId));
 await page.locator('#token').fill(token); token='';
 await page.locator('#login-form button').click();
 await page.locator('#workspace').waitFor({state:'visible'});
 await page.locator('[data-tab="knowledge"]').click();
 await page.waitForFunction(()=>document.querySelector('#panel h2')?.textContent?.trim()==='知识变更与发布');
 // The workspace is revealed before the initial hash-selected Run finishes
 // loading.  Waiting only for the panel heading can therefore capture the
 // transient run-less render and produce a false failure.  Wait for the
 // selected Run header and the knowledge pane's own Run marker to settle.
 await page.waitForFunction((id)=>{
   const title=document.querySelector('#run-title')?.textContent||'';
   const panel=document.querySelector('#panel')?.textContent||'';
   return title.includes(id) && panel.includes('运行：'+id);
 }, runId);
 report.debugLocationHash=await page.evaluate(()=>location.hash);
 report.debugRunTitle=await page.locator('#run-title').textContent();
 // `state` is intentionally module-local in the admin page; the settled
 // header is the observable Run context used by this browser acceptance.
 report.debugStateRunId=report.debugRunTitle.includes(runId)?runId:null;
 const panelText=await page.locator('#panel').innerText();
 const checkboxes=page.locator('#panel input[type="checkbox"]');
 const count=await checkboxes.count();
 let disabled=0; for(let i=0;i<count;i++) if(await checkboxes.nth(i).isDisabled()) disabled++;
 report.page={heading:'知识变更与发布',candidateCheckboxCount:count,disabledCandidateCheckboxes:disabled,
   allCandidatesDisabled:count>0&&disabled===count,requestEvidenceVisible:panelText.includes('request-evidence'),
   noPublicationClaim:!panelText.includes('已发布知识版本')};
 const before=await page.evaluate(async streamKey=>({
   changes:await (await fetch('/api/agent-admin/knowledge/change-sets?streamKey='+encodeURIComponent(streamKey))).json(),
   publications:await (await fetch('/api/agent-admin/knowledge/publications?streamKey='+encodeURIComponent(streamKey))).json()
 }), 'run:'+runId);
 report.before={changeSets:before.changes.length,publications:before.publications.length};
 const attempted=await page.evaluate(async ({runId,candidateId})=>{
   const response=await fetch('/api/agent-admin/knowledge/runs/'+encodeURIComponent(runId)+'/change-sets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({streamKey:'run:'+runId,candidateIds:[candidateId],reason:'Verify unresolved evidence blocks knowledge proposal',idempotencyKey:'phase4-blocked-gate-'+candidateId})});
   return {status:response.status,body:await response.text()};
 },{runId,candidateId});
 report.serverProposalAttempt={status:attempted.status,bodyRedacted:attempted.body};
 const after=await page.evaluate(async streamKey=>({
   changes:await (await fetch('/api/agent-admin/knowledge/change-sets?streamKey='+encodeURIComponent(streamKey))).json(),
   publications:await (await fetch('/api/agent-admin/knowledge/publications?streamKey='+encodeURIComponent(streamKey))).json()
 }), 'run:'+runId);
 report.after={changeSets:after.changes.length,publications:after.publications.length};
 await page.screenshot({path:'docs/verification/phase4/knowledge-blocked-console.png',fullPage:true});
 report.passed=report.browserErrors.length===0&&report.page.allCandidatesDisabled&&report.page.requestEvidenceVisible&&report.serverProposalAttempt.status===409&&report.before.changeSets===0&&report.before.publications===0&&report.after.changeSets===0&&report.after.publications===0;
 if(!report.passed)process.exitCode=1;
} catch(error){report.error=error.message;process.exitCode=1;}
finally{fs.mkdirSync('docs/verification/phase4',{recursive:true});fs.writeFileSync('docs/verification/phase4/knowledge-blocked.json',JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,serverStatus:report.serverProposalAttempt?.status,before:report.before,after:report.after,browserErrors:report.browserErrors.length}));await browser.close();}
