/** Retired. Inner loop drawing is swarm.js. */

export const cam = {
  yaw: 0.72,
  pitch: 0.48,
  dist: 9.6,
  auto: true,
};

let look = [0, 0, 0];

const UP = 2.35;
const PALETTE = {
  bg: "#0c1018",
  ink: "#ece6d4",
  mute: "rgba(232,226,212,0.45)",
  gold: "#e2c36a",
  teal: "#6fb4ae",
  rust: "#d56a56",
};

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function isolines(field, level) {
  const n = field.length;
  const segs = [];
  const interp = (ax, ay, va, bx, by, vb) => {
    const t = (level - va) / (vb - va + 1e-9);
    return [lerp(ax, bx, t), lerp(ay, by, t)];
  };
  for (let i = 0; i < n - 1; i += 1) {
    for (let j = 0; j < n - 1; j += 1) {
      const v = [field[i][j], field[i][j + 1], field[i + 1][j + 1], field[i + 1][j]];
      let m = 0;
      if (v[0] >= level) m |= 1;
      if (v[1] >= level) m |= 2;
      if (v[2] >= level) m |= 4;
      if (v[3] >= level) m |= 8;
      if (m === 0 || m === 15) continue;
      const e = [
        interp(i, j, v[0], i, j + 1, v[1]),
        interp(i, j + 1, v[1], i + 1, j + 1, v[2]),
        interp(i + 1, j + 1, v[2], i + 1, j, v[3]),
        interp(i + 1, j, v[3], i, j, v[0]),
      ];
      const pairs = {
        1: [0, 3],
        2: [0, 1],
        3: [3, 1],
        4: [1, 2],
        5: [0, 3, 1, 2],
        6: [0, 2],
        7: [3, 2],
        8: [3, 2],
        9: [0, 2],
        10: [0, 1, 3, 2],
        11: [1, 2],
        12: [3, 1],
        13: [0, 1],
        14: [0, 3],
      }[m];
      for (let k = 0; k < pairs.length; k += 2) segs.push([e[pairs[k]], e[pairs[k + 1]]]);
    }
  }
  return segs;
}

function worldOf(p, L, up = UP) {
  return [p[0] - (L - 1) / 2, (p[2] ?? 0) * up, p[1] - (L - 1) / 2];
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

function projectW(w, W, H) {
  const [x, y, z] = rot([w[0] - look[0], w[1] - look[1], w[2] - look[2]], cam.yaw, cam.pitch);
  const depth = z + cam.dist;
  const s = (1.08 * Math.min(W, H)) / Math.max(depth, 0.35);
  return [W * 0.5 + x * s, H * 0.54 - y * s, depth, s];
}

function Pgrid(p, L, W, H) {
  return projectW(worldOf(p, L), W, H);
}

function fade(depth, near = 0.15, far = 0.02) {
  const t = Math.max(0, Math.min(1, (18 - depth) / 12));
  return near * t + far * (1 - t);
}

function strokePoly(ctx, pts) {
  ctx.beginPath();
  pts.forEach((p, i) => {
    if (i === 0) ctx.moveTo(p[0], p[1]);
    else ctx.lineTo(p[0], p[1]);
  });
  ctx.closePath();
  ctx.stroke();
}

function fillPoly(ctx, pts) {
  ctx.beginPath();
  pts.forEach((p, i) => {
    if (i === 0) ctx.moveTo(p[0], p[1]);
    else ctx.lineTo(p[0], p[1]);
  });
  ctx.closePath();
  ctx.fill();
}

function sphereCenter(form, L) {
  const c = worldOf([form.center[0], form.center[1], 0], L);
  c[1] = form.radius * 0.92;
  return c;
}

function spherePoint(C, R, u) {
  return [C[0] + R * u[0], C[1] + R * u[1], C[2] + R * u[2]];
}

function fibonacciSphere(k) {
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

function unitToWorld(C, R, u) {
  return [C[0] + R * u[0], C[1] + R * u[2], C[2] + R * u[1]];
}

function drawVignette(ctx, W, H) {
  const g = ctx.createRadialGradient(W * 0.5, H * 0.48, Math.min(W, H) * 0.12, W * 0.5, H * 0.5, Math.max(W, H) * 0.72);
  g.addColorStop(0, "rgba(18, 28, 42, 0)");
  g.addColorStop(1, "rgba(6, 8, 12, 0.55)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

function drawSphereShell(ctx, C, R, W, H) {
  const meridians = 14;
  const parallels = 7;
  const steps = 48;
  const lines = [];

  for (let m = 0; m < meridians; m += 1) {
    const phi = (m / meridians) * Math.PI * 2;
    const pts = [];
    for (let i = 0; i <= steps; i += 1) {
      const th = (i / steps) * Math.PI;
      const u = [Math.sin(th) * Math.cos(phi), Math.cos(th), Math.sin(th) * Math.sin(phi)];
      const q = projectW(spherePoint(C, R, u), W, H);
      pts.push(q);
    }
    lines.push(pts);
  }
  for (let p = 1; p < parallels; p += 1) {
    const th = (p / parallels) * Math.PI;
    const pts = [];
    for (let i = 0; i <= steps; i += 1) {
      const phi = (i / steps) * Math.PI * 2;
      const u = [Math.sin(th) * Math.cos(phi), Math.cos(th), Math.sin(th) * Math.sin(phi)];
      pts.push(projectW(spherePoint(C, R, u), W, H));
    }
    lines.push(pts);
  }

  const equator = [];
  for (let i = 0; i <= 64; i += 1) {
    const phi = (i / 64) * Math.PI * 2;
    equator.push(projectW(spherePoint(C, R, [Math.cos(phi), 0, Math.sin(phi)]), W, H));
  }

  const disc = equator.slice();
  ctx.fillStyle = "rgba(226,195,106,0.05)";
  fillPoly(ctx, disc);

  const front = (pt) => pt[2] < C[2] + cam.dist;

  ctx.lineWidth = 1;
  for (const pts of lines) {
    ctx.beginPath();
    let drawing = false;
    for (let i = 0; i < pts.length; i += 1) {
      const a = fade(pts[i][2], front(pts[i]) ? 0.38 : 0.1, 0.04);
      if (i === 0) {
        ctx.strokeStyle = `rgba(232,226,212,${a})`;
        ctx.moveTo(pts[i][0], pts[i][1]);
        drawing = true;
        continue;
      }
      ctx.lineTo(pts[i][0], pts[i][1]);
      if (i % 4 === 0 || i === pts.length - 1) {
        ctx.strokeStyle = `rgba(232,226,212,${a})`;
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(pts[i][0], pts[i][1]);
      }
    }
    if (drawing) ctx.stroke();
  }

  ctx.strokeStyle = "rgba(226,195,106,0.55)";
  ctx.lineWidth = 1.35;
  ctx.beginPath();
  equator.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath();
  ctx.stroke();
}

function label(ctx, text, x, y, color = PALETTE.mute, size = 12, align = "left") {
  ctx.fillStyle = color;
  ctx.font = `${size}px Palatino Linotype, Palatino, Songti SC, STSong, serif`;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y);
}

export function drawWorld(canvas, snap) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  if (canvas.width !== Math.floor(W * dpr) || canvas.height !== Math.floor(H * dpr)) {
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = PALETTE.bg;
  ctx.fillRect(0, 0, W, H);

  if (cam.auto) cam.yaw += 0.0024;

  const L = snap.L;
  const form = snap.form;
  if (form && form.slots.length > 1) {
    const C = sphereCenter(form, L);
    look = [C[0], C[1] * 0.22, C[2]];
  } else {
    look = [0, 0.6, 0];
  }
  const P = (p) => Pgrid(p, L, W, H);

  const corners = [
    [0, 0, 0],
    [L - 1, 0, 0],
    [L - 1, L - 1, 0],
    [0, L - 1, 0],
  ].map((c) => P(c));

  ctx.fillStyle = "rgba(111,180,174,0.035)";
  fillPoly(ctx, corners);
  ctx.strokeStyle = "rgba(232,226,212,0.28)";
  ctx.lineWidth = 1.15;
  strokePoly(ctx, corners);

  for (let i = 0; i < L; i += 1) {
    const a = P([i, 0, 0]);
    const b = P([i, L - 1, 0]);
    ctx.strokeStyle = "rgba(232,226,212,0.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    const c = P([0, i, 0]);
    const d = P([L - 1, i, 0]);
    ctx.beginPath();
    ctx.moveTo(c[0], c[1]);
    ctx.lineTo(d[0], d[1]);
    ctx.stroke();
  }

  for (let i = 0; i < L - 1; i += 1) {
    for (let j = 0; j < L - 1; j += 1) {
      const b =
        (snap.team[i][j] + snap.team[i][j + 1] + snap.team[i + 1][j + 1] + snap.team[i + 1][j]) / 4;
      const seen = snap.visited[i][j] || snap.visited[i][j + 1] || snap.visited[i + 1][j] || snap.visited[i + 1][j + 1];
      if (!seen && Math.abs(b - 0.5) < 0.04) continue;
      const cert = Math.abs(b - 0.5) * 2;
      const q = [P([i, j, 0]), P([i, j + 1, 0]), P([i + 1, j + 1, 0]), P([i + 1, j, 0])];
      const alpha = (seen ? 0.16 : 0.05) + cert * 0.42;
      ctx.fillStyle = b > 0.5 ? `rgba(213,106,86,${alpha})` : `rgba(111,180,174,${alpha})`;
      fillPoly(ctx, q);
    }
  }

  const levels = [
    [0.5, "rgba(232,226,212,0.35)", 1, "p = ½"],
    [0.7, "rgba(111,180,174,0.85)", 1.5, "p = 0.70"],
    [0.85, "rgba(226,195,106,0.95)", 2.1, "p = 0.85"],
  ];
  for (const [lv, col, w, name] of levels) {
    const segs = isolines(snap.team, lv);
    ctx.strokeStyle = col;
    ctx.lineWidth = w;
    let tag = null;
    for (const [a, b] of segs) {
      const pa = P([a[0], a[1], 0]);
      const pb = P([b[0], b[1], 0]);
      ctx.beginPath();
      ctx.moveTo(pa[0], pa[1]);
      ctx.lineTo(pb[0], pb[1]);
      ctx.stroke();
      const mid = [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2];
      if (!tag || mid[0] > tag[0]) tag = mid;
    }
    if (tag && segs.length > 4 && lv >= 0.84) label(ctx, name, tag[0] + 10, tag[1], col, 12);
  }

  snap.pos.forEach((p, i) => {
    if (!snap.alive[i]) return;
    const z = p[2] ?? 0;
    const rSense = 1.15 + z * 0.85;
    const ring = [];
    for (let k = 0; k <= 36; k += 1) {
      const th = (k / 36) * Math.PI * 2;
      ring.push(P([p[0] + rSense * Math.cos(th), p[1] + rSense * Math.sin(th), 0]));
    }
    ctx.fillStyle = z === 2 ? "rgba(232,226,212,0.05)" : "rgba(111,180,174,0.05)";
    fillPoly(ctx, ring);
  });

  const k = form.slots.length;
  if (k > 1) {
    const unit = form.unit && form.unit.length === k ? form.unit : fibonacciSphere(k);
    const C = sphereCenter(form, L);
    const R = form.radius;
    drawSphereShell(ctx, C, R, W, H);

    const idealPts = unit.map((u) => projectW(unitToWorld(C, R, u), W, H));

    const chords = [];
    for (let a = 0; a < k; a += 1) {
      for (let b = a + 1; b < k; b += 1) {
        const ua = unit[a];
        const ub = unit[b];
        const d = Math.hypot(ua[0] - ub[0], ua[1] - ub[1], (ua[2] ?? 0) - (ub[2] ?? 0));
        chords.push({ a, b, d });
      }
    }
    chords.sort((x, y) => x.d - y.d);
    const minD = chords[0]?.d ?? 1;
    for (const ch of chords) {
      const pa = idealPts[ch.a];
      const pb = idealPts[ch.b];
      const near = ch.d < minD + 0.25;
      ctx.strokeStyle = near ? "rgba(226,195,106,0.95)" : `rgba(226,195,106,${0.16 + 0.28 * (2 - ch.d)})`;
      ctx.lineWidth = near ? 2.35 : 1.15;
      ctx.beginPath();
      ctx.moveTo(pa[0], pa[1]);
      ctx.lineTo(pb[0], pb[1]);
      ctx.stroke();
    }
    if (chords[0]) {
      const pa = idealPts[chords[0].a];
      const pb = idealPts[chords[0].b];
      label(ctx, "δ  最小弦", (pa[0] + pb[0]) / 2 + 10, (pa[1] + pb[1]) / 2 - 8, PALETTE.gold, 12);
    }

    const north = projectW(spherePoint(C, R, [0, 1, 0]), W, H);
    label(ctx, "S²  球面码", north[0] + 14, north[1] - 10, PALETTE.ink, 15);

    form.alive?.forEach((agent, t) => {
      const slot = form.assign ? form.assign[t] : t;
      const g = P(snap.pos[agent]);
      const s = idealPts[slot];
      if (!s || !snap.alive[agent]) return;
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = "rgba(226,195,106,0.55)";
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.moveTo(g[0], g[1]);
      ctx.lineTo(s[0], s[1]);
      ctx.stroke();
      ctx.setLineDash([]);
    });

    idealPts.forEach((s) => {
      ctx.beginPath();
      ctx.arc(s[0], s[1], 3.2, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.gold;
      ctx.fill();
    });
  }

  ctx.lineWidth = 1.15;
  ctx.strokeStyle = snap.jammed ? "rgba(213,106,86,0.22)" : "rgba(111,180,174,0.55)";
  for (const [a, b] of snap.links) {
    const pa = P(snap.pos[a]);
    const pb = P(snap.pos[b]);
    ctx.beginPath();
    ctx.moveTo(pa[0], pa[1]);
    ctx.lineTo(pb[0], pb[1]);
    ctx.stroke();
  }

  const marks = [];
  snap.targets.forEach((t) => marks.push({ kind: "t", p: t, depth: P(t)[2] }));
  snap.pos.forEach((p, i) => marks.push({ kind: "u", i, p, depth: P(p)[2] }));
  marks.sort((a, b) => b.depth - a.depth);

  for (const m of marks) {
    if (m.kind === "t") {
      const q = P(m.p);
      ctx.strokeStyle = PALETTE.rust;
      ctx.lineWidth = 1.7;
      ctx.beginPath();
      ctx.moveTo(q[0] - 6, q[1]);
      ctx.lineTo(q[0] + 6, q[1]);
      ctx.moveTo(q[0], q[1] - 6);
      ctx.lineTo(q[0], q[1] + 6);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(q[0], q[1], 7.5, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(213,106,86,0.45)";
      ctx.stroke();
      continue;
    }
    const g = P([m.p[0], m.p[1], 0]);
    const q = P(m.p);
    ctx.strokeStyle = "rgba(232,226,212,0.28)";
    ctx.lineWidth = 1.05;
    ctx.beginPath();
    ctx.moveTo(g[0], g[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.stroke();
    if (!snap.alive[m.i]) {
      ctx.strokeStyle = PALETTE.rust;
      ctx.beginPath();
      ctx.moveTo(q[0] - 6, q[1] - 6);
      ctx.lineTo(q[0] + 6, q[1] + 6);
      ctx.moveTo(q[0] + 6, q[1] - 6);
      ctx.lineTo(q[0] - 6, q[1] + 6);
      ctx.stroke();
      continue;
    }
    const r = 5.2 + m.p[2] * 1.8;
    ctx.beginPath();
    ctx.arc(q[0], q[1], r + 3.2, 0, Math.PI * 2);
    ctx.fillStyle = m.p[2] === 2 ? "rgba(232,226,212,0.16)" : "rgba(226,195,106,0.14)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(q[0], q[1], r, 0, Math.PI * 2);
    ctx.fillStyle = m.p[2] === 0 ? "#d4893c" : m.p[2] === 2 ? "#f3ebd4" : "#e2c36a";
    ctx.fill();
    ctx.fillStyle = PALETTE.bg;
    ctx.font = "11px Palatino Linotype, Palatino, serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(m.i), q[0], q[1] + 0.5);
  }

  if (snap.jammed) {
    label(ctx, "通联切断 · 图 G 变稀疏", W * 0.5, 28, PALETTE.rust, 14, "center");
  }

  drawVignette(ctx, W, H);
}

export function bindOrbit(canvas) {
  let dragging = false;
  let lx = 0;
  let ly = 0;
  canvas.addEventListener("pointerdown", (e) => {
    dragging = true;
    cam.auto = false;
    lx = e.clientX;
    ly = e.clientY;
    canvas.classList.add("drag");
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    cam.yaw += (e.clientX - lx) * 0.008;
    cam.pitch = Math.max(0.18, Math.min(1.12, cam.pitch + (e.clientY - ly) * 0.006));
    lx = e.clientX;
    ly = e.clientY;
  });
  const end = () => {
    dragging = false;
    canvas.classList.remove("drag");
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      cam.dist = Math.max(7.5, Math.min(22, cam.dist + e.deltaY * 0.01));
    },
    { passive: false },
  );
}

export function drawSpark(canvas, seriesA, seriesB) {
  const ctx = canvas.getContext("2d");
  const w = canvas.clientWidth || 160;
  const h = canvas.clientHeight || 44;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const plot = (data, color) => {
    if (!data.length) return;
    const max = Math.max(...data, 0.01);
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
  };
  plot(seriesA, "rgba(111,180,174,0.95)");
  plot(seriesB, "rgba(226,195,106,0.95)");
}
