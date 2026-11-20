/** Shared theater: layered envelopes, finite waves, confidence gate. No person auto-kill. */

import { CloudKB } from "./knowledge.js";
import { RuntimeLedger, agentIri, runIri } from "./agent-runtime.js";

export const TAU = 0.85;
export const N_MAX = { hi: 3, mid: 3, lo: 6 };
export const K_MAX = 4;
export const M_SAM0 = 4;
/** Gate inner bound as a fraction of SAM.r. Edge band is SAM_RIM·R … R. */
export const SAM_RIM = 0.82;
/** Physics tick used by cop.js accumulator. */
export const TICK_S = 0.04;
export const HOST_LO_KMH = 720;
export const UNK_LO_KMH = 480;
export const BLUE_HI_KMH = 280;
export const BLUE_MID_KMH = 320;
/** Lateral formation keep for red lo; does not steal inbound ground speed. */
export const FORM_LO_LAT_KMH = 90;
let RUN_SEQUENCE = 0;

export const BMD = { c: [2.15, 0.05, 2.35], r: 3.15, z0: 0.05 };
export const SAM = { c: [0.35, 0.05, 0.85], r: 2.05, z0: 0.05 };
export const SHIP = { c: [-2.55, 0.12, -3.05], r: 1.2 };
/** Bow seaward. 000° = inland. Same axis as blue default. */
export const SHIP_HDG = 180;
export const C2 = [3.55, 0.55, 3.15];
export const COR = { x0: -3.35, x1: 0.95, z0: -4.15, z1: -0.55, y0: 0.06, y1: 0.42 };
/** Shore search and SAM fire-control. Distinct from launchers. */
export const RADAR = {
  shore: [1.62, 0.2, 0.18],
  sam: [0.92, 0.18, 1.22],
};
/** Shore search coverage: fan seaward from 岸搜. Not an engagement bubble. */
export const SEARCH = { c: [1.62, 0.2, 0.18], r: 4.4, az: Math.PI, half: (58 * Math.PI) / 180 };
/** Corridor jammer. Bloom only while emitting. */
export const JAM = { c: [-1.85, 0.22, -2.55], r: 1.55 };

export function dist(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

export function inHemi(p, b) {
  if (p[1] < b.z0) return false;
  return dist(p, b.c) <= b.r;
}

export function inShip(p) {
  return dist(p, SHIP.c) <= SHIP.r;
}

export function inCor(p) {
  return p[0] >= COR.x0 && p[0] <= COR.x1 && p[2] >= COR.z0 && p[2] <= COR.z1 && p[1] >= COR.y0 && p[1] <= COR.y1;
}

export function clampCor(p) {
  return [
    Math.min(COR.x1, Math.max(COR.x0, p[0])),
    Math.min(COR.y1, Math.max(COR.y0, p[1])),
    Math.min(COR.z1, Math.max(COR.z0, p[2])),
  ];
}

export function toRim(p) {
  const d = dist(p, SAM.c);
  const rim = SAM.r * SAM_RIM;
  if (d <= rim) return { km: 0, label: "区内" };
  return { km: d - rim, label: `${(d - rim).toFixed(1)} km` };
}

export function zoneOf(p) {
  if (inHemi(p, SAM)) {
    const d = dist(p, SAM.c) / SAM.r;
    if (d > SAM_RIM) return "edge";
    return "in";
  }
  if (inShip(p)) return "ship";
  if (inCor(p)) return "cor";
  return "out";
}

function fibonacci(k) {
  const pts = [];
  const offset = 2 / k;
  const inc = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < k; i += 1) {
    const y = i * offset - 1 + offset / 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = i * inc;
    pts.push([Math.cos(phi) * r, y, Math.sin(phi) * r]);
  }
  return pts;
}

export function sphereSlots(center, radius, k, squashY) {
  return fibonacci(k).map((u) => [
    center[0] + radius * u[0],
    center[1] + radius * u[1] * squashY,
    center[2] + radius * u[2],
  ]);
}

function assignError(pts, slots) {
  if (!pts.length || !slots.length) return { err: 0, map: [] };
  const used = new Set();
  const map = [];
  let err = 0;
  for (const p of pts) {
    let best = Infinity;
    let j0 = 0;
    for (let j = 0; j < slots.length; j += 1) {
      if (used.has(j)) continue;
      const d = dist(p, slots[j]);
      if (d < best) {
        best = d;
        j0 = j;
      }
    }
    used.add(j0);
    map.push(j0);
    err += best;
  }
  return { err: err / pts.length, map };
}

function lerp3(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Station on the bubble rim, facing `toward`, at layer altitude. Fan with yaw. */
function stationOnRim(b, y, toward, yaw = 0) {
  const maxR = Math.sqrt(Math.max(0.4, (b.r * 0.86) ** 2 - (y - b.c[1]) ** 2));
  let dx = toward[0] - b.c[0];
  let dz = toward[2] - b.c[2];
  const h = Math.hypot(dx, dz) || 1;
  dx /= h;
  dz /= h;
  if (yaw) {
    const c = Math.cos(yaw);
    const s = Math.sin(yaw);
    const x = dx * c - dz * s;
    const z = dx * s + dz * c;
    dx = x;
    dz = z;
  }
  return [b.c[0] + dx * maxR, y, b.c[2] + dz * maxR];
}

/** 情报成品：身份、意图、依据。航迹是探测，这一条才是识别判断的输出。 */
export function intelOf(a, jammed = false, pairs = []) {
  const watched = pairs.some((p) => p.red === a.id);
  if (a.side === "blue") {
    return { iden: "我", intent: a.task || "巡逻", src: "编成", kind: "own", file: null };
  }
  if (a.side === "unk") {
    return {
      iden: "不明",
      intent: "身份未判明",
      src: watched ? (jammed ? "机载识别·局部" : "岸搜+机载识别") : jammed ? "局部航迹" : "岸搜·未分类",
      kind: "open",
      file: "unk_lo",
    };
  }
  const z = zoneOf(a.p);
  let src = jammed ? "局部航迹" : "岸搜";
  if (z === "ship") src = jammed ? "舰载·局部" : "舰载+岸搜";
  else if (z === "in" || z === "edge") src = jammed ? "制导·局部" : "岸搜+制导";
  if (watched && !jammed) src += "+盯梢";
  let intent = "贴海突防";
  if (a.gated) intent = "已进入交战";
  else if (z === "in") intent = "进SAM交战区";
  else if (z === "edge") intent = "压沿接近";
  else if (z === "ship") intent = "进舰探测";
  return { iden: "敌", intent, src, kind: "host", file: "host_lo" };
}

export function velFromKmh(kmh, dx, dz) {
  const h = Math.hypot(dx, dz) || 1;
  const mag = (kmh / 3600) * TICK_S;
  return [(dx / h) * mag, 0, (dz / h) * mag];
}

export function groundSpeedKmh(a) {
  if (a.speedKmh) return a.speedKmh;
  const v = a.v || [0, 0, 0];
  return Math.hypot(v[0], v[2]) * (3600 / TICK_S);
}

/** 虚拟装备档案。外形与量级按公开低空突防/小型固定翼平台归纳，不是某一实装型号。 */
export const INTEL_FILES = {
  host_lo: {
    code: "H-17",
    name: "贴海",
    klass: "低空突防无人机",
    img: "./img/intel-host-lo.png?v=2",
    note: "类型为想定编号。高度带对齐走廊；速度取公开亚声速贴海平台量级。载荷只对海/对阵地，授权门保留。",
    combat: [
      ["突防高", "50–400 m"],
      ["突击速", "650–820 km/h"],
      ["RCS估", "0.06–0.12 m²"],
      ["载荷", "对海/对阵地 · 不开对人"],
    ],
  },
  unk_lo: {
    code: "U-?",
    name: "未分型",
    klass: "小型空中平台",
    img: "./img/intel-unk-lo.png",
    note: "岸搜成像不足。外形接近小型飞翼，身份未判明，不进交战清单。",
    combat: [
      ["速度估", "400–600 km/h"],
      ["高度带", "贴海走廊"],
      ["身份", "未判明"],
      ["处置", "只识别，不打"],
    ],
  },
};

/** 战场情报摘要：企图、电磁、威胁、空隙。装备识别只是其中一块。 */
export function briefIntel(state) {
  const lo = living(state.lo);
  const unk = living(state.unk || []);
  const nIn = lo.filter((a) => zoneOf(a.p) === "in").length;
  const nEdge = lo.filter((a) => zoneOf(a.p) === "edge").length;
  const nShip = lo.filter((a) => zoneOf(a.p) === "ship").length;
  const waitP = lo.filter((a) => zoneOf(a.p) === "in" && !a.gated && a.belief < TAU);
  let scheme = "走廊空，本波来袭企图未现";
  if (lo.length) {
    if (nIn) scheme = `第 ${state.k} 波沿走廊贴海压进 SAM，已有 ${nIn} 批进区`;
    else if (nEdge) scheme = `第 ${state.k} 波贴 SAM 区沿，企图进入中层交战区`;
    else if (nShip) scheme = `第 ${state.k} 波先切舰载探测区，尚未进 SAM`;
    else scheme = `第 ${state.k} 波 ${lo.length} 批沿走廊贴海突防，指向岸上防空`;
  } else if (unk.length) {
    scheme = "走廊有不明平台，企图未判明";
  }
  const em = state.jammed
    ? "干扰源开机 · 岸上数据链/舰岸/机站断 · 仅机间 · 队级融合不可用"
    : "电磁相对平静 · 岸上数据链通 · 岸搜+舰载+制导可融合";
  const src = state.jammed ? "局部航迹" : "岸搜+舰载+制导";
  const gaps = [];
  if (unk.length) gaps.push(`${unk.map((a) => a.tn).join("、")} 身份未判明`);
  if (state.jammed) gaps.push("队级信念冻结，局部 p 不能当授权真值");
  if (state.mSam <= 0) gaps.push("SAM 通道满，饱和不是开门理由");
  if (!lo.length && !unk.length) gaps.push("走廊无航迹，敌编成本波未现");
  if (waitP.length) gaps.push(`区内 ${waitP.map((a) => a.tn).join("、")} p 不足，识别未完成`);
  const threats = lo
    .slice()
    .sort((a, b) => toRim(a.p).km - toRim(b.p).km)
    .slice(0, 4)
    .map((a) => {
      const z = zoneOf(a.p);
      const where = z === "in" ? "区内" : z === "edge" ? "区沿" : z === "ship" ? "舰载区" : "走廊";
      return { id: a.id, tn: a.tn, where, rim: toRim(a.p).label, p: a.belief, gated: a.gated };
    });
  const opens = unk.map((a) => ({ id: a.id, tn: a.tn, p: a.belief }));
  return {
    t: (state.t * TICK_S).toFixed(1),
    src,
    area: "岸海交界 · 陆北海上 · 东距 / 距岸 km · 高 m",
    scheme,
    em,
    wave: `${state.k}/${K_MAX}`,
    host: lo.length,
    unk: unk.length,
    gaps: gaps.join("；") || "—",
    threats,
    opens,
  };
}

/** 单批目标诸元。航迹表是探测，这一条才是给指挥用的数据。 */
export function trackSheet(a, state) {
  const intel = intelOf(a, state.jammed, state.pairs || []);
  const v = verdict(a, state.jammed);
  const rec = intel.file ? INTEL_FILES[intel.file] : null;
  const hdg = Math.round((((a.heading || 0) % 360) + 360) % 360);
  return {
    tn: a.tn,
    iden: intel.iden,
    type: rec ? `${rec.code}` : intel.iden,
    klass: rec ? rec.klass : "",
    p: a.belief,
    x: a.p[0],
    y: a.p[1],
    z: a.p[2],
    east: a.p[0],
    shore: a.p[2],
    altM: Math.round(a.p[1] * 1000),
    kmh: groundSpeedKmh(a),
    hdg,
    intent: intel.intent,
    src: intel.src,
    rim: a.side === "blue" ? "—" : toRim(a.p).label,
    act: v.act,
    pos: v.pos,
    file: intel.file,
  };
}

export function verdict(a, jammed = false) {
  if (a.side === "blue") {
    const pos = a.layer === "hi" ? "高空" : "中空";
    if (a.task === "识别") return { pos, act: a.quarryTn ? `识别 ${a.quarryTn}` : "识别" };
    if (a.task === "盯梢") return { pos, act: a.quarryTn ? `盯梢 ${a.quarryTn}` : "盯梢" };
    if (a.task === "压沿") return { pos, act: a.quarryTn ? `压沿 ${a.quarryTn}` : "压沿" };
    if (a.task === "警戒") return { pos, act: "警戒来袭" };
    return { pos, act: "巡逻" };
  }
  if (a.side === "unk") return { pos: "走廊·不明", act: "不明·不打" };
  if (a.gated) return { pos: "SAM区内", act: "已过门" };
  const z = zoneOf(a.p);
  if (z === "in") {
    if (jammed) return { pos: "SAM区内", act: "不打·拒止" };
    if (a.belief >= TAU) return { pos: "SAM区内", act: "待门" };
    return { pos: "SAM区内", act: "不打·p低" };
  }
  if (z === "edge") return { pos: "SAM区沿", act: "跟踪不打" };
  if (z === "ship") return { pos: "舰载区内", act: "跟踪不打" };
  if (z === "cor") return { pos: "走廊·未进SAM", act: "不打" };
  return { pos: "区外", act: "不打" };
}

export function living(list) {
  return list.filter((a) => a.alive);
}

export function createBattle() {
  RUN_SEQUENCE += 1;
  const runId = `run-coast-raid-k-${RUN_SEQUENCE}`;
  const runtime = new RuntimeLedger({ runId, modelId: "coast-raid-k-battle", modelRevision: "battle-runtime-v1" });
  const kb = new CloudKB({ tau: TAU, nMaxLo: N_MAX.lo });
  const seq = { F: 0, H: 0, U: 0 };

  // Service agents are semantic decision subjects even though they do not
  // have a physical marker in the scene.
  runtime.registerAgent({
    id: "agent.fusion.c2",
    side: "blue",
    layer: "service",
    agentType: agentIri("agent-type-fusion"),
    implementedOn: agentIri("platform-c2"),
    roles: [agentIri("role-fusion")],
    capabilities: [agentIri("capability-track-fusion")],
    functionDomain: ["World"],
    timeScale: ["Tactical", "Operational"],
    humanControlMode: "on-the-loop",
    connectivityMode: "online",
    failureContainment: "operational",
    safeFallbackPolicy: "stale-world-local-self",
    reversibility: "recoverable",
    decisionMode: "hybrid",
    autonomyLevel: "delegated",
  });
  runtime.activate("agent.fusion.c2", 0);
  runtime.registerAgent({
    id: "agent.options.planner",
    side: "blue",
    layer: "service",
    agentType: agentIri("agent-type-options-planner"),
    implementedOn: agentIri("platform-c2"),
    roles: [agentIri("role-options-planner")],
    capabilities: [agentIri("capability-plan-and-task")],
    functionDomain: ["Options"],
    timeScale: ["Tactical", "Operational"],
    humanControlMode: "on-the-loop",
    connectivityMode: "online",
    failureContainment: "operational",
    safeFallbackPolicy: "shrink-options-to-local",
    reversibility: "recoverable",
    decisionMode: "hybrid",
    autonomyLevel: "delegated",
  });
  runtime.activate("agent.options.planner", 0);
  runtime.registerAgent({
    id: "agent.monitor.gate",
    side: "blue",
    layer: "service",
    agentType: agentIri("agent-type-gate-monitor"),
    implementedOn: agentIri("platform-c2"),
    roles: [agentIri("role-gate-monitor")],
    capabilities: [agentIri("capability-authorization-evaluation")],
    authorityScope: [agentIri("authority-gate-policy")],
    functionDomain: ["Gate"],
    timeScale: ["Immediate", "Tactical"],
    humanControlMode: "on-the-loop",
    connectivityMode: "online",
    failureContainment: "team",
    safeFallbackPolicy: "deny-new-authorization",
    reversibility: "recoverable",
    decisionMode: "rule",
    autonomyLevel: "supervised",
  });
  runtime.activate("agent.monitor.gate", 0);

  function makeAgent(p, side, layer, wave) {
    const prefix = side === "blue" ? "F" : side === "unk" ? "U" : "H";
    seq[prefix] += 1;
    const agent = {
      id: `${side}-${layer}-${seq[prefix]}`,
      tn: `${prefix}${String(seq[prefix]).padStart(2, "0")}`,
      p: p.slice(),
      side,
      layer,
      wave,
      belief: 0.12,
      gated: false,
      alive: true,
      v: [0, 0, 0],
      heading: side === "red" ? 6 : 180,
      task: side === "blue" ? "巡逻" : "",
      quarry: null,
      quarryTn: "",
      slot: null,
      // Semantic identity and runtime linkage. The compact fields above are
      // retained for the renderer and physics loop.
      iri: agentIri(`${runId}-${side}-${layer}-${seq[prefix]}`),
      runId,
      lifecycleState: "initialized",
      agentType: agentIri(`agent-type-${side}-${layer}`),
      beliefStateIri: agentIri(`belief-state-${side}-${layer}-${seq[prefix]}`),
      observationIris: [],
      beliefAssertionIris: [],
      decisionIris: [],
      actionRequestIris: [],
      authorizationIris: [],
    };
    runtime.registerAgent({
      id: agent.id,
      side,
      layer,
      agentType: agent.agentType,
      implementedOn: `${agentIri("platform")}-${side}-${layer}`,
      roles: [agentIri(`role-${side === "blue" ? (layer === "mid" ? "track-fusion" : "air-watch") : "raid"}`)],
      capabilities: [agentIri(`capability-${side === "blue" ? "observe-decide" : "penetrate"}`)],
      // Physical agents never carry Gate authority.  Blue effectors can
      // execute only an allow result issued by agent.monitor.gate.
      authorityScope: [],
      functionDomain: side === "blue" ? ["Effectors"] : ["Adversary"],
      timeScale: side === "blue" ? ["Immediate"] : ["Immediate", "Tactical"],
      humanControlMode: side === "blue" ? "in-the-loop" : "out-of-the-loop",
      connectivityMode: "online",
      failureContainment: side === "blue" ? "local-action" : "team",
      safeFallbackPolicy: side === "blue" ? "abort-and-safe-state" : "hold-last-safe-state",
      reversibility: side === "blue" ? "irreversible" : "recoverable",
      decisionMode: side === "blue" ? "hybrid" : "reactive",
      autonomyLevel: side === "blue" ? "delegated" : "autonomous",
    });
    runtime.activate(agent.id, 0);
    agent.lifecycleState = "active";
    return agent;
  }

  function seedBlue() {
    const hiC = [BMD.c[0] - 0.2, 2.15, BMD.c[2] - 0.35];
    const midC = [SAM.c[0] + 0.15, 1.15, SAM.c[2] - 0.2];
    const hi = sphereSlots(hiC, 0.72, N_MAX.hi, 1).map((s) => {
      const a = makeAgent(s, "blue", "hi", 0);
      a.speedKmh = BLUE_HI_KMH;
      return a;
    });
    const mid = sphereSlots(midC, 0.62, N_MAX.mid, 1).map((s) => {
      const a = makeAgent(s, "blue", "mid", 0);
      a.speedKmh = BLUE_MID_KMH;
      return a;
    });
    return { hi, mid, hiC, midC };
  }

  function seedUnk() {
    const a = makeAgent([-0.55, 0.18, -3.35], "unk", "lo", 0);
    a.speedKmh = UNK_LO_KMH;
    a.v = velFromKmh(UNK_LO_KMH, 0.001, 0.006);
    a.belief = 0.22;
    a.heading = 8;
    return [a];
  }

  function spawnLo(wave, n) {
    const cz = COR.z0 + 0.38;
    const cx = (COR.x0 + COR.x1) * 0.5 - 0.1 + (wave - 1) * 0.7;
    const cy = (COR.y0 + COR.y1) * 0.5;
    const slots = sphereSlots([cx, cy, cz], 0.52, n, 0.28).map(clampCor);
    return slots.map((s) => {
      const a = makeAgent(s, "red", "lo", wave);
      a.speedKmh = HOST_LO_KMH;
      a.v = velFromKmh(HOST_LO_KMH, 0.002, 0.018);
      return a;
    });
  }

  const state = {
    k: 0,
    jammed: false,
    mSam: M_SAM0,
    reload: 0,
    t: 0,
    caption: "走廊空。等有限波次。",
    events: [],
    ...seedBlue(),
    lo: [],
    unk: seedUnk(),
    gated: 0,
    epsLo: 0,
    epsMid: 0,
    epsHi: 0,
    pairs: [],
    runId,
    runtime: null,
  };

  function syncRuntime() {
    state.runtime = runtime.snapshot();
    state.runtime.run.tick = state.t;
    state.runtime.run.jammed = state.jammed;
  }
  syncRuntime();

  function note(text, kind = "info") {
    state.caption = text;
    state.events.unshift({ t: state.t, text, kind });
    if (state.events.length > 7) state.events.pop();
    runtime.audit(kind, runIri(runId), state.t, { text });
  }

  function recordObservation(a, observationKind, confidence, payload = {}) {
    const kbObservation = kb.observe({
      agentId: "agent.fusion.c2",
      entityId: a.iri,
      kind: observationKind,
      tick: state.t,
      confidence,
      payload,
    });
    const obs = runtime.observe({
      assertedBy: "agent.fusion.c2",
      observedEntity: a.id,
      observationKind,
      tick: state.t,
      sourceIri: agentIri(observationKind === "local-track" ? "sensor-local" : "sensor-shore-fusion"),
      confidence,
      location: a.p,
      payload: { ...payload, kbObservationId: kbObservation.id },
    });
    a.observationIris.push(obs.iri);
    if (a.observationIris.length > 16) a.observationIris.shift();
    return obs;
  }

  function recordBelief(a, probability, observation, proposition = "threatIdentity=hostile") {
    const kbBelief = kb.reviseBelief({
      agentId: "agent.fusion.c2",
      entityId: a.iri,
      probability,
      derivedFrom: observation ? [observation.iri] : [],
      tick: state.t,
      proposition,
    });
    const belief = runtime.believe({
      assertedBy: "agent.fusion.c2",
      aboutIri: a.iri,
      proposition,
      probability,
      confidence: probability,
      derivedFrom: observation ? [observation.iri] : [],
      derivedBy: `cloud-belief-revision:${kbBelief.id}`,
      tick: state.t,
    });
    a.beliefAssertionIris.push(belief.iri);
    if (a.beliefAssertionIris.length > 16) a.beliefAssertionIris.shift();
    a.beliefStateIri = belief.iri;
    return belief;
  }

  function faceToward(a, target) {
    a.heading = (Math.atan2(target[0] - a.p[0], target[2] - a.p[2]) * 180) / Math.PI;
  }

  function faceBlue(a) {
    if (a.quarry) {
      const q =
        living(state.lo).find((r) => r.id === a.quarry) || living(state.unk || []).find((r) => r.id === a.quarry);
      if (q) {
        faceToward(a, q.p);
        return;
      }
    }
    const raid = living(state.lo).length ? living(state.lo) : living(state.unk || []);
    if (raid.length) {
      const c = [0, 0, 0];
      raid.forEach((r) => {
        c[0] += r.p[0];
        c[2] += r.p[2];
      });
      faceToward(a, [c[0] / raid.length, 0, c[2] / raid.length]);
      return;
    }
    a.heading = 180;
  }

  function steerTo(a, s, mag, axes) {
    const dx = s[0] - a.p[0];
    const dy = s[1] - a.p[1];
    const dz = axes === "xy" ? 0 : s[2] - a.p[2];
    const d = Math.hypot(dx, dy, dz);
    if (d < 1e-9) return 0;
    if (d <= mag) {
      a.p[0] = s[0];
      a.p[1] = s[1];
      if (axes !== "xy") a.p[2] = s[2];
      return 0;
    }
    a.p[0] += (dx / d) * mag;
    a.p[1] += (dy / d) * mag;
    if (axes !== "xy") a.p[2] += (dz / d) * mag;
    return axes === "xy" ? Math.hypot(s[0] - a.p[0], s[1] - a.p[1]) : dist(a.p, s);
  }

  function pullToSlots(agents, center, radius, squash, spin = true, dt = TICK_S, kmh = BLUE_MID_KMH, axes = "xyz") {
    const pts = living(agents).filter((a) => !(a.layer === "lo" && a.gated));
    if (!pts.length) return 0;
    const mag = (kmh / 3600) * dt;
    const useOwn = pts.every((a) => a.slot);
    let err = 0;
    if (useOwn) {
      pts.forEach((a) => {
        err += steerTo(a, a.slot, mag, axes);
        if (a.layer !== "lo") faceBlue(a);
      });
      return err / pts.length;
    }
    let slots = sphereSlots(center, radius, pts.length, squash);
    if (pts[0].layer === "lo") slots = slots.map(clampCor);
    else if (spin) {
      const ang = state.t * 0.02;
      const c = Math.cos(ang);
      const s = Math.sin(ang);
      slots = slots.map((p) => {
        const dx = p[0] - center[0];
        const dz = p[2] - center[2];
        return [center[0] + dx * c - dz * s, p[1], center[2] + dx * s + dz * c];
      });
    }
    const { map } = assignError(
      pts.map((a) => a.p),
      slots,
    );
    pts.forEach((a, i) => {
      const s = slots[map[i]];
      err += steerTo(a, s, mag, axes);
      if (pts[0].layer !== "lo") faceBlue(a);
    });
    return err / pts.length;
  }

  /** Slots the inner loop actually steers to. Picture must use this, not a second code. */
  function packLayer(agents, center, radius, squash, opts = {}) {
    const gated = living(agents).filter((a) => a.layer === "lo" && a.gated);
    const pts = living(agents).filter((a) => !(a.layer === "lo" && a.gated));
    const base = {
      layer: opts.layer || "",
      agents: pts,
      gated,
      center,
      radius,
      squash,
      slots: [],
      map: [],
      err: 0,
      own: false,
    };
    if (!pts.length) return base;
    if (pts.every((a) => a.slot)) {
      const slots = pts.map((a) => a.slot);
      let err = 0;
      pts.forEach((a, i) => {
        err += dist(a.p, slots[i]);
      });
      return { ...base, slots, map: pts.map((_, i) => i), err: err / pts.length, own: true };
    }
    let slots = sphereSlots(center, radius, pts.length, squash);
    if (opts.clamp) slots = slots.map(clampCor);
    const { map, err } = assignError(
      pts.map((a) => a.p),
      slots,
    );
    return { ...base, slots, map, err };
  }

  function codes() {
    const liveLo = living(state.lo);
    const loC = [0, 0.2, -2];
    if (liveLo.length) {
      loC[0] = liveLo.reduce((s, a) => s + a.p[0], 0) / liveLo.length;
      loC[1] = liveLo.reduce((s, a) => s + a.p[1], 0) / liveLo.length;
      loC[2] = liveLo.reduce((s, a) => s + a.p[2], 0) / liveLo.length;
    }
    return {
      lo: packLayer(state.lo, loC, 0.68, 0.28, { clamp: true, layer: "lo" }),
      mid: packLayer(state.mid, state.midC, 0.62, 1, { layer: "mid" }),
      hi: packLayer(state.hi, state.hiC, 0.72, 1, { layer: "hi" }),
    };
  }

  function reactBlue(liveLo) {
    const homeMid = [SAM.c[0] + 0.15, 1.15, SAM.c[2] - 0.2];
    const homeHi = [BMD.c[0] - 0.2, 2.15, BMD.c[2] - 0.35];
    const mids = living(state.mid);
    const his = living(state.hi);
    mids.forEach((a) => {
      a.task = "巡逻";
      a.quarry = null;
      a.quarryTn = "";
      a.slot = null;
    });
    his.forEach((a) => {
      a.task = "巡逻";
      a.quarry = null;
      a.quarryTn = "";
      a.slot = null;
    });
    state.pairs = [];
    const unks = living(state.unk || []);
    if (!liveLo.length && !unks.length) {
      state.midC = lerp3(state.midC, homeMid, 0.06);
      state.hiC = lerp3(state.hiC, homeHi, 0.06);
      return;
    }
    const aimSrc = liveLo.length ? liveLo : unks;
    const aim = [
      aimSrc.reduce((s, a) => s + a.p[0], 0) / aimSrc.length,
      aimSrc.reduce((s, a) => s + a.p[1], 0) / aimSrc.length,
      aimSrc.reduce((s, a) => s + a.p[2], 0) / aimSrc.length,
    ];
    state.midC = lerp3(state.midC, stationOnRim(SAM, 1.15, aim), 0.07);
    state.hiC = lerp3(state.hiC, stationOnRim(BMD, 2.15, aim), 0.05);
    his.forEach((a, i) => {
      a.task = "警戒";
      a.slot = stationOnRim(BMD, 2.15, aim, (i - (his.length - 1) / 2) * 0.22);
    });
    const reserve = unks.length && mids.length > 1 ? 1 : 0;
    const threats = liveLo.slice().sort((a, b) => dist(a.p, SAM.c) - dist(b.p, SAM.c));
    const nPair = Math.min(mids.length - reserve, threats.length);
    const ordered = threats.slice(0, nPair).sort((a, b) => a.p[0] - b.p[0]);
    const shadows = ordered.map((t, i) => stationOnRim(SAM, 1.15, t.p, (i - (nPair - 1) / 2) * 0.3));
    const extras =
      mids.length - reserve > nPair ? sphereSlots(state.midC, 0.38, mids.length - reserve - nPair, 1) : [];
    const idSlot = reserve && unks[0] ? [stationOnRim(SAM, 1.15, unks[0].p, 0.45)] : [];
    const slots = [...shadows, ...extras, ...idSlot];
    const { map } = assignError(
      mids.map((a) => a.p),
      slots,
    );
    const idIndex = shadows.length + extras.length;
    mids.forEach((a, i) => {
      const ti = map[i];
      a.slot = slots[ti];
      if (reserve && ti >= idIndex && unks[0]) {
        a.quarry = unks[0].id;
        a.quarryTn = unks[0].tn;
        a.task = "识别";
        state.pairs.push({ blue: a.id, red: unks[0].id });
      } else if (ti < nPair) {
        const q = ordered[ti];
        a.quarry = q.id;
        a.quarryTn = q.tn;
        const z = zoneOf(q.p);
        a.task = z === "in" || z === "edge" ? "压沿" : "盯梢";
        state.pairs.push({ blue: a.id, red: q.id });
      } else a.task = "压沿";
    });

    // Task allocation is a Decision followed by an ActionRequest. Movement
    // and tracking are requests; the engagement request is separately gated
    // in step() by the Gate Monitor authorization service.
    [...mids, ...his].forEach((a) => {
      const action = a.task === "识别" ? "identify" : a.task === "盯梢" ? "track" : a.task === "压沿" ? "hold-rim" : "watch";
      const decision = runtime.decide({
        agent: "agent.options.planner",
        action,
        target: a.quarry || undefined,
        reason: `task=${a.task || "巡逻"}`,
        tick: state.t,
      });
      const request = runtime.request({
        agent: "agent.options.planner",
        action,
        target: a.id,
        decision,
        tick: state.t,
        parameters: { task: a.task || "巡逻", quarry: a.quarry || null, quarryTn: a.quarryTn || "" },
      });
      a.decisionIris.push(decision.iri);
      a.actionRequestIris.push(request.iri);
      runtime.audit("task-assigned", a.id, state.t, { task: a.task || "巡逻", quarry: a.quarry || null });
    });
  }

  function nextWave() {
    const live = living(state.lo).length;
    const room = N_MAX.lo - live;
    if (state.k >= K_MAX) {
      note(`波次用尽 ${state.k}/${K_MAX}。N 仍受 N_max=${N_MAX.lo} 卡住。`, "warn");
      return;
    }
    if (room < 3) {
      note(`走廊已有 ${live} 架，N_max=${N_MAX.lo}，本波不能再灌。`, "warn");
      return;
    }
    const n = Math.min(kb.sampleN(room), room);
    state.k += 1;
    state.lo.push(...spawnLo(state.k, n));
    kb.evolve("wave");
    runtime.audit("wave-spawned", runIri(runId), state.t, { wave: state.k, count: n });
    reactBlue(living(state.lo));
    note(
      state.k === 1
        ? `第 1 波 N=${n}。中空前出盯梢；未进 SAM 区不打。`
        : `第 ${state.k} 波 N=${n}。中空再分配盯梢；未进 SAM 区仍不打。`,
      "wave",
    );
    syncRuntime();
  }

  function inject(kind) {
    if (kind === "jam") {
      state.jammed = true;
      runtime.audit("link-degraded", "agent.fusion.c2", state.t, { jammed: true });
      note("走廊干扰源开机。岸上数据链被切；机间不断；沿上过不了门。", "jam");
    }
    if (kind === "restore") {
      state.jammed = false;
      runtime.audit("link-restored", "agent.fusion.c2", state.t, { jammed: false });
      note("通联恢复。岸上雷达与舰载区重新并信念。", "ok");
    }
    if (kind === "attrition") {
      const live = living(state.lo);
      if (!live.length) {
        note("低空没有可减的平台。先下一波。", "info");
        return;
      }
      live[live.length - 1].alive = false;
      live[live.length - 1].lifecycleState = "destroyed";
      runtime.audit("agent-attrition", live[live.length - 1].id, state.t, { alive: false });
      kb.evolve("attrition");
      note(`减员。剩余 ${live.length - 1} 架在同一层球面码上重收敛。`, "warn");
    }
    syncRuntime();
  }

  function step(dt = TICK_S, opts = {}) {
    const formSpin = opts.formSpin !== false;
    state.t += 1;
    if (state.reload > 0) state.reload -= 1;
    if (state.reload === 0 && state.mSam < M_SAM0) {
      state.mSam += 1;
      state.reload = 90;
    }

    const loC = [0, 0, 0];
    const liveLo = living(state.lo);
    liveLo.forEach((a) => {
      loC[0] += a.p[0];
      loC[1] += a.p[1];
      loC[2] += a.p[2];
    });
    if (liveLo.length) {
      loC[0] /= liveLo.length;
      loC[1] /= liveLo.length;
      loC[2] /= liveLo.length;
    }

    function advance(a, fallbackKmh) {
      const mag = ((a.speedKmh || fallbackKmh) / 3600) * dt;
      const h = Math.hypot(a.v[0], a.v[2]) || 1;
      a.p[0] += (a.v[0] / h) * mag;
      a.p[2] += (a.v[2] / h) * mag;
      a.heading = (Math.atan2(a.v[0], a.v[2]) * 180) / Math.PI;
      a.p = clampCor(a.p);
    }

    liveLo.forEach((a) => {
      if (a.gated) return;
      const z = zoneOf(a.p);
      const inner = z === "in";
      const ship = z === "ship";
      let src = 0.1;
      if (ship) src = 0.42;
      if (z === "edge" || inner) {
        const d = dist(a.p, SAM.c) / SAM.r;
        if (inner) src = state.jammed ? 0.5 + 0.12 * (1 - d) : 0.88 + 0.1 * (1 - d);
        else src = state.jammed ? 0.38 + 0.2 * (1 - d) : 0.55 + 0.18 * (1 - d);
      }
      a.belief += (src - a.belief) * 0.07;
      const observation = recordObservation(a, state.jammed ? "local-track" : "shore-fused-track", src, {
        zone: z,
        jammed: state.jammed,
        feature: kb.feature(a.p, SAM),
      });
      const belief = recordBelief(a, a.belief, observation);
      const f = kb.feature(a.p, SAM);
      let gateResult = null;
      if (inner && !a.gated) {
        const gate = kb.authorizeGate(a, state, inner, f);
        gateResult = gate;
        const decision = runtime.decide({
          agent: "agent.options.planner",
          action: "request-engagement",
          target: a.id,
          reason: gate.reasonCode,
          tick: state.t,
          evidence: [observation.iri, belief.iri],
        });
        const request = runtime.request({
          agent: "agent.options.planner",
          action: "engage",
          target: a.id,
          decision,
          tick: state.t,
          parameters: { zone: z, belief: a.belief, feature: f },
        });
        a.decisionIris.push(decision.iri);
        a.actionRequestIris.push(request.iri);
        const authorization = runtime.authorize({
          request,
          decidedBy: "agent.monitor.gate",
          result: gate.result,
          reasonCode: gate.reasonCode,
          checks: gate.checks,
          evidence: [observation.iri, belief.iri],
          tick: state.t,
        });
        a.authorizationIris.push(authorization.iri);
        a.lastAuthorization = authorization.result;
        if (gate.result === "allow") {
          runtime.execute({
            request,
            executor: a.id,
            result: "started",
            effect: { channelConsumed: 1, engagementZone: true },
            tick: state.t,
          });
          runtime.audit("authorization-allow", a.id, state.t, { authorization: authorization.iri });
        }
      }
      if (gateResult?.result === "allow") {
        a.gated = true;
        state.mSam -= 1;
        state.gated += 1;
        state.reload = Math.max(state.reload, 70);
        kb.evolve("gate");
        note(`过门：区内且 p>=${TAU}。占用通道。余 m=${state.mSam}/${M_SAM0}。`, "gate");
      }
    });

    living(state.unk).forEach((a) => {
      a.belief += (0.28 - a.belief) * 0.03;
      const observation = recordObservation(a, state.jammed ? "local-identification" : "shore-identification", a.belief, {
        classification: "unknown",
        jammed: state.jammed,
      });
      recordBelief(a, a.belief, observation, "threatIdentity=unknown");
    });

    state.epsLo = liveLo.length
      ? pullToSlots(state.lo, liveLo.length ? loC : [0, 0.2, -2], 0.68, 0.28, false, dt, FORM_LO_LAT_KMH, "xy")
      : 0;
    liveLo.forEach((a) => {
      if (a.gated) return;
      advance(a, HOST_LO_KMH);
    });
    living(state.unk).forEach((a) => advance(a, UNK_LO_KMH));

    reactBlue(liveLo);
    state.epsMid = pullToSlots(state.mid, state.midC, 0.62, 1, formSpin, dt, BLUE_MID_KMH, "xyz");
    state.epsHi = pullToSlots(state.hi, state.hiC, 0.72, 1, formSpin, dt, BLUE_HI_KMH, "xyz");
    syncRuntime();
  }

  function reset() {
    kb.reset();
    runtime.reset(runId);
    seq.F = 0;
    seq.H = 0;
    seq.U = 0;
    Object.assign(state, {
      k: 0,
      jammed: false,
      mSam: M_SAM0,
      reload: 0,
      t: 0,
      gated: 0,
      lo: [],
      unk: [],
      events: [],
      caption: "复位。高/中空蓝方槽位仍在；低空走廊空着。",
      ...seedBlue(),
      unk: seedUnk(),
      epsLo: 0,
      epsMid: 0,
      epsHi: 0,
      pairs: [],
    });
    syncRuntime();
  }

  function soWhat() {
    const live = living(state.lo);
    const latest = live.filter((a) => a.wave === state.k);
    const pack = latest.length ? latest : live;
    const n = live.length;
    if (state.jammed) return { id: "foe", text: "干扰源开机 · 岸上数据链断 · 沿上过不了门 · 不明仍不打" };
    if (state.mSam <= 0) return { id: "assign", text: "中层通道满 · 饱和不是开门理由" };
    const unkN = living(state.unk).length;
    if (unkN && !n) return { id: "read", text: `走廊有 ${unkN} 批不明 · 识别，不打` };
    const acts = pack.map((a) => verdict(a, state.jammed));
    const nGate = pack.filter((a) => a.gated).length;
    const nIn = acts.filter((v) => v.pos === "SAM区内").length;
    const nEdge = acts.filter((v) => v.pos === "SAM区沿").length;
    const nShip = acts.filter((v) => v.pos === "舰载区内").length;
    const nCor = acts.filter((v) => v.pos.startsWith("走廊") || v.pos === "区外").length;
    if (nGate) return { id: "gate", text: `第 ${state.k} 波已过门 ${nGate} 批 · 占用通道` };
    if (nIn) return { id: "gate", text: `第 ${state.k} 波 ${nIn} 批在 SAM 区内 · p≥${TAU} 才打` };
    const nPair = (state.pairs || []).length;
    if (nEdge && nCor) {
      return { id: "read", text: `第 ${state.k} 波：未进区 ${nCor} 批不打 · 区沿 ${nEdge} 批跟踪不打` };
    }
    if (nEdge) return { id: "read", text: `第 ${state.k} 波贴 SAM 沿 ${nEdge} 批 · 跟踪，不打` };
    if (nShip) return { id: "read", text: `第 ${state.k} 波进舰载区 ${nShip} 批 · 跟踪，不打` };
    if (n && nPair) {
      return { id: "assign", text: `中空 ${nPair} 架前出盯梢 · 第 ${state.k} 波未进 SAM 区 · 不打` };
    }
    if (n) return { id: "see", text: `第 ${state.k} 波在走廊 · 未进 SAM 区 · 不打` };
    return { id: "see", text: "走廊空 · 等有限波次" };
  }

  function branches() {
    const nLo = living(state.lo).length;
    const nUnk = living(state.unk).length;
    const nBlue = living(state.hi).length + living(state.mid).length;
    const nPair = (state.pairs || []).length;
    const r = kb.readout();
    const waitGate = living(state.lo).filter((a) => zoneOf(a.p) === "in" && !a.gated).length;
    return [
      {
        id: "see",
        title: "探测跟踪",
        value: `敌 ${nLo} · 不明 ${nUnk} · 我 ${nBlue}`,
        hot: nLo > 0,
        tip: `<b>敌</b> 已判明为敌方的低空来袭批数，轨迹约束在走廊内。<b>不明</b> 身份未判明，只跟踪识别，不进交战清单。<b>我</b> 己方高空与中空无人机合计，各层数量受 Nmax 上限约束。`,
      },
      {
        id: "read",
        title: "识别判断",
        value: state.jammed
          ? `不明 ${nUnk} · 局部 ${r.local}`
          : `敌判明 ${nLo} · 不明 ${nUnk}`,
        hot: state.jammed || nLo > 0 || nUnk > 0,
        tip: `<b>情报成品</b> 在右侧抽屉 · 情报，不只是装备识别：还有企图、电磁、威胁排序和空隙。探测给航迹，本项给「是谁、要干什么、电磁是否可用、缺什么」。不明批是空隙，不进交战清单。岸上数据链断则依据变局部，不能当队级真值去授权。`,
      },
      {
        id: "assign",
        title: "任务分配",
        value: `跟踪 ${nPair}/${nLo} · 通道 ${state.mSam}/${M_SAM0}`,
        hot: nLo > 0 || state.mSam <= 1,
        tip: `<b>跟踪 a/b</b> 已派己方平台实施跟踪或识别的批数 / 敌方批数。比值小于 1 表示盯不过来，受中空有限 N 限制，不是加打的理由。<b>通道 m/M</b> 中层 SAM 剩余交战通道 / 满编。通道为 0 是饱和，饱和不能代替交战授权。`,
      },
      {
        id: "form",
        title: "编队协同",
        value: `低空误差 ε ${state.epsLo.toFixed(2)}`,
        hot: state.epsLo > 0.35,
        tip: `<b>ε</b> 该层无人机相对球面码槽位的平均位置误差，单位与图上一致（1 ≈ 1 km）。越小编队越收敛。减员或新波次后在同一层槽位上重收敛，不跨层共用一套槽位。ε 高于约 0.35 时本项告警。`,
      },
      {
        id: "gate",
        title: "交战授权",
        value: `已许可 ${state.gated} · 待许可 ${waitGate}`,
        hot: waitGate > 0 || state.gated > 0,
        tip: `<b>已许可</b> 已满足交战条件并占用通道的批数：位于 SAM 授权区内（虚线内界以内），且单批 p≥0.85，且通道有余。<b>待许可</b> 已在授权区内但尚未授权（p 不足、电子对抗导致信念不够、或通道已满）。区沿只跟踪不打。不明批不进入本项。人在回路：算法只给许可条件，不开对人自动交战。`,
      },
      {
        id: "foe",
        title: "对抗敌情",
        value: state.jammed ? `电子对抗 · 波次 ${state.k}/${K_MAX}` : `波次 ${state.k}/${K_MAX}`,
        hot: state.jammed,
        tip: `<b>波次 k/K</b> 环境给出的有限来袭波，每波 N 仍受 Nmax 卡住，不是无限蜂群。<b>电子对抗</b> 走廊干扰源开机：岸上数据链与机站中断，队级融合冻结，沿上不能授权交战；机间链路仍在。这是对通信图的有界注入，不是把物理包络删掉。`,
      },
    ];
  }

  return {
    kb,
    state,
    living,
    nextWave,
    inject,
    step,
    reset,
    soWhat,
    branches,
    codes,
    verdict,
    idle() {
      state.t += 1;
      reactBlue(living(state.lo));
      state.epsMid = pullToSlots(state.mid, state.midC, 0.62, 1, true, TICK_S, BLUE_MID_KMH, "xyz");
      state.epsHi = pullToSlots(state.hi, state.hiC, 0.72, 1, true, TICK_S, BLUE_HI_KMH, "xyz");
      syncRuntime();
    },
    tickKb() {
      kb.tick(state, SAM, living, inCor);
      syncRuntime();
    },
    runtime,
    ontologySnapshot() {
      syncRuntime();
      return state.runtime;
    },
  };
}
