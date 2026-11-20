"""Fill coast-raid-k ABox/TBox from open sources. JSON remains SoR.

Licenses:
  BFO 2020 CC BY 4.0; CCO BSD-3; milsymbol SIDC MIT; QUDT CC BY 4.0;
  GeoSPARQL OGC; PROV-O W3C. Not ingested: JC3IEDM (NATO/MIP), real harbours,
  satellite, Jane's OOB.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SIDC_SRC = "https://github.com/spatialillusions/milsymbol (MIT) · MIL-STD-2525D numeric SIDC"
BFO = "http://purl.obolibrary.org/obo/"
CCO = "https://www.commoncoreontologies.org/"
GEO = "http://www.opengis.net/ont/geosparql#"
PROV = "http://www.w3.org/ns/prov#"
QUDT_U = "http://qudt.org/vocab/unit/"

# 2525D: VV C S SS H E AA EEEEEE M1M2  — identity 3 friend / 6 hostile / 1 unknown
SIDC = {
    "C2": {
        "code": "10061000001100000000",
        "std": "2525D",
        "symbolSet": "10",
        "entity": "110000",
        "identity": "hostile",
        "label": "Land unit · Command and Control",
        "src": SIDC_SRC,
    },
    "RADAR_SHORE": {
        "code": "10061500002203000300",
        "std": "2525D",
        "symbolSet": "15",
        "entity": "220300",
        "identity": "hostile",
        "label": "Land equipment · Radar · Early warning",
        "src": SIDC_SRC,
    },
    "RADAR_SAM": {
        "code": "10061500002203000000",
        "std": "2525D",
        "symbolSet": "15",
        "entity": "220300",
        "identity": "hostile",
        "label": "Land equipment · Radar (fire control)",
        "src": SIDC_SRC,
    },
    "SAM_TEL": {
        "code": "10061500001111010000",
        "std": "2525D",
        "symbolSet": "15",
        "entity": "111101",
        "identity": "hostile",
        "label": "AD missile launcher SAM · short range",
        "src": SIDC_SRC,
    },
    "BMD_SILO": {
        "code": "10061500001111070000",
        "std": "2525D",
        "symbolSet": "15",
        "entity": "111107",
        "identity": "hostile",
        "label": "AD missile launcher SAM · long range",
        "src": SIDC_SRC,
    },
    "SHIP": {
        "code": "10063000001202000000",
        "std": "2525D",
        "symbolSet": "30",
        "entity": "120200",
        "identity": "hostile",
        "label": "Sea surface · Surface combatant, line",
        "src": SIDC_SRC,
    },
    "JAMMER": {
        "code": "10063000001202000500",
        "std": "2525D",
        "symbolSet": "30",
        "entity": "120200",
        "identity": "hostile",
        "label": "Surface combatant · Electronic warfare modifier",
        "src": SIDC_SRC,
    },
    "blue_uav": {
        "code": "10030100001103000000",
        "std": "2525D",
        "symbolSet": "01",
        "entity": "110300",
        "identity": "friend",
        "label": "Air · Unmanned aerial vehicle",
        "src": SIDC_SRC,
    },
    "host_lo": {
        "code": "10060100001103000000",
        "std": "2525D",
        "symbolSet": "01",
        "entity": "110300",
        "identity": "hostile",
        "label": "Air · Unmanned aerial vehicle",
        "src": SIDC_SRC,
    },
    "unk_lo": {
        "code": "10010100001103000000",
        "std": "2525D",
        "symbolSet": "01",
        "entity": "110300",
        "identity": "unknown",
        "label": "Air · Unmanned aerial vehicle",
        "src": SIDC_SRC,
    },
}

EQUIV = {
    "Continuant": [BFO + "BFO_0000002"],
    "Occurrent": [BFO + "BFO_0000003"],
    "IndependentContinuant": [BFO + "BFO_0000004"],
    "MaterialObject": [BFO + "BFO_0000040"],
    "SpatialRegion": [BFO + "BFO_0000006"],
    "Quality": [BFO + "BFO_0000019"],
    "Role": [BFO + "BFO_0000023"],
    "Process": [BFO + "BFO_0000015"],
}

SEE = {
    "Entity": [BFO + "BFO_0000001"],
    "DependentContinuant": [BFO + "BFO_0000020", BFO + "BFO_0000031"],
    "InformationEntity": [CCO + "ont00000958", BFO + "BFO_0000031", PROV + "Entity"],
    "Relator": [BFO + "BFO_0000020"],
    "Algorithm": [BFO + "BFO_0000034"],
    "Rule": [PROV + "Plan"],
    "Package": [PROV + "Entity"],
    "Shoreline": [GEO + "Feature", GEO + "Geometry"],
    "Region": [GEO + "Feature"],
    "Envelope": [GEO + "Feature"],
    "Corridor": [GEO + "Feature"],
    "SearchSector": [GEO + "Feature"],
    "DetectionDisk": [GEO + "Feature"],
    "JamBloom": [GEO + "Feature"],
    "CommandPost": [CCO + "ont00000958"],
    "Sensor": [GEO + "Feature"],
    "Shooter": [GEO + "Feature"],
    "Platform": [GEO + "Feature"],
    "Emitter": [GEO + "Feature"],
    "Needline": [GEO + "Feature"],
    "CloudDef": ["https://doi.org/10.1002/int.20340"],
    "Track": [PROV + "Entity"],
    "Identity": [PROV + "Entity"],
    "Permit": [PROV + "Entity"],
}

ALGO = {
    "layer_detect": {
        "impl": "platform/battle.js zoneOf + detect",
        "paper": "Finn 1968 CFAR (public literature). Local detector, not a claim.",
        "license": "implementation local",
    },
    "track_filter": {
        "impl": "platform/battle.js track filter",
        "paper": "Kalman 1960; Bar-Shalom IMM. EKF/IMM as tool.",
        "license": "implementation local",
    },
    "cloud_shadow": {
        "impl": "platform/knowledge.js CloudKB",
        "paper": "Li Deyi, Liu Changyu, Gan Wenyan, 2009, Int. J. Intelligent Systems 24:357–375. doi:10.1002/int.20340",
        "license": "citation only; parameters are package ABox",
    },
    "iff_logic": {
        "impl": "platform/knowledge.js iff",
        "paper": "Local IFF/unk rule. Not STANAG 4193 text.",
        "license": "implementation local",
    },
    "finite_wave_assign": {
        "impl": "platform/battle.js wave assign",
        "paper": "Finite N assignment. Not mean-field.",
        "license": "implementation local",
    },
    "reactBlue_tasks": {
        "impl": "platform/battle.js reactBlue",
        "paper": "Local tasking: 巡逻/警戒/盯梢/压沿/识别.",
        "license": "implementation local",
    },
    "spherical_code": {
        "impl": "platform/battle.js sphereSlots",
        "paper": "Tammes problem / spherical codes; Conway–Sloane Sphere Packings (public math).",
        "license": "implementation local",
    },
    "MAPPO": {
        "impl": "unused in 40ms snapshot",
        "paper": "Yu et al., The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games, arXiv:2103.01955",
        "license": "tool citation; claim=false; scope=tool",
    },
    "HAPPO": {
        "impl": "unused in 40ms snapshot",
        "paper": "Kuba et al., Trust Region Policy Optimisation in Multi-Agent Reinforcement Learning, arXiv:2109.11251",
        "license": "tool citation; claim=false; scope=tool",
    },
    "QMIX": {
        "impl": "unused in 40ms snapshot",
        "paper": "Rashid et al., QMIX, ICML 2018, arXiv:1803.11485",
        "license": "tool citation; claim=false; scope=tool",
    },
    "mayGate": {
        "impl": "platform/knowledge.js CloudKB.mayGate",
        "paper": "Local gate: in ∧ p≥τ ∧ G(t) ∧ m>0 ∧ identity. Human/RoE remains.",
        "license": "implementation local",
    },
    "RoE": {
        "impl": "platform/knowledge.js RoE checks",
        "paper": "Local LegalRuleML-shaped obligations. Not a copied RoE manual.",
        "license": "implementation local",
    },
    "bounded_inject": {
        "impl": "platform/battle.js inject",
        "paper": "Bounded jam/restore/attrition. Not POSG Nash.",
        "license": "implementation local",
    },
}

RULE_META = {
    "N_bound": {
        "enforce": "compile + wave spawn. fail ⇒ do not spawn.",
        "sbvr": "It is necessary that N_k ≤ N_max per layer.",
    },
    "lo_in_corridor": {
        "enforce": "each tick lo. fail ⇒ trajectory illegal.",
        "sbvr": "It is necessary that lo-track is subset of C_low.",
    },
    "gate": {
        "enforce": "Permit insert. fail ⇒ no Permit.",
        "sbvr": "It is obligatory that Permit only if in ∧ p≥τ ∧ G_up ∧ m>0 ∧ identity separable.",
    },
    "unk_no_list": {
        "enforce": "Identity / Permit. fail ⇒ not listable.",
        "sbvr": "It is prohibited that 不明 is listable.",
    },
    "saturate_no_open": {
        "enforce": "Permit insert. fail ⇒ still closed.",
        "sbvr": "It is prohibited that saturation opens the gate.",
    },
    "ship_no_shoot": {
        "enforce": "zoneOf=ship. fail ⇒ no Permit in B_ship.",
        "sbvr": "It is prohibited that Permit occurs in B_ship.",
    },
    "envelope_not_occupy": {
        "enforce": "compile + depict. fail ⇒ package rejected.",
        "sbvr": "It is necessary that Envelope is a feasible set, not an occupation zone.",
    },
    "no_person_auto": {
        "enforce": "compile. fail ⇒ package rejected.",
        "sbvr": "It is prohibited that person is an engageable class with automatic fire.",
    },
    "no_mean_field": {
        "enforce": "compile. fail ⇒ package rejected.",
        "sbvr": "It is necessary that N is finite. Mean-field is excluded.",
    },
    "no_satellite_sensor": {
        "enforce": "compile. fail ⇒ package rejected.",
        "sbvr": "It is prohibited that Sensor kind is satellite.",
    },
    "fusion_split": {
        "enforce": "each tick vis. G_denied ⇒ local p only.",
        "sbvr": "If G(t) is denied then team fusion is unavailable.",
    },
}

CLOUD = {
    "inSam": {
        "vis": "team",
        "source": "theater.intel.src_team · 岸搜+舰载+制导（无天基）",
        "wasGeneratedBy": "cloud_shadow",
        "used": "Track",
        "floor": 0,
        "ceil": 1,
        "note": "Li Deyi cloud: Ex expectation, En entropy, He hyper-entropy. Not NN weights.",
    },
    "nEnv": {
        "vis": "local",
        "source": "编成计数 / finite N",
        "wasGeneratedBy": "cloud_shadow",
        "used": "Wave",
        "floor": 0,
        "note": "Expected environment count. Bounded by Rule.N_bound.",
    },
    "threatSam": {
        "vis": "team",
        "source": "Identity × inSam",
        "wasGeneratedBy": "iff_logic",
        "used": "Identity",
        "floor": 0,
        "ceil": 1,
        "note": "Threat belief. Gate still needs p≥τ.",
    },
}

NEW_CODES = [
    {
        "id": "StandardIdentity",
        "values": ["0", "1", "2", "3", "4", "5", "6"],
        "label": "2525D 标准身份 0 pending … 3 friend … 6 hostile",
    },
    {
        "id": "SymbolSet",
        "values": ["01", "10", "15", "20", "30"],
        "label": "2525D 符号集 air/land-unit/land-eq/installation/sea",
    },
    {
        "id": "QudtUnit",
        "values": [QUDT_U + "KiloM", QUDT_U + "KiloM-PER-HR", QUDT_U + "M"],
        "label": "QUDT 单位",
    },
    {
        "id": "CrsId",
        "values": ["CRK:ENG"],
        "label": "砂盘工程坐标",
    },
    {
        "id": "GeoSparqlGeom",
        "values": ["Point", "LineString", "Polygon", "Polygon Z"],
        "label": "GeoSPARQL WKT 类型（本包足迹，不是占领区）",
    },
    {
        "id": "UasGroup",
        "values": ["1", "2", "3", "4", "5"],
        "label": "DoD UAS Group",
    },
    {
        "id": "NatoUasClass",
        "values": ["I-micro", "I-mini", "I-small", "II", "III-MALE", "III-HALE", "III-strike"],
        "label": "NATO UAS Class (STANAG 4670 公开表)",
    },
    {
        "id": "Airframe",
        "values": ["fixed-wing", "rotary", "vtol", "sea-skimming"],
        "label": "气动构型",
    },
    {
        "id": "UasRole",
        "values": ["isr", "ew", "decoy", "raid", "cargo", "civilian", "relay"],
        "label": "无人机职能型",
    },
    {
        "id": "EasaClass",
        "values": ["C0", "C1", "C2", "C3", "C4", "C5", "C6"],
        "label": "EASA 2019/947 类标识",
    },
]


SIDC_NEU_UAV = {
    "code": "10040100001103000000",
    "std": "2525D",
    "symbolSet": "01",
    "entity": "110300",
    "identity": "neutral",
    "label": "Air · Unmanned aerial vehicle (catalog)",
    "src": SIDC_SRC,
}


def _sidc(entity: str, label: str, m1: str = "00") -> dict:
    code = f"1004010000{entity}{m1}00"
    if len(code) != 20:
        raise ValueError(f"SIDC length {len(code)} for {entity}")
    return {
        "code": code,
        "std": "2525D",
        "symbolSet": "01",
        "entity": entity,
        "identity": "neutral",
        "label": label,
        "src": SIDC_SRC,
    }


# Catalog OBJECT-TYPE only. Not spawned this raid. Bands from JP 3-30 / NATO class table.
UAS_CATALOG = [
    {
        "id": "uas-micro",
        "inScene": False,
        "klass": "微型无人机",
        "uasGroup": 1,
        "natoClass": "I-micro",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_max": 2,
        "alt_m_max": 61,
        "alt_ref": "AGL",
        "speed_kt_max": 100,
        "speed_kmh": 185,
        "missionRadius": "5 km LOS",
        "source": "NATO Class I micro · DoD Group 1",
        "note": "型谱。本幕不实例化。",
        "sidc": _sidc("110300", "UAV micro"),
    },
    {
        "id": "uas-mini",
        "inScene": False,
        "klass": "迷你无人机",
        "uasGroup": 1,
        "natoClass": "I-mini",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_min": 2,
        "mtow_kg_max": 20,
        "alt_m_max": 914,
        "alt_ref": "AGL",
        "speed_kt_max": 100,
        "speed_kmh": 185,
        "missionRadius": "25 km LOS",
        "source": "NATO Class I mini · DoD Group 1",
        "note": "型谱。手抛/单兵。本幕不实例化。",
        "sidc": _sidc("110300", "UAV mini"),
    },
    {
        "id": "uas-small",
        "inScene": False,
        "klass": "小型无人机",
        "uasGroup": 2,
        "natoClass": "I-small",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_min": 20,
        "mtow_kg_max": 150,
        "alt_m_max": 1524,
        "alt_ref": "AGL",
        "speed_kt_max": 250,
        "speed_kmh": 463,
        "missionRadius": "50 km LOS",
        "source": "NATO Class I small · DoD Group 2 (FAA 55 lb 对齐)",
        "note": "型谱。本幕 unk_lo 可对照，但不把本型直接进波次。",
        "sidc": _sidc("110300", "UAV small"),
    },
    {
        "id": "uas-tactical",
        "inScene": False,
        "klass": "战术无人机",
        "uasGroup": 3,
        "natoClass": "II",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_min": 150,
        "mtow_kg_max": 600,
        "alt_m_max": 3048,
        "alt_ref": "AGL",
        "speed_kt_max": 250,
        "speed_kmh": 463,
        "missionRadius": "200 km LOS",
        "source": "NATO Class II · DoD Group 3",
        "note": "型谱。blue_uav 分层 ISR 可对照。本幕实例仍是 blue_uav。",
        "sidc": _sidc("110300", "UAV tactical"),
    },
    {
        "id": "uas-male",
        "inScene": False,
        "klass": "中空长航时",
        "uasGroup": 4,
        "natoClass": "III-MALE",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_min": 600,
        "alt_m_max": 13716,
        "alt_ref": "MSL",
        "missionRadius": "BLOS unlimited",
        "source": "NATO Class III MALE · DoD Group 4/5",
        "note": "型谱。超出本幕包络。不是卫星。不开对人。",
        "sidc": _sidc("110300", "UAV MALE"),
    },
    {
        "id": "uas-hale",
        "inScene": False,
        "klass": "高空长航时",
        "uasGroup": 5,
        "natoClass": "III-HALE",
        "airframe": "fixed-wing",
        "role": "isr",
        "mtow_kg_min": 600,
        "alt_m_max": 19812,
        "alt_ref": "MSL",
        "missionRadius": "BLOS unlimited",
        "source": "NATO Class III HALE · DoD Group 5",
        "note": "型谱。空气动力平台，不是天基传感器。本幕 excludes satelliteSensor，HALE 也不进波次。",
        "sidc": _sidc("110300", "UAV HALE"),
    },
    {
        "id": "uas-vtol",
        "inScene": False,
        "klass": "垂直起降无人机",
        "uasGroup": 2,
        "natoClass": "I-small",
        "airframe": "vtol",
        "role": "isr",
        "mtow_kg_max": 150,
        "alt_m_max": 1067,
        "alt_ref": "AGL",
        "speed_kt_max": 100,
        "speed_kmh": 185,
        "missionRadius": "LOS",
        "source": "2525D VT-UAV entity 110400",
        "note": "型谱。本幕岸线砂盘无起降场个体。",
        "sidc": _sidc("110400", "VT-UAV"),
    },
    {
        "id": "uas-ew",
        "inScene": False,
        "klass": "电子战无人机",
        "uasGroup": 3,
        "natoClass": "II",
        "airframe": "fixed-wing",
        "role": "ew",
        "alt_m_max": 3048,
        "alt_ref": "AGL",
        "source": "2525D jammer / ECM entity 110108",
        "note": "型谱。本幕干扰是海面 Emitter，不是此型。",
        "sidc": _sidc("110108", "UAV / ECM"),
    },
    {
        "id": "uas-decoy",
        "inScene": False,
        "klass": "诱饵无人机",
        "uasGroup": 2,
        "natoClass": "I-small",
        "airframe": "fixed-wing",
        "role": "decoy",
        "source": "2525D air decoy entity 130200",
        "note": "型谱。未来饱和/诱饵，不是开门理由（见 saturate_no_open）。",
        "sidc": _sidc("130200", "air decoy"),
    },
    {
        "id": "uas-sea-skimming",
        "inScene": False,
        "klass": "贴海突防无人机",
        "uasGroup": 3,
        "natoClass": "II",
        "airframe": "sea-skimming",
        "role": "raid",
        "band_m": [50, 400],
        "speed_kmh": 720,
        "source": "本包 host_lo 对照型 · 走廊 C_low",
        "note": "型谱。本幕 ITEM 仍是 host_lo。payload 不开对人。",
        "payload": "对海/对阵地 · 不开对人",
        "sidc": _sidc("110300", "UAV sea-skimming"),
    },
    {
        "id": "uas-cargo",
        "inScene": False,
        "klass": "运补无人机",
        "uasGroup": 3,
        "natoClass": "II",
        "airframe": "fixed-wing",
        "role": "cargo",
        "source": "2525D cargo entity 110107",
        "note": "型谱。本幕无后勤波次。",
        "sidc": _sidc("110107", "UAV cargo"),
    },
    {
        "id": "uas-civilian",
        "inScene": False,
        "klass": "民用无人机",
        "uasGroup": 1,
        "natoClass": "I-mini",
        "airframe": "rotary",
        "role": "civilian",
        "mtow_kg_max": 25,
        "list": "不进交战清单",
        "source": "EASA Open · 2525D civilian UAV 120300",
        "note": "型谱。不得自动交战。不明/民用不进清单。",
        "sidc": {
            "code": "10040100001203000000",
            "std": "2525D",
            "symbolSet": "01",
            "entity": "120300",
            "identity": "neutral",
            "label": "Civilian unmanned aerial vehicle",
            "src": SIDC_SRC,
        },
    },
    {
        "id": "uas-relay",
        "inScene": False,
        "klass": "中继无人机",
        "uasGroup": 3,
        "natoClass": "II",
        "airframe": "fixed-wing",
        "role": "relay",
        "source": "Needline 备份构想 · 本幕 G(t) 仍是岸上星形",
        "note": "型谱。不替代 G(t)，不引入 Link-16。",
        "sidc": _sidc("110119", "UAV communications"),
    },
]


PT_FIELDS = [
    {"name": "inScene", "label": "是否本幕", "type": "boolean", "required": False, "facet": "型谱"},
    {"name": "uasGroup", "label": "DoD Group", "type": "code", "required": False, "code": "UasGroup", "facet": "型谱"},
    {"name": "natoClass", "label": "NATO Class", "type": "code", "required": False, "code": "NatoUasClass", "facet": "型谱"},
    {"name": "airframe", "label": "构型", "type": "code", "required": False, "code": "Airframe", "facet": "型谱"},
    {"name": "role", "label": "职能", "type": "code", "required": False, "code": "UasRole", "facet": "能力"},
    {"name": "mtow_kg_max", "label": "最大起飞质量", "type": "number", "required": False, "unit": "kg", "facet": "能力"},
    {"name": "alt_m_max", "label": "高度上限", "type": "number", "required": False, "unit": "m", "facet": "能力"},
    {"name": "speed_kt_max", "label": "速度上限", "type": "number", "required": False, "unit": "kt", "facet": "能力"},
    {"name": "missionRadius", "label": "任务半径", "type": "string", "required": False, "facet": "能力"},
    {"name": "source", "label": "分类出处", "type": "string", "required": False, "facet": "约束"},
    {"name": "sidc", "label": "SIDC", "type": "object", "required": False, "facet": "符号"},
]


NEW_INVARIANTS = [
    {"id": "type_not_item", "text": "inScene=false 的平台型不得进本幕波次"},
    {"id": "hale_not_scene", "text": "HALE/G5 不进本幕；它不是天基传感器，但超出本幕包络"},
]

NEW_RULES = [
    {
        "id": "type_not_item",
        "type": "Rule",
        "modality": "hard",
        "text": "inScene=false 的平台型不得进本幕波次",
        "evalOn": "compile + wave spawn",
        "states": ["pass", "fail"],
        "illegal": ["spawn catalog type"],
        "enforce": "compile。型库只在 型谱，不进 N_k。",
        "sbvr": "It is necessary that only inScene PlatformType appears in a wave.",
    },
    {
        "id": "hale_not_scene",
        "type": "Rule",
        "modality": "hard",
        "text": "HALE/G5 不进本幕；它不是天基传感器，但超出本幕包络",
        "evalOn": "compile",
        "states": ["pass", "fail"],
        "illegal": ["HALE in wave", "treat HALE as satelliteSensor"],
        "enforce": "compile。uas-hale.inScene 必须为 false。",
        "sbvr": "It is prohibited that HALE/G5 is an inScene type in this package.",
    },
]


def enrich_uas_catalog() -> None:
    th = load("theater.json")
    types = th.setdefault("platformTypes", [])
    by_id = {p["id"]: p for p in types}
    for sid in ("blue_uav", "host_lo", "unk_lo"):
        if sid in by_id:
            by_id[sid]["inScene"] = True
    by_id["blue_uav"].setdefault("uasGroup", 3)
    by_id["blue_uav"].setdefault("natoClass", "II")
    by_id["blue_uav"].setdefault("airframe", "fixed-wing")
    by_id["blue_uav"].setdefault("role", "isr")
    by_id["blue_uav"].setdefault("source", "本幕编成 · 对照 uas-tactical")
    by_id["host_lo"].setdefault("uasGroup", 3)
    by_id["host_lo"].setdefault("natoClass", "II")
    by_id["host_lo"].setdefault("airframe", "sea-skimming")
    by_id["host_lo"].setdefault("role", "raid")
    by_id["host_lo"].setdefault("source", "本幕敌情 · 对照 uas-sea-skimming")
    by_id["unk_lo"].setdefault("uasGroup", 2)
    by_id["unk_lo"].setdefault("natoClass", "I-small")
    by_id["unk_lo"].setdefault("airframe", "fixed-wing")
    by_id["unk_lo"].setdefault("role", "isr")
    by_id["unk_lo"].setdefault("source", "本幕不明 · 对照 uas-small / uas-civilian")
    for rec in UAS_CATALOG:
        if rec["id"] in by_id:
            by_id[rec["id"]].update(rec)
        else:
            types.append(rec)
    dump("theater.json", th)


def enrich_invariants() -> None:
    cs = load("constraints.json")
    have = {i["id"] for i in cs.get("invariants") or []}
    for inv in NEW_INVARIANTS:
        if inv["id"] not in have:
            cs["invariants"].append(inv)
    dump("constraints.json", cs)



def load(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def dump(name: str, data) -> None:
    (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"write {name}")


def enrich_tbox() -> None:
    tb = load("tbox.json")
    for cls in tb.get("classes") or []:
        cid = cls["id"]
        if cid in EQUIV:
            cls["equivalentTo"] = EQUIV[cid]
        if cid in SEE:
            cls["seeAlso"] = SEE[cid]
        if cid == "PlatformType":
            cls["comment"] = (
                "OBJECT-TYPE。inScene=true 才是本幕 ITEM；false 是型库，供未来场景引用。"
                "不是波次个体。HALE 不是天基传感器。"
            )
    dump("tbox.json", tb)


def enrich_theater() -> None:
    th = load("theater.json")
    for s in th.get("sites") or []:
        if s["id"] in SIDC:
            s["sidc"] = SIDC[s["id"]]
    for p in th.get("platformTypes") or []:
        if p["id"] in SIDC:
            p["sidc"] = SIDC[p["id"]]
    dump("theater.json", th)


def enrich_gis() -> None:
    gis = load("gis.json")
    gis["crs"] = {
        "id": "CRK:ENG",
        "type": "EngineeringCRS",
        "unit": QUDT_U + "KiloM",
        "epsg": None,
        "note": "Local sand-table. x east, y up, z shore-distance. 1 = 1 km. Not EPSG geographic. Not a real harbour.",
    }
    dump("gis.json", gis)


def enrich_constraints() -> None:
    cs = load("constraints.json")
    for cid, extra in CLOUD.items():
        if cid in cs.get("clouds") or {}:
            cs["clouds"][cid].update(extra)
    have = {i["id"] for i in cs.get("invariants") or []}
    for inv in NEW_INVARIANTS:
        if inv["id"] not in have:
            cs.setdefault("invariants", []).append(inv)
    dump("constraints.json", cs)


def enrich_algorithms() -> None:
    alg = load("algorithms.json")
    for t in alg.get("tools") or []:
        extra = ALGO.get(t["id"])
        if extra:
            t.update(extra)
    dump("algorithms.json", alg)


def enrich_rules() -> None:
    ru = load("rules.json")
    have = {r["id"] for r in ru.get("rules") or []}
    for r in ru.get("rules") or []:
        extra = RULE_META.get(r["id"])
        if extra:
            r.update(extra)
    for r in NEW_RULES:
        if r["id"] not in have:
            ru.setdefault("rules", []).append(r)
    dump("rules.json", ru)


def enrich_dictionary() -> None:
    d = load("dictionary.json")
    have = {c["id"] for c in d.get("codes") or []}
    for c in NEW_CODES:
        if c["id"] not in have:
            d["codes"].append(c)
    d["version"] = "1.3.0"
    store = d.setdefault("store", {})
    store["sqlite"] = "ontology/coast-raid-k/coast-raid-k.sqlite"
    store["sources"] = "sources.json"
    for t in d.get("tables") or []:
        if t.get("id") != "PlatformType":
            continue
        t["rows"] = "本幕 ITEM: blue_uav, host_lo, unk_lo · 型库 OBJECT-TYPE: uas-*"
        have_f = {f["name"] for f in t.get("fields") or []}
        for f in t.get("fields") or []:
            if f.get("name") == "side":
                f["required"] = False
        for f in PT_FIELDS:
            if f["name"] not in have_f:
                t.setdefault("fields", []).append(f)
    dump("dictionary.json", d)


def enrich_catalog() -> None:
    cat = load("catalog.json")
    cat["parts"]["sources"] = "sources.json"
    cat["parts"]["sqlite"] = "coast-raid-k.sqlite"
    dump("catalog.json", cat)


def write_sources() -> None:
    doc = {
        "package": "coast-raid-k",
        "type": "OpenSourceRegister",
        "note": "Provenance of ingested open vocabularies. Not a 40ms payload. Not JC3IEDM.",
        "items": [
            {
                "id": "BFO-2020",
                "usedAs": "TBox equivalentClass / seeAlso",
                "iri": "https://github.com/BFO-ontology/BFO-2020",
                "license": "CC BY 4.0",
                "what": "ISO/IEC 21838-2 upper ontology class IRIs",
            },
            {
                "id": "CCO-2.0",
                "usedAs": "rdfs:seeAlso InformationEntity",
                "iri": "https://github.com/CommonCoreOntology/CommonCoreOntologies",
                "license": "BSD-3-Clause",
                "what": "ont00000958 Information Content Entity",
            },
            {
                "id": "milsymbol-2525D",
                "usedAs": "Scene 符号 SIDC",
                "iri": "https://github.com/spatialillusions/milsymbol",
                "license": "MIT",
                "what": "Numeric 20-digit SIDC for sand-table sites and platform types",
            },
            {
                "id": "QUDT",
                "usedAs": "CRS unit",
                "iri": "http://qudt.org/vocab/unit/KiloM",
                "license": "CC BY 4.0",
                "what": "Kilometre and km/h",
            },
            {
                "id": "GeoSPARQL",
                "usedAs": "几何 geo:asWKT",
                "iri": "http://www.opengis.net/ont/geosparql",
                "license": "OGC",
                "what": "WKT footprints derived from theater/gis. Not OSM harbour.",
            },
            {
                "id": "PROV-O",
                "usedAs": "知识 谱系",
                "iri": "http://www.w3.org/ns/prov#",
                "license": "W3C",
                "what": "wasGeneratedBy / used",
            },
            {
                "id": "cloud-model-2009",
                "usedAs": "CloudDef 数字特征出处",
                "iri": "https://doi.org/10.1002/int.20340",
                "license": "citation",
                "what": "Li Deyi et al. cloud model Ex/En/He. Parameters are local ABox.",
            },
            {
                "id": "JP-3-30-UAS-groups",
                "usedAs": "PlatformType.uasGroup 型谱",
                "iri": "https://www.congress.gov/crs_external_products/IF/PDF/IF12797/IF12797.2.pdf",
                "license": "US government work (CRS) citing JP 3-30",
                "what": "DoD UAS Groups 1–5: MTOW, altitude, speed bands. Category types, not named OOB.",
            },
            {
                "id": "NATO-UAS-class",
                "usedAs": "PlatformType.natoClass 型谱",
                "iri": "https://doi.org/10.18372/1990-5548.74.17354",
                "license": "citation of STANAG 4670 class table (public secondary)",
                "what": "NATO Class I micro/mini/small, II tactical, III MALE/HALE. Not the STANAG text dump.",
            },
            {
                "id": "EASA-2019-947",
                "usedAs": "dictionary.codes EasaClass",
                "iri": "https://eur-lex.europa.eu/eli/reg_impl/2019/947/oj",
                "license": "EU official (public)",
                "what": "Open/Specific/Certified and C0–C6 class marks. Catalog only.",
            },
        ],
        "rejected": [
            {"id": "JC3IEDM", "why": "NATO/MIP restricted; tabs use the TYPE/ITEM/LOCATION face, not the STANAG dump"},
            {"id": "OSM-harbour", "why": "would recinematize a real coast; shoreline stays harmonic sand-table"},
            {"id": "satellite", "why": "catalog.excludes satelliteSensor"},
            {"id": "Janes-OOB", "why": "named platform performance / unit OOB is not open type data"},
        ],
    }
    dump("sources.json", doc)


def main() -> int:
    enrich_tbox()
    enrich_theater()
    enrich_uas_catalog()
    enrich_gis()
    enrich_constraints()
    enrich_invariants()
    enrich_algorithms()
    enrich_rules()
    enrich_dictionary()
    enrich_catalog()
    write_sources()
    print("enrich ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
