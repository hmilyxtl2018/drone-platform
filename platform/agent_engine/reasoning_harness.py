"""Observable, evidence bounded reasoning on the existing execution DAG.

Checkpoints contain decisions and audit summaries, never hidden model thoughts.
Provider/tool calls continue to use the existing invocation and budget ledgers.
"""
from __future__ import annotations

from copy import deepcopy
import json

from .reasoning_gates import QualityGateEngine, gate_summary
from .reasoning_repository import reasoning_hash


def stage_for(profile, step_key):
    policy = getattr(profile, 'reasoning_policy', {}) or {}
    return next((s for s in policy.get('stages', []) if s.get('stepKey', s.get('key')) == step_key), None)


def _unique(items):
    result, seen = [], set()
    for item in items:
        digest = json.dumps(item, sort_keys=True, default=str)
        if digest not in seen:
            seen.add(digest)
            result.append(deepcopy(item))
    return result


def model_reasoning_context(ctx, step_key):
    """Bounded data projection; source text cannot change the frozen contract."""
    policy = getattr(ctx.profile, 'reasoning_policy', {}) or {}
    if not policy:
        return None
    state = ctx.state or {}
    summary = state.get('reasoning', {})
    return {
        'policyId': policy.get('policyId'), 'policyVersion': policy.get('version'),
        'stage': deepcopy(stage_for(ctx.profile, step_key) or {}),
        'iteration': int(ctx.iteration) + 1,
        'task': {k: deepcopy(ctx.input[k]) for k in ('question', 'objective', 'scope', 'timeHorizon', 'perspectives') if k in ctx.input},
        'ontologyContract': deepcopy(policy.get('ontology', {})),
        'knownEvidence': [str(v.get('evidenceId') or v.get('claimId')) for v in state.get('evidence', []) if isinstance(v, dict)][:200],
        'missingEvidence': [str(v.get('reason', ''))[:180] for v in state.get('capabilityGaps', []) if isinstance(v, dict)][:32],
        'previousCheckpoint': {k: deepcopy(summary.get(k)) for k in ('stageKey', 'decision', 'nextAction', 'quality') if k in summary},
        'outputRequirements': ['use-only-bound-evidence-ids', 'declare-conditions-counterarguments-and-unknowns',
                               'propose-relations-with-endpoints-and-evidence', 'never-promote-to-truth-or-publication'],
    }


class ReasoningHarness:
    def __init__(self, repository):
        self.repo = repository
        self.gates = QualityGateEngine()

    def _saved(self, ctx, key, prior=None):
        """Recover a frozen finalization bundle, including pre-checkpoint crashes."""
        if prior:
            artifact = self.repo.get_artifact(ctx.run_id, prior.get('resultArtifactId'))
            if not artifact:
                raise ValueError('reasoning recovery result artifact absent')
            artifacts = [artifact]
        else:
            artifacts = []
            for row in reversed(self.repo.list_artifact_summaries(ctx.run_id)):
                if row.get('artifact_type') == 'reasoning-stage-result':
                    artifacts.append(self.repo.get_artifact(ctx.run_id, row['artifact_id']))
        for artifact in artifacts:
            if not artifact or artifact.get('run_id') != ctx.run_id:
                raise ValueError('reasoning recovery artifact ownership mismatch')
            content = artifact['content_json']
            if reasoning_hash(content) != artifact['content_hash']:
                raise ValueError('reasoning recovery artifact hash mismatch')
            if content.get('operationKey') == key:
                if content.get('schemaVersion') != 1:
                    raise ValueError('unsupported reasoning recovery bundle')
                return artifact
        if prior:
            raise ValueError('reasoning recovery operation mismatch')
        return None

    def before_step(self, ctx, step):
        stage = stage_for(ctx.profile, step.key)
        if not stage:
            if getattr(ctx.profile, 'reasoning_policy', {}):
                raise ValueError('execution node has no frozen reasoning stage: ' + step.key)
            return None
        iteration = int(ctx.iteration) + 1
        key = f'reasoning:{ctx.run_id}:{stage["key"]}:{iteration}'
        # A crashed worker can reuse the completed result without another
        # model/tool call. The immutable checkpoint still refers to its first attempt.
        prior = next((v for v in self.repo.list_reasoning_checkpoints(ctx.run_id) if v['operationKey'] == key), None)
        saved = self._saved(ctx, key, prior)
        if saved:
            return deepcopy(saved['content_json']['result'])
        self.repo.enter_reasoning_stage(ctx.run_id, stage['key'], iteration, key + ':enter')
        return None

    def _trusted(self, ctx):
        mapping = {'evidence_id': 'evidenceId', 'claim_id': 'claimId', 'source_artifact_id': 'sourceArtifactId',
                   'source_artifact_hash': 'sourceArtifactHash', 'content_hash': 'contentHash', 'locator_json': 'locator',
                   'source_uri': 'sourceUri', 'published_at': 'publishedAt', 'independence_status': 'independenceStatus'}
        rows = self.repo.list_future_evidence_references(ctx.run_id)
        output = []
        for row in rows:
            value = {mapping.get(k, k): deepcopy(v) for k, v in row.items()}
            value['publicationDateStatus'] = 'known' if value.get('publishedAt') else 'unknown'
            output.append(value)
        return output

    @staticmethod
    def _relations(payload, findings):
        report = payload.get('judgmentReport') or payload
        ontology = report.get('ontology', {})
        if ontology.get('assertions'):
            return deepcopy(ontology['assertions'])
        rows = []
        for finding in findings:
            for relation in finding.get('relations', []):
                # Namespace local endpoints so different perspectives cannot
                # accidentally collapse two unrelated candidate entities.
                entities = {e['id']: e for e in finding.get('entities', [])}
                if relation.get('subject') not in entities or relation.get('object') not in entities:
                    continue
                row = deepcopy(relation)
                row['perspectiveKey'] = finding.get('perspectiveKey')
                row['subject'] = deepcopy(entities[row['subject']])
                row['object'] = deepcopy(entities[row['object']])
                row['conditions'] = deepcopy(row.get('conditions') or finding.get('conditions', []))
                row['truthStatus'] = 'unverified'
                rows.append(row)
        return rows

    def after_step(self, ctx, step, result):
        stage = stage_for(ctx.profile, step.key)
        if not stage:
            return result
        policy = ctx.profile.reasoning_policy
        iteration = int(ctx.iteration) + 1
        key = f'reasoning:{ctx.run_id}:{stage["key"]}:{iteration}'
        prior = next((v for v in self.repo.list_reasoning_checkpoints(ctx.run_id) if v['operationKey'] == key), None)
        saved = self._saved(ctx, key, prior)
        if saved:
            return self._finalize(ctx, step, prior, saved)
        report = result.get('judgmentReport') or result
        findings = report.get('findings') or result.get('perspectiveFindings') or []
        if not findings:
            findings = (ctx.state.get('steps', {}).get('analyze-by-perspective') or {}).get('perspectiveFindings', [])
        trusted = self._trusted(ctx)
        evidence = report.get('evidence') or trusted
        relations = self._relations(result, findings) if step.key in {'analyze-by-perspective', 'compose-judgment-report'} else []
        # Support-review proofs are reconstructed from this Run's or a completed
        # perspective Action's immutable calls and artifacts. Model fields never
        # count as proof, and a repository without this verifier fails closed.
        validated_support = []
        validator = getattr(self.repo, 'validate_support_reviews', None)
        if relations and callable(validator):
            validated_support = validator(ctx.run_id, relations)
        # Evidence IDs are validated by the append-only source-artifact trigger.
        # A syntactically plausible model-generated reference is not trusted.
        model = ctx.profile.model_policy.get('model_name') or ctx.profile.model_policy.get('model')
        context = {'input': ctx.input, 'taskContract': ctx.input,
                   'ontologyContract': policy.get('ontology', {}), 'validatedEvidence': trusted,
                   'evidence': evidence, 'findings': findings,
                   'candidateRelations': relations, 'validatedSupportReviews': validated_support,
                   'sourcePolicy': (ctx.profile.model_policy.get('config_json') or {}).get('sourcePolicy', {}),
                   'reviewMetadata': {'generatorModel': model, 'reviewerModel': model}}
        results = self.gates.evaluate(policy, stage_key=stage['key'], payload=result, context=context)
        for gate in results:
            gate['blocking'] = gate['status'] == 'failed' and gate['severity'] == 'blocker' and gate['affectsPublication']
        if results:
            self.repo.reserve_budget(ctx.run_id, {'gate_checks': len(results)}, operation_key=key + ':gates', step_key=step.key)
        summary = gate_summary(results)
        failed = [v for v in results if v['status'] == 'failed']
        stop = any(v['action'] == 'stop' for v in failed)
        decision = 'stop' if stop else ('retain-gaps' if failed else 'continue')
        known = {str(e.get('evidenceId') or e.get('claimId')) for e in trusted}
        # Unbound proposals stay in the checkpoint; only supported, run-owned
        # references can enter the reviewable candidate ledger.
        bound = [r for r in relations if r.get('evidenceIds') and set(r['evidenceIds']) <= known]
        checkpoint_data = {
                'iteration': iteration, 'stepKey': step.key,
                'knownEvidence': sorted(known)[:500],
                'missingEvidence': _unique([{'reason': g['gateId'], 'action': g['action']} for g in failed]
                                           + [{'reason': str(v.get('reason', 'unknown'))[:200]} for v in result.get('gaps', [])])[:100],
                'candidateRelations': [{k: deepcopy(v.get(k)) for k in ('id', 'subject', 'predicate', 'object', 'evidenceIds')} for v in relations][:200],
                'uncertainties': _unique(report.get('unknowns', []) + [u for f in findings for u in f.get('unknowns', [])])[:80],
                'decision': decision, 'nextAction': 'stop-with-reason' if stop else ('bounded-evidence-or-retain-gap' if failed else 'next-dag-node'),
                'quality': summary, 'truthVerified': False,
            }
        bundle = {'schemaVersion': 1, 'operationKey': key, 'stageKey': stage['key'],
                  'stepRunId': ctx.current_step_run_id, 'result': deepcopy(result),
                  'checkpoint': checkpoint_data, 'gates': results, 'relations': bound}
        artifact_id = self.repo.add_artifact(ctx.run_id, 'reasoning-stage-result', bundle, ctx.current_step_run_id)
        return self._finalize(ctx, step, None, self.repo.get_artifact(ctx.run_id, artifact_id))

    def _finalize(self, ctx, step, prior, artifact):
        """Replay only immutable data; partial writes are idempotent repository ops."""
        bundle = artifact['content_json']
        result = deepcopy(bundle['result'])
        checkpoint_data = {**deepcopy(bundle['checkpoint']), 'resultArtifactId': artifact['artifact_id']}
        checkpoint = prior or self.repo.append_reasoning_checkpoint(
            ctx.run_id, bundle['stageKey'], checkpoint_data, bundle['operationKey'],
            step_run_id=bundle['stepRunId'])
        results = deepcopy(bundle['gates'])
        self.repo.record_reasoning_gates(ctx.run_id, checkpoint['checkpointId'], results)
        if bundle['relations']:
            self.repo.upsert_relation_candidates(ctx.run_id, checkpoint['checkpointId'], deepcopy(bundle['relations']))
        failed = [v for v in results if v['status'] == 'failed']
        result['reasoning'] = {k: deepcopy(checkpoint[k]) for k in ('checkpointId', 'stageKey', 'decision', 'nextAction', 'quality')}
        ctx.state['reasoning'] = deepcopy(result['reasoning'])
        for gate in failed:
            result.setdefault('gaps', []).append({'reason': 'quality-gate:' + gate['gateId'], 'stageKey': bundle['stageKey'],
                                                'severity': gate['severity'], 'nextAction': gate['action']})
        result['gaps'] = _unique(result.get('gaps', []))
        if checkpoint['decision'] == 'stop':
            result['status'] = 'blocked'
        if result.get('judgmentReport'):
            all_gates = self.repo.list_reasoning_gates(ctx.run_id)
            policy = ctx.profile.reasoning_policy
            result['judgmentReport']['reasoning'] = {'policyId': policy.get('policyId'), 'policyVersion': policy.get('version'),
                'quality': gate_summary(all_gates), 'checkpointCount': len(self.repo.list_reasoning_checkpoints(ctx.run_id)),
                'reviewIndependence': 'correlated-same-model', 'truthVerified': False}
        return result
