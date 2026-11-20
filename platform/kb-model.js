// Knowledge presentation model. Navigation groups never establish RDF types.
export const CRK = 'https://local.ontology/coast-raid-k#';
export const AG = 'https://local.ontology/coast-raid-k/agent/1.0.0#';
const own = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);
export const localName = (s) => String(s || '').split(/[#/]/).pop();
export const pointerEscape = (s) => String(s).replace(/~/g, '~0').replace(/\//g, '~1');
const list = (v) => v == null ? [] : Array.isArray(v) ? v : [v];
const SCHEMA_PROFILE = {
  Envelope: 'Body', Corridor: 'Body', SearchSector: 'Body', DetectionDisk: 'Body', JamBloom: 'Body',
  CommandPost: 'Site', Sensor: 'Site', Shooter: 'Site', Platform: 'Site', Emitter: 'Site', Package: 'PackageCatalog',
};
const BEHAVIOR_FIELDS = new Set(['stages', 'stage', 'transition', 'transitions', 'trigger', 'triggers', 'effect', 'effects', 'action', 'actions', 'safeFallbackPolicy', 'abortConditions']);
const INTERFACE_FIELDS = new Set(['reads', 'writes', 'inputs', 'outputs', 'input', 'output', 'eats', 'give', 'protocol', 'message', 'messages']);
export function readingCategory(path, kind = 'Individual') {
  const tokens = path.split('/').filter(Boolean);
  if (tokens.length === 1 && tokens[0] === 'type') return 'class';
  if (kind === 'Class' && tokens.some(x => ['subClassOf', 'equivalentTo', 'disjointWith'].includes(x))) return 'class';
  if (tokens.some(x => INTERFACE_FIELDS.has(x))) return '接口';
  if (tokens.some(x => BEHAVIOR_FIELDS.has(x))) return '行为';
  return '属性';
}
export function valueState(record, key) {
  if (!own(record, key)) return '缺字段';
  return record[key] === null ? '显式 null' : '有值';
}
export function jsonKind(node = {}) {
  if (node.$ref) return `JSON 引用 ${node.$ref}`;
  if (own(node, 'const')) return `固定值 ${JSON.stringify(node.const)}`;
  if (node.enum) return `枚举 ${JSON.stringify(node.enum)}`;
  if (node.oneOf || node.anyOf) return 'JSON 联合结构（见约束）';
  return list(node.type).join(' | ') || '未限定 JSON 类型';
}
export function schemaFields(defs, name, visited = new Set(), conditional = false) {
  const node = typeof name === 'string' ? defs[name] : name;
  if (!node) return [];
  if (typeof name === 'string') {
    if (visited.has(name)) return [];
    visited = new Set([...visited, name]);
  }
  let rows = [];
  if (node.$ref && node.$ref.startsWith('#/$defs/')) rows.push(...schemaFields(defs, node.$ref.slice(8), visited, conditional));
  for (const part of node.allOf || []) rows.push(...schemaFields(defs, part, visited, conditional));
  for (const part of [...node.oneOf || [], ...node.anyOf || []]) rows.push(...schemaFields(defs, part, visited, true));
  for (const [key, spec] of Object.entries(node.properties || {})) {
    rows.push({key, path: '/' + pointerEscape(key), spec, required: (node.required || []).includes(key), conditional,
      presence: (node.required || []).includes(key) ? (conditional ? '分支内必需' : '必需键') : '可选键',
      items: spec.type === 'array' ? `${spec.minItems ?? 0}..${spec.maxItems ?? '*'} 项` : '',
      schemaName: typeof name === 'string' ? name : null});
  }
  return rows;
}
export function flattenRecord(record, path = '') {
  if (record === null || typeof record !== 'object' || Object.keys(record).length === 0) return [{path: path || '/', value: record}];
  return Object.entries(record).flatMap(([k, v]) => flattenRecord(v, `${path}/${pointerEscape(k)}`));
}
export function createKnowledgeModel(files, inputEntries = []) {
  const classes = new Map(), properties = new Map();
  for (const [file, ns] of [['tbox.json', CRK], ['agent.extension.json', AG]]) {
    const doc = files[file] || {};
    for (const c of doc.classes || []) {
      const iri = c.iri || ns + c.id;
      // The legacy renderer may have already merged extension terms into tbox.
      if (file === 'tbox.json' && c.iri?.startsWith(AG)) continue;
      classes.set(iri, {...c, iri, file});
    }
    for (const [key, kind] of [['objectProperties', 'ObjectProperty'], ['dataProperties', 'DatatypeProperty']]) {
      for (const p of doc[key] || []) {
        if (file === 'tbox.json' && p.iri?.startsWith(AG)) continue;
        const iri = p.iri || ns + p.id;
        properties.set(iri, {...p, iri, file, kind});
      }
    }
  }
  function resolveClass(ref) {
    if (classes.has(ref)) return classes.get(ref);
    const hits = [...classes.values()].filter(c => c.id === ref);
    return hits.length === 1 ? hits[0] : null;
  }
  const expand = (ref, ownerFile) => resolveClass(ref)?.iri || (String(ref || '').includes(':') ? ref : (ownerFile === 'agent.extension.json' ? AG : CRK) + ref);
  for (const c of classes.values()) c.parents = list(c.subClassOf).map(p => expand(p, c.file));
  for (const p of properties.values()) {
    p.domains = list(p.domain).map(d => expand(d, p.file));
    p.ranges = list(p.range).map(r => expand(r, p.file));
  }
  function ancestors(iri, seen = new Set()) {
    if (seen.has(iri)) return seen;
    seen.add(iri);
    for (const p of classes.get(iri)?.parents || []) ancestors(p, seen);
    return seen;
  }
  const defs = {...files['schema.json']?.$defs, ...files['schema.agent.extension.json']?.$defs, ...files['schema.intelligence.json']?.$defs};
  const identityDoc = files['identity-registry.json'] || files['identity-map.json'] || {};
  const identities = identityDoc.entries || [];
  const mappingDoc = files['semantic-mapping.json'] || {};
  const mappings = mappingDoc.mappings || mappingDoc.fields || [];
  function semanticValidationReport() {
    const value = files['semantics/semantic-validation-report.json'];
    if (!value) return null;
    if (value && value.format === 'text' && typeof value.text === 'string') {
      try { return JSON.parse(value.text); } catch { return null; }
    }
    return value && typeof value === 'object' ? value : null;
  }
  const semanticReport = semanticValidationReport();
  function validationFor(e, iri, classIri, subjectKind) {
    if (!semanticReport) return {status: '未执行', note: '当前发布未附语义验证报告；页面展示不构成 JSON Schema、SHACL 或 OWL 验证。', report: null};
    if (subjectKind === 'Class') {
      const summary = semanticReport.validation || {};
      return {
        status: semanticReport.status || '已执行',
        note: semanticReport.scope || '语义验证报告已附于当前发布。',
        report: semanticReport,
        summary,
      };
    }
    const subject = (semanticReport.subjects || []).find(x => x.iri === iri && x.classIri === classIri);
    const summary = semanticReport.validation || {};
    if (!subject) return {status: '未覆盖', note: '语义报告已执行，但没有找到当前主体的逐主体记录。', report: semanticReport, summary};
    const layers = [subject.JSONSchema, subject.SHACL, subject.OWLRL];
    const failed = layers.find(x => x && ['failed','engine-error','global-failure-unattributed'].includes(x.status));
    const uncovered = layers.find(x => x && ['target-not-covered','not-run','target-coverage-incomplete'].includes(x.status));
    const allPassed = layers.every(x => x && ['passed','scope-passed'].includes(x.status) && x.targetCount>0);
    return {
      status: failed ? failed.status : uncovered ? uncovered.status : allPassed ? '通过（声明范围内）' : '报告不完整 / 未覆盖',
      note: semanticReport.scope || '逐主体结果来自当前发布同版本语义验证报告。',
      report: semanticReport,
      summary,
      subject,
      layers,
    };
  }
  function recordOf(e) {
    if (e.type === 'Class') {
      const cls = resolveClass(e.iri || e.id);
      return cls ? (files[cls.file]?.classes || []).find(c => (c.iri || (cls.file === 'agent.extension.json' ? AG : CRK) + c.id) === cls.iri) || null : null;
    }
    const doc = files[e.file];
    if (!doc) return null;
    if (e.type === 'Package') return doc;
    if (e.type === 'Membership') return (doc.membership || []).find(r => `${r.of}∈${r.region}` === e.id) || null;
    if (e.type === 'CloudDef') return doc.clouds?.[e.id] ?? null;
    if (e.type === 'Monitor' && doc.permitWriter === e.id) return {permitWriter: doc.permitWriter};
    if (e.type === 'Exclude' && (doc.excludes || []).includes(e.id)) return {id: e.id};
    if (e.type === 'Shoreline') return doc.shoreline || null;
    if (e.type === 'Needline') return doc.shoreTdl || null;
    const found = [];
    function walk(value) {
      if (!value || typeof value !== 'object') return;
      if (!Array.isArray(value) && value.id === e.id) found.push(value);
      for (const child of Object.values(value)) if (child && typeof child === 'object') walk(child);
    }
    walk(doc);
    const exact = e.iri ? found.filter(r => r.iri === e.iri) : [];
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) return null;
    const scoped = e.runId ? found.filter(r => r.runId === e.runId) : found;
    const typed = scoped.filter(r => r.type === e.type);
    if (typed.length === 1) return typed[0];
    return scoped.length === 1 ? scoped[0] : null;
  }
  function iriOf(e, rec = recordOf(e)) {
    if (e.type === 'Class') return resolveClass(e.iri || e.id)?.iri || e.iri || null;
    const reg = identities.filter(x => x.type === e.type && x.id === e.id && (!x.runId || x.runId === rec?.runId));
    return rec?.iri || (reg.length === 1 ? reg[0].iri : null) || e.iri || null;
  }
  const entries = inputEntries.map(e => ({...e, iri: iriOf(e)}));
  const version = files['catalog.json']?.version || null;
  function relationsFor(e) {
    if (e.type === 'Class') return [];
    const graph=files['graph.json'] || {}, result=[];
    // These are source relations, not guessed RDF predicates. Match complete
    // identifiers only; compound endpoint expressions remain source expressions.
    const ids=new Set([e.id,e.iri].filter(Boolean));
    const hit=(v)=>typeof v==='string' && (ids.has(v) || v.split('|').some(x=>ids.has(x)));
    function add(collection, rows, fields, source='graph.json') {
      (rows || []).forEach((r,i)=>{
        const match=Array.isArray(r)?r.some(hit):fields.some(k=>hit(r[k]));
        if(match)result.push({source,jsonPointer:`/${collection}/${i}`,relation:source==='intelligence-ontology.json' ? r.relation : collection,value:r,contentRole:source==='intelligence-ontology.json' ? '已审核发布关系' : '关联引用'});
      });
    }
    add('owns',graph.owns,['site','body']); add('covers',graph.covers,['sensor','body']);
    add('notSameAs',graph.notSameAs,[]);add('shoreTdl/spokes',graph.shoreTdl?.spokes,['from','to']);
    add('otherLinks',graph.otherLinks,['from','to','among']);
    add('membership',files['gis.json']?.membership,['of','region'],'gis.json');
    add('relations',files['intelligence-ontology.json']?.relations,['from','to'],'intelligence-ontology.json');
    if(hit(graph.shoreTdl?.deniedBy))result.push({source:'graph.json',jsonPointer:'/shoreTdl/deniedBy',relation:'deniedBy',value:{needline:graph.shoreTdl.id,deniedBy:graph.shoreTdl.deniedBy},contentRole:'关联引用'});
    return result;
  }
  function describe(e) {
    const record = recordOf(e), iri = iriOf(e, record);
    const cls = resolveClass(e.type === 'Class' ? (e.iri || e.id) : e.type);
    const subjectKind = e.type === 'Class' ? 'Class' : 'Individual';
    const schemaName = e.file === 'intelligence-ontology.json' ? 'PublishedIntelligenceObject' : defs[e.type === 'Class' ? e.id : e.type] ? (e.type === 'Class' ? e.id : e.type) : SCHEMA_PROFILE[e.type === 'Class' ? e.id : e.type];
    const schemaRows = schemaFields(defs, schemaName);
    const classIri = cls?.iri || null;
    const matchedMappings = mappings.filter(m => m.classIri === classIri);
    function mappingFor(path) {
      return matchedMappings.find(m => m.jsonPointer === path || m.jsonPath === path) || null;
    }
    const values = record && subjectKind !== 'Class' ? Object.entries(record).map(([key, value]) => {
      const path = '/' + pointerEscape(key), mapping = mappingFor(path);
      return {key, path, value, state: valueState(record, key), category: mapping?.category || readingCategory(path), mapping,
        selectedSubjectIri: iri, valueOwnerIri: iri, subjectKind, contentRole: '实例断言', source: e.file, version};
    }) : [];
    const missing = schemaRows.filter(r => !r.conditional && !own(record, r.key)).map(r => ({...r, state: '缺字段'}));
    const lineage = cls ? ancestors(cls.iri) : new Set();
    const propertyRows = [...properties.values()].filter(p => p.domains.some(d => lineage.has(d))).map(p => ({...p,
      contentRole: p.domains.includes(classIri) ? '本类域声明' : '祖先域候选',
      applicable: p.domains.includes(classIri) ? '域声明；展示与校验以字段契约为准' : '候选；未自动成为本对象字段'}));
    const rawBase = files['tbox.json'] || {}, rawAgent = files['agent.extension.json'] || {};
    const axioms = [];
    for (const [file, doc] of [['tbox.json', rawBase], ['agent.extension.json', rawAgent]]) {
      for (const ax of doc.axioms || []) {
        const endpoints = [ax.subject,ax.left,ax.right,...list(ax.classes),...(ax.kind==='mapping' ? [ax.mapsTo] : [])].filter(Boolean);
        if (endpoints.some(x=>expand(x,file)===classIri)) axioms.push({...ax, source: file});
        else if (!ax.subject && ax.manchester) {
          const first = ax.manchester.trim().split(/\s+/)[0];
          if (expand(first, file) === classIri) axioms.push({...ax, source: file});
        }
      }
    }
    const disjoint = [];
    for (const [file, doc] of [['tbox.json', rawBase], ['agent.extension.json', rawAgent]]) {
      for (const group of doc.disjoint || []) {
        const members = group.map(x => expand(x, file));
        if (members.includes(classIri)) disjoint.push(...members.filter(x => x !== classIri));
      }
      for (const ax of doc.axioms || []) if (ax.kind === 'disjointClasses') {
        const members = list(ax.classes).map(x => expand(x, file));
        if (members.includes(classIri)) disjoint.push(...members.filter(x => x !== classIri));
      }
      for (const ax of doc.axioms || []) if (ax.kind === 'disjoint') {
        const members=[ax.left,ax.right].filter(Boolean).map(x=>expand(x,file));
        if(members.includes(classIri))disjoint.push(...members.filter(x=>x!==classIri));
      }
    }
    const relatedEntries = entries.filter(x => x.type !== 'Class' && resolveClass(x.type)?.iri === classIri);
    const definition = cls?.comment || null;
    const definitionReview=(files['class-definition-review.json']?.classes || []).find(c=>c.classIri===classIri) || null;
    const lexicon=files['lexicon.json'] || {};
    const typeKey=subjectKind==='Class'?e.id:e.type;
    const presentation=[];
    if(lexicon.types?.[typeKey])presentation.push({source:'lexicon.json',path:'/types/'+pointerEscape(typeKey),scope:'类型的项目展示配置',value:lexicon.types[typeKey]});
    if(subjectKind==='Individual' && lexicon.ids?.[e.id])presentation.push({source:'lexicon.json',path:'/ids/'+pointerEscape(e.id),scope:'短名的项目展示配置',value:lexicon.ids[e.id]});
    const codeReferences=(files['dictionary.json']?.codes || []).filter(c=>(cls?.enumCodes || []).includes(c.id));
    const dictionaryProfile=(files['dictionary.json']?.tables || []).find(t=>t.id===typeKey) || null;
    function documentConfig(source) {
      const sourceDocument=files[source] || {};
      return {source,scope:'所属源文档的整体配置，不作为当前主体的实例断言',
        values:Object.fromEntries(['package','type','version','note','stateModel','innerLoop','crs','unit','frame','permitWriter','excludes','zoneActs','grid','presentation'].filter(k=>own(sourceDocument,k)).map(k=>[k,sourceDocument[k]]))};
    }
    const documentReference=documentConfig(e.file);
    const packageEntry=entries.find(x=>x.type==='Package');
    const packageDocuments=e.type==='Package'?Object.values(files['catalog.json']?.parts || {}).filter(name=>files[name] && !files[name].format).map(documentConfig):[];
    const admissionIds = new Set([record?.admissionId, ...list(record?.admissionIds), ...relationsFor(e).map(r=>r.value?.admissionId)].filter(Boolean));
    const admissionProvenance = e.file === 'intelligence-ontology.json' ? (files['intelligence-ontology.json']?.provenance || []).filter(p=>admissionIds.has(p.admissionId)) : [];
    return {entry: e, iri, classIri, subjectKind, record, cls, definition, version, schemaName, schema: defs[schemaName], schemaRows,
      values, missing, leaves: record ? flattenRecord(record) : [], propertyRows, axioms, disjoint: [...new Set(disjoint)],
      parents: cls?.parents || [], children: [...classes.values()].filter(c => c.parents.includes(classIri)),
      relatedEntries, mappings: matchedMappings, relations: relationsFor(e), definitionReview,
      presentation, depiction:Object.assign({},...presentation.map(p=>p.value)), codeReferences, dictionaryProfile, documentReference, packageEntry, packageDocuments,
      evidence: {admissions: admissionProvenance, sources: [...(files['sources.json']?.items || []).map(s=>({...s,registry:'sources.json'})),
        ...(files['agent.extension.json']?.provenance?.sources || []).map(s=>({...s,registry:'agent.extension.json#/provenance/sources'}))],
        directSourceIds: list(record?.provenance).filter(x=>typeof x==='string'),
        standards: files['standards-evidence.json']?.items || [], decisions: files['semantic-decisions.json']?.decisions || []},
      validation: validationFor(e, iri, classIri, subjectKind),
      references: subjectKind === 'Individual' && ['Shoreline','Region','Envelope','Corridor','SearchSector','DetectionDisk','JamBloom','CommandPost','Sensor','Shooter','Platform','Emitter'].includes(e.type)
        ? [{owner: identities.find(x=>x.type==='Package')?.iri || null, source:'gis.json', label:'包级坐标系', value:files['gis.json']?.crs},
           {owner:identities.find(x=>x.type==='Package')?.iri || null,source:'catalog.json',label:'包级单位',value:files['catalog.json']?.unit}].filter(r=>r.value != null) : []};
  }
  return {files, entries, classes, properties, resolveClass, recordOf, iriOf, describe, ancestors, version};
}
