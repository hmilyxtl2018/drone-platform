/* Versioned research inputs. All identity, hashes and bindings come from API. */
async function renderWorkspaces(panel){
  panel.append(el('h2','研究工作区'),el('p','先保存问题、研判视角与资源预算，再按指定版本运行。补充材料保留为待核验输入。', 'help-note help-flow'));
  const root=el('div'),picker=el('select'),editor=el('div');picker.setAttribute('aria-label','选择研究工作区');
  picker.append(new Option('新建工作区',''));
  const preset=el('button','载入“未来战场研判”模板'); preset.type='button'; preset.onclick=()=>{picker.value='';open({preset:'future-battlefield'}).catch(e=>notice(e.message,true));}; root.append(picker,preset);
  for(const row of await api('/workspaces'))picker.append(new Option(row.title+' · r'+row.current_revision,row.workspace_id));
  root.append(picker,editor);panel.append(root);
  let saved=null,currentHead=0,operationKey=null;
  const field=(parent,label,type='text',value='')=>{const l=el('label',label),i=el(type==='textarea'?'textarea':'input');if(type!=='textarea')i.type=type;i.value=value??'';l.append(i);parent.append(l);return i;};
  const action=(label,fn)=>{const b=el('button',label);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{b.disabled=false;}};return b;};
  async function open(revisionOrOptions){
    const preset=revisionOrOptions&&typeof revisionOrOptions==='object'?revisionOrOptions.preset:null;
    const revision=typeof revisionOrOptions==='number'?revisionOrOptions:null;
    saved=picker.value?await api('/workspaces/'+encodeURIComponent(picker.value)+(revision?'?revision='+revision:'')):null;
    currentHead=saved?.workspace.current_revision||0;operationKey=null;editor.replaceChildren();
    const content=saved?.revision.content_json||{title:'',question:'',entityId:'',requirements:{},materials:[],perspectives:[],resources:{}};
    if(preset==='future-battlefield'){
      const template=await fetch('/future_battlefield_workspace_template.json').then(r=>r.json());
      Object.assign(content,{title:template.name,question:'',requirements:{},materials:template.materials,perspectives:template.perspectives,resources:template.resources,agentKey:template.agentKey,profileVersion:template.profileVersion});
    }
    const readonly=!!saved&&saved.revision.revision!==currentHead;
    if(saved)editor.append(el('p','修订 r'+saved.revision.revision+' · '+saved.revision.content_hash,'muted'));
    if(readonly){const status=el('p','历史修订 · 只读','muted');status.append(miniTip('可运行该版本；编辑请切回最新修订。','help','历史修订'));editor.append(status);}
    const form=el('form');form.onsubmit=e=>e.preventDefault();editor.append(form);
    const title=field(form,'工作区名称','text',content.title),entity=field(form,'实体 ID','text',content.entityId),question=field(form,'研究问题','textarea',content.question);question.rows=4;
    const perspectives=el('section'); perspectives.append(el('h3','研判视角'));
    const perspectiveRows=[];
    for(const value of content.perspectives||[]){
      const row=el('div',undefined,'config-row'); const enabled=el('input'); enabled.type='checkbox'; enabled.checked=value.enabled!==false;
      const name=el('strong',value.name+' · '+value.key); const q=field(row,'核心问题','text',value.question||''); row.prepend(enabled,name); perspectives.append(row); perspectiveRows.push({value,enabled,q});
    }
    form.append(perspectives);
    const resources=el('section'); resources.append(el('h3','资源预算'));
    const maxTools=field(resources,'最大工具调用','number',content.resources?.maxToolCalls??80), maxModels=field(resources,'最大模型调用','number',content.resources?.maxModelCalls??24), maxRuntime=field(resources,'运行时间上限（秒）','number',content.resources?.maxRuntimeSeconds??1800), noProgress=field(resources,'无新增证据停止轮数','number',content.resources?.noNewFindingRounds??2);
    form.append(resources);
    const requirements=el('section');requirements.append(el('h3','结构化需求'));form.append(requirements);const reqRows=[];
    function addRequirement(metric='',value={}){
      const row=el('div',undefined,'input-row');requirements.append(row);
      const key=field(row,'属性','text',metric),op=el('select');op.setAttribute('aria-label','比较方式');for(const name of ['>=','>','<=','<','==','between'])op.append(new Option(name,name));op.value=value.operator||'>=';row.append(op);
      const low=field(row,'数值 / 区间下限','number',value.value??value.range?.min),high=field(row,'区间上限（可选）','number',value.range?.max),unit=field(row,'单位','text',value.unit||''),conditions=field(row,'工况（每行 属性=值）','textarea',Object.entries(value.conditions||{}).map(([k,v])=>k+'='+String(v)).join('\n'));
      for(const i of [low,high])i.step='any';const remove=action('移除',()=>{row.remove();reqRows.splice(reqRows.indexOf(item),1);});row.append(remove);const item={row,key,op,low,high,unit,conditions};reqRows.push(item);
    }
    for(const [metric,value]of Object.entries(content.requirements))addRequirement(metric,value||{});
    form.append(action('添加需求',()=>addRequirement()));
    const materials=el('section');materials.append(el('h3','补充材料（待核验）'));form.append(materials);const materialRows=[];
    function addMaterial(value={}){const row=el('div',undefined,'config-row');materials.append(row);const t=field(row,'材料标题','text',value.title),text=field(row,'材料正文','textarea',value.text);text.rows=4;const item={row,t,text};materialRows.push(item);row.append(action('移除',()=>{row.remove();materialRows.splice(materialRows.indexOf(item),1);}));}
    for(const value of content.materials)addMaterial(value);form.append(action('添加材料',()=>addMaterial()));
    function collect(){const result={title:title.value,entityId:entity.value,question:question.value,requirements:{},materials:materialRows.map(v=>({title:v.t.value,text:v.text.value})),perspectives:perspectiveRows.map(v=>({...v.value,enabled:v.enabled.checked,question:v.q.value})),resources:{maxToolCalls:Number(maxTools.value),maxModelCalls:Number(maxModels.value),maxRuntimeSeconds:Number(maxRuntime.value),noNewFindingRounds:Number(noProgress.value)}};for(const r of reqRows){if(!r.key.value.trim()||Object.hasOwn(result.requirements,r.key.value.trim()))throw Error('需求属性应唯一且非空');if(r.low.value==='')throw Error('请填写需求数值');const conditions={};for(const line of r.conditions.value.split('\n').filter(v=>v.trim())){const at=line.indexOf('=');if(at<1||!line.slice(at+1).trim())throw Error('工况格式为 属性=值');const key=line.slice(0,at).trim();if(Object.hasOwn(conditions,key))throw Error('工况属性重复');conditions[key]=line.slice(at+1).trim();}result.requirements[r.key.value.trim()]={operator:r.op.value,unit:r.unit.value,conditions,...(r.high.value!==''?{range:{min:Number(r.low.value),max:Number(r.high.value)}}:{value:Number(r.low.value)})};}return result;}
    const initially=JSON.stringify(collectSafely());function collectSafely(){try{return collect();}catch{return content;}}
    form.append(action('保存修订',async()=>{const body=collect();const result=saved?await api('/workspaces/'+encodeURIComponent(picker.value),{method:'PUT',body:JSON.stringify({expectedRevision:currentHead,content:body})}):await api('/workspaces',{method:'POST',body:JSON.stringify(body)});if(!saved){picker.append(new Option(body.title,result.workspaceId));picker.value=result.workspaceId;}notice('已保存修订 r'+result.revision);await open();}));
    if(readonly||!hasRole('operator'))for(const control of form.querySelectorAll('input,textarea,select,button'))control.disabled=true;
    if(!saved)return;
    const versions=el('select');versions.setAttribute('aria-label','工作区修订');for(const v of await api('/workspaces/'+encodeURIComponent(picker.value)+'/revisions'))versions.append(new Option('修订 r'+v.revision,v.revision));versions.value=saved.revision.revision;versions.onchange=()=>open(Number(versions.value)).catch(e=>notice(e.message,true));editor.append(el('h3','历史修订'),versions);
    const runs=await api('/workspaces/'+encodeURIComponent(picker.value)+'/runs');const runBox=el('div');editor.append(el('h3','关联运行'),runBox);for(const run of runs){const a=el('a','r'+run.revision+' · '+run.run_id+' · '+run.status);a.href='/agent-admin.html#run='+encodeURIComponent(run.run_id);runBox.append(a,el('br'));}
    if(hasRole('operator')){
      const settings=el('div');editor.append(el('h3','运行已保存的修订'),settings);
      const models=field(settings,'模型调用上限','number','8'),search=field(settings,'搜索上限','number','0'),scrape=field(settings,'抓取上限','number','0');
      const check=(label)=>{const l=el('label',undefined,'check'),i=el('input');i.type='checkbox';l.append(i,document.createTextNode(label));settings.append(l);return i;};
      const collaborate=check('联合证据审查 Agent'),design=check('包含设计与验证建议');
      const prior=el('select');prior.setAttribute('aria-label','前次运行');prior.append(new Option('独立研究（不关联前次运行）',''));for(const r of runs.filter(v=>['completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled'].includes(v.status)&&v.revision<=saved.revision.revision))prior.append(new Option(r.run_id,r.run_id));settings.append(prior);const reason=field(settings,'后续运行理由（选择前次运行时必填）');
      settings.oninput=()=>{operationKey=null;};
      settings.append(action('按此修订提交并入队',async()=>{if(JSON.stringify(collect())!==initially)throw Error('输入已修改，请先保存修订');if(prior.value&&!reason.value.trim())throw Error('请填写后续运行理由');operationKey=operationKey||'workspace-'+crypto.randomUUID();const result=await api('/workspaces/'+encodeURIComponent(picker.value)+'/runs',{method:'POST',body:JSON.stringify({revision:saved.revision.revision,idempotencyKey:operationKey,options:{collaborate:collaborate.checked,includeDesign:design.checked,budgetLimits:{model_calls:Number(models.value),search_calls:Number(search.value),scrape_calls:Number(scrape.value)}},...(prior.value?{previousRunId:prior.value,reason:reason.value}:{})})});notice('运行已入队：'+result.runId);await loadRuns();await open();}));
    }
    if(runs.length>1){const left=el('select'),right=el('select');left.setAttribute('aria-label','比较左侧运行');right.setAttribute('aria-label','比较右侧运行');for(const r of runs){left.append(new Option(r.run_id,r.run_id));right.append(new Option(r.run_id,r.run_id));}right.selectedIndex=runs.length-1;const result=el('div');editor.append(el('h3','历史结果比较'),left,right,action('比较',async()=>{const data=await api('/workspaces/'+encodeURIComponent(picker.value)+'/compare?leftRunId='+encodeURIComponent(left.value)+'&rightRunId='+encodeURIComponent(right.value));result.replaceChildren(el('p','变化的输入：'+(data.changedInputFields.join('、')||'无')+'；报告变化：'+(data.outputChanged?'是':'否')),el('p','历史比较保留各自的来源与审核状态。', 'help-note help-info'),json(data));}),result);}
  }
  picker.onchange=()=>open().catch(e=>notice(e.message,true));await open();
}
