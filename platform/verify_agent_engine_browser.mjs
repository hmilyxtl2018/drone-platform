// Live, capped paid acceptance: actual console UI -> PostgreSQL worker -> providers.
// A temporary test reviewer exercises request-evidence; this is no factual approval.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token='';for await(const chunk of process.stdin)token+=chunk;token=token.trim();
const folder='docs/verification/phase3';
const report={scope:'live console and automated test identity; no human factual approval',passed:false};
const browser=await chromium.launch({headless:true,channel:'msedge'});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.name+': '+e.message));
 await page.goto('http://127.0.0.1:8090/agent-admin.html');
 await page.locator('#token').fill(token);token='';
 await page.locator('#login-form button').click();
 await page.locator('#workspace').waitFor({state:'visible'});
 if(!process.env.AGENT_ACCEPTANCE_RUN_ID){
 await page.locator('#entity').fill('equipment-ff2d751e27858b020dd2');
 await page.locator('#question').fill('Assess MQ-1C Gray Eagle endurance, payload and cruise speed. Cruise speed is missing: use one targeted Firecrawl search and at most one known manufacturer page. Never replace cruise speed with maximum speed. Deliver a conceptual design and independent evidence review; missing engineering inputs remain explicit data requests.');
 await page.locator('#metrics').fill('enduranceHours,payloadKg,cruiseSpeedMps');
 await page.locator('#model-budget').fill('10');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/agent-admin/runs')&&r.request().method()==='POST');
 await page.locator('#create-form button').click();
 const submitted=await response;report.submissionStatus=submitted.status();
 const result=await submitted.json();if(submitted.status()!==202)throw Error('UI submission rejected: '+JSON.stringify(result));
 report.runId=result.runId;console.log(JSON.stringify({runId:result.runId,status:'submitted-through-ui'}));
 }else{
 report.runId=process.env.AGENT_ACCEPTANCE_RUN_ID;report.submissionStatus=202;
 report.reusedRun='previously submitted through this driver UI';
 await page.goto('http://127.0.0.1:8090/agent-admin.html#run='+report.runId);
 }
 const get=path=>page.evaluate(async path=>{const r=await fetch('/api/agent-admin'+path);if(!r.ok)throw Error('view HTTP '+r.status);return r.json();},path);
 const terminal=new Set(['completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled']);
 let ids=[report.runId],complete=false;
 for(let round=0;round<180;round++){
   const runs=[];for(let i=0;i<ids.length;i++){
    const id=ids[i],r=await get('/runs/'+id),hs=await get('/runs/'+id+'/handoffs');
    runs.push({runId:id,agentKey:r.run.agent_key,status:r.run.status,error:r.run.error_message});
    for(const h of hs)if(!ids.includes(h.childRunId))ids.push(h.childRunId);
   }
   if(runs.some(r=>['failed','cancelled','blocked'].includes(r.status))){report.runs=runs;throw Error('business Run did not deliver');}
   if(ids.length===3&&runs.length===3&&runs.every(r=>terminal.has(r.status))){report.runs=runs;complete=true;break;}
   await new Promise(resolve=>setTimeout(resolve,5000));
 }
 if(!complete)throw Error('three-Agent chain did not finish within probe limit');
 report.budget=await get('/runs/'+report.runId+'/budget');
 report.calls=[];report.artifacts=[];
 for(const id of ids){report.calls.push(...await get('/runs/'+id+'/model-calls'));report.artifacts.push(...(await get('/runs/'+id+'/artifacts')).map(a=>({runId:id,type:a.artifact_type,hash:a.content_hash})));}
 if(report.calls.some(c=>c.requested_model!=='deepseek-v4-pro'||c.actual_model!=='deepseek-v4-pro'))throw Error('model mismatch');
 for(const type of ['Assessment','DesignBrief','DataRequestList','VerificationPlan','ReviewPackage','TraceabilityMatrix'])if(!report.artifacts.some(a=>a.type===type))throw Error('missing role artifact '+type);
 await page.reload();await page.locator('#workspace').waitFor({state:'visible'});
 await page.waitForFunction(id=>document.getElementById('run-title').textContent.includes(id),report.runId);
 await page.locator('[data-tab="evidence"]').click();
 await page.locator('.candidate').first().waitFor();
 page.once('dialog',dialog=>dialog.accept('Automated acceptance test: request additional condition-matched original material; this is not a human truth approval.'));
 const reviewResponse=page.waitForResponse(r=>r.url().endsWith('/reviews')&&r.request().method()==='POST');
 await page.locator('.candidate').first().getByRole('button',{name:'要求补证',exact:true}).click();
 const review=await reviewResponse;report.testReviewStatus=review.status();
 if(!review.ok())throw Error('request-evidence review failed');
 report.testReview=await review.json();
 await page.screenshot({path:folder+'/console-evidence.png',fullPage:true});
 report.browserErrors=errors;report.humanFactualApproval='pending';
 report.passed=errors.length===0;
 console.log(JSON.stringify({runId:report.runId,passed:report.passed,roles:report.runs.length,humanFactualApproval:'pending'}));
}catch(error){report.error=error.message;console.log(JSON.stringify({passed:false,error:error.message}));process.exitCode=1;}
finally{fs.writeFileSync(folder+'/live-console.json',JSON.stringify(report,null,2));await browser.close();}
