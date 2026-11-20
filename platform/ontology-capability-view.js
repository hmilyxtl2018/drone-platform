const esc = (v) => String(v ?? "未知").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const metricValue = (m) => m.claimState === 'unknown' ? '未知' : `${m.rawValue ?? '未知'}${m.unit && /^[-+]?\d+(\.\d+)?$/.test(m.rawValue ?? '') ? ' '+m.unit : ''}`;
const stateName = (m) => ({verified:'已核验',conflict:'来源冲突',unknown:'待补证',rejected:'已拒绝'}[m.verificationStatus] || (m.origin === 'candidate' ? '待审核候选' : '未核验'));
const link = (ref) => /^https?:\/\//i.test(ref.sourceRef) ? `<a href="${esc(ref.sourceRef)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>` : `<span>${esc(ref.sourceRef)}</span>`;
export function metricDetail(m) {
  return `<div class="cap-record-detail"><span>属性键 <code>${esc(m.attributeKey)}</code></span><span>规范值 ${esc(m.value)} ${esc(m.unit || '')}</span><span>限定 ${esc(m.qualifier)} · ${esc(m.lowerBound)} / ${esc(m.upperBound)}</span><span>改型 ${esc(m.variant)} · 配置 ${esc(m.configuration)}</span><span>条件 ${esc(m.conditions)}</span><span>模型提取置信度 ${m.extractionConfidence == null ? '未记载' : esc(m.extractionConfidence)}${m.importConfidence != null ? ` · 导入评分 ${esc(m.importConfidence)}` : ''} · ${stateName(m)}</span><span>比较 ${m.comparability.eligible ? '符合条件' : esc(m.comparability.reasons.join('；'))}</span>${m.evidenceRefs.map(r => `<div class="cap-source">${link(r)} <small>${r.sourceType === 'workbook' ? `Excel · ${esc(r.locator.sheetName)} ${esc(r.locator.cellRange)}` : r.snapshotMatched ? '正文匹配' : '正文未匹配'}</small><q>${esc(r.quote)}</q></div>`).join('')}${m.missingReason ? `<span>${esc(m.missingReason)}</span>` : ''}</div>`;
}
function metricRow(m) {
  return `<details class="cap-record" id="record-${esc(m.id)}"><summary><b>${esc(m.label)}</b><span title="${esc(metricValue(m))}">${esc(metricValue(m))}</span><span title="${esc(m.conditions)}">${esc(m.conditions)}</span><small>${m.evidenceRefs.length} 引用</small><i class="${m.verificationStatus}">${stateName(m)}</i></summary>${metricDetail(m)}${m.origin === 'candidate' && !['unknown','rejected'].includes(m.claimState) && m.reviewStatus!=='published' ? `<button class="mini-action" data-cap-review="${esc(m.id)}">${m.claimState==='conflict'?'审核来源冲突':'审核此值'}</button>` : ''}</details>`;
}
export function conflictPeers(profile,record) {
  return profile.capabilities.flatMap(c=>c.metrics).filter(m=>m.claimState!=='unknown' && ['attributeKey','variant','configuration','conditions'].every(k=>m[k]===record[k]));
}
export function conflictReviewMarkup(profile,record) {
  if(record.claimState!=='conflict')return '';
  return `<div class="cap-conflict-review"><b>同一属性与条件下的来源值</b>${conflictPeers(profile,record).map(m=>`<details ${m.id===record.id?'open':''}><summary><strong>${m.id===record.id?'拟采纳':'对照值'}</strong><span>${esc(metricValue(m))}</span><small>${m.evidenceRefs.length} 引用</small></summary>${metricDetail(m)}</details>`).join('')}<label><input id="cap-conflict-confirm" type="checkbox">我已对照全部来源值；采纳当前值，其余值保留为历史出处</label><label>冲突处置依据<textarea id="cap-conflict-reason" placeholder="说明采用此来源的依据及对其他来源的处置"></textarea></label></div>`;
}
export function capabilityMarkup(p, {group = 'all',query = '',view = 'profile'} = {}) {
  const q = p.quality;
  const stats = `<div class="cap-quality"><span title="能力注册表口径，不等同于装备档案字段">能力字段 <b>${q.presentFieldCount}/${q.expectedFieldCount}</b></span><span title="含 Excel 单元格出处，排除已拒绝的历史值">出处引用 <b>${q.referencedCount}/${q.valueCount}</b></span><span title="必须匹配已保存网页正文">网页支持 <b>${q.publicSourceCount}/${q.valueCount}</b></span><span title="经逐条审核与发布">已核验 <b>${q.verifiedCount}/${q.valueCount}</b></span>${q.conflictCount ? `<span class="conflict">冲突 <b>${q.conflictCount}</b></span>` : ''}${q.historicalRejectedCount ? `<span>历史排除 <b>${q.historicalRejectedCount}</b></span>` : ''}<small>版本 ${p.objectRevision} · 配置 ${esc(p.identity.configuration)}</small></div>`;
  const views = `<div class="cap-view-tabs">${[['profile','能力画像'],['graph','关联图谱'],['charts','指标与演化'],['insights','事实与验证需求']].map(([id,label])=>`<button data-cap-view="${id}" aria-selected="${view === id}">${label}</button>`).join('')}</div>`;
  if (view === 'graph') return stats+views+graphMarkup(p);
  if (view === 'charts') return stats+views+chartMarkup(p);
  if (view === 'insights') return stats+views+`<div class="cap-insights">${p.insights.filter(i=>`${i.statement} ${i.kind}`.toLowerCase().includes(query.toLowerCase())).map(i=>`<details><summary><b>${esc({fact:'来源事实',hypothesis:'待验证假设',dependency:'技术依赖','verification-need':'验证需求'}[i.kind])}</b><span>${esc(i.statement)}</span><small>${i.supportingRecordIds.length} 依据</small></summary><div>${i.limitations.map(t=>`<p>${esc(t)}</p>`).join('')}${i.supportingRecordIds.map(id=>{const m=p.capabilities.flatMap(c=>c.metrics).find(m=>m.id===id);return `<button class="mini-action" data-cap-record="${esc(id)}">${esc(m?.label)}</button>`;}).join('')}</div></details>`).join('')}</div>`;
  const tabs = `<div class="cap-domains"><button data-cap-group="all" aria-selected="${group === 'all'}">概览</button>${p.capabilities.map(c=>`<button data-cap-group="${c.code}" aria-selected="${group === c.code}">${esc(c.label)} <small>${c.summary.presentCount}</small></button>`).join('')}</div>`;
  const identity = `<div class="cap-role"><b>任务角色</b> ${p.missionRoles.map(r=>`${esc(r.label)} <small>${r.basis === 'classification' ? '分类标签 · 待任务效果核验' : '来源记载'}</small>`).join(' · ')}</div>`;
  const groups = p.capabilities.filter(c=>group === 'all' || group === c.code);
  const body = groups.map(c=>{
    const records=c.metrics.filter(m=>`${m.label} ${m.attributeKey} ${m.rawValue ?? ''} ${m.missingReason ?? ''}`.toLowerCase().includes(query.toLowerCase()));
    if (!records.length) return '';
    const supported=(label,items)=>`<div class="cap-chain-row"><b>${label}</b><span>${items.length ? items.map(s=>esc(s.text)).join('；') : '公开材料尚未提供明确证据'}</span></div>`;
    return `<details class="cap-domain" ${group !== 'all' || query ? 'open' : ''}><summary><b>${esc(c.label)}</b><span>${c.metrics.filter(m=>m.claimState !== 'unknown').slice(0,3).map(m=>`${esc(m.label)} ${esc(metricValue(m))}`).join(' · ') || '指标待补证'}</span><small>有值 ${c.summary.presentCount} · 未知 ${c.summary.unknownCount} · 已核验 ${c.summary.verifiedCount}</small></summary><div class="cap-chain">${supported('任务效果',c.operationalEffects)}${supported('技术机制',c.technicalMechanisms)}${supported('条件限制',c.constraints)}</div><div class="cap-record-head"><span>指标</span><span>来源值</span><span>适用条件</span><span>出处</span><span>核验状态</span></div>${records.map(metricRow).join('')}<div class="cap-questions">${esc(c.questions.join('；'))}</div></details>`;
  }).join('');
  return stats+views+identity+tabs+body;
}

function chartMarkup(p) {
  const metrics=p.capabilities.flatMap(c=>c.metrics);
  const numeric=metrics.filter(m=>typeof m.value==='number' && m.claimState!=='unknown' && m.qualifier==='exact');
  const included=numeric.filter(m=>m.comparability.eligible);
  const fields=[...new Map(metrics.filter(m=>m.unit || m.attributeKey==='endurance').map(m=>[m.attributeKey,m])).values()];
  return `<div class="cap-chart-header"><b>可比指标 ${included.length} · 排除 ${metrics.length-included.length}</b><small>按单位、配置、条件分别比较；未知值不补零</small></div><div class="cap-chart-list">${numeric.map(m=>`<details><summary><b>${esc(m.label)}</b><span>${esc(m.value)} ${esc(m.unit || '')}</span><i>${m.comparability.eligible ? '可比' : '不可比'}</i></summary>${metricDetail(m)}</details>`).join('') || '<p class="empty">尚无可绘图数值。</p>'}</div><div class="cap-chart-header"><b>能力字段覆盖</b><small>图表示数据完整性，不能解读为任务效果</small></div>${p.capabilities.map(c=>`<div class="cap-coverage-row"><b>${esc(c.label)}</b><meter min="0" max="${c.metrics.length}" value="${c.summary.presentCount}" aria-label="${esc(c.label)}字段覆盖"></meter><span>${c.summary.presentCount}/${c.metrics.length}</span></div>`).join('')}<div class="cap-chart-header"><b>型号沿袭与能力演化</b><label class="cap-evolution-tools"><select data-cap-evolution-field aria-label="沿袭对比指标">${fields.map(m=>`<option value="${esc(m.attributeKey)}" ${m.attributeKey==='endurance'?'selected':''}>${esc(m.label)}</option>`).join('')}</select><button class="mini-action" data-cap-evolution>查看指标与版本</button></label></div><div id="cap-evolution-result">${p.lineage.map(e=>`<div class="cap-lineage">${esc(e.predecessor_name)} → ${esc(e.successor_name)} <small>${esc(e.family_name)} · ${esc(e.review_status)} · 沿袭不能单独证明性能提升</small></div>`).join('') || '<p class="empty">未登记沿袭关系。</p>'}</div>`;
}

function graphMarkup(p) {
  const metrics=new Map(p.capabilities.flatMap(c=>c.metrics).map(m=>[m.id,m]));
  const fallback={nodes:[{id:p.entityId,label:p.identity.name,type:'EquipmentModel'}],edges:[]};
  if(!p.graph)p.capabilities.forEach(c=>{fallback.nodes.push({id:c.id,label:c.label,type:'CapabilityDomain'});fallback.edges.push({from:p.entityId,to:c.id,relation:'hasCapabilityDomain'});c.metrics.filter(m=>m.claimState!=='unknown').forEach(m=>{fallback.nodes.push({id:m.id,label:m.label,type:'CapabilityObservation'});fallback.edges.push({from:c.id,to:m.id,relation:'hasMetric'});});});
  const graph=p.graph || fallback;
  const types={EquipmentModel:['装备',0],CapabilityDomain:['能力域',1],technicalMechanisms:['技术机制',2],operationalEffects:['来源效果',2],constraints:['条件',2],Technology:['技术机制',2],CapabilityObservation:['指标观测',3],CapabilityMetric:['指标',3],Event:['事件',4],TestEvent:['测试事件',4],EventObservation:['事件观测',4],EvidenceSource:['证据',5],EvidenceItem:['证据',5],SourceDocument:['来源',6]};
  const counts=Array(6).fill(0);
  const nodes=graph.nodes.map(n=>{const [category,lane]=types[n.type] || ['对象',2];let label=n.label || n.id,recordId='';const m=metrics.get(n.id);if(m){label=`${m.label} ${metricValue(m)}`;recordId=m.id;}if(['EvidenceSource','EvidenceItem','SourceDocument'].includes(n.type)){const edge=graph.edges.find(e=>e.to===n.id&&metrics.has(e.from));recordId=edge?.from || '';label=/^https?:/i.test(label)?new URL(label).hostname:label;}return {...n,label,recordId,category,lane,x:20+lane*190,y:28+counts[lane]++*32};});
  const height=Math.max(535,Math.max(...counts)*32+55),width=1130;
  const byId=new Map(nodes.map(n=>[n.id,n]));
  nodes.filter(n=>n.type==='EquipmentModel').forEach(n=>n.y=height/2-12);
  const svg=`<svg id="cap-graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="装备能力证据与事件图谱"><g id="cap-graph-transform">${graph.edges.filter(e=>byId.has(e.from)&&byId.has(e.to)).map(e=>{const from=byId.get(e.from),to=byId.get(e.to);return `<path d="M${from.x+150},${from.y+12} C${from.x+175},${from.y+12} ${to.x-25},${to.y+12} ${to.x},${to.y+12}"><title>${esc(e.relation)} · ${esc(e.status)}</title></path>`;}).join('')}${nodes.map(n=>`<g class="cap-node cap-node-${esc(n.type)} ${['EvidenceSource','EvidenceItem','SourceDocument'].includes(n.type)?'is-evidence':''}" transform="translate(${n.x},${n.y})" data-cap-record="${esc(n.recordId)}" ${['Event','TestEvent'].includes(n.type)?`data-cap-event="${esc(n.id)}"`:''}><title>${esc(n.label)} · ${esc(n.category)}</title><rect width="150" height="23" rx="4"/><text x="7" y="15">${esc(n.label.length>18?n.label.slice(0,17)+'…':n.label)}</text></g>`).join('')}</g></svg>`;
  return `<div class="cap-graph-tools"><span>装备 · 能力 · 指标 · 证据 · 事件</span><small>${nodes.length} 对象 · ${graph.edges.length} 关联 · 点击指标或证据查看属性原文</small><button data-cap-zoom="in" aria-label="放大">＋</button><button data-cap-zoom="out" aria-label="缩小">−</button><button data-cap-zoom="reset">复位</button></div>${svg}`;
}

export function bindCapabilityGraph(host) {
  const svg=host.querySelector('#cap-graph'),layer=host.querySelector('#cap-graph-transform');
  if(!svg||!layer)return;
  let scale=1,x=0,y=0,drag=null;
  const render=()=>layer.setAttribute('transform',`translate(${x},${y}) scale(${scale})`);
  host.querySelectorAll('[data-cap-zoom]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.capZoom==='reset'){scale=1;x=0;y=0;}else scale=Math.max(.3,Math.min(3,scale*(b.dataset.capZoom==='in'?1.2:1/1.2)));render();}));
  svg.addEventListener('wheel',e=>{e.preventDefault();const pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;const at=pt.matrixTransform(svg.getScreenCTM().inverse());const next=Math.max(.3,Math.min(3,scale*(e.deltaY<0?1.1:1/1.1)));x=at.x-(at.x-x)*next/scale;y=at.y-(at.y-y)*next/scale;scale=next;render();},{passive:false});
  svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={clientX:e.clientX,clientY:e.clientY,x,y};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const matrix=svg.getScreenCTM().inverse();x=drag.x+(e.clientX-drag.clientX)*matrix.a;y=drag.y+(e.clientY-drag.clientY)*matrix.d;render();});
  svg.addEventListener('pointerup',()=>drag=null);svg.addEventListener('pointercancel',()=>drag=null);
}
