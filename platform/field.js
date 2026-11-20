/** Compose page: same GIS as 岸海态势, same dock as 交战本体. */

import { battle, state, P, onAfterRender } from "./space.js?v=39";
import { C2, RADAR, living } from "./battle.js?v=25";
import { createBodyPanel } from "./body-panel.js?v=9";
import { createCollabView } from "./collab-map.js?v=2";

const world = document.getElementById("world");
const panel = createBodyPanel(battle, {
  bodyEl: document.getElementById("body"),
  detailEl: document.getElementById("detail"),
});
const eye = createCollabView(document.getElementById("eye"), {
  battle,
  compact: true,
  allLive: () => panel.allLive(),
  getSelected: () => panel.getSelected(),
});

panel.bind();

function openHash() {
  const id = (location.hash || "").replace("#", "");
  const kind = { see: "gold", form: "form", assign: "pair", gate: "gate" }[id];
  panel.showKind(kind || "gold");
}

openHash();
window.addEventListener("hashchange", openHash);
onAfterRender(() => {
  panel.paint();
  eye.draw();
  const lab = document.getElementById("eye-lab");
  if (lab) lab.textContent = state.jammed ? "G 断" : "G 通";
});

document.getElementById("reset")?.addEventListener("click", () => {
  panel.showKind("gold");
});

function pickAt(ev) {
  if (!world || !state) return null;
  const rect = world.getBoundingClientRect();
  const x = ev.clientX - rect.left;
  const y = ev.clientY - rect.top;
  let best = null;
  let bestD = 24;
  const live = [...living(state.hi), ...living(state.mid), ...living(state.lo), ...living(state.unk || [])];
  live.forEach((a) => {
    let s;
    try {
      s = P(a.p);
    } catch {
      return;
    }
    if (s[2] < 0.35) return;
    const d = Math.hypot(s[0] - x, s[1] - y);
    const hit = Math.max(16, (s[3] || 12) * 0.4);
    if (d < hit && d < bestD) {
      best = { type: "agent", a };
      bestD = d;
    }
  });
  [
    { kind: "gold", p: C2 },
    { kind: "gold", p: RADAR.shore },
  ].forEach((site) => {
    let q;
    try {
      q = P(site.p);
    } catch {
      return;
    }
    const d = Math.hypot(q[0] - x, q[1] - y);
    if (d < 20 && d < bestD) {
      best = { type: "kind", kind: site.kind };
      bestD = d;
    }
  });
  return best;
}

function applyPick(n) {
  if (!n) return;
  if (n.type === "agent") panel.showAgent(n.a);
  else panel.showKind(n.kind);
}

world?.addEventListener("click", (e) => applyPick(pickAt(e)));
document.getElementById("eye")?.addEventListener("click", (e) => applyPick(eye.hitAt(e)));
window.addEventListener("resize", () => eye.draw());

const dock = document.querySelector(".dock");
const drawer = document.querySelector(".drawer");
drawer?.addEventListener("click", () => {
  const shut = dock.classList.toggle("shut");
  document.body.classList.toggle("dock-shut", shut);
  drawer.textContent = shut ? "<" : ">";
  drawer.setAttribute("aria-label", shut ? "展开" : "收起");
  drawer.setAttribute("aria-expanded", shut ? "false" : "true");
  if (!shut) {
    dock.addEventListener("transitionend", () => eye.draw(), { once: true });
    eye.draw();
  }
});
