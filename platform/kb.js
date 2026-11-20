import {
  drawUAV,
  drawUnk,
  drawShip,
  drawSam,
  drawBmd,
  drawC2,
  drawRadar,
  drawSearchRadar,
  drawJammer,
} from "./icons.js?v=22";
import { CONCEPT_GROUPS } from "./kb-navigation.js?v=6";
import { createKnowledgeModel } from "./kb-model.js";
import { knowledgeTabs, renderKnowledgePane } from "./kb-profiles.js";

const GOLD = "#e2c36a";

const pkgLab = document.getElementById("pkg-lab");
const qEl = document.getElementById("q");
const countEl = document.getElementById("count");
const groupsEl = document.getElementById("groups");
const navViewsEl = document.getElementById("nav-views");
const inspectEl = document.getElementById("inspect");

const GROUPS = [
  { id: "onto", n: "01", lab: "领域本体", sub: "Ontology", kind: "tree", ico: "onto" },
  { id: "Scene", n: "02", lab: "战场", sub: "Battlespace", kind: "env", ico: "scene" },
  { id: "Rule", n: "03", lab: "规则", sub: "Rules", kind: "env", ico: "rule" },
  { id: "Algorithm", n: "04", lab: "算法", sub: "Algorithms", kind: "env", ico: "algo" },
  { id: "Engagement", n: "05", lab: "交战", sub: "Engagement", kind: "env", ico: "fight" },
  { id: "Agent", n: "06", lab: "Agent", sub: "Agent", kind: "env", ico: "agent" },
  { id: "Knowledge", n: "07", lab: "知识", sub: "Knowledge", kind: "env", ico: "know" },
];
const NAV_ICO = {
  onto: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 2.5v4M4 13.5V9h8v4.5M8 6.5H4.5V9M8 6.5h3.5V9"/></svg>',
  scene: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 11.5 6 7l2.5 3 2-2.5 3 4"/><circle cx="11" cy="4.5" r="1.2"/></svg>',
  rule: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3.5" y="2.5" width="9" height="11"/><path d="M6 6h4M6 8.5h4M6 11h2.5"/></svg>',
  algo: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="5.2"/><rect x="6" y="6" width="4" height="4"/></svg>',
  fight: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 13 8 3l5 10M5.2 8.5h5.6"/></svg>',
  agent: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="5" r="2"/><path d="M3.5 13c.6-2.4 2.2-3.5 4.5-3.5s3.9 1.1 4.5 3.5"/></svg>',
  know: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3.5 4.5h4.2c.8 0 1.3.4 1.3 1.2V13M12.5 4.5H8.3C7.5 4.5 7 4.9 7 5.7V13"/><path d="M3.5 13h4.2M8.3 13h4.2"/></svg>',
};
const ENV_TYPES = {
  Scene: ["Shoreline", "Region", "Membership", "Envelope", "Corridor", "SearchSector", "DetectionDisk", "JamBloom", "CommandPost", "Sensor", "Shooter", "Platform", "Emitter", "PlatformType", "Needline", "Link", "Exclude"],
  Rule: ["Rule"],
  Algorithm: ["Algorithm"],
  Engagement: ["FunctionBind", "Wave", "Channel", "Permit", "Track", "Identity", "Pair", "Slot", "Inject", "Event", "Invariant"],
  Agent: ["AgentInstance", "ShellTemplate", "Monitor"],
  Knowledge: ["CloudDef", "EquipmentModel", "CapabilityObservation", "Organization", "Technology", "Program", "TestEvent", "ProcurementEvent", "ProductionEvent"],
};

// These sections are a navigation projection only. They express operational
// responsibility boundaries (World/Self/Adversary/Options/Gate/Effectors/Learn);
// the TBox rdfs:subClassOf hierarchy remains the source of truth. A function
// is composable and does not grant authority by itself.
const AGENT_NAV_SECTIONS = [
  {
    id: "world",
    n: "01",
    label: "World｜世界模型与输入",
    sub: "WORLD",
    hint: "观测、信念和消息证据；不直接产生决心",
    classes: ["Observation", "BeliefState", "BeliefAssertion", "Message", "CommunicationAct", "Protocol"],
  },
  {
    id: "self",
    n: "02",
    label: "Self｜己方状态与身份",
    sub: "SELF",
    hint: "主体、组织、平台、链路、能力、资源和当前状态",
    classes: ["Agent", "AgentInstance", "AgentFunction", "OperationalTimeScale", "AgentClass", "AgentType", "AgentRole", "EmbodiedAgent", "SoftwareAgent", "OrganizationModel", "Organization", "RoleAssignment", "PlatformBinding", "LinkState", "Capability", "Resource", "AgentState"],
  },
  {
    id: "adversary",
    n: "03",
    label: "Adversary｜对手意图与适应",
    sub: "ADVERSARY",
    hint: "对手模型待建模；当前不把通用 Goal、Desire、Intention 误标为对手事实",
    classes: [],
  },
  {
    id: "options",
    n: "04",
    label: "Options｜方案与推演",
    sub: "OPTIONS",
    hint: "任务、计划、决策和过程规范；不持有发射权",
    classes: ["Goal", "Desire", "Intention", "Task", "Plan", "Decision", "DecisionDraft", "ProcessSpecification"],
  },
  {
    id: "gate",
    n: "05",
    label: "Gate｜约束、授权与人在环",
    sub: "GATE",
    hint: "策略、权限范围和授权裁决；仅此层可否决方案",
    classes: ["Policy", "Authority", "Authorization", "ApprovalRequest", "HumanDecision", "Constraint", "AssistantSession", "Operator"],
  },
  {
    id: "effectors",
    n: "06",
    label: "Effectors｜效果执行与中止",
    sub: "EFFECTORS",
    hint: "动作和效果；必须服从短寿命授权与中止条件",
    classes: ["ActionRequest", "Action", "ActionExecution", "AgentExecution", "Effect", "StateTransition"],
  },
  {
    id: "learn",
    n: "07",
    label: "Learn｜运行轨迹与战后复盘",
    sub: "LEARN",
    hint: "只收集轨迹和错误；不能直接改 Gate 红线",
    classes: ["Run", "AuditEvent", "AgentCycle", "TimeInterval", "TriggerDefinition"],
  },
]; 
const AGENT_NAV_SECTION_BY_CLASS = new Map(
  AGENT_NAV_SECTIONS.flatMap((section) => section.classes.map((id) => [id, section.id])),
);
const AXIOM_ZH = {
  "entity-has-quality": "凡实体皆有状态",
  "algorithm-no-claim": "算法不得作为权利要求",
  "shell-no-permit": "壳体不得写入许可",
  "monitor-writes-permit": "只有监视者可写许可",
  "unk-not-listable": "不明不得进交战清单",
  "inner-loop": "内环只允许球面码，MAPPO 不是内环",
};

// The ten competency questions from agent-modeling-research.md are surfaced
// here as a reviewable implementation ledger.  Status describes what is
// currently verifiable in this checked-in package; it does not claim that a
// future runtime/export path is complete.
const COMPETENCY_QUESTIONS = [
  {
    no: 1,
    question: "当前运行中有哪些 AgentInstance？每个 Agent 的 stable IRI、平台、组织、角色和生命周期是什么？",
    status: "已落实",
    tone: "done",
    evidence: "agent.instances.json；运行时 RuntimeLedger.registerAgent()",
    links: [["AgentInstance", "agent.monitor.gate"], ["AgentInstance", "agent.options.planner"], ["AgentInstance", "agent.fusion.c2"]],
  },
  {
    no: 2,
    question: "某个 Agent 观察到了哪些 Observation？来源传感器、算法和有效时间是什么？",
    status: "已落实",
    tone: "done",
    evidence: "agent.extension.json Observation；RuntimeLedger.observe() 记录 assertedBy、sourceIri、observedAt",
    links: [["Class", "Observation"], ["Class", "AgentInstance"]],
  },
  {
    no: 3,
    question: "某个 Identity 是事实、信念还是推理结果？来源、置信度和融合算法是什么？",
    status: "部分落实",
    tone: "partial",
    evidence: "BeliefAssertion + CloudKB.reviseBelief()；置信度、derivedFrom、derivedBy 可追溯，但 Identity 的事实/信念/推理分类仍需显式化",
    links: [["Class", "BeliefAssertion"], ["Class", "Identity"]],
  },
  {
    no: 4,
    question: "某个 Task 是谁分配的？由哪个 Goal、Plan 和 Policy 支持？",
    status: "待补齐",
    tone: "pending",
    evidence: "Goal / Task / Plan / Policy 已有本体定义；运行时决策链已记录，但独立任务分配实例和支撑链仍需补齐",
    links: [["Class", "Task"], ["Class", "Goal"], ["Class", "Plan"]],
  },
  {
    no: 5,
    question: "某个 ActionRequest 是谁提出的？是否需要 Permit？最终由谁授权？",
    status: "部分落实",
    tone: "partial",
    evidence: "RuntimeLedger.request() → authorize() → execute() 已分离请求、Gate 授权和 Effectors 执行；执行会检查 Gate 功能、allow + 未过期授权、短寿命 TTL、窄执行面与安全回退，持久化授权服务仍待接入",
    links: [["Class", "ActionRequest"], ["Class", "Authorization"]],
  },
  {
    no: 6,
    question: "某个 Permit 的每个门控条件是否都有可验证证据？",
    status: "部分落实",
    tone: "partial",
    evidence: "CloudKB.authorizeGate() 已记录几何、通道、通信和置信度证据；SHACL 已约束 Gate-only、有效期、撤销和中止字段，但证据时效、人在环确认和失联收紧尚未形成完整运行时校验链",
    links: [["Class", "Authorization"], ["Class", "Permit"], ["Monitor", "monitor.gate"]],
  },
  {
    no: 7,
    question: "通信中断后，哪些 Agent 仍可使用共享信念，哪些只能使用局部信念？",
    status: "部分落实",
    tone: "partial",
    evidence: "CloudKB team/local cloud-shadow；通信拒止会切换局部/融合观测，但每个 Agent 的 LinkState 和共享信念可见性尚未独立投影",
    links: [["Class", "LinkState"], ["Class", "BeliefState"]],
  },
  {
    no: 8,
    question: "一个 Agent 减员或失联后，哪些 Task、Pair、Slot 和 Resource 需要重新分配？",
    status: "部分落实",
    tone: "partial",
    evidence: "battle.js 有限波次、槽位和减员重收敛；Task/Pair/Resource 的统一重分配审计仍需补齐",
    links: [["Class", "Resource"], ["Class", "Task"], ["Class", "Slot"]],
  },
  {
    no: 9,
    question: "browser-3d 和 sim-2d 中同一目标的产品是否属于不同 run_id/model_id？",
    status: "已落实",
    tone: "done",
    evidence: "RuntimeEnvelope；RuntimeLedger 为事件和 IRI 加入 runId/modelId 隔离",
    links: [["Class", "Run"], ["Class", "AuditEvent"]],
  },
  {
    no: 10,
    question: "一次运行结束后，是否能从 AuditEvent 重放观察、信念、任务、授权和动作链？",
    status: "部分落实",
    tone: "partial",
    evidence: "RuntimeLedger 已记录 Observation→Belief→Decision→ActionRequest→Authorization→Execution→AuditEvent；持久化导出/重放 UI 待补",
    links: [["Class", "AuditEvent"], ["Class", "ActionExecution"]],
  },
];

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const files = {};
let lexicon = { types: {}, ids: {}, facets: {}, depict: {} };
let tbox = { classes: [], objectProperties: [], dataProperties: [], axioms: [], disjoint: [] };
let dictionary = { codes: [], tables: [] };
let schemaDoc = { $defs: {} };
let entries = [];
let knowledgeModel = null;
let abox = [];
let selectedClass = "Sensor";
let selectedClassIri = null;
let picked = null;
let pickedIri = null;
let query = "";
let open = new Set(["Entity", "Continuant", "IndependentContinuant", "MaterialObject", "Sensor"]);
let groupOpen = "onto";
let bomOpen = new Set(["SEA", "LAND", "TYPE_SCENE", "TYPE_CATALOG"]);
let tab = "ann";
let navRail = localStorage.getItem("kb-nav-rail") === "1";
let navView = localStorage.getItem("kb-nav-view") || "topic";
let storeInfo = { kind: "postgres" };
let reviewOpen = false;
let agentSectionOpen = new Set(["world"]);
let topicGroupOpen = new Set(["agents"]);
let topicSectionOpen = new Set(["identity"]);

async function loadBoot() {
  const res = await fetch("/api/kb/boot");
  const boot = await res.json().catch(() => ({}));
  if (!res.ok || boot.ok === false) {
    throw new Error(boot.error || `boot ${res.status}`);
  }
  return boot;
}

function metaOf(e) {
  const typeKey = e.type === "Class" ? e.id : e.type;
  const t = lexicon.types[typeKey] || lexicon.types[e.type] || {};
  const id = (lexicon.ids || {})[e.id] || {};
  return {
    depict: id.depict || t.depict || "mark",
    glyph: id.glyph || t.glyph || "pack",
    photo: id.photo || t.photo || null,
    has: t.has || [],
    what: t.what || e.label || e.type,
    usedIn: t.usedIn || [],
  };
}

function recordOf(e) {
  return knowledgeModel?.recordOf(e) || {};
}

function drawMark(ctx, glyph, color) {
  const cx = ctx.canvas.width / 2;
  const cy = ctx.canvas.height / 2;
  ctx.translate(cx, cy);
  ctx.strokeStyle = color || GOLD;
  ctx.fillStyle = color || GOLD;
  ctx.lineWidth = 1.4;
  if (glyph === "hemi") {
    ctx.beginPath();
    ctx.arc(0, 6, 16, Math.PI, 0);
    ctx.stroke();
  } else if (glyph === "box") {
    ctx.strokeRect(-16, -8, 32, 16);
  } else if (glyph === "disk") {
    ctx.beginPath();
    ctx.arc(0, 0, 14, 0, Math.PI * 2);
    ctx.stroke();
  } else if (glyph === "sector") {
    ctx.beginPath();
    ctx.moveTo(0, 8);
    ctx.arc(0, 8, 22, -Math.PI * 0.72, -Math.PI * 0.28);
    ctx.closePath();
    ctx.stroke();
  } else if (glyph === "bloom") {
    for (let i = 0; i < 6; i += 1) {
      const a = (i * Math.PI) / 3;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(Math.cos(a) * 16, Math.sin(a) * 16);
      ctx.stroke();
    }
  } else if (glyph === "shore") {
    ctx.beginPath();
    ctx.moveTo(-20, 4);
    for (let x = -20; x <= 20; x += 2) ctx.lineTo(x, Math.sin(x * 0.35) * 5);
    ctx.stroke();
  } else if (glyph === "sea" || glyph === "land") {
    ctx.globalAlpha = glyph === "sea" ? 0.35 : 0.7;
    ctx.fillRect(-18, -12, 36, 24);
    ctx.globalAlpha = 1;
    ctx.strokeRect(-18, -12, 36, 24);
  } else if (glyph === "subset") {
    ctx.beginPath();
    ctx.arc(-4, 0, 10, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(6, 0, 14, 0, Math.PI * 2);
    ctx.stroke();
  } else if (glyph === "cloud") {
    ctx.beginPath();
    ctx.arc(-8, 2, 6, 0, Math.PI * 2);
    ctx.arc(0, -2, 8, 0, Math.PI * 2);
    ctx.arc(8, 2, 6, 0, Math.PI * 2);
    ctx.stroke();
  } else if (glyph === "gate") {
    ctx.strokeRect(-10, -12, 20, 24);
    ctx.beginPath();
    ctx.moveTo(-4, 4);
    ctx.lineTo(0, 8);
    ctx.lineTo(8, -6);
    ctx.stroke();
  } else if (glyph === "ban") {
    ctx.beginPath();
    ctx.arc(0, 0, 14, 0, Math.PI * 2);
    ctx.moveTo(-10, -10);
    ctx.lineTo(10, 10);
    ctx.stroke();
  } else if (glyph === "link") {
    ctx.beginPath();
    ctx.moveTo(-16, 8);
    ctx.lineTo(-4, -8);
    ctx.lineTo(4, -8);
    ctx.lineTo(16, 8);
    ctx.stroke();
  } else if (glyph === "shell" || glyph === "fn") {
    ctx.strokeRect(-14, -12, 28, 24);
    ctx.beginPath();
    ctx.moveTo(-8, -4);
    ctx.lineTo(8, -4);
    ctx.moveTo(-8, 4);
    ctx.lineTo(8, 4);
    ctx.stroke();
  } else if (glyph === "class") {
    ctx.beginPath();
    ctx.moveTo(0, -16);
    ctx.lineTo(-16, 12);
    ctx.lineTo(16, 12);
    ctx.closePath();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, -6);
    ctx.lineTo(0, 12);
    ctx.stroke();
  } else if (glyph === "rule") {
    ctx.strokeRect(-14, -16, 28, 32);
    ctx.beginPath();
    ctx.moveTo(-8, -8);
    ctx.lineTo(8, -8);
    ctx.moveTo(-8, 0);
    ctx.lineTo(8, 0);
    ctx.moveTo(-8, 8);
    ctx.lineTo(4, 8);
    ctx.stroke();
  } else if (glyph === "algo") {
    ctx.beginPath();
    ctx.arc(0, 0, 12, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeRect(-5, -5, 10, 10);
  } else {
    ctx.strokeRect(-12, -12, 24, 24);
  }
}

function paintGlyph(canvas, e, lite) {
  const m = metaOf(e);
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = lite ? "#eef2f1" : "#11161c";
  ctx.fillRect(0, 0, w, h);
  const g = m.glyph;
  const cx = w / 2;
  const cy = h / 2;
  if (g === "uav-blue") drawUAV(ctx, cx, cy, false, 0);
  else if (g === "uav-red") drawUAV(ctx, cx, cy, true, 0);
  else if (g === "uav-unk") drawUnk(ctx, cx, cy, 0);
  else if (g === "uav") drawUAV(ctx, cx, cy, false, 0);
  else if (g === "ship") drawShip(ctx, cx, cy, 180);
  else if (g === "sam") drawSam(ctx, cx, cy);
  else if (g === "bmd") drawBmd(ctx, cx, cy);
  else if (g === "c2") drawC2(ctx, cx, cy);
  else if (g === "radar") drawRadar(ctx, cx, cy);
  else if (g === "search") drawSearchRadar(ctx, cx, cy);
  else if (g === "jammer") drawJammer(ctx, cx, cy, true);
  else {
    ctx.save();
    drawMark(ctx, g, lite ? "#2e5efe" : GOLD);
    ctx.restore();
  }
}

function classById(id) {
  const hits = (tbox.classes || []).filter(c => c.id === id || c.iri === id);
  return hits.length === 1 ? hits[0] : null;
}

function agentExtensionIds() {
  return new Set(((files["agent.extension.json"] || {}).classes || []).map((c) => c.id));
}

function isAgentExtensionClass(id) {
  return agentExtensionIds().has(id);
}

function normalizeClassRef(value) {
  const text = String(value || "");
  const hash = text.lastIndexOf("#");
  if (hash >= 0) return text.slice(hash + 1);
  const slash = text.lastIndexOf("/");
  return slash >= 0 ? text.slice(slash + 1) : text;
}

function mergeAgentExtension() {
  const ext = files["agent.extension.json"] || {};
  const extensionClasses = (ext.classes || []).map((c) => ({
    ...c,
    subClassOf: (c.subClassOf || []).map(normalizeClassRef),
  }));
  const baseNs = "https://local.ontology/coast-raid-k#";
  const baseClasses = (tbox.classes || []).map(c => ({...c, iri: c.iri || baseNs + c.id}));
  const known = new Set(baseClasses.map(c => c.iri));
  tbox.classes = [...baseClasses, ...extensionClasses.filter(c => !known.has(c.iri))];
  // Keep extension properties visible in the same TBox views as the base
  // package.  The extension uses full IRIs while the browser indexes classes
  // by local IDs, so normalize domain/range only for this presentation layer.
  const normalizeProperty = (p) => ({
    ...p,
    domain: normalizeClassRef(p.domain),
    range: p.range && !String(p.range).startsWith("xsd:") ? normalizeClassRef(p.range) : p.range,
  });
  const mergeTerms = (key) => {
    const base = (tbox[key] || []).map(p => ({...p, iri: p.iri || baseNs + p.id}));
    const seen = new Set(base.map(p => p.iri));
    const added = (ext[key] || []).filter(p => !seen.has(p.iri)).map(normalizeProperty);
    tbox[key] = [...base, ...added];
  };
  mergeTerms("objectProperties");
  mergeTerms("dataProperties");
  tbox.axioms = [...(tbox.axioms || []), ...(ext.axioms || [])];
  const entryIris = new Set(entries.filter((e) => e.type === "Class").map((e) => e.iri || (e.file === "agent.extension.json" ? "https://local.ontology/coast-raid-k/agent/1.0.0#" : baseNs) + e.id));
  entries = [
    ...entries,
    ...extensionClasses
      .filter((c) => !entryIris.has(c.iri))
      .map((c) => ({
        id: c.id,
        type: "Class",
        module: "Agent",
        file: "agent.extension.json",
        iri: c.iri,
        label: c.label,
      })),
  ];
}

function labelOf(id) {
  return (classById(id) && classById(id).label) || id;
}

function chainOf(name) {
  const out = [];
  const guard = new Set();
  let cur = name;
  while (cur && !guard.has(cur)) {
    guard.add(cur);
    out.push(cur);
    const c = classById(cur);
    cur = (c && c.subClassOf && c.subClassOf[0]) || "";
  }
  return out;
}

function childrenOf(id) {
  return (tbox.classes || []).filter(c => (c.subClassOf || []).includes(id));
}

function ancestorIds(id, seen = new Set()) {
  if (seen.has(id)) return seen;
  seen.add(id);
  for (const parent of classById(id)?.subClassOf || []) ancestorIds(parent, seen);
  return seen;
}

function descendantIds(id) {
  const out = new Set([id]);
  const walk = (cur) => {
    childrenOf(cur).forEach((c) => {
      if (!out.has(c.id)) {
        out.add(c.id);
        walk(c.id);
      }
    });
  };
  walk(id);
  return out;
}

function keyOf(e) {
  return `${e.type}:${e.id}`;
}

function individualNavAttributes(e) {
  return `data-id="${escapeHtml(keyOf(e))}" data-iri="${escapeHtml(e.iri || "")}"`;
}

function classNavAttributes(c) {
  return `data-class="${escapeHtml(c.id)}" data-iri="${escapeHtml(c.iri || classEntry(c.id)?.iri || "")}"`;
}

function ambiguousClassNavHtml() {
  const classes = entries.filter(e => e.type === "Class");
  const counts = new Map();
  classes.forEach(e => counts.set(e.id, (counts.get(e.id) || 0) + 1));
  const ambiguous = classes.filter(e => counts.get(e.id) > 1 && matchesQuery(e));
  if (!ambiguous.length) return "";
  return `<section class="kb-agent-runtime"><div class="kb-nav-label">同名类 · 按完整 IRI 区分</div>`
    + ambiguous.map(e => `<button type="button" class="kb-item" ${classNavAttributes(e)}><span>${escapeHtml(e.label || e.id)}</span><span class="en">${escapeHtml(e.iri)}</span></button>`).join("")
    + `</section>`;
}

function classEntry(id) {
  return findEntity("Class", id);
}

function findEntity(type, id) {
  let hits = entries.filter(e => e.type === type && (e.id === id || e.iri === id));
  if (!hits.length && /^[a-z][a-z0-9+.-]*:/i.test(id)) hits = resolveIriEntries(id).filter(e => e.type === type);
  return hits.length === 1 ? hits[0] : null;
}

function resolveIriEntries(iri) {
  const canonical = entries.filter(e => e.iri === iri);
  if (canonical.length) return canonical;
  const aliases = new Set(((files["identity-registry.json"] || {}).entries || [])
    .filter(e => (e.aliases || []).includes(iri)).map(e => e.iri));
  return entries.filter(e => aliases.has(e.iri));
}

function currentEntry() {
  if (picked) {
    const byIri = pickedIri ? entries.filter(e => e.iri === pickedIri) : [];
    return byIri.length === 1 ? byIri[0] : findEntity(picked.split(":")[0], picked.slice(picked.indexOf(":") + 1));
  }
  return classEntry(selectedClassIri || selectedClass);
}

function underClass(e, cls) {
  if (!cls) return true;
  return descendantIds(cls).has(e.type);
}

function inEnv(e, envId) {
  if (e.file === "intelligence-ontology.json") return envId === "Knowledge";
  const types = ENV_TYPES[envId] || [];
  return types.includes(e.type);
}

function matchesQuery(e) {
  if (!query) return true;
  const rec = recordOf(e);
  const m = metaOf(e);
  const syn = (e.type === "Class" ? rec.synonyms : (classById(e.type) || {}).synonyms) || [];
  const blob = [e.id, e.type, e.label, m.what, rec.text, rec.title, rec.modality, rec.bind, rec.comment, ...(syn)].filter(Boolean).join(" ").toLowerCase();
  return blob.includes(query);
}

function listed() {
  return abox.filter((e) => underClass(e, selectedClass)).filter(matchesQuery);
}

function envMembers(envId) {
  const rows = abox.filter((e) => inEnv(e, envId));
  if (!query) return rows;
  return rows.filter(matchesQuery);
}

function groupForType(type) {
  for (const g of GROUPS) {
    if (g.kind === "env" && (ENV_TYPES[g.id] || []).includes(type)) return g.id;
  }
  return "onto";
}

function classMatchesQuery(c) {
  if (!query) return true;
  const blob = [c.id, c.label, c.comment].filter(Boolean).join(" ").toLowerCase();
  return blob.includes(query);
}

function visibleClassIds() {
  if (!query) return null;
  const ids = new Set();
  (tbox.classes || []).forEach((c) => {
    if (classMatchesQuery(c)) ancestorIds(c.id).forEach(id => ids.add(id));
  });
  return ids;
}

function zoneOf(e) {
  const typeId = e.type === "Class" ? e.id : e.type;
  const gid = groupForType(typeId);
  const g = GROUPS.find((x) => x.id === gid);
  const rec = recordOf(e);
  const parent = e.type === "Class" ? ((rec.subClassOf || [])[0] || "") : e.type;
  if (!g || gid === "onto") return parent ? `领域本体 / ${labelOf(parent)}` : "领域本体";
  return `${g.lab} / ${labelOf(typeId)}`;
}

function hashOf(e) {
  return e.iri ? `#iri/${encodeURIComponent(e.iri)}` : `#${e.type}/${encodeURIComponent(e.id)}`;
}

function writeHash(e) {
  const next = hashOf(e);
  if (location.hash !== next) history.replaceState(null, "", next);
}

function statements(e, rec) {
  const rows = [];
  if (e.type === "Class") {
    if (rec.comment) rows.push(["说明", rec.comment]);
    if (rec.label) rows.push(["中文名", rec.label]);
    return rows;
  }
  if (e.type === "Package") rows.push(["封皮", (rec.envelope || []).join(" · ")]);
  if (e.type === "Rule") {
    rows.push(["模态", rec.modality]);
    rows.push(["陈述", rec.text]);
    if (rec.evalOn) rows.push(["何时评估", rec.evalOn]);
    if (rec.params && rec.params.tau != null) rows.push(["τ", String(rec.params.tau)]);
  }
  if (e.type === "Algorithm") {
    rows.push(["名称", rec.title || e.id]);
    rows.push(["权利要求", rec.claim ? "是（违规）" : "否"]);
    rows.push(["范围", rec.scope || "—"]);
  }
  if (e.type === "FunctionBind") {
    rows.push(["产出", rec.give]);
    if (rec.human != null) rows.push(["需人在环", rec.human ? "是" : "否"]);
  }
  if (e.type === "CloudDef") rows.push(["云", `Ex ${rec.Ex} · En ${rec.En} · He ${rec.He}`]);
  if (e.type === "Exclude") rows.push(["禁项", "不得出现在本包"]);
  if (rec.p) rows.push(["位置", rec.p.map((n) => Number(n).toFixed(2)).join(", ")]);
  if (rec.c && rec.r != null) rows.push(["半径", `${rec.r} km`]);
  if (rec.shape === "aabb") rows.push(["盒", `${rec.x0}…${rec.x1} × ${rec.z0}…${rec.z1}`]);
  if (rec.predicate) rows.push(["谓词", rec.predicate]);
  if (rec.stages) rows.push(["段", rec.stages.map((s) => s.name).join(" → ")]);
  if (rec.side) rows.push(["方", rec.side]);
  if (!rows.length && (e.label || rec.label)) rows.push(["标注", e.label || rec.label]);
  return rows;
}

function relations(e, rec) {
  const rows = [];
  const add = (lab, type, id) => {
    if (id) rows.push({ lab, type, id });
  };
  if (e.type === "Algorithm") add("绑定到职能", "FunctionBind", rec.bind);
  if (e.type === "FunctionBind" && rec.give) add("产出产品类", "Class", rec.give);
  if (e.type === "Rule" && rec.evalOn) add("评估时机", null, rec.evalOn);
  if (rec.covers) add("覆盖", null, rec.covers);
  if (rec.envelope) add("拥有包络", null, rec.envelope);
  if (rec.region) add("所在域", "Region", rec.region);
  if (e.type === "ShellTemplate" && rec.bindsPlatformType) add("绑定平台型", "PlatformType", rec.bindsPlatformType);
  if (e.type === "Monitor") add("写许可", "Class", "Permit");
  if (e.type === "Membership") add("隶属对象", null, rec.of);
  return rows;
}

function axiomsFor(e, rec) {
  const name = e.type === "Class" ? e.id : e.type;
  const hits = (tbox.axioms || []).filter((a) => a.id === "entity-has-quality" || (a.manchester || "").includes(name));
  const disj = (tbox.disjoint || []).filter((pair) => pair[0] === name || pair[1] === name);
  const illegal = rec.illegal || ((lexicon.types[e.type] || {}).state || {}).illegal || [];
  return { hits, disj, illegal };
}

function memberCount(cls) {
  const set = descendantIds(cls);
  return abox.filter(e => set.has(e.type)).length;
}

function ensureOpenPath(id) {
  ancestorIds(id).forEach(x => open.add(x));
  open.add(id);
}

function closeBranch(id) {
  descendantIds(id).forEach((x) => {
    if (x !== id) open.delete(x);
  });
}

function toggleOpen(id) {
  if (!childrenOf(id).length) return;
  if (open.has(id)) {
    open.delete(id);
    closeBranch(id);
    return;
  }
  open.add(id);
}

function treeHtml(id, depth, trail = new Set()) {
  if (trail.has(id)) return "";
  trail = new Set([...trail, id]);
  const vis = visibleClassIds();
  if (vis && !vis.has(id)) return "";
  const kids = childrenOf(id).filter((c) => !isAgentExtensionClass(c.id));
  const hasKids = kids.length > 0;
  const isOpen = open.has(id) || Boolean(query);
  const n = memberCount(id);
  const pad = 10 + depth * 14;
  const on = !picked && id === selectedClass;
  const cls = ["kb-node", on ? "on" : "", hasKids ? (isOpen ? "open" : "shut") : "leaf"].filter(Boolean).join(" ");
  let html = `<div class="${cls}" style="padding-left:${pad}px">`
    + `<button type="button" class="tog" data-toggle="${id}" ${hasKids ? `aria-expanded="${isOpen}"` : "disabled"}></button>`
    + `<button type="button" class="cls" ${classNavAttributes(classById(id) || {id})}><span class="zh">${labelOf(id)}</span><span class="en">${id}</span></button>`
    + `<span class="n">${n}</span></div>`;
  if (hasKids && isOpen) kids.forEach(c => { html += treeHtml(c.id, depth + 1, trail); });
  return html;
}

function findPart(id) {
  if (!id) return null;
  return abox.find((e) => e.id === id && e.type !== "Membership") || null;
}

function sceneItem(e, pad) {
  const k = keyOf(e);
  return `<button type="button" class="kb-item ${pickedIri === e.iri ? "on" : ""}" ${individualNavAttributes(e)} style="padding-left:${pad}px"><span>${escapeHtml(e.id)}</span><span class="en">${escapeHtml(e.type)}</span></button>`;
}

function sceneRegionBom(region, kids) {
  const isOpen = bomOpen.has(region.id) || Boolean(query);
  const on = picked === keyOf(region);
  const hasKids = kids.length > 0;
  const cls = ["kb-node", on ? "on" : "", hasKids ? (isOpen ? "open" : "shut") : "leaf"].filter(Boolean).join(" ");
  let html = `<div class="${cls}" style="padding-left:10px">`
    + `<button type="button" class="tog" data-bom="${region.id}" ${hasKids ? `aria-expanded="${isOpen}"` : "disabled"}></button>`
    + `<button type="button" class="cls" ${individualNavAttributes(region)}><span class="zh">${escapeHtml(region.id)}</span><span class="en">Region</span></button>`
    + `<span class="n">${kids.length}</span></div>`;
  if (hasKids && isOpen) {
    kids.forEach((m) => {
      const rec = recordOf(m);
      const part = findPart(rec.of);
      const k = part ? keyOf(part) : keyOf(m);
      const onKid = picked === k || picked === keyOf(m);
      const rule = (rec.rule || "").toUpperCase();
      const typ = part ? part.type : "Membership";
      html += `<button type="button" class="kb-item kb-bom-part ${onKid ? "on" : ""}" ${individualNavAttributes(part || m)}><span>${escapeHtml(rec.of || m.id)}</span><span class="en">${escapeHtml(rule)} · ${escapeHtml(typ)}</span></button>`;
      html += `<button type="button" class="kb-item kb-bom-part ${pickedIri === m.iri ? "on" : ""}" ${individualNavAttributes(m)} style="padding-left:38px"><span>${escapeHtml(m.id)}</span><span class="en">Membership · 关系记录</span></button>`;
    });
  }
  return html;
}

function sceneTypeBom(id, zh, en, kids) {
  const isOpen = bomOpen.has(id) || Boolean(query);
  const hasKids = kids.length > 0;
  const cls = ["kb-node", hasKids ? (isOpen ? "open" : "shut") : "leaf"].filter(Boolean).join(" ");
  let html = `<div class="${cls}" style="padding-left:10px">`
    + `<button type="button" class="tog" data-bom="${id}" ${hasKids ? `aria-expanded="${isOpen}"` : "disabled"}></button>`
    + `<span class="cls" style="cursor:default"><span class="zh">${zh}</span><span class="en">${en}</span></span>`
    + `<span class="n">${kids.length}</span></div>`;
  if (hasKids && isOpen) {
    kids.forEach((e) => {
      const rec = recordOf(e);
      const tag = rec.inScene === true
        ? (rec.side || "本幕")
        : [rec.natoClass, rec.role].filter(Boolean).join(" · ") || "catalog";
      const k = keyOf(e);
      html += `<button type="button" class="kb-item kb-bom-part ${pickedIri === e.iri ? "on" : ""}" ${individualNavAttributes(e)}><span>${escapeHtml(e.id)}</span><span class="en">${escapeHtml(tag)}</span></button>`;
    });
  }
  return html;
}

function sceneNavHtml() {
  const all = abox.filter((e) => inEnv(e, "Scene"));
  const match = (e) => !query || matchesQuery(e);
  const shore = all.filter((e) => e.type === "Shoreline" && match(e));
  const regions = all.filter((e) => e.type === "Region");
  const mems = all.filter((e) => e.type === "Membership");
  const nestedPartIds = new Set(mems.map((m) => recordOf(m).of).filter(Boolean));
  const types = all.filter((e) => e.type === "PlatformType" && match(e));
  const skip = new Set(["Shoreline", "Region", "Membership", "PlatformType"]);
  const rest = all.filter((e) => !skip.has(e.type) && !nestedPartIds.has(e.id) && match(e));
  let html = `<div class="kb-tree kb-bom">`;
  shore.forEach((e) => { html += sceneItem(e, 12); });
  const ordered = ["SEA", "LAND"]
    .map((id) => regions.find((r) => r.id === id))
    .filter(Boolean)
    .concat(regions.filter((r) => r.id !== "SEA" && r.id !== "LAND"));
  ordered.forEach((region) => {
    const kids = mems.filter((m) => recordOf(m).region === region.id).filter((m) => {
      if (!query) return true;
      const part = findPart(recordOf(m).of);
      return matchesQuery(region) || matchesQuery(m) || (part && matchesQuery(part));
    });
    if (query && !matchesQuery(region) && !kids.length) return;
    html += sceneRegionBom(region, kids);
  });
  const scenePts = types.filter((e) => recordOf(e).inScene === true);
  const catPts = types.filter((e) => recordOf(e).inScene !== true);
  if (!query || scenePts.length) html += sceneTypeBom("TYPE_SCENE", "本幕型", "ITEM", scenePts);
  if (!query || catPts.length) html += sceneTypeBom("TYPE_CATALOG", "型库", "OBJECT-TYPE", catPts);
  html += `</div>`;
  html += rest.map((e) => sceneItem(e, 12)).join("");
  return html || `<p class="kb-item" style="cursor:default;color:var(--mute)">无个体</p>`;
}

function agentSectionForClass(id) {
  return AGENT_NAV_SECTION_BY_CLASS.get(id) || "other";
}

function agentClassItem(c) {
  const parent = ((classById(c.id) || {}).subClassOf || [])[0] || "";
  const parentLabel = parent ? labelOf(parent) : "根类";
  const parentText = parent ? `上位：${parentLabel} · ${parent}` : "上位：根类";
  return `<button type="button" class="kb-item kb-agent-class ${!picked && selectedClassIri === c.iri ? "on" : ""}" ${classNavAttributes(c)} title="${escapeHtml(parentText)}"><span>${escapeHtml(c.label || c.id)}</span><span class="en">${escapeHtml(c.id)}</span><span class="parent">${escapeHtml(parentText)}</span></button>`;
}

function agentSectionHtml(section, classes) {
  const opened = agentSectionOpen.has(section.id) || Boolean(query);
  const cls = `kb-agent-section ${opened ? "open" : ""}`;
  const body = opened
    ? (classes.length
      ? classes.map(agentClassItem).join("")
      : `<p class="kb-item kb-empty" style="cursor:default;color:var(--mute)">无匹配的 Agent 类</p>`)
    : "";
  return `<section class="${cls}">`
    + `<button type="button" class="kb-agent-section-head" data-agent-section="${section.id}" aria-expanded="${opened}">`
    + `<span class="chev" aria-hidden="true"></span><span class="num">${section.n}</span><span class="lab">${section.label}</span><span class="cnt">${classes.length}</span>`
    + `<span class="sub">${section.sub}</span>`
    + `</button>`
    + `<p class="kb-agent-section-hint">${section.hint}</p>`
    + body
    + `</section>`;
}

function agentRuntimeHtml(instances) {
  const runtimeGroups = [
    { id: "AgentInstance", label: "运行实例", hint: "某次 Run 中承担决策责任的具体 Agent" },
    { id: "ShellTemplate", label: "壳体模板", hint: "平台与阶段的可复用运行模板" },
    { id: "Monitor", label: "监视与门控", hint: "评估条件并写入授权结果的角色对象" },
  ];
  let html = `<section class="kb-agent-runtime"><div class="kb-nav-label">运行对象 · ${instances.length}</div>`;
  runtimeGroups.forEach((group) => {
    const rows = instances.filter((e) => e.type === group.id);
    if (query && !rows.some(matchesQuery)) return;
    html += `<div class="kb-agent-runtime-group"><div class="kb-agent-runtime-head"><span>${group.label}</span><span class="cnt">${rows.length}</span></div><p class="kb-agent-runtime-hint">${group.hint}</p>`;
    const visible = rows.filter((e) => !query || matchesQuery(e));
    html += visible.length
      ? visible.map((e) => {
        const k = keyOf(e);
        return `<button type="button" class="kb-item ${pickedIri === e.iri ? "on" : ""}" ${individualNavAttributes(e)}><span>${escapeHtml(e.id)}</span><span class="en">${escapeHtml(e.type)}</span></button>`;
      }).join("")
      : `<p class="kb-item kb-empty" style="cursor:default;color:var(--mute)">无运行对象</p>`;
    html += `</div>`;
  });
  return html + `</section>`;
}

function agentNavHtml() {
  const extensionClasses = ((files["agent.extension.json"] || {}).classes || []);
  const extensionById = new Map(extensionClasses.map((c) => [c.id, c]));
  const instances = envMembers("Agent");
  let html = `<div class="kb-agent-intro"><strong>Agent 本体 · TBox</strong><span>分组是面向任务的浏览视图；继承关系仍以 rdfs:subClassOf 为准</span><small>WHO → OBSERVE → COMMUNICATE → DECIDE → TASK → ACT → RUN → ASSIST</small></div>`;
  AGENT_NAV_SECTIONS.forEach((section) => {
    const classes = section.classes
      .map((id) => extensionById.get(id))
      .filter(Boolean)
      .filter((c) => !query || classMatchesQuery(c));
    // Keep empty sections visible while browsing so users can learn the
    // complete navigation model; search results hide sections with no hit.
    if (query && !classes.length) return;
    html += agentSectionHtml(section, classes);
  });
  const unmapped = extensionClasses
    .filter((c) => agentSectionForClass(c.id) === "other")
    .filter((c) => !query || classMatchesQuery(c));
  if (unmapped.length) {
    html += agentSectionHtml({ id: "other", n: "07", label: "待归类扩展", sub: "OTHER", hint: "尚未映射到生命周期分组的类" }, unmapped);
  }
  html += agentRuntimeHtml(instances);
  return html;
}

function navClassRecord(id) {
  return classById(id)
    || ((files["agent.extension.json"] || {}).classes || []).find((c) => c.id === id)
    || { id, label: id, subClassOf: [] };
}

function topicClassItem(id) {
  const c = navClassRecord(id);
  const individuals = abox.filter(e => e.type === id && (!query || matchesQuery(e)));
  if (query && !classMatchesQuery(c) && !individuals.length) return "";
  const parent = (c.subClassOf || [])[0] || "";
  const parentLabel = parent ? labelOf(parent) : "根类";
  const parentText = parent ? `上位：${parentLabel} · ${parent}` : "上位：根类";
  return `<button type="button" class="kb-item kb-topic-class ${!picked && selectedClassIri === c.iri ? "on" : ""}" ${classNavAttributes(c)} title="${escapeHtml(parentText)}"><span>${escapeHtml(c.label || c.id)}</span><span class="en">${escapeHtml(c.id)}</span><span class="parent">${escapeHtml(parentText)}</span></button>`
    + individuals.map(e => `<button type="button" class="kb-item ${pickedIri === e.iri ? "on" : ""}" ${individualNavAttributes(e)} style="padding-left:34px"><span>${escapeHtml(e.label || e.id)}</span><span class="en">${escapeHtml(e.id)} · 个体</span></button>`).join("");
}

function topicSectionHtml(group, section) {
  const visible = section.classes.filter(id => !query || classMatchesQuery(navClassRecord(id)) || abox.some(e => e.type === id && matchesQuery(e)));
  if (query && !visible.length) return "";
  const key = `${group.id}:${section.id}`;
  const opened = topicSectionOpen.has(key) || Boolean(query);
  const body = opened
    ? (visible.map(topicClassItem).join("") || `<p class="kb-item kb-empty" style="cursor:default;color:var(--mute)">无匹配的类</p>`)
    : "";
  return `<section class="kb-topic-section ${opened ? "open" : ""}">`
    + `<button type="button" class="kb-topic-section-head" data-topic-section="${escapeHtml(key)}" aria-expanded="${opened}"><span class="chev" aria-hidden="true"></span><span class="lab">${escapeHtml(section.label)}</span><span class="cnt">${visible.length}</span></button>`
    + `<p class="kb-topic-section-hint">${escapeHtml(section.hint)}</p>`
    + body
    + `</section>`;
}

function topicGroupHtml(group) {
  const classIds = group.sections.flatMap((section) => section.classes);
  const visibleCount = classIds.filter(id => !query || classMatchesQuery(navClassRecord(id)) || abox.some(e => e.type === id && matchesQuery(e))).length;
  if (query && !visibleCount) return "";
  const opened = topicGroupOpen.has(group.id) || Boolean(query);
  const objectCount = abox.filter(e => classIds.includes(e.type) && (!query || matchesQuery(e))).length;
  const body = opened
    ? group.sections.map((section) => topicSectionHtml(group, section)).join("")
    : "";
  return `<section class="kb-topic-group ${opened ? "open" : ""}">`
    + `<button type="button" class="kb-topic-group-head" data-topic-group="${escapeHtml(group.id)}" aria-expanded="${opened}"><span class="ico">${NAV_ICO[group.ico] || ""}</span><span class="chev" aria-hidden="true"></span><span class="lab">${escapeHtml(group.label)}</span><span class="sub">${escapeHtml(group.hint)}</span><span class="cnt">${visibleCount} 类${objectCount ? ` · ${objectCount} 对象` : ""}</span></button>`
    + body
    + `</section>`;
}

function navViewSwitch() {
  return `<label class="kb-nav-radio ${navView === "topic" ? "on" : ""}" data-nav-view="topic"><input type="radio" name="kb-nav-view" value="topic" ${navView === "topic" ? "checked" : ""}><span class="radio-dot" aria-hidden="true"></span><strong>按主题</strong><small>语义投影</small></label>`
    + `<label class="kb-nav-radio ${navView === "module" ? "on" : ""}" data-nav-view="module"><input type="radio" name="kb-nav-view" value="module" ${navView === "module" ? "checked" : ""}><span class="radio-dot" aria-hidden="true"></span><strong>按模块</strong><small>数据目录</small></label>`;
}

function topicNavHtml() {
  return CONCEPT_GROUPS.map(topicGroupHtml).join("") + ambiguousClassNavHtml();
}

function topicGroupForClass(id) {
  return CONCEPT_GROUPS.find((group) => group.sections.some((section) => section.classes.includes(id))) || null;
}

function moduleNavHtml() {
  if (query) {
    (tbox.classes || []).forEach((c) => {
      if (classMatchesQuery(c)) ensureOpenPath(c.id);
    });
  }
  return GROUPS.map((g) => {
    const opened = groupOpen === g.id;
    const showBody = opened && !navRail;
    let body = "";
    if (showBody && g.kind === "tree") {
      const base = (tbox.classes || []).filter(c => !isAgentExtensionClass(c.id));
      const ids = new Set(base.map(c => c.id));
      const roots = base.filter(c => !(c.subClassOf || []).some(p => ids.has(p)));
      const reachable = new Set(roots.flatMap(c => [...descendantIds(c.id)]));
      const unrooted = base.filter(c => !reachable.has(c.id));
      body = `<div class="kb-tree">${roots.map(c => treeHtml(c.id, 0)).join("")}</div>`;
      if (unrooted.length) body += `<div class="kb-nav-label">层级未闭合的类</div>${unrooted.filter(c => !query || classMatchesQuery(c)).map(agentClassItem).join("")}`;
      body += abox.filter(e => e.type === "Package" && matchesQuery(e)).map(e => sceneItem(e, 12)).join("");
    }
    if (showBody && g.kind === "env") {
      if (g.id === "Scene") body = sceneNavHtml();
      else if (g.id === "Agent") body = agentNavHtml();
      else {
        const rows = envMembers(g.id);
        body = rows.length
          ? rows.map((e) => {
            const k = keyOf(e);
            return `<button type="button" class="kb-item ${pickedIri === e.iri ? "on" : ""}" ${individualNavAttributes(e)}><span>${escapeHtml(e.id)}</span><span class="en">${escapeHtml(e.type)}</span></button>`;
          }).join("")
          : `<p class="kb-item" style="cursor:default;color:var(--mute)">无个体</p>`;
      }
    }
    const n = g.kind === "tree"
      ? (tbox.classes || []).filter((c) => !isAgentExtensionClass(c.id)).length
      : abox.filter((e) => inEnv(e, g.id)).length;
    const countLabel = g.id === "Agent"
      ? `${((files["agent.extension.json"] || {}).classes || []).length} 类 · ${n} 对象`
      : `${n} 分类`;
    return `<section class="kb-sec ${opened ? "open" : ""}">`
      + `<button type="button" class="kb-sec-h" data-group="${g.id}" title="${g.lab} ${g.sub}"><span class="ico">${NAV_ICO[g.ico]}</span><span class="chev"></span><span class="num">${g.n}</span><span class="lab">${g.lab}</span><span class="sub">${g.sub}</span><span class="cnt">${countLabel}</span></button>`
      + body
      + `</section>`;
  }).join("") + ambiguousClassNavHtml();
}

function setNavigationTip(el, parts) {
  const values = [];
  parts.forEach((part) => {
    const value = String(part || "").replace(/\s+/g, " ").trim();
    if (value && !values.includes(value)) values.push(value);
  });
  if (!values.length) return;
  const tip = values.join(" · ");
  el.title = tip;
  el.dataset.navTip = tip;
  if (el.matches("button, label")) el.setAttribute("aria-label", tip);
}

function navigationPrimaryText(el) {
  const child = Array.from(el.children).find((node) => {
    const cls = node.classList;
    return !cls.contains("en") && !cls.contains("parent") && !cls.contains("sub")
      && !cls.contains("cnt") && !cls.contains("num") && !cls.contains("n")
      && !cls.contains("chev") && !cls.contains("ico") && !cls.contains("radio-dot");
  });
  return child ? child.textContent : el.textContent;
}

function decorateNavigationTips() {
  if (!groupsEl) return;

  groupsEl.querySelectorAll(".kb-sec-h, .kb-topic-group-head, .kb-topic-section-head, .kb-agent-section-head").forEach((head) => {
    const hint = head.nextElementSibling && head.nextElementSibling.matches(".kb-topic-section-hint, .kb-agent-section-hint")
      ? head.nextElementSibling.textContent : "";
    setNavigationTip(head, [navigationPrimaryText(head), head.querySelector(".sub")?.textContent, hint, head.querySelector(".cnt")?.textContent]);
  });

  groupsEl.querySelectorAll(".kb-agent-runtime-head").forEach((head) => {
    setNavigationTip(head, [navigationPrimaryText(head), head.nextElementSibling?.textContent, head.querySelector(".cnt")?.textContent]);
  });

  groupsEl.querySelectorAll(".kb-item").forEach((item) => {
    if (item.classList.contains("kb-empty")) return;
    setNavigationTip(item, [
      navigationPrimaryText(item),
      item.querySelector(".en")?.textContent,
      item.querySelector(".parent")?.textContent,
      item.getAttribute("title"),
    ]);
  });

  groupsEl.querySelectorAll(".kb-node .cls").forEach((item) => {
    setNavigationTip(item, [
      navigationPrimaryText(item),
      item.querySelector(".en")?.textContent,
      item.getAttribute("title"),
    ]);
  });

  groupsEl.querySelectorAll(".kb-agent-intro").forEach((intro) => {
    setNavigationTip(intro, [intro.querySelector("strong")?.textContent, intro.querySelector("span")?.textContent, intro.querySelector("small")?.textContent]);
  });
  navViewsEl?.querySelectorAll(".kb-nav-radio").forEach((radio) => {
    setNavigationTip(radio, [radio.querySelector("strong")?.textContent, radio.querySelector("small")?.textContent]);
  });
}

function renderNav() {
  if (navViewsEl) navViewsEl.innerHTML = navViewSwitch();
  groupsEl.innerHTML = navView === "topic" ? topicNavHtml() : moduleNavHtml();
  decorateNavigationTips();
}

const SCHEMA_DEFS = {
  Envelope: ["Body"],
  Corridor: ["Body"],
  SearchSector: ["Body"],
  DetectionDisk: ["Body"],
  JamBloom: ["Body"],
  CommandPost: ["Site"],
  Sensor: ["Site"],
  Shooter: ["Site"],
  Platform: ["Site"],
  Emitter: ["Site"],
  Package: ["PackageCatalog"],
};

function defRef(node) {
  const r = node && node.$ref;
  if (!r) return "";
  const m = String(r).match(/#\/\$defs\/([^/]+)$/);
  return m ? m[1] : "";
}

function schemaDef(name) {
  return (schemaDoc.$defs || {})[name] || null;
}

function defsForClass(cls) {
  const names = [cls, ...(SCHEMA_DEFS[cls] || [])];
  const seen = new Set();
  const out = [];
  names.forEach((n) => {
    if (seen.has(n)) return;
    seen.add(n);
    const def = schemaDef(n);
    if (def) out.push({ name: n, def });
  });
  return out;
}

function dictTable(cls) {
  return (dictionary.tables || []).find((t) => t.id === cls) || null;
}

function dictField(cls, name) {
  const table = dictTable(cls);
  return ((table && table.fields) || []).find((f) => f.name === name || f.name.split(".")[0] === name) || null;
}

function enumOfNode(node) {
  if (!node) return [];
  if (node.enum) return node.enum;
  if (node.const !== undefined) return [node.const];
  const ref = defRef(node);
  if (!ref) return [];
  const inner = schemaDef(ref);
  return (inner && inner.enum) || [];
}

function xsdOf(node) {
  if (!node) return "rdfs:Literal";
  if (node.const !== undefined) return `owl:hasValue ${JSON.stringify(node.const)}`;
  const en = node.enum;
  if (en) return `{ ${en.map((v) => JSON.stringify(v)).join(" ")} }`;
  const ref = defRef(node);
  if (ref) {
    if (classById(ref)) return ref;
    const inner = schemaDef(ref);
    if (inner && inner.enum) return `${ref} ≡ { ${inner.enum.map((v) => JSON.stringify(v)).join(" ")} }`;
    if (inner) return xsdOf(inner);
    return ref;
  }
  const t = node.type;
  if (t === "string") return "xsd:string";
  if (t === "number") return "xsd:decimal";
  if (t === "integer") return "xsd:integer";
  if (t === "boolean") return "xsd:boolean";
  if (t === "array") return `rdf:List ( ${xsdOf(node.items || {})} )`;
  if (Array.isArray(t)) return t.map((x) => xsdOf({ type: x })).join(" ⊔ ");
  if (t === "object") return "rdfs:Literal";
  return "rdfs:Literal";
}

function isObjectField(name, node, df) {
  if (df && df.fk) return true;
  const ref = defRef(node);
  if (ref && classById(ref)) return true;
  if (["of", "covers", "envelope", "bind", "give", "bindsPlatformType", "target", "src", "beam"].includes(name)) return true;
  if (name === "region" && classById("Region")) return true;
  return false;
}

function objectRange(name, node, df) {
  if (df && df.fk) {
    const t = String(df.fk).split(".")[0].split("|")[0];
    return t;
  }
  const ref = defRef(node);
  if (ref && classById(ref)) return ref;
  if (name === "bind") return "FunctionBind";
  if (name === "give") return "Class";
  if (name === "bindsPlatformType") return "PlatformType";
  if (name === "covers" || name === "envelope") return "SpatialRegion";
  if (name === "region") return "Region";
  if (name === "of") return "Entity";
  return "Entity";
}

function cardinality(required, node) {
  if (node && node.type === "array") {
    const min = node.minItems != null ? node.minItems : (required ? 1 : 0);
    const max = node.maxItems != null ? String(node.maxItems) : "*";
    return `${min}..${max}`;
  }
  if (required) return "exactly 1";
  return "0..1";
}

function schemaFields(cls) {
  const out = [];
  const seen = new Set();
  const push = (row) => {
    if (seen.has(row.name)) return;
    seen.add(row.name);
    out.push(row);
  };
  defsForClass(cls).forEach(({ name: defName, def }) => {
    const required = new Set(def.required || []);
    Object.entries(def.properties || {}).forEach(([name, node]) => {
      const df = dictField(cls, name) || dictField(defName, name);
      const object = isObjectField(name, node, df);
      push({
        name,
        label: (df && df.label) || name,
        defName,
        node,
        required: required.has(name),
        object,
        range: object ? objectRange(name, node, df) : xsdOf(node),
        card: cardinality(required.has(name), node),
        fk: df && df.fk,
        enums: enumOfNode(node),
        closed: def.additionalProperties === false,
      });
    });
  });
  const table = dictTable(cls);
  ((table && table.fields) || []).forEach((f) => {
    const name = f.name;
    if (seen.has(name) || name.includes(".")) return;
    const object = Boolean(f.fk);
    push({
      name,
      label: f.label || name,
      defName: "dictionary",
      node: { enum: f.enum, type: f.type },
      required: f.required === true,
      object,
      range: object ? String(f.fk).split(".")[0] : (f.enum ? `{ ${f.enum.join(" ")} }` : (f.type || "rdfs:Literal")),
      card: f.required === true ? "exactly 1" : "0..1",
      fk: f.fk,
      enums: f.enum || [],
      closed: false,
    });
  });
  return out;
}

function schemaAxioms(cls) {
  const fields = schemaFields(cls);
  const axioms = [];
  fields.forEach((f) => {
    if (f.node && f.node.const !== undefined) {
      axioms.push(`${cls} ⊑ ${f.name} value ${JSON.stringify(f.node.const)}`);
    } else if (f.required) {
      axioms.push(`${cls} ⊑ ${f.name} some ${f.range}`);
    }
    if (f.enums.length && !f.object) {
      axioms.push(`${cls} ⊑ ${f.name} only { ${f.enums.map((v) => JSON.stringify(v)).join(" ")} }`);
    }
  });
  defsForClass(cls).forEach(({ name, def }) => {
    if (def.additionalProperties === false) {
      axioms.push(`Schema ${name} additionalProperties false ⇒ ${cls} 仅拥有已列数据/对象属性`);
    }
    if (def.not) axioms.push(`Schema ${name} not … ⇒ 否定公理（见源 JSON）`);
    (def.allOf || []).forEach((block) => {
      const shape = block.if && block.if.properties && block.if.properties.shape && block.if.properties.shape.const;
      const req = (block.then && block.then.required) || [];
      if (shape && req.length) {
        axioms.push(`${cls} and (${cls} ⊑ shape value "${shape}") ⊑ ${req.map((p) => `${p} some Thing`).join(" ⊓ ")}`);
      }
    });
  });
  return axioms;
}

function manchesterLink(range) {
  if (classById(range)) return `<button type="button" class="link" data-jump="Class:${range}">${range}</button>`;
  return range;
}

function typeNameOf(e) {
  return e.type === "Class" ? e.id : e.type;
}

function domainHit(prop, cls) {
  return chainOf(cls).includes(prop.domain);
}

function emptyPane(msg) {
  return `<p class="insp-empty">${msg}</p>`;
}

function sheetOf(rows) {
  if (!rows.length) return "";
  return `<table class="sheet">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v || "未登记"}</td></tr>`).join("")}</table>`;
}

function pillsOf(items) {
  if (!items.length) return `<span class="mute">未登记</span>`;
  return `<ul class="pills">${items.map((x) => `<li>${x}</li>`).join("")}</ul>`;
}

function synonymsOf(e) {
  const rec = e.type === "Class" ? recordOf(e) : (classById(e.type) || {});
  return rec.synonyms || [];
}

function enumerationsOf(e) {
  const cls = typeNameOf(e);
  const rec = e.type === "Class" ? recordOf(e) : (classById(cls) || {});
  const local = rec.enumerations || [];
  const fromCodes = (dictionary.codes || []).filter((c) => (rec.enumCodes || []).includes(c.id))
    .map((c) => ({ id: c.id, label: c.label || c.id, values: c.values || [] }));
  const fromSchema = [];
  schemaFields(cls).forEach((f) => {
    if (f.enums.length) fromSchema.push({ id: f.name, label: f.label || f.name, values: f.enums });
  });
  const seen = new Set();
  const out = [];
  [...local, ...fromCodes, ...fromSchema].forEach((en) => {
    const k = `${en.id}:${(en.values || []).join(",")}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push(en);
  });
  return out;
}

function owlHead(title, manchester) {
  return `<h3 class="child-h">${title}<span class="owl-tag">${manchester}</span></h3>`;
}

function paneDef(e, rec, m) {
  const cls = typeNameOf(e);
  const defs = defsForClass(cls);
  const syn = synonymsOf(e);
  const enums = enumerationsOf(e);
  const st = ((lexicon.types[e.type] || lexicon.types.Class || {}).state) || rec.stateModel || {};
  const rows = [
    ["rdf:type", e.type === "Class" ? "owl:Class" : `owl:NamedIndividual , ${cls}`],
    ["rdfs:label", e.type === "Class" ? (rec.label || labelOf(cls)) : (m.what || e.id)],
    ["IRI", rec.iri || e.iri || e.id],
    ["rdfs:comment", rec.comment || m.what || "未登记"],
    ["Schema $defs", defs.length ? defs.map((d) => d.name).join(" · ") : "无直接 $defs，沿用 TBox / 字典"],
    ["hasState some Quality", st.kind ? `${st.kind} · ${(st.states || rec.states || []).join(" / ") || "—"}` : "Entity ⊑ hasState some Quality"],
  ];
  if (e.type !== "Class") {
    statements(e, rec).forEach((pair) => {
      if (!["中文名", "说明", "名称"].includes(pair[0])) rows.push(pair);
    });
  }
  let html = owlHead("定义", "owl:Class · rdfs:comment · skos:altLabel") + sheetOf(rows);
  html += owlHead("军事同义词", "skos:altLabel") + pillsOf(syn);
  html += owlHead("枚举", "owl:oneOf / DatatypeRestriction");
  if (!enums.length) html += emptyPane("Schema 未给出 enum / const / $ref 枚举。");
  else {
    html += enums.map((en) => `<p class="ax">${en.label || en.id}<span class="dl">${en.id} ≡ { ${(en.values || []).map((v) => JSON.stringify(v)).join(" ")} }</span></p>${pillsOf((en.values || []).map((v) => String(v)))}`).join("");
  }
  return html;
}

function paneAttr(e, rec) {
  const cls = typeNameOf(e);
  const data = schemaFields(cls).filter((f) => !f.object);
  const tboxData = (tbox.dataProperties || []).filter((p) => domainHit(p, cls));
  let html = owlHead("属性", "owl:DatatypeProperty ← Schema 字面量字段");
  if (data.length) {
    html += `<table class="child-tbl"><thead><tr><th>数据属性</th><th>值域</th><th>基数</th><th>Schema</th></tr></thead><tbody>`
      + data.map((f) => `<tr><td>${f.name}<div class="mute">${f.label}</div></td><td>${f.range}</td><td>${f.card}</td><td>${f.defName}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  const extra = tboxData.filter((p) => !data.some((f) => f.name === p.id));
  if (extra.length) {
    html += owlHead("TBox 数据属性", "owl:DatatypeProperty")
      + sheetOf(extra.map((p) => [p.id, `${p.range}${p.comment ? " · " + p.comment : ""}`]));
  }
  if (e.type !== "Class") {
    const facts = statements(e, rec);
    if (facts.length) html += owlHead("本个体字面量", "DataPropertyAssertion") + sheetOf(facts);
  }
  if (!data.length && !extra.length && e.type === "Class") return html + emptyPane("Schema 无字面量字段，故无数据属性。");
  return html;
}

function paneAct(e, rec) {
  const cls = typeNameOf(e);
  let html = owlHead("行为", "Process / participatesIn · Schema 的 Stage、evalOn、stateModel");
  html += `<p class="owl-lead">Schema 不写方法。作业段、评估时机、状态寿命都是发生体或质量。</p>`;
  const fields = schemaFields(cls);
  const processish = fields.filter((f) => ["stages", "evalOn", "stateModel", "states", "events"].includes(f.name));
  if (processish.length) {
    html += `<table class="child-tbl"><thead><tr><th>Schema</th><th>OWL</th><th>值域</th></tr></thead><tbody>`
      + processish.map((f) => {
        let owl = `${f.name} some rdfs:Literal`;
        if (f.name === "stages") owl = "specifiedBy some Stage · participatesIn some Process";
        if (f.name === "evalOn") owl = "evaluatesOn some Entity";
        if (f.name === "stateModel" || f.name === "states") owl = "hasState some Quality";
        return `<tr><td>${f.name}</td><td>${owl}</td><td>${f.range}</td></tr>`;
      }).join("")
      + `</tbody></table>`;
  }
  const ops = (tbox.objectProperties || []).filter((p) => domainHit(p, cls) && ["participatesIn", "specifiedBy", "evaluatesOn"].includes(p.id));
  if (ops.length) {
    html += owlHead("过程对象属性", "owl:ObjectProperty")
      + sheetOf(ops.map((p) => [p.id, `${p.domain} → ${p.range}`]));
  }
  const templates = ((files["shells.json"] || {}).templates || []);
  const showStages = cls === "ShellTemplate" || cls === "Stage" || cls === "Process" || e.type === "ShellTemplate";
  if (showStages) {
    const rows = [];
    templates.forEach((t) => (t.stages || []).forEach((s) => rows.push({ t: t.id, ...s })));
    if (e.type === "ShellTemplate" && rec.stages) rec.stages.forEach((s) => rows.push({ t: rec.id, ...s }));
    if (rows.length) {
      html += owlHead("Stage 个体", "rdf:type Stage · reads/writes some InformationEntity")
        + `<table class="child-tbl"><thead><tr><th>段</th><th>reads some</th><th>writes some</th></tr></thead><tbody>`
        + rows.map((s) => `<tr><td>${s.name}</td><td>${(s.reads || []).join("、")}</td><td>${(s.writes || []).join("、")}</td></tr>`).join("")
        + `</tbody></table>`;
    }
  }
  return html;
}

function paneIface(e, rec, m) {
  const cls = typeNameOf(e);
  const obj = schemaFields(cls).filter((f) => f.object);
  const tboxObj = (tbox.objectProperties || []).filter((p) => domainHit(p, cls));
  let html = owlHead("接口", "owl:ObjectProperty ← Schema $ref / fk");
  html += `<p class="owl-lead">Schema 的外键与 $ref 是对象属性，不是 OOP interface。</p>`;
  if (obj.length) {
    html += `<table class="child-tbl"><thead><tr><th>对象属性</th><th>值域</th><th>基数</th><th>Schema</th></tr></thead><tbody>`
      + obj.map((f) => `<tr><td>${f.name}<div class="mute">${f.label}</div></td><td>${manchesterLink(f.range)}</td><td>${f.card}</td><td>${f.fk || f.defName}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  const extra = tboxObj.filter((p) => !obj.some((f) => f.name === p.id || (f.name === "bind" && p.id === "boundTo") || (f.name === "give" && p.id === "gives")));
  if (extra.length) {
    html += owlHead("TBox 对象属性", "owl:ObjectProperty")
      + sheetOf(extra.map((p) => [p.id, `${p.domain} → ${manchesterLink(p.range)}${p.inverseOf ? " · inverseOf " + p.inverseOf : ""}`]));
  }
  const rels = relations(e, rec);
  if (rels.length) html += owlHead("本个体断言", "ObjectPropertyAssertion") + sheetOf(rels.map((r) => [r.lab, r.type ? `<button type="button" class="link" data-jump="${r.type}:${r.id}">${r.id}</button>` : r.id]));
  const uses = (m.usedIn || []).filter((u) => u.href);
  if (uses.length) html += owlHead("出现于", "annotation") + `<ul class="uses">${uses.map((u) => `<li><a href="${u.href}">${u.page}</a> · ${u.look}</li>`).join("")}</ul>`;
  return html;
}

function paneRule(e, rec) {
  const cls = typeNameOf(e);
  let html = owlHead("规则", "OWL 公理 ← Schema required / const / enum / closed");
  const compiled = schemaAxioms(cls);
  if (compiled.length) {
    html += compiled.map((a) => `<p class="ax">Schema ⇒ OWL<span class="dl">${a}</span></p>`).join("");
  }
  const ax = axiomsFor(e, rec);
  const axHtml = ax.hits.map((a) => `<p class="ax">${AXIOM_ZH[a.id] || a.comment || "公理"}<span class="dl">${a.manchester}</span></p>`).join("")
    + ax.disj.map((pair) => `<p class="ax">owl:disjointWith<span class="dl">${pair[0]} DisjointWith ${pair[1]}</span></p>`).join("");
  if (axHtml) html += owlHead("TBox 公理", "owl:Axiom") + axHtml;
  if (ax.illegal.length) html += `<p class="ax">¬ ${ax.illegal.join(" ； ¬ ")}</p>`;
  const lab = labelOf(cls);
  const rules = ((files["rules.json"] || {}).rules || []);
  const hits = cls === "Rule" || e.type === "Rule"
    ? (e.type === "Rule" ? rules.filter((r) => r.id === e.id) : rules)
    : rules.filter((r) => [r.id, r.text, r.evalOn, ...(r.illegal || [])].join(" ").includes(cls) || [r.id, r.text, r.evalOn].join(" ").includes(lab));
  if (hits.length) {
    html += owlHead("Rule 个体", "rdf:type Rule")
      + `<table class="child-tbl"><thead><tr><th>个体</th><th>hasModality</th><th>hasText</th></tr></thead><tbody>`
      + hits.map((r) => `<tr><td><button type="button" data-jump="Rule:${r.id}">${r.id}</button></td><td>${r.modality || ""}</td><td>${r.text || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneClassOf(e) {
  const cls = typeNameOf(e);
  const chain = chainOf(cls).slice().reverse();
  const kids = childrenOf(cls);
  let html = owlHead("ClassOf", "rdf:type · rdfs:subClassOf");
  html += sheetOf([
    ["rdf:type", e.type === "Class" ? "owl:Class" : `<button type="button" class="link" data-jump="Class:${cls}">${cls}</button>`],
    ["rdfs:subClassOf", chain.map((id) => `<button type="button" class="link" data-jump="Class:${id}">${id}</button>`).join(" ⊑ ")],
  ]);
  if (kids.length) {
    html += owlHead("直接子类", "C rdfs:subClassOf this")
      + `<table class="child-tbl"><thead><tr><th>rdfs:label</th><th>owl:Class</th></tr></thead><tbody>`
      + kids.map((c) => `<tr><td><button type="button" data-jump="Class:${c.id}">${c.label || labelOf(c.id)}</button></td><td>${c.id}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  const objs = e.type === "Class" ? listed() : [];
  if (objs.length) {
    html += owlHead("命名个体", "rdf:type " + cls)
      + `<table class="child-tbl"><thead><tr><th>个体</th><th>rdf:type</th></tr></thead><tbody>`
      + objs.map((x) => `<tr><td><button type="button" data-jump="${x.type}:${x.id}">${x.id}</button></td><td>${x.type}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneSrc(e, rec) {
  const cls = typeNameOf(e);
  const defs = defsForClass(cls);
  let html = owlHead("源 JSON", "Schema $defs + 本记录");
  if (defs.length) {
    html += defs.map((d) => `<p class="ax">$defs/${d.name}</p><pre class="kb-raw">${JSON.stringify(d.def, null, 2)}</pre>`).join("");
  } else html += emptyPane("无对应 Schema $defs。");
  html += `<p class="ax">记录</p><pre class="kb-raw kb-raw-full">${JSON.stringify(rec, null, 2)}</pre>`;
  return html;
}

function paneDepict(e, rec, m) {
  const kind = m.depict || "mark";
  const kindZh = { icon: "标准图符", photo: "实物照片", volume: "体积示意", mark: "抽象记号", none: "不画" }[kind] || kind;
  const note = (lexicon.depict || {})[kind] || "";
  const sidc = rec.sidc || {};
  const photo = m.photo
    ? `<figure class="depict-cell"><img src="${m.photo}" alt="${e.id}" /><figcaption>foaf:depiction · 实物照片</figcaption></figure>`
    : "";
  const icon = kind === "none"
    ? emptyPane("词表 depict=none，不画。")
    : `<figure class="depict-cell"><canvas data-glyph="1" width="200" height="200"></canvas><figcaption>标准 icon · glyph ${m.glyph}</figcaption></figure>`;
  let html = owlHead("图示", "depict / foaf:depiction")
    + `<p class="owl-lead">外观是标注，不是类型。照片与图符只说明怎么画，不改变 rdf:type。</p>`
    + sheetOf([
      ["depict", `${kind} · ${kindZh}`],
      ["glyph", m.glyph || "—"],
      ["说明", note],
    ]);
  html += owlHead("符号", "SIDC · MIL-STD-2525D / milsymbol MIT")
    + (sidc.code
      ? sheetOf([
        ["SIDC", sidc.code],
        ["std", sidc.std],
        ["symbolSet", sidc.symbolSet],
        ["entity", sidc.entity],
        ["identity", sidc.identity],
        ["label", sidc.label],
        ["src", sidc.src],
      ])
      : emptyPane("本对象无 20 位 SIDC（体积/规则不是 2525 点符号）。"));
  return html + `<div class="depict-board">${photo}${icon}</div>`;
}

function inspectGroupOf(e) {
  if (!e || e.type === "Package") return "onto";
  if (e.type === "Class") return isAgentExtensionClass(e.id) ? "Agent" : "onto";
  return groupForType(e.type);
}

const TAB_SETS = {
  onto: [
    ["ann", "注释", "Annotation"],
    ["subclass", "子类", "rdfs:subClassOf"],
    ["equiv", "等价", "owl:equivalentClass"],
    ["disjoint", "不相交", "owl:disjointWith"],
    ["data", "数据属性", "DatatypeProperty"],
    ["object", "对象属性", "ObjectProperty"],
    ["valuedomain", "值域", "Value Domain"],
    ["individuals", "个体", "NamedIndividual"],
    ["axiom", "公理", "Axiom"],
  ],
  Scene: [
    ["otype", "型谱", "OBJECT-TYPE"],
    ["item", "个体", "OBJECT-ITEM"],
    ["geom", "几何", "Geometry"],
    ["loc", "位置", "LOCATION"],
    ["cap", "能力", "CAPABILITY"],
    ["assoc", "关联", "ASSOCIATION"],
    ["constraint", "约束", "GFM Constraint"],
    ["sidc", "符号", "SIDC"],
  ],
  Rule: [
    ["stmt", "表述", "Statement"],
    ["mod", "模态", "Modality"],
    ["ant", "条件", "Antecedent"],
    ["params", "参数", "Value Domain"],
    ["effect", "裁决", "Effect"],
    ["prohib", "禁止", "Prohibition"],
    ["enforce", "执行", "Enforcement"],
  ],
  Algorithm: [
    ["fn", "职能", "Function"],
    ["scope", "范围", "Scope"],
    ["input", "输入", "Input"],
    ["output", "输出", "Output"],
    ["binding", "绑定", "Binding"],
    ["claim", "主张", "Claim"],
    ["impl", "实现", "Implementation"],
  ],
  Engagement: [
    ["who", "谁", "Who"],
    ["what", "何事", "What"],
    ["when", "何时", "When"],
    ["where", "何地", "Where"],
    ["why", "为何", "Why"],
    ["reporting", "出处", "REPORTING-DATA"],
    ["authority", "授权", "Authority"],
  ],
  Agent: [
    ["atype", "型谱", "Type"],
    ["acap", "能力", "Capability"],
    ["stage", "阶段", "Stage"],
    ["flow", "读写", "Information flow"],
    ["role", "角色", "Role"],
    ["nowrite", "禁写", "Constraint"],
    ["boundary", "责任边界", "timeScale × permission × failureContainment"],
  ],
  Knowledge: [
    ["source", "来源", "INFORMATION-SOURCE"],
    ["numeric", "数字特征", "Numerical characteristic"],
    ["quality", "质量", "Quality"],
    ["prov", "谱系", "PROV-O"],
    ["grain", "粒度", "Granularity"],
    ["vis", "可见性", "Visibility"],
  ],
};
const SHARED_TABS = [
  ["depict", "图示", "depict"],
  ["src", "源 JSON", "SoR"],
];

function tabsOf(e) {
  return knowledgeTabs(knowledgeModel, e);
}

function navStandard(e) {
  const gid = inspectGroupOf(e);
  const navId = gid === "onto" ? "Ontology" : gid;
  const rec = (dictionary.nav || []).find((n) => n.id === navId);
  return rec ? rec.standard : "";
}

function ensureTab(e) {
  const ids = tabsOf(e).map((t) => t[0]);
  if (!ids.includes(tab)) tab = ids[0];
}

function jumpBtn(type, id) {
  if (id == null || id === "") return "—";
  if (!type) return String(id);
  return `<button type="button" class="link" data-jump="${type}:${id}">${id}</button>`;
}

function functionById(id) {
  return ((files["algorithms.json"] || {}).functions || []).find((f) => f.id === id) || null;
}

function paneAnn(e, rec) {
  const cls = typeNameOf(e);
  const syn = synonymsOf(e);
  const iri = `https://local.ontology/coast-raid-k#${e.id}`;
  let html = owlHead("注释", "rdfs:label · skos:altLabel · rdfs:comment")
    + sheetOf([
      ["rdfs:label", e.type === "Class" ? (rec.label || labelOf(cls)) : (rec.label || e.label || e.id)],
      ["IRI", rec.iri || e.iri || iri],
      ["rdf:type", e.type === "Class" ? "owl:Class" : `owl:NamedIndividual , ${cls}`],
      ["rdfs:comment", rec.comment || "未登记"],
    ]);
  html += owlHead("军事同义词", "skos:altLabel") + pillsOf(syn);
  return html;
}

function paneSubclass(e) {
  return paneClassOf(e);
}

function paneEquiv(e, rec) {
  const cls = e.type === "Class" ? rec : classById(typeNameOf(e)) || {};
  const eq = cls.equivalentTo || [];
  const see = cls.seeAlso || [];
  let html = owlHead("等价", "owl:equivalentClass · BFO 2020 CC BY 4.0");
  html += `<p class="owl-lead">只对齐上层 BFO。领域类用 rdfs:seeAlso，不伪造定义。</p>`;
  if (eq.length) {
    html += eq.map((iri) => `<p class="ax">owl:equivalentClass<span class="dl">${iri}</span></p>`).join("");
  } else {
    html += emptyPane("本类无 owl:equivalentClass（未把必要包含写成定义）。");
  }
  html += owlHead("参见", "rdfs:seeAlso · CCO / GeoSPARQL / PROV-O");
  if (see.length) {
    html += see.map((iri) => `<p class="ax">rdfs:seeAlso<span class="dl">${iri}</span></p>`).join("");
  } else {
    html += emptyPane("无 seeAlso。");
  }
  const src = files["sources.json"];
  if (src && (src.items || []).length) {
    html += owlHead("开源出处", "sources.json")
      + `<table class="child-tbl"><thead><tr><th>id</th><th>license</th><th>usedAs</th></tr></thead><tbody>`
      + src.items.map((it) => `<tr><td>${it.id}</td><td>${it.license || ""}</td><td>${it.usedAs || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneDisjoint(e) {
  const cls = typeNameOf(e);
  const hits = (tbox.disjoint || []).filter((pair) => pair.includes(cls));
  let html = owlHead("不相交", "owl:disjointWith");
  if (!hits.length) return html + emptyPane("本类未出现在 tbox.disjoint。");
  return html + hits.map((pair) => `<p class="ax">owl:disjointWith<span class="dl">${pair[0]} DisjointWith ${pair[1]}</span></p>`).join("");
}

function paneValueDomain(e) {
  const enums = enumerationsOf(e);
  let html = owlHead("值域", "ISO 11179 Value Domain · owl:oneOf");
  if (!enums.length) return html + emptyPane("无 enum / const / 字典码。");
  return html + enums.map((en) => `<p class="ax">${en.label || en.id}<span class="dl">${en.id} ≡ { ${(en.values || []).map((v) => JSON.stringify(v)).join(" ")} }</span></p>${pillsOf((en.values || []).map((v) => String(v)))}`).join("");
}

function paneIndividuals(e) {
  const cls = typeNameOf(e);
  const objs = e.type === "Class" ? listed() : abox.filter((x) => x.type === cls);
  let html = owlHead("个体", "owl:NamedIndividual · rdf:type");
  if (!objs.length) return html + emptyPane("封皮中无本类命名个体。");
  return html
    + `<table class="child-tbl"><thead><tr><th>个体</th><th>rdf:type</th></tr></thead><tbody>`
    + objs.map((x) => `<tr><td><button type="button" data-jump="${x.type}:${x.id}">${x.id}</button></td><td>${x.type}</td></tr>`).join("")
    + `</tbody></table>`;
}

function paneAxiomOnly(e, rec) {
  const cls = typeNameOf(e);
  let html = owlHead("公理", "owl:Axiom ← Schema required / const / closed");
  const compiled = schemaAxioms(cls);
  if (compiled.length) html += compiled.map((a) => `<p class="ax">Schema ⇒ OWL<span class="dl">${a}</span></p>`).join("");
  const ax = axiomsFor(e, rec);
  const axHtml = ax.hits.map((a) => `<p class="ax">${AXIOM_ZH[a.id] || a.comment || "公理"}<span class="dl">${a.manchester}</span></p>`).join("")
    + ax.disj.map((pair) => `<p class="ax">owl:disjointWith<span class="dl">${pair[0]} DisjointWith ${pair[1]}</span></p>`).join("");
  if (axHtml) html += owlHead("TBox 公理", "owl:Axiom") + axHtml;
  if (ax.illegal.length) html += `<p class="ax">¬ ${ax.illegal.join(" ； ¬ ")}</p>`;
  if (!compiled.length && !axHtml && !ax.illegal.length) html += emptyPane("无编译出的公理。");
  return html;
}

function paneOType(e, rec) {
  let html = owlHead("型谱", "JC3IEDM OBJECT-TYPE");
  html += `<p class="owl-lead">型不是个体。inScene=true 才可进本幕波次；false 是型库，供未来场景引用。</p>`;
  if (e.type === "PlatformType") {
    const rows = [
      ["id", rec.id || e.id],
      ["inScene", rec.inScene === true ? "true · 本幕 ITEM" : rec.inScene === false ? "false · 型库" : ""],
      ["side", rec.side],
      ["uasGroup", rec.uasGroup != null ? `DoD Group ${rec.uasGroup}` : ""],
      ["natoClass", rec.natoClass],
      ["airframe", rec.airframe],
      ["role", rec.role],
      ["klass", rec.klass],
      ["layers", (rec.layers || []).join(" · ")],
      ["tasks", (rec.tasks || []).join(" · ")],
      ["mtow_kg", [rec.mtow_kg_min, rec.mtow_kg_max].filter((x) => x != null).join("–")],
      ["alt_m_max", rec.alt_m_max != null ? `${rec.alt_m_max} ${rec.alt_ref || "m"}` : ""],
      ["speed_kt_max", rec.speed_kt_max != null ? String(rec.speed_kt_max) : ""],
      ["speed_kmh", rec.speed_kmh != null ? String(rec.speed_kmh) : ""],
      ["speed_kmh_byLayer", rec.speed_kmh_byLayer ? JSON.stringify(rec.speed_kmh_byLayer) : ""],
      ["form_lo_lat_kmh", rec.form_lo_lat_kmh != null ? String(rec.form_lo_lat_kmh) : ""],
      ["band_m", rec.band_m ? rec.band_m.join("–") : ""],
      ["missionRadius", rec.missionRadius],
      ["payload", rec.payload],
      ["identity", rec.identity],
      ["source", rec.source],
      ["note", rec.note],
      ["src", rec.src],
    ].filter(([, v]) => v);
    return html + sheetOf(rows);
  }
  const pts = (files["theater.json"] || {}).platformTypes || [];
  if (e.type === "ShellTemplate" && rec.bindsPlatformType) {
    const pt = pts.find((p) => p.id === rec.bindsPlatformType);
    html += sheetOf([["bindsPlatformType", jumpBtn("PlatformType", rec.bindsPlatformType)]]);
    if (pt) html += sheetOf([["side", pt.side], ["inScene", String(pt.inScene)], ["layers", (pt.layers || []).join(" · ")]]);
    return html;
  }
  if (pts.length) {
    html += `<table class="child-tbl"><thead><tr><th>型</th><th>inScene</th><th>Group</th><th>NATO</th><th>role</th></tr></thead><tbody>`
      + pts.map((p) => `<tr><td><button type="button" data-jump="PlatformType:${p.id}">${p.id}</button></td><td>${p.inScene === true ? "本幕" : "型库"}</td><td>${p.uasGroup || ""}</td><td>${p.natoClass || ""}</td><td>${p.role || p.side || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneItem(e, rec) {
  return owlHead("个体", "JC3IEDM OBJECT-ITEM")
    + sheetOf([
      ["id", e.id],
      ["type", e.type],
      ["kind", rec.kind],
      ["label", rec.label || e.label],
      ["not", rec.not],
    ]);
}

function wktOf(e, rec) {
  if (rec.p && rec.p.length === 3) return `POINT Z (${rec.p[0]} ${rec.p[1]} ${rec.p[2]})`;
  const shape = rec.shape;
  if ((shape === "hemisphere" || shape === "disk" || shape === "sector_slab") && rec.c && rec.r != null) {
    const x = rec.c[0];
    const z = rec.c[2];
    const r = rec.r;
    return `POLYGON((${x - r} ${z - r}, ${x + r} ${z - r}, ${x + r} ${z + r}, ${x - r} ${z + r}, ${x - r} ${z - r}))`;
  }
  if (shape === "aabb" || (rec.x0 != null && rec.z0 != null)) {
    return `POLYGON((${rec.x0} ${rec.z0}, ${rec.x1} ${rec.z0}, ${rec.x1} ${rec.z1}, ${rec.x0} ${rec.z1}, ${rec.x0} ${rec.z0}))`;
  }
  if (rec.kind === "harmonic" && rec.params && rec.params.terms) {
    const terms = rec.params.terms;
    const pts = [];
    for (let i = 0; i <= 48; i += 1) {
      const x = -6 + 12 * (i / 48);
      let z = 0;
      terms.forEach((t) => { z += (t.amp || 0) * Math.sin((t.freq || 0) * x + (t.phase || 0)); });
      pts.push(`${x.toFixed(4)} 0 ${z.toFixed(4)}`);
    }
    return `LINESTRING Z (${pts.join(", ")})`;
  }
  return rec.wkt || "";
}

function paneGeom(e, rec) {
  let html = owlHead("几何", "ISO 19107 / GeoSPARQL geo:hasGeometry");
  html += `<p class="owl-lead">可行集体积，不是占领区。CRS 是 CRK:ENG 工程坐标，单位 QUDT KiloM。不是 OSM 港湾。</p>`;
  const gis = files["gis.json"] || {};
  const crs = gis.crs || {};
  const rows = [];
  if (crs.id) rows.push(["crs", `${crs.id} · ${crs.type || ""}`]);
  if (crs.unit) rows.push(["unit", crs.unit]);
  if (Object.prototype.hasOwnProperty.call(crs, "epsg")) rows.push(["EPSG", crs.epsg == null ? "无（砂盘）" : String(crs.epsg)]);
  if (rec.shape) rows.push(["shape", rec.shape]);
  if (rec.c) rows.push(["c [x,y,z] km", rec.c.join(", ")]);
  if (rec.r != null) rows.push(["r km", String(rec.r)]);
  if (rec.rim != null) rows.push(["rim", String(rec.rim)]);
  if (rec.rim_r != null) rows.push(["rim_r km", String(rec.rim_r)]);
  if (rec.shape === "aabb" || rec.x0 != null) {
    rows.push(["x0…x1", `${rec.x0} … ${rec.x1}`]);
    rows.push(["z0…z1", `${rec.z0} … ${rec.z1}`]);
    rows.push(["y0…y1", `${rec.y0} … ${rec.y1}`]);
  }
  if (rec.az_deg != null) rows.push(["azimuth", `${rec.az_deg}° ± ${rec.half_deg}°`]);
  if (rec.h != null) rows.push(["h km", String(rec.h)]);
  if (rec.kind === "harmonic" && rec.params && rec.params.terms) {
    rows.push(["岸线", `harmonic · ${rec.params.terms.length} 项`]);
  }
  const wkt = wktOf(e, rec);
  if (wkt) rows.push(["geo:asWKT", wkt]);
  if (!rows.length) return html + emptyPane("本对象无几何字段（型谱/链路不是 GM_Object）。");
  return html + sheetOf(rows);
}

function paneLoc(e, rec) {
  const gis = files["gis.json"] || {};
  const mem = (gis.membership || []).filter((m) => m.of === e.id || `${m.of}∈${m.region}` === e.id);
  let html = owlHead("位置", "JC3IEDM OBJECT-ITEM-LOCATION")
    + sheetOf([
      ["p [x,y,z] km", rec.p ? rec.p.join(", ") : ""],
      ["region", rec.region ? jumpBtn("Region", rec.region) : ""],
      ["predicate", rec.predicate],
    ]);
  if (mem.length) {
    html += owlHead("隶属", "Membership")
      + `<table class="child-tbl"><thead><tr><th>of</th><th>region</th><th>rule</th></tr></thead><tbody>`
      + mem.map((m) => `<tr><td>${m.of}</td><td>${m.region}</td><td>${m.rule}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneCap(e, rec) {
  let html = owlHead("能力", "JC3IEDM CAPABILITY · sosa:Procedure");
  html += `<p class="owl-lead">Sensor 的 covers/beam 是观测程序；Shooter 的 envelope/channels0 是交战能力。能力不是 methods。长度单位 QUDT KiloM。</p>`;
  return html + sheetOf([
    ["covers", rec.covers],
    ["envelope", rec.envelope],
    ["beam", rec.beam],
    ["channels0", rec.channels0 != null ? String(rec.channels0) : ""],
    ["heading", rec.heading || (rec.heading_deg != null ? `${rec.heading_deg}°` : "")],
    ["role", rec.role],
    ["r km", rec.r != null ? String(rec.r) : ""],
    ["speed_kmh", rec.speed_kmh != null ? String(rec.speed_kmh) : ""],
    ["unit", "http://qudt.org/vocab/unit/KiloM"],
  ]);
}

function paneAssoc(e) {
  const g = files["graph.json"] || {};
  const owns = (g.owns || []).filter((r) => r.site === e.id || r.body === e.id);
  const covers = (g.covers || []).filter((r) => r.sensor === e.id || r.body === e.id);
  const pairs = (g.notSameAs || []).filter((p) => p[0] === e.id || p[1] === e.id);
  const tdl = g.shoreTdl || {};
  const spokes = (tdl.spokes || []).filter((s) => s.from === e.id || s.to === e.id || e.id === tdl.id);
  const links = (g.otherLinks || []).filter((l) => l.id === e.id || l.from === e.id || l.to === e.id || (l.among || "").includes(e.id));
  let html = owlHead("关联", "JC3IEDM ASSOCIATION · Relator");
  if (owns.length) {
    html += owlHead("owns", "MaterialObject → SpatialRegion")
      + `<table class="child-tbl"><thead><tr><th>site</th><th>body</th><th>beam</th></tr></thead><tbody>`
      + owns.map((r) => `<tr><td>${r.site}</td><td>${r.body || "—"}</td><td>${r.beam || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  if (covers.length) {
    html += owlHead("covers", "Sensor → SpatialRegion")
      + `<table class="child-tbl"><thead><tr><th>sensor</th><th>body</th><th>mode</th></tr></thead><tbody>`
      + covers.map((r) => `<tr><td>${r.sensor}</td><td>${r.body || "—"}</td><td>${r.mode || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  if (pairs.length) html += owlHead("notSameAs", "owl:differentFrom") + sheetOf(pairs.map((p) => [p[0], p[1]]));
  if (e.type === "Needline" || spokes.length) {
    html += owlHead("Needline G(t)", "岸上数据链")
      + sheetOf([
        ["id", tdl.id],
        ["root", tdl.root],
        ["deniedBy", tdl.deniedBy],
        ["onDenied", tdl.onDenied],
      ]);
    if ((tdl.spokes || []).length) {
      html += `<table class="child-tbl"><thead><tr><th>from</th><th>to</th><th>role</th><th>km</th></tr></thead><tbody>`
        + (tdl.spokes || []).map((s) => `<tr><td>${s.from}</td><td>${s.to}</td><td>${s.role || ""}</td><td>${s.km != null ? s.km : ""}</td></tr>`).join("")
        + `</tbody></table>`;
    }
  }
  if (links.length) {
    html += owlHead("Link", "通信边")
      + links.map((l) => sheetOf([["id", l.id], ["from", l.from], ["to", l.to], ["among", l.among], ["survivesJam", l.survivesJam == null ? "" : String(l.survivesJam)]])).join("");
  }
  if (!owns.length && !covers.length && !pairs.length && !spokes.length && !links.length && e.type !== "Needline") {
    html += emptyPane("graph.json 未登记本对象的 owns/covers/needline/link。");
  }
  return html;
}

function paneSceneConstraint(e, rec) {
  const cat = files["catalog.json"] || {};
  const rules = ((files["rules.json"] || {}).rules || []).filter((r) => r.id === "envelope_not_occupy" || r.id === "no_satellite_sensor");
  let html = owlHead("约束", "ISO 19109 GFM Constraint");
  html += `<p class="owl-lead">战场约束不是 RoE。占领区、天基传感器、Link-16 是排除项。</p>`;
  html += sheetOf([
    ["not", rec.not],
    ["excludes", (cat.excludes || []).join(" · ")],
  ]);
  if (rules.length) {
    html += rules.map((r) => `<p class="ax">${r.id}<span class="dl">${r.text}</span></p>`).join("");
  }
  return html;
}

function paneStmt(e, rec) {
  return owlHead("表述", "SBVR Statement")
    + sheetOf([
      ["id", e.id],
      ["text", rec.text],
      ["sbvr", rec.sbvr],
    ]);
}

function paneMod(e, rec) {
  const map = { hard: "SBVR necessity（定义性 / alethic）", roe: "LegalRuleML Obligation/Prohibition（行为性 / deontic）", comm: "通信 / 可见性约束" };
  return owlHead("模态", "SBVR Modality")
    + sheetOf([
      ["modality", rec.modality],
      ["解读", map[rec.modality] || ""],
    ]);
}

function paneAnt(e, rec) {
  return owlHead("条件", "Antecedent · evaluatesOn")
    + sheetOf([["evalOn", rec.evalOn]]);
}

function paneParams(e, rec) {
  const p = rec.params || {};
  const rows = Object.keys(p).map((k) => [k, typeof p[k] === "object" ? JSON.stringify(p[k]) : String(p[k])]);
  return owlHead("参数", "Value Domain")
    + (rows.length ? sheetOf(rows) : emptyPane("本条无 params。数字住在规则上，不镜像到 Invariant。"));
}

function paneEffect(e, rec) {
  const sm = (files["rules.json"] || {}).stateModel || {};
  return owlHead("裁决", "Effect · LegalRuleML Violation")
    + sheetOf([
      ["states", (rec.states || []).join(" / ")],
      ["catalog.states", (sm.states || []).join(" / ")],
      ["kind", sm.kind],
    ]);
}

function paneProhib(e, rec) {
  return owlHead("禁止", "LegalRuleML Prohibition")
    + pillsOf(rec.illegal || []);
}

function paneEnforce(e, rec) {
  const sm = (files["rules.json"] || {}).stateModel || {};
  return owlHead("执行", "Enforcement")
    + sheetOf([
      ["enforce", rec.enforce],
      ["structure", sm.structure],
      ["kind", sm.kind],
      ["illegal(catalog)", (sm.illegal || []).join(" · ")],
    ])
    + `<p class="owl-lead">结构冻结。每拍只评 pass/fail/n/a，不得豁免 hard 与 roe。</p>`;
}

function paneFn(e, rec) {
  const fn = e.type === "FunctionBind" ? rec : functionById(rec.bind);
  return owlHead("职能", "boundTo some FunctionBind")
    + sheetOf([
      ["bind", rec.bind ? jumpBtn("FunctionBind", rec.bind) : (rec.id || "")],
      ["title", fn && fn.title],
      ["give", fn && fn.give ? jumpBtn("Class", fn.give) : ""],
    ]);
}

function paneScope(e, rec) {
  const note = {
    sense: "≈ JDL L1 航迹",
    know: "≈ JDL L2 身份",
    decide: "分配",
    inner: "内环球面码",
    roe: "过门",
    inject: "对抗注入",
    tool: "非主张工具",
  };
  return owlHead("范围", "Scope")
    + sheetOf([["scope", rec.scope], ["解读", note[rec.scope] || ""]]);
}

function paneInput(e, rec) {
  const fn = e.type === "FunctionBind" ? rec : functionById(rec.bind);
  const eats = (fn && fn.eats) || [];
  return owlHead("输入", "Input · FunctionBind.eats")
    + (eats.length ? pillsOf(eats) : emptyPane("职能未登记 eats。"));
}

function paneOutput(e, rec) {
  const fn = e.type === "FunctionBind" ? rec : functionById(rec.bind);
  return owlHead("输出", "Output · FunctionBind.give")
    + sheetOf([["give", fn && fn.give ? jumpBtn("Class", fn.give) : ""]]);
}

function paneBinding(e, rec) {
  const alg = files["algorithms.json"] || {};
  const fns = alg.functions || [];
  const id = e.type === "Algorithm" ? e.id : rec.id;
  const hits = fns.filter((f) => (f.bound || []).includes(id) || (f.toolsNotClaim || []).includes(id) || f.id === rec.bind);
  let html = owlHead("绑定", "FunctionBind.bound / toolsNotClaim");
  if (e.type === "Algorithm") {
    html += sheetOf([["bind", jumpBtn("FunctionBind", rec.bind)]]);
  }
  if (hits.length) {
    html += `<table class="child-tbl"><thead><tr><th>职能</th><th>bound</th><th>toolsNotClaim</th></tr></thead><tbody>`
      + hits.map((f) => `<tr><td><button type="button" data-jump="FunctionBind:${f.id}">${f.id}</button></td><td>${(f.bound || []).join(" ")}</td><td>${(f.toolsNotClaim || []).join(" ")}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  const inner = alg.innerLoop || {};
  html += sheetOf([["innerLoop", inner.name || ""], ["inner.scope", inner.scope || ""]]);
  return html;
}

function paneClaim(e, rec) {
  return owlHead("主张", "claims value false")
    + sheetOf([
      ["claim", rec.claim === false ? "false" : String(rec.claim)],
      ["公理", "Algorithm ⊑ claims value false"],
    ])
    + `<p class="owl-lead">权重不进 40ms 快照。MAPPO/HAPPO/QMIX 只能 scope=tool。</p>`;
}

function paneImpl(e, rec) {
  const fn = e.type === "FunctionBind" ? rec : functionById(rec.bind);
  const cat = files["catalog.json"] || {};
  return owlHead("实现", "Implementation")
    + sheetOf([
      ["impl", rec.impl],
      ["paper", rec.paper],
      ["license", rec.license],
      ["runtime", fn && fn.runtime],
      ["catalog.runtime.source", (cat.runtime || {}).source],
      ["catalog.runtime.knowledge", (cat.runtime || {}).knowledge],
    ]);
}

function paneWho(e, rec) {
  const rows = [];
  if (rec.target) rows.push(["对象", rec.target]);
  if (rec.blue) rows.push(["执行者", rec.blue]);
  if (rec.red) rows.push(["目标", rec.red]);
  if (rec.agent) rows.push(["Agent", rec.agent]);
  if (rec.src) rows.push(["报告者", rec.src]);
  if (rec.writer) rows.push(["写入者", rec.writer]);
  if (e.type === "Permit" || e.type === "FunctionBind") {
    rows.push(["签发者", ((files["shells.json"] || {}).permitWriter) || "monitor.gate"]);
  }
  if (rec.human != null) rows.push(["人在环", rec.human ? "是" : "否"]);
  if (e.type === "Monitor") rows.push(["角色", "授权监视，不是壳体"]);
  return owlHead("谁", "C-BML Who") + (rows.length ? sheetOf(rows) : emptyPane("本产品未登记 Who。"));
}

function paneWhat(e, rec) {
  return owlHead("何事", "C-BML What · ACTION")
    + sheetOf([
      ["type", e.type],
      ["title", rec.title],
      ["task", rec.task],
      ["give", rec.give],
      ["kind", rec.kind],
      ["product", rec.product],
    ]);
}

function paneWhen(e, rec) {
  return owlHead("何时", "C-BML When")
    + sheetOf([
      ["t", rec.t != null ? String(rec.t) : ""],
      ["k", rec.k != null ? String(rec.k) : ""],
      ["evalOn", rec.evalOn],
      ["runtime", rec.runtime],
    ]);
}

function paneWhere(e, rec) {
  const zones = (files["graph.json"] || {}).zoneActs || {};
  return owlHead("何地", "C-BML Where")
    + `<p class="owl-lead">位置引用战场对象，不内嵌体积。</p>`
    + sheetOf([
      ["p", rec.p ? rec.p.join(", ") : ""],
      ["layer", rec.layer],
      ["s", rec.s != null ? String(rec.s) : ""],
      ["zone 释义", Object.entries(zones).map(([k, v]) => `${k}=${v}`).join(" · ")],
    ]);
}

function paneWhy(e, rec) {
  return owlHead("为何", "C-BML Why")
    + sheetOf([
      ["intent", rec.intent],
      ["task", rec.task],
      ["why", rec.why],
      ["listable", rec.listable == null ? "" : String(rec.listable)],
      ["notClaim", rec.notClaim],
      ["not", rec.not],
    ]);
}

function paneReporting(e, rec) {
  return owlHead("出处", "JC3IEDM REPORTING-DATA")
    + sheetOf([
      ["src", rec.src],
      ["vis", rec.vis],
      ["p_belief", rec.p_belief != null ? String(rec.p_belief) : ""],
    ]);
}

function paneAuthority(e, rec) {
  const gate = functionById("gate") || {};
  const writer = (files["shells.json"] || {}).permitWriter;
  return owlHead("授权", "Authority")
    + sheetOf([
      ["permitWriter", writer],
      ["gate.human", gate.human ? "true" : ""],
      ["gate.give", gate.give],
      ["ok", rec.ok == null ? "" : String(rec.ok)],
      ["why", rec.why],
    ])
    + `<p class="owl-lead">Permit 只由 Monitor 写。饱和不是开门理由。</p>`;
}

function paneAType(e, rec) {
  return owlHead("型谱", "bindsPlatformType")
    + sheetOf([["bindsPlatformType", rec.bindsPlatformType ? jumpBtn("PlatformType", rec.bindsPlatformType) : ""]]);
}

function paneACap(e, rec) {
  const stages = rec.stages || ((files["shells.json"] || {}).templates || []).flatMap((t) => t.stages || []);
  const reads = [...new Set(stages.flatMap((s) => s.reads || []))];
  const writes = [...new Set(stages.flatMap((s) => s.writes || []))];
  return owlHead("能力", "IEEE 1872 Capability")
    + sheetOf([["reads", reads.join("、")], ["writes", writes.join("、")]]);
}

function paneStage(e, rec) {
  return paneAct(e, rec);
}

function paneFlow(e, rec) {
  const stages = rec.stages || [];
  let html = owlHead("读写", "reads / writes some InformationEntity");
  html += `<p class="owl-lead">信息流，不是 API 签名。</p>`;
  if (!stages.length) return html + emptyPane("无 Stage。");
  return html
    + `<table class="child-tbl"><thead><tr><th>段</th><th>reads</th><th>writes</th></tr></thead><tbody>`
    + stages.map((s) => `<tr><td>${s.name}</td><td>${(s.reads || []).join("、")}</td><td>${(s.writes || []).join("、")}</td></tr>`).join("")
    + `</tbody></table>`;
}

function paneRole(e, rec) {
  const writer = (files["shells.json"] || {}).permitWriter;
  return owlHead("角色", "BFO Role")
    + sheetOf([
      ["本条 rdf:type", e.type],
      ["permitWriter", writer],
      ["公理", "Role ⊥ InformationEntity ； Role ⊥ ShellTemplate"],
    ])
    + `<p class="owl-lead">Monitor 是角色，不是 Agent 壳体，不是签发者物体。</p>`;
}

function paneNoWrite(e, rec) {
  const stages = rec.stages || [];
  const bad = stages.filter((s) => (s.writes || []).includes("Permit"));
  return owlHead("禁写", "shell-no-permit")
    + sheetOf([
      ["writes 含 Permit", bad.length ? bad.map((s) => s.name).join("、") : "无（合规）"],
      ["公理", "ShellTemplate ⊑ writes only (not Permit)"],
    ]);
}

function paneSource(e, rec) {
  const tools = ((files["algorithms.json"] || {}).tools || []).filter((t) => t.bind === "read");
  const src = files["sources.json"] || {};
  let html = owlHead("来源", "INFORMATION-SOURCE")
    + sheetOf([
      ["id", e.id],
      ["type", e.type],
      ["source", rec.source],
      ["wasGeneratedBy", rec.wasGeneratedBy],
      ["used", rec.used],
    ]);
  html += owlHead("维护算法", "bind = read")
    + (tools.length
      ? `<table class="child-tbl"><thead><tr><th>算法</th><th>title</th></tr></thead><tbody>${tools.map((t) => `<tr><td><button type="button" data-jump="Algorithm:${t.id}">${t.id}</button></td><td>${t.title || ""}</td></tr>`).join("")}</tbody></table>`
      : emptyPane("无 read 算法。"));
  if ((src.items || []).length) {
    html += owlHead("开源登记", "sources.json")
      + `<table class="child-tbl"><thead><tr><th>id</th><th>license</th><th>what</th></tr></thead><tbody>`
      + src.items.map((it) => `<tr><td>${it.id}</td><td>${it.license || ""}</td><td>${it.what || ""}</td></tr>`).join("")
      + `</tbody></table>`;
  }
  return html;
}

function paneNumeric(e, rec) {
  return owlHead("数字特征", "云模型 Ex / En / He")
    + sheetOf([
      ["Ex 期望", rec.Ex != null ? String(rec.Ex) : ""],
      ["En 熵", rec.En != null ? String(rec.En) : ""],
      ["He 超熵", rec.He != null ? String(rec.He) : ""],
    ])
    + `<p class="owl-lead">不确定表征，不是神经网络权重，不是占领区。</p>`;
}

function paneQuality(e, rec) {
  return owlHead("质量", "BFO Quality · hasState")
    + sheetOf([
      ["依存", `${e.id} 是 Quality，inheresIn 态势对象`],
      ["公理", "Entity ⊑ hasState some Quality"],
    ]);
}

function paneProv(e, rec) {
  return owlHead("谱系", "PROV-O")
    + sheetOf([
      ["wasGeneratedBy", rec.wasGeneratedBy || "cloud_shadow / iff_logic"],
      ["used", rec.used || "Track / Identity / G(t)"],
      ["note", rec.note],
    ])
    + `<p class="owl-lead">影与候选在运行时生成，封皮只存 CloudDef 名义。Shadow 实例不入库。</p>`;
}

function paneGrain(e) {
  const rule = ((files["rules.json"] || {}).rules || []).find((r) => r.id === "fusion_split");
  return owlHead("粒度", "Granularity")
    + sheetOf([["fusion_split", rule ? rule.text : "G(t) 断则队级融合不可用，只用局部 p"]])
    + `<p class="owl-lead">队影 vs 局影。链断不把队级当局部。</p>`;
}

function paneVis(e, rec) {
  return owlHead("可见性", "Visibility")
    + sheetOf([
      ["vis", rec.vis],
      ["码", "team | local | any"],
      ["金线", "与 Needline G(t) 同一条：岸上数据链通否决定队级可见性"],
    ]);
}

function paneBody(tabId, e) {
  return renderKnowledgePane(knowledgeModel, tabId, e);
}

function inspect(e) {
  if (!e) {
    inspectEl.innerHTML = `<p class="insp-empty">从左侧导航选择一条类或个体。</p>`;
    return;
  }
  ensureTab(e);
  const rec = recordOf(e);
  const m = metaOf(e);
  const typeName = typeNameOf(e);
  const chain = chainOf(typeName).slice().reverse();
  const gid = inspectGroupOf(e);
  const g = GROUPS.find((x) => x.id === gid);
  const crumbParts = [`<span>${g ? g.lab : "领域本体"}</span>`];
  chain.forEach((id, i) => {
    const last = e.type === "Class" && i === chain.length - 1;
    crumbParts.push(last
      ? `<span>/</span><strong>${escapeHtml(labelOf(id))}</strong>`
      : classById(id) ? `<span>/</span><button type="button" data-jump="Class:${escapeHtml(classById(id).iri || id)}">${escapeHtml(labelOf(id))}</button>`
        : `<span>/</span><span>${escapeHtml(id)}（未解析）</span>`);
  });
  if (e.type !== "Class") crumbParts.push(`<span>/</span><strong>${escapeHtml(e.id)}</strong>`);
  if (chain.some(id => (classById(id)?.subClassOf || []).length > 1)) crumbParts.push(`<span>（已声明路径之一）</span>`);
  const crumb = crumbParts.join("");
  const title = e.type === "Class" ? (rec.label || labelOf(e.id)) : e.id;
  const tabs = tabsOf(e).map(([id, lab, owl]) => `<button type="button" data-tab="${id}" class="${tab === id ? "on" : ""}">${lab}<span class="owl">${owl}</span></button>`).join("");
  const body = paneBody(tab, e, rec, m);
  const cat = files["catalog.json"] || {};
  const kindLab = e.type === "Class" ? "owl:Class" : "owl:NamedIndividual";
  inspectEl.innerHTML = `
    <div class="stage-head">
      <div>
        <nav class="crumb">${crumb}</nav>
        <h2 class="stage-title">${escapeHtml(title)}</h2>
      </div>
      <p class="stage-meta">${escapeHtml(g?.lab || "领域本体")}<br>${kindLab} · ${escapeHtml(e.id)} · v${escapeHtml(cat.version || "1")} · ${escapeHtml(storeInfo.kind || "store")}</p>
    </div>
    <div class="tab-bar">
      <button type="button" class="tab-arrow" data-tab-scroll="left" title="向左" aria-label="向左">
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 3.2 5.2 8 10 12.8"/></svg>
      </button>
      <nav class="tabs">${tabs}</nav>
      <button type="button" class="tab-arrow" data-tab-scroll="right" title="向右" aria-label="向右">
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3.2 10.8 8 6 12.8"/></svg>
      </button>
    </div>
    ${body}
  `;
  inspectEl.querySelectorAll("canvas[data-glyph]").forEach((cv) => paintGlyph(cv, e, true));
  bindTabStrip();
}

function paneAgentBoundary(e, rec) {
  const values = [
    ["functionDomain", rec.functionDomain || rec.functions],
    ["timeScale", rec.timeScale || rec.timeScales],
    ["humanControlMode", rec.humanControlMode || rec.controlMode],
    ["connectivityMode", rec.connectivityMode || rec.connectionMode],
    ["failureContainment", rec.failureContainment || rec.failureContainmentBoundary],
    ["safeFallbackPolicy", rec.safeFallbackPolicy || rec.safeFallback],
    ["reversibility", rec.reversibility || rec.reversibilityClass],
    ["authorizationTtl", rec.authorizationTtlSeconds != null ? `${rec.authorizationTtlSeconds} s` : rec.authorizationTtl],
    ["lifecycleState", rec.lifecycleState],
    ["validTime", rec.validTime ? JSON.stringify(rec.validTime) : ""],
  ].map(([name, value]) => [name, Array.isArray(value) ? value.join(" · ") : value]).filter(([, value]) => value != null && value !== "");
  let html = owlHead("作战责任边界", "timeScale × permission × failureContainment")
    + `<p class="owl-lead">功能域可以组合；授权与效果执行必须分别受 Gate 和短寿命安全回退约束。</p>`;
  return html + (values.length ? sheetOf(values) : emptyPane("该 AgentInstance 尚未登记时间尺度、权限和失败隔离字段。"));
}

function questionLink(type, id) {
  const hit = type === "Class" ? classById(id) : findEntity(type, id);
  if (!hit) return `<span class="review-ref">${escapeHtml(id)}（扩展定义）</span>`;
  return `<button type="button" class="review-link" data-jump="${escapeHtml(type)}:${escapeHtml(id)}">${escapeHtml(id)}</button>`;
}

function renderQuestionReview() {
  const done = COMPETENCY_QUESTIONS.filter((q) => q.tone === "done").length;
  const partial = COMPETENCY_QUESTIONS.filter((q) => q.tone === "partial").length;
  const pending = COMPETENCY_QUESTIONS.filter((q) => q.tone === "pending").length;
  const cards = COMPETENCY_QUESTIONS.map((q) => {
    const links = q.links.map(([type, id]) => questionLink(type, id)).filter(Boolean).join(" ");
    return `<article class="review-card ${q.tone}">
      <div class="review-card-head"><span class="review-no">${String(q.no).padStart(2, "0")}</span><span class="review-status ${q.tone}">${escapeHtml(q.status)}</span></div>
      <h3>${escapeHtml(q.question)}</h3>
      <p class="review-evidence"><strong>当前证据：</strong>${escapeHtml(q.evidence)}</p>
      ${links ? `<div class="review-links"><span>查看对象：</span>${links}</div>` : ""}
    </article>`;
  }).join("");
  inspectEl.innerHTML = `<div class="review-head">
    <div><p class="crumb"><span>Agent</span><span>/</span><strong>10项问题落实</strong></p><h2 class="stage-title">10项能力问题 · 实施核对</h2></div>
    <p class="stage-meta">${done} 项已落实 · ${partial} 项部分落实 · ${pending} 项待补齐<br>依据 agent-modeling-research.md §11</p>
  </div>
  <p class="review-lead">这里把研究文档中的十个 competency questions 映射到当前项目的本体、运行时和验证证据。点击“查看对象”可回到知识库对象页；“部分落实”表示数据结构已有，但完整的运行时投影或重放界面仍需继续补齐。</p>
  <div class="review-grid">${cards}</div>`;
}

function tabStrip() {
  return inspectEl.querySelector(".tabs");
}

function syncTabArrows() {
  const strip = tabStrip();
  const left = inspectEl.querySelector("[data-tab-scroll='left']");
  const right = inspectEl.querySelector("[data-tab-scroll='right']");
  if (!strip || !left || !right) return;
  const max = Math.max(0, strip.scrollWidth - strip.clientWidth);
  left.disabled = strip.scrollLeft <= 1;
  right.disabled = strip.scrollLeft >= max - 1;
}

function scrollTabs(dir) {
  const strip = tabStrip();
  if (!strip) return;
  const step = Math.max(140, Math.round(strip.clientWidth * 0.7));
  strip.scrollBy({ left: dir === "left" ? -step : step, behavior: "smooth" });
}

function bindTabStrip() {
  const strip = tabStrip();
  if (!strip) return;
  strip.addEventListener("scroll", syncTabArrows, { passive: true });
  const on = strip.querySelector("button.on");
  if (on) strip.scrollLeft = Math.max(0, on.offsetLeft - 16);
  requestAnimationFrame(syncTabArrows);
}

function applyNavRail() {
  document.body.classList.toggle("nav-rail", navRail);
  const btn = document.getElementById("nav-toggle");
  if (!btn) return;
  btn.title = navRail ? "展开导航" : "收起导航";
  btn.setAttribute("aria-expanded", navRail ? "false" : "true");
  btn.setAttribute("aria-label", btn.title);
}

function toggleNavRail() {
  navRail = !navRail;
  localStorage.setItem("kb-nav-rail", navRail ? "1" : "0");
  paint();
}

function paint() {
  applyNavRail();
  renderNav();
  countEl.textContent = `${abox.length} 个对象`;
  if (reviewOpen) {
    renderQuestionReview();
    return;
  }
  const current = currentEntry();
  inspect(current);
  if (current) writeHash(current);
}

function selectClass(id, keepPick) {
  const entry = classEntry(id || "Entity");
  if (!entry) {
    inspectEl.innerHTML = `<p class="insp-empty">类标识不存在或存在歧义，请使用完整 IRI。</p>`;
    return;
  }
  selectedClass = entry.id;
  selectedClassIri = entry.iri;
  groupOpen = isAgentExtensionClass(selectedClass) ? "Agent" : "onto";
  const agentSection = agentSectionForClass(selectedClass);
  if (groupOpen === "Agent" && agentSection !== "other") agentSectionOpen.add(agentSection);
  const topicGroup = topicGroupForClass(selectedClass);
  if (topicGroup) {
    topicGroupOpen.add(topicGroup.id);
    const topicSection = topicGroup.sections.find((section) => section.classes.includes(selectedClass));
    if (topicSection) topicSectionOpen.add(`${topicGroup.id}:${topicSection.id}`);
  }
  ensureOpenPath(selectedClass);
  if (!keepPick) {
    picked = null;
    tab = "ann";
  }
  const e = currentEntry();
  if (e) writeHash(e);
  paint();
}

function selectIndividual(type, id) {
  const e = findEntity(type, id);
  if (!e) return;
  picked = keyOf(e);
  pickedIri = e.iri;
  selectedClass = e.type;
  selectedClassIri = knowledgeModel.resolveClass(e.type)?.iri || null;
  groupOpen = groupForType(e.type);
  const topicGroup = topicGroupForClass(e.type);
  if (topicGroup) {
    topicGroupOpen.add(topicGroup.id);
    const section = topicGroup.sections.find(s => s.classes.includes(e.type));
    if (section) topicSectionOpen.add(`${topicGroup.id}:${section.id}`);
  }
  ensureOpenPath(selectedClass);
  if (e.type === "Membership") {
    const rid = recordOf(e).region;
    if (rid) bomOpen.add(rid);
  } else {
    const mem = abox.find((m) => m.type === "Membership" && recordOf(m).of === e.id);
    if (mem) {
      const rid = recordOf(mem).region;
      if (rid) bomOpen.add(rid);
    }
  }
  tab = tabsOf(e)[0][0];
  writeHash(e);
  paint();
}

function jump(spec) {
  const i = spec.indexOf(":");
  const type = spec.slice(0, i);
  const id = spec.slice(i + 1);
  if (type === "Class") {
    query = "";
    qEl.value = "";
    selectClass(id);
    return;
  }
  const e = findEntity(type, id);
  if (e) {
    query = "";
    qEl.value = "";
    selectIndividual(type, id);
    return;
  }
  if (classById(id)) {
    query = "";
    qEl.value = "";
    selectClass(id);
  }
}

function applyHash() {
  let h;
  try {
    h = decodeURIComponent((location.hash || "").replace(/^#/, ""));
  } catch {
    renderNav();
    inspectEl.innerHTML = `<p class="insp-empty">地址中的对象标识编码无效。</p>`;
    return;
  }
  if (!h) return;
  let hits;
  if (h.startsWith("iri/") || /^[a-z][a-z0-9+.-]*:/i.test(h)) {
    const iri = h.startsWith("iri/") ? h.slice(4) : h;
    hits = resolveIriEntries(iri);
  } else {
    const i = h.indexOf("/");
    if (i < 0) hits = entries.filter(e => e.id === h);
    else {
      const type = h.slice(0, i), id = h.slice(i + 1);
      hits = entries.filter(e => e.type === type && e.id === id);
    }
  }
  if (hits.length !== 1) {
    renderNav();
    const message = hits.length ? "对象短名存在歧义，请使用完整 IRI。" : "没有找到此对象；请从导航选择已有对象。";
    const candidates = hits.map(e => `<li><button type="button" class="semantic-link" data-jump="${escapeHtml(e.type)}:${escapeHtml(e.iri)}">${escapeHtml(e.type)} / ${escapeHtml(e.id)}</button> <code>${escapeHtml(e.iri)}</code></li>`).join("");
    inspectEl.innerHTML = `<p class="insp-empty">${message}</p><pre class="semantic-json">${escapeHtml(h)}</pre>${candidates ? `<ul>${candidates}</ul>` : ""}`;
    return;
  }
  const hit = hits[0];
  if (hit.type === "Class") selectClass(hit.iri || hit.id);
  else selectIndividual(hit.type, hit.iri || hit.id);
}

try {
  const boot = await loadBoot();
  storeInfo = boot.store || storeInfo;
  const catalog = boot.files["catalog.json"] || boot.package;
  const inventory = boot.inventory;
  lexicon = boot.files["lexicon.json"] || lexicon;
  tbox = boot.files["tbox.json"] || tbox;
  dictionary = boot.files["dictionary.json"] || dictionary;
  Object.assign(files, boot.files || {});
  files["catalog.json"] = catalog;
  // Keep source documents intact; only the legacy navigation projection is
  // merged/normalized below.  The semantic model reads the original terms.
  files["tbox.json"] = structuredClone(tbox);
  files["dictionary.json"] = dictionary;
  const baseSchema = files["schema.json"] || schemaDoc;
  const agentSchema = files["schema.agent.extension.json"] || {};
  schemaDoc = {
    ...baseSchema,
    $defs: { ...(baseSchema.$defs || {}), ...(agentSchema.$defs || {}) },
  };
  entries = inventory.entries || [];
  mergeAgentExtension();
  knowledgeModel = createKnowledgeModel(files, entries);
  entries = knowledgeModel.entries;
  abox = entries.filter((e) => e.type !== "Class");
  pkgLab.textContent = `${catalog.id} · v${catalog.version}`;
  const storeEl = document.getElementById("store-lab");
  if (storeEl) storeEl.textContent = `${storeInfo.kind} · ${storeInfo.dbname || "coast_raid_k"}`;
  countEl.textContent = `${abox.length} 个对象`;
  // Restore a deep link before the first paint. paint() commits the current
  // selection to location.hash, so painting first would overwrite a link such
  // as #Class/Stage with the default Sensor selection.
  if (location.hash) applyHash();
  else paint();
  applyNavRail();
  document.getElementById("nav-toggle").addEventListener("click", toggleNavRail);
  document.getElementById("questions-btn").addEventListener("click", () => {
    reviewOpen = !reviewOpen;
    if (reviewOpen) {
      renderQuestionReview();
    } else {
      paint();
    }
  });
  navViewsEl.addEventListener("click", (ev) => {
    const view = ev.target.closest("[data-nav-view]");
    if (!view) return;
    navView = view.dataset.navView === "module" ? "module" : "topic";
    localStorage.setItem("kb-nav-view", navView);
    reviewOpen = false;
    renderNav();
  });
  groupsEl.addEventListener("click", (ev) => {
    const topicGroup = ev.target.closest("button[data-topic-group]");
    if (topicGroup) {
      const id = topicGroup.dataset.topicGroup;
      if (topicGroupOpen.has(id)) topicGroupOpen.delete(id);
      else topicGroupOpen.add(id);
      renderNav();
      return;
    }
    const topicSection = ev.target.closest("button[data-topic-section]");
    if (topicSection) {
      const id = topicSection.dataset.topicSection;
      if (topicSectionOpen.has(id)) topicSectionOpen.delete(id);
      else topicSectionOpen.add(id);
      renderNav();
      return;
    }
    const head = ev.target.closest("[data-group]");
    if (head) {
      const gid = head.dataset.group;
      reviewOpen = false;
      if (navRail) {
        navRail = false;
        localStorage.setItem("kb-nav-rail", "0");
        groupOpen = gid;
      } else {
        groupOpen = groupOpen === gid ? "" : gid;
      }
      paint();
      return;
    }
    const agentSection = ev.target.closest("button[data-agent-section]");
    if (agentSection) {
      const id = agentSection.dataset.agentSection;
      if (agentSectionOpen.has(id)) agentSectionOpen.delete(id);
      else agentSectionOpen.add(id);
      renderNav();
      return;
    }
    const tog = ev.target.closest("button[data-toggle]");
    if (tog && !tog.disabled) {
      toggleOpen(tog.dataset.toggle);
      renderNav();
      return;
    }
    const bom = ev.target.closest("button[data-bom]");
    if (bom && !bom.disabled) {
      const id = bom.dataset.bom;
      if (bomOpen.has(id)) bomOpen.delete(id);
      else bomOpen.add(id);
      renderNav();
      return;
    }
    const cls = ev.target.closest("button[data-class]");
    if (cls) {
      reviewOpen = false;
      query = "";
      qEl.value = "";
      selectClass(cls.dataset.iri || cls.dataset.class);
      return;
    }
    const item = ev.target.closest("button[data-id]");
    if (!item) return;
    reviewOpen = false;
    const k = item.dataset.id;
    const i = k.indexOf(":");
    selectIndividual(k.slice(0, i), item.dataset.iri || k.slice(i + 1));
  });
  inspectEl.addEventListener("click", (ev) => {
    const scrollBtn = ev.target.closest("[data-tab-scroll]");
    if (scrollBtn && !scrollBtn.disabled) {
      scrollTabs(scrollBtn.dataset.tabScroll);
      return;
    }
    const tabBtn = ev.target.closest("button[data-tab]");
    if (tabBtn) {
      tab = tabBtn.dataset.tab;
      const current = currentEntry();
      inspect(current);
      return;
    }
    const btn = ev.target.closest("button[data-jump]");
    if (btn) {
      reviewOpen = false;
      jump(btn.dataset.jump);
    }
  });
  qEl.addEventListener("input", () => {
    query = qEl.value.trim().toLowerCase();
    paint();
  });
  window.addEventListener("hashchange", applyHash);
  window.addEventListener("resize", syncTabArrows);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && document.activeElement !== qEl) {
      ev.preventDefault();
      if (navRail) {
        navRail = false;
        localStorage.setItem("kb-nav-rail", "0");
        applyNavRail();
        renderNav();
      }
      qEl.focus();
    }
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    const rows = listed();
    if (!rows.length) return;
    ev.preventDefault();
    const idx = rows.findIndex(e => pickedIri ? e.iri === pickedIri : keyOf(e) === picked);
    const next = idx < 0 ? (ev.key === "ArrowDown" ? 0 : rows.length - 1)
      : ev.key === "ArrowDown" ? Math.min(rows.length - 1, idx + 1) : Math.max(0, idx - 1);
    const e = rows[next];
    selectIndividual(e.type, e.iri || e.id);
  });
} catch (err) {
  inspectEl.innerHTML = `<p class="insp-empty">读库失败：${escapeHtml(err.message)}。请检查本地服务与当前知识库发布目录。</p>`;
}
