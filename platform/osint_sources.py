"""Curated publisher scopes for public equipment and procurement research.

Categories describe publisher roles, never verified truth or independence.
This initial registry is deliberately bounded and is not global coverage.
"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit


SEARCH_SCOPES = [
    {"id": "defense-official", "name": "军方与防务官方 · 装备项目", "category": "defense-official", "sourceRole": "official-publication", "contentNature": "official-statement", "sourceReliability": "primary", "defaultSelected": True,
     "description": "军方、防务机构公开的装备技术与项目文献；官方发布不等于独立核验，当前来源以美英及北约为主。",
     "domains": ["defense.gov", "army.mil", "navy.mil", "af.mil", "marines.mil", "darpa.mil", "mod.gov.uk", "nato.int"], "queryTerms": "(equipment OR technology OR acquisition OR program)"},
    {"id": "test-evaluation", "name": "试验、审计与评估", "category": "test-evaluation", "sourceRole": "evaluation-report", "contentNature": "reported-evaluation", "sourceReliability": "primary", "defaultSelected": True,
     "description": "公开试验评估、审计及研究报告；需核对方法、测试条件和材料是否为原始报告。",
     "domains": ["gao.gov", "dote.osd.mil", "dtic.mil", "nrl.navy.mil"], "queryTerms": "(defense OR military OR equipment) (test OR evaluation OR audit OR report)"},
    {"id": "procurement-budget", "name": "采购合同、预算与交付", "category": "procurement-budget", "sourceRole": "procurement-record", "contentNature": "procurement-record", "sourceReliability": "primary", "defaultSelected": True,
     "description": "政府公开采购、合同与预算记录；预算、合同和交付分别保留，不能推断已形成能力。",
     "domains": ["defense.gov", "sam.gov", "usaspending.gov", "comptroller.defense.gov", "contractsfinder.service.gov.uk", "ted.europa.eu"], "queryTerms": "(defense OR military OR equipment) (contract OR procurement OR budget OR delivery)"},
    {"id": "manufacturer-disclosure", "name": "厂商披露 · 待核验声明", "category": "manufacturer-disclosure", "sourceRole": "manufacturer-disclosure", "contentNature": "manufacturer-claim", "sourceReliability": "primary", "defaultSelected": False,
     "description": "厂商公告、产品与产业披露，默认标记厂商声明；需试验或评估来源补证。",
     "domains": ["lockheedmartin.com", "rtx.com", "northropgrumman.com", "baesystems.com", "thalesgroup.com", "leonardo.com", "boeing.com", "airbus.com", "baykartech.com", "ga-asi.com", "generaldynamics.com", "textronsystems.com", "insitu.com", "avinc.com", "aerovironment.com", "elbitsystems.com", "iai.co.il", "safran-group.com", "rheinmetall.com", "quantum-systems.com", "uavision.com", "edgeautonomy.io", "skydio.com"], "queryTerms": "(equipment OR technology OR production OR contract)"},
    {"id": "defense-media", "name": "专业防务报道 · 补充线索", "category": "defense-media", "sourceRole": "secondary-report", "contentNature": "secondary-report", "sourceReliability": "secondary", "defaultSelected": False,
     "description": "专业防务媒体提供补充线索；转载不视为独立来源，应回查其引用的原始文件。",
     "domains": ["defensenews.com", "breakingdefense.com", "janes.com", "defenseone.com", "navalnews.com"], "queryTerms": "(equipment OR technology OR procurement OR industry)"},
    {"id": "public-imagery", "name": "公开遥感影像档案 · 待核验线索", "category": "public-imagery", "sourceRole": "public-imagery-archive", "contentNature": "image-search-discovery", "sourceReliability": "unknown", "defaultSelected": False,
     "description": "按研究对象定向发现政府遥感档案及 Planet、Maxar、Vantor、Airbus 已公开的历史卫星影像发布页，供公开产业或试验背景的来源核验。仅保存图片引用与原始发布页；授权依据、采集日期、传感器、真实性及对象相关性待人工核实。禁止实时追踪；普通网页图片不视为卫星影像，图片或图注不会自动提高置信度，也不证明协同能力。",
     "domains": ["nasa.gov", "esa.int", "copernicus.eu", "usgs.gov", "noaa.gov", "planet.com", "maxar.com", "vantor.com", "airbus.com", "intelligence-airbusds.com"], "queryTerms": '("satellite image" OR "satellite imagery")', "searchType": "images"},
]
for _scope in SEARCH_SCOPES:
    _scope.update({"engine": "firecrawl", "verificationStatus": "unverified"})

SEARCH_SCOPE_BY_ID = {scope["id"]: scope for scope in SEARCH_SCOPES}
DEFAULT_SCOPE_IDS = [scope["id"] for scope in SEARCH_SCOPES if scope["defaultSelected"]]


def publisher_domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").rstrip(".").casefold().encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""


def domain_in_scope(url: str, domains: list[str]) -> bool:
    host = publisher_domain(url)
    return bool(host) and any(host == domain or host.endswith("." + domain) for domain in domains)


def public_search_scopes():
    return deepcopy(SEARCH_SCOPES)
