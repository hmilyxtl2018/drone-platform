"""Bounded, single-attempt Firecrawl adapters behind the Engine ToolGateway.

The v2 search/scrape contract is already used by osint_collector.py. This module
does not inherit that collector's retry loop: each Gateway attempt reserves one
actual HTTP call. Public-provider access still needs separate live verification.
"""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

import asyncio
import ipaddress
import json
import os
import re
import socket
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from .evidence import canonical_source_url, content_hash
from .ontology_runtime import arguments
from agent_engine.providers.deepseek import redact_payload


WEB_SCHEMAS = {
    "web.search": {"type": "object", "required": ["status", "results", "query", "retrievedAt", "excludedCount"]},
    "web.scrape": {"type": "object", "required": ["status", "sourceUri", "artifactId", "contentHash", "retrievedAt", "contentKind"]},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _split_markdown(markdown, limit):
    """Split a long provider response into bounded, provenance-preserving parts."""
    if len(markdown) <= limit:
        return [{"markdown": markdown, "index": 0, "count": 1,
                 "contentHash": content_hash(markdown)}]
    parts = []
    start = 0
    while start < len(markdown):
        end = min(len(markdown), start + limit)
        if end < len(markdown):
            boundary = markdown.rfind("\n\n", start + max(1, limit // 2), end)
            if boundary > start:
                end = boundary
        body = markdown[start:end]
        parts.append({"markdown": body, "startOffset": start, "endOffset": end,
                      "index": len(parts), "contentHash": content_hash(body)})
        start = end
    count = len(parts)
    for part in parts:
        part["count"] = count
    return parts


def _future_network_grant(ctx, host, method):
    profile = getattr(ctx, 'profile', None)
    policy = getattr(profile, 'model_policy', {}) or {}
    config = policy.get('config_json', {}) if isinstance(policy, dict) else {}
    from agent_engine.cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
    cognitive = getattr(profile, 'agent_key', None) in COGNITIVE_KEYS
    if not cognitive and (getattr(profile, 'agent_key', None) not in FUTURE_AGENT_KEYS or config.get('runtimeTruthGate') != 'fixed-runtime-preflight-v2'):
        return
    grants = (ctx.profile.harness_contract or {}).get('networkGrants', [])
    if not any((grant.get('domain_pattern') == host or
                (grant.get('domain_pattern','').startswith('*.') and host.endswith(grant['domain_pattern'][1:])))
               and method in grant.get('methods', []) and not grant.get('approvalRequired') for grant in grants):
        raise PermissionError('network request is outside the frozen Harness grants')


async def resolve_addresses(host):
    loop = asyncio.get_running_loop()
    rows = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list({row[4][0] for row in rows})


def source_policy(ctx):
    policy = getattr(ctx.profile, "source_policy", None) or ctx.profile.resource_policy.get("source_policy", {})
    if not isinstance(policy, dict):
        raise ValueError("frozen source policy must be an object")
    domains = policy.get("allowedDomains")
    if not isinstance(domains, list) or not 1 <= len(domains) <= 50:
        raise PermissionError("web tools require an explicit frozen allowedDomains policy")
    checked = []
    for domain in domains:
        if not isinstance(domain, str) or not domain or domain != domain.strip() or any(c in domain for c in "/:@*?#\\"):
            raise ValueError("source allowlist must contain hostnames only")
        try:
            normalized = domain.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            raise ValueError("invalid source allowlist hostname") from None
        if len(normalized) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", normalized):
            raise ValueError("invalid source allowlist hostname")
        checked.append(normalized)
    result = {**policy, "allowedDomains": checked}
    for key, default, maximum in (("maxSources", 6, 50), ("maxPages", 8, 50),
                                  ("maxPayloadBytes", 2097152, 8388608), ("maxMarkdownChars", 150000, 1000000)):
        value = result.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise ValueError(f"invalid source policy limit: {key}")
        result[key] = value
    if not isinstance(result.get("includeSubdomains", False), bool):
        raise ValueError("includeSubdomains must be boolean")
    return result


class FirecrawlRuntimeTools:
    def __init__(self, repository, *, client=None, resolver=None, secret_resolver=None, timeout_seconds=45):
        self.repository = repository
        self.client = client
        self.resolver = resolver or resolve_addresses
        self.secret_resolver = secret_resolver or os.environ.get
        self.timeout_seconds = timeout_seconds

    async def _public_url(self, url, policy):
        if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            raise ValueError("source URL must be a bounded public HTTPS URL")
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
            port = parsed.port
        except (ValueError, UnicodeError):
            raise ValueError("invalid source URL") from None
        if parsed.scheme != "https" or not hostname or parsed.username is not None or parsed.password is not None or port not in (None, 443):
            raise ValueError("source URL must use HTTPS without credentials or a custom port")
        allowed = any(hostname == domain or (policy.get("includeSubdomains", False) and hostname.endswith("." + domain)) for domain in policy["allowedDomains"])
        if not allowed:
            raise PermissionError("source hostname is outside the frozen allowlist")
        try:
            addresses = [str(ipaddress.ip_address(hostname))]
        except ValueError:
            try:
                addresses = await self.resolver(hostname)
            except (OSError, TimeoutError):
                raise ValueError("source hostname could not be resolved") from None
        if not addresses:
            raise ValueError("source hostname has no public address")
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise PermissionError("source hostname resolves to a non-public address")
        except ValueError:
            raise ValueError("source resolver returned an invalid address") from None
        return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))

    def _provider(self, policy):
        provider = policy.get("firecrawlProvider", {})
        if not isinstance(provider, dict):
            raise ValueError("frozen Firecrawl provider must be an object")
        endpoint = provider.get("baseUrl", "https://api.firecrawl.dev/v2").rstrip("/")
        if endpoint != "https://api.firecrawl.dev/v2":
            raise ValueError("this adapter requires the official Firecrawl HTTPS v2 endpoint")
        credential_ref = provider.get("credentialRef", "FIRECRAWL_API_KEY")
        if not isinstance(credential_ref, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", credential_ref):
            raise ValueError("Firecrawl credential must be an environment reference")
        secret = self.secret_resolver(credential_ref)
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("Firecrawl credential is not configured")
        return endpoint, secret.strip()

    async def _post(self, operation, payload, ctx, policy):
        endpoint, secret = self._provider(policy)
        _future_network_grant(ctx, urlsplit(endpoint).hostname, 'POST')
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw) > policy["maxPayloadBytes"]:
            raise ValueError("Firecrawl request exceeds configured payload cap")
        charges = {"http_calls": 1, f"{operation}_calls": 1}
        # No operation_key: every real retry is separately charged, even if the
        # external provider's actual credits after a timeout remain unknown.
        try:
            reservation = self.repository.reserve_budget(ctx.run_id, charges, step_key=ctx.current_step_key)
        except TypeError as exc:
            if 'step_key' not in str(exc):
                raise
            reservation = self.repository.reserve_budget(ctx.run_id, charges)

        async def send(client):
            try:
                async with client.stream("POST", endpoint + "/" + operation, content=raw,
                                         headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"},
                                         follow_redirects=False, timeout=self.timeout_seconds) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        raise PermissionError("Firecrawl provider redirect refused")
                    if status in {401, 403}:
                        raise PermissionError(f"Firecrawl authentication/access rejected (HTTP {status})")
                    if status >= 400:
                        if status in {408, 425, 429, 500, 502, 503, 504}:
                            raise RuntimeError(f"Firecrawl temporary HTTP {status}")
                        raise ValueError(f"Firecrawl request rejected (HTTP {status})")
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > policy["maxPayloadBytes"]:
                            raise ValueError("Firecrawl response exceeds configured payload cap")
                        chunks.append(chunk)
            except httpx.TimeoutException:
                raise TimeoutError("Firecrawl request timed out") from None
            except httpx.HTTPError:
                # The library's exception may contain request headers or a
                # provider body; expose only a deterministic sanitized message.
                raise RuntimeError("Firecrawl transport failed") from None
            try:
                decoded = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                raise ValueError("Firecrawl returned invalid JSON") from None
            if not isinstance(decoded, dict) or decoded.get("success") is not True:
                raise ValueError("Firecrawl returned an unsuccessful response")
            # A remote response can echo a credential in arbitrary text or
            # object keys. Redact before snapshots, tool logs or model context.
            return redact_payload(decoded, (secret,))

        try:
            if self.client is not None:
                return await send(self.client)
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                return await send(client)
        finally:
            # Even cancelled/failed attempts consumed a request reservation.
            self.repository.settle_budget(reservation, charges)

    async def search(self, payload, ctx):
        args, policy = arguments(payload), source_policy(ctx)
        query = args.get("query")
        limit = args.get("limit", min(3, policy["maxSources"]))
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
            raise ValueError("web.search requires a nonempty bounded query")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= policy["maxSources"]:
            raise ValueError("web.search limit exceeds configured source cap")
        # Recover a completed response committed before the caller's checkpoint.
        # Identical discovery never needs a second paid request in the same Run.
        if hasattr(self.repository, "list_artifacts"):
            for artifact in self.repository.list_artifacts(ctx.run_id):
                value = artifact.get("content_json", {})
                if (artifact.get("artifact_type") == "web-search-results" and isinstance(value, dict)
                        and value.get("query") == query.strip() and value.get("requestedLimit", limit) == limit):
                    known = ctx.state.setdefault("webSourceUrls", [])
                    for row in value.get("results", []):
                        if row["sourceUri"] not in known:
                            known.append(row["sourceUri"])
                    return {**deepcopy(value), "cacheHit": True}
        sites = " OR ".join("site:" + domain for domain in policy["allowedDomains"])
        scoped_query = "(" + query.strip() + ") (" + sites + ")"
        decoded = await self._post("search", {"query": scoped_query, "limit": limit, "sources": ["web"]}, ctx, policy)
        data = decoded.get("data")
        rows = data.get("web") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("Firecrawl search response has no web result list")
        results, seen, excluded = [], set(), 0
        fetched = utc_now()
        known = ctx.state.setdefault("webSourceUrls", [])
        for row in rows[:limit]:
            if not isinstance(row, dict):
                excluded += 1
                continue
            try:
                url = await self._public_url(row.get("url"), policy)
            except (ValueError, PermissionError):
                excluded += 1
                continue
            if url in seen or (url not in known and len(known) >= policy["maxSources"]):
                excluded += 1
                continue
            seen.add(url)
            if url not in known:
                known.append(url)
            results.append({"sourceUri": url, "title": str(row.get("title") or "")[:1000],
                            "snippet": str(row.get("description") or "")[:4000], "contentKind": "search-snippet",
                            "retrievedAt": fetched, "observedAt": None, "truthVerified": False})
        output = {"status": "found" if results else "no-results", "query": query.strip(), "requestedLimit": limit, "results": results,
                  "retrievedAt": fetched, "excludedCount": excluded, "provider": "firecrawl"}
        self.repository.add_artifact(ctx.run_id, "web-search-results", output)
        return output

    async def scrape(self, payload, ctx):
        args, policy = arguments(payload), source_policy(ctx)
        url = await self._public_url(args.get("url"), policy)
        _future_network_grant(ctx, urlsplit(url).hostname, 'GET')
        cache = ctx.state.setdefault("webSourceSnapshots", {})
        if url not in cache and hasattr(self.repository, "list_artifacts"):
            for artifact in self.repository.list_artifacts(ctx.run_id):
                if artifact.get("artifact_type") != "web-source-snapshot":
                    continue
                content = artifact.get("content_json", {})
                if isinstance(content, str):
                    content = json.loads(content)
                if content.get("sourceUri") == url:
                    cache[url] = {**deepcopy(content), "artifactId": artifact["artifact_id"]}
                    break
        if url in cache:
            return {**deepcopy(cache[url]), "cacheHit": True}
        known = ctx.state.setdefault("webSourceUrls", [])
        if url not in known:
            if len(known) >= policy["maxSources"]:
                raise ValueError("web source cap exhausted")
            known.append(url)
        attempts = ctx.state.get("webScrapeAttempts", 0)
        if attempts >= policy["maxPages"]:
            raise ValueError("web page attempt cap exhausted")
        ctx.state["webScrapeAttempts"] = attempts + 1
        decoded = await self._post("scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True}, ctx, policy)
        data = decoded.get("data")
        if not isinstance(data, dict):
            raise ValueError("Firecrawl scrape response has no source object")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Firecrawl source metadata is invalid")
        reported_url = metadata.get("sourceURL") or metadata.get("url")
        if not reported_url or canonical_source_url(await self._public_url(reported_url, policy)) != canonical_source_url(url):
            raise PermissionError("Firecrawl source URL differs or is absent; remote redirect cannot be accepted")
        status_code = metadata.get("statusCode", 200)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            raise ValueError("Firecrawl source did not return a successful page")
        markdown = data.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError("Firecrawl source has no markdown body")
        segments = _split_markdown(markdown, policy["maxMarkdownChars"])
        truncated = len(segments) > 1
        snapshot = {"status": "scraped" if markdown.strip() else "no-content", "sourceUri": url,
                    "contentKind": "markdown", "markdown": segments[0]["markdown"],
                    "contentHash": segments[0]["contentHash"], "segments": segments,
                    "truncated": truncated, "originalCharacterCount": len(markdown),
                    "originalContentHash": content_hash(markdown),
                    "retrievedAt": utc_now(), "observedAt": None,
                    "publishedAt": (metadata.get("publishedTime") or metadata.get("publishedAt")
                                    or metadata.get("publishedDate") or metadata.get("date")
                                    or metadata.get("article:published_time")),
                    "title": str(metadata.get("title") or "")[:1000], "provider": "firecrawl",
                    "truthVerified": False, "reviewStatus": "pending", "sourceVersion": "sha256:" + content_hash(markdown),
                    "redirectValidation": "reported-final-url-matches; provider-remote-fetch-unverified"}
        snapshot["artifactId"] = self.repository.add_artifact(ctx.run_id, "web-source-snapshot", snapshot)
        from agent_engine.connectors.events import observe_source_artifact
        observe_source_artifact(self.repository, snapshot['artifactId'])
        cache[url] = deepcopy(snapshot)
        return snapshot
