from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS
from dataclasses import replace
from jsonschema import validate, ValidationError

class DataContractError(ValidationError, ValueError):
 code = 'data-contract-mismatch'

class ValidationEngine:
 def schema(self,result,schema):
  if isinstance(schema,str):
   import json; schema=json.loads(schema or '{}')
  try: validate(result,schema or {})
  except ValidationError as exc:
   # Persist locations and rules, never a dump of the source/model payload.
   path='/'+'/'.join(str(v).replace('~','~0').replace('/','~1') for v in exc.absolute_path)
   schema_path='/'+'/'.join(map(str,exc.absolute_schema_path))
   raise DataContractError(f'data-contract-mismatch at {path}: {exc.validator}; schema={schema_path}') from None
 def step(self,result):
  if not isinstance(result,dict): raise ValueError('step output must be an object')
 def step_contract(self,step,result):
  self.step(result)
  schema=step.output_contract or {}
  if isinstance(schema,str):
   import json; schema=json.loads(schema or '{}')
  if schema: self.schema(result,schema)
 def output(self,profile,result):
  if not isinstance(result,dict): raise ValueError('agent output must be an object')
  schema=profile.output_contract.get('schema_json') or {}
  if isinstance(schema,str):
   import json; schema=json.loads(schema or '{}')
  if schema: self.schema(result,schema)
  required=schema.get('required',[]) if isinstance(schema,dict) else []
  missing=[x for x in required if x not in result]
  if missing: raise ValueError(f'missing output fields: {missing}')
  if profile.output_contract.get('required_evidence') and 'evidence' not in result:
   raise ValueError('output requires evidence')
  if profile.output_contract.get('required_assumptions') and 'assumptions' not in result:
   raise ValueError('output requires assumptions')
  if profile.output_contract.get('required_confidence') and not result.get('confidence'):
   raise ValueError('output requires confidence')
  if profile.agent_key in FUTURE_AGENT_KEYS:
   self._future_evidence_integrity(result)
   from .decision_semantics import enabled, validate_report
   if enabled(profile): validate_report(result)
   if 'ontology' in result:
    self._future_ontology_integrity(result)

 def research_output(self,profile,result):
  # A frozen research Profile declares a separate report envelope. The legacy
  # fixed-plan output (for example designOptions/budgets) describes a different
  # execution mode and must not invalidate the role's research deliverables.
  config=profile.model_policy.get('config_json',{})
  contract=config.get('researchOutputContract') or {}
  if not contract:
   return self.output(profile,result)
  schema=contract.get('schema_json') or {'type':'object','required':contract.get('required',[])}
  output_contract={**profile.output_contract,'schema_json':schema}
  self.output(replace(profile,output_contract=output_contract),result)
  deliverables=result.get('deliverables') or {}
  for kind in contract.get('requiredDeliverables',[]):
   if not isinstance(deliverables.get(kind),str) or not deliverables[kind]:
    raise ValueError(f'missing research deliverable: {kind}')

 def _future_ontology_integrity(self,result):
  from .future_report import ENTITY_TYPES, PREDICATES
  graph=result['ontology']
  if not isinstance(graph,dict) or graph.get('schemaVersion')!='1.0':
   raise ValueError('unsupported report ontology schema')
  nodes=graph.get('nodes',[])
  by_id={n['id']:n for n in nodes}
  if len(by_id)!=len(nodes): raise ValueError('ontology node identities must be unique')
  for edge in graph.get('edges',[]):
   if edge.get('subject') not in by_id or edge.get('object') not in by_id:
    raise ValueError('ontology edge references unknown node')
  evidence={str(e.get('evidenceId') or e.get('claimId')):e for e in result.get('evidence',[])}
  findings={f.get('findingId') or f.get('perspectiveKey'):f for f in result.get('findings',[])}
  seen=set()
  for assertion in graph.get('assertions',[]):
   identity=assertion.get('id')
   if identity in seen: raise ValueError('duplicate ontology assertion')
   seen.add(identity)
   if (identity not in by_id or assertion.get('subject') not in by_id or assertion.get('object') not in by_id
       or assertion.get('predicate') not in PREDICATES):
    raise ValueError('invalid ontology relationship')
   if any(by_id[assertion[key]].get('type') not in ENTITY_TYPES for key in ('subject','object')):
    raise ValueError('ontology relationship endpoint must be a domain entity')
   finding=findings.get(assertion.get('findingId') or assertion.get('perspectiveKey'),{})
   if (finding.get('status')!='candidate' or assertion.get('supportReview',{}).get('verdict')!='supported'
       or assertion.get('truthStatus')!='unverified'):
    raise ValueError('ontology assertion lacks supported candidate judgment')
   ids=assertion.get('evidenceIds',[])
   refs={r.get('evidenceId') for r in finding.get('supportingEvidence',[])}
   if not ids or not set(ids)<=evidence.keys() or not set(ids)<=refs:
    raise ValueError('ontology assertion references unsupported evidence')

 def _future_evidence_integrity(self,result):
  """Require structured evidence references for every substantive finding.

  Empty references are allowed only for findings explicitly marked as
  insufficient evidence.  A string that merely resembles a citation is not a
  valid reference to a stored artifact.
  """
  evidence = result.get('evidence') or []
  evidence_ids = {str(item.get('evidenceId') or item.get('claimId')) for item in evidence
                  if isinstance(item, dict) and (item.get('evidenceId') or item.get('claimId'))}
  evidence_by_id = {str(item.get('evidenceId') or item.get('claimId')): item for item in evidence
                    if isinstance(item, dict)}
  for index, finding in enumerate(result.get('findings') or []):
   if not isinstance(finding, dict):
    raise ValueError(f'findings[{index}] must be an object')
   refs = finding.get('supportingEvidence')
   if refs is None:
    raise ValueError(f'findings[{index}] requires structured supportingEvidence')
   if not isinstance(refs, list):
    raise ValueError(f'findings[{index}].supportingEvidence must be an array')
   insufficient = finding.get('status') == 'insufficient-evidence'
   if not refs and not insufficient:
    raise ValueError(f'findings[{index}] has no supporting evidence')
   for ref_index, ref in enumerate(refs):
    if not isinstance(ref, dict):
     raise ValueError(f'findings[{index}].supportingEvidence[{ref_index}] must be an object')
    required = ('evidenceId', 'sourceArtifactId', 'sourceArtifactHash', 'contentHash', 'locator', 'truthStatus')
    missing = [key for key in required if not ref.get(key)]
    if missing:
     raise ValueError(f'findings[{index}].supportingEvidence[{ref_index}] missing {missing}')
    if str(ref['evidenceId']) not in evidence_ids:
     raise ValueError(f'findings[{index}] references unknown evidenceId: {ref["evidenceId"]}')
    source = evidence_by_id[str(ref['evidenceId'])]
    if any(ref.get(field) != source.get(field) for field in required):
     raise ValueError(f'findings[{index}] reference differs from its validated evidence')
    if ref.get('truthStatus') not in {'unverified', 'human-reviewed-unverified', 'verified'}:
     raise ValueError(f'findings[{index}] has invalid truthStatus')
    if not isinstance(ref.get('sourceArtifactHash'), str) or len(ref['sourceArtifactHash']) != 64:
     raise ValueError(f'findings[{index}] has invalid sourceArtifactHash')
 def evidence_gaps(self,profile,result):
  policy=profile.evidence_policy; items=result.get('evidence',[]); gaps=[]
  if len(items) < int(policy.get('minimum_evidence_count',1)):
   gaps.append('minimum evidence count not reached')
  for i,item in enumerate(items):
   if policy.get('require_source_uri') and not item.get('sourceUri'): gaps.append(f'evidence[{i}] has no source URI')
   if policy.get('require_data_timestamp') and not item.get('dataTimestamp'): gaps.append(f'evidence[{i}] has no data timestamp')
   if item.get('provenance',{}).get('mode') == 'ontology-sqlite':
    if item.get('truthVerified') is not True or item.get('verificationStatus') != 'verified':
     gaps.append(f'evidence[{i}] is not verified')
    if item.get('reviewStatus') != 'approved': gaps.append(f'evidence[{i}] is not approved')
    if not item.get('conditions'): gaps.append(f'evidence[{i}] has no measurement conditions')
  gaps.extend(result.get('sourceGaps',[]))
  if profile.agent_key == 'uav-capability-analyst':
   compliance=result.get('assessment',{}).get('compliance',{})
   gaps.extend(f'requirement not assessed: {key}' for key in compliance.get('unknownRequirements',[]))
   if not result.get('assessment',{}).get('requirements'): gaps.append('no requirements supplied')
  return list(dict.fromkeys(gaps))
