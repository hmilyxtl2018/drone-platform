/** Retired. Inner loop is swarm.js + battle.js. Do not use as theater. */

export const MOVES = [
  [0, 0, 0],
  [-1, 0, 0],
  [1, 0, 0],
  [0, -1, 0],
  [0, 1, 0],
  [0, 0, 1],
  [0, 0, -1],
];

export const N_MAX = 6;
export const N_MIN = 3;
export const H = 3;
export const LAYER = ["低空", "中空", "高空"];

export function nFromEnv(grid, nTargets, rSense) {
  const areaNeed = (grid * grid) / Math.max(Math.PI * rSense * rSense, 1);
  const n = Math.round(0.4 * areaNeed + 0.3 * nTargets + 0.4 * H);
  return Math.min(N_MAX, Math.max(N_MIN, n));
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function rng() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function entropy(p) {
  const x = Math.min(1 - 1e-6, Math.max(1e-6, p));
  return -(x * Math.log(x) + (1 - x) * Math.log(1 - x));
}

function dist(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], (a[2] ?? 0) - (b[2] ?? 0));
}

function senseRange(z) {
  return 1.35 + z * 1.15;
}

function pDetectAt(z, base = 0.9) {
  return Math.max(0.55, base - z * 0.16);
}

function pFalseAt(z, base = 0.03) {
  return Math.min(0.22, base + z * 0.035);
}

function sphereSlots(k) {
  const pts = [];
  const offset = 2 / k;
  const inc = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < k; i += 1) {
    const y = i * offset - 1 + offset / 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = i * inc;
    pts.push([Math.cos(phi) * r, Math.sin(phi) * r, y]);
  }
  return pts;
}

export const SCENARIOS = {
  dynamic: {
    id: "dynamic",
    name: "立体动态对抗",
    blurb: "三维多面体队形 → 高低空分工 → 拒止 / 减员后在立体槽位上重收敛",
    grid: 8,
    nTargets: 4,
    nFixed: null,
    rSense: 2.2,
    rComm: 4.2,
    pDetect: 0.9,
    pFalse: 0.03,
    useFormation: true,
    strikeThreshold: 0.85,
    horizon: 70,
    script: [
      { t: 10, type: "jam", label: "通信拒止" },
      { t: 22, type: "attrition", label: "平台减员" },
      { t: 34, type: "restore", label: "通联恢复" },
      { t: 42, type: "spawn", label: "新目标出现" },
      { t: 50, type: "falseAlarm", label: "虚警抬升" },
    ],
  },
  scale: {
    id: "scale",
    name: "环境定规模",
    blurb: "地面幅员与目标密度决定有限架次，高度层是额外自由度",
    grid: 10,
    nTargets: 5,
    nFixed: null,
    rSense: 2.2,
    rComm: 5,
    pDetect: 0.9,
    pFalse: 0.03,
    useFormation: true,
    strikeThreshold: 0.85,
    horizon: 55,
    script: [],
  },
  formation: {
    id: "formation",
    name: "立体队形收敛",
    blurb: "内环收到有限多面体，不是平面圆阵",
    grid: 8,
    nTargets: 4,
    nFixed: 5,
    rSense: 2.2,
    rComm: 4.2,
    pDetect: 0.9,
    pFalse: 0.03,
    useFormation: true,
    strikeThreshold: 0.85,
    horizon: 45,
    script: [],
  },
  denied: {
    id: "denied",
    name: "通信拒止",
    blurb: "立体通联同样可被切断，未融合信念不能当真值",
    grid: 8,
    nTargets: 4,
    nFixed: 5,
    rSense: 2.2,
    rComm: 0,
    pDetect: 0.9,
    pFalse: 0.03,
    useFormation: true,
    strikeThreshold: 0.85,
    horizon: 45,
    script: [],
  },
};

export class SwarmSim {
  constructor(scenario, policy, seed = 7) {
    this.reset(scenario, policy, seed);
  }

  reset(scenario, policy, seed = 7) {
    this.scenario = { ...SCENARIOS[scenario] };
    this.policy = policy;
    this.rng = mulberry32(seed);
    this.savedComm = this.scenario.rComm;
    this.jammed = this.scenario.rComm <= 0;
    const uc = this.scenario;
    this.nEnv = nFromEnv(uc.grid, uc.nTargets, uc.rSense);
    this.n = uc.nFixed ?? this.nEnv;
    this.L = uc.grid;
    this.alive = Array(this.n).fill(true);
    this.pos = [];
    for (let i = 0; i < this.n; i += 1) {
      this.pos.push([i % this.L, (i * 3) % this.L, i % H]);
    }
    this.belief = Array.from({ length: this.n }, () =>
      Array.from({ length: this.L }, () => Array(this.L).fill(0.5)),
    );
    this.visited = Array.from({ length: this.L }, () => Array(this.L).fill(false));
    this.targets = this.placeTargets(uc.nTargets);
    this.trueStrikes = 0;
    this.falseStrikes = 0;
    this.collisions = 0;
    this.t = 0;
    this.events = [];
    this.formHist = [];
    this.covHist = [];
    this.flash = null;
    this.lastEffect = "等待开局：有限规模的立体编队。高空拉视场，低空做确认与交战。";
    this.scriptFired = new Set();
    this.senseAndFuse();
    this.tryStrike();
    this.record();
  }

  placeTargets(k) {
    const used = new Set();
    const out = [];
    while (out.length < k) {
      const cell = Math.floor(this.rng() * this.L * this.L);
      if (used.has(cell)) continue;
      used.add(cell);
      out.push([Math.floor(cell / this.L), cell % this.L, 0]);
    }
    return out;
  }

  aliveIdx() {
    return this.alive.map((v, i) => (v ? i : -1)).filter((i) => i >= 0);
  }

  layers() {
    const c = [0, 0, 0];
    for (const i of this.aliveIdx()) c[this.pos[i][2]] += 1;
    return c;
  }

  formationSlots() {
    const idx = this.aliveIdx();
    if (idx.length <= 1) return { center: [0, 0, 1], slots: [], radius: 0, error: 0, edges: [] };
    const pts = idx.map((i) => this.pos[i]);
    const c = [0, 0, 0];
    pts.forEach((p) => {
      c[0] += p[0];
      c[1] += p[1];
      c[2] += p[2];
    });
    c[0] /= pts.length;
    c[1] /= pts.length;
    c[2] /= pts.length;
    const k = pts.length;
    const radius = Math.max(1.4, 0.42 * k + 0.9);
    const unit = sphereSlots(k);
    const slots = unit.map((u) => [
      c[0] + radius * u[0],
      c[1] + radius * u[1],
      Math.min(H - 1, Math.max(0, c[2] + 0.85 * radius * u[2])),
    ]);
    const used = new Set();
    let err = 0;
    const assign = [];
    for (const p of pts) {
      let best = Infinity;
      let bestJ = 0;
      for (let j = 0; j < k; j += 1) {
        if (used.has(j)) continue;
        const d = dist(p, slots[j]);
        if (d < best) {
          best = d;
          bestJ = j;
        }
      }
      used.add(bestJ);
      assign.push(bestJ);
      err += best;
    }
    const edges = [];
    for (let a = 0; a < k; a += 1) {
      for (let b = a + 1; b < k; b += 1) {
        const chord = dist(unit[a], unit[b]);
        if (chord < 1.82) edges.push([a, b, chord]);
      }
    }
    return { center: c, slots, radius, error: err / k, edges, unit, assign, alive: idx };
  }

  teamBelief() {
    const team = Array.from({ length: this.L }, () => Array(this.L).fill(0.5));
    const idx = this.aliveIdx();
    for (let x = 0; x < this.L; x += 1) {
      for (let y = 0; y < this.L; y += 1) {
        let m = 0.5;
        for (const i of idx) {
          const v = this.belief[i][x][y];
          if (Math.abs(v - 0.5) > Math.abs(m - 0.5)) m = v;
        }
        team[x][y] = m;
      }
    }
    return team;
  }

  coverage() {
    const team = this.teamBelief();
    let c = 0;
    for (let x = 0; x < this.L; x += 1) {
      for (let y = 0; y < this.L; y += 1) {
        if (Math.abs(team[x][y] - 0.5) > 0.25) c += 1;
      }
    }
    return c / (this.L * this.L);
  }

  exploration() {
    let v = 0;
    for (let x = 0; x < this.L; x += 1) {
      for (let y = 0; y < this.L; y += 1) if (this.visited[x][y]) v += 1;
    }
    return v / (this.L * this.L);
  }

  detections() {
    const idx = this.aliveIdx();
    let found = this.trueStrikes;
    for (const t of this.targets) {
      if (idx.some((i) => this.belief[i][t[0]][t[1]] >= 0.7)) found += 1;
    }
    return found / Math.max(this.scenario.nTargets, 1);
  }

  senseAndFuse() {
    const uc = this.scenario;
    const occ = Array.from({ length: this.L }, () => Array(this.L).fill(false));
    for (const t of this.targets) occ[t[0]][t[1]] = true;
    for (const i of this.aliveIdx()) {
      const z = this.pos[i][2];
      const rs = senseRange(z);
      const pd = pDetectAt(z, uc.pDetect);
      const pf = pFalseAt(z, uc.pFalse);
      for (let x = 0; x < this.L; x += 1) {
        for (let y = 0; y < this.L; y += 1) {
          if (dist(this.pos[i], [x, y, 0]) > rs) continue;
          this.visited[x][y] = true;
          const pObs1 = occ[x][y] ? pd : pf;
          const obs = this.rng() < pObs1;
          const p = this.belief[i][x][y];
          let num;
          let den;
          if (obs) {
            num = p * pd;
            den = num + (1 - p) * pf;
          } else {
            num = p * (1 - pd);
            den = num + (1 - p) * (1 - pf);
          }
          this.belief[i][x][y] = Math.min(1 - 1e-4, Math.max(1e-4, num / Math.max(den, 1e-8)));
        }
      }
    }
    if (uc.rComm <= 0) return;
    const idx = this.aliveIdx();
    const fused = this.belief.map((b) => b.map((row) => row.slice()));
    for (const a of idx) {
      const acc = [this.belief[a]];
      for (const b of idx) {
        if (a === b) continue;
        if (dist(this.pos[a], this.pos[b]) <= uc.rComm) acc.push(this.belief[b]);
      }
      for (let x = 0; x < this.L; x += 1) {
        for (let y = 0; y < this.L; y += 1) {
          fused[a][x][y] = acc.reduce((s, m) => s + m[x][y], 0) / acc.length;
        }
      }
    }
    this.belief = fused;
  }

  tryStrike() {
    const tau = this.scenario.strikeThreshold;
    for (const i of this.aliveIdx()) {
      const [x, y, z] = this.pos[i];
      if (z !== 0) continue;
      if (this.belief[i][x][y] < tau) continue;
      const hit = this.targets.findIndex((t) => t[0] === x && t[1] === y);
      if (hit >= 0) {
        this.trueStrikes += 1;
        this.targets.splice(hit, 1);
        this.pushEvent("真打击", `平台 ${i} 低空确认并清除`);
      } else {
        this.falseStrikes += 1;
        this.pushEvent("误打击", `平台 ${i} 低空把虚警当真`);
      }
      this.belief[i][x][y] = 0.05;
    }
  }

  igVector(i) {
    const [x, y, z] = this.pos[i];
    let best = [0, 0, 0];
    let bestV = -1;
    for (let dx = -3; dx <= 3; dx += 1) {
      for (let dy = -3; dy <= 3; dy += 1) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= this.L || ny >= this.L) continue;
        const v = entropy(this.belief[i][nx][ny]) / (1 + 0.35 * (Math.abs(dx) + Math.abs(dy)));
        if (v > bestV) {
          bestV = v;
          const wantLow = this.belief[i][nx][ny] > 0.7;
          const dz = wantLow ? -z : Math.min(H - 1, 2) - z;
          best = [dx, dy, dz];
        }
      }
    }
    return best;
  }

  formVector(i) {
    const idx = this.aliveIdx();
    if (idx.length <= 1) return [0, 0, 0];
    const { slots } = this.formationSlots();
    const rank = idx.indexOf(i);
    const s = slots[rank];
    return [s[0] - this.pos[i][0], s[1] - this.pos[i][1], s[2] - this.pos[i][2]];
  }

  clipPos(p) {
    return [
      Math.min(this.L - 1, Math.max(0, p[0])),
      Math.min(this.L - 1, Math.max(0, p[1])),
      Math.min(H - 1, Math.max(0, p[2])),
    ];
  }

  occupied(nxt, self) {
    return this.aliveIdx().some(
      (j) => j !== self && this.pos[j][0] === nxt[0] && this.pos[j][1] === nxt[1] && this.pos[j][2] === nxt[2],
    );
  }

  chooseMove(i, vec) {
    let best = MOVES[0];
    let bestS = -1e9;
    for (const mv of MOVES) {
      const nxt = this.clipPos([
        this.pos[i][0] + mv[0],
        this.pos[i][1] + mv[1],
        this.pos[i][2] + mv[2],
      ]);
      const occ = this.occupied(nxt, i) ? 1 : 0;
      const mag = Math.abs(mv[0]) + Math.abs(mv[1]) + Math.abs(mv[2]);
      const align = mag === 0 ? 0 : mv[0] * vec[0] + mv[1] * vec[1] + mv[2] * vec[2];
      const s = align - 2.5 * occ;
      if (s > bestS) {
        bestS = s;
        best = mv;
      }
    }
    return best;
  }

  act() {
    for (const i of this.aliveIdx()) {
      if (this.policy === "random") {
        const mv = MOVES[Math.floor(this.rng() * MOVES.length)];
        const nxt = this.clipPos([
          this.pos[i][0] + mv[0],
          this.pos[i][1] + mv[1],
          this.pos[i][2] + mv[2],
        ]);
        if (!this.occupied(nxt, i)) this.pos[i] = nxt;
        continue;
      }
      const ig = this.igVector(i);
      let vec = ig;
      if (this.policy === "form_greedy" && this.scenario.useFormation) {
        const form = this.formVector(i);
        const w = this.t < 10 ? 0.82 : 0.52;
        vec = [
          (1 - w) * ig[0] + w * form[0],
          (1 - w) * ig[1] + w * form[1],
          (1 - w) * ig[2] + w * form[2],
        ];
      }
      const mv = this.chooseMove(i, vec);
      this.pos[i] = this.clipPos([
        this.pos[i][0] + mv[0],
        this.pos[i][1] + mv[1],
        this.pos[i][2] + mv[2],
      ]);
    }
  }

  collide() {
    const idx = this.aliveIdx();
    let extra = 0;
    for (let a = 0; a < idx.length; a += 1) {
      for (let b = a + 1; b < idx.length; b += 1) {
        if (dist(this.pos[idx[a]], this.pos[idx[b]]) < 0.01) extra += 1;
      }
    }
    this.collisions += extra;
  }

  inject(type, source = "手动") {
    if (type === "jam") {
      this.scenario.rComm = 0;
      this.jammed = true;
      this.flash = "jam";
      this.lastEffect = "通信拒止：三维链路上的信念也不再融合。";
      this.pushEvent(source, "通信拒止");
    } else if (type === "restore") {
      this.scenario.rComm = this.savedComm || 4.2;
      this.jammed = false;
      this.flash = "restore";
      this.lastEffect = "通联恢复：高低空平台重新共享地面信念。";
      this.pushEvent(source, "通联恢复");
    } else if (type === "attrition") {
      const idx = this.aliveIdx();
      if (idx.length <= 1) return;
      const dead = idx[Math.floor(this.rng() * idx.length)];
      this.alive[dead] = false;
      this.flash = "attrition";
      this.lastEffect = `减员：平台 ${dead} 失效。多面体槽位按存活数在三维重排。`;
      this.pushEvent(source, `平台 ${dead} 减员`);
    } else if (type === "spawn") {
      this.targets.push(...this.placeTargets(1));
      this.scenario.nTargets += 1;
      this.flash = "spawn";
      this.lastEffect = "新地面目标：高空扩视场，低空准备确认。";
      this.pushEvent(source, "新目标出现");
    } else if (type === "falseAlarm") {
      this.scenario.pFalse = Math.min(0.16, this.scenario.pFalse + 0.06);
      this.flash = "falseAlarm";
      this.lastEffect = `虚警基线上抬。高空虚警更狠，交战仍要求降到低空。`;
      this.pushEvent(source, "虚警抬升");
    } else if (type === "expand") {
      this.targets.push(...this.placeTargets(2));
      this.scenario.nTargets += 2;
      this.flash = "expand";
      this.lastEffect = `环境变密。N 仍 ≤ ${N_MAX}，用高度层补覆盖而不是加无限架次。`;
      this.pushEvent(source, "环境目标密度上升");
    }
  }

  pushEvent(kind, text) {
    this.events.unshift({ t: this.t, kind, text });
    this.events = this.events.slice(0, 14);
  }

  record() {
    this.formHist.push(this.formationSlots().error);
    this.covHist.push(this.coverage());
    if (this.formHist.length > 80) {
      this.formHist.shift();
      this.covHist.shift();
    }
  }

  runScript() {
    for (const ev of this.scenario.script || []) {
      const key = `${ev.t}:${ev.type}`;
      if (this.t === ev.t && !this.scriptFired.has(key)) {
        this.scriptFired.add(key);
        this.inject(ev.type, "剧本");
      }
    }
  }

  step() {
    this.t += 1;
    this.flash = null;
    this.runScript();
    this.act();
    this.collide();
    this.senseAndFuse();
    this.tryStrike();
    this.record();
    if (!this.flash) {
      const form = this.formationSlots().error;
      const ly = this.layers();
      if (this.t < 10 && this.policy === "form_greedy") {
        this.lastEffect = `立体队形内环主导。形状误差 ${form.toFixed(2)}，层分布 低${ly[0]}/中${ly[1]}/高${ly[2]}。`;
      } else if (this.jammed) {
        this.lastEffect = "拒止中：各高度层只信自己的地面视场。";
      } else if (this.policy === "random") {
        this.lastEffect = "随机三维游走：无多面体内环，对照用。";
      } else {
        this.lastEffect = `高空扩搜、低空确认。探索 ${(this.exploration() * 100).toFixed(0)}%，层 低${ly[0]}/中${ly[1]}/高${ly[2]}。`;
      }
    }
    return this.snapshot();
  }

  links() {
    const idx = this.aliveIdx();
    const links = [];
    const r = this.scenario.rComm;
    if (r <= 0) return links;
    for (let a = 0; a < idx.length; a += 1) {
      for (let b = a + 1; b < idx.length; b += 1) {
        if (dist(this.pos[idx[a]], this.pos[idx[b]]) <= r) links.push([idx[a], idx[b]]);
      }
    }
    return links;
  }

  snapshot() {
    const form = this.formationSlots();
    return {
      t: this.t,
      L: this.L,
      H,
      n: this.n,
      nEnv: this.nEnv,
      nAlive: this.aliveIdx().length,
      layers: this.layers(),
      policy: this.policy,
      scenario: this.scenario,
      jammed: this.jammed,
      pos: this.pos.map((p) => p.slice()),
      alive: this.alive.slice(),
      targets: this.targets.map((p) => p.slice()),
      team: this.teamBelief(),
      visited: this.visited,
      links: this.links(),
      form,
      rComm: this.scenario.rComm,
      coverage: this.coverage(),
      exploration: this.exploration(),
      detections: this.detections(),
      trueStrikes: this.trueStrikes,
      falseStrikes: this.falseStrikes,
      lastEffect: this.lastEffect,
      flash: this.flash,
      events: this.events,
      formHist: this.formHist.slice(),
      covHist: this.covHist.slice(),
    };
  }
}
