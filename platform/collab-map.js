/** Shared schematic: 交战本体 fullscreen, or compact hawk-eye on 合成. */

import { zoneOf } from "./battle.js?v=25";

export function createCollabView(canvas, opts) {
  const { battle, compact } = opts;
  const { state } = battle;
  let hits = [];

  function allLive() {
    return opts.allLive();
  }

  function getSelected() {
    return opts.getSelected();
  }

  function plotBox(W, H) {
    if (compact) {
      return { x: 22, y: 12, w: Math.max(80, W - 28), h: Math.max(70, H - 18) };
    }
    const left = W < 960 ? 12 : 20;
    const right = W < 960 ? 16 : Math.min(456, W * 0.38 + 44);
    const top = 110;
    const bottom = 118;
    return { x: left, y: top, w: Math.max(220, W - left - right), h: Math.max(180, H - top - bottom) };
  }

  function xOf(box, a) {
    return box.x + 40 + ((a.p[2] + 5.2) / 9.6) * (box.w - 80);
  }

  function yOf(box, a) {
    const band = { hi: 0.28, mid: 0.52, lo: 0.78 };
    const east = (a.p[0] + 5.5) / 11;
    return box.y + box.h * (band[a.layer] + (east - 0.5) * 0.08);
  }

  function layout(box) {
    const s = compact ? 0.55 : 1;
    const nodes = [
      { type: "site", id: "c2", kind: "gold", x: box.x + box.w * 0.34, y: box.y + box.h * 0.08, r: 20 * s, label: compact ? "C2" : "岸上C2" },
      { type: "site", id: "radar", kind: "gold", x: box.x + box.w * 0.58, y: box.y + box.h * 0.08, r: 16 * s, label: compact ? "搜" : "岸搜" },
      { type: "site", id: "gate", kind: "gate", x: box.x + box.w * 0.9, y: box.y + box.h * 0.78, r: 18 * s, label: compact ? "门" : "授权门" },
    ];
    allLive().forEach((a) => {
      nodes.push({
        type: "agent",
        id: a.id,
        kind: a.side === "blue" ? (a.quarryTn ? "pair" : "form") : a.side === "unk" ? "pair" : a.gated ? "gate" : "form",
        a,
        x: xOf(box, a),
        y: yOf(box, a),
        r: compact ? 6 : 11,
        label: a.tn,
      });
    });
    return nodes;
  }

  function alpha(kind, on, off) {
    const selected = getSelected();
    if (selected.type === "kind") return selected.id === kind ? on : off;
    return on;
  }

  function drawLink(ctx, a, b, color, dash, width) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 1.2;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.restore();
  }

  function bead(ctx, a, b, phase, color, r) {
    const x = a.x + (b.x - a.x) * phase;
    const y = a.y + (b.y - a.y) * phase;
    ctx.beginPath();
    ctx.arc(x, y, r || 2.6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  function involved(n) {
    const selected = getSelected();
    if (selected.type !== "agent") return false;
    if (n.id === selected.id) return true;
    return (state.pairs || []).some(
      (p) => (p.blue === selected.id && p.red === n.id) || (p.red === selected.id && p.blue === n.id),
    );
  }

  function draw() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    if (!W || !H) return;
    const dw = Math.floor(W * dpr);
    const dh = Math.floor(H * dpr);
    if (canvas.width !== dw || canvas.height !== dh) {
      canvas.width = dw;
      canvas.height = dh;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0a0e14";
    ctx.fillRect(0, 0, W, H);

    const box = plotBox(W, H);
    const selected = getSelected();
    const fs = compact ? 8 : 11;
    const lw = compact ? 0.9 : 1;

    ctx.strokeStyle = "rgba(42,46,56,0.9)";
    ctx.lineWidth = lw;
    [0.18, 0.4, 0.64, 0.9].forEach((y) => {
      const yy = box.y + box.h * y;
      ctx.beginPath();
      ctx.moveTo(box.x, yy);
      ctx.lineTo(box.x + box.w, yy);
      ctx.stroke();
    });

    ctx.fillStyle = "rgba(141,136,120,0.8)";
    ctx.font = `${fs}px Segoe UI, PingFang SC, sans-serif`;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const bands = compact
      ? [
          [0.08, "岸"],
          [0.28, "高"],
          [0.52, "中"],
          [0.78, "廊"],
        ]
      : [
          [0.08, "岸上"],
          [0.28, "高空"],
          [0.52, "中空"],
          [0.78, "低空走廊"],
        ];
    bands.forEach(([y, lab]) => {
      ctx.fillText(lab, compact ? 4 : box.x + 8, box.y + box.h * y);
    });

    const nodes = layout(box);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const c2 = byId.c2;
    const radar = byId.radar;
    const gate = byId.gate;
    const t = state.t * 0.035;

    const goldA = alpha("gold", 0.9, 0.12);
    drawLink(
      ctx,
      c2,
      radar,
      state.jammed ? `rgba(213,106,86,${goldA * 0.7})` : `rgba(226,195,106,${goldA})`,
      state.jammed ? [5, 4] : [],
      compact ? 1.2 : 1.7,
    );
    if (!state.jammed) {
      bead(ctx, c2, radar, t % 1, `rgba(226,195,106,${goldA})`, compact ? 2 : 3);
      bead(ctx, radar, c2, (t * 0.7 + 0.4) % 1, `rgba(232,226,212,${goldA})`, compact ? 1.6 : 2.2);
    }

    nodes
      .filter((n) => n.a && n.a.side === "blue")
      .forEach((n, i) => {
        drawLink(
          ctx,
          c2,
          n,
          state.jammed ? `rgba(213,106,86,${goldA * 0.28})` : `rgba(226,195,106,${goldA * 0.42})`,
          state.jammed ? [4, 4] : [],
          compact ? 0.8 : 1,
        );
        if (!state.jammed) bead(ctx, n, c2, (t + i * 0.18) % 1, `rgba(226,195,106,${goldA * 0.95})`, compact ? 1.6 : 2.3);
      });

    function mesh(list, color, kind) {
      const a = alpha(kind, 0.72, 0.1);
      for (let i = 0; i < list.length; i += 1) {
        for (let j = i + 1; j < list.length; j += 1) {
          drawLink(ctx, list[i], list[j], color.replace("A", String(a)), [], compact ? 0.9 : 1.15);
        }
      }
    }
    mesh(
      nodes.filter((n) => n.a && n.a.layer === "hi"),
      "rgba(232,226,212,A)",
      "form",
    );
    mesh(
      nodes.filter((n) => n.a && n.a.layer === "mid"),
      "rgba(111,180,174,A)",
      "form",
    );
    mesh(
      nodes.filter((n) => n.a && n.a.layer === "lo" && n.a.side === "red" && !n.a.gated),
      "rgba(226,195,106,A)",
      "form",
    );

    const pairA = alpha("pair", 1, 0.12);
    const pairList = (state.pairs || []).filter((p) => byId[p.blue] && byId[p.red]);
    pairList.forEach((p, i) => {
      const b = byId[p.blue];
      const r = byId[p.red];
      const hot = selected.type === "agent" && (selected.id === p.blue || selected.id === p.red);
      const kindPair = selected.type === "kind" && selected.id === "pair";
      const a = hot ? 1 : pairA;
      drawLink(ctx, b, r, `rgba(122,210,232,${0.92 * a})`, [5, 4], hot ? (compact ? 1.6 : 2.2) : compact ? 1.2 : 1.8);
      bead(ctx, b, r, (t * 1.15 + i * 0.33) % 1, `rgba(186,232,244,${a})`, compact ? 2.2 : 3.1);
      const showLab = !compact && (hot || kindPair);
      if (!showLab) return;
      const dx = r.x - b.x;
      const dy = r.y - b.y;
      const len = Math.hypot(dx, dy) || 1;
      const spread = kindPair ? (i - (pairList.length - 1) / 2) * 16 : 0;
      const lx = (b.x + r.x) / 2 - (dy / len) * (12 + spread);
      const ly = (b.y + r.y) / 2 + (dx / len) * (12 + spread);
      ctx.fillStyle = `rgba(186,220,216,${0.95 * a})`;
      ctx.font = "11px Segoe UI, PingFang SC, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(`${b.a.tn}盯${r.a.tn}`, lx, ly);
    });

    const gateA = alpha("gate", 0.9, 0.1);
    nodes
      .filter((n) => n.a && n.a.side === "red")
      .forEach((n) => {
        const z = zoneOf(n.a.p);
        if (z !== "in" && z !== "edge" && !n.a.gated) return;
        drawLink(
          ctx,
          n,
          gate,
          n.a.gated ? `rgba(226,195,106,${gateA})` : `rgba(213,106,86,${gateA * 0.55})`,
          n.a.gated ? [] : [4, 4],
          n.a.gated ? (compact ? 1.2 : 1.7) : compact ? 0.9 : 1.1,
        );
        if (n.a.gated) bead(ctx, n, gate, (t * 0.8) % 1, `rgba(226,195,106,${gateA})`, compact ? 1.8 : 2.4);
      });

    nodes.forEach((n) => {
      const on =
        selected.type === "agent" ? selected.id === n.id || involved(n) : selected.type === "kind" && n.kind === selected.id;
      ctx.beginPath();
      if (n.type === "site") {
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = n.kind === "gate" ? "rgba(213,106,86,0.18)" : "rgba(226,195,106,0.16)";
        ctx.fill();
        ctx.strokeStyle = on ? "#e2c36a" : n.kind === "gate" ? "rgba(213,106,86,0.7)" : "rgba(226,195,106,0.75)";
        ctx.lineWidth = on ? 2 : compact ? 1 : 1.2;
        ctx.stroke();
      } else if (n.a.side === "unk") {
        const u = compact ? 5 : 9;
        ctx.moveTo(n.x, n.y - u);
        ctx.lineTo(n.x + u * 0.78, n.y);
        ctx.lineTo(n.x, n.y + u);
        ctx.lineTo(n.x - u * 0.78, n.y);
        ctx.closePath();
        ctx.fillStyle = "#e2c36a";
        ctx.fill();
      } else {
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = n.a.side === "blue" ? (n.a.layer === "hi" ? "#f3ebd4" : "#6fb4ae") : "#d56a56";
        ctx.fill();
        if (on) {
          ctx.strokeStyle = "#e2c36a";
          ctx.lineWidth = compact ? 1.4 : 2;
          ctx.stroke();
        }
        const rad = ((n.a.heading || 0) * Math.PI) / 180;
        const nose = compact ? 9 : 15;
        const tail = compact ? 4 : 7;
        ctx.beginPath();
        ctx.moveTo(n.x + Math.cos(rad) * nose, n.y + Math.sin(rad) * (compact ? 4 : 6));
        ctx.lineTo(n.x + Math.cos(rad) * tail, n.y + Math.sin(rad) * (compact ? 1.8 : 3));
        ctx.strokeStyle = "rgba(236,230,212,0.85)";
        ctx.lineWidth = compact ? 1 : 1.4;
        ctx.stroke();
      }
      ctx.fillStyle = "#ece6d4";
      ctx.font = `${fs}px Segoe UI, PingFang SC, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(n.label, n.x, n.y + (n.type === "site" ? n.r + (compact ? 2 : 4) : compact ? 7 : 10));
    });

    ctx.fillStyle = state.jammed ? "#d56a56" : "rgba(226,195,106,0.85)";
    ctx.font = `${compact ? 9 : 12}px Segoe UI, PingFang SC, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    if (!compact) {
      ctx.fillText(state.jammed ? "G(t) 断 · 队影冻" : "G(t) 通 · 队影合成", (c2.x + radar.x) / 2, H * 0.11 - 18);
    }

    hits = nodes;
  }

  function hitAt(ev) {
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    let best = null;
    let bestD = compact ? 18 : 26;
    hits.forEach((n) => {
      const d = Math.hypot(n.x - x, n.y - y);
      if (d < n.r + (compact ? 8 : 10) && d < bestD) {
        best = n;
        bestD = d;
      }
    });
    return best;
  }

  return { draw, hitAt };
}
