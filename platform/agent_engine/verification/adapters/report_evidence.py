"""Report/graph/source traceability, with no factual or semantic certification."""
from ...evidence_repository import artifact_json_hash, artifact_source
from ...ontology_explorer import evidence_index, source_excerpt, explorer_graph
from ...uav_report import validate_uav_graph
from .run_contract import RunContractAdapter, check_item
from ..contracts import VerificationError


class SnapshotSources:
    def __init__(self, context): self.context = context
    def get_artifact(self, run_id, artifact_id): return self.context.artifact(run_id, artifact_id)
    def get_source_snapshot(self, run_id, source_id):
        row = self.context.query('''SELECT artifact_id,artifact_hash,content_hash,locator_json
            FROM agent_source_snapshot WHERE run_id=%s AND source_id=%s''', (run_id, source_id))
        if not row: raise ValueError('source-unavailable')
        artifact = self.context.artifact(run_id, row['artifact_id'])
        source = artifact_source(artifact, run_id, row['locator_json'].get('evidenceIndex'))
        if (source['artifactHash'] != row['artifact_hash'] or source['contentHash'] != row['content_hash']
                or source['locator'] != row['locator_json']):
            raise ValueError('source-hash-mismatch')
        return {**source, 'sourceId': source_id}


class ReportEvidenceAdapter:
    kind = 'report-evidence'

    def load(self, subject, context):
        run_id, identity = subject['run_id'], subject['artifact_id']
        frozen = RunContractAdapter().load({'run_id': run_id}, context)
        artifact = context.artifact(run_id, identity)
        if not artifact: raise VerificationError('verification-subject-not-found', 404)
        if not (artifact['artifact_type'].endswith('-report') or artifact['artifact_type'] == 'agent-output'):
            raise VerificationError('unsupported-report-artifact', 400)
        checks = []
        def add(code, state, reason, refs=None):
            checks.append(check_item(code, state, reason, identity))
            if refs: checks[-1]['evidenceRefs'] = refs
        body = artifact['content_json']
        if not isinstance(body, dict):
            add('REP-001', 'inconclusive', 'report-unavailable-or-too-large')
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        valid = artifact_json_hash(body) == artifact['content_hash']
        add('REP-001', 'passed' if valid else 'failed', 'report-artifact-hash-match' if valid else 'report-artifact-hash-mismatch')
        if not valid:
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        graph = body.get('ontology') or body.get('ontologyGraph') or {}
        evidence = body.get('evidence') or []
        report = {'runId': run_id, 'ontologyGraph': graph, 'evidence': evidence,
                  'status': frozen['run']['status'], 'agentKey': frozen['run']['agent_key']}
        if not isinstance(graph, dict) or not isinstance(evidence, list):
            add('REP-002', 'failed', 'report-graph-schema-invalid')
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        nodes, edges, assertions = graph.get('nodes', []), graph.get('edges', []), graph.get('assertions', [])
        if any(not isinstance(items, list) or any(not isinstance(x, dict) for x in items)
               for items in (nodes, edges, assertions, evidence)):
            add('REP-002', 'failed', 'report-graph-schema-invalid')
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        if len(nodes) > 2000 or len(edges) > 4000 or len(assertions) > 500 or len(evidence) > 100:
            add('REP-002', 'inconclusive', 'report-verification-coverage-limit')
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        try:
            ids = {n['id'] for n in nodes}
            valid = bool(nodes) and len(ids) == len(nodes) and all(
                e.get('source', e.get('subject')) in ids and e.get('target', e.get('object')) in ids for e in edges)
            valid = valid and all(a['subject'] in ids and a['object'] in ids for a in assertions)
            if str(graph.get('schemaVersion', '')).startswith('uav-'):
                valid = valid and not validate_uav_graph(graph, run_id)
            projection = explorer_graph(report) if valid else None
        except (ValueError, TypeError, KeyError, AttributeError):
            valid, projection = False, None
        add('REP-002', 'passed' if valid else 'failed' if graph else 'inconclusive',
            'graph-references-and-projection-valid' if valid else 'graph-references-or-projection-unverified')
        if not graph:
            # Older agent-output documents predate graph/evidence identities.
            # Missing historical instrumentation is not proof of corruption.
            return {**frozen, 'checks': [*frozen['checks'], *checks], 'limitations': [
                'This historical report has no stored graph schema; graph and evidence coverage cannot be established.']}
        # Duplicate identities must not silently overwrite earlier evidence.
        evidence_ids = [str(e.get('evidenceId') or e.get('candidateId') or e.get('claimId')) for e in evidence]
        if len(set(evidence_ids)) != len(evidence_ids) or 'None' in evidence_ids:
            add('REP-003', 'failed', 'evidence-identity-duplicate-or-missing')
            return {**frozen, 'checks': [*frozen['checks'], *checks]}
        index = evidence_index(report)
        bound = bool(assertions) and all(isinstance(a.get('evidenceIds'), list) and a['evidenceIds']
            and all(str(e) in index for e in a['evidenceIds']) for a in assertions)
        add('REP-003', 'passed' if bound else 'failed' if assertions else 'inconclusive',
            'assertion-evidence-references-match' if bound else 'assertion-evidence-references-unverified')
        if not index:
            add('REP-004', 'inconclusive', 'report-evidence-unavailable')
        repo = SnapshotSources(context)
        for i, (evidence_id, entry) in enumerate(index.items()):
            if i >= 100:
                add('REP-LIMIT', 'inconclusive', 'report-evidence-coverage-limit')
                break
            if not entry.get('sourceArtifactId') or not entry.get('quote') or not entry.get('contentHash'):
                add(f'REP-E{i:03}', 'inconclusive', 'evidence-reference-incomplete')
                continue
            try:
                source_excerpt(report, evidence_id, repo)
                add(f'REP-E{i:03}', 'passed', 'evidence-source-hash-and-quote-match', [identity, str(evidence_id)])
            except (ValueError, KeyError, TypeError, AttributeError):
                add(f'REP-E{i:03}', 'failed', 'evidence-source-or-quote-mismatch', [identity, str(evidence_id)])
        return {**frozen, 'checks': [*frozen['checks'], *checks],
            'authority': {**frozen['authority'], 'reportArtifactId': identity, 'reportHash': artifact['content_hash'],
                          'graphProjectionHash': projection.get('contentHash') if projection else None},
            'limitations': ['Source location and graph traceability do not establish semantic support or factual correctness.']}
