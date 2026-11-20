// Read-only view of source-qualified assertions. Presentation categories do
// not create new capability claims, events, or trusted ontology facts.
export const capabilityGroups = [
  {id:"sensing", label:"侦察与感知", keys:["sensor-payload"]},
  {id:"communications", label:"通信与信息共享", keys:["data-link"]},
  {id:"autonomy", label:"自主与协同", keys:["autonomy-mode", "human-oversight", "swarm-compatibility"]},
  {id:"mission", label:"任务执行", keys:["primary-weapons"]},
  {id:"support", label:"保障与部署", keys:["takeoff-method", "landing-method", "maintenance-requirements", "deployment-conditions"]},
  {id:"survivability", label:"生存与可靠性", keys:["stealth-feature", "rcs", "jamming-resistance", "reliability"]},
];

export const historyGroups = [
  {id:"all", label:"全部"}, {id:"development", label:"型号发展"},
  {id:"test", label:"试验验证"}, {id:"service", label:"采购与服役"},
  {id:"use", label:"部署与使用"}, {id:"loss", label:"事故与损失"},
  {id:"support", label:"维护与保障"},
];

const filled = value => value !== null && value !== undefined && value !== "";
const array = value => Array.isArray(value) ? value : [];
const entryValue = value => filled(value?.normalizedValue) ? value.normalizedValue : value?.rawValue;

export function verificationState(record = {}) {
  if (record.state === "conflict" || record.verificationStatus === "conflict") return "conflict";
  // A source's self-reported credibility or an AI confidence score is never
  // a substitute for the explicit fact verification flag.
  if (record.truthVerified === true || record.verificationStatus === "verified") return "verified";
  return "unverified";
}

export function verificationLabel(record = {}) {
  return {conflict:"存在冲突", verified:"已核验", unverified:"待核验"}[verificationState(record)];
}

export function evidenceStage(value = {}) {
  const stages = {
    "manufacturer-claim":"厂商宣称", "manufacturer-disclosure":"厂商宣称",
    "test-observation":"试验记录", "test-result":"试验记录",
    "service-record":"服役记录", "operational-observation":"实际使用记录",
    "combat-observation":"实际使用记录", "inference":"推断",
  };
  return stages[value.claimKind] || (value.basis === "workbook" ? "工作簿记录" : "公开资料记录");
}

export function classificationDimensions(entity = {}, attributes = {}) {
  const text = [entity.category, entity.subcategory,
    ...array(attributes.category?.values).map(entryValue),
    ...array(attributes.subcategory?.values).map(entryValue)].filter(filled).join(" ");
  const matches = specs => specs.filter(([pattern]) => pattern.test(text)).map(([,label]) => label);
  return {
    missionRoles: matches([[/侦察|监视|\bisr\b|recon|surveillance/i,"侦察监视"],
      [/察打|攻击|\bucav\b|armed|strike/i,"察打一体 / 攻击"],
      [/电子战|electronic warfare/i,"电子战"], [/中继|relay/i,"通信中继"],
      [/运输|补给|cargo|logistic/i,"运输补给"], [/僚机|协同|wingman|\bcca\b/i,"自主协同"],
      [/靶机|target drone/i,"靶机 / 试验"]]),
    airframeTypes: matches([[/固定翼|fixed[- ]wing/i,"固定翼"], [/(?<!多)旋翼|rotary[- ]wing|helicopter/i,"旋翼"],
      [/多旋翼|multirotor/i,"多旋翼"], [/复合翼|hybrid[- ]wing/i,"复合翼"]]),
    platformLevels: matches([[/\bmale\b|中空长航时/i,"中空长航时（MALE）"],
      [/\bhale\b|高空长航时/i,"高空长航时（HALE）"], [/\bmicro\b|微型/i,"微型"]]),
    deploymentModes: matches([[/舰载|航母|shipborne|carrier/i,"舰载"], [/垂直起降|\bvtol\b/i,"垂直起降"]]),
  };
}

export function attributeAvailability(key, bucket = {}, jobs = [], candidates = []) {
  const values = array(bucket.values);
  if (bucket.state === "conflict") return {id:"conflict", label:"存在冲突"};
  if (values.length) return {id:verificationState(values[0]), label:verificationLabel(values[0])};
  if (bucket.state === "not-applicable") return {id:"not-applicable", label:"不适用"};
  if (bucket.state === "withheld") return {id:"withheld", label:"未公开"};
  if (candidates.some(candidate => candidate.attributeKey === key && ["under-review", "edited", "needs-evidence", "accepted"].includes(candidate.status))) {
    return {id:"candidate", label:"候选属性待审 / 待入档"};
  }
  const relevant = jobs.filter(job => array(job.attributeKeys).includes(key));
  if (relevant.some(job => array(job.result?.items).length)) return {id:"unextracted", label:"有材料，未形成属性"};
  if (relevant.length && relevant.every(job => ["completed", "needs-review", "needs_review"].includes(job.status) && !array(job.result?.items).length)) {
    return {id:"not-found", label:"本次检索未找到资料"};
  }
  if (relevant.some(job => job.status === "failed")) return {id:"failed", label:"检索失败，待补证"};
  return {id:"missing", label:"暂无属性资料"};
}

export function buildCapabilities(entity = {}, document = {}, jobs = [], candidates = []) {
  const attributes = document.attributes || {};
  return capabilityGroups.map(group => ({...group, records:group.keys.flatMap(key => {
    const bucket = attributes[key] || {};
    const values = array(bucket.values);
    const availability = attributeAvailability(key, bucket, jobs, candidates);
    return (values.length ? values : [null]).map((value,index) => ({
      capabilityId:`${entity.entityId}:capability:${group.id}`,
      recordId:value?.assertionId || `${entity.entityId}:${key}:${index}`,
      entityId:entity.entityId, objectRevision:entity.currentRevision, groupId:group.id,
      attributeKey:key, rawValue:value?.rawValue ?? null, value:entryValue(value) ?? null,
      unit:value?.unit ?? null, conditions:value?.conditions ?? null,
      validTime:value?.validTime ?? null, stage:value ? evidenceStage(value) : "尚无证据",
      confidence:value?.confidence ?? null,
      verificationStatus:value ? verificationState({...value,state:bucket.state}) : availability.id,
      statusLabel:value ? verificationLabel({...value,state:bucket.state}) : availability.label,
      evidenceRefs:array(value?.evidenceRefs),
      reviewStatus:value?.reviewStatus ?? null,
      relatedEventIds:array(value?.relatedEventIds), claimKind:value?.claimKind ?? null,
    }));
  })}));
}

export function buildHistory(entity = {}, document = {}, sources = [], lineage = []) {
  const attributes = document.attributes || {};
  const records = [];
  for (const [key,type,label] of [["first-flight-year","development","首飞记录"], ["service-entry-year","service","服役记录"]]) {
    for (const value of array(attributes[key]?.values)) {
      records.push({eventId:value.assertionId || `${entity.entityId}:${key}`, eventType:type,
        label, eventTime:entryValue(value), timeBasis:"属性记录", entityId:entity.entityId,
        modelName:entity.preferredName, modelVersion:null, participants:[],
        mission:null, reportedResult:value.rawValue, location:null, loss:null,
        evidenceRefs:array(value.evidenceRefs), verificationStatus:verificationState({...value,state:attributes[key]?.state}),
        relatedCapabilityIds:[], stage:evidenceStage(value), confidence:value.confidence ?? null});
    }
  }
  for (const source of sources) {
    const explicitType = source.details?.eventType;
    const type = source.sourceScope === "combat-event" ? "use" : source.sourceScope === "loss-record" ? "loss"
      : historyGroups.some(group => group.id === explicitType && group.id !== "all") ? explicitType : null;
    if (!type) continue;
    const d = source.details || {};
    records.push({eventId:source.sourceReferenceId, eventType:type,
      label:d.title || d.conflict || d.theater || historyGroups.find(group => group.id === type)?.label,
      // Never use crawl/creation time or page publication time as event time.
      eventTime:d.time || d.eventDate || null, timeBasis:d.time || d.eventDate ? "来源记录" : "时间未记载",
      entityId:entity.entityId, modelName:source.subjectName || entity.preferredName,
      modelVersion:d.modelVersion || null, participants:array(d.participants),
      mission:d.mission || null, reportedResult:d.result || d.lossOrSorties || null,
      location:d.conflict || d.theater || null, loss:d.loss || d.defeatMechanism || null,
      evidenceRefs:[{evidenceId:source.sourceReferenceId, sourceRef:source.sourceUrl,
        sourceName:source.sourceName, quote:source.citationText,
        locator:{sheetName:source.sheetName, cellRange:source.cellRange},
        sourceReliability:source.sourceReliability}],
      verificationStatus:verificationState(source), relatedCapabilityIds:array(d.relatedCapabilityIds),
      stage:type === "use" ? "公开使用记录" : "来源事件记录", confidence:null,
      declaredCredibility:d.declaredCredibility || null});
  }
  const startYear = value => {const match=String(value ?? "").match(/\b(19\d{2}|20\d{2})\b/);return match ? Number(match[1]) : Infinity;};
  records.sort((a,b) => startYear(a.eventTime)-startYear(b.eventTime) || String(a.eventId).localeCompare(String(b.eventId)));
  return {events:records, lineage:lineage.map(edge => ({...edge, eventType:"lineage", eventTime:null}))};
}

export function buildDossier(entity, document, sources = [], lineage = [], jobs = [], candidates = []) {
  return {schemaVersion:"ontology-dossier.v1", entityId:entity.entityId,
    objectRevision:entity.currentRevision, classification:classificationDimensions(entity,document.attributes),
    capabilities:buildCapabilities(entity,document,jobs,candidates),
    history:buildHistory(entity,document,sources,lineage)};
}
