"""Versioned reasoning ownership and immutable material receipts for Actions."""
from copy import deepcopy


def child_reasoning(profile):
    return profile.model_policy.get('config_json', {}).get('actionReasoningVersion') == 'child-v1'


class ActionMaterialReceipt:
    """Copy only referenced evidence artifacts across an actual Action edge.

    Ownership checks remain unchanged. Receipts preserve the original owner and
    hash; neither model-call records nor review artifacts are reattributed.
    """
    TYPES = {'web-source-snapshot', 'future-source-bundle', 'future-claim-candidates'}

    def __init__(self, repo, ctx, source_run_id):
        self.repo, self.ctx, self.source = repo, ctx, source_run_id
        self.cache, self.active = {}, set()
        self.return_receipts = {row['content_json']['receivedArtifactId']: row['content_json']
            for row in repo.list_artifacts(source_run_id)
            if row.get('artifact_type') == 'action-material-receipt'
            and (row.get('content_json') or {}).get('sourceRunId') == ctx.run_id}
        for row in repo.list_artifacts(ctx.run_id):
            if row.get('artifact_type') != 'action-material-receipt':
                continue
            data = row.get('content_json') or {}
            if data.get('sourceRunId') == source_run_id:
                self.cache[data['sourceArtifactId']] = data

    def artifact(self, identity, expected_hash=None):
        if identity in self.cache:
            receipt = self.cache[identity]
            if expected_hash and receipt['sourceArtifactHash'] != expected_hash:
                raise ValueError('Action source artifact hash mismatch')
            return receipt['receivedArtifactId'], receipt['receivedArtifactHash']
        if identity in self.active or len(self.active) > 8:
            raise ValueError('cyclic Action material references')
        row = self.repo.get_artifact(self.source, identity)
        from .evidence_repository import artifact_json_hash
        if not row or row.get('run_id') != self.source or row.get('artifact_type') not in self.TYPES:
            raise ValueError('Action material is not an eligible source artifact')
        if artifact_json_hash(row['content_json']) != row['content_hash'] or (expected_hash and expected_hash != row['content_hash']):
            raise ValueError('Action source artifact hash mismatch')
        returning = self.return_receipts.get(identity)
        if returning:
            original = self.repo.get_artifact(self.ctx.run_id, returning['sourceArtifactId'])
            if (returning['receivedArtifactHash'] != row['content_hash'] or not original or
                    original.get('run_id') != self.ctx.run_id or original['content_hash'] != returning['sourceArtifactHash'] or
                    artifact_json_hash(original['content_json']) != original['content_hash']):
                raise ValueError('Action material return receipt mismatch')
            return original['artifact_id'], original['content_hash']
        self.active.add(identity)
        content = self.project(row['content_json'])
        if row['artifact_type'] == 'future-source-bundle':
            content['sources'] = self.sources(content.get('sources', []))
        self.active.remove(identity)
        received = self.repo.add_artifact(self.ctx.run_id, row['artifact_type'], content, self.ctx.current_step_run_id)
        digest = artifact_json_hash(content)
        receipt = {'sourceRunId': self.source, 'sourceArtifactId': identity, 'sourceArtifactHash': row['content_hash'],
                   'receivedRunId': self.ctx.run_id, 'receivedArtifactId': received, 'receivedArtifactHash': digest}
        self.repo.add_artifact(self.ctx.run_id, 'action-material-receipt', receipt, self.ctx.current_step_run_id)
        self.cache[identity] = receipt
        return received, digest

    def sources(self, rows):
        result = deepcopy(rows)
        for row in result:
            if row.get('artifactId'):
                row['artifactId'], row['artifactHash'] = self.artifact(row['artifactId'], row.get('artifactHash'))
        return result

    def project(self, value):
        if isinstance(value, list):
            return [self.project(item) for item in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        result = {key: self.project(item) for key, item in value.items()}
        if value.get('sourceArtifactId'):
            result['sourceArtifactId'], result['sourceArtifactHash'] = self.artifact(value['sourceArtifactId'], value.get('sourceArtifactHash'))
        if value.get('sourceSnapshotId'):
            result['sourceSnapshotId'], _ = self.artifact(value['sourceSnapshotId'])
        return result


def receive_child_result(repo, ctx, child_run_id, output):
    return ActionMaterialReceipt(repo, ctx, child_run_id).project(output)
