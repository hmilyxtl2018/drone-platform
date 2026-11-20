import { buildDossier, capabilityGroups, historyGroups, verificationLabel, evidenceStage, attributeAvailability } from "./ontology-dossier-model.js?v=11-dossier-views";
import {capabilityMarkup,metricDetail,bindCapabilityGraph,conflictPeers,conflictReviewMarkup} from './ontology-capability-view.js?v=13-conflict-review';
import {mountDynamicUavViewer} from './ontology-model-viewer.js?v=3-overview-model';

const $ = (selector) => document.querySelector(selector);
const state = { mode: "trend", entities: [], filtered: [], selected: null, detail: null, jobs: [], sources: [], lineage: [], relations: [], manufacturing: null, factorGraph: null, signalRecords: {observations: [], claims: [], evidenceBundles: [], verificationRuns: [], emitterSignatures: []}, signalFilter: {query: "", evidenceLevel: "all", status: "all"}, selectedSignal: null, dossier: null, trendSummary: null, agentState: null, agentResponse: null, objectAgent: null, analysisView: "overview", mainView: "run", analysisRunStatus: "ready", analysisRun: null, analysisRunPoll: null, analysisConversationRunId: null, analysisSessionId: null, analysisSessions: [], analysisSessionsLoaded: false, conversationApi: null, conversationSessions: [], conversationSession: null, conversationLoadPromise: null, conversationApiError: "", suppressConversationPersistence: false, tab: "overview", manufacturingTab: "assembly", attributeGroup: "all", historyGroup: "all", rowQuery: "", collapsedCategories: new Set(), governance: { assessments: [], selectedId: new URLSearchParams(location.search).get("assessmentId") || "", audits: [], findings: [], admissions: [], loading: false } };
Object.assign(state,{profile:null,capabilityGroup:'all',capabilityView:'profile',capabilityRun:null,auditRun:null});
let entityRequest=0;

/* The workbook stores an entity type and a functional category. Keep those
   dimensions separate in the UI: the first level is the ontology family,
   the second level is the concrete ontology class, and aircraft receive a
   mission-oriented third level. */
const equipmentSubclassMeta = (item = {}) => {
  const known = {
    "strike": ["察打一体 / 攻击型", "category-strike"],
    "reconnaissance": ["侦察监视型", "category-recon"],
    "electronic-warfare": ["电子战型", "category-electronic"],
    "communications-relay": ["通信中继型", "category-relay"],
    "logistics": ["运输补给型", "category-logistics"],
    "collaborative-wingman": ["无人僚机 / 协同作战型", "category-wingman"],
    "target": ["靶机 / 试验型", "category-target"],
    "shipborne": ["舰载无人机", "category-ship"],
    "general-purpose": ["通用任务型", "category-general"],
    "loitering-munition": ["巡飞弹 / 游荡弹药", "category-loiter"],
    "system-platform": ["无人机平台 / 系统", "category-platform"],
  };
  const key = String(item.equipmentSubclass || "").trim();
  if (known[key]) return {key, label: item.equipmentSubclassLabel || known[key][0], iconClass: known[key][1]};
  const text = [item.preferredName, item.englishName, item.category, item.subcategory].filter(Boolean).join(" ");
  const patterns = [
    [/察打|攻击|ucav|armed|strike/i, "strike"],
    [/侦察|监视|isr|recon|surveillance/i, "reconnaissance"],
    [/电子战|干扰|电子支援|ew|electronic warfare|jamming/i, "electronic-warfare"],
    [/中继|通信|数据链|relay|communications/i, "communications-relay"],
    [/运输|补给|货运|物流|cargo|logistic|delivery/i, "logistics"],
    [/僚机|忠诚僚机|协同|蜂群|cca|loyal wingman|wingman|teaming/i, "collaborative-wingman"],
    [/靶机|试验靶|target drone|target/i, "target"],
    [/舰载|航母|shipborne|carrier/i, "shipborne"],
  ];
  const match = patterns.find(([pattern]) => pattern.test(text));
  const resolvedKey = match?.[1] || "general-purpose";
  return {key: resolvedKey, label: known[resolvedKey][0], iconClass: known[resolvedKey][1]};
};
const equipmentSubclassOrder = {
  reconnaissance: 10,
  strike: 20,
  "electronic-warfare": 30,
  "communications-relay": 40,
  logistics: 50,
  "collaborative-wingman": 60,
  shipborne: 70,
  target: 80,
  "general-purpose": 90,
};

const entityTaxonomy = (item = {}) => {
  const type = String(item.entityType || item.entity_type || "");
  const text = [item.preferredName, item.englishName, item.category, item.subcategory].filter(Boolean).join(" ");
  if (type === "Organization" || String(item.canonicalKey || "").startsWith("organization-")) return {topKey:"organization", topLabel:"组织", classKey:"organization", classLabel:"组织机构", iconClass:"category-organization"};
  if (type === "Program" || String(item.canonicalKey || "").startsWith("program-")) return {topKey:"program", topLabel:"项目", classKey:"program", classLabel:"项目 / 计划", iconClass:"category-program"};
  if (type === "Technology" || String(item.canonicalKey || "").startsWith("technology-")) return {topKey:"technology", topLabel:"技术", classKey:"technology", classLabel:"技术对象", iconClass:"category-technology"};
  if (type === "EventRecord" || String(item.canonicalKey || "").startsWith("event-")) return {topKey:"event", topLabel:"事件", classKey:"event", classLabel:"公开事件", iconClass:"category-event"};
  if (type === "EquipmentModel" || String(item.canonicalKey || "").startsWith("equipment-")) {
    const loitering = /巡飞|游荡弹|loitering|loitering munition/i.test(text);
    const platform = /系统|平台|system|platform|uas\b|istar|tuas|stuas/i.test(text);
    if (loitering) return {topKey:"equipment", topLabel:"装备", classKey:"loitering", classLabel:"巡飞弹 / 游荡弹药", iconClass:"category-loiter", subclassKey:"loitering-munition", subclassLabel:"巡飞弹 / 游荡弹药", subclassIconClass:"category-loiter"};
    if (platform) return {topKey:"equipment", topLabel:"装备", classKey:"platform", classLabel:"无人机平台 / 系统", iconClass:"category-platform", subclassKey:"system-platform", subclassLabel:"无人机平台 / 系统", subclassIconClass:"category-platform"};
    const subclass = equipmentSubclassMeta(item);
    return {topKey:"equipment", topLabel:"装备", classKey:"aircraft", classLabel:"无人机", iconClass:subclass.iconClass, subclassKey:subclass.key, subclassLabel:subclass.label, subclassIconClass:subclass.iconClass};
  }
  return {topKey:"other", topLabel:"其他主体", classKey:"other", classLabel:"待分类主体", iconClass:"category-generic"};
};

const ontologyIconMarkup = (meta = {}) => {
  const paths = {
    "category-organization": '<path d="M4 20V5h9v15M13 10h5v10M2 20h20M7 8h3M7 12h3M7 16h3"/>',
    "category-platform": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h10M7 13h6M16 13h1M8 2v3M16 2v3"/>',
    "category-aircraft": '<path d="M3 13h7l2-8 2 8h7l-3 2h-4l-2 5-1-5H7z"/>',
    "category-recon": '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/>',
    "category-strike": '<path d="M13 2 5 13h6l-1 9 9-13h-6z"/>',
    "category-electronic": '<path d="M12 18v3M8.5 15.5a5 5 0 0 1 7 0M5.5 12.5a9 9 0 0 1 13 0M2.5 9.5a13 13 0 0 1 19 0"/>',
    "category-relay": '<path d="M12 20V8M8 20h8M9 8l3-5 3 5zM6 12a8 8 0 0 1 12 0M3 9a12 12 0 0 1 18 0"/>',
    "category-logistics": '<path d="M3 7h11v10H3zM14 11h4l3 3v3h-7zM7 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/>',
    "category-loiter": '<path d="M12 3 16 12l-4 9-4-9 4-9Z"/><path d="M8 9 4 7M16 9l4-2M8 15l-4 2M16 15l4 2"/>',
    "category-program": '<path d="M3 5h7l2 2h9v12H3z"/><path d="M3 9h18"/>',
    "category-technology": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/><path d="M9 9h6v6H9z"/>',
    "category-event": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 2v6M16 2v6M4 10h16M8 14h3M13 14h3"/>',
    "category-general": '<path d="M7 10h10l2 2-2 2H7l-2-2 2-2Z"/><path d="M8 10 6 6M16 10l2-4M8 14l-2 4M16 14l2 4"/><path d="M10 12h4"/>',
    "category-generic": '<circle cx="12" cy="12" r="8"/><path d="M8 12h8M12 8v8"/>'
  };
  const title = meta.classLabel || meta.topLabel || "待分类主体";
  return `<svg viewBox="0 0 24 24" role="img" aria-label="${esc(title)}" focusable="false"><title>${esc(title)}</title>${paths[meta.iconClass] || paths["category-generic"]}</svg>`;
};

const entityIconMarkup = (item) => ontologyIconMarkup(entityTaxonomy(item));

const attributeGroups = [
  ["all", "全部", []],
  ["performance", "飞行性能", ["max-speed", "ceiling", "flight-range", "endurance"]],
  ["mission", "任务与武器", ["primary-weapons", "stealth-feature", "swarm-compatibility"]],
  ["combat", "使用记录汇总", ["combat-status", "combat-conflict-count", "combat-hours-or-production"]],
  ["autonomy", "自主与协同", ["autonomy-mode", "human-oversight"]],
  ["c4isr", "C4ISR", ["sensor-payload", "data-link"]],
  ["survivability", "生存与可靠性", ["rcs", "jamming-resistance", "reliability"]],
  ["support", "保障与部署", ["takeoff-method", "landing-method", "maintenance-requirements", "deployment-conditions"]],
  ["dimensions", "尺寸与重量", ["length", "wingspan", "max-takeoff-weight", "payload-capacity"]],
  ["propulsion", "动力系统", ["propulsion-type"]],
  ["identity", "身份与状态", ["category", "subcategory", "lifecycle-status", "first-flight-year", "service-entry-year"]],
  ["manufacturing", "制造与成本", ["reference-unit-cost", "production-count"]],
  ["research", "研究评估", ["technical-highlights", "composite-score"]],
];
const attributeGroupMap = Object.fromEntries(attributeGroups.flatMap(([group, , keys]) => keys.map((key) => [key, group])));
const attributeGroupOrder = Object.fromEntries(attributeGroups.map(([group], index) => [group, index]));
const attributeLabelMap = {
  category:"类别", subcategory:"子类别", "lifecycle-status":"生命周期", "first-flight-year":"首飞年份", "service-entry-year":"服役年份",
  length:"机长", wingspan:"翼展", "max-takeoff-weight":"最大起飞重量", "payload-capacity":"有效载荷",
  "max-speed":"最大速度", ceiling:"升限", "flight-range":"航程", endurance:"续航时间", "propulsion-type":"动力系统",
  "primary-weapons":"任务载荷/武器", "stealth-feature":"隐身特征", "swarm-compatibility":"蜂群协同",
  "sensor-payload":"传感器载荷", "data-link":"数据链", rcs:"雷达散射截面", "jamming-resistance":"抗干扰能力",
  "autonomy-mode":"自主模式", "human-oversight":"人在回路", "reference-unit-cost":"参考单价", "production-count":"产量",
  "technical-highlights":"技术亮点", "composite-score":"综合评分", "combat-status":"实战状态",
  "combat-conflict-count":"参战冲突数", "combat-hours-or-production":"作战时长/生产量",
  "takeoff-method":"起飞方式", "landing-method":"回收方式", "maintenance-requirements":"维护要求",
  "deployment-conditions":"部署条件", reliability:"可靠性记录"
};
const attributeLabel = (key) => attributeLabelMap[key] || key;
const stateLabel = (value) => ({reported:"已有记录", unknown:"待补证", derived:"推导", conflict:"有冲突"}[value] || value || "--");

const categoryMeta = (category = "") => {
  if (/侦察|ISR/i.test(category)) return ["ISR", "category-isr", "侦察无人机"];
  if (/察打|攻击|UCAV/i.test(category)) return ["ATK", "category-strike", "察打一体/攻击无人机"];
  if (/巡飞|弹药/i.test(category)) return ["LM", "category-loiter", "巡飞弹/游荡弹药"];
  if (/僚机|协同/i.test(category)) return ["CCA", "category-wingman", "无人僚机/协同平台"];
  if (/靶机/i.test(category)) return ["TGT", "category-target", "靶机"];
  if (/舰载/i.test(category)) return ["SEA", "category-ship", "舰载无人机"];
  return ["UAV", "category-generic", "无人机"];
};

const categoryIconMarkup = (category = "", label = "UAV") => {
  const [, cls, title = "无人机"] = categoryMeta(category);
  const paths = {
    "category-isr": '<circle cx="12" cy="12" r="6.8"/><path d="M12 5.2V2.5M12 21.5v-2.7M5.2 12H2.5M21.5 12h-2.7"/><circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/>',
    "category-strike": '<path d="M4 20 20 4M8.3 4H20v11.7M4 8.3V20h11.7"/><circle cx="12" cy="12" r="2.3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>',
    "category-loiter": '<path d="M12 3 16 12l-4 9-4-9 4-9Z"/><path d="M8 9 4 7M16 9l4-2M8 15l-4 2M16 15l4 2"/>',
    "category-wingman": '<circle cx="7" cy="12" r="3"/><circle cx="17" cy="7" r="3"/><circle cx="17" cy="17" r="3"/><path d="m9.7 10.7 4.6-2.4M9.7 13.3l4.6 2.4"/>',
    "category-target": '<path d="M9 3h6M10 3v6l-5 8a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-8V3M7 15h10M8 18h3"/>',
    "category-ship": '<path d="M3 15h18l-3 4H6l-3-4Z"/><path d="M8 15V7h8v8M12 7V4"/><path d="M3 21c1.5-1 3-1 4.5 0s3 1 4.5 0 3-1 4.5 0 3 1 4.5 0"/>',
    "category-generic": '<path d="M7 9h10l2 3-2 3H7l-2-3 2-3Z"/><path d="M9 9 7 5M15 9l2-4M9 15l-2 4M15 15l2 4"/><circle cx="9" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1" fill="currentColor" stroke="none"/>'
  };
  return `<svg viewBox="0 0 24 24" role="img" aria-label="${esc(title)}" focusable="false"><title>${esc(title)}</title>${paths[cls] || paths["category-generic"]}</svg>`;
};

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const safeHref = (value) => {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (error) {
    return "";
  }
};
const fmt = (value) => value === null || value === undefined || value === "" ? "--" : typeof value === "object" ? JSON.stringify(value, null, 0) : String(value);
const status = (value) => `<span class="status ${esc(value || "pending")}">${esc(value || "pending")}</span>`;
const verificationStatus = (job) => job?.stopReason === "stale-timeout"
  ? `<span class="status recovered" title="${esc(job.error || "执行进程无心跳，系统已回收任务租约")}">已回收</span>`
  : status(job?.status);
const toast = (message, type = "success") => { const node = $("#toast"); node.textContent = message; node.className = `toast show ${type}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 2800); };
const sourceLink = (url, label) => {
  const href = safeHref(url);
  return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label || href)} ↗</a>` : esc(label || "来源链接待核验");
};
const targetLabel = (type) => ({identity:"身份核验", performance:"性能参数", systems:"任务系统", collaboration:"自主协同", industrial:"产业采购", combat:"作战记录", imagery:"公开影像"})[type] || type;

/* A review decision must be grounded in the material that produced the
   candidate. Keep the review list compact, but make the exact quote,
   publisher, URL, locator, conditions and competing values available in the
   same row before the operator accepts or rejects it. */
const compactEvidenceText = (value, max = 150) => {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
};
const candidateEvidenceMarkup = (candidate, candidates = []) => {
  const evidence = Array.isArray(candidate?.evidence) ? candidate.evidence : [];
  const primary = evidence[0] || {};
  const peers = [...new Set(candidates
    .filter((item) => item.attributeKey === candidate.attributeKey && item.candidateId !== candidate.candidateId)
    .map((item) => item.proposedValue)
    .filter(Boolean))];
  const verification = candidate.evidenceVerification || {};
  const verificationText = verification.status === "eligible"
    ? `完整性通过 · ${verification.verifiedCount || 0}/${verification.evidenceCount || 0} · 可自动采纳候选`
    : `需核验 · ${verification.verifiedCount || 0}/${verification.evidenceCount || evidence.length}`;
  const facts = [
    ["适用条件", candidate.conditions || candidate.metadata?.qualifier || "未记载"],
    ["改型/配置", candidate.metadata?.variant || candidate.metadata?.configuration || "未记载"],
    ["模型置信度", candidate.confidence == null ? "未提供" : `${Math.round(Number(candidate.confidence) * 100)}%`],
    ["证据核验", verificationText],
    ["来源任务", candidate.sourceJobId || "未绑定任务"],
  ];
  const evidenceBody = evidence.length ? evidence.map((item, index) => {
    const uri = item.sourceUri || item.sourceUrl || item.source;
    const locator = [item.lineStart != null ? `第 ${item.lineStart} 行` : "", item.lineEnd != null && item.lineEnd !== item.lineStart ? `至 ${item.lineEnd} 行` : ""].filter(Boolean).join(" ");
    return `<div class="candidate-evidence-item"><div class="candidate-evidence-source"><b>${esc(item.sourceName || item.publisherDomain || "公开来源")}</b>${uri ? sourceLink(uri, compactEvidenceText(uri, 96)) : "<span>来源链接待补充</span>"}<small>${esc(locator || "原文位置未记录")}</small></div><blockquote>${esc(item.quote || "未提供原文引句")}</blockquote>${item.contentHash ? `<small class="candidate-evidence-hash">内容哈希：${esc(item.contentHash)}</small>` : ""}</div>`;
  }).join("") : `<p class="candidate-evidence-empty">未保存原文引句，不能直接作出采纳决定。</p>`;
  const conflict = peers.length ? `<div class="candidate-evidence-conflict"><b>同属性其他候选值</b><span>${esc(peers.slice(0, 8).join(" · "))}${peers.length > 8 ? " …" : ""}</span></div>` : "";
  const sourceLabel = primary.sourceName || primary.publisherDomain || "来源待核验";
  const verificationNote = verification.reasons?.length ? `<p class="candidate-evidence-verification-note">核验原因：${esc(verification.reasons.join("；"))}</p>` : "";
  return `<details class="candidate-evidence"><summary><span class="candidate-evidence-label">证据 ${evidence.length} 条</span><span class="candidate-evidence-source-label" title="${esc(sourceLabel)}">${esc(sourceLabel)}</span><span class="candidate-evidence-quote" title="${esc(primary.quote || "未提供原文引句")}">${esc(compactEvidenceText(primary.quote || "未提供原文引句"))}</span><span class="candidate-evidence-caret">⌄</span></summary><div class="candidate-evidence-body"><div class="candidate-evidence-facts">${facts.map(([label, value]) => `<span><small>${esc(label)}</small><b>${esc(value)}</b></span>`).join("")}</div>${verificationNote}${conflict}${evidenceBody}</div></details>`;
};

async function api(path, options) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function renderEntities() {
  const query = $("#entity-search").value.trim().toLowerCase();
  const category = $("#category-filter").value;
  state.filtered = state.entities.filter((item) => {
    const categoryMatch = !category || item.category === category;
    const taxonomy = entityTaxonomy(item);
    const haystack = [item.preferredName,item.englishName,item.manufacturer,item.originCountry,item.category,item.subcategory,item.entityType,taxonomy.topLabel,taxonomy.classLabel,taxonomy.subclassLabel].join(" ").toLowerCase();
    return categoryMatch && (!query || haystack.includes(query));
  });
  $("#visible-total").textContent = state.filtered.length;
  const groups = new Map();
  state.filtered.forEach((item) => {
    const taxonomy = entityTaxonomy(item);
    if (!groups.has(taxonomy.topKey)) groups.set(taxonomy.topKey, {meta: taxonomy, classes: new Map()});
    const top = groups.get(taxonomy.topKey);
    if (!top.classes.has(taxonomy.classKey)) top.classes.set(taxonomy.classKey, {meta: taxonomy, items: [], subclasses: new Map()});
    const group = top.classes.get(taxonomy.classKey);
    if (taxonomy.classKey === "aircraft") {
      const subclassKey = taxonomy.subclassKey || "general-purpose";
      if (!group.subclasses.has(subclassKey)) group.subclasses.set(subclassKey, {meta: taxonomy, items: []});
      group.subclasses.get(subclassKey).items.push(item);
    } else {
      group.items.push(item);
    }
  });
  const renderRows = (items) => items.map((item) => {
    const taxonomy = entityTaxonomy(item);
    const secondary = [...new Set([item.category || taxonomy.subclassLabel, item.subcategory, item.englishName || item.manufacturer || "来源未记录"].filter(Boolean))].join(" · ");
    return `<button class="entity-row ${state.selected === item.entityId ? "active" : ""}" data-entity="${esc(item.entityId)}" role="option" aria-selected="${state.selected === item.entityId}" title="${esc(`${item.preferredName} · ${secondary}`)}"><span class="entity-tree-branch" aria-hidden="true"></span><span class="entity-row-icon ${esc(taxonomy.iconClass)}">${entityIconMarkup(item)}</span><span><strong>${esc(item.preferredName)}</strong><small>${esc(secondary)}</small></span><em>${esc(item.reviewStatus)}</em></button>`;
  }).join("");
  const groupMarkup = [...groups.values()].sort((a,b) => a.meta.topLabel.localeCompare(b.meta.topLabel, "zh-CN")).map((top) => {
    const topKey = `top:${top.meta.topKey}`;
    const topCollapsed = state.collapsedCategories.has(topKey);
    const classMarkup = topCollapsed ? "" : [...top.classes.values()].sort((a,b) => a.meta.classLabel.localeCompare(b.meta.classLabel, "zh-CN")).map((subgroup) => {
      const classKey = `class:${top.meta.topKey}:${subgroup.meta.classKey}`;
      const classCollapsed = state.collapsedCategories.has(classKey);
      const classItems = subgroup.meta.classKey === "aircraft" ? [...subgroup.subclasses.values()].reduce((total, item) => total + item.items.length, 0) : subgroup.items.length;
      const classMeta = subgroup.meta.classKey === "aircraft" ? {...subgroup.meta, iconClass:"category-aircraft"} : subgroup.meta;
      const subclassMarkup = classCollapsed ? "" : subgroup.meta.classKey === "aircraft" ? [...subgroup.subclasses.values()].sort((a,b) => (equipmentSubclassOrder[a.meta.subclassKey] ?? 999) - (equipmentSubclassOrder[b.meta.subclassKey] ?? 999)).map((subclass) => {
        const subclassKey = `subclass:${top.meta.topKey}:${subgroup.meta.classKey}:${subclass.meta.subclassKey}`;
        const subclassCollapsed = state.collapsedCategories.has(subclassKey);
        const subclassMeta = {...subclass.meta, classLabel: subclass.meta.subclassLabel, iconClass: subclass.meta.subclassIconClass || subclass.meta.iconClass};
        return `<section class="entity-tree-subclass ${subclassCollapsed ? "collapsed" : ""}"><button type="button" class="entity-tree-subheading entity-tree-subclass-heading" data-category-toggle="${esc(subclassKey)}" aria-expanded="${!subclassCollapsed}"><span class="tree-caret" aria-hidden="true">${subclassCollapsed ? "›" : "⌄"}</span><span class="tree-category-icon ${esc(subclassMeta.iconClass)}">${ontologyIconMarkup(subclassMeta)}</span><strong>${esc(subclass.meta.subclassLabel || "通用任务型")}</strong><b>${subclass.items.length}</b></button><div class="entity-tree-items">${subclassCollapsed ? "" : renderRows(subclass.items)}</div></section>`;
      }).join("") : `<div class="entity-tree-items">${renderRows(subgroup.items)}</div>`;
      return `<section class="entity-tree-subgroup ${classCollapsed ? "collapsed" : ""}"><button type="button" class="entity-tree-subheading" data-category-toggle="${esc(classKey)}" aria-expanded="${!classCollapsed}"><span class="tree-caret" aria-hidden="true">${classCollapsed ? "›" : "⌄"}</span><span class="tree-category-icon ${esc(classMeta.iconClass)}">${ontologyIconMarkup(classMeta)}</span><strong>${esc(subgroup.meta.classLabel)}</strong><b>${classItems}</b></button><div class="entity-tree-subclasses">${subclassMarkup}</div></section>`;
    }).join("");
    const count = [...top.classes.values()].reduce((total, subgroup) => total + (subgroup.meta.classKey === "aircraft" ? [...subgroup.subclasses.values()].reduce((subtotal, subclass) => subtotal + subclass.items.length, 0) : subgroup.items.length), 0);
    return `<section class="entity-tree-group ${topCollapsed ? "collapsed" : ""}"><button type="button" class="entity-tree-heading" data-category-toggle="${esc(topKey)}" aria-expanded="${!topCollapsed}"><span class="tree-caret" aria-hidden="true">${topCollapsed ? "›" : "⌄"}</span><span class="tree-category-icon">${ontologyIconMarkup(top.meta)}</span><strong>${esc(top.meta.topLabel)}</strong><b>${count}</b></button><div class="entity-tree-classes">${classMarkup}</div></section>`;
  }).join("");
  $("#entity-list").innerHTML = groupMarkup || `<p class="empty">没有匹配的装备对象。</p>`;
  document.querySelectorAll("[data-entity]").forEach((button) => button.addEventListener("click", () => selectEntity(button.dataset.entity)));
  document.querySelectorAll("[data-category-toggle]").forEach((button) => button.addEventListener("click", () => {
    const group = button.dataset.categoryToggle;
    if (state.collapsedCategories.has(group)) state.collapsedCategories.delete(group); else state.collapsedCategories.add(group);
    renderEntities();
  }));
}

function latestRevision() { return state.detail?.revisions?.[0] || null; }
function attributeRows() {
  const doc = latestRevision()?.attributes;
  if (!doc) return [];
  return Object.entries(doc.attributes || {})
    .map(([key,bucket]) => ({key,bucket,value:bucket.values?.[0] || null,group:attributeGroupMap[key] || "research"}))
    .sort((a,b) => (attributeGroupOrder[a.group] ?? 99) - (attributeGroupOrder[b.group] ?? 99));
}
function evidenceRows() {
  const seen = new Set(); const rows = [];
  attributeRows().forEach(({key,bucket}) => (bucket.values || []).forEach((value) => (value.evidenceRefs || []).forEach((ref) => {
    const identity = `${key}|${ref.evidenceId}`; if (seen.has(identity)) return; seen.add(identity); rows.push({key,value,ref});
  })));
  return rows;
}
function combatRows() {
  return state.sources.filter((source) => source.sourceScope === "combat-event" || source.sourceScope === "loss-record").map((source) => ({
    kind: source.sourceScope === "combat-event" ? "实战事件" : "战损汇总",
    conflict: source.details?.conflict || source.details?.theater || "--",
    time: source.details?.time || "--",
    mission: source.details?.mission || source.details?.lossOrSorties || "--",
    result: source.details?.result || "--",
    loss: source.details?.loss || source.details?.defeatMechanism || "--",
    source,
  }));
}

function syncTabNavigation() {
  document.querySelectorAll("[data-tab]").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === state.tab)));
}

function capabilityRows() {
  return (state.dossier?.capabilities || []).flatMap((group) => group.records.map((record) => ({...record, groupLabel:group.label})));
}

function historyRows() {
  return state.dossier?.history?.events || [];
}

function evidenceCount(record) {
  return Array.isArray(record?.evidenceRefs) ? record.evidenceRefs.length : 0;
}

function classificationMarkup() {
  const dimensions = state.dossier?.classification || {};
  const labels = [["missionRoles", "任务角色"], ["airframeTypes", "机体形态"], ["platformLevels", "平台层级"], ["deploymentModes", "部署方式"]];
  const tags = labels.flatMap(([key, label]) => (dimensions[key] || []).map((value) => `<span title="${esc(label)}">${esc(value)}</span>`));
  return tags.length ? `<div class="classification-strip"><b>分类维度</b>${tags.join("")}</div>` : `<div class="classification-strip is-empty"><b>分类维度</b><span>待补充明确分类依据</span></div>`;
}

function dossierStatus(label = "待核验") {
  const kind = label === "已核验" ? "verified" : label === "存在冲突" ? "conflict" : "pending";
  return `<span class="dossier-status ${kind}">${esc(label)}</span>`;
}

function displayValue(value, unit) {
  const text = typeof value === "boolean" ? (value ? "是" : "否") : Array.isArray(value) ? value.map(fmt).join("、") : fmt(value);
  return `${text}${unit ? ` ${unit}` : ""}`;
}

function locatorLabel(locator = {}) {
  return [locator.sheetName, locator.cellRange, locator.lineStart != null ? `行 ${locator.lineStart}${locator.lineEnd != null && locator.lineEnd !== locator.lineStart ? `–${locator.lineEnd}` : ""}` : null, locator.page != null ? `页 ${locator.page}` : null].filter(Boolean).join(" / ") || "未提供定位";
}

function evidenceMarkup(refs = []) {
  return refs.length ? refs.map((ref) => `<div class="dossier-evidence"><div>${sourceLink(ref.sourceRef, ref.sourceName || ref.sourceRef || ref.evidenceId || "来源链接待补充")}<span>${esc(locatorLabel(ref.locator))}</span></div><p>${esc(ref.quote || "未提供原文引文")}</p>${ref.sourceVersion ? `<small>来源版本：${esc(ref.sourceVersion)}</small>` : ""}</div>`).join("") : `<p class="dossier-note">尚无关联证据。</p>`;
}

function lineageMarkup() {
  const edges = state.dossier?.history?.lineage || [];
  return `<details class="dossier-lineage" ${edges.length ? "open" : ""}><summary>型号沿袭 <b>${edges.length} 条</b><span>改型 / 衍生关系</span></summary>${edges.length ? edges.map((edge) => `<details class="lineage-item"><summary><span>${esc(edge.predecessorName)} <b>→</b> ${esc(edge.successorName)}</span><small>${esc(edge.familyName || "家族未记录")}</small><span class="dossier-status pending">沿袭待核验</span></summary><div class="dossier-detail"><p>原始沿袭链：${esc(edge.rawChain || "未记载")}</p><p>关系限定：${esc(edge.qualifier || "未记载")} · 身份匹配：${esc(edge.matchStatus || "待匹配")}</p><small>关系 ID：${esc(edge.lineageEdgeId)}</small></div></details>`).join("") : `<p class="dossier-note">暂无已登记的型号沿袭关系。</p>`}</details>`;
}

function renderHeader() {
  const item = state.detail;
  const revision = latestRevision();
  const doc = revision?.attributes;
  state.dossier = item ? buildDossier(item, doc || {}, state.sources, state.lineage, state.jobs, state.objectAgent?.candidates || []) : null;
  const taxonomy = entityTaxonomy(item || {});
  $("#object-icon").className = `category-icon ${taxonomy.iconClass}`; $("#object-icon").innerHTML = entityIconMarkup(item || {});
  $("#object-name").textContent = item?.preferredName || "选择一个装备对象";
  $("#object-status").className = `status ${item?.reviewStatus || "pending"}`; $("#object-status").textContent = item?.reviewStatus || "pending";
  $("#object-context").textContent = item ? ["实体档案工作区",taxonomy.classLabel,taxonomy.subclassLabel,item.englishName,item.manufacturer,item.originCountry].filter(Boolean).join(" · ") : "实体档案工作区 · 从左侧本体树进入属性、证据和核验任务。";
  const facts = [["上位类",item ? taxonomy.topLabel : null],["本体类",item ? taxonomy.classLabel : null],["任务分类",item ? taxonomy.subclassLabel : null],[taxonomy.topKey === "organization" ? "组织范围" : "制造商",item?.manufacturer || (taxonomy.topKey === "organization" ? item?.originCountry : null)],["原产国",item?.originCountry],["当前版本",item ? `Revision ${item.currentRevision}` : null]];
  $("#object-facts").innerHTML = facts.filter(([key,value]) => key !== "任务分类" || value !== taxonomy.classLabel).map(([key,value]) => `<span title="${esc(`${key}：${fmt(value)}`)}"><small>${key}</small><b>${esc(fmt(value))}</b></span>`).join("");
  $("#attribute-count").textContent = attributeRows().length; $("#capability-count").textContent = capabilityRows().filter((row) => row.value !== null).length; $("#evidence-count").textContent = evidenceRows().length; $("#signal-count").textContent = (state.signalRecords?.claims?.length || 0) + (state.signalRecords?.observations?.length || 0); $("#relation-count").textContent = state.relations.length; $("#lineage-count").textContent = state.lineage.length; $("#combat-count").textContent = historyRows().length; $("#audit-count").textContent = state.auditRun ? (state.auditRun.events?.length || 1) : 0; $("#review-count").textContent = (state.objectAgent?.candidates || []).filter((candidate) => !["rejected", "superseded", "published"].includes(candidate.status)).length + (state.objectAgent?.relationCandidates || []).filter((candidate) => !["rejected", "superseded", "published"].includes(candidate.status)).length; $("#version-count").textContent = item?.revisions?.length || 0;
  if(state.profile)$('#capability-count').textContent=state.profile.quality.presentFieldCount;
  syncTabNavigation();
  $("#create-plan").disabled = !item; $("#refresh-object").disabled = !item;
  renderMediaPanel();
  renderObjectWorkflow();
}

function renderObjectWorkflow() {
  const host = $("#object-workflow");
  if (!host) return;
  const item = state.detail;
  if(state.profile) { renderCapabilityWorkflow(host); renderRelationCandidatePanel(host); return; }
  const objectAgent = state.objectAgent || {};
  if (!item) {
    host.innerHTML = "";
    return;
  }
  const summary = objectAgent.summary || {};
  const candidates = objectAgent.candidates || [];
  const activeCandidates = candidates.filter((candidate) => ["under-review", "needs-evidence", "edited"].includes(candidate.status));
  const deepseekReady = state.agentState?.tools?.deepseek?.configured === true;
  const scopeOptions = [
    ["missing", "缺失/未知字段"],
    ["all", "全部属性范围"],
    ["performance", "性能参数"],
    ["systems", "任务系统"],
    ["combat", "作战记录"],
    ["imagery", "公开影像"],
  ];
  const candidateMarkup = activeCandidates.map((candidate) => `<article class="candidate-review-card"><div><b>${esc(attributeLabel(candidate.attributeKey))}</b><span>${esc(candidate.currentValue || "当前无值")} → <strong>${esc(candidate.proposedValue)}</strong></span><small>${esc(candidate.modelProvider || "DeepSeek")} · ${esc(candidate.status)} · ${esc(candidate.sourceJobId || "未绑定任务")}</small>${candidateEvidenceMarkup(candidate, candidates)}</div><div class="candidate-actions"><button type="button" data-candidate-action="accept" data-candidate-id="${esc(candidate.candidateId)}">采纳</button><button type="button" data-candidate-action="reject" data-candidate-id="${esc(candidate.candidateId)}">拒绝</button><button type="button" data-candidate-action="request-more-evidence" data-candidate-id="${esc(candidate.candidateId)}">补证</button></div></article>`).join("");
  const candidatePanel = activeCandidates.length ? `<details class="workflow-panel workflow-candidates"><summary title="检查属性名称、原值和候选值；采纳仅记录审核结果，尚未写入档案">③ 审核属性 <b>${activeCandidates.length}</b></summary><div class="candidate-review-list">${candidateMarkup}</div></details>` : "";
  const extractTitle = !summary.jobCount ? "请先点击“检索证据”获取材料" : deepseekReady ? "从已检索材料提取属性候选" : "DeepSeek v4 未配置，不能提取候选";
  host.innerHTML = `<div class="workflow-compact" aria-label="完善档案：检索证据、AI 提取属性、审核属性"><span class="workflow-label">完善档案</span><span class="workflow-count">已有属性 ${esc(fmt(summary.presentCount))}</span><span class="workflow-count is-gap">待补属性 ${esc(fmt(summary.missingCount))}</span><label class="workflow-scope"><span>补充范围</span><select id="attribute-scope">${scopeOptions.map(([value,label]) => `<option value="${value}">${label}</option>`).join("")}</select></label><button type="button" class="workflow-primary" data-object-workflow="collect" title="搜索当前对象的公开资料，保存属性证据">① 检索证据</button><button type="button" class="workflow-primary" data-object-workflow="extract" ${summary.jobCount && deepseekReady ? "" : "disabled"} title="${extractTitle}">② AI 提取属性</button>${candidatePanel || '<span class="workflow-review-empty" title="提取出候选属性后，在这里审核">③ 审核属性 · 暂无候选</span>'}</div>`;
  host.querySelectorAll("[data-object-workflow]").forEach((button) => button.addEventListener("click", () => runObjectWorkflow(button.dataset.objectWorkflow, button)));
  host.querySelectorAll("[data-candidate-action]").forEach((button) => button.addEventListener("click", () => reviewAttributeCandidate(button.dataset.candidateId, button.dataset.candidateAction, button)));
  renderRelationCandidatePanel(host);
}

function renderRelationCandidatePanel(host) {
  const candidates=(state.objectAgent?.relationCandidates || []).filter((candidate) => ["under-review","needs-evidence","accepted"].includes(candidate.status));
  if (!candidates.length) return;
  const rows=candidates.map((candidate) => `<article class="candidate-review-card relation-candidate-card"><div><b>${esc(candidate.targetName)}</b><span>→ ${esc(candidate.predicate)}</span><small>${esc(candidate.targetEntityId || "目标未匹配")} · ${esc(candidate.status)} · ${esc(candidate.sourceJobId || "未绑定任务")}</small></div><div class="candidate-actions">${candidate.status !== "accepted" ? `<button type="button" data-relation-action="accept" data-relation-id="${esc(candidate.candidateId)}">采纳</button><button type="button" data-relation-action="request-more-evidence" data-relation-id="${esc(candidate.candidateId)}">补证</button>` : `<button type="button" data-relation-action="publish" data-relation-id="${esc(candidate.candidateId)}">发布关系</button>`}</div></article>`).join("");
  const panel=document.createElement("details"); panel.className="workflow-panel workflow-candidates relation-candidates"; panel.open=false; panel.innerHTML=`<summary title="关系/谓词候选需先人工审核；发布后才进入正式关系断言">④ 审核关系 <b>${candidates.length}</b></summary><div class="candidate-review-list">${rows}</div>`;
  host.appendChild(panel);
  panel.querySelectorAll("[data-relation-action]").forEach((button)=>button.addEventListener("click",()=>reviewRelationCandidate(button.dataset.relationId,button.dataset.relationAction,button)));
}

function renderMediaPanel() {
  const item = state.detail;
  const objectAgent = state.objectAgent || {};
  const imageSources = state.sources.filter((source) => source.sourceScope === "image" && safeHref(source.sourceUrl));
  const panel = $("#media-panel");
  if (!panel) return;
  const lineage = state.lineage;
  const actions = objectAgent.actions || [
    {action:"plan", label:"生成补证计划"},
    {action:"combat", label:"搜索作战记录"},
    {action:"performance", label:"核验性能参数"},
    {action:"systems", label:"核验任务系统"},
    {action:"imagery", label:"查找公开影像"},
  ];
  const researchButtons = actions.map(({action, label, status, recommended}) => `<button type="button" class="agent-tool-button ${recommended ? "recommended" : ""}" data-agent-action="${esc(action)}" ${item ? "" : "disabled"}>${esc(label)}${status ? `<small>${esc(status)}</small>` : ""}</button>`).join("");
  const summary = objectAgent.summary || {};
  panel.innerHTML = `<div class="agent-tool-card"><div class="media-panel-heading"><b>对象研究 Agent</b><span>${esc(item?.preferredName || "未选择")}</span></div><p>${esc(objectAgent.answer || "选择对象后，Agent 会读取对象档案、字段缺口、来源和核验任务，再编排开源补证。")}</p><div class="object-agent-metrics"><span>已登记字段 <b>${esc(fmt(summary.presentCount))}</b></span><span>字段缺口 <b>${esc(fmt(summary.missingCount))}</b></span><span>来源 <b>${esc(fmt(summary.sourceCount))}</b></span><span>核验任务 <b>${esc(fmt(summary.jobCount))}</b></span><span>关系候选 <b>${esc(fmt(summary.relationCandidateCount || 0))}</b></span></div><div class="agent-tool-grid">${researchButtons}</div><div class="agent-tool-status"><span>数据库</span><b>已连接</b><span>DeepSeek v4</span><b>${state.agentState?.tools?.deepseek?.configured ? "可草拟" : "未配置"}</b><span>Firecrawl</span><b>按任务触发</b></div></div><div class="media-view media-image-view"><span class="media-placeholder-icon">◈</span><b>${imageSources.length ? "原始图片来源" : "暂无图片来源"}</b>${imageSources.length ? `<div class="media-source-links">${imageSources.map((source) => sourceLink(source.sourceUrl, source.sourceName)).join("")}</div>` : "<small>采编来源中尚未登记有效图片链接</small>"}</div><div class="media-view media-model-view"><div id="dynamic-model-viewer" class="dynamic-model-viewer" aria-label="数据驱动动态模型"></div></div><div class="lineage-panel"><div class="media-panel-heading"><b>改型血缘</b><span>${lineage.length} 条</span></div>${lineage.length ? lineage.slice(0,5).map((edge) => `<div class="lineage-row" title="${esc(edge.rawChain)}"><span>${esc(edge.predecessorName)} → ${esc(edge.successorName)}</span><i>${esc(edge.familyName)}</i></div>`).join("") : `<small>Excel 未登记或尚待身份匹配</small>`}</div>`;
  const modelHost = panel.querySelector("#dynamic-model-viewer");
  if (modelHost && item && !panel.closest('.dossier-workspace')) {
    mountDynamicUavViewer(modelHost, {
      entity: item,
      attributes: latestRevision()?.attributes?.attributes || latestRevision()?.attributes || {},
      candidates: [...(objectAgent.candidates || []), ...(state.manufacturing?.candidates || [])],
      assembly: state.manufacturing?.assembly || [],
      sources: state.sources,
      factorGraph: state.factorGraph,
    });
  }
  panel.querySelectorAll("[data-agent-action]").forEach((button) => button.addEventListener("click", () => runObjectAgentAction(button.dataset.agentAction, button)));
}

function matchRow(...values) { const q = state.rowQuery.toLowerCase(); return !q || values.join(" ").toLowerCase().includes(q); }
function renderCapabilityWorkflow(host) {
  if(state.profile.objectRevision<1){host.innerHTML='<div class="cap-run-tools"><small>此实体仅登记身份与沿袭，尚无装备档案版本；能力值待归档。</small></div>';return;}
  const run=state.auditRun || state.capabilityRun;
  const labels={queued:'排队中',planning:'规划中',collecting:'采集中',extracting:'属性提取中',reconciling:'校验与归并中',failed:'执行失败',cancelled:'已取消','awaiting-review':'采编完成 · 候选待审核','awaiting-human-review':'采编完成 · 候选待审核','completed-with-gaps':'采编完成 · 仍有证据缺口',completed:'采编完成'};
  const active=run && ['queued','planning','collecting','extracting','reconciling','running'].includes(run.status);
  const runId=run?.auditRunId || run?.runId;
  const events=run?.events || [];
   host.innerHTML=`<div class="cap-run-tools"><b>实体审计</b><select id="cap-run-domain" aria-label="选择能力补证范围"><option value="all">全部能力域</option>${state.profile.capabilities.map(c=>`<option value="${c.code}">${esc(c.label)}</option>`).join('')}</select><button class="mini-action" id="cap-run-start" ${active?'disabled':''}>${active?'自动执行中…':'启动实体审计'}</button>${run?`<span class="cap-run-state">${esc(labels[run.status]||run.status)} · ${esc(new Date(run.createdAt).toLocaleTimeString())}${run.result?.durationSeconds!=null?` · ${run.result.durationSeconds} 秒`:''}${run.result?.attributeCandidates!=null?` · 候选 ${run.result.attributeCandidates}`:''}</span><details class="cap-run-trace"><summary>执行记录 ${events.length}</summary>${events.map(e=>{const d=e.detail||{};return `<div><time>${esc(new Date(e.createdAt).toLocaleTimeString())}</time><b>${esc({trigger:'触发',planning:'规划',plan:'计划',collecting:'正文采集',collection:'正文采集',extracting:'属性提取',extraction:'属性提取',reconciling:'校验归并',validation:'结构校验',profile:'能力画像',audit:'审计结果',publication:'发布',failure:'失败',recovery:'恢复'}[e.stage]||e.stage)}</b><span>${esc(e.status)}</span><small>${esc(d.target||d.stopReason||d.error||d.reason||'')}${d.items!=null?` · ${d.items} 材料`:''}${d.candidateCount!=null?` · ${d.candidateCount} 候选`:''}${d.attributeCandidates!=null?` · 属性候选 ${d.attributeCandidates}`:''}${d.relationCandidates!=null?` · 关系候选 ${d.relationCandidates}`:''}</small></div>`;}).join('')}</details>`:'<small>读取档案缺口 → Firecrawl 定向采集 → DeepSeek 提取 → 校验归并 → 人工审核 → 发布修订</small>'}</div>`;
  const select=host.querySelector('#cap-run-domain');select.value=state.capabilityGroup==='all'?'platform':state.capabilityGroup;
  host.querySelector('#cap-run-start').addEventListener('click',async()=>{
    const selected=state.selected,code=select.value;
    const codes=code==='all'?state.profile.capabilities.map(c=>c.code):[code];
    const button=host.querySelector('#cap-run-start');button.disabled=true;
    try {
       const data=await api(`/api/ontology/entities/${encodeURIComponent(selected)}/audit-runs`,{method:'POST',body:JSON.stringify({trigger:'manual',capabilityCodes:codes,maxJobs:code==='all'?7:2,maxModelCalls:code==='all'?8:2,maxElapsedSeconds:900,maxResponseBytes:4000000})});
      if(state.selected!==selected)return;
       state.auditRun=data.auditRun;renderObjectWorkflow();pollAuditRun(data.auditRun.auditRunId,selected);
    }catch(error){toast(error.message,'error');button.disabled=false;}
  });
}
async function pollAuditRun(auditId,entityId) {
  if(state.selected!==entityId)return;
  try {
    const data=await api(`/api/ontology/audit-runs/${encodeURIComponent(auditId)}`);
    if(state.selected!==entityId)return;
    state.auditRun=data.auditRun;renderObjectWorkflow();
    if(['queued','planning','collecting','extracting','reconciling'].includes(data.auditRun.status)){setTimeout(()=>pollAuditRun(auditId,entityId),2000);return;}
    const profile=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/capability-profile`);
    if(state.selected!==entityId)return;
    state.profile=profile.profile;renderHeader();renderContent();
  } catch(error){toast(error.message,'error');}
}
async function pollCapabilityRun(runId,entityId) {
  if(state.selected!==entityId)return;
  try{
    const data=await api(`/api/ontology/capability-runs/${encodeURIComponent(runId)}`);
    if(state.selected!==entityId)return;
    state.capabilityRun=data.run;renderObjectWorkflow();
    if(['queued','running'].includes(data.run.status)){setTimeout(()=>pollCapabilityRun(runId,entityId),2000);return;}
    const profile=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/capability-profile`);
    if(state.selected!==entityId)return;
    state.profile=profile.profile;renderHeader();renderContent();
  }catch(error){toast(error.message,'error');}
}
function renderCapabilities() {
  if(!state.profile){renderLegacyCapabilities();return;}
  const p=state.profile,host=$('#workbench-content');
  $('#view-summary').textContent=`${p.capabilities.length} 能力域 · ${p.quality.presentFieldCount}/${p.quality.expectedFieldCount} 字段有值 · ${p.quality.verifiedCount} 条核验`;
  host.innerHTML=capabilityMarkup(p,{group:state.capabilityGroup,query:state.rowQuery,view:state.capabilityView});
  host.querySelectorAll('[data-cap-group]').forEach(b=>b.addEventListener('click',()=>{state.capabilityGroup=b.dataset.capGroup;renderCapabilities();}));
  host.querySelectorAll('[data-cap-view]').forEach(b=>b.addEventListener('click',()=>{state.capabilityView=b.dataset.capView;renderCapabilities();}));
  host.querySelectorAll('[data-cap-review]').forEach(b=>b.addEventListener('click',()=>openCapabilityReview(b.dataset.capReview)));
  host.querySelectorAll('[data-cap-record]').forEach(b=>b.addEventListener('click',()=>{if(!b.dataset.capRecord)return;const m=p.capabilities.flatMap(c=>c.metrics).find(m=>m.id===b.dataset.capRecord);if(m)openCapabilityReview(m.id,true);}));
  host.querySelector('[data-cap-evolution]')?.addEventListener('click',loadCapabilityEvolution);
  bindCapabilityGraph(host);
}
async function loadCapabilityEvolution() {
  const selected=state.selected;
  const ids=[selected,...state.profile.lineage.flatMap(e=>[e.predecessor_entity_id,e.successor_entity_id])].filter(Boolean);
  const target=$('#cap-evolution-result');target.textContent='正在校验同系指标口径…';
  try{
    const key=$('[data-cap-evolution-field]')?.value || 'endurance';
    const [data,history]=await Promise.all([api(`/api/ontology/capability-compare?entityIds=${encodeURIComponent([...new Set(ids)].slice(0,10).join(','))}&attributeKey=${encodeURIComponent(key)}`),api(`/api/ontology/entities/${encodeURIComponent(selected)}/capability-profile-history?attributeKey=${encodeURIComponent(key)}`)]);
    if(state.selected!==selected)return;
    const c=data.comparison;
    target.innerHTML=`<div class="cap-chart-header"><b>${esc(c.label)} · 可比 ${c.included.length} · 排除 ${c.excluded.length}</b><small>同系沿袭不自动等于能力提升</small></div>${c.cohorts.map(cohort=>{const max=Math.max(...cohort.records.map(r=>r.record.value),1);return `<div class="cap-chart-header"><small>样本 ${cohort.sampleSize} · ${esc(cohort.context.join(' / '))}</small></div>${cohort.records.map(r=>`<details><summary class="cap-coverage-row"><b>${esc(r.name)}</b><meter min="0" max="${max}" value="${r.record.value}"></meter><span>${r.record.value} ${esc(r.record.unit)}</span></summary>${metricDetail(r.record)}</details>`).join('')}`;}).join('')}${c.excluded.map(r=>`<details><summary class="cap-lineage"><b>${esc(r.name)}</b><span>${esc(r.record.rawValue ?? '未知')}</span><small>${esc(r.record.comparability.reasons.join('；'))}</small></summary>${metricDetail(r.record)}</details>`).join('')}<div class="cap-chart-header"><b>当前对象的档案版本</b><small>保存时间；性能变更发生时间以原文为准</small></div>${history.history.snapshots.map(s=>`<details class="cap-version"><summary><b>版本 ${s.objectRevision}</b><time>${esc(new Date(s.createdAt).toLocaleString())}</time><span>${esc(s.records.map(m=>m.rawValue ?? '未知').join(' / '))}</span></summary><small>画像 ${esc(s.profileId)}</small>${s.records.map(metricDetail).join('')}</details>`).join('') || '<p class="empty">尚未保存能力画像快照。</p>'}`;
  }catch(error){target.textContent=error.message;}
}
function openCapabilityReview(recordId,readOnly=false) {
  const m=state.profile.capabilities.flatMap(c=>c.metrics).find(m=>m.id===recordId);
  if(!m)return;
  document.querySelector('#cap-review-dialog')?.remove();
  const dialog=document.createElement('dialog');dialog.id='cap-review-dialog';dialog.className='cap-review-dialog';
  const entityId=state.selected,displayedProfile=state.profile;
  const reviewable=!readOnly && ['observed','conflict'].includes(m.claimState) && m.origin==='candidate' && m.reviewStatus!=='published' && m.evidenceRefs.some(r=>r.snapshotMatched&&r.sourceType!=='workbook');
  dialog.innerHTML=`<form method="dialog"><div class="cap-dialog-head"><b>${esc(m.label)} · ${esc(m.rawValue)}</b><button aria-label="关闭">×</button></div>${metricDetail(m)}${reviewable?`${conflictReviewMarkup(displayedProfile,m)}<label>审核人<input id="cap-reviewer" required autocomplete="name"></label><label>审核依据<textarea id="cap-reason" required placeholder="说明原文如何支持此值、型号归属以及适用条件"></textarea></label><label class="cap-verified-check"><input id="cap-confidence-confirm" type="checkbox">我已核对原文、型号归属及冲突，确认此条事实证据可信</label><div class="cap-dialog-actions"><button type="button" data-cap-decision="request-more-evidence">需要补证</button><button type="button" data-cap-decision="reject">拒绝</button><button type="button" data-cap-decision="accept">审核通过</button><button type="button" id="cap-publish" disabled>发布到本体库</button></div><small id="cap-review-result" role="status">审核仅记录决定；发布需单独点击。</small>`:''}</form>`;
  document.body.append(dialog);dialog.showModal();
  let snapshot=null;
  dialog.querySelectorAll('[data-cap-decision]').forEach(b=>b.addEventListener('click',async()=>{
    const reviewer=dialog.querySelector('#cap-reviewer').value.trim(),reason=dialog.querySelector('#cap-reason').value.trim(),action=b.dataset.capDecision;
    if(!reviewer||!reason){dialog.querySelector('#cap-review-result').textContent='请填写审核人和依据。';return;}
    if(action==='accept'&&!dialog.querySelector('#cap-confidence-confirm').checked){dialog.querySelector('#cap-review-result').textContent='请先核对来源并勾选事实核验。';return;}
    let resolution;
    if(action==='accept'&&m.claimState==='conflict'){
      const reason=dialog.querySelector('#cap-conflict-reason').value.trim();
      if(!dialog.querySelector('#cap-conflict-confirm').checked||!reason){dialog.querySelector('#cap-review-result').textContent='请对照全部来源值，填写冲突处置依据并确认。';return;}
      resolution={competingRecordIds:conflictPeers(displayedProfile,m).map(r=>r.id),reason};
    }
    b.disabled=true;
    try{
      if(!snapshot){const data=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/capability-profile`,{method:'POST',body:'{}'});if(data.profile.contentHash!==displayedProfile.contentHash)throw Error('资料已经更新，请关闭窗口并刷新画像后重新核对。');snapshot=data.profile;}
      await api(`/api/ontology/capability-profiles/${snapshot.profileId}/review`,{method:'POST',body:JSON.stringify({recordId,action,reviewer,reason,resolution,verifiedConfidence:action==='accept'?'high':null})});
      dialog.querySelector('#cap-publish').disabled=action!=='accept';dialog.querySelector('#cap-review-result').textContent=action==='accept'?'审核已记录，可发布此条事实。':'审核已记录。';
    }catch(error){dialog.querySelector('#cap-review-result').textContent=error.message;}finally{b.disabled=false;}
  }));
  dialog.querySelector('#cap-publish')?.addEventListener('click',async(e)=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      await api(`/api/ontology/capability-profiles/${snapshot.profileId}/publish`,{method:'POST',body:JSON.stringify({recordIds:[recordId],publishedBy:dialog.querySelector('#cap-reviewer').value.trim(),reason:dialog.querySelector('#cap-reason').value.trim()})});
      dialog.querySelector('#cap-review-result').textContent='已发布到本体库，证据与审核记录已保留。';
      const data=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/capability-profile`);if(state.selected===entityId){state.profile=data.profile;renderHeader();renderCapabilities();}
    }catch(error){dialog.querySelector('#cap-review-result').textContent=error.message;button.disabled=false;}
  });
}
function renderLegacyCapabilities() {
  const allRows = capabilityRows();
  const rows = allRows.filter((row) => matchRow(row.groupLabel, row.attributeKey, attributeLabel(row.attributeKey), row.rawValue, row.value, row.unit, row.conditions, row.stage, row.statusLabel));
  const groups = capabilityGroups.map((group) => ({...group, rows: rows.filter((row) => row.groupId === group.id)})).filter((group) => group.rows.length);
  const body = groups.map((group) => `<div class="capability-group-heading"><b>${esc(group.label)}</b><span>${group.rows.length} 项</span></div>${group.rows.map((row) => {
    const candidates = (state.objectAgent?.candidates || []).filter((candidate) => candidate.attributeKey === row.attributeKey && !["rejected", "superseded"].includes(candidate.status));
    const linkedEvents = historyRows().filter((event) => row.relatedEventIds.includes(event.eventId) || event.relatedCapabilityIds?.includes(row.capabilityId) || event.relatedCapabilityIds?.includes(row.recordId));
    return `<details class="dossier-record" data-record-id="${esc(row.recordId)}"><summary class="data-row capability-grid"><span class="cell-key" title="${esc(row.attributeKey)}">${esc(attributeLabel(row.attributeKey))}</span><span class="cell-value" title="${esc(displayValue(row.value, row.unit))}">${esc(displayValue(row.value, row.unit))}</span><span title="${esc(fmt(row.conditions))}">${esc(row.conditions == null ? "条件未记载" : fmt(row.conditions))}</span><span>${esc(row.stage)}</span><span>${evidenceCount(row)} 条</span><span>${dossierStatus(row.statusLabel)}</span></summary><div class="dossier-detail"><div class="dossier-detail-facts"><span>原始值：${esc(fmt(row.rawValue))}</span><span>有效时间：${esc(fmt(row.validTime))}</span><span>提取置信度：${esc(fmt(row.confidence))}</span><span>属性审核：${esc(row.reviewStatus || "未登记")}</span></div><p class="dossier-note">${row.claimKind === "inference" ? "本条为推断，需与公开事实分开使用。" : "此处保留来源记载；单个技术参数或载荷清单不等同于经过验证的任务能力。"}</p>${evidenceMarkup(row.evidenceRefs)}${candidates.length ? `<div class="dossier-candidates"><b>候选值（尚未入档）</b>${candidates.map((candidate) => `<p>${esc(fmt(candidate.proposedValue))} · ${esc(candidate.status === "accepted" ? "已采纳，待发布" : "待审核 / 补证")}</p>`).join("")}</div>` : ""}<div class="dossier-links"><b>关联履历</b>${linkedEvents.length ? linkedEvents.map((event) => `<button type="button" class="mini-action" data-event-link="${esc(event.eventId)}">${esc(event.label)}</button>`).join("") : "<span>未登记明确关联</span>"}</div><small>观测 ID：${esc(row.recordId)} · 档案版本：${esc(fmt(row.objectRevision))}</small></div></details>`;
  }).join("")}`).join("") || `<p class="empty">没有匹配的能力指标。</p>`;
  const verified = allRows.filter((row) => row.verificationStatus === "verified").length;
  const present = allRows.filter((row) => row.value !== null).length;
  $("#view-summary").textContent = `${rows.length} 条指标记录 · 已登记 ${present} · 已核验 ${verified}`;
  $("#workbench-content").innerHTML = `<div class="capability-overview"><span>来源值 · 适用条件 · 证据阶段 · 核验状态，点击任一行查看依据</span><b>已登记 ${present} · 已核验 ${verified}</b></div><div class="data-head capability-grid"><span>能力指标</span><span>值 / 单位</span><span>适用条件</span><span>证据阶段</span><span>证据</span><span>核验状态</span></div>${body}`;
  bindDossierLinks();
}
function renderAttributes() {
  const allRows = attributeRows();
  const counts = Object.fromEntries(attributeGroups.map(([group]) => [group, group === "all" ? allRows.length : allRows.filter((row) => row.group === group).length]));
  const rows = allRows.filter(({group,key,bucket,value}) => (state.attributeGroup === "all" || group === state.attributeGroup) && matchRow(key,bucket.state,value?.rawValue,value?.normalizedValue,value?.unit));
  $("#view-summary").textContent = `${rows.length} 个属性字段 · 原始值与规范值并列显示`;
  const body = rows.map(({key,bucket,value}) => {
    const locator = value?.evidenceRefs?.[0]?.locator; const where = locator ? `${locator.sheetName} / ${locator.cellRange}` : "--";
    return `<div class="data-row attribute-grid"><span class="cell-key" title="${esc(key)}">${esc(attributeLabel(key))}</span><span class="cell-state">${esc(stateLabel(bucket.state))}</span><span class="${value ? "cell-value" : "cell-unknown"}" title="${esc(fmt(value?.rawValue || "未知"))}">${esc(fmt(value?.rawValue || "未知"))}</span><span title="${esc(fmt(value?.normalizedValue))}">${esc(fmt(value?.normalizedValue))}</span><span>${esc(fmt(value?.unit))}</span><span>${esc(fmt(value?.confidence))}</span><span title="${esc(where)}">${esc(where)}</span><span>${status(value?.reviewStatus || "pending")}</span></div>`;
  }).join("") || `<p class="empty">没有匹配的属性。</p>`;
  const groupTabs = attributeGroups.map(([group,label]) => `<button type="button" class="attribute-group-tab" data-attribute-group="${group}" aria-selected="${state.attributeGroup === group}">${label}<b>${counts[group]}</b></button>`).join("");
  $("#workbench-content").innerHTML = `${classificationMarkup()}<div class="archive-overview"><span>装备身份、型号沿袭与技术事实</span><b>${allRows.length} 个字段 · ${state.lineage.length} 条沿袭</b></div>${lineageMarkup()}<div class="attribute-groups" role="tablist" aria-label="属性分组"><button type="button" class="attribute-group-nav" data-attribute-scroll="left" aria-label="向左滚动属性分组">‹</button><div class="attribute-group-tab-strip">${groupTabs}</div><button type="button" class="attribute-group-nav" data-attribute-scroll="right" aria-label="向右滚动属性分组">›</button></div><div class="data-head attribute-grid"><span>属性</span><span>数据状态</span><span>原始值</span><span>规范值</span><span>单位</span><span>置信度</span><span>证据定位</span><span>审核</span></div>${body}`;
  document.querySelectorAll("[data-attribute-group]").forEach((button) => button.addEventListener("click", () => { state.attributeGroup = button.dataset.attributeGroup; renderAttributes(); }));
  const tabStrip = document.querySelector(".attribute-group-tab-strip");
  document.querySelectorAll("[data-attribute-scroll]").forEach((button) => button.addEventListener("click", () => tabStrip?.scrollBy({left: button.dataset.attributeScroll === "left" ? -220 : 220, behavior: "smooth"})));
}
function renderEvidence() {
  const rows = evidenceRows().filter(({key,value,ref}) => matchRow(key,value.rawValue,ref.quote,ref.sourceRef,ref.locator?.cellRange));
  const sourceRows = state.sources.filter((source) => matchRow(sourceScopeLabel(source.sourceScope), source.sourceName, source.citationText, source.sourceUrl, source.sheetName, source.cellRange, source.subjectName));
  $("#view-summary").textContent = `${rows.length} 条属性证据 · ${sourceRows.length} 条采编来源`;
  const body = rows.map(({key,value,ref}) => `<div class="data-row evidence-grid"><span class="cell-key">${esc(key)}</span><span>${esc(ref.sourceType)}</span><span title="${esc(ref.quote)}">${esc(ref.quote)}</span><span title="${esc(ref.sourceRef)}">${esc(ref.sourceRef)}</span><span>${esc(ref.locator ? `${ref.locator.sheetName || ""} ${ref.locator.cellRange || ""}` : "--")}</span><span>${status(value.reviewStatus)}</span></div>`).join("") || `<p class="empty">没有匹配的证据。</p>`;
  const sourceBody = sourceRows.map((source) => `<div class="data-row source-grid"><span>${esc(sourceScopeLabel(source.sourceScope))}</span><span class="cell-key">${esc(source.subjectName || "工作簿级")}</span><span title="${esc(source.citationText)}">${source.sourceUrl ? sourceLink(source.sourceUrl, source.sourceName) : esc(source.sourceName)}</span><span title="${esc(source.citationText)}">${esc(source.citationText)}</span><span>${esc(`${source.sheetName || ""} / ${source.cellRange || ""}`)}</span><span>${status(source.sourceReliability)} ${status(source.verificationStatus)}</span></div>`).join("") || `<p class="empty">没有匹配的采编来源。</p>`;
  $("#workbench-content").innerHTML = `<section class="evidence-section"><div class="audit-section-heading"><b>属性证据</b><span>${rows.length} 条</span></div><div class="data-head evidence-grid"><span>属性键</span><span>来源类型</span><span>引文</span><span>来源</span><span>定位</span><span>审核</span></div>${body}</section><section class="evidence-section"><div class="audit-section-heading"><b>采编来源</b><span>${sourceRows.length} 条</span></div><div class="data-head source-grid"><span>来源类型</span><span>对象</span><span>来源</span><span>引文/说明</span><span>Excel 定位</span><span>状态</span></div>${sourceBody}</section>`;
}
function sourceScopeLabel(scope) { return ({identity:"身份来源", "combat-event":"实战记录（Excel）", "loss-record":"战损数据（Excel）", image:"图片来源", "workbook-general":"工作簿说明"}[scope] || scope); }
function renderSources() {
  const rows = state.sources.filter((source) => matchRow(sourceScopeLabel(source.sourceScope), source.sourceName, source.citationText, source.sourceUrl, source.sheetName, source.cellRange, source.subjectName));
  $("#view-summary").textContent = `${rows.length} 条采编来源 · 外部事实仍需核验`;
  const body = rows.map((source) => `<div class="data-row source-grid"><span>${esc(sourceScopeLabel(source.sourceScope))}</span><span class="cell-key">${esc(source.subjectName || "工作簿级")}</span><span title="${esc(source.citationText)}">${source.sourceUrl ? sourceLink(source.sourceUrl, source.sourceName) : esc(source.sourceName)}</span><span title="${esc(source.citationText)}">${esc(source.citationText)}</span><span>${esc(`${source.sheetName} / ${source.cellRange}`)}</span><span>${status(source.sourceReliability)} ${status(source.verificationStatus)}</span></div>`).join("") || `<p class="empty">没有匹配的采编来源。</p>`;
  $("#workbench-content").innerHTML = `<div class="data-head source-grid"><span>来源类型</span><span>对象</span><span>来源</span><span>引文/说明</span><span>Excel 定位</span><span>状态</span></div>${body}`;
}
function renderRelations() {
  const selected = state.selected;
  const rows = state.relations.filter((relation) => matchRow(relation.predicate, relation.fromName, relation.toName, relation.qualifier, relation.claimKind, relation.reviewStatus));
  $("#view-summary").textContent = `${rows.length} 条关联关系 · 方向与证据均来自候选本体断言`;
  const body = rows.map((relation) => {
    const outgoing = relation.fromEntityId === selected;
    const subject = outgoing ? relation.fromName : relation.toName;
    const target = outgoing ? relation.toName : relation.fromName;
    const direction = outgoing ? "出边" : "入边";
    const evidence = Array.isArray(relation.evidenceIds) ? relation.evidenceIds.join("、") : "--";
    const qualifier = typeof relation.qualifier === "object" ? Object.entries(relation.qualifier).map(([key, value]) => `${key}=${fmt(value)}`).join("；") : fmt(relation.qualifier);
    return `<div class="data-row relation-grid"><span class="cell-key" title="${esc(relation.relationId)}">${esc(direction)}</span><span>${esc(subject || "未知主体")}</span><span class="cell-value" title="${esc(relation.predicate)}">${esc(relation.predicate || "关系未命名")}</span><span>${esc(target || "未知对象")}</span><span title="${esc(qualifier)}">${esc(qualifier || "无限定")}</span><span title="${esc(evidence)}">${esc(Array.isArray(relation.evidenceIds) && relation.evidenceIds.length ? `${relation.evidenceIds.length} 条证据` : "无证据")}</span><span>${status(relation.reviewStatus || "pending")}</span></div>`;
  }).join("") || `<p class="empty">当前对象没有登记的实体关联关系。</p>`;
  $("#workbench-content").innerHTML = `<div class="data-head relation-grid"><span>方向</span><span>主体</span><span>谓词</span><span>关联对象</span><span>限定条件</span><span>证据</span><span>审核</span></div>${body}`;
}
function renderCombat() {
  const group = state.historyGroup || "all";
  const allRows = historyRows();
  const rows = allRows.filter((row) => (group === "all" || row.eventType === group) && matchRow(row.label, row.eventType, row.eventTime, row.mission, row.reportedResult, row.loss, row.location, row.stage));
  const groupTabs = historyGroups.map((item) => `<button type="button" class="history-group-tab" data-history-group="${item.id}" aria-selected="${group === item.id}">${item.label}<b>${item.id === "all" ? allRows.length : allRows.filter((row) => row.eventType === item.id).length}</b></button>`).join("");
  const body = rows.map((row) => {
    const details = [row.mission && `任务：${row.mission}`, row.reportedResult && `结果：${row.reportedResult}`, row.loss && `损失：${row.loss}`, row.participants?.length && `参与方：${row.participants.join("、")}`, row.relatedCapabilityIds?.length && `关联能力：${row.relatedCapabilityIds.length} 项`].filter(Boolean);
    const evidence = row.evidenceRefs?.[0];
    return `<div class="data-row history-grid"><span class="cell-key">${esc(row.label || row.eventType)}</span><span>${esc(row.eventTime || "时间未记载")}</span><span title="${esc(row.location)}">${esc(row.location || "地点未记载")}</span><span title="${esc(row.reportedResult || row.mission)}">${esc(row.reportedResult || row.mission || "事件事实未记载")}</span><span>${esc(row.stage)}</span><span>${status(row.verificationStatus === "verified" ? "verified" : row.verificationStatus === "conflict" ? "conflict" : "pending")} ${esc(verificationLabel(row))}</span><span>${evidence?.sourceRef ? sourceLink(evidence.sourceRef, "来源 ↗") : esc(evidence?.sourceName || "来源未链接")}</span>${details.length ? `<details class="history-detail"><summary>详情</summary><div>${details.map((item) => `<span>${esc(item)}</span>`).join("")}</div></details>` : ""}</div>`;
  }).join("") || `<p class="empty">暂无已记录履历；时间未记载的来源不会被伪造为事件日期。</p>`;
  const lineageMarkup = state.dossier?.history?.lineage?.length ? state.dossier.history.lineage.map((edge) => `<div class="history-lineage-row"><span>${esc(edge.predecessorName)} <b>→</b> ${esc(edge.successorName)}</span><small>${esc(edge.familyName || "型号家族")}${edge.rawChain ? ` · ${esc(edge.rawChain)}` : ""}</small></div>`).join("") : `<p class="history-empty">暂无已匹配的型号沿袭记录。</p>`;
  $("#view-summary").textContent = `${rows.length} 条履历 · ${state.lineage.length} 条型号沿袭 · 日期只采用来源明确记载`;
  $("#workbench-content").innerHTML = `<div class="history-filters" role="tablist" aria-label="履历分类">${groupTabs}</div><section class="history-lineage"><div class="history-section-heading"><b>型号沿袭关系</b><span>${state.lineage.length} 条</span></div>${lineageMarkup}</section><div class="history-section-heading history-combat-heading"><b>事件履历</b><span>${rows.length} 条</span></div><div class="data-head history-grid"><span>事件</span><span>时间</span><span>地点/战区</span><span>结果/任务</span><span>证据阶段</span><span>核验</span><span>来源</span></div>${body}`;
  document.querySelectorAll("[data-history-group]").forEach((button) => button.addEventListener("click", () => { state.historyGroup = button.dataset.historyGroup; renderCombat(); }));
}
function renderJobs() {
  const rows = state.jobs.filter((job) => matchRow(job.targetType,job.query,job.status,job.sourceScopeIds?.join(" ")));
  $("#view-summary").textContent = `${rows.length} 个定向任务 · Firecrawl 结果不会自动确认属性`;
  const body = rows.map((job) => `<div class="data-row job-grid"><span class="cell-key">${esc(job.targetType)}</span><span>${verificationStatus(job)}</span><span title="${esc(job.query)}">${esc(job.query)}</span><span title="${esc((job.sourceScopeIds||[]).join(", "))}">${esc((job.sourceScopeIds||[]).join(", "))}</span><span>${job.result?.items?.length ?? "--"}</span><span><button class="mini-action" data-run-job="${esc(job.verificationId)}" ${job.status === "running" ? "disabled" : ""}>${job.stopReason === "stale-timeout" ? "从断点重试" : (job.status === "planned" || job.status === "failed" ? "执行" : "重新核验")}</button></span></div>`).join("") || `<p class="empty">尚未生成核验任务。</p>`;
  $("#workbench-content").innerHTML = `<div class="data-head job-grid"><span>核验类型</span><span>状态</span><span>定向查询</span><span>来源范围</span><span>结果</span><span>操作</span></div>${body}`;
  document.querySelectorAll("[data-run-job]").forEach((button) => button.addEventListener("click", () => executeJob(button.dataset.runJob, button)));
}
function renderVersions() {
  const rows = (state.detail?.revisions || []).filter((revision) => matchRow(revision.revision,revision.attributesSha256,revision.reviewStatus,revision.createdAt));
  $("#view-summary").textContent = `${rows.length} 个不可变属性版本`;
  const body = rows.map((revision) => `<div class="data-row version-grid"><span class="cell-key">r${revision.revision}</span><span>${esc(revision.createdAt)}</span><span title="${esc(revision.attributesSha256)}">${esc(revision.attributesSha256)}</span><span>${esc(revision.attributesSchemaVersion)}</span><span>${status(revision.reviewStatus)}</span></div>`).join("") || `<p class="empty">暂无版本。</p>`;
  $("#workbench-content").innerHTML = `<div class="data-head version-grid"><span>版本</span><span>生成时间</span><span>内容哈希</span><span>Schema</span><span>审核</span></div>${body}`;
}
async function renderProductionCostSummary() {
  const id = state.selected;
  try {
    const [data,research] = await Promise.all([
      api(`/api/ontology/entities/${encodeURIComponent(id)}/manufacturing`),
      api(`/api/ontology/entities/${encodeURIComponent(id)}/manufacturing-research`),
    ]);
    if (state.selected !== id || state.tab !== 'overview') return;
    const target = $('#workbench-content .dossier-overview');
    if (!target) return;
    target.querySelector('[data-production-cost]')?.remove();
    const labels = {production:'生产',delivery:'交付',order:'订购','unit-production':'单机生产成本',flyaway:'单机交付价','system-procurement':'系统采购金额',development:'研发费用',operating:'运行费用',lifecycle:'全寿命费用',other:'其他费用',pending:'待审核',accepted:'已采纳',rejected:'已拒绝',published:'已发布','under-review':'待审核','needs-evidence':'待补证',composition:'组成与构型',process:'制造流程',organization:'制造组织'};
    const relationLabels = {partOfAircraft:'飞行器组成',partOfGroundSegment:'地面配套',installedOn:'安装于',compatibleWith:'兼容配套'};
    const processLabels = {design:'设计',fabrication:'部件制造',assembly:'总装',inspection:'检验', 'ground-test':'地面试验','flight-test':'飞行试验',maintenance:'维护'};
    const roleLabels = {designer:'设计方',manufacturer:'制造方',assembler:'装配方',supplier:'供应方',integrator:'集成方',operator:'运营方'};
    const valueStatusLabels = {reported:'公开披露',estimated:'估算',calculated:'计算值',unknown:'未知'};
    const recordStateLabel = row => row.record_state === 'accepted-not-published' ? '已采纳 · 待发布' : (labels[row.review_status] || row.review_status || '状态未记录');
    const configurations = new Map((data.configurations || []).map(config => [config.configuration_id, config]));
    const organizations = new Map((data.organizationRoles || []).filter(row => row.organization_id).map(row => [row.organization_id, row.organization_name]));
    const assemblyParts = new Map((data.assembly || []).map(row => [row.assembly_item_id, row.part_name]));
    const configurationName = row => {
      const config = configurations.get(row.configuration_id);
      return config ? [config.name, config.variant].filter(Boolean).join(' · ') : row.configuration || row.configuration_id || '';
    };
    const stringify = value => value && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
    const display = value => esc(stringify(value));
    const evidenceText = row => row.evidence?.quote || row.evidence?.text || stringify(row.evidence_json) || '证据待补';
    const sourceMarkup = row => {
      const uri = safeHref(row.evidence?.sourceUri || row.evidence?.source_uri || row.source_uri || '');
      return uri ? sourceLink(uri, '来源 ↗') : '';
    };
    const recordMarkup = (items, summarize, facts) => items.length ? items.map(row => {
      const detail = facts(row).filter(([, value]) => value !== null && value !== undefined && value !== '');
      const quote = evidenceText(row);
      const source = sourceMarkup(row);
      return `<article class="manufacturing-record"><div class="manufacturing-record-head"><b>${summarize(row)}</b><span>${esc(recordStateLabel(row))}</span></div>${detail.length ? `<dl>${detail.map(([label,value]) => `<div><dt>${esc(label)}</dt><dd>${display(value)}</dd></div>`).join('')}</dl>` : ''}<details class="manufacturing-evidence"><summary>${esc(quote)}</summary><blockquote>${esc(quote)}</blockquote>${source ? `<div class="manufacturing-source">${source}</div>` : ''}</details></article>`;
    }).join('') : '<p class="manufacturing-empty">暂无已发布记录</p>';
    const assembly = data.assembly || [];
    const manufacturing = [...(data.processes || []), ...(data.organizationRoles || [])];
    const production = data.production || [];
    const costs = data.costs || [];
    const candidates=data.candidates||[];
    const factorGraphRuns=data.factorGraphRuns||[];
    const pending=candidates.filter(c=>['under-review','needs-evidence'].includes(c.status));
    const verification=research.verification||{};
    const researchLabels={planned:'待执行',running:'采集中','needs-review':'待核验',completed:'已完成',failed:'失败'};
    const researchStatus=verification.status?researchLabels[verification.status]||verification.status:'尚未搜索';
    const firecrawlStatus=research.collector?.configured?'Firecrawl 已配置':'Firecrawl 未配置';
    const modelStatus=research.extractor?.configured?`DeepSeek ${research.extractor.model||'已连接'}`:'DeepSeek 未配置';
    const modelContent = `<section class="manufacturing-model-tab" aria-label="数据3D建模"><div class="manufacturing-model-tab-head"><div><span class="trend-eyebrow">DATA DRIVEN ASSEMBLY</span><h3>数据3D建模</h3><p>按当前无人机本体的构型、部件、传感器、载荷和证据状态生成装配示意。</p></div><span>拖动旋转 · 滚轮缩放 · 展开构型</span></div><div id="manufacturing-dynamic-model" class="dynamic-model-viewer" aria-label="数据驱动装配动态模型"></div></section>`;
    const tabs = [
      {key:'assembly',label:'组成与构型',items:assembly,content:recordMarkup(assembly,row=>`${display(row.part_name || row.name || '组成项未命名')} · ${esc(relationLabels[row.relation] || row.relation || '关系未记录')}` ,row=>[['配置',configurationName(row)],['父级组成',assemblyParts.get(row.parent_item_id) || row.parent_item_id],['数量',row.quantity]])},
      {key:'model',label:'数据3D建模',items:[],badge:'视图',content:modelContent},
      {key:'manufacturing',label:'制造信息',items:manufacturing,content:recordMarkup(manufacturing,row=>`${display(row.process_name || row.organization_name || row.name || '制造记录未命名')} · ${esc(processLabels[row.process_type] || roleLabels[row.role] || row.process_type || row.role || '类型未记录')}`,row=>[['组织',row.organization_name || organizations.get(row.organization_id)],['组织编号',!row.organization_name && !organizations.has(row.organization_id) ? row.organization_id : null],['配置',configurationName(row)],['流程次序',row.sequence_no],['起始时间',row.valid_from],['结束时间',row.valid_to]])},
      {key:'delivery',label:'生产与交付',items:production,content:recordMarkup(production,row=>`${esc(labels[row.record_type] || row.record_type || '生产记录')} · ${display(row.quantity ?? '数量未知')} ${esc(row.quantity_unit || '')}`,row=>[['配置',row.configuration],['开始时间',row.start_date],['结束时间',row.end_date],['组织编号',row.organization_id]])},
      {key:'costs',label:'成本与价格',items:costs,content:recordMarkup(costs,row=>`${esc(labels[row.cost_type] || row.cost_type || '费用')} · ${display(row.amount ?? '金额未披露')} ${esc(row.currency || '')}`,row=>[['年份',row.price_basis_year],['数量',row.quantity],['计量单位',row.quantity_unit],['包含范围',row.includes],['不含范围',row.excludes],['估值性质',valueStatusLabels[row.value_status] || row.value_status],['配置',row.configuration]])},
      {key:'candidates',label:'待审核候选',items:pending,content:pending.length ? pending.map(candidate=>{
        const payload = candidate.payload || {};
        const title = payload.part_name || payload.process_name || payload.organization_name || payload.record_type || payload.cost_type || payload.name || labels[candidate.candidate_type] || candidate.candidate_type || '候选记录';
        const quote = candidate.evidence?.quote || candidate.evidence?.text || '证据待补';
        const source = sourceMarkup(candidate);
        return `<article class="manufacturing-record manufacturing-candidate"><div class="manufacturing-record-head"><b>${display(title)}</b><span>${esc(labels[candidate.status] || candidate.status || '待审核')}</span></div><dl><div><dt>候选类型</dt><dd>${esc(labels[candidate.candidate_type] || candidate.candidate_type || '未分类')}</dd></div><div><dt>提取内容</dt><dd>${display(payload)}</dd></div></dl><details class="manufacturing-evidence"><summary>${esc(quote)}</summary><blockquote>${esc(quote)}</blockquote>${source ? `<div class="manufacturing-source">${source}</div>` : ''}</details></article>`;
      }).join('') : '<p class="manufacturing-empty">暂无待审核候选</p>'},
    ];
    const activeTab = tabs.some(tab => tab.key === state.manufacturingTab) ? state.manufacturingTab : 'assembly';
    state.manufacturingTab = activeTab;
    const tabMarkup = tabs.map((tab,index)=>`<button type="button" role="tab" id="manufacturing-tab-${tab.key}" aria-controls="manufacturing-panel-${tab.key}" aria-selected="${activeTab===tab.key}" tabindex="${activeTab===tab.key?'0':'-1'}" data-manufacturing-tab="${tab.key}"><span>${tab.label}</span>${tab.badge == null ? `<b>${tab.items.length}</b>` : `<b>${tab.badge}</b>`}</button>`).join('');
    const panelMarkup = tabs.map(tab=>`<section class="manufacturing-panel" role="tabpanel" id="manufacturing-panel-${tab.key}" aria-labelledby="manufacturing-tab-${tab.key}" tabindex="0" ${activeTab===tab.key?'':'hidden'}>${tab.content}</section>`).join('');
    const controls=`<div class="manufacturing-research-controls"><span>${esc(researchStatus)} · ${research.snapshotCount||0} 条来源 · ${esc(firecrawlStatus)} · ${esc(modelStatus)}</span><div><button type="button" class="mini-action" data-manufacturing-action="collect" ${!research.collector?.configured||verification.status==='running'?'disabled':''}>Firecrawl 定向搜索</button><button type="button" class="mini-action" data-manufacturing-action="extract" ${!research.snapshotCount||!research.extractor?.configured?'disabled':''}>DeepSeek 提取候选</button>${factorGraphRuns.length?`<button type="button" class="mini-action" data-factor-graph>证据关系 ${factorGraphRuns[0].status==='conflict'?'· 有冲突':`· ${factorGraphRuns[0].summary?.observationCount||0} 条观测`}</button>`:''}</div></div>`;
    disposeDynamicModelViewers(target);
    target.insertAdjacentHTML('beforeend', `<section data-production-cost><div class="manufacturing-section-head"><h3>生产制造信息</h3>${controls}</div><div class="manufacturing-tabs" role="tablist" aria-label="生产制造信息分类">${tabMarkup}</div>${panelMarkup}</section>`);
    const section = target.querySelector('[data-production-cost]');
    const mountManufacturingModel = () => {
      const modelHost = section?.querySelector('#manufacturing-dynamic-model');
      if (!modelHost || state.manufacturingTab !== 'model' || !state.detail) return;
      mountDynamicUavViewer(modelHost, {entity: state.detail, attributes: latestRevision()?.attributes?.attributes || latestRevision()?.attributes || {}, candidates: [...(state.objectAgent?.candidates || []), ...(data.candidates || [])], assembly: data.assembly || [], sources: state.sources, factorGraph: state.factorGraph});
    };
    const selectManufacturingTab = (key, focus = false) => {
      state.manufacturingTab = key;
      section.querySelectorAll('[data-manufacturing-tab]').forEach(button => {
        const selected = button.dataset.manufacturingTab === key;
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
        if (selected && focus) button.focus();
      });
      section.querySelectorAll('.manufacturing-panel').forEach(panel => { panel.hidden = panel.id !== `manufacturing-panel-${key}`; });
      if (key !== 'model') disposeDynamicModelViewers(section);
      mountManufacturingModel();
    };
    section.querySelector('.manufacturing-tabs').addEventListener('click', event => {
      const button = event.target.closest('[data-manufacturing-tab]');
      if (button) selectManufacturingTab(button.dataset.manufacturingTab);
    });
    section.querySelector('.manufacturing-tabs').addEventListener('keydown', event => {
      const buttons = [...section.querySelectorAll('[data-manufacturing-tab]')];
      const index = buttons.indexOf(event.target.closest('[data-manufacturing-tab]'));
      if (index < 0) return;
      const next = event.key === 'ArrowRight' ? (index + 1) % buttons.length : event.key === 'ArrowLeft' ? (index - 1 + buttons.length) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); selectManufacturingTab(buttons[next].dataset.manufacturingTab, true); }
    });
    target.querySelectorAll('[data-manufacturing-action]').forEach(button=>button.addEventListener('click',()=>runManufacturingResearch(id,button.dataset.manufacturingAction,button)));
    target.querySelector('[data-factor-graph]')?.addEventListener('click',()=>showFactorGraph(id));
    mountManufacturingModel();
  } catch (error) { /* Keep the equipment dossier available when this API fails. */ }
}

async function showFactorGraph(entityId) {
  try {
    const data=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/factor-graph`);
    const graph=data.graph;
    if(!graph){toast('当前本体尚无证据因子图','error');return;}
    document.querySelector('#factor-graph-dialog')?.remove();
    const escJson=value=>esc(JSON.stringify(value??{},null,2));
    const statusLabels={'pending-review':'待人工审核','needs-evidence':'待补证','conflict':'有冲突','complete-with-gaps':'已完成但有缺口'};
    const factorLabels={'source-support':'来源支持','identity-match':'型号匹配','configuration-scope':'构型范围','time-consistency':'时间一致性','unit-normalization':'单位归一','source-independence':'来源独立性','conflict':'冲突','review-gate':'审核门禁'};
    const variableRows=(graph.variables||[]).map(row=>`<div class="factor-graph-row"><b>${esc(row.variableKey)}</b><span>${esc(row.variableType)}</span><span>${esc(row.state)}</span><code>${escJson(row.value)}</code></div>`).join('')||'<p class="manufacturing-empty">暂无变量</p>';
    const factorRows=(graph.factors||[]).map(row=>`<div class="factor-graph-row"><b>${esc(factorLabels[row.factorType]||row.factorType)}</b><span>${esc(row.status)}</span><span>${esc((row.observationIds||[]).length)} 条观测</span><code>${escJson(row.details)}</code></div>`).join('')||'<p class="manufacturing-empty">暂无因子</p>';
    const observationRows=(graph.observations||[]).map(row=>`<details class="factor-graph-observation"><summary>${esc(row.sourceUri)} · ${esc(row.candidateId||'无候选')}</summary><blockquote>${esc(row.quote)}</blockquote><small>快照：${esc(row.sourceSnapshotId)} · 哈希：${esc(row.contentHash)}</small></details>`).join('')||'<p class="manufacturing-empty">暂无观测</p>';
    const dialog=document.createElement('dialog');dialog.id='factor-graph-dialog';dialog.className='factor-graph-dialog';
    dialog.innerHTML=`<form method="dialog"><div class="factor-graph-head"><div><b>证据关系 · ${esc(graph.entity?.name||'无人机本体')}</b><small>${esc(statusLabels[graph.inference?.status]||graph.inference?.status||'待审核')} · ${esc(graph.graphRunId)}</small></div><button aria-label="关闭">×</button></div><div class="factor-graph-summary"><span>变量 ${graph.variables?.length||0}</span><span>观测 ${graph.observations?.length||0}</span><span>因子 ${graph.factors?.length||0}</span><span>约束 ${graph.constraints?.length||0}</span><span>冲突 ${graph.inference?.conflicts?.length||0}</span></div><section><h4>变量与候选值</h4>${variableRows}</section><section><h4>来源观测</h4>${observationRows}</section><section><h4>因子检查</h4>${factorRows}</section>${graph.inference?.gaps?.length?`<section><h4>待补证</h4><ul>${graph.inference.gaps.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></section>`:''}<div class="factor-graph-foot">图谱只记录证据支持、冲突和约束；候选采纳与正式发布仍需走人工审核门禁。</div></form>`;
    document.body.append(dialog);dialog.showModal();
  } catch(error) { toast(error.message||'读取证据关系失败','error'); }
}

async function runManufacturingResearch(entityId, action, button) {
  const label=button.textContent;
  button.disabled=true;
  button.textContent=action==='collect'?'搜索中…':'提取中…';
  try {
    const result=await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/manufacturing-research/${action}`,{method:'POST',body:JSON.stringify(action==='collect'?{force:true}:{})});
    if(result.status==='failed'||result.status==='extraction-failed')throw Error('定向研究未完成，请检查任务状态与服务日志');
    if(result.status==='model-unavailable')throw Error('DeepSeek 尚未配置');
    if(result.status==='no-evidence')throw Error('请先完成 Firecrawl 定向搜索');
    const count=action==='collect'?result.retainedItems:result.candidateCount;
    toast(action==='collect'?`定向搜索完成：${count||0} 条来源`:`候选提取完成：${count||0} 条待审核`);
    await renderProductionCostSummary();
  } catch(error) {
    button.disabled=false;
    button.textContent=label;
    if(state.selected===entityId)await renderProductionCostSummary();
    toast(error.message||'制造信息研究失败','error');
  }
}

function disposeDynamicModelViewers(root = document) {
  root.querySelectorAll?.('.dynamic-model-viewer').forEach(host => host.__dynamicViewer?.dispose?.());
}

function renderOverview() {
  queueMicrotask(renderProductionCostSummary);
  const item = state.detail;
  const rows = attributeRows();
  const present = rows.filter((row) => row.value !== null).length;
  const verified = rows.filter((row) => row.value?.reviewStatus === "verified" || row.value?.reviewStatus === "published").length;
  const missing = rows.length - present;
  const audit = state.auditRun;
  const auditLabel = {queued:"排队中",planning:"规划中",collecting:"采集中",extracting:"属性提取中",reconciling:"校验归并中",failed:"执行失败",cancelled:"已取消", "awaiting-review":"待审核", "completed-with-gaps":"完成但有缺口", completed:"已完成"}[audit?.status] || (audit ? audit.status : "尚未启动");
  const summary = [
    ["实体类型", entityTaxonomy(item || {}).classLabel || "--"],
    ["档案版本", item ? `Revision ${item.currentRevision}` : "--"],
    ["属性覆盖", `${present}/${rows.length || 0}`],
    ["已核验", `${verified} 条`],
    ["证据来源", `${state.sources.length} 条`],
    ["关联关系", `${state.relations.length} 条`],
    ["型号沿袭", `${state.lineage.length} 条`],
    ["审计状态", auditLabel],
  ];
  const gaps = rows.filter((row) => row.value === null).slice(0, 8).map((row) => `<span>${esc(attributeLabel(row.key))}</span>`).join("");
  $("#view-summary").textContent = item ? `${item.preferredName} · ${present} 项已有值 · ${missing} 项待补证` : "请选择对象";
  disposeDynamicModelViewers();
  $("#workbench-content").innerHTML = `<section class="dossier-overview"><div class="dossier-overview-heading"><div><span class="trend-eyebrow">ENTITY DOSSIER</span><h3>${esc(item?.preferredName || "实体档案")}</h3><p>本体树中的实体入口；属性、关系、事件和审计记录均绑定当前版本。</p></div><span class="dossier-overview-state">${esc(auditLabel)}</span></div><div class="dossier-overview-facts">${summary.map(([label,value]) => `<span><small>${esc(label)}</small><b>${esc(value)}</b></span>`).join("")}</div><div class="dossier-overview-sections"><section><b>能力与指标</b><p>${esc(state.profile ? `${state.profile.quality.presentFieldCount}/${state.profile.quality.expectedFieldCount} 个能力字段已有记录` : `${present} 个档案字段已有记录`)}</p><button type="button" class="mini-action" data-overview-tab="capabilities">打开能力档案</button></section><section><b>待补缺口</b><p>${gaps || "当前没有已识别的属性缺口。"}</p><button type="button" class="mini-action" data-overview-tab="attributes">查看属性与指标</button></section><section><b>证据与治理</b><p>${esc(`${state.sources.length} 条来源 · ${state.jobs.length} 个核验任务 · ${state.objectAgent?.candidates?.length || 0} 条属性候选`)}</p><button type="button" class="mini-action" data-overview-tab="audit">打开实体审计</button></section></div></section>`;
  document.querySelectorAll("[data-overview-tab]").forEach((button) => button.addEventListener("click", () => { state.tab = button.dataset.overviewTab; renderContent(); }));
}

function renderLineage() {
  const rows = state.lineage.filter((edge) => matchRow(edge.predecessorName, edge.successorName, edge.familyName, edge.rawChain, edge.qualifier, edge.matchStatus));
  $("#view-summary").textContent = `${rows.length} 条型号沿袭 · 沿袭关系与能力变化分开核验`;
  const body = rows.map((edge) => `<div class="data-row lineage-grid"><span class="cell-key">${esc(edge.predecessorName || "未知前型")}</span><span class="cell-value">→</span><span>${esc(edge.successorName || "未知后型")}</span><span>${esc(edge.familyName || "型号家族未记载")}</span><span title="${esc(edge.rawChain || "")}">${esc(edge.rawChain || "原始沿袭链未记载")}</span><span>${esc(edge.matchStatus || "待匹配")}</span><span>${dossierStatus(edge.reviewStatus || "沿袭待核验")}</span></div>`).join("") || `<p class="empty">暂无已匹配的型号沿袭关系。</p>`;
  $("#workbench-content").innerHTML = `<div class="lineage-note">型号沿袭只表达身份、改型和衍生关系；能力是否提升必须由独立指标、条件和证据支持。</div><div class="data-head lineage-grid"><span>前型</span><span></span><span>后型</span><span>家族</span><span>原始沿袭链</span><span>匹配状态</span><span>审核</span></div>${body}`;
}

function renderAudit() {
  const run = state.auditRun;
  const labels={queued:"排队中",planning:"规划中",collecting:"采集中",extracting:"属性提取中",reconciling:"校验归并中",failed:"执行失败",cancelled:"已取消", "awaiting-review":"候选待审核", "completed-with-gaps":"完成但有证据缺口", completed:"已完成"};
  const events = run?.events || [];
  const jobs = state.jobs.filter((job) => matchRow(job.targetType, job.query, job.status));
  const eventRows = events.map((event) => { const detail = event.detail || {}; return `<div class="audit-event"><time>${esc(new Date(event.createdAt).toLocaleString())}</time><b>${esc({trigger:"触发",planning:"规划",collecting:"正文采集",extracting:"属性提取",reconciling:"校验归并",failure:"失败",publication:"发布"}[event.stage] || event.stage)}</b><span>${esc(event.status || "")}</span><small>${esc(detail.target || detail.reason || detail.stopReason || detail.error || "")}</small></div>`; }).join("") || `<p class="empty">尚未记录实体审计事件。</p>`;
  const jobRows = jobs.map((job) => `<div class="data-row job-grid"><span class="cell-key">${esc(job.targetType)}</span><span>${verificationStatus(job)}</span><span title="${esc(job.query)}">${esc(job.query)}</span><span>${esc((job.sourceScopeIds || []).join(", "))}</span><span>${job.result?.items?.length ?? "--"}</span><span><button class="mini-action" data-run-job="${esc(job.verificationId)}" ${job.status === "running" ? "disabled" : ""}>${job.stopReason === "stale-timeout" ? "从断点重试" : (job.status === "planned" || job.status === "failed" ? "执行" : "重新核验")}</button></span></div>`).join("") || `<p class="empty">暂无定向核验任务。</p>`;
  $("#view-summary").textContent = run ? `实体审计 · ${labels[run.status] || run.status} · ${events.length} 个事件` : "实体审计尚未启动";
  const recoveredCount = jobs.filter((job) => job.stopReason === "stale-timeout").length;
  $("#workbench-content").innerHTML = `<section class="audit-summary"><div><b>实体审计</b><span>${esc(run ? labels[run.status] || run.status : "尚未启动")}</span><small>${run ? `触发于 ${new Date(run.createdAt).toLocaleString()}` : "从上方审计工具启动定向补证"}</small></div><div><b>${esc(String(run?.result?.attributeCandidates ?? state.objectAgent?.candidates?.length ?? 0))}</b><small>属性候选</small></div><div><b>${esc(String(run?.result?.relationCandidates ?? state.objectAgent?.relationCandidates?.length ?? 0))}</b><small>关系候选</small></div><div><b>${esc(String(state.sources.length))}</b><small>来源</small></div></section><section class="audit-events"><div class="audit-section-heading"><b>阶段事件</b><span>${events.length} 条</span></div>${eventRows}</section><section class="audit-jobs"><div class="audit-section-heading"><b>定向核验任务</b><span>${jobs.length} 个${recoveredCount ? ` · ${recoveredCount} 个已回收` : ""} <button type="button" class="mini-action" data-reclaim-verifications>回收卡住任务</button></span></div><div class="data-head job-grid"><span>核验类型</span><span>状态</span><span>定向查询</span><span>来源范围</span><span>结果</span><span>操作</span></div>${jobRows}</section>`;
  document.querySelectorAll("[data-run-job]").forEach((button) => button.addEventListener("click", () => executeJob(button.dataset.runJob, button)));
  document.querySelector("[data-reclaim-verifications]")?.addEventListener("click", reclaimVerificationJobs);
}

/* The signal view is deliberately kept separate from the generic attribute
   tables. A capability claim is a reviewable assertion; observations and
   evidence bundles are the supporting chain and never become published facts
   merely by being displayed here. The API is allowed to return an empty
   chain while an audit is still running. */
const signalLevelLabel = (value) => ({E0:"E0 推测",E1:"E1 单一来源",E2:"E2 文献互证",E3:"E3 测试/影像",E4:"E4 独立观测",E5:"E5 实测/实战"}[String(value || "")] || value || "未分级");
const signalStatusLabel = (value) => ({"under-review":"待审核",unverified:"未核验",verified:"已核验",accepted:"已采纳",published:"已发布",conflict:"有冲突",unknown:"未判定"}[String(value || "")] || value || "未判定");
const signalRecordKey = (kind, id) => `${kind}:${id}`;
const signalRecordTitle = (kind, record) => kind === "claim" ? (record.metric || record.capabilityDomain || "能力断言") : kind === "observation" ? (record.observationType || "信号观测") : kind === "evidence" ? "证据束" : "核验运行";
const signalRecordStatus = (record) => record?.reviewStatus || record?.verificationStatus || record?.claimState || record?.status || "unknown";
const signalRecordText = (record) => [record?.metric,record?.capabilityDomain,record?.rawValue,record?.observationType,record?.collectionLocation,record?.operationalEffect].filter(Boolean).join(" ");
const signalJson = (value) => {
  if (value === null || value === undefined || value === "") return "未记录";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value); } catch { return String(value); }
};
function signalSourceMarkup(refs = []) {
  const rows = Array.isArray(refs) ? refs : [];
  if (!rows.length) return '<span class="signal-muted">未绑定来源快照</span>';
  return rows.slice(0, 4).map((ref) => {
    const url = safeHref(ref?.sourceUri || ref?.sourceUrl || ref?.url || ref?.source);
    return url ? sourceLink(url, ref.sourceName || ref.publisherDomain || "公开来源") : `<span>${esc(ref?.sourceName || ref?.id || "来源快照")}</span>`;
  }).join(" · ");
}
function signalDetailMarkup(kind, record) {
  if (!record) return '<p class="empty">点击链路节点查看详情。</p>';
  const fields = kind === "claim" ? [
    ["能力域",record.capabilityDomain],["指标",record.metric],["原始值",record.rawValue],["规范值",signalJson(record.normalizedValue)],["单位",record.unit],
    ["改型/配置",[record.variant,record.configuration].filter(Boolean).join(" / ") || "未记录"],["适用条件",signalJson(record.conditions)],
    ["作战影响",record.operationalEffect],["模型置信度",record.confidence == null ? "未提供" : `${Math.round(Number(record.confidence) * 100)}%`],
    ["证据等级",signalLevelLabel(record.evidenceLevel)],["来源独立性",record.sourceIndependence ? signalJson(record.sourceIndependence) : "由证据束核验"],
    ["审核状态",signalStatusLabel(signalRecordStatus(record))],["观测引用",(record.sourceObservationIds || []).join("、") || "未绑定"],["证据束",(record.evidenceBundleIds || []).join("、") || "未绑定"],
  ] : kind === "observation" ? [
    ["观测类型",record.observationType],["采集时间",record.collectedAt],["采集地点",signalJson(record.collectionLocation)],["观测条件",signalJson(record.conditions)],
    ["信号特征",signalJson(record.features)],["发射源特征",record.emitterSignatureId || "未绑定"],["证据等级",signalLevelLabel(record.evidenceLevel)],
    ["核验状态",signalStatusLabel(signalRecordStatus(record))],["来源",signalSourceMarkup(record.sourceRefs)],
  ] : kind === "evidence" ? [
    ["证据束",record.evidenceBundleId],["能力断言",(record.claimIds || []).join("、") || "未绑定"],["关联观测",(record.observationIds || []).join("、") || "未绑定"],
    ["来源证据",(record.evidenceIds || []).join("、") || "未绑定"],["来源独立性",signalJson(record.sourceIndependence)],["有效性",signalJson(record.validity)],
    ["反证",(record.counterEvidenceIds || []).join("、") || "未记录"],["证据等级",signalLevelLabel(record.evidenceLevel)],["核验状态",signalStatusLabel(record.verificationStatus)],
  ] : [["运行类型",record.runType],["状态",signalStatusLabel(record.status)],["方法版本",record.methodVersion],["结果",signalJson(record.result)],["检查",signalJson(record.checks)],["证据等级",signalLevelLabel(record.evidenceLevel)],["置信度",record.confidence == null ? "未提供" : `${Math.round(Number(record.confidence) * 100)}%`]];
  return `<dl class="signal-detail-grid">${fields.map(([label,value]) => `<div><dt>${esc(label)}</dt><dd>${typeof value === "string" && value.includes("<") ? value : esc(fmt(value))}</dd></div>`).join("")}</dl>`;
}
function renderSignals() {
  const records = state.signalRecords || {};
  const claims = Array.isArray(records.claims) ? records.claims : [];
  const observations = Array.isArray(records.observations) ? records.observations : [];
  const bundles = Array.isArray(records.evidenceBundles) ? records.evidenceBundles : [];
  const runs = Array.isArray(records.verificationRuns) ? records.verificationRuns : [];
  const filter = state.signalFilter || {};
  const query = String(filter.query || "").trim().toLowerCase();
  const matches = (record) => (!query || signalRecordText(record).toLowerCase().includes(query)) && (filter.evidenceLevel === "all" || String(record.evidenceLevel || "") === filter.evidenceLevel) && (filter.status === "all" || signalRecordStatus(record) === filter.status);
  const visibleClaims = claims.filter(matches);
  const visibleObservations = observations.filter((record) => !query || signalRecordText(record).toLowerCase().includes(query));
  const visibleBundles = bundles.filter((record) => !query || signalJson(record).toLowerCase().includes(query));
  const entity = state.detail?.preferredName || "当前装备实体";
  const claimById = new Map(claims.map((record) => [String(record.claimId), record]));
  const obsById = new Map(observations.map((record) => [String(record.observationId), record]));
  const bundleById = new Map(bundles.map((record) => [String(record.evidenceBundleId), record]));
  const selected = state.selectedSignal ? {kind: state.selectedSignal.split(":")[0], id: state.selectedSignal.split(":").slice(1).join(":")} : null;
  const selectedRecord = selected ? ({claim:claimById,observation:obsById,evidence:bundleById}[selected.kind]?.get(selected.id) || null) : null;
  const node = (kind, record, title, meta = "") => `<button type="button" class="signal-chain-node signal-node-${kind} ${state.selectedSignal === signalRecordKey(kind, record.claimId || record.observationId || record.evidenceBundleId || record.verificationRunId) ? "active" : ""}" data-signal-node="${esc(signalRecordKey(kind, record.claimId || record.observationId || record.evidenceBundleId || record.verificationRunId))}"><b>${esc(title)}</b><small>${esc(meta)}</small></button>`;
  const claimRows = visibleClaims.length ? visibleClaims.map((claim) => {
    const refs = (claim.sourceObservationIds || []).map((id) => obsById.get(String(id))).filter(Boolean);
    const evidences = (claim.evidenceBundleIds || []).map((id) => bundleById.get(String(id))).filter(Boolean);
    const level = signalLevelLabel(claim.evidenceLevel);
    return `<div class="signal-chain-row">${node("claim",claim,claim.metric || claim.capabilityDomain || "能力断言",`${claim.rawValue || "值未记录"} · ${level} · ${signalStatusLabel(signalRecordStatus(claim))}`)}<span class="signal-chain-arrow" aria-hidden="true">→</span><div class="signal-chain-linked">${refs.length ? refs.map((obs) => node("observation",obs,obs.observationType || "信号观测",`${obs.collectedAt || "时间未记录"} · ${signalLevelLabel(obs.evidenceLevel)}`)).join("") : '<span class="signal-empty-node">未绑定观测</span>'}</div><span class="signal-chain-arrow" aria-hidden="true">→</span><div class="signal-chain-linked">${evidences.length ? evidences.map((bundle) => node("evidence",bundle,"证据束",`${(bundle.evidenceIds || []).length} 条来源 · ${signalLevelLabel(bundle.evidenceLevel)}`)).join("") : '<span class="signal-empty-node">未绑定证据束</span>'}</div></div>`;
  }).join("") : '<p class="empty">暂无符合筛选条件的能力断言。审计完成后，候选会显示在此处。</p>';
  const sourceCount = new Set([...visibleObservations.flatMap((item) => item.sourceRefs || []), ...visibleBundles.flatMap((item) => item.evidenceIds || [])].map((item) => item.id || item.sourceUri || item)).size;
  const overview = `<section class="signal-overview"><span><b>${visibleClaims.length}</b>能力断言</span><span><b>${visibleObservations.length}</b>信号观测</span><span><b>${visibleBundles.length}</b>证据束</span><span><b>${sourceCount}</b>来源引用</span><span><b>${runs.length}</b>核验运行</span><small>模型候选与证据链分开保存，审核后才能发布</small></section>`;
  const controls = `<div class="signal-toolbar"><label>查找 <input id="signal-query" type="search" value="${esc(filter.query || "")}" placeholder="能力、指标、观测类型"></label><label>证据等级 <select id="signal-level"><option value="all">全部</option>${["E0","E1","E2","E3","E4","E5"].map((value) => `<option value="${value}" ${filter.evidenceLevel === value ? "selected" : ""}>${signalLevelLabel(value)}</option>`).join("")}</select></label><label>状态 <select id="signal-status"><option value="all">全部</option>${["under-review","unverified","verified","accepted","published","conflict"].map((value) => `<option value="${value}" ${filter.status === value ? "selected" : ""}>${signalStatusLabel(value)}</option>`).join("")}</select></label><button type="button" class="mini-action" id="signal-refresh">刷新链路</button></div>`;
  const entityNode = `<div class="signal-chain-entity"><span class="signal-entity-mark">实体</span><b>${esc(entity)}</b><small>当前实体档案 · Revision ${esc(state.detail?.currentRevision || "--")}</small></div>`;
  const detail = `<aside class="signal-detail-panel" aria-label="信号链节点详情"><div class="signal-detail-heading"><b>节点详情</b><span>${selectedRecord ? esc(signalRecordTitle(selected.kind, selectedRecord)) : "未选择节点"}</span></div>${signalDetailMarkup(selected?.kind, selectedRecord)}</aside>`;
  $("#view-summary").textContent = `${claims.length} 条能力断言 · ${observations.length} 条信号观测 · ${bundles.length} 个证据束`;
  $("#workbench-content").innerHTML = `<section class="signal-section"><div class="signal-section-heading"><div><span class="trend-eyebrow">SIGNALS & OBSERVATIONS</span><h3>能力断言与信号证据链</h3><p>实体 → 作战能力 → 信号观测 → 证据束。点击节点查看条件、型号/配置、来源独立性和核验状态。</p></div><span class="signal-chain-note">E0–E5 证据等级</span></div>${overview}${controls}<div class="signal-chain-board"><div class="signal-chain-head"><span>实体</span><span>能力断言</span><span>观测</span><span>证据</span></div>${entityNode}<div class="signal-chain-rows">${claimRows}</div></div>${detail}</section>`;
  $("#signal-query")?.addEventListener("input", (event) => { state.signalFilter.query = event.target.value; renderSignals(); });
  $("#signal-level")?.addEventListener("change", (event) => { state.signalFilter.evidenceLevel = event.target.value; renderSignals(); });
  $("#signal-status")?.addEventListener("change", (event) => { state.signalFilter.status = event.target.value; renderSignals(); });
  $("#signal-refresh")?.addEventListener("click", () => loadSignalRecords(state.selected, true));
  document.querySelectorAll("[data-signal-node]").forEach((button) => button.addEventListener("click", () => { state.selectedSignal = button.dataset.signalNode; renderSignals(); }));
}

function renderPendingReview() {
  const allAttributes = state.objectAgent?.candidates || [];
  const allRelations = state.objectAgent?.relationCandidates || [];
  const attributes = allAttributes.filter((candidate) => !["rejected", "superseded", "published"].includes(candidate.status));
  const relations = allRelations.filter((candidate) => !["rejected", "superseded", "published"].includes(candidate.status));
  const reviews = state.objectAgent?.reviewHistory || [];
  const reviewLabel = {accept:"采纳", reject:"拒绝", "request-more-evidence":"要求补证", edit:"修改候选"};
  const statusLabel = {accepted:"已采纳", rejected:"已拒绝", "needs-evidence":"待补证", edited:"已修改"};
  const reviewTime = (value) => { try { return value ? new Date(value).toLocaleString() : "时间未记录"; } catch { return value || "时间未记录"; } };
  const evidencePoolFor = (candidateType) => candidateType === "relation" ? allRelations : allAttributes;
  const candidateTitle = (review) => {
    const candidate = review.candidate || {};
    return review.candidateType === "relation"
      ? `${candidate.targetName || "未知对象"} → ${candidate.predicate || "关系未命名"}`
      : attributeLabel(candidate.attributeKey);
  };
  const candidateValue = (review) => {
    const candidate = review.candidate || {};
    return review.candidateType === "relation"
      ? `${candidate.targetEntityId || "目标待匹配"} · ${candidate.qualifier ? fmt(candidate.qualifier) : "无限定"}`
      : `${candidate.currentValue || "当前无值"} → ${candidate.proposedValue || "候选值未记录"}`;
  };
  $("#view-summary").textContent = `${attributes.length} 条待审核 · ${reviews.length} 条已处理结果 · 审核后仍需发布`;
  const attrRows = attributes.map((candidate) => `<div class="review-row"><span class="cell-key">${esc(attributeLabel(candidate.attributeKey))}</span><span>${esc(candidate.currentValue || "当前无值")} → <b>${esc(candidate.proposedValue)}</b></span><span>${esc(candidate.modelProvider || "DeepSeek")}</span><span>${esc(candidate.status || "待审核")}</span><span class="review-actions"><button type="button" class="mini-action" data-candidate-action="accept" data-candidate-id="${esc(candidate.candidateId)}">采纳</button><button type="button" class="mini-action" data-candidate-action="reject" data-candidate-id="${esc(candidate.candidateId)}">拒绝</button><button type="button" class="mini-action" data-candidate-action="request-more-evidence" data-candidate-id="${esc(candidate.candidateId)}">补证</button></span><div class="review-evidence-cell">${candidateEvidenceMarkup(candidate, allAttributes)}</div></div>`).join("") || `<p class="empty">暂无待审核属性候选。</p>`;
  const relationRows = relations.map((candidate) => `<div class="review-row"><span class="cell-key">${esc(candidate.targetName || "未知对象")}</span><span>→ ${esc(candidate.predicate || "关系未命名")}</span><span>${esc(candidate.targetEntityId || "目标待匹配")}</span><span>${esc(candidate.status || "待审核")}</span><span class="review-actions"><button type="button" class="mini-action" data-relation-action="accept" data-relation-id="${esc(candidate.candidateId)}">采纳</button><button type="button" class="mini-action" data-relation-action="request-more-evidence" data-relation-id="${esc(candidate.candidateId)}">补证</button></span><div class="review-evidence-cell">${candidateEvidenceMarkup(candidate, allRelations)}</div></div>`).join("") || `<p class="empty">暂无待审核关系候选。</p>`;
  const historyRows = reviews.map((review) => {
    const candidate = review.candidate || {};
    const action = reviewLabel[review.action] || review.action || "已处理";
    const stateText = statusLabel[review.status] || review.status || "已处理";
    const decisionClass = review.status || review.action || "pending";
    return `<div class="review-history-row"><span class="cell-key" title="${esc(candidateTitle(review))}">${esc(candidateTitle(review))}</span><span title="${esc(candidateValue(review))}">${esc(candidateValue(review))}</span><span class="review-history-decision ${esc(decisionClass)}">${esc(action)} · ${esc(stateText)}</span><span>${esc(review.reviewer || "未记录")}</span><span title="${esc(review.reason || "")}">${esc(review.reason || "未记录审核依据")}</span><span>${esc(reviewTime(review.createdAt))}</span><div class="review-history-evidence">${candidateEvidenceMarkup(candidate, evidencePoolFor(review.candidateType))}</div></div>`;
  }).join("") || `<p class="empty">暂无采纳、拒绝或补证记录。</p>`;
  $("#workbench-content").innerHTML = `<section class="review-section"><div class="review-section-heading"><b>待审核候选</b><span>${attributes.length + relations.length} 条</span></div><div class="review-head"><span>属性/目标</span><span>当前值 → 候选值</span><span>模型</span><span>状态</span><span>操作</span></div>${attrRows}${relationRows}</section><section class="review-history-section"><div class="review-section-heading"><b>已处理结果</b><span>${reviews.length} 条 · 采纳/拒绝/补证均保留</span></div><div class="review-history-head"><span>候选</span><span>候选值</span><span>决定</span><span>审核人</span><span>审核依据</span><span>时间</span></div>${historyRows}</section><p class="review-note">每条记录都可展开原文、来源、定位和冲突值。证据完整性核验通过后才可进入自动采纳候选；采纳本身仍不等于发布，正式本体写入需要单独发布。</p>`;
  document.querySelectorAll("[data-candidate-action]").forEach((button) => button.addEventListener("click", () => reviewAttributeCandidate(button.dataset.candidateId, button.dataset.candidateAction, button)));
  document.querySelectorAll("[data-relation-action]").forEach((button) => button.addEventListener("click", () => reviewRelationCandidate(button.dataset.relationId, button.dataset.relationAction, button)));
}

function renderContent() { disposeDynamicModelViewers(); ({overview:renderOverview,attributes:renderAttributes,capabilities:renderCapabilities,evidence:renderEvidence,sources:renderSources,relations:renderRelations,lineage:renderLineage,combat:renderCombat,signals:renderSignals,audit:renderAudit,verification:renderAudit,review:renderPendingReview,versions:renderVersions}[state.tab] || renderOverview)(); syncTabNavigation(); }

function trendRows(rows, valueKey = "count") {
  const max = Math.max(1, ...rows.map((row) => Number(row[valueKey]) || 0));
  return rows.slice(0, 8).map((row) => {
    const value = Number(row[valueKey]) || 0;
    return `<div class="trend-bar-row"><span title="${esc(row.label)}">${esc(row.label)}</span><div class="trend-bar-track"><i style="width:${Math.max(3, Math.round(value / max * 100))}%"></i></div><b>${esc(fmt(value))}</b></div>`;
  }).join("") || `<p class="trend-empty">暂无可用字段</p>`;
}

function chartRowsForBar(spec = {}) {
  const rows = Array.isArray(spec.rows) ? spec.rows : [];
  const valueKey = spec.valueKey || (rows[0] && Object.keys(rows[0]).find((key) => ["count", "value", "models", "total"].includes(key))) || "count";
  return rows.map((row) => ({
    label: row?.label ?? row?.category ?? row?.name ?? row?.key ?? "--",
    [valueKey]: row?.[valueKey] ?? row?.count ?? row?.value ?? row?.models ?? 0,
  }));
}

function comparisonChartMarkup(spec = {}) {
  const rows = Array.isArray(spec.rows) ? spec.rows : [];
  const columns = Array.isArray(spec.columns) && spec.columns.length
    ? spec.columns.slice(0, 5)
    : [{key: "avgEndurance", label: "续航 h"}, {key: "avgSpeed", label: "速度 km/h"}, {key: "avgPayload", label: "载荷 kg"}];
  const grid = `minmax(100px,1.5fr) ${columns.map(() => "minmax(58px,1fr)").join(" ")}`;
  const header = `<div class="trend-compare-head" style="grid-template-columns:${grid}"><span>${esc(spec.labelKey || "类别")}</span>${columns.map((column) => `<span>${esc(column.label || column.key || "指标")}</span>`).join("")}</div>`;
  const body = rows.slice(0, 12).map((row) => `<div class="trend-compare-row" style="grid-template-columns:minmax(100px,1.5fr) ${columns.map(() => "minmax(58px,1fr)").join(" ")}"><span title="${esc(row?.category ?? row?.label ?? "--")}">${esc(row?.category ?? row?.label ?? "--")}</span>${columns.map((column) => `<span>${esc(fmt(row?.[column.key]))}</span>`).join("")}</div>`).join("") || `<p class="trend-empty">暂无可比较的指标</p>`;
  return `${header}${body}`;
}

function renderTrendCharts(summary = {}, chartSpecs = []) {
  const charts = $("#trend-charts");
  if (!charts) return;
  const specs = Array.isArray(chartSpecs) ? chartSpecs.filter((spec) => spec && typeof spec === "object") : [];
  if (!specs.length) {
    charts.innerHTML = `<p class="trend-empty">本次查询未返回可视化数据。</p>`;
    return;
  }
  charts.innerHTML = specs.slice(0, 6).map((spec, index) => {
    const type = String(spec.type || "bar").toLowerCase();
    const title = spec.title || `分析图表 ${index + 1}`;
    const subtitle = spec.subtitle || spec.unit || "本次查询计算";
    const chartBody = type === "comparison"
      ? comparisonChartMarkup(spec)
      : trendRows(chartRowsForBar(spec), spec.valueKey || "count");
    const className = type === "comparison" ? "trend-chart trend-chart-performance trend-chart-wide" : "trend-chart trend-chart-live";
    return `<div class="${className}" data-chart-type="${esc(type)}"><div class="trend-chart-title"><b>${esc(title)}</b><span>${esc(subtitle)}</span></div>${chartBody}</div>`;
  }).join("");
}

function renderTrendMetrics(summary = {}) {
  const metrics = $("#trend-metrics");
  if (!metrics) return;
  const topCategory = summary.categoryCounts?.[0];
  const topCountry = summary.countryCounts?.[0];
  metrics.innerHTML = `<div><small>本体对象总数</small><b>${esc(fmt(summary.entityCount))}</b><span>当前候选本体库</span></div><div><small>上位类数</small><b>${esc(fmt(summary.ontologyLayerCounts?.length || 0))}</b><span>组织 / 无人机 / 项目</span></div><div><small>本体类数</small><b>${esc(fmt(summary.ontologyClassCounts?.length || summary.categoryCounts?.length || 0))}</b><span>平台与飞行器分开</span></div><div><small>主要来源国</small><b>${esc(topCountry?.label || "--")}</b><span>${topCountry ? `${topCountry.count} 个对象` : "暂无数据"}</span></div><div><small>最大上位类</small><b>${esc(summary.ontologyLayerCounts?.[0]?.label || topCategory?.label || "--")}</b><span>${esc(fmt(summary.ontologyLayerCounts?.[0]?.count || topCategory?.count || 0))} 个对象</span></div>`;
}

function topLabels(rows = [], count = 3) {
  return rows.slice(0, count).map((row) => `${row.label}（${row.count}）`).join("、") || "暂无数据";
}

function bestMetric(rows = [], key, label) {
  const candidates = rows.filter((row) => Number.isFinite(Number(row[key])));
  candidates.sort((a, b) => Number(b[key]) - Number(a[key]));
  return candidates[0] ? `${candidates[0].category} ${label} ${fmt(candidates[0][key])}` : `${label} 暂无可比字段`;
}

function analysisCopy(summary = {}) {
  const performance = summary.performanceByCategory || [];
  const layerRows = summary.ontologyLayerCounts || summary.categoryCounts || [];
  const classRows = summary.ontologyClassCounts || summary.categoryCounts || [];
  const view = state.analysisView;
  if (view === "taxonomy") return {
    title: "类别结构视角",
    body: `当前本体上位类集中在 ${topLabels(layerRows, 4)}；细分本体类包括 ${topLabels(classRows, 4)}。建议先按上位类进入，再区分平台/系统与飞行器。`,
    bullets: ["看类别规模与样本不均衡", "进入某类后再看典型型号、缺失字段和核验任务", "模型生成结论只能解释结构，不能替代来源核验"],
  };
  if (view === "geography") return {
    title: "国家/厂商视角",
    body: `来源国分布以 ${topLabels(summary.countryCounts, 4)} 为主。这个视角适合回答产业生态、出口扩散和来源偏差问题。`,
    bullets: ["国家数量不是能力排名", "厂商与授权生产关系需要来源证据", "缺少公开来源的国家应标记为数据缺口"],
  };
  if (view === "capability") return {
    title: "能力指标视角",
    body: [bestMetric(performance, "avgEndurance", "平均续航"), bestMetric(performance, "avgSpeed", "平均速度"), bestMetric(performance, "avgPayload", "平均载荷")].join("；") + "。这些只是公开字段统计，不是作战效能评分。",
    bullets: ["速度、载荷、续航的测试条件不同，不能直接合成总分", "缺单位或口径不明的数值不参与均值", "模型只提出解释和补证建议，不直接写入本体"],
  };
  if (view === "governance") return {
    title: "任务运行与治理视角",
    body: "建议把分析运行过程拆成：数据读取、确定性统计、研判草稿、证据核验、人工发布。发布仍走证据审核链。",
    bullets: ["统计结果来自本体候选库", "模型只生成分析草稿和补证建议", "所有入库结论必须带来源、版本、引文和人工审核记录"],
  };
  return {
    title: "数据总览视角",
    body: `当前本体候选库包含 ${summary.entityCount || 0} 个对象、${layerRows.length} 个上位类、${classRows.length} 个本体类。`,
    bullets: ["默认页先回答“数据集中有什么”", "再按类别、国家、能力、治理切换视角", "对象详情仍由左侧树进入，避免主页面和对象档案混杂"],
  };
}

function governanceAssessmentLabel(assessment = {}) {
  return assessment.title || assessment.question || assessment.assessmentId || "未命名研判";
}

function renderGovernancePanel() {
  const panel = $("#governance-panel");
  if (!panel) return;
  panel.classList.toggle("is-visible", state.mainView === "review" || state.analysisView === "governance");
  const select = $("#governance-assessment");
  const statusNode = $("#governance-status");
  const summaryNode = $("#governance-summary");
  const findingsNode = $("#governance-findings");
  if (!select || !summaryNode || !findingsNode) return;
  const assessments = state.governance.assessments || [];
  select.innerHTML = `<option value="">选择研判任务…</option>${assessments.map((item) => `<option value="${esc(item.assessmentId)}">${esc(governanceAssessmentLabel(item))}</option>`).join("")}`;
  select.value = state.governance.selectedId || "";
  const selected = assessments.find((item) => item.assessmentId === state.governance.selectedId);
  const audits = state.governance.audits || [];
  const findings = state.governance.findings || [];
  const admissions = state.governance.admissions || [];
  const latestAudit = audits[0];
  const openFindings = findings.filter((item) => ["open", "needs-evidence"].includes(item.state));
  statusNode.textContent = selected ? `${latestAudit?.currentStatus || selected.status || "待审计"} · ${openFindings.length} 个待处理问题` : "尚未选择研判";
  $("#governance-audit").disabled = !selected || state.governance.loading;
  $("#governance-propose").disabled = !selected || state.governance.loading;
  if (!selected) {
    summaryNode.innerHTML = `<span>请选择研判任务，查看审计、审核和发布状态。</span>`;
    findingsNode.innerHTML = "";
    return;
  }
  const published = admissions.filter((item) => item.status === "published").length;
  const accepted = admissions.filter((item) => item.status === "accepted").length;
  summaryNode.innerHTML = `<span>审计 ${audits.length} 次 · 问题 <b>${findings.length}</b>（待处理 <b>${openFindings.length}</b>） · 候选 <b>${admissions.length}</b>（已接受 ${accepted} · 已发布 ${published}）</span><span class="governance-note">模型只生成问题和候选；人工确认后才可发布。</span>`;
  const findingMarkup = findings.length ? `<div class="governance-section-title"><b>审计问题</b><small>逐项人工处理</small></div>${findings.map((item) => {
    const actionable = ["open", "needs-evidence"].includes(item.state);
    return `<div class="governance-finding"><span>${status(item.state)}<small>${esc(item.severity)}</small></span><p title="${esc(item.message)}"><b>${esc(item.code)}</b> · ${esc(item.message)}</p><div class="governance-finding-actions">${actionable ? `<input class="gov-reviewer" placeholder="审核人" aria-label="审核人"><input class="gov-reason" placeholder="处理理由" aria-label="处理理由"><button type="button" class="mini-action" data-gov-finding="${esc(item.findingId)}" data-gov-action="confirm">确认</button><button type="button" class="mini-action" data-gov-finding="${esc(item.findingId)}" data-gov-action="request-more-evidence">补证</button>` : `<small>已处理 · ${esc(item.reviews?.[item.reviews.length - 1]?.reviewer || "人工")}</small>`}</div></div>`;
  }).join("")}` : `<div class="governance-note">当前研判尚无审计问题；点击“运行周期审计”生成不可变审计记录。</div>`;
  const admissionMarkup = admissions.length ? `<div class="governance-section-title"><b>本体候选与发布门禁</b><small>审核通过后逐项发布</small></div>${admissions.map((item) => {
    const statusValue = item.status || "under-review";
    const review = item.reviews?.[item.reviews.length - 1];
    const canReview = statusValue === "under-review";
    const canPublish = statusValue === "accepted";
    return `<div class="governance-admission"><span>${status(statusValue)}<small>${esc(item.kind || item.mapping?.kind || "candidate")}</small></span><p title="${esc(JSON.stringify(item.assertion || item.mapping || {}))}"><b>${esc(item.assertion?.equipment || item.mapping?.metricName || item.candidateIri || item.admissionId)}</b> · ${esc(item.assertion?.metric || item.mapping?.propertyPath || "本体候选")}</p><div class="governance-admission-actions">${canReview ? `<input class="gov-admission-reviewer" placeholder="审核人" aria-label="候选审核人"><input class="gov-admission-reason" placeholder="审核依据" aria-label="候选审核依据"><button type="button" class="mini-action" data-gov-admission="${esc(item.admissionId)}" data-gov-action="accept">接受</button>` : canPublish ? `<input class="gov-admission-publisher" placeholder="发布人" aria-label="发布人"><button type="button" class="mini-action" data-gov-publish="${esc(item.admissionId)}">发布</button>` : `<small>${esc(review?.reviewer || "已处理")}</small>`}</div></div>`;
  }).join("")}` : `<div class="governance-note">还没有本体候选。完成图谱/能力提取后点击“生成本体候选”。</div>`;
  findingsNode.innerHTML = findingMarkup + admissionMarkup;
  findingsNode.querySelectorAll("[data-gov-finding]").forEach((button) => button.addEventListener("click", () => reviewGovernanceFinding(button)));
  findingsNode.querySelectorAll("[data-gov-admission]").forEach((button) => button.addEventListener("click", () => reviewGovernanceAdmission(button)));
  findingsNode.querySelectorAll("[data-gov-publish]").forEach((button) => button.addEventListener("click", () => publishGovernanceAdmission(button)));
}

async function loadGovernanceState(preferredId = state.governance.selectedId) {
  state.governance.loading = true;
  try {
    const list = await api("/api/intelligence/assessments");
    state.governance.assessments = list.assessments || [];
    const requested = preferredId && state.governance.assessments.some((item) => item.assessmentId === preferredId) ? preferredId : state.governance.assessments[0]?.assessmentId || "";
    state.governance.selectedId = requested;
    if (requested) {
      const [auditData, admissionData] = await Promise.all([api(`/api/intelligence/assessments/${encodeURIComponent(requested)}/audit`), api(`/api/intelligence/assessments/${encodeURIComponent(requested)}/admissions`)]);
      state.governance.audits = auditData.audits || [];
      state.governance.findings = auditData.findings || [];
      state.governance.admissions = admissionData.admissions || [];
    } else {
      state.governance.audits = []; state.governance.findings = []; state.governance.admissions = [];
    }
  } catch (error) {
    state.governance.audits = []; state.governance.findings = []; state.governance.admissions = [];
    toast(`治理状态读取失败：${error.message}`, "error");
  } finally {
    state.governance.loading = false;
    renderGovernancePanel();
  }
}

async function runGovernanceAudit() {
  const assessmentId = state.governance.selectedId;
  if (!assessmentId) return;
  $("#governance-audit").disabled = true;
  try {
    await api(`/api/intelligence/assessments/${encodeURIComponent(assessmentId)}/audit`, {method: "POST", body: JSON.stringify({actor: "operator.demo", trigger: "manual"})});
    await loadGovernanceState(assessmentId); toast("周期审计已完成，问题已进入人工审核队列。");
  } catch (error) { toast(error.message, "error"); renderGovernancePanel(); }
}

async function proposeGovernanceAdmissions() {
  const assessmentId = state.governance.selectedId;
  if (!assessmentId) return;
  $("#governance-propose").disabled = true;
  try {
    await api(`/api/intelligence/assessments/${encodeURIComponent(assessmentId)}/admissions`, {method: "POST", body: JSON.stringify({})});
    await loadGovernanceState(assessmentId); toast("本体候选已生成，等待逐项人工审核。");
  } catch (error) { toast(error.message, "error"); renderGovernancePanel(); }
}

async function reviewGovernanceFinding(button) {
  const finding = state.governance.findings.find((item) => item.findingId === button.dataset.govFinding);
  const row = button.closest(".governance-finding");
  const reviewer = row?.querySelector(".gov-reviewer")?.value.trim();
  const reason = row?.querySelector(".gov-reason")?.value.trim();
  if (!reviewer || !reason) { toast("请填写审核人和处理理由。", "error"); return; }
  button.disabled = true;
  try {
    await api(`/api/intelligence/audit-findings/${encodeURIComponent(button.dataset.govFinding)}/review`, {method: "POST", body: JSON.stringify({action: button.dataset.govAction, reviewer, reason, expectedRevision: finding?.revision || 1})});
    await loadGovernanceState(state.governance.selectedId); toast("审计问题处理已记录。");
  } catch (error) { toast(error.message, "error"); button.disabled = false; }
}

async function reviewGovernanceAdmission(button) {
  const item = state.governance.admissions.find((candidate) => candidate.admissionId === button.dataset.govAdmission);
  const row = button.closest(".governance-admission");
  const reviewer = row?.querySelector(".gov-admission-reviewer")?.value.trim();
  const reason = row?.querySelector(".gov-admission-reason")?.value.trim();
  if (!reviewer || !reason) { toast("请填写候选审核人和审核依据。", "error"); return; }
  button.disabled = true;
  try {
    await api(`/api/intelligence/admissions/${encodeURIComponent(button.dataset.govAdmission)}/review`, {method: "POST", body: JSON.stringify({action: "accept", reviewer, reason, expectedRevision: item?.revision || 1, verifiedConfidence: "high"})});
    await loadGovernanceState(state.governance.selectedId); toast("本体候选已人工接受，可继续发布。");
  } catch (error) { toast(error.message, "error"); button.disabled = false; }
}

async function publishGovernanceAdmission(button) {
  const item = state.governance.admissions.find((candidate) => candidate.admissionId === button.dataset.govPublish);
  const row = button.closest(".governance-admission");
  const publisher = row?.querySelector(".gov-admission-publisher")?.value.trim();
  if (!publisher) { toast("请填写发布人。", "error"); return; }
  button.disabled = true;
  try {
    await api(`/api/intelligence/admissions/${encodeURIComponent(button.dataset.govPublish)}/publish`, {method: "POST", body: JSON.stringify({publisher, reviewer: publisher, expectedRevision: item?.revision || 1})});
    await loadGovernanceState(state.governance.selectedId); toast("候选已发布到本体库。");
  } catch (error) { toast(error.message, "error"); button.disabled = false; }
}

function evidenceChainMarkup(summary = {}, response = {}) {
  const evidence = Array.isArray(response.evidence) ? response.evidence : [];
  const claims = state.signalRecords?.claims?.length || summary.capabilityClaimCount || 0;
  const observations = state.signalRecords?.observations?.length || summary.signalObservationCount || 0;
  const sourceCount = summary.sourceCount || summary.materialCount || evidence.length || state.sources.length || 0;
  const entityCount = summary.entityCount || state.entities.length || 0;
  const sourceRows = evidence.slice(0, 8).map((item) => `<div class="evidence-chain-row"><span class="evidence-node entity"><b>${esc(item.entityId || "实体档案")}</b><small>${esc(item.kind || "对象")}</small></span><i>→</i><span class="evidence-node claim"><b>${esc(item.label || "能力断言")}</b><small>待核验候选</small></span><i>→</i><span class="evidence-node source"><b>${esc(item.sourceName || item.label || "公开来源")}</b><small>${esc(item.sourceUri || "来源快照")}</small></span></div>`).join("");
  return `<div class="evidence-overview"><span><small>实体</small><b>${esc(entityCount)}</b></span><span><small>能力断言</small><b>${esc(claims)}</b></span><span><small>信号观测</small><b>${esc(observations)}</b></span><span><small>来源证据</small><b>${esc(sourceCount)}</b></span></div><div class="evidence-chain"><div class="evidence-chain-head"><span>实体</span><span>能力 / 关系</span><span>观测 / 来源</span></div>${sourceRows || `<p class="analysis-empty">选择实体或完成采编后，这里显示“实体 → 能力断言 → 信号观测 → 证据包”的可追溯链。</p>`}</div>`;
}

function runContextMarkup(run = {}) {
  const tasks = Array.isArray(run.tasks) ? run.tasks.filter(Boolean) : [];
  const events = Array.isArray(run.events) ? run.events.filter(Boolean) : [];
  const artifacts = Array.isArray(run.artifacts) ? run.artifacts.filter(Boolean) : [];
  const rows = tasks.map((task) => `<div class="run-context-row"><span class="run-context-dot ${esc(task.status || "queued")}"></span><b>${esc(analysisRoleLabels[task.role] || task.role || "分析阶段")}</b><small>${esc(({succeeded: "已完成", running: "执行中", ready: "待执行", queued: "排队", failed: "失败", blocked: "阻塞", cancelled: "已取消"}[task.status] || task.status || "未知"))}</small></div>`).join("");
  return `<div class="context-stat-strip"><span><small>阶段</small><b>${esc(tasks.length)}</b></span><span><small>事件</small><b>${esc(events.length)}</b></span><span><small>产物</small><b>${esc(artifacts.length)}</b></span></div><div class="run-context-list">${rows || `<p class="context-empty">尚未创建运行；点击“启动分析”后显示阶段状态。</p>`}</div>`;
}

function liveAnalysisTraceMarkup(response = {}) {
  const trace = Array.isArray(response.trace) ? response.trace : [];
  if (!trace.length) return "";
  const rows = trace.slice(-8).map((item) => {
    const status = item.status || "done";
    const label = item.step || item.operation || "工具步骤";
    return `<div class="live-trace-row"><i class="live-trace-dot ${esc(status)}"></i><span>${esc(label)}</span><small>${esc(item.detail || status)}</small></div>`;
  }).join("");
  return `<details class="live-analysis-trace"><summary>本次执行过程 · ${trace.length} 步</summary><div>${rows}</div></details>`;
}

function liveAnalysisPanelMarkup(response = {}) {
  const execution = response.queryExecution || {};
  const status = execution.status || "completed";
  const statusLabel = {running: "实时计算中", completed: "本次已计算", fallback: "回退结果", failed: "计算失败"}[status] || status;
  const model = execution.model || {};
  const modelLabel = model.status === "completed"
    ? `DeepSeek ${model.model || "解释"} 已返回`
    : model.status === "failed" ? "DeepSeek 解释失败，保留数据库统计" : "DeepSeek 解释未启用";
  const computedAt = execution.computedAt ? new Date(execution.computedAt).toLocaleTimeString() : "--";
  const question = response.question || execution.question || "当前查询";
  const summary = response.summary || {};
  const hasStats = Object.keys(summary).length > 0;
  const explanation = response.modelExplanation?.text || "";
  const statusClass = status === "running" ? "is-running" : status === "failed" ? "is-error" : "";
  return `<div class="trend-analysis-head"><div><h3>实时分析结果</h3><small class="live-analysis-subtitle">本次查询 · 数据库工具实时读取</small></div><span id="trend-analysis-status" class="analysis-status ${statusClass}">${esc(statusLabel)}</span></div><div class="live-analysis-query" title="${esc(question)}">${esc(question)}</div><div class="live-analysis-source"><span>数据源：${esc(execution.source || "ontology_entity + ontology_entity_revision")}</span><span>计算：${esc(execution.statistics || "确定性统计")}</span><span>时间：${esc(computedAt)}</span><span>${esc(modelLabel)}</span></div>${explanation ? `<div class="live-analysis-explanation"><small>模型解释</small><p>${esc(explanation)}</p></div>` : ""}${hasStats ? `<div id="trend-metrics" class="trend-metrics"></div><div id="trend-charts" class="trend-charts"></div>` : `<div class="live-analysis-pending">${status === "running" ? "Agent 正在读取本体数据库并计算本次查询…" : "本次查询没有返回统计数据。"}</div>`}${liveAnalysisTraceMarkup(response)}`;
}

function waitingAnalysisPanelMarkup() {
  return `<div class="trend-analysis-head"><div><h3>实时分析结果</h3><small class="live-analysis-subtitle">等待 Agent 查询</small></div><span id="trend-analysis-status" class="analysis-status">未计算</span></div><div class="live-analysis-pending">输入问题或点击样本 prompt 后，Agent 会实时读取本体数据库，计算指标并生成图表。</div>`;
}

function renderRightContext(summary = {}, response = {}) {
  const panel = $("#trend-analysis");
  if (!panel) return;
  if (state.mainView === "run") {
    // A restored conversation from an older build may contain only answer,
    // trace and evidence. It is still a real Agent response and should not
    // be mistaken for the initial empty state.
    if (response && typeof response === "object" && Object.keys(response).length > 0) {
      panel.innerHTML = liveAnalysisPanelMarkup(response);
      const liveSummary = response.summary || {};
      if (Object.keys(liveSummary).length) {
        renderTrendMetrics(liveSummary);
        renderTrendCharts(liveSummary, response.charts || []);
      }
    } else if (state.analysisRun) {
      panel.innerHTML = `<div class="trend-analysis-head"><div><h3>运行上下文</h3></div><span id="trend-analysis-status" class="analysis-status">${esc(state.analysisRun?.status || "就绪")}</span></div>${runContextMarkup(state.analysisRun || {})}`;
    } else {
      panel.innerHTML = waitingAnalysisPanelMarkup();
    }
    return;
  }
  if (state.mainView === "evidence") {
    panel.innerHTML = `<div class="trend-analysis-head"><div><h3>证据上下文</h3></div><span class="analysis-status">可追溯</span></div>${evidenceChainMarkup(summary, response)}`;
    return;
  }
  const admissions = Array.isArray(state.governance.admissions) ? state.governance.admissions : [];
  const findings = Array.isArray(state.governance.findings) ? state.governance.findings : [];
  panel.innerHTML = `<div class="trend-analysis-head"><div><h3>审核上下文</h3></div><span class="analysis-status">人在回路</span></div><div class="review-context-list"><div><small>待审核候选</small><b>${esc(admissions.filter((item) => item?.status === "under-review").length)}</b></div><div><small>开放问题</small><b>${esc(findings.filter((item) => ["open", "needs-evidence"].includes(item?.state)).length)}</b></div><div><small>已发布</small><b>${esc(admissions.filter((item) => item?.status === "published").length)}</b></div></div><p class="context-empty">逐条采纳、拒绝、补证和发布操作位于审核面板。</p>`;
}

function analysisStageStatus(run, roles = []) {
  if (!run) return "planned";
  const tasks = (run.tasks || []).filter((task) => roles.includes(task.role));
  if (tasks.some((task) => ["failed", "blocked"].includes(task.status))) return "blocked";
  if (tasks.length && tasks.every((task) => task.status === "succeeded")) return "done";
  if (tasks.some((task) => ["running", "ready"].includes(task.status))) return "active";
  return run.status === "failed" ? "blocked" : "planned";
}

function userRunTrace(summary = {}) {
  const run = state.analysisRun;
  if (!run) return [
    {step: "读取本体", status: "done", detail: `${summary.entityCount || state.entities.length || 0} 个实体`},
    {step: "定向采编", status: "planned", detail: "选择实体后开始采集"},
    {step: "属性与关系", status: "planned", detail: "从已采集材料提取候选"},
    {step: "能力量化", status: "planned", detail: "条件化作战能力映射"},
    {step: "审核发布", status: "manual", detail: "人工确认后入本体"},
  ];
  const planner = analysisStageStatus(run, ["ontology-planner"]);
  const collector = analysisStageStatus(run, ["intelligence-collector"]);
  const extraction = analysisStageStatus(run, ["attribute-relation-extractor", "signal-imagery-analyst"]);
  const quantification = analysisStageStatus(run, ["evidence-fusion", "capability-quantification"]);
  const review = analysisStageStatus(run, ["red-team-governance-reviewer"]);
  const reviewStatus = review === "done" && run.status === "completed" ? "manual" : review;
  return [
    {step: "读取本体", status: planner, detail: planner === "done" ? "实体档案已读取" : "等待运行"},
    {step: "定向采编", status: collector, detail: collector === "done" ? "来源材料已保存" : "等待前置"},
    {step: "属性与关系", status: extraction, detail: extraction === "done" ? "候选已提取" : "等待材料"},
    {step: "能力量化", status: quantification, detail: quantification === "done" ? "指标已生成" : "等待候选"},
    {step: "审核发布", status: reviewStatus, detail: reviewStatus === "manual" ? "候选待人工审核" : review === "done" ? "审核包已生成" : "等待量化"},
  ];
}

function renderAnalysisWorkspace(summary = {}) {
function analysisRunResultMarkup(run = {}) {
  if (!run) return "";
  const tasks = Array.isArray(run.tasks) ? run.tasks : [];
  const outputOf = (role) => tasks.find((task) => task.role === role)?.output || {};
  const collector = outputOf("intelligence-collector");
  const extractor = outputOf("attribute-relation-extractor");
  const signal = outputOf("signal-imagery-analyst");
  const fusion = outputOf("evidence-fusion");
  const capability = outputOf("capability-quantification");
  const reviewer = outputOf("red-team-governance-reviewer");
  const capabilityResult = capability.result || {};
  const quality = capabilityResult.quality || reviewer.quality || {};
  const sourceCount = Number(collector.sourceCount || collector.sources?.length || 0);
  const candidateCount = Number(extractor.candidates?.length || extractor.candidateFacts?.length || reviewer.attributeCandidateCount || capabilityResult.attributeCandidates || 0);
  const observationCount = Number(signal.observations?.length || 0);
  const evidenceCount = Number(fusion.evidenceBundles?.length || capabilityResult.evidenceBundles || 0);
  const conflictCount = Number(fusion.conflictCount || capabilityResult.conflicts || reviewer.findings?.length || 0);
  const coverage = quality.fieldCoverage == null ? "--" : `${Math.round(Number(quality.fieldCoverage) * 100)}%`;
  const statusLabel = {queued: "排队", planning: "规划中", running: "执行中", paused: "已暂停", completed: "已生成", "completed-with-gaps": "已生成 · 有缺口", failed: "失败", cancelled: "已取消"}[run.status] || run.status || "处理中";
  const metrics = [["来源材料", sourceCount], ["属性候选", candidateCount], ["信号观测", observationCount], ["证据包", evidenceCount], ["字段覆盖", coverage], ["冲突", conflictCount]];
  return `<section class="analysis-run-result"><div class="analysis-result-head"><b>运行结果</b><span>${esc(statusLabel)}</span></div><div class="analysis-result-metrics">${metrics.map(([label, value]) => `<span><small>${esc(label)}</small><b>${esc(value)}</b></span>`).join("")}</div><p>${run.status === "completed" ? "结果已绑定实体版本和来源证据，下一步请打开“证据与结论”或“审核与发布”。" : "阶段结果会持续写入本次运行，完成后进入审核队列。"}</p></section>`;
}

  document.querySelectorAll("[data-main-view]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.mainView === state.mainView)));
  const statusNode = $("#analysis-run-status");
  if (statusNode) statusNode.textContent = ({queued: "排队", planning: "规划中", running: "运行中", paused: "已暂停", completed: "已完成", "completed-with-gaps": "部分完成", failed: "失败", cancelled: "已取消", ready: "就绪"}[state.analysisRunStatus] || state.analysisRunStatus || "就绪");
  const taskNode = $("#analysis-task-label");
  if (taskNode) {
    const stageLabels = {"ontology-planner": "制定研究计划", "intelligence-collector": "公开来源采编", "attribute-relation-extractor": "属性关系提取", "signal-imagery-analyst": "信号影像核验", "evidence-fusion": "证据融合", "capability-quantification": "能力量化", "red-team-governance-reviewer": "红队审核"};
    taskNode.textContent = state.analysisRun?.currentStage === "completed" ? "运行已完成" : stageLabels[state.analysisRun?.currentStage] || (state.analysisRun ? `运行 ${String(state.analysisRun.analysisRunId || "").slice(-8)}` : "本体库分析");
  }
  const startButton = $("#analysis-run");
  if (startButton) startButton.textContent = state.analysisRun ? (state.analysisRunStatus === "running" ? "新建运行" : "再次运行") : "启动分析";
  const pauseButton = $("#analysis-pause");
  if (pauseButton) {
    pauseButton.textContent = state.analysisRunStatus === "paused" ? "恢复" : "暂停";
    pauseButton.disabled = !state.analysisRun || ["completed", "completed-with-gaps", "failed", "cancelled"].includes(state.analysisRun.status);
  }
  const panel = $("#analysis-run-panel");
  if (!panel) return;
  const response = state.agentResponse || {};
  const copy = response ? {title: response.view || "本体分析", body: response.answer || "", bullets: (Array.isArray(response.actions) ? response.actions : []).map((action) => action.label || action.kind).slice(0, 4)} : analysisCopy(summary);
  const factsRows = Array.isArray(response.facts) ? response.facts : [];
  const tablesRows = Array.isArray(response.tables) ? response.tables : [];
  const facts = factsRows.length ? `<div class="agent-facts">${factsRows.map((fact) => `<span>${esc(fact.label)}<b>${esc(fmt(fact.value))}</b></span>`).join("")}</div>` : "";
  const tables = tablesRows.length ? tablesRows.map((table) => `<details class="agent-result-table" open><summary>${esc(table.title || "分析表")}</summary><table><thead><tr>${(Array.isArray(table.columns) ? table.columns : []).map((col) => `<th>${esc(col)}</th>`).join("")}</tr></thead><tbody>${(Array.isArray(table.rows) ? table.rows : []).slice(0, 12).map((row) => `<tr>${(Array.isArray(row) ? row : []).map((cell) => `<td>${esc(fmt(cell))}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`).join("") : "";
  const runBadge = ({running: "运行中", paused: "已暂停", queued: "排队", planning: "规划中", completed: "已完成", "completed-with-gaps": "部分完成", failed: "失败", cancelled: "已取消", ready: "可启动"}[state.analysisRunStatus] || "可启动");
  const runView = `<div class="analysis-copy"><div class="view-title-line"><h3>运行摘要</h3><span class="run-badge">${esc(runBadge)}</span></div><p>${esc(copy.body || "分析任务会读取本体数据，整理证据并生成待审核结果。")}</p>${facts}</div>${analysisRunResultMarkup(state.analysisRun)}`;
  const evidenceView = `<div class="analysis-copy analysis-wide"><div class="view-title-line"><h3>证据与结论</h3><span class="run-badge">可追溯</span></div><p>结论沿实体、能力断言、观测和来源证据逐级回溯；候选结果需经过核验。</p>${evidenceChainMarkup(summary, response)}${tables}</div>`;
  const reviewView = `<div class="analysis-copy analysis-wide"><div class="view-title-line"><h3>审核与发布</h3><span class="run-badge">人在回路</span></div><p>审核队列承接冲突、缺证和能力候选；接受后仍需显式发布，系统保留版本和回退点。</p><div class="review-gate-summary"><span><small>待审核候选</small><b>${esc(state.governance.admissions?.filter((item) => item.status === "under-review").length ?? "--")}</b></span><span><small>开放问题</small><b>${esc(state.governance.findings?.filter((item) => ["open", "needs-evidence"].includes(item.state)).length ?? "--")}</b></span><span><small>已发布版本</small><b>${esc(state.governance.admissions?.filter((item) => item.status === "published").length ?? "--")}</b></span></div><p class="analysis-note">下方治理面板提供逐条采纳、拒绝、补证和发布操作。</p></div>`;
  const workspace = $("#trend-workspace");
  if (workspace) workspace.dataset.mainView = state.mainView;
  panel.innerHTML = state.mainView === "evidence" ? evidenceView : state.mainView === "review" ? reviewView : runView;
  panel.dataset.view = state.mainView;
  renderRightContext(summary, response);
  renderGovernancePanel();
}

function trendAnswer(query) {
  const summary = state.trendSummary || {};
  const country = /国家|国别|服役|厂商|产业/.test(query);
  const performance = /续航|速度|载荷|性能|能力|指标/.test(query);
  const governance = /任务|运行|审核|发布|补证|流程/.test(query);
  const taxonomy = /类别|结构|分布|缺口/.test(query);
  state.analysisView = governance ? "governance" : country ? "geography" : performance ? "capability" : taxonomy ? "taxonomy" : state.analysisView;
  const rows = country ? (summary.countryCounts || []) : performance ? (summary.performanceByCategory || []) : (summary.categoryCounts || []);
  const result = governance ? "一次完整分析应先读本体库和统计工具，再形成研判草稿，最后把缺口转成对象级开源补证任务；任何档案更新都必须经过证据审核和发布。" : country ? `当前本体库中，型号来源国以 ${rows.slice(0, 3).map((row) => `${row.label}（${row.count}）`).join("、") || "暂无数据"} 为主，可继续追问厂商、授权生产和来源偏差。` : performance ? `我按类别汇总了有单位的公开字段。续航、速度、载荷的平均值只在字段和单位可比时计算，不能直接等同于作战能力。` : `当前本体库包含 ${summary.entityCount || 0} 个型号，类别分布和首飞年份已绘制在右侧；对象级补证请点击左侧具体无人机。`;
  const caveat = "数据来自已导入本体和 Excel 字段；外部来源仍须经过精确引用和人工审核，图表不会自动写入已发布知识。";
  return { result, caveat, focus: country ? "country" : performance ? "performance" : "category" };
}

async function appendConversationMessage(role, content, options = {}) {
  const sessionId = state.conversationSession?.conversationSessionId || state.conversationSession?.id || state.analysisSessionId;
  if (state.suppressConversationPersistence || !state.conversationApi || !sessionId) return null;
  const body = {
    role,
    content: String(content || ""),
    analysisRunId: Object.prototype.hasOwnProperty.call(options, "analysisRunId") ? options.analysisRunId : (state.analysisRun?.analysisRunId || state.analysisConversationRunId || null),
    intent: options.intent && typeof options.intent === "object" ? options.intent : {},
    contentJson: options.contentJson && typeof options.contentJson === "object" ? options.contentJson : {},
  };
  if (!body.content.trim()) return null;
  try {
    const result = await api(`/api/ontology/conversation-sessions/${encodeURIComponent(sessionId)}/messages`, {method: "POST", body: JSON.stringify(body)});
    state.conversationSession = {...(state.conversationSession || {}), ...(result.session || {}), conversationSessionId: sessionId};
    return result.message || null;
  } catch (error) {
    // Conversation persistence is an enhancement. If an older service or a
    // transient network failure is encountered, retain the local transcript
    // and keep the analysis workflow usable.
    state.conversationApiError = error.message || "会话接口不可用";
    state.conversationApi = false;
    return null;
  }
}

function appendTrendMessage(kind, text, meta = "", options = {}) {
  const thread = $("#trend-thread");
  if (!thread) return;
  thread.insertAdjacentHTML("beforeend", `<div class="trend-message ${kind}"><span class="trend-message-label">${kind === "user" ? "你" : "Agent"}</span><p>${esc(text)}</p>${meta ? `<small>${esc(meta)}</small>` : ""}</div>`);
  thread.scrollTop = thread.scrollHeight;
  persistAnalysisSession();
  if (options.persist !== false && (kind === "user" || kind === "agent" || kind === "assistant")) appendConversationMessage(kind === "user" ? "user" : "assistant", text, {contentJson: {meta}});
}

function resultDetails(response = {}) {
  const factsRows = Array.isArray(response.facts) ? response.facts : [];
  const tablesRows = Array.isArray(response.tables) ? response.tables : [];
  const evidenceRows = Array.isArray(response.evidence) ? response.evidence : [];
  const traceRows = Array.isArray(response.trace) ? response.trace : [];
  const modelExplanation = response.modelExplanation?.text ? `<div class="agent-model-explanation"><small>本次模型解释</small><p>${esc(response.modelExplanation.text)}</p>${(response.modelExplanation.insights || []).slice(0, 4).map((item) => `<span>${esc(item)}</span>`).join("")}</div>` : "";
  const facts = factsRows.length ? `<div class="agent-facts compact">${factsRows.map((fact) => `<span>${esc(fact.label)}<b>${esc(fmt(fact.value))}</b></span>`).join("")}</div>` : "";
  const tables = tablesRows.length ? tablesRows.map((table) => `<details class="agent-result-table"><summary>${esc(table.title || "查看分析表")}</summary><table><thead><tr>${(Array.isArray(table.columns) ? table.columns : []).map((col) => `<th>${esc(col)}</th>`).join("")}</tr></thead><tbody>${(Array.isArray(table.rows) ? table.rows : []).slice(0, 10).map((row) => `<tr>${(Array.isArray(row) ? row : []).map((cell) => `<td>${esc(fmt(cell))}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`).join("") : "";
  const evidence = evidenceRows.length ? `<details class="agent-evidence"><summary>数据来源与运行记录</summary>${evidenceRows.map((item) => `<span>${esc(item.kind || "source")} · ${esc(item.label || item.entityId || "")}</span>`).join("")}${traceRows.map((item) => `<span>${esc(item.step)} · ${esc(item.detail || "")}</span>`).join("")}</details>` : "";
  if (!facts && !tables && !evidence && !modelExplanation) return "";
  return `<div class="agent-response-details">${modelExplanation}${facts}${tables}${evidence}</div>`;
}

function appendAgentResponse(response, fallbackMeta = "") {
  const thread = $("#trend-thread");
  if (!thread) return;
  const meta = fallbackMeta || `${response.trace?.length || 0} 个运行步骤 · ${response.evidence?.length || 0} 条数据来源`;
  thread.insertAdjacentHTML("beforeend", `<div class="trend-message assistant"><span class="trend-message-label">Agent</span><p>${esc(response.answer || "")}</p>${meta ? `<small>${esc(meta)}</small>` : ""}${resultDetails(response)}</div>`);
  thread.scrollTop = thread.scrollHeight;
  persistAnalysisSession();
  const responseRunId = response.analysisRunId || response.runId || null;
  appendConversationMessage("assistant", response.answer || "", {analysisRunId: responseRunId, contentJson: {meta, trace: Array.isArray(response.trace) ? response.trace : [], evidence: Array.isArray(response.evidence) ? response.evidence : [], response}});
}

function createAnalysisSessionId() {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `session-${suffix}`;
}

const ANALYSIS_SESSION_STORAGE_KEY = "ontology-workbench.analysis-sessions.v1";

function loadAnalysisSessions() {
  if (state.analysisSessionsLoaded) return;
  state.analysisSessionsLoaded = true;
  // Conversation history belongs in the right hand workspace.  Clear the
  // legacy browser cache so old local-only sessions cannot reappear in the
  // left navigation after the server-side history is purged.
  state.analysisSessions = [];
  try { globalThis.localStorage?.removeItem(ANALYSIS_SESSION_STORAGE_KEY); } catch (error) { /* private mode or unavailable storage */ }
}

function mapConversationSession(session = {}) {
  const id = session.conversationSessionId || session.sessionId || session.id;
  if (!id) return null;
  return {
    id,
    conversationSessionId: id,
    title: session.title || "本体分析会话",
    entityId: session.entityId || null,
    status: session.status === "active" ? "ready" : (session.status || "ready"),
    updatedAt: session.updatedAt || session.lastMessageAt || session.createdAt || new Date().toISOString(),
    createdAt: session.createdAt || session.updatedAt || new Date().toISOString(),
    runId: session.runId || session.analysisRunId || null,
    html: session.html || "",
  };
}

function mergeConversationSession(session) {
  const mapped = mapConversationSession(session);
  if (!mapped) return null;
  const index = state.analysisSessions.findIndex((item) => item.id === mapped.id);
  if (index >= 0) state.analysisSessions[index] = {...state.analysisSessions[index], ...mapped};
  else state.analysisSessions.unshift(mapped);
  state.analysisSessions.sort((a, b) => String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || "")));
  return mapped;
}

async function loadConversationSessionsFromApi({force = false} = {}) {
  if (state.conversationLoadPromise && !force) return state.conversationLoadPromise;
  state.conversationLoadPromise = (async () => {
    try {
      const data = await api("/api/ontology/conversation-sessions?status=active&limit=50");
      const remote = Array.isArray(data.sessions) ? data.sessions.map(mapConversationSession).filter(Boolean) : [];
      state.conversationApi = true;
      state.conversationApiError = "";
      state.conversationSessions = remote;
      loadAnalysisSessions();
      // Do not repopulate the left navigation from either localStorage or
      // remote history.  The right panel is the sole conversation history
      // surface; the left panel is intentionally session-free.
      state.analysisSessions = [];
      renderAnalysisSessionList();
      // On a fresh page, restore the latest durable conversation instead of
      // leaving the user in the temporary local shell created by first paint.
      const current = currentAnalysisSession();
      const thread = $("#trend-thread");
      if (remote.length && (!current || !String(current.id).startsWith("conversation-")) && (!thread || thread.children.length <= 1)) {
        await restoreAnalysisSession(remote[0].id);
      }
      return remote;
    } catch (error) {
      state.conversationApi = false;
      state.conversationApiError = error.message || "会话接口不可用";
      loadAnalysisSessions();
      renderAnalysisSessionList();
      return [];
    } finally {
      state.conversationLoadPromise = null;
    }
  })();
  return state.conversationLoadPromise;
}

async function createConversationSession(title = "新会话") {
  if (state.conversationApi === null) await loadConversationSessionsFromApi();
  if (!state.conversationApi) return null;
  try {
    const result = await api("/api/ontology/conversation-sessions", {method: "POST", body: JSON.stringify({
      entityId: state.selected || null,
      title: title || "新会话",
      client: "ontology-workbench",
      context: {view: state.mainView || "run", mode: state.mode || "trend"},
    })});
    const session = result.session || result;
    const mapped = mergeConversationSession(session);
    state.conversationSession = session;
    if (mapped) {
      state.analysisSessionId = mapped.id;
      state.analysisConversationRunId = mapped.runId || null;
    }
    renderAnalysisSessionList();
    return session;
  } catch (error) {
    state.conversationApi = false;
    state.conversationApiError = error.message || "会话创建失败";
    return null;
  }
}

async function ensureBackendConversationSession(title = "新会话") {
  if (state.conversationApi === null) await loadConversationSessionsFromApi();
  if (!state.conversationApi) return state.analysisSessionId;
  const currentId = state.analysisSessionId;
  if (state.conversationSession && (state.conversationSession.conversationSessionId || state.conversationSession.sessionId) === currentId) return currentId;
  if (String(currentId || "").startsWith("conversation-")) {
    try {
      const data = await api(`/api/ontology/conversation-sessions/${encodeURIComponent(currentId)}?includeMessages=true`);
      state.conversationSession = data.session || data;
      return currentId;
    } catch (error) {
      state.conversationApi = false;
      state.conversationApiError = error.message || "会话读取失败";
      return currentId;
    }
  }
  const oldId = currentId;
  const created = await createConversationSession(title || sessionTitleFromThread());
  if (created) {
    const old = state.analysisSessions.find((item) => item.id === oldId);
    const mapped = currentAnalysisSession();
    if (old && mapped && old !== mapped) mapped.html = old.html || mapped.html;
    if (oldId && oldId !== state.analysisSessionId) state.analysisSessions = state.analysisSessions.filter((item) => item.id !== oldId);
    saveAnalysisSessions();
    renderAnalysisSessionList();
  }
  return state.analysisSessionId;
}

async function loadConversationSessionFromApi(sessionId) {
  if (!state.conversationApi || !sessionId) return null;
  try {
    const data = await api(`/api/ontology/conversation-sessions/${encodeURIComponent(sessionId)}?includeMessages=true`);
    state.conversationSession = data.session || data;
    const session = state.conversationSession;
    const messages = Array.isArray(session.messages) ? session.messages : [];
    const latestRunId = [...messages].reverse().find((message) => message && message.analysisRunId)?.analysisRunId || null;
    const mapped = mergeConversationSession({...session, runId: latestRunId});
    if (mapped) {
      mapped.runId = latestRunId || mapped.runId;
      state.analysisSessionId = mapped.id;
    }
    return session;
  } catch (error) {
    state.conversationApiError = error.message || "会话读取失败";
    return null;
  }
}

function saveAnalysisSessions() {
  try { globalThis.localStorage?.setItem(ANALYSIS_SESSION_STORAGE_KEY, JSON.stringify(state.analysisSessions.slice(0, 20))); } catch (error) { /* private mode or unavailable storage */ }
}

function sessionTitleFromThread() {
  const text = $("#trend-thread")?.querySelector(".trend-message.user p")?.textContent?.trim();
  return text ? text.slice(0, 34) : "新会话";
}

function currentAnalysisSession() {
  return state.analysisSessions.find((item) => item.id === state.analysisSessionId) || null;
}

function persistAnalysisSession() {
  if (!state.analysisSessionId) return;
  loadAnalysisSessions();
  const thread = $("#trend-thread");
  const current = currentAnalysisSession() || {id: state.analysisSessionId, createdAt: new Date().toISOString()};
  current.title = sessionTitleFromThread();
  current.updatedAt = new Date().toISOString();
  current.runId = state.analysisRun?.analysisRunId || state.analysisConversationRunId || null;
  current.status = state.analysisRun?.status || state.analysisRunStatus || "ready";
  current.html = thread?.innerHTML || current.html || "";
  const index = state.analysisSessions.findIndex((item) => item.id === current.id);
  if (index >= 0) state.analysisSessions[index] = current; else state.analysisSessions.unshift(current);
  state.analysisSessions.sort((a, b) => String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || "")));
  saveAnalysisSessions();
  renderAnalysisSessionList();
}

function renderAnalysisSessionList() {
  const list = $("#analysis-session-list");
  if (!list) return;
  loadAnalysisSessions();
  list.innerHTML = `<p class="analysis-session-empty">历史会话显示在右侧</p>`;
}

function ensureAnalysisSession() {
  loadAnalysisSessions();
  if (!state.analysisSessionId) state.analysisSessionId = state.analysisSessions[0]?.id || createAnalysisSessionId();
  if (!currentAnalysisSession()) persistAnalysisSession();
  const label = $("#agent-session-label");
  if (label) label.textContent = `会话 ${state.analysisSessionId.slice(-8)}`;
  renderAnalysisSessionList();
}

function clearConversationDisplay(message = "当前对话显示已清空。历史审计运行、证据和审核记录仍然保留。") {
  const thread = $("#trend-thread");
  if (!thread) return;
  state.agentResponse = null;
  thread.innerHTML = "";
  appendTrendMessage("agent", message, "可以输入新的 prompt 开始对话", {persist: false});
  persistAnalysisSession();
  const sessionId = state.conversationSession?.conversationSessionId || state.conversationSession?.sessionId || (String(state.analysisSessionId || "").startsWith("conversation-") ? state.analysisSessionId : null);
  if (state.conversationApi && sessionId) {
    api(`/api/ontology/conversation-sessions/${encodeURIComponent(sessionId)}/clear-display`, {method: "POST", body: "{}"}).catch((error) => {
      state.conversationApiError = error.message || "显示游标更新失败";
    });
  }
  renderAnalysisWorkspace(state.trendSummary || {});
}

function isRedundantWelcomeMessage(content = "") {
  return /你可以直接问我无人机本体数据|我会读取本体候选库回答；结果不自动入库/.test(String(content || ""));
}

async function startNewAnalysisSession() {
  persistAnalysisSession();
  clearTimeout(state.analysisRunPoll);
  state.analysisSessionId = createAnalysisSessionId();
  state.conversationSession = null;
  state.analysisConversationRunId = null;
  state.analysisRun = null;
  state.analysisRunStatus = "ready";
  state.agentResponse = null;
  state.mainView = "run";
  ensureAnalysisSession();
  clearConversationDisplay("新对话已创建。请输入研究问题或实体审计 prompt。", "历史审计运行仍可在实体档案和审核记录中查看");
  // Create the durable session as soon as the operator starts a new one. If
  // the service is unavailable the temporary local id remains usable.
  await ensureBackendConversationSession("新会话");
  renderAnalysisWorkspace(state.trendSummary || {});
}

async function restoreAnalysisSession(sessionId) {
  loadAnalysisSessions();
  let remoteSession = null;
  if (state.conversationApi === null) await loadConversationSessionsFromApi();
  if (state.conversationApi && String(sessionId || "").startsWith("conversation-")) remoteSession = await loadConversationSessionFromApi(sessionId);
  const session = state.analysisSessions.find((item) => item.id === sessionId) || mapConversationSession(remoteSession || {});
  if (!session) return;
  const previousSuppress = state.suppressConversationPersistence;
  state.suppressConversationPersistence = true;
  try {
    persistAnalysisSession();
    clearTimeout(state.analysisRunPoll);
    state.analysisSessionId = session.id;
    state.analysisConversationRunId = session.runId || null;
    state.analysisRun = null;
    state.analysisRunStatus = session.status || "ready";
    state.agentResponse = null;
    const thread = $("#trend-thread");
    if (thread) thread.innerHTML = "";
    if (remoteSession && Array.isArray(remoteSession.messages)) {
      remoteSession.messages.forEach((message) => {
        if (!message || !message.content) return;
        if (message.role !== "user" && isRedundantWelcomeMessage(message.content)) return;
        const contentJson = message.contentJson && typeof message.contentJson === "object" ? message.contentJson : {};
        if (message.role === "user") appendTrendMessage("user", message.content, "", {persist: false});
        else if (message.role === "assistant") {
          const restoredResponse = contentJson.response && typeof contentJson.response === "object"
            ? contentJson.response
            : {answer: message.content, trace: contentJson.trace || [], evidence: contentJson.evidence || []};
          state.agentResponse = {...restoredResponse, answer: message.content || restoredResponse.answer || ""};
          state.trendSummary = state.agentResponse.summary || state.trendSummary;
          appendAgentResponse(state.agentResponse, contentJson.meta || "");
        }
        else appendTrendMessage("agent", message.content, message.role, {persist: false});
      });
    } else if (thread) {
      thread.innerHTML = session.html || "";
      thread.querySelectorAll(".trend-message").forEach((message) => {
        if (isRedundantWelcomeMessage(message.querySelector("p")?.textContent || "")) message.remove();
      });
    }
    // Older local sessions may have saved the removed execution event block.
    // Strip it while restoring the transcript so the new compact layout is
    // applied without requiring the user to clear the conversation manually.
    thread?.querySelectorAll("[data-analysis-event-stream], .analysis-run-message, .analysis-stage-message").forEach((node) => node.remove());
    if (thread && !thread.children.length) appendTrendMessage("agent", "已恢复该会话。请输入新的研究问题，或查看右侧运行上下文。", "历史运行和审核记录仍然保留", {persist: false});
    ensureAnalysisSession();
    renderAnalysisWorkspace(state.trendSummary || {});
    if (session.runId) {
      const run = await loadAnalysisRun(session.runId);
      if (run && ["queued", "planning", "running"].includes(run.status)) pollAnalysisRun(run.analysisRunId);
    }
  } finally {
    state.suppressConversationPersistence = previousSuppress;
  }
}

const analysisRoleLabels = {
  "ontology-planner": "读取本体",
  "intelligence-collector": "定向采编",
  "attribute-relation-extractor": "属性与关系",
  "signal-imagery-analyst": "信号观测",
  "evidence-fusion": "证据融合",
  "capability-quantification": "能力量化",
  "red-team-governance-reviewer": "审核发布",
};

function analysisRunTaskForEvent(run, event) {
  return (run?.tasks || []).find((task) => task.agentTaskId === event?.agentTaskId) || {};
}

function analysisRunStageResult(task = {}) {
  const role = task.role || "";
  const output = task.output || {};
  const result = output.result || {};
  const quality = result.quality || output.quality || {};
  let summary = "阶段结果已保存，等待下一阶段使用。";
  let details = [];
  const providerCalls = Array.isArray(output.providerCalls) ? output.providerCalls : [];
  let providerDetail = "";
  if (providerCalls.length) {
    const providers = providerCalls.reduce((counts, call) => {
      const provider = String(call?.provider || call?.model || "运行时工具").toLowerCase();
      const label = /firecrawl|crawl/.test(provider) ? "Firecrawl" : /deepseek|model|llm/.test(provider) ? "DeepSeek" : (call?.provider || call?.model || "运行时工具");
      counts[label] = (counts[label] || 0) + 1;
      return counts;
    }, {});
    providerDetail = `工具调用：${Object.entries(providers).map(([label, count]) => `${label} ${count} 次`).join(" · ")}`;
  }
  if (role === "ontology-planner") {
    summary = "已读取实体档案并确定本次审计范围。";
    details = [output.entityId ? `实体：${output.entityId}` : "实体：当前选中对象", output.objectRevision ? `档案版本：${output.objectRevision}` : "档案版本：当前版本"];
  } else if (role === "intelligence-collector") {
    const count = Number(output.sourceCount || output.sources?.length || output.events?.length || 0);
    summary = `已完成定向采编，形成 ${count} 条来源材料。`;
    details = [`来源材料：${count}`, "来源正文和采编事件已绑定到本次运行"];
  } else if (role === "attribute-relation-extractor") {
    const candidates = output.candidateFacts?.length || output.candidates?.length || 0;
    const claims = output.capabilityClaims?.length || 0;
    summary = `已提取 ${candidates} 个属性候选、${claims} 个能力断言。`;
    details = [`属性候选：${candidates}`, `能力断言：${claims}`, "当前状态：待审核"];
  } else if (role === "signal-imagery-analyst") {
    const observations = output.observations?.length || 0;
    const signatures = output.emitterSignatures?.length || 0;
    summary = `已整理 ${observations} 个信号观测和 ${signatures} 个影像 / 发射特征。`;
    details = [`信号观测：${observations}`, `特征记录：${signatures}`];
  } else if (role === "evidence-fusion") {
    const bundles = output.evidenceBundles?.length || 0;
    const conflicts = Number(output.conflictCount || quality.conflictCount || 0);
    summary = `已完成证据融合，绑定 ${bundles} 个证据包，发现 ${conflicts} 个冲突。`;
    details = [`证据包：${bundles}`, `来源冲突：${conflicts}`, "来源独立性：待审核"];
  } else if (role === "capability-quantification") {
    const coverage = quality.fieldCoverage == null ? "--" : `${Math.round(Number(quality.fieldCoverage) * 100)}%`;
    const profile = result.profile || output.profile || {};
    const metrics = profile.metrics?.length || profile.capabilities?.length || result.metrics?.length || 0;
    summary = `已生成能力量化结果，覆盖率 ${coverage}，形成 ${metrics} 项能力指标。`;
    details = [`字段覆盖率：${coverage}`, `能力指标：${metrics}`, "结果状态：待审核"];
  } else if (role === "red-team-governance-reviewer") {
    const findings = output.findings?.length || 0;
    const candidates = Number(output.attributeCandidateCount || output.claimCount || 0);
    summary = `已生成审核包，包含 ${candidates} 个候选结果和 ${findings} 个待处理问题。`;
    details = [`候选结果：${candidates}`, `待处理问题：${findings}`, "发布条件：人工审核后显式发布"];
  }
  if (providerDetail) details.push(providerDetail);
  return {summary, details};
}

function analysisRunStageMarkup(run, event, task, status) {
  const role = task.role || "";
  const label = analysisRoleLabels[role] || "分析阶段";
  const result = analysisRunStageResult(task);
  const running = status === "running";
  const failed = status === "failed";
  const headline = running ? `正在${label}…` : failed ? `${label}失败` : `${label}完成`;
  const body = running ? `Agent 正在${label}，完成后会把结果继续交给下一阶段。` : failed ? `该阶段未能完成：${task.error || "请查看运行错误并重试。"}` : result.summary;
  const detailMarkup = !running && !failed ? `<details class="analysis-stage-result"><summary>查看本阶段结果</summary>${result.details.map((item) => `<span>${esc(item)}</span>`).join("")}</details>` : "";
  return `<span class="trend-message-label">Agent · ${esc(label)}</span><p>${esc(headline)}</p><small>${esc(body)}</small>${detailMarkup}`;
}

const analysisEventLabels = {
  "run-created": "运行已创建", "tasks-planned": "执行计划", "run-started": "运行开始",
  "run-paused": "运行暂停", "run-cancelled": "运行取消", "run-completed": "运行完成",
  "run-failed": "运行失败", "run-budget-exceeded": "预算停止", "task-ready": "Agent 已就绪",
  "task-started": "Agent 开始执行", "task-succeeded": "Agent 已完成", "task-failed": "Agent 执行失败",
  "task-retry": "Agent 等待重试", "task-retry-requested": "Agent 已重新排队", "task-blocked": "Agent 被阻塞",
  "task-reclaimed": "任务已回收", "artifact-created": "产物已保存", "agent-handoff-created": "交接已创建",
  "agent-handoff-updated": "交接状态更新",
};

const analysisArtifactLabels = {
  "research-plan.v1": "研究计划", "source-bundle.v1": "来源快照", "ontology-candidates.v1": "属性与关系候选",
  "observation-bundle.v1": "信号 / 影像观测", "evidence-bundle.v1": "证据融合包", "capability-assessment.v1": "能力量化结果",
};

function analysisEventTask(run, event) {
  return (run?.tasks || []).find((task) => task.agentTaskId === event?.agentTaskId) || {};
}

function analysisEventTime(event) {
  if (!event?.createdAt) return "";
  const parsed = new Date(event.createdAt);
  return Number.isNaN(parsed.getTime()) ? String(event.createdAt) : parsed.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function analysisEventDescriptor(run, event, index) {
  const type = String(event?.eventType || "event");
  const detail = event?.detail && typeof event.detail === "object" ? event.detail : {};
  const task = analysisEventTask(run, event);
  const label = analysisRoleLabels[task.role] || (event?.agentTaskId ? "分析 Agent" : "Agent");
  const status = type.includes("failed") || type === "run-failed" || type === "task-blocked" ? "failed" : type.includes("succeeded") || type === "run-completed" || type === "artifact-created" ? "success" : type.includes("started") || type === "run-started" ? "running" : "neutral";
  let actor = analysisEventLabels[type] || "运行事件";
  let summary = "运行事件已记录。";
  let icon = status === "success" ? "✓" : status === "failed" ? "!" : status === "running" ? "›" : "·";
  if (type === "run-created") summary = `${detail.taskCount || run?.tasks?.length || 0} 个 Agent 阶段已登记`;
  else if (type === "tasks-planned") summary = `${Array.isArray(detail.roles) ? detail.roles.length : run?.tasks?.length || 0} 个阶段等待调度`;
  else if (type === "run-started") summary = "后台协同执行已启动";
  else if (type === "run-completed") summary = `运行状态：${detail.status || run?.status || "completed"} · 候选需人工审核后发布`;
  else if (type === "run-failed") summary = detail.reason || run?.error || "请查看失败阶段并重试";
  else if (["run-paused", "run-cancelled", "run-budget-exceeded"].includes(type)) summary = detail.reason || detail.stopReason || run?.stopReason || "运行已停止";
  else if (type === "task-ready") summary = `${Array.isArray(detail.dependencies) ? detail.dependencies.length : 0} 个前置依赖已满足`;
  else if (type === "task-started") summary = `第 ${detail.attempt || task.attempt || 1} 次尝试 · 租约已建立`;
  else if (type === "task-succeeded") summary = `输出已写入运行记录${Array.isArray(detail.outputKeys) && detail.outputKeys.length ? ` · ${detail.outputKeys.length} 个字段` : ""}`;
  else if (["task-failed", "task-retry"].includes(type)) summary = detail.error || "阶段执行失败，等待重试";
  else if (["task-retry-requested", "task-reclaimed"].includes(type)) summary = detail.reason || "已重新排队，等待 Agent 领取";
  else if (type === "task-blocked") summary = detail.reason || "依赖阶段未完成";
  else if (type === "artifact-created") summary = `${analysisArtifactLabels[detail.artifactType] || detail.artifactType || "分析产物"} · ${detail.status || "under-review"}`;
  else if (type === "agent-handoff-created") {
    const from = (run?.tasks || []).find((item) => item.agentTaskId === detail.fromTaskId);
    const to = (run?.tasks || []).find((item) => item.agentTaskId === detail.toTaskId);
    actor = "Agent 交接";
    summary = `${analysisRoleLabels[from?.role] || "上游 Agent"} → ${analysisRoleLabels[to?.role] || "下游 Agent"}${detail.artifactId ? " · 产物已绑定" : ""}`;
    icon = "↗";
  } else if (type === "agent-handoff-updated") {
    const handoff = (run?.handoffs || []).find((item) => item.handoffId === detail.handoffId);
    const from = (run?.tasks || []).find((item) => item.agentTaskId === handoff?.fromTaskId);
    const to = (run?.tasks || []).find((item) => item.agentTaskId === handoff?.toTaskId);
    actor = "Agent 交接";
    summary = `${analysisRoleLabels[from?.role] || "上游 Agent"} → ${analysisRoleLabels[to?.role] || "下游 Agent"} · ${detail.status || handoff?.status || "updated"}`;
    icon = "↗";
  }
  return {id: String(event?.eventId || `${type}-${index}`), type, actor: `${label} · ${actor}`, summary, status, icon, time: analysisEventTime(event)};
}

function analysisExecutionMarkup(run, events) {
  const rows = events.map((event, index) => {
    const item = analysisEventDescriptor(run, event, index);
    return `<div class="analysis-event-row ${item.status}" data-analysis-event-id="${esc(item.id)}"><span class="analysis-event-icon" aria-hidden="true">${item.icon}</span><span class="analysis-event-copy"><b>${esc(item.actor)}</b><span>${esc(item.summary)}</span></span><time>${esc(item.time)}</time></div>`;
  }).join("");
  return `<div class="trend-message assistant analysis-execution-message" data-analysis-run-id="${esc(String(run.analysisRunId))}" data-analysis-event-stream="true"><span class="trend-message-label">Agent · 执行过程 <span class="analysis-event-count">${events.length} 个事件</span></span><div class="analysis-execution-stream">${rows || `<span class="context-empty">等待后台 Agent 事件…</span>`}</div></div>`;
}

function syncAnalysisConversation(run) {
  const thread = $("#trend-thread");
  if (!thread || !run?.analysisRunId) return;
  const runId = String(run.analysisRunId);
  // Execution events remain persisted in the AnalysisRun and are available
  // from the run context. They are intentionally kept out of the chat stream
  // so the conversation contains the user's question and useful results.
  [...thread.querySelectorAll("[data-analysis-run-id]")]
    .filter((node) => node.dataset.analysisRunId === runId && (node.dataset.analysisEventStream === "true" || node.classList.contains("analysis-run-message") || node.classList.contains("analysis-stage-message")))
    .forEach((node) => node.remove());
  persistAnalysisSession();
  return;
  const events = (Array.isArray(run.events) ? run.events : [])
    .filter((event) => event && typeof event === "object")
    .map((event, index) => ({...event, __order: index}))
    .sort((a, b) => {
      const left = Date.parse(a.createdAt || "");
      const right = Date.parse(b.createdAt || "");
      if (Number.isFinite(left) && Number.isFinite(right) && left !== right) return left - right;
      return a.__order - b.__order;
    });
  let eventStream = [...thread.querySelectorAll("[data-analysis-event-stream]")].find((node) => node.dataset.analysisRunId === runId);
  if (!eventStream) {
    thread.insertAdjacentHTML("beforeend", analysisExecutionMarkup(run, events));
  } else {
    eventStream.outerHTML = analysisExecutionMarkup(run, events);
  }
  const eventLabels = {"run-created": "已创建本次分析运行。", "tasks-planned": `已生成 ${run.tasks?.length || 0} 个阶段的执行计划。`, "run-started": "已开始执行，后续结果会在对话中逐步展开。", "run-completed": run.status === "completed" ? "全部阶段已完成，结果已进入审核队列。" : "运行已结束，但仍存在未完成阶段，请查看缺口。", "run-failed": `运行失败：${run.error || "请查看失败阶段并重试。"}`};
  events.forEach((event) => {
    const type = event.eventType;
    const task = analysisRunTaskForEvent(run, event);
    if (["task-started", "task-succeeded", "task-failed"].includes(type)) {
      const role = task.role || event.agentTaskId || "unknown";
      let card = [...thread.querySelectorAll("[data-analysis-run-id]")].find((node) => node.dataset.analysisRunId === runId && node.dataset.analysisRole === role);
      const status = type === "task-started" ? "running" : type === "task-failed" ? "failed" : "done";
      if (!card) {
        card = document.createElement("div");
        card.className = "trend-message assistant analysis-stage-message";
        card.dataset.analysisRunId = runId;
        card.dataset.analysisRole = role;
        thread.appendChild(card);
      }
      card.dataset.analysisStatus = status;
      card.dataset.analysisEventId = event.eventId || "";
      card.innerHTML = analysisRunStageMarkup(run, event, task, status);
      return;
    }
    if (!eventLabels[type]) return;
    const eventKey = String(event.eventId || `${type}-${event.__order}`);
    if ([...thread.querySelectorAll("[data-analysis-event-id]")].some((node) => node.dataset.analysisEventId === eventKey)) return;
    if (["run-created", "tasks-planned", "run-started"].includes(type) && [...thread.querySelectorAll("[data-analysis-event-type]")].some((node) => node.dataset.analysisRunId === runId && node.dataset.analysisEventType === type)) return;
    const message = document.createElement("div");
    message.className = "trend-message assistant analysis-run-message";
    message.dataset.analysisRunId = runId;
    message.dataset.analysisEventId = eventKey;
    message.dataset.analysisEventType = type;
    message.innerHTML = `<span class="trend-message-label">Agent · 运行状态</span><p>${esc(eventLabels[type])}</p><small>${event.createdAt ? esc(new Date(event.createdAt).toLocaleTimeString()) : ""}</small>`;
    thread.appendChild(message);
  });
  if (run.status === "completed" || run.status === "completed-with-gaps" || run.status === "failed") {
    const terminalId = `${runId}:terminal`;
    if (![...thread.querySelectorAll("[data-analysis-event-id]")].some((node) => node.dataset.analysisEventId === terminalId)) {
      const terminal = document.createElement("div");
      terminal.className = "trend-message assistant analysis-run-message analysis-run-terminal";
      terminal.dataset.analysisRunId = runId;
      terminal.dataset.analysisEventId = terminalId;
       terminal.innerHTML = `<span class="trend-message-label">Agent · 下一步</span><p>${run.status === "failed" ? "请修复失败阶段后重新运行。" : "你可以继续在当前对话追问，或打开实体档案处理候选结果。"}</p><small>候选结果不会自动写入正式本体。</small>`;
      thread.appendChild(terminal);
    }
  }
  thread.scrollTop = thread.scrollHeight;
  persistAnalysisSession();
}

function renderTrendWorkspace() {
  const trend = $("#trend-workspace");
  const dossier = $("#dossier-workspace");
  if (!trend || !dossier) return;
  ensureAnalysisSession();
  trend.hidden = state.mode !== "trend";
  dossier.hidden = state.mode !== "dossier";
  if (state.mode !== "trend") return;
  const summary = state.trendSummary || {};
  const entityCountNode = $("#trend-entity-count");
  if (entityCountNode) entityCountNode.textContent = summary.entityCount ?? state.entities.length ?? "--";
  renderAnalysisWorkspace(summary);
}

async function loadTrendSummary() {
  try {
    const [data, agent] = await Promise.all([api("/api/ontology/trend-summary"), api("/api/ontology/agent/state")]);
    state.trendSummary = data.summary || agent.summary || {};
    state.agentState = agent;
  } catch (error) {
    const counts = new Map();
    const layers = new Map(); const classes = new Map();
    state.entities.forEach((entity) => { const taxonomy = entityTaxonomy(entity); const category = entity.category || taxonomy.classLabel; counts.set(category, (counts.get(category) || 0) + 1); layers.set(taxonomy.topLabel, (layers.get(taxonomy.topLabel) || 0) + 1); classes.set(taxonomy.classLabel, (classes.get(taxonomy.classLabel) || 0) + 1); });
    const sorted = (map) => [...map].map(([label, count]) => ({label, count})).sort((a, b) => b.count - a.count);
    state.trendSummary = { entityCount: state.entities.length, categoryCounts: sorted(counts), ontologyLayerCounts: sorted(layers), ontologyClassCounts: sorted(classes), countryCounts: [], firstFlightByYear: [], performanceByCategory: [] };
    toast("趋势统计接口暂不可用，已显示对象列表统计", "error");
  }
  renderTrendWorkspace();
}

function showTrendWorkspace() { state.mode = "trend"; state.selected = null; state.detail = null; renderEntities(); renderTrendWorkspace(); }

function isExecutionPrompt(text = "") {
  return /请审计|启动.*审计|补证|检索证据|定向采编|采集.*来源|提取.*属性|生成.*审核包|补全.*属性|firecrawl|deepseek/i.test(String(text));
}

async function submitTrendQuery(query) {
  const text = String(query || "").trim();
  if (!text) return;
  // Resolve a durable conversation before creating a run. The local session
  // id is replaced with the ConversationStore id when the API is available.
  await ensureBackendConversationSession(text.slice(0, 80));
  const executionPrompt = isExecutionPrompt(text);
  appendTrendMessage("user", text, "", {persist: false});
  if (executionPrompt) {
    await startAnalysisRun(text);
    return;
  }
  await appendConversationMessage("user", text, {analysisRunId: null, contentJson: {view: state.mainView || "run", entityId: state.selected || null}, intent: {type: "ontology-query"}});
  await queryAgent({question: text});
}

async function queryAgent({question = "", view = ""} = {}) {
  const fallback = trendAnswer(question || view);
  state.agentResponse = {
    question,
    answer: "",
    summary: null,
    charts: [],
    facts: [],
    trace: [{step: "agent-dispatch", status: "running", detail: "等待本体数据库工具返回"}],
    evidence: [],
    queryExecution: {status: "running", question, source: "ontology_entity + ontology_entity_revision", statistics: "实时计算"},
  };
  renderAnalysisWorkspace(state.trendSummary || {});
  try {
    const response = await api("/api/ontology/agent/query", {method:"POST", body: JSON.stringify({question, view})});
    state.agentResponse = response;
    state.trendSummary = response.summary || state.trendSummary;
    state.analysisView = response.view || state.analysisView;
    appendAgentResponse(response);
    renderAnalysisWorkspace(response.summary || state.trendSummary || {});
    document.querySelectorAll(".trend-chart").forEach((chart) => chart.classList.toggle("is-focus", chart.classList.contains(`trend-chart-${response.view === "geography" ? "country" : response.view === "capability" ? "performance" : "category"}`)));
  } catch (error) {
    state.analysisRun = null;
    state.analysisRunStatus = "ready";
    state.agentResponse = {
      question,
      answer: fallback.result,
      summary: null,
      charts: [],
      facts: [],
      trace: [{step: "agent-query", status: "failed", detail: error.message || "Agent API 不可用"}],
      evidence: [],
      queryExecution: {status: "failed", question, source: "ontology database", statistics: "无法完成实时计算"},
    };
    appendTrendMessage("agent", fallback.result, `${fallback.caveat}；Agent API 暂不可用：${error.message}`);
    renderAnalysisWorkspace(state.trendSummary || {});
    document.querySelectorAll(".trend-chart").forEach((chart) => chart.classList.toggle("is-focus", chart.classList.contains(`trend-chart-${fallback.focus}`)));
  }
}

async function loadJobs(entityId) { const data = await api(`/api/ontology/verifications?entityId=${encodeURIComponent(entityId)}`); state.jobs = data.verifications || []; }
async function loadSources(entityId) { const data = await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/sources`); state.sources = data.sources || []; }
async function loadLineage(entityId) { const data = await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/lineage`); state.lineage = data.lineage || []; }
async function loadSignalRecords(entityId, refresh = false) {
  if (!entityId) return state.signalRecords;
  try {
    const data = await api(`/api/ontology/entities/${encodeURIComponent(entityId)}/signal-chain${refresh ? `?t=${Date.now()}` : ""}`);
    state.signalRecords = {
      observations: Array.isArray(data.observations) ? data.observations : [],
      claims: Array.isArray(data.claims) ? data.claims : [],
      evidenceBundles: Array.isArray(data.evidenceBundles) ? data.evidenceBundles : [],
      verificationRuns: Array.isArray(data.verificationRuns) ? data.verificationRuns : [],
      emitterSignatures: Array.isArray(data.emitterSignatures) ? data.emitterSignatures : [],
    };
  } catch (error) {
    /* Older service builds do not expose the signal-chain route. Keep the
       dossier usable and let the tab explain that no records are available. */
    state.signalRecords = {observations: [], claims: [], evidenceBundles: [], verificationRuns: [], emitterSignatures: []};
  }
  state.selectedSignal = null;
  if (state.tab === "signals") renderSignals();
  return state.signalRecords;
}
async function selectEntity(entityId) {
  const request=++entityRequest;
  state.mode = "dossier"; state.selected = entityId; state.analysisRun = null; state.analysisRunStatus = "ready"; clearTimeout(state.analysisRunPoll); state.objectAgent = null;state.profile=null;state.capabilityRun=null;state.auditRun=null;state.manufacturing=null;state.factorGraph=null;state.signalRecords={observations:[],claims:[],evidenceBundles:[],verificationRuns:[],emitterSignatures:[]};state.selectedSignal=null;state.relations=[];state.rowQuery='';$('#row-search').value=''; renderEntities(); renderTrendWorkspace(); $("#workbench-content").innerHTML = `<p class="loading">正在读取对象版本与证据…</p>`;
  try {
    const base=`/api/ontology/entities/${encodeURIComponent(entityId)}`;
    const [detail,objectAgent,jobs,sources,lineage,relations,profile,runs,audits,signals,manufacturing,factorGraph]=await Promise.all([api(base),api(base+'/agent'),api(`/api/ontology/verifications?entityId=${encodeURIComponent(entityId)}`),api(base+'/sources'),api(base+'/lineage'),api(base+'/relations'),api(base+'/capability-profile'),api(base+'/capability-runs'),api(base+'/audit-runs'),api(base+'/signal-chain').catch(() => ({})),api(base+'/manufacturing').catch(() => ({})),api(base+'/factor-graph').catch(() => ({}))]);
    if(request!==entityRequest)return;
    state.detail=detail.entity;state.objectAgent=objectAgent;state.jobs=jobs.verifications||[];state.sources=sources.sources||[];state.lineage=lineage.lineage||[];state.relations=relations.relations||[];state.manufacturing=manufacturing||null;state.factorGraph=factorGraph.graph||null;state.signalRecords={observations:Array.isArray(signals.observations)?signals.observations:[],claims:Array.isArray(signals.claims)?signals.claims:[],evidenceBundles:Array.isArray(signals.evidenceBundles)?signals.evidenceBundles:[],verificationRuns:Array.isArray(signals.verificationRuns)?signals.verificationRuns:[],emitterSignatures:Array.isArray(signals.emitterSignatures)?signals.emitterSignatures:[]};state.profile=detail.entity.entityType==='EquipmentModel'?profile.profile:null;state.capabilityRun=runs.runs?.[0]||null;state.auditRun=audits.auditRuns?.[0]||null;
    state.attributeGroup='all';state.historyGroup='all';state.capabilityGroup='all';state.capabilityView='profile';state.tab='overview';
    history.replaceState(null,'',`?entityId=${encodeURIComponent(state.detail.entityId)}`);renderHeader();renderContent();
    if(state.auditRun && ['queued','planning','collecting','extracting','reconciling'].includes(state.auditRun.status))pollAuditRun(state.auditRun.auditRunId,entityId);
    await loadLatestAnalysisRun(entityId);
    if(state.analysisRun && ['queued','planning','running'].includes(state.analysisRun.status)) pollAnalysisRun(state.analysisRun.analysisRunId);
  }
  catch (error) { toast(error.message,"error"); }
}
async function createPlan() {
  if (!state.selected) return;
  const button = $("#create-plan"); button.disabled = true;
  try { await planVerificationJobs(); }
  catch(error){ toast(error.message,"error"); } finally { button.disabled = false; }
}
async function planVerificationJobs({quiet = false} = {}) {
  if (!state.selected) return [];
  const data = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/verification-plan`,{method:"POST",body:"{}"});
  state.jobs = data.verifications || [];
  renderHeader();
  if (state.tab === "audit") renderAudit();
  if (!quiet) toast(`已生成 ${state.jobs.length} 个定向核验任务`);
  return state.jobs;
}
async function executeJob(id, button) {
  button.disabled = true; button.textContent = "执行中";
  try { const data = await api(`/api/ontology/verifications/${encodeURIComponent(id)}/execute`,{method:"POST",body:"{}"}); const index = state.jobs.findIndex((job) => job.verificationId === id); if(index >= 0) state.jobs[index] = data.verification; renderHeader(); renderContent(); toast(`核验完成：${data.verification.result?.items?.length || 0} 条材料`); }
  catch(error){ toast(error.message,"error"); button.disabled = false; button.textContent = "重试"; }
}
async function reclaimVerificationJobs(button) {
  button = button?.currentTarget || button;
  if (button) { button.disabled = true; button.textContent = "检查中…"; }
  try {
    const data = await api("/api/ontology/verification-recovery", {method:"POST", body:JSON.stringify({})});
    if (state.selected) {
      const jobs = await api(`/api/ontology/verifications?entityId=${encodeURIComponent(state.selected)}`);
      state.jobs = jobs.verifications || [];
    }
    renderHeader(); renderContent();
    toast(data.count ? `已回收 ${data.count} 个无心跳任务，可重新核验。` : "没有发现超时任务。", data.count ? "success" : "");
  } catch (error) { toast(error.message, "error"); }
  finally { if (button) { button.disabled = false; button.textContent = "回收卡住任务"; } }
}
/* Keep the object-level actions in module scope so the compact workflow bar
   can call them after it is rendered dynamically. */
async function runObjectWorkflow(action, button) {
  if (!state.selected) return;
  const scope = $("#attribute-scope")?.value || "missing";
  const original = button.textContent;
  button.disabled = true;
  button.textContent = action === "collect" ? "检索中…" : "提取中…";
  try {
    const endpoint = action === "collect" ? "collect" : "extract";
    const result = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent/${endpoint}`, {method:"POST", body: JSON.stringify({scope})});
    if (result.jobs) state.jobs = result.jobs;
    if (result.candidates) state.objectAgent = {...(state.objectAgent || {}), candidates: result.candidates};
    const brief = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent`);
    state.objectAgent = brief;
    state.jobs = brief.jobs || state.jobs;
    if (action === "collect") {
      state.tab = "audit";
      syncTabNavigation();
      renderAudit();
    }
    renderHeader();
    toast(result.answer || "对象流程已完成。", result.status === "model-unavailable" || result.status === "no-evidence" ? "error" : "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function reviewAttributeCandidate(candidateId, action, button) {
  const original = button.textContent;
  button.disabled = true;
  try {
    await api(`/api/ontology/attribute-candidates/${encodeURIComponent(candidateId)}/review`, {method:"POST", body: JSON.stringify({action, reviewer:"operator.demo", reason: action === "accept" ? "人工确认候选可进入待发布修订" : "对象页人工审核"})});
    state.objectAgent = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent`);
    renderHeader();
    renderContent();
    toast(action === "accept" ? "已记录采纳结果，尚未写入档案。" : "候选审核状态已更新。");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function reviewRelationCandidate(candidateId, action, button) {
  const original=button.textContent; button.disabled=true;
  try {
    if(action === "publish") {
      await api(`/api/ontology/relation-candidates/${encodeURIComponent(candidateId)}/publish`, {method:"POST", body:JSON.stringify({publishedBy:"operator.demo",reason:"人工核对来源引文、目标实体和谓词后发布"})});
      toast("关系已发布到本体关系层。","success");
    } else {
      await api(`/api/ontology/relation-candidates/${encodeURIComponent(candidateId)}/review`, {method:"POST", body:JSON.stringify({action,reviewer:"operator.demo",reason:action === "accept" ? "人工确认目标实体、谓词和来源引文" : "需要补充关联实体证据"})});
      toast(action === "accept" ? "关系已采纳，仍需显式发布。" : "关系已标记补证。","success");
    }
    state.objectAgent=await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent`); renderHeader(); renderContent();
  } catch(error) { toast(error.message,"error"); }
  finally { button.disabled=false; button.textContent=original; }
}

async function runObjectAgentAction(action, button) {
  if (!state.selected) return;
  button.disabled = true;
  const original = button.textContent;
  try {
    button.textContent = action === "plan" ? "生成中" : "检索中";
    const result = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent/actions`, {method:"POST", body: JSON.stringify({action})});
    if (result.jobs) state.jobs = result.jobs;
    if (result.job) {
      const index = state.jobs.findIndex((job) => job.verificationId === result.job.verificationId);
      if (index >= 0) state.jobs[index] = result.job; else state.jobs.push(result.job);
    }

    state.objectAgent = await api(`/api/ontology/entities/${encodeURIComponent(state.selected)}/agent`);
    state.tab = "audit";
    syncTabNavigation();
    renderHeader(); renderAudit();
    toast(result.answer || `${targetLabel(action)}动作已完成。`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function applyAnalysisRun(run) {
  if (!run) return;
  state.analysisRun = run;
  state.analysisRunStatus = run.status || "ready";
  state.analysisConversationRunId = run.analysisRunId || state.analysisConversationRunId;
  if (run.entityId && !state.selected) { state.selected = run.entityId; if (state.mode === "trend") renderEntities(); }
  renderAnalysisWorkspace(state.trendSummary || {});
  syncAnalysisConversation(run);
}

async function loadAnalysisRun(runId) {
  if (!runId) return null;
  try {
    const data = await api(`/api/ontology/analysis-runs/${encodeURIComponent(runId)}`);
    applyAnalysisRun(data.analysisRun);
    return data.analysisRun;
  } catch (error) {
    return null;
  }
}

async function loadLatestAnalysisRun(entityId = null) {
  try {
    const query = entityId ? `?entityId=${encodeURIComponent(entityId)}&limit=1` : "?limit=1";
    const data = await api(`/api/ontology/analysis-runs${query}`);
    const run = data.analysisRuns?.[0];
    if (run) await loadAnalysisRun(run.analysisRunId);
  } catch (error) {
    /* Optional feature: do not block the ontology tree if the run API is down. */
  }
}

function pollAnalysisRun(runId) {
  clearTimeout(state.analysisRunPoll);
  if (!runId) return;
  state.analysisRunPoll = setTimeout(async () => {
    const run = await loadAnalysisRun(runId);
    if (run && ["queued", "planning", "running"].includes(run.status)) pollAnalysisRun(runId);
  }, 1600);
}

async function startAnalysisRun(question = "") {
  const button = $("#analysis-run");
  if (button) button.disabled = true;
  try {
    const entityId = state.selected || state.entities.find((item) => item.entityType === "EquipmentModel")?.entityId || state.entities[0]?.entityId;
    if (!entityId) throw new Error("当前没有可分析的装备实体");
    const request = String(question || "补全当前实体的属性、关系、信号观测和作战能力，生成带证据的审核包").trim();
    const existingUserMessages = [...($("#trend-thread")?.querySelectorAll(".trend-message.user p") || [])].map((node) => node.textContent?.trim());
    if (!existingUserMessages.includes(request)) appendTrendMessage("user", request, "", {persist: false});
    await ensureBackendConversationSession(request.slice(0, 80));
    state.agentResponse = null;
    state.mainView = "run";
    const sessionId = state.conversationSession?.conversationSessionId || state.conversationSession?.sessionId || (String(state.analysisSessionId || "").startsWith("conversation-") ? state.analysisSessionId : null);
    const createPayload = {entityId, question: request, prompt: request, trigger: "conversation", autoPublish: false, intent: {type: "ontology-audit", trigger: "conversation"}};
    if (sessionId) { createPayload.conversationSessionId = sessionId; createPayload.sessionId = sessionId; }
    // ConversationStore's prompt boundary atomically records the user prompt
    // and creates the AnalysisRun. Fall back to the run endpoint for older
    // service builds that predate the conversation route.
    const created = sessionId
      ? await api(`/api/ontology/conversation-sessions/${encodeURIComponent(sessionId)}/prompt`, {method: "POST", body: JSON.stringify(createPayload)}).catch(() => api("/api/ontology/analysis-runs", {method: "POST", body: JSON.stringify(createPayload)}))
      : await api("/api/ontology/analysis-runs", {method: "POST", body: JSON.stringify(createPayload)});
    let run = created.analysisRun;
    applyAnalysisRun(run);
    if (sessionId) {
      // The orchestrator binds during create; this idempotent call repairs
      // older service builds and keeps the prompt/intent trace explicit.
      await api(`/api/ontology/conversation-sessions/${encodeURIComponent(sessionId)}/bind-run`, {method: "POST", body: JSON.stringify({analysisRunId: run.analysisRunId, prompt: request, intent: {type: "ontology-audit", trigger: "conversation"}})}).catch(() => null);
    }
    const started = await api(`/api/ontology/analysis-runs/${encodeURIComponent(run.analysisRunId)}/start`, {method: "POST", body: "{}"});
    applyAnalysisRun(started.analysisRun || started);
    const dispatched = await api(`/api/ontology/analysis-runs/${encodeURIComponent(run.analysisRunId)}/execute`, {method: "POST", body: "{}"});
    applyAnalysisRun(dispatched.analysisRun || dispatched);
    renderAnalysisWorkspace(state.trendSummary || {});
    pollAnalysisRun(run.analysisRunId);
  } catch (error) {
    toast(`分析运行启动失败：${error.message}`, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function toggleAnalysisRun() {
  const run = state.analysisRun;
  if (!run) return startAnalysisRun();
  const action = state.analysisRunStatus === "paused" ? "resume" : "pause";
  const button = $("#analysis-pause"); if (button) button.disabled = true;
  try {
    const data = await api(`/api/ontology/analysis-runs/${encodeURIComponent(run.analysisRunId)}/${action}`, {method: "POST", body: JSON.stringify({reason: "workbench operator"})});
    applyAnalysisRun(data.analysisRun || data);
    if (action === "resume") pollAnalysisRun(run.analysisRunId);
  } catch (error) {
    toast(`运行${action === "pause" ? "暂停" : "恢复"}失败：${error.message}`, "error");
  } finally { if (button) button.disabled = false; }
}

async function init() {
  // Paint the conversation shell before any network request so a slow or
  // large auxiliary response never leaves the user with an empty white pane.
  renderTrendWorkspace();
  // ConversationStore is authoritative when available; the local transcript
  // remains a transparent fallback for older service builds or offline work.
  loadConversationSessionsFromApi();
  try {
    // Render the usable shell as soon as the entity tree is available. The
    // verification endpoint can be large, so it must not block the Agent
    // conversation from appearing in the first paint.
    const entitiesData = await api("/api/ontology/entities?limit=500");
    state.entities = entitiesData.entities || [];
    $("#entity-total").textContent = state.entities.length;
    $("#pending-total").textContent = state.entities.filter((item) => item.reviewStatus === "pending").length;
    $("#verification-total").textContent = "…";
    state.collapsedCategories = new Set();
    $("#api-state").className = "api-state ok";
    $("#api-state").innerHTML = "<i></i>API 已连接";
    renderEntities();
    renderTrendWorkspace();
    api("/api/ontology/verifications").then((jobsData) => {
      $("#verification-total").textContent = (jobsData.verifications || []).length;
    }).catch(() => {
      $("#verification-total").textContent = "--";
    });
    await loadTrendSummary();
    // Wait for the durable conversation restore before selecting a fallback
    // latest run; otherwise an unrelated run could overwrite the session the
    // operator just opened.
    await loadConversationSessionsFromApi();
    if (!state.analysisRun) await loadLatestAnalysisRun();
    const requestedEntity=new URLSearchParams(location.search).get('entityId');if(requestedEntity)await selectEntity(requestedEntity);
  } catch(error) { $("#api-state").className = "api-state error"; $("#api-state").innerHTML = "<i></i>API 不可用"; $("#entity-list").innerHTML = `<p class="empty">${esc(error.message)}</p>`; }
  if ($("#api-state").classList.contains("error")) {
    const trendPanel = $("#trend-analysis");
    if (trendPanel) trendPanel.innerHTML = `<div class="trend-analysis-head"><div><h3>实时分析结果</h3></div><span id="trend-analysis-status" class="analysis-status is-error">API 不可用</span></div><div class="live-analysis-pending">无法读取本体候选 API：${esc($("#entity-list").textContent)}。请确认本地服务端口和数据库路径。</div>`;
    const connection = document.querySelector(".trend-connection");
    if (connection) connection.innerHTML = "<i></i>本体数据未连接";
  }
}

$("#entity-search").addEventListener("input",renderEntities); $("#category-filter").addEventListener("change",renderEntities);
$("#row-search").addEventListener("input",(event)=>{state.rowQuery=event.target.value.trim();renderContent();});
document.querySelectorAll("[data-tab]").forEach((button)=>button.addEventListener("click",()=>{state.tab=button.dataset.tab;renderContent();}));
$("#utility-tab")?.addEventListener("change", (event) => { if (!event.target.value) return; state.tab = event.target.value; renderContent(); });
$("#create-plan").addEventListener("click",createPlan); $("#refresh-object").addEventListener("click",()=>selectEntity(state.selected)); $("#show-trend").addEventListener("click",showTrendWorkspace);
$("#trend-form").addEventListener("submit",(event)=>{event.preventDefault(); const input=$("#trend-input"); submitTrendQuery(input.value); input.value="";});
document.querySelectorAll("[data-trend-prompt]").forEach((button)=>button.addEventListener("click",()=>submitTrendQuery(button.dataset.trendPrompt)));
document.querySelectorAll("[data-test-prompt]").forEach((button)=>button.addEventListener("click",()=>{
  const input = $("#trend-input");
  if (!input) return;
  input.value = button.dataset.testPrompt || "";
  input.focus();
  toast("Prompt 已填入，请点击“分析”提交。");
}));
document.querySelectorAll("[data-main-view]").forEach((button)=>button.addEventListener("click",()=>{
  state.mainView = button.dataset.mainView || "run";
  if (state.mainView === "review" && !state.governance.assessments.length) loadGovernanceState();
  renderAnalysisWorkspace(state.trendSummary || {});
}));
$("#analysis-run")?.addEventListener("click", startAnalysisRun);
$("#analysis-pause")?.addEventListener("click", toggleAnalysisRun);
$("#new-analysis-session")?.addEventListener("click", startNewAnalysisSession);
$("#clear-analysis-history")?.addEventListener("click", () => clearConversationDisplay());
const agentInfoTip = $("#agent-info-tip");
const agentInfoPopover = $("#agent-info-popover");
const closeAgentInfoTip = () => {
  if (!agentInfoTip || !agentInfoPopover) return;
  agentInfoPopover.hidden = true;
  agentInfoTip.setAttribute("aria-expanded", "false");
};
agentInfoTip?.addEventListener("click", (event) => {
  event.stopPropagation();
  const open = agentInfoPopover?.hidden !== false;
  if (agentInfoPopover) agentInfoPopover.hidden = !open;
  agentInfoTip.setAttribute("aria-expanded", String(open));
});
agentInfoPopover?.addEventListener("click", (event) => event.stopPropagation());
document.addEventListener("click", (event) => {
  if (!event.target.closest(".agent-info")) closeAgentInfoTip();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAgentInfoTip();
});
$("#governance-assessment")?.addEventListener("change", (event) => { state.governance.selectedId = event.target.value; loadGovernanceState(state.governance.selectedId); });
$("#governance-refresh")?.addEventListener("click", () => loadGovernanceState(state.governance.selectedId));
$("#governance-audit")?.addEventListener("click", runGovernanceAudit);
$("#governance-propose")?.addEventListener("click", proposeGovernanceAdmissions);
init();

