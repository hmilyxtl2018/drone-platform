import test from 'node:test';
import assert from 'node:assert/strict';
import {capabilityMarkup,metricDetail,conflictPeers,conflictReviewMarkup} from './ontology-capability-view.js';
const m={id:'metric-a',attributeKey:'endurance',label:'续航时间',value:0,rawValue:'0 h',unit:'h',qualifier:'exact',lowerBound:null,upperBound:null,conditions:null,variant:null,configuration:null,claimState:'observed',verificationStatus:'unverified',reviewStatus:'pending',origin:'archive',extractionConfidence:.98,evidenceRefs:[],comparability:{eligible:false,reasons:['尚未核验']}};
const profile={entityId:'equipment-a',objectRevision:1,identity:{name:'Test <UAV>',configuration:null},missionRoles:[{label:'侦察',basis:'classification'}],quality:{presentFieldCount:1,expectedFieldCount:38,valueCount:1,referencedCount:0,publicSourceCount:0,verifiedCount:0,conflictCount:0},capabilities:[{id:'equipment-a:platform',code:'platform',label:'飞行与任务持续',metrics:[m],summary:{presentCount:1,unknownCount:0,verifiedCount:0},questions:['配置？'],operationalEffects:[],technicalMechanisms:[],constraints:[]}],lineage:[],insights:[]};
test('zero remains a literal value and model confidence does not label it verified',()=>{const html=capabilityMarkup(profile);assert.match(html,/0 h/);assert.match(html,/未核验/);assert.match(metricDetail(m),/0.98/);assert.match(html,/已核验 <b>0\/1/);});
test('source text is escaped and evidence expands in place',()=>{const evil={...m,rawValue:'<img src=x onerror=alert(1)>',evidenceRefs:[{sourceRef:'javascript:alert(1)',sourceType:'web-page',snapshotMatched:false,quote:'<script>alert(1)</script>',locator:{}}]};const html=metricDetail(evil);assert.doesNotMatch(html,/<script>|href="javascript:/);assert.match(html,/&lt;script&gt;/);assert.match(html,/正文未匹配/);});
test('graph provides zoom and observed values plus evidence categories',()=>{const html=capabilityMarkup(profile,{view:'graph'});assert.match(html,/data-cap-zoom="in"/);assert.match(html,/data-cap-zoom="out"/);assert.match(html,/0 h/);assert.match(html,/Test &lt;UAV&gt;/);});
test('chart view marks inadmissible numbers instead of treating them comparable',()=>{const html=capabilityMarkup(profile,{view:'charts'});assert.match(html,/可比指标 0/);assert.match(html,/不可比/);assert.match(html,/样本|字段覆盖/);});
test('conflict review exposes each source while excluding another configuration',()=>{
  const a={...m,origin:'candidate',claimState:'conflict',verificationStatus:'conflict'};
  const b={...a,id:'metric-b',rawValue:'20 h'},other={...b,id:'metric-c',configuration:'other'};
  const p={...profile,capabilities:[{...profile.capabilities[0],metrics:[a,b,other]}]};
  assert.deepEqual(conflictPeers(p,a).map(m=>m.id),['metric-a','metric-b']);
  const html=conflictReviewMarkup(p,a);assert.match(html,/拟采纳/);assert.match(html,/20 h/);assert.match(html,/cap-conflict-confirm/);
  assert.match(capabilityMarkup(p),/审核来源冲突/);
  assert.equal(conflictReviewMarkup(p,{...a,claimState:'observed'}),'');
});
test('graph renders all stored observations and events instead of two per domain',()=>{
  const metrics=Array.from({length:5},(_,i)=>({...m,id:'metric-'+i,rawValue:i+' h'}));
  const p={...profile,capabilities:[{...profile.capabilities[0],metrics}],graph:{nodes:[{id:'event-1',label:'公开测试事件',type:'Event'},...metrics.map(m=>({id:m.id,label:m.label,type:'CapabilityObservation'}))],edges:[]}};
  const html=capabilityMarkup(p,{view:'graph'});assert.match(html,/公开测试事件/);assert.match(html,/data-cap-record="metric-4"/);assert.match(html,/6 对象/);
});
