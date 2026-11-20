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
  TICK_S,
  createBattle,
  living,
  verdict,
  intelOf,
  INTEL_FILES,
  briefIntel,
  trackSheet,
  toRim,
  zoneOf,
} from "./battle.js?v=25";
import { shoreZ } from "./scene-gis.js?v=1";
import {
  drawTrack,
  drawTrackCut,
  drawShip,
  drawShipSide,
  drawSam,
  drawBmd,
  drawC2,
  drawRadar,
  drawSearchRadar,
  drawRadarSide,
  drawJammer,
  drawKey,
  inkOf,
} from "./icons.js?v=22";

window.__copReady = true;
document.getElementById("boot-fail")?.remove();

const battle = createBattle();
const { state } = battle;

const vol = document.getElementById("vol");
const plan = document.getElementById("plan");
const cut = document.getElementById("cut");
const cam = { yaw: 0.62, pitch: 0.78, dist: 6.8 };
const look = [0.2, 0.35, 0.15];
let layer = "all";
let hoverId = null;
let selectedId = null;
let playing = false;
let frozen = false;
let showVol = false;

function activeId() {
  return hoverId || selectedId;
}

function rot(v, yaw, pitch) {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const x1 = v[0] * cy + v[2] * sy;
  const z1 = -v[0] * sy + v[2] * cy;
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  return [x1, v[1] * cp - z1 * sp, v[1] * sp + z1 * cp];
}

function proj(w, W, H) {
  const [x, y, z] = rot([w[0] - look[0], w[1] - look[1], w[2] - look[2]], cam.yaw, cam.pitch);
  const depth = z + cam.dist;
  const s = (1.15 * Math.min(W, H)) / Math.max(depth, 0.4);
  return [W * 0.5 + x * s, H * 0.58 - y * s, depth, s];
}

function size(c) {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const W = Math.max(1, c.clientWidth || c.parentElement?.clientWidth || 1);
  const H = Math.max(1, c.clientHeight || c.parentElement?.clientHeight || 1);
  if (c.width !== Math.floor(W * dpr) || c.height !== Math.floor(H * dpr)) {
    c.width = Math.floor(W * dpr);
    c.height = Math.floor(H * dpr);
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, W, H];
}

function show(a) {
  if (layer === "all") return true;
  if (layer === "lo") return a.layer === "lo";
  if (layer === "mid") return a.layer === "mid" || a.layer === "lo";
  if (layer === "hi") return a.layer === "hi";
  return true;
}

function strokePoly(ctx, pts, close = true) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  if (close) ctx.closePath();
  ctx.stroke();
}

function fillPoly(ctx, pts) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath();
  ctx.fill();
}

function ring(ctx, cx, cy, r, n = 64) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

const BOX = { x0: -4.1, x1: 4.3, z0: -4.5, z1: 3.6 };

function shorePts(W, H, dz = 0) {
  const pts = [];
  for (let x = BOX.x0; x <= BOX.x1 + 1e-6; x += 0.12) {
    pts.push(toPlan([x, 0, shoreZ(x) + dz], W, H));
  }
  return pts;
}

function toPlan(p, W, H, pad = 18) {
  const x = pad + ((p[0] - BOX.x0) / (BOX.x1 - BOX.x0)) * (W - pad * 2);
  const y = pad + (1 - (p[2] - BOX.z0) / (BOX.z1 - BOX.z0)) * (H - pad * 2);
  return [x, y];
}

function trackTag(ctx, x, y, a, slot = 0) {
  const v = verdict(a, state.jammed);
  const intel = intelOf(a, state.jammed, state.pairs || []);
  const selected = a.id === activeId();
  const flip = a.side !== "blue" && slot % 2 === 1;
  const dx = flip ? -12 : 12;
  ctx.font = "600 10px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = inkOf(a);
  ctx.textAlign = flip ? "right" : "left";
  ctx.textBaseline = "middle";
  ctx.fillText(a.tn, x + dx, y - 1);
  let second = "";
  if (a.side === "blue") second = v.act;
  else if (a.side === "unk") second = "不明";
  else if (a.gated) second = "过门";
  else {
    const z = zoneOf(a.p);
    if (z === "in") second = "区内";
    else if (z === "edge") second = "区沿";
  }
  if (selected) {
    second = a.side === "blue" ? v.act : `${second || intel.iden}  p ${a.belief.toFixed(2)}`.trim();
  }
  if (!second) return;
  ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = v.act.includes("不打") || intel.kind === "open" ? "#8d8878" : "#c8c2b4";
  ctx.fillText(second, x + dx, y + 11);
}

function siteTag(ctx, x, y, name) {
  ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = "#8d8878";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillText(name, x, y + 11);
}

function wrapPi(a) {
  while (a > Math.PI) a -= Math.PI * 2;
  while (a < -Math.PI) a += Math.PI * 2;
  return a;
}

function scanNow() {
  return performance.now() / 1000;
}

function fanPath(ctx, W, H) {
  const n = 28;
  ctx.beginPath();
  const o = toPlan(SEARCH.c, W, H);
  ctx.moveTo(o[0], o[1]);
  for (let i = 0; i <= n; i++) {
    const a = SEARCH.az - SEARCH.half + (2 * SEARCH.half * i) / n;
    const q = toPlan([SEARCH.c[0] + SEARCH.r * Math.sin(a), 0, SEARCH.c[2] + SEARCH.r * Math.cos(a)], W, H);
    ctx.lineTo(q[0], q[1]);
  }
  ctx.closePath();
}

function drawJamSignal(ctx, cx, cy, rMax, t, on) {
  ctx.save();
  if (!on) {
    ctx.strokeStyle = "rgba(141,136,120,0.35)";
    ctx.setLineDash([3, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, rMax, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
    return;
  }
  ctx.fillStyle = "rgba(213,106,86,0.12)";
  ctx.beginPath();
  ctx.arc(cx, cy, rMax, 0, Math.PI * 2);
  ctx.fill();
  for (let i = 0; i < 4; i++) {
    const u = ((t / 1.25) + i / 4) % 1;
    const wobble = 1 + 0.08 * Math.sin(t * 19 + i * 2.1);
    ctx.strokeStyle = "rgba(255,107,90,0.95)";
    ctx.globalAlpha = (1 - u) * 0.72;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(3, u * rMax * wobble), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  for (let k = 0; k < 8; k++) {
    const a = (k / 8) * Math.PI * 2 + t * 1.1;
    const pulse = 0.4 + 0.6 * Math.abs(Math.sin(t * 7.5 + k));
    ctx.globalAlpha = 0.28 * pulse;
    ctx.strokeStyle = "rgba(255,140,110,0.95)";
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * 7, cy + Math.sin(a) * 7);
    ctx.lineTo(cx + Math.cos(a) * rMax * 0.55 * pulse, cy + Math.sin(a) * rMax * 0.55 * pulse);
    ctx.stroke();
  }
  ctx.restore();
}

function drawRipples(ctx, cx, cy, rMax, t, period, color, n = 3) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  for (let i = 0; i < n; i++) {
    const u = ((t / period) + i / n) % 1;
    ctx.globalAlpha = (1 - u) * 0.55;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(2, u * rMax), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

function drawFanSweep(ctx, W, H, t, jammed) {
  const o = toPlan(SEARCH.c, W, H);
  const period = jammed ? 7.2 : 4.2;
  const u = (t / period) % 1;
  const ang = SEARCH.az - SEARCH.half + u * 2 * SEARCH.half;
  const beam = jammed ? 0.07 : 0.11;
  const n = 10;
  const pts = [o];
  for (let i = 0; i <= n; i++) {
    const a = ang - beam + (2 * beam * i) / n;
    pts.push(toPlan([SEARCH.c[0] + SEARCH.r * Math.sin(a), 0, SEARCH.c[2] + SEARCH.r * Math.cos(a)], W, H));
  }
  ctx.save();
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath();
  const g = ctx.createRadialGradient(o[0], o[1], 4, o[0], o[1], SEARCH.r * planScale(W, H));
  g.addColorStop(0, jammed ? "rgba(213,106,86,0.28)" : "rgba(226,195,106,0.32)");
  g.addColorStop(1, "rgba(226,195,106,0)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = jammed ? "rgba(213,106,86,0.85)" : "rgba(236,214,140,0.9)";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(o[0], o[1]);
  const tip = toPlan([SEARCH.c[0] + SEARCH.r * Math.sin(ang), 0, SEARCH.c[2] + SEARCH.r * Math.cos(ang)], W, H);
  ctx.lineTo(tip[0], tip[1]);
  ctx.stroke();
  ctx.restore();
  return ang;
}

function paintPing(ctx, x, y, color) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.85;
  ctx.beginPath();
  ctx.arc(x, y, 5.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawCutRipples(ctx, x, yGround, rMax, t, period, color, seaward) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.1;
  for (let i = 0; i < 3; i++) {
    const u = ((t / period) + i / 3) % 1;
    ctx.globalAlpha = (1 - u) * 0.5;
    ctx.beginPath();
    const r = Math.max(4, u * rMax);
    if (seaward) ctx.arc(x, yGround, r, Math.PI * 0.55, Math.PI * 1.45, false);
    else ctx.arc(x, yGround, r, Math.PI * 1.55, Math.PI * 2.45, false);
    ctx.stroke();
  }
  ctx.restore();
}

function planScale(W, H, pad = 18) {
  return ((W - pad * 2) / (BOX.x1 - BOX.x0) + (H - pad * 2) / (BOX.z1 - BOX.z0)) * 0.5;
}

function drawCompass(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = "#c8c2b4";
  ctx.fillStyle = "#c8c2b4";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(0, 11);
  ctx.lineTo(0, -12);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -12);
  ctx.lineTo(-3.5, -5);
  ctx.lineTo(3.5, -5);
  ctx.closePath();
  ctx.fill();
  ctx.font = "9px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("000 陆", 0, -18);
  ctx.fillStyle = "#8d8878";
  ctx.fillText("180 海", 0, 18);
  ctx.restore();
}

function drawPlan() {
  const [ctx, W, H] = size(plan);
  ctx.clearRect(0, 0, W, H);
  const sc = planScale(W, H);
  const shore = shorePts(W, H, 0);
  const near = shorePts(W, H, -0.1);
  const belt = shorePts(W, H, 0.1);

  const sea = [toPlan([BOX.x0, 0, BOX.z0], W, H), toPlan([BOX.x1, 0, BOX.z0], W, H), ...[...shore].reverse()];
  ctx.fillStyle = "#101820";
  fillPoly(ctx, sea);
  const land = [...shore, toPlan([BOX.x1, 0, BOX.z1], W, H), toPlan([BOX.x0, 0, BOX.z1], W, H)];
  ctx.fillStyle = "#1e2620";
  fillPoly(ctx, land);
  ctx.fillStyle = "#161c1c";
  fillPoly(ctx, [...shore, ...[...near].reverse()]);
  ctx.fillStyle = "#242820";
  fillPoly(ctx, [...shore, ...[...belt].reverse()]);

  ctx.strokeStyle = "rgba(232,226,208,0.22)";
  ctx.lineWidth = 0.6;
  ctx.beginPath();
  shore.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.stroke();

  ctx.lineWidth = 1;
  ctx.font = "9px Segoe UI, sans-serif";
  ctx.fillStyle = "#8d8878";
  const landStroke = (g) => (g === 0 ? "rgba(232,226,208,0.09)" : "rgba(236,230,212,0.022)");
  for (let g = -4; g <= 4; g++) {
    ctx.strokeStyle = landStroke(g);
    const zLand = Math.max(shoreZ(g), BOX.z0);
    if (zLand >= BOX.z1) continue;
    const v0 = toPlan([g, 0, zLand], W, H);
    const v1 = toPlan([g, 0, BOX.z1], W, H);
    ctx.beginPath();
    ctx.moveTo(v0[0], v0[1]);
    ctx.lineTo(v1[0], v1[1]);
    ctx.stroke();
  }
  for (let g = 0; g <= 4; g++) {
    ctx.strokeStyle = landStroke(g);
    ctx.beginPath();
    let drawing = false;
    for (let x = BOX.x0; x <= BOX.x1 + 1e-6; x += 0.12) {
      const xx = Math.min(x, BOX.x1);
      if (g < shoreZ(xx)) {
        drawing = false;
        continue;
      }
      const q = toPlan([xx, 0, g], W, H);
      if (drawing) ctx.lineTo(q[0], q[1]);
      else {
        ctx.moveTo(q[0], q[1]);
        drawing = true;
      }
    }
    ctx.stroke();
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let g = -4; g <= 4; g++) {
    const p = toPlan([g, 0, BOX.z1], W, H);
    ctx.fillText(`${g}`, p[0], p[1] + 2);
  }
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let g = 0; g <= 3; g++) {
    const p = toPlan([BOX.x0, 0, g], W, H);
    ctx.fillText(`${g}`, p[0] + 3, p[1]);
  }
  ctx.textBaseline = "alphabetic";
  ctx.font = "9px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("距岸 km", toPlan([BOX.x0, 0, BOX.z1], W, H)[0] + 3, 14);
  ctx.textAlign = "right";
  ctx.fillText("东距 km", toPlan([BOX.x1, 0, BOX.z1], W, H)[0] - 4, 14);

  const compass = toPlan([0.15, 0, BOX.z1 - 0.42], W, H);
  drawCompass(ctx, compass[0], compass[1]);

  const samC = toPlan(SAM.c, W, H);
  [2, 4].forEach((km) => {
    const faint = km === 4;
    ctx.strokeStyle = faint ? "rgba(126,200,194,0.11)" : "rgba(126,200,194,0.34)";
    ctx.lineWidth = faint ? 0.8 : 1.15;
    if (faint) ctx.setLineDash([4, 6]);
    strokePoly(ctx, ring(ctx, samC[0], samC[1], km * sc));
    ctx.setLineDash([]);
    const lab = faint
      ? toPlan([SAM.c[0] + km * 0.62, 0, SAM.c[2] + km * 0.18], W, H)
      : toPlan([SAM.c[0] + km * 0.72, 0, SAM.c[2] - km * 0.35], W, H);
    ctx.fillStyle = faint ? "rgba(126,200,194,0.4)" : "rgba(126,200,194,0.75)";
    ctx.font = "10px Segoe UI, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`${km} km`, lab[0], lab[1]);
  });
  const bar0 = toPlan([BOX.x0 + 0.25, 0, BOX.z0 + 0.35], W, H);
  const bar1 = toPlan([BOX.x0 + 2.25, 0, BOX.z0 + 0.35], W, H);
  ctx.strokeStyle = "#ece6d4";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(bar0[0], bar0[1]);
  ctx.lineTo(bar1[0], bar1[1]);
  ctx.stroke();
  ctx.fillStyle = "#ece6d4";
  ctx.font = "10px Segoe UI, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("2 km", (bar0[0] + bar1[0]) / 2, bar0[1] - 5);

  {
    const n = 28;
    const pts = [toPlan(SEARCH.c, W, H)];
    for (let i = 0; i <= n; i++) {
      const a = SEARCH.az - SEARCH.half + (2 * SEARCH.half * i) / n;
      pts.push(
        toPlan(
          [SEARCH.c[0] + SEARCH.r * Math.sin(a), 0, SEARCH.c[2] + SEARCH.r * Math.cos(a)],
          W,
          H,
        ),
      );
    }
    ctx.fillStyle = "rgba(226,195,106,0.06)";
    fillPoly(ctx, pts);
    ctx.strokeStyle = "rgba(226,195,106,0.5)";
    ctx.lineWidth = 1.1;
    ctx.setLineDash([5, 4]);
    strokePoly(ctx, pts);
    ctx.setLineDash([]);
    const lab = toPlan([SEARCH.c[0] - 0.15, 0, SEARCH.c[2] - 2.35], W, H);
    ctx.fillStyle = "rgba(226,195,106,0.85)";
    ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("岸搜覆盖", lab[0], lab[1]);
    const t = scanNow();
    const shoreP0 = toPlan(SEARCH.c, W, H);
    ctx.save();
    fanPath(ctx, W, H);
    ctx.clip();
    drawRipples(ctx, shoreP0[0], shoreP0[1], SEARCH.r * sc, t, state.jammed ? 4.6 : 2.8, state.jammed ? "rgba(213,106,86,0.85)" : "rgba(226,195,106,0.9)", 3);
    ctx.restore();
    const sweepAng = drawFanSweep(ctx, W, H, t, state.jammed);
    const beam = state.jammed ? 0.08 : 0.12;
    living([...state.lo, ...(state.unk || [])]).forEach((a) => {
      if (!show(a)) return;
      const az = Math.atan2(a.p[0] - SEARCH.c[0], a.p[2] - SEARCH.c[2]);
      if (Math.abs(wrapPi(az - sweepAng)) < beam) {
        const q = toPlan(a.p, W, H);
        paintPing(ctx, q[0], q[1], a.side === "unk" ? "rgba(226,195,106,0.9)" : "rgba(255,120,96,0.9)");
      }
    });
  }

  function groundRing(b, color, dash) {
    const c = toPlan(b.c, W, H);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.globalAlpha = layer === "all" ? 0.55 : 0.22;
    if (dash) ctx.setLineDash(dash);
    strokePoly(ctx, ring(ctx, c[0], c[1], b.r * sc));
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }
  if (layer !== "lo") groundRing(BMD, "rgba(111,180,174,0.85)");
  if (layer !== "hi") {
    groundRing(SAM, "rgba(126,200,194,0.9)");
    groundRing({ c: SAM.c, r: SAM.r * SAM_RIM, z0: SAM.z0 }, "rgba(126,200,194,0.5)", [5, 4]);
  }
  groundRing(SHIP, "rgba(216,210,196,0.7)");
  ctx.font = "10px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(126,200,194,0.85)";
  const samLab = toPlan([SAM.c[0] + 1.35, 0, SAM.c[2] + 1.15], W, H);
  if (layer !== "hi") ctx.fillText("SAM交战", samLab[0], samLab[1]);
  ctx.fillStyle = "rgba(126,200,194,0.55)";
  const rimLab = toPlan([SAM.c[0] + 0.55, 0, SAM.c[2] + SAM.r * SAM_RIM * 0.72], W, H);
  if (layer !== "hi") ctx.fillText("内界", rimLab[0], rimLab[1]);
  ctx.fillStyle = "rgba(111,180,174,0.85)";
  const bmdLab = toPlan([BMD.c[0] + 1.6, 0, BMD.c[2] + 1.7], W, H);
  if (layer !== "lo") ctx.fillText("BMD交战", bmdLab[0], bmdLab[1]);

  const box = [
    toPlan([COR.x0, 0, COR.z0], W, H),
    toPlan([COR.x1, 0, COR.z0], W, H),
    toPlan([COR.x1, 0, COR.z1], W, H),
    toPlan([COR.x0, 0, COR.z1], W, H),
  ];
  ctx.fillStyle = "rgba(196,80,70,0.1)";
  fillPoly(ctx, box);
  ctx.strokeStyle = "rgba(213,106,86,0.85)";
  ctx.lineWidth = 1.2;
  strokePoly(ctx, box);

  const jamP = toPlan(JAM.c, W, H);
  drawJamSignal(ctx, jamP[0], jamP[1], JAM.r * sc, scanNow(), state.jammed);
  drawJammer(ctx, jamP[0], jamP[1], state.jammed);
  siteTag(ctx, jamP[0], jamP[1] + 2, state.jammed ? "干扰信号·开" : "干扰源");

  const linkAir = state.jammed ? null : "rgba(122,210,232,0.55)";
  const mesh = "rgba(122,210,232,0.55)";
  function strokeLink(a, b, color, dash = [], width = 1.2) {
    if (!color) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const samP = toPlan(SAM.c, W, H);
  const bmdP = toPlan(BMD.c, W, H);
  const shipP = toPlan(SHIP.c, W, H);
  const c2P = toPlan(C2, W, H);
  const shoreP = toPlan(RADAR.shore, W, H);
  const samRP = toPlan(RADAR.sam, W, H);

  drawSam(ctx, samP[0], samP[1]);
  siteTag(ctx, samP[0], samP[1], "SAM");
  drawBmd(ctx, bmdP[0], bmdP[1]);
  siteTag(ctx, bmdP[0], bmdP[1], "BMD");
  drawShip(ctx, shipP[0], shipP[1], SHIP_HDG);
  siteTag(ctx, shipP[0], shipP[1] + 6, "军舰");
  drawC2(ctx, c2P[0], c2P[1]);
  siteTag(ctx, c2P[0], c2P[1], "C2");
  drawSearchRadar(ctx, shoreP[0], shoreP[1]);
  siteTag(ctx, shoreP[0], shoreP[1] + 4, "岸搜");
  drawRadar(ctx, samRP[0], samRP[1]);
  siteTag(ctx, samRP[0], samRP[1] + 4, "制导");
  {
    const t = scanNow();
    const fcR = SAM.r * 0.72 * sc;
    drawRipples(ctx, samRP[0], samRP[1], fcR, t, state.jammed ? 3.4 : 1.8, "rgba(126,200,194,0.85)", 2);
    drawRipples(ctx, shipP[0], shipP[1], SHIP.r * sc, t + 0.6, 3.1, "rgba(216,210,196,0.7)", 2);
  }
  drawKey(ctx, 12, 36, state.jammed);

  const picks = [];
  let raidSlot = 0;
  function plot(list) {
    living(list).forEach((a) => {
      if (!show(a)) return;
      const q = toPlan(a.p, W, H);
      drawTrack(ctx, q[0], q[1], a);
      const slot = a.side === "blue" ? 0 : raidSlot++;
      trackTag(ctx, q[0], q[1], a, slot);
      picks.push({ a, x: q[0], y: q[1] });
    });
  }
  plot(state.hi);
  plot(state.mid);
  plot(state.lo);
  plot(state.unk || []);
  function stationOf(a) {
    return a.layer === "hi" ? shoreP : samRP;
  }
  if (linkAir) {
    [...living(state.hi), ...living(state.mid)].forEach((a) => {
      if (!show(a)) return;
      const q = toPlan(a.p, W, H);
      strokeLink(q, stationOf(a), linkAir, [], 0.9);
    });
  }
  function meshLayer(list) {
    const pts = living(list).filter(show).map((a) => toPlan(a.p, W, H));
    if (pts.length < 2) return;
    pts.forEach((p, i) => strokeLink(p, pts[(i + 1) % pts.length], mesh, [2, 3], 0.9));
  }
  meshLayer(state.hi);
  meshLayer(state.mid);
  (state.pairs || []).forEach((pair) => {
    const pb = picks.find((p) => p.a.id === pair.blue);
    const pr = picks.find((p) => p.a.id === pair.red);
    if (!pb || !pr) return;
    ctx.strokeStyle = "rgba(122,210,232,0.55)";
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(pb.x, pb.y);
    ctx.lineTo(pr.x, pr.y);
    ctx.stroke();
    ctx.setLineDash([]);
  });
  plan._picks = picks;
}

function drawCut() {
  const [ctx, W, H] = size(cut);
  ctx.clearRect(0, 0, W, H);
  const pad = 28;
  const z0 = -5.2;
  const z1 = 5.0;
  const yMax = 3.4;
  const X = (z) => pad + ((z - z0) / (z1 - z0)) * (W - pad * 2);
  const Y = (y) => H - pad - (y / yMax) * (H - pad * 2);

  ctx.fillStyle = "#0e1620";
  ctx.fillRect(pad, Y(0), Math.max(0, X(0) - pad), H - pad - Y(0));
  ctx.fillStyle = "#141a16";
  ctx.fillRect(X(0), Y(0), Math.max(0, W - pad - X(0)), H - pad - Y(0));

  ctx.strokeStyle = "rgba(232,226,208,0.45)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(X(0), pad);
  ctx.lineTo(X(0), H - pad);
  ctx.stroke();
  ctx.strokeStyle = "#2a2e38";
  ctx.beginPath();
  ctx.moveTo(pad, Y(0));
  ctx.lineTo(W - pad, Y(0));
  ctx.stroke();

  ctx.fillStyle = "#8d8878";
  ctx.font = "9px Segoe UI, sans-serif";
  ctx.textAlign = "left";
  [0, 0.1, 0.4, 1, 2].forEach((h) => {
    ctx.fillText(h === 0 ? "0" : h < 1 ? `${Math.round(h * 1000)} m` : `${h} km`, 4, Y(h) + 3);
    ctx.strokeStyle = "rgba(236,230,212,0.08)";
    ctx.beginPath();
    ctx.moveTo(pad, Y(h));
    ctx.lineTo(W - pad, Y(h));
    ctx.stroke();
  });
  ctx.fillStyle = "#8d8878";
  ctx.textAlign = "center";
  [-4, -2, 0, 2, 4].forEach((z) => {
    ctx.fillText(z === 0 ? "岸 0" : `${z}`, X(z), H - 10);
  });
  ctx.textAlign = "right";
  ctx.fillText("距岸 km · 海→陆", W - pad, H - 10);

  function arc(b, color, dash) {
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.75;
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    let started = false;
    for (let i = 0; i <= 48; i++) {
      const th = (i / 48) * Math.PI;
      const z = b.c[2] + b.r * Math.cos(th);
      const y = b.c[1] + b.r * Math.sin(th);
      if (y < 0) continue;
      if (!started) {
        ctx.moveTo(X(z), Y(y));
        started = true;
      } else ctx.lineTo(X(z), Y(y));
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }
  if (layer !== "lo") arc(BMD, "rgba(111,180,174,0.85)");
  if (layer !== "hi") {
    arc(SAM, "rgba(126,200,194,0.9)");
    arc({ c: SAM.c, r: SAM.r * SAM_RIM, z0: SAM.z0 }, "rgba(126,200,194,0.5)", [5, 4]);
  }
  arc({ c: SHIP.c, r: SHIP.r, z0: 0 }, "rgba(216,210,196,0.65)");

  ctx.fillStyle = "rgba(196,80,70,0.12)";
  ctx.strokeStyle = "rgba(213,106,86,0.85)";
  ctx.beginPath();
  ctx.rect(X(COR.z0), Y(COR.y1), X(COR.z1) - X(COR.z0), Y(COR.y0) - Y(COR.y1));
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "rgba(213,106,86,0.85)";
  ctx.font = "9px Segoe UI, PingFang SC, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("走廊 60–420 m", X(COR.z0) + 4, Y(COR.y1) - 4);

  drawShipSide(ctx, X(SHIP.c[2]), Y(0));
  drawRadarSide(ctx, X(RADAR.shore[2]), Y(0));
  drawRadarSide(ctx, X(RADAR.sam[2]), Y(0));
  {
    const t = scanNow();
    const sx = (X(1) - X(0));
    drawCutRipples(ctx, X(RADAR.shore[2]), Y(0), SEARCH.r * Math.abs(sx), t, state.jammed ? 4.6 : 2.8, state.jammed ? "rgba(213,106,86,0.8)" : "rgba(226,195,106,0.85)", true);
    drawCutRipples(ctx, X(RADAR.sam[2]), Y(0), SAM.r * 0.72 * Math.abs(sx), t, state.jammed ? 3.4 : 1.8, "rgba(126,200,194,0.8)", true);
    drawCutRipples(ctx, X(SHIP.c[2]), Y(0), SHIP.r * Math.abs(sx), t + 0.6, 3.1, "rgba(216,210,196,0.7)", true);
  }

  function dots(list) {
    living(list).forEach((a) => {
      if (!show(a)) return;
      ctx.strokeStyle = "rgba(236,230,212,0.22)";
      ctx.beginPath();
      ctx.moveTo(X(a.p[2]), Y(0));
      ctx.lineTo(X(a.p[2]), Y(a.p[1]));
      ctx.stroke();
      drawTrackCut(ctx, X(a.p[2]), Y(a.p[1]), a);
      if (a.side === "red" || a.side === "unk" || a.task === "盯梢" || a.task === "压沿" || a.task === "识别" || a.id === activeId()) {
        trackTag(ctx, X(a.p[2]), Y(a.p[1]), a);
      }
    });
  }
  dots(state.hi);
  dots(state.mid);
  dots(state.lo);
  dots(state.unk || []);
  drawJammer(ctx, X(JAM.c[2]), Y(JAM.c[1]), state.jammed);
  drawJamSignal(ctx, X(JAM.c[2]), Y(JAM.c[1]), JAM.r * Math.abs(X(1) - X(0)), scanNow(), state.jammed);
  (state.pairs || []).forEach((pair) => {
    const b = [...state.mid, ...state.hi].find((a) => a.id === pair.blue);
    const r = state.lo.find((a) => a.id === pair.red) || (state.unk || []).find((a) => a.id === pair.red);
    if (!b || !r || !show(b) || !show(r)) return;
    ctx.strokeStyle = "rgba(122,210,232,0.45)";
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(X(b.p[2]), Y(b.p[1]));
    ctx.lineTo(X(r.p[2]), Y(r.p[1]));
    ctx.stroke();
    ctx.setLineDash([]);
  });
}

function drawHemi(ctx, W, H, b, color) {
  const mer = 10;
  const steps = 28;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.35;
  for (let m = 0; m < mer; m++) {
    const phi = (m / mer) * Math.PI * 2;
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const th = (i / steps) * (Math.PI / 2);
      const u = [Math.sin(th) * Math.cos(phi), Math.cos(th), Math.sin(th) * Math.sin(phi)];
      const q = proj([b.c[0] + b.r * u[0], b.c[1] + b.r * u[1], b.c[2] + b.r * u[2]], W, H);
      i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawVol() {
  const [ctx, W, H] = size(vol);
  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = "rgba(232,226,208,0.22)";
  ctx.lineWidth = 1;
  const g0 = proj([-5, 0, 0], W, H);
  const g1 = proj([5.2, 0, 0], W, H);
  ctx.beginPath();
  ctx.moveTo(g0[0], g0[1]);
  ctx.lineTo(g1[0], g1[1]);
  ctx.stroke();

  const pc = [
    [COR.x0, COR.y0, COR.z0],
    [COR.x1, COR.y0, COR.z0],
    [COR.x1, COR.y0, COR.z1],
    [COR.x0, COR.y0, COR.z1],
  ].map((p) => proj(p, W, H));
  const pu = [
    [COR.x0, COR.y1, COR.z0],
    [COR.x1, COR.y1, COR.z0],
    [COR.x1, COR.y1, COR.z1],
    [COR.x0, COR.y1, COR.z1],
  ].map((p) => proj(p, W, H));
  ctx.fillStyle = "rgba(196,80,70,0.08)";
  fillPoly(ctx, pc);
  ctx.strokeStyle = "rgba(213,106,86,0.7)";
  strokePoly(ctx, pc);
  strokePoly(ctx, pu);
  for (let i = 0; i < 4; i++) {
    ctx.beginPath();
    ctx.moveTo(pc[i][0], pc[i][1]);
    ctx.lineTo(pu[i][0], pu[i][1]);
    ctx.stroke();
  }

  if (layer !== "lo") drawHemi(ctx, W, H, BMD, "rgba(111,180,174,0.75)");
  if (layer !== "hi") {
    drawHemi(ctx, W, H, SAM, "rgba(126,200,194,0.8)");
    drawHemi(ctx, W, H, { c: SAM.c, r: SAM.r * SAM_RIM, z0: SAM.z0 }, "rgba(126,200,194,0.4)");
  }
  drawHemi(ctx, W, H, { c: SHIP.c, r: SHIP.r, z0: 0 }, "rgba(216,210,196,0.55)");

  function lollipop(a) {
    if (!show(a)) return;
    const foot = proj([a.p[0], 0, a.p[2]], W, H);
    const head = proj(a.p, W, H);
    ctx.strokeStyle = "rgba(236,230,212,0.28)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(foot[0], foot[1]);
    ctx.lineTo(head[0], head[1]);
    ctx.stroke();
    drawTrack(ctx, head[0], head[1], a);
  }
  living(state.hi).forEach(lollipop);
  living(state.mid).forEach(lollipop);
  living(state.lo).forEach(lollipop);

  ctx.fillStyle = "#8d8878";
  ctx.font = "11px Palatino Linotype, Songti SC, serif";
  ctx.textAlign = "center";
  const lb = proj([BMD.c[0], BMD.c[1] + BMD.r, BMD.c[2]], W, H);
  const ls = proj([SAM.c[0], SAM.c[1] + SAM.r, SAM.c[2]], W, H);
  if (layer !== "lo") ctx.fillText("B_BMD", lb[0], lb[1] - 4);
  if (layer !== "hi") ctx.fillText("B_SAM", ls[0], ls[1] - 4);
}

function fillIntel() {
  const list = [...living(state.unk || []), ...living(state.lo)].filter(show);
  const dock = document.getElementById("dock");
  if (dock.dataset.open === "intel") {
    document.getElementById("drawer-title").textContent = state.jammed ? "情报 · 局部" : "情报";
  }
  const brief = briefIntel(state);
  const briefBox = document.getElementById("intel-brief");
  const threatRows = brief.threats.length
    ? brief.threats
        .map((t, i) => {
          const on = t.id === activeId() ? "on" : "";
          const tag = t.gated ? "已过门" : t.where;
          return `<div class="intel-row host ${on}" data-id="${t.id}"><span class="tn">${i + 1} ${t.tn}</span><span class="iden">${tag}</span><span class="intent">${t.rim}</span><span class="src">p ${t.p.toFixed(2)}</span></div>`;
        })
        .join("")
    : `<p class="empty">无判明威胁</p>`;
  const openRows = brief.opens.length
    ? brief.opens
        .map((t) => {
          const on = t.id === activeId() ? "on" : "";
          return `<div class="intel-row unk ${on}" data-id="${t.id}"><span class="tn">${t.tn}</span><span class="iden">不明</span><span class="intent">未判明</span><span class="src">p ${t.p.toFixed(2)}</span></div>`;
        })
        .join("")
    : "";
  const briefHtml = `<p class="file-lab">情报综合 · t ${brief.t} s · ${brief.src}</p>
    <dl class="brief">
    <div><dt>地域</dt><dd>${brief.area}</dd></div>
    <div><dt>敌情</dt><dd>波 ${brief.wave} · 敌 ${brief.host} · 不明 ${brief.unk}<br>${brief.scheme}</dd></div>
    <div><dt>电磁</dt><dd>${brief.em}</dd></div>
    <div><dt>空隙</dt><dd>${brief.gaps}</dd></div>
    <div><dt>威胁</dt><dd>按距 SAM 内界，近者优先</dd></div>
  </dl>${threatRows}${openRows ? `<p class="file-lab">未判明</p>${openRows}` : ""}`;
  if (briefBox.dataset.sig !== briefHtml) {
    briefBox.dataset.sig = briefHtml;
    briefBox.innerHTML = briefHtml;
  }

  const listBox = document.getElementById("intel-list");
  const fileBox = document.getElementById("intel-file");
  if (!list.length) {
    const empty = `<p class="file-lab">目标诸元</p><p class="empty">无来袭批</p>`;
    if (listBox.dataset.sig !== empty) {
      listBox.dataset.sig = empty;
      listBox.innerHTML = empty;
    }
    if (fileBox.dataset.key !== "none") {
      fileBox.dataset.key = "none";
      fileBox.innerHTML = "";
    }
    return;
  }
  const pick = list.find((a) => a.id === activeId()) || list[0];
  const sheet = trackSheet(pick, state);
  const rec = sheet.file ? INTEL_FILES[sheet.file] : null;
  const sheetHtml = `<p class="file-lab">目标诸元 · ${sheet.tn}</p>
    <dl class="sheet">
      <div><dt>身份</dt><dd>${sheet.iden} · ${sheet.type} · p ${sheet.p.toFixed(2)}</dd></div>
      <div><dt>位置</dt><dd>东 ${sheet.east.toFixed(2)} km · 距岸 ${sheet.shore.toFixed(2)} km · 高 ${sheet.altM} m</dd></div>
      <div><dt>运动</dt><dd>${Math.round(sheet.kmh)} km/h · 航向 ${String(sheet.hdg).padStart(3, "0")}°（000 向陆）</dd></div>
      <div><dt>企图</dt><dd>${sheet.intent}</dd></div>
      <div><dt>依据</dt><dd>${sheet.src}</dd></div>
      <div><dt>距界</dt><dd>${sheet.pos} · ${sheet.rim}</dd></div>
      <div><dt>建议</dt><dd>${sheet.act}${sheet.act.includes("不打") || sheet.act === "待门" ? " · 人在回路" : ""}</dd></div>
    </dl>`;
  if (listBox.dataset.sig !== sheetHtml) {
    listBox.dataset.sig = sheetHtml;
    listBox.innerHTML = sheetHtml;
  }
  const key = rec ? `${sheet.file}:${Math.round(sheet.kmh)}` : "none";
  if (fileBox.dataset.key !== key) {
    fileBox.dataset.key = key;
    if (!rec) {
      fileBox.innerHTML = "";
    } else {
      const combat = (rec.combat || []).map((s) => `<div><dt>${s[0]}</dt><dd>${s[1]}</dd></div>`).join("");
      const obs = rec.code === "U-?"
        ? `本批观测 ${Math.round(sheet.kmh)} km/h · 档案 400–600`
        : `本批观测 ${Math.round(sheet.kmh)} km/h · 档案突击 650–820`;
      fileBox.innerHTML = `<p class="file-lab">类型参考</p><figure class="dossier"><img src="${rec.img}" alt="${rec.code}" /><figcaption><b>${rec.code} ${rec.klass}</b><i>成像参考 · ${obs}</i></figcaption></figure><dl class="spec">${combat}</dl><p class="intel-note">${rec.note}</p>`;
    }
  }
}

function fillTable() {
  const rows = [...living(state.lo), ...living(state.unk || []), ...living(state.mid), ...living(state.hi)].filter(show);
  const body = document.getElementById("track-body");
  body.innerHTML = rows
    .map((a) => {
      const on = a.id === activeId() ? "on" : "";
      const side = a.side === "red" ? "host" : a.side === "unk" ? "unk" : "friend";
      const v = verdict(a, state.jammed);
      const intel = intelOf(a, state.jammed, state.pairs || []);
      const rim = a.side === "blue" ? "—" : toRim(a.p).label;
      const actCls = v.act.includes("不打") ? "no" : v.act.includes("过门") || v.act === "待门" ? "go" : "";
      return `<tr class="${side} ${on}" data-id="${a.id}" title="依据：${intel.src}"><td>${a.tn}</td><td>${Math.round(a.p[1] * 1000)}</td><td>${a.belief.toFixed(2)}</td><td class="iden ${side}">${intel.iden}</td><td>${intel.intent}</td><td>${rim}</td><td class="act ${actCls}">${v.act}</td></tr>`;
    })
    .join("");
}

function hud() {
  const sw = battle.soWhat();
  const el = document.getElementById("say");
  el.innerHTML = sw.text.replace(/(p≥0\.85|过不了门|饱和不是开门理由|跟踪，不打|未进 SAM 区 · 不打|前出盯梢|不明仍不打|识别，不打)/, "<em>$1</em>");
  const box = document.getElementById("branches");
  const html = battle
    .branches()
    .map(
      (b) =>
        `<div class="branch ${b.hot ? "hot" : ""} ${b.id === sw.id ? "active" : ""}"><p class="ttl">${b.title}</p><p class="val">${b.value}</p><div class="hint">${b.tip}</div></div>`,
    )
    .join("");
  if (box.dataset.sig !== html) {
    box.dataset.sig = html;
    box.innerHTML = html;
  }
  document.getElementById("events").innerHTML = state.events
    .slice(0, 6)
    .map((e) => `<li><b>t ${e.t}</b>${e.text}</li>`)
    .join("");
  fillIntel();
  fillTable();
}

function render() {
  battle.tickKb();
  drawPlan();
  drawCut();
  if (showVol) drawVol();
  hud();
}

function pick(mx, my) {
  const picks = plan._picks || [];
  let best = 28;
  let id = null;
  picks.forEach((p) => {
    const d = Math.hypot(p.x - mx, p.y - my);
    if (d < best) {
      best = d;
      id = p.a.id;
    }
  });
  hoverId = id;
}

plan.addEventListener("pointermove", (e) => {
  const r = plan.getBoundingClientRect();
  pick(e.clientX - r.left, e.clientY - r.top);
});
plan.addEventListener("pointerleave", () => {
  hoverId = null;
});

let drag = false;
let lx = 0;
let ly = 0;
vol.addEventListener("pointerdown", (e) => {
  drag = true;
  lx = e.clientX;
  ly = e.clientY;
  vol.setPointerCapture(e.pointerId);
});
vol.addEventListener("pointermove", (e) => {
  if (!drag) return;
  cam.yaw += (e.clientX - lx) * 0.008;
  cam.pitch = Math.max(0.35, Math.min(1.2, cam.pitch + (e.clientY - ly) * 0.006));
  lx = e.clientX;
  ly = e.clientY;
});
vol.addEventListener("pointerup", () => {
  drag = false;
});
vol.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    cam.dist = Math.max(6.5, Math.min(18, cam.dist + e.deltaY * 0.01));
  },
  { passive: false },
);

document.getElementById("track-body").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  selectedId = tr.dataset.id;
});
document.getElementById("intel-body").addEventListener("click", (e) => {
  const row = e.target.closest("[data-id]");
  if (!row) return;
  selectedId = row.dataset.id;
});

function setDrawer(id) {
  const dock = document.getElementById("dock");
  const drawer = document.getElementById("drawer");
  const titles = { intel: "情报", cmd: "指挥", tracks: "航迹" };
  const next = dock.dataset.open === id ? "" : id || "";
  dock.dataset.open = next;
  drawer.classList.toggle("open", Boolean(next));
  drawer.setAttribute("aria-hidden", next ? "false" : "true");
  document.getElementById("drawer-title").textContent = titles[next] || "";
  document.querySelectorAll(".rail-btn").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.drawer === next);
  });
  document.querySelectorAll(".card").forEach((card) => {
    card.classList.toggle("shown", Boolean(next) && card.id === `card-${next}`);
  });
  render();
}
document.querySelectorAll(".rail-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setDrawer(btn.dataset.drawer);
  });
});
document.getElementById("drawer-close")?.addEventListener("click", (e) => {
  e.stopPropagation();
  setDrawer("");
});

function setView(volOn) {
  showVol = volOn;
  document.getElementById("track-wrap").hidden = volOn;
  vol.hidden = !volOn;
  document.getElementById("view-list").classList.toggle("on", !volOn);
  document.getElementById("view-vol").classList.toggle("on", volOn);
  if (volOn) setDrawer("tracks");
}
document.getElementById("view-list").addEventListener("click", () => setView(false));
document.getElementById("view-vol").addEventListener("click", () => setView(true));

function setLayer(id) {
  layer = id;
  ["all", "lo", "mid", "hi"].forEach((k) => {
    document.getElementById(`ly-${k}`).classList.toggle("on", k === id);
  });
}
document.getElementById("ly-all").addEventListener("click", () => setLayer("all"));
document.getElementById("ly-lo").addEventListener("click", () => setLayer("lo"));
document.getElementById("ly-mid").addEventListener("click", () => setLayer("mid"));
document.getElementById("ly-hi").addEventListener("click", () => setLayer("hi"));

document.getElementById("play").addEventListener("click", () => {
  playing = !playing;
  const freezeEl = document.getElementById("freeze");
  if (playing) {
    frozen = false;
    acc = 0;
    last = performance.now();
    if (freezeEl) freezeEl.hidden = true;
    document.getElementById("play").textContent = "止";
    if (!state.k) battle.nextWave();
  } else {
    document.getElementById("play").textContent = "演";
  }
});
document.getElementById("wave").addEventListener("click", () => battle.nextWave());
document.getElementById("reset").addEventListener("click", () => {
  playing = false;
  frozen = false;
  acc = 0;
  last = performance.now();
  document.getElementById("play").textContent = "演";
  const freezeEl = document.getElementById("freeze");
  if (freezeEl) freezeEl.hidden = true;
  battle.reset();
  battle.nextWave();
});
document.querySelectorAll("[data-inject]").forEach((btn) => {
  btn.addEventListener("click", () => battle.inject(btn.dataset.inject));
});

let acc = 0;
let last = performance.now();

document.addEventListener("visibilitychange", () => {
  acc = 0;
  last = performance.now();
  if (document.hidden && playing) {
    playing = false;
    frozen = true;
    document.getElementById("play").textContent = "止（冻）";
    const freezeEl = document.getElementById("freeze");
    if (freezeEl) freezeEl.hidden = false;
  }
});

function loop(now) {
  const dt = now - last;
  last = now;
  if (document.hidden || frozen) {
    acc = 0;
  } else {
    acc += dt;
    while (acc >= 40) {
      acc -= 40;
      if (playing) battle.step(TICK_S);
      else battle.idle();
    }
  }
  render();
  requestAnimationFrame(loop);
}

window.addEventListener("resize", render);
battle.nextWave();
const extraWaves = Math.max(0, Number(new URLSearchParams(location.search).get("w") || 1) - 1);
for (let i = 0; i < extraWaves; i += 1) battle.nextWave();
const warmup = Number(new URLSearchParams(location.search).get("t") || 0);
for (let i = 0; i < warmup; i += 1) battle.idle();
requestAnimationFrame(loop);
