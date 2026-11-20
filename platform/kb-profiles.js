import { localName, jsonKind } from './kb-model.js';

const esc = value => String(value ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const empty = text => `<p class="semantic-empty">${esc(text)}</p>`;
const heading = (label, note = '') => `<h3 class="semantic-title">${esc(label)}</h3>${note ? `<p class="semantic-note">${esc(note)}</p>` : ''}`;
const badge = text => `<span class="semantic-badge">${esc(text)}</span>`;
const code = value => `<code>${esc(value)}</code>`;
const externalLink = url => /^https?:\/\//.test(String(url||'')) ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>` : code(url || '未登记');
function valueHTML(value) {
  if (value === undefined) return badge('缺字段');
  if (value === null) return badge('显式 null');
  if (Array.isArray(value)) {
    if (!value.length) return code('[]');
    return `<ol class="semantic-array">${value.map(v=>`<li>${valueHTML(v)}</li>`).join('')}</ol>`;
  }
  if (typeof value === 'object') {
    if (!Object.keys(value).length) return code('{}');
    return `<dl class="semantic-values">${Object.entries(value).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${valueHTML(v)}</dd>`).join('')}</dl>`;
  }
  if (value === '') return code('""（空字符串）');
  return esc(value);
}
const table = (heads, rows) => rows.length ? `<div class="semantic-scroll"><table class="semantic-table"><thead><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : empty('当前范围没有登记内容。');
const link = (type, id, label = id) => `<button type="button" class="semantic-link" data-jump="${esc(type)}:${esc(id)}">${esc(label)}</button>`;
const classLink = (model, iri) => {
  const c = model.resolveClass(iri);
  return c ? link('Class',c.iri,c.label || c.id) + ' ' + code(iri) : code(iri);
};
export const validationLabel = status => ({
  'failed':'未通过', 'engine-error':'校验执行异常', 'not-run':'未执行',
  'target-not-covered':'未覆盖', 'target-coverage-incomplete':'覆盖不完整',
  'scope-passed':'声明范围内未检出冲突', 'passed':'通过',
  'global-failure-unattributed':'数据集未通过（未定位到本主体）',
  'candidate-with-data-gaps':'候选版 · 存在数据缺项',
  'candidate-conforms':'候选版 · 已登记范围通过',
}[status] || status || '未执行');
function validationLayer(name, result) {
  if(!result)return heading(name)+empty('当前主体未附该层验证报告。');
  const details = [
    ['状态',badge(validationLabel(result.status))], ['命中目标数',esc(result.targetCount ?? '未记录')],
    ['执行时间',esc(result.executedAt || '未记录')], ['工具',esc(result.tool || '未记录')],
  ];
  if(result.scope)details.push(['实际支持范围',esc(result.scope)]);
  if(result.schemaProfile)details.push(['Schema profile',code(result.schemaProfile)]);
  if(result.shapeCount !== undefined)details.push(['Shape 数',esc(result.shapeCount)]);
  let html=heading(name)+table(['项目','结果'],details);
  if(result.violations?.length)html+=table(['路径 / 焦点','未通过原因'],result.violations.map(v=>[
    code(v.path || v.focusNode || v.sourceShape || '数据集'),esc(v.message || JSON.stringify(v))]));
  if(result.coveredShapes?.length)html+=`<details><summary>实际命中的 Shape</summary>${valueHTML(result.coveredShapes)}</details>`;
  html+=`<details><summary>工具版本与输入哈希</summary>${valueHTML({versions:result.versions || {},inputHashes:result.inputHashes || {}})}</details>`;
  return html;
}
function validationHTML(d) {
  const v=d.validation,report=v.report;
  let html=heading('验证结果')+badge(validationLabel(v.status));
  if(!report)return html+empty(v.note);
  html+=empty('JSON Schema 检查源文档结构；SHACL 检查已供应图的目标约束；OWL RL 只报告支持范围内的冲突。未覆盖不等于通过。');
  if(d.subjectKind==='Individual') {
    html+=validationLayer('JSON Schema',v.subject?.JSONSchema)+validationLayer('SHACL',v.subject?.SHACL)+validationLayer('OWL RL',v.subject?.OWLRL);
  } else {
    const subjects=(report.subjects || []).filter(s=>s.classIri===d.classIri);
    html+=heading('本类个体验证','此处按直接类型查询已有实例，不能据此把整个类声明为已验证。')+
      table(['主体 IRI','JSON Schema','SHACL','OWL RL'],subjects.map(s=>[
        code(s.iri),badge(validationLabel(s.JSONSchema?.status)),badge(validationLabel(s.SHACL?.status)),badge(validationLabel(s.OWLRL?.status))]));
    const coverage=report.shacl?.coverage || [];
    html+=heading('本类 Shape 覆盖')+table(['Shape','目标数','状态'],coverage.filter(s=>(s.targetClasses || []).includes(d.classIri)).map(s=>[
      code(s.shape),esc(s.targetCount),badge(s.targetCount>0?'已命中':'未覆盖')]));
    const rules=(report.compiler?.rules || []).filter(r=>d.axioms.some(a=>a.id===r.id && a.source===r.source));
    html+=heading('本类规则编译')+table(['规则','形式载体','编译结果 / 边界'],rules.map(r=>[code(r.id),esc(r.carrier),esc(r.status)+' · '+esc(r.reason)]));
  }
  html+=`<details><summary>当前发布报告与输入版本</summary>${table(['项目','值'],[
    ['执行时间',esc(report.executedAt)],['报告范围',esc(report.scope)],['包候选状态',badge(validationLabel(report.status))],
  ])}${valueHTML({toolVersions:report.toolVersions,sourceHashes:report.sourceHashes})}</details>`;
  return html;
}
function schemaTable(desc) {
  return table(['字段 / JSON 路径','JSON 类型与结构','存在性 / 数组项数','正式谓词映射'],desc.schemaRows.map(r=>{
    const m = desc.mappings.find(x=>x.jsonPointer === r.path);
    return [code(r.path), esc(jsonKind(r.spec)), esc([r.presence,r.items].filter(Boolean).join(' · ')), mappingHTML(m)];
  }));
}
function mappingHTML(mapping) {
  if(mapping?.status==='identity')return badge('主体身份字段');
  if(!mapping?.predicateIri)return badge('未映射');
  const contract=Object.fromEntries(['nodeKind','datatype','sequencePolicy','nullPolicy','referencePolicy','unit','time'].filter(k=>mapping[k]!=null).map(k=>[k,mapping[k]]));
  return code(mapping.predicateIri)+`<details><summary>查看映射约定</summary>${valueHTML(contract)}</details>`;
}
function actualValues(desc, category = null) {
  const rows = desc.values.filter(r=>(!category || r.category === category) && !(category && desc.entry.type==='ShellTemplate' && r.key==='stages'));
  return table(['字段 / 分类','当前对象的实际值','归属与来源','RDF 映射'], rows.map(r=>[
    `${code(r.path)} ${badge(r.category)}`,
    valueHTML(r.value),
    `${badge('本对象')} ${esc(r.source)}<br>${badge(r.state)}`,
    mappingHTML(r.mapping),
  ]));
}
export function knowledgeTabs(model, entry) {
  if (entry.type === 'Class') return [['definition','定义',''],['structure','类结构',''],['fields','字段与关系',''],['constraints','约束',''],['instances','个体',''],['source','来源','']];
  const d=model.describe(entry);
  const tabs=[['overview','概览',''],['values','属性与关系','']];
  if (d.values.some(v=>v.category==='行为')) tabs.push(['behavior','行为','']);
  if (d.values.some(v=>v.category==='接口') || entry.type==='ShellTemplate') tabs.push(['interfaces','接口','']);
  if (d.record?.sidc || (d.depiction.depict!=='none' && (d.depiction.glyph || d.depiction.depict))) tabs.push(['symbols','图示与符号','']);
  if (d.references.length || ['Envelope','Corridor','SearchSector','DetectionDisk','JamBloom','Shoreline'].includes(entry.type)) tabs.push(['geometry','几何与单位','']);
  return [...tabs,['validation','校验',''],['source','来源','']];
}
export function renderKnowledgePane(model, tab, entry) {
  const d = model.describe(entry);
  const identity = table(['项目','内容'],[
    ['主体类型',badge(d.subjectKind === 'Class' ? '类定义' : '个体')],
    ['完整 IRI',d.iri ? code(d.iri) : badge('身份未登记')],
    ['直接类型',d.subjectKind==='Class' ? code('owl:Class') : d.classIri ? classLink(model,d.classIri) : badge('类型未登记')],
    ['来源',esc(entry.file || '未登记')],
  ]);
  let html='';
  if (tab==='definition' || tab==='overview') {
    html = heading(d.subjectKind==='Class' ? '正式定义' : '当前对象');
    html += d.subjectKind==='Class' ? `<p class="semantic-definition">${esc(d.definition || '正式定义未登记。')}</p>` : '';
    html += identity;
    if (d.subjectKind !== 'Class') {
      if (!d.record) html += empty('未定位到此对象的静态源记录；目录声明不代表已有运行实例。');
      if (entry.type === 'CloudDef') html += empty('直接类型为 CloudDef；其上位类为 InformationEntity。Ex / En / He 是该信息实体描述的数值特征。');
      if (entry.type === 'PlatformType') html += empty('这是平台型谱目录个体；inScene 标志不将其变成物理平台或 OWL 类。');
      if (entry.type === 'FunctionBind') html += empty('这是职能角色的登记条目；输入、输出和绑定来自本条记录，不据此推定授权主体。');
      if (d.cls?.comment) html += heading('类型定义') + `<p>${esc(d.cls.comment)}</p>`;
    }
    if (d.subjectKind==='Class') {
      if(d.cls?.synonyms?.length)html+=heading('登记同义词')+valueHTML(d.cls.synonyms);
      if(d.cls?.definitionStatus)html+=heading('定义审议状态')+badge(d.cls.definitionStatus==='project-definition-pending-domain-review'?'项目定义 · 待领域复核':d.cls.definitionStatus);
      if(d.cls?.definitionSource || d.cls?.provenance)html+=heading('定义依据')+valueHTML(d.cls.definitionSource || d.cls.provenance);
      if(d.definitionReview) {
        const review=d.definitionReview;
        html+=heading('概念边界与审议','以下正反例是概念说明，用于复核定义，不是知识库中的已发生事实。')+table(['内容','登记'],[
          ['区别特征',valueHTML(review.distinguishingFeatures)],['边界',valueHTML(review.boundary)],
          ['正例（说明）',valueHTML(review.positiveExamples)],['反例（说明）',valueHTML(review.negativeExamples)],
          ['来源',valueHTML(review.sources)],['待复核问题',valueHTML(review.openQuestions)],
          ['审议状态',badge(['pending-domain-review','draft-pending-domain-review'].includes(review.reviewStatus)?'待领域复核':review.reviewStatus)],
        ]);
      }
    }
  } else if (tab==='structure') {
    html=heading('直接上位类','每行表达“当前类 rdfs:subClassOf 上位类”；多继承分别列出。');
    html+=table(['当前类','关系','上位类'],d.parents.map(p=>[classLink(model,d.classIri),code('rdfs:subClassOf'),classLink(model,p)]));
    html+=heading('直接下位类')+table(['下位类','完整 IRI'],d.children.map(c=>[link('Class',c.iri,c.label||c.id),code(c.iri)]));
    html+=heading('等价声明')+table(['关系','对象'],(d.cls?.equivalentTo||[]).map(ref=>[code('owl:equivalentClass'),code(ref)]));
    html+=heading('参见声明','参见不等于等价；外部标准采用范围见来源。')+table(['关系','对象'],(d.cls?.seeAlso||[]).map(ref=>[code('rdfs:seeAlso'),externalLink(ref)]));
    html+=heading('不相交声明')+table(['关系','对象'],d.disjoint.map(ref=>[code('owl:disjointWith'),classLink(model,ref)]));
  } else if (tab==='fields') {
    html=heading('字段规范',`来自 JSON Schema ${d.schemaName || '（未登记）'}。键是否必需与数组至少几项是不同约束。`)+schemaTable(d);
    html+=heading('RDF 属性及关系','domain 表达类型推断。祖先域候选不自动成为本类必填项，也不代表实例已有取值。');
    html+=table(['谓词','种类 / 来源','domain → range','归属'],d.propertyRows.map(p=>[
      code(p.iri)+(p.comment?`<p>${esc(p.comment)}</p>`:''),esc(`${p.kind} · ${p.file}`),
      `${p.domains.map(esc).join('<br>')} → ${p.ranges.map(esc).join('<br>')}`,badge(p.contentRole)+`<p>${esc(p.applicable)}</p>`,
    ]));
    if(d.cls?.enumerations?.length || d.codeReferences.length)html+=heading('枚举与字典值域','来自类登记或明确引用的字典码；不自动解释为 owl:oneOf。')+
      table(['登记来源','原始约定'],[...(d.cls.enumerations || []).map(x=>[esc(d.cls.file)+' · enumerations',valueHTML(x)]),...d.codeReferences.map(x=>['dictionary.json · '+code(x.id),valueHTML(x)])]);
  } else if (tab==='constraints') {
    html=heading('JSON Schema 文档约束','以下约束校验 JSON 文档，不等同于 OWL 公理。');
    html+=d.schema ? `<details open><summary>${esc(d.schemaName)}</summary><pre class="semantic-json">${esc(JSON.stringify(d.schema,null,2))}</pre></details>` : empty('本类未登记专用 JSON Schema。');
    html+=heading('本体公理与规则登记','形式导出支持范围见来源中的语义报告；解释性规则不能标成 OWL 已验证。');
    html+=table(['标识 / 种类','登记内容','来源'],d.axioms.map(a=>[code(a.id)+' '+badge(a.kind || 'Manchester登记'),valueHTML(Object.fromEntries(Object.entries(a).filter(([k])=>!['id','source'].includes(k)))),esc(a.source)]));
    html+=validationHTML(d);
  } else if (tab==='instances') {
    html=heading('直接类型匹配的个体','按当前类的完整 IRI 查询；导航搜索词不影响此列表。')+
      table(['个体','IRI','源记录'],d.relatedEntries.map(e=>[link(e.type,e.iri||e.id,e.id),e.iri?code(e.iri):badge('身份未登记'),esc(e.file)]));
  } else if (tab==='values' || tab==='behavior' || tab==='interfaces') {
    const category=tab==='behavior'?'行为':tab==='interfaces'?'接口':null;
    html=heading(category || '属性与关系','只列当前对象源记录中的值。属性、行为、接口是阅读分类，RDF 种类由正式映射确定。')+actualValues(d,category);
    if (entry.type==='ShellTemplate' && (tab==='behavior'||tab==='interfaces')) {
      const stages=d.record?.stages || [];
      html+=heading('阶段顺序与输入输出')+table(['顺序','阶段','读取','写入'],stages.map((s,i)=>[esc(i+1),esc(s.name),valueHTML(s.reads),valueHTML(s.writes)]));
    }
    if (tab==='values') {
      html+=heading('关联记录','来自独立关系记录的引用；保留来源、方向及附加字段，不当作当前记录自带的值。')+
        table(['关系','原始关联值','归属与来源'],d.relations.map(r=>[esc(r.relation),valueHTML(r.value),`${badge(r.contentRole)} ${esc(r.source)} ${code(r.jsonPointer)}`]));
      html+=heading('适用字段的缺项')+table(['字段','存在性','状态'],d.missing.map(r=>[code(r.path),esc(r.presence),badge(r.state)]));
      html+=`<details><summary>查看字段定义</summary>${schemaTable(d)}</details>`;
    }
  } else if (tab==='symbols') {
    html=heading('符号登记','SIDC 来自源记录，项目图示来自明确登记的展示配置；图示不改变类型或构成标准符合性证明。');
    html+=d.record?.sidc?valueHTML(d.record.sidc):empty('当前源记录未登记 SIDC。');
    html+=table(['归属与来源','展示配置'],d.presentation.map(p=>[esc(p.scope)+' · '+esc(p.source)+code(p.path),valueHTML(p.value)]));
    if(d.depiction.depict!=='none')html+='<figure class="depict-cell"><canvas data-glyph="1" width="200" height="200"></canvas><figcaption>项目示意图</figcaption></figure>';
  } else if (tab==='geometry') {
    html=heading('解析几何与位置','原始形状和参数按源记录显示；没有把包围盒当成精确 WKT。');
    const geoFields=new Set(['shape','p','c','r','x0','x1','y0','y1','z0','z1','params','heading','heading_deg','bearing','bearing_deg','frame','crs','geometry','bbox']);
    html+=table(['字段','原始值'],d.values.filter(r=>geoFields.has(r.key)).map(r=>[code(r.path),valueHTML(r.value)]));
    html+=heading('坐标与单位参考')+table(['内容','来源与归属','值'],d.references.map(r=>[esc(r.label),`${badge('包级参考')} ${esc(r.source)}`,valueHTML(r.value)]));
    html+=empty('CRK:ENG 为工程坐标：x 向东、y 向上、z 距岸，长度使用 km；速度、角度和时间采用各字段约定。没有地理锚定时不换算 EPSG 经纬度。');
  } else if (tab==='validation') {
    html=validationHTML(d);
    html+=heading('静态字段核对','这只是页面对照，不代替正式校验。')+table(['检查','结果'],[
      ['源记录',badge(d.record ? '已定位' : '未定位')],['完整 IRI',badge(d.iri?'已登记':'未登记')],
      ['必需键缺失（不含条件分支）',esc(d.missing.filter(r=>r.required).length)],
      ['字段映射',`${esc(d.values.filter(v=>v.mapping?.predicateIri).length)} / ${esc(d.values.length)} 个顶层字段已登记谓词`],
    ]);
  } else if (tab==='source') {
    html=heading('来源与版本')+identity+table(['版本','内容'],[['包版本',esc(d.version||'未登记')],['符合性',badge('参考词汇不等于标准符合性认证')]]);
    const direct=d.evidence.sources.filter(s=>d.evidence.directSourceIds.includes(s.id));
    if(direct.length)html+=heading('本条定义直接引用')+table(['资料','采用内容','链接'],direct.map(s=>[esc(s.id),esc(s.use||s.usedAs||s.what),externalLink(s.iri)]));
    if (d.references.length) html+=heading('包级参考')+table(['内容','归属','值'],d.references.map(r=>[esc(r.label),esc(r.source),valueHTML(r.value)]));
    if(entry.file==='intelligence-ontology.json') {
      html+=heading('本体入库证据与人工审核','每个已发布对象、属性或关系保留自己的入库审核。高置信度表示审核人的判断，原始报道仍需结合出处与条件阅读。');
      html+=table(['入库记录 / 研判','原文证据','审核与发布'],d.evidence.admissions.map(p=>[
        esc(p.admissionId)+`<p>${esc(p.assessmentId)}</p>`+code(p.sourceSnapshotHash),
        (p.evidenceRefs || (p.evidence ? [p.evidence] : [])).map(ref=>externalLink(ref.sourceUri)+`<blockquote>${esc(ref.quote)}</blockquote>`+code(ref.sourceVersion)+`<p>${esc(ref.contentHash)}</p>`).join(''),
        valueHTML({review:p.review,publication:p.publication}),
      ]));
      html+=`<p class="semantic-note"><a href="/api/intelligence/ontology" target="_blank" rel="noopener">查看当前已发布本体层</a></p>`;
    } else if(entry.file)html+=`<p class="semantic-note"><a href="/ontology/coast-raid-k/${encodeURIComponent(entry.file)}" target="_blank" rel="noopener">查看所属源文档全文 · ${esc(entry.file)}</a></p>`;
    if(d.packageEntry && entry.type!=='Package')html+=`<p class="semantic-note">包级其他分册与配置：${link('Package',d.packageEntry.iri || d.packageEntry.id,d.packageEntry.id)}</p>`;
    if(Object.keys(d.documentReference.values).length)html+=`<details><summary>所属文档的整体配置（非当前主体断言）</summary>${empty(d.documentReference.scope)}${table(['源路径','文档级参考值'],Object.entries(d.documentReference.values).map(([key,value])=>[esc(d.documentReference.source)+' '+code('/'+key),valueHTML(value)]))}</details>`;
    if(d.packageDocuments.length)html+=heading('包内分册与整体配置','按包目录登记来源；分册配置不能推定为每个个体的属性或授权。')+d.packageDocuments.map(doc=>`<details><summary>${esc(doc.source)}</summary><p><a href="/ontology/coast-raid-k/${encodeURIComponent(doc.source)}" target="_blank" rel="noopener">查看分册原文</a></p>${table(['源路径','文档级参考值'],Object.entries(doc.values).map(([key,value])=>[code('/'+key),valueHTML(value)]))}</details>`).join('');
    if(d.presentation.length)html+=`<details><summary>项目展示配置（不作为个体事实或授权依据）</summary>${table(['归属与来源','原始配置'],d.presentation.map(p=>[esc(p.scope)+' · '+esc(p.source)+code(p.path),valueHTML(p.value)]))}</details>`;
    if(d.dictionaryProfile)html+=`<details><summary>对应数据字典（正式约束以 Schema 和映射为准）</summary>${valueHTML(d.dictionaryProfile)}</details>`;
    html+=`<details><summary>包级标准采用范围与证据</summary>${table(['标准 / 版本','采用范围','状态 / 证据'],d.evidence.standards.map(s=>[esc(`${s.label} · ${s.version||'版本未登记'}`)+'<br>'+externalLink(s.uri),esc(s.scope),badge(s.status)+`<p>${esc(s.evidence)}</p>`]))}</details>`;
    html+=`<details><summary>包级来源登记（不等于每个来源都适用于本对象）</summary>${table(['来源','登记用途','链接'],d.evidence.sources.map(s=>[esc(s.id)+`<p>${esc(s.registry)}</p>`,esc(s.usedAs||s.use||s.what),externalLink(s.iri)]))}</details>`;
    html+=`<details><summary>语义决策记录</summary>${table(['事项','处理与边界','状态'],d.evidence.decisions.map(s=>[esc(`${s.id} · ${s.topic}`),esc(s.decision)+`<p>${esc(s.review||'')}</p>`,badge(s.status)]))}</details>`;
    html+=`<details><summary>原始记录</summary>${d.record?`<pre class="semantic-json">${esc(JSON.stringify(d.record,null,2))}</pre>`:empty('未定位原始记录。')}</details>`;
    html+=`<details><summary>字段路径覆盖</summary>${table(['JSON 路径','源值'],d.leaves.map(r=>[code(r.path),valueHTML(r.value)]))}</details>`;
  } else html=empty('此页签不适用于当前对象。');
  return `<section class="semantic-pane" data-subject-iri="${esc(d.iri||'')}" data-subject-kind="${esc(d.subjectKind)}" data-pane="${esc(tab)}">${html}</section>`;
}
