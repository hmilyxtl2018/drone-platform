"""Publication checks for versioned tool transport; never probe a service."""
from urllib.parse import urlsplit
import re


def validate_tool_connection(tool_key, registry, version, config):
    transport = version.get('execution_transport') or registry.get('protocol') or 'python'
    endpoint = version.get('endpoint')
    if transport == 'python':
        if tool_key.startswith('uav.'):
            from agent_engine.connectors.contracts import builtin_tool_keys
            if tool_key not in builtin_tool_keys():
                raise ValueError('local tool has no registered implementation')
        return
    if transport not in {'http', 'https'}:
        raise ValueError('unsupported execution transport')
    if not endpoint and not version.get('execution_transport') and tool_key.startswith('uav.'):
        from agent_engine.connectors.contracts import builtin_tool_keys
        if tool_key in builtin_tool_keys():
            return  # Immutable, supported legacy bindings keep their explicit compatibility route.
        raise ValueError('legacy tool has no endpoint or local implementation')
    try:
        parts = urlsplit(endpoint or '')
        valid = (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                 and not parts.query and not parts.fragment and parts.port in (None, 443)
                 and not any(c.isspace() for c in endpoint) and '\\' not in endpoint)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('remote tool requires a configured HTTPS endpoint')
    if parts.hostname not in config.get('allowedHosts', []):
        raise ValueError('remote endpoint is outside the configured host scope')
    credential = config.get('credentialRef')
    if credential is not None and (not isinstance(credential, str) or not re.fullmatch(r'env:[A-Z][A-Z0-9_]{1,127}', credential)):
        raise ValueError('remote tool credential must be a server reference')
