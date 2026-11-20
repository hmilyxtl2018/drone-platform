/** Sand-table figures for knowledge-base cards. Not satellite, not photos except INTEL_FILES. */

const G = "#e2c36a";
const T = "#6fb4ae";
const R = "#d56a56";
const I = "#ece6d4";
const M = "#8d8878";
const LN = "#2a2e38";

function svg(inner) {
  return `<svg viewBox="0 0 160 100" aria-hidden="true">${inner}</svg>`;
}

function hemi(cx, cy, r, stroke) {
  return `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy} Z" fill="none" stroke="${stroke}" stroke-width="1.6"/>
    <line x1="${cx - r}" y1="${cy}" x2="${cx + r}" y2="${cy}" stroke="${stroke}" stroke-width="1" opacity=".5"/>`;
}

function shore() {
  return `<path d="M8 58 C 28 48, 48 66, 72 56 S 120 48, 152 58" fill="none" stroke="${G}" stroke-width="1.8"/>
    <path d="M8 58 C 28 48, 48 66, 72 56 S 120 48, 152 58 L 152 92 L 8 92 Z" fill="#152028" opacity=".9"/>
    <path d="M8 58 C 28 48, 48 66, 72 56 S 120 48, 152 58 L 152 8 L 8 8 Z" fill="#2a322c" opacity=".85"/>`;
}

export function figureOf(e, rec) {
  const id = e.id;
  const t = e.type;
  if (id === "host_lo") {
    return `<img src="./img/intel-host-lo.png?v=2" alt="H-17 低空突防" />`;
  }
  if (id === "unk_lo") {
    return `<img src="./img/intel-unk-lo.png" alt="不明空中平台" />`;
  }
  if (t === "Package") {
    return svg(`${shore()}
      ${hemi(78, 62, 42, G)}
      ${hemi(62, 64, 28, T)}
      <rect x="36" y="70" width="40" height="12" fill="none" stroke="${R}" stroke-width="1.4"/>`);
  }
  if (t === "Exclude") {
    return svg(`<circle cx="80" cy="50" r="28" fill="none" stroke="${M}" stroke-width="1.4"/>
      <line x1="60" y1="30" x2="100" y2="70" stroke="${R}" stroke-width="3"/>
      <line x1="100" y1="30" x2="60" y2="70" stroke="${R}" stroke-width="3"/>`);
  }
  if (t === "Shoreline") return svg(shore());
  if (t === "Region" && id === "SEA") {
    return svg(`<rect width="160" height="100" fill="#152028"/>
      <path d="M0 40 C 40 28, 80 52, 160 38 L 160 100 L 0 100 Z" fill="#1c2c36"/>
      <path d="M0 40 C 40 28, 80 52, 160 38" fill="none" stroke="${G}" stroke-width="1.6"/>`);
  }
  if (t === "Region" && id === "LAND") {
    return svg(`<rect width="160" height="100" fill="#2a322c"/>
      <path d="M0 0 L 160 0 L 160 38 C 80 52, 40 28, 0 40 Z" fill="#3a4238"/>
      <path d="M0 40 C 40 28, 80 52, 160 38" fill="none" stroke="${G}" stroke-width="1.6"/>`);
  }
  if (t === "Membership") {
    const sea = id.includes("SEA");
    return svg(`${shore()}
      <circle cx="${sea ? 52 : 112}" cy="${sea ? 72 : 28}" r="8" fill="${sea ? T : G}"/>`);
  }
  if (t === "Envelope" && id === "B_BMD") {
    return svg(`${shore()}${hemi(96, 58, 46, G)}<circle cx="96" cy="58" r="3" fill="${G}"/>`);
  }
  if (t === "Envelope") {
    return svg(`${shore()}${hemi(70, 60, 34, T)}<circle cx="70" cy="60" r="3" fill="${T}"/>`);
  }
  if (t === "Corridor") {
    return svg(`${shore()}<rect x="28" y="64" width="64" height="18" fill="none" stroke="${R}" stroke-width="1.8"/>
      <text x="40" y="77" fill="${R}" font-size="9">C_low</text>`);
  }
  if (t === "DetectionDisk") {
    return svg(`${shore()}<circle cx="48" cy="74" r="18" fill="none" stroke="${T}" stroke-width="1.5"/>
      <rect x="42" y="72" width="14" height="5" fill="${T}"/>`);
  }
  if (t === "SearchSector") {
    return svg(`${shore()}<path d="M108 42 L 48 78 L 148 78 Z" fill="${G}" opacity=".18" stroke="${G}" stroke-width="1.3"/>
      <circle cx="108" cy="42" r="4" fill="${G}"/>`);
  }
  if (t === "JamBloom" || id === "JAMMER") {
    return svg(`${shore()}<circle cx="50" cy="72" r="16" fill="none" stroke="${R}" stroke-dasharray="3 2"/>
      <path d="M50 62 L53 70 L50 68 L47 70 Z" fill="${R}"/>`);
  }
  if (t === "CommandPost") {
    return svg(`${shore()}<rect x="100" y="22" width="22" height="14" fill="none" stroke="${G}" stroke-width="1.6"/>
      <path d="M97 22 L111 12 L125 22" fill="none" stroke="${G}" stroke-width="1.6"/>`);
  }
  if (t === "Sensor" && id === "RADAR_SAM") {
    return svg(`${shore()}<rect x="92" y="28" width="10" height="8" fill="${T}"/>
      <line x1="97" y1="32" x2="48" y2="78" stroke="${T}" stroke-width="1.4"/>`);
  }
  if (t === "Sensor") {
    return svg(`${shore()}<path d="M104 30 Q 88 18 104 10" fill="none" stroke="${G}" stroke-width="1.8"/>
      <rect x="100" y="28" width="10" height="8" fill="${G}"/>`);
  }
  if (t === "Shooter" && id === "BMD_SILO") {
    return svg(`${shore()}<circle cx="108" cy="30" r="9" fill="none" stroke="${G}" stroke-width="1.6"/>
      <rect x="105" y="24" width="6" height="14" fill="${G}"/>`);
  }
  if (t === "Shooter") {
    return svg(`${shore()}<rect x="64" y="26" width="16" height="10" fill="${T}"/>
      <line x1="68" y1="26" x2="62" y2="16" stroke="${T}" stroke-width="1.6"/>
      <line x1="72" y1="26" x2="72" y2="14" stroke="${T}" stroke-width="1.6"/>
      <line x1="76" y1="26" x2="82" y2="16" stroke="${T}" stroke-width="1.6"/>`);
  }
  if (t === "Platform" && id === "SHIP") {
    return svg(`${shore()}<path d="M28 74 L 44 68 L 70 68 L 78 74 L 70 78 L 44 78 Z" fill="${T}"/>`);
  }
  if (t === "PlatformType" && id === "blue_uav") {
    return svg(`<rect width="160" height="100" fill="#10161c"/>
      <path d="M40 52 L80 48 L120 52 L114 58 L80 54 L46 58 Z" fill="${T}"/>
      <path d="M80 38 L86 58 L80 64 L74 58 Z" fill="${T}"/>`);
  }
  if (t === "PlatformType") {
    const stroke = rec && rec.inScene === false ? M : T;
    return svg(`<rect width="160" height="100" fill="#10161c"/>
      <path d="M36 54 L80 50 L124 54 L116 60 L80 56 L44 60 Z" fill="none" stroke="${stroke}" stroke-width="1.6"/>
      <path d="M80 40 L85 60 L80 66 L75 60 Z" fill="none" stroke="${stroke}" stroke-width="1.4"/>
      <text x="80" y="86" text-anchor="middle" fill="${stroke}" font-size="9" font-family="sans-serif">${(rec && rec.natoClass) || "UAS"}</text>`);
  }
  if (t === "Needline") {
    return svg(`${shore()}
      <circle cx="112" cy="24" r="4" fill="${G}"/>
      <line x1="112" y1="24" x2="104" y2="42" stroke="${G}"/>
      <line x1="112" y1="24" x2="78" y2="36" stroke="${G}"/>
      <line x1="112" y1="24" x2="70" y2="30" stroke="${G}"/>
      <line x1="112" y1="24" x2="108" y2="30" stroke="${G}"/>`);
  }
  if (t === "Link" && id === "mesh") {
    return svg(`<circle cx="50" cy="40" r="5" fill="${T}"/><circle cx="110" cy="36" r="5" fill="${T}"/>
      <circle cx="80" cy="70" r="5" fill="${T}"/>
      <path d="M50 40 L110 36 L80 70 Z" fill="none" stroke="${T}" stroke-dasharray="3 3"/>`);
  }
  if (t === "Link" && id === "air_station") {
    return svg(`${shore()}<circle cx="70" cy="36" r="4" fill="${T}"/>
      <rect x="100" y="28" width="10" height="8" fill="${G}"/>
      <line x1="70" y1="36" x2="105" y2="32" stroke="${T}" stroke-width="1.2"/>`);
  }
  if (t === "Link") {
    return svg(`${shore()}<path d="M40 74 L 56 70 L 78 74" fill="${T}"/>
      <path d="M104 30 Q 88 18 104 10" fill="none" stroke="${G}" stroke-width="1.6"/>
      <line x1="70" y1="72" x2="104" y2="36" stroke="${G}" stroke-dasharray="4 3"/>`);
  }
  if (t === "Invariant") {
    const on = !id.startsWith("no_");
    return svg(`<rect x="58" y="28" width="44" height="48" fill="none" stroke="${LN}"/>
      <circle cx="80" cy="50" r="12" fill="${on ? G : R}" opacity=".85"/>
      <text x="80" y="54" text-anchor="middle" font-size="11" fill="#111">${on ? "灯" : "禁"}</text>`);
  }
  if (t === "CloudDef") {
    return svg(`<ellipse cx="80" cy="52" rx="40" ry="22" fill="none" stroke="${G}" stroke-width="1.6"/>
      <ellipse cx="80" cy="52" rx="24" ry="12" fill="${G}" opacity=".25"/>
      <text x="80" y="56" text-anchor="middle" font-size="10" fill="${I}">${(rec && rec.Ex) != null ? "Ex " + rec.Ex : "云"}</text>`);
  }
  if (t === "Monitor") {
    return svg(`<rect x="44" y="30" width="72" height="40" fill="none" stroke="${G}" stroke-width="1.8"/>
      <path d="M70 50 L78 58 L94 38" fill="none" stroke="${G}" stroke-width="2"/>`);
  }
  if (t === "ShellTemplate") {
    const labs = ["感", "理", "决", "交", "行"];
    const boxes = labs
      .map((lab, i) => {
        const x = 12 + i * 30;
        return `<rect x="${x}" y="36" width="26" height="28" fill="none" stroke="${T}"/>
          <text x="${x + 13}" y="54" text-anchor="middle" font-size="10" fill="${I}">${lab}</text>`;
      })
      .join("");
    return svg(boxes);
  }
  if (t === "FunctionBind") {
    const give = (rec && rec.give) || e.label || "";
    return svg(`<rect x="16" y="38" width="50" height="24" fill="none" stroke="${M}"/>
      <text x="41" y="54" text-anchor="middle" font-size="9" fill="${M}">吃</text>
      <path d="M72 50 L92 50" stroke="${G}" stroke-width="1.6"/>
      <polygon points="92,50 86,46 86,54" fill="${G}"/>
      <rect x="98" y="38" width="50" height="24" fill="none" stroke="${G}"/>
      <text x="123" y="54" text-anchor="middle" font-size="9" fill="${G}">${give}</text>`);
  }
  return svg(`<rect x="40" y="30" width="80" height="40" fill="none" stroke="${M}"/>
    <text x="80" y="54" text-anchor="middle" font-size="11" fill="${M}">${t}</text>`);
}

export function titleOf(e, rec) {
  if (e.type === "FunctionBind" && rec && rec.title) return rec.title;
  if (e.type === "Exclude") {
    const map = {
      satelliteSensor: "不含天基传感器",
      link16: "不是 Link-16 实物",
      ibcsIfcn: "不是 IBCS 接口",
      meanField: "不用均值场",
      personAutoEngage: "不自动对人交战",
      occupationZone: "包络不是占领区",
    };
    return map[e.id] || e.id;
  }
  if (e.type === "Membership") return `${e.id.replace("∈", " ∈ ")}`;
  if (e.label && !String(e.label).startsWith("Ex ") && e.label !== "blue" && e.label !== "subset" && e.label !== "in") {
    return e.label;
  }
  if (e.type === "CloudDef") return `概念云 ${e.id}`;
  if (e.type === "Monitor") return "授权监视器";
  if (e.type === "ShellTemplate") return "蓝机 Agent 壳体";
  if (e.type === "PlatformType" && e.id === "blue_uav") return "己方无人机";
  if (e.type === "PlatformType") return e.label || e.id;
  return e.id;
}

export function keyOf(e) {
  return `${e.file}|${e.type}|${e.id}`;
}
