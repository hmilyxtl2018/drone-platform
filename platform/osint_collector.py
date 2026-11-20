"""Bounded public-source OSINT collector for the intelligence Agent.

Firecrawl searches public web materials when configured; the explicit RSS
mode reads curated feeds. Both emit untrusted, reviewable evidence records.

The public API is :func:`collect` and :class:`OSINTCollector.collect`::

    result = collect(["gao-reports"], ["procurement"], since_days=30)

Every failure is returned in ``errors`` and in the corresponding
``sourceResults`` entry.  Callers must not treat a partial result as a complete
view of the sources.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import html
import ipaddress
import json
import re
import socket
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from osint_sources import SEARCH_SCOPES, SEARCH_SCOPE_BY_ID, DEFAULT_SCOPE_IDS, domain_in_scope, publisher_domain
    from research_trace import trace_call
except ModuleNotFoundError:
    from .osint_sources import SEARCH_SCOPES, SEARCH_SCOPE_BY_ID, DEFAULT_SCOPE_IDS, domain_in_scope, publisher_domain
    from .research_trace import trace_call


# URLs are intentionally curated in code.  A caller may choose among these
# source IDs but cannot submit an arbitrary URL to the collector.
SOURCES: list[dict[str, str]] = [
    {
        "id": "gao-reports",
        "name": "U.S. GAO Reports",
        "url": "https://www.gao.gov/rss/reports.xml",
        "category": "defense-policy",
    },
    {
        "id": "defense-news-pentagon",
        "name": "Defense News · Pentagon",
        "url": "https://www.defensenews.com/arc/outboundfeeds/rss/category/pentagon/?outputType=xml",
        "category": "defense-policy",
    },
    {
        "id": "defense-news-industry",
        "name": "Defense News · Industry",
        "url": "https://www.defensenews.com/arc/outboundfeeds/rss/category/industry/?outputType=xml",
        "category": "equipment-procurement-industry",
    },
    {
        "id": "csis",
        "name": "CSIS Analysis",
        "url": "https://www.csis.org/rss.xml",
        "category": "security-research",
    },
    {
        "id": "congress-bills",
        "name": "U.S. Congress Bills",
        "url": "https://www.congress.gov/rss/most-viewed-bills.xml",
        "category": "defense-policy",
    },
]

_RSS_ROLES = {
    "gao-reports": ("evaluation-report", "reported-evaluation", "primary"),
    "congress-bills": ("official-publication", "official-statement", "primary"),
    "defense-news-pentagon": ("secondary-report", "secondary-report", "secondary"),
    "defense-news-industry": ("secondary-report", "secondary-report", "secondary"),
    "csis": ("research-analysis", "source-report", "unknown"),
}
for _source in SOURCES:
    _role, _nature, _reliability = _RSS_ROLES[_source["id"]]
    _source.update({"sourceRole": _role, "contentNature": _nature, "sourceReliability": _reliability, "engine": "rss", "defaultSelected": _source["id"] == "gao-reports", "verificationStatus": "unverified", "description": "显式 RSS/Atom 模式，仅采集当前 feed 保留条目；不代表完整历史档案。", "domains": [publisher_domain(_source["url"])]})

_SOURCES_BY_ID = {source["id"]: source for source in SOURCES}
_USER_AGENT = "OntologyAgents-OSINT/0.1 (+public-research-demo)"
_DEFAULT_TIMEOUT = 12.0
_DEFAULT_MAX_BYTES = 2_000_000
_MAX_ITEMS = 100
_MAX_KEYWORDS = 32
# Search expressions may contain a subject group plus several context groups.
# The former 120-character cap discarded the preset's complete technology
# expression before it reached Firecrawl, leaving later rounds with only
# generic UAV/UAS anchors. Keep a bounded but useful expression intact.
_MAX_KEYWORD_CHARS = 400
_MAX_FEED_URL_LENGTH = 2048
_NAMESPACE_CONTENT = "http://purl.org/rss/1.0/modules/content/"
_NAMESPACE_ATOM = "http://www.w3.org/2005/Atom"
_NAMESPACE_DC = "http://purl.org/dc/elements/1.1/"
_FIRECRAWL_MAX_RESPONSE = 4_000_000
_MAX_SEARCH_REQUESTS = 6
_MAX_COLLECTION_ROUNDS = 3
_MAX_AUTOMATED_COLLECTION_ROUNDS = 24
_MAX_TOTAL_SEARCH_REQUESTS = _MAX_SEARCH_REQUESTS * _MAX_COLLECTION_ROUNDS
_MAX_SCRAPES_PER_SCOPE = 8
_MAX_SEARCH_RETRIES = 3
_MAX_HTTP_RETRIES = 2
_DEFAULT_FIRECRAWL_TIMEOUT = 90.0
_FIRECRAWL_DEADLINE = contextvars.ContextVar('firecrawl_request_deadline',default=None)
_MAX_FIRECRAWL_TIMEOUT = 120.0
FIRECRAWL_SOURCE = {"id": "firecrawl-search", "name": "Firecrawl（旧任务兼容：官方优先来源组）", "url": "https://api.firecrawl.dev/v1/search", "category": "collection-engine", "engine": "firecrawl", "legacy": True}


class OSINTCollectionError(ValueError):
    """A source or request failed a bounded collector validation."""


def _is_supplementary_item(item: Mapping[str, Any]) -> bool:
    """Return whether an item is an image/discovery clue rather than正文.

    Image search records remain in ``items`` for backwards compatibility, but
    they cannot satisfy the requested evidence-material target.  Keep this
    check in the collector so convergence, budget and returned diagnostics use
    the same definition.
    """
    provenance = item.get("provenance") if isinstance(item, Mapping) else None
    if not isinstance(provenance, Mapping):
        return False
    return (provenance.get("retrievalKind") == "search-discovery-only"
            or provenance.get("contentKind") == "image-search-metadata")


def _automated_round_budget(max_items: int, scope_count: int) -> tuple[int, dict[str, Any]]:
    """Return a finite, target-aware search-round budget.

    A round gives each selected source scope one request.  Search results are
    filtered, scraped and de-duplicated before they count toward the target,
    so the budget uses a conservative expected yield of two retained
    materials per round. This leaves room for source filtering and duplicate
    suppression (25 items plans thirteen rounds, while a target of 50 plans
    twenty-four, capped) and still stops immediately when the target is reached.
    The returned policy is persisted with the collection budget so the
    execution overview can explain both the plan and the actual stop round.
    """
    target = max(1, int(max_items))
    scopes = max(1, int(scope_count))
    expected_retained_per_round = 2
    rounds = max(1, min(_MAX_AUTOMATED_COLLECTION_ROUNDS,
                        (target + expected_retained_per_round - 1) // expected_retained_per_round))
    formula = "min(24, ceil(maxItems / expectedRetainedItemsPerRound))"
    return rounds, {
        "formula": formula,
        "targetItems": target,
        "selectedSourceScopes": scopes,
        "expectedRetainedItemsPerRound": expected_retained_per_round,
        "plannedRounds": rounds,
        "minimumRounds": 1,
        "maximumRounds": _MAX_AUTOMATED_COLLECTION_ROUNDS,
    }


class _NoSearchRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSINTCollectionError("Firecrawl redirect rejected")


class FirecrawlSearchProvider:
    """Search public web pages through Firecrawl's JSON API.

    Firecrawl is a search boundary only. Results remain untrusted source
    material and are normalized into the same evidence shape as RSS items.
    The API key is read by the backend process and never returned to callers.
    """

    provider_id = "firecrawl-search"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, timeout: float | None = None, opener: Callable[..., Any] | None = None):
        self.api_key = (api_key if api_key is not None else os.environ.get("FIRECRAWL_API_KEY", "")).strip()
        self.base_url = (base_url or os.environ.get("FIRECRAWL_BASE_URL") or "https://api.firecrawl.dev/v1").rstrip("/")
        if timeout is None:
            try:
                timeout = float(os.environ.get("FIRECRAWL_TIMEOUT_SECONDS", str(_DEFAULT_FIRECRAWL_TIMEOUT)))
            except (TypeError, ValueError):
                timeout = _DEFAULT_FIRECRAWL_TIMEOUT
        self.timeout = max(2.0, min(float(timeout), _MAX_FIRECRAWL_TIMEOUT))
        self.opener = opener

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def metadata(self) -> dict[str, Any]:
        return {"id": self.provider_id, "configured": self.configured, "api": "search+scrape", "role": "collection-engine", "fallback": None, "timeoutSeconds": self.timeout, "maxSearchScopes": _MAX_SEARCH_REQUESTS, "maxCollectionRounds": _MAX_AUTOMATED_COLLECTION_ROUNDS, "maxInteractiveCollectionRounds": _MAX_COLLECTION_ROUNDS, "maxSearchRequests": _MAX_SEARCH_REQUESTS * _MAX_AUTOMATED_COLLECTION_ROUNDS, "maxSearchRetriesPerScope": _MAX_SEARCH_RETRIES, "maxScrapesPerScope": _MAX_SCRAPES_PER_SCOPE, "maxTotalRequestedItems": 100, "maxResponseBytesPerRequest": _FIRECRAWL_MAX_RESPONSE, "maxBodyCharacters": 20000, "imageDiscovery": "public-archive-metadata-only"}

    @staticmethod
    def _result_url(item: Mapping[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        return str(item.get("url") or item.get("sourceURL") or metadata.get("sourceURL") or "").strip()

    @staticmethod
    def _valid_result_url(url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").rstrip(".").casefold()
            if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
                return False
            if host in {"localhost", "localhost.localdomain", "ip6-localhost"} or host.endswith(".local") or host.endswith(".localhost"):
                return False
            if re.fullmatch(r"[0-9a-fA-F:.]+", host) and not _is_public_ip(host):
                return False
            return True
        except ValueError:
            return False

    def search(self, question: str, keywords: Sequence[str], *, since_days: int, max_items: int, source_ids: Sequence[str] = (), now: datetime | None = None, ensure_target: bool = False, deadline_monotonic: float | None = None, source_quotas: Mapping[str, int] | None = None) -> dict[str, Any]:
        """Collect until the requested material target or a hard search budget.

        A single search page is often exhausted by domain, subject, date, or
        duplicate filtering. The collector therefore makes up to three
        bounded rounds, relaxing the query expression after the first round,
        while retaining the curated publisher scope and a hard request cap.
        Automated task runs can request the extended bounded loop so a sparse
        first page does not immediately become a partial task.
        """
        _FIRECRAWL_DEADLINE.set(deadline_monotonic)
        if not self.configured:
            raise OSINTCollectionError("Firecrawl search is selected but FIRECRAWL_API_KEY is not configured")
        if not str(question or "").strip() or type(max_items) is not int or not 1 <= max_items <= 100 or type(since_days) is not int or not 0 <= since_days <= 3650:
            raise OSINTCollectionError("Firecrawl requires a nonempty query and bounded search limits")
        ids = list(dict.fromkeys(str(x) for x in source_ids)) if source_ids else list(DEFAULT_SCOPE_IDS)
        if "firecrawl-search" in ids:
            ids = list(dict.fromkeys([*(x for x in ids if x != "firecrawl-search"), *DEFAULT_SCOPE_IDS]))
        scopes = []
        for scope_id in ids:
            scope = SEARCH_SCOPE_BY_ID.get(scope_id)
            # Existing RSS source IDs remain usable as restricted search
            # scopes in Firecrawl mode, but never widen to the entire web.
            if scope is None and scope_id in _SOURCES_BY_ID:
                feed = _SOURCES_BY_ID[scope_id]
                scope = {**feed, "domains": [publisher_domain(feed["url"])], "queryTerms": "(defense OR military OR equipment)"}
            if scope is None:
                raise OSINTCollectionError(f"source ID is not in the curated search allowlist: {scope_id}")
            scopes.append(scope)
        if not scopes or len(scopes) > _MAX_SEARCH_REQUESTS:
            raise OSINTCollectionError("select between one and six public source scopes")
        # Stable priority order makes the default official sources receive
        # scarce budget first even if a browser sends a different ordering.
        priority = {scope["id"]: idx for idx, scope in enumerate(SEARCH_SCOPES)}
        scopes.sort(key=lambda scope: priority.get(scope["id"], 99))
        ids = [scope["id"] for scope in scopes]
        anchors = _subject_anchors(keywords)
        # Direct provider calls stay one bounded page. Automated equipment
        # jobs opt into the finite replenishment loop explicitly.
        # Historical/batch calls with an explicit clock get three bounded
        # replenishment rounds. Interactive calls remain one round; image
        # discovery also stays cheap unless an automated job explicitly
        # requests the target-filling loop.
        if ensure_target:
            max_collection_rounds, round_policy = _automated_round_budget(max_items, len(scopes))
        else:
            max_collection_rounds = (3 if now is not None and not any(scope.get("searchType") == "images" for scope in scopes) else 1)
            round_policy = {"formula": "interactive fixed request budget", "targetItems": max_items, "selectedSourceScopes": len(scopes), "plannedRounds": max_collection_rounds, "minimumRounds": max_collection_rounds, "maximumRounds": max_collection_rounds}
        max_total_requests = _MAX_SEARCH_REQUESTS * max_collection_rounds
        quotas = {str(k): max(0, int(v)) for k, v in (source_quotas or {}).items() if str(k).strip()}
        budget = {"maxItems": max_items, "requestedLimitTotal": 0, "plannedRequests": 0, "executedRequests": 0, "retryRequests": 0, "maxRequests": max_total_requests, "maxSearchScopes": _MAX_SEARCH_REQUESTS, "maxCollectionRounds": max_collection_rounds, "maxSearchRetriesPerScope": _MAX_SEARCH_RETRIES, "maxScrapesPerScope": _MAX_SCRAPES_PER_SCOPE, "maxResponseBytesPerRequest": _FIRECRAWL_MAX_RESPONSE, "maxTotalResponseBytes": max_total_requests * _FIRECRAWL_MAX_RESPONSE, "timeoutSecondsPerRequest": self.timeout, "maxSequentialRequestSeconds": max_total_requests * self.timeout, "pagination": False, "allocation": "source-quota-first-with-bounded-query-rounds" if quotas else "target-first-with-bounded-query-rounds", "selectedScopes": ids, "sourceQuotas": quotas, "roundPolicy": round_policy, "keywordExpressionLimit": _MAX_KEYWORD_CHARS, "targetBasis": "正文材料（图片检索线索为辅助）" if ensure_target else "全部保留材料"}
        items, errors, seen_urls, seen_hashes = [], [], {}, {}
        result_by_source: dict[str, dict[str, Any]] = {}
        source_unique_counts = {scope["id"]: 0 for scope in scopes}
        query_plan: list[dict[str, Any]] = []
        # Do not spend a request when a scope would issue the exact same
        # normalized query again.  Later rounds rotate the anchor/context;
        # finite query combinations are recorded as skips instead.
        seen_queries: set[tuple[str, str]] = set()
        request_count = 0
        rounds_completed = 0
        # One invocation shares successful page bodies across rounds/scopes.
        # A provider instance may serve concurrent runs, so keep this local.
        scrape_cache: dict[str, dict[str, Any]] = {}

        def aggregate(scope: Mapping[str, Any], source_result: Mapping[str, Any], *, round_number: int, requested_limit: int, new_count: int, duplicate_count: int, remaining_target: int | None = None, stop_reason: str | None = None) -> dict[str, Any]:
            entry = result_by_source.setdefault(scope["id"], {"sourceId": scope["id"], "sourceName": scope["name"], "sourceRole": scope.get("sourceRole"), "collector": self.provider_id, "status": "ok", "itemCount": 0, "requestedLimit": 0, "domains": list(scope["domains"]), "searchRounds": 0, "rawResultCount": 0, "responseBytes": 0, "searchResponseBytes": 0, "scrapeResponseBytes": 0, "scrapeRequestCount": 0, "searchRetryCount": 0, "duplicateCount": 0, "excludedCounts": {key: 0 for key in (source_result.get("excludedCounts") or {})}, "queries": [], "roundDetails": []})
            entry["requestedLimit"] += requested_limit
            entry["searchRounds"] += 1
            entry["rawResultCount"] += int(source_result.get("rawResultCount") or 0)
            for key in ("responseBytes", "searchResponseBytes", "scrapeResponseBytes", "scrapeRequestCount", "searchRetryCount"):
                entry[key] += int(source_result.get(key) or 0)
            for key, value in (source_result.get("excludedCounts") or {}).items():
                entry.setdefault("excludedCounts", {}).setdefault(key, 0)
                entry["excludedCounts"][key] += int(value or 0)
            entry["duplicateCount"] += duplicate_count
            entry["itemCount"] = source_unique_counts[scope["id"]]
            entry["effectiveSearchLimit"] = source_result.get("effectiveSearchLimit")
            entry["targetAnchors"] = source_result.get("targetAnchors", anchors)
            entry["relevanceGate"] = source_result.get("relevanceGate")
            entry["queries"].append({"round": round_number + 1, "query": source_result.get("query", "")})
            detail = {"round": round_number + 1, "rawResultCount": int(source_result.get("rawResultCount") or 0), "retainedItemCount": new_count, "duplicateCount": duplicate_count}
            if remaining_target is not None:
                detail["remainingTarget"] = max(0, int(remaining_target))
            if stop_reason:
                detail["stopReason"] = stop_reason
            entry["roundDetails"].append(detail)
            return entry

        def target_items() -> list[dict[str, Any]]:
            return [item for item in items if not (ensure_target and _is_supplementary_item(item))]

        def target_count() -> int:
            return len(target_items()) if ensure_target else len(items)

        for round_number in range(max_collection_rounds):
            if target_count() >= max_items or request_count >= max_total_requests or (deadline_monotonic is not None and time.monotonic()>=deadline_monotonic):
                break
            rounds_completed = round_number + 1
            remaining = max_items - target_count()
            # Every selected scope gets a chance in each round. The request
            # limit is the remaining target divided across scopes, with a
            # minimum of one so an empty first round can recover on the next.
            for scope in scopes:
                if target_count() >= max_items or request_count >= max_total_requests or (deadline_monotonic is not None and time.monotonic()>=deadline_monotonic):
                    break
                role = str(scope.get("sourceRole") or "unclassified")
                quota = quotas.get(scope["id"], quotas.get(role))
                used = source_unique_counts[scope["id"]]
                if quota is not None and used >= quota:
                    continue
                fair_share = (remaining + len(scopes) - 1) // len(scopes)
                # Search pages are filtered for scope, date, relevance and
                # duplicates.  Automatic replenishment therefore oversamples
                # each share; interactive calls retain their old allocation.
                candidate_share = max(fair_share, min(100, fair_share * 2)) if ensure_target else fair_share
                limit = max(1, min(100, candidate_share, quota - used if quota is not None else candidate_share))
                query = self._query(question, keywords, scope=scope, since_days=since_days, now=now, round_number=round_number)
                plan = {"sourceId": scope["id"], "searchType": scope.get("searchType", "web"), "round": round_number + 1, "requestedLimit": limit, "query": query, "targetAnchors": anchors, "relevanceGate": "subject-expression-any" if anchors else "legacy-keywords", "status": "planned"}
                query_key = (scope["id"], re.sub(r"\s+", " ", query).strip().casefold())
                if query_key in seen_queries:
                    plan.update({"status": "skipped-duplicate-query", "requestedLimit": 0})
                    query_plan.append(plan)
                    continue
                seen_queries.add(query_key)
                query_plan.append(plan)
                budget["plannedRequests"] += 1
                budget["requestedLimitTotal"] += limit
                request_count += 1
                budget["executedRequests"] = request_count
                try:
                    result = self._search_scope(question, keywords, since_days=since_days, max_items=limit, scope=scope, selected_scopes=ids, now=now, round_number=round_number, scrape_cache=scrape_cache)
                    source_result = result["sourceResults"][0]
                    budget["retryRequests"] += int(source_result.get("searchRetryCount") or 0)
                    kept = []
                    duplicate_count = 0
                    for item in result["items"]:
                        canonical_url = urllib.parse.urlunsplit(urllib.parse.urlsplit(item["itemUri"])._replace(fragment=""))
                        if canonical_url in seen_urls or item["contentHash"] in seen_hashes:
                            duplicate_count += 1
                            original = seen_urls.get(canonical_url) or seen_hashes[item["contentHash"]]
                            matched_scopes = original["provenance"].setdefault("retrievedInScopes", [original["sourceId"]])
                            if scope["id"] not in matched_scopes:
                                matched_scopes.append(scope["id"])
                            media = original["provenance"].setdefault("mediaReferences", [])
                            for ref in item["provenance"].get("mediaReferences", []):
                                if ref not in media and len(media) < 24:
                                    media.append(ref)
                            if original["provenance"].get("contentKind") == "image-search-metadata":
                                old_hash = original["contentHash"]
                                original["contentHash"] = _image_metadata_hash(original["title"], original["itemUri"], media)
                                if not original["publishedAt"]:
                                    original["sourceVersion"] = original["contentHash"][:16]
                                if seen_hashes.get(old_hash) is original:
                                    del seen_hashes[old_hash]
                                seen_hashes[original["contentHash"]] = original
                            continue
                        seen_urls[canonical_url] = item
                        seen_hashes[item["contentHash"]] = item
                        kept.append(item)
                    source_unique_counts[scope["id"]] += len(kept)
                    items.extend(kept)
                    plan.update({"status": "target-reached" if target_count() >= max_items else "completed", "rawResultCount": int(source_result.get("rawResultCount") or 0), "retainedItemCount": len(kept), "excludedCounts": source_result.get("excludedCounts") or {}})
                    remaining = max_items - target_count()
                    aggregate(scope, source_result, round_number=round_number, requested_limit=limit, new_count=len(kept), duplicate_count=duplicate_count,
                              remaining_target=remaining, stop_reason="target-reached" if remaining <= 0 else None)
                except (OSINTCollectionError, ValueError, UnicodeError) as exc:
                    message = str(exc)[:500]
                    errors.append({"sourceId": scope["id"], "round": round_number + 1, "error": message})
                    error_result = {"rawResultCount": 0, "excludedCounts": {}}
                    aggregate(scope, error_result, round_number=round_number, requested_limit=limit, new_count=0, duplicate_count=0,
                              remaining_target=max(0, max_items - target_count()), stop_reason="source-error")["status"] = "partial"
                    plan.update({"status": "error", "error": message})

        for scope in scopes:
            if scope["id"] not in result_by_source:
                result_by_source[scope["id"]] = {"sourceId": scope["id"], "sourceName": scope["name"], "status": "skipped-target" if target_count() >= max_items else "skipped-budget", "requestedLimit": 0, "itemCount": 0, "domains": list(scope["domains"]), "searchRounds": 0, "rawResultCount": 0, "excludedCounts": {}}
        body_items = target_items()
        auxiliary_items = [item for item in items if ensure_target and _is_supplementary_item(item)]
        target_reached = target_count() >= max_items
        budget.update({"plannedRounds": max_collection_rounds, "searchRounds": rounds_completed, "actualRounds": rounds_completed, "targetReached": target_reached, "remainingItems": max(0, max_items - target_count()), "textItems": len(body_items), "auxiliaryItems": len(auxiliary_items), "supplementaryItems": len(auxiliary_items), "stopReason": "target-reached" if target_reached else "bounded-rounds-exhausted", "sourceQuotaCounts": {scope["id"]: source_unique_counts[scope["id"]] for scope in scopes}, "quotaGaps": {scope["id"]: max(0, quotas.get(scope["id"], quotas.get(str(scope.get("sourceRole") or "unclassified"), 0)) - source_unique_counts[scope["id"]]) for scope in scopes if scope["id"] in quotas or str(scope.get("sourceRole") or "unclassified") in quotas}})
        if deadline_monotonic is not None and time.monotonic()>=deadline_monotonic and not target_reached:
            budget['stopReason']='time-budget-exhausted'
        if ensure_target:
            # Preserve auxiliary image clues without letting them displace
            # requested正文材料 in the returned compatibility list.
            returned_items = body_items[:max_items] + auxiliary_items
        else:
            returned_items = items[:max_items]
        return {"items": returned_items, "errors": errors, "fetchedAt": _iso(now or datetime.now(timezone.utc)), "sourceResults": list(result_by_source.values()), "budget": budget, "queryPlan": query_plan, "collector": self.provider_id, "selectedScopes": ids, "targetReached": target_reached, "collectionRounds": rounds_completed}

    @staticmethod
    def _query(question: str, keywords: Sequence[str], *, scope: Mapping[str, Any], since_days: int, now: datetime | None = None, round_number: int = 0) -> str:
        research_question = str(question or "").strip()
        terms = [str(item).strip() for item in keywords if str(item).strip()]
        anchors = _subject_anchors(terms)
        relevance_terms = _relevance_terms(terms)
        if round_number > 0 and anchors:
            # Rotate the named subject and a context expression on every
            # replenishment round. The previous implementation permanently
            # reused anchors[:2] from round three onward, so a sparse search
            # repeatedly returned the same page set. Keep the subject anchor
            # in every query for the relevance gate, but vary technology,
            # country, test and procurement terms around it.
            context_terms = [term for term in relevance_terms if term.casefold() not in {anchor.casefold() for anchor in anchors}]
            anchor = anchors[(round_number - 1) % len(anchors)]
            if context_terms:
                context = context_terms[((round_number - 1) // max(1, len(anchors))) % len(context_terms)]
                query = f'("{anchor}") AND ({context})'
            else:
                query = f'("{anchor}")'
        elif round_number > 0 and terms:
            # Legacy keyword lists without a quoted subject also rotate one
            # expression per round instead of issuing the same request.
            query = f'({terms[(round_number - 1) % len(terms)]})'
        else:
            query = research_question
        if scope.get("searchType") == "images" and anchors:
            # Image captions rarely name software capabilities. Search the
            # explicit subject expression, but rotate the named anchor and
            # context on replenishment rounds. The old branch overwrote the
            # rotating query with anchors[:2] on every round.
            if round_number == 0:
                query = f'"{anchors[0]}"' if len(anchors) == 1 else " OR ".join(f'"{anchor}"' for anchor in anchors[:2])
            else:
                image_anchor = anchors[(round_number - 1) % len(anchors)]
                image_context = [term for term in relevance_terms if term.casefold() not in {a.casefold() for a in anchors}]
                query = f'"{image_anchor}"'
                if image_context:
                    query += f' "{image_context[((round_number - 1) // max(1, len(anchors))) % len(image_context)]}"'
        elif terms and round_number == 0:
            # Legacy requests lack an explicit subject expression, so retain
            # their supplied topic rather than dropping it from image search.
            query = " ".join(terms[:12]) if re.search(r"[\u4e00-\u9fff]", research_question) else f"{research_question} {' '.join(terms[:12])}".strip()
        query = "(" + query[:2400] + ") " + str(scope.get("queryTerms") or "(defense OR military OR equipment)")
        query += " (" + " OR ".join(f"site:{domain}" for domain in scope["domains"]) + ")"
        if since_days > 0:
            anchor = now or datetime.now(timezone.utc)
            query += f" after:{(anchor - timedelta(days=since_days)).date().isoformat()}"
        if len(query) > 4000:
            raise OSINTCollectionError("scoped query exceeds 4000 characters")
        return query

    def _endpoint(self, image_search: bool, operation: str = "search") -> str:
        if operation not in {"search", "scrape"}:
            raise OSINTCollectionError("unsupported Firecrawl operation")
        endpoint = f"{self.base_url}/{operation}"
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise OSINTCollectionError("Firecrawl endpoint must be HTTPS without credentials")
        if image_search:
            # Image discovery uses the documented v2 sources API. Never
            # rewrite a self-hosted or alternate provider's endpoint.
            if parsed.hostname.casefold() != "api.firecrawl.dev" or parsed.port not in (None, 443) or parsed.path not in {"/v1/search", "/v2/search"}:
                raise OSINTCollectionError("public image discovery requires the official Firecrawl v1 or v2 endpoint")
            endpoint = urllib.parse.urlunsplit(parsed._replace(path="/v2/search"))
        return endpoint

    def _post_json(self, endpoint: str, payload: Mapping[str, Any], *, operation: str) -> tuple[dict[str, Any], int]:
        request = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": _USER_AGENT}, method="POST")
        opener = self.opener or urllib.request.build_opener(_NoSearchRedirect())
        for attempt in range(_MAX_HTTP_RETRIES + 1):
            deadline=_FIRECRAWL_DEADLINE.get()
            remaining=deadline-time.monotonic() if deadline is not None else self.timeout
            if remaining<=0:raise OSINTCollectionError('Firecrawl collection time budget exhausted')
            timeout=min(self.timeout,remaining)
            try:
                response = opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)
                with contextlib.closing(response):
                    raw = response.read(_FIRECRAWL_MAX_RESPONSE + 1)
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                # A 429/5xx response is safe to retry; other HTTP errors are
                # deterministic request failures and should be surfaced.
                if status in {408, 425, 429, 500, 502, 503, 504} and attempt < _MAX_HTTP_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise OSINTCollectionError(f"Firecrawl HTTP {status}; {operation} failed without switching providers") from None
            except TimeoutError:
                if attempt < _MAX_HTTP_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise OSINTCollectionError(f"Firecrawl request timed out after {self.timeout:g}s; {operation} was not switched to RSS") from None
            except urllib.error.URLError as exc:
                if attempt < _MAX_HTTP_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if isinstance(getattr(exc, "reason", None), TimeoutError):
                    raise OSINTCollectionError(f"Firecrawl request timed out after {self.timeout:g}s; {operation} was not switched to RSS") from None
                raise OSINTCollectionError(f"Firecrawl connection failed; {operation} was not switched to RSS") from None
            except OSError:
                if attempt < _MAX_HTTP_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise OSINTCollectionError(f"Firecrawl connection failed; {operation} was not switched to RSS") from None
        if len(raw) > _FIRECRAWL_MAX_RESPONSE:
            raise OSINTCollectionError(f"Firecrawl {operation} response exceeds byte cap")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OSINTCollectionError(f"Firecrawl {operation} returned invalid JSON") from None
        if not isinstance(decoded, dict) or decoded.get("success") is False:
            raise OSINTCollectionError(f"Firecrawl {operation} returned an error")
        return decoded, len(raw)

    def _search_candidates(self, endpoint: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int, int, int]:
        """Return bounded discovery results, reducing the limit on oversized responses."""
        requested = int(payload["limit"])
        attempt_limit = requested
        retries = 0
        while True:
            attempt = {**payload, "limit": attempt_limit}
            try:
                decoded, response_bytes = self._post_json(endpoint, attempt, operation="search")
                return decoded, response_bytes, attempt_limit, retries
            except OSINTCollectionError as exc:
                if "response exceeds byte cap" not in str(exc) or retries >= _MAX_SEARCH_RETRIES or attempt_limit <= 2:
                    raise
                next_limit = max(2, attempt_limit // 2)
                if next_limit == attempt_limit:
                    raise
                attempt_limit = next_limit
                retries += 1

    def _scrape_candidate(self, url: str) -> tuple[dict[str, Any], int]:
        endpoint = self._endpoint(False, "scrape")
        decoded, response_bytes = self._post_json(endpoint, {"url": url, "formats": ["markdown"], "onlyMainContent": True}, operation="scrape")
        data = decoded.get("data")
        if not isinstance(data, Mapping):
            data = decoded
        return dict(data), response_bytes

    def _search_scope(self, question: str, keywords: Sequence[str], *, since_days: int, max_items: int, scope: Mapping[str, Any], selected_scopes: Sequence[str], now: datetime | None = None, round_number: int = 0, scrape_cache: dict | None = None) -> dict[str, Any]:
        with trace_call(provider="firecrawl",operation="search-images" if scope.get("searchType") == "images" else "search-source",details={"sourceId":scope["id"],"sourceScopes":list(selected_scopes),"requestedLimit":max_items,"round":round_number+1,"domains":list(scope["domains"]),"sinceDays":since_days,"timeoutSeconds":self.timeout,"method":"POST"}) as trace:
            result=self._search_scope_request(question,keywords,since_days=since_days,max_items=max_items,scope=scope,selected_scopes=selected_scopes,now=now,round_number=round_number,scrape_cache=scrape_cache)
            source_result=result["sourceResults"][0]
            trace.update({"responseBytes":source_result["responseBytes"],"rawResultCount":source_result["rawResultCount"],"retainedItemCount":source_result["itemCount"],"excludedCounts":source_result["excludedCounts"]})
            return result

    def _search_scope_request(self, question: str, keywords: Sequence[str], *, since_days: int, max_items: int, scope: Mapping[str, Any], selected_scopes: Sequence[str], now: datetime | None = None, round_number: int = 0, scrape_cache: dict | None = None) -> dict[str, Any]:
        research_question = str(question or "").strip()
        if not research_question or not 1 <= max_items <= 100 or not 0 <= since_days <= 3650:
            raise OSINTCollectionError("Firecrawl requires a nonempty query and bounded search limits")
        terms = [str(item).strip() for item in keywords if str(item).strip()]
        target_anchors = _subject_anchors(terms)
        # Broader later queries must retain the same named-object gate.
        # Context words such as "autonomy" cannot substitute for that object.
        relevance_terms = target_anchors
        query = self._query(question, keywords, scope=scope, since_days=since_days, now=now, round_number=round_number)
        image_search = scope.get("searchType") == "images"
        payload = {"query": query, "limit": min(max_items, 100)}
        if image_search:
            payload["sources"] = ["images"]
        endpoint = self._endpoint(image_search)
        decoded, search_response_bytes, effective_limit, search_retries = self._search_candidates(endpoint, payload)
        if image_search:
            data = decoded.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("images"), list):
                raise OSINTCollectionError("Firecrawl image search response must contain data.images")
            rows = data["images"]
        else:
            rows = decoded.get("data") or decoded.get("results") or []
            if isinstance(rows, dict):
                rows = rows.get("web", [])
        if not isinstance(rows, list):
            raise OSINTCollectionError("Firecrawl search response has no result list")
        fetched = _iso(now or datetime.now(timezone.utc))
        items: list[dict[str, Any]] = []
        excluded = {"outsideScope": 0, "invalidUrl": 0, "outsideTimeWindow": 0, "emptyBody": 0, "unrelatedSubject": 0, "genericSummary": 0, "invalidImageUrl": 0, "scrapeFailed": 0, "scrapeBudget": 0}
        scrape_count = 0
        scrape_response_bytes = 0
        cache=scrape_cache if scrape_cache is not None else {}
        def scrape_page(url):
            nonlocal scrape_count
            key=urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(fragment=''))
            if key in cache:return cache[key],0
            scrape_count+=1
            page,response_bytes=self._scrape_candidate(url)
            cache[key]=page
            return page,response_bytes
        for row in rows[:effective_limit]:
            if not isinstance(row, Mapping):
                continue
            url = self._result_url(row)
            if not self._valid_result_url(url):
                excluded["invalidUrl"] += 1
                continue
            if not domain_in_scope(url, scope["domains"]):
                excluded["outsideScope"] += 1
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            title = strip_html(row.get("title") or metadata.get("title") or url, max_chars=500)
            content_kind = "image-search-metadata" if image_search else "markdown" if row.get("markdown") else "content" if row.get("content") else "search-snippet"
            # A discovery caption is metadata, never a scraped article body
            # or a measurement inferred from an unexamined picture.
            discovery_body = "" if image_search else str(row.get("markdown") or row.get("content") or row.get("description") or "").strip()
            published_at, publication_date_evidence = _publication_date({**row, **metadata})
            if published_at and since_days and _parse_datetime(published_at) < (now or datetime.now(timezone.utc)) - timedelta(days=since_days):
                excluded["outsideTimeWindow"] += 1
                continue
            source_text = f"{title} {discovery_body}"
            matched_anchors = [anchor for anchor in target_anchors if _keyword_matches(source_text, anchor)]
            # Broad annual reports and tables of contents often consume the
            # entire paid quota. Require an explicit subject in the saved
            # body of such documents, even if the search title names it.
            if target_anchors and not image_search and _generic_summary(title, discovery_body) and not any(_keyword_matches(discovery_body, anchor) for anchor in target_anchors):
                excluded["genericSummary"] += 1
                continue
            matched_relevance = [term for term in relevance_terms if _keyword_matches(source_text, term)]
            if target_anchors and not matched_relevance:
                excluded["unrelatedSubject"] += 1
                continue
            raw_body = discovery_body
            if not image_search and not row.get("markdown") and not row.get("content"):
                if scrape_count >= _MAX_SCRAPES_PER_SCOPE and url not in cache:
                    excluded["scrapeBudget"] += 1
                    continue
                try:
                    scraped, scraped_bytes = scrape_page(url)
                    scrape_response_bytes += scraped_bytes
                    scraped_metadata = scraped.get("metadata") if isinstance(scraped.get("metadata"), Mapping) else {}
                    raw_body = str(scraped.get("markdown") or scraped.get("content") or "").strip()
                    content_kind = "markdown" if scraped.get("markdown") else "content"
                    if not published_at:
                        published_at, publication_date_evidence = _publication_date(scraped_metadata)
                except OSINTCollectionError:
                    excluded["scrapeFailed"] += 1
                    # Keep a bounded search snippet when a page/PDF scrape is
                    # unavailable. It remains unverified and is never treated
                    # as a full source quote, but it is useful for review and
                    # prevents a transient scrape outage from erasing search
                    # coverage entirely.
                    if not discovery_body:
                        continue
                    raw_body = discovery_body
                    content_kind = "search-snippet"
                if published_at and since_days and _parse_datetime(published_at) < (now or datetime.now(timezone.utc)) - timedelta(days=since_days):
                    excluded["outsideTimeWindow"] += 1
                    continue
            body = raw_body[:20_000]
            discovery_url=None
            # Manufacturer catalogues identify an aircraft but rarely give its
            # parameters. Follow only an exact subject slug on the same allowed
            # publisher; do not mistake neighboring variants for the target.
            if not image_search and target_anchors and re.search(r'^(?:Remotely Piloted Aircraft|Products|Aircraft Portfolio|Equipment Catalogue)(?:\s*[|–-].*)?$',title,re.I):
                slug=lambda s:re.sub(r'[^a-z0-9]+','-',s.casefold()).strip('-')
                slugs={slug(a) for a in target_anchors}
                detail_urls=list(dict.fromkeys(candidate for candidate in re.findall(r'\]\((https?://[^\s)]+)\)',raw_body) if self._valid_result_url(candidate) and publisher_domain(candidate)==publisher_domain(url) and domain_in_scope(candidate,scope['domains']) and urllib.parse.urlsplit(candidate).path.rstrip('/').rsplit('/',1)[-1].casefold() in slugs))
                if not detail_urls or scrape_count>=_MAX_SCRAPES_PER_SCOPE:
                    excluded['genericSummary']+=1
                    continue
                detail_url=detail_urls[0]
                try:
                    scraped,detail_bytes=scrape_page(detail_url)
                    scrape_response_bytes+=detail_bytes
                    discovery_url=url;url=detail_url
                    raw_body=str(scraped.get('markdown') or scraped.get('content') or '')
                    body=raw_body[:20_000]
                    metadata= scraped.get('metadata') or {}
                    title=strip_html(metadata.get('title') or urllib.parse.urlsplit(url).path.rsplit('/',1)[-1],max_chars=500)
                    published_at, publication_date_evidence = _publication_date(metadata)
                    content_kind='markdown' if scraped.get('markdown') else 'content'
                except OSINTCollectionError:
                    excluded['scrapeFailed']+=1
                    continue
            if not published_at and not image_search:
                published_at, publication_date_evidence = _publication_date({}, body=body, title=title)
            if published_at and since_days and _parse_datetime(published_at) < (now or datetime.now(timezone.utc)) - timedelta(days=since_days):
                excluded["outsideTimeWindow"] += 1
                continue
            if not body and not image_search:
                excluded["emptyBody"] += 1
                continue
            full_source_text = f"{title} {body}"
            matched_anchors = [anchor for anchor in target_anchors if _keyword_matches(full_source_text, anchor)]
            matched_relevance = [term for term in relevance_terms if _keyword_matches(full_source_text, term)]
            if target_anchors and not matched_relevance:
                excluded["unrelatedSubject"] += 1
                continue
            matched = [keyword for keyword in terms if _keyword_matches(full_source_text, keyword)]
            media_references = []
            if image_search:
                image_url = str(row.get("imageUrl") or "").strip()
                if not self._valid_result_url(image_url):
                    excluded["invalidImageUrl"] += 1
                    continue
                media_references = [_media_reference(image_url, url, title)]
                digest = _image_metadata_hash(title, url, media_references)
            else:
                media_references = _linked_images(raw_body, url, self._valid_result_url)
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            classification = {"sourceCategory": scope["category"], "sourceRole": scope["sourceRole"], "contentNature": scope["contentNature"], "sourceReliability": scope["sourceReliability"], "publisherDomain": publisher_domain(url), "verificationStatus": "unverified", "classificationBasis": "publisher-scope", "documentTypeVerified": False}
            items.append({"title": title or "未命名公开材料", "sourceId": scope["id"], "sourceName": publisher_domain(url), "sourceUri": url, "itemUri": url, "sourceVersion": published_at or digest[:16], "publishedAt": published_at, "category": scope["category"], **classification, "body": body, "contentHash": digest, "matchedKeywords": matched, "reviewState": "pending-review", "provenance": {"collector": self.provider_id, "query": query, "researchQuestion": research_question, "sourceIds": list(selected_scopes), "selectedScopes": list(selected_scopes), "selectedDomains": list(scope["domains"]), **classification, "fetchedAt": fetched, "contentKind": content_kind, "retrievalKind": "search-discovery-only" if image_search else "public-web-material", "targetAnchors": target_anchors, "matchedTargetAnchors": matched_anchors, "relevanceGate": "subject-expression-any" if target_anchors else "legacy-keywords", "mediaReferences": media_references, "truncated": len(raw_body)>len(body), "publicationDatePresent": bool(published_at), "publicationDateVerified": False}})
            if publication_date_evidence:
                items[-1]["provenance"]["publicationDateEvidence"] = publication_date_evidence
            if discovery_url:items[-1]['provenance']['discoveredFrom']=discovery_url
        return {"items": items, "errors": [], "fetchedAt": fetched, "sourceResults": [{"sourceId": scope["id"], "sourceName": scope["name"], "sourceRole": scope["sourceRole"], "collector": self.provider_id, "status": "ok", "itemCount": len(items), "requestedLimit": max_items, "effectiveSearchLimit": effective_limit, "query": query, "searchType": "images" if image_search else "web", "targetAnchors": target_anchors, "relevanceGate": "subject-expression-any" if target_anchors else "legacy-keywords", "domains": list(scope["domains"]), "responseBytes": search_response_bytes + scrape_response_bytes, "searchResponseBytes": search_response_bytes, "scrapeResponseBytes": scrape_response_bytes, "searchRetryCount": search_retries, "scrapeRequestCount": scrape_count, "rawResultCount": len(rows), "excludedCounts": excluded}]}


def _subject_anchors(keywords: Sequence[str]) -> list[str]:
    """First expression holds quoted subjects; later expressions are context.

    This keeps legacy unquoted keyword lists working and prevents a generic
    quoted phrase such as "human supervision" from satisfying a named
    equipment query merely because it appears in a broad source report.
    """
    anchors = []
    first = str(keywords[0]).strip() if keywords else ""
    if not first or not re.search(r'["“”]|\bOR\b', first, re.IGNORECASE):
        return []
    for value in re.split(r'\s+OR\s+', first, flags=re.IGNORECASE):
        candidate = value.strip().strip("()").strip().strip("'\"“”‘’").strip()
        if candidate and candidate.casefold() not in {"and", "or", "not"} and len(candidate) <= _MAX_KEYWORD_CHARS:
            anchors.append(candidate)
    return list(dict.fromkeys(anchors))[:16]


def _relevance_terms(keywords: Sequence[str]) -> list[str]:
    """Expand grouped query expressions for bounded later search rounds."""
    terms: list[str] = []
    for raw in keywords:
        value = str(raw).strip()
        for candidate in re.split(r"\s+OR\s+", value, flags=re.IGNORECASE):
            candidate = candidate.strip().strip("()").strip().strip("'\"“”‘’").strip()
            if candidate and candidate.casefold() not in {"and", "or", "not"} and len(candidate) <= _MAX_KEYWORD_CHARS:
                terms.append(candidate)
    return list(dict.fromkeys(terms))[:32]


def _generic_summary(title: str, body: str) -> bool:
    return bool(re.search(r"\b(?:annual report|year in review|table of contents)\b|年度报告|年度总结|^目录(?:\s|$)", f"{title}\n{body[:1200]}", re.IGNORECASE))


def _media_reference(image_url: str, source_url: str, caption: str) -> dict[str, Any]:
    return {"imageUrl": image_url, "sourceUrl": source_url, "caption": caption[:500], "mediaKind": "unclassified-image", "captureDate": None, "verificationStatus": "unverified"}


def _image_metadata_hash(title: str, source_url: str, media_references: list[dict[str, Any]]) -> str:
    metadata = json.dumps({"title": title, "sourceUrl": source_url, "mediaReferences": media_references}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(metadata.encode("utf-8")).hexdigest()


class _HTMLImageExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() == "img":
            attributes = dict(attrs)
            self.images.append((attributes.get("src") or "", attributes.get("alt") or ""))


def _linked_images(content: str, source_url: str, valid_url: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Record actual links without fetching, viewing or classifying pixels."""
    candidates = [(match.group(2), match.group(1)) for match in re.finditer(r'!\[([^\]\n]{0,500})\]\(<?([^\s<>]+)>?(?:\s+"[^"\n]*")?\)', content)]
    parser = _HTMLImageExtractor()
    try:
        parser.feed(content)
        parser.close()
        candidates.extend(parser.images)
    except (ValueError, TypeError):
        pass
    refs, seen = [], set()
    for raw_url, raw_caption in candidates:
        if not raw_url or len(raw_url) > 2048:
            continue
        image_url = urllib.parse.urljoin(source_url, html.unescape(raw_url))
        if image_url in seen or not valid_url(image_url):
            continue
        filename = urllib.parse.urlsplit(image_url).path.rsplit("/", 1)[-1]
        if re.search(r"(?:^|[-_.])(?:logo|favicon|sprite|social-icon|menu-icon|nav-icon)(?:[-_.]|$)", filename, re.IGNORECASE):
            continue
        seen.add(image_url)
        refs.append(_media_reference(image_url, source_url, strip_html(raw_caption, max_chars=500)))
        if len(refs) >= 24:
            break
    return refs


class _TextExtractor(HTMLParser):
    """Extract visible text from an RSS/Atom HTML fragment.

    HTMLParser is used instead of regex replacement so malformed markup and
    comments cannot accidentally make tags part of evidence text.  Script and
    style contents are ignored because they are not human-readable evidence.
    """

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(value: Any, *, max_chars: int = 20_000) -> str:
    """Return bounded, whitespace-normalized human-readable feed text."""

    if value is None:
        return ""
    text = str(value)
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
        text = " ".join(parser.parts)
    except Exception:
        # Feed descriptions are untrusted data.  If an unusual fragment
        # defeats HTMLParser, retaining text with tags removed is safer than
        # failing the whole source.
        text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text[:max_chars]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(node):
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    wanted = {name.casefold() for name in names}
    for child in list(node):
        if _local_name(child.tag) in wanted:
            return child
    return None


_MONTH_NAMES = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_PUBLICATION_CALENDAR_DATE = rf"(?:\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}|\d{{4}}年\d{{1,2}}月\d{{1,2}}日|{_MONTH_NAMES}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH_NAMES}\.?,?\s+\d{{4}})"
_PUBLICATION_DATE_TAIL = r"(?:\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?\s*(?:[A-Z]{2,5}|[+-]\d{2}:?\d{2})?)?(?:\s*\d+\s+min(?:ute)?s?\s+read)?"
_MONTH_NUMBERS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _parse_publication_datetime(value: Any) -> datetime | None:
    """Parse a publisher date only when the source supplies an explicit year."""
    raw = str(value or "").strip()
    if not re.search(r"(?<!\d)\d{4}(?!\d)", raw):
        return None
    parsed = _parse_datetime(raw)
    if parsed:
        return parsed
    match = re.match(rf"^({_PUBLICATION_CALENDAR_DATE})(.*)$", raw, re.IGNORECASE)
    if not match:
        return None
    # Body labels often retain a time, bullet or read-time after the date.
    # Do not accept a full narrative sentence as a publication date.
    tail = match.group(2).strip(" ,•|\\")
    if tail and not re.fullmatch(_PUBLICATION_DATE_TAIL, tail, re.IGNORECASE):
        return None
    date = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", match.group(1), flags=re.IGNORECASE)
    try:
        numeric = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", date)
        if numeric:
            year, month, day = map(int, numeric.groups())
        else:
            tokens = re.sub(r"[.,]", "", date).split()
            if tokens[0].isdigit():
                day, month, year = int(tokens[0]), _MONTH_NUMBERS[tokens[1][:3].casefold()], int(tokens[2])
            else:
                month, day, year = _MONTH_NUMBERS[tokens[0][:3].casefold()], int(tokens[1]), int(tokens[2])
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (KeyError, ValueError, IndexError, OverflowError):
        return None


def _publication_date(metadata: Mapping[str, Any], *, body: str = "", title: str = "") -> tuple[str | None, dict[str, Any]]:
    """Recover explicit publisher metadata or an article header date.

    Never substitute fetch time, URL/image dates, event dates in prose, or
    dates under related-news sections. Body recovery keeps its exact locator.
    """
    for field in ("publishedTime", "publishedAt", "datePublished", "publishedDate", "published_at", "article:published_time", "publicationDate", "pubdate", "dc.date.issued", "dcterms.issued"):
        values = metadata.get(field)
        for raw in values if isinstance(values, list) else [values]:
            parsed = _parse_publication_datetime(raw)
            if parsed:
                return _iso(parsed), {"basis": "publisher-metadata", "field": field, "rawValue": str(raw)}
    if not body:
        return None, {}
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*#{1,6}\s+(?:Related(?:\s+(?:Stories|Articles))?|More\s+news\s+articles|Discover\s+more)\b", line, re.IGNORECASE):
            lines = lines[:index]
            break

    def found(raw: str, start: int, end: int, basis: str):
        candidate = re.match(rf"^({_PUBLICATION_CALENDAR_DATE})(.*)$", raw, re.IGNORECASE)
        if not candidate:
            return None
        tail = candidate.group(2).strip(" ,•|\\")
        if tail and not re.fullmatch(_PUBLICATION_DATE_TAIL, tail, re.IGNORECASE):
            return None
        # Preserve only the printed calendar day; do not invent a timezone
        # for a visible header time. The full original label remains quoted.
        parsed = _parse_publication_datetime(candidate.group(1))
        if not parsed:
            return None
        return _iso(parsed), {"basis": basis, "rawValue": raw, "precision": "day", "lineStart": start + 1, "lineEnd": end + 1, "quote": "\n".join(lines[start:end + 1])}

    # Explicit labels outrank a nearby deadline, photo date or document version.
    for index, line in enumerate(lines):
        clean = line.strip(" #*\t")
        label = re.fullmatch(r"(?:Published(?:\s+(?:Date|on))?|Date\s+published|Publication\s+Date|发布日期|发布时间)\s*[:：]?\s*(.*)", clean, re.IGNORECASE)
        if label:
            value = label.group(1).strip()
            end = index
            if not value:
                end = next((n for n in range(index + 1, min(index + 5, len(lines))) if lines[n].strip()), index)
                value = lines[end].strip(" #*\t")
            result = found(value, index, end, "published-date-label")
            if result:
                return result
    normalize_title = lambda text: re.sub(r"\W+", " ", text.casefold()).strip()
    title_key = normalize_title(re.sub(r"(?:\.\.\.|…)\s*$", "", title))[:70]
    headings = [index for index, line in enumerate(lines) if re.match(r"^\s*#{1,6}\s+", line) and title_key and title_key in normalize_title(line)]
    # Only a narrow region around this article's own heading is a plausible
    # byline area. Unanchored standalone dates may be references or events.
    header_indices = set()
    for heading in headings:
        header_indices.update(range(max(0, heading - 10), min(len(lines), heading + 11)))
    for index in sorted(header_indices):
        previous = next((line.strip(" #*\t") for line in reversed(lines[:index]) if line.strip()), "")
        if re.search(r"(?:\b(?:date|time|deadline|due|updated|references|photo information)\b|拍摄|发生|截止|更新|参考)", previous, re.IGNORECASE):
            continue
        result = found(lines[index].strip(" #*\t"), index, index, "article-header-date")
        if result:
            return result
    # A wire-service dateline is explicitly separated from the article prose.
    for index, line in enumerate(lines[:100]):
        dateline = re.match(rf"^\*\*[A-Z][A-Z ,.()\-]{{1,80}}\s+[–—-]\s+({_PUBLICATION_CALENDAR_DATE})\*\*\s+[–—-]", line, re.IGNORECASE)
        if dateline:
            result = found(dateline.group(1), index, index, "press-release-dateline")
            if result:
                return result
    return None, {}


def _parse_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    try:
        iso = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _canonical_keyword(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _keyword_matches(text: str, keyword: str) -> bool:
    """Match Latin terms as words so ``AI`` does not match ``said``."""
    haystack = _canonical_keyword(text)
    term = _canonical_keyword(keyword)
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 _+./-]*", term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", haystack) is not None
    return term in haystack


def _normalize_keywords(keywords: Iterable[Any] | None) -> list[str]:
    if keywords is None:
        return []
    if isinstance(keywords, (str, bytes)):
        keywords = [keywords]
    result: list[str] = []
    for value in keywords:
        item = _canonical_keyword(value)
        if not item or len(item) > _MAX_KEYWORD_CHARS or item in result:
            continue
        result.append(item)
        if len(result) >= _MAX_KEYWORDS:
            break
    return result


def _is_public_ip(host: str) -> bool:
    """Reject all loopback/private/link-local/reserved DNS answers."""

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _validate_public_url(url: str, allowed_hosts: set[str], *, resolve_host: bool = True) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or len(url) > _MAX_FEED_URL_LENGTH:
        raise OSINTCollectionError("source URL is invalid or too long")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").casefold()
    if parsed.scheme.casefold() != "https" or not host:
        raise OSINTCollectionError("only HTTPS feed URLs are allowed")
    if parsed.username or parsed.password:
        raise OSINTCollectionError("source URL credentials are not allowed")
    if parsed.port not in (None, 443):
        raise OSINTCollectionError("non-standard source ports are not allowed")
    if host not in allowed_hosts:
        raise OSINTCollectionError("redirect target is outside the curated source host")
    # A custom opener is only intended for deterministic tests/in-process
    # adapters.  It has no DNS context, so still reject literal private IPs
    # while leaving resolution to that adapter.  The production urllib path
    # always resolves every host before opening it.
    if resolve_host:
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise OSINTCollectionError(f"source host could not be resolved: {host}") from exc
        if not addresses or not all(_is_public_ip(address) for address in addresses):
            raise OSINTCollectionError("source host resolves to a private or reserved address")
    elif not _is_public_ip(host) and re.fullmatch(r"[0-9a-fA-F:.]+", host):
        raise OSINTCollectionError("source host is private or reserved")
    elif host in {"localhost", "localhost.localdomain", "ip6-localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        raise OSINTCollectionError("local source hosts are not allowed")
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        _validate_public_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _feed_entries(root: ET.Element) -> tuple[list[ET.Element], str]:
    root_name = _local_name(root.tag)
    if root_name == "rss" or root_name == "rdf":
        channel = _child(root, "channel")
        if channel is None:
            channel = root
        return [child for child in list(channel) if _local_name(child.tag) == "item"], _child_text(channel, "lastBuildDate", "updated", "pubDate")
    if root_name == "feed":
        return [child for child in list(root) if _local_name(child.tag) == "entry"], _child_text(root, "updated", "published")
    raise OSINTCollectionError("feed root must be RSS or Atom")


def _entry_link(entry: ET.Element) -> str:
    # We retain this as metadata only; the collector never follows it.
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").casefold()
        if href and rel in {"alternate", ""}:
            return href
        if href:
            return href
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return _child_text(entry, "link")


def _parse_feed(data: bytes, source: Mapping[str, str], since: datetime | None, keywords: list[str], max_items: int) -> list[dict[str, Any]]:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise OSINTCollectionError("DOCTYPE and ENTITY declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise OSINTCollectionError(f"invalid RSS/Atom XML: {exc}") from exc
    entries, feed_version = _feed_entries(root)
    output: list[dict[str, Any]] = []
    for entry in entries:
        title = strip_html(_child_text(entry, "title"), max_chars=500)
        description = _child_text(entry, "description", "summary", "content", "encoded")
        # ``content:encoded`` has local name encoded; only an explicitly
        # namespaced element is accepted to avoid confusing arbitrary fields.
        for child in list(entry):
            if child.tag == f"{{{_NAMESPACE_CONTENT}}}encoded":
                description = "".join(child.itertext())
                break
        body = strip_html(description)
        date_raw = _child_text(entry, "pubDate", "published", "updated", "date")
        published = _parse_datetime(date_raw)
        if since and published and published < since:
            continue
        guid = _child_text(entry, "guid", "id") or _entry_link(entry) or feed_version
        search_text = f"{title} {body}".casefold()
        matched = [keyword for keyword in keywords if _keyword_matches(search_text, keyword)]
        if keywords and not matched:
            continue
        content = body or title
        item: dict[str, Any] = {
            "title": title or "(untitled)",
            "sourceId": source["id"],
            "sourceName": source["name"],
            "sourceUri": source["url"],
            "category": source.get("category"),
            "sourceVersion": guid[:500] if guid else None,
            "publishedAt": _iso(published),
            "body": content,
            "contentHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "matchedKeywords": matched,
            "itemUri": _entry_link(entry) or None,
            "provenance": {"collector": "bounded-rss-atom", "sourceCategory": source.get("category"), "sourceRole": source.get("sourceRole", "unknown"), "contentNature": source.get("contentNature", "unknown"), "sourceReliability": source.get("sourceReliability", "unknown"), "publisherDomain": publisher_domain(_entry_link(entry) or source["url"]), "selectedScopes": [source["id"]], "classificationBasis": "publisher-scope", "verificationStatus": "unverified", "documentTypeVerified": False, "publicationDatePresent": bool(published), "publicationDateVerified": False},
        }
        output.append(item)
        if len(output) >= max_items:
            break
    return output


class OSINTCollector:
    """Fetch and normalize bounded public RSS/Atom source data."""

    def __init__(
        self,
        sources: Sequence[Mapping[str, str]] | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        opener: Callable[..., Any] | None = None,
        now: Callable[[], datetime] | None = None,
        search_provider: FirecrawlSearchProvider | None = None,
    ) -> None:
        self.sources = tuple(dict(item) for item in (sources or SOURCES))
        self._by_id = {item["id"]: item for item in self.sources}
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = max(16_384, min(int(max_bytes), 10_000_000))
        self.opener = opener
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.search_provider = search_provider or (FirecrawlSearchProvider(opener=opener) if os.environ.get("CRK_SEARCH_PROVIDER", "").casefold() == "firecrawl" else None)

    def _fetch(self, source: Mapping[str, str]) -> tuple[bytes, str]:
        with trace_call(provider="rss",operation="fetch-feed",details={"sourceId":source["id"],"domains":[publisher_domain(source["url"])],"timeoutSeconds":self.timeout,"method":"GET"}) as trace:
            data,final_url=self._fetch_request(source)
            trace.update({"responseBytes":len(data)})
            return data,final_url

    def _fetch_request(self, source: Mapping[str, str]) -> tuple[bytes, str]:
        initial = urllib.parse.urlsplit(source["url"])
        allowed_hosts = {(initial.hostname or "").rstrip(".").casefold()}
        _validate_public_url(source["url"], allowed_hosts, resolve_host=self.opener is None)
        request = urllib.request.Request(source["url"], headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9"})
        if self.opener is not None:
            response = self.opener(request, timeout=self.timeout)
        else:
            safe = urllib.request.build_opener(_SafeRedirectHandler(allowed_hosts))
            response = safe.open(request, timeout=self.timeout)
        with contextlib.closing(response):
            final_url = getattr(response, "geturl", lambda: source["url"])()
            _validate_public_url(final_url, allowed_hosts, resolve_host=self.opener is None)
            length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
            if length and int(length) > self.max_bytes:
                raise OSINTCollectionError("feed exceeds byte cap")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(65_536, self.max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_bytes:
                    raise OSINTCollectionError("feed exceeds byte cap")
                chunks.append(chunk)
            return b"".join(chunks), final_url

    def collect(
        self,
        source_ids: Iterable[str] | None = None,
        keywords: Iterable[Any] | None = None,
        *,
        since_days: int = 30,
        max_items: int = 20,
        question: str | None = None,
        ensure_target: bool = False,
        source_quotas: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        if since_days < 0 or since_days > 3650:
            raise OSINTCollectionError("since_days must be between 0 and 3650")
        if max_items < 1 or max_items > _MAX_ITEMS:
            raise OSINTCollectionError(f"max_items must be between 1 and {_MAX_ITEMS}")
        selected = (list(DEFAULT_SCOPE_IDS) if self.search_provider else list(self._by_id)) if source_ids is None else list(source_ids)
        normalized_keywords = _normalize_keywords(keywords)
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        # A selected search scope explicitly chooses Firecrawl. Explicit RSS
        # mode keeps feed IDs as feeds even when an adapter was injected.
        scoped_search = any(source_id in SEARCH_SCOPE_BY_ID or source_id == "firecrawl-search" for source_id in selected)
        search_provider = self.search_provider
        if scoped_search and search_provider is None:
            search_provider = FirecrawlSearchProvider(opener=self.opener)
        if scoped_search or (search_provider is not None and os.environ.get("CRK_SEARCH_PROVIDER", "").casefold() != "rss"):
            return search_provider.search(question or " ".join(normalized_keywords), normalized_keywords, since_days=since_days, max_items=max_items, source_ids=[str(item) for item in selected], now=now, ensure_target=ensure_target, source_quotas=source_quotas)
        selected_sources: list[Mapping[str, str]] = []
        errors: list[dict[str, str]] = []
        seen: set[str] = set()
        for source_id in selected:
            if source_id in seen:
                continue
            seen.add(source_id)
            source = self._by_id.get(str(source_id))
            if source is None:
                errors.append({"sourceId": str(source_id), "error": "source ID is not in the curated allowlist"})
            else:
                selected_sources.append(source)
        since = now.astimezone(timezone.utc) - timedelta(days=since_days)
        items: list[dict[str, Any]] = []
        source_results: list[dict[str, Any]] = []
        for source in selected_sources:
            result: dict[str, Any] = {"sourceId": source["id"], "sourceName": source["name"], "sourceUri": source["url"], "category": source.get("category"), "status": "ok", "itemCount": 0}
            try:
                data, final_url = self._fetch(source)
                parsed = _parse_feed(data, source, since, normalized_keywords, max_items)
                result["finalUri"] = final_url
                result["itemCount"] = len(parsed)
                items.extend(parsed)
            except (OSINTCollectionError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                result["status"] = "error"
                result["error"] = str(exc)[:500]
                errors.append({"sourceId": source["id"], "error": str(exc)[:500]})
            source_results.append(result)
        items.sort(key=lambda item: (item.get("publishedAt") or "", item["sourceId"], item["title"]), reverse=True)
        return {"items": items[:max_items], "errors": errors, "fetchedAt": _iso(now), "sourceResults": source_results}


def collect(source_ids: Iterable[str] | None = None, keywords: Iterable[Any] | None = None, *, since_days: int = 30, max_items: int = 20, question: str | None = None) -> dict[str, Any]:
    """Convenience wrapper around a fresh :class:`OSINTCollector`."""

    return OSINTCollector().collect(source_ids, keywords, since_days=since_days, max_items=max_items, question=question)


__all__ = ["SOURCES", "SEARCH_SCOPES", "DEFAULT_SCOPE_IDS", "FIRECRAWL_SOURCE", "OSINTCollectionError", "OSINTCollector", "FirecrawlSearchProvider", "collect", "strip_html"]
