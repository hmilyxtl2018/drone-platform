"""Assemble providers exclusively from an immutable run Profile."""
from __future__ import annotations

from .model_gateway import ModelGateway
from .providers import DeepSeekProvider, ProviderConfiguration, ProviderConfigurationError


def model_configuration(profile):
    policy = profile.model_policy
    registry = policy.get("providerSnapshot")
    if not isinstance(registry, dict) or not registry.get("enabled"):
        raise ProviderConfigurationError("frozen-provider-configuration-missing")
    options = registry.get("config_json", {})
    if registry.get("base_url") != "https://api.deepseek.com":
        raise ProviderConfigurationError("unsupported-deepseek-endpoint")
    return ProviderConfiguration(
        provider_key=registry["provider_key"], base_url=registry["base_url"],
        credential_ref=registry["credential_ref"], model=policy["model_name"],
        timeout_seconds=options.get("timeoutSeconds", 45),
        max_output_tokens=policy.get("max_output_tokens", 2048),
        max_input_bytes=options.get("maxInputBytes", 180000),
        max_response_bytes=options.get("maxResponseBytes", 4000000),
        retry_limit=min(options.get("retryLimit", 1), policy.get("config_json", {}).get("retryLimit", 1)),
        thinking=options.get("thinking", False),
        allowed_actual_models=tuple(options.get("allowedActualModels", [])))


def model_gateway(repository, profile, *, client=None, environment=None):
    config = model_configuration(profile)
    return ModelGateway(repository, DeepSeekProvider(config, client=client, environment=environment), config)

