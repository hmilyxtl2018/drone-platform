"""A redacted, read-only view across the existing connector registries.

The three existing registries remain authoritative. A catalog item is a service
connection and can expose many tool operations; it is deliberately not another
registry and never returns endpoints, URIs, credential references or configs.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import json
import os
import math
from typing import Any
from psycopg2.extras import Json

from .contracts import BUILTIN_UAV_TOOL_KEYS, builtin_tool_keys, connector_manifest


_REMOTE = {'http', 'https'}
_RETIRED = {'disabled', 'inactive', 'retired', 'deprecated'}


def _row(value):
    return dict(value) if value is not None else None


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


class ConnectorCatalog:
    """Build a safe connector directory from a DB connection factory."""

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _configured(ref, credential_ref, enabled):
        if not enabled:
            return 'disabled'
        if credential_ref:
            if isinstance(credential_ref, str) and credential_ref.startswith('env:'):
                return 'configured' if bool(os.environ.get(credential_ref[4:], '').strip()) else 'invalid'
            return 'configured'
        return 'configured'

    @staticmethod
    def _policy(row):
        if not row:
            return None
        return {
            'policyVersion': row.get('policy_version', 0),
            'maxConcurrent': row.get('max_concurrency'),
            'callsPerMinute': row.get('requests_per_minute'),
            'leaseSeconds': row.get('lease_seconds'),
            'failureThreshold': row.get('failure_threshold'),
            'cooldownSeconds': row.get('cooldown_seconds'),
            'enabled': row.get('enabled'),
        }

    @staticmethod
    def _metrics(observations):
        if observations is None:
            return {'windowHours': 24, 'calls': None, 'failed': None, 'p95LatencyMs': None}
        deduplicated = {}
        for index,item in enumerate(observations):
            call_id = item.get('call_id')
            key = ('call', call_id) if call_id else ('attempt', item.get('run_id'), item.get('operation_key'), item.get('attempt_key')) if item.get('attempt_key') else ('row',index)
            current = deduplicated.get(key)
            # The governance observation carries the typed failure class; the
            # legacy gateway row carries the canonical call ID. Keep both fields.
            if current is None:
                deduplicated[key] = item
            else:
                deduplicated[key] = {**item, **current, **{k: v for k, v in item.items() if v is not None}}
        observations = list(deduplicated.values())
        values = [int(item['latency_ms']) for item in observations
                  if isinstance(item.get('latency_ms'), (int, float)) and item['latency_ms'] >= 0]
        failures = sum(1 for item in observations if item.get('status') in {'failed', 'timeout', 'rejected'})
        values.sort()
        p95 = values[min(len(values) - 1, max(0, math.ceil(len(values) * .95) - 1))] if values else None
        return {'windowHours': 24, 'calls': len(observations), 'failed': failures,
                'p95LatencyMs': p95}

    @staticmethod
    def _actual_transport(key, configured, endpoint, builtin, explicit=False):
        if key in BUILTIN_UAV_TOOL_KEYS and configured in _REMOTE and not endpoint and not explicit:
            return 'in_process_compat'
        if configured in _REMOTE and not endpoint:
            return 'unavailable'
        if key in builtin and configured == 'python':
            return 'python'
        if configured in _REMOTE and endpoint:
            return configured
        return configured or 'unknown'

    def _read(self):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                c.execute('''SELECT table_name,column_name FROM information_schema.columns
                              WHERE table_schema=current_schema()''')
                columns = defaultdict(set)
                for row in c.fetchall():
                    columns[row['table_name']].add(row['column_name'])
                def query(sql, args=()):
                    c.execute(sql, args)
                    return [_row(item) for item in c.fetchall()]
                providers = query('SELECT * FROM provider_registry ORDER BY provider_key') if 'provider_registry' in columns else []
                sources = query('SELECT * FROM data_source_registry ORDER BY source_key') if 'data_source_registry' in columns else []
                execution = 'v.execution_transport' if 'execution_transport' in columns['tool_version'] else 'NULL::text AS execution_transport'
                adapter = 'v.adapter_version' if 'adapter_version' in columns['tool_version'] else 'NULL::text AS adapter_version'
                binding_clause = (''' OR EXISTS(SELECT 1 FROM agent_tool_binding b
                                    WHERE b.tool_key=t.tool_key AND b.tool_version=v.version AND b.enabled=1)'''
                                  if 'agent_tool_binding' in columns else '')
                tools = query(f'''SELECT t.*, v.version AS bound_version, v.endpoint AS version_endpoint,
                                      {execution}, {adapter}, v.input_schema AS version_input_schema,
                                      v.output_schema AS version_output_schema
                                 FROM tool_registry t
                                 LEFT JOIN tool_version v ON v.tool_key=t.tool_key AND (v.version=t.current_version {binding_clause})
                                ORDER BY t.tool_key,v.version''') if {'tool_registry','tool_version'} <= columns.keys() else []
                bindings = query('''SELECT b.agent_key,b.tool_key,b.tool_version,b.enabled,b.config_json,
                                           d.display_name
                                      FROM agent_tool_binding b
                                      LEFT JOIN agent_definition d USING(agent_key)''') if {'agent_tool_binding','agent_definition'} <= columns.keys() else []
                model_bindings = query('''SELECT m.agent_key,m.model_provider,m.model_name,m.config_json,d.display_name
                                           FROM agent_model_policy m JOIN agent_definition d USING(agent_key)''') if {'agent_model_policy','agent_definition'} <= columns.keys() else []
                policies = query('SELECT * FROM connector_policy') if 'connector_policy' in columns else []
                health = query('''SELECT h.*, connection.value AS connection_json, link.call_id
                    FROM connector_health_observation h
                    LEFT JOIN agent_run_connector_snapshot s USING(run_id)
                    LEFT JOIN LATERAL jsonb_array_elements(COALESCE(s.manifest_json->'connections','[]'::jsonb)) connection
                        ON connection.value->>'ref'=h.connector_ref
                    LEFT JOIN connector_call_link link ON link.run_id=h.run_id
                        AND link.operation_key=h.operation_key AND link.attempt_key=h.attempt_key
                    WHERE h.status<>'rejected' AND COALESCE(h.error_code,'')<>'connector-not-dispatched'
                    ORDER BY h.observed_at''') if {'connector_health_observation','agent_run_connector_snapshot'} <= columns.keys() else None
                # Recorded provider calls establish transport and output health
                # independently. Match the exact frozen configuration so a new
                # address/version never inherits an old connection's success.
                recorded = {}
                if 'agent_run_profile_snapshot' in columns:
                    for provider in providers:
                        typ = provider.get('provider_type'); key = provider['provider_key']
                        if typ == 'model' and {'provider_key','status','created_at','error_code','latency_ms'} <= columns['agent_model_call']:
                            frozen = {k: provider.get(k) for k in ('provider_key','base_url','credential_ref','enabled','version','config_json')}
                            recorded['provider:' + key] = query('''SELECT a.call_id,a.status,a.error_code,a.latency_ms,a.created_at AS observed_at
                                FROM agent_model_call a JOIN agent_run_profile_snapshot s USING(run_id)
                                WHERE a.provider_key=%s AND s.profile_json #> '{modelPolicy,providerSnapshot}'=%s::jsonb
                                ORDER BY a.created_at''', (key,Json(frozen)))
                        elif typ == 'web' and {'tool_key','status','created_at','latency_ms','error_message','output_json'} <= columns['agent_tool_call']:
                            ref = provider.get('credential_ref') or ''
                            frozen = {'providerKey':key,'baseUrl':provider.get('base_url'),'credentialRef':ref[4:] if ref.startswith('env:') else ref,'version':provider.get('version')}
                            enabled_web_agents = {binding['agent_key'] for binding in bindings
                                                  if binding.get('tool_key') in {'web.search','web.scrape'} and bool(binding.get('enabled'))}
                            scopes = []
                            for model in model_bindings:
                                cfg = _json(model.get('config_json'), {})
                                if model.get('agent_key') not in enabled_web_agents or cfg.get('webProviderKey','firecrawl') != key:
                                    continue
                                scope = deepcopy((cfg.get('sourcePolicy') or {}).get('allowedDomains', []))
                                if scope not in scopes:
                                    scopes.append(scope)
                            recorded['provider:' + key] = query('''SELECT a.call_id,a.status,
                                CASE WHEN a.error_message ~* 'timeout|connect|network|transport|resolve' THEN 'network-error'
                                     WHEN a.error_message ~* 'auth|401|403' THEN 'authentication'
                                     WHEN a.error_message ~* 'quota|429|rate' THEN 'rate-limit'
                                     WHEN a.error_message ~* 'schema|json|output|payload|response' THEN 'invalid-output'
                                     WHEN a.error_message IS NOT NULL THEN 'tool-error' END AS error_code,
                                a.latency_ms,a.created_at AS observed_at
                                FROM agent_tool_call a JOIN agent_run_profile_snapshot s USING(run_id)
                                WHERE a.tool_key IN ('web.search','web.scrape')
                                  AND COALESCE(a.output_json->>'cacheHit','false') <> 'true'
                                  AND s.profile_json #> '{sourcePolicy,firecrawlProvider}' @> %s::jsonb
                                  AND COALESCE(s.profile_json #> '{sourcePolicy,firecrawlProvider,config}','{}'::jsonb)=%s::jsonb
                                  AND EXISTS (SELECT 1 FROM jsonb_array_elements(%s::jsonb) scopes(value)
                                      WHERE COALESCE(s.profile_json #> '{sourcePolicy,allowedDomains}','[]'::jsonb)=scopes.value)
                                ORDER BY a.created_at''', (Json(frozen),Json(provider.get('config_json') or {}),Json(scopes)))
                return providers, sources, tools, bindings, model_bindings, policies, health, recorded

    def list(self):
        providers, sources, tools, bindings, model_bindings, policies, health, recorded = self._read()
        builtin = builtin_tool_keys()
        by_policy = {item.get('connector_ref'): item for item in policies}
        by_health = defaultdict(list, recorded)
        current_provider_refs = {f"provider:{item.get('provider_key')}": item for item in providers}
        current_source_refs = {f"source:{item.get('source_key')}": item for item in sources}
        model_by_agent = {item.get('agent_key'): item for item in model_bindings}
        # Resolve operation dependencies using the same pure contract builder
        # as runtime, not hard-coded default source/provider names.
        assignments = []
        expected_connections = defaultdict(list)
        def identity(connection):
            return json.dumps({k:v for k,v in connection.items() if k!='policy'},
                              sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False,default=str)
        for row in tools:
            key=row['tool_key']; version=str(row.get('bound_version') or row.get('current_version') or 'legacy')
            active=[b for b in bindings if b.get('tool_key')==key and str(b.get('tool_version'))==version and bool(b.get('enabled'))]
            if key.startswith('document.') and not active:
                # A document reader registration is an operation, not an
                # invented connection. It needs an explicit source binding.
                continue
            for binding in active or [None]:
                model_row=model_by_agent.get((binding or {}).get('agent_key'),{})
                model_config=_json(model_row.get('config_json'),{})
                config=deepcopy(_json((binding or {}).get('config_json'),{}))
                if row.get('execution_transport'):
                    config['executionTransport']=row['execution_transport']
                if row.get('adapter_version'):
                    config['adapterVersion']=row['adapter_version']
                source_policy=deepcopy(model_config.get('sourcePolicy') or {})
                webkey=model_config.get('webProviderKey','firecrawl')
                provider=current_provider_refs.get('provider:'+webkey)
                if provider:
                    credential=provider.get('credential_ref') or ''
                    source_policy['firecrawlProvider']={'providerKey':webkey,'version':provider.get('version'),
                        'baseUrl':provider.get('base_url'),'credentialRef':credential[4:] if credential.startswith('env:') else credential or None,
                        'config':deepcopy(provider.get('config_json') or {})}
                sourcekey=model_config.get('ontologySourceKey','uav-ontology')
                ontology=current_source_refs.get('source:'+sourcekey)
                if ontology:
                    source_policy['ontologySource']=deepcopy(ontology)
                document=current_source_refs.get('source:'+str(config.get('sourceKey')))
                if document:
                    source_policy['documentSources']=[deepcopy(document)]
                endpoint=row.get('version_endpoint') if row.get('bound_version') is not None else row.get('endpoint')
                tool=SimpleNamespace(enabled=True,version=version,protocol=row.get('protocol') or 'python',endpoint=endpoint,
                    config=config,input_schema=_json(row.get('version_input_schema') or row.get('input_schema'),{}),
                    output_schema=_json(row.get('version_output_schema') or row.get('output_schema'),{}))
                try:
                    manifest=connector_manifest(SimpleNamespace(tools={key:tool},model_policy={'config_json':model_config},source_policy=source_policy))
                    operation=manifest['operations'][0]
                    connection=next(conn for conn in manifest['connections'] if conn['ref']==operation['connectorRef'])
                    expected_connections[connection['ref']].append(connection)
                except (ValueError,TypeError,KeyError):
                    operation={'connectorRef':'http:'+key,'driver':'unavailable'}
                    connection=None
                assignments.append((row,binding,operation,connection))
        if health is not None:
            for item in health:
                ref = item.get('connector_ref', '')
                connection = item.get('connection_json') or {}
                if not connection or str(connection.get('version')) != str(item.get('connector_version')):
                    continue
                full_identity=identity(connection)
                full_hash=hashlib.sha256(full_identity.encode()).hexdigest()
                if item.get('binding_hash') and item['binding_hash']!=full_hash:
                    continue
                if ref.startswith('provider:'):
                    provider = current_provider_refs.get(ref)
                    if not provider or str(provider.get('version')) != str(item.get('connector_version')):
                        continue
                    credential = provider.get('credential_ref') or ''
                    credential = credential[4:] if credential.startswith('env:') else credential
                    # Scope can differ by Agent. The shared service health is
                    # tied to the exact provider address/credential/config and
                    # version; per-Agent allowed domains remain in the frozen
                    # manifest but do not create a different provider service.
                    if (connection.get('endpoint') != provider.get('base_url')
                            or connection.get('credentialRef') != (('env:' + credential) if credential else None)
                            or connection.get('config', {}) != (provider.get('config_json') or {})):
                        continue
                    if provider.get('provider_type')=='web' and expected_connections.get(ref) and not any(identity(candidate)==full_identity for candidate in expected_connections[ref]):
                        continue
                elif ref.startswith('source:'):
                    source = current_source_refs.get(ref)
                    if not source or str(source['policy_version']) != str(item.get('connector_version')) or connection.get('sourceUri') != source['root_uri'] or connection.get('config', {}) != (source.get('config_json') or {}):
                        continue
                    if not any(identity(candidate)==full_identity for candidate in expected_connections.get(ref,[])):
                        continue
                elif ref.startswith('http:'):
                    if not any(identity(candidate)==full_identity for candidate in expected_connections.get(ref,[])):
                        continue
                elif ref not in {'local:builtin', 'local:legacy-uav'}:
                    continue
                by_health[ref].append(item)
        items = {}
        def add(ref, name, kind, version, configured, actual, enabled, status,
                mismatch=False, operations=(), agents=(), credential=None, policy=None):
            observations = by_health.get(ref)
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            window = [o for o in observations or [] if o.get('observed_at') and o['observed_at'] >= since]
            observed = observations is not None or (health is not None and ref not in recorded)
            item = items.setdefault(ref, {'ref': ref, 'name': name, 'kind': kind, 'version': str(version or 'legacy'),
                'configuredTransport': configured, 'actualTransport': actual, 'enabled': bool(enabled),
                'configurationStatus': status, 'configurationIssues': [], 'observationStatus': 'unknown',
                'lastObservedAt': None, 'legacyMismatch': bool(mismatch), 'operations': [], 'agents': [],
                'metrics': self._metrics(window if observed else None),
                'policy': self._policy(by_policy.get(ref))})
            item['legacyMismatch'] = item['legacyMismatch'] or bool(mismatch)
            if status == 'invalid':
                item['configurationStatus'] = 'invalid'
            if observations:
                latest = max(observations,key=lambda value:value['observed_at'])
                error = str(latest.get('error_code') or latest.get('error_class') or '').lower()
                item['lastObservedAt'] = latest.get('observed_at')
                item['observationStatus'] = ('success' if latest.get('status') in {'success','succeeded'}
                                             else 'unknown' if latest.get('status') == 'unknown' else 'failed')
                item['lastFailureClass'] = ('output' if any(part in error for part in ('invalid-output','invalid-json','schema','invalid-model'))
                                            else 'authentication' if any(part in error for part in ('auth','401','403'))
                                            else 'rate-limit' if any(part in error for part in ('rate','quota','429'))
                                            else 'network' if any(part in error for part in ('network','transport','timeout','connect'))
                                            else 'other' if error else None)
                item['transportStatus'] = ('success' if item['observationStatus']=='success' or item['lastFailureClass']=='output'
                                            else 'failed' if item['lastFailureClass'] in {'network','authentication'} else 'unknown')
            for op in operations:
                if op not in item['operations']:
                    item['operations'].append(op)
            for agent in agents:
                if agent not in item['agents']:
                    item['agents'].append(agent)
            if status == 'invalid' and 'configuration-invalid' not in item['configurationIssues']:
                item['configurationIssues'].append('configuration-invalid')
            return item

        for row in providers:
            key, typ = row.get('provider_key'), row.get('provider_type')
            ref = 'provider:' + key
            agents = [{'agentKey':m['agent_key'],'displayName':m.get('display_name') or m['agent_key']}
                      for m in model_bindings if m.get('model_provider') == key]
            item = add(ref, key, typ or 'provider', row.get('version'), 'https', 'https', row.get('enabled'),
                       self._configured(ref, row.get('credential_ref'), row.get('enabled')), agents=agents, policy=by_policy.get(ref))
            if typ == 'model':
                item['operations'] = [{'ref':f"model:{key}@{m.get('model_name')}",'name':m.get('model_name'),
                                       'actualTransport':'https'} for m in model_bindings if m.get('model_provider') == key]
        for row in sources:
            key = row.get('source_key'); ref = 'source:' + key
            item = add(ref, key, 'data-source', row.get('policy_version'), row.get('connector'), row.get('connector'),
                       row.get('enabled'), self._configured(ref, row.get('credential_ref'), row.get('enabled')),
                       policy=by_policy.get(ref))
        for row,binding,operation,connection in assignments:
            key = row.get('tool_key'); version = row.get('bound_version') or row.get('current_version') or 'legacy'
            binding_config = _json((binding or {}).get('config_json'), {})
            explicit_transport = bool(row.get('execution_transport') or binding_config.get('executionTransport'))
            configured = (operation.get('configuredTransport') or row.get('execution_transport')
                          or row.get('protocol') or 'python')
            endpoint = row.get('version_endpoint') if row.get('bound_version') is not None else row.get('endpoint')
            actual = self._actual_transport(key,configured,endpoint,builtin,explicit_transport)
            ref = operation.get('connectorRef') or 'http:'+key
            driver = (connection or {}).get('driver')
            if driver == 'firecrawl': actual = 'https'
            elif driver in {'sqlite','files','in_process_compat'}: actual = driver
            elif driver == 'http': actual = configured if (connection or {}).get('endpoint') or endpoint else 'unavailable'
            elif driver == 'unavailable': actual = 'unavailable'
            if connection is None:
                actual='unavailable'
            invalid = bool(connection is None or row.get('status') in _RETIRED or actual=='unavailable'
                           or (actual in _REMOTE and not ((connection or {}).get('endpoint') or endpoint))
                           or (actual=='python' and key not in builtin))
            if ref.startswith('source:'):
                source_row=current_source_refs.get(ref)
                invalid = invalid or not source_row or not source_row.get('enabled') or not source_row.get('read_only')
            if ref.startswith('provider:'):
                provider_row=current_provider_refs.get(ref)
                invalid = invalid or not provider_row or not provider_row.get('enabled')
            mismatch = bool(actual=='in_process_compat' and configured in _REMOTE)
            op={'ref':f'tool:{key}@{version}','toolKey':key,'toolVersion':str(version),
                'name':row.get('tool_name') or key,'actualTransport':actual,
                'configurationStatus':'invalid' if invalid else 'configured'}
            agents = ([{'agentKey':binding.get('agent_key'),'displayName':binding.get('display_name') or binding.get('agent_key')}]
                      if binding is not None else [])
            item = add(ref, '本地工具' if ref=='local:builtin' else '旧版本本地兼容工具' if ref=='local:legacy-uav' else row.get('tool_name') or key,
                       'http' if ref.startswith('http:') else 'local', (connection or {}).get('version',version),
                       configured,actual,row.get('status') not in _RETIRED,'invalid' if invalid else 'configured',
                       mismatch,[op],agents,policy=by_policy.get(ref))
            if invalid:
                research = (_json(model_by_agent.get((binding or {}).get('agent_key'), {}).get('config_json'), {})
                            .get('engineMode') == 'research-v1')
                issue = ('missing-local-handler' if key.startswith('uav.') and not endpoint and key not in builtin
                         else 'legacy-research-binding-invalid' if research and key in BUILTIN_UAV_TOOL_KEYS and not endpoint
                         else 'configuration-invalid')
                if issue not in item['configurationIssues']:
                    item['configurationIssues'].append(issue)
        for item in items.values():
            item['operations'].sort(key=lambda value: value['ref'])
            item['agents'].sort(key=lambda value: (value.get('displayName') or '', value.get('agentKey') or ''))
        return sorted(items.values(), key=lambda value: value['ref'])

    def get(self, ref):
        return next((item for item in self.list() if item['ref'] == ref), None)

    def for_profile(self, profile):
        """Return only the non-sensitive dependencies of one frozen Profile."""
        from .contracts import connector_manifest
        manifest = connector_manifest(profile)
        refs = {item['ref'] for item in manifest.get('connections', [])}
        return [item for item in self.list() if item['ref'] in refs]
