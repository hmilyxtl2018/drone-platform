"""HTTP tools use server-frozen endpoints; model arguments cannot select hosts."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

import httpx

from ..providers.deepseek import redact_payload


class HttpToolTransportError(RuntimeError):
    """The request may have reached a remote service but its response was lost."""


class HttpToolTemporaryError(RuntimeError):
    """A deterministic temporary HTTP response that may be retried."""


def _public_addresses(addresses):
    """Return only canonical IP literals; reject mixed/private DNS responses."""
    if not isinstance(addresses, (list, tuple)) or not addresses:
        raise PermissionError("HTTP tool endpoint has no public address")
    normalized = []
    for address in addresses:
        if not isinstance(address, str) or "%" in address:
            raise PermissionError("HTTP tool endpoint resolved to an invalid address")
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            raise PermissionError("HTTP tool endpoint resolved to an invalid address") from None
        effective = parsed.ipv4_mapped if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped else parsed
        if not parsed.is_global or not effective.is_global:
            raise PermissionError("HTTP tool endpoint resolves to a non-public address")
        if str(parsed) not in normalized:
            normalized.append(str(parsed))
    return normalized


def _pinned_request_url(url, address):
    """Keep the path but give the connection layer an IP, never the DNS name.

    Host selects the original HTTP virtual host. httpcore's sni_hostname request
    extension preserves both TLS SNI and certificate hostname verification while
    its connect_tcp receives the validated literal. Environment proxies are off.
    """
    original = httpx.URL(url)
    return original.copy_with(host=address, port=None), original.raw_host.decode("ascii")


class HttpToolAdapter:
    def __init__(self, repository, key, *, client=None, resolver=None, environment=None):
        self.repository, self.key, self.client = repository, key, client
        self.resolver = resolver or self._resolve
        self.environment = os.environ if environment is None else environment

    @staticmethod
    async def _resolve(host):
        import asyncio
        rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return list({r[4][0] for r in rows})

    async def __call__(self, payload, ctx):
        binding = ctx.profile.tools[self.key]
        cfg = binding.config
        url = binding.endpoint
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                    or any(c.isspace() for c in url) or "\\" in url):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("HTTP tool endpoint must be a configured public HTTPS URL") from None
        if parsed.hostname not in cfg.get("allowedHosts", []):
            raise PermissionError("HTTP tool endpoint is outside the frozen host policy")
        try:
            direct_ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            addresses = _public_addresses(await self.resolver(parsed.hostname))
        else:
            addresses = _public_addresses([str(direct_ip)])
        pinned_url, tls_hostname = _pinned_request_url(url, addresses[0])
        # Explicit endpoint only; endpoint/template/path/header overrides in
        # model-produced arguments are rejected even if schema was too broad.
        def check(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if re.sub(r"[^a-z]", "", str(key).lower()) in {
                        "endpoint", "baseurl", "url", "headers", "authorization", "credentialref", "apikey"}:
                        raise PermissionError("HTTP tool arguments cannot override transport configuration")
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
        check(payload.get("arguments", payload))
        headers = {"Content-Type": "application/json", "Host": tls_hostname}
        if ":" in tls_hostname:
            headers["Host"] = "[" + tls_hostname + "]"
        secrets = ()
        if cfg.get("credentialRef"):
            ref = cfg["credentialRef"]
            if not re.fullmatch(r"env:[A-Z][A-Z0-9_]{1,127}", ref):
                raise ValueError("HTTP tool credential must be an environment reference")
            secret = self.environment.get(ref[4:])
            if not isinstance(secret, str) or not secret or any(c.isspace() for c in secret):
                raise ValueError("HTTP tool credential is missing or invalid")
            secrets = (secret,)
            headers["Authorization"] = "Bearer " + secret
        raw = json.dumps(redact_payload(payload, secrets), allow_nan=False, ensure_ascii=False).encode()
        max_bytes = cfg.get("maxPayloadBytes", 1048576)
        if type(max_bytes) is not int or not 256 <= max_bytes <= 4000000 or len(raw) > max_bytes:
            raise ValueError("HTTP tool payload exceeds configured cap")
        try:
            reservation = self.repository.reserve_budget(
                ctx.run_id, {"http_calls": 1}, step_key=getattr(ctx, "current_step_key", None))
        except TypeError as exc:
            if "step_key" not in str(exc):
                raise
            reservation = self.repository.reserve_budget(ctx.run_id, {"http_calls": 1})

        async def request(client):
            try:
                async with client.stream("POST", pinned_url, content=raw, headers=headers, follow_redirects=False,
                                         extensions={"sni_hostname": tls_hostname}, timeout=binding.timeout_seconds) as response:
                    if 300 <= response.status_code < 400:
                        raise PermissionError("HTTP tool redirect rejected")
                    if response.status_code in {401, 403}:
                        raise PermissionError("HTTP tool authentication rejected")
                    if response.status_code >= 400:
                        if response.status_code in {408, 429, 500, 502, 503, 504}:
                            raise HttpToolTemporaryError("HTTP tool temporary failure")
                        raise ValueError("HTTP tool request rejected")
                    chunks, count = [], 0
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > max_bytes:
                            raise ValueError("HTTP tool response exceeds configured cap")
                        chunks.append(chunk)
            except httpx.TimeoutException:
                raise TimeoutError("HTTP tool timeout") from None
            except httpx.HTTPError:
                raise HttpToolTransportError("HTTP tool transport failed") from None
            try:
                output = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                raise ValueError("HTTP tool returned invalid JSON") from None
            if not isinstance(output, dict):
                raise ValueError("HTTP tool must return a JSON object")
            return redact_payload(output, secrets)

        try:
            if self.client:
                return await request(self.client)
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                return await request(client)
        finally:
            self.repository.settle_budget(reservation, {"http_calls": 1})
