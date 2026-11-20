"""Read-only ontology extraction; freeze records before creating a runtime Run."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / 'ontology/coast-raid-k/coast-raid-k-intelligence.sqlite'
METRICS = {
    'endurance': ('enduranceHours', {'h': 1, 'min': 1 / 60}),
    'payload-capacity': ('payloadKg', {'kg': 1}),
    'max-takeoff-weight': ('mtowKg', {'kg': 1}),
    # Maximum speed never supplies a cruise-speed requirement.
    'max-speed': ('maxSpeedMps', {'km/h': 1 / 3.6, 'm/s': 1}),
}


def capability_input(entity_id, requirements, source_path=None):
    """Extract one current revision in a read transaction, without altering the source.

    Exact, single reported values are retained as provisional comparisons. All
    source claims and their qualifications remain in the frozen input. Missing,
    ambiguous and unsupported quantities never become numeric defaults.
    """
    path = Path(source_path or DEFAULT_SOURCE).resolve(strict=True)
    gaps, evidence, claims = [], [], {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        row = con.execute('''
            SELECT e.entity_id,e.preferred_name,e.current_revision,
                   r.entity_revision_id,r.attributes_json,r.attributes_sha256,
                   r.source_snapshot_id,r.review_status,r.created_at
            FROM ontology_entity e JOIN ontology_entity_revision r
              ON r.entity_id=e.entity_id AND r.revision=e.current_revision
            WHERE e.entity_id=?''', (entity_id,)).fetchone()
        if row is None:
            raise ValueError(f'entity has no current revision: {entity_id}')
        attributes = json.loads(row['attributes_json']).get('attributes', {})
        uav = {'uavId': entity_id, 'name': row['preferred_name']}
        for attribute, (metric, units) in METRICS.items():
            group = attributes.get(attribute, {})
            values = group.get('values', [])
            claims[metric] = group
            exact = group.get('state') == 'reported' and len(values) == 1 and values[0].get('qualifier') == 'exact'
            if not exact:
                gaps.append(f'{metric}: missing, conflicting or non-exact value')
            for claim in values:
                raw = claim.get('normalizedValue', claim.get('value'))
                unit = claim.get('unit')
                valid_number = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0
                if exact and valid_number and unit in units:
                    uav[metric] = raw * units[unit]
                elif exact:
                    gaps.append(f'{metric}: unsupported unit or non-numeric value')
                if claim.get('truthVerified') is not True:
                    gaps.append(f'{metric}: source claim is unverified')
                if claim.get('reviewStatus') != 'approved':
                    gaps.append(f'{metric}: source review is not approved')
                if not claim.get('conditions'):
                    gaps.append(f'{metric}: measurement conditions are missing')
                if not claim.get('observedAt'):
                    gaps.append(f'{metric}: observation timestamp is missing')
                refs = claim.get('evidenceRefs', [])
                if not refs:
                    gaps.append(f'{metric}: source evidence is missing')
                for ref in refs:
                    eid = ref.get('evidenceId')
                    linked = con.execute('SELECT * FROM ontology_evidence_link WHERE evidence_id=?', (eid,)).fetchone()
                    source = dict(linked) if linked else {}
                    if not linked:
                        gaps.append(f'{metric}: evidence link is missing: {eid}')
                    item = {
                        'source': 'ontology-attribute', 'recordId': eid, 'metric': metric,
                        'sourceUri': path.as_uri() + '#evidence=' + quote(str(eid)),
                        'dataTimestamp': claim.get('observedAt'),
                        'sourceRef': source.get('source_ref', ref.get('sourceRef')),
                        'sourceVersion': source.get('source_version', ref.get('sourceVersion')),
                        'contentHash': source.get('content_hash', ref.get('contentHash')),
                        'quote': source.get('quote', ref.get('quote')),
                        'locator': json.loads(source['locator_json']) if source.get('locator_json') else ref.get('locator'),
                        'verificationStatus': source.get('verification_status', 'unknown'),
                        'truthVerified': claim.get('truthVerified'), 'reviewStatus': claim.get('reviewStatus'),
                        'conditions': claim.get('conditions'), 'qualifier': claim.get('qualifier'),
                        'publishedAt': source.get('published_at'), 'retrievedAt': source.get('retrieved_at'),
                        'importedAt': source.get('created_at'),
                        'provenance': {'mode': 'ontology-sqlite', 'entityId': entity_id,
                                       'revision': row['current_revision'], 'assertionId': claim.get('assertionId')},
                    }
                    if item['verificationStatus'] != 'verified':
                        gaps.append(f'{metric}: evidence verification is incomplete')
                    if not item['sourceVersion'] or not item['contentHash'] or not item['locator']:
                        gaps.append(f'{metric}: evidence provenance is incomplete')
                    evidence.append(item)
        snapshot = {
            'mode': 'ontology-sqlite', 'sourceUri': path.as_uri(), 'entityId': entity_id,
            'revision': row['current_revision'], 'entityRevisionId': row['entity_revision_id'],
            'attributesSha256': row['attributes_sha256'], 'sourceSnapshotId': row['source_snapshot_id'],
            'reviewStatus': row['review_status'], 'importedAt': row['created_at'],
            'extractedAt': datetime.now(timezone.utc).isoformat(),
        }
    gaps.append('flight/mission/energy/battery telemetry is not available from this ontology adapter')
    if snapshot['reviewStatus'] != 'approved':
        gaps.append('entity revision review is not approved')
    frozen = {
        'uavId': entity_id, 'requirements': requirements, 'uavData': uav,
        'sourceSnapshot': snapshot, 'sourceClaims': claims, 'sourceEvidence': evidence,
        'sourceGaps': list(dict.fromkeys(gaps)),
        'assumptions': ['Ontology-reported values support provisional comparison only; this is not an engineering certification.'],
    }
    snapshot['extractionHash'] = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return frozen
