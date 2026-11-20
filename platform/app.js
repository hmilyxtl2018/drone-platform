/** Retired. swarm.html now loads swarm.js + battle.js. */
import { SwarmSim, SCENARIOS } from "./engine.js";
import { bindOrbit, cam, drawSpark, drawWorld } from "./view.js";

const world = document.getElementById("world");
const spark = document.getElementById("spark");
const caption = document.getElementById("caption");

const ui = {
  scenario: document.getElementById("scenario"),
  policy: document.getElementById("policy"),
  play: document.getElementById("play"),
  step: document.getElementById("step"),
  reset: document.getElementById("reset"),
  speed: document.getElementById("speed"),
};

let sim = new SwarmSim("dynamic", "form_greedy", 11);
let playing = false;
let acc = 0;
let last = performance.now();
let interval = 400;
let holdUntil = 0;

function render() {
  const snap = sim.snapshot();
  drawWorld(world, snap);
  drawSpark(spark, snap.covHist, snap.formHist);
  caption.textContent = snap.lastEffect;
  document.getElementById("v-n").textContent = `${snap.nAlive}/${snap.n}`;
  document.getElementById("v-e").textContent = snap.form.error.toFixed(2);
  document.getElementById("v-c").textContent = snap.coverage.toFixed(2);
  document.getElementById("v-z").textContent = snap.layers.join("·");
  document.getElementById("v-t").textContent = `${snap.t}/${snap.scenario.horizon}`;
}

function setPlaying(on) {
  playing = on;
  ui.play.textContent = playing ? "止" : "演";
  if (playing) cam.auto = true;
}

function reset() {
  sim = new SwarmSim(ui.scenario.value, ui.policy.value, 11);
  acc = 0;
  holdUntil = 0;
  cam.yaw = 0.72;
  cam.pitch = 0.48;
  cam.dist = 9.6;
  setPlaying(false);
  render();
}

ui.play.addEventListener("click", () => setPlaying(!playing));
ui.step.addEventListener("click", () => {
  setPlaying(false);
  sim.step();
  render();
});
ui.reset.addEventListener("click", reset);
ui.scenario.addEventListener("change", reset);
ui.policy.addEventListener("change", reset);
ui.speed.addEventListener("input", () => {
  interval = Number(ui.speed.value);
});

document.querySelectorAll("[data-inject]").forEach((btn) => {
  btn.addEventListener("click", () => {
    sim.inject(btn.dataset.inject, "手动");
    holdUntil = performance.now() + 1400;
    render();
  });
});

function loop(now) {
  const dt = now - last;
  last = now;
  if (playing && now >= holdUntil) {
    acc += dt;
    while (acc >= interval) {
      acc -= interval;
      if (sim.t >= sim.scenario.horizon) {
        setPlaying(false);
        sim.lastEffect = "构型演化结束。换策略或扰动，看同一球面码是否仍收敛。";
        break;
      }
      const before = sim.events.length;
      sim.step();
      if (sim.flash || sim.events.length > before) {
        holdUntil = now + 1200;
        acc = 0;
        break;
      }
    }
  }
  render();
  requestAnimationFrame(loop);
}

ui.scenario.innerHTML = Object.values(SCENARIOS)
  .map((s) => `<option value="${s.id}">${s.name}</option>`)
  .join("");
bindOrbit(world);
render();
requestAnimationFrame(loop);
