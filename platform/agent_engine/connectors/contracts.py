"""Pure, deterministic connection manifests derived only from frozen Profiles.

Connections represent services/drivers; operations reuse the bound tool version
schemas. This module never reads a registry, environment secret or network.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from ..snapshot_contracts import validate_credential_references


SCHEMA_VERSION = '1.0'


@lru_cache(maxsize=1)
def builtin_uav_tool_keys():
    from agent_tools.uav import register_uav_tools
    keys = set()
    register_uav_tools(SimpleNamespace(register_tool=lambda key, *args: keys.add(key)))
    return frozenset(keys)


BUILTIN_UAV_TOOL_KEYS = builtin_uav_tool_keys()


@lru_cache(maxsize=1)
def builtin_tool_keys():
    from agent_tools.ontology_runtime import register_source_tools
    from agent_tools.engineering_runtime import register_engineering_tools
    from agent_tools.future_battlefield import register_future_battlefield_tools
    from agent_tools.research import register_research_tools
    from agent_tools.document_runtime import register_document_tools
    keys = set(BUILTIN_UAV_TOOL_KEYS)
    capture = SimpleNamespace(repo=object(), register_tool=lambda key, *args: keys.add(key))
    for register in (register_source_tools, register_engineering_tools,
                     register_future_battlefield_tools, register_research_tools, register_document_tools):
        register(capture)
    return frozenset(keys)


def manifest_hash(value):
    payload = {key: item for key, item in value.items() if key != 'manifestHash'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _ref(value):
    if value is None:
        return None
    if isinstance(value, str) and ':' not in value:
        value = 'env:' + value
    validate_credential_references({'credentialRef': value})
    return value


def _safe_location(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('connector location must be a string')
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError('connector location cannot contain credentials')
    return value


def connector_manifest(profile):
    """Return a JSON manifest without resolving a mutable configuration.

    No model connection is invented for a legacy Profile without a frozen
    provider. Legacy HTTP-without-endpoint is explicitly diagnosed, never given
    an invented remote endpoint. Disabled bindings are excluded from grants.
    """
    model = _get(profile, 'model_policy', {}) or {}
    source = _get(profile, 'source_policy', {}) or {}
    tools = _get(profile, 'tools', {}) or {}
    connections, operations = {}, []

    def connection(ref, version, driver, **values):
        item = {'ref': ref, 'version': str(version or 'legacy'), 'driver': driver,
                'adapterVersion': str(values.pop('adapterVersion', None) or '1'),
                'scope': values.pop('scope', {}), 'config': values.pop('config', {}),
                'policy': values.pop('policy', {}), **values}
        validate_credential_references(item)
        previous = connections.get(ref)
        if previous is not None and previous != item:
            raise ValueError('inconsistent frozen connector binding')
        connections[ref] = item
        return ref

    provider = model.get('providerSnapshot')
    if isinstance(provider, dict):
        provider_key = provider.get('provider_key', model.get('model_provider', 'unknown'))
        ref = connection('provider:' + provider_key, provider.get('version'),
                         'deepseek' if provider_key == 'deepseek' else 'model',
                         endpoint=_safe_location(provider.get('base_url')),
                         credentialRef=_ref(provider.get('credential_ref')),
                         enabled=bool(provider.get('enabled')),
                         config=deepcopy(provider.get('config_json') or {}))
        operations.append({'ref': f"model:{provider_key}@{model.get('model_name', 'unknown')}",
                           'connectorRef': ref, 'driver': connections[ref]['driver'],
                           'adapterVersion': '1', 'modelName': model.get('model_name'),
                           'config': {'modelName': model.get('model_name')},
                           'timeoutSeconds': (provider.get('config_json') or {}).get('timeoutSeconds', 45),
                           'retryLimit': min((provider.get('config_json') or {}).get('retryLimit', 1),
                                             (model.get('config_json') or {}).get('retryLimit', 1)),
                           'accessMode': 'read_only'})

    research = (model.get('config_json') or {}).get('engineMode') == 'research-v1'
    for key, binding in sorted(tools.items()):
        if not _get(binding, 'enabled', True):
            continue
        config = deepcopy(_get(binding, 'config', {}) or {})
        transport = _get(binding, 'execution_transport') or config.get('executionTransport') or _get(binding, 'protocol', 'python')
        endpoint = _get(binding, 'endpoint')
        version = str(_get(binding, 'version', 'legacy'))
        adapter = _get(binding, 'adapter_version') or config.get('adapterVersion') or '1'
        if key.startswith('web.') and transport not in {'http', 'https'}:
            web = source.get('firecrawlProvider') or {}
            ref = connection('provider:' + web.get('providerKey', 'firecrawl'), web.get('version'), 'firecrawl',
                             endpoint=_safe_location(web.get('baseUrl')),
                             credentialRef=_ref(web.get('credentialRef')),
                             config=deepcopy(web.get('config') or {}),
                             scope={'allowedDomains': deepcopy(source.get('allowedDomains', []))})
        elif key.startswith('ontology.') and transport not in {'http', 'https'}:
            ontology = source.get('ontologySource') or {}
            ref = connection('source:' + ontology.get('source_key', 'uav-ontology'), ontology.get('policy_version'),
                             ontology.get('connector', 'sqlite'), sourceUri=_safe_location(ontology.get('root_uri')),
                             config=deepcopy(ontology.get('config_json') or {}),
                             scope={'readOnly': ontology.get('read_only', True)})
        elif key.startswith('document.') and transport not in {'http', 'https'}:
            source_key = config.get('sourceKey')
            selected = [item for item in source.get('documentSources', []) if item.get('source_key') == source_key]
            if len(selected) != 1:
                raise ValueError('document source is not frozen in the Profile')
            document = selected[0]
            ref = connection('source:' + source_key, document.get('policy_version'), 'files',
                             sourceUri=_safe_location(document.get('root_uri')),
                             config=deepcopy(document.get('config_json') or {}),
                             scope={'readOnly': document.get('read_only') is True,
                                    'documentIds': sorted((document.get('config_json') or {}).get('documents', {}))})
        elif (transport in {'http', 'https'} and not endpoint and key in BUILTIN_UAV_TOOL_KEYS
              and not research and not _get(binding, 'execution_transport') and not config.get('executionTransport')):
            ref = connection('local:legacy-uav', 'legacy', 'in_process_compat')
        elif transport in {'http', 'https'}:
            ref = connection('http:' + key, version, 'http', endpoint=_safe_location(endpoint),
                             credentialRef=_ref(config.get('credentialRef')),
                             scope={'allowedHosts': deepcopy(config.get('allowedHosts', []))},
                             config=config, adapterVersion=adapter)
        else:
            ref = connection('local:builtin', '1', 'python')
        operations.append({'ref': f'tool:{key}@{version}', 'toolKey': key, 'toolVersion': version,
                           'connectorRef': ref, 'driver': connections[ref]['driver'], 'adapterVersion': str(adapter),
                           'configuredTransport': transport,
                           'inputSchema': deepcopy(_get(binding, 'input_schema', {}) or {}),
                           'outputSchema': deepcopy(_get(binding, 'output_schema', {}) or {}),
                           'timeoutSeconds': _get(binding, 'timeout_seconds', 60),
                           'retryLimit': _get(binding, 'retry_limit', 2),
                           'accessMode': _get(binding, 'access_mode', 'read_only')})
    result = {'schemaVersion': SCHEMA_VERSION,
              'connections': sorted(connections.values(), key=lambda item: item['ref']),
              'operations': sorted(operations, key=lambda item: item['ref'])}
    validate_credential_references(result)
    result['manifestHash'] = manifest_hash(result)
    return result
