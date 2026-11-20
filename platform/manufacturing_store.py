"""Evidence-backed production and cost records for equipment models."""
from __future__ import annotations
import hashlib, json, secrets
from datetime import datetime, timezone
try:
    from manufacturing_contract import assert_valid
except ModuleNotFoundError:  # pragma: no cover
    from .manufacturing_contract import assert_valid

def _now(): return datetime.now(timezone.utc).isoformat()
def _json(v, default): return json.dumps(v if v is not None else default, ensure_ascii=False, sort_keys=True)
def _row(row):
    d = dict(row)
    for key in ('source_snapshot_ids_json','evidence_json','includes_json','excludes_json'):
        if key in d:
            out = key[:-5] if key.endswith('_json') else key
            try: d[out] = json.loads(d.pop(key))
            except Exception: d[out] = [] if key.endswith('ids_json') or key in ('includes_json','excludes_json') else {}
    return d

class ManufacturingStore:
    def __init__(self, store): self.store = getattr(store, 'store', store)
    def _list(self, table, entity_id):
        with self.store._connect() as con:
            return [_row(r) for r in con.execute(f'SELECT * FROM {table} WHERE entity_id=? ORDER BY updated_at DESC', (entity_id,)).fetchall()]
    def get(self, entity_id):
        # Published domain rows are the authoritative fact tables. Accepted
        # candidates are exposed through the same five read shapes with an
        # explicit accepted-not-published state, so the dossier can present a
        # structured preview without silently promoting model output to fact.
        accepted = self._accepted_records(entity_id)
        try:
            from factor_graph import FactorGraphStore
        except ModuleNotFoundError:  # pragma: no cover
            from .factor_graph import FactorGraphStore
        return {
            'entityId': entity_id,
            'production': self._list('ontology_production_record', entity_id) + accepted['production'],
            'costs': self._list('ontology_cost_record', entity_id) + accepted['costs'],
            'configurations': self._domain_list('ontology_manufacturing_configuration', entity_id) + accepted['configurations'],
            'assembly': self._domain_list('ontology_assembly_item', entity_id) + accepted['assembly'],
            'processes': self._domain_list('ontology_manufacturing_process_record', entity_id) + accepted['processes'],
            'organizationRoles': self._domain_list('ontology_manufacturing_org_role', entity_id) + accepted['organizationRoles'],
            'candidates': self.list_candidates(entity_id),
            'acceptedCandidateCount': accepted['count'],
            'factorGraphRuns': FactorGraphStore(self.store).list_runs(entity_id),
        }

    @staticmethod
    def _accepted_base(candidate, payload):
        return {
            'record_state': 'accepted-not-published',
            'review_status': 'accepted',
            'accepted_candidate_id': candidate['candidate_id'],
            'evidence': candidate.get('evidence') or payload.get('evidence') or {},
            'source_snapshot_ids': candidate.get('sourceSnapshotIds') or payload.get('sourceSnapshotIds') or [],
            'created_at': candidate.get('created_at'),
            'updated_at': candidate.get('updated_at'),
        }

    def _accepted_records(self, entity_id):
        result = {
            'count': 0, 'configurations': [], 'assembly': [], 'processes': [],
            'organizationRoles': [], 'production': [], 'costs': [],
        }
        configuration_ids = {}
        for candidate in self.list_candidates(entity_id):
            if candidate.get('status') != 'accepted':
                continue
            payload = candidate.get('payload') or {}
            base = self._accepted_base(candidate, payload)
            kind = candidate.get('candidate_type')
            configuration = payload.get('configuration')
            if configuration:
                configuration_key = (str(configuration), str(payload.get('variant') or ''))
                if configuration_key not in configuration_ids:
                    configuration_id = 'accepted-configuration-' + hashlib.sha256(
                        f'{entity_id}|{configuration_key[0]}|{configuration_key[1]}'.encode('utf-8')
                    ).hexdigest()[:16]
                    configuration_ids[configuration_key] = configuration_id
                    result['configurations'].append({
                        'configuration_id': configuration_id,
                        'entity_id': entity_id,
                        'name': configuration,
                        'variant': payload.get('variant'),
                        'valid_from': payload.get('validFrom'),
                        'valid_to': payload.get('validTo'),
                        'evidence': base['evidence'],
                        **{key: base[key] for key in ('record_state', 'review_status', 'accepted_candidate_id', 'source_snapshot_ids')},
                    })
                configuration_id = configuration_ids[configuration_key]
            else:
                configuration_id = None
            if kind == 'composition':
                result['assembly'].append({
                    'assembly_item_id': candidate['candidate_id'], 'entity_id': entity_id,
                    'configuration_id': configuration_id, 'parent_item_id': payload.get('parentItemId'),
                    'part_name': payload.get('partName'), 'relation': payload.get('relation'),
                    'configuration': configuration,
                    'quantity': payload.get('quantity'), **base,
                })
            elif kind == 'process':
                result['processes'].append({
                    'process_record_id': candidate['candidate_id'], 'entity_id': entity_id,
                    'configuration_id': configuration_id, 'process_name': payload.get('processName'),
                    'process_type': payload.get('processType'), 'sequence_no': payload.get('sequenceNo'),
                    'organization_id': payload.get('organizationId'), 'configuration': configuration, **base,
                })
            elif kind == 'organization':
                result['organizationRoles'].append({
                    'org_role_id': candidate['candidate_id'], 'entity_id': entity_id,
                    'organization_id': payload.get('organizationId'), 'organization_name': payload.get('organizationName'),
                    'role': payload.get('role'), 'valid_from': payload.get('validFrom'),
                    'valid_to': payload.get('validTo'), 'configuration': configuration, **base,
                })
            elif kind == 'production':
                result['production'].append({
                    'production_record_id': candidate['candidate_id'], 'entity_id': entity_id,
                    'record_type': payload.get('recordType'), 'organization_id': payload.get('organizationId'),
                    'quantity': payload.get('quantity'), 'quantity_unit': payload.get('quantityUnit') or 'unit',
                    'start_date': payload.get('startDate'), 'end_date': payload.get('endDate'),
                    'configuration': configuration, **base,
                })
            elif kind == 'cost':
                result['costs'].append({
                    'cost_record_id': candidate['candidate_id'], 'entity_id': entity_id,
                    'cost_type': payload.get('costType'), 'amount': payload.get('amount'),
                    'currency': payload.get('currency'), 'price_basis_year': payload.get('priceBasisYear'),
                    'quantity': payload.get('quantity'), 'quantity_unit': payload.get('quantityUnit') or 'unit',
                    'includes': payload.get('includes') or [], 'excludes': payload.get('excludes') or [],
                    'value_status': payload.get('valueStatus') or 'reported', 'configuration': configuration,
                    **base,
                })
            result['count'] += 1
        return result
    def _domain_list(self, table, entity_id):
        with self.store._connect() as con:
            rows = con.execute(f'SELECT * FROM {table} WHERE entity_id=? ORDER BY updated_at DESC', (entity_id,)).fetchall()
        return [_row(row) for row in rows]
    def list_candidates(self, entity_id=None):
        where = ' WHERE entity_id=?' if entity_id else ''
        with self.store._connect() as con:
            rows = con.execute('SELECT * FROM ontology_manufacturing_candidate'+where+' ORDER BY created_at DESC', (entity_id,) if entity_id else ()).fetchall()
        result=[]
        for row in rows:
            item=dict(row); item['payload']=json.loads(item.pop('payload_json')); item['evidence']=json.loads(item.pop('evidence_json')); item['sourceSnapshotIds']=json.loads(item.pop('source_snapshot_ids_json')); result.append(item)
        return result
    def create_candidate(self, entity_id, candidate_type, payload):
        payload = dict(payload or {})
        if candidate_type not in {'composition','process','organization','production','cost'}:
            raise ValueError('candidate type must be composition, process, organization, production or cost')
        # Validate with the same rules as publication, but do not create a fact.
        if candidate_type == 'production': self._validate_production(payload)
        elif candidate_type == 'cost': self._validate_cost(payload)
        else: self._validate_domain(candidate_type, payload)
        evidence=payload.get('evidence') or {}
        if not evidence and (payload.get('citation') or payload.get('sourceUrl') or payload.get('quote')):
            evidence={'sourceUri': payload.get('citation') or payload.get('sourceUrl'), 'quote': payload.get('quote') or ''}
        snapshots=payload.get('sourceSnapshotIds') or []
        if not evidence and not snapshots: raise ValueError('candidate requires evidence or sourceSnapshotIds')
        payload['evidence'] = evidence
        payload['sourceSnapshotIds'] = snapshots
        canonical=_json({'entityId':entity_id,'candidateType':candidate_type,'payload':payload,'evidence':evidence,'sourceSnapshotIds':snapshots}, {})
        digest=hashlib.sha256(canonical.encode()).hexdigest(); ident='mfg-candidate-'+digest[:20]; stamp=_now()
        with self.store._lock, self.store._connect() as con:
            con.execute('INSERT OR IGNORE INTO ontology_manufacturing_candidate(candidate_id,entity_id,candidate_type,payload_json,evidence_json,source_snapshot_ids_json,content_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,entity_id,candidate_type,_json(payload,{}),_json(evidence,{}),_json(snapshots,[]),digest,'under-review',stamp,stamp))
        return next(x for x in self.list_candidates(entity_id) if x['candidate_id']==ident)
    def ingest_extraction(self, document, expected_entity_id=None):
        assert_valid(document)
        entity_id=document['entity']['entityId']
        if expected_entity_id and entity_id != expected_entity_id:
            raise ValueError('extraction entityId does not match requested entity')
        created=[]
        for observation in document.get('observations') or []:
            payload=dict(observation.get('data') or {})
            payload.update({key: observation[key] for key in ('configuration','validFrom','validTo','confidence') if key in observation})
            evidence=observation['evidence']
            payload['evidence']=evidence
            payload['sourceSnapshotIds']=[evidence['snapshotId']] if evidence.get('snapshotId') else []
            created.append(self.create_candidate(entity_id,observation['category'],payload))
        return {'entityId':entity_id,'candidateCount':len(created),'candidates':created,'gaps':document.get('gaps') or []}
    def review_candidate(self, candidate_id, payload):
        action=str(payload.get('action') or '').strip(); reviewer=str(payload.get('reviewer') or 'operator').strip() or 'operator'; reason=str(payload.get('reason') or '').strip()
        if action not in {'accept','reject','request-more-evidence','edit'}: raise ValueError('invalid review action')
        if not reason: raise ValueError('reason is required')
        with self.store._lock, self.store._connect() as con:
            row=con.execute('SELECT * FROM ontology_manufacturing_candidate WHERE candidate_id=?',(candidate_id,)).fetchone()
            if row is None: raise LookupError('manufacturing candidate not found')
            status={'accept':'accepted','reject':'rejected','request-more-evidence':'needs-evidence','edit':'under-review'}[action]
            edited=payload.get('editedPayload') if action=='edit' else None
            if action=='edit' and isinstance(edited,dict):
                if row['candidate_type']=='production': self._validate_production(edited)
                elif row['candidate_type']=='cost': self._validate_cost(edited)
                else: self._validate_domain(row['candidate_type'], edited)
                con.execute('UPDATE ontology_manufacturing_candidate SET payload_json=?,status=?,updated_at=? WHERE candidate_id=?',(_json(edited,{}),status,_now(),candidate_id))
            else: con.execute('UPDATE ontology_manufacturing_candidate SET status=?,updated_at=? WHERE candidate_id=?',(status,_now(),candidate_id))
            review_id='mfg-review-'+secrets.token_hex(8); con.execute('INSERT INTO ontology_manufacturing_candidate_review(review_id,candidate_id,action,reviewer,reason,edited_payload_json,created_at) VALUES(?,?,?,?,?,?,?)',(review_id,candidate_id,action,reviewer,reason,_json(edited,{}) if isinstance(edited,dict) else None,_now()))
        return next(x for x in self.list_candidates() if x['candidate_id']==candidate_id)
    def publish_candidate(self, candidate_id, payload):
        publisher=str(payload.get('publishedBy') or payload.get('reviewer') or '').strip(); reason=str(payload.get('reason') or '').strip()
        if not publisher or not reason: raise ValueError('publishedBy and reason are required')
        candidate=next((x for x in self.list_candidates() if x['candidate_id']==candidate_id),None)
        if not candidate: raise LookupError('manufacturing candidate not found')
        if candidate['status']!='accepted': raise ValueError('candidate requires accepted human review')
        record=self._publish_domain(candidate)
        with self.store._lock, self.store._connect() as con: con.execute("UPDATE ontology_manufacturing_candidate SET status='published',updated_at=? WHERE candidate_id=?",(_now(),candidate_id))
        return {'candidateId':candidate_id,'record':record,'publishedBy':publisher,'reason':reason}
    @staticmethod
    def _validate_production(payload):
        if payload.get('recordType','production') not in {'production','delivery','order'}: raise ValueError('invalid recordType')
        if payload.get('quantity') is None or float(payload['quantity']) < 0: raise ValueError('quantity must be non-negative')
    @staticmethod
    def _validate_cost(payload):
        if payload.get('amount') is None or float(payload['amount']) < 0 or not payload.get('currency'): raise ValueError('amount, currency required')
    @staticmethod
    def _validate_domain(candidate_type, payload):
        required = {'composition': ('partName', 'relation'), 'process': ('processName', 'processType'), 'organization': ('organizationName', 'role')}[candidate_type]
        missing = [key for key in required if not payload.get(key)]
        if missing: raise ValueError(f"{candidate_type} requires {', '.join(missing)}")
        enums = {
            'composition': ('relation', {'partOfAircraft','partOfGroundSegment','installedOn','compatibleWith'}),
            'process': ('processType', {'design','fabrication','assembly','inspection','ground-test','flight-test','maintenance'}),
            'organization': ('role', {'designer','manufacturer','assembler','supplier','integrator','operator'}),
        }
        field, allowed = enums[candidate_type]
        if payload[field] not in allowed: raise ValueError(f'invalid {field}')
    def _configuration_id(self, con, entity_id, payload, evidence_json):
        name = payload.get('configuration')
        if not name: return None
        row = con.execute('SELECT configuration_id FROM ontology_manufacturing_configuration WHERE entity_id=? AND name=?',(entity_id,str(name))).fetchone()
        if row: return row[0]
        ident='configuration-'+secrets.token_hex(8); stamp=_now()
        con.execute('INSERT INTO ontology_manufacturing_configuration(configuration_id,entity_id,name,variant,valid_from,valid_to,evidence_json,review_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,entity_id,str(name),payload.get('variant'),payload.get('validFrom'),payload.get('validTo'),evidence_json,'published',stamp,stamp))
        return ident
    def _publish_domain(self, candidate):
        payload=candidate['payload']; kind=candidate['candidate_type']; entity_id=candidate['entity_id']
        if kind=='production': return self.create_production(entity_id,payload,review_status='published')
        if kind=='cost': return self.create_cost(entity_id,payload,review_status='published')
        stamp=_now(); ident=kind+'-'+secrets.token_hex(8); evidence=_json(candidate.get('evidence') or payload.get('evidence'),{})
        with self.store._lock,self.store._connect() as con:
            configuration_id=self._configuration_id(con,entity_id,payload,evidence)
            if kind=='composition':
                parent_id=payload.get('parentItemId')
                if payload.get('parentPart'):
                    parents=con.execute('SELECT assembly_item_id FROM ontology_assembly_item WHERE entity_id=? AND configuration_id IS ? AND part_name=?',(entity_id,configuration_id,str(payload['parentPart']))).fetchall()
                    if len(parents)!=1: raise ValueError('parentPart must resolve to exactly one published assembly item in this configuration')
                    parent_id=parents[0][0]
                if parent_id and not con.execute('SELECT 1 FROM ontology_assembly_item WHERE assembly_item_id=? AND entity_id=?',(parent_id,entity_id)).fetchone():
                    raise ValueError('parentItemId must reference a part of the same equipment model')
                con.execute('INSERT INTO ontology_assembly_item(assembly_item_id,entity_id,configuration_id,parent_item_id,part_name,relation,quantity,evidence_json,review_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,entity_id,configuration_id,parent_id,payload['partName'],payload['relation'],payload.get('quantity'),evidence,'published',stamp,stamp))
                return _row(con.execute('SELECT * FROM ontology_assembly_item WHERE assembly_item_id=?',(ident,)).fetchone())
            if kind=='process':
                con.execute('INSERT INTO ontology_manufacturing_process_record(process_record_id,entity_id,configuration_id,process_name,process_type,sequence_no,organization_id,evidence_json,review_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,entity_id,configuration_id,payload['processName'],payload['processType'],payload.get('sequenceNo'),payload.get('organizationId'),evidence,'published',stamp,stamp))
                return _row(con.execute('SELECT * FROM ontology_manufacturing_process_record WHERE process_record_id=?',(ident,)).fetchone())
            if kind=='organization':
                con.execute('INSERT INTO ontology_manufacturing_org_role(org_role_id,entity_id,organization_id,organization_name,role,valid_from,valid_to,evidence_json,review_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,entity_id,payload.get('organizationId'),payload['organizationName'],payload['role'],payload.get('validFrom'),payload.get('validTo'),evidence,'published',stamp,stamp))
                return _row(con.execute('SELECT * FROM ontology_manufacturing_org_role WHERE org_role_id=?',(ident,)).fetchone())
        raise ValueError(f'unsupported manufacturing candidate type: {kind}')
    def create_production(self, entity_id, payload, *, review_status='pending'):
        self._validate_production(payload)
        record_type = str(payload.get('recordType') or 'production')
        if record_type not in {'production', 'delivery', 'order'}:
            raise ValueError('recordType must be production, delivery or order')
        quantity = payload.get('quantity')
        if quantity is None or float(quantity) < 0:
            raise ValueError('quantity must be a non-negative number')
        if not payload.get('evidence') and not payload.get('sourceSnapshotIds'):
            raise ValueError('production record requires evidence or sourceSnapshotIds')
        return self._create('ontology_production_record', 'production_record_id', 'production-' + secrets.token_hex(8), entity_id, payload, {
            'record_type': record_type, 'organization_id': payload.get('organizationId'), 'quantity': float(quantity), 'quantity_unit': payload.get('quantityUnit') or 'unit', 'start_date': payload.get('startDate'), 'end_date': payload.get('endDate'), 'configuration': payload.get('configuration'), 'source_snapshot_ids_json': _json(payload.get('sourceSnapshotIds'), []), 'evidence_json': _json(payload.get('evidence'), {}), 'review_status': review_status})
    def create_cost(self, entity_id, payload, *, review_status='pending'):
        self._validate_cost(payload)
        cost_type = str(payload.get('costType') or 'other')
        if cost_type not in {'unit-production','flyaway','system-procurement','development','operating','lifecycle','other'}:
            raise ValueError('unsupported costType')
        try: amount = float(payload.get('amount'))
        except (TypeError, ValueError): raise ValueError('amount must be a number')
        if amount < 0 or not payload.get('currency'):
            raise ValueError('cost requires non-negative amount and currency')
        if not payload.get('evidence') and not payload.get('sourceSnapshotIds'):
            raise ValueError('cost record requires evidence or sourceSnapshotIds')
        return self._create('ontology_cost_record', 'cost_record_id', 'cost-' + secrets.token_hex(8), entity_id, payload, {
            'cost_type': cost_type, 'amount': amount, 'currency': str(payload.get('currency')).upper(), 'price_basis_year': payload.get('priceBasisYear'), 'quantity': payload.get('quantity'), 'quantity_unit': payload.get('quantityUnit') or 'unit', 'configuration': payload.get('configuration'), 'includes_json': _json(payload.get('includes'), []), 'excludes_json': _json(payload.get('excludes'), []), 'source_snapshot_ids_json': _json(payload.get('sourceSnapshotIds'), []), 'evidence_json': _json(payload.get('evidence'), {}), 'value_status': payload.get('valueStatus') or 'reported', 'review_status': review_status})
    def _create(self, table, key, ident, entity_id, payload, values):
        stamp = _now(); cols = [key, 'entity_id', *values, 'created_at', 'updated_at']; vals = [ident, entity_id, *values.values(), stamp, stamp]
        with self.store._lock, self.store._connect() as con:
            con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", vals)
            return _row(con.execute(f'SELECT * FROM {table} WHERE {key}=?', (ident,)).fetchone())
