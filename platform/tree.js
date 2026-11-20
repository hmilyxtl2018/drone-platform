/** Combat-agent shells around a live engagement body. Algorithms stay folded. */

import { TICK_S, createBattle } from "./battle.js?v=25";
import { createBodyPanel } from "./body-panel.js?v=9";
import { createCollabView } from "./collab-map.js?v=2";

const battle = createBattle();
const { state } = battle;

const canvas = document.getElementById("collab");
const soEl = document.getElementById("so-what");
const playBtn = document.getElementById("play");

const panel = createBodyPanel(battle, {
  bodyEl: document.getElementById("body"),
  detailEl: document.getElementById("detail"),
});

const collab = createCollabView(canvas, {
  battle,
  compact: false,
  allLive: () => panel.allLive(),
  getSelected: () => panel.getSelected(),
});

let playing = false;
let acc = 0;
let last = performance.now();

function paintLive() {
  const sw = battle.soWhat();
  if (soEl) soEl.textContent = sw.text;
  collab.draw();
  panel.paint();
}

function openHash() {
  const id = (location.hash || "").replace("#", "");
  const kind = Object.entries({ gold: "see", form: "form", pair: "assign", gate: "gate" }).find(([, rid]) => rid === id);
  panel.showKind(kind ? kind[0] : "gold");
}

function setPlaying(on) {
  playing = on;
  if (playBtn) playBtn.textContent = playing ? "止" : "演";
  if (playing && !state.k) battle.nextWave();
}

if (canvas) {
  canvas.addEventListener("click", (e) => {
    const n = collab.hitAt(e);
    if (!n) return;
    if (n.type === "agent") panel.showAgent(n.a);
    else panel.showKind(n.kind);
  });
}

panel.bind();

if (playBtn) playBtn.addEventListener("click", () => setPlaying(!playing));
document.getElementById("wave")?.addEventListener("click", () => battle.nextWave());
document.getElementById("reset")?.addEventListener("click", () => {
  battle.reset();
  battle.nextWave();
  setPlaying(false);
  panel.showKind("gold");
});
document.querySelectorAll("[data-inject]").forEach((btn) => {
  btn.addEventListener("click", () => battle.inject(btn.dataset.inject));
});

function loop(now) {
  const dt = now - last;
  last = now;
  if (playing) {
    acc += dt;
    while (acc >= 40) {
      acc -= 40;
      battle.step(TICK_S);
    }
  }
  battle.tickKb();
  paintLive();
  requestAnimationFrame(loop);
}

battle.nextWave();
openHash();
window.addEventListener("hashchange", openHash);
window.addEventListener("resize", () => collab.draw());
requestAnimationFrame(loop);
