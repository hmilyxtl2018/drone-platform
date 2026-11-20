/** Inner loop: three spherical codes from the same theater as 岸海态势. No strike. */

import {
  BMD,
  SAM,
  SHIP,
  COR,
  N_MAX,
  K_MAX,
  TICK_S,
  createBattle,
  living,
} from "./battle.js?v=25";

const battle = createBattle();
const { state } = battle;

const cam = { yaw: 0.58, pitch: 0.46, dist: 6.2 };
const look = [0.18, 0.85, -0.35];
let view = null;
let show = { hi: true, mid: true, lo: true };

const hist = { hi: [], mid: [], lo: [] };

const STEPS = [
  {
    title: "这页不是整场战斗",
    body: "岸海态势看全局。这一页只看一件事：自己人不要散开。高、中、低三层高度各有一套固定机位，飞机往自己那层的机位飞。先不用点「演」。",
    lookHint: "请看整幅图。金、青、浅三色是三套队形，不是一颗大球。",
    layer: "all",
    focus: "all",
    spot: null,
    cam: { yaw: 0.58, pitch: 0.46, dist: 6.8 },
    look: [0.18, 0.85, -0.35],
  },
  {
    title: "底板怎么读",
    body: "浅色方块是海/地平面。字「东距」是左右，「距岸」是离岸远近，高度是往上。按住画面拖可以转，滚轮拉近。",
    lookHint: "请看图上「东距」「距岸」两个字。转一下，确认这是立体的。",
    layer: "all",
    focus: "floor",
    spot: null,
    cam: { yaw: 0.12, pitch: 0.78, dist: 8.0 },
    look: [0.2, 0.08, -0.2],
  },
  {
    title: "红盒子是贴海走廊",
    body: "低空来袭必须待在这个扁盒子里，不能随便爬高。所以低空那套机位也是扁的，像压扁的饼，不是圆球。",
    lookHint: "请看红色细框，边上有「走廊」。",
    layer: "lo",
    focus: "cor",
    spot: null,
    cam: { yaw: 0.72, pitch: 0.38, dist: 5.9 },
    look: [-1.2, 0.28, -2.3],
  },
  {
    title: "为什么是三套码",
    body: "高空、中空、低空各飞各的高度，不能共用一套机位。金色是低空，青色是中空，浅色是高空。减员以后，只重排被打掉的那一层。",
    lookHint: "底栏点「全」，三套会叠在一起。金圈会轮流标出三层中心。",
    layer: "all",
    focus: "all",
    spot: null,
    cam: { yaw: 0.5, pitch: 0.5, dist: 7.6 },
    look: [0.3, 1.1, 0.2],
  },
  {
    title: "圆点是机位，虚线是「该去那儿」",
    body: "金色实心小点是规定机位。红点是飞机。虚线连着「我现在在这儿」和「我该停的机位」。飞机顺着虚线收拢，不是随便飞。",
    lookHint: "请看低空那一层：点、线、飞机是否对得上。",
    layer: "lo",
    focus: "lo",
    spot: null,
    cam: { yaw: 0.95, pitch: 0.42, dist: 4.9 },
    look: [0, 0.25, -2.2],
  },
  {
    title: "右侧 ε：散没散",
    body: "ε 是飞机离机位的平均距离，大约 1 等于 1 公里。数字越小，队形收得越紧。下面三条小线是高、中、低三层各自的散开程度。",
    lookHint: "请看右上角三条 ε。点「演」后，线应慢慢往下走。",
    layer: "all",
    focus: "all",
    spot: "spot-eps",
    cam: { yaw: 0.58, pitch: 0.46, dist: 6.5 },
    look: [0.18, 0.85, -0.35],
  },
  {
    title: "飞机数量有上限",
    body: "N 是这一层最多几架。不是无限蜂群。k 是第几波来袭。走廊里已经有人时，下一波不一定灌得进去。",
    lookHint: "请看右上角 k 和 N。高 3、中 3、低 6 是上限，不是目标歼灭数。",
    layer: "all",
    focus: "all",
    spot: "spot-n",
    cam: { yaw: 0.58, pitch: 0.46, dist: 6.5 },
    look: [0.18, 0.85, -0.35],
  },
  {
    title: "p 只是数字，这页不打",
    body: "飞机旁边的 p 是「有多像敌方来袭」的把握，0 到 1。这页只让你看见它，不会开火。黄菱形是身份不明，不进任何一套机位，也不打。",
    lookHint: "请看红点旁的 p，以及黄菱形「不明 · 不进码」。",
    layer: "lo",
    focus: "p",
    spot: null,
    cam: { yaw: 0.88, pitch: 0.4, dist: 5.2 },
    look: [-0.4, 0.22, -2.6],
  },
  {
    title: "减员、拒止：打不散同层队形",
    body: "点「减员」：少一架，剩下的在同一层重新占机位，不会跑去别的高度。点「拒止」：岸上通信断了，各层仍按自己的机位收，不混编。",
    lookHint: "底栏「减员」「拒止」会亮起来。可以点一下，看金色点是否变少。",
    layer: "lo",
    focus: "lo",
    spot: "btn-attrition",
    cam: { yaw: 0.9, pitch: 0.4, dist: 5.0 },
    look: [0, 0.25, -2.2],
  },
  {
    title: "可以自己看了",
    body: "底栏「高 / 中 / 低」一次只看一层，不容易糊。看全局去「岸海态势」。这页始终只负责队形，不开门。",
    lookHint: "点「结束」关闭解说。随时再点底栏「解说」重来。方向键也可翻页。",
    layer: "all",
    focus: "all",
    spot: "ly-all",
    cam: { yaw: 0.58, pitch: 0.46, dist: 6.5 },
    look: [0.18, 0.85, -0.35],
    last: true,
  },
];

const guide = { on: true, i: 0 };

const orbit = {
  yawVel: 0,
  pitchVel: 0,
  distGoal: cam.dist,
  dragging: false,
  lastX: 0,
  lastY: 0,
  lastT: 0,
};

const ride = { on: false, t0: 0, dur: 720, from: null, to: null };

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp(v, a, b) {
  return Math.max(a, Math.min(b, v));
}

function easeInOutCubic(t) {
  const x = Math.max(0, Math.min(1, t));
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
}

function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[1], a[0] * b[1] - a[1] * b[0]];
}
function norm(a) {
  const n = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / n, a[1] / n, a[2] / n];
}

function camPos() {
  const cp = Math.cos(cam.pitch);
  const sp = Math.sin(cam.pitch);
  const cy = Math.cos(cam.yaw);
  const sy = Math.sin(cam.yaw);
  return [
    look[0] + cam.dist * cp * sy,
    look[1] + cam.dist * sp,
    look[2] - cam.dist * cp * cy,
  ];
}

function setView(W, H) {
  const eye = camPos();
  const forward = norm(sub(look, eye));
  const right = norm(cross([0, 1, 0], forward));
  const up = cross(forward, right);
  view = { eye, forward, right, up, f: 0.95 * Math.min(W, H), W, H };
}

function P(w) {
  const v = sub(w, view.eye);
  const x = dot(v, view.right);
  const y = dot(v, view.up);
  const z = dot(v, view.forward);
  const d = Math.max(z, 0.45);
  return [view.W * 0.5 + (view.f * x) / d, view.H * 0.58 - (view.f * y) / d, z, view.f / d];
}

function fillPoly(ctx, pts) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath();
  ctx.fill();
}

function strokePoly(ctx, pts, close = true) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  if (close) ctx.closePath();
  ctx.stroke();
}

function unitOf(slot, center, radius, squash) {
  return [
    (slot[0] - center[0]) / Math.max(radius, 1e-6),
    (slot[1] - center[1]) / Math.max(radius * squash, 1e-6),
    (slot[2] - center[2]) / Math.max(radius, 1e-6),
  ];
}

function drawFloor(ctx) {
  const x0 = -5.2;
  const x1 = 4.8;
  const z0 = -5.2;
  const z1 = 3.6;
  const q = [
    P([x0, 0, z0]),
    P([x1, 0, z0]),
    P([x1, 0, z1]),
    P([x0, 0, z1]),
  ];
  ctx.fillStyle = "rgba(232,226,212,0.035)";
  fillPoly(ctx, q);
  ctx.strokeStyle = "rgba(232,226,212,0.16)";
  ctx.lineWidth = 1;
  strokePoly(ctx, q);
  ctx.fillStyle = "rgba(141,136,120,0.85)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const e = P([x1, 0.02, 0]);
  const s = P([0, 0.02, z0]);
  ctx.fillText("东距", e[0] + 6, e[1]);
  ctx.fillText("距岸", s[0], s[1] + 12);
}

function drawGhostEnvelopes(ctx) {
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(213,106,86,0.28)";
  const box = [
    P([COR.x0, COR.y0, COR.z0]),
    P([COR.x1, COR.y0, COR.z0]),
    P([COR.x1, COR.y0, COR.z1]),
    P([COR.x0, COR.y0, COR.z1]),
  ];
  strokePoly(ctx, box);
  const lab = P([(COR.x0 + COR.x1) / 2, COR.y0, COR.z0]);
  ctx.fillStyle = "rgba(213,106,86,0.55)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("走廊", lab[0], lab[1] + 12);

  function equator(b, stroke) {
    const ring = [];
    for (let i = 0; i <= 64; i += 1) {
      const phi = (i / 64) * Math.PI * 2;
      ring.push(P([b.c[0] + b.r * Math.cos(phi), 0.02, b.c[2] + b.r * Math.sin(phi)]));
    }
    ctx.strokeStyle = stroke;
    ctx.setLineDash([5, 5]);
    strokePoly(ctx, ring);
    ctx.setLineDash([]);
  }
  equator(SAM, "rgba(111,180,174,0.28)");
  equator(BMD, "rgba(232,226,212,0.16)");
  const samL = P([SAM.c[0], 0.04, SAM.c[2] + SAM.r * 0.15]);
  const bmdL = P([BMD.c[0], 0.04, BMD.c[2] + BMD.r * 0.12]);
  ctx.fillStyle = "rgba(111,180,174,0.55)";
  ctx.fillText("B_SAM", samL[0], samL[1]);
  ctx.fillStyle = "rgba(232,226,212,0.4)";
  ctx.fillText("B_BMD", bmdL[0], bmdL[1]);
}

function shellPoint(c, r, squash, th, phi) {
  return [
    c[0] + r * Math.sin(th) * Math.cos(phi),
    c[1] + r * squash * Math.cos(th),
    c[2] + r * Math.sin(th) * Math.sin(phi),
  ];
}

function drawShell(ctx, c, r, squash, stroke) {
  const M = squash < 0.5 ? 10 : 12;
  const steps = 36;
  ctx.lineWidth = 1;
  ctx.strokeStyle = stroke;
  for (let m = 0; m < M; m += 1) {
    const phi = (m / M) * Math.PI * 2;
    const arc = [];
    for (let i = 0; i <= steps; i += 1) arc.push(P(shellPoint(c, r, squash, (i / steps) * Math.PI, phi)));
    ctx.globalAlpha = 0.35;
    strokePoly(ctx, arc, false);
  }
  const eq = [];
  for (let i = 0; i <= 48; i += 1) {
    const phi = (i / 48) * Math.PI * 2;
    eq.push(P(shellPoint(c, r, squash, Math.PI / 2, phi)));
  }
  ctx.globalAlpha = 0.7;
  ctx.lineWidth = 1.2;
  strokePoly(ctx, eq);
  ctx.globalAlpha = 1;
}

function drawCode(ctx, pack, color, name) {
  const { center, radius, squash, slots, agents, map, gated, own } = pack;
  if (slots.length > 1) {
    drawShell(ctx, center, radius, squash, color);
    const units = slots.map((s) => unitOf(s, center, radius, squash));
    const chords = [];
    for (let a = 0; a < slots.length; a += 1) {
      for (let b = a + 1; b < slots.length; b += 1) {
        const d = Math.hypot(units[a][0] - units[b][0], units[a][1] - units[b][1], units[a][2] - units[b][2]);
        chords.push({ a, b, d });
      }
    }
    chords.sort((x, y) => x.d - y.d);
    const minD = chords[0]?.d ?? 1;
    for (const ch of chords) {
      if (ch.d > minD + 0.55 && ch.d > 1.15) continue;
      const pa = P(slots[ch.a]);
      const pb = P(slots[ch.b]);
      ctx.strokeStyle = ch.d <= minD + 0.08 ? color : color.replace(/[\d.]+\)$/, "0.28)");
      ctx.lineWidth = ch.d <= minD + 0.08 ? 1.8 : 1;
      ctx.beginPath();
      ctx.moveTo(pa[0], pa[1]);
      ctx.lineTo(pb[0], pb[1]);
      ctx.stroke();
    }
    slots.forEach((s) => {
      const q = P(s);
      ctx.beginPath();
      ctx.arc(q[0], q[1], 3.1, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    });
    if (chords[0]) {
      const pa = P(slots[chords[0].a]);
      const pb = P(slots[chords[0].b]);
      ctx.fillStyle = color;
      ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("δ", (pa[0] + pb[0]) / 2 + 6, (pa[1] + pb[1]) / 2 - 6);
    }
  }

  agents.forEach((a, i) => {
    const s = slots[map[i]];
    const g = P(a.p);
    if (s) {
      const q = P(s);
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1.05;
      ctx.beginPath();
      ctx.moveTo(g[0], g[1]);
      ctx.lineTo(q[0], q[1]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    ctx.beginPath();
    ctx.arc(g[0], g[1], 5.4, 0, Math.PI * 2);
    ctx.fillStyle = a.side === "blue" ? "#d8c9a0" : "#d56a56";
    ctx.fill();
    ctx.fillStyle = "#0c1018";
    ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(a.tn, g[0], g[1] + 0.4);
    ctx.fillStyle = "rgba(232,226,212,0.72)";
    ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`p ${a.belief.toFixed(2)}`, g[0] + 8, g[1] - 8);
  });

  gated.forEach((a) => {
    const g = P(a.p);
    ctx.strokeStyle = "rgba(141,136,120,0.8)";
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(g[0] - 5, g[1] - 5);
    ctx.lineTo(g[0] + 5, g[1] + 5);
    ctx.moveTo(g[0] + 5, g[1] - 5);
    ctx.lineTo(g[0] - 5, g[1] + 5);
    ctx.stroke();
    ctx.fillStyle = "rgba(141,136,120,0.8)";
    ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("过门 · 不进该层码", g[0] + 8, g[1]);
  });

  if (slots.length || agents.length) {
    const anchor = slots[0] ? P(slots[0]) : P(center);
    ctx.fillStyle = color;
    ctx.font = "13px Palatino Linotype, Palatino, Songti SC, serif";
    ctx.textAlign = "left";
    ctx.fillText(own ? `${name} · 沿上槽` : name, anchor[0] + 10, anchor[1] - 16);
  }
}

function drawUnk(ctx) {
  living(state.unk || []).forEach((a) => {
    const g = P(a.p);
    ctx.fillStyle = "#e2c36a";
    ctx.beginPath();
    ctx.moveTo(g[0], g[1] - 7);
    ctx.lineTo(g[0] + 5, g[1]);
    ctx.lineTo(g[0], g[1] + 7);
    ctx.lineTo(g[0] - 5, g[1]);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "rgba(226,195,106,0.8)";
    ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`不明 · 不进码 · p ${a.belief.toFixed(2)}`, g[0] + 8, g[1]);
  });
}

function drawShip(ctx) {
  const q = P(SHIP.c);
  ctx.fillStyle = "rgba(111,180,174,0.35)";
  ctx.beginPath();
  ctx.arc(q[0], q[1], 4, 0, Math.PI * 2);
  ctx.fill();
}

function drawFocus(ctx) {
  if (!guide.on || !view) return;
  const s = STEPS[guide.i];
  const t = (performance.now() / 720) % 1;
  const pulse = (pt, r0) => {
    if (!pt) return;
    const q = P(pt);
    ctx.strokeStyle = `rgba(226,195,106,${(0.78 - 0.5 * t).toFixed(3)})`;
    ctx.lineWidth = 1.85;
    ctx.beginPath();
    ctx.arc(q[0], q[1], r0 + 20 * t, 0, Math.PI * 2);
    ctx.stroke();
  };
  const codes = battle.codes();
  if (s.focus === "lo") pulse(codes.lo.center, 38);
  if (s.focus === "mid") pulse(codes.mid.center, 34);
  if (s.focus === "hi") pulse(codes.hi.center, 34);
  if (s.focus === "all") {
    pulse(codes.hi.center, 26);
    pulse(codes.mid.center, 26);
    if (codes.lo.agents.length) pulse(codes.lo.center, 30);
  }
  if (s.focus === "cor") {
    const box = [
      P([COR.x0, COR.y0, COR.z0]),
      P([COR.x1, COR.y0, COR.z0]),
      P([COR.x1, COR.y0, COR.z1]),
      P([COR.x0, COR.y0, COR.z1]),
    ];
    ctx.strokeStyle = `rgba(226,195,106,${(0.45 + 0.4 * (1 - t)).toFixed(3)})`;
    ctx.lineWidth = 2.3;
    strokePoly(ctx, box);
  }
  if (s.focus === "floor") {
    const q = [
      P([-5.2, 0, -5.2]),
      P([4.8, 0, -5.2]),
      P([4.8, 0, 3.6]),
      P([-5.2, 0, 3.6]),
    ];
    ctx.strokeStyle = `rgba(226,195,106,${(0.4 + 0.35 * (1 - t)).toFixed(3)})`;
    ctx.lineWidth = 2;
    strokePoly(ctx, q);
  }
  if (s.focus === "p") {
    codes.lo.agents.forEach((a) => pulse(a.p, 16));
    living(state.unk || []).forEach((a) => pulse(a.p, 16));
  }
}

function drawWorld(canvas) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  if (canvas.width !== Math.floor(W * dpr) || canvas.height !== Math.floor(H * dpr)) {
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0c1018";
  ctx.fillRect(0, 0, W, H);
  setView(W, H);
  drawFloor(ctx);
  drawGhostEnvelopes(ctx);
  drawShip(ctx);
  const codes = battle.codes();
  const layers = [
    [show.hi, codes.hi, "rgba(232,226,212,0.85)", "高空码"],
    [show.mid, codes.mid, "rgba(111,180,174,0.9)", "中空码"],
    [show.lo, codes.lo, "rgba(226,195,106,0.95)", "低空码"],
  ];
  layers
    .filter((row) => row[0])
    .sort((a, b) => P(b[1].center)[2] - P(a[1].center)[2])
    .forEach(([, pack, color, name]) => drawCode(ctx, pack, color, name));
  if (show.lo) drawUnk(ctx);
  drawFocus(ctx);
}

function drawSpark(canvas) {
  const ctx = canvas.getContext("2d");
  const w = canvas.clientWidth || 148;
  const h = canvas.clientHeight || 40;
  const dpr = window.devicePixelRatio || 1;
  const dw = Math.floor(w * dpr);
  const dh = Math.floor(h * dpr);
  if (canvas.width !== dw || canvas.height !== dh) {
    canvas.width = dw;
    canvas.height = dh;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const max = Math.max(0.05, ...hist.hi, ...hist.mid, ...hist.lo);
  function plot(data, color) {
    if (data.length < 2) return;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / Math.max(data.length - 1, 1)) * (w - 2) + 1;
      const y = h - 2 - (v / max) * (h - 4);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.15;
    ctx.stroke();
  }
  plot(hist.hi, "rgba(232,226,212,0.85)");
  plot(hist.mid, "rgba(111,180,174,0.95)");
  plot(hist.lo, "rgba(226,195,106,0.95)");
}

function rememberEps() {
  hist.hi.push(state.epsHi || 0);
  hist.mid.push(state.epsMid || 0);
  hist.lo.push(state.epsLo || 0);
  if (hist.hi.length > 80) {
    hist.hi.shift();
    hist.mid.shift();
    hist.lo.shift();
  }
}

function say() {
  const c = battle.codes();
  const bits = [];
  if (c.hi.agents.length) bits.push(`高 ε ${(state.epsHi || 0).toFixed(2)}`);
  if (c.mid.agents.length) bits.push(`中 ε ${(state.epsMid || 0).toFixed(2)}`);
  if (c.lo.agents.length) bits.push(`低 ε ${(state.epsLo || 0).toFixed(2)}`);
  if (c.lo.gated.length) bits.push(`过门 ${c.lo.gated.length} 不进低空码，仍占 N`);
  if (living(state.unk || []).length) bits.push("不明不进码");
  if (state.jammed) bits.push("金线断 · 仍按层收");
  if (!c.lo.agents.length && !c.lo.gated.length) bits.push("低空走廊空 · 高/中空码按包络尺度站槽");
  return bits.join(" · ");
}

function hud() {
  const nHi = living(state.hi).length;
  const nMid = living(state.mid).length;
  const nLo = living(state.lo).length;
  document.getElementById("v-k").textContent = `${state.k}/${K_MAX}`;
  document.getElementById("v-n").textContent = `高 ${nHi}/${N_MAX.hi} · 中 ${nMid}/${N_MAX.mid} · 低 ${nLo}/${N_MAX.lo}`;
  document.getElementById("v-eh").textContent = (state.epsHi || 0).toFixed(2);
  document.getElementById("v-em").textContent = (state.epsMid || 0).toFixed(2);
  document.getElementById("v-el").textContent = (state.epsLo || 0).toFixed(2);
  document.getElementById("caption").textContent = say();
  document.getElementById("caption").hidden = guide.on;
  drawSpark(document.getElementById("spark"));
}

function render() {
  try {
    battle.tickKb();
    drawWorld(document.getElementById("world"));
    hud();
  } catch (err) {
    console.error(err);
    const cap = document.getElementById("caption");
    if (cap) cap.textContent = String(err && err.message ? err.message : err);
  }
}

function bindOrbit(canvas) {
  canvas.addEventListener("pointerdown", (e) => {
    yieldRide();
    orbit.dragging = true;
    orbit.yawVel = 0;
    orbit.pitchVel = 0;
    orbit.lastX = e.clientX;
    orbit.lastY = e.clientY;
    orbit.lastT = performance.now();
    canvas.classList.add("drag");
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!orbit.dragging) return;
    const now = performance.now();
    const dt = Math.max(0.008, (now - orbit.lastT) / 1000);
    const dx = e.clientX - orbit.lastX;
    const dy = e.clientY - orbit.lastY;
    const kY = 0.0072;
    const kP = 0.0052;
    cam.yaw += dx * kY;
    cam.pitch = clamp(cam.pitch + dy * kP, 0.16, 1.15);
    orbit.yawVel = clamp((dx * kY) / dt, -2.2, 2.2);
    orbit.pitchVel = clamp((dy * kP) / dt, -1.4, 1.4);
    orbit.lastX = e.clientX;
    orbit.lastY = e.clientY;
    orbit.lastT = now;
  });
  const end = () => {
    if (performance.now() - orbit.lastT > 80) {
      orbit.yawVel = 0;
      orbit.pitchVel = 0;
    }
    orbit.dragging = false;
    canvas.classList.remove("drag");
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      yieldRide();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1;
      const raw = e.deltaY * unit;
      const step = Math.sign(raw) * Math.min(Math.abs(raw) * 0.007, 0.72);
      orbit.distGoal = clamp(orbit.distGoal + step, 3.6, 14);
    },
    { passive: false },
  );
}

function setLayer(which, fromGuide = false) {
  if (which === "all") show = { hi: true, mid: true, lo: true };
  else {
    show = { hi: which === "hi", mid: which === "mid", lo: which === "lo" };
  }
  ["all", "hi", "mid", "lo"].forEach((id) => {
    const el = document.getElementById(`ly-${id}`);
    if (el) el.classList.toggle("on", which === "all" ? id === "all" : id === which);
  });
  if (!fromGuide) {
    const p = pivotNow();
    look[0] = p[0];
    look[1] = p[1];
    look[2] = p[2];
    render();
  }
}

function applySpot(id) {
  document.querySelectorAll(".spot").forEach((el) => el.classList.remove("spot"));
  if (id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("spot");
  }
}

function pivotNow() {
  const codes = battle.codes();
  if (show.lo && !show.hi && !show.mid) return codes.lo.center.slice();
  if (show.mid && !show.hi && !show.lo) return codes.mid.center.slice();
  if (show.hi && !show.mid && !show.lo) return codes.hi.center.slice();
  return [0.18, 0.85, -0.35];
}

function freezeTarget() {
  const s = STEPS[guide.i];
  const codes = battle.codes();
  let at = (s.look || [0.18, 0.85, -0.35]).slice();
  if (s.focus === "lo" && codes.lo.center) at = codes.lo.center.slice();
  if (s.focus === "mid" && codes.mid.center) at = codes.mid.center.slice();
  if (s.focus === "hi" && codes.hi.center) at = codes.hi.center.slice();
  if (s.focus === "cor") at = [(COR.x0 + COR.x1) / 2, 0.24, (COR.z0 + COR.z1) / 2];
  if (s.focus === "p") {
    const u = living(state.unk || [])[0];
    at = u ? u.p.slice() : codes.lo.center.slice();
  }
  return { yaw: s.cam.yaw, pitch: s.cam.pitch, dist: s.cam.dist, look: at };
}

function yieldRide() {
  ride.on = false;
}

function beginRide() {
  ride.on = true;
  ride.t0 = performance.now();
  ride.from = {
    yaw: cam.yaw,
    pitch: cam.pitch,
    dist: cam.dist,
    look: look.slice(),
  };
  ride.to = freezeTarget();
  orbit.yawVel = 0;
  orbit.pitchVel = 0;
}

function tickCamera(now, dtMs) {
  const dt = Math.min(0.05, Math.max(0, dtMs / 1000));
  if (ride.on) {
    const u = easeInOutCubic((now - ride.t0) / ride.dur);
    const a = ride.from;
    const b = ride.to;
    cam.yaw = lerp(a.yaw, b.yaw, u);
    cam.pitch = lerp(a.pitch, b.pitch, u);
    cam.dist = lerp(a.dist, b.dist, u);
    look[0] = lerp(a.look[0], b.look[0], u);
    look[1] = lerp(a.look[1], b.look[1], u);
    look[2] = lerp(a.look[2], b.look[2], u);
    if (u >= 1) {
      ride.on = false;
      cam.yaw = b.yaw;
      cam.pitch = b.pitch;
      cam.dist = b.dist;
      look[0] = b.look[0];
      look[1] = b.look[1];
      look[2] = b.look[2];
      orbit.distGoal = cam.dist;
    }
    return;
  }
  if (!orbit.dragging) {
    cam.yaw += orbit.yawVel * dt;
    cam.pitch = clamp(cam.pitch + orbit.pitchVel * dt, 0.16, 1.15);
    const decay = Math.exp(-7.8 * dt);
    orbit.yawVel *= decay;
    orbit.pitchVel *= decay;
    if (Math.abs(orbit.yawVel) < 0.015) orbit.yawVel = 0;
    if (Math.abs(orbit.pitchVel) < 0.015) orbit.pitchVel = 0;
  }
  cam.dist += (orbit.distGoal - cam.dist) * (1 - Math.exp(-14 * dt));
}

function paintGuide() {
  const s = STEPS[guide.i];
  const n = STEPS.length;
  document.getElementById("guide-idx").textContent = `${guide.i + 1} / ${n}`;
  document.getElementById("guide-title").textContent = s.title;
  document.getElementById("guide-body").textContent = s.body;
  document.getElementById("guide-look").textContent = s.lookHint;
  document.getElementById("guide-prev").disabled = guide.i === 0;
  document.getElementById("guide-next").textContent = s.last ? "结束" : "下一步";
  applySpot(s.spot);
  if (s.layer) setLayer(s.layer, true);
}

function openGuide(i = 0) {
  guide.on = true;
  guide.i = Math.max(0, Math.min(STEPS.length - 1, i));
  document.body.classList.add("touring");
  document.getElementById("guide").hidden = false;
  paintGuide();
  beginRide();
  render();
}

function closeGuide() {
  guide.on = false;
  yieldRide();
  document.body.classList.remove("touring");
  document.getElementById("guide").hidden = true;
  applySpot(null);
  setLayer("all", true);
  const p = pivotNow();
  look[0] = p[0];
  look[1] = p[1];
  look[2] = p[2];
  orbit.distGoal = cam.dist;
  render();
}

function guideDelta(d) {
  if (!guide.on) return;
  const n = guide.i + d;
  if (n < 0) return;
  if (n >= STEPS.length) {
    closeGuide();
    return;
  }
  guide.i = n;
  paintGuide();
  beginRide();
  render();
}

const world = document.getElementById("world");
const playBtn = document.getElementById("play");
const freezeEl = document.getElementById("freeze");
let playing = false;
let frozen = false;
let acc = 0;
let last = performance.now();

function reset() {
  battle.reset();
  battle.nextWave();
  hist.hi = [];
  hist.mid = [];
  hist.lo = [];
  yieldRide();
  cam.yaw = 0.58;
  cam.pitch = 0.46;
  cam.dist = 6.2;
  look[0] = 0.18;
  look[1] = 0.85;
  look[2] = -0.35;
  orbit.yawVel = 0;
  orbit.pitchVel = 0;
  orbit.distGoal = cam.dist;
  playing = false;
  frozen = false;
  acc = 0;
  last = performance.now();
  playBtn.textContent = "演";
  if (freezeEl) freezeEl.hidden = true;
  render();
}

playBtn.addEventListener("click", () => {
  playing = !playing;
  if (playing) {
    frozen = false;
    acc = 0;
    last = performance.now();
    if (freezeEl) freezeEl.hidden = true;
    playBtn.textContent = "止";
    if (!state.k) battle.nextWave();
  } else {
    playBtn.textContent = "演";
  }
});
document.getElementById("wave").addEventListener("click", () => {
  battle.nextWave();
  render();
});
document.getElementById("reset").addEventListener("click", reset);
document.querySelectorAll("[data-inject]").forEach((btn) => {
  btn.addEventListener("click", () => {
    battle.inject(btn.dataset.inject);
    render();
  });
});
document.getElementById("ly-all").addEventListener("click", () => setLayer("all"));
document.getElementById("ly-hi").addEventListener("click", () => setLayer("hi"));
document.getElementById("ly-mid").addEventListener("click", () => setLayer("mid"));
document.getElementById("ly-lo").addEventListener("click", () => setLayer("lo"));

document.addEventListener("visibilitychange", () => {
  acc = 0;
  last = performance.now();
  if (document.hidden && playing) {
    playing = false;
    frozen = true;
    playBtn.textContent = "止（冻）";
    if (freezeEl) freezeEl.hidden = false;
  }
});

function loop(now) {
  const dt = now - last;
  last = now;
  tickCamera(now, dt);
  if (document.hidden || frozen) {
    acc = 0;
  } else if (playing) {
    acc += dt;
    while (acc >= 40) {
      acc -= 40;
      battle.step(TICK_S, { formSpin: false });
      rememberEps();
    }
  }
  render();
  requestAnimationFrame(loop);
}

document.getElementById("guide-next").addEventListener("click", () => guideDelta(1));
document.getElementById("guide-prev").addEventListener("click", () => guideDelta(-1));
document.getElementById("guide-skip").addEventListener("click", closeGuide);
document.getElementById("guide-open").addEventListener("click", () => openGuide(0));
document.addEventListener("keydown", (e) => {
  if (e.target && ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.key === "ArrowRight") guideDelta(1);
  if (e.key === "ArrowLeft") guideDelta(-1);
  if (e.key === "Escape") closeGuide();
});

bindOrbit(world);
battle.nextWave();
paintGuide();
beginRide();
render();
requestAnimationFrame(loop);
