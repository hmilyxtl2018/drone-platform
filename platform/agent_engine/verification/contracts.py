"""Public v1 contracts and immutable internal verification materials."""
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

DSH_AGENT_KEYS = frozenset({
    'future-battlefield-codex', 'future-battlefield-analyst',
    'uav-capability-analyst', 'uav-design-engineer', 'uav-evidence-reviewer',
})
SCHEMA = 'verification.v1'
PROJECTION_SCHEMA = 'agent-public-contract.v1'
POLICY = 'agent-registration.v1'
RULE_VERSION = '1.1.0'
MODES = frozenset({'unattended-draft', 'expert-draft'})


class VerificationError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def parse_subject(body):
    if not isinstance(body, dict) or set(body) - {'subject', 'observedFingerprint'}:
        raise VerificationError('invalid-verification-request', 400)
    subject = body.get('subject')
    if not isinstance(subject, dict):
        raise VerificationError('unsupported-verification-subject', 400)
    kind = subject.get('kind')
    fields = {'agent-registration': {'kind', 'agent_key'}, 'run-contract': {'kind', 'run_id'},
              'invocation': {'kind', 'run_id', 'invocation_kind', 'invocation_id'},
              'report-evidence': {'kind', 'run_id', 'artifact_id'}, 'run-integrity': {'kind', 'run_id'}}
    if not isinstance(kind, str) or kind not in fields or set(subject) != fields[kind]:
        raise VerificationError('unsupported-verification-subject', 400)
    if kind != 'agent-registration':
        if body.get('observedFingerprint') is not None:
            raise VerificationError('unexpected-observed-fingerprint', 400)
        run = subject['run_id']
        if not isinstance(run, str) or not re.fullmatch(r'run-[A-Za-z0-9-]{1,100}', run):
            raise VerificationError('invalid-run-id', 400)
        if kind == 'invocation' and (not isinstance(subject['invocation_kind'], str) or
                subject['invocation_kind'] not in {'model', 'tool', 'skill'} or
                not isinstance(subject['invocation_id'], str) or
                not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', subject['invocation_id'])):
            raise VerificationError('invalid-invocation-id', 400)
        if kind == 'report-evidence' and (not isinstance(subject['artifact_id'], str) or
                not re.fullmatch(r'artifact-[A-Za-z0-9-]{1,100}', subject['artifact_id'])):
            raise VerificationError('invalid-report-artifact-id', 400)
        return run, None
    key = subject.get('agent_key')
    if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', key):
        raise VerificationError('invalid-agent-key', 400)
    if key not in DSH_AGENT_KEYS:
        raise VerificationError('verification-subject-not-found', 404)
    observed = body.get('observedFingerprint')
    if observed is not None and (not isinstance(observed, str) or
            not re.fullmatch(r'sha256:[a-f0-9]{64}', observed)):
        raise VerificationError('invalid-observed-fingerprint', 400)
    return key, observed


def fingerprint(projection):
    goal = projection['goalContract']
    canonical = {
        'agent_key': projection['agent_key'], 'current_version': projection['current_version'],
        'goalContract': {k: goal[k] for k in ('contractId', 'revision', 'contentHash')},
        'allowedModes': sorted(set(projection['allowedModes'])),
        'budgetLimits': projection['budgetLimits'],
    }
    value = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return 'sha256:' + hashlib.sha256(value.encode('utf-8')).hexdigest()


def overall(checks):
    required = [x for x in checks if x['required'] and x['status'] != 'not-applicable']
    if any(x['status'] == 'failed' for x in required):
        return 'failed'
    if not required or any(x['status'] != 'passed' for x in required):
        return 'inconclusive'
    return 'passed'


@dataclass(frozen=True)
class EvidenceBundle:
    """JSON bytes keep nested records immutable and prevent accidental mutation."""
    agent_key: str
    facts_json: bytes
    missing: tuple[str, ...] = ()

    def facts(self) -> dict[str, Any]:
        return json.loads(self.facts_json)

    @classmethod
    def make(cls, key, facts, missing=()):
        return cls(key, json.dumps(facts, default=str, allow_nan=False).encode(), tuple(missing))
