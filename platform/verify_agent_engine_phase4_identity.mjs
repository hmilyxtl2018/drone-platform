// Real local UI and database acceptance; no model calls or factual reviews.
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire('C:/Users/hmily/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const {chromium}=require('playwright');
let token='';for await(const chunk of process.stdin)token+=chunk;token=token.trim();
const folder='docs/verification/phase4';fs.mkdirSync(folder,{recursive:true});
const report={scope:'temporary test identities; no provider calls or human factual review',passed:false};
const browser=await chromium.launch({headless:true,channel:'msedge'});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',error=>errors.push(error.name+': '+error.message));
 await page.goto('http://127.0.0.1:8090/agent-admin.html');
 report.firstLoginHelp=await page.getByText('第一次登录，如何取得令牌？',{exact:true}).isVisible();
 await page.locator('#token').fill(token);token='';
 await page.locator('#login-form button').click();await page.locator('#workspace').waitFor({state:'visible'});
 await page.locator('[data-tab="identities"]').click();
 await page.getByRole('heading',{name:'用户与访问权限',exact:true}).waitFor();
 await page.getByLabel('新用户名称',{exact:true}).fill(process.env.AGENT_IDENTITY_PROBE_NAME);
 await page.getByLabel('创建理由',{exact:true}).fill('Automated Phase 4 identity UI acceptance fixture');
 const issuedResponse=page.waitForResponse(r=>r.url().endsWith('/identities')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'创建用户并签发一次性可见令牌',exact:true}).click();
 const issued=await issuedResponse;report.creationStatus=issued.status();
 if(issued.status()!==201)throw Error('identity issuance failed');
 report.issuanceNotCached=issued.headers()['cache-control']==='no-store';
 await page.getByRole('button',{name:'清除显示',exact:true}).click();
 const block=page.locator('.config-row').filter({has:page.getByText(process.env.AGENT_IDENTITY_PROBE_NAME,{exact:true})});
 await block.waitFor();
 await block.getByLabel('审核',{exact:true}).uncheck();
 page.once('dialog',d=>d.accept('Automated test: restrict fixture to operator'));
 const updated=page.waitForResponse(r=>r.url().includes('/identities/')&&r.request().method()==='PATCH');
 await block.getByRole('button',{name:'保存角色',exact:true}).click();
 report.roleChangeStatus=(await updated).status();
 await page.waitForFunction(name=>Array.from(document.querySelectorAll('.config-row')).some(row=>row.querySelector('strong')?.textContent===name&&!Array.from(row.querySelectorAll('input[type="checkbox"]')).find(box=>box.value==='reviewer')?.checked),process.env.AGENT_IDENTITY_PROBE_NAME);
 page.once('dialog',d=>d.accept('Automated test: revoke fixture access'));
 const revoked=page.waitForResponse(r=>r.url().includes('/identity-tokens/')&&r.url().endsWith('/revoke'));
 await block.getByRole('button',{name:'撤销',exact:true}).click();report.revocationStatus=(await revoked).status();
 await block.getByText(/revoked/).waitFor();
 const metadata=await page.evaluate(async()=>{
   const responses=await Promise.all(['/identities','/identity-tokens','/identity-audit'].map(path=>fetch('/api/agent-admin'+path)));
   return {bodies:await Promise.all(responses.map(r=>r.text())),statuses:responses.map(r=>r.status)};
 });
 report.metadataStatuses=metadata.statuses;
 report.metadataContainsNoCredentials=metadata.bodies.every(body=>!body.includes('aat_')&&!body.includes('token_hash'));
 report.browserStorageContainsNoToken=await page.evaluate(()=>![...Object.values(localStorage),...Object.values(sessionStorage)].some(v=>v.includes('aat_')));
 await page.screenshot({path:folder+'/identity-console.png',fullPage:false});
 await page.goto('http://127.0.0.1:8090/agent-admin.html#run=run-beb348292ff36dac');
 await page.waitForFunction(()=>document.getElementById('run-title').textContent.includes('run-beb348292ff36dac'));
 await page.locator('[data-tab="evidence"]').click();await page.locator('.candidate').first().waitFor();
 report.existingReportEvidenceVisible=true;
 await page.screenshot({path:folder+'/existing-evidence-console.png',fullPage:true});
 report.browserErrors=errors;
 report.passed=report.firstLoginHelp&&report.issuanceNotCached&&report.roleChangeStatus===200&&report.revocationStatus===200&&report.metadataContainsNoCredentials&&report.browserStorageContainsNoToken&&metadata.statuses.every(status=>status===200)&&errors.length===0;
 if(!report.passed)process.exitCode=1;
 console.log(JSON.stringify({passed:report.passed,scope:report.scope}));
}catch(error){report.error=error.message;console.log(JSON.stringify({passed:false,error:error.message}));process.exitCode=1;}
finally{fs.writeFileSync(folder+'/identity-console.json',JSON.stringify(report,null,2));await browser.close();}
