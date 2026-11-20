/** Plan-view pictograms: filled teal family. Side views stay profiles. Labels stay outside. */

const TEAL = "#4eb6c8";

export function headingOf(agent) {
  if (agent.heading != null) return agent.heading;
  if (agent.v && (agent.v[0] || agent.v[2])) {
    return (Math.atan2(agent.v[0], agent.v[2]) * 180) / Math.PI;
  }
  return 0;
}

export function inkOf(agent) {
  if (agent.side === "blue") return TEAL;
  if (agent.side === "unk") return "#e2c36a";
  return "#ff6b5a";
}

function pic(ctx, x, y, paint) {
  ctx.save();
  ctx.translate(x, y);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  paint(ctx);
  ctx.restore();
}

/** Friend = filled UAV. Hostile = filled delta. Nose follows heading. */
export function drawUAV(ctx, x, y, hostile, heading = 0) {
  const ink = hostile ? "#ff6b5a" : TEAL;
  pic(ctx, x, y, () => {
    ctx.rotate((heading * Math.PI) / 180);
    ctx.fillStyle = ink;
    if (hostile) {
      ctx.beginPath();
      ctx.moveTo(0, -8);
      ctx.lineTo(5.2, 6.5);
      ctx.lineTo(0, 3.2);
      ctx.lineTo(-5.2, 6.5);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(-12, 1.2);
      ctx.lineTo(-1, -0.4);
      ctx.lineTo(1, -0.4);
      ctx.lineTo(12, 1.2);
      ctx.lineTo(11.2, 3.1);
      ctx.lineTo(1, 1.6);
      ctx.lineTo(-1, 1.6);
      ctx.lineTo(-11.2, 3.1);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(0, -9);
      ctx.lineTo(2.1, 6);
      ctx.lineTo(0, 8.5);
      ctx.lineTo(-2.1, 6);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(-0.4, 5.2);
      ctx.lineTo(-5.4, 9.2);
      ctx.lineTo(0, 7);
      ctx.lineTo(5.4, 9.2);
      ctx.lineTo(0.4, 5.2);
      ctx.closePath();
      ctx.fill();
    }
  });
}

/** Top-down hull. Bow follows heading. 000° inland. */
export function drawShip(ctx, x, y, heading = 90) {
  pic(ctx, x, y, () => {
    ctx.rotate(((heading - 90) * Math.PI) / 180);
    ctx.fillStyle = TEAL;
    ctx.beginPath();
    ctx.moveTo(16, 0);
    ctx.lineTo(10, -4.2);
    ctx.lineTo(-8, -5);
    ctx.lineTo(-14, -3.2);
    ctx.lineTo(-14, 3.2);
    ctx.lineTo(-8, 5);
    ctx.lineTo(10, 4.2);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#1a3d45";
    ctx.beginPath();
    ctx.moveTo(3, -2.4);
    ctx.lineTo(3, 2.4);
    ctx.lineTo(-6, 2.4);
    ctx.lineTo(-6, -2.4);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#ff6b5a";
    ctx.fillRect(13, -1, 2, 2);
  });
}

/** Launcher from above. Not a radar. */
export function drawSam(ctx, x, y) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = TEAL;
    ctx.fillRect(-8, -4, 16, 8);
    ctx.fillStyle = "#1a3d45";
    ctx.fillRect(-6, -2.4, 12, 4.8);
    ctx.fillStyle = TEAL;
    ctx.beginPath();
    ctx.moveTo(-5, -2.4);
    ctx.lineTo(-2, -8);
    ctx.lineTo(8, -8);
    ctx.lineTo(6, -2.4);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "#1a3d45";
    ctx.lineWidth = 1;
    for (let i = 0; i < 4; i += 1) {
      const u = -3.5 + i * 2.4;
      ctx.beginPath();
      ctx.moveTo(u, -2.2);
      ctx.lineTo(u + 2.2, -7.4);
      ctx.stroke();
    }
  });
}

/** Silo hatch in plan. Not a side-view tower. */
export function drawBmd(ctx, x, y) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = TEAL;
    ctx.beginPath();
    ctx.arc(0, 0, 8.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#1a3d45";
    ctx.beginPath();
    ctx.arc(0, 0, 5.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = TEAL;
    ctx.beginPath();
    ctx.arc(0, 0, 2.2, 0, Math.PI * 2);
    ctx.fill();
  });
}

/** Command post: flag, not a house outline. */
export function drawC2(ctx, x, y) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = TEAL;
    ctx.fillRect(-1.2, -11, 2.2, 18);
    ctx.beginPath();
    ctx.moveTo(1, -11);
    ctx.lineTo(12, -7);
    ctx.lineTo(1, -3);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(-4, 7);
    ctx.lineTo(4, 7);
    ctx.lineTo(3, 9);
    ctx.lineTo(-3, 9);
    ctx.closePath();
    ctx.fill();
  });
}

/** Fire-control dish. */
export function drawRadar(ctx, x, y) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = TEAL;
    ctx.fillRect(-3, 1, 6, 5);
    ctx.fillRect(-1.2, -4, 2.4, 6);
    ctx.beginPath();
    ctx.ellipse(0, -6, 8, 5.2, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#1a3d45";
    ctx.beginPath();
    ctx.ellipse(0, -6, 3.6, 2.4, 0, 0, Math.PI * 2);
    ctx.fill();
  });
}

/** 2D search antenna from above: pad + rotating bar. Not a dish. */
export function drawSearchRadar(ctx, x, y) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = TEAL;
    ctx.beginPath();
    ctx.arc(0, 2, 6.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#1a3d45";
    ctx.beginPath();
    ctx.arc(0, 2, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = TEAL;
    ctx.fillRect(-9, -3.2, 18, 4.4);
  });
}

/** Elevation: dish on a mast, sitting on the waterline/ground. */
export function drawRadarSide(ctx, x, yGround) {
  pic(ctx, x, yGround, () => {
    ctx.fillStyle = TEAL;
    ctx.fillRect(-5, -4, 10, 4);
    ctx.fillRect(-1.2, -16, 2.4, 12);
    ctx.beginPath();
    ctx.ellipse(-1, -16, 7, 5, -0.35, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#1a3d45";
    ctx.beginPath();
    ctx.ellipse(-1, -16, 3.2, 2.2, -0.35, 0, Math.PI * 2);
    ctx.fill();
  });
}

export function drawTrack(ctx, x, y, agent) {
  if (agent.side === "unk") {
    drawUnk(ctx, x, y, headingOf(agent));
    return;
  }
  drawUAV(ctx, x, y, agent.side === "red", headingOf(agent));
}

export function drawUnk(ctx, x, y, heading = 0) {
  pic(ctx, x, y, () => {
    ctx.fillStyle = "#e2c36a";
    [[0, -4.4], [4.4, 0], [-4.4, 0], [0, 4.4]].forEach(([px, py], i) => {
      ctx.beginPath();
      if (i === 3) ctx.arc(px, py, 3.2, Math.PI * 1.12, Math.PI * 1.88);
      else ctx.arc(px, py, 3.2, 0, Math.PI * 2);
      ctx.fill();
    });
  });
  ctx.save();
  ctx.strokeStyle = "#e2c36a";
  ctx.lineWidth = 1.15;
  const r = (heading * Math.PI) / 180;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + Math.sin(r) * 12, y - Math.cos(r) * 12);
  ctx.stroke();
  ctx.restore();
}

/** Radio mast, not a warning triangle. */
export function drawJammer(ctx, x, y, on = false) {
  const ink = on ? "#ff6b5a" : TEAL;
  pic(ctx, x, y, () => {
    ctx.fillStyle = ink;
    ctx.beginPath();
    ctx.moveTo(-2.2, 8);
    ctx.lineTo(2.2, 8);
    ctx.lineTo(1.2, -4);
    ctx.lineTo(-1.2, -4);
    ctx.closePath();
    ctx.fill();
    ctx.fillRect(-1, -11, 2, 8);
    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.35;
    ctx.beginPath();
    ctx.arc(0, -10, 5, -0.9, 0.9);
    ctx.stroke();
    if (on) {
      ctx.beginPath();
      ctx.arc(0, -10, 8, -0.75, 0.75);
      ctx.stroke();
    }
  });
}

/** Side elevation: fuselage along the inbound axis. Nose follows +Z (land, right) or −Z (sea, left). */
export function drawUAVSide(ctx, x, y, hostile, towardLand = true, inkOverride) {
  const ink = inkOverride || (hostile ? "#ff6b5a" : TEAL);
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(towardLand ? 1 : -1, 1);
  ctx.fillStyle = ink;
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(-10, 1.2);
  ctx.lineTo(-8, 0);
  ctx.lineTo(8, 0);
  ctx.lineTo(12, -2.2);
  ctx.lineTo(8, 2);
  ctx.lineTo(-8, 2);
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(-8, 0);
  ctx.lineTo(-9.5, -6);
  ctx.lineTo(-4.5, 0);
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(-1, 1.5);
  ctx.lineTo(-2, 5.5);
  ctx.lineTo(3.2, 5.5);
  ctx.lineTo(2.2, 1.5);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** Ship on the waterline, bow to seaward (left). Not the plan-view hull. */
export function drawShipSide(ctx, x, yWater) {
  ctx.save();
  ctx.translate(x, yWater);
  ctx.fillStyle = TEAL;
  ctx.beginPath();
  ctx.moveTo(-20, 0);
  ctx.lineTo(-12, -6);
  ctx.lineTo(11, -6);
  ctx.lineTo(18, 0);
  ctx.lineTo(12, 4);
  ctx.lineTo(-12, 4);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#1a3d45";
  ctx.fillRect(-4, -12, 10, 6);
  ctx.fillRect(2, -17, 3, 5);
  ctx.fillStyle = TEAL;
  ctx.fillRect(2.2, -22, 2.2, 5);
  ctx.fillStyle = "#ff6b5a";
  ctx.fillRect(-16, -4, 2, 2);
  ctx.restore();
}

export function drawTrackCut(ctx, x, y, agent) {
  const h = headingOf(agent) * (Math.PI / 180);
  const towardLand = Math.cos(h) >= 0;
  if (agent.side === "unk") {
    drawUAVSide(ctx, x, y, false, towardLand, "#e2c36a");
    return;
  }
  drawUAVSide(ctx, x, y, agent.side === "red", towardLand);
}

export function drawKey(ctx, x, y, jammed) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(0.68, 0.68);
  const w = 176;
  const h = 148;
  ctx.fillStyle = "rgba(11, 14, 20, 0.82)";
  ctx.strokeStyle = "#2a2e38";
  ctx.lineWidth = 1.2;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeRect(0, 0, w, h);
  ctx.font = "9px Segoe UI, PingFang SC, sans-serif";
  ctx.fillStyle = "#8d8878";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("图例", 8, 10);

  const row = 26;
  drawUAV(ctx, 14, row, true, 20);
  ctx.fillStyle = "#c8c2b4";
  ctx.fillText("敌无人机", 26, row);
  drawUAV(ctx, 94, row, false, 20);
  ctx.fillText("己无人机", 106, row);

  drawShip(ctx, 20, row + 18, 90);
  ctx.fillText("军舰", 36, row + 18);
  drawRadar(ctx, 94, row + 18);
  ctx.fillText("雷达站", 106, row + 18);

  drawSam(ctx, 14, row + 38);
  ctx.fillStyle = "#c8c2b4";
  ctx.fillText("SAM发射", 26, row + 38);
  drawBmd(ctx, 100, row + 38);
  ctx.fillStyle = "#c8c2b4";
  ctx.fillText("BMD阵地", 114, row + 38);

  drawUnk(ctx, 14, row + 56, 20);
  ctx.fillText("不明", 26, row + 56);
  drawJammer(ctx, 94, row + 56, true);
  ctx.fillText("干扰源", 106, row + 56);

  const links = [
    { dash: [], color: jammed ? "rgba(136,68,56,0.55)" : "rgba(122,210,232,0.9)", name: jammed ? "机站 断" : "机站", thin: true },
    { dash: [2, 3], color: "rgba(122,210,232,0.7)", name: "机间·不断", thin: true },
  ];
  links.forEach((r, i) => {
    const yy = row + 72 + i * 12;
    ctx.strokeStyle = r.color;
    ctx.lineWidth = r.thin ? 1 : 1.4;
    ctx.setLineDash(r.dash);
    ctx.beginPath();
    ctx.moveTo(10, yy);
    ctx.lineTo(36, yy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#c8c2b4";
    ctx.fillText(r.name, 42, yy);
  });
  ctx.fillStyle = "#8d8878";
  ctx.font = "8px Segoe UI, PingFang SC, sans-serif";
  ctx.fillText("队级融合：看航迹来源", 8, row + 100);
  ctx.fillText("波纹=扫描示意 · 干扰圈=噪声示意", 8, h - 8);
  ctx.restore();
}
