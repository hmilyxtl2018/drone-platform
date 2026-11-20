// Run with the worker stopped. Verify UI creation then cancel before paid execution.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token='';for await(const part of process.stdin)token+=part;
const previous=process.env.AGENT_ACCEPTANCE_RUN_ID;
if(!previous)throw Error('select a previously completed research Run');
const browser=await chromium.launch({headless:true,channel:'msedge'});
const report={passed:false,scope:'real UI follow-up creation/cancellation; worker stopped; no paid calls',previousRunId:previous};
let page,runId;
try{
 page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8090/agent-admin.html#run='+encodeURIComponent(previous));
 await page.locator('#token').fill(token.trim());token='';await page.locator('#login-form button').click();
 await page.locator('#followup-section').waitFor({state:'visible'});
 const api=(path,body)=>page.evaluate(async({path,body})=>{const r=await fetch('/api/agent-admin'+path,
   body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
   if(!r.ok)throw Error('API '+r.status);return r.json();},{path,body});
 const old=await api('/runs/'+previous);
 await page.locator('#followup-material').fill('Automated UI creation test: request original condition-matched payload measurements. Context remains unverified.');
 const response=page.waitForResponse(r=>r.url().endsWith('/followups')&&r.request().method()==='POST');
 await page.locator('#followup-form button').click();const submitted=await response;report.submissionStatus=submitted.status();
 const result=await submitted.json();runId=result.runId;report.runId=runId;if(!runId||submitted.status()!==202)throw Error('follow-up rejected');
 await api('/runs/'+runId+'/cancel',{});
 const next=await api('/runs/'+runId),after=await api('/runs/'+previous),budget=await api('/runs/'+runId+'/budget');
 const reviews=await api('/runs/'+runId+'/reviews');report.budget=budget;report.status=next.run.status;
 if(next.run.input_json.previousRunId!==previous||budget.root_run_id!==runId)throw Error('link or budget root mismatch');
 if(next.run.model_call_count!==0||reviews.length!==0)throw Error('follow-up executed or copied approvals');
 if(JSON.stringify(old.run.output_json)!==JSON.stringify(after.run.output_json))throw Error('previous output changed');
 report.previousOutputUnchanged=true;report.priorReviewsNotCopied=true;report.modelCalls=0;report.browserErrors=errors;
 report.passed=errors.length===0&&next.run.status==='cancelled';
}catch(error){report.error=error.message;process.exitCode=1;}
finally{
 if(runId&&page){try{await page.evaluate(async id=>fetch('/api/agent-admin/runs/'+id+'/cancel',{method:'POST'}),runId);}catch{}}
 fs.writeFileSync('docs/verification/phase3/console-followup.json',JSON.stringify(report,null,2));
 await browser.close();console.log(JSON.stringify(report));
}
