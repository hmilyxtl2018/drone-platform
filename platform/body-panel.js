/** Shared 本波影 / 选中 dock for 交战本体 and 合成. */

import { TAU, N_MAX, living, zoneOf, intelOf, INTEL_FILES, trackSheet } from "./battle.js?v=25";
import { PAGES, META, TREE } from "./tree-data.js?v=6";

export { PAGES, META, TREE };

export const KIND_META = {
  gold: "see",
  form: "form",
  pair: "assign",
  gate: "gate",
};

const ZONE_LAB = { in: "区内", edge: "区沿", ship: "舰探", cor: "走廊", out: "区外" };

export function createBodyPanel(battle, els) {
  const { state, kb } = battle;
  const bodyEl = els.bodyEl;
  const detailEl = els.detailEl;
  let selected = { type: "kind", id: "gold" };
  let lastBody = "";

  function allLive() {
    return [...living(state.hi), ...living(state.mid), ...living(state.lo), ...living(state.unk || [])];
  }

  function findA(id) {
    return allLive().find((a) => a.id === id);
  }

  function fieldHtml(field) {
    if (!field) return "";
    const pg = PAGES[field.page];
    if (!pg) return "";
    const click = field.click ? ` · 点「${field.click}」` : "";
    return `<p class="field">本场 <a href="${pg.href}">${pg.name}</a>${click}</p>`;
  }

  function toolsHtml(roleId) {
    const branch = TREE.find((b) => b.id === roleId);
    if (!branch) return "";
    const items = branch.algos
      .map((a) => `<li><button class="leaf" type="button" data-role="${roleId}" data-name="${a.name}">${a.name}</button></li>`)
      .join("");
    return `<details class="tools"><summary>这类写入可用的工具</summary><ul>${items}</ul></details>`;
  }

  function pairTalk() {
    return (state.pairs || [])
      .map((p) => {
        const b = findA(p.blue);
        const r = findA(p.red);
        if (!b || !r) return null;
        return `${b.tn} ${b.task || "盯梢"} ${r.tn}`;
      })
      .filter(Boolean);
  }

  function permitOf(a) {
    if (a.side === "blue") return null;
    if (a.side === "unk") return { ok: false, why: "不明不进清单" };
    if (a.gated) return { ok: true, why: "已过门，占通道" };
    const z = zoneOf(a.p);
    if (state.jammed && (z === "in" || z === "edge")) return { ok: false, why: "金线断，队级 p 不能授权" };
    if (state.mSam <= 0 && z === "in") return { ok: false, why: "饱和不是开门理由" };
    if (z !== "in") return { ok: false, why: z === "edge" ? "区沿只跟踪" : "区外不打" };
    if (a.belief < TAU) return { ok: false, why: `p ${a.belief.toFixed(2)} < τ` };
    return { ok: false, why: "待许可" };
  }

  function permitShort(pmt) {
    if (!pmt) return "—";
    if (pmt.ok) return "过门";
    if (pmt.why.includes("不明")) return "拒·不明";
    if (pmt.why.includes("饱和")) return "拒·饱和";
    if (pmt.why.includes("金线")) return "拒·断链";
    if (pmt.why.includes("区沿")) return "拒·区沿";
    if (pmt.why.includes("区外")) return "拒·区外";
    if (pmt.why.includes("p")) return "拒·p";
    return "拒";
  }

  function rejects() {
    const out = [];
    if (state.jammed) out.push("队影合成 · 金线断");
    if (state.mSam <= 0) out.push("饱和 · 不开门");
    living(state.unk || []).forEach((a) => out.push(`${a.tn} · 不明不进清单`));
    living(state.lo).forEach((a) => {
      const p = permitOf(a);
      if (!p || p.ok) return;
      const z = zoneOf(a.p);
      if (z === "in" || z === "edge") out.push(`${a.tn} · ${permitShort(p)}`);
    });
    return out.slice(0, 5);
  }

  function denyHtml() {
    const d = rejects();
    if (!d.length) return `<p class="now-ok">本拍拒绝 · 无</p>`;
    return `<p class="now-deny">本拍拒绝</p><ul class="rejects">${d.map((t) => `<li>${t}</li>`).join("")}</ul>`;
  }

  function markLegend(kind) {
    document.querySelectorAll(".legend button").forEach((b) => b.classList.toggle("on", b.dataset.kind === kind));
  }

  function showKind(kind) {
    selected = { type: "kind", id: kind };
    const roleId = KIND_META[kind];
    const r = { ...TREE.find((b) => b.id === roleId), ...META[roleId] };
    const titles = {
      gold: "金线 · 队影合成",
      form: "编队 · 同层阵位",
      pair: "盯梢 · 谁盯谁",
      gate: "授权 · 过门许可",
    };
    const talks = pairTalk();
    const now = {
      gold: state.jammed ? "断 · 队影冻，只用局影，不能授权" : "通 · 岸搜与各机合成队影",
      form: `高 ε ${(state.epsHi || 0).toFixed(2)} · 中 ${(state.epsMid || 0).toFixed(2)} · 低 ${(state.epsLo || 0).toFixed(2)} · 一层一套码`,
      pair: talks.length ? talks.join("；") : "尚无盯梢 · 点演后中空有限匹配",
      gate: `已许可 ${state.gated} · m=${state.mSam} · 区内且 p≥${TAU} 才过门`,
    };
    if (detailEl) {
      detailEl.innerHTML = `
        <p class="where">本体 · ${r.role}</p>
        <h3>${titles[kind]}</h3>
        <div class="bdi">
          <i>现在</i><b>${now[kind]}</b>
          <i>吃 / 交</i><b>${r.eat} → ${r.give}</b>
          <i>注入</i><b>${r.cut}</b>
        </div>
        ${fieldHtml(r.field)}
        ${denyHtml()}
        ${toolsHtml(roleId)}
      `;
    }
    history.replaceState(null, "", `#${roleId}`);
    markLegend(kind);
    setDetailSide(null);
  }

  function showAgent(a) {
    selected = { type: "agent", id: a.id };
    const intel = intelOf(a, state.jammed, state.pairs || []);
    const z = zoneOf(a.p);
    const layer = a.layer === "hi" ? "高空" : a.layer === "mid" ? "中空" : "低空走廊";
    const mates = allLive().filter((b) => b.layer === a.layer && b.side === a.side && b.id !== a.id);
    let roleId = "form";
    let belief;
    let goal;
    let intent;
    let know;
    let able;
    if (a.side === "blue" && a.quarryTn) {
      roleId = "assign";
      belief = `${intel.src} · 目标 ${a.quarryTn} 身份已分。金线${state.jammed ? "断，依据是局影" : "通，依据是队影"}。`;
      goal = a.task || "盯梢";
      intent = `写入盯梢 ${a.tn}→${a.quarryTn}，并飞向中空阵位。`;
      know = `读 ${a.quarryTn} 的航迹 / 身份；读通道 m=${state.mSam}。`;
      able = "中空可盯梢或识别，不跨层混码，不自己开门。";
    } else if (a.side === "blue") {
      roleId = a.layer === "hi" ? "see" : "form";
      belief = `${intel.src} · ${intel.intent}。金线${state.jammed ? "断" : "通"}。`;
      goal = a.task || "巡逻";
      intent = `写入本层阵位，与 ${(mates.map((m) => m.tn).join("、") || "同层")} 收编。`;
      know = a.layer === "hi" ? "读 SEARCH / B_BMD 覆盖，断言警戒航迹。" : "读本层阵位与 ε。";
      able = a.layer === "hi" ? "高空警戒，不进走廊。" : "中空编队，N≤3。";
    } else if (a.side === "unk") {
      roleId = "read";
      belief = `${intel.src} · p ${a.belief.toFixed(2)} · 身份未判明。`;
      goal = "等待识别";
      intent = "不写入交战清单，不请求过门。";
      know = "身份保持不明（存异优先于一致）。";
      able = "可被识别，不可被打。";
    } else {
      const pmt = permitOf(a);
      const watch = (state.pairs || [])
        .filter((p) => p.red === a.id)
        .map((p) => findA(p.blue)?.tn)
        .filter(Boolean);
      roleId = a.gated || z === "in" ? "gate" : "form";
      belief = `${intel.src} · ${ZONE_LAB[z] || z} · p ${a.belief.toFixed(2)}。`;
      goal = intel.intent;
      intent = pmt?.ok ? "许可已写入，占用通道。" : `请求过门被拒：${pmt?.why || "不打"}。`;
      know = `${watch.length ? `被 ${watch.join("、")} 盯梢。` : "尚无盯梢。"} 与 ${(mates.map((m) => m.tn).join("、") || "同波")} 走同一套低空码。`;
      able = "轨迹必须留在走廊；过门才占通道。";
    }
    if (detailEl) {
      detailEl.innerHTML = `
        <p class="where">作战 Agent · ${intel.iden} · ${layer}</p>
        <h3>${a.tn}${a.task ? " · " + a.task : ""}${a.quarryTn ? " " + a.quarryTn : ""}</h3>
        <div class="bdi">
          <i>信念</i><b>${belief}</b>
          <i>目标</i><b>${goal}</b>
          <i>意图</i><b>${intent}</b>
          <i>知识</i><b>${know}${able ? " · " + able : ""}</b>
        </div>
        ${fieldHtml(META[roleId].field)}
        ${denyHtml()}
        ${toolsHtml(roleId)}
      `;
    }
    markLegend(null);
    setDetailSide(sideClass(a));
  }

  function sideClass(a) {
    if (a.side === "blue") return "side-own";
    if (a.side === "unk") return "side-unk";
    return "side-foe";
  }

  function setDetailSide(side) {
    if (!detailEl) return;
    detailEl.classList.remove("side-own", "side-foe", "side-unk");
    if (side) detailEl.classList.add(side);
  }

  function rowOn(id) {
    return selected.type === "agent" && selected.id === id ? "on" : "";
  }

  function bodyHTML() {
    const rd = kb.readout();
    const nLo = living(state.lo).length;
    const lamps = [
      ["N", nLo <= N_MAX.lo && kb.rules.nOk, `${nLo}/${N_MAX.lo}`],
      ["廊", kb.rules.corOk, kb.rules.corOk ? "在廊" : "出廊"],
      ["门", kb.rules.gateOk, `m ${state.mSam}`],
      ["金线", !state.jammed, state.jammed ? "断" : "通"],
    ]
      .map(([k, ok, v]) => `<span class="${ok ? "ok" : "bad"}">${k} ${v}</span>`)
      .join("");

    const tracks = [...living(state.lo), ...living(state.unk || [])]
      .map((a) => {
        const intel = intelOf(a, state.jammed, state.pairs || []);
        const pmt = permitOf(a);
        const z = ZONE_LAB[zoneOf(a.p)] || zoneOf(a.p);
        const st = `${intel.iden} · ${z} · ${a.belief.toFixed(2)}`;
        const wr = permitShort(pmt);
        return `<tr class="${sideClass(a)} ${rowOn(a.id)}" data-pick="agent:${a.id}" title="${intel.src}">
        <td>${a.tn}</td>
        <td>${st}</td>
        <td class="${pmt?.ok ? "ok" : "deny"}">${wr}</td>
      </tr>`;
      })
      .join("");

    const intents = living(state.hi)
      .concat(living(state.mid))
      .map((a) => {
        const st = a.quarryTn ? `${a.task || "盯梢"} ${a.quarryTn}` : a.task || "巡逻";
        const wr = a.quarryTn ? "盯梢" : "阵位";
        return `<tr class="${sideClass(a)} ${rowOn(a.id)}" data-pick="agent:${a.id}">
        <td>${a.tn}</td>
        <td>${st}</td>
        <td class="ok">${wr}</td>
      </tr>`;
      })
      .join("");

    return `
    <p class="lamps">${lamps}</p>
    <p class="shadows">
      <span>队 ${rd.team}</span>
      <span>局 ${rd.local}</span>
    </p>
    <table>
      <thead><tr><th>批</th><th>状态</th><th>写入</th></tr></thead>
      <tbody>
        ${tracks || `<tr><td colspan="3">走廊空 · 尚无航迹</td></tr>`}
        ${intents}
      </tbody>
    </table>
  `;
  }

  function renderBody() {
    if (!bodyEl) return;
    const html = bodyHTML();
    if (html === lastBody) return;
    lastBody = html;
    bodyEl.innerHTML = html;
  }

  function specHtml(a) {
    const sheet = trackSheet(a, state);
    const rec = sheet.file ? INTEL_FILES[sheet.file] : null;
    if (!rec) return "";
    const combat = (rec.combat || []).map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
    return `
      <figure>
        <img src="${rec.img}" alt="${rec.klass}" />
        <figcaption><b>${rec.code} · ${rec.klass}</b><i>${a.tn} · 岸搜光学跟踪 · 类型参考</i></figcaption>
      </figure>
      <dl class="spec">${combat}</dl>
      <dl class="spec live">
        <div><dt>本批速</dt><dd>${Math.round(sheet.kmh)} km/h</dd></div>
        <div><dt>高度</dt><dd>${sheet.altM} m</dd></div>
        <div><dt>p</dt><dd>${sheet.p.toFixed(2)}</dd></div>
        <div><dt>企图</dt><dd>${sheet.intent}</dd></div>
      </dl>
      <p class="intel-note">${rec.note}</p>
    `;
  }

  function ensurePop() {
    let pop = document.getElementById("spec-pop");
    if (pop) return pop;
    pop = document.createElement("aside");
    pop.id = "spec-pop";
    pop.className = "spec-pop";
    pop.hidden = true;
    pop.setAttribute("aria-hidden", "true");
    document.body.appendChild(pop);
    return pop;
  }

  function placePop(anchor) {
    const pop = ensurePop();
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth || 280;
    const h = pop.offsetHeight || 300;
    let left = r.left - w - 12;
    if (left < 8) left = Math.min(window.innerWidth - w - 8, r.right + 12);
    let top = r.top;
    if (top + h > window.innerHeight - 56) top = Math.max(8, window.innerHeight - 56 - h);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  let hoverId = null;
  let lastSpecSig = "";

  function hideSpec() {
    hoverId = null;
    lastSpecSig = "";
    const pop = document.getElementById("spec-pop");
    if (!pop) return;
    pop.hidden = true;
    pop.setAttribute("aria-hidden", "true");
  }

  function showSpec(a, anchor) {
    if (!a || a.side !== "red") {
      hideSpec();
      return;
    }
    hoverId = a.id;
    const pop = ensurePop();
    const html = specHtml(a);
    if (html !== lastSpecSig) {
      lastSpecSig = html;
      pop.innerHTML = html;
    }
    pop.hidden = false;
    pop.setAttribute("aria-hidden", "false");
    placePop(anchor);
  }

  function refreshSpec() {
    if (!hoverId) return;
    const a = findA(hoverId);
    const tr = bodyEl?.querySelector(`[data-pick="agent:${hoverId}"]`);
    if (!a || a.side !== "red" || !tr) {
      hideSpec();
      return;
    }
    showSpec(a, tr);
  }

  function paint() {
    const waveLab = document.getElementById("wave-lab");
    if (waveLab) waveLab.textContent = `k ${state.k}`;
    renderBody();
    if (selected.type === "agent" && !findA(selected.id)) showKind("gold");
    refreshSpec();
  }

  function bind() {
    bodyEl?.addEventListener("click", (e) => {
      const tr = e.target.closest("[data-pick]");
      if (!tr) return;
      const [kind, id] = tr.dataset.pick.split(":");
      if (kind === "agent") {
        const a = findA(id);
        if (a) showAgent(a);
      } else showKind(id);
    });
    document.getElementById("legend")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-kind]");
      if (btn) showKind(btn.dataset.kind);
    });
    detailEl?.addEventListener("click", (e) => {
      const btn = e.target.closest(".leaf");
      if (!btn) return;
      const branch = TREE.find((b) => b.id === btn.dataset.role);
      const algo = branch?.algos.find((a) => a.name === btn.dataset.name);
      if (!algo) return;
      let card = detailEl.querySelector(".tool-card");
      if (!card) {
        card = document.createElement("div");
        card.className = "tool-card";
        detailEl.querySelector(".tools")?.appendChild(card);
      }
      card.innerHTML = `<b>${algo.name}</b>　${algo.does}<br>本场：${algo.use}${fieldHtml(algo.field)}`;
    });
    bodyEl?.addEventListener("mouseover", (e) => {
      const tr = e.target.closest("tr[data-pick]");
      if (!tr) return;
      const [, id] = tr.dataset.pick.split(":");
      const a = findA(id);
      if (a && a.side === "red") showSpec(a, tr);
      else hideSpec();
    });
    bodyEl?.addEventListener("mouseleave", hideSpec);
  }

  return {
    paint,
    bind,
    showKind,
    showAgent,
    getSelected: () => selected,
    findA,
    allLive,
  };
}
