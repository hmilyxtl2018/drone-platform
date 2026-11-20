from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS
from agent_engine.cognitive_identity import AGENT_KEYS as COGNITIVE_AGENT_KEYS

import asyncio
import json
import secrets
import re
import time
from copy import copy, deepcopy

from jsonschema import ValidationError
from .approvals import HumanApprovalRequired
from .connectors.runtime import attempt_scope
from .validation import ValidationEngine


class ToolGateway:
    def __init__(self, repository):
        self.repository = repository
        self.handlers = {}
        self.schemas = {}

    def register(self, key, handler, input_schema=None, output_schema=None):
        self.handlers[key] = handler
        self.schemas[key] = (input_schema or {}, output_schema or {})

    def _web_cache_hint(self, ctx, key, handler, payload):
        """Only a known local scrape adapter can bypass remote admission.

        The adapter still validates its source scope and returns the saved
        artifact. A model-provided cache flag never grants this bypass.
        """
        if key != 'web.scrape':
            return False
        from agent_tools.web import FirecrawlRuntimeTools
        if (type(getattr(handler, '__self__', None)) is not FirecrawlRuntimeTools
                or getattr(handler, '__func__', None) is not FirecrawlRuntimeTools.scrape):
            return False
        from urllib.parse import urlsplit, urlunsplit
        try:
            value = (payload.get('arguments') or {}).get('url')
            parts = urlsplit(value)
            hostname = (parts.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
            if parts.scheme != 'https' or not hostname or parts.username is not None or parts.password is not None:
                return False
            url = urlunsplit(('https', hostname, parts.path or '/', parts.query, ''))
        except (AttributeError, TypeError, ValueError, UnicodeError):
            return False
        if url in (getattr(ctx, 'state', {}) or {}).get('webSourceSnapshots', {}):
            return True
        if hasattr(self.repository, 'list_artifacts'):
            for artifact in self.repository.list_artifacts(ctx.run_id):
                if artifact.get('artifact_type') != 'web-source-snapshot':
                    continue
                content = artifact.get('content_json') or {}
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except ValueError:
                        continue
                if isinstance(content, dict) and content.get('sourceUri') == url:
                    return True
        return False

    @staticmethod
    def _connector_error_code(exc):
        """Classify trusted adapter messages without returning their text."""
        if isinstance(exc, TimeoutError):
            return 'connector-timeout'
        if isinstance(exc, ValidationError):
            return 'connector-schema-invalid'
        message = str(exc)
        if isinstance(exc, PermissionError):
            if message == 'HTTP tool authentication rejected' or re.fullmatch(
                    r'Firecrawl authentication/access rejected \(HTTP (401|403)\)', message):
                return 'connector-authentication-rejected'
            return 'connector-policy-denied'
        if isinstance(exc, ValueError):
            if message in {'Firecrawl returned invalid JSON', 'Firecrawl returned an unsuccessful response',
                    'Firecrawl search response has no web result list', 'Firecrawl scrape response has no source object',
                    'Firecrawl source metadata is invalid', 'HTTP tool returned invalid JSON',
                    'HTTP tool must return a JSON object'}:
                return 'provider-invalid-output'
            return 'connector-input-invalid'
        if message == 'Firecrawl temporary HTTP 429':
            return 'connector-rate-limit'
        if exc.__class__.__name__ == 'HttpToolTransportError' or message == 'Firecrawl transport failed':
            return 'connector-transport-failed'
        return 'connector-operation-failed'

    @staticmethod
    def _harness_tool_grant(ctx, key, step_key, tool_version=None, *, consume=False):
        contract = getattr(ctx.profile, 'harness_contract', {}) or {}
        if not contract:
            return None
        grants = [item for item in contract.get('toolGrants', [])
                  if item.get('tool_key') == key and
                  (tool_version is None or item.get('tool_version') == tool_version)]
        if contract.get('defaultDeny', True) and not grants:
            raise PermissionError(f'tool is not granted by the Harness contract: {key}')
        if grants and all(item.get('permission') not in {'invoke','read','write'} for item in grants):
            raise PermissionError(f'tool invocation permission is denied by the Harness contract: {key}')
        if step_key and consume:
            budgets = {item.get('step_key'): item for item in contract.get('stepBudgets', [])}
            budget = budgets.get(step_key)
            if budget and int(budget.get('tool_calls', 0) or 0) > 0:
                # The process-local count is conservative; the durable root
                # budget remains the final shared limit across child Runs.
                current = sum(1 for item in getattr(ctx, 'state', {}).get('_harnessToolCalls', [])
                              if item == step_key)
                if current >= int(budget['tool_calls']):
                    raise RuntimeError(f'Harness step tool budget exceeded: {step_key}')
                getattr(ctx, 'state', {}).setdefault('_harnessToolCalls', []).append(step_key)
        return grants[0] if grants else None

    def authorize(self, ctx, key, step_key=None, *, consume=False):
        if hasattr(self.repository, 'assert_task_dispatch'):
            self.repository.assert_task_dispatch(ctx.run_id, tool=key,
                operation='search-public' if self._ledger_kind(key) in {'search','scrape','http'} else 'read-evidence')
        binding = ctx.profile.tools.get(key)
        if not binding or not binding.enabled:
            raise PermissionError(f'tool is not enabled: {key}')
        grant = self._harness_tool_grant(ctx, key, step_key, binding.version, consume=consume)
        permitted = ctx.profile.resource_policy.get('permitted_risk_levels_json', ['low', 'medium'])
        if isinstance(permitted, str):
            permitted = json.loads(permitted)
        if binding.risk_level not in set(permitted):
            raise PermissionError(f'tool risk level is not permitted: {key} ({binding.risk_level})')
        if key not in self.handlers and binding.protocol not in {'http', 'https'}:
            raise ValueError(f'no adapter registered: {key}')
        if binding.protocol in {'http', 'https'} and not binding.endpoint:
            from .connectors.contracts import BUILTIN_UAV_TOOL_KEYS
            research = ctx.profile.model_policy.get('config_json', {}).get('engineMode') == 'research-v1'
            legacy = (not research and key in BUILTIN_UAV_TOOL_KEYS and key in self.handlers
                      and not binding.config.get('executionTransport'))
            if not legacy:
                raise ValueError('remote tool endpoint is missing')
        needs_approval = binding.approval_required or binding.access_mode != 'read_only' or binding.risk_level in {'high', 'critical'}
        if grant and grant.get('approvalRequired'):
            needs_approval = True
        if needs_approval:
            action = f'tool:{key}'
            matching = [a for a in self.repository.list_approvals(ctx.run_id) if a['action'] == action and a.get('step_key') == step_key]
            latest = matching[-1] if matching else None
            if latest and latest['status'] == 'rejected':
                raise PermissionError(f'approval rejected for {key}')
            if not latest or latest['status'] != 'approved':
                aid = latest['approval_id'] if latest and latest['status'] == 'pending' else self.repository.request_approval(ctx.run_id, action, f'tool requires approval: {key}', 'agent-engine', step_key)
                raise HumanApprovalRequired(aid, action)
        return binding

    @staticmethod
    def _ledger_kind(key):
        if key.startswith('web.search') or key.endswith('.search'):
            return 'search'
        if key.startswith('web.scrape') or key.endswith('.scrape'):
            return 'scrape'
        if key.startswith('http.') or key.startswith('source.'):
            return 'http'
        return 'tool'

    async def invoke(self, ctx, step_run_id, key, args, step_key=None):
        binding = self.authorize(ctx, key, step_key, consume=True)
        research = ctx.profile.model_policy.get('config_json', {}).get('engineMode') == 'research-v1'
        # Select transport from this Run's frozen binding. A handler cached by
        # tool key from another version must not override its configured protocol.
        # Phase 2 persisted built-in UAV bindings as http with no endpoint.
        # Their existing immutable snapshots retain the documented in-process
        # implementation; research/remote bindings always follow their protocol.
        from .connectors.contracts import BUILTIN_UAV_TOOL_KEYS
        legacy_builtin = (not research and key in BUILTIN_UAV_TOOL_KEYS and not binding.endpoint
                          and key in self.handlers and not binding.config.get('executionTransport'))
        if binding.protocol in {'http', 'https'} and not legacy_builtin:
            from .adapters.http import HttpToolAdapter
            registered = self.handlers.get(key)
            handler = registered if isinstance(registered, HttpToolAdapter) else HttpToolAdapter(self.repository, key)
        else:
            handler = self.handlers[key]
        max_calls = int(ctx.profile.convergence.get('max_tool_calls', 0) or 0)
        retry_limit = min(binding.retry_limit, int(ctx.profile.resource_policy.get('max_retries_per_tool', binding.retry_limit)))
        baseline = getattr(ctx.profile, 'execution_baseline', {}) or {}
        node = next((item for item in (baseline.get('graph', {}) or {}).get('nodes', [])
                     if item.get('id') == step_key), {}) if isinstance(baseline, dict) else {}
        node_budget = node.get('budget', {}) if isinstance(node, dict) else {}
        if 'max_retries' in node_budget:
            retry_limit = min(retry_limit, max(0, int(node_budget['max_retries'])))
        # Mutating adapters require their own idempotency contract before retries.
        if binding.access_mode != 'read_only':
            retry_limit = 0
        payload = deepcopy(args)
        # A tool receives the same bounded context contract as a model.  The
        # legacy envelope remains available for old bindings, but the context
        # identity is always attached to the audit record when supported.
        from .context_builder import ContextBuilder
        from .context_contract import invocation_key
        operation_key = invocation_key(f"tool:{key}:{step_key or key}:{getattr(ctx, 'iteration', 0)}",
                                       payload, binding.output_schema)
        direct_dependencies = []
        if key.startswith('ontology.') and isinstance(payload, dict):
            entity = payload.get('entityId') or payload.get('uavId')
            if isinstance(entity, str) and entity:
                direct_dependencies.append({
                    'dependencyId': entity,
                    'dependencyKind': 'ontology-entity',
                    'versionRef': (getattr(ctx, 'state', {}) or {}).get('ontologyRevision'),
                    'readMode': 'absence' if payload.get('negativeQuery') is True else 'read',
                })
            query = payload.get('query') or payload.get('metric')
            if isinstance(query, str) and query:
                direct_dependencies.append({
                    'dependencyId': f'{key}:{query}',
                    'dependencyKind': 'ontology-query',
                    'predicate': {'query': query},
                    'readMode': 'absence' if payload.get('negativeQuery') is True else 'read',
                })
        evidence = (getattr(ctx, 'state', {}) or {}).get('evidence', [])
        if not evidence and hasattr(self.repository, 'evidence_bundle'):
            try:
                evidence = self.repository.evidence_bundle(ctx.run_id).get('candidates', [])
            except Exception:
                evidence = []
        manifest = ContextBuilder(self.repository).build(
            ctx, node_key=step_key or key,
            operation_key=operation_key,
            request=payload,
            output_schema=output_schema if 'output_schema' in locals() else binding.output_schema,
            evidence=evidence,
            upstream=(getattr(ctx, 'input', {}) or {}).get('upstreamArtifacts', []),
            dependencies=direct_dependencies, step_run_id=step_run_id)
        context_id = getattr(ctx, 'context_id', None)
        manifest_hash = manifest.get('manifestHash')
        operation_key = manifest.get('operationKey')
        # In research mode {} is an explicit frozen contract, not a missing
        # value. Phase 2 snapshots retain their built-in schema compatibility.
        input_schema = binding.input_schema if research else binding.input_schema or self.schemas.get(key, ({}, {}))[0]
        output_schema = binding.output_schema if research else binding.output_schema or self.schemas.get(key, ({}, {}))[1]
        context_options = (ctx.profile.model_policy.get('config_json', {}) or {}).get('contextPolicy', {})
        if context_options.get('toolProjection') is True and isinstance(payload, dict):
            properties = input_schema.get('properties', {}) if isinstance(input_schema, dict) else {}
            if 'input' in properties:
                payload['input'] = deepcopy(manifest.get('task', {}))
            if 'state' in properties:
                payload['state'] = deepcopy(manifest.get('coordination', {}))
        for attempt in range(retry_limit + 1):
            attempt_id = "tool-attempt-" + secrets.token_hex(12)
            cache_only = self._web_cache_hint(ctx, key, handler, payload)
            async with attempt_scope(self.repository, ctx, kind="tool", key=key, version=binding.version,
                    operation_key=operation_key, attempt_key=attempt_id, step_run_id=step_run_id,
                    timeout_seconds=binding.timeout_seconds, cache_only=cache_only) as connector_receipt:
                if hasattr(self.repository, 'assert_task_dispatch'):
                    self.repository.assert_task_dispatch(ctx.run_id, tool=key,
                        operation='search-public' if self._ledger_kind(key) in {'search','scrape','http'} else 'read-evidence')
                if max_calls and ctx.tool_calls >= max_calls:
                    raise RuntimeError(f'max tool calls exceeded: {max_calls}')
                config = ctx.profile.model_policy.get('config_json', {})
                if (config.get('engineMode') == 'research-v1' or config.get('agentLayer') == 'action'
                        or ctx.profile.agent_key in FUTURE_AGENT_KEYS
                        or ctx.profile.agent_key in COGNITIVE_AGENT_KEYS):
                    # Durable, shared budget includes every actual adapter attempt,
                    # independently of the process-local counter and HTTP budgets.
                    try:
                        self.repository.reserve_budget(ctx.run_id, {'tool_calls': 1}, step_key=step_key)
                    except TypeError as exc:
                        # Small in-memory test repositories and legacy adapters have
                        # the pre-P0 signature; production Phase3Repository supports
                        # the durable node reservation.
                        if 'step_key' not in str(exc):
                            raise
                        self.repository.reserve_budget(ctx.run_id, {'tool_calls': 1})
                ctx.tool_calls += 1
                if hasattr(self.repository, "update_invocation_slot"):
                    self.repository.update_invocation_slot(ctx.run_id, operation_key, "running", increment=True)
                start = time.perf_counter()
                try:
                    ValidationEngine().schema(payload, input_schema)
                    call_ctx = copy(ctx)
                    call_ctx.current_step_key = step_key
                    connector_receipt.start_call()
                    result = await asyncio.wait_for(handler(deepcopy(payload), call_ctx), timeout=binding.timeout_seconds)
                    ValidationEngine().schema(result, output_schema)
                    if (isinstance(result, dict) and result.get('cacheHit') is True
                            and self._web_cache_hint(ctx, key, handler, payload)):
                        connector_receipt.cache_hit()
                except asyncio.CancelledError:
                    if hasattr(self.repository, "update_invocation_slot"):
                        # Cancellation can race an external HTTP request.  Keep
                        # the slot unknown so recovery never silently duplicates
                        # a possibly accepted remote operation.
                        self.repository.update_invocation_slot(ctx.run_id, operation_key, "unknown")
                    call_id = self._record_tool_call(ctx, step_run_id, key, binding, 'cancelled', payload,
                                           error='runtime deadline or worker cancellation',
                                           latency=int((time.perf_counter()-start)*1000), retry=attempt,
                                           context_id=context_id, manifest_hash=manifest_hash,
                                           operation_key=operation_key)
                    connector_receipt.record("cancelled", error_code="connector-call-cancelled", call_id=call_id)
                    self._record_budget_ledger(ctx, key, step_key, operation_key, attempt, 'cancelled',
                                               {'tool_calls': 1}, {}, 'runtime deadline or worker cancellation', True)
                    raise
                except Exception as exc:
                    status = 'timeout' if isinstance(exc, TimeoutError) else 'failed'
                    if hasattr(self.repository, "update_invocation_slot"):
                        # A remote POST can have been accepted even when the
                        # response is lost.  The HTTP adapter intentionally
                        # normalises timeout and transport failures to safe
                        # built-in exceptions; keep the durable slot unknown for
                        # either case so recovery never silently duplicates an
                        # external side effect.  Deterministic HTTP status and
                        # schema/permission failures remain failed.
                        transport_error = exc.__class__.__name__ == "HttpToolTransportError"
                        external_uncertain = (binding.protocol in {'http', 'https'}
                                              and (transport_error or isinstance(exc, (TimeoutError,
                                                                                         asyncio.TimeoutError))))
                        if external_uncertain:
                            self.repository.update_invocation_slot(ctx.run_id, operation_key, "unknown")
                        elif attempt >= retry_limit or isinstance(exc, (ValidationError, ValueError, PermissionError)):
                            self.repository.update_invocation_slot(ctx.run_id, operation_key, "failed")
                    call_id = self._record_tool_call(ctx, step_run_id, key, binding, status, payload,
                                           error=str(exc) or 'tool timeout', latency=int((time.perf_counter()-start)*1000), retry=attempt,
                                           context_id=context_id, manifest_hash=manifest_hash,
                                           operation_key=operation_key)
                    safe_code = self._connector_error_code(exc)
                    connector_receipt.record(status, error_code=safe_code, call_id=call_id)
                    self._record_budget_ledger(ctx, key, step_key, operation_key, attempt, status,
                                               {'tool_calls': 1}, {}, str(exc)[:1000],
                                               status in {'timeout'} and binding.protocol in {'http', 'https'})
                    if isinstance(exc, (ValidationError, ValueError, PermissionError)) or attempt >= retry_limit:
                        raise
                else:
                    if hasattr(self.repository, "update_invocation_slot"):
                        self.repository.update_invocation_slot(ctx.run_id, operation_key, "succeeded")
                    call_id = self._record_tool_call(ctx, step_run_id, key, binding, 'succeeded', payload, result,
                                           latency=int((time.perf_counter()-start)*1000), retry=attempt,
                                           context_id=context_id, manifest_hash=manifest_hash,
                                           operation_key=operation_key)
                    connector_receipt.record("succeeded", call_id=call_id)
                    self._record_budget_ledger(ctx, key, step_key, operation_key, attempt, 'succeeded',
                                               {'tool_calls': 1}, {'tool_calls': 1})
                    return result
            await asyncio.sleep(min(2 ** attempt, 8))

    def _record_budget_ledger(self, ctx, key, step_key, operation_key, attempt, status,
                              estimate, actual, reason=None, incomplete=False):
        recorder = getattr(self.repository, 'record_budget_ledger', None)
        if recorder is None:
            return
        try:
            recorder(ctx.run_id, kind=self._ledger_kind(key), status=status,
                     estimate=estimate, actual=actual,
                     operation_key=f'{operation_key}:attempt:{attempt}', step_key=step_key,
                     retry_count=attempt, cancellation_reason=reason, incomplete=incomplete)
        except Exception:
            # Ledger persistence must never turn a successful provider result
            # into a second execution attempt. The durable tool/model audit is
            # already recorded; operations can alert on a missing ledger row.
            return

    def _record_tool_call(self, ctx, step_run_id, key, binding, status, payload, output=None,
                          error=None, latency=0, retry=0, context_id=None, manifest_hash=None,
                          operation_key=None):
        try:
            return self.repository.tool_call(ctx.run_id, step_run_id, key, binding.version, status,
                payload, output, error, latency, retry, context_id, manifest_hash, operation_key)
        except TypeError as exc:
            # In-memory/legacy repositories have the original positional
            # recorder; do not make them depend on migration 055.
            if 'positional' not in str(exc) and 'argument' not in str(exc):
                raise
            return self.repository.tool_call(ctx.run_id, step_run_id, key, binding.version, status,
                payload, output, error, latency, retry)
