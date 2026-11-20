/** Shore-sea SA: tracks, intel, collab on envelopes. Same theater as COP. */

import {
  BMD,
  SAM,
  SAM_RIM,
  SHIP,
  SHIP_HDG,
  C2,
  COR,
  RADAR,
  SEARCH,
  JAM,
  N_MAX,
  K_MAX,
  M_SAM0,
  TICK_S,
  createBattle,
  living,
  sphereSlots,
  dist,
  zoneOf,
  verdict,
  TAU,
  intelOf,
} from "./battle.js?v=25";
import { headingOf } from "./icons.js";
import { shoreZ } from "./scene-gis.js?v=1";

const battle = createBattle();
const { state } = battle;

const COMPOSE = document.body.hasAttribute("data-compose");
const CAM0 = COMPOSE
  ? { yaw: 0.36, pitch: 0.42, dist: 9.2 }
  : { yaw: 0.5, pitch: 0.44, dist: 9.0 };
const LOOK0 = COMPOSE ? [-0.18, 0.32, 0.12] : [0.22, 0.32, 0.2];
const cam = { yaw: CAM0.yaw, pitch: CAM0.pitch, dist: CAM0.dist };
const look = LOOK0.slice();
let view = null;
const afterRenderFns = [];

export function onAfterRender(fn) {
  afterRenderFns.push(fn);
}

function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
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
  view = { eye, forward, right, up, f: 0.9 * Math.min(W, H), W, H };
}

function P(w) {
  const v = sub(w, view.eye);
  const x = dot(v, view.right);
  const y = dot(v, view.up);
  const z = dot(v, view.forward);
  const d = Math.max(z, 0.45);
  const cx = COMPOSE ? view.W * 0.36 : view.W * 0.5;
  return [cx + (view.f * x) / d, view.H * 0.62 - (view.f * y) / d, z, view.f / d];
}

function convexHull2(pts) {
  const p = pts.map((q) => [q[0], q[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const q of p) {
    while (lower.length >= 2 && cr(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  const upper = [];
  for (let i = p.length - 1; i >= 0; i -= 1) {
    const q = p[i];
    while (upper.length >= 2 && cr(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
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

function ringAt(b, yFrac) {
  const th = yFrac * (Math.PI / 2);
  const rr = b.r * Math.sin(th);
  const y = b.c[1] + b.r * Math.cos(th);
  const ring = [];
  for (let i = 0; i <= 96; i += 1) {
    const phi = (i / 96) * Math.PI * 2;
    ring.push(P([b.c[0] + rr * Math.cos(phi), y, b.c[2] + rr * Math.sin(phi)]));
  }
  return ring;
}

function groundRing(b) {
  const ring = [];
  for (let i = 0; i <= 96; i += 1) {
    const phi = (i / 96) * Math.PI * 2;
    ring.push(P([b.c[0] + b.r * Math.cos(phi), 0, b.c[2] + b.r * Math.sin(phi)]));
  }
  return ring;
}

function hemiPoint(b, th, phi) {
  return [
    b.c[0] + b.r * Math.sin(th) * Math.cos(phi),
    b.c[1] + b.r * Math.cos(th),
    b.c[2] + b.r * Math.sin(th) * Math.sin(phi),
  ];
}

function oblatePoint(b, th, phi, h) {
  return [
    b.c[0] + b.r * Math.sin(th) * Math.cos(phi),
    Math.max(0, b.c[1] + b.r * h * Math.cos(th)),
    b.c[2] + b.r * Math.sin(th) * Math.sin(phi),
  ];
}

function drawRim(ctx, b, stroke, label) {
  const foot = groundRing(b);
  ctx.fillStyle = "rgba(126,200,194,0.04)";
  fillPoly(ctx, foot);
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = stroke;
  ctx.globalAlpha = 0.42;
  ctx.lineWidth = 1.2;
  strokePoly(ctx, foot);
  ctx.globalAlpha = 1;
  ctx.setLineDash([]);
  if (!label) return;
  const lab = P([b.c[0], 0.04, b.c[2] + b.r * 0.08]);
  ctx.fillStyle = "rgba(186, 220, 216, 0.75)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, lab[0], lab[1]);
}

function hemiHull(b, nPhi = 96, nTh = 24) {
  const cloud = [];
  for (let m = 0; m < nPhi; m += 1) {
    const phi = (m / nPhi) * Math.PI * 2;
    for (let i = 0; i <= nTh; i += 1) cloud.push(P(hemiPoint(b, (i / nTh) * (Math.PI / 2), phi)));
  }
  return convexHull2(cloud);
}

function drawEngage(ctx, b, fill, stroke, label) {
  const foot = groundRing(b);
  ctx.fillStyle = fill;
  fillPoly(ctx, foot);

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  const M = 16;
  for (let m = 0; m < M; m += 1) {
    const phi = (m / M) * Math.PI * 2;
    const eq = hemiPoint(b, Math.PI / 2, phi);
    const back = dot([Math.cos(phi), 0, Math.sin(phi)], sub(view.eye, eq)) < 0;
    const arc = [];
    for (let i = 0; i <= 48; i += 1) arc.push(P(hemiPoint(b, (i / 48) * (Math.PI / 2), phi)));
    ctx.setLineDash(back ? [3.5, 4] : []);
    ctx.globalAlpha = back ? 0.07 : 0.14;
    ctx.strokeStyle = stroke;
    ctx.lineWidth = back ? 0.85 : 1.05;
    strokePoly(ctx, arc, false);
  }
  ctx.globalAlpha = 0.16;
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1;
  [0.35, 0.7].forEach((frac) => strokePoly(ctx, ringAt(b, frac)));
  ctx.setLineDash([]);
  ctx.globalAlpha = 0.32;
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = stroke;
  strokePoly(ctx, foot);

  ctx.setLineDash([]);
  ctx.lineWidth = 1.55;
  ctx.globalAlpha = 0.4;
  strokePoly(ctx, hemiHull(b));

  ctx.restore();

  const base = P([b.c[0], 0, b.c[2]]);
  const pole = P([b.c[0], b.c[1] + b.r, b.c[2]]);
  ctx.strokeStyle = stroke;
  ctx.globalAlpha = 0.2;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(base[0], base[1]);
  ctx.lineTo(pole[0], pole[1]);
  ctx.stroke();
  ctx.globalAlpha = 0.45;
  ctx.beginPath();
  ctx.arc(base[0], base[1], 2.8, 0, Math.PI * 2);
  ctx.fillStyle = stroke;
  ctx.fill();
  ctx.globalAlpha = 1;

  if (!label) return;
  ctx.fillStyle = "rgba(236,230,212,0.58)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillText(label, pole[0], pole[1] - 4);
}

function drawDetect(ctx, b, label) {
  const h = 0.22;
  const foot = groundRing(b);
  ctx.fillStyle = "rgba(216,210,196,0.07)";
  fillPoly(ctx, foot);

  ctx.setLineDash([4, 5]);
  ctx.strokeStyle = "rgba(216,210,196,0.4)";
  ctx.lineWidth = 1;
  [0.4, 0.7, 1].forEach((frac) => {
    const ring = [];
    for (let i = 0; i <= 64; i += 1) {
      const phi = (i / 64) * Math.PI * 2;
      ring.push(P([b.c[0] + b.r * frac * Math.cos(phi), 0.02, b.c[2] + b.r * frac * Math.sin(phi)]));
    }
    strokePoly(ctx, ring);
  });

  ctx.setLineDash([]);
  ctx.strokeStyle = "rgba(216,210,196,0.55)";
  ctx.lineWidth = 1;
  for (let m = 0; m < 6; m += 1) {
    const phi = (m / 6) * Math.PI * 2;
    const arc = [];
    for (let i = 0; i <= 14; i += 1) arc.push(P(oblatePoint(b, (i / 14) * (Math.PI / 2), phi, h)));
    strokePoly(ctx, arc, false);
  }
  const cap = [];
  for (let i = 0; i <= 48; i += 1) cap.push(P(oblatePoint(b, 0.7, (i / 48) * Math.PI * 2, h)));
  ctx.globalAlpha = 0.45;
  strokePoly(ctx, cap);
  ctx.globalAlpha = 1;

  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = "rgba(216,210,196,0.65)";
  ctx.lineWidth = 1.25;
  strokePoly(ctx, foot);
  ctx.setLineDash([]);

  if (!label) return;
  const lab = P([b.c[0], 0.04, b.c[2] - b.r * 0.18]);
  ctx.fillStyle = "#ece6d4";
  ctx.font = "12px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, lab[0], lab[1]);
}

function drawBox(ctx, W, H) {
  const y0 = COR.y0;
  const y1 = COR.y1;
  const c = [
    [COR.x0, y0, COR.z0],
    [COR.x1, y0, COR.z0],
    [COR.x1, y0, COR.z1],
    [COR.x0, y0, COR.z1],
  ];
  const u = [
    [COR.x0, y1, COR.z0],
    [COR.x1, y1, COR.z0],
    [COR.x1, y1, COR.z1],
    [COR.x0, y1, COR.z1],
  ];
  const pc = c.map((p) => P(p));
  const pu = u.map((p) => P(p));
  ctx.fillStyle = "rgba(196, 80, 70, 0.14)";
  fillPoly(ctx, pc);
  ctx.strokeStyle = "rgba(213, 106, 86, 0.4)";
  ctx.lineWidth = 1.15;
  strokePoly(ctx, pc);
  strokePoly(ctx, pu);
  for (let i = 0; i < 4; i += 1) {
    ctx.beginPath();
    ctx.moveTo(pc[i][0], pc[i][1]);
    ctx.lineTo(pu[i][0], pu[i][1]);
    ctx.stroke();
  }
  const lab = P([COR.x0, y0, (COR.z0 + COR.z1) / 2]);
  ctx.fillStyle = "#d56a56";
  ctx.globalAlpha = 0.7;
  ctx.font = "12px Palatino Linotype, Palatino, Songti SC, serif";
  ctx.textAlign = "right";
  ctx.fillText("走廊", lab[0] - 8, lab[1]);
  ctx.globalAlpha = 1;
}

const SLAB = { x0: -6.6, x1: 6.6, z0: -6.2, z1: 4.8, thick: 0.78 };
const HAZE = [78, 90, 102];

function lightFrom(azDeg, altDeg = 42) {
  const az = (azDeg * Math.PI) / 180;
  const alt = (altDeg * Math.PI) / 180;
  return norm([Math.sin(az) * Math.cos(alt), Math.sin(alt), Math.cos(az) * Math.cos(alt)]);
}

/** Imhof / GDAL multidirectional: 225, 270, 315, 360. */
const LIGHTS = [
  [lightFrom(225), 0.18],
  [lightFrom(270), 0.22],
  [lightFrom(315), 0.38],
  [lightFrom(360), 0.22],
];

const PADS = [
  [C2[0], C2[2], 0.62],
  [BMD.c[0], BMD.c[2], 0.72],
  [SAM.c[0], SAM.c[2], 0.58],
  [RADAR.shore[0], RADAR.shore[2], 0.48],
  [RADAR.sam[0], RADAR.sam[2], 0.48],
];

function padW(x, z) {
  let w = 0;
  for (const [px, pz, r] of PADS) {
    const d = Math.hypot(x - px, z - pz);
    if (d < r) w = Math.max(w, 1 - d / r);
  }
  return w;
}

function fract(v) {
  return v - Math.floor(v);
}

function hash2(x, z) {
  return fract(Math.sin(x * 127.1 + z * 311.7) * 43758.5453);
}

function fbm(x, z) {
  return (
    0.55 * Math.sin(x * 0.85 + z * 0.42) * Math.cos(x * 0.31 - z * 0.7) +
    0.28 * Math.sin(x * 1.9 + z * 1.15 + 0.6) +
    0.12 * Math.sin(x * 4.1 - z * 2.4 + 1.3)
  );
}

/** Display relief only. Pads stay flat. Ridge stays behind C2, below envelope scale. */
function landH(x, z) {
  const s = shoreZ(x);
  if (z <= s + 0.015) return 0;
  const inland = z - s;
  const berm = 0.028 * Math.min(1, inland / 0.38);
  const dunes = 0.018 * (0.5 + 0.5 * Math.sin(x * 2.4 + 0.4)) * Math.min(1, inland / 0.7) * Math.max(0, 1 - inland / 1.6);
  const roll = 0.07 * (0.55 + 0.45 * fbm(x, z)) * Math.min(1, inland / 1.05);
  const micro = 0.012 * Math.sin(x * 5.2) * Math.sin(z * 4.4) * Math.min(1, inland / 0.5);
  const ridge =
    z > 3.35 ? 0.42 * Math.min(1, (z - 3.35) / 1.05) * (0.55 + 0.45 * Math.sin(x * 0.58 + 1.05)) : 0;
  const westHill =
    x < -3.15 && z > 2.55
      ? 0.22 * Math.min(1, (-3.15 - x) / 1.4) * Math.min(1, (z - 2.55) / 1.1)
      : 0;
  return Math.max(0, (berm + dunes + roll + micro + ridge + westHill) * (1 - padW(x, z) * 0.95));
}

function mix3(a, b, t) {
  const u = Math.max(0, Math.min(1, t));
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

function clamp255(v) {
  return Math.max(0, Math.min(255, Math.round(v)));
}

function rgbStr(c) {
  return `rgb(${clamp255(c[0])},${clamp255(c[1])},${clamp255(c[2])})`;
}

function seaDepth(x, z) {
  const s = shoreZ(x);
  if (z >= s) return 0;
  const off = s - z;
  const bar = 0.022 * Math.max(0, Math.sin(off * 2.15 + x * 0.45));
  return Math.min(0.46, Math.max(0.018, 0.032 + off * 0.078 - bar + 0.018 * fbm(x, z)));
}

function bedY(x, z) {
  const s = shoreZ(x);
  if (z >= s + 0.01) return landH(x, z);
  return -seaDepth(x, z);
}

function slopeAt(x, z) {
  const e = 0.08;
  const dhx = (bedY(x + e, z) - bedY(x - e, z)) / (2 * e);
  const dhz = (bedY(x, z + e) - bedY(x, z - e)) / (2 * e);
  return Math.hypot(dhx, dhz);
}

function normalAt(x, z) {
  const e = 0.07;
  const k = z < shoreZ(x) ? 1.5 : 2.6;
  return norm([
    (bedY(x - e, z) - bedY(x + e, z)) * k,
    2 * e,
    (bedY(x, z - e) - bedY(x, z + e)) * k,
  ]);
}

function shadeMulti(n) {
  let s = 0;
  let w = 0;
  for (const [L, wt] of LIGHTS) {
    s += wt * Math.max(0.02, dot(n, L));
    w += wt;
  }
  return 0.16 + 0.96 * (s / w);
}

function aoAt(x, z) {
  const h = bedY(x, z);
  const dirs = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
    [0.7, 0.7],
    [-0.7, 0.7],
    [0.7, -0.7],
    [-0.7, -0.7],
  ];
  function occ(r) {
    let s = 0;
    for (const [dx, dz] of dirs) {
      s += Math.max(0, (bedY(x + dx * r, z + dz * r) - h) / r);
    }
    return Math.min(1, s / dirs.length);
  }
  let ao = 1 - 0.18 * occ(0.15) - 0.12 * occ(0.4);
  const { x0, x1, z0, z1 } = SLAB;
  const edge = Math.min(x - x0, x1 - x, z - z0, z1 - z);
  if (edge < 0.2) ao *= 0.7 + 0.3 * (edge / 0.2);
  return ao;
}

function hazeMix(c, depth) {
  const t = Math.max(0, Math.min(1, (depth - 5.2) / 12));
  const mid = 96;
  const crushed = [
    c[0] + (mid - c[0]) * t * 0.48,
    c[1] + (mid - c[1]) * t * 0.48,
    c[2] + (mid - c[2]) * t * 0.48,
  ];
  return mix3(crushed, HAZE, t * 0.3);
}

function albedo(x, z) {
  const s = shoreZ(x);
  const dz = z - s;
  const sl = slopeAt(x, z);
  const h = landH(x, z);
  const cover = 0.5 + 0.5 * fbm(x * 1.15, z * 1.15);
  if (dz < 0) return mix3([170, 150, 114], [40, 60, 68], Math.min(1, -dz / 2.1));
  let c;
  if (dz < 0.1) c = mix3([86, 70, 54], [118, 98, 76], dz / 0.1);
  else if (dz < 0.2) c = mix3([210, 190, 148], [198, 178, 136], (dz - 0.1) / 0.1);
  else if (dz < 0.48) c = mix3([198, 178, 136], [128, 112, 78], (dz - 0.2) / 0.28);
  else {
    c = mix3([74, 100, 58], [48, 70, 42], cover);
    if (h > 0.18) c = mix3(c, [130, 124, 106], Math.min(1, (h - 0.18) / 0.2) * 0.45);
  }
  if (dz > 0.22) c = mix3(c, [126, 120, 102], Math.max(0, Math.min(1, (sl - 0.05) / 0.14)));
  const pw = padW(x, z);
  if (pw > 0.2) c = mix3(c, [96, 92, 74], (pw - 0.2) / 0.8);
  return c;
}

function groundColor(x, z, depth) {
  let c = albedo(x, z);
  const n = normalAt(x, z);
  let sh = shadeMulti(n) * aoAt(x, z) * ridgeLight(x, z, n);
  c = [c[0] * sh, c[1] * sh, c[2] * sh];
  return rgbStr(hazeMix(c, depth));
}

/** Imhof ridge lighting: crest picks a grazing highlight, not a texture. */
function ridgeLight(x, z, n) {
  const inland = z - shoreZ(x);
  const west = x < -3.1 && z > 2.45;
  if (inland < 2.7 && !west) return 1;
  const h = landH(x, z);
  const peak = h - 0.5 * (landH(x - 0.14, z) + landH(x + 0.14, z));
  const sl = slopeAt(x, z);
  const crest = Math.max(0, Math.min(1, peak / 0.035)) * Math.max(0, Math.min(1, (sl - 0.04) / 0.1));
  const lift = west ? 0.22 : 0.32;
  const graze = Math.max(0, 1 - n[1]);
  return 1 + lift * crest * (0.45 + 0.55 * graze);
}

function waterFill(x, z, depth) {
  const s = shoreZ(x);
  const dz = z - s;
  const d = seaDepth(x, z);
  let a = 0.1 + 0.28 * Math.min(1, d / 0.4);
  if (dz > -0.4) a *= 0.55;
  let c = mix3([86, 156, 152], [22, 52, 72], Math.min(1, d / 0.42));
  if (dz > -0.08 && dz < 0) c = mix3(c, [176, 198, 196], (dz + 0.08) / 0.08 * 0.35);
  c = hazeMix(c, depth);
  return `rgba(${clamp255(c[0])},${clamp255(c[1])},${clamp255(c[2])},${a.toFixed(3)})`;
}

function drawSlabWall(ctx, topA, topB, yb, fillRock) {
  const n = 36;
  const top = [];
  const bot = [];
  for (let i = 0; i <= n; i += 1) {
    const t = i / n;
    const x = topA[0] + (topB[0] - topA[0]) * t;
    const z = topA[2] + (topB[2] - topA[2]) * t;
    top.push(P([x, bedY(x, z), z]));
    bot.push(P([x, yb, z]));
  }
  const face = top.concat(bot.reverse());
  ctx.fillStyle = fillRock;
  fillPoly(ctx, face);
  const bands = [
    [-0.04, "rgba(8, 6, 4, 0.28)"],
    [-0.05, "#6a5e4c"],
    [-0.16, "#53483c"],
    [-0.32, "#3e3830"],
    [-0.5, "#2c2926"],
  ];
  for (const [y0, col] of bands) {
    const band = [];
    for (let i = 0; i <= n; i += 1) {
      const t = i / n;
      const x = topA[0] + (topB[0] - topA[0]) * t;
      const z = topA[2] + (topB[2] - topA[2]) * t;
      const wobble = 0.03 * Math.sin(x * 2.4 + z * 1.6);
      band.push(P([x, Math.min(bedY(x, z), y0 + wobble), z]));
    }
    for (let i = n; i >= 0; i -= 1) {
      const t = i / n;
      const x = topA[0] + (topB[0] - topA[0]) * t;
      const z = topA[2] + (topB[2] - topA[2]) * t;
      band.push(P([x, yb, z]));
    }
    ctx.fillStyle = col;
    fillPoly(ctx, band);
  }
  ctx.strokeStyle = "rgba(18, 16, 14, 0.5)";
  ctx.lineWidth = 1;
  strokePoly(ctx, face);
}

function drawSeaCut(ctx, x0, x1, z) {
  const n = 28;
  const poly = [];
  for (let i = 0; i <= n; i += 1) {
    const x = x0 + ((x1 - x0) * i) / n;
    poly.push(P([x, 0, z]));
  }
  for (let i = n; i >= 0; i -= 1) {
    const x = x0 + ((x1 - x0) * i) / n;
    poly.push(P([x, -seaDepth(x, z), z]));
  }
  ctx.fillStyle = "rgba(48, 122, 136, 0.48)";
  fillPoly(ctx, poly);
}

const gis = { key: "", tiles: [], water: [], foam: [], scrub: [] };

function gisRebuild() {
  const key = `m2|${cam.dist.toFixed(3)}|${view.W}|${view.H}`;
  if (key === gis.key) return;
  gis.key = key;
  const { x0, x1, z0, z1 } = SLAB;
  const tiles = [];
  const water = [];
  const close = cam.dist < 11;
  const xsLand = close ? 0.09 : 0.12;
  for (let x = x0; x < x1 - 1e-6; x += xsLand) {
    const x2 = Math.min(x1, x + xsLand);
    for (let z = z0; z < z1 - 1e-6; ) {
      const xm0 = (x + x2) / 2;
      const dShore = Math.abs(z - shoreZ(xm0));
      let zs = close ? 0.13 : 0.18;
      if (dShore < 0.35) zs = close ? 0.042 : 0.06;
      else if (dShore < 0.85) zs = close ? 0.06 : 0.085;
      else if (dShore < 1.55) zs = close ? 0.085 : 0.12;
      if (z > 3.15) zs = Math.min(zs, close ? 0.07 : 0.1);
      if (x < -3.05 && z > 2.45) zs = Math.min(zs, close ? 0.07 : 0.1);
      const z2 = Math.min(z1, z + zs);
      const xm = (x + x2) / 2;
      const zm = (z + z2) / 2;
      const ym = bedY(xm, zm);
      const q = P([xm, ym, zm]);
      tiles.push({
        depth: q[2],
        color: groundColor(xm, zm, q[2]),
        quad: [
          P([x, bedY(x, z), z]),
          P([x2, bedY(x2, z), z]),
          P([x2, bedY(x2, z2), z2]),
          P([x, bedY(x, z2), z2]),
        ],
      });
      if (zm < shoreZ(xm) - 0.02) {
        const wq = P([xm, 0.01, zm]);
        water.push({
          depth: wq[2],
          color: waterFill(xm, zm, wq[2]),
          quad: [P([x, 0.01, z]), P([x2, 0.01, z]), P([x2, 0.01, z2]), P([x, 0.01, z2])],
        });
      }
      z = z2;
    }
  }
  tiles.sort((a, b) => b.depth - a.depth);
  water.sort((a, b) => b.depth - a.depth);
  gis.tiles = tiles;
  gis.water = water;

  gis.foam = [];
  for (let x = x0; x <= x1 + 1e-6; x += 0.1) {
    gis.foam.push(P([x, 0.014, shoreZ(x) - 0.035]));
  }

  const scrub = [];
  for (let x = x0 + 0.25; x < x1 - 0.2; x += 0.3) {
    for (let z = 0.7; z < z1 - 0.15; z += 0.3) {
      if (z - shoreZ(x) < 0.55 || padW(x, z) > 0.35 || slopeAt(x, z) > 0.16) continue;
      if (hash2(x, z) < 0.62) continue;
      const q = P([x + (hash2(z, x) - 0.5) * 0.12, landH(x, z) + 0.03, z]);
      scrub.push({ q, depth: q[2], s: 2.0 + hash2(x + 2, z) * 2.2 });
    }
  }
  scrub.sort((a, b) => b.depth - a.depth);
  gis.scrub = scrub;
}

function drapeY(x, z) {
  return z >= shoreZ(x) - 0.02 ? landH(x, z) + 0.014 : 0.012;
}

function drapeRing(cx, cz, r, n = 80) {
  const ring = [];
  for (let i = 0; i <= n; i += 1) {
    const phi = (i / n) * Math.PI * 2;
    const x = cx + r * Math.cos(phi);
    const z = cz + r * Math.sin(phi);
    ring.push(P([x, drapeY(x, z), z]));
  }
  return ring;
}

function drawHemiContact(ctx, b, strength) {
  const foot = drapeRing(b.c[0], b.c[2], b.r);
  ctx.fillStyle = `rgba(8, 10, 12, ${(0.055 * strength).toFixed(3)})`;
  fillPoly(ctx, foot);
  ctx.strokeStyle = `rgba(6, 8, 10, ${(0.2 * strength).toFixed(3)})`;
  ctx.lineWidth = 8;
  strokePoly(ctx, foot);
  ctx.strokeStyle = `rgba(6, 8, 10, ${(0.1 * strength).toFixed(3)})`;
  ctx.lineWidth = 16;
  strokePoly(ctx, drapeRing(b.c[0], b.c[2], b.r * 1.02, 80));
}

function drawContactShadows(ctx) {
  drawHemiContact(ctx, BMD, 0.95);
  drawHemiContact(ctx, SAM, 1.05);
  const box = [
    P([COR.x0, drapeY(COR.x0, COR.z0), COR.z0]),
    P([COR.x1, drapeY(COR.x1, COR.z0), COR.z0]),
    P([COR.x1, drapeY(COR.x1, COR.z1), COR.z1]),
    P([COR.x0, drapeY(COR.x0, COR.z1), COR.z1]),
  ];
  ctx.fillStyle = "rgba(8, 10, 12, 0.06)";
  fillPoly(ctx, box);
  ctx.strokeStyle = "rgba(6, 8, 10, 0.16)";
  ctx.lineWidth = 6;
  strokePoly(ctx, box);
  drawHemiContact(ctx, SHIP, 0.55);
}

function drawLand(ctx) {
  gisRebuild();
  const { x0, x1, z0, z1, thick } = SLAB;
  const yb = -thick;
  const sorted = [
    { a: [x0, 0, z0], b: [x1, 0, z0], sea: true },
    { a: [x1, 0, z0], b: [x1, 0, z1], sea: false },
    { a: [x1, 0, z1], b: [x0, 0, z1], sea: false },
    { a: [x0, 0, z1], b: [x0, 0, z0], sea: false },
  ]
    .map((w) => ({
      ...w,
      depth: P([(w.a[0] + w.b[0]) / 2, yb / 2, (w.a[2] + w.b[2]) / 2])[2],
    }))
    .sort((a, b) => b.depth - a.depth);

  function paintWall(w) {
    drawSlabWall(ctx, w.a, w.b, yb, "#3c3730");
    if (w.sea) drawSeaCut(ctx, w.a[0], w.b[0], w.a[2]);
  }
  sorted.slice(0, 2).forEach(paintWall);

  for (const t of gis.tiles) {
    ctx.fillStyle = t.color;
    fillPoly(ctx, t.quad);
  }
  for (const t of gis.water) {
    ctx.fillStyle = t.color;
    fillPoly(ctx, t.quad);
  }

  sorted.slice(2).forEach(paintWall);

  const rim = [];
  [
    ...edgeWalk(x0, z0, x1, z0),
    ...edgeWalk(x1, z0, x1, z1),
    ...edgeWalk(x1, z1, x0, z1),
    ...edgeWalk(x0, z1, x0, z0),
  ].forEach(([x, z]) => rim.push(P([x, bedY(x, z) + 0.008, z])));
  ctx.strokeStyle = "rgba(196, 176, 140, 0.5)";
  ctx.lineWidth = 1.25;
  strokePoly(ctx, rim);

  ctx.strokeStyle = "rgba(228, 222, 210, 0.28)";
  ctx.lineWidth = 0.9;
  strokePoly(ctx, gis.foam, false);

  ctx.fillStyle = "rgba(28, 46, 30, 0.82)";
  for (const t of gis.scrub) {
    ctx.beginPath();
    ctx.ellipse(t.q[0], t.q[1], t.s * 0.72, t.s * 0.42, -0.4, 0, Math.PI * 2);
    ctx.fill();
  }

  const seaLab = P([-4.2, 0.02, -3.1]);
  const landLab = P([4.2, landH(4.2, 2.8), 2.8]);
  ctx.font = "12px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = "#8aa0b4";
  ctx.fillText("海", seaLab[0], seaLab[1]);
  ctx.fillStyle = "#b8c4a4";
  ctx.fillText("陆", landLab[0], landLab[1]);
  const sh = P([5.55, 0.03, shoreZ(5.55)]);
  ctx.fillStyle = "#ece6d4";
  ctx.textAlign = "left";
  ctx.fillText("岸线  距岸 0", sh[0] + 8, sh[1] - 6);
}

function edgeWalk(x0, z0, x1, z1) {
  const n = 18;
  const pts = [];
  for (let i = 0; i < n; i += 1) {
    const t = i / n;
    pts.push([x0 + (x1 - x0) * t, z0 + (z1 - z0) * t]);
  }
  return pts;
}

function drawGrid(ctx, W, H) {
  const { x0, x1, z0, z1 } = SLAB;
  ctx.lineWidth = 1;
  ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = "#8d8878";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const gy = (x, z) => landH(x, z) + 0.012;
  const onLand = (x, z) => z >= shoreZ(x) - 0.02;
  const landStroke = (g) => (g === 0 ? "rgba(232,226,208,0.09)" : "rgba(236,230,212,0.022)");

  for (let g = Math.ceil(x0); g <= Math.floor(x1); g += 1) {
    ctx.strokeStyle = landStroke(g);
    ctx.beginPath();
    let drawing = false;
    for (let z = z0; z <= z1 + 1e-6; z += 0.2) {
      const zz = Math.min(z, z1);
      if (!onLand(g, zz)) {
        drawing = false;
        continue;
      }
      const q = P([g, gy(g, zz), zz]);
      if (drawing) ctx.lineTo(q[0], q[1]);
      else {
        ctx.moveTo(q[0], q[1]);
        drawing = true;
      }
    }
    ctx.stroke();
  }
  for (let g = Math.ceil(z0); g <= Math.floor(z1); g += 1) {
    if (g < 0) continue;
    ctx.strokeStyle = landStroke(g);
    ctx.beginPath();
    let drawing = false;
    for (let x = x0; x <= x1 + 1e-6; x += 0.2) {
      const xx = Math.min(x, x1);
      if (!onLand(xx, g)) {
        drawing = false;
        continue;
      }
      const q = P([xx, gy(xx, g), g]);
      if (drawing) ctx.lineTo(q[0], q[1]);
      else {
        ctx.moveTo(q[0], q[1]);
        drawing = true;
      }
    }
    ctx.stroke();
  }

  for (let g = Math.ceil(z0); g <= Math.floor(z1); g += 1) {
    if (g === 0 || g % 2 !== 0) continue;
    if (g > 0) {
      const lab = P([x0 + 0.18, gy(x0 + 0.18, g), g]);
      ctx.fillStyle = "#8d8878";
      ctx.fillText(`${g}`, lab[0], lab[1]);
    } else {
      const tick = P([x0 + 0.04, 0.03, g]);
      ctx.fillStyle = "#6a7a88";
      ctx.fillText(`${g}`, tick[0], tick[1]);
    }
  }
  for (let g = Math.ceil(x0); g <= Math.floor(x1); g += 1) {
    if (g !== 0 && g % 2 === 0) {
      const east = P([g, 0.03, z0 + 0.08]);
      ctx.fillStyle = "#6a7a88";
      ctx.fillText(`${g}`, east[0], east[1]);
    }
  }
  const eastLab = P([5.6, 0.03, z0 + 0.18]);
  const shoreLab = P([x0 + 0.22, gy(x0 + 0.22, 4.4), 4.4]);
  ctx.fillStyle = "#8d8878";
  ctx.fillText("东距 km", eastLab[0], eastLab[1] + 12);
  ctx.fillText("距岸 km", shoreLab[0], shoreLab[1] - 12);
}

function drawAxis(ctx, W, H, to, color, label) {
  const a = P([0, 0, 0]);
  const b = P(to);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.35;
  ctx.setLineDash([7, 5]);
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.arc(b[0], b[1], 2.8, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = "12px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(label, b[0] + 7, b[1] - 2);
}

function drawFrame(ctx, W, H) {
  drawAxis(ctx, W, H, [6.0, 0, 0], "#d3926a", "东 +x");
  drawAxis(ctx, W, H, [0, 0, 4.6], "#7aa0c4", "距岸 +z · 000 陆");
  drawAxis(ctx, W, H, [0, 0, -5.8], "#6a8899", "海 −z · 180");
  drawAxis(ctx, W, H, [0, 3.2, 0], "#cbb56a", "高 +y");
  ctx.fillStyle = "#cbb56a";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  [0.4, 1, 2, 3].forEach((h) => {
    const q = P([0, h, 0]);
    const t = P([0.14, h, 0]);
    ctx.strokeStyle = "rgba(203,181,106,0.45)";
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(q[0], q[1]);
    ctx.lineTo(t[0], t[1]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#cbb56a";
    ctx.fillText(h < 1 ? `${Math.round(h * 1000)} m` : `${h} km`, t[0] + 4, t[1]);
  });
  const o = P([0, 0, 0]);
  ctx.fillStyle = "#d3926a";
  ctx.beginPath();
  ctx.arc(o[0], o[1], 3.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = "12px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = "#d3926a";
  ctx.fillText("O 岸线中点", o[0] + 8, o[1] + 14);
  const bar0 = P([0.4, 0, -5.6]);
  const bar1 = P([2.4, 0, -5.6]);
  ctx.strokeStyle = "#d3926a";
  ctx.lineWidth = 1.4;
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(bar0[0], bar0[1]);
  ctx.lineTo(bar1[0], bar1[1]);
  ctx.stroke();
  ctx.fillStyle = "#d3926a";
  ctx.textAlign = "center";
  ctx.fillText("2 km", (bar0[0] + bar1[0]) / 2, bar0[1] - 8);
}

function drawLink(ctx, a, b, on) {
  const pa = P(a);
  const pb = P(b);
  ctx.setLineDash(on ? [4, 5] : [3, 6]);
  ctx.strokeStyle = on ? "rgba(226,195,106,0.55)" : "rgba(213,106,86,0.4)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pa[0], pa[1]);
  ctx.lineTo(pb[0], pb[1]);
  ctx.stroke();
  ctx.setLineDash([]);
}

function scanT() {
  return performance.now() / 1000;
}

function searchSweepAz() {
  const period = state.jammed ? 7.2 : 4.2;
  return SEARCH.az - SEARCH.half + ((scanT() / period) % 1) * 2 * SEARCH.half;
}

function azBasis(az) {
  const fx = Math.sin(az);
  const fz = Math.cos(az);
  return { fx, fz, sx: fz, sz: -fx };
}

const SEARCH_H = 0.55;

function searchPt(az, r, y) {
  return [SEARCH.c[0] + r * Math.sin(az), y, SEARCH.c[2] + r * Math.cos(az)];
}

function drawGroundRipples(ctx, c, rMax, t, period, color, n = 3) {
  for (let i = 0; i < n; i += 1) {
    const u = ((t / period + i / n) % 1 + 1) % 1;
    const rr = Math.max(0.08, u * rMax);
    const ring = [];
    for (let k = 0; k <= 48; k += 1) {
      const phi = (k / 48) * Math.PI * 2;
      ring.push(P([c[0] + rr * Math.cos(phi), 0.04, c[2] + rr * Math.sin(phi)]));
    }
    ctx.globalAlpha = (1 - u) * 0.55;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([]);
    strokePoly(ctx, ring);
  }
  ctx.globalAlpha = 1;
}

function drawFanRipples(ctx, y, t, period, color) {
  const az0 = SEARCH.az - SEARCH.half;
  const az1 = SEARCH.az + SEARCH.half;
  for (let i = 0; i < 3; i += 1) {
    const u = ((t / period + i / 3) % 1 + 1) % 1;
    const rr = Math.max(0.12, u * SEARCH.r);
    ctx.globalAlpha = (1 - u) * 0.16;
    ctx.beginPath();
    for (let k = 0; k <= 24; k += 1) {
      const q = P(searchPt(az0 + ((az1 - az0) * k) / 24, rr, y));
      k ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.1;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawSearchVolume(ctx) {
  const jammed = state.jammed;
  const az0 = SEARCH.az - SEARCH.half;
  const az1 = SEARCH.az + SEARCH.half;
  const r = SEARCH.r;
  const h = SEARCH_H;
  const y0 = 0.03;
  const fill = jammed ? "rgba(213,106,86,0.04)" : "rgba(226,195,106,0.045)";
  const apex = P([SEARCH.c[0], SEARCH.c[1], SEARCH.c[2]]);
  const floor = [P([SEARCH.c[0], y0, SEARCH.c[2]])];
  const lid = [P([SEARCH.c[0], h, SEARCH.c[2]])];
  for (let i = 0; i <= 24; i += 1) {
    const a = az0 + ((az1 - az0) * i) / 24;
    floor.push(P(searchPt(a, r, y0)));
    lid.push(P(searchPt(a, r, h)));
  }
  ctx.fillStyle = fill;
  fillPoly(ctx, floor);
  ctx.fillStyle = jammed ? "rgba(213,106,86,0.03)" : "rgba(226,195,106,0.035)";
  fillPoly(ctx, lid);

  function wall(az) {
    const far0 = P(searchPt(az, r, y0));
    const farH = P(searchPt(az, r, h));
    const top = P([SEARCH.c[0], h, SEARCH.c[2]]);
    ctx.beginPath();
    ctx.moveTo(apex[0], apex[1]);
    ctx.lineTo(far0[0], far0[1]);
    ctx.lineTo(farH[0], farH[1]);
    ctx.lineTo(top[0], top[1]);
    ctx.closePath();
    ctx.fillStyle = jammed ? "rgba(213,106,86,0.03)" : "rgba(226,195,106,0.03)";
    ctx.fill();
  }
  wall(az0);
  wall(az1);

  ctx.beginPath();
  for (let i = 0; i <= 16; i += 1) {
    const q = P(searchPt(az0 + ((az1 - az0) * i) / 16, r, y0));
    i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
  }
  for (let i = 16; i >= 0; i -= 1) {
    const q = P(searchPt(az0 + ((az1 - az0) * i) / 16, r, h));
    ctx.lineTo(q[0], q[1]);
  }
  ctx.closePath();
  ctx.fillStyle = jammed ? "rgba(213,106,86,0.035)" : "rgba(226,195,106,0.04)";
  ctx.fill();

  const lab = P(searchPt(SEARCH.az, r * 0.52, h));
  ctx.fillStyle = jammed ? "rgba(213,106,86,0.9)" : "rgba(226,195,106,0.9)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillText(jammed ? "岸搜 · 受扰" : "岸搜", lab[0], lab[1] - 4);
  const lab2 = P(searchPt(SEARCH.az, r * 0.58, y0));
  ctx.fillStyle = "rgba(226,195,106,0.45)";
  ctx.textBaseline = "top";
  ctx.fillText("覆盖", lab2[0], lab2[1] + 6);
}

function drawSearchSweep(ctx, t) {
  const jammed = state.jammed;
  const ang = searchSweepAz();
  const r = SEARCH.r;
  const h = SEARCH_H;
  const y0 = 0.03;
  const o0 = P([SEARCH.c[0], SEARCH.c[1], SEARCH.c[2]]);
  const tip0 = P(searchPt(ang, r, y0));
  const tipH = P(searchPt(ang, r, h));
  const oH = P([SEARCH.c[0], h, SEARCH.c[2]]);
  ctx.fillStyle = jammed ? "rgba(213,106,86,0.12)" : "rgba(236,214,140,0.14)";
  fillPoly(ctx, [o0, tip0, tipH, oH]);
}

function drawFcScan(ctx) {
  const c = RADAR.sam;
  const raid = living(state.lo);
  if (!raid.length) return;
  const tgt = raid.reduce((best, a) => (dist(a.p, c) < dist(best.p, c) ? a : best), raid[0]).p;
  const rng = dist(c, tgt);
  if (rng < 0.15) return;
  const ux = (tgt[0] - c[0]) / rng;
  const uy = (tgt[1] - c[1]) / rng;
  const uz = (tgt[2] - c[2]) / rng;
  const half = 0.028;
  const side = [-uz, 0, ux];
  const sl = Math.hypot(side[0], side[2]) || 1;
  side[0] /= sl;
  side[2] /= sl;
  const reach = Math.min(rng, SAM.r * 0.9);
  const far = [c[0] + ux * reach, c[1] + uy * reach, c[2] + uz * reach];
  const w = reach * half;
  const a = P([far[0] + side[0] * w, far[1], far[2] + side[2] * w]);
  const b = P([far[0] - side[0] * w, far[1], far[2] - side[2] * w]);
  const o = P(c);
  const tip = P(far);
  ctx.fillStyle = "rgba(126,200,194,0.16)";
  fillPoly(ctx, [o, a, b]);
  ctx.strokeStyle = "rgba(126,200,194,0.8)";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(o[0], o[1]);
  ctx.lineTo(tip[0], tip[1]);
  ctx.stroke();
  ctx.fillStyle = "rgba(126,200,194,0.85)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("制导波束", o[0] + (tip[0] - o[0]) * 0.74, o[1] + (tip[1] - o[1]) * 0.74 - 8);
}

function drawRadarFx(ctx) {
  const t = scanT();
  const jammed = state.jammed;
  const gold = jammed ? "rgba(213,106,86,0.85)" : "rgba(226,195,106,0.88)";
  drawSearchSweep(ctx, t);
  drawFanRipples(ctx, 0.04, t, jammed ? 4.6 : 2.8, gold);
  drawFanRipples(ctx, SEARCH_H, t + 0.15, jammed ? 4.6 : 2.8, gold);
  drawGroundRipples(ctx, SHIP.c, SHIP.r, t + 0.6, 3.1, "rgba(216,210,196,0.7)", 2);
  drawFcScan(ctx);
}

function stem(ctx, w) {
  const g = P([w[0], 0, w[2]]);
  const q = P(w);
  ctx.strokeStyle = "rgba(232,226,212,0.28)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(g[0], g[1]);
  ctx.lineTo(q[0], q[1]);
  ctx.stroke();
  return q;
}

function glyph(ctx, w, paint, tag, dx = 14, dy = -2, align = "left") {
  const q = stem(ctx, w);
  const sc = Math.max(0.9, Math.min(1.45, q[3] / 48));
  ctx.save();
  ctx.translate(q[0], q[1]);
  ctx.scale(sc, sc);
  paint(ctx, 0, 0);
  ctx.restore();
  if (!tag) return;
  ctx.fillStyle = "#ece6d4";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(tag, q[0] + dx, q[1] + dy);
}

function drawPad(ctx, c, r = 0.13) {
  const ring = [];
  const inner = [];
  for (let i = 0; i <= 36; i += 1) {
    const phi = (i / 36) * Math.PI * 2;
    ring.push(P([c[0] + r * Math.cos(phi), 0.01, c[2] + r * Math.sin(phi)]));
    inner.push(P([c[0] + r * 0.52 * Math.cos(phi), 0.012, c[2] + r * 0.52 * Math.sin(phi)]));
  }
  ctx.fillStyle = "rgba(28, 92, 150, 0.28)";
  fillPoly(ctx, ring);
  ctx.strokeStyle = "rgba(72, 172, 224, 0.85)";
  ctx.lineWidth = 1.3;
  strokePoly(ctx, ring);
  ctx.strokeStyle = "rgba(72, 172, 224, 0.32)";
  ctx.lineWidth = 0.85;
  strokePoly(ctx, inner);
}

function drawSearchAntenna(ctx) {
  const c = RADAR.shore;
  const { fx, fz, sx, sz } = azBasis(searchSweepAz());
  function w(lat, y, along) {
    return [c[0] + sx * lat + fx * along, y, c[2] + sz * lat + fz * along];
  }
  function q(lat, y, along) {
    return P(w(lat, y, along));
  }
  ctx.fillStyle = "rgba(72, 84, 90, 0.96)";
  ctx.strokeStyle = "rgba(36, 48, 52, 0.95)";
  ctx.lineWidth = 1.05;
  fillPoly(ctx, [q(-0.045, 0.018, -0.03), q(0.045, 0.018, -0.03), q(0.045, 0.018, 0.03), q(-0.045, 0.018, 0.03)]);
  fillPoly(ctx, [q(-0.028, 0.018, -0.018), q(0.028, 0.018, -0.018), q(0.02, 0.1, -0.012), q(-0.02, 0.1, -0.012)]);
  strokePoly(ctx, [q(-0.028, 0.018, -0.018), q(0.028, 0.018, -0.018), q(0.02, 0.1, -0.012), q(-0.02, 0.1, -0.012)]);

  const y0 = 0.1;
  const y1 = 0.36;
  const half = 0.05;
  function face(lat, y) {
    const u = lat / half;
    return q(lat, y, -0.03 - 0.038 * (1 - u * u));
  }
  const panel = [];
  for (let i = 0; i <= 10; i += 1) panel.push(face(-half + (2 * half * i) / 10, y0));
  for (let i = 10; i >= 0; i -= 1) panel.push(face(-half + (2 * half * i) / 10, y1));
  ctx.fillStyle = "rgba(198, 208, 212, 0.96)";
  fillPoly(ctx, panel);
  ctx.strokeStyle = "rgba(40, 54, 60, 0.95)";
  ctx.lineWidth = 1.25;
  strokePoly(ctx, panel);
  ctx.strokeStyle = "rgba(80, 100, 108, 0.5)";
  ctx.lineWidth = 0.8;
  for (let k = 1; k <= 4; k += 1) {
    const y = y0 + ((y1 - y0) * k) / 5;
    const rib = [];
    for (let i = 0; i <= 10; i += 1) rib.push(face(-half + (2 * half * i) / 10, y));
    strokePoly(ctx, rib, false);
  }
  const root = q(0, (y0 + y1) / 2, -0.06);
  const feed = q(0, (y0 + y1) / 2, 0.025);
  ctx.strokeStyle = "rgba(40, 54, 60, 0.9)";
  ctx.lineWidth = 1.15;
  ctx.beginPath();
  ctx.moveTo(root[0], root[1]);
  ctx.lineTo(feed[0], feed[1]);
  ctx.stroke();
  const lab = q(0.12, y1 + 0.04, 0.02);
  ctx.fillStyle = "#ece6d4";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("岸搜", lab[0] + 8, lab[1] - 10);
}

function drawFcAntenna(ctx) {
  const c = RADAR.sam;
  const raid = living(state.lo);
  let u = [0, 0.2, -1];
  if (raid.length) {
    const tgt = raid.reduce((best, a) => (dist(a.p, c) < dist(best.p, c) ? a : best), raid[0]).p;
    const rng = dist(c, tgt) || 1;
    u = [(tgt[0] - c[0]) / rng, (tgt[1] - c[1]) / rng, (tgt[2] - c[2]) / rng];
  } else {
    const n = Math.hypot(u[0], u[1], u[2]) || 1;
    u = [u[0] / n, u[1] / n, u[2] / n];
  }
  const tmp = Math.abs(u[1]) < 0.86 ? [0, 1, 0] : [1, 0, 0];
  let rgt = [u[1] * tmp[2] - u[2] * tmp[1], u[2] * tmp[0] - u[0] * tmp[2], u[0] * tmp[1] - u[1] * tmp[0]];
  const rl = Math.hypot(rgt[0], rgt[1], rgt[2]) || 1;
  rgt = [rgt[0] / rl, rgt[1] / rl, rgt[2] / rl];
  const up = [rgt[1] * u[2] - rgt[2] * u[1], rgt[2] * u[0] - rgt[0] * u[2], rgt[0] * u[1] - rgt[1] * u[0]];
  const apex = [c[0], c[1] + 0.07, c[2]];
  const dishC = [apex[0] - u[0] * 0.025, apex[1] - u[1] * 0.025, apex[2] - u[2] * 0.025];
  const R = 0.075;
  function ring(scale) {
    const pts = [];
    for (let i = 0; i <= 22; i += 1) {
      const th = (i / 22) * Math.PI * 2;
      const ct = Math.cos(th);
      const st = Math.sin(th);
      pts.push(
        P([
          dishC[0] + (rgt[0] * ct + up[0] * st) * R * scale,
          dishC[1] + (rgt[1] * ct + up[1] * st) * R * scale,
          dishC[2] + (rgt[2] * ct + up[2] * st) * R * scale,
        ]),
      );
    }
    return pts;
  }
  const foot = P([c[0], 0.02, c[2]]);
  const neck = P(apex);
  ctx.strokeStyle = "rgba(70, 82, 88, 0.95)";
  ctx.lineWidth = 2.1;
  ctx.beginPath();
  ctx.moveTo(foot[0], foot[1]);
  ctx.lineTo(neck[0], neck[1]);
  ctx.stroke();
  ctx.fillStyle = "rgba(205, 214, 218, 0.96)";
  fillPoly(ctx, ring(1));
  ctx.strokeStyle = "rgba(40, 54, 60, 0.95)";
  ctx.lineWidth = 1.2;
  strokePoly(ctx, ring(1));
  ctx.fillStyle = "rgba(88, 118, 126, 0.88)";
  fillPoly(ctx, ring(0.42));
  const lab = P([c[0] + 0.22, c[1] + 0.22, c[2] + 0.18]);
  ctx.fillStyle = "#ece6d4";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("制导", lab[0] + 6, lab[1] - 12);
}

function drawSamIso(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#2c3a40";
  ctx.lineWidth = 1.1;
  ctx.fillStyle = "#8a989e";
  ctx.beginPath();
  ctx.moveTo(-9, 6);
  ctx.lineTo(-7, 2);
  ctx.lineTo(8, 2);
  ctx.lineTo(10, 6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#b7c4c8";
  ctx.beginPath();
  ctx.moveTo(-6, 2);
  ctx.lineTo(-4, -3);
  ctx.lineTo(7, -6);
  ctx.lineTo(8, 2);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = "#4a5a60";
  ctx.lineWidth = 0.9;
  for (let i = 0; i < 4; i += 1) {
    const u = i / 3;
    ctx.beginPath();
    ctx.moveTo(-5 + u * 10.5, 1.2);
    ctx.lineTo(-3.2 + u * 9.5, -3.8);
    ctx.stroke();
  }
  ctx.restore();
}

function drawBmdIso(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = "#2c3a40";
  ctx.lineWidth = 1.1;
  ctx.fillStyle = "#6d7c82";
  ctx.beginPath();
  ctx.moveTo(-8, 6);
  ctx.lineTo(-6, 1);
  ctx.lineTo(6, 1);
  ctx.lineTo(8, 6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#c9d4d8";
  ctx.beginPath();
  ctx.ellipse(0, -2, 5.2, 3.4, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#4a5a60";
  ctx.beginPath();
  ctx.ellipse(0, -2, 2.4, 1.6, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawC2Iso(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#5a4a28";
  ctx.lineWidth = 1.1;
  ctx.fillStyle = "#c5b48a";
  ctx.beginPath();
  ctx.moveTo(-8, 6);
  ctx.lineTo(-8, -1);
  ctx.lineTo(0, -8);
  ctx.lineTo(8, -1);
  ctx.lineTo(8, 6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#8a7a52";
  ctx.fillRect(-3, 0, 6, 6);
  ctx.strokeRect(-3, 0, 6, 6);
  ctx.fillStyle = "#c9d4d8";
  ctx.beginPath();
  ctx.ellipse(6, -9, 4.2, 2.6, -0.3, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#2c3a40";
  ctx.stroke();
  ctx.restore();
}

function drawJammerIso(ctx, x, y, on) {
  ctx.save();
  ctx.translate(x, y);
  const ink = on ? "#ff7a6a" : "#8a989e";
  ctx.strokeStyle = ink;
  ctx.fillStyle = on ? "#6a4038" : "#6d7c82";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(-4, 6);
  ctx.lineTo(-2.5, 1);
  ctx.lineTo(2.5, 1);
  ctx.lineTo(4, 6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, 1);
  ctx.lineTo(0, -11);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(-6, -4);
  ctx.lineTo(6, -8);
  ctx.moveTo(-6, -8);
  ctx.lineTo(6, -4);
  ctx.stroke();
  if (on) {
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.arc(0, -11, 5, -0.8, 0.8);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, -11, 8, -0.7, 0.7);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

function drawJam(ctx) {
  const foot = [];
  for (let i = 0; i <= 48; i += 1) {
    const phi = (i / 48) * Math.PI * 2;
    foot.push(P([JAM.c[0] + JAM.r * Math.cos(phi), 0.03, JAM.c[2] + JAM.r * Math.sin(phi)]));
  }
  const on = state.jammed;
  ctx.fillStyle = on ? "rgba(213,106,86,0.12)" : "rgba(141,136,120,0.05)";
  fillPoly(ctx, foot);
  drawPad(ctx, JAM.c, 0.12);
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = on ? "rgba(255,107,90,0.7)" : "rgba(141,136,120,0.35)";
  ctx.lineWidth = 1.2;
  strokePoly(ctx, foot);
  ctx.setLineDash([]);
  glyph(ctx, JAM.c, (c, x, y) => drawJammerIso(c, x, y, on), on ? "干扰·开机" : "干扰");
}

function drawWarship(ctx) {
  const hdg = (SHIP_HDG * Math.PI) / 180;
  const fx = Math.sin(hdg);
  const fz = Math.cos(hdg);
  const sx = fz;
  const sz = -fx;
  const o = SHIP.c;
  function w(lat, y, lon) {
    return [o[0] + sx * lat + fx * lon, y, o[2] + sz * lat + fz * lon];
  }
  function q(lat, y, lon) {
    return P(w(lat, y, lon));
  }

  const yW = 0.018;
  const yD = 0.055;
  const bow = 0.3;
  const stern = -0.28;
  const stn = [
    [bow, 0],
    [0.2, 0.03],
    [0.06, 0.048],
    [-0.08, 0.05],
    [-0.2, 0.036],
    [stern, 0.022],
  ];

  for (let i = 1; i <= 4; i += 1) {
    const d = 0.07 * i;
    const ww = 0.016 + i * 0.011;
    ctx.globalAlpha = 0.2 / i;
    ctx.strokeStyle = "rgba(186,220,216,0.9)";
    ctx.lineWidth = 1.05;
    strokePoly(ctx, [q(-ww, 0.01, stern - d * 0.55), q(0, 0.01, stern - d * 1.2), q(ww, 0.01, stern - d * 0.55)], false);
  }
  ctx.globalAlpha = 1;

  function belt(y, slim) {
    return stn
      .map(([lon, b]) => q(b * slim, y, lon))
      .concat(stn.map(([lon, b]) => q(-b * slim, y, lon)).reverse());
  }
  ctx.fillStyle = "rgba(16, 30, 36, 0.96)";
  fillPoly(ctx, belt(yW, 1));
  ctx.strokeStyle = "rgba(143,212,220,0.95)";
  ctx.lineWidth = 1.35;
  strokePoly(ctx, belt(yW, 1));

  for (let s = 1; s >= -1; s -= 2) {
    for (let i = 0; i < stn.length - 1; i += 1) {
      const [l0, b0] = stn[i];
      const [l1, b1] = stn[i + 1];
      ctx.fillStyle = s > 0 ? "rgba(28, 52, 58, 0.92)" : "rgba(22, 42, 48, 0.92)";
      fillPoly(ctx, [q(s * b0, yW, l0), q(s * b1, yW, l1), q(s * b1 * 0.78, yD, l1), q(s * b0 * 0.78, yD, l0)]);
    }
  }

  ctx.fillStyle = "rgba(40, 64, 70, 0.94)";
  fillPoly(ctx, belt(yD, 0.78));
  ctx.strokeStyle = "rgba(143,212,220,0.5)";
  ctx.lineWidth = 0.9;
  strokePoly(ctx, belt(yD, 0.78));

  ctx.fillStyle = "rgba(48, 78, 84, 0.95)";
  fillPoly(ctx, [q(-0.03, yD, -0.2), q(0.03, yD, -0.2), q(0.028, 0.1, -0.05), q(-0.028, 0.1, -0.05)]);
  ctx.fillStyle = "rgba(56, 88, 94, 0.96)";
  fillPoly(ctx, [q(-0.028, yD, -0.03), q(0.028, yD, -0.03), q(0.02, 0.128, 0.1), q(-0.02, 0.128, 0.1)]);
  ctx.strokeStyle = "rgba(143,212,220,0.85)";
  ctx.lineWidth = 1.05;
  strokePoly(ctx, [q(-0.028, yD, -0.03), q(0.028, yD, -0.03), q(0.02, 0.128, 0.1), q(-0.02, 0.128, 0.1)]);

  ctx.fillStyle = "rgba(90, 170, 176, 0.35)";
  fillPoly(ctx, [q(0.028, 0.072, 0.0), q(0.021, 0.12, 0.05), q(0.018, 0.12, 0.09), q(0.024, 0.072, 0.04)]);
  fillPoly(ctx, [q(-0.028, 0.072, 0.0), q(-0.021, 0.12, 0.05), q(-0.018, 0.12, 0.09), q(-0.024, 0.072, 0.04)]);
  ctx.strokeStyle = "rgba(143,212,220,0.8)";
  ctx.lineWidth = 0.9;
  strokePoly(ctx, [q(0.028, 0.072, 0.0), q(0.021, 0.12, 0.05), q(0.018, 0.12, 0.09), q(0.024, 0.072, 0.04)]);
  strokePoly(ctx, [q(-0.028, 0.072, 0.0), q(-0.021, 0.12, 0.05), q(-0.018, 0.12, 0.09), q(-0.024, 0.072, 0.04)]);

  const mast0 = q(0, 0.128, 0.055);
  const mast1 = q(0, 0.235, 0.042);
  ctx.strokeStyle = "#9adce4";
  ctx.lineWidth = 1.45;
  ctx.beginPath();
  ctx.moveTo(mast0[0], mast0[1]);
  ctx.lineTo(mast1[0], mast1[1]);
  ctx.stroke();
  const yard = [q(-0.045, 0.2, 0.048), q(0.045, 0.2, 0.048)];
  ctx.lineWidth = 1.05;
  ctx.beginPath();
  ctx.moveTo(yard[0][0], yard[0][1]);
  ctx.lineTo(yard[1][0], yard[1][1]);
  ctx.stroke();
  const top = q(0, 0.248, 0.04);
  ctx.fillStyle = "#d7f4f6";
  ctx.beginPath();
  ctx.arc(top[0], top[1], 1.7, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = "rgba(143,212,220,0.55)";
  ctx.lineWidth = 0.8;
  for (let i = 0; i < 3; i += 1) {
    for (let j = 0; j < 2; j += 1) {
      const lon = 0.145 + i * 0.032;
      const lat = -0.012 + j * 0.024;
      strokePoly(ctx, [
        q(lat - 0.008, yD + 0.004, lon - 0.01),
        q(lat + 0.008, yD + 0.004, lon - 0.01),
        q(lat + 0.008, yD + 0.004, lon + 0.01),
        q(lat - 0.008, yD + 0.004, lon + 0.01),
      ]);
    }
  }

  const light = q(0, yD + 0.008, bow - 0.012);
  ctx.fillStyle = "#ff6b5a";
  ctx.beginPath();
  ctx.arc(light[0], light[1], 1.7, 0, Math.PI * 2);
  ctx.fill();

  const lab = q(0.07, 0.14, -0.02);
  ctx.fillStyle = "#ece6d4";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("军舰", lab[0], lab[1]);
}

function drawSites(ctx) {
  drawPad(ctx, RADAR.shore);
  drawPad(ctx, RADAR.sam);
  drawPad(ctx, [SAM.c[0], 0, SAM.c[2]]);
  drawPad(ctx, [BMD.c[0], 0, BMD.c[2]]);
  drawPad(ctx, C2, 0.15);
  drawSearchAntenna(ctx);
  drawFcAntenna(ctx);
  glyph(ctx, [SAM.c[0], 0.32, SAM.c[2]], drawSamIso, "发射", -20, 8, "right");
  glyph(ctx, [BMD.c[0], 0.42, BMD.c[2]], drawBmdIso, "井", 16, -16);
  glyph(ctx, C2, drawC2Iso, "C2", 16, -10);
  drawWarship(ctx);
}

const trails = new Map();
const TRAIL_N = 56;

function rememberTrails() {
  const live = new Set();
  [state.hi, state.mid, state.lo, state.unk || []].forEach((list) => {
    living(list).forEach((a) => {
      live.add(a.id);
      let t = trails.get(a.id);
      if (!t) {
        t = [];
        trails.set(a.id, t);
      }
      if (a.gated) return;
      const last = t[t.length - 1];
      if (!last || dist(last, a.p) > 0.018) {
        t.push(a.p.slice());
        if (t.length > TRAIL_N) t.shift();
      }
    });
  });
  for (const id of [...trails.keys()]) if (!live.has(id)) trails.delete(id);
}

function trailStroke(a, p) {
  if (a.side === "blue") return a.layer === "hi" ? "rgba(232,226,212,1)" : "rgba(127,210,204,1)";
  if (a.side === "unk") return "rgba(226,195,106,1)";
  const z = zoneOf(p);
  if (z === "in") return "rgba(126,200,194,1)";
  if (z === "edge") return "rgba(226,195,106,1)";
  if (z === "ship") return "rgba(216,210,196,1)";
  return "rgba(255,120,100,1)";
}

function drawTrails(ctx, list) {
  living(list).forEach((a) => {
    const t = trails.get(a.id);
    if (!t || t.length < 2) return;
    const young = a.side !== "blue" && a.wave === state.k && t.length < 18;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (let i = 1; i < t.length; i += 1) {
      const a0 = P(t[i - 1]);
      const a1 = P(t[i]);
      const fade = 0.12 + (0.5 * i) / t.length;
      ctx.globalAlpha = Math.min(0.85, fade * (young ? 1.35 : 1));
      ctx.strokeStyle = trailStroke(a, t[i]);
      ctx.lineWidth = a.side === "blue" ? 1.6 : 1.15;
      ctx.beginPath();
      ctx.moveTo(a0[0], a0[1]);
      ctx.lineTo(a1[0], a1[1]);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  });
}

function drawBlueUavMark(ctx) {
  ctx.fillStyle = "rgba(8, 14, 20, 0.3)";
  ctx.beginPath();
  ctx.ellipse(0, 5, 12, 3.2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.lineWidth = 1.15;
  ctx.strokeStyle = "#2c3a40";
  ctx.fillStyle = "#b7c4c8";
  ctx.beginPath();
  ctx.moveTo(-17, 0.6);
  ctx.lineTo(-1.2, -1.3);
  ctx.lineTo(1.2, -1.3);
  ctx.lineTo(17, 0.6);
  ctx.lineTo(16.4, 2.7);
  ctx.lineTo(1.2, 1.5);
  ctx.lineTo(-1.2, 1.5);
  ctx.lineTo(-16.4, 2.7);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#d5dee2";
  ctx.beginPath();
  ctx.moveTo(0, -13);
  ctx.bezierCurveTo(3.1, -10, 2.7, 2, 2.1, 8);
  ctx.lineTo(0, 12);
  ctx.lineTo(-2.1, 8);
  ctx.bezierCurveTo(-2.7, 2, -3.1, -10, 0, -13);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#5a7a88";
  ctx.beginPath();
  ctx.ellipse(0, -6.2, 1.25, 2.3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#c5d0d4";
  ctx.beginPath();
  ctx.moveTo(-0.5, 7);
  ctx.lineTo(-7.2, 13);
  ctx.lineTo(-0.3, 10.4);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0.5, 7);
  ctx.lineTo(7.2, 13);
  ctx.lineTo(0.3, 10.4);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#8a9aa0";
  ctx.beginPath();
  ctx.ellipse(0, -1.2, 1.45, 2.1, 0, 0, Math.PI * 2);
  ctx.fill();
}

function drawCraft(ctx, a, q) {
  const deg = headingOf(a) * (Math.PI / 180);
  const nose = P([a.p[0] + Math.sin(deg) * 0.32, a.p[1], a.p[2] + Math.cos(deg) * 0.32]);
  const ang = Math.atan2(nose[0] - q[0], q[1] - nose[1]);
  ctx.save();
  ctx.translate(q[0], q[1]);
  ctx.rotate(ang);
  if (a.side === "blue") {
    drawBlueUavMark(ctx);
  } else if (a.side === "unk") {
    ctx.strokeStyle = "#e2c36a";
    ctx.fillStyle = "rgba(226,195,106,0.18)";
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    ctx.moveTo(0, -8);
    ctx.lineTo(3.6, 0);
    ctx.lineTo(0, 8);
    ctx.lineTo(-3.6, 0);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else {
    ctx.fillStyle = "#6a7074";
    ctx.strokeStyle = "#ff7a6a";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(0, -8.5);
    ctx.lineTo(2.4, 5.5);
    ctx.lineTo(0, 3);
    ctx.lineTo(-2.4, 5.5);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-6, 0.4);
    ctx.lineTo(6, 0.4);
    ctx.stroke();
  }
  ctx.restore();
}

/** Raid-track zone line: 区内 p0.91 / 区沿 / 不明·不打. Blue task tags stay one line. */
function raidZoneLine(a) {
  if (a.side === "unk") return verdict(a, state.jammed).act;
  const z = zoneOf(a.p);
  if (z === "in") return `区内 p${a.belief.toFixed(2)}`;
  if (z === "edge") return "区沿";
  if (z === "ship") return "舰区";
  return "区外";
}

function drawAgents(ctx, list) {
  const marks = living(list)
    .map((a) => ({ a, q: P(a.p) }))
    .sort((x, y) => y.q[2] - x.q[2]);
  for (const { a, q } of marks) {
    const g = P([a.p[0], 0, a.p[2]]);
    ctx.strokeStyle = "rgba(232,226,212,0.18)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(g[0], g[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.stroke();
    if (a.side !== "blue") {
      const src = intelOf(a, state.jammed, state.pairs || []).src || "";
      const mark = src.includes("局部") ? "局" : src.includes("制导") ? "制" : src.includes("舰") ? "舰" : "岸";
      ctx.fillStyle = "rgba(141,136,120,0.72)";
      ctx.font = "9px Segoe UI, PingFang SC, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText(mark, g[0] + 4, g[1] + 3);
    }
    drawCraft(ctx, a, q);
    if (a.gated) {
      ctx.strokeStyle = "rgba(236,230,212,0.85)";
      ctx.lineWidth = 1.15;
      ctx.beginPath();
      ctx.arc(q[0], q[1], 8, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
    ctx.textBaseline = "bottom";
    if (a.side === "blue") {
      ctx.fillStyle = "rgba(186,220,216,0.92)";
      ctx.textAlign = "left";
      const tag = a.quarryTn ? `${a.tn} ${a.task}→${a.quarryTn}` : `${a.tn} ${a.task || "巡逻"}`;
      ctx.fillText(tag, q[0] + 9, q[1] - 7);
    } else {
      ctx.fillStyle = a.side === "unk" ? "rgba(226,195,106,0.88)" : "rgba(255,140,120,0.88)";
      ctx.textAlign = "left";
      ctx.fillText(a.tn, q[0] + 8, q[1] + 14);
      const hot = a.side !== "unk" && zoneOf(a.p) === "in" && a.belief >= TAU && !state.jammed;
      ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
      ctx.fillStyle = hot ? "rgba(255,140,120,0.88)" : "rgba(232,226,212,0.58)";
      ctx.fillText(raidZoneLine(a), q[0] + 8, q[1] + 25);
    }
  }
}

function findLive(id) {
  return [...state.hi, ...state.mid, ...state.lo, ...(state.unk || [])].find((a) => a.alive && a.id === id);
}

function drawMesh(ctx, list) {
  const live = living(list);
  if (live.length < 2) return;
  ctx.setLineDash([2, 4]);
  ctx.strokeStyle = "rgba(232,226,212,0.28)";
  ctx.lineWidth = 0.95;
  for (let i = 0; i < live.length; i += 1) {
    const a = P(live[i].p);
    const b = P(live[(i + 1) % live.length].p);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

let lastPairSig = "";
let pairFlashUntil = 0;

function pairSig() {
  return (state.pairs || [])
    .map((p) => `${p.blue}:${p.red}`)
    .sort()
    .join("|");
}

function notePairs() {
  const s = pairSig();
  if (s !== lastPairSig) {
    if (lastPairSig) pairFlashUntil = performance.now() + 1800;
    lastPairSig = s;
  }
}

function drawPairs(ctx) {
  const flash = performance.now() < pairFlashUntil;
  (state.pairs || []).forEach((pair) => {
    const b = findLive(pair.blue);
    const r = findLive(pair.red);
    if (!b || !r) return;
    const pb = P(b.p);
    const pr = P(r.p);
    ctx.setLineDash(state.jammed ? [3, 5] : [5, 4]);
    ctx.strokeStyle = state.jammed ? "rgba(226,195,106,0.4)" : flash ? "rgba(122,210,232,0.9)" : "rgba(122,210,232,0.7)";
    ctx.lineWidth = flash ? 1.45 : 1.15;
    ctx.beginPath();
    ctx.moveTo(pb[0], pb[1]);
    ctx.lineTo(pr[0], pr[1]);
    ctx.stroke();
    ctx.setLineDash([]);
    if (!flash) return;
    ctx.fillStyle = "rgba(186,220,216,0.88)";
    ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(b.task || "盯梢", (pb[0] + pr[0]) / 2, (pb[1] + pr[1]) / 2 - 5);
  });
}

function drawSlots(ctx, W, H, center, radius, k, squash, color) {
  if (k < 2) return;
  const slots = sphereSlots(center, radius, k, squash);
  const fib = fibonacci(k);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.15;
  for (let i = 0; i < k; i += 1) {
    for (let j = i + 1; j < k; j += 1) {
      if (dist(fib[i], fib[j]) > 1.82) continue;
      const a = P(slots[i]);
      const b = P(slots[j]);
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
    }
  }
}

function loCenter() {
  const live = living(state.lo);
  if (!live.length) return [0, 0.2, -2];
  const c = [0, 0, 0];
  live.forEach((a) => {
    c[0] += a.p[0];
    c[1] += a.p[1];
    c[2] += a.p[2];
  });
  return [c[0] / live.length, c[1] / live.length, c[2] / live.length];
}

function drawVoid(ctx, W, H) {
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, "#07090e");
  g.addColorStop(0.4, "#0b1018");
  g.addColorStop(0.56, "#161e28");
  g.addColorStop(0.7, "#0a0e14");
  g.addColorStop(1, "#05070a");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
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
  drawVoid(ctx, W, H);
  setView(W, H);

  drawLand(ctx);
  drawGrid(ctx, W, H);
  drawContactShadows(ctx);
  drawSearchVolume(ctx);
  drawRadarFx(ctx);
  drawBox(ctx, W, H);
  drawDetect(ctx, SHIP, "舰探");
  drawJam(ctx);
  drawEngage(ctx, BMD, "rgba(90,150,145,0.08)", "rgba(111,180,174,0.55)", "B_BMD");
  drawEngage(ctx, SAM, "rgba(90,160,155,0.1)", "rgba(126,200,194,0.7)", "B_SAM");
  drawRim(ctx, { c: SAM.c, r: SAM.r * SAM_RIM, z0: SAM.z0 }, "rgba(126,200,194,0.7)", "授权内界");

  const bmdSite = [BMD.c[0], 0.45, BMD.c[2]];
  const samSite = [SAM.c[0], 0.35, SAM.c[2]];
  const onNet = !state.jammed;
  drawLink(ctx, C2, RADAR.shore, onNet);
  drawLink(ctx, C2, RADAR.sam, onNet);
  drawLink(ctx, C2, samSite, onNet);
  drawLink(ctx, C2, bmdSite, onNet);
  const cq = P(C2);
  ctx.fillStyle = onNet ? "rgba(226,195,106,0.88)" : "rgba(213,106,86,0.78)";
  ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(onNet ? "岸上数据链" : "岸上数据链 · 断", cq[0] + 16, cq[1] + 12);

  const liveLo = living(state.lo);
  if (liveLo.length > 1) drawSlots(ctx, W, H, loCenter(), 0.68, liveLo.length, 0.28, "rgba(226,195,106,0.55)");
  drawSlots(ctx, W, H, state.midC, 0.62, living(state.mid).length, 1, "rgba(111,180,174,0.7)");
  drawSlots(ctx, W, H, state.hiC, 0.72, living(state.hi).length, 1, "rgba(232,226,212,0.4)");

  drawSites(ctx);
  drawTrails(ctx, state.hi);
  drawTrails(ctx, state.mid);
  drawTrails(ctx, state.lo);
  drawTrails(ctx, state.unk || []);
  drawMesh(ctx, state.hi);
  drawMesh(ctx, state.mid);
  drawPairs(ctx);
  drawAgents(ctx, state.hi);
  drawAgents(ctx, state.mid);
  drawAgents(ctx, state.lo);
  drawAgents(ctx, state.unk || []);
  drawFrame(ctx, W, H);
}

function bindZoom(canvas) {
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      cam.dist = Math.max(6.5, Math.min(20, cam.dist + e.deltaY * 0.012));
    },
    { passive: false },
  );
}

const world = document.getElementById("world");
const caption = document.getElementById("caption");
const playBtn = document.getElementById("play");
const freezeEl = document.getElementById("freeze");
let playing = false;
let frozen = false;
let acc = 0;
let last = performance.now();

function setTxt(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function hud() {
  const nLo = living(state.lo).length;
  const nUnk = living(state.unk || []).length;
  const nIn = living(state.lo).filter((a) => zoneOf(a.p) === "in").length;
  const nEdge = living(state.lo).filter((a) => zoneOf(a.p) === "edge").length;
  const nNew = living(state.lo).filter((a) => a.wave === state.k).length;
  setTxt("v-k", `${state.k}/${K_MAX}`);
  setTxt("v-n", `${nLo}/${N_MAX.lo}`);
  setTxt("v-e", (state.epsLo ?? 0).toFixed(2));
  setTxt("v-m", `${state.mSam}/${M_SAM0}`);
  setTxt("v-g", String(state.gated));
  if (caption) caption.textContent = battle.soWhat().text;
  const fusion = state.jammed ? "局部" : "队级";
  const intelEl = document.getElementById("k-intel");
  if (intelEl) intelEl.textContent = `敌 ${nLo} 不明 ${nUnk} · ${fusion}`;
  const coopBits = [...living(state.hi), ...living(state.mid)]
    .filter((a) => a.quarryTn)
    .map((a) => `${a.tn}${a.task}→${a.quarryTn}`);
  const coopEl = document.getElementById("k-coop");
  if (coopEl) coopEl.textContent = `${coopBits.join(" · ") || "未配对"} · ε${(state.epsLo ?? 0).toFixed(2)}`;
  const trackEl = document.getElementById("k-track");
  if (trackEl) {
    trackEl.textContent = state.k
      ? `第 ${state.k} 波 ${nNew} 批 · 区内 ${nIn} 沿 ${nEdge}`
      : "走廊空";
  }
}

function render() {
  try {
    battle.tickKb();
    notePairs();
    rememberTrails();
    drawWorld(world);
    hud();
    afterRenderFns.forEach((fn) => fn());
  } catch (err) {
    console.error(err);
    if (caption) caption.textContent = String(err && err.message ? err.message : err);
  }
}

function reset() {
  battle.reset();
  battle.nextWave();
  trails.clear();
  lastPairSig = "";
  pairFlashUntil = 0;
  cam.yaw = CAM0.yaw;
  cam.pitch = CAM0.pitch;
  cam.dist = CAM0.dist;
  look[0] = LOOK0[0];
  look[1] = LOOK0[1];
  look[2] = LOOK0[2];
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
  if (document.hidden || frozen) {
    acc = 0;
  } else if (playing) {
    acc += dt;
    while (acc >= 40) {
      acc -= 40;
      battle.step(TICK_S, { formSpin: false });
      if (state.t % 240 === 40 && state.k < K_MAX && living(state.lo).length > 0 && living(state.lo).length <= 3) {
        battle.nextWave();
      }
    }
  }
  render();
  requestAnimationFrame(loop);
}

bindZoom(world);
battle.nextWave();
render();
requestAnimationFrame(loop);

export { battle, state, cam, look, P };
