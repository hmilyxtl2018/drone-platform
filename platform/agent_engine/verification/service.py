"""Bounded verification orchestration; deliberately independent of Run execution."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import secrets
import re
from threading import BoundedSemaphore
import time

from .adapters.registration import AgentRegistrationAdapter, public_projection
from .adapters.run_contract import RunContractAdapter
from .adapters.invocation import InvocationAdapter
from .adapters.report_evidence import ReportEvidenceAdapter
from .composite import RunIntegrity
from .audit import FileAuditSink
from .checks import registration_checks
from .contracts import (DSH_AGENT_KEYS, SCHEMA, PROJECTION_SCHEMA, POLICY, RULE_VERSION,
                        VerificationError, fingerprint, overall, parse_subject)
from .reader import read_snapshot


class VerificationService:
    def __init__(self, factory, *, audit=None, projector=None):
        self.factory = factory
        self.audit = audit or FileAuditSink()
        self.projector = projector or public_projection
        self.adapters = {'agent-registration': AgentRegistrationAdapter(),
                         'run-contract': RunContractAdapter(), 'invocation': InvocationAdapter(),
                         'report-evidence': ReportEvidenceAdapter(), 'run-integrity': RunIntegrity()}
        self._slots = BoundedSemaphore(4)
        source = b''.join((Path(__file__).parent / f).read_bytes() for f in (
            'contracts.py', 'reader.py', 'checks.py', 'service.py', 'composite.py', 'adapters/registration.py'))
        source += b''.join(p.read_bytes() for p in sorted((Path(__file__).parent / 'adapters').glob('*.py')))
        source += (Path(__file__).parent.parent / 'dsh_api.py').read_bytes()
        source += (Path(__file__).parent / 'audit.py').read_bytes()
        source += b''.join((Path(__file__).parent.parent / f).read_bytes() for f in (
            'goal_contract.py', 'task_intent.py', 'skills.py', 'execution_baseline.py',
            'evidence_repository.py', 'ontology_explorer.py', 'uav_report.py',
            'context_contract.py', 'ontology_semantics.py', 'ontology_quality.py'))
        self.build_hash = hashlib.sha256(source).hexdigest()

    @staticmethod
    def authorize(actor, channel):
        if not actor or getattr(actor, 'kind', None) != 'human':
            raise VerificationError('verification-authentication-required', 401)
        if channel not in {'dsh', 'admin'} or not any(actor.permits(r) for r in ('reviewer', 'operator', 'admin')):
            raise VerificationError('verification-subject-not-found', 404)

    def check(self, body, actor, *, channel):
        started = time.monotonic()
        verification_id = 'verify-' + secrets.token_hex(12)
        audit = {'verificationId': verification_id, 'event': 'verification',
                 'queriedAt': datetime.now(timezone.utc).isoformat(),
                 'actorId': getattr(actor, 'principal_id', None), 'channel': channel,
                 'policyId': POLICY, 'ruleVersion': RULE_VERSION, 'buildHash': self.build_hash}
        acquired = False
        try:
            self.authorize(actor, channel)
            key, observed = parse_subject(body)
            kind = body['subject']['kind']
            audit['subject'] = {'kind': kind, 'id': key}
            if kind in {'invocation', 'report-evidence'}:
                audit['subject'].update(runId=key, id=body['subject'].get('invocation_id', body['subject'].get('artifact_id')))
            if kind == 'invocation': audit['subject']['invocationKind'] = body['subject']['invocation_kind']
            audit['policyId'] = kind + '.v1'
            acquired = self._slots.acquire(blocking=False)
            if not acquired:
                raise VerificationError('verification-busy')
            with read_snapshot(self.factory) as ctx:
                ctx.actor, ctx.channel = actor, channel
                if kind != 'agent-registration':
                    loaded = self.adapters[kind].load(body['subject'], ctx)
                    checks = loaded['checks']
                    references = {}
                    for name, value in loaded['authority'].items():
                        pattern = (r'[a-f0-9]{64}' if name.endswith('Hash') else
                                   r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?' if name == 'profileVersion' else
                                   r'artifact-[A-Za-z0-9-]{1,100}')
                        references[name] = value if isinstance(value, str) and re.fullmatch(pattern, value) else None
                    # Only bounded identifiers leave the internal evidence context.
                    for item in checks:
                        item['evidenceRefs'] = [ref for ref in item.get('evidenceRefs', [])
                            if isinstance(ref, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', ref)]
                        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,400}', item['checkId']):
                            item['checkId'] = 'check-' + hashlib.sha256(item['checkId'].encode()).hexdigest()[:24]
                    result = {
                        'schemaVersion': SCHEMA, 'verificationId': verification_id,
                        'subject': audit['subject'], 'status': overall(checks),
                        'verifiedScope': {
                            'run-contract': ['frozen-contract', 'delegation-chain'],
                            'invocation': ['frozen-contract', 'invocation-receipt', 'budget-records'],
                            'report-evidence': ['frozen-contract', 'report-artifact', 'graph-projection', 'source-quote-location'],
                            'run-integrity': ['frozen-contract', 'required-node-records', 'delegation-chain',
                                              'invocation-receipts', 'report-evidence'],
                        }[kind],
                        'authority': {'source': 'database', 'references': references},
                        'checker': {'policyId': kind + '.v1', 'ruleVersion': RULE_VERSION, 'buildHash': self.build_hash},
                        'queriedAt': audit['queriedAt'], 'checks': checks,
                        'coverage': {'mandatoryPassed': sum(x['status'] == 'passed' for x in checks),
                                     'mandatoryFailed': sum(x['status'] == 'failed' for x in checks),
                                     'mandatoryUnknown': sum(x['status'] == 'inconclusive' for x in checks),
                                     'notApplicable': sum(x['status'] == 'not-applicable' for x in checks)},
                        'currentness': loaded.get('currentness', 'unknown'),
                        'runStatus': loaded['run']['status'], 'semanticValidation': 'not-assessed',
                        'observationComparison': {'status': 'not-supplied'},
                        'limitations': loaded.get('limitations', []),
                    }
                    if loaded.get('scan'): result['scan'] = loaded['scan']
                else:
                    tables = ctx.available()
                    bundle = self.adapters['agent-registration'].load(body['subject'], ctx, tables=tables)
                    try:
                        projection = self.projector(bundle)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        projection = None
                    checks = registration_checks(bundle, projection)
                    facts = bundle.facts()
                    goal = facts.get('goal') or {}
                    version = facts['definition']['current_version']
                    safe_version = version if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', version) else None
                    goal_id = goal.get('contract_id')
                    safe_goal_id = goal_id if isinstance(goal_id, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', goal_id) else None
                    result = {'schemaVersion': SCHEMA, 'verificationId': verification_id,
                        'subject': audit['subject'], 'status': overall(checks),
                        'verifiedScope': ['registration', 'goal-reference', 'allowed-modes', 'effective-budget', 'public-projection'],
                        'authority': {'source': 'database', 'references': {
                            'profileVersion': safe_version,
                            'goalContractId': safe_goal_id,
                            'goalRevision': goal.get('revision') if type(goal.get('revision')) is int else None}},
                        'checker': {'policyId': POLICY, 'ruleVersion': RULE_VERSION, 'buildHash': self.build_hash},
                        'queriedAt': audit['queriedAt'], 'projectionSchemaVersion': PROJECTION_SCHEMA,
                        'coverage': {'mandatoryPassed': sum(x['status'] == 'passed' for x in checks),
                                     'mandatoryFailed': sum(x['status'] == 'failed' for x in checks),
                                     'mandatoryUnknown': sum(x['status'] == 'inconclusive' for x in checks)},
                        'checks': checks, 'semanticValidation': 'not-assessed',
                        'observationComparison': {'status': 'not-supplied'}}
                    if result['status'] == 'passed':
                        result['contractFingerprint'] = fingerprint(projection)
                        result['contract'] = {k: projection[k] for k in (
                            'agent_key', 'current_version', 'allowedModes', 'budgetLimits')}
                        result['contract']['goalContract'] = {k: projection['goalContract'][k]
                            for k in ('contractId', 'revision', 'contentHash')}
                        if observed:
                            result['observationComparison'] = {'status': 'same' if observed == result['contractFingerprint'] else 'different',
                                                               'referenceSource': 'caller-supplied'}
                    elif observed:
                        result['observationComparison'] = {'status': 'inconclusive', 'referenceSource': 'caller-supplied'}
            audit.update(status=result['status'], contractFingerprint=result.get('contractFingerprint'),
                         authority=result['authority'], reasonCodes=[x['reasonCode'] for x in checks])
        except Exception as exc:
            error = exc if isinstance(exc, VerificationError) else VerificationError('verification-read-unavailable')
            audit.update(status='error', reasonCode=error.code)
            audit['durationMs'] = round((time.monotonic() - started) * 1000)
            self.audit.record(audit)
            raise error from None
        finally:
            if acquired:
                self._slots.release()
        audit['durationMs'] = round((time.monotonic() - started) * 1000)
        self.audit.record(audit)
        result['audit'] = {'recordId': verification_id, 'status': 'recorded'}
        return result

    def capabilities(self, actor):
        self.authorize(actor, 'dsh')
        if not self._slots.acquire(blocking=False):
            raise VerificationError('verification-busy')
        try:
            with read_snapshot(self.factory) as ctx:
                tables, profiles = ctx.available(), []
                queried = datetime.now(timezone.utc).isoformat()
                for key in sorted(DSH_AGENT_KEYS):
                    try:
                        bundle = self.adapters['agent-registration'].load({'agent_key': key}, ctx, tables=tables)
                    except VerificationError as exc:
                        if exc.status == 404:
                            continue
                        raise
                    value = self.projector(bundle)
                    checks = registration_checks(bundle, value)
                    if overall(checks) != 'passed':
                        raise VerificationError('capability-contract-unverified')
                    value.update(projectionSchemaVersion=PROJECTION_SCHEMA,
                                 contractFingerprint=fingerprint(value), queriedAt=queried)
                    profiles.append(value)
                return profiles
        except VerificationError:
            raise
        except Exception:
            raise VerificationError('capability-contract-unavailable') from None
        finally:
            self._slots.release()
