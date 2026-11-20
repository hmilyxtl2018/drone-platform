"""Persistent entity audit lifecycle around the ontology enrichment workflow.

The capability workflow performs Firecrawl/DeepSeek work.  This module gives
that work a durable user-facing task boundary with explicit trigger, review,
publication and retry semantics.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from capability_workflow import CapabilityWorkflow
    from capability_profile import CapabilityProfiles
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from signal_contracts import SignalContractError, with_content_hash, validate_record, db_json
except ModuleNotFoundError:  # pragma: no cover
    from .capability_workflow import CapabilityWorkflow
    from .capability_profile import CapabilityProfiles
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from .signal_contracts import SignalContractError, with_content_hash, validate_record, db_json


STATUSES = {'queued','planning','collecting','extracting','reconciling','awaiting-review','completed','completed-with-gaps','failed','cancelled'}
TRIGGERS = {'manual','batch','periodic','conversation'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def decode(value: Any, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class EntityAuditRuns:
    def __init__(self, agent):
        self.agent = agent
        self.store = agent.store

    def recover(self) -> int:
        """Leave interrupted work visible and retryable after a restart."""
        changed = 0
        with self.store._lock, self.store._connect() as con:
            rows = con.execute("SELECT audit_run_id FROM ontology_audit_run WHERE status IN ('queued','planning','collecting','extracting','reconciling')").fetchall()
            for row in rows:
                aid = row[0]
                stamp = now()
                con.execute("UPDATE ontology_audit_run SET status='failed',error_message=?,stop_reason='service-restarted',updated_at=?,completed_at=? WHERE audit_run_id=?", ('服务重启中断；可使用重试继续执行', stamp, stamp, aid))
                self._event_tx(con, aid, 'recovery', 'failed', {'reason': 'service-restarted', 'retryable': True})
                changed += 1
        return changed

    @staticmethod
    def _event_tx(con, audit_id: str, stage: str, status: str, detail: dict[str, Any]):
        con.execute('INSERT INTO ontology_audit_event(audit_run_id,stage,status,detail_json,created_at) VALUES(?,?,?,?,?)', (audit_id, stage, status, dump(detail), now()))

    def _event(self, audit_id, stage, status, detail):
        with self.store._lock, self.store._connect() as con:
            self._event_tx(con, audit_id, stage, status, detail)

    @staticmethod
    def _request(payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload or {}
        trigger = str(payload.get('trigger') or 'manual').strip()
        if trigger not in TRIGGERS:
            raise IntelligenceValidationError('trigger must be manual, batch, periodic or conversation')
        # CapabilityWorkflow remains the single source of budget validation.
        request = dict(payload)
        request['trigger'] = trigger
        request.setdefault('capabilityCodes', ['platform'])
        request.setdefault('maxJobs', 2)
        request.setdefault('maxModelCalls', 2)
        request.setdefault('maxElapsedSeconds', 600)
        request.setdefault('maxFollowupRounds', 1)
        request.setdefault('maxResponseBytes', 4000000)
        request.setdefault('force', False)
        return request

    def _row(self, row, *, include_events=True):
        if row is None:
            raise IntelligenceNotFound('audit run not found')
        item = dict(row)
        out = {
            'auditRunId': item['audit_run_id'], 'entityId': item['entity_id'],
            'objectRevision': item['object_revision'], 'trigger': item['trigger_type'],
            'status': item['status'], 'request': decode(item['request_json'], {}),
            'result': decode(item['result_json'], {}) if item.get('result_json') else {},
            'capabilityRunId': item['capability_run_id'], 'retryOf': item['retry_of'],
            'stopReason': item['stop_reason'], 'error': item['error_message'],
            'createdAt': item['created_at'], 'startedAt': item['started_at'],
            'updatedAt': item['updated_at'], 'completedAt': item['completed_at'],
        }
        if include_events:
            with self.store._connect() as con:
                out['events'] = [{'eventId': r['event_id'], 'stage': r['stage'], 'status': r['status'], 'detail': decode(r['detail_json'], {}), 'createdAt': r['created_at']} for r in con.execute('SELECT * FROM ontology_audit_event WHERE audit_run_id=? ORDER BY event_id', (item['audit_run_id'],))]
                out['candidates'] = []
                for r in con.execute('SELECT candidate_type,candidate_id,created_at FROM ontology_audit_candidate WHERE audit_run_id=? ORDER BY created_at', (item['audit_run_id'],)):
                    if r['candidate_type'] == 'attribute':
                        candidate = self.agent.get_attribute_candidate(r['candidate_id'])
                    else:
                        candidate = self.agent.get_relation_candidate(r['candidate_id'])
                    # Keep deterministic evidence integrity beside every
                    # candidate.  This is deliberately an eligibility check,
                    # never an automatic acceptance or publication decision.
                    verifier = getattr(self.agent, 'verify_candidate_evidence', None)
                    if callable(verifier):
                        try:
                            candidate['evidenceVerification'] = verifier(candidate)
                        except Exception as exc:  # a malformed candidate must remain visible
                            candidate['evidenceVerification'] = {
                                'status': 'review-required',
                                'autoAcceptEligible': False,
                                'reasons': [f'evidence verification failed: {str(exc)[:200]}'],
                            }
                    out['candidates'].append({'candidateType': r['candidate_type'], 'candidateId': r['candidate_id'], 'createdAt': r['created_at'], 'candidate': candidate})
                out['reviews'] = [{'auditReviewId': r['audit_review_id'], 'candidateType': r['candidate_type'], 'candidateId': r['candidate_id'], 'action': r['action'], 'reviewer': r['reviewer'], 'reason': r['reason'], 'decision': decode(r['decision_json'], {}), 'createdAt': r['created_at']} for r in con.execute('SELECT * FROM ontology_audit_review WHERE audit_run_id=? ORDER BY created_at', (item['audit_run_id'],))]
            # Typed signal/evidence records are first-class audit outputs.  A
            # missing table on a pre-018 database is tolerated so historical
            # audit runs remain readable while the migration is applied.
            try:
                out['signal'] = self.list_signal_records(audit_id=item['audit_run_id'], limit=500)
            except Exception:
                out['signal'] = {'observations': [], 'claims': [], 'evidenceBundles': [], 'verificationRuns': [], 'emitterSignatures': []}
        return out

    def _is_cancelled(self, audit_id: str) -> bool:
        """Return whether a run was cancelled while its worker was executing.

        CapabilityWorkflow runs in a background thread.  Without a cooperative
        check it could finish after an operator cancellation and overwrite the
        terminal state.  This helper is intentionally read-only and is used at
        each stage boundary.
        """
        with self.store._connect() as con:
            row = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
        return bool(row and row[0] == 'cancelled')

    def _mark_stage(self, audit_id: str, status: str, stage: str, *, detail=None):
        """Atomically move the user-visible audit stage and append its event."""
        if status not in STATUSES:
            raise IntelligenceValidationError(f'unsupported audit status: {status}')
        stamp = now()
        with self.store._lock, self.store._connect() as con:
            row = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
            if row is None:
                raise IntelligenceNotFound('audit run not found')
            if row[0] == 'cancelled':
                return False
            con.execute('UPDATE ontology_audit_run SET status=?,updated_at=? WHERE audit_run_id=?', (status, stamp, audit_id))
            self._event_tx(con, audit_id, stage, 'running', detail or {})
        return True

    # ------------------------------------------------------------------
    # Signal and capability evidence chain
    # ------------------------------------------------------------------
    # These records are deliberately stored separately from the legacy
    # attribute/relation candidate tables.  They are immutable candidate
    # records and therefore cannot be mistaken for a published ontology fact.

    def _entity_id(self, value: str) -> str:
        entity, _ = self.agent._entity_document(value)
        return entity['entityId']

    def _check_audit_entity(self, audit_id: str | None, entity_id: str) -> None:
        if not audit_id:
            return
        with self.store._connect() as con:
            row = con.execute('SELECT entity_id FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
        if row is None:
            raise IntelligenceNotFound('audit run not found')
        if row[0] != entity_id:
            raise IntelligenceConflict('audit run does not belong to the entity')

    @staticmethod
    def _record_error(exc: Exception) -> Exception:
        if isinstance(exc, SignalContractError):
            return IntelligenceValidationError(str(exc))
        return exc

    def _normal_signal_record(self, entity_id: str, payload: dict[str, Any], record_type: str, *, audit_id=None) -> dict[str, Any]:
        value = dict(payload or {})
        value['recordType'] = record_type
        value['entityId'] = entity_id
        if audit_id:
            value['auditRunId'] = audit_id
        value.setdefault('createdAt', now())
        # Inbound records are model/provider candidates.  A caller cannot use
        # this ingestion endpoint to smuggle a verified or published claim
        # past the audit review and publication gates.
        if record_type == 'CapabilityClaim':
            if value.get('reviewStatus') not in (None, 'pending', 'under-review') or value.get('truthVerified') is True or value.get('verificationStatus') == 'verified':
                raise IntelligenceValidationError('capability claims enter as under-review candidates; use audit review and publication to verify them')
            value['reviewStatus'] = 'under-review'
            value['truthVerified'] = False
            value['verificationStatus'] = 'under-review'
        elif record_type in {'SignalObservation', 'EmitterSignature', 'EvidenceBundle'} and value.get('verificationStatus') == 'verified':
            raise IntelligenceValidationError(f'{record_type} cannot be ingested as verified; evidence review is required')
        id_field = {
            'SignalObservation': 'observationId',
            'EmitterSignature': 'signatureId',
            'CapabilityClaim': 'claimId',
            'EvidenceBundle': 'evidenceBundleId',
            'VerificationRun': 'verificationRunId',
        }[record_type]
        value.setdefault(id_field, f"{record_type[0].lower()}-{secrets.token_hex(10)}")
        try:
            return with_content_hash(value)
        except SignalContractError as exc:
            raise IntelligenceValidationError(str(exc)) from exc

    @staticmethod
    def _signal_row_json(row, fields: dict[str, str], json_fields: set[str]) -> dict[str, Any]:
        item = dict(row)
        result = {}
        for db_name, api_name in fields.items():
            value = item.get(db_name)
            if db_name in json_fields:
                if db_name == 'normalized_value':
                    value = decode(value, None)
                else:
                    value = decode(value, {} if db_name.endswith('_json') and db_name not in {'source_refs', 'claim_ids', 'observation_ids', 'evidence_ids', 'counter_evidence_ids'} else [])
            result[api_name] = value
        return result

    def _load_signal_records(self, *, entity_id=None, audit_id=None, kind=None, limit=100):
        """Read signal-chain rows in the public JSON contract shape."""
        limit = max(1, min(int(limit), 500))
        clauses, values = [], []
        if entity_id:
            clauses.append('entity_id=?'); values.append(entity_id)
        if audit_id:
            clauses.append('audit_run_id=?'); values.append(audit_id)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.store._connect() as con:
            result = {}
            if kind in (None, 'emitter-signatures'):
                rows = con.execute('SELECT * FROM ontology_emitter_signature' + where.replace('audit_run_id', 'entity_id') + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall() if not audit_id else []
                result['emitterSignatures'] = []
                for row in rows:
                    result['emitterSignatures'].append(self._signal_row_json(row, {'signature_id':'signatureId','entity_id':'entityId','signature_type':'signatureType','features_json':'features','attribution_json':'attribution','source_refs':'sourceRefs','confidence':'confidence','verification_status':'verificationStatus','content_hash':'contentHash','created_at':'createdAt'}, {'features_json','attribution_json','source_refs'}))
            if kind in (None, 'observations', 'signals'):
                obs_where = where
                rows = con.execute('SELECT * FROM ontology_signal_observation' + obs_where + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall()
                result['observations'] = []
                for row in rows:
                    result['observations'].append(self._signal_row_json(row, {'observation_id':'observationId','entity_id':'entityId','audit_run_id':'auditRunId','observation_type':'observationType','collected_at':'collectedAt','collection_location':'collectionLocation','conditions_json':'conditions','features_json':'features','emitter_signature_id':'emitterSignatureId','source_refs':'sourceRefs','claim_state':'claimState','confidence':'confidence','verification_status':'verificationStatus','evidence_level':'evidenceLevel','content_hash':'contentHash','created_at':'createdAt'}, {'collection_location','conditions_json','features_json','source_refs'}))
            if kind in (None, 'claims', 'capability-claims'):
                rows = con.execute('SELECT * FROM ontology_capability_claim' + where + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall()
                result['claims'] = []
                for row in rows:
                    item = self._signal_row_json(row, {'claim_id':'claimId','entity_id':'entityId','entity_revision_id':'entityRevisionId','audit_run_id':'auditRunId','capability_domain':'capabilityDomain','metric':'metric','raw_value':'rawValue','value_type':'valueType','normalized_value':'normalizedValue','unit':'unit','qualifier':'qualifier','conditions_json':'conditions','variant':'variant','configuration':'configuration','operational_effect':'operationalEffect','source_observation_ids':'sourceObservationIds','evidence_bundle_ids':'evidenceBundleIds','claim_state':'claimState','verification_status':'verificationStatus','evidence_level':'evidenceLevel','confidence':'confidence','review_status':'reviewStatus','truth_verified':'truthVerified','content_hash':'contentHash','created_at':'createdAt'}, {'conditions_json','source_observation_ids','evidence_bundle_ids','normalized_value'})
                    item['truthVerified'] = bool(item.get('truthVerified'))
                    result['claims'].append(item)
            if kind in (None, 'evidence-bundles'):
                rows = con.execute('SELECT * FROM ontology_evidence_bundle' + where + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall()
                result['evidenceBundles'] = []
                for row in rows:
                    result['evidenceBundles'].append(self._signal_row_json(row, {'evidence_bundle_id':'evidenceBundleId','entity_id':'entityId','audit_run_id':'auditRunId','claim_ids':'claimIds','observation_ids':'observationIds','evidence_ids':'evidenceIds','source_independence_json':'sourceIndependence','validity_json':'validity','counter_evidence_ids':'counterEvidenceIds','evidence_level':'evidenceLevel','verification_status':'verificationStatus','content_hash':'contentHash','created_at':'createdAt'}, {'claim_ids','observation_ids','evidence_ids','source_independence_json','validity_json','counter_evidence_ids'}))
            if kind in (None, 'verification-runs'):
                rows = con.execute('SELECT * FROM ontology_verification_run' + where + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall()
                result['verificationRuns'] = []
                for row in rows:
                    result['verificationRuns'].append(self._signal_row_json(row, {'verification_run_id':'verificationRunId','entity_id':'entityId','audit_run_id':'auditRunId','run_type':'runType','status':'status','method_version':'methodVersion','input_refs':'inputRefs','result_json':'result','checks_json':'checks','evidence_level':'evidenceLevel','confidence':'confidence','started_at':'startedAt','completed_at':'completedAt','created_at':'createdAt'}, {'input_refs','result_json','checks_json'}))
            return result

    def list_signal_records(self, *, entity_id=None, audit_id=None, kind=None, limit=100):
        if entity_id:
            entity_id = self._entity_id(entity_id)
        if audit_id:
            self._check_audit_entity(audit_id, entity_id) if entity_id else None
        return self._load_signal_records(entity_id=entity_id, audit_id=audit_id, kind=kind, limit=limit)

    def create_signal_record(self, entity_id: str, record_type: str, payload: dict[str, Any], *, audit_id=None):
        entity_id = self._entity_id(entity_id)
        self._check_audit_entity(audit_id, entity_id)
        record = self._normal_signal_record(entity_id, payload, record_type, audit_id=audit_id)
        with self.store._lock, self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            duplicate = False
            if record_type == 'SignalObservation':
                sql = ('INSERT OR IGNORE INTO ontology_signal_observation(observation_id,entity_id,audit_run_id,observation_type, collected_at,collection_location,conditions_json,features_json,emitter_signature_id,source_refs,claim_state,confidence,verification_status,evidence_level,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
                params = (record['observationId'],entity_id,record.get('auditRunId'),record['observationType'],record['collectedAt'],db_json(record.get('collectionLocation',{})),db_json(record.get('conditions',{})),db_json(record.get('features',{})),record.get('emitterSignatureId'),db_json(record.get('sourceRefs',[])),record['claimState'],record.get('confidence'),record['verificationStatus'],record['evidenceLevel'],record['contentHash'],record['createdAt'])
            elif record_type == 'EmitterSignature':
                sql = ('INSERT OR IGNORE INTO ontology_emitter_signature(signature_id,entity_id,signature_type,features_json,attribution_json,source_refs,confidence,verification_status,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)')
                params = (record['signatureId'],entity_id,record['signatureType'],db_json(record.get('features',{})),db_json(record.get('attribution',{})),db_json(record.get('sourceRefs',[])),record.get('confidence'),record['verificationStatus'],record['contentHash'],record['createdAt'])
            elif record_type == 'CapabilityClaim':
                sql = ('INSERT OR IGNORE INTO ontology_capability_claim(claim_id,entity_id,entity_revision_id,audit_run_id,capability_domain,metric,raw_value,value_type,normalized_value,unit,qualifier,conditions_json,variant,configuration,operational_effect,source_observation_ids,evidence_bundle_ids,claim_state,verification_status,evidence_level,confidence,review_status,truth_verified,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
                params = (record['claimId'],entity_id,record.get('entityRevisionId'),record.get('auditRunId'),record['capabilityDomain'],record['metric'],record['rawValue'],record['valueType'],None if record.get('normalizedValue') is None else db_json(record.get('normalizedValue')),record.get('unit'),record.get('qualifier','unknown'),db_json(record.get('conditions',{})),record.get('variant'),record.get('configuration'),record.get('operationalEffect'),db_json(record.get('sourceObservationIds',[])),db_json(record.get('evidenceBundleIds',[])),record['claimState'],record['verificationStatus'],record['evidenceLevel'],record.get('confidence'),record.get('reviewStatus','under-review'),1 if record.get('truthVerified') else 0,record['contentHash'],record['createdAt'])
            elif record_type == 'EvidenceBundle':
                sql = ('INSERT OR IGNORE INTO ontology_evidence_bundle(evidence_bundle_id,entity_id,audit_run_id,claim_ids,observation_ids,evidence_ids,source_independence_json,validity_json,counter_evidence_ids,evidence_level,verification_status,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)')
                params = (record['evidenceBundleId'],entity_id,record.get('auditRunId'),db_json(record.get('claimIds',[])),db_json(record.get('observationIds',[])),db_json(record.get('evidenceIds',[])),db_json(record.get('sourceIndependence',{})),db_json(record.get('validity',{})),db_json(record.get('counterEvidenceIds',[])),record['evidenceLevel'],record['verificationStatus'],record['contentHash'],record['createdAt'])
            elif record_type == 'VerificationRun':
                sql = ('INSERT OR IGNORE INTO ontology_verification_run(verification_run_id,entity_id,audit_run_id,run_type,status,method_version,input_refs,result_json,checks_json,evidence_level,confidence,started_at,completed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
                params = (record['verificationRunId'],entity_id,record.get('auditRunId'),record['runType'],record['status'],record['methodVersion'],db_json(record.get('inputRefs',{})),db_json(record.get('result',{})),db_json(record.get('checks',[])),record['evidenceLevel'],record.get('confidence'),record.get('startedAt'),record.get('completedAt'),record['createdAt'])
            else:
                raise IntelligenceValidationError('unsupported signal recordType')
            cursor = con.execute(sql, params)
            duplicate = cursor.rowcount == 0
        records = self.list_signal_records(entity_id=entity_id, kind={
            'SignalObservation':'observations','EmitterSignature':'emitter-signatures','CapabilityClaim':'claims','EvidenceBundle':'evidence-bundles','VerificationRun':'verification-runs'}[record_type], limit=500)
        key = {'SignalObservation':'observations','EmitterSignature':'emitterSignatures','CapabilityClaim':'claims','EvidenceBundle':'evidenceBundles','VerificationRun':'verificationRuns'}[record_type]
        id_key = {'SignalObservation':'observationId','EmitterSignature':'signatureId','CapabilityClaim':'claimId','EvidenceBundle':'evidenceBundleId','VerificationRun':'verificationRunId'}[record_type]
        stored = next((item for item in records.get(key, []) if item.get(id_key) == record.get(id_key)), record)
        return {'ok': True, 'recordType': record_type, 'record': stored, 'duplicate': duplicate}

    def get(self, audit_id: str):
        with self.store._connect() as con:
            row = con.execute('SELECT * FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
        return self._row(row)

    def list(self, *, entity_id=None, status=None, limit=50):
        limit = max(1, min(int(limit), 200))
        clauses, values = [], []
        if entity_id:
            entity, _ = self.agent._entity_document(entity_id)
            clauses.append('entity_id=?'); values.append(entity['entityId'])
        if status:
            clauses.append('status=?'); values.append(status)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.store._connect() as con:
            rows = con.execute('SELECT * FROM ontology_audit_run' + where + ' ORDER BY created_at DESC LIMIT ?', (*values, limit)).fetchall()
        return [self._row(row) for row in rows]

    def _ensure_identity_archive(self, entity_id: str) -> dict[str, Any] | None:
        """Create the first immutable attributes snapshot when an imported entity
        has identity data but no archive revision yet.

        Some service-history imports intentionally create an entity before the
        workbook attribute projection is available.  An audit still needs a
        revision boundary so every candidate can be tied to a stable object
        revision.  This bootstrap contains only explicit ``unknown`` buckets;
        it never invents an equipment fact and remains ``pending`` until the
        normal evidence review/publication flow runs.
        """
        try:
            from attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
            from capability_profile import REGISTRY
        except ModuleNotFoundError:  # pragma: no cover
            from .attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
            from .capability_profile import REGISTRY

        with self.store._lock, self.store._connect() as con:
            row = con.execute(
                "SELECT entity_id,entity_type,current_revision,source_snapshot_ids "
                "FROM ontology_entity WHERE entity_id=? OR canonical_key=?",
                (entity_id, entity_id),
            ).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
            if int(row["current_revision"] or 0) >= 1:
                return None

            snapshot_ids = decode(row["source_snapshot_ids"], []) or []
            if not isinstance(snapshot_ids, list):
                snapshot_ids = []
            snapshot_id = None
            for candidate in snapshot_ids:
                exists = con.execute(
                    "SELECT snapshot_id FROM ontology_import_snapshot WHERE snapshot_id=?",
                    (str(candidate),),
                ).fetchone()
                if exists:
                    snapshot_id = exists[0]
                    break
            if snapshot_id is None:
                latest = con.execute(
                    "SELECT snapshot_id FROM ontology_import_snapshot "
                    "WHERE status='completed' ORDER BY imported_at DESC LIMIT 1"
                ).fetchone()
                snapshot_id = latest[0] if latest else None
            if not snapshot_id:
                raise IntelligenceConflict(
                    "entity has no import snapshot; complete identity ingestion before auditing"
                )

            raw = con.execute(
                "SELECT raw_record_id FROM ontology_raw_record "
                "WHERE canonical_model_id=? ORDER BY created_at LIMIT 1",
                (row["entity_id"],),
            ).fetchone()
            raw_record_id = raw[0] if raw else None
            keys = sorted({key for group in REGISTRY.get("groups", []) for key in group.get("keys", [])})
            stamp = now()
            document: dict[str, Any] = {
                "schemaVersion": "equipment-attributes.v1",
                "objectId": row["entity_id"],
                "objectType": row["entity_type"],
                "objectRevision": 1,
                "attributes": {key: {"state": "unknown", "values": []} for key in keys},
                "completeness": {
                    "mode": "template",
                    "templateId": "equipment-capability-registry.v1",
                    "expectedKeys": keys,
                    "presentKeys": [],
                    "unknownKeys": keys,
                    "notApplicableKeys": [],
                    "conflictKeys": [],
                    "missingRequiredKeys": [],
                    "coverageRatio": 0,
                    "status": "incomplete",
                    "checkedAt": stamp,
                    "validatorVersion": "attributes-jsonb.v1",
                },
                "provenance": {
                    "sourceSnapshotIds": [str(snapshot_id)],
                    "sourceRecordIds": [raw_record_id] if raw_record_id else [],
                    "generatedBy": "identity-archive-bootstrap",
                    "createdAt": stamp,
                    "updatedAt": stamp,
                    "schemaVersion": "equipment-attributes.v1",
                },
            }
            document["provenance"]["contentHash"] = canonical_attributes_hash(document)
            errors = validate_attributes_jsonb(document)
            if errors:
                raise IntelligenceValidationError(
                    "identity archive JSONB invalid: " + "; ".join(errors)
                )
            revision_id = f"entity-revision-{row['entity_id']}-{document['provenance']['contentHash'][:24]}"
            con.execute(
                "INSERT OR IGNORE INTO ontology_entity_revision "
                "(entity_revision_id,entity_id,revision,attributes_json,attributes_schema_version,"
                "attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    row["entity_id"],
                    1,
                    dump(document),
                    "equipment-attributes.v1",
                    document["provenance"]["contentHash"],
                    snapshot_id,
                    raw_record_id,
                    "pending",
                    stamp,
                ),
            )
            con.execute(
                "UPDATE ontology_entity SET current_revision=1,updated_at=? "
                "WHERE entity_id=? AND current_revision=0",
                (stamp, row["entity_id"]),
            )
            return {
                "entityId": row["entity_id"],
                "revision": 1,
                "entityRevisionId": revision_id,
                "sourceSnapshotId": snapshot_id,
                "sourceRecordId": raw_record_id,
                "status": "pending",
            }

    def submit(self, payload, entity_id, *, background=True, retry_of=None):
        identity_archive = self._ensure_identity_archive(entity_id)
        entity, _ = self.agent._entity_document(entity_id)
        if entity['currentRevision'] < 1:
            raise IntelligenceConflict('entity has no archive revision; complete its identity archive first')
        request = self._request(payload)
        audit_id = 'audit-' + secrets.token_hex(12)
        created = now()
        with self.store._lock, self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            active = con.execute("SELECT audit_run_id FROM ontology_audit_run WHERE entity_id=? AND status IN ('queued','planning','collecting','extracting','reconciling','awaiting-review')", (entity['entityId'],)).fetchone()
            if active:
                raise IntelligenceConflict('an entity audit is already active: ' + active[0])
            con.execute('INSERT INTO ontology_audit_run(audit_run_id,entity_id,object_revision,trigger_type,status,request_json,retry_of,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (audit_id, entity['entityId'], entity['currentRevision'], request['trigger'], 'queued', dump(request), retry_of, created, created))
            detail = {'trigger': request['trigger'], 'entityId': entity['entityId'], 'objectRevision': entity['currentRevision'], 'budgets': {key: request.get(key) for key in ('maxJobs','maxModelCalls','maxElapsedSeconds','maxFollowupRounds','maxResponseBytes')}}
            if identity_archive:
                detail['identityArchive'] = identity_archive
            self._event_tx(con, audit_id, 'trigger', 'queued', detail)
        if background:
            threading.Thread(target=self.execute, args=(audit_id,), daemon=True, name=audit_id).start()
        else:
            self.execute(audit_id)
        return self.get(audit_id)

    def submit_batch(self, payload, entity_ids):
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if not isinstance(entity_ids, list) or not entity_ids or len(entity_ids) > 200:
            raise IntelligenceValidationError('entityIds must contain 1 to 200 entities')
        request = {**(payload or {}), 'trigger': 'batch'}
        runs = []
        for entity_id in dict.fromkeys(entity_ids):
            runs.append(self.submit(request, entity_id, background=True))
        return {'trigger': 'batch', 'count': len(runs), 'auditRuns': runs}

    def execute(self, audit_id):
        audit = self.get(audit_id)
        started = now()
        request = dict(audit['request'])
        try:
            with self.store._lock, self.store._connect() as con:
                # A queued run may be cancelled before its worker starts.
                row = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
                if not row:
                    raise IntelligenceNotFound('audit run not found')
                if row[0] == 'cancelled':
                    return self.get(audit_id)
                con.execute("UPDATE ontology_audit_run SET status='planning',started_at=?,updated_at=? WHERE audit_run_id=?", (started, started, audit_id))
                self._event_tx(con, audit_id, 'planning', 'completed', {'entityId': audit['entityId'], 'objectRevision': audit['objectRevision']})
            if self._is_cancelled(audit_id):
                return self.get(audit_id)
            if not self._mark_stage(audit_id, 'collecting', 'collecting', detail={'provider': 'Firecrawl', 'budget': request.get('maxJobs')}):
                return self.get(audit_id)
            cap = CapabilityWorkflow(self.agent).submit(request, audit['entityId'], background=False)
            cap_id = cap['runId']
            with self.store._lock, self.store._connect() as con:
                exists = con.execute('SELECT 1 FROM ontology_capability_run WHERE run_id=?', (cap_id,)).fetchone()
                con.execute("UPDATE ontology_audit_run SET capability_run_id=?,status='extracting',updated_at=? WHERE audit_run_id=?", (cap_id if exists else None, now(), audit_id))
            self._event(audit_id, 'collecting', 'completed', {'capabilityRunId': cap_id, 'sourceRun': cap.get('result', {})})
            self._event(audit_id, 'extracting', 'completed', {'capabilityRunId': cap_id, 'modelCalls': (cap.get('result') or {}).get('modelCalls', 0), 'candidateCount': (cap.get('result') or {}).get('quality', {}).get('presentCount', 0)})
            if self._is_cancelled(audit_id):
                return self.get(audit_id)
            if not self._mark_stage(audit_id, 'reconciling', 'reconciling', detail={'capabilityRunId': cap_id}):
                return self.get(audit_id)
            cap_result = cap.get('result') or {}
            profile_id = cap_result.get('profileId')
            projection = self._attach_candidates(audit_id, audit['entityId'], audit['createdAt'])
            with self.store._lock, self.store._connect() as con:
                counts = {
                    'attributeCandidates': con.execute("SELECT COUNT(*) FROM ontology_audit_candidate WHERE audit_run_id=? AND candidate_type='attribute'", (audit_id,)).fetchone()[0],
                    'relationCandidates': con.execute("SELECT COUNT(*) FROM ontology_audit_candidate WHERE audit_run_id=? AND candidate_type='relation'", (audit_id,)).fetchone()[0],
                    'capabilityClaims': con.execute("SELECT COUNT(*) FROM ontology_capability_claim WHERE audit_run_id=?", (audit_id,)).fetchone()[0],
                    'evidenceBundles': con.execute("SELECT COUNT(*) FROM ontology_evidence_bundle WHERE audit_run_id=?", (audit_id,)).fetchone()[0],
                    'conflicts': int((cap_result.get('quality') or {}).get('conflictCount', 0)),
                }
            self._event(audit_id, 'reconciling', 'completed', counts)
            mapped = {'failed': 'failed', 'awaiting-human-review': 'awaiting-review', 'completed-with-gaps': 'completed-with-gaps', 'completed': 'completed'}.get(cap.get('status'), 'failed')
            # A run that produced candidate facts or conflicts is reviewable,
            # even when the provider itself reported completed.  The provider's
            # status reflects execution; this boundary reflects governance.
            if mapped == 'completed' and (counts['attributeCandidates'] or counts['relationCandidates'] or counts['conflicts']):
                mapped = 'awaiting-review'
            result = {**cap_result, **counts, 'capabilityRunId': cap_id, 'profileId': profile_id, 'capabilityClaimIds': projection.get('capabilityClaimIds', []), 'review': 'required' if mapped == 'awaiting-review' else 'none', 'publication': 'requires-explicit-human-review'}
            self._event(audit_id, 'audit', mapped, result)
            finished = now()
            with self.store._lock, self.store._connect() as con:
                # Never let a worker overwrite an operator cancellation.
                current = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
                if current and current[0] != 'cancelled':
                    con.execute('UPDATE ontology_audit_run SET status=?,result_json=?,stop_reason=?,updated_at=?,completed_at=?,error_message=NULL WHERE audit_run_id=?', (mapped, dump(result), result.get('stopReason'), finished, finished, audit_id))
        except Exception as exc:
            error = str(exc)[:500]
            finished = now()
            self._event(audit_id, 'audit', 'failed', {'error': error, 'retryable': True})
            with self.store._lock, self.store._connect() as con:
                current = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
                if current and current[0] != 'cancelled':
                    con.execute("UPDATE ontology_audit_run SET status='failed',error_message=?,stop_reason='execution-failed',updated_at=?,completed_at=? WHERE audit_run_id=?", (error, finished, finished, audit_id))
        return self.get(audit_id)

    def _attach_candidates(self, audit_id, entity_id, created_at):
        attr_ids = []
        relation_ids = []
        with self.store._lock, self.store._connect() as con:
            rows = con.execute("SELECT candidate_id FROM ontology_attribute_candidate WHERE entity_id=? AND created_at>=?", (entity_id, created_at)).fetchall()
            relation_rows = con.execute("SELECT candidate_id FROM ontology_relation_candidate WHERE entity_id=? AND created_at>=?", (entity_id, created_at)).fetchall()
            for row in rows:
                con.execute('INSERT OR IGNORE INTO ontology_audit_candidate(audit_run_id,candidate_type,candidate_id,created_at) VALUES(?,?,?,?)', (audit_id, 'attribute', row[0], now()))
                attr_ids.append(row[0])
            for row in relation_rows:
                con.execute('INSERT OR IGNORE INTO ontology_audit_candidate(audit_run_id,candidate_type,candidate_id,created_at) VALUES(?,?,?,?)', (audit_id, 'relation', row[0], now()))
                relation_ids.append(row[0])

        # Materialize an immutable CapabilityClaim candidate for each model
        # attribute.  The legacy candidate remains the review control record;
        # this typed claim gives the graph and signal/evidence views a stable
        # link without promoting anything to the trusted ontology.
        claim_ids = []
        for candidate_id in attr_ids:
            try:
                candidate = self.agent.get_attribute_candidate(candidate_id)
                raw = str(candidate.get('proposedValue') or '').strip()
                if not raw:
                    continue
                metadata = candidate.get('metadata') if isinstance(candidate.get('metadata'), dict) else {}
                conditions = candidate.get('conditions')
                if isinstance(conditions, dict):
                    condition_obj = conditions
                elif conditions:
                    condition_obj = {'text': str(conditions)}
                else:
                    condition_obj = {}
                value_type = 'number' if isinstance(metadata.get('normalizedValue'), (int, float)) and not isinstance(metadata.get('normalizedValue'), bool) else 'text'
                claim = {
                    'claimId': f"claim-{candidate_id}",
                    'entityId': entity_id,
                    'auditRunId': audit_id,
                    'capabilityDomain': str(metadata.get('capabilityDomain') or metadata.get('domain') or candidate.get('attributeKey') or 'equipment-capability'),
                    'metric': str(candidate.get('attributeKey') or 'unknown'),
                    'rawValue': raw,
                    'valueType': value_type,
                    'normalizedValue': metadata.get('normalizedValue') if value_type == 'number' else None,
                    'unit': candidate.get('unit'),
                    'qualifier': str(metadata.get('qualifier') or 'unknown'),
                    'conditions': condition_obj,
                    'variant': metadata.get('variant'),
                    'configuration': metadata.get('configuration'),
                    'operationalEffect': metadata.get('operationalEffect'),
                    'sourceObservationIds': list(metadata.get('sourceObservationIds') or []),
                    'evidenceBundleIds': [],
                    'claimState': 'reported',
                    'verificationStatus': 'under-review',
                    'evidenceLevel': 'E1' if candidate.get('evidence') else 'E0',
                    'confidence': candidate.get('confidence'),
                    'reviewStatus': 'under-review',
                    'truthVerified': False,
                }
                evidence = candidate.get('evidence') if isinstance(candidate.get('evidence'), list) else []
                if evidence:
                    bundle = {
                        'evidenceBundleId': f"bundle-{candidate_id}",
                        'auditRunId': audit_id,
                        'claimIds': [claim['claimId']],
                        'observationIds': claim['sourceObservationIds'],
                        'evidenceIds': [str(ref.get('evidenceId') or ref.get('snapshotId')) for ref in evidence if isinstance(ref, dict) and (ref.get('evidenceId') or ref.get('snapshotId'))],
                        'sourceIndependence': {},
                        'validity': {'candidateStatus': candidate.get('status', 'under-review')},
                        'counterEvidenceIds': [],
                        'evidenceLevel': claim['evidenceLevel'],
                        'verificationStatus': 'under-review',
                    }
                    if bundle['evidenceIds']:
                        bundle_result = self.create_signal_record(entity_id, 'EvidenceBundle', bundle, audit_id=audit_id)
                        claim['evidenceBundleIds'] = [bundle_result['record']['evidenceBundleId']]
                claim_result = self.create_signal_record(entity_id, 'CapabilityClaim', claim, audit_id=audit_id)
                claim_ids.append(claim_result['record']['claimId'])
            except (IntelligenceValidationError, IntelligenceNotFound, KeyError, TypeError, ValueError):
                # A malformed provider candidate remains in its legacy review
                # queue; failure to project it must not make the audit worker
                # fail or cause an implicit acceptance.
                continue
        return {'attributeCandidateIds': attr_ids, 'relationCandidateIds': relation_ids, 'capabilityClaimIds': claim_ids}

    def cancel(self, audit_id, reason='operator cancelled'):
        with self.store._lock, self.store._connect() as con:
            row = con.execute('SELECT status FROM ontology_audit_run WHERE audit_run_id=?', (audit_id,)).fetchone()
            if not row: raise IntelligenceNotFound('audit run not found')
            if row[0] in {'completed','completed-with-gaps','failed','cancelled'}: raise IntelligenceConflict('audit run is already terminal')
            stamp = now(); con.execute("UPDATE ontology_audit_run SET status='cancelled',stop_reason=?,updated_at=?,completed_at=? WHERE audit_run_id=?", (reason, stamp, stamp, audit_id))
            con.execute("UPDATE ontology_capability_run SET status='cancelled',completed_at=? WHERE run_id=(SELECT capability_run_id FROM ontology_audit_run WHERE audit_run_id=?) AND status IN ('queued','running')", (stamp, audit_id))
            self._event_tx(con, audit_id, 'audit', 'cancelled', {'reason': reason})
        return self.get(audit_id)

    def retry(self, audit_id, payload=None):
        current = self.get(audit_id)
        if current['status'] not in {'failed','cancelled','completed-with-gaps'}:
            raise IntelligenceConflict('only failed, cancelled or gap-completed audits can be retried')
        request = {**current['request'], **(payload or {}), 'trigger': current['trigger']}
        return self.submit(request, current['entityId'], background=True, retry_of=audit_id)

    def review(self, audit_id, candidate_type, candidate_id, payload):
        audit = self.get(audit_id)
        if audit['status'] not in {'awaiting-review','completed-with-gaps','completed'}:
            raise IntelligenceConflict('audit is not ready for review')
        if candidate_type not in {'attribute','relation'}:
            raise IntelligenceValidationError('candidateType must be attribute or relation')
        action = str((payload or {}).get('action') or '').strip()
        if action not in {'accept','reject','request-more-evidence','edit','mark-conflict'}:
            raise IntelligenceValidationError('unsupported review action')
        if not any(item['candidateType'] == candidate_type and item['candidateId'] == candidate_id for item in audit['candidates']):
            raise IntelligenceConflict('candidate is not attached to this audit run')
        decision = dict(payload or {})
        if action != 'mark-conflict':
            if candidate_type == 'attribute':
                result = self.agent.review_attribute_candidate(candidate_id, decision)
            else:
                if action == 'edit': raise IntelligenceValidationError('relation candidates cannot be edited in place')
                result = self.agent.review_relation_candidate(candidate_id, decision)
        else:
            result = {'ok': True, 'candidateId': candidate_id, 'action': action}
        reviewer = str(decision.get('reviewer') or 'operator').strip()[:200] or 'operator'
        reason = str(decision.get('reason') or '').strip()
        if not reason: raise IntelligenceValidationError('reason is required')
        review_id = 'audit-review-' + secrets.token_hex(8)
        with self.store._lock, self.store._connect() as con:
            con.execute('INSERT INTO ontology_audit_review(audit_review_id,audit_run_id,candidate_type,candidate_id,action,reviewer,reason,decision_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (review_id, audit_id, candidate_type, candidate_id, action, reviewer, reason, dump(decision), now()))
        return {'ok': True, 'auditReviewId': review_id, 'result': result, 'audit': self.get(audit_id)}

    def publish(self, audit_id, payload):
        payload = payload or {}
        audit = self.get(audit_id)
        if audit['status'] not in {'awaiting-review', 'completed-with-gaps', 'completed'}:
            raise IntelligenceConflict('only a completed audit awaiting review can be published')
        profile_id = str((payload or {}).get('profileId') or (audit['result'] or {}).get('profileId') or '').strip()
        if not profile_id: raise IntelligenceValidationError('profileId is required to publish an audit result')
        # Publishing must reference this run's profile.  A profile from a
        # different entity/run is an easy way to bypass the audit boundary.
        profile = CapabilityProfiles(self.agent).get(profile_id)
        if profile.get('entityId') != audit['entityId'] or int(profile.get('objectRevision') or 0) != int(audit['objectRevision']):
            raise IntelligenceConflict('profile does not belong to the audited entity revision')
        selected = payload.get('recordIds')
        if not isinstance(selected, list) or not selected:
            raise IntelligenceValidationError('recordIds must explicitly select reviewed facts for publication')
        attached = {item['candidateId'] for item in audit['candidates'] if item['candidateType'] == 'attribute'}
        if not set(selected).issubset(attached):
            raise IntelligenceConflict('publication contains a capability record that is not attached to this audit run')
        result = CapabilityProfiles(self.agent).publish(profile_id, {**(payload or {}), 'auditRunId': audit_id})
        with self.store._lock, self.store._connect() as con:
            result_payload = {**(audit['result'] or {}), 'publicationId': result.get('publicationId'), 'publishedAt': now(), 'publishedBy': payload.get('publishedBy')}
            con.execute("UPDATE ontology_audit_run SET status='completed',result_json=?,updated_at=?,completed_at=? WHERE audit_run_id=?", (dump(result_payload), now(), now(), audit_id))
            self._event_tx(con, audit_id, 'publication', 'completed', {'publicationId': result.get('publicationId'), 'publishedBy': payload.get('publishedBy'), 'reason': payload.get('reason')})
        return {'ok': True, 'audit': self.get(audit_id), 'publication': result}

    def run_periodic(self, payload=None, *, background=True):
        payload = payload or {}
        max_age_days = float(payload.get('maxAgeDays', 30))
        entities = self.agent._entities()
        due = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        for entity in entities:
            if entity.get('entityType') != 'EquipmentModel': continue
            reasons = []
            try:
                updated = datetime.fromisoformat(str(entity.get('updatedAt','')).replace('Z','+00:00'))
                if updated < cutoff: reasons.append('stale-revision')
            except ValueError: reasons.append('missing-updated-at')
            _, doc = self.agent._entity_document(entity['entityId'])
            if (doc.get('completeness') or {}).get('unknownKeys'): reasons.append('missing-capability-fields')
            with self.store._connect() as con:
                pending = con.execute("SELECT COUNT(*) FROM ontology_attribute_candidate WHERE entity_id=? AND status IN ('under-review','needs-evidence')", (entity['entityId'],)).fetchone()[0]
            if pending: reasons.append('unresolved-candidates')
            if reasons: due.append({'entityId': entity['entityId'], 'reasons': reasons})
        runs = []
        for item in due:
            request = {**payload, 'trigger': 'periodic', 'periodicReasons': item['reasons']}
            try: runs.append(self.submit(request, item['entityId'], background=background))
            except IntelligenceConflict: continue
        return {'trigger': 'periodic', 'dueCount': len(due), 'submittedCount': len(runs), 'due': due, 'auditRuns': runs}

    def list_schedules(self):
        with self.store._connect() as con:
            rows = con.execute('SELECT * FROM ontology_audit_schedule ORDER BY created_at').fetchall()
        return [{
            'scheduleId': row['schedule_id'], 'name': row['name'], 'enabled': bool(row['enabled']),
            'intervalSeconds': row['interval_seconds'], 'policy': decode(row['policy_json'], {}),
            'lastRunAt': row['last_run_at'], 'nextRunAt': row['next_run_at'],
            'createdAt': row['created_at'], 'updatedAt': row['updated_at'],
        } for row in rows]

    def create_schedule(self, payload):
        payload = payload or {}
        name = str(payload.get('name') or '').strip()
        interval = payload.get('intervalSeconds', 86400)
        if not name or type(interval) is not int or interval < 300:
            raise IntelligenceValidationError('name and intervalSeconds (>=300) are required')
        schedule_id = 'audit-schedule-' + secrets.token_hex(8)
        stamp = now()
        next_run = (datetime.now(timezone.utc) + timedelta(seconds=interval)).isoformat()
        policy = dict(payload.get('policy') or {})
        with self.store._lock, self.store._connect() as con:
            con.execute('INSERT INTO ontology_audit_schedule(schedule_id,name,enabled,interval_seconds,policy_json,next_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (schedule_id, name, 1 if payload.get('enabled', True) else 0, interval, dump(policy), next_run, stamp, stamp))
        return next(item for item in self.list_schedules() if item['scheduleId'] == schedule_id)

    def run_schedule(self, schedule_id):
        with self.store._connect() as con:
            row = con.execute('SELECT * FROM ontology_audit_schedule WHERE schedule_id=?', (schedule_id,)).fetchone()
        if row is None:
            raise IntelligenceNotFound('audit schedule not found')
        if not row['enabled']:
            return {'scheduleId': schedule_id, 'status': 'disabled', 'submittedCount': 0, 'auditRuns': []}
        result = self.run_periodic(decode(row['policy_json'], {}), background=True)
        stamp = now(); next_run = (datetime.now(timezone.utc) + timedelta(seconds=row['interval_seconds'])).isoformat()
        with self.store._lock, self.store._connect() as con:
            con.execute('UPDATE ontology_audit_schedule SET last_run_at=?,next_run_at=?,updated_at=? WHERE schedule_id=?', (stamp, next_run, stamp, schedule_id))
        return {'scheduleId': schedule_id, 'lastRunAt': stamp, 'nextRunAt': next_run, **result}

    def run_due_schedules(self):
        due = []
        now_value = datetime.now(timezone.utc)
        for schedule in self.list_schedules():
            if not schedule['enabled']:
                continue
            try:
                if not schedule['nextRunAt'] or datetime.fromisoformat(schedule['nextRunAt'].replace('Z', '+00:00')) <= now_value:
                    due.append(self.run_schedule(schedule['scheduleId']))
            except ValueError:
                due.append(self.run_schedule(schedule['scheduleId']))
        return {'checkedAt': now(), 'dueCount': len(due), 'schedules': due}
