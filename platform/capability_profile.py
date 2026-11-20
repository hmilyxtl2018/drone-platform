"""Version-bound, source-qualified equipment capability projections.

Grouping a parameter does not assert mission effectiveness. This module never
calls a provider or publishes facts during a read. Legacy assertions are read
unchanged; missing evidence is recovered only by the exact assertion ID.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
except ModuleNotFoundError:
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError

ROOT = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k"
SCHEMA_VERSION = "equipment-capability-profile.v1"
REGISTRY = json.loads((ROOT / "equipment-capability-registry.json").read_text("utf-8"))
SCHEMA = json.loads((ROOT / "schema.equipment-capability-profile.json").read_text("utf-8"))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def profile_hash(profile):
    return digest({k:v for k,v in profile.items() if k not in {"profileId", "contentHash"}})


def record_hash(record):
    """Bind a decision to source content and context, independent of review state."""
    keys = ('id','attributeKey','origin','rawValue','value','unit','qualifier',
            'lowerBound','upperBound','conditions','validTime','variant',
            'configuration','capabilityRole','evidenceRefs','derivation')
    return digest({k:record.get(k) for k in keys})


def comparability_for(record):
    reasons=[]
    value=record['value']
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value): reasons.append('不是单一数值')
    if record['qualifier']!='exact': reasons.append('限定值与精确值不能直接混合')
    if not record['unit'] or record['unit']!=REGISTRY['fields'][record['attributeKey']].get('unit'): reasons.append('单位缺失或口径不同')
    if not record['conditions']: reasons.append('适用条件未记载')
    if not record['variant'] or not record['configuration']: reasons.append('改型或配置未记载')
    if record['verificationStatus']!='verified': reasons.append('尚未核验')
    if record['origin']=='candidate' and record['reviewStatus']!='published': reasons.append('候选尚未入档')
    if record['claimState']=='conflict': reasons.append('来源冲突')
    if record['claimState']=='rejected': reasons.append('历史值已被逐条审核排除')
    return {'eligible':not reasons,'reasons':reasons}


def normalize_measurement(raw, normalized, unit, spec):
    """Keep ranges and bounds; never turn the first number into an exact value."""
    value = normalized if normalized is not None else raw
    qualifier, lower, upper = "exact", None, None
    if spec.get("valueType") not in {"number", "quantity"}:
        return value, unit, qualifier, lower, upper
    text = str(raw or "").strip().replace(",", "")
    numeric = r"(?:\d+(?:\.\d+)?|\.\d+)"
    canonical = spec.get('unit')
    explicit_unit=re.search(rf'{numeric}\s*(km/h|KTAS|kts|kt|nautical miles|hours|hour|minutes|pounds|feet|lbs|lb|km|kg|ms|min|ft|h|s|m|%)(?![a-zA-Z])',text,re.I)
    if explicit_unit:unit=explicit_unit[1]
    if not unit and text:
        for token in (canonical,'km/h','ktas','kts','kt','km','kg','lbs','lb','ms','min','h','s','m','%'):
            if token and re.search(rf'(?<![a-zA-Z]){re.escape(token)}(?![a-zA-Z])',text,re.I):
                unit = token
                break
    interval = re.match(rf"^\s*({numeric})\s*(?:[-–—~～]|至|to)\s*({numeric})(?:\s|[a-zA-Z%]|$)", text, re.I)
    if interval:
        lower, upper = float(interval[1]), float(interval[2])
        value, qualifier = None, 'range'
    bound = re.match(rf"^\s*(>=|<=|≥|≤|>|<|至少|不低于|至多|不超过)\s*({numeric})", text)
    trailing = re.match(rf"^\s*({numeric})\s*\+", text)
    approximate = re.match(r"^\s*(?:~|≈|约|大约|about\b|approx(?:imately)?\.?\b)", text, re.I)
    if interval:
        pass
    elif bound or trailing:
        prefix = bound[1] if bound else "+"
        value = float(bound[2] if bound else trailing[1])
        qualifier = {">=":"at-least", "≥":"at-least", "至少":"at-least", "不低于":"at-least", "+":"at-least", "<=":"at-most", "≤":"at-most", "至多":"at-most", "不超过":"at-most", ">":"greater-than", "<":"less-than"}[prefix]
        lower, upper = (value,None) if qualifier in {"at-least","greater-than"} else (None,value)
    elif approximate:
        qualifier = "approximate"
        match = re.search(numeric,text[approximate.end():])
        value = float(match[0]) if match else None
    if qualifier == 'exact' or isinstance(value, str):
        # Parse the literal raw value before accepting legacy normalization.
        match = re.fullmatch(rf"\s*({numeric})\s*(?:{re.escape(str(unit))})?\s*",text,re.I) if unit else re.fullmatch(rf"\s*({numeric})\s*",text)
        # Legacy imports sometimes stored the first number from a compound
        # expression. Do not turn two payload components into one scalar.
        value = float(match[1]) if match else text if text else value
    conversions = {('hr','h'):1,('hrs','h'):1,('hours','h'):1,('hour','h'):1,('minutes','h'):1/60,('feet','m'):0.3048,('ft','m'):0.3048,('pounds','kg'):0.45359237,('nautical miles','km'):1.852,("kt","km/h"):1.852, ("kts","km/h"):1.852, ("ktas","km/h"):1.852, ("lb","kg"):0.45359237, ("lbs","kg"):0.45359237, ("min","h"):1/60, ("m","km"):0.001}
    factor = conversions.get((str(unit).lower(),str(canonical).lower()))
    if factor and (isinstance(value,(int,float)) or qualifier == "range"):
        value = value*factor if isinstance(value,(int,float)) and not isinstance(value,bool) else value
        lower, upper = (lower*factor if lower is not None else None), (upper*factor if upper is not None else None)
        unit = canonical
    return value, unit, qualifier, lower, upper


def quality_for(capabilities):
    records = [m for c in capabilities for m in c["metrics"]]
    values = [m for m in records if m["claimState"] not in {"unknown","rejected"} and m["rawValue"] is not None]
    expected = {m["attributeKey"] for m in records}
    present = {m["attributeKey"] for m in values}
    referenced = [m for m in values if any(r["snapshotMatched"] for r in m["evidenceRefs"])]
    public = [m for m in values if any(r["snapshotMatched"] and r["sourceType"] != "workbook" and r["sourceRef"].startswith(("https://","http://")) for r in m["evidenceRefs"])]
    verified = [m for m in values if m["verificationStatus"] == "verified"]
    count = len(values)
    ratio = lambda n,d: n/d if d else None
    result = {"expectedFieldCount":len(expected), "presentFieldCount":len(present), "valueCount":count,
            "referencedCount":len(referenced), "publicSourceCount":len(public), "verifiedCount":len(verified),
            "conflictCount":sum(m["claimState"] == "conflict" for m in values),
            "fieldCoverage":ratio(len(present),len(expected)), "referenceCoverage":ratio(len(referenced),count),
            "publicSourceCoverage":ratio(len(public),count), "verifiedCoverage":ratio(len(verified),count)}
    rejected=sum(m['claimState']=='rejected' for m in records)
    if rejected:result['historicalRejectedCount']=rejected
    return result


def validate_profile(profile):
    errors = [f"{'/'.join(map(str,e.path))}: {e.message}" for e in Draft202012Validator(SCHEMA).iter_errors(profile)]
    if errors:
        return errors
    if profile_hash(profile) != profile["contentHash"]:
        errors.append("contentHash does not match the immutable profile")
    if profile["profileId"] != "cap-profile-"+profile["contentHash"][:24]:
        errors.append("profileId does not match contentHash")
    if quality_for(profile["capabilities"]) != profile["quality"]:
        errors.append("quality counts or coverage do not match the records")
    ids = set()
    groups = {g["id"]:g for g in REGISTRY["groups"]}
    for cap in profile["capabilities"]:
        group = groups.get(cap["code"])
        if not group:
            errors.append("unregistered capability code")
            continue
        for m in cap["metrics"]:
            if m["id"] in ids:
                errors.append("duplicate metric ID")
            ids.add(m["id"])
            if m["attributeKey"] not in group["keys"]:
                errors.append("metric does not belong to its declared capability")
            if m["claimState"] == "unknown" and (m["value"] is not None or m["lowerBound"] is not None or m["upperBound"] is not None):
                errors.append("unknown cannot contain a value or bound")
            if m["lowerBound"] is not None and m["upperBound"] is not None and m["lowerBound"] > m["upperBound"]:
                errors.append("lowerBound exceeds upperBound")
            if m["qualifier"] == "range" and (m["lowerBound"] is None or m["upperBound"] is None or m["value"] is not None):
                errors.append("range requires bounds and cannot pretend to be an exact value")
            if m["claimState"] in {"derived","assessed"} and not m["derivation"]:
                errors.append("derived/assessed record requires derivation inputs and method")
            if m["verificationStatus"] == "verified" and (m["claimState"] in {"unknown","conflict","rejected","assessed"} or m["reviewStatus"] not in {"accepted","published"} or not any(r["snapshotMatched"] and r["sourceType"] != "workbook" for r in m["evidenceRefs"])):
                errors.append("verified requires accepted review and matching public evidence")
    for insight in profile["insights"]:
        if any(i not in ids for i in insight["supportingRecordIds"]):
            errors.append("insight references an unknown metric")
    for cap in profile['capabilities']:
        local_ids={m['id'] for m in cap['metrics']}
        for statement in cap['operationalEffects']+cap['technicalMechanisms']+cap['constraints']:
            if not set(statement['supportingRecordIds'])<=local_ids:
                errors.append('capability statement references an unrelated metric')
            if statement['claimState']=='observed' and not any(r['snapshotMatched'] and statement['text'] in r['quote'] for r in statement['evidenceRefs']):
                errors.append('reported capability statement is not literal source text')
    return sorted(set(errors))


class CapabilityProfiles:
    def __init__(self, agent):
        self.agent, self.store = agent, agent.store

    def _evidence(self, assertion, candidate, con):
        if candidate:
            refs = candidate.get("evidence") or []
            output = []
            for i,ref in enumerate(refs):
                snapshot = con.execute('SELECT item_json FROM ontology_collected_source_snapshot WHERE snapshot_id=? AND verification_id=?',(ref.get('snapshotId'),ref.get('sourceJobId'))).fetchone()
                item = decode(snapshot[0],{}) if snapshot else {}
                quote = str(ref.get("quote") or "")
                matched = bool(quote and quote in str(item.get("body") or "") and ref.get("contentHash") == item.get("contentHash") and (ref.get("sourceUri") or "") == (item.get("sourceUri") or item.get("itemUri") or ""))
                output.append({"evidenceId":str(ref.get('snapshotId') or f"{candidate['candidateId']}:ref:{i}"), "sourceType":"web-page", "sourceRef":str(ref.get("sourceUri") or ""), "sourceVersion":ref.get("sourceVersion"), "contentHash":ref.get("contentHash"), "quote":quote, "locator":{"lineStart":ref.get("lineStart"),"lineEnd":ref.get("lineEnd")}, "snapshotMatched":matched})
            return output
        refs = assertion.get("evidenceRefs") or []
        if not isinstance(refs,list):
            refs = []
        row = con.execute("SELECT evidence_ids FROM ontology_assertion WHERE assertion_id=?", (assertion.get("assertionId"),)).fetchone()
        bound_ids = decode(row[0],[]) if row else []
        ids = list(dict.fromkeys([r.get("evidenceId") for r in refs if isinstance(r,dict)]+bound_ids))
        output = []
        for evidence_id in ids:
            evidence = con.execute("SELECT * FROM ontology_evidence_link WHERE evidence_id=?", (evidence_id,)).fetchone()
            if not evidence:
                continue
            r = dict(evidence)
            if evidence_id not in bound_ids and not any(ref.get("evidenceId")==evidence_id and ref.get("quote")==r["quote"] and ref.get("contentHash")==r["content_hash"] for ref in refs):
                continue
            output.append({"evidenceId":r["evidence_id"], "sourceType":r["source_type"], "sourceRef":r["source_ref"], "sourceVersion":r["source_version"], "contentHash":r["content_hash"], "quote":r["quote"], "locator":decode(r["locator_json"],{}), "snapshotMatched":True})
        return output

    def read(self, entity_id):
        entity, doc = self.agent._entity_document(entity_id)
        candidates = self.agent.list_attribute_candidates(entity["entityId"])
        jobs = self.agent.queue.list(entity_id=entity["entityId"])
        attrs = doc.get("attributes") or {}
        capabilities = []
        with self.store._connect() as con:
            current = con.execute("SELECT entity_revision_id FROM ontology_entity_revision WHERE entity_id=? AND revision=?",(entity["entityId"],entity["currentRevision"])).fetchone()
            jobs = [j for j in jobs if current and j["entityRevisionId"]==current[0]]
            job_ids = {j["verificationId"] for j in jobs}
            candidates = [c for c in candidates if c.get("sourceJobId") in job_ids]
            sources = [dict(r) for r in con.execute("SELECT * FROM ontology_source_reference WHERE entity_id=? ORDER BY source_reference_id", (entity["entityId"],))]
            lineage = [dict(r) for r in con.execute("SELECT * FROM ontology_lineage_edge WHERE predecessor_entity_id=? OR successor_entity_id=? ORDER BY family_name,sequence_no", (entity["entityId"],entity["entityId"]))]
            events = [dict(r) for r in con.execute("SELECT * FROM ontology_event WHERE EXISTS (SELECT 1 FROM json_each(subject_entity_ids) WHERE value=?) ORDER BY start_time,event_id", (entity["entityId"],))]
            for group in REGISTRY["groups"]:
                metrics = []
                for key in group["keys"]:
                    bucket = attrs.get(key) or {}
                    values = [(v,None) for v in bucket.get("values",[]) if isinstance(v,dict)]
                    proposed = [c for c in candidates if c["attributeKey"]==key and c["status"] not in {"rejected","withdrawn"}]
                    values += [({**c.get('metadata',{}),"assertionId":c["candidateId"], "rawValue":c["proposedValue"], "normalizedValue":c["proposedValue"], "unit":c["unit"], "conditions":c["conditions"], "confidence":c["confidence"], "reviewStatus":c["status"]},c) for c in proposed]
                    if not values:
                        values = [({},None)]
                    for index,(v,candidate) in enumerate(values):
                        spec = REGISTRY["fields"][key]
                        exists = v.get("rawValue") is not None or v.get("normalizedValue") is not None
                        refs = self._evidence(v,candidate,con) if exists else []
                        normalized = normalize_measurement(v.get("rawValue"),v.get("normalizedValue"),v.get("unit"),spec)
                        value,unit,qualifier,lower,upper = normalized if exists else (None,None,"unspecified",None,None)
                        declared = v.get('qualifier')
                        declared={'approximately':'approximate','unknown':'unspecified'}.get(declared,declared)
                        if qualifier=='exact' and declared in {'approximate','at-least','at-most','greater-than','less-than','unspecified'}:
                            qualifier=declared
                            if isinstance(value,(int,float)) and not isinstance(value,bool):
                                if qualifier in {'at-least','greater-than'}:lower=value
                                if qualifier in {'at-most','less-than'}:upper=value
                        claim = "conflict" if bucket.get("state")=="conflict" else "derived" if v.get("claimKind")=="inference" else "observed" if exists else "unknown"
                        review = v.get("reviewStatus") or "pending"
                        verified = bool(v.get("truthVerified") and review in {"accepted","published"} and any(r["snapshotMatched"] and r["sourceType"] != "workbook" for r in refs) and claim == "observed")
                        reasons = []
                        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value): reasons.append("不是单一数值")
                        if qualifier != "exact": reasons.append("限定值与精确值不能直接混合")
                        if not unit or unit != spec.get("unit"): reasons.append("单位缺失或口径不同")
                        if not v.get("conditions"): reasons.append("适用条件未记载")
                        if not v.get('variant') or not v.get('configuration'): reasons.append('改型或配置未记载')
                        if not verified: reasons.append("尚未核验")
                        if candidate: reasons.append("候选尚未入档")
                        if claim == "conflict": reasons.append("来源冲突")
                        relevant = [j for j in jobs if key in j.get("attributeKeys",[])]
                        missing = None if exists else "本次检索未找到资料" if relevant and all(j["status"] in {"completed","needs-review"} and not (j.get("result") or {}).get("items") for j in relevant) else "检索失败" if any(j["status"]=="failed" for j in relevant) else "有材料，未形成属性" if any((j.get("result") or {}).get("items") for j in relevant) else "尚无属性资料"
                        metrics.append({"id":str(v.get("assertionId") or f"{entity['entityId']}:{key}:{index}"), "attributeKey":key,"label":spec["label"], "assertionId":v.get("assertionId"), "origin":"candidate" if candidate else "archive" if exists else "gap", "rawValue":str(v.get("rawValue") if v.get('rawValue') is not None else v.get('normalizedValue')) if exists else None, "value":value,"unit":unit,"qualifier":qualifier,"lowerBound":lower,"upperBound":upper, "conditions":v.get("conditions"),"validTime":v.get("validTime"),"variant":v.get("variant"),"configuration":v.get("configuration"), "capabilityRole":v.get("capabilityRole"), "claimState":claim,"verificationStatus":"conflict" if claim=="conflict" else "verified" if verified else "unverified" if exists else "unknown", "reviewStatus":review, "extractionConfidence":v.get("confidence") if candidate else None,'importConfidence':v.get('confidence') if exists and not candidate else None, "evidenceRefs":refs, "missingReason":missing,"comparability":{"eligible":not reasons,"reasons":reasons},"derivation":v.get("derivation") or ({"method":"legacy-inference","inputAssertionIds":[],"limitations":["未保存推导过程"]} if claim=="derived" else None)})
                capabilities.append({"id":f"{entity['entityId']}:capability:{group['id']}", "code":group["id"],"label":group["label"], "missionRole":None,"operationalEffects":[],"technicalMechanisms":[],"metrics":metrics,"constraints":[],"questions":group["questions"],"summary":{"recordCount":len(metrics),"presentCount":sum(m["claimState"]!="unknown" for m in metrics),"unknownCount":sum(m["claimState"]=="unknown" for m in metrics),"verifiedCount":sum(m["verificationStatus"]=="verified" for m in metrics),"conflictCount":sum(m["claimState"]=="conflict" for m in metrics)}})
        self._enrich(capabilities,entity)
        metrics = [m for c in capabilities for m in c["metrics"]]
        insights = [{"id":f"{c['id']}:validation","kind":"verification-need", "statement":f"{c['label']}："+"；".join(c["questions"]),"supportingRecordIds":[m["id"] for m in c["metrics"] if m["claimState"]!="unknown"],"limitations":["分组与字段存在不能单独证明任务效果"]} for c in capabilities]
        for m in metrics:
            if m['claimState'] in {'unknown','rejected'}:continue
            kind='dependency' if m['attributeKey'] in {'supply-chain-dependency','manufacturing-maturity','propulsion-type'} else 'fact'
            insights.append({'id':m['id']+':source-statement','kind':kind,'statement':f"来源记载 {m['label']}：{m['rawValue']}",'supportingRecordIds':[m['id']],'limitations':[] if m['verificationStatus']=='verified' else ['来源陈述尚未核验；不能外推为任务效果']})
        if any(m['claimState'] not in {'unknown','rejected'} for m in metrics):
            insights.append({'id':entity['entityId']+':effect-hypothesis','kind':'hypothesis','statement':'所记录的平台指标能否支持其分类任务角色，仍需与配置、任务条件及使用观察关联核验。','supportingRecordIds':[m['id'] for m in metrics if m['claimState'] not in {'unknown','rejected'}],'limitations':['验证问题，尚未给出能力增益数值。']})
        graph = {"nodes":[{"id":entity["entityId"],"type":"EquipmentModel","label":entity["preferredName"]}],"edges":[]}
        # Keep the profile graph aligned with the published ontology layer.
        # The old UI used the metric record itself as a capability node and
        # called every citation an EvidenceSource.  That loses the distinction
        # between an observation, its metric definition, the evidence excerpt,
        # and the immutable source document.  Preserve the record id for UI
        # actions, while adding the typed resources and declared relations.
        def add_graph_node(node):
            if not any(existing.get("id") == node.get("id") for existing in graph["nodes"]):
                graph["nodes"].append(node)

        def add_evidence_links(from_id, refs):
            for ref in refs or []:
                evidence_id = str(ref.get("evidenceId") or ref.get("itemId") or "")
                if not evidence_id:
                    continue
                evidence_node_id = "evidence:" + digest(evidence_id)[:24]
                source_ref = str(ref.get("sourceRef") or "")
                source_node_id = "source:" + digest(source_ref or evidence_id)[:24]
                add_graph_node({
                    "id": evidence_node_id,
                    "type": "EvidenceItem",
                    "label": str(ref.get("quote") or source_ref or evidence_id)[:240],
                    "evidenceId": evidence_id,
                    "quote": ref.get("quote"),
                    "sourceRef": source_ref,
                    "sourceType": ref.get("sourceType"),
                    "contentHash": ref.get("contentHash"),
                    "snapshotMatched": bool(ref.get("snapshotMatched")),
                    "locator": ref.get("locator") or {},
                })
                add_graph_node({
                    "id": source_node_id,
                    "type": "SourceDocument",
                    "label": source_ref or evidence_id,
                    "sourceRef": source_ref,
                    "contentHash": ref.get("contentHash"),
                })
                graph["edges"].append({"from": from_id, "to": evidence_node_id, "relation": "supportedBy", "status": "source-reference"})
                graph["edges"].append({"from": evidence_node_id, "to": source_node_id, "relation": "derivedFrom", "status": "source-reference"})

        metric_evidence={m['id']:{r['evidenceId'] for r in m['evidenceRefs']} for m in metrics}
        # Events and their quantitative observations are first-class graph
        # objects.  Link them only through stored evidence IDs; a shared topic
        # or date is never enough to invent an event-to-metric relation.
        for event in events:
            event_id=str(event.get('event_id') or event.get('eventId') or '')
            if not event_id: continue
            event_evidence=set(decode(event.get('evidence_ids') or event.get('evidenceIds'),[]) or [])
            graph['nodes'].append({'id':event_id,'type':'Event','label':event.get('title') or event.get('event_type') or event_id,'evidenceIds':sorted(event_evidence)})
            graph['edges'].append({'from':entity['entityId'],'to':event_id,'relation':'participatesInEvent','status':event.get('review_status') or event.get('reviewStatus') or 'pending'})
            for metric_id, evidence_ids in metric_evidence.items():
                if evidence_ids & event_evidence:
                    graph['edges'].append({'from':metric_id,'to':event_id,'relation':'observedDuringEvent','status':'evidence-linked'})
            with self.store._connect() as event_con:
                observations=event_con.execute('SELECT * FROM ontology_event_observation WHERE event_id=? ORDER BY observation_id',(event_id,)).fetchall()
            for obs in observations:
                obs_id=str(obs['observation_id'])
                obs_evidence=set(decode(obs['evidence_ids'],[]) or [])
                graph['nodes'].append({'id':obs_id,'type':'EventObservation','label':obs['metric_type'],'value':obs['value_exact'] if obs['value_exact'] is not None else obs['value_low'],'unit':obs['unit'],'evidenceIds':sorted(obs_evidence)})
                graph['edges'].append({'from':event_id,'to':obs_id,'relation':'hasEventObservation','status':obs['review_status']})
                for metric_id,evidence_ids in metric_evidence.items():
                    if evidence_ids & obs_evidence:
                        graph['edges'].append({'from':metric_id,'to':obs_id,'relation':'quantifiedByEventObservation','status':'evidence-linked'})
        for c in capabilities:
            graph["nodes"].append({"id":c["id"],"type":"CapabilityDomain","label":c["label"]})
            graph["edges"].append({"from":entity["entityId"],"to":c["id"],"relation":"hasCapabilityDomain","status":"classification"})
            for kind,relation in [('technicalMechanisms','implementedBy'),('operationalEffects','reportedEffect'),('constraints','subjectTo')]:
                for index,statement in enumerate(c[kind]):
                    sid=f"{c['id']}:{kind}:{index}"
                    node_type = 'Technology' if kind == 'technicalMechanisms' else kind
                    add_graph_node({'id':sid,'type':node_type,'label':statement['text'],'evidenceRefs':statement['evidenceRefs']})
                    graph['edges'].append({'from':c['id'],'to':sid,'relation':relation,'status':'source-reported'})
                    for record_id in statement['supportingRecordIds']:
                        graph['edges'].append({'from':sid,'to':record_id,'relation':'supportedByObservation','status':'source-reference'})
                    add_evidence_links(sid, statement.get('evidenceRefs'))
            for m in c["metrics"]:
                if m["claimState"]=="unknown": continue
                add_graph_node({"id":m["id"],"type":"CapabilityObservation","label":m["label"],"value":m["value"],"unit":m["unit"],"status":m["verificationStatus"],"attributeKey":m["attributeKey"],"claimState":m["claimState"]})
                graph["edges"].append({"from":c["id"],"to":m["id"],"relation":"hasMetric","status":m["verificationStatus"]})
                metric_id = m["id"] + ":metric"
                add_graph_node({"id":metric_id,"type":"CapabilityMetric","label":m["label"],"metricName":m["label"],"attributeKey":m["attributeKey"],"value":m["value"],"unit":m["unit"],"qualifier":m["qualifier"],"conditions":m["conditions"],"claimState":m["claimState"],"status":m["verificationStatus"]})
                graph["edges"].append({"from":m["id"],"to":metric_id,"relation":"hasMetric","status":m["verificationStatus"]})
                add_evidence_links(m["id"], m["evidenceRefs"])
        profile = {"schemaVersion":SCHEMA_VERSION, "entityId":entity["entityId"],"objectRevision":entity["currentRevision"],"profileId":"","contentHash":"", "identity":{"name":entity["preferredName"],"entityType":entity["entityType"],"category":entity.get("category"),"manufacturer":entity.get("manufacturer"),"originCountry":entity.get("originCountry"),"variant":None,"configuration":None,"updatedAt":entity.get("updatedAt")}, "missionRoles":[{"label":entity.get("equipmentSubclassLabel") or entity.get("category") or "待明确","basis":"classification","evidenceRefs":[]}],"capabilities":capabilities,"quality":quality_for(capabilities),"lineage":lineage,"events":events,"sources":sources,"insights":insights,"graph":graph, "limitations":["分类角色和指标分组不等同于经过验证的任务效果。","Excel 单元格引用保留导入出处，外部事实仍需原文核验。","未记录改型、配置或测试条件时不自动进行横向比较。"]}
        profile["contentHash"] = profile_hash(profile)
        profile["profileId"] = "cap-profile-"+profile["contentHash"][:24]
        errors = validate_profile(profile)
        if errors: raise ValueError("invalid capability profile: "+"; ".join(errors))
        return profile

    def _enrich(self, capabilities, entity):
        published={}
        resolutions={}
        for publication in self.publications(entity['entityId']):
            if publication['publication']['objectRevision'] != entity['currentRevision']: continue
            for m in publication['publication']['records']:
                published[m['id']]=m
            for review in publication['publication']['reviews']:
                resolution=decode(review.get('resolution_json'),{})
                if resolution:resolutions[review['record_id']]=resolution
        mechanisms={'sensor-payload','data-link','propulsion-type','payload-interface','interoperability-standard','autonomy-mode'}
        with self.store._connect() as con:
            for cap in capabilities:
                for m in cap['metrics']:
                    admitted=published.get(m['id'])
                    if admitted and record_hash(admitted)==record_hash(m):
                        m['verificationStatus']='verified'
                        m['reviewStatus']='published'
                # Different values with the same declared context require review.
                buckets={}
                for m in cap['metrics']:
                    if m['claimState']=='unknown':continue
                    buckets.setdefault((m['attributeKey'],m['variant'],m['configuration'],m['conditions']),[]).append(m)
                for values in buckets.values():
                    distinct={dump([m['value'],m['unit'],m['qualifier'],m['lowerBound'],m['upperBound']]) for m in values}
                    if len(distinct)>1:
                        peer_hashes={m['id']:record_hash(m) for m in values}
                        winners=[m for m in values if m['id'] in resolutions and m['verificationStatus']=='verified' and resolutions[m['id']].get('competingRecordHashes')==peer_hashes]
                        if len(winners)==1:
                            winner=winners[0];winner['claimState']='observed'
                            for m in values:
                                if m['id']==winner['id']:continue
                                m['claimState']='rejected';m['verificationStatus']='rejected';m['reviewStatus']='superseded'
                            continue
                        for m in values:
                            m['claimState']='conflict';m['verificationStatus']='conflict'
                # Statements and chart eligibility use the final conflict state.
                # Rejected source values remain visible but cannot support effects.
                for m in cap['metrics']:
                    m['comparability']=comparability_for(m)
                    if m['claimState']!='observed':continue
                    row=con.execute('SELECT metadata_json FROM ontology_attribute_candidate WHERE candidate_id=?',(m['id'],)).fetchone()
                    role=decode(row[0],{}).get('capabilityRole') if row else None
                    refs=[r for r in m['evidenceRefs'] if r['snapshotMatched'] and r['sourceType']!='workbook']
                    if refs and (role=='mechanism' or m['attributeKey'] in mechanisms):
                        cap['technicalMechanisms'].append({'text':m['rawValue'],'claimState':'observed','evidenceRefs':refs,'supportingRecordIds':[m['id']]})
                    if refs and role=='effect':
                        cap['operationalEffects'].append({'text':m['rawValue'],'claimState':'observed','evidenceRefs':refs,'supportingRecordIds':[m['id']]})
                    if refs and m['conditions']:
                        cap['constraints'].append({'text':m['conditions'],'claimState':'observed','evidenceRefs':refs,'supportingRecordIds':[m['id']]})
                cap['summary']={'recordCount':len(cap['metrics']),'presentCount':sum(m['claimState'] not in {'unknown','rejected'} for m in cap['metrics']),'unknownCount':sum(m['claimState']=='unknown' for m in cap['metrics']),'verifiedCount':sum(m['verificationStatus']=='verified' for m in cap['metrics']),'conflictCount':sum(m['claimState']=='conflict' for m in cap['metrics'])}

    def compare(self, entity_ids, attribute_key):
        if attribute_key not in REGISTRY['fields'] or not isinstance(entity_ids,list) or not 1 <= len(entity_ids)<=10:
            raise IntelligenceValidationError('select 1 to 10 entities and a registered capability field')
        included,excluded=[],[]
        cohorts={}
        for entity_id in dict.fromkeys(entity_ids):
            p=self.read(entity_id)
            for c in p['capabilities']:
                for m in c['metrics']:
                    if m['attributeKey']!=attribute_key:continue
                    record={'entityId':p['entityId'],'name':p['identity']['name'],'profileId':p['profileId'],'objectRevision':p['objectRevision'],'record':m}
                    if not m['comparability']['eligible']:
                        excluded.append(record);continue
                    context=dump([m['unit'],m['conditions'],m['variant'],m['configuration']])
                    cohorts.setdefault(context,[]).append(record);included.append(record)
        return {'attributeKey':attribute_key,'label':REGISTRY['fields'][attribute_key]['label'],'included':included,'excluded':excluded,'cohorts':[{'context':decode(k),'records':v,'sampleSize':len(v)} for k,v in cohorts.items()],'limitations':['只在相同单位、条件、改型和配置口径内比较；不计算综合战力分。']}

    def save(self, entity_id):
        profile = self.read(entity_id)
        if profile['objectRevision']<1:raise IntelligenceConflict('entity has no archived object revision')
        with self.store._lock, self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT current_revision FROM ontology_entity WHERE entity_id=?',(profile['entityId'],)).fetchone()
            if not current or current[0]!=profile['objectRevision']:
                raise IntelligenceConflict('object revision changed while building profile')
            cursor = con.execute("INSERT OR IGNORE INTO ontology_capability_profile(profile_id,entity_id,object_revision,schema_version,content_hash,profile_json,created_at) VALUES(?,?,?,?,?,?,?)",(profile["profileId"],profile["entityId"],profile["objectRevision"],profile["schemaVersion"],profile["contentHash"],dump(profile),datetime.now(timezone.utc).isoformat()))
        return {"ok":True,"profile":profile,"duplicate":cursor.rowcount==0}

    def get(self, profile_id):
        with self.store._connect() as con:
            row = con.execute("SELECT profile_json FROM ontology_capability_profile WHERE profile_id=?",(profile_id,)).fetchone()
        if not row: raise IntelligenceNotFound("capability profile not found")
        profile=decode(row[0])
        errors=validate_profile(profile)
        if errors: raise IntelligenceValidationError('stored profile is invalid: '+'; '.join(errors))
        return profile

    def history(self, entity_id, attribute_key=None):
        entity,_=self.agent._entity_document(entity_id)
        if attribute_key is not None and attribute_key not in REGISTRY['fields']:
            raise IntelligenceValidationError('history requires a registered capability field')
        with self.store._connect() as con:
            rows=con.execute('SELECT profile_id,object_revision,created_at FROM ontology_capability_profile WHERE entity_id=? ORDER BY created_at DESC,profile_id LIMIT 20',(entity['entityId'],)).fetchall()
        snapshots=[]
        for row in rows:
            profile=self.get(row['profile_id'])
            records=[m for c in profile['capabilities'] for m in c['metrics'] if attribute_key and m['attributeKey']==attribute_key]
            snapshots.append({'profileId':profile['profileId'],'contentHash':profile['contentHash'],'objectRevision':profile['objectRevision'],'createdAt':row['created_at'],'quality':profile['quality'],'records':records})
        return {'entityId':entity['entityId'],'attributeKey':attribute_key,'snapshots':snapshots,'limit':20,'limitations':['时间轴表示档案快照保存时间，不代表装备能力发生变化的时间。','历史快照保留当时的核验状态；当前状态以最新画像为准。']}

    def _record(self, profile, record_id):
        record = next((m for c in profile["capabilities"] for m in c["metrics"] if m["id"]==record_id),None)
        if not record: raise IntelligenceNotFound("capability record not found in profile")
        return record

    def review(self, profile_id, payload):
        profile = self.get(profile_id)
        record_id, action = payload.get("recordId"), payload.get("action")
        reviewer,reason = str(payload.get("reviewer") or "").strip(),str(payload.get("reason") or "").strip()
        if action not in {"accept","reject","request-more-evidence"} or not reviewer or not reason:
            raise IntelligenceValidationError("explicit action, reviewer and reason are required")
        record = self._record(profile,record_id)
        confidence = payload.get("verifiedConfidence")
        resolution={}
        if action == 'accept' and confidence!='high':
            raise IntelligenceValidationError('acceptance requires explicitly verified high confidence')
        if confidence not in {None,"low","medium","high"}:
            raise IntelligenceValidationError("invalid confidence grade")
        with self.store._lock,self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute("SELECT current_revision FROM ontology_entity WHERE entity_id=?",(profile["entityId"],)).fetchone()
            if not current or current[0]!=profile["objectRevision"]: raise IntelligenceConflict("profile belongs to a stale object revision")
            if action=='accept':
                latest=self.read(profile['entityId'])
                self._require_current_record(latest,record)
                if record['claimState']=='conflict' or self._record(latest,record_id)['claimState']=='conflict':
                    resolution=self._validate_resolution(profile,latest,record,payload.get('resolution'))
                self._publishable_record(record,con,allow_conflict=bool(resolution))
            cursor = con.execute("INSERT INTO ontology_capability_review(profile_id,record_id,action,reviewer,reason,verified_confidence,created_at,resolution_json) VALUES(?,?,?,?,?,?,?,?)",(profile_id,record_id,action,reviewer,reason,confidence,datetime.now(timezone.utc).isoformat(),dump(resolution)))
        return {"ok":True,"reviewId":cursor.lastrowid,"profileId":profile_id,"recordId":record_id,"action":action}

    @staticmethod
    def _conflict_peers(profile,record):
        return [m['id'] for c in profile['capabilities'] for m in c['metrics'] if m['claimState']!='unknown' and all(m.get(k)==record.get(k) for k in ('attributeKey','variant','configuration','conditions'))]

    def _require_current_record(self,latest,record):
        try:current=self._record(latest,record['id'])
        except IntelligenceNotFound as exc:raise IntelligenceConflict('record no longer belongs to the current profile') from exc
        if record_hash(current)!=record_hash(record) or current['claimState']=='rejected':
            raise IntelligenceConflict('record value, evidence or disposition changed; save and review a fresh profile')

    def _validate_resolution(self,profile,latest,record,resolution):
        if not isinstance(resolution,dict) or not str(resolution.get('reason') or '').strip():
            raise IntelligenceConflict('conflict resolution needs explicit competingRecordIds and a source selection reason')
        peers=set(self._conflict_peers(latest,record));frozen=set(self._conflict_peers(profile,record))
        supplied=resolution.get('competingRecordIds')
        if not isinstance(supplied,list) or len(supplied)!=len(set(supplied)) or set(supplied)!=peers or peers!=frozen or record['id'] not in peers:
            raise IntelligenceConflict('save a fresh profile and acknowledge every competing value')
        hashes={i:record_hash(self._record(profile,i)) for i in peers}
        current_hashes={i:record_hash(self._record(latest,i)) for i in peers}
        if hashes!=current_hashes or (resolution.get('competingRecordHashes') is not None and resolution['competingRecordHashes']!=hashes):
            raise IntelligenceConflict('competing source content changed; save and review a fresh profile')
        return {'winnerRecordId':record['id'],'competingRecordIds':sorted(peers),'competingRecordHashes':hashes,'reason':str(resolution['reason']).strip(),'reviewedProfileHash':profile['contentHash']}

    def _publishable_record(self, record, con=None, *, allow_conflict=False):
        resolved=allow_conflict and record['claimState']=='conflict' and record['origin']=='candidate' and not record['derivation']
        if (record["claimState"] != "observed" and not resolved) or record["rawValue"] is None:
            raise IntelligenceValidationError("only source-observed values can be reviewed as facts")
        refs = [r for r in record["evidenceRefs"] if r["snapshotMatched"] and r["sourceType"] != "workbook" and r["sourceRef"].startswith(("https://","http://"))]
        if not refs: raise IntelligenceValidationError("fact publication requires matching public source evidence")
        raw = record["rawValue"].strip()
        if not any(raw.casefold() in r["quote"].casefold() for r in refs):
            raise IntelligenceValidationError("the raw value is not supported literally by the cited quote")
        if con is None:
            with self.store._connect() as source_con:
                return self._publishable_record(record,source_con,allow_conflict=allow_conflict)
        for ref in refs:
            snapshot=con.execute('SELECT item_json,content_hash FROM ontology_collected_source_snapshot WHERE snapshot_id=?',(ref['evidenceId'],)).fetchone()
            if not snapshot: continue
            item=decode(snapshot[0],{})
            body=str(item.get('body') or '')
            if hashlib.sha256(body.encode()).hexdigest()!=ref.get('contentHash') or snapshot[1]!=ref.get('contentHash'):
                continue
            if ref['quote'] in body and (item.get('sourceUri') or item.get('itemUri'))==ref['sourceRef'] and raw.casefold() in ref['quote'].casefold():
                return
        raise IntelligenceValidationError('source snapshot no longer validates against the record')

    def publish(self, profile_id, payload):
        profile = self.get(profile_id)
        selected = payload.get("recordIds")
        publisher,reason = str(payload.get("publishedBy") or "").strip(),str(payload.get("reason") or "").strip()
        if not isinstance(selected,list) or not selected or len(selected)!=len(set(selected)) or not publisher or not reason:
            raise IntelligenceValidationError("select explicit recordIds and supply publisher and reason")
        records = [self._record(profile,i) for i in selected]
        with self.store._lock,self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute("SELECT current_revision FROM ontology_entity WHERE entity_id=?",(profile["entityId"],)).fetchone()
            if not current or current[0]!=profile["objectRevision"]: raise IntelligenceConflict("cannot publish a stale object revision")
            latest=self.read(profile['entityId'])
            reviews = []
            for record in records:
                self._require_current_record(latest,record)
                row = con.execute("SELECT * FROM ontology_capability_review WHERE profile_id=? AND record_id=? ORDER BY review_id DESC LIMIT 1",(profile_id,record["id"])).fetchone()
                if not row or row["action"]!="accept" or row["verified_confidence"]!="high":
                    raise IntelligenceConflict("each selected fact requires its own current accepted review")
                resolution=decode(row['resolution_json'],{})
                if record['claimState']=='conflict' or self._record(latest,record['id'])['claimState']=='conflict':
                    self._validate_resolution(profile,latest,record,resolution)
                if resolution and len(set(selected)&set(resolution['competingRecordIds']))>1:
                    raise IntelligenceConflict('only one winner may be published for each conflicting context')
                self._publishable_record(record,con,allow_conflict=bool(resolution))
                reviews.append(dict(row))
            published_records=[{**m,'claimState':'observed','verificationStatus':'verified','reviewStatus':'published'} for m in records]
            audit_run_id = str(payload.get('auditRunId') or '').strip() or None
            document = {"schemaVersion":"equipment-capability-publication.v1","entityId":profile["entityId"],"objectRevision":profile["objectRevision"],"profileId":profile_id,"auditRunId":audit_run_id,"identity":profile["identity"],"records":published_records,"reviews":reviews}
            publication_id = "cap-publication-"+digest(document)[:24]
            cursor = con.execute("INSERT OR IGNORE INTO ontology_capability_publication(publication_id,profile_id,entity_id,audit_run_id,selected_record_ids,publication_json,published_by,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(publication_id,profile_id,profile["entityId"],audit_run_id,dump(selected),dump(document),publisher,reason,datetime.now(timezone.utc).isoformat()))
            revision = None
            if cursor.rowcount:
                revision = self._publish_entity_revision(con, profile, published_records, publisher, reason, publication_id)
        return {"ok":True,"publicationId":publication_id,"duplicate":cursor.rowcount==0,"publication":document,"entityRevision":revision}

    def _publish_entity_revision(self, con, profile, records, publisher, reason, publication_id):
        """Materialize accepted capability facts as a new immutable JSONB revision.

        The current archive is copied first, then only explicitly published
        records are merged.  The old revision remains untouched and the entity
        pointer advances atomically with the publication.
        """
        import copy
        try:
            from attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
        except ModuleNotFoundError:  # pragma: no cover
            from .attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
        row = con.execute("SELECT current_revision,source_snapshot_ids FROM ontology_entity WHERE entity_id=?", (profile["entityId"],)).fetchone()
        current = con.execute("SELECT * FROM ontology_entity_revision WHERE entity_id=? AND revision=?", (profile["entityId"], profile["objectRevision"])).fetchone()
        if not row or not current:
            raise IntelligenceConflict('current entity archive revision is missing')
        document = decode(current["attributes_json"], {})
        document = copy.deepcopy(document)
        next_revision = int(row["current_revision"]) + 1
        document["objectRevision"] = next_revision
        attrs = document.setdefault("attributes", {})
        expected = list((document.get("completeness") or {}).get("expectedKeys") or attrs.keys())
        for record in records:
            key = str(record.get("attributeKey") or "").strip()
            if not key: continue
            refs = []
            for ref in record.get("evidenceRefs") or []:
                mapped = {name: ref.get(name) for name in ('evidenceId','sourceType','sourceRef','sourceVersion','quote','contentHash','locator','publishedAt','retrievedAt','sourceReliability') if ref.get(name) is not None}
                if not mapped.get('sourceVersion'): mapped['sourceVersion'] = 'unknown'
                if not mapped.get('sourceRef') or not mapped.get('contentHash'): continue
                refs.append(mapped)
            if not refs: continue
            value_type = 'number' if isinstance(record.get('value'), (int,float)) and not isinstance(record.get('value'), bool) else 'text'
            value = record.get('value') if record.get('value') is not None else record.get('rawValue')
            assertion = {
                'assertionId': str(record.get('id') or record.get('assertionId') or f"{profile['entityId']}:{key}:{publication_id}"),
                'rawValue': str(record.get('rawValue') or value), 'valueType': value_type,
                'value': value, 'normalizedValue': value, 'unit': record.get('unit'),
                'qualifier': {'approximate':'approximately'}.get(record.get('qualifier'), record.get('qualifier')) if {'exact','approximately','at-least','at-most','greater-than','less-than','unknown'}.__contains__({'approximate':'approximately'}.get(record.get('qualifier'), record.get('qualifier'))) else 'unknown',
                'conditions': record.get('conditions'), 'validTime': record.get('validTime'), 'observedAt': None,
                'claimKind': 'reported-test' if any(r.get('sourceType') == 'source-document' for r in refs) else 'reported',
                'basis': 'reported-test' if any(r.get('sourceType') == 'source-document' for r in refs) else 'source-document',
                'evidenceRefs': refs, 'confidence': record.get('extractionConfidence') if isinstance(record.get('extractionConfidence'), (int,float)) else 'high',
                'reviewStatus': 'accepted', 'truthVerified': True, 'verifiedConfidence': 'high',
                'reviewer': publisher, 'reviewedAt': now_iso(), 'reviewId': f"{publication_id}:review:{record.get('id')}"
            }
            bucket = attrs.setdefault(key, {'state': 'unknown', 'values': []})
            values = [item for item in bucket.get('values', []) if item.get('assertionId') != assertion['assertionId']]
            values.append(assertion)
            bucket['values'] = values
            bucket['state'] = 'verified'
        present = [key for key, bucket in attrs.items() if bucket.get('state') in {'reported','verified','inferred','conflict'} and bucket.get('values')]
        unknown = [key for key in expected if key not in present and (attrs.get(key) or {}).get('state') == 'unknown']
        completeness = document.setdefault('completeness', {})
        completeness.update({'expectedKeys': expected, 'presentKeys': sorted(set(present)), 'unknownKeys': sorted(set(unknown)), 'notApplicableKeys': completeness.get('notApplicableKeys') or [], 'conflictKeys': completeness.get('conflictKeys') or [], 'missingRequiredKeys': completeness.get('missingRequiredKeys') or [], 'coverageRatio': len(set(present)) / len(expected) if expected else 1.0, 'status': 'valid' if not unknown else 'incomplete', 'checkedAt': now_iso(), 'validatorVersion': 'attributes-jsonb.v1'})
        provenance = document.setdefault('provenance', {})
        provenance.update({'updatedAt': now_iso(), 'generatedBy': 'ontology-audit-publication', 'publicationId': publication_id, 'publishedBy': publisher, 'publicationReason': reason})
        provenance['contentHash'] = canonical_attributes_hash(document)
        errors = validate_attributes_jsonb(document)
        if errors:
            raise IntelligenceValidationError('published attributes JSONB invalid: ' + '; '.join(errors))
        revision_id = f"entity-revision-{profile['entityId']}-{provenance['contentHash'][:24]}"
        con.execute("INSERT INTO ontology_entity_revision(entity_revision_id,entity_id,revision,attributes_json,attributes_schema_version,attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (revision_id, profile['entityId'], next_revision, dump(document), 'equipment-attributes.v1', provenance['contentHash'], current['source_snapshot_id'], current['source_record_id'], 'published', now_iso()))
        con.execute("UPDATE ontology_entity SET current_revision=?,review_status='published',updated_at=? WHERE entity_id=?", (next_revision, now_iso(), profile['entityId']))
        return {'entityRevisionId': revision_id, 'revision': next_revision, 'contentHash': provenance['contentHash'], 'publishedBy': publisher, 'publicationId': publication_id}

    def publications(self, entity_id=None):
        with self.store._connect() as con:
            rows = con.execute("SELECT publication_id,publication_json,created_at FROM ontology_capability_publication"+(" WHERE entity_id=?" if entity_id else "")+" ORDER BY created_at",(entity_id,) if entity_id else ()).fetchall()
        return [{"publicationId":r[0],"publication":decode(r[1]),"createdAt":r[2]} for r in rows]


def published_capability_ontology(store, *, detailed: bool = False):
    """Only explicit per-record human publications enter the shared fact layer."""
    ns='https://local.ontology/coast-raid-k#'
    instance='https://local.ontology/coast-raid-k/intelligence#'
    objects,relations,provenance={},[],[]
    current_profiles={}
    try:
        from ontology_agent import OntologyAgent
    except ModuleNotFoundError:
        from .ontology_agent import OntologyAgent
    profiles=CapabilityProfiles(OntologyAgent(store))
    with store._connect() as con:
        rows=con.execute('SELECT * FROM ontology_capability_publication ORDER BY created_at').fetchall()
        for row in rows:
            doc=decode(row['publication_json'],{})
            if doc['entityId'] not in current_profiles:
                current_profiles[doc['entityId']]=profiles.read(doc['entityId'])
            current=current_profiles[doc['entityId']]
            current_records={m['id']:m for c in current['capabilities'] for m in c['metrics']}
            equipment=instance+doc['entityId']
            objects.setdefault(equipment,{'id':equipment,'iri':equipment,'type':'EquipmentModel','ontologyClass':ns+'EquipmentModel','label':doc['identity']['name']})
            for record in doc['records']:
                current_record=current_records.get(record['id'])
                valid=bool(current['objectRevision']==doc['objectRevision'] and current_record and record_hash(current_record)==record_hash(record) and current_record['verificationStatus']=='verified')
                validity={'isCurrentVerified':valid,'currentObjectRevision':current['objectRevision'],'currentVerificationStatus':current_record['verificationStatus'] if current_record else 'not-in-current-profile','historicalReviewConfidence':'high'}
                iri=instance+row['publication_id']+'-'+digest(record['id'])[:16]
                objects[iri]={'id':iri,'iri':iri,'type':'CapabilityObservation','ontologyClass':ns+'CapabilityObservation','label':doc['identity']['name']+' · '+record['label']+' '+record['rawValue'],'assertion':record,'profileId':doc['profileId'],'objectRevision':doc['objectRevision'],'publicationId':row['publication_id'],'verifiedConfidence':'high' if valid else None,'validity':validity}
                relations.append({'id':iri+':equipment','from':equipment,'to':iri,'relation':'hasCapabilityObservation','predicate':ns+'hasCapabilityObservation','publicationId':row['publication_id']})
                if detailed:
                    # A publication record is an observation; its metric,
                    # evidence, mechanism and event links are separate
                    # ontology resources so downstream reasoning never has
                    # to infer them from a display label.
                    metric_iri=iri+':metric'
                    objects[metric_iri]={'id':metric_iri,'iri':metric_iri,'type':'CapabilityMetric','ontologyClass':ns+'CapabilityMetric','label':record['label'],'metricName':record.get('label'),'rawValue':record.get('rawValue'),'value':record.get('value'),'unit':record.get('unit'),'qualifier':record.get('qualifier'),'conditions':record.get('conditions'),'validTime':record.get('validTime'),'claimState':record.get('claimState'),'verificationStatus':record.get('verificationStatus'),'assertion':record,'observationId':iri,'publicationId':row['publication_id']}
                    relations.append({'id':iri+':metric','from':iri,'to':metric_iri,'relation':'hasMetric','predicate':ns+'hasMetric','publicationId':row['publication_id']})
                    source_ids=set()
                    for ref in record.get('evidenceRefs') or []:
                        evidence_id=str(ref.get('evidenceId') or ref.get('itemId') or '')
                        if not evidence_id: continue
                        evidence_iri=instance+'evidence-'+digest(evidence_id)[:20]
                        source_ref=str(ref.get('sourceRef') or '')
                        source_iri=instance+'source-'+digest(source_ref or evidence_id)[:20]
                        source_ids.add(evidence_id)
                        objects.setdefault(source_iri,{'id':source_iri,'iri':source_iri,'type':'SourceDocument','ontologyClass':ns+'SourceDocument','label':source_ref or evidence_id,'sourceRef':source_ref,'contentHash':ref.get('contentHash')})
                        objects.setdefault(evidence_iri,{'id':evidence_iri,'iri':evidence_iri,'type':'EvidenceItem','ontologyClass':ns+'EvidenceItem','label':str(ref.get('quote') or '')[:240],'quote':ref.get('quote'),'evidenceId':evidence_id,'sourceDocumentId':source_iri,'contentHash':ref.get('contentHash')})
                        relations.append({'id':iri+':evidence:'+digest(evidence_id)[:16],'from':iri,'to':evidence_iri,'relation':'supportedBy','predicate':ns+'supportedBy','publicationId':row['publication_id']})
                        relations.append({'id':evidence_iri+':source','from':evidence_iri,'to':source_iri,'relation':'derivedFrom','predicate':ns+'derivedFrom','publicationId':row['publication_id']})
                    # Mechanism fields become source-bound Technology nodes.
                    if record.get('attributeKey') in {'sensor-payload','data-link','propulsion-type','payload-interface','interoperability-standard','autonomy-mode'} or record.get('capabilityRole') == 'mechanism':
                        technology_iri=instance+'technology-'+digest([record.get('attributeKey'),record.get('rawValue'),record.get('evidenceRefs')])[:20]
                        objects.setdefault(technology_iri,{'id':technology_iri,'iri':technology_iri,'type':'Technology','ontologyClass':ns+'Technology','label':str(record.get('rawValue') or record.get('label') or ''),'assertion':record,'publicationId':row['publication_id']})
                        relations.append({'id':equipment+':technology:'+digest(technology_iri)[:12],'from':equipment,'to':technology_iri,'relation':'usesTechnology','predicate':ns+'usesTechnology','publicationId':row['publication_id']})
                    # Link to stored test/observation events only when the
                    # event and metric share an immutable evidence ID.
                    for event in current.get('events') or []:
                        event_ids=decode(event.get('evidence_ids') or event.get('evidenceIds'),[]) or []
                        if not source_ids.intersection(str(item) for item in event_ids): continue
                        event_id=str(event.get('event_id') or event.get('eventId') or '')
                        if not event_id: continue
                        event_iri=instance+'event-'+event_id
                        objects.setdefault(event_iri,{'id':event_iri,'iri':event_iri,'type':'TestEvent','ontologyClass':ns+'TestEvent','label':event.get('title') or event.get('event_type') or event_id,'assertion':event,'evidenceIds':event_ids})
                        relations.append({'id':iri+':event:'+event_id,'from':iri,'to':event_iri,'relation':'measuredIn','predicate':ns+'measuredIn','publicationId':row['publication_id']})
                        relations.append({'id':event_iri+':equipment:'+equipment,'from':event_iri,'to':equipment,'relation':'testsEquipment','predicate':ns+'testsEquipment','publicationId':row['publication_id']})
                provenance.append({'publicationId':row['publication_id'],'profileId':doc['profileId'],'objectRevision':doc['objectRevision'],'recordId':record['id'],'evidenceRefs':record['evidenceRefs'],'reviews':doc['reviews'],'publishedBy':row['published_by'],'publishedAt':row['created_at'],'reason':row['reason'],'validity':validity})
    return {'objects':list(objects.values()),'relations':relations,'provenance':provenance,'admissionCount':len(rows),'detailLevel':'full' if detailed else 'compatibility'}
