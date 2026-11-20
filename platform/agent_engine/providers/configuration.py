"""Provider configuration contains references, never credential values."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit


class ProviderConfigurationError(ValueError):
    def __init__(self, code: str = "invalid-provider-configuration"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProviderConfiguration:
    provider_key: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    credential_ref: str = "env:DEEPSEEK_API_KEY"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2048
    max_input_bytes: int = 180_000
    max_response_bytes: int = 4_000_000
    retry_limit: int = 1
    retry_backoff_seconds: float = 0.5
    thinking: bool = False
    allowed_actual_models: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.allowed_actual_models, tuple):
            raise ProviderConfigurationError()
        try:
            parsed = urlsplit(self.base_url)
            valid_url = (parsed.scheme == "https" and bool(parsed.hostname)
                         and not parsed.username and not parsed.password
                         and not parsed.query and not parsed.fragment
                         and parsed.port in (None, 443)
                         and not any(ch.isspace() or ord(ch) < 32 for ch in self.base_url))
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            raise ProviderConfigurationError("invalid-provider-endpoint")
        for value in (self.provider_key, self.model, *self.allowed_actual_models):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
                raise ProviderConfigurationError()
        if not isinstance(self.credential_ref, str) or not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]{0,127}", self.credential_ref):
            raise ProviderConfigurationError("invalid-credential-reference")
        limits = ((self.max_output_tokens, 1, 8192), (self.max_input_bytes, 256, 180_000),
                  (self.max_response_bytes, 256, 4_000_000), (self.retry_limit, 0, 1))
        if any(type(value) is not int or not low <= value <= high for value, low, high in limits):
            raise ProviderConfigurationError()
        if (type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 300
                or type(self.retry_backoff_seconds) not in (int, float)
                or not math.isfinite(self.retry_backoff_seconds) or not 0 <= self.retry_backoff_seconds <= 5
                or type(self.thinking) is not bool):
            raise ProviderConfigurationError()

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None):
        env = os.environ if environment is None else environment
        try:
            return cls(base_url=env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
                       model=env.get("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
                       credential_ref=env.get("DEEPSEEK_CREDENTIAL_REF", "env:DEEPSEEK_API_KEY"),
                       timeout_seconds=float(env.get("DEEPSEEK_TIMEOUT_SECONDS", "30")),
                       max_output_tokens=int(env.get("DEEPSEEK_MAX_OUTPUT_TOKENS", "2048")),
                       retry_limit=int(env.get("DEEPSEEK_RETRY_COUNT", "1")))
        except (TypeError, ValueError):
            raise ProviderConfigurationError() from None

    def resolve_credential(self, environment: Mapping[str, str] | None = None) -> str:
        env = os.environ if environment is None else environment
        key = env.get(self.credential_ref[4:], "")
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigurationError("provider-credential-missing")
        if len(key) > 4096 or any(ch.isspace() or ord(ch) < 32 for ch in key):
            raise ProviderConfigurationError("provider-credential-invalid")
        return key

    def safe_metadata(self, environment: Mapping[str, str] | None = None) -> dict:
        try:
            self.resolve_credential(environment)
            configured = True
        except ProviderConfigurationError:
            configured = False
        return {"providerKey": self.provider_key, "model": self.model, "baseUrl": self.base_url,
                "credentialRef": self.credential_ref, "configured": configured,
                "timeoutSeconds": self.timeout_seconds, "maxOutputTokens": self.max_output_tokens,
                "retryLimit": self.retry_limit, "allowedActualModels": list(self.allowed_actual_models)}
