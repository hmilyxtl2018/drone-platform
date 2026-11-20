"""Explicit implementation fingerprint for the ontology cognitive suite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    'platform/agent_engine/cognitive_implementation.py',
    'platform/agent_engine/cognitive_runtime.py',
    'platform/agent_engine/cognitive_contracts.py',
    'platform/agent_engine/cognitive_legacy_adapter.py',
    'platform/agent_engine/cognitive_revision.py',
    'platform/agent_engine/cognitive_budget.py',
    'platform/agent_engine/cognitive_termination.py',
    'platform/agent_engine/cognitive_admission.py',
    'platform/agent_engine/cognitive_profiles.py',
    'platform/agent_engine/cognitive_identity.py',
    'platform/agent_tools/cognitive.py',
    'platform/contracts/ontology-cognitive.v1.schema.json',
    'platform/agent_engine/execution_baseline.py',
    'platform/agent_engine/orchestration.py',
    'platform/agent_engine/executor.py',
    'platform/agent_engine/phase3_repository.py',
    'platform/agent_engine/engine.py',
    'platform/agent_engine/tool_gateway.py',
    'platform/agent_engine/execution_service.py',
    'platform/agent_engine/configuration.py',
    'platform/agent_engine/repository.py',
    'platform/agent_engine/task_intent.py',
    'platform/agent_engine/validation.py',
    'platform/agent_engine/providers/deepseek.py',
    'platform/agent_engine/providers/configuration.py',
)


def manifest() -> dict:
    files = []
    for relative in FILES:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        files.append({'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    files.sort(key=lambda item: item['path'])
    payload = {'schemaVersion': 'ontology-cognitive-implementation.v1', 'files': files}
    payload['implementationHash'] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return payload


def implementation_hash() -> str:
    return manifest()['implementationHash']


def assert_profile_implementation(profile) -> None:
    from .cognitive_identity import AGENT_KEYS
    if profile.agent_key not in AGENT_KEYS:
        return
    frozen = profile.model_policy.get('config_json', {}).get('cognitiveImplementationHash')
    if not frozen or frozen != implementation_hash():
        raise ValueError('cognitive implementation differs from its frozen Profile; publish a reviewed successor')
